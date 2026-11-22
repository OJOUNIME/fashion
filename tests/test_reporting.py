"""Tests for derived business reports."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from fashion_studio.backend.customers import add_customer
from fashion_studio.backend.orders import create_order
from fashion_studio.backend.payments import record_payment
from fashion_studio.backend.reporting import (
    get_orders_due_soon,
    get_overdue_orders,
    get_business_report,
)
from fashion_studio.backend.orders import cancel_order
from fashion_studio.backend.payments import (
    calculate_outstanding_balance,
    get_payment_status,
)


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.customer_file = root / "customers.json"
        self.order_file = root / "orders.json"
        self.payment_file = root / "payments.json"
        customer = add_customer("David", "08012345678", customers_file=self.customer_file)
        self.order = create_order(
            customer["id"],
            "Dress",
            "Ankara dress",
            1,
            "25000.00",
            "2026-11-20",
            order_date="2026-11-01",
            orders_file=self.order_file,
            customers_file=self.customer_file,
        )
        record_payment(
            self.order["order_id"],
            "10000.00",
            "transfer",
            payments_file=self.payment_file,
            orders_file=self.order_file,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_report_totals_and_overdue(self):
        report = get_business_report(
            self.customer_file,
            self.order_file,
            self.payment_file,
        )
        self.assertEqual(report["total_customers"], 1)
        self.assertEqual(report["total_orders"], 1)
        self.assertEqual(report["pending_orders"], 1)
        self.assertEqual(report["total_sales_value"], "25000.00")
        self.assertEqual(report["total_payments_received"], "10000.00")
        self.assertEqual(report["total_outstanding_balances"], "15000.00")
        overdue = get_overdue_orders(date(2026, 11, 22), self.order_file)
        self.assertEqual(len(overdue), 1)
        due = get_orders_due_soon(7, date(2026, 11, 20), self.order_file)
        self.assertEqual(len(due), 1)

    def test_cancelled_order_is_not_sales_or_outstanding(self):
        cancel_order(self.order["order_id"], self.order_file)
        report = get_business_report(
            self.customer_file,
            self.order_file,
            self.payment_file,
        )
        self.assertEqual(report["total_sales_value"], "0.00")
        self.assertEqual(report["total_outstanding_balances"], "0.00")
        self.assertEqual(str(calculate_outstanding_balance(
            self.order["order_id"], self.order_file, self.payment_file,
        )), "0.00")
        self.assertEqual(get_payment_status(
            self.order["order_id"], self.order_file, self.payment_file,
        ), "cancelled")


if __name__ == "__main__":
    unittest.main()
