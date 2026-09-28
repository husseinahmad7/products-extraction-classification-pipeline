# Evidence Pipeline

Declarative extraction with field-level evidence, offline replay, and an operator dashboard. Built from the original product-scraping notebook into a Python package and self-hosted service.

**Development alpha — the approved full-release plan is not complete. Do not deploy as a public SaaS yet.** The release workflow fails closed until the missing capability and comparative-benchmark gates are met. See [implementation status](docs/implementation-status.md).

The developer-first [GitHub Pages documentation source](site/index.html) has an [authoring and deployment guide](docs/site.md). The public site goes live only after the Pages workflow runs from `main` and the repository is configured for GitHub Actions publishing.

## What works

- Define sources, schemas and extraction recipes as data; supported new sources do not require site-specific Python classes.
- Extract from HTML/JSON, rendered-HTML captures, and canonical PDF text blocks. Live acquisition supports bounded HTML/JSON navigation and HTTPS bearer credentials referenced from the environment.
- Retain content-addressed captures and per-field locators, raw values, transforms, errors and hashes. Replay saved captures without network access.
- Quarantine invalid/ambiguous identities, compare semantic dataset changes, and require an audited decision for removals.
- Use a durable job queue with leases, generation fencing, idempotency, optimistic versions, interval schedules, shared origin throttling and quota pause/resume.
- Manage sources, schemas, taxonomies, proposed recipes, jobs, evidence, reviews, revisions and audit events through the React dashboard.
- Request taxonomy suggestions without an API key. Suggestions always abstain until a separately calibrated classifier is certified.

This is configuration-driven within a bounded recipe language, **not a promise to scrape every site without work**. Navigation and environment-referenced bearer authentication cover supported HTML/JSON sources; complex login flows, browser/PDF isolation and several production controls remain unfinished.

## Offline quick start

Requires Python 3.12 or 3.13. No API key, model weights, GPU, browser, or paid service is needed for these examples.

```sh
python -m pip install -e .
product-pipeline validate examples/catalog-recipe.yaml
product-pipeline extract examples/catalog-recipe.yaml examples/catalog.json --output catalog-result.json
product-pipeline replay examples/catalog-recipe.yaml examples/catalog.json catalog-result.json
```

The second example extracts documentation metadata rather than products:

```sh
product-pipeline extract examples/documentation-recipe.yaml examples/documentation.json --output docs-result.json
```

Python SDK:

```python
from pathlib import Path
import yaml
from product_pipeline import Recipe, extract, replay

recipe = Recipe.model_validate(yaml.safe_load(Path("examples/catalog-recipe.yaml").read_text()))
capture = Path("examples/catalog.json").read_bytes()
result = extract(capture, recipe, workspace_id="default")
assert replay(capture, recipe, result, workspace_id="default") == result
print(result.records[0].data)
```

Public contracts are generated in [schemas/](schemas/). Recipes support CSS text/attribute locators, a bounded JSONPath subset, JSON Pointer, JSON-LD and canonical PDF labelled values. Arbitrary Python/JavaScript execution is not part of the recipe language.

## Dashboard and worker

See the [operator runbook](docs/operator-runbook.md) for token bootstrap, local development, PostgreSQL, quota recovery and release setup.

```sh
uv sync --frozen --extra server --extra dev --python 3.13
uv run --no-sync product-pipeline migrate
uv run --no-sync product-pipeline admin-bootstrap
```

In separate terminals, start the API, worker and dashboard development server:

```sh
uv run --no-sync product-pipeline serve
uv run --no-sync product-pipeline worker
```

```sh
cd dashboard
npm ci --ignore-scripts
npm run dev
```

Open the URL printed by Vite and enter the locally bootstrapped token. Tokens are held in browser memory only. SQLite is a single-process development convenience; it does not establish PostgreSQL concurrency guarantees.

For a PostgreSQL-based development deployment, configure `.env` from `.env.example`, then run `docker compose up --build -d` and bootstrap with `docker compose exec api product-pipeline admin-bootstrap`. The [PostgreSQL 17 integration suite](benchmarks/reports/postgres-integration-pg17.json) passed in a private Kaggle notebook, including process-death recovery, quota races and a database-only restore. Docker/Compose and coordinated database-plus-artifact recovery remain unverified. The supplied Compose stack is **not an Internet-ready production deployment**.

## Classification and Kaggle

The package does not download or load Laya. All model experiments belong in Kaggle through MCP; notebook scripts refuse to run outside `/kaggle/working`. Model caches stay in `/tmp`, outside notebook outputs. Only metrics and review artifacts return to this repository.

The [Laya smoke report](benchmarks/reports/laya-smoke.json) verifies a pinned model loaded and ran on a Kaggle T4; it makes no quality claim. The [benchmark protocol](docs/benchmark-protocol.md) defines independent data splits, calibration, abstention, and promotion criteria. The legacy CSV contains model predictions, not gold labels. See the [AI spot review](benchmarks/review/ai-spot-review.json) and [structural audit](benchmarks/reports/legacy-audit.json).

The completed [exploratory comparison](benchmarks/reports/ai-reviewed-diagnostic.json) used 52 AI-reviewed records, of which only 23 had proposed leaf labels. Agreement on those 23 was 2 for taxonomy TF-IDF, 6 for frozen embeddings, and 0 for Laya with embedding-retrieved candidates. These are small-sample diagnostic agreements, **not independently measured accuracy**. This configuration does not justify enabling Laya in production; the supervised baseline and full independent comparative benchmark for the original two-vendor corpus remain unrun. All 1,095 original rows received a structural audit, but only 52 received a semantic spot review.

A separate [external building-materials diagnostic](benchmarks/reports/external-building-diagnostic.json) ran on Kaggle T4 with 663 deduplicated product titles, manufacturer-disjoint train/calibration/test splits (406/102/155), and five inherited source-list categories. On the 155-title test split, macro-F1 was 0.621 for trained TF-IDF linear, 0.554 for Laya, 0.478 for frozen embeddings and 0.129 for taxonomy TF-IDF. These publisher list labels were **not independently reviewed product-type gold**, and this is a different taxonomy from the original two-vendor corpus. The result does not certify accuracy, enable auto-accept, or clear the full-release gate.

## Development and delivery

```sh
uv run --no-sync ruff check src tests scripts benchmarks
uv run --no-sync ruff format --check src tests scripts benchmarks
uv run --no-sync mypy src/product_pipeline
uv run --no-sync pytest --cov=product_pipeline
uv run --no-sync python scripts/export_schemas.py --check
uv run --no-sync python -m build
uv run --no-sync python scripts/release_gate.py --expect-blocked
```

GitHub workflows define Python/OS matrix tests, PostgreSQL checks, MinIO/S3 integration, a Docker Compose smoke run, dashboard and documentation checks, dependency audits and an SBOM. Those new container/object-store jobs are configured but have not run remotely. Signed tags, PyPI OIDC, attestations and signed container digests are configured as a gated release workflow, not already-published artifacts. GitHub Pages publishes static documentation only; no cloud/SaaS deployment is implied.

Python is the reference engine. Rust is not selected or shipped without golden parity and measured throughput/memory benefit. Billing and multi-tenancy are deliberately deferred until product validation.

## Project history and license

The original `products_pipeline.ipynb`, `technical_approach.md`, datasets and images remain unchanged. The original README is preserved in [docs/legacy-notebook.md](docs/legacy-notebook.md).

New project code is Apache-2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party models, dependencies and scraped content retain their own licenses/rights. See [SECURITY.md](SECURITY.md) before hosting or collecting data.
