"""Tests for clothing-order operations."""

import tempfile
import unittest
import json
import multiprocessing
import sys
from pathlib import Path

import fcntl

from fashion_studio.backend.customers import add_customer
from fashion_studio.backend.exceptions import InvalidStatusTransitionError, OrderNotFoundError
from fashion_studio.backend.exceptions import DataStorageError
from fashion_studio.backend.exceptions import CustomerNotFoundError, ValidationError
from fashion_studio.backend.orders import get_order_by_id
from fashion_studio.backend.customers import (
    update_customer,
    deactivate_customer,
)
from fashion_studio.backend.orders import (
    cancel_order,
    create_order,
    get_customer_orders,
    update_order,
    update_order_status,
    load_validated_orders,
)
from fashion_studio.backend.storage import save_data


def _process_create_order(customer_file, order_file, index, queue):
    try:
        from fashion_studio.backend.orders import create_order
        record = create_order(
            1,
            "Concurrent " + str(index),
            "Concurrent " + str(index),
            1,
            "10.00",
            "2026-12-31",
            orders_file=str(order_file),
            customers_file=str(customer_file),
        )
        queue.put(("ok", record["order_id"]))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def _process_delete_customer(customer_file, order_file, queue):
    try:
        from fashion_studio.backend.customers import delete_customer
        deleted = delete_customer(1, customer_file, order_file)
        queue.put(("deleted", deleted["id"]))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


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

    def test_all_valid_status_transitions_and_cancelled_timestamp(self):
        valid_paths = (
            ("pending", "in_progress"),
            ("pending", "cancelled"),
            ("in_progress", "ready"),
            ("in_progress", "cancelled"),
            ("ready", "delivered"),
            ("ready", "cancelled"),
        )
        for start_status, final_status in valid_paths:
            with self.subTest(start=start_status, final=final_status):
                order = create_order(
                    self.customer["id"],
                    "Shirt",
                    "Shirt",
                    1,
                    "100.00",
                    "2026-12-01",
                    orders_file=self.order_file,
                    customers_file=self.customer_file,
                )
                if start_status == "in_progress":
                    update_order_status(order["order_id"], "in_progress", self.order_file)
                elif start_status == "ready":
                    update_order_status(order["order_id"], "in_progress", self.order_file)
                    update_order_status(order["order_id"], "ready", self.order_file)
                updated = update_order_status(order["order_id"], final_status, self.order_file)
                self.assertEqual(updated["status"], final_status)
                if final_status == "cancelled":
                    self.assertIn("cancelled_at", updated)

    def test_rejected_status_transitions(self):
        order = create_order(
            self.customer["id"], "Shirt", "Shirt", 1, "100.00",
            "2026-12-01", orders_file=self.order_file, customers_file=self.customer_file,
        )
        rejected = (
            "ready",
            "delivered",
            "cancelled",
        )
        for status in rejected:
            with self.subTest(status=status):
                if status == "cancelled":
                    cancel_order(order["order_id"], self.order_file)
                    with self.assertRaises(InvalidStatusTransitionError):
                        update_order_status(order["order_id"], "pending", self.order_file)
                    break
                with self.assertRaises(InvalidStatusTransitionError):
                    update_order_status(order["order_id"], status, self.order_file)

        cancelled = create_order(
            self.customer["id"], "Dress", "Dress", 1, "100.00",
            "2026-12-01", orders_file=self.order_file, customers_file=self.customer_file,
        )
        cancel_order(cancelled["order_id"], self.order_file)
        for status in ("pending", "in_progress", "ready", "delivered"):
            with self.subTest(reopen=status):
                with self.assertRaises(InvalidStatusTransitionError):
                    update_order_status(cancelled["order_id"], status, self.order_file)

        delivered = create_order(
            self.customer["id"], "Suit", "Suit", 1, "100.00",
            "2026-12-01", orders_file=self.order_file, customers_file=self.customer_file,
        )
        update_order_status(delivered["order_id"], "in_progress", self.order_file)
        update_order_status(delivered["order_id"], "ready", self.order_file)
        update_order_status(delivered["order_id"], "delivered", self.order_file)
        for status in ("pending", "in_progress", "ready", "cancelled"):
            with self.subTest(reverse=status):
                with self.assertRaises(InvalidStatusTransitionError):
                    update_order_status(delivered["order_id"], status, self.order_file)

    def test_combined_cancellation_rejects_business_details_and_notes(self):
        order = create_order(
            self.customer["id"], "Shirt", "Blue shirt", 1, "100.00",
            "2026-12-01", orders_file=self.order_file, customers_file=self.customer_file,
        )
        with self.assertRaises(InvalidStatusTransitionError):
            update_order(
                order["order_id"], quantity=2, status="cancelled",
                orders_file=self.order_file, customers_file=self.customer_file,
            )
        with self.assertRaises(InvalidStatusTransitionError):
            update_order(
                order["order_id"], description="Red shirt", status="cancelled",
                orders_file=self.order_file, customers_file=self.customer_file,
            )

        cancelled = create_order(
            self.customer["id"], "Dress", "Dress", 1, "100.00",
            "2026-12-01", orders_file=self.order_file, customers_file=self.customer_file,
        )
        cancel_order(cancelled["order_id"], self.order_file)
        with self.assertRaises(InvalidStatusTransitionError):
            update_order(cancelled["order_id"], notes="changed", orders_file=self.order_file)
        with self.assertRaises(InvalidStatusTransitionError):
            update_order(cancelled["order_id"], status="pending", orders_file=self.order_file)

        delivered = create_order(
            self.customer["id"], "Suit", "Suit", 1, "100.00",
            "2026-12-01", orders_file=self.order_file, customers_file=self.customer_file,
        )
        update_order_status(delivered["order_id"], "in_progress", self.order_file)
        update_order_status(delivered["order_id"], "ready", self.order_file)
        update_order_status(delivered["order_id"], "delivered", self.order_file)
        with self.assertRaises(InvalidStatusTransitionError):
            update_order(delivered["order_id"], notes="changed", orders_file=self.order_file)
        with self.assertRaises(InvalidStatusTransitionError):
            update_order(delivered["order_id"], status="ready", orders_file=self.order_file)

    def test_update_persists_fields_and_cancelled_at_is_not_overwritten(self):
        order = create_order(
            self.customer["id"], "Shirt", "Blue shirt", 2, "100.00",
            "2026-12-01", orders_file=self.order_file, customers_file=self.customer_file,
        )
        updated = update_order(
            order["order_id"], description="Updated shirt", quantity=3,
            delivery_date="2026-12-05", notes="Call customer",
            orders_file=self.order_file, customers_file=self.customer_file,
        )
        self.assertEqual(updated["description"], "Updated shirt")
        self.assertEqual(updated["quantity"], 3)
        self.assertEqual(updated["total_amount"], "300.00")
        self.assertEqual(updated["delivery_date"], "2026-12-05")
        stored = get_order_by_id(order["order_id"], self.order_file)
        self.assertEqual(stored["notes"], "Call customer")

        cancelled = cancel_order(order["order_id"], self.order_file)
        cancellation_timestamp = cancelled["cancelled_at"]
        ordinary = update_order(order["order_id"], orders_file=self.order_file)
        self.assertEqual(ordinary["status"], "cancelled")
        self.assertEqual(ordinary["cancelled_at"], cancellation_timestamp)

    def test_inactive_and_malformed_customers_cannot_create_orders(self):
        deactivated = deactivate_customer(self.customer["id"], self.customer_file)
        self.assertFalse(deactivated["active"])
        with self.assertRaises(ValidationError):
            create_order(
                self.customer["id"], "Shirt", "Shirt", 1, "100.00",
                "2026-12-01", orders_file=self.order_file, customers_file=self.customer_file,
            )

        malformed_cases = (
            {"id": 20, "name": "A", "phone": "08012345678", "email": None,
             "address": None, "measurements": {}, "active": "false",
             "created_at": "2026-11-22T00:00:00+00:00", "updated_at": "2026-11-22T00:00:00+00:00"},
            {"id": 21, "name": "A", "phone": "08012345679", "email": None,
             "address": None, "measurements": {}, "active": 0,
             "created_at": "2026-11-22T00:00:00+00:00", "updated_at": "2026-11-22T00:00:00+00:00"},
            {"id": 22, "name": "A", "phone": "08012345670", "email": None,
             "address": None, "measurements": {}, "active": None,
             "created_at": "2026-11-22T00:00:00+00:00", "updated_at": "2026-11-22T00:00:00+00:00"},
        )
        for index, customer in enumerate(malformed_cases):
            malformed_file = Path(self.temp_dir.name) / ("malformed_customer_" + str(index) + ".json")
            malformed_file.write_text(json.dumps([customer]), encoding="utf-8")
            with self.subTest(active=customer["active"]):
                with self.assertRaises(DataStorageError):
                    create_order(
                        customer["id"], "Shirt", "Shirt", 1, "100.00",
                        "2026-12-01", orders_file=self.order_file,
                        customers_file=malformed_file,
                    )

        missing_file = Path(self.temp_dir.name) / "missing_customer_field.json"
        missing = dict(malformed_cases[0])
        missing.pop("phone")
        missing_file.write_text(json.dumps([missing]), encoding="utf-8")
        with self.assertRaises(DataStorageError):
            create_order(
                missing["id"], "Shirt", "Shirt", 1, "100.00",
                "2026-12-01", orders_file=self.order_file, customers_file=missing_file,
            )

    def test_malformed_and_duplicate_order_records_are_rejected(self):
        malformed = [{
            "order_id": 1,
            "customer_id": self.customer["id"],
            "item_name": "Shirt",
            "description": "Shirt",
            "quantity": 0,
            "unit_price": "100.00",
            "total_amount": "0.00",
            "measurements_snapshot": {},
            "order_date": "2026-11-22",
            "delivery_date": "2026-12-01",
            "status": "pending",
            "notes": None,
            "created_at": "2026-11-22T00:00:00+00:00",
            "updated_at": "2026-11-22T00:00:00+00:00",
        }]
        save_data(malformed, self.order_file)
        with self.assertRaises(DataStorageError):
            load_validated_orders(self.order_file, self.customer_file)

        duplicate = [dict(malformed[0], quantity=1, total_amount="100.00"),
                     dict(malformed[0], quantity=1, total_amount="100.00")]
        self.order_file.write_text(json.dumps(duplicate), encoding="utf-8")
        with self.assertRaises(DataStorageError):
            load_validated_orders(self.order_file, self.customer_file)

    def test_multiprocess_order_creation_allocates_unique_persisted_ids(self):
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        processes = [
            context.Process(
                target=_process_create_order,
                args=(str(self.customer_file), str(self.order_file), index, queue),
            )
            for index in range(6)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)

        results = [queue.get(timeout=2) for _ in processes]
        successful = [result for result in results if result[0] == "ok"]
        self.assertEqual(len(successful), 6)
        ids = [result[1] for result in successful]
        self.assertEqual(len(set(ids)), 6)
        persisted = load_validated_orders(self.order_file, self.customer_file)
        self.assertEqual(len(persisted), 6)
        self.assertEqual({record["order_id"] for record in persisted}, set(ids))

    def test_counter_recovery_does_not_reuse_existing_order_id(self):
        first = create_order(
            self.customer["id"], "Shirt", "Shirt", 1, "100.00",
            "2026-12-01", orders_file=self.order_file, customers_file=self.customer_file,
        )
        counter = self.order_file.parent / ".orders.next_id.json"
        if counter.exists():
            counter.unlink()
        second = create_order(
            self.customer["id"], "Dress", "Dress", 1, "100.00",
            "2026-12-01", orders_file=self.order_file, customers_file=self.customer_file,
        )
        self.assertGreater(second["order_id"], first["order_id"])

    def test_multiprocess_customer_delete_and_order_create_leave_no_orphan(self):
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        create_process = context.Process(
            target=_process_create_order,
            args=(str(self.customer_file), str(self.order_file), 99, queue),
        )
        delete_process = context.Process(
            target=_process_delete_customer,
            args=(str(self.customer_file), str(self.order_file), queue),
        )
        create_process.start()
        delete_process.start()
        create_process.join(timeout=10)
        delete_process.join(timeout=10)
        self.assertFalse(create_process.is_alive())
        self.assertFalse(delete_process.is_alive())
        self.assertEqual(create_process.exitcode, 0)
        self.assertEqual(delete_process.exitcode, 0)

        results = [queue.get(timeout=2), queue.get(timeout=2)]
        customer_file = self.customer_file
        order_records = load_validated_orders(self.order_file, customer_file)
        customer_exists = json.loads(customer_file.read_text(encoding="utf-8")) != []
        for order in order_records:
            if customer_exists:
                continue
            self.fail("An orphaned order remains after customer deletion.")


if __name__ == "__main__":
    unittest.main()
