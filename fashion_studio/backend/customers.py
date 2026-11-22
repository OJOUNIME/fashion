"""Customer and customer-body-measurement business operations."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .exceptions import (
    CustomerNotFoundError,
    DataStorageError,
    ValidationError,
)
from .storage import (
    CUSTOMERS_FILE,
    ORDERS_FILE,
    delete_record,
    load_data,
    read_next_id,
    record_lock,
    save_data,
    save_next_id,
    update_record,
)
from .validators import (
    validate_customer_name,
    validate_email,
    validate_id,
    validate_measurements,
    validate_phone,
    normalize_phone,
    validate_complete_iso_datetime,
)

_UNSET = object()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_customer_file(customers_file: Optional[Any] = None) -> Any:
    return CUSTOMERS_FILE if customers_file is None else customers_file


def load_validated_customers(customers_file: Optional[Any] = None) -> list[dict[str, Any]]:
    """Load customer JSON after enforcing the customer record schema."""
    path = _resolve_customer_file(customers_file)
    records = load_data(path)
    seen_ids: set[int] = set()
    seen_phones: set[str] = set()

    for index, customer in enumerate(records):
        label = "Customer " + str(customer.get("id", index + 1))
        if not isinstance(customer, dict):
            raise DataStorageError("Every customer record must be an object.")

        customer_id = customer.get("id")
        if isinstance(customer_id, bool) or not isinstance(customer_id, int) or customer_id <= 0:
            raise DataStorageError(label + " has a malformed or missing ID.")
        if customer_id in seen_ids:
            raise DataStorageError("Duplicate customer ID " + str(customer_id) + " found.")
        seen_ids.add(customer_id)

        try:
            validate_customer_name(customer.get("name"))
        except ValidationError as exc:
            raise DataStorageError(label + " has an invalid name.") from exc

        phone = customer.get("phone")
        try:
            normalized_phone = normalize_phone(phone)
        except ValidationError as exc:
            raise DataStorageError(label + " has an invalid phone number.") from exc
        if normalized_phone in seen_phones:
            raise DataStorageError(
                "Duplicate customer phone number " + str(phone) + " found."
            )
        seen_phones.add(normalized_phone)

        if "email" not in customer:
            raise DataStorageError(label + " is missing required field: email.")
        try:
            validate_email(customer["email"])
        except ValidationError as exc:
            raise DataStorageError(label + " has an invalid email.") from exc

        if "address" not in customer:
            raise DataStorageError(label + " is missing required field: address.")
        address = customer["address"]
        if address is not None and not isinstance(address, str):
            raise DataStorageError(label + " has an invalid address.")

        if "measurements" not in customer:
            raise DataStorageError(label + " is missing required field: measurements.")
        measurements = customer["measurements"]
        if not isinstance(measurements, dict):
            raise DataStorageError(label + " has malformed measurements.")
        try:
            validate_measurements(measurements)
        except ValidationError as exc:
            raise DataStorageError(label + " has malformed measurements.") from exc

        if "active" not in customer or not isinstance(customer["active"], bool):
            raise DataStorageError(label + " has an invalid active/deactivated status.")

        for field_name in ("created_at", "updated_at"):
            if field_name not in customer:
                raise DataStorageError(
                    label + " is missing required field: " + field_name + "."
                )
            timestamp = customer[field_name]
            if not isinstance(timestamp, str):
                raise DataStorageError(
                    label + " has an invalid " + field_name + " timestamp."
                )
            try:
                validate_complete_iso_datetime(timestamp, field_name)
            except ValidationError as exc:
                raise DataStorageError(
                    label + " has an invalid " + field_name + " timestamp."
                ) from exc

    return records


def generate_customer_id(customers_file: Optional[Any] = None) -> int:
    path = _resolve_customer_file(customers_file)
    records = load_validated_customers(path)
    id_field = "id"
    highest = max(
        (record.get(id_field, 0) for record in records if isinstance(record.get(id_field), int)),
        default=0,
    )
    persisted = read_next_id("customer", path)
    return max(highest + 1, persisted)


def add_customer(
    name: str,
    phone: str,
    measurements: Optional[dict[str, Any]] = None,
    email: Optional[str] = None,
    address: Optional[str] = None,
    customers_file: Optional[Any] = None,
) -> dict[str, Any]:
    name = validate_customer_name(name)
    phone = validate_phone(phone)
    email = validate_email(email)
    if address is not None:
        if not isinstance(address, str):
            raise ValidationError("Address must be a string or null.")
        address = address.strip() or None
    measurements = validate_measurements(measurements)

    path = _resolve_customer_file(customers_file)
    with record_lock(path):
        records = load_validated_customers(path)
        normalized_phone = normalize_phone(phone)
        if any(normalize_phone(record.get("phone")) == normalized_phone for record in records):
            raise ValidationError("A customer with this phone number already exists.")

        highest = max(
            (record.get("id", 0) for record in records if isinstance(record.get("id"), int)),
            default=0,
        )
        persisted = read_next_id("customer", path)
        customer_id = max(highest + 1, persisted)
        timestamp = _timestamp()
        record = {
            "id": customer_id,
            "name": name,
            "phone": phone,
            "email": email,
            "address": address,
            "measurements": measurements,
            "active": True,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        save_data([*records, record], path)
        save_next_id("customer", customer_id + 1, path)
        return record


def get_customer_by_id(customer_id: int, customers_file: Optional[Any] = None) -> dict[str, Any]:
    customer_id = validate_id(customer_id, "Customer ID")
    path = _resolve_customer_file(customers_file)
    for record in load_validated_customers(path):
        if record.get("id") == customer_id:
            return record
    raise CustomerNotFoundError("Customer " + str(customer_id) + " does not exist.")


def get_all_customers(customers_file: Optional[Any] = None) -> list[dict[str, Any]]:
    return load_validated_customers(customers_file)


def search_customers(query: Any, customers_file: Optional[Any] = None) -> list[dict[str, Any]]:
    """Search names and phones by partial text; integer or exact numeric text searches use exact customer IDs."""
    if isinstance(query, str):
        query_text = query.strip().lower()
        if not query_text:
            return get_all_customers(customers_file)
    elif isinstance(query, int) and not isinstance(query, bool):
        query_text = str(query)
    else:
        raise ValidationError("Search query must be a name, phone number or ID.")

    matches = []
    for customer in get_all_customers(customers_file):
        name = str(customer.get("name", "")).lower()
        phone = str(customer.get("phone", "")).lower()
        customer_id = str(customer.get("id", ""))
        if query_text in name or query_text in phone or query_text == customer_id:
            matches.append(customer)
    return matches


def update_customer(
    customer_id: int,
    name: Any = _UNSET,
    phone: Any = _UNSET,
    email: Any = _UNSET,
    address: Any = _UNSET,
    measurements: Any = _UNSET,
    customers_file: Optional[Any] = None,
) -> dict[str, Any]:
    customer_id = validate_id(customer_id, "Customer ID")
    updates: dict[str, Any] = {}
    if name is not _UNSET:
        updates["name"] = validate_customer_name(name)
    if phone is not _UNSET:
        phone = validate_phone(phone)
        updates["phone"] = phone
    if email is not _UNSET:
        updates["email"] = validate_email(email)
    if address is not _UNSET:
        if address is None:
            updates["address"] = None
        else:
            if not isinstance(address, str):
                raise ValidationError("Address must be a string or null.")
            updates["address"] = address.strip() or None
    updates["updated_at"] = _timestamp()

    path = _resolve_customer_file(customers_file)
    with record_lock(path):
        records = load_validated_customers(path)
        customer_index = next(
            (index for index, record in enumerate(records) if record.get("id") == customer_id),
            None,
        )
        if customer_index is None:
            raise CustomerNotFoundError("Customer " + str(customer_id) + " does not exist.")

        existing_record = records[customer_index]
        if phone is not _UNSET:
            normalized_phone = normalize_phone(phone)
            for record in records:
                if record.get("id") != customer_id:
                    if normalize_phone(record.get("phone")) == normalized_phone:
                        raise ValidationError(
                            "A customer with this phone number already exists."
                        )

        if measurements is not _UNSET:
            if measurements is None:
                raise ValidationError("Measurements must be an object.")
            existing = existing_record["measurements"]
            if not isinstance(existing, dict):
                raise DataStorageError(
                    "Customer " + str(customer_id) + " has malformed measurements."
                )
            try:
                existing = validate_measurements(existing)
            except ValidationError as exc:
                raise DataStorageError(
                    "Customer " + str(customer_id) + " has malformed measurements."
                ) from exc
            merged = {**existing, **validate_measurements(measurements)}
            updates["measurements"] = merged

        records[customer_index] = {**existing_record, **updates}
        save_data(records, path)
        return records[customer_index].copy()


def deactivate_customer(customer_id: int, customers_file: Optional[Any] = None) -> dict[str, Any]:
    return _set_active(customer_id, False, customers_file)


def reactivate_customer(customer_id: int, customers_file: Optional[Any] = None) -> dict[str, Any]:
    """Reactivate an existing inactive customer without changing its business identity or history."""
    customer_id = validate_id(customer_id, "Customer ID")
    path = _resolve_customer_file(customers_file)
    with record_lock(path):
        records = load_validated_customers(path)
        customer = next(
            (record for record in records if record.get("id") == customer_id),
            None,
        )
        if customer is None:
            raise CustomerNotFoundError("Customer " + str(customer_id) + " does not exist.")
        if customer["active"] is True:
            raise ValidationError("Customer " + str(customer_id) + " is already active.")

        normalized_phone = normalize_phone(customer["phone"])
        for record in records:
            if record.get("id") != customer_id and record["active"] is True:
                if normalize_phone(record["phone"]) == normalized_phone:
                    raise ValidationError(
                        "A customer with this phone number is already active."
                    )

        customer["active"] = True
        customer["updated_at"] = _timestamp()
        save_data(records, path)
        return customer.copy()


def _set_active(customer_id: int, active: bool, customers_file: Optional[Any] = None) -> dict[str, Any]:
    customer_id = validate_id(customer_id, "Customer ID")
    path = _resolve_customer_file(customers_file)
    with record_lock(path):
        records = load_validated_customers(path)
        customer = next(
            (record for record in records if record.get("id") == customer_id),
            None,
        )
        if customer is None:
            raise CustomerNotFoundError("Customer " + str(customer_id) + " does not exist.")
        if not isinstance(customer["active"], bool):
            raise DataStorageError(
                "Customer " + str(customer_id) + " has an invalid active/deactivated status."
            )
        customer["active"] = active
        customer["updated_at"] = _timestamp()
        save_data(records, path)
        return customer.copy()


def delete_customer(
    customer_id: int,
    customers_file: Optional[Any] = None,
    orders_file: Optional[Any] = None,
) -> dict[str, Any]:
    customer_id = validate_id(customer_id, "Customer ID")
    customer_path = _resolve_customer_file(customers_file)
    order_path = ORDERS_FILE if orders_file is None else orders_file
    from .orders import load_validated_orders

    # Acquire in the same customer-first, order-second order as order creation.
    with record_lock(customer_path):
        with record_lock(order_path):
            records = load_validated_customers(customer_path)
            customer_index = next(
                (index for index, record in enumerate(records) if record.get("id") == customer_id),
                None,
            )
            if customer_index is None:
                raise CustomerNotFoundError("Customer " + str(customer_id) + " does not exist.")

            orders = load_validated_orders(order_path, customer_path)
            if any(order.get("customer_id") == customer_id for order in orders):
                raise ValidationError(
                    "Customer " + str(customer_id)
                    + " cannot be deleted because associated orders or financial records exist."
                )

            deleted = records.pop(customer_index)
            save_data(records, customer_path)
            return deleted.copy()


def get_customer_order_history(
    customer_id: int,
    customers_file: Optional[Any] = None,
    orders_file: Optional[Any] = None,
    payments_file: Optional[Any] = None,
) -> dict[str, Any]:
    """Return history with eligible order value/outstanding and all historical payments.

    Total value includes active and delivered orders only. Cancelled orders contribute
    zero to total value and outstanding balance. Total paid includes every historical
    payment, including payments recorded before an order is cancelled.
    """
    from .orders import get_customer_orders
    from .payments import calculate_outstanding_balance, calculate_total_paid

    customer = get_customer_by_id(customer_id, customers_file)
    order_path = ORDERS_FILE if orders_file is None else orders_file
    payment_path = None if payments_file is None else payments_file
    orders = get_customer_orders(customer_id, order_path)
    total_paid = sum(
        (calculate_total_paid(order["order_id"], payment_path) for order in orders),
        start=Decimal("0"),
    )
    outstanding = sum(
        (
            calculate_outstanding_balance(order["order_id"], order_path, payment_path)
            for order in orders
            if order.get("status") != "cancelled"
        ),
        start=Decimal("0"),
    )
    total_amount = sum(
        (Decimal(order["total_amount"]) for order in orders if order.get("status") != "cancelled"),
        start=Decimal("0"),
    )
    return {
        "customer": customer,
        "orders": orders,
        "order_count": len(orders),
        "total_amount": format(total_amount, ".2f"),
        "total_paid": format(total_paid, ".2f"),
        "outstanding_balance": format(outstanding, ".2f"),
        "current_order_statuses": [order.get("status") for order in orders],
        "delivered_orders": sum(order.get("status") == "delivered" for order in orders),
        "cancelled_orders": sum(order.get("status") == "cancelled" for order in orders),
    }
