"""Payment and outstanding-balance business operations."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from .exceptions import (
    InsufficientBalanceError,
    InvalidStatusTransitionError,
    PaymentNotFoundError,
    ValidationError,
)
from .storage import (
    ORDERS_FILE,
    PAYMENTS_FILE,
    append_record,
    load_data,
    read_next_id,
    record_lock,
    save_next_id,
)
from .validators import (
    validate_id,
    validate_money,
    validate_payment_method,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_payments_file(payments_file: Optional[Any] = None) -> Any:
    return PAYMENTS_FILE if payments_file is None else payments_file


def generate_payment_id(payments_file: Optional[Any] = None) -> int:
    path = _resolve_payments_file(payments_file)
    records = load_data(path)
    highest = max(
        (record.get("payment_id", 0) for record in records if isinstance(record.get("payment_id"), int)),
        default=0,
    )
    return max(highest + 1, read_next_id("payment", path))


def record_payment(
    order_id: int,
    amount: Any,
    payment_method: str,
    reference: Optional[str] = None,
    notes: Optional[str] = None,
    payment_date: Optional[Any] = None,
    payments_file: Optional[Any] = None,
    orders_file: Optional[Any] = None,
) -> dict[str, Any]:
    order_id = validate_id(order_id, "Order ID")
    amount_string = validate_money(amount, "Payment amount", allow_zero=False)
    payment_method = validate_payment_method(payment_method)
    if reference is not None:
        if not isinstance(reference, str):
            raise ValidationError("Payment reference must be a string or null.")
        reference = reference.strip() or None
    if notes is not None:
        if not isinstance(notes, str):
            raise ValidationError("Payment notes must be a string or null.")
        notes = notes.strip() or None

    order_path = ORDERS_FILE if orders_file is None else orders_file
    payment_path = _resolve_payments_file(payments_file)
    from .orders import get_order_by_id
    with record_lock(order_path):
        order = get_order_by_id(order_id, order_path)
        if order.get("status") == "cancelled":
            raise InvalidStatusTransitionError(
                "Payments cannot be recorded for a cancelled order."
            )

        outstanding = calculate_outstanding_balance(order_id, order_path, payment_path)
        if outstanding < Decimal("0.00"):
            outstanding = Decimal("0.00")
        amount_decimal = Decimal(amount_string)
        if amount_decimal > outstanding:
            raise InsufficientBalanceError(
                "Payment amount " + amount_string
                + " exceeds the outstanding balance " + format(outstanding, ".2f")
                + "."
            )

        if payment_date is None:
            payment_date_value = _timestamp()
        else:
            if isinstance(payment_date, datetime):
                payment_date_value = payment_date.isoformat()
            elif isinstance(payment_date, str):
                try:
                    datetime.fromisoformat(payment_date.replace("Z", "+00:00"))
                except ValueError:
                    raise ValidationError("Payment date must be an ISO-8601 timestamp.") from None
                payment_date_value = payment_date
            else:
                raise ValidationError("Payment date must be an ISO-8601 timestamp.")

        payment_id = generate_payment_id(payment_path)
        record = {
            "payment_id": payment_id,
            "order_id": order_id,
            "amount": amount_string,
            "payment_method": payment_method,
            "payment_date": payment_date_value,
            "reference": reference,
            "notes": notes,
        }
        append_record(record, payment_path)
        save_next_id("payment", payment_id + 1, payment_path)
        return record


def get_payment_by_id(payment_id: int, payments_file: Optional[Any] = None) -> dict[str, Any]:
    payment_id = validate_id(payment_id, "Payment ID")
    for record in load_data(_resolve_payments_file(payments_file)):
        if record.get("payment_id") == payment_id:
            return record
    raise PaymentNotFoundError("Payment " + str(payment_id) + " does not exist.")


def get_all_payments(payments_file: Optional[Any] = None) -> list[dict[str, Any]]:
    return load_data(_resolve_payments_file(payments_file))


def get_order_payments(order_id: int, payments_file: Optional[Any] = None) -> list[dict[str, Any]]:
    order_id = validate_id(order_id, "Order ID")
    return [
        record for record in get_all_payments(payments_file)
        if record.get("order_id") == order_id
    ]


def calculate_total_paid(order_id: int, payments_file: Optional[Any] = None) -> Decimal:
    order_id = validate_id(order_id, "Order ID")
    total = sum(
        (Decimal(record["amount"]) for record in get_order_payments(order_id, payments_file)),
        start=Decimal("0"),
    )
    return total.quantize(Decimal("0.01"))


def calculate_outstanding_balance(
    order_id: int,
    orders_file: Optional[Any] = None,
    payments_file: Optional[Any] = None,
) -> Decimal:
    from .orders import get_order_by_id
    order = get_order_by_id(order_id, orders_file)
    if order.get("status") == "cancelled":
        return Decimal("0.00")
    total = Decimal(order["total_amount"])
    paid = calculate_total_paid(order_id, payments_file)
    return max(Decimal("0.00"), (total - paid).quantize(Decimal("0.01")))


def get_payment_status(
    order_id: int,
    orders_file: Optional[Any] = None,
    payments_file: Optional[Any] = None,
) -> str:
    from .orders import get_order_by_id
    order = get_order_by_id(order_id, orders_file)
    if order.get("status") == "cancelled":
        return "cancelled"
    paid = calculate_total_paid(order_id, payments_file)
    if paid == 0:
        return "unpaid"
    outstanding = calculate_outstanding_balance(order_id, orders_file, payments_file)
    if outstanding <= 0:
        return "fully_paid"
    return "partially_paid"
