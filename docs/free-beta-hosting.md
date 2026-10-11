# Free beta hosting: Wasmer + Neon

The code has bounded workers and explicit compatibility probes. A public cloud
deployment is **not verified** until the actual locked build, credentials and
provider acceptance checks pass. Local Python or Docker success is not Wasmer
success. No model weights are part of the app package.

The hosting track has tested exact-lock WASIX wheel resolution with PyPI plus
Wasmer's native-wheel registry. It fails on missing native builds, so no Wasmer
package has been published or deployed. Actual WASIX execution remains
`NOT_RUN`; native and disposable CI checks are reported separately. Account
authentication does not establish runtime compatibility or authorize paid plans.
The read-only Wasmer account check passed with `isPro=false`, visible billing
and zero subscriptions. No credential values or account identifiers are retained
in the [checked report](../deploy/wasmer/compatibility-report.json).
Neon management reported the free endpoint idle, and an authenticated MCP
`SELECT 1` resumed PostgreSQL 17.11. A native workstation connection did not
pass, so these control-plane observations are not proof of connectivity from
the application runtime. Actual WASIX PostgreSQL/S3 checks and bounded-worker
idle/resume acceptance remain unverified.

## Limits and architecture

Checked against the official provider documentation on **2026-10-10**:

| Service | Relevant permanent free allowance | Intended use |
| --- | --- | --- |
| Wasmer Hobby | 6 active CPU-hours, 400 GB-hours memory, 1 GB package/volume storage | Small Python API and short scheduled batches |
| Neon Free | 100 CU-hours, 1 GB PostgreSQL storage, 5 GB object storage and 5 GB shared public egress per project | Durable queue, identities, audit and private evidence |

These are ceilings, not promised application capacity. A continuously awake
0.25-CU compute uses 180 CU-hours in a 30-day month. Model training, evaluation
scraping and model weights stay in Kaggle/Colab. Neon AI Gateway has no Free-plan
inference allowance. Remain on the free plans; do not enable a paid fallback.
[Wasmer pricing](https://wasmer.io/pricing), [Neon pricing](https://neon.com/pricing).

Use the existing Python API, not a Neon Functions rewrite. Wasmer documents
FastAPI/Uvicorn support, but the exact Pydantic core, regex, Argon2, Psycopg,
SQLAlchemy and any required greenlet builds must work in **WASIX**. The current
locked synchronous SQLAlchemy runtime does not require its optional asyncio
greenlet dependency. Wasmer's current greenlet
support makes this a feasibility test, not a reason to assume it is unsupported.
Install only base + `server` dependencies from the lock, excluding browser, PDF,
ML and development extras. Build and measure the package before publishing it.
[Supported frameworks](https://docs.wasmer.io/edge/learn/supported-frameworks-and-languages/),
[greenlet support](https://wasmer.io/posts/greenlet-support-python-wasm).

On 2026-10-10, the mixed-index resolution of `uv.lock` first failed on
`argon2-cffi-bindings==26.1.0`. Individual exact-pin checks also failed for
`pydantic-core==2.46.5`, `psycopg-binary==3.3.6`, `cryptography==50.0.2` and
`regex==2026.9.10`. The registry contains older WASIX builds, but silently
downgrading would invalidate the lock and security/runtime checks. This is a
current build-availability blocker, not proof that these libraries can never run
on WASIX. Pure-Python packages such as Alembic resolve from PyPI; absence from the
self-contained WASIX index alone is not a native-runtime incompatibility.
[WASIX Python installation](https://www.wasix.org/docs/language-guide/python/installation/).

Reproduce the wheel-availability check in a disposable dependency directory:

```sh
uv export --frozen --extra server --no-dev --no-emit-project --no-hashes --format requirements-txt --output-file requirements-wasix.txt
python -m pip download --requirement requirements-wasix.txt --dest wasix-wheels --index-url https://pypi.org/simple/ --extra-index-url https://python-registry.wasmer.app/simple/ --platform wasix_wasm32 --python-version 3.13.19 --implementation cp --abi cp313 --only-binary=:all: --disable-pip-version-check --no-cache-dir
```

This downloads runtime dependencies only, never model weights. Re-export after
any lock update and retain the digest. Passing wheel resolution would still
require native-operation, HTTP, PostgreSQL and S3 checks in the actual runtime;
cross-platform dependency markers also need confirmation inside that build.

## Bounded dispatch

```sh
uv run --no-sync product-pipeline worker --batch --max-jobs 3 --max-seconds 120
```

The default `worker` invocation is a bounded batch. `--once` is a bounded batch
of one job. The worker exits after the first idle claim, the job budget, or its
cooperative deadline, and prints a JSON summary. Public Neon-auth mode claims
across tenants with a per-claim quota store and the public-source/resource guard.
The legacy single-workspace polling loop requires explicit `--forever` and is
rejected in public mode.

Scheduled beta work reserves job, runtime, page, source-byte and lifetime metadata
budgets before any run or job row is created. A denied admission rolls back all
partial reservations and pauses that schedule with an audit reason. It does not
enqueue another failing job every tick. Resume through the existing schedule API
with its current ETag after the source approval or capacity issue is resolved;
source approval is checked again when an admitted job executes.

The deadline checks processing stages and page boundaries and clamps live HTTP
timeouts. It is **not a hard process timeout**: CPU work or blocked DB/S3 calls
can exceed it. Use a 180-second host timeout around a 120-second batch. Cooperative
yields requeue with `BATCH_DEADLINE`, do not consume crash retries, and reuse
retained pages/raw bytes/snapshots. Hard eviction recovers through expiring leases
and generation fencing; repeated actual crashes still use the dead-letter policy.

Start with a **30-minute** dispatch interval. Missed schedule intervals coalesce;
scheduled jobs do not imply continuous workers. The template at
`deploy/wasmer/app.yaml.example` uses Wasmer's documented execute-job interface.
It requires a verified package exposing a `python` command; it does not build that
package or provision resources. Provider retries are disabled because the durable
queue owns recovery. [Wasmer jobs](https://docs.wasmer.io/edge/configuration/jobs/).

Probe `/health/live` for liveness. Never poll `/health/ready`, jobs, usage, audit or the
dashboard continuously when idle: these touch the DB and can prevent suspension.
Disable external SQL editors/monitoring while verifying a quiet window. Neon
documents a five-minute Free-plan inactivity threshold, but the tested endpoint's
management timestamps showed roughly ten minutes between last activity and
suspension. This is not yet an end-to-end bounded-worker idle/resume measurement.
Until that gate passes, budget a ten-minute awake tail: an empty 30-minute cron
alone can accrue about 60 CU-hours/month at 0.25 CU, before API traffic or actual
batch work. A 15-minute cron under the same assumption would exceed the Free
compute allowance. Recheck actual provider meters and suspension behavior.
[Scale to zero](https://neon.com/docs/introduction/scale-to-zero).

## Compatibility probes

Run the following on native Python first, then **inside the actual Wasmer build**:

```sh
uv run --no-sync python scripts/hosting_probe.py
python -m product_pipeline.hosting_probe --runtime wasmer --lock uv.lock
```

The report captures the lock digest, installed dependency versions, native
operations, application startup and query-free liveness. It does not print
credentials, connection strings, endpoint URLs or driver exception messages.
Missing remote credentials are `NOT_RUN`, never a fake PASS.

For an actual private test deployment, inject credentials into the runtime's
secret store, then opt into the remote checks:

```sh
python -m product_pipeline.hosting_probe --runtime wasmer --lock uv.lock --postgres --s3-write-probe --neon-idle-probe --require-ready
```

Required secret configuration:

- `PIPELINE_DATABASE_URL`: pooled `postgresql+psycopg://...` application connection
  with TLS. Use a separate **direct** connection for reviewed Alembic migrations.
- `PIPELINE_S3_ENDPOINT`, `PIPELINE_PROBE_S3_BUCKET`, `AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`: an existing **private probe bucket** on
  the chosen provider. The probe does not create buckets and does not use the
  application evidence bucket implicitly.
- `NEON_API_KEY`, `NEON_PROJECT_ID`, `NEON_ENDPOINT_ID`: read-authorized management
  access for the opt-in idle test. These are probe-only; do not add a management
  credential to the ordinary public API or worker.

Use Wasmer's secure environment editor, not committed YAML or shell commands
containing credentials. Confirm the Neon project region supports Object Storage
(currently AWS us-east-2, us-east-1, eu-central-1 or ap-southeast-1).
[Wasmer secrets](https://docs.wasmer.io/edge/learn/secrets/),
[Neon connection pooling](https://neon.com/docs/connect/connection-pooling),
[Neon Object Storage](https://neon.com/docs/storage/overview).

The S3 probe creates uniquely named synthetic objects, verifies conditional
creation, a two-writer race, the AES256 response/header contract, application
deduplication and hash-mismatch rejection, then deletes only those probe objects.
A cleanup failure fails the report. An echoed encryption header is a wire-contract
check, not independent proof of physical encryption at rest; retain the provider's
security contract. Do not drop `IfNoneMatch` or encryption requirements to force
compatibility. Neon documents only core S3 operations; lifecycle/versioning rules
are not enforced, so they cannot be our evidence retention mechanism.
[Neon S3 compatibility](https://neon.com/docs/storage/s3-compatibility).

The idle probe defaults to 310 seconds; use `--idle-seconds 650` when verifying
the observed ten-minute endpoint tail. It verifies the management endpoint
matches the tested database, performs one read-only connection, closes it, makes
**no database queries** during the quiet window, checks the provider reports
`idle`, and measures the resumed connection. It only uses management GETs and
never forcibly suspends, resizes or reconfigures compute. Concurrent clients can
make the check fail; that is useful evidence, not a reason to bypass it.
[Neon compute endpoint API](https://api-docs.neon.tech/reference/getprojectendpoint).

## Opening the beta

Keep public signup disabled at the hosting layer until the locked WASIX runtime,
actual PostgreSQL connection, S3 evidence semantics and idle/resume checks pass.
Then verify tenant isolation, approved-domain rejection, quota pauses and recovery
after a host timeout. Record real package size, CPU and memory consumption and
provider usage alongside the report. Application quotas are conservative workload
reservations; they are not a substitute for checking account-wide provider meters.

Only reviewed model suggestions are enabled. Cloud deployment does not certify
classification accuracy or unlock automatic model promotion, full release,
billing, browser acquisition or live PDF acquisition.
