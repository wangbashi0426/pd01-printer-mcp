"""Durable SQLite queue used by the cloud telegram relay."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any


ACTIVE_STATUSES = {"prepared", "queued", "leased", "review_required"}


def content_hash(text: str, font_size: int, align: str) -> str:
    payload = f"{font_size}\0{align}\0{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class RelayStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS telegram_jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    text TEXT,
                    content_hash TEXT NOT NULL,
                    font_size INTEGER NOT NULL,
                    align TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    queued_at REAL,
                    available_at REAL,
                    lease_id TEXT,
                    lease_until REAL,
                    device_id TEXT,
                    delivered_at REAL,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_telegram_jobs_ready
                ON telegram_jobs(status, available_at, queued_at);
                """
            )

    @staticmethod
    def _public(row: sqlite3.Row | None, *, include_text: bool = False) -> dict[str, Any] | None:
        if row is None:
            return None
        result = {
            "job_id": row["job_id"],
            "idempotency_key": row["idempotency_key"],
            "content_hash": row["content_hash"],
            "font_size": row["font_size"],
            "align": row["align"],
            "mode": row["mode"],
            "status": row["status"],
            "created_at": row["created_at"],
            "queued_at": row["queued_at"],
            "available_at": row["available_at"],
            "lease_until": row["lease_until"],
            "device_id": row["device_id"],
            "delivered_at": row["delivered_at"],
            "last_error": row["last_error"],
        }
        if include_text:
            result["text"] = row["text"]
        return result

    def get(self, job_id: str, *, include_text: bool = False) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM telegram_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._public(row, include_text=include_text)

    def get_by_idempotency(
        self, idempotency_key: str, *, include_text: bool = False
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM telegram_jobs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        return self._public(row, include_text=include_text)

    def prepare(
        self,
        text: str,
        font_size: int,
        align: str,
        *,
        mode: str,
        idempotency_key: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        digest = content_hash(text, font_size, align)
        job_id = secrets.token_hex(12)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM telegram_jobs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if existing["content_hash"] != digest:
                        raise ValueError("idempotency_key is already used for different content")
                    connection.commit()
                    return self._public(existing, include_text=True) or {}
            connection.execute(
                """
                INSERT INTO telegram_jobs (
                    job_id, idempotency_key, text, content_hash, font_size, align,
                    mode, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', ?)
                """,
                (job_id, idempotency_key, text, digest, font_size, align, mode, now),
            )
            connection.commit()
        return self.get(job_id, include_text=True) or {}

    def queue(self, job_id: str, available_at: float, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM telegram_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown job_id")
            if row["status"] in {"queued", "leased", "delivered"}:
                connection.commit()
                return self._public(row, include_text=True) or {}
            if row["status"] != "prepared":
                raise ValueError(f"job cannot be queued from status={row['status']}")
            connection.execute(
                """
                UPDATE telegram_jobs
                SET status='queued', queued_at=?, available_at=?, last_error=NULL
                WHERE job_id=?
                """,
                (now, available_at, job_id),
            )
            connection.commit()
        return self.get(job_id, include_text=True) or {}

    def lease_next(
        self,
        device_id: str,
        lease_seconds: int,
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        now = time.time() if now is None else now
        lease_id = secrets.token_hex(12)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE telegram_jobs
                SET status='queued', lease_id=NULL, lease_until=NULL, device_id=NULL,
                    last_error=COALESCE(last_error, 'lease expired before acknowledgement')
                WHERE status='leased' AND lease_until < ?
                """,
                (now,),
            )
            row = connection.execute(
                """
                SELECT * FROM telegram_jobs
                WHERE status='queued' AND available_at <= ?
                ORDER BY queued_at, created_at
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE telegram_jobs
                SET status='leased', lease_id=?, lease_until=?, device_id=?
                WHERE job_id=? AND status='queued'
                """,
                (lease_id, now + lease_seconds, device_id, row["job_id"]),
            )
            connection.commit()
        leased = self.get(row["job_id"], include_text=True)
        if leased is not None:
            leased["lease_id"] = lease_id
        return leased

    def _check_lease(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        lease_id: str,
        *,
        allow_delivered: bool = False,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM telegram_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown job_id")
        if row["status"] == "delivered" and allow_delivered:
            return row
        if row["status"] != "leased" or row["lease_id"] != lease_id:
            raise ValueError("lease is missing or no longer current")
        return row

    def acknowledge(self, job_id: str, lease_id: str, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._check_lease(connection, job_id, lease_id, allow_delivered=True)
            if row["status"] != "delivered":
                connection.execute(
                    """
                    UPDATE telegram_jobs
                    SET status='delivered', delivered_at=?, text=NULL,
                        lease_id=NULL, lease_until=NULL, last_error=NULL
                    WHERE job_id=?
                    """,
                    (now, job_id),
                )
            connection.commit()
        return self.get(job_id) or {}

    def release_safe_failure(
        self,
        job_id: str,
        lease_id: str,
        error: str,
        retry_after: int,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_lease(connection, job_id, lease_id)
            connection.execute(
                """
                UPDATE telegram_jobs
                SET status='queued', available_at=?, lease_id=NULL, lease_until=NULL,
                    device_id=NULL, last_error=?
                WHERE job_id=?
                """,
                (now + retry_after, error[:500], job_id),
            )
            connection.commit()
        return self.get(job_id) or {}

    def mark_uncertain(self, job_id: str, lease_id: str, error: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_lease(connection, job_id, lease_id)
            connection.execute(
                """
                UPDATE telegram_jobs
                SET status='review_required', lease_id=NULL, lease_until=NULL,
                    last_error=?
                WHERE job_id=?
                """,
                (error[:500], job_id),
            )
            connection.commit()
        return self.get(job_id) or {}

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM telegram_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown job_id")
            if row["status"] not in {"prepared", "queued", "review_required"}:
                raise ValueError(f"job cannot be cancelled from status={row['status']}")
            connection.execute(
                """
                UPDATE telegram_jobs
                SET status='cancelled', text=NULL, lease_id=NULL, lease_until=NULL
                WHERE job_id=?
                """,
                (job_id,),
            )
            connection.commit()
        return self.get(job_id) or {}

    def count_created(self, start: float, end: float, *, mode: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM telegram_jobs
                WHERE created_at >= ? AND created_at < ? AND mode = ?
                  AND status != 'cancelled'
                """,
                (start, end, mode),
            ).fetchone()
        return int(row["count"])

    def summary(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM telegram_jobs GROUP BY status"
            ).fetchall()
        return {row["status"]: int(row["count"]) for row in rows}

    def prune(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM telegram_jobs
                WHERE (status='prepared' AND created_at < ?)
                   OR (status IN ('delivered', 'cancelled') AND COALESCE(delivered_at, created_at) < ?)
                """,
                (now - 3600, now - 7 * 86400),
            )
        return cursor.rowcount
