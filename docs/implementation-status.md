# Implementation status — development alpha

This document separates implemented code from runtime verification and remaining work. It does not redefine the approved plan into an independently shippable first milestone. The intended full release remains blocked.

| Area | Implemented | Still needed for approved full release |
| --- | --- | --- |
| Contracts / extraction | Strict source/schema/taxonomy/recipe contracts, Python offline engine, transforms, identity, field evidence, generated schemas, two domain examples | Broader golden corpus, complete capability validation |
| Acquisition | DNS-pinned bounded HTML/JSON; fail-closed robots; bounded next-link/detail-link discovery, HTTPS environment-referenced bearer credentials, shared origin reservations, raw/canonical page checkpoints | Complex login-flow profiles; fully isolated browser/PDF workers and resource limits; broader live-source compatibility tests |
| Assisted compiler | Single-sample bounded proposals; validation and operator activation | Multi-sample verification, perturbation tests, stability/repair gates |
| Durable execution | Leases, generation fencing, retry/dead-letter, idempotency, cancellation, CAS publication, removal review, interval scheduling and origin rate enforcement; earlier PostgreSQL 17 claim/quota/process-death tests | External outbox delivery; broader schedule/rate fault and load testing |
| Evidence / storage | Content-addressed local/S3 implementations, hash verification, durable quota reservations, pause/expand/resume | S3/MinIO integration, retention/roles, backup-restore rehearsal, retained-runtime OCI replay |
| Classification | Always-abstaining no-key suggestions; independent-split/calibration metrics and promotion evaluator | Full comparative Kaggle benchmark; model registry, corrections, certified classifier activation |
| Review data | Structural audit of all 1,095 original records; explicit AI semantic spot review of 52 records | 1,043 records not semantically reviewed; independent gold/adjudication, taxonomy cleanup, at least three additional vendor groups |
| Dashboard | Real CRUD subset and lifecycle views, operations, schedules, evidence, errors, cancellation, replay, decision reasons, storage controls, token rotation, exports, audit | Browser/rendered/accessibility E2E verification, broader settings and correction workflow |
| Packaging / delivery | Apache license, Python wheel/sdist, lockfiles, Docker/Compose source, CI matrix/audits/SBOM, gated signed release configuration, static GitHub Pages docs source/workflow; PostgreSQL migrations and database-only restore tested on Kaggle | Run workflows remotely, including new MinIO/S3 and container smoke jobs; configure Pages and protected publishing environments; verify signing trust, PyPI OIDC, real deployment/coordinated restore |
| Rust / SaaS | Explicit promotion and demand-validation decision gates | Rust parity/performance spike; no Rust promotion, billing, multi-tenancy or public SaaS release yet |

## Verification boundary

The last full local Python suite completed with **84 passed, 6 skipped, and 83% line coverage**. Its six skipped tests require PostgreSQL. The focused acquisition/security suite also passed all 18 tests after the final IP-address normalization change. Local JUnit results are retained under `.artifacts/verification-final.xml` and `.artifacts/security-final.xml` (not committed). The dashboard has five passing component tests and a successful production build; its dependency audit reports no known vulnerabilities. Ruff, formatting, generated schemas and mypy passed; wheel/source-distribution builds succeeded. These local checks do not exercise S3, containers, or a rendered browser.

After explicit permission to upload backend code and tests (no credentials, datasets or model files), **seven PostgreSQL integration cases passed on Kaggle**, including a newly added real process-death recovery case. The PostgreSQL 17.11 run also passed five additional eight-writer quota-race repetitions, migration repeatability/drift checks, and the seven-case suite after a database-only backup/restore. [The retained report](../benchmarks/reports/postgres-integration-pg17.json) identifies private notebook version 2 and the exact source bundle/per-file hashes. [Version 1](../benchmarks/reports/postgres-integration-pg14.json) also passed on PostgreSQL 14.24. These are seven distinct cases with repetitions, not 19 distinct tests. Object-store recovery, coordinated service restore and production security isolation are still unverified.

Local verification uses Python 3.13 and Node 24. Python 3.12 is configured in CI but its local interpreter encountered a native environment crash, so no local 3.12 pass is claimed. Docker/PostgreSQL services were unavailable on this workstation. The new S3 integration tests skip explicitly when no object-store endpoint is configured; three such skips were verified locally. CI has not yet run the MinIO or complete-container jobs. Browser capture/control was previously unavailable because of the Windows sandbox ACL-helper failure; static design review and component tests are not a rendered dashboard visual or accessibility sign-off.

The Kaggle Laya infrastructure smoke completed on a T4. The separate AI-reviewed exploratory diagnostic is not the required comparative release benchmark. Read the machine-readable reports for exact status; neither synthetic examples nor AI labels can satisfy the independent-gold certification gate.

A further [external building-materials diagnostic](../benchmarks/reports/external-building-diagnostic.json) completed on a private Kaggle T4 notebook. It deduplicated 861 Hong Kong Buildings Department list rows to 663 titles across 250 manufacturer groups, then used manufacturer-disjoint train/calibration/test splits of 406/102/155. On the 155-title test split, trained TF-IDF linear reached 0.621 macro-F1, Laya 0.554, frozen embeddings 0.478, and taxonomy TF-IDF 0.129. Categories were inherited from source lists rather than independently adjudicated product labels, and the five-class title-only task differs from the original two-vendor taxonomy. This is diagnostic evidence, not release gold; classifier auto-accept remains disabled and the release gate remains blocked.

`benchmarks/release-manifest.json` remains `blocked`. Its capability booleans mean **verified for the approved full release**, not merely present in alpha source; implemented-but-unverified navigation, scheduling and throttling therefore remain false there. CI and GitHub Pages configuration are present but have not been pushed/run remotely. No package, image, documentation site, PR, or SaaS deployment has been published from this implementation.

## Required next evidence

1. Run container/S3 integration and browser E2E suites in a suitable environment; complete the missing capability implementations above and extend fault/load testing beyond the verified database cases.
2. Resolve taxonomy gaps/duplicates, acquire permissioned additional vendor groups, and obtain independent adjudication before freezing benchmark splits.
3. Complete all baseline/Laya experiments on Kaggle with calibration-only fitting and untouched test evaluation, even if Laya loses.
4. Verify recovery, retained OCI replay and security/role boundaries. Evaluate Rust only against the reference golden corpus and measured performance gate.
5. Only then mark release capabilities ready, configure protected publishing environments and create a signed release tag.
