"""Tests for payment and balance operations."""

import tempfile
import unittest
from pathlib import Path

from fashion_studio.backend.customers import add_customer
from fashion_studio.backend.orders import create_order
from fashion_studio.backend.payments import (
    calculate_outstanding_balance,
    calculate_total_paid,
    get_payment_status,
    record_payment,
)
from fashion_studio.backend.exceptions import InsufficientBalanceError
from fashion_studio.backend.exceptions import (
    InvalidStatusTransitionError,
    OrderNotFoundError,
    PaymentNotFoundError,
)
from fashion_studio.backend.orders import cancel_order
from fashion_studio.backend.payments import get_all_payments, get_payment_by_id
from fashion_studio.backend.exceptions import ValidationError


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.customer_file = root / "customers.json"
        self.order_file = root / "orders.json"
        self.payment_file = root / "payments.json"
        customer = add_customer("David", "08012345678", customers_file=self.customer_file)
        self.order = create_order(
            customer["id"],
            "Suit",
            "Black suit",
            1,
            "10000.00",
            "2026-12-01",
            orders_file=self.order_file,
            customers_file=self.customer_file,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_payment_calculations_and_status(self):
        record_payment(
            self.order["order_id"], "3000.00", "transfer",
            payments_file=self.payment_file, orders_file=self.order_file,
        )
        self.assertEqual(str(calculate_total_paid(self.order["order_id"], self.payment_file)), "3000.00")
        self.assertEqual(str(calculate_outstanding_balance(
            self.order["order_id"], self.order_file, self.payment_file,
        )), "7000.00")
        self.assertEqual(get_payment_status(
            self.order["order_id"], self.order_file, self.payment_file,
        ), "partially_paid")
        record_payment(
            self.order["order_id"], "7000.00", "cash",
            payments_file=self.payment_file, orders_file=self.order_file,
        )
        self.assertEqual(get_payment_status(
            self.order["order_id"], self.order_file, self.payment_file,
        ), "fully_paid")

    def test_reject_overpayment(self):
        with self.assertRaises(InsufficientBalanceError):
            record_payment(
                self.order["order_id"], "10001.00", "card",
                payments_file=self.payment_file, orders_file=self.order_file,
            )

    def test_unpaid_status_nonexistent_order_and_multiple_history(self):
        self.assertEqual(get_payment_status(
            self.order["order_id"], self.order_file, self.payment_file,
        ), "unpaid")
        first = record_payment(
            self.order["order_id"], "2000.00", "cash",
            payments_file=self.payment_file, orders_file=self.order_file,
        )
        second = record_payment(
            self.order["order_id"], "3000.00", "card",
            payments_file=self.payment_file, orders_file=self.order_file,
        )
        payments = get_all_payments(self.payment_file)
        self.assertEqual(len(payments), 2)
        self.assertEqual(
            {payment["payment_id"] for payment in payments},
            {first["payment_id"], second["payment_id"]},
        )
        self.assertEqual(get_payment_by_id(first["payment_id"], self.payment_file)["amount"], "2000.00")
        with self.assertRaises(PaymentNotFoundError):
            get_payment_by_id(999, self.payment_file)

    def test_cancelled_order_rejects_payment_and_zero_amount_rejected(self):
        cancel_order(self.order["order_id"], self.order_file)
        with self.assertRaises(InvalidStatusTransitionError):
            record_payment(
                self.order["order_id"], "100.00", "transfer",
                payments_file=self.payment_file, orders_file=self.order_file,
            )
        with self.assertRaises(ValidationError):
            record_payment(
                self.order["order_id"], "0.00", "cash",
                payments_file=self.payment_file, orders_file=self.order_file,
            )

    def test_payment_status_rejects_nonexistent_order(self):
        with self.assertRaises(OrderNotFoundError):
            get_payment_status(999, self.order_file, self.payment_file)


if __name__ == "__main__":
    unittest.main()
