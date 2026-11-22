"""Clothing-order business operations."""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from copy import deepcopy
from typing import Any, Optional

from .exceptions import (
    InvalidStatusTransitionError,
    OrderNotFoundError,
    ValidationError,
    CustomerNotFoundError,
    DataStorageError,
    DuplicateRecordError,
)
from .storage import (
    CUSTOMERS_FILE,
    ORDERS_FILE,
    load_data,
    resolve_path,
    record_lock,
    save_data,
    read_next_id,
    save_next_id,
)
from .validators import (
    parse_date,
    validate_description,
    validate_id,
    validate_measurements,
    validate_money,
    validate_order_status,
    validate_quantity,
    validate_complete_iso_datetime,
)


TRANSITIONS = {
    "pending": {"in_progress", "cancelled"},
    "in_progress": {"ready", "cancelled"},
    "ready": {"delivered", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
}

_UNSET = object()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_orders_file(orders_file: Optional[Any] = None) -> Any:
    return ORDERS_FILE if orders_file is None else orders_file


def load_validated_orders(
    orders_file: Optional[Any] = None,
    customers_file: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """Load order JSON after enforcing the order record schema."""
    path = _resolve_orders_file(orders_file)
    try:
        records = load_data(path)
    except DuplicateRecordError as exc:
        raise DataStorageError(str(exc)) from exc
    seen_ids: set[int] = set()

    from .customers import load_validated_customers
    customer_ids: Optional[set[int]] = None
    if customers_file is not None:
        customer_path = customers_file
        customer_ids = {
            record["id"] for record in load_validated_customers(customer_path)
        }

    for index, order in enumerate(records):
        label = "Order " + str(order.get("order_id", index + 1))
        if not isinstance(order, dict):
            raise DataStorageError("Every order record must be an object.")

        order_id = order.get("order_id")
        if isinstance(order_id, bool) or not isinstance(order_id, int) or order_id <= 0:
            raise DataStorageError(label + " has a malformed or missing ID.")
        if order_id in seen_ids:
            raise DataStorageError("Duplicate order ID " + str(order_id) + " found.")
        seen_ids.add(order_id)

        customer_id = order.get("customer_id")
        if isinstance(customer_id, bool) or not isinstance(customer_id, int) or customer_id <= 0:
            raise DataStorageError(label + " has a malformed or missing customer ID.")
        if customer_ids is not None and customer_id not in customer_ids:
            raise DataStorageError(
                label + " references nonexistent customer " + str(customer_id) + "."
            )

        for field_name in ("item_name", "description"):
            try:
                value = validate_description(order.get(field_name))
            except ValidationError as exc:
                raise DataStorageError(label + " has an invalid " + field_name + ".") from exc
            if order.get(field_name) != value:
                raise DataStorageError(label + " has an invalid " + field_name + " format.")

        quantity = order.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise DataStorageError(label + " has an invalid quantity.")

        try:
            unit_price = validate_money(order.get("unit_price"), "Unit price", allow_zero=True)
        except ValidationError as exc:
            raise DataStorageError(label + " has an invalid unit price.") from exc
        if order.get("unit_price") != unit_price:
            raise DataStorageError(label + " has an invalid unit price format.")

        try:
            total = Decimal(str(order.get("total_amount")))
            if not total.is_finite() or total < 0:
                raise ValueError
            if format(total.quantize(Decimal("0.01")), ".2f") != str(order.get("total_amount")):
                raise ValueError
        except (InvalidOperation, TypeError, ValueError):
            raise DataStorageError(label + " has an invalid total amount.") from None
        expected_total = (Decimal(unit_price) * quantity).quantize(Decimal("0.01"))
        if total != expected_total:
            raise DataStorageError(label + " total amount is inconsistent with quantity and unit price.")

        status = order.get("status")
        try:
            validate_order_status(status)
        except ValidationError as exc:
            raise DataStorageError(label + " has an invalid order status.") from exc

        for field_name in ("order_date", "delivery_date"):
            try:
                parsed = parse_date(order.get(field_name), field_name)
            except ValidationError as exc:
                raise DataStorageError(label + " has an invalid " + field_name + ".") from exc
            if order.get(field_name) != parsed.isoformat():
                raise DataStorageError(label + " has an invalid " + field_name + " format.")
        if parse_date(order["delivery_date"]) < parse_date(order["order_date"]):
            raise DataStorageError(label + " delivery date cannot be earlier than order date.")

        measurements = order.get("measurements_snapshot")
        if not isinstance(measurements, dict):
            raise DataStorageError(label + " has malformed measurements.")
        try:
            validate_measurements(measurements)
        except ValidationError as exc:
            raise DataStorageError(label + " has malformed measurements.") from exc

        notes = order.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise DataStorageError(label + " has invalid notes.")

        for field_name in ("created_at", "updated_at"):
            timestamp = order.get(field_name)
            if not isinstance(timestamp, str):
                raise DataStorageError(label + " has an invalid " + field_name + " timestamp.")
            try:
                validate_complete_iso_datetime(timestamp, field_name)
            except ValidationError as exc:
                raise DataStorageError(label + " has an invalid " + field_name + " timestamp.") from exc

        if status == "cancelled":
            cancelled_at = order.get("cancelled_at")
            if not isinstance(cancelled_at, str):
                raise DataStorageError(label + " is missing a valid cancelled_at timestamp.")
            try:
                validate_complete_iso_datetime(cancelled_at, "cancelled_at")
            except ValidationError as exc:
                raise DataStorageError(label + " has an invalid cancelled_at timestamp.") from exc
        elif "cancelled_at" in order:
            cancelled_at = order["cancelled_at"]
            if not isinstance(cancelled_at, str):
                raise DataStorageError(label + " has an invalid cancelled_at timestamp.")
            try:
                validate_complete_iso_datetime(cancelled_at, "cancelled_at")
            except ValidationError as exc:
                raise DataStorageError(label + " has an invalid cancelled_at timestamp.") from exc

    return records


def generate_order_id(
    orders_file: Optional[Any] = None,
    customers_file: Optional[Any] = None,
) -> int:
    path = _resolve_orders_file(orders_file)
    with record_lock(path):
        records = load_validated_orders(path, customers_file)
        highest = max(
            (record.get("order_id", 0) for record in records if isinstance(record.get("order_id"), int)),
            default=0,
        )
        return max(highest + 1, read_next_id("order", path))


def create_order(
    customer_id: int,
    item_name: Optional[str] = None,
    description: Optional[str] = None,
    quantity: int = 1,
    unit_price: Any = "0.00",
    delivery_date: Any = None,
    order_date: Any = None,
    notes: Optional[str] = None,
    measurements_snapshot: Optional[dict[str, Any]] = None,
    orders_file: Optional[Any] = None,
    customers_file: Optional[Any] = None,
) -> dict[str, Any]:
    customer_id = validate_id(customer_id, "Customer ID")
    from .customers import load_validated_customers

    order_path = _resolve_orders_file(orders_file)
    customer_path = CUSTOMERS_FILE if customers_file is None else customers_file

    with record_lock(customer_path):
        with record_lock(order_path):
            customers = load_validated_customers(customer_path)
            customer = next(
                (record for record in customers if record.get("id") == customer_id),
                None,
            )
            if customer is None:
                raise CustomerNotFoundError("Customer " + str(customer_id) + " does not exist.")
            if customer["active"] is not True:
                raise ValidationError("Orders cannot be created for inactive customers.")

            if item_name is None:
                item_name = description
            if item_name is None:
                raise ValidationError("An item name or order description is required.")
            item_name = validate_description(item_name)
            if description is None:
                description = item_name
            description = validate_description(description)

            quantity = validate_quantity(quantity)
            unit_price = validate_money(unit_price, "Unit price", allow_zero=True)
            total = Decimal(unit_price) * quantity
            total_amount = format(total.quantize(Decimal("0.01")), ".2f")

            order_date_value = date.today() if order_date is None else parse_date(order_date, "Order date")
            if delivery_date is None:
                raise ValidationError("Delivery date is required.")
            delivery_date_value = parse_date(delivery_date, "Delivery date")
            if delivery_date_value < order_date_value:
                raise ValidationError("Delivery date cannot be earlier than order date.")

            if measurements_snapshot is None:
                measurements_snapshot = customer.get("measurements", {})
            measurements_snapshot = validate_measurements(measurements_snapshot)
            if notes is not None:
                if not isinstance(notes, str):
                    raise ValidationError("Notes must be a string or null.")
                notes = notes.strip() or None

            records = load_validated_orders(order_path, customer_path)
            highest = max(
                (record.get("order_id", 0) for record in records if isinstance(record.get("order_id"), int)),
                default=0,
            )
            order_id = max(highest + 1, read_next_id("order", order_path))
            timestamp = _timestamp()
            record = {
                "order_id": order_id,
                "customer_id": customer_id,
                "item_name": item_name,
                "description": description,
                "quantity": quantity,
                "unit_price": unit_price,
                "total_amount": total_amount,
                "measurements_snapshot": measurements_snapshot,
                "order_date": order_date_value.isoformat(),
                "delivery_date": delivery_date_value.isoformat(),
                "status": "pending",
                "notes": notes,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            save_data([*records, deepcopy(record)], order_path)
            save_next_id("order", order_id + 1, order_path)
            return record


def get_order_by_id(
    order_id: int,
    orders_file: Optional[Any] = None,
    customers_file: Optional[Any] = None,
) -> dict[str, Any]:
    order_id = validate_id(order_id, "Order ID")
    for record in load_validated_orders(orders_file, customers_file):
        if record.get("order_id") == order_id:
            return record
    raise OrderNotFoundError("Order " + str(order_id) + " does not exist.")


def get_all_orders(
    orders_file: Optional[Any] = None,
    customers_file: Optional[Any] = None,
) -> list[dict[str, Any]]:
    return load_validated_orders(orders_file, customers_file)


def get_customer_orders(
    customer_id: int,
    orders_file: Optional[Any] = None,
    customers_file: Optional[Any] = None,
) -> list[dict[str, Any]]:
    customer_id = validate_id(customer_id, "Customer ID")
    return [
        record for record in get_all_orders(orders_file, customers_file)
        if record.get("customer_id") == customer_id
    ]


def update_order(
    order_id: int,
    item_name: Optional[str] = None,
    description: Optional[str] = None,
    quantity: Optional[int] = None,
    unit_price: Any = None,
    delivery_date: Any = None,
    notes: Any = _UNSET,
    status: Optional[str] = None,
    orders_file: Optional[Any] = None,
    customers_file: Optional[Any] = None,
    payments_file: Optional[Any] = None,
) -> dict[str, Any]:
    order_id = validate_id(order_id, "Order ID")
    order_path = _resolve_orders_file(orders_file)

    with record_lock(order_path):
        records = load_validated_orders(order_path, customers_file)
        order = next(
            (record for record in records if record.get("order_id") == order_id),
            None,
        )
        if order is None:
            raise OrderNotFoundError("Order " + str(order_id) + " does not exist.")

        current_status = validate_order_status(order["status"])
        final_status = current_status
        if status is not None:
            final_status = validate_order_status(status)
            if final_status not in TRANSITIONS[current_status]:
                raise InvalidStatusTransitionError(
                    "Cannot change order status from " + current_status
                    + " to " + final_status + "."
                )

        changing_business_fields = any(
            value is not None
            for value in (item_name, description, quantity, unit_price, delivery_date)
        ) or notes is not _UNSET
        if current_status in {"cancelled", "delivered"}:
            if changing_business_fields:
                raise InvalidStatusTransitionError(
                    "A " + current_status + " order cannot change business details."
                )
        if final_status == "cancelled" and changing_business_fields:
            raise InvalidStatusTransitionError(
                "A cancelled order cannot change business details."
            )

        updates: dict[str, Any] = {}
        if item_name is not None:
            updates["item_name"] = validate_description(item_name)
        if description is not None:
            updates["description"] = validate_description(description)
        if quantity is not None:
            updates["quantity"] = validate_quantity(quantity)
        if unit_price is not None:
            updates["unit_price"] = validate_money(unit_price, "Unit price", allow_zero=True)
        if delivery_date is not None:
            delivery = parse_date(delivery_date, "Delivery date")
            order_date = parse_date(order["order_date"], "Order date")
            if delivery < order_date:
                raise ValidationError("Delivery date cannot be earlier than order date.")
            updates["delivery_date"] = delivery.isoformat()
        if notes is not _UNSET:
            if not isinstance(notes, str):
                raise ValidationError("Notes must be a string or null.")
            updates["notes"] = notes.strip() or None

        final_quantity = updates.get("quantity", order["quantity"])
        final_unit_price = updates.get("unit_price", order["unit_price"])
        if quantity is not None or unit_price is not None:
            new_total = (Decimal(final_unit_price) * final_quantity).quantize(Decimal("0.01"))
            from .payments import calculate_total_paid
            paid = calculate_total_paid(order_id, payments_file)
            if new_total < paid:
                raise ValidationError(
                    "Order total cannot be lower than the amount already paid."
                )
            updates["total_amount"] = format(new_total, ".2f")

        if status is not None:
            updates["status"] = final_status
            if final_status == "cancelled":
                updates["cancelled_at"] = _timestamp()

        timestamp = _timestamp()
        updates["updated_at"] = timestamp
        index = next(
            index for index, record in enumerate(records)
            if record.get("order_id") == order_id
        )
        records[index] = {**records[index], **deepcopy(updates)}
        save_data(records, order_path)
        return deepcopy(records[index])


def update_order_status(
    order_id: int,
    new_status: str,
    orders_file: Optional[Any] = None,
    customers_file: Optional[Any] = None,
) -> dict[str, Any]:
    order_id = validate_id(order_id, "Order ID")
    order_path = _resolve_orders_file(orders_file)
    with record_lock(order_path):
        records = load_validated_orders(order_path, customers_file)
        order = next(
            (record for record in records if record.get("order_id") == order_id),
            None,
        )
        if order is None:
            raise OrderNotFoundError("Order " + str(order_id) + " does not exist.")
        new_status = validate_order_status(new_status)
        current = validate_order_status(order["status"])
        if new_status not in TRANSITIONS[current]:
            raise InvalidStatusTransitionError(
                "Cannot change order status from " + current + " to " + new_status + "."
            )
        updates: dict[str, Any] = {
            "status": new_status,
            "updated_at": _timestamp(),
        }
        if new_status == "cancelled":
            updates["cancelled_at"] = _timestamp()
        index = next(
            index for index, record in enumerate(records)
            if record.get("order_id") == order_id
        )
        records[index] = {**records[index], **updates}
        save_data(records, order_path)
        return deepcopy(records[index])


def cancel_order(order_id: int, orders_file: Optional[Any] = None) -> dict[str, Any]:
    order = get_order_by_id(order_id, orders_file)
    if order["status"] in {"delivered", "cancelled"}:
        raise InvalidStatusTransitionError(
            "A " + order["status"] + " order cannot be cancelled."
        )
    return update_order_status(order_id, "cancelled", orders_file)
