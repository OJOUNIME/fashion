"""Tests for customer and measurement operations."""

import tempfile
import unittest
import threading
import multiprocessing
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

from fashion_studio.backend.customers import (
    add_customer,
    delete_customer,
    get_all_customers,
    get_customer_by_id,
    reactivate_customer,
    search_customers,
    update_customer,
)
from fashion_studio.backend.exceptions import (
    CustomerNotFoundError,
    DataStorageError,
    DuplicateRecordError,
    ValidationError,
)
from fashion_studio.backend.orders import cancel_order, create_order
from fashion_studio.backend.customers import deactivate_customer, get_customer_order_history
from fashion_studio.backend.payments import record_payment
from fashion_studio.backend.storage import save_data, validate_integrity
from fashion_studio.backend.validators import (
    normalize_phone,
    validate_complete_iso_datetime,
    validate_phone,
)


def _process_add_customer(customer_file, name, phone, queue):
    try:
        customer = add_customer(name, phone, customers_file=customer_file)
        queue.put(("ok", customer["id"]))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def _process_update_customer(customer_file, customer_id, phone, queue):
    try:
        customer = update_customer(customer_id, phone=phone, customers_file=customer_file)
        queue.put(("ok", customer["id"], customer["phone"]))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def _process_reactivate_customer(customer_file, customer_id, queue):
    try:
        customer = reactivate_customer(customer_id, customers_file=customer_file)
        queue.put(("ok", customer["id"]))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


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

    def test_concurrent_customer_creation_prevents_duplicate_ids_and_phones(self):
        results = []
        errors = []

        def create(index):
            try:
                results.append(
                    add_customer(
                        "Customer " + str(index),
                        "08012345678",
                        customers_file=self.customer_file,
                    )
                )
            except ValidationError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=create, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 7)
        self.assertEqual(len({customer["id"] for customer in results}), 1)

    def test_cancelled_order_preserves_historical_payment_summary(self):
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
        cancel_order(order["order_id"], self.order_file)

        history = get_customer_order_history(
            customer["id"],
            customers_file=self.customer_file,
            orders_file=self.order_file,
            payments_file=payment_file,
        )
        self.assertEqual(history["total_amount"], "0.00")
        self.assertEqual(history["total_paid"], "200.00")
        self.assertEqual(history["outstanding_balance"], "0.00")
        self.assertEqual(history["cancelled_orders"], 1)

    def test_malformed_existing_measurements_are_rejected(self):
        customer = self.add_customer()
        save_data(
            [{
                **customer,
                "measurements": ["not", "an", "object"],
            }],
            self.customer_file,
        )
        with self.assertRaises(DataStorageError):
            update_customer(
                customer["id"],
                measurements={"chest": 42},
                customers_file=self.customer_file,
            )

    def test_partial_measurement_update_preserves_existing_values(self):
        customer = self.add_customer()
        updated = update_customer(
            customer["id"],
            measurements={"hip": 38},
            customers_file=self.customer_file,
        )
        self.assertEqual(updated["measurements"]["chest"], 40.5)
        self.assertEqual(updated["measurements"]["waist"], 34.0)
        self.assertEqual(updated["measurements"]["hip"], 38.0)

    def test_reactivation_active_missing_and_history_behavior(self):
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
        deactivated = deactivate_customer(customer["id"], self.customer_file)
        self.assertFalse(deactivated["active"])
        reactivated = reactivate_customer(customer["id"], self.customer_file)
        self.assertTrue(reactivated["active"])
        self.assertEqual(reactivated["measurements"], customer["measurements"])
        with self.assertRaises(ValidationError):
            reactivate_customer(customer["id"], self.customer_file)
        with self.assertRaises(CustomerNotFoundError):
            reactivate_customer(999, self.customer_file)
        history = get_customer_order_history(
            customer["id"],
            customers_file=self.customer_file,
            orders_file=self.order_file,
        )
        self.assertEqual(history["order_count"], 1)
        self.assertEqual(history["orders"][0]["order_id"], order["order_id"])

    def test_malformed_emails_are_rejected(self):
        for email in ("@example.com", "a@.com", "a b@example.com", "name@example"):
            with self.subTest(email=email):
                with self.assertRaises(ValidationError):
                    add_customer(
                        "David",
                        "08012345678",
                        email=email,
                        customers_file=self.customer_file,
                    )

    def test_customer_schema_validation_and_duplicate_phone_integrity(self):
        payment_file = Path(self.temp_dir.name) / "payments.json"
        save_data([
            {
                "id": 1,
                "name": "",
                "phone": "phone",
                "email": "@example.com",
                "address": 123,
                "measurements": ["bad"],
                "active": "yes",
            },
        ], self.customer_file)
        result = validate_integrity(
            self.customer_file,
            self.order_file,
            payment_file,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("missing required fields" in issue for issue in result["issues"]))
        self.assertTrue(any("invalid name" in issue for issue in result["issues"]))
        self.assertTrue(any("invalid phone" in issue for issue in result["issues"]))

        save_data([
            {
                "id": 1,
                "name": "First",
                "phone": "08012345678",
                "email": None,
                "address": None,
                "measurements": {},
                "active": True,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": 2,
                "name": "Second",
                "phone": "08012345678",
                "email": None,
                "address": None,
                "measurements": {},
                "active": True,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        ], self.customer_file)
        duplicate_result = validate_integrity(
            self.customer_file,
            self.order_file,
            payment_file,
        )
        self.assertFalse(duplicate_result["valid"])
        self.assertTrue(any("Duplicate customer phone number" in issue for issue in duplicate_result["issues"]))

    @unittest.skipUnless(fcntl is not None, "fcntl file locking is unavailable")
    def test_multiprocess_concurrent_customer_creation(self):
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        processes = [
            context.Process(
                target=_process_add_customer,
                args=(str(self.customer_file), "Customer " + str(index),
                      "080-1234-" + str(index).zfill(4), queue),
            )
            for index in range(1, 5)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            self.assertFalse(process.is_alive(), "customer creation process timed out")
            self.assertEqual(process.exitcode, 0)

        results = [queue.get(timeout=2) for _ in processes]
        successful = [result for result in results if result[0] == "ok"]
        self.assertEqual(len(successful), 4)
        self.assertEqual(len({result[1] for result in successful}), 4)
        final_records = get_all_customers(str(self.customer_file))
        self.assertEqual(len(final_records), 4)
        self.assertEqual(len({record["id"] for record in final_records}), 4)
        self.assertEqual(
            {record["id"] for record in final_records},
            {result[1] for result in successful},
        )

    @unittest.skipUnless(fcntl is not None, "fcntl file locking is unavailable")
    def test_multiprocess_normalized_duplicate_phone_creation(self):
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        phone_variants = [
            "08012345678",
            "080-1234-5678",
            "080 1234 5678",
            "080.1234.5678",
        ]
        processes = [
            context.Process(
                target=_process_add_customer,
                args=(str(self.customer_file), "Customer " + str(index),
                      phone, queue),
            )
            for index, phone in enumerate(phone_variants)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            self.assertFalse(process.is_alive(), "phone creation process timed out")
            self.assertEqual(process.exitcode, 0)

        results = [queue.get(timeout=2) for _ in processes]
        successful = [result for result in results if result[0] == "ok"]
        errors = [result for result in results if result[0] == "error"]
        self.assertEqual(len(successful), 1)
        self.assertEqual(len(errors), 3)
        self.assertTrue(all("phone number already exists" in result[2] for result in errors))
        final_records = get_all_customers(str(self.customer_file))
        normalized_phones = [normalize_phone(record["phone"]) for record in final_records]
        self.assertEqual(len(final_records), 1)
        self.assertEqual(normalized_phones, ["08012345678"])
        self.assertEqual(len(set(normalized_phones)), 1)

    @unittest.skipUnless(fcntl is not None, "fcntl file locking is unavailable")
    def test_multiprocess_phone_updates_are_unique(self):
        first = self.add_customer("First", "08011111111")
        second = self.add_customer("Second", "08022222222")
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        processes = [
            context.Process(
                target=_process_update_customer,
                args=(str(self.customer_file), customer["id"], "080-3333-3333", queue),
            )
            for customer in (first, second)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            self.assertFalse(process.is_alive(), "phone update process timed out")
            self.assertEqual(process.exitcode, 0)

        results = [queue.get(timeout=2) for _ in processes]
        successful = [result for result in results if result[0] == "ok"]
        errors = [result for result in results if result[0] == "error"]
        self.assertEqual(len(successful), 1)
        self.assertEqual(len(errors), 1)
        final_records = get_all_customers(str(self.customer_file))
        normalized_phones = [normalize_phone(record["phone"]) for record in final_records]
        self.assertEqual(len(set(normalized_phones)), len(final_records))
        successful_update = successful[0]
        stored = get_customer_by_id(successful_update[1], str(self.customer_file))
        self.assertEqual(normalize_phone(stored["phone"]), "08033333333")
        self.assertIn(stored["phone"], ("080-3333-3333", "08033333333"))

    @unittest.skipUnless(fcntl is not None, "fcntl file locking is unavailable")
    def test_multiprocess_reactivation_cannot_take_another_phone(self):
        inactive = deactivate_customer(
            self.add_customer("Inactive", "08012345678")["id"],
            self.customer_file,
        )
        active = self.add_customer("Active", "08098765432")
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        processes = [
            context.Process(
                target=_process_reactivate_customer,
                args=(str(self.customer_file), inactive["id"], queue),
            ),
            context.Process(
                target=_process_update_customer,
                args=(str(self.customer_file), active["id"], "080-1234-5678", queue),
            ),
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            self.assertFalse(process.is_alive(), "reactivation process timed out")
            self.assertEqual(process.exitcode, 0)

        results = [queue.get(timeout=2) for _ in processes]
        successful = [result for result in results if result[0] == "ok"]
        errors = [result for result in results if result[0] == "error"]
        self.assertEqual(len(successful), 1)
        self.assertEqual(len(errors), 1)
        final_records = get_all_customers(str(self.customer_file))
        normalized_phones = [normalize_phone(record["phone"]) for record in final_records]
        self.assertEqual(len(set(normalized_phones)), len(final_records))
        final_inactive = get_customer_by_id(inactive["id"], str(self.customer_file))
        final_active = get_customer_by_id(active["id"], str(self.customer_file))
        self.assertNotEqual(
            normalize_phone(final_inactive["phone"]),
            normalize_phone(final_active["phone"]),
        )
        if successful[0][1] == inactive["id"]:
            self.assertTrue(final_inactive["active"])
            self.assertTrue(final_active["active"])
        else:
            self.assertFalse(final_inactive["active"])
            self.assertTrue(final_active["active"])

    def test_phone_format_validation(self):
        for phone in (
            "08012345678",
            "080-1234-5678",
            "080 1234 5678",
            "080.1234.5678",
        ):
            with self.subTest(phone=phone):
                self.assertEqual(validate_phone(phone), phone)
                self.assertEqual(normalize_phone(phone), "08012345678")
        for phone in (
            "080--1234..5678",
            "080////12345678",
            "080@12345678",
        ):
            with self.subTest(phone=phone):
                with self.assertRaises(ValidationError):
                    validate_phone(phone)

    def test_complete_iso_datetime_validation(self):
        for timestamp in (
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T12:30:45Z",
        ):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(validate_complete_iso_datetime(timestamp), timestamp)
        for timestamp in (
            "",
            "2026-01-01",
            "2026-02-30T12:00:00+00:00",
            "not-a-timestamp",
        ):
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(ValidationError):
                    validate_complete_iso_datetime(timestamp)

        save_data([{
            "id": 1,
            "name": "David",
            "phone": "08012345678",
            "email": None,
            "address": None,
            "measurements": {},
            "active": True,
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01T12:00:00+00:00",
        }], self.customer_file)
        with self.assertRaises(DataStorageError):
            get_all_customers(self.customer_file)

    def test_malformed_customer_records_are_rejected_by_loading(self):
        base = {
            "id": 1,
            "name": "David",
            "phone": "08012345678",
            "email": "david@example.com",
            "address": "Lagos",
            "measurements": {"chest": 40},
            "active": True,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        malformed_values = (
            ("missing_measurements", {key: value for key, value in base.items() if key != "measurements"}),
            ("null_measurements", {**base, "measurements": None}),
            ("list_measurements", {**base, "measurements": ["bad"]}),
            ("invalid_measurement_value", {**base, "measurements": {"chest": -1}}),
            ("invalid_active_status", {**base, "active": "yes"}),
            ("malformed_timestamp", {**base, "created_at": "not-a-date"}),
        )
        for label, record in malformed_values:
            with self.subTest(label=label):
                save_data([record], self.customer_file)
                with self.assertRaises(DataStorageError):
                    get_all_customers(self.customer_file)

        save_data([base], self.customer_file)
        self.assertEqual(get_all_customers(self.customer_file)[0]["id"], 1)

    def test_duplicate_normalized_phones_and_ids_are_rejected(self):
        save_data([{
            "id": 1,
            "name": "First",
            "phone": "08012345678",
            "email": None,
            "address": None,
            "measurements": {},
            "active": True,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }, {
            "id": 2,
            "name": "Second",
            "phone": "080-1234-5678",
            "email": None,
            "address": None,
            "measurements": {},
            "active": True,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }], self.customer_file)
        with self.assertRaises(DataStorageError):
            get_all_customers(self.customer_file)

        valid_duplicate = {
            "id": 1,
            "name": "David",
            "phone": "08011111111",
            "email": None,
            "address": None,
            "measurements": {},
            "active": True,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        with self.assertRaises(DuplicateRecordError):
            save_data([valid_duplicate, {**valid_duplicate}], self.customer_file)


if __name__ == "__main__":
    unittest.main()
