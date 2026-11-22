"""Tests for customer and measurement operations."""

import tempfile
import unittest
from pathlib import Path

from fashion_studio.backend.customers import (
    add_customer,
    delete_customer,
    get_customer_by_id,
    search_customers,
    update_customer,
)
from fashion_studio.backend.exceptions import CustomerNotFoundError, ValidationError
from fashion_studio.backend.orders import create_order
from fashion_studio.backend.customers import deactivate_customer, get_customer_order_history
from fashion_studio.backend.payments import record_payment


class CustomerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.customer_file = Path(self.temp_dir.name) / "customers.json"
        self.order_file = Path(self.temp_dir.name) / "orders.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_customer(self, name="David", phone="08012345678"):
        return add_customer(
            name,
            phone,
            {"chest": 40.5, "waist": 34},
            customers_file=self.customer_file,
        )

    def test_add_and_retrieve_customer(self):
        customer = self.add_customer()
        self.assertEqual(get_customer_by_id(customer["id"], self.customer_file)["name"], "David")

    def test_reject_empty_name_and_invalid_measurement(self):
        with self.assertRaises(ValidationError):
            add_customer("", "08012345678", customers_file=self.customer_file)
        with self.assertRaises(ValidationError):
            add_customer("David", "08012345678", {"chest": -1}, customers_file=self.customer_file)

    def test_search_update_and_measurement_update(self):
        customer = self.add_customer()
        self.assertEqual(search_customers("dav", self.customer_file)[0]["id"], customer["id"])
        updated = update_customer(
            customer["id"],
            name="David King",
            measurements={"chest": 41.25},
            customers_file=self.customer_file,
        )
        self.assertEqual(updated["name"], "David King")
        self.assertEqual(updated["measurements"]["chest"], 41.25)

    def test_missing_customer_and_delete(self):
        with self.assertRaises(CustomerNotFoundError):
            get_customer_by_id(999, self.customer_file)
        customer = self.add_customer()
        deleted = delete_customer(customer["id"], self.customer_file, self.order_file)
        self.assertEqual(deleted["id"], customer["id"])

    def test_ids_are_not_reused_and_associated_orders_block_delete(self):
        first = self.add_customer("First", "08011111111")
        second = self.add_customer("Second", "08022222222")
        order = create_order(
            first["id"],
            "Suit",
            "Formal suit",
            1,
            "50000.00",
            "2026-12-01",
            orders_file=self.order_file,
            customers_file=self.customer_file,
        )
        with self.assertRaises(ValidationError):
            delete_customer(first["id"], self.customer_file, self.order_file)
        delete_customer(second["id"], self.customer_file, self.order_file)
        third = self.add_customer("Third", "08033333333")
        self.assertGreater(third["id"], second["id"])
        self.assertNotEqual(third["id"], first["id"])

    def test_search_by_phone_and_id_and_duplicate_phone_rejected(self):
        customer = self.add_customer("David", "08012345678")
        self.assertEqual(search_customers("080123", self.customer_file)[0]["id"], customer["id"])
        self.assertEqual(search_customers(customer["id"], self.customer_file)[0]["id"], customer["id"])
        with self.assertRaises(ValidationError):
            add_customer("Other", "08012345678", customers_file=self.customer_file)
        with self.assertRaises(ValidationError):
            add_customer("Invalid", "phone08012345678", customers_file=self.customer_file)

    def test_unrelated_update_fields_and_optional_fields_are_preserved_or_cleared(self):
        customer = self.add_customer()
        customer = update_customer(
            customer["id"],
            email="david@example.com",
            address="Lagos",
            customers_file=self.customer_file,
        )
        updated = update_customer(
            customer["id"],
            name="David Updated",
            email=None,
            address=None,
            customers_file=self.customer_file,
        )
        self.assertEqual(updated["name"], "David Updated")
        self.assertIsNone(updated["email"])
        self.assertIsNone(updated["address"])
        self.assertEqual(updated["phone"], "08012345678")

    def test_history_and_deactivation(self):
        customer = self.add_customer()
        order = create_order(
            customer["id"],
            "Suit",
            "Suit",
            1,
            "500.00",
            "2026-12-01",
            orders_file=self.order_file,
            customers_file=self.customer_file,
        )
        payment_file = Path(self.temp_dir.name) / "payments.json"
        record_payment(
            order["order_id"],
            "200.00",
            "cash",
            payments_file=payment_file,
            orders_file=self.order_file,
        )
        history = get_customer_order_history(
            customer["id"],
            customers_file=self.customer_file,
            orders_file=self.order_file,
            payments_file=payment_file,
        )
        self.assertEqual(history["order_count"], 1)
        self.assertEqual(history["total_paid"], "200.00")
        self.assertEqual(history["outstanding_balance"], "300.00")
        deactivated = deactivate_customer(customer["id"], self.customer_file)
        self.assertFalse(deactivated["active"])


if __name__ == "__main__":
    unittest.main()
