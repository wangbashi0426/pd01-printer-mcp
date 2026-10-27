"""Local durable receipt ledger for duplicate-safe physical printing."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


class DeliveryLedger:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_receipts (
                    job_id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    last_error TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM delivery_receipts WHERE job_id=?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def _set(self, job_id: str, digest: str, state: str, error: str | None = None) -> None:
        existing = self.get(job_id)
        if existing and existing["content_hash"] != digest:
            raise ValueError("relay reused a job_id for different content")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO delivery_receipts(job_id, content_hash, state, updated_at, last_error)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    state=excluded.state,
                    updated_at=excluded.updated_at,
                    last_error=excluded.last_error
                """,
                (job_id, digest, state, time.time(), error[:500] if error else None),
            )

    def mark_printing(self, job_id: str, digest: str) -> None:
        self._set(job_id, digest, "printing")

    def mark_printed(self, job_id: str, digest: str) -> None:
        self._set(job_id, digest, "printed")

    def mark_uncertain(self, job_id: str, digest: str, error: str) -> None:
        self._set(job_id, digest, "uncertain", error)

    def mark_acked(self, job_id: str, digest: str) -> None:
        self._set(job_id, digest, "acked")

    def clear_safe_failure(self, job_id: str, digest: str) -> None:
        existing = self.get(job_id)
        if existing and existing["content_hash"] != digest:
            raise ValueError("relay reused a job_id for different content")
        with self._connect() as connection:
            connection.execute("DELETE FROM delivery_receipts WHERE job_id=?", (job_id,))

    def summary(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM delivery_receipts GROUP BY state"
            ).fetchall()
        return {row["state"]: int(row["count"]) for row in rows}
