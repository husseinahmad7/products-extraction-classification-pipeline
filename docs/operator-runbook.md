# Operator runbook

This is a development/self-hosted evaluation runbook, not a production authorization. Read `SECURITY.md` and `implementation-status.md` first. Never expose the alpha directly to the Internet.

## Local evaluation

1. Install Python 3.13, Node 24 and uv. From the repository root run `uv sync --frozen --extra server --extra dev --python 3.13`.
2. Run `uv run --no-sync product-pipeline migrate`. This creates/upgrades the configured database; SQLite `pipeline.db` is the default development database.
3. In an interactive local terminal run `uv run --no-sync product-pipeline admin-bootstrap`. Store the displayed token securely. It is not retrievable later.
4. Start `uv run --no-sync product-pipeline serve` and `uv run --no-sync product-pipeline worker` in separate terminals.
5. In `dashboard/`, run `npm ci --ignore-scripts` then `npm run dev`. Vite proxies the API to port 8000. Paste the token into the connect screen. A reload/disconnect clears the browser token.

For a built same-origin dashboard, run `npm run build` in `dashboard/`, set `PIPELINE_DASHBOARD_DIR` to its absolute `dist` directory, and restart the API. No external font/CDN is needed.

### Operator workflow

1. Add a source with its exact domain, then add a target JSON Schema and optional taxonomy.
2. Select the source and supply a UTF-8 captured HTML/JSON sample, target schema ID and stable identity pointers. Compilation queues an operation and produces a draft.
3. Review/edit the proposed recipe. Validate it against the retained sample, inspect validation state, then approve with a meaningful decision reason.
4. Queue the active recipe. A retained artifact hash gives offline execution. Leaving it blank performs live HTML/JSON acquisition, optionally following bounded next/detail links declared on the source. For an authenticated source, configure an HTTPS `auth_ref: env:NAME` and provide that bearer token only to the worker environment; never put the secret in a source document. Browser/PDF live execution is explicitly unsupported.
5. Inspect records and field evidence. Invalid/empty results do not publish. Removals pause publication for an audited approval/rejection. Export a successful immutable dataset revision as JSONL; request replay to check current-engine deterministic hashes.
6. For recurring acquisition, create a schedule for the approved HTML/JSON recipe. Its first run is due after one full interval (60 seconds to 30 days); the worker coalesces missed intervals, and the schedule can be paused or resumed.

This workflow validates a single capture; it does not establish multi-page stability or certify classification. Suggested categories remain uncalibrated/abstained.

## PostgreSQL development stack

Create `.env` locally using `.env.example`, replacing the placeholder with a long random **URL-safe** database password. Do not commit it. `docker compose up --build -d` runs PostgreSQL, migrations, API/dashboard and worker. The API binds host loopback port 8000; bootstrap using `docker compose exec api product-pipeline admin-bootstrap`.

The API and worker share the artifact volume. Multiple hosts require a shared, verified S3-compatible backend rather than independent local disks. All workers must use the same database/workspace and compatible engine version. Use `product-pipeline migrate`, not `init-db`, for tracked upgrades. The migration CLI is packaged in the wheel and does not require a repository-relative Alembic config.

`init-db` is only a development helper and skips migration triggers. Do not adopt an `init-db` database into production by blindly stamping migrations. Create a separate migrated database and validate an explicit migration/import procedure.

## Durable operations and recovery

Every mutation except token rotation/artifact content-addressed upload needs an `Idempotency-Key`. Keep the same key when a network error makes the outcome ambiguous. A reused key with a different body fails. Version-sensitive decisions also require the ETag returned by resource GET in `If-Match`; a 412 means refresh and review the current state.

Polling `GET /v1/operations/{id}` returns queued/running/paused/succeeded/failed/cancelled. A successful operation can still produce an invalid recipe or an extraction awaiting review; inspect its referenced resource. Cancellation fences publication but may wait for a bounded network call to return before the worker stops. Crash reclamation increments generation and disallows stale-worker commits.

### Quota recovery

`GET /v1/storage` returns reserved/stored usage and the accounting limit. When a worker cannot reserve an artifact, its operation/run enters `paused` with `QUOTA_EXCEEDED`; it is not hot-looped or evicted. Successfully captured raw bytes are checkpointed so a resume does not fetch newer content.

First verify/provision actual backing storage. Then call `POST /v1/storage/expand` with a larger `limit_bytes`, a reason and a new idempotency key. This changes an accounting limit, not disk capacity. Call `POST /v1/operations/{id}/resume` with its own idempotency key. Insufficient capacity can pause again. A fresh upload that exceeds capacity returns HTTP 507 and must be retried after expansion. Never erase evidence or reset quota counters to suppress the error.

The dashboard shows quota usage and supports accounting-limit expansion and paused-operation resume. The same endpoints remain available through the HTTP SDK or an authenticated API client. Expansion is not a substitute for provisioning real backing storage.

### Administrator recovery

Token rotation: `POST /v1/admin-tokens/rotate` with `{"overlap_seconds": 300}` (maximum 900). It is intentionally not persisted as an idempotency response because that would store a plaintext token. If the response is lost, use local recovery. `product-pipeline admin-bootstrap --recover` revokes existing tokens and issues a new secret; use only from an authorized local interactive terminal.

## Backup and restore requirements

A disposable PostgreSQL 17 database-only backup/restore was verified on Kaggle; the integration suite passed again against a separate restored database. This is not a validated full-service recovery workflow. Before production, rehearse coordinated database + artifact backups into a separate environment, verify artifact hashes and reference completeness, replay retained runs, and record RPO/RTO. Preserve recipes, schemas, taxonomies, evidence, audit, quota reservations and runtime manifests together. Never test restore by overwriting the only live database or artifact bucket. Destructive Alembic downgrade is disabled; restore a verified backup into a separate database instead.

## CI and gated release

- `ci.yml` runs Python 3.12/3.13 on Linux/Windows, tests/type/lint/schema drift/build, PostgreSQL migrations/claim/append-only checks, S3 integration against a pinned RustFS test container, a complete Docker Compose smoke run, dashboard and documentation checks, dependency audits and a CycloneDX SBOM. These alpha-branch jobs have run remotely; see [implementation status](implementation-status.md) for the exact verification boundary.
- `release.yml` accepts version tags but requires CI plus a ready manifest and the full comparative Kaggle benchmark. The manifest currently blocks release deliberately.
- `pages.yml` validates the static documentation site and deploys `site/` on `main` once GitHub Pages is configured with GitHub Actions as its source. See [documentation site setup](site.md); Pages does not host the dashboard or API.
- Configure protected GitHub environments `pypi` and `ghcr`, an approved PyPI trusted publisher matching repository/workflow/environment, and repository variable `RELEASE_SIGNING_PUBLIC_KEY` containing the release GPG public key. Keep private signing material outside the repository. Create signed tags whose name matches the Python version.
- Packages use OIDC/attestations; images use build provenance/SBOM and cosign against immutable digests. The workflow does not install TLS, provision hosting, migrate a live production database, or imply a SaaS deployment.
- Full atomic publication across PyPI and GHCR is not guaranteed; a partial publisher failure needs an explicit recovery procedure. Do not claim supply-chain verification merely because workflow files exist.

## Diagnostics

`/health/live` checks the process; `/health/ready` checks database connectivity only, not the artifact backend or full capability readiness. Audit event sequence supplies state-change order. Worker logs include operation IDs and must be secured like the source data they may describe. Do not log administrator tokens or signed object URLs.

Use `python scripts/release_gate.py --expect-blocked` during alpha verification. Removing a gate to get a green badge is not a remediation.
