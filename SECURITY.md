# Security and trust boundaries

This is a development alpha, not a security-certified public service. Keep it on localhost or a private test network with non-sensitive source data until the production gates in `docs/implementation-status.md` are verified.

## Implemented boundaries

- Administrator bearer credentials are random secrets, stored as Argon2 hashes. Bootstrap/recovery requires a local TTY; rotation supports a bounded overlap. The UI retains the token in memory, not local/session storage or cookies.
- API mutations require idempotency keys; version-sensitive decisions require `If-Match`. Request bodies are bounded before JSON parsing. Tokens and source credentials must never be written into audit events.
- The recipe language does not execute arbitrary code. JSON rejects duplicate keys and non-finite values. Schema references and unbounded schema regular expressions are rejected. Supported regex transforms have a timeout.
- HTTP acquisition requires exact allowlisted hosts, public resolved IPs, HTTP(S), ports 80/443, a pinned connection and normal TLS certificate checks. Redirects recheck policy. Private/link-local/loopback DNS, HTTPS downgrades and compressed responses are rejected.
- `robots.txt` is respected and failures are closed; delays are enforced within an acquisition. Robots compliance is not authorization: operators remain responsible for permission, site terms, privacy and rate limits.
- Content-addressed artifacts are verified on read. Quota reservations include failed/in-progress writes and survive crashes. Capacity exhaustion pauses work; evidence is not evicted to make room.
- PostgreSQL migration triggers reject audit updates/deletes and immutable revision/schema/taxonomy updates/deletes. Fenced commits prevent stale workers from publishing. Operators with database-owner privileges can still alter triggers; these are not tamper-proof storage.
- Dashboard rendering escapes source text; it does not execute captured HTML. API replies are no-store, and security headers restrict framing, sources and referrers.

## Not yet a production boundary

There is no sandboxed browser/PDF execution, distributed per-origin limiter, fully bounded DNS/header wall-clock enforcement, remote authentication profile vault, multi-tenant authorization, external audit sink, tested backup/restore, retained OCI replay, or verified public deployment. The transactional outbox is persisted but no external dispatcher is implemented. Source parsing still runs in the worker process; memory/CPU isolation is incomplete.

The Compose database account owns its development schema. Production requires separate migration and least-privileged application roles, private database networking, TLS, controlled egress, monitoring and tested restore procedures. S3 uses conditional immutable writes and server-side encryption, but bucket versioning/Object Lock and MinIO KMS compatibility have not been verified here. Do not represent audit logs as legally tamper-proof.

Treat captures and model inputs as untrusted data. Models must never gain network, filesystem, secret or deployment authority from source text. Raw confidence is not a calibrated probability. Do not bypass an abstention or classification gate because a model sounds certain.

## Reporting

Use the repository's private vulnerability-reporting channel if enabled, or contact the maintainer privately. Do not publish administrator tokens, signed object URLs, private source documents, or unredacted exploit payloads in an issue. Never commit `.env`, model checkpoints, or bearer credentials.
