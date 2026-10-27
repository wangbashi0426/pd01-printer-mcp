import tempfile
import unittest
from pathlib import Path

from telegram_ledger import DeliveryLedger


class DeliveryLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.ledger = DeliveryLedger(Path(self.tempdir.name) / "receipts.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_printed_receipt_survives_reopen(self):
        self.ledger.mark_printing("job-1", "hash-1")
        self.ledger.mark_printed("job-1", "hash-1")
        reopened = DeliveryLedger(Path(self.tempdir.name) / "receipts.sqlite3")
        self.assertEqual(reopened.get("job-1")["state"], "printed")

    def test_safe_failure_removes_incomplete_receipt(self):
        self.ledger.mark_printing("job-2", "hash-2")
        self.ledger.clear_safe_failure("job-2", "hash-2")
        self.assertIsNone(self.ledger.get("job-2"))

    def test_job_id_cannot_be_reused_for_different_content(self):
        self.ledger.mark_printed("job-3", "hash-3")
        with self.assertRaises(ValueError):
            self.ledger.mark_printing("job-3", "different-hash")


if __name__ == "__main__":
    unittest.main()
