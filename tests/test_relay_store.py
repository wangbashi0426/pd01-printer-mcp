import tempfile
import unittest
from pathlib import Path

from relay_store import RelayStore


class RelayStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = RelayStore(Path(self.tempdir.name) / "relay.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def _queued_job(self, *, key="telegram-0001"):
        job = self.store.prepare(
            "hello", 24, "left", mode="autonomous", idempotency_key=key, now=100
        )
        self.store.queue(job["job_id"], 100, now=100)
        return job

    def test_acknowledgement_erases_message_text(self):
        job = self._queued_job()
        leased = self.store.lease_next("home", 120, now=101)
        self.assertEqual(leased["text"], "hello")

        acknowledged = self.store.acknowledge(job["job_id"], leased["lease_id"], now=102)
        self.assertEqual(acknowledged["status"], "delivered")
        stored = self.store.get(job["job_id"], include_text=True)
        self.assertIsNone(stored["text"])

    def test_idempotency_key_returns_same_job_and_rejects_changed_content(self):
        first = self.store.prepare(
            "same", 24, "left", mode="autonomous", idempotency_key="telegram-0002"
        )
        second = self.store.prepare(
            "same", 24, "left", mode="autonomous", idempotency_key="telegram-0002"
        )
        self.assertEqual(first["job_id"], second["job_id"])
        with self.assertRaises(ValueError):
            self.store.prepare(
                "changed", 24, "left", mode="autonomous", idempotency_key="telegram-0002"
            )

    def test_safe_failure_requeues_after_delay(self):
        job = self._queued_job(key="telegram-0003")
        leased = self.store.lease_next("home", 120, now=101)
        released = self.store.release_safe_failure(
            job["job_id"], leased["lease_id"], "printer asleep", 60, now=102
        )
        self.assertEqual(released["status"], "queued")
        self.assertIsNone(self.store.lease_next("home", 120, now=161))
        self.assertIsNotNone(self.store.lease_next("home", 120, now=162))

    def test_uncertain_output_is_not_automatically_leased_again(self):
        job = self._queued_job(key="telegram-0004")
        leased = self.store.lease_next("home", 120, now=101)
        stopped = self.store.mark_uncertain(
            job["job_id"], leased["lease_id"], "connection dropped after bytes were sent"
        )
        self.assertEqual(stopped["status"], "review_required")
        self.assertIsNone(self.store.lease_next("home", 120, now=1000))

    def test_expired_lease_can_be_recovered(self):
        first = self._queued_job(key="telegram-0005")
        leased_once = self.store.lease_next("home-a", 30, now=101)
        leased_twice = self.store.lease_next("home-b", 30, now=132)
        self.assertEqual(first["job_id"], leased_twice["job_id"])
        self.assertNotEqual(leased_once["lease_id"], leased_twice["lease_id"])

    def test_delivered_job_cannot_be_released_back_to_queue(self):
        job = self._queued_job(key="telegram-0006")
        leased = self.store.lease_next("home", 120, now=101)
        self.store.acknowledge(job["job_id"], leased["lease_id"], now=102)
        with self.assertRaises(ValueError):
            self.store.release_safe_failure(
                job["job_id"], leased["lease_id"], "late duplicate call", 60, now=103
            )
        self.assertEqual(self.store.get(job["job_id"])["status"], "delivered")


if __name__ == "__main__":
    unittest.main()
