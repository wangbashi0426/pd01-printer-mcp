#!/usr/bin/env python3
"""Cloud-side durable MCP queue for asynchronous thermal telegrams."""

from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, time as wall_time, timedelta
from zoneinfo import ZoneInfo

os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")

from dotenv import load_dotenv
from fastmcp import FastMCP

from relay_store import RelayStore

load_dotenv()


DB_PATH = os.getenv("RELAY_DB_PATH", "telegram_relay.sqlite3")
RELAY_HOST = os.getenv("RELAY_HOST", "127.0.0.1")
RELAY_PORT = int(os.getenv("RELAY_PORT", "4177"))
WORKER_SECRET = os.getenv("RELAY_WORKER_SECRET", "")
ALLOW_AUTONOMOUS = os.getenv("ALLOW_AUTONOMOUS_TELEGRAMS", "0") == "1"
TIMEZONE = ZoneInfo(os.getenv("TELEGRAM_TIMEZONE", "Asia/Shanghai"))
ALLOWED_START = os.getenv("TELEGRAM_ALLOWED_START", "08:00")
ALLOWED_END = os.getenv("TELEGRAM_ALLOWED_END", "23:00")
MAX_PER_DAY = int(os.getenv("TELEGRAM_MAX_PER_DAY", "6"))
MAX_CHARS = int(os.getenv("TELEGRAM_MAX_CHARS", "280"))

CONFIRM_QUEUE = "CONFIRM_QUEUE"
AUTONOMOUS_AUTHORIZATION = "AUTONOMOUS_TELEGRAM"

store = RelayStore(DB_PATH)
mcp = FastMCP(
    "pd01-telegram-relay",
    instructions=(
        "Durable cloud mailbox for a PD01 thermal printer. It queues text only; "
        "the home computer pulls jobs and controls the physical printer."
    ),
)


def _parse_clock(value: str) -> wall_time:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return wall_time(hour=hour, minute=minute)
    except (TypeError, ValueError) as error:
        raise ValueError("allowed hours must use HH:MM") from error


def next_allowed_timestamp(now: float | None = None) -> float:
    now = time.time() if now is None else now
    local_now = datetime.fromtimestamp(now, TIMEZONE)
    start = _parse_clock(ALLOWED_START)
    end = _parse_clock(ALLOWED_END)
    current = local_now.time().replace(tzinfo=None)

    if start < end:
        if start <= current < end:
            return now
        target_date = local_now.date() if current < start else local_now.date() + timedelta(days=1)
        return datetime.combine(target_date, start, TIMEZONE).timestamp()

    if current >= start or current < end:
        return now
    return datetime.combine(local_now.date(), start, TIMEZONE).timestamp()


def _local_day_bounds(now: float | None = None) -> tuple[float, float]:
    now = time.time() if now is None else now
    local_now = datetime.fromtimestamp(now, TIMEZONE)
    start = datetime.combine(local_now.date(), wall_time.min, TIMEZONE)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def _validate_message(text: str, font_size: int, align: str) -> None:
    if not text.strip():
        raise ValueError("text cannot be empty")
    if len(text) > MAX_CHARS:
        raise ValueError(f"telegram is too long; maximum is {MAX_CHARS} characters")
    if not 12 <= font_size <= 72:
        raise ValueError("font_size must be between 12 and 72")
    if align not in {"left", "center", "right"}:
        raise ValueError("align must be left, center, or right")


def _worker_authorized(worker_secret: str) -> bool:
    return bool(WORKER_SECRET) and secrets.compare_digest(worker_secret, WORKER_SECRET)


@mcp.tool()
def prepare_telegram(text: str, font_size: int = 24, align: str = "left") -> dict:
    """Prepare a manual telegram. This does not queue or print it."""
    _validate_message(text, font_size, align)
    job = store.prepare(text, font_size, align, mode="manual")
    return {
        "prepared": True,
        "job_id": job["job_id"],
        "text": text,
        "font_size": font_size,
        "align": align,
        "content_hash": job["content_hash"],
        "next_step": "Show the exact text and obtain approval before queue_prepared_telegram.",
    }


@mcp.tool()
def queue_prepared_telegram(job_id: str, confirmation: str) -> dict:
    """Queue a manually prepared telegram after exact-content approval."""
    if confirmation != CONFIRM_QUEUE:
        return {"queued": False, "error": f"pass confirmation={CONFIRM_QUEUE} after approval"}
    job = store.queue(job_id, next_allowed_timestamp())
    return {"queued": True, **job}


@mcp.tool()
def enqueue_telegram(
    text: str,
    idempotency_key: str,
    authorization: str,
    font_size: int = 24,
    align: str = "left",
) -> dict:
    """Queue one pre-authorized autonomous telegram within configured limits."""
    if not ALLOW_AUTONOMOUS:
        return {"queued": False, "error": "autonomous telegram mode is disabled on the relay"}
    if authorization != AUTONOMOUS_AUTHORIZATION:
        return {"queued": False, "error": "autonomous telegram authorization is missing"}
    if not 8 <= len(idempotency_key) <= 128:
        return {"queued": False, "error": "idempotency_key must be 8-128 characters"}
    _validate_message(text, font_size, align)

    existing = store.get_by_idempotency(idempotency_key, include_text=True)
    if existing is not None:
        from relay_store import content_hash

        if existing["content_hash"] != content_hash(text, font_size, align):
            return {"queued": False, "error": "idempotency_key belongs to different content"}
        if existing["status"] == "prepared":
            existing = store.queue(existing["job_id"], next_allowed_timestamp())
        return {"queued": existing["status"] in {"queued", "leased", "delivered"}, **existing}

    day_start, day_end = _local_day_bounds()
    if store.count_created(day_start, day_end, mode="autonomous") >= MAX_PER_DAY:
        return {"queued": False, "error": f"daily autonomous limit reached ({MAX_PER_DAY})"}

    job = store.prepare(
        text,
        font_size,
        align,
        mode="autonomous",
        idempotency_key=idempotency_key,
    )
    queued = store.queue(job["job_id"], next_allowed_timestamp())
    return {"queued": True, **queued}


@mcp.tool()
def telegram_status(job_id: str = "") -> dict:
    """Read queue status without returning message text."""
    store.prune()
    if job_id:
        job = store.get(job_id)
        return {"found": job is not None, "job": job}
    return {"ok": True, "counts": store.summary()}


@mcp.tool()
def cancel_telegram(job_id: str) -> dict:
    """Cancel a telegram that has not started physical printing."""
    try:
        return {"cancelled": True, **store.cancel(job_id)}
    except ValueError as error:
        return {"cancelled": False, "error": str(error)}


@mcp.tool()
def lease_next_telegram(worker_secret: str, device_id: str, lease_seconds: int = 120) -> dict:
    """Lease the next queued telegram to the authenticated home worker."""
    if not _worker_authorized(worker_secret):
        return {"leased": False, "error": "worker authentication failed"}
    if not 30 <= lease_seconds <= 900:
        return {"leased": False, "error": "lease_seconds must be between 30 and 900"}
    job = store.lease_next(device_id[:80], lease_seconds)
    return {"leased": job is not None, "job": job}


@mcp.tool()
def acknowledge_telegram(worker_secret: str, job_id: str, lease_id: str) -> dict:
    """Acknowledge durable local completion and erase delivered message text."""
    if not _worker_authorized(worker_secret):
        return {"acknowledged": False, "error": "worker authentication failed"}
    try:
        return {"acknowledged": True, **store.acknowledge(job_id, lease_id)}
    except ValueError as error:
        return {"acknowledged": False, "error": str(error)}


@mcp.tool()
def release_telegram(
    worker_secret: str,
    job_id: str,
    lease_id: str,
    error: str,
    retry_after: int = 60,
) -> dict:
    """Retry later only when no printer bytes were written."""
    if not _worker_authorized(worker_secret):
        return {"released": False, "error": "worker authentication failed"}
    retry_after = min(max(retry_after, 10), 3600)
    try:
        return {
            "released": True,
            **store.release_safe_failure(job_id, lease_id, error, retry_after),
        }
    except ValueError as failure:
        return {"released": False, "error": str(failure)}


@mcp.tool()
def mark_telegram_uncertain(worker_secret: str, job_id: str, lease_id: str, error: str) -> dict:
    """Stop automatic retries when paper output may have started."""
    if not _worker_authorized(worker_secret):
        return {"marked": False, "error": "worker authentication failed"}
    try:
        return {"marked": True, **store.mark_uncertain(job_id, lease_id, error)}
    except ValueError as failure:
        return {"marked": False, "error": str(failure)}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host=RELAY_HOST, port=RELAY_PORT)
