# Evidence Pipeline

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Approved implementation plan: Python SDK/CLI, FastAPI control plane, React dashboard,
PostgreSQL jobs and metadata, S3-compatible immutable artifacts. Rust is an optional
measured acceleration experiment, not an assumed rewrite.

## Users

The initial operator is a developer/data practitioner configuring extraction,
reviewing records, and investigating failed runs. Broader customer personas and
commercial demand remain unvalidated.

## Product Purpose

Turn allowed web sources into schema-validated, versioned datasets with field-level
evidence and explicit human decisions. Add supported sources and taxonomies by
configuration, without adding site-specific core code.

## Operating Context

Single-workspace, self-hosted developer tool first. Operators configure a source,
target schema and identity rules, capture evidence, validate a proposed recipe,
approve it, run extraction, inspect differences, and replay retained evidence.

## Capabilities and Constraints

The approved release includes static HTML, rendered HTML, JSON APIs and PDFs.
Implementation status is tracked separately; the dashboard must distinguish
canonical-fixture support from live acquisition support.

Classification is optional and abstains without a certified calibration policy.
Laya and competing baselines must be evaluated using human-reviewed, source-disjoint
labels on Kaggle. No model weights or adapters may pass through this workstation.
Existing predicted product categories are not gold labels.

The release is gated on the completed benchmark. SaaS billing and multi-tenancy
are deferred. Apache-2.0 was selected for the new project code. Preserve the
original research notebook and data.

## Evidence on Hand

The original notebook and CSVs describe the prior experiment, not production
accuracy. There are no verified customer, revenue, uptime, or accuracy claims.
The approved rough evidence-lifecycle wireframe establishes information structure,
not proof of completed functionality or a polished visual design.

## Product Principles

- Every published field must be traceable to captured evidence.
- Unsupported capabilities fail explicitly; partial data is never disguised as success.
- Review decisions and dataset publications are auditable and concurrency-safe.
- Useful offline extraction does not depend on a paid API or a language model.
- Performance changes require parity and measured benefit.
