# AI Integration Guide

This document defines safe agent behavior for both local printing and cloud telegram delivery.

## Local printer tools

### `scan_printers(timeout_seconds=8)`

Read-only. It lists nearby BLE device names and does not expose hardware addresses.

### `check_printer()`

Read-only with respect to paper. Call it before the first local print in a session. If it fails, ask the human to wake the printer, move it near the PC, and close the phone printing app.

### `prepare_text(text, font_size=24, align="left")`

Preparation only. After calling it, show the returned `text` exactly, show the relevant layout information, and ask for current explicit confirmation.

### `print_prepared(job_id, confirmation)`

Physical side effect. Call it once only after the human confirms the exact prepared text, passing `confirmation="CONFIRM_PRINT"`.

If the call times out or returns an ambiguous error, do not retry. Ask the human to inspect the printer.

### `telegram_worker_status()`

Read-only. It reports whether the outbound worker is enabled/configured and summarizes local receipt states without returning messages or secrets.

## Cloud relay tools

### Manual workflow

1. Call `prepare_telegram`.
2. Show the exact returned text and formatting.
3. Obtain current explicit confirmation.
4. Call `queue_prepared_telegram` once with `confirmation="CONFIRM_QUEUE"`.
5. Use `telegram_status(job_id)` to report queue progress.

The human's approval to prepare text is not approval to queue it. An old confirmation cannot authorize changed content.

### Autonomous workflow

Use `enqueue_telegram` only when the relay explicitly permits autonomous telegrams and the human has opted into that behavior. Pass:

- the exact message text;
- a stable, unique `idempotency_key` for this one message intent;
- `authorization="AUTONOMOUS_TELEGRAM"`.

Retries of the same intent must reuse the same idempotency key. Changed content must use a new key. Never invent multiple keys merely because the first response timed out.

Respect the relay's quiet hours, daily limit, and size limit. A queued response may have a future `available_at`; do not create a second job to make it print sooner.

### Status and cancellation

`telegram_status` does not return message content. `cancel_telegram` is allowed only before physical printing starts. A `review_required` job may have printed partially or fully, so do not create a replacement automatically.

## Delivery semantics

The home worker records a durable receipt before printing. It retries only failures known to have happened before any printer bytes were written. Once bytes may have been written, the job stops for human review.

If the paper prints successfully but acknowledgement fails, the worker later acknowledges the same job from its local receipt without printing again. Do not bypass or delete either the relay database or local receipt ledger to force retries.

## Privacy and security

- Do not expose Bluetooth addresses, relay URLs, credentials, worker secrets, local paths, logs, previous messages, or queue database contents.
- Do not put secrets into printable text, idempotency keys, status messages, or public issue reports.
- Treat confirmation strings as workflow guards rather than authentication.
- Require authenticated HTTPS for a public relay endpoint.
- Do not claim that paper physically emerged solely because the software returned `delivered`; ask the human when physical certainty matters.
