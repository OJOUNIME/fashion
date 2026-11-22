# Fashion Studio Backend

A JSON-backed Python backend for managing fashion-studio customers,
measurements, clothing orders, payments, balances, and business reports.

## Importing

Run commands from the repository root:

```python
from fashion_studio.backend.customers import add_customer
from fashion_studio.backend.orders import create_order
from fashion_studio.backend.payments import record_payment
from fashion_studio.backend.reporting import get_business_report

customer = add_customer(
    name="David",
    phone="08012345678",
    measurements={"chest": 40.5, "waist": 34.0},
)

order = create_order(
    customer_id=customer["id"],
    item_name="Native wear",
    description="Traditional two-piece outfit",
    quantity=1,
    unit_price="80000.00",
    delivery_date="2026-12-01",
)

payment = record_payment(
    order_id=order["order_id"],
    amount="30000.00",
    payment_method="transfer",
)

print(get_business_report())
```

## Public modules

- `customers.py`: customer and measurement operations
- `orders.py`: order creation, updates, status transitions, and cancellation
- `payments.py`: payment records and financial calculations
- `reporting.py`: counts, sales, payments, balances, due and overdue orders
- `storage.py`: isolated JSON persistence with atomic file replacement
- `validators.py`: reusable validation helpers
- `exceptions.py`: meaningful domain and storage exceptions

## Customer and order lifecycle

Customers are created with a unique persistent ID, contact details, optional
email/address, measurements, timestamps, and an active flag. Measurement
updates merge with existing measurements. Customers with associated orders
cannot be deleted, and customer IDs are never reused.

Orders contain a snapshot of the customer's measurements at creation.
Later measurement changes do not alter existing snapshots. Valid status
progression is `pending -> in_progress -> ready -> delivered`, with
cancellation permitted only before delivery. Cancelled and delivered orders
are terminal.

## Customer history and reporting

`get_customer_order_history()` combines customer details, orders, and derived
payment totals. Cancelled orders contribute no sales value or outstanding
balance, but remain in customer history. `get_business_report()` reports
customer activity, order states, sales, payments, balances, and due/overdue
orders. `get_customer_order_statistics()` provides per-customer order counts.

Customer search matches partial names and phone numbers. An integer or numeric
text query matches a customer ID exactly; searches do not use partial customer
IDs.

Phone uniqueness ignores display formatting and compares digits only. For
example, `08012345678`, `080-1234-5678`, and `080 1234 5678` are the same
phone number. The original display value is preserved; no country code is added.

## Integrity and backups

`validate_integrity()` in `storage.py` detects malformed or duplicate IDs,
orphaned orders/payments, and invalid money or quantity values without
silently repairing data. Use `create_backup()` to create a timestamped JSON
backup and `restore_backup()` to validate and atomically restore one.

## Status transitions

`pending` may become `in_progress` or `cancelled`; `in_progress` may become
`ready` or `cancelled`; `ready` may become `delivered`. `delivered` and
`cancelled` are terminal states.

## Financial rules

Amounts are stored as JSON strings with two decimal places. Calculations use
`Decimal`, with `ROUND_HALF_UP` for consistent rounding. Payments cannot exceed
the remaining balance.

## Testing

```bash
python -m unittest discover -s tests -v
```

Tests use temporary directories and do not modify real JSON data.

## JSON storage limitation

JSON storage is suitable for the initial application and uses atomic file
replacement plus cooperative per-file advisory locks. It does not provide
database transactions across customers, orders, and payments, and its locks
are not enforced by external processes. Related updates across separate JSON
files are not globally atomic. For a multi-user deployment, migrate the
persistence layer to a transactional database.