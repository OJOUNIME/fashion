"""Tests for clothing-order operations."""

import tempfile
import unittest
from pathlib import Path

from fashion_studio.backend.customers import add_customer
from fashion_studio.backend.exceptions import InvalidStatusTransitionError, OrderNotFoundError
from fashion_studio.backend.exceptions import CustomerNotFoundError, ValidationError
from fashion_studio.backend.orders import get_order_by_id
from fashion_studio.backend.customers import update_customer
from fashion_studio.backend.orders import (
    cancel_order,
    create_order,
    get_customer_orders,
    update_order_status,
)


class OrderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.customer_file = Path(self.temp_dir.name) / "customers.json"
        self.order_file = Path(self.temp_dir.name) / "orders.json"
        self.customer = add_customer(
            "David",
            "08012345678",
            {"chest": 40.5},
            customers_file=self.customer_file,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_and_retrieve_customer_orders(self):
        order = create_order(
            self.customer["id"],
            item_name="Native wear",
            quantity=2,
            unit_price="80000.00",
            delivery_date="2026-12-01",
            orders_file=self.order_file,
            customers_file=self.customer_file,
        )
        self.assertEqual(order["total_amount"], "160000.00")
        self.assertEqual(order["measurements_snapshot"]["chest"], 40.5)
        self.assertEqual(len(get_customer_orders(self.customer["id"], self.order_file)), 1)

    def test_status_transitions_and_cancel(self):
        order = create_order(
            self.customer["id"], "Shirt", "Blue shirt", 1, "5000.00",
            "2026-12-01", orders_file=self.order_file, customers_file=self.customer_file,
        )
        in_progress = update_order_status(order["order_id"], "in_progress", self.order_file)
        self.assertEqual(in_progress["status"], "in_progress")
        ready = update_order_status(order["order_id"], "ready", self.order_file)
        self.assertEqual(ready["status"], "ready")
        delivered = update_order_status(order["order_id"], "delivered", self.order_file)
        self.assertEqual(delivered["status"], "delivered")
        with self.assertRaises(InvalidStatusTransitionError):
            cancel_order(order["order_id"], self.order_file)

    def test_existing_customer_required_valid_dates_and_snapshot_preserved(self):
        with self.assertRaises(CustomerNotFoundError):
            create_order(
                999,
                "Suit",
                "Suit",
                1,
                "100.00",
                "2026-12-01",
                orders_file=self.order_file,
                customers_file=self.customer_file,
            )
        with self.assertRaises(ValidationError):
            create_order(
                self.customer["id"],
                "Suit",
                "Suit",
                1,
                "100.00",
                "2026-10-01",
                order_date="2026-11-01",
                orders_file=self.order_file,
                customers_file=self.customer_file,
            )
        with self.assertRaises(ValidationError):
            create_order(
                self.customer["id"],
                "Suit",
                "Suit",
                1,
                "100.00",
                "2026-12-01-extra",
                orders_file=self.order_file,
                customers_file=self.customer_file,
            )
        order = create_order(
            self.customer["id"],
            "Suit",
            "Suit",
            1,
            "100.00",
            "2026-12-01",
            orders_file=self.order_file,
            customers_file=self.customer_file,
        )
        update_customer(
            self.customer["id"],
            measurements={"chest": 45.0},
            customers_file=self.customer_file,
        )
        self.assertEqual(
            get_order_by_id(order["order_id"], self.order_file)["measurements_snapshot"]["chest"],
            40.5,
        )
        second = create_order(
            self.customer["id"],
            "Dress",
            "Dress",
            1,
            "200.00",
            "2026-12-02",
            orders_file=self.order_file,
            customers_file=self.customer_file,
        )
        self.assertEqual(second["measurements_snapshot"]["chest"], 45.0)


if __name__ == "__main__":
    unittest.main()
