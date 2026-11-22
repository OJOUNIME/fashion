"""Centralised JSON persistence for Fashion Studio records."""

import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Optional

from .exceptions import DataStorageError, DuplicateRecordError


DATA_DIR = Path(__file__).resolve().parent / "data"
CUSTOMERS_FILE = DATA_DIR / "customers.json"
ORDERS_FILE = DATA_DIR / "orders.json"
PAYMENTS_FILE = DATA_DIR / "payments.json"


def _validate_unique_ids(data: list[dict[str, Any]]) -> None:
    for id_field in ("id", "order_id", "payment_id"):
        if id_field == "order_id" and any(
            isinstance(record, dict) and "payment_id" in record
            for record in data
        ):
            continue
        seen: set[int] = set()
        for record in data:
            if not isinstance(record, dict):
                raise DataStorageError("Every JSON record must be an object.")
            value = record.get(id_field)
            if isinstance(value, int) and not isinstance(value, bool):
                if value in seen:
                    raise DuplicateRecordError(
                        "Duplicate " + id_field + " " + str(value) + " found."
                    )
                seen.add(value)


def resolve_path(file_path: Optional[Any] = None, default: Optional[Path] = None) -> Path:
    if file_path is None:
        if default is None:
            raise DataStorageError("A JSON data file path is required.")
        return Path(default)
    path = Path(file_path)
    if not path.is_absolute():
        path = DATA_DIR / path
    return path


def load_data(file_path: Optional[Any] = None, default: Optional[Path] = None) -> list[dict[str, Any]]:
    path = resolve_path(file_path, default)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise DataStorageError("Corrupted JSON data in " + str(path) + ": " + str(exc)) from exc
    except OSError as exc:
        raise DataStorageError("Could not read " + str(path) + ": " + str(exc)) from exc
    if not isinstance(data, list):
        raise DataStorageError("JSON data in " + str(path) + " must contain a list.")
    _validate_unique_ids(data)
    return data


def save_data(data: Any, file_path: Optional[Any] = None, default: Optional[Path] = None) -> None:
    path = resolve_path(file_path, default)
    if not isinstance(data, (list, dict)):
        raise DataStorageError("Data to save must be a JSON list or object.")
    if isinstance(data, list):
        _validate_unique_ids(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(data, handle, indent=4, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except (OSError, TypeError, ValueError) as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise DataStorageError("Could not safely write " + str(path) + ": " + str(exc)) from exc


def append_record(record: dict[str, Any], file_path: Optional[Any] = None, default: Optional[Path] = None) -> dict[str, Any]:
    path = resolve_path(file_path, default)
    with record_lock(path):
        records = load_data(path)
        records.append(deepcopy(record))
        save_data(records, path)
        return deepcopy(record)


def update_record(
    record_id: int,
    updates: dict[str, Any],
    file_path: Optional[Any] = None,
    default: Optional[Path] = None,
    id_field: str = "id",
) -> dict[str, Any]:
    path = resolve_path(file_path, default)
    with record_lock(path):
        records = load_data(path)
        for index, record in enumerate(records):
            if record.get(id_field) == record_id:
                records[index] = {**record, **deepcopy(updates)}
                save_data(records, path)
                return deepcopy(records[index])
    raise KeyError("Record with " + id_field + " " + str(record_id) + " was not found.")


def delete_record(
    record_id: int,
    file_path: Optional[Any] = None,
    default: Optional[Path] = None,
    id_field: str = "id",
) -> dict[str, Any]:
    path = resolve_path(file_path, default)
    with record_lock(path):
        records = load_data(path)
        for index, record in enumerate(records):
            if record.get(id_field) == record_id:
                deleted = records.pop(index)
                save_data(records, path)
                return deepcopy(deleted)
    raise KeyError("Record with " + id_field + " " + str(record_id) + " was not found.")


def read_next_id(entity: str, file_path: Optional[Any] = None) -> int:
    path = resolve_path(file_path)
    if path.name:
        counter_path = path.parent / ("." + path.stem + ".next_id.json")
    else:
        counter_path = DATA_DIR / ("." + entity + ".next_id.json")
    if counter_path.exists():
        try:
            with counter_path.open("r", encoding="utf-8") as handle:
                counter = json.load(handle)
            if isinstance(counter, dict):
                counter = counter.get("next_id")
            if isinstance(counter, int) and not isinstance(counter, bool) and counter > 0:
                return counter
        except (OSError, json.JSONDecodeError):
            pass
    records = load_data(path)
    id_field = "id" if entity == "customer" else ("order_id" if entity == "order" else "payment_id")
    highest = max((record.get(id_field, 0) for record in records if isinstance(record.get(id_field, 0), int)), default=0)
    return highest + 1


def save_next_id(entity: str, next_id: int, file_path: Optional[Any] = None) -> None:
    path = resolve_path(file_path)
    counter_path = path.parent / ("." + path.stem + ".next_id.json")
    save_data({"next_id": next_id}, counter_path)


@contextmanager
def record_lock(file_path: Any):
    """Best-effort advisory lock for cooperating processes on one JSON file."""
    path = resolve_path(file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(path.name + ".lock")
        handle = lock_path.open("a+", encoding="utf-8")
    except OSError as exc:
        raise DataStorageError(
            "Could not open the lock file for " + str(path) + ": " + str(exc)
        ) from exc

    acquired = False
    try:
        try:
            import fcntl
        except ImportError as exc:
            raise DataStorageError(
                "Advisory file locking is unavailable on this platform."
            ) from exc
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            acquired = True
        except (AttributeError, OSError) as exc:
            raise DataStorageError(
                "Could not acquire the lock for " + str(path) + ": " + str(exc)
            ) from exc

        try:
            yield
        finally:
            if acquired:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except (AttributeError, OSError) as exc:
                    raise DataStorageError(
                        "Could not release the lock for " + str(path) + ": " + str(exc)
                    ) from exc
    finally:
        handle.close()


def create_backup(file_path: Optional[Any] = None) -> Path:
    """Create a timestamped copy of a valid JSON data file."""
    path = resolve_path(file_path)
    if not path.exists():
        raise DataStorageError("Cannot back up missing file: " + str(path))
    records = load_data(path)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = path.with_name(path.name + "." + timestamp + ".bak")
    save_data(records, backup_path)
    return backup_path


def restore_backup(backup_path: Any, file_path: Any) -> list[dict[str, Any]]:
    """Validate a backup, then atomically restore it."""
    backup = resolve_path(backup_path)
    target = resolve_path(file_path)
    records = load_data(backup)
    save_data(records, target)
    return deepcopy(records)


def validate_integrity(
    customers_file: Optional[Any] = None,
    orders_file: Optional[Any] = None,
    payments_file: Optional[Any] = None,
) -> dict[str, Any]:
    """Return relationship and data-integrity findings without repairing records."""
    customer_path = resolve_path(customers_file, CUSTOMERS_FILE)
    order_path = resolve_path(orders_file, ORDERS_FILE)
    payment_path = resolve_path(payments_file, PAYMENTS_FILE)
    issues: list[str] = []

    customers = load_data(customer_path)
    orders = load_data(order_path)
    payments = load_data(payment_path)

    customer_ids = [record.get("id") for record in customers]
    order_ids = [record.get("order_id") for record in orders]
    payment_ids = [record.get("payment_id") for record in payments]
    for field_name, values in (
        ("customer", customer_ids),
        ("order", order_ids),
        ("payment", payment_ids),
    ):
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in values):
            issues.append("Malformed or missing " + field_name + " ID.")
        if len(values) != len(set(values)):
            issues.append("Duplicate " + field_name + " IDs.")

    from .validators import (
        ValidationError,
        normalize_phone,
        validate_customer_name,
        validate_complete_iso_datetime,
        validate_email,
        validate_measurements,
        validate_phone,
    )

    customer_phones: set[str] = set()
    for index, customer in enumerate(customers):
        label = "Customer " + str(customer.get("id", index + 1))
        required_fields = (
            "id", "name", "phone", "email", "address", "measurements",
            "active", "created_at", "updated_at",
        )
        missing = [field for field in required_fields if field not in customer]
        if missing:
            issues.append(label + " is missing required fields: " + ", ".join(missing) + ".")

        try:
            validate_customer_name(customer.get("name"))
        except ValidationError:
            issues.append(label + " has an invalid name.")

        phone = customer.get("phone")
        try:
            validated_phone = validate_phone(phone)
            normalized_phone = normalize_phone(validated_phone)
            if normalized_phone in customer_phones:
                issues.append("Duplicate customer phone number: " + validated_phone + ".")
            customer_phones.add(normalized_phone)
        except ValidationError:
            issues.append(label + " has an invalid phone number.")

        try:
            validate_email(customer.get("email"))
        except ValidationError:
            issues.append(label + " has an invalid email.")

        address = customer.get("address")
        if address is not None and not isinstance(address, str):
            issues.append(label + " has an invalid address.")

        measurements = customer.get("measurements")
        if not isinstance(measurements, dict):
            issues.append(label + " has malformed measurements.")
        else:
            try:
                validate_measurements(measurements)
            except ValidationError:
                issues.append(label + " has malformed measurements.")

        if not isinstance(customer.get("active"), bool):
            issues.append(label + " has an invalid active/deactivated status.")
        for timestamp_field in ("created_at", "updated_at"):
            timestamp = customer.get(timestamp_field)
            if not isinstance(timestamp, str):
                issues.append(label + " has an invalid " + timestamp_field + " value.")
            else:
                try:
                    validate_complete_iso_datetime(timestamp, timestamp_field)
                except ValidationError:
                    issues.append(label + " has an invalid " + timestamp_field + " value.")

    valid_customer_ids = {
        value for value in customer_ids
        if isinstance(value, int) and not isinstance(value, bool)
    }
    for order in orders:
        if order.get("customer_id") not in valid_customer_ids:
            issues.append(
                "Order " + str(order.get("order_id")) + " references a nonexistent customer."
            )
        try:
            quantity = order.get("quantity")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                raise ValueError
            total = Decimal(str(order.get("total_amount")))
            if not total.is_finite() or total < 0:
                raise ValueError
        except (InvalidOperation, TypeError, ValueError):
            issues.append("Order " + str(order.get("order_id")) + " has invalid monetary or quantity data.")

    valid_order_ids = {
        value for value in order_ids
        if isinstance(value, int) and not isinstance(value, bool)
    }
    for payment in payments:
        if payment.get("order_id") not in valid_order_ids:
            issues.append(
                "Payment " + str(payment.get("payment_id")) + " references a nonexistent order."
            )
        try:
            amount = Decimal(str(payment.get("amount")))
            if not amount.is_finite() or amount <= 0:
                raise ValueError
        except (InvalidOperation, TypeError, ValueError):
            issues.append("Payment " + str(payment.get("payment_id")) + " has an invalid amount.")

    return {"valid": not issues, "issues": issues}
