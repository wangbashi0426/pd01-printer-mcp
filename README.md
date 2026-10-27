# PD01 Printer MCP

A privacy-conscious MCP bridge for PD01-compatible 58 mm BLE thermal printers on Windows.

The repository supports two independent workflows:

- **Local printing:** prepare text, show it to the human, and print only after explicit confirmation.
- **Cloud telegrams:** an authenticated cloud relay keeps a durable queue. The home PC polls it over an outbound connection and prints queued telegrams when it is online. No inbound PC tunnel is required.

## Local tools

1. `scan_printers` lists nearby BLE device names without exposing addresses.
2. `check_printer` checks the BLE connection without using paper.
3. `prepare_text` renders a job without using paper.
4. `print_prepared` prints only after the human approves the exact text.
5. `telegram_worker_status` reports cloud-worker configuration and local receipt counts without exposing secrets.
6. `disconnect_printer` closes the BLE connection.

## Why the cloud relay survives an unstable home connection

The public MCP endpoint queues text in a persistent SQLite database. The home worker initiates every connection and leases one job at a time. A local receipt ledger records physical-print progress before acknowledgement:

- A failure before any printer byte is written is safe to retry later.
- A failure after printing may have started becomes `review_required` and is never retried automatically.
- If printing finishes but the network fails before acknowledgement, the durable local receipt causes the worker to acknowledge the job later without printing it twice.
- A successful acknowledgement erases the message body from the relay while retaining minimal delivery metadata.

This provides duplicate-resistant delivery, but software cannot prove that paper physically emerged. Check the printer when physical certainty matters.

## Requirements

- Windows 10 or Windows 11 for the home printer worker
- Python 3.11 or 3.12
- A PD01-compatible BLE printer using the 0x5178 protocol
- 58 mm thermal paper
- For cloud telegrams: an HTTPS host with authentication and persistent storage

## Local quick start

```powershell
git clone YOUR_REPOSITORY_URL
cd pd01-printer-mcp
setup_windows.cmd
start_printer.cmd
```

Pair the printer once in Windows Bluetooth settings first. “Paired, not connected” is normal for BLE devices; the server connects when needed.

The local MCP URL is `http://127.0.0.1:4176/mcp`.

```json
{
  "mcpServers": {
    "pd01-printer": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:4176/mcp"
    }
  }
}
```

## Deploy the cloud relay

Build `Dockerfile.relay` on an always-on container host. Give the container a persistent volume mounted at `/data`, configure the values from `relay.env.example`, and publish port 4177 only through authenticated HTTPS.

```powershell
docker build -f Dockerfile.relay -t pd01-telegram-relay .
docker run --rm -p 4177:4177 --env-file relay.env -v relay-data:/data pd01-telegram-relay
```

Generate `RELAY_WORKER_SECRET` with a password manager or:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the public MCP URL, the same worker secret, and any proxy access token in the home PC's `.env`:

```dotenv
TELEGRAM_RELAY_URL=https://your-relay.example/mcp
TELEGRAM_RELAY_ACCESS_TOKEN=
TELEGRAM_RELAY_WORKER_SECRET=replace-with-the-same-long-secret
TELEGRAM_AUTOPRINT=1
```

Restart `start_printer.cmd`. `telegram_worker_status` should then report `enabled: true` and `configured: true`.

The relay's `ALLOW_AUTONOMOUS_TELEGRAMS` default is `0`. In that state, an AI must use `prepare_telegram`, show the exact content, obtain current approval, and call `queue_prepared_telegram` with `CONFIRM_QUEUE`. Set it to `1` only after the human explicitly opts into the configured quiet hours, daily limit, and maximum message length.

## Configuration

The local server reads `.env` automatically; `.env` is ignored by Git. The cloud host can use its normal secret/environment settings. Never commit Bluetooth addresses, relay URLs, access tokens, worker secrets, logs, queue databases, or print content.

## Security model

- The local server binds to `127.0.0.1` by default.
- The cloud relay does not expose the printer or the home PC to inbound connections.
- `RELAY_WORKER_SECRET` authenticates worker-only queue operations; it does not authenticate public enqueue tools.
- Put the entire public relay behind HTTPS authentication and rate limiting.
- `CONFIRM_PRINT`, `CONFIRM_QUEUE`, and `AUTONOMOUS_TELEGRAM` are workflow guards, not network authentication.
- Use a unique idempotency key for every autonomous message intent.

See [HUMAN_GUIDE_ZH.md](HUMAN_GUIDE_ZH.md) for the Chinese setup guide, [AI_GUIDE.md](AI_GUIDE.md) for agent behavior, and [SECURITY.md](SECURITY.md) for deployment requirements.

## Protocol attribution

The PD01 command framing and GATT details are based on public reverse-engineering work, including [rhnvrm/catprinter](https://github.com/rhnvrm/catprinter), which is MIT licensed.

## License

MIT. See [LICENSE](LICENSE).
