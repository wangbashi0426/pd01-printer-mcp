# Security Policy

This project controls physical hardware and can consume paper.

## Safe deployment requirements

- Keep the default `127.0.0.1` bind address for local use.
- Require HTTPS and authentication before remote exposure.
- Apply request-size and rate limits at the network edge.
- Never commit real device addresses, server addresses, credentials, keys, logs, or print content.
- Treat `CONFIRM_PRINT` as a workflow guard, not as authentication.
- Put the cloud relay behind authenticated HTTPS; `RELAY_WORKER_SECRET` protects
  worker operations only and does not authenticate message-enqueue tools.
- Store relay data on a persistent encrypted volume where the host supports it.
- Preserve the local receipt ledger. Deleting it can allow a delivered job to
  print twice after an acknowledgement failure.
- Rotate both the edge access token and worker secret after suspected exposure.

## Reporting a vulnerability

Open a GitHub security advisory or contact the repository maintainer privately. Do not include credentials, private print content, or identifying device data in a public issue.
