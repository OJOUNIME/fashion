"""Reusable input and data validation helpers."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional

from .exceptions import ValidationError


VALID_ORDER_STATUSES = {"pending", "in_progress", "ready", "delivered", "cancelled"}
VALID_PAYMENT_METHODS = {"cash", "transfer", "card", "other"}
VALID_MEASUREMENTS = {
    "chest", "waist", "shoulder", "sleeve",
    "neck", "hip", "inseam", "length",
}


def require_fields(data: Mapping[str, Any], fields: Iterable[str]) -> None:
    missing = [field for field in fields if field not in data or data[field] is None]
    if missing:
        raise ValidationError("Missing required fields: " + ", ".join(missing))


def validate_customer_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("Customer name must not be empty.")
    return name.strip()


def validate_phone(phone: Any) -> str:
    if not isinstance(phone, str):
        raise ValidationError("Phone number must be a string.")
    cleaned = phone.strip()
    if not cleaned:
        raise ValidationError("Phone number must contain digits and valid phone punctuation only.")
    if any(character not in "0123456789- . " for character in cleaned):
        raise ValidationError("Phone number must contain digits and valid phone punctuation only.")
    if cleaned[0] in "- ." or cleaned[-1] in "- .":
        raise ValidationError("Phone number has invalid formatting.")
    for separator in ("--", "..", "  "):
        if separator in cleaned:
            raise ValidationError("Phone number has invalid formatting.")
    if any(
        cleaned[index] in "- . " and cleaned[index + 1] in "- . "
        for index in range(len(cleaned) - 1)
    ):
        raise ValidationError("Phone number has invalid formatting.")
    digits = "".join(character for character in cleaned if character.isdigit())
    if not 7 <= len(digits) <= 15:
        raise ValidationError("Phone number must contain between 7 and 15 digits.")
    return cleaned


def normalize_phone(phone: Any) -> str:
    """Normalize accepted phone display formats for uniqueness comparison.

    Comparison removes formatting punctuation and keeps only digits. It does not
    add a country code or otherwise change the user's number.
    """
    cleaned = validate_phone(phone)
    return "".join(character for character in cleaned if character.isdigit())


def validate_complete_iso_datetime(value: Any, field_name: str = "Timestamp") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(field_name + " must be a valid complete ISO 8601 datetime.")
    timestamp = value.strip()
    if " " in timestamp:
        raise ValidationError(field_name + " must be a valid complete ISO 8601 datetime.")
    if "T" not in timestamp:
        raise ValidationError(field_name + " must be a valid complete ISO 8601 datetime.")
    try:
        parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp)
    except ValueError:
        raise ValidationError(field_name + " must be a valid complete ISO 8601 datetime.") from None
    if parsed.isoformat().split("T", 1)[0] != timestamp.split("T", 1)[0]:
        raise ValidationError(field_name + " must be a valid complete ISO 8601 datetime.")
    return timestamp


def validate_email(email: Optional[str]) -> Optional[str]:
    if email is None:
        return None
    if not isinstance(email, str):
        raise ValidationError("Email must be a string or null.")
    email = email.strip()
    if not email:
        return None
    if any(character.isspace() for character in email):
        raise ValidationError("Email address is invalid.")
    if email.count("@") != 1:
        raise ValidationError("Email address is invalid.")
    local, domain = email.rsplit("@", 1)
    if not local or not domain or local.startswith(".") or local.endswith(".") or ".." in local:
        raise ValidationError("Email address is invalid.")
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        raise ValidationError("Email address is invalid.")
    labels = domain.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        raise ValidationError("Email address is invalid.")
    if any(not all(character.isalnum() or character == "-" for character in label) for label in labels):
        raise ValidationError("Email address is invalid.")
    if any(label.startswith("-") or label.endswith("-") for label in labels):
        raise ValidationError("Email address is invalid.")
    return email


def validate_measurements(measurements: Optional[Mapping[str, Any]]) -> dict[str, float]:
    if measurements is None:
        return {}
    if not isinstance(measurements, Mapping):
        raise ValidationError("Measurements must be an object.")

    validated: dict[str, float] = {}
    for name, value in measurements.items():
        if name not in VALID_MEASUREMENTS:
            raise ValidationError("Unknown measurement: " + str(name))
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
            raise ValidationError("Measurement values must be numeric.")
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValidationError("Measurement values must be numeric.") from None
        if not amount.is_finite() or amount <= 0:
            raise ValidationError("Measurement values must be positive.")
        validated[name] = float(amount)
    return validated


def validate_description(description: Any) -> str:
    if not isinstance(description, str) or not description.strip():
        raise ValidationError("Order description must not be empty.")
    return description.strip()


def parse_date(value: Any, field_name: str = "Date") -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(field_name + " must be a valid ISO date.")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValidationError(field_name + " must be a valid ISO date (YYYY-MM-DD).") from None


def validate_date(value: Any, field_name: str = "Date") -> str:
    return parse_date(value, field_name).isoformat()


def parse_money(value: Any, field_name: str = "Amount") -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValidationError(field_name + " must be a valid monetary amount.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError(field_name + " must be a valid monetary amount.") from None
    if not amount.is_finite():
        raise ValidationError(field_name + " must be a valid monetary amount.")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_money(value: Any, field_name: str = "Amount", *, allow_zero: bool = True) -> str:
    amount = parse_money(value, field_name)
    if amount < 0 or (not allow_zero and amount <= 0):
        raise ValidationError(field_name + " must be greater than zero.")
    return format(amount, ".2f")


def validate_id(value: Any, field_name: str = "ID") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(field_name + " must be a positive integer.")
    return value


def validate_quantity(quantity: Any) -> int:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValidationError("Quantity must be a positive integer.")
    return quantity


def validate_order_status(status: Any) -> str:
    if status not in VALID_ORDER_STATUSES:
        raise ValidationError("Invalid order status: " + str(status))
    return status


def validate_payment_method(method: Any) -> str:
    if method not in VALID_PAYMENT_METHODS:
        raise ValidationError("Invalid payment method: " + str(method))
    return method
