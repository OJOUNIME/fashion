"""Tests for centralised JSON persistence."""

import json
import tempfile
import unittest
from pathlib import Path

from fashion_studio.backend.exceptions import DataStorageError, DuplicateRecordError
from fashion_studio.backend.storage import (
    append_record,
    create_backup,
    load_data,
    restore_backup,
    save_data,
    validate_integrity,
)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "records.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_load(self):
        save_data([{"id": 1}], self.path)
        self.assertEqual(load_data(self.path), [{"id": 1}])

    def test_missing_and_malformed_files(self):
        self.assertEqual(load_data(self.path), [])
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(DataStorageError):
            load_data(self.path)

    def test_failed_write_preserves_original(self):
        save_data([{"id": 1}], self.path)
        original = self.path.read_text(encoding="utf-8")
        with self.assertRaises(DataStorageError):
            save_data([{"bad": object()}], self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_duplicate_ids_are_rejected(self):
        save_data([{"id": 1}], self.path)
        with self.assertRaises(DuplicateRecordError):
            append_record({"id": 1}, self.path)
        with self.assertRaises(DuplicateRecordError):
            save_data([{"order_id": 1}, {"order_id": 1}], self.path)

    def test_backup_restore_and_integrity_report(self):
        records = [{"id": 1, "name": "David"}]
        save_data(records, self.path)
        backup = create_backup(self.path)
        save_data([{"id": 2}], self.path)
        self.assertEqual(restore_backup(backup, self.path), records)
        customers = Path(self.temp_dir.name) / "customers.json"
        orders = Path(self.temp_dir.name) / "orders.json"
        payments = Path(self.temp_dir.name) / "payments.json"
        save_data([{"id": 1}], customers)
        save_data([{"order_id": 1, "customer_id": 999, "quantity": 1, "total_amount": "10.00"}], orders)
        save_data([{"payment_id": 1, "order_id": 999, "amount": "10.00"}], payments)
        result = validate_integrity(customers, orders, payments)
        self.assertFalse(result["valid"])
        self.assertGreaterEqual(len(result["issues"]), 2)


if __name__ == "__main__":
    unittest.main()
