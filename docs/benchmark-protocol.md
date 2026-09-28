# Classification benchmark protocol

## Data is not gold merely because it has a category column

The original CSV contains 1,095 model-predicted rows from only Sika and Flex. The reproducible audit is `python scripts/audit_legacy_data.py`. It checks every record structurally and merges explicit AI spot reviews into a separate output; original records are never overwritten. `benchmarks/review/ai-spot-review.json` is AI-reviewed, not human-reviewed. Its 23 proposed labels and 29 abstentions do not certify accuracy; 1,043 records have not received semantic review.

The audit flags repeated products/URLs, missing/unknown category IDs and duplicate taxonomy names. Product variants and identical content must remain in one split. Near duplicates require manual or documented similarity adjudication, not only exact text hashing. Existing predictions must be hidden from annotators; ambiguous, unsupported and insufficient-evidence cases remain explicit rather than being forced into a leaf.

## Freeze before training

- At least 400 independently reviewed labels across at least five real product-source/vendor groups. Random URL partitioning within the same vendor is not source-disjoint validation.
- Separate train, calibration (at least 100), and untouched test (at least 150) partitions; each source group belongs to exactly one partition. Group allocation must preserve meaningful coverage of the declared certified taxonomy subset. Two existing vendors cannot satisfy this.
- Keep stable IDs, source URLs, evidence hashes, reviewer identity/time, taxonomy version and allowed subset. Do not relabel AI output as human review. Freeze dataset and split-manifest hashes before any test inference.
- Duplicate taxonomy IDs with equivalent names and unsupported categories must be resolved/versioned before annotation. Models cannot repair a contradictory label ontology by scoring more confidently.

The `BenchmarkDataset` contract enforces minimum sizes, group isolation, leaf membership and exact normalized-text deduplication. These mechanical checks do not attest a reviewer identity or detect every semantic near duplicate. Governance remains required.

## Comparative run (Kaggle only)

Compare TF-IDF taxonomy retrieval, TF-IDF + a trained linear classifier, frozen-embedding retrieval/classification, and Laya under the same frozen taxonomy/data protocol. Train only on train, select/calibrate policies only on calibration, then evaluate untouched test once for release selection. A Laya OOM/latency/quality failure is a measured result, not a reason to skip the comparison. Persist runtime/package versions, model revisions, input hashes, seed, GPU model, batch policy and per-example timings.

The scripts in `benchmarks/` refuse workstation execution. Use Kaggle MCP to submit private notebooks; caches are under `/tmp`, not notebook outputs. Fetch checkpoint weights directly inside Kaggle. Never download, upload, synchronize or commit model weights/adapters through this workstation. Return metrics, predictions, provenance and logs only.

The completed synthetic smoke answers only: can the pinned Laya runtime load and infer on a T4? The exploratory diagnostic uses 52 AI-reviewed records, taxonomy TF-IDF, a frozen embedding model and top-20-shortlisted Laya; its comparison is labelled AI-proposal agreement, not gold accuracy. It has no train/calibration/test split and cannot clear release gates. CPU diagnostic timings must not be represented as T4 timings.

## External source-list diagnostic

An additional, deliberately **non-certifying** five-class diagnostic is staged in
`benchmarks/kaggle_external_building_diagnostic.py`. The [Hong Kong Buildings
Department catalog](https://data.gov.hk/en-data/dataset/hk-bd-opendata-cdbbm)
publishes seven building-material CSVs; a remote-only audit found 880 populated
records across the seven lists. Five sufficiently populated lists account for 861
raw records and are pinned by SHA-256 in the script. The catalog is available for
commercial and non-commercial reuse with source acknowledgement under its
[terms](https://data.gov.hk/en/terms-and-conditions). The script fetches CSVs and
any model weights **inside Kaggle only**. It uses product titles as inputs,
withholds manufacturer names and source category fields, removes exact-title
duplicates/conflicts, and splits by normalized manufacturer group before any
test inference. It compares taxonomy TF-IDF, trained TF-IDF linear, frozen
embeddings, and Laya on the same held-out rows.

These labels are inherited from the publisher's source lists. They are not
independent project annotations of the product-text task, and the lists are a
five-class subset of one publisher rather than five product-source websites.
Aliases and near duplicates may remain after mechanical grouping. This
diagnostic can reveal gross transfer failures and runtime costs; it cannot
certify the original Sika/Flex taxonomy, authorize auto-accept, or satisfy the
full release gate.

The [private Kaggle T4 comparison](https://www.kaggle.com/code/husseiniahmad/evidence-pipeline-building-comparison)
(version 1) completed on 663 deduplicated rows with a manufacturer-disjoint
406/102/155 train/calibration/test split. The calibration partition was reserved
but not used for threshold fitting; this run reports closed-set top-one metrics,
not a selective-classification policy. On 155 held-out rows, trained TF-IDF
linear reached macro-F1 0.621 and p95 1.16 ms; Laya reached 0.554 and 39.31 ms;
frozen embedding retrieval reached 0.478 and 6.70 ms; taxonomy TF-IDF retrieval
reached 0.129 and 0.60 ms. Class-level recall is uneven, and source-list labels
may not align perfectly with product types. The full per-case predictions,
confusion matrices, runtime versions, input hashes, and split hash are in
`benchmarks/reports/external-building-diagnostic.json`, with script/report
hashes in `benchmarks/reports/external-building-attestation.json`. Laya is
neither higher quality nor within the latency promotion gate on this diagnostic.

A second candidate, [FireApproved's CC BY 4.0 CAL FIRE OSFM census](https://www.kaggle.com/datasets/fireapproved/osfm-bml-censuses),
was audited remotely at 276 records across 16 listing codes. It is separately
sourced and potentially useful as external validation, but its categories are
not interchangeable with the Hong Kong taxonomy. Any mapping must be frozen
and reviewed before comparison; concatenating the two labels would fabricate a
common gold standard.

## Calibration and certification

`fit_policy` consumes exactly calibration predictions, fits a temperature and chooses an acceptance threshold. `evaluate` consumes exactly test predictions and the frozen dataset-bound policy. Every probability vector covers precisely the taxonomy leaves. Raw model confidence and cosine similarity are not calibrated probabilities.

Report leaf accuracy, micro/macro F1, ancestor/path accuracy, tree distance, Brier score, ECE, coverage, correct auto-accept coverage, selective risk, p50/p95 latency, sequential throughput, cold start and peak memory. For each activated declared group: at least 100 accepted test examples, a 95% Wilson lower bound on precision of at least 0.95, and at least 0.70 coverage unless another target was frozen before testing. If the gate fails, abstain; do not lower the bar after looking at test results.

Promote Laya only if licensing is compatible, it fits the target T4 envelope, p95 is at most 2x the baseline, and it delivers either at least +0.03 macro F1, or is within -0.01 macro F1 while improving ECE or **correct** auto-accept coverage by at least 25%. Compare against the strongest eligible baseline, not a weak cherry-picked one. Quality improvements do not remove the deployment/latency gate.

## Rust gate

Keep Python as the reference. A Rust implementation must match 100% of the full golden corpus, including canonical hashes, evidence and edge cases; then show at least 2x throughput or at least 40% lower peak RSS without p95 regression on representative workloads. A microbenchmark of an unrelated primitive is not sufficient. No Rust implementation is promoted by this alpha.

## Release gate

The full comparative Kaggle benchmark must finish before the approved full release, even if the decision is to keep a baseline. `NOT_RUN`, smoke-only, and AI-labelled exploratory runs cannot unlock `scripts/release_gate.py`. Auto-accept and model integration remain disabled until independent certification and registry binding are implemented.
