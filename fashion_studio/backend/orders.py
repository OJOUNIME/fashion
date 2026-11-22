"""Clothing-order business operations."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from .exceptions import (
    InvalidStatusTransitionError,
    OrderNotFoundError,
    ValidationError,
)
from .storage import (
    CUSTOMERS_FILE,
    ORDERS_FILE,
    append_record,
    load_data,
    read_next_id,
    save_next_id,
    update_record,
)
from .validators import (
    parse_date,
    validate_description,
    validate_id,
    validate_measurements,
    validate_money,
    validate_order_status,
    validate_quantity,
)


TRANSITIONS = {
    "pending": {"in_progress", "cancelled"},
    "in_progress": {"ready", "cancelled"},
    "ready": {"delivered"},
    "delivered": set(),
    "cancelled": set(),
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_orders_file(orders_file: Optional[Any] = None) -> Any:
    return ORDERS_FILE if orders_file is None else orders_file


def generate_order_id(orders_file: Optional[Any] = None) -> int:
    path = _resolve_orders_file(orders_file)
    records = load_data(path)
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

    customer_path = CUSTOMERS_FILE if customers_file is None else customers_file
    customers = load_data(customer_path)
    customer = next((record for record in customers if record.get("id") == customer_id), None)
    if customer is None:
        from .exceptions import CustomerNotFoundError
        raise CustomerNotFoundError("Customer " + str(customer_id) + " does not exist.")
    if customer.get("active", True) is False:
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

    order_path = _resolve_orders_file(orders_file)
    order_id = generate_order_id(order_path)
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
    append_record(record, order_path)
    save_next_id("order", order_id + 1, order_path)
    return record


def get_order_by_id(order_id: int, orders_file: Optional[Any] = None) -> dict[str, Any]:
    order_id = validate_id(order_id, "Order ID")
    for record in load_data(_resolve_orders_file(orders_file)):
        if record.get("order_id") == order_id:
            return record
    raise OrderNotFoundError("Order " + str(order_id) + " does not exist.")


def get_all_orders(orders_file: Optional[Any] = None) -> list[dict[str, Any]]:
    return load_data(_resolve_orders_file(orders_file))


def get_customer_orders(customer_id: int, orders_file: Optional[Any] = None) -> list[dict[str, Any]]:
    customer_id = validate_id(customer_id, "Customer ID")
    return [
        record for record in get_all_orders(orders_file)
        if record.get("customer_id") == customer_id
    ]


def update_order(
    order_id: int,
    item_name: Optional[str] = None,
    description: Optional[str] = None,
    quantity: Optional[int] = None,
    unit_price: Any = None,
    delivery_date: Any = None,
    notes: Any = None,
    status: Optional[str] = None,
    orders_file: Optional[Any] = None,
) -> dict[str, Any]:
    order = get_order_by_id(order_id, orders_file)
    if order["status"] in {"cancelled", "delivered"}:
        changing_business_fields = any(
            value is not None
            for value in (item_name, description, quantity, unit_price, delivery_date)
        )
        if changing_business_fields:
            raise InvalidStatusTransitionError(
                "A " + order["status"] + " order cannot change business details."
            )
    updates: dict[str, Any] = {}
    if item_name is not None:
        updates["item_name"] = validate_description(item_name)
    if description is not None:
        updates["description"] = validate_description(description)
    if quantity is not None:
        quantity = validate_quantity(quantity)
        updates["quantity"] = quantity
    if unit_price is not None:
        updates["unit_price"] = validate_money(unit_price, "Unit price", allow_zero=True)
    if delivery_date is not None:
        delivery = parse_date(delivery_date, "Delivery date")
        order_date = parse_date(order["order_date"], "Order date")
        if delivery < order_date:
            raise ValidationError("Delivery date cannot be earlier than order date.")
        updates["delivery_date"] = delivery.isoformat()
    if notes is not None:
        if not isinstance(notes, str):
            raise ValidationError("Notes must be a string or null.")
        updates["notes"] = notes.strip() or None
    final_quantity = updates.get("quantity", order["quantity"])
    final_unit_price = updates.get("unit_price", order["unit_price"])
    if quantity is not None or unit_price is not None:
        updates["total_amount"] = format(
            Decimal(final_unit_price) * final_quantity,
            ".2f",
        )
    if status is not None:
        update_order_status(order_id, status, orders_file)
        order = get_order_by_id(order_id, orders_file)
    updates["updated_at"] = _timestamp()
    try:
        updated = update_record(order_id, updates, _resolve_orders_file(orders_file), id_field="order_id")
    except KeyError:
        raise OrderNotFoundError("Order " + str(order_id) + " does not exist.") from None
    return updated


def update_order_status(order_id: int, new_status: str, orders_file: Optional[Any] = None) -> dict[str, Any]:
    order = get_order_by_id(order_id, orders_file)
    new_status = validate_order_status(new_status)
    current = validate_order_status(order["status"])
    if new_status not in TRANSITIONS[current]:
        raise InvalidStatusTransitionError(
            "Cannot change order status from " + current + " to " + new_status + "."
        )
    try:
        return update_record(
            order_id,
            {"status": new_status, "updated_at": _timestamp()},
            _resolve_orders_file(orders_file),
            id_field="order_id",
        )
    except KeyError:
        raise OrderNotFoundError("Order " + str(order_id) + " does not exist.") from None


def cancel_order(order_id: int, orders_file: Optional[Any] = None) -> dict[str, Any]:
    order = get_order_by_id(order_id, orders_file)
    if order["status"] in {"delivered", "cancelled"}:
        raise InvalidStatusTransitionError(
            "A " + order["status"] + " order cannot be cancelled."
        )
    return update_order_status(order_id, "cancelled", orders_file)
