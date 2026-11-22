"""Customer and customer-body-measurement business operations."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .exceptions import (
    CustomerNotFoundError,
    ValidationError,
)
from .storage import (
    CUSTOMERS_FILE,
    ORDERS_FILE,
    append_record,
    delete_record,
    load_data,
    read_next_id,
    save_next_id,
    update_record,
)
from .validators import (
    validate_customer_name,
    validate_email,
    validate_id,
    validate_measurements,
    validate_phone,
)

_UNSET = object()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_customer_file(customers_file: Optional[Any] = None) -> Any:
    return CUSTOMERS_FILE if customers_file is None else customers_file


def generate_customer_id(customers_file: Optional[Any] = None) -> int:
    path = _resolve_customer_file(customers_file)
    records = load_data(path)
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
    for record in load_data(path):
        if record.get("phone") == phone:
            raise ValidationError("A customer with this phone number already exists.")
    customer_id = generate_customer_id(path)
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
    append_record(record, path)
    save_next_id("customer", customer_id + 1, path)
    return record


def get_customer_by_id(customer_id: int, customers_file: Optional[Any] = None) -> dict[str, Any]:
    customer_id = validate_id(customer_id, "Customer ID")
    path = _resolve_customer_file(customers_file)
    for record in load_data(path):
        if record.get("id") == customer_id:
            return record
    raise CustomerNotFoundError("Customer " + str(customer_id) + " does not exist.")


def get_all_customers(customers_file: Optional[Any] = None) -> list[dict[str, Any]]:
    return load_data(_resolve_customer_file(customers_file))


def search_customers(query: Any, customers_file: Optional[Any] = None) -> list[dict[str, Any]]:
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
    get_customer_by_id(customer_id, customers_file)
    updates: dict[str, Any] = {}
    if name is not _UNSET:
        updates["name"] = validate_customer_name(name)
    if phone is not _UNSET:
        phone = validate_phone(phone)
        for record in load_data(_resolve_customer_file(customers_file)):
            if record.get("phone") == phone and record.get("id") != customer_id:
                raise ValidationError("A customer with this phone number already exists.")
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
    if measurements is not _UNSET:
        if measurements is None:
            raise ValidationError("Measurements must be an object.")
        existing = get_customer_by_id(customer_id, customers_file)["measurements"]
        merged = {**existing, **validate_measurements(measurements)}
        updates["measurements"] = merged
    updates["updated_at"] = _timestamp()
    try:
        return update_record(customer_id, updates, _resolve_customer_file(customers_file), id_field="id")
    except KeyError:
        raise CustomerNotFoundError("Customer " + str(customer_id) + " does not exist.") from None


def deactivate_customer(customer_id: int, customers_file: Optional[Any] = None) -> dict[str, Any]:
    return _set_active(customer_id, False, customers_file)


def _set_active(customer_id: int, active: bool, customers_file: Optional[Any] = None) -> dict[str, Any]:
    customer_id = validate_id(customer_id, "Customer ID")
    get_customer_by_id(customer_id, customers_file)
    return update_record(
        customer_id,
        {"active": active, "updated_at": _timestamp()},
        _resolve_customer_file(customers_file),
        id_field="id",
    )


def delete_customer(
    customer_id: int,
    customers_file: Optional[Any] = None,
    orders_file: Optional[Any] = None,
) -> dict[str, Any]:
    customer_id = validate_id(customer_id, "Customer ID")
    get_customer_by_id(customer_id, customers_file)
    order_path = ORDERS_FILE if orders_file is None else orders_file
    if any(order.get("customer_id") == customer_id for order in load_data(order_path)):
        raise ValidationError(
            "Customer " + str(customer_id)
            + " cannot be deleted because associated orders or financial records exist."
        )
    try:
        return delete_record(customer_id, _resolve_customer_file(customers_file), id_field="id")
    except KeyError:
        raise CustomerNotFoundError("Customer " + str(customer_id) + " does not exist.") from None


def get_customer_order_history(
    customer_id: int,
    customers_file: Optional[Any] = None,
    orders_file: Optional[Any] = None,
    payments_file: Optional[Any] = None,
) -> dict[str, Any]:
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
