# Architecture and invariants

The product is a reusable evidence-first Python SDK/CLI and a single-workspace operator service. Schema, taxonomy and recipes are user data, not vendor-specific classes. Generality means new records and supported sources can be configured; unsupported login flows, navigation patterns, live browser/PDF acquisition or extraction structures must fail explicitly.

```text
SourceSpec + TargetSchema + retained capture
                  |
           assisted draft compiler
                  |
         validation -> human decision -> active recipe
                                              |
API + idempotency -> PostgreSQL durable job -> fenced worker
                                              |
                           raw capture -> canonical artifact -> extraction
                                              |
                    schema + identity checks + per-field evidence
                                              |
                    quarantine / removal review / CAS publication
                                              |
                        immutable revision + audit + outbox
```

## Contracts and deterministic engine

`contracts.py` owns frozen, extra-field-forbidding v1 contracts; `scripts/export_schemas.py` generates public JSON Schemas. `engine.py` is pure offline extraction. Canonical JSON uses RFC 8785, artifact/recipe/result identities use SHA-256, record IDs bind workspace + source + declared identity values, and output record order is stable. Exact duplicate records merge their evidence; conflicting identities quarantine every conflicting instance. Missing, null, observed and derived fields remain distinguishable.

Locators and transforms are separate. A valid transform must serialize into another valid transform; emitted parameters belong only to its operation. Normalization is independent of acquisition so a quota-paused job can resume from raw bytes without recapturing mutable source content.

The assisted compiler proposes mappings from a single retained sample and a bounded schema. It never activates a recipe. Multi-capture stability, perturbation testing and richer mapping repair are still planned. Offline replay currently compares current-engine record/evidence hashes; it does not reproduce an old retained OCI runtime.

## Control plane and worker

FastAPI is the control plane. HTTP SDK and dashboard call the same API. Long-running compile, validation, extraction and replay actions return an operation ID/Location. Durable PostgreSQL jobs are claimed with `FOR UPDATE SKIP LOCKED`, leased, heartbeated and committed under owner + generation + lease fencing. Retryable failures back off; repeated crashes reach a terminal failure. Cancellation invalidates the publication fence. It cannot immediately interrupt a blocking socket read.

State-sensitive recipe activation and review decisions use optimistic resource versions. Dataset publication performs a head-version compare-and-swap so concurrent runs cannot silently overwrite a newer revision. An audit event and outbox row commit with each protected transition. External outbox delivery is not implemented yet.

The worker stores raw/canonical captures before publishing results. Each successful capture is checkpointed behind its job fence. Quota reserves bytes under a locked workspace row before storage writes; reservations survive failures, retries deduplicate by digest, and no background eviction occurs. A quota-paused operation stays unclaimable until explicitly resumed after capacity expansion. Expansion changes an accounting limit only; it does not provision backing disk/S3 capacity.

PostgreSQL is required for multi-worker deployment. SQLite test success is not evidence that PostgreSQL concurrency guarantees hold. Local filesystem artifacts are appropriate only when workers share that filesystem; S3-compatible storage is an optional, not yet integration-verified backend.

The private Kaggle PostgreSQL 17 integration run verified skip-locked claims, stale-generation rejection after actual process death, eight-writer quota reservations, immutable resources and append-only audit guards. Migration repeatability/schema checks and a database-only backup/restore also passed. Retained report/source hashes are in `benchmarks/reports/postgres-integration-pg17.json`; this evidence does not certify distributed throttling, object storage, coordinated recovery or production isolation.

## Classification

Current taxonomy matching returns ranked suggestions plus `UNCALIBRATED` abstention. No model score is silently promoted to a classification. The offline benchmark evaluator enforces group-disjoint splits, calibration-only policy fitting, test-only evaluation, coverage and Wilson precision gates. A future classifier registry must bind model, taxonomy, dataset and calibration-policy hashes before enabling auto-accept; it is not yet wired into runs.

Kaggle is the experiment environment, not a public production inference service. Workstation code never loads remote model weights. Rust remains a measurement-gated candidate; rewriting orchestration into Rust would not fix schema drift, calibration, evidence or access-control defects.
