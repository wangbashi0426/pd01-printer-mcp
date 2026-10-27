import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import printer_server
from telegram_ledger import DeliveryLedger


class _FakeClient:
    def __init__(self, fail_on_write=None):
        self.is_connected = True
        self.fail_on_write = fail_on_write
        self.writes = 0

    async def write_gatt_char(self, *_args, **_kwargs):
        self.writes += 1
        if self.fail_on_write == self.writes:
            raise RuntimeError("BLE dropped")

    async def disconnect(self):
        self.is_connected = False


class PrintDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        printer_server.current_client = None

    async def test_connect_failure_is_safe_to_retry(self):
        with (
            patch.object(printer_server, "png_to_rows", return_value=[bytes(48)]),
            patch.object(
                printer_server,
                "ensure_connected",
                new=AsyncMock(side_effect=RuntimeError("printer asleep")),
            ),
        ):
            with self.assertRaises(printer_server.PrintAttemptError) as caught:
                await printer_server.print_png(b"png")
        self.assertFalse(caught.exception.uncertain)

    async def test_failure_after_a_write_is_uncertain(self):
        fake = _FakeClient(fail_on_write=2)
        with (
            patch.object(printer_server, "png_to_rows", return_value=[bytes(48)]),
            patch.object(printer_server, "ensure_connected", new=AsyncMock(return_value=fake)),
            patch.object(printer_server.asyncio, "sleep", new=AsyncMock()),
        ):
            with self.assertRaises(printer_server.PrintAttemptError) as caught:
                await printer_server.print_png(b"png")
        self.assertTrue(caught.exception.uncertain)

    async def test_printed_receipt_is_acknowledged_without_reprinting(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = DeliveryLedger(Path(directory) / "receipts.sqlite3")
            text = "already printed"
            digest = printer_server._telegram_hash(text, 24, "left")
            ledger.mark_printed("job-1", digest)
            job = {
                "job_id": "job-1",
                "lease_id": "lease-1",
                "text": text,
                "font_size": 24,
                "align": "left",
                "content_hash": digest,
            }
            relay_call = AsyncMock(return_value={"acknowledged": True})
            print_call = AsyncMock()
            with (
                patch.object(printer_server, "_relay_call", new=relay_call),
                patch.object(printer_server, "print_png", new=print_call),
            ):
                await printer_server.process_relay_job(object(), ledger, job)
            print_call.assert_not_awaited()
            self.assertEqual(ledger.get("job-1")["state"], "acked")

    async def test_safe_print_failure_releases_job_and_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = DeliveryLedger(Path(directory) / "receipts.sqlite3")
            text = "try again later"
            digest = printer_server._telegram_hash(text, 24, "left")
            job = {
                "job_id": "job-2",
                "lease_id": "lease-2",
                "text": text,
                "font_size": 24,
                "align": "left",
                "content_hash": digest,
            }
            relay_call = AsyncMock(return_value={"released": True})
            with (
                patch.object(printer_server, "_relay_call", new=relay_call),
                patch.object(
                    printer_server,
                    "render_text",
                    return_value=(b"png", 384, 8, 1),
                ),
                patch.object(
                    printer_server,
                    "print_png",
                    new=AsyncMock(
                        side_effect=printer_server.PrintAttemptError(
                            "printer asleep", uncertain=False
                        )
                    ),
                ),
            ):
                await printer_server.process_relay_job(object(), ledger, job)
            self.assertIsNone(ledger.get("job-2"))
            self.assertEqual(relay_call.await_args.args[1], "release_telegram")


if __name__ == "__main__":
    unittest.main()
