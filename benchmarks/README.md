# Remote-only training comparison

`kaggle_training_comparison.py` compares trained TF-IDF, encoder-fine-tuned SetFit
BGE-small-en-v1.5, zero-shot GLiClass-edge-v3.0 with runtime taxonomy descriptions,
English Laya, and full encoder-and-head RLCD-fine-tuned Laya. All model and source
downloads, training, and checkpoint hashing happen **inside a private Kaggle job**.
Do not run it on a workstation, synchronize its caches, or retrieve checkpoints.

Build the notebook payload locally with
`python benchmarks/build_training_notebook.py`. This command reads two Python
source files and prints JSON to stdout; it does not fetch anything. Send its
`text` through Kaggle MCP `save_notebook`, with `isPrivate=true`, GPU/internet
enabled, the supplied Docker image digest, `NvidiaTeslaT4`, and a 5,700-second
session timeout. The internal wall budget is 5,400 seconds; every candidate also
has a smaller process timeout. Inspect Kaggle accelerator quota before dispatch.

## Reviewed correction snapshot input

The default is the exploratory Hong Kong comparison below. For another use case,
build a notebook using an immutable server `training-dataset/v1` export URL:

```sh
python benchmarks/build_training_notebook.py \
  --dataset-url https://api.example.com/v1/datasets/DATASET_ID/export \
  --dataset-hash EXPECTED_DATASET_SHA256 \
  --taxonomy-hash EXPECTED_TAXONOMY_SHA256 \
  --text-pointer /name --text-pointer /description \
  --bearer-secret-name training_export_token
```

Replace placeholders with the hashes returned when the dataset snapshot was
created. The raw response hash must match the immutable RFC8785 snapshot hash;
embedded taxonomy and retained evidence records are independently hash-checked.
The URL must use HTTPS/443, without embedded credentials, query or fragment;
private-network DNS answers and **all redirects** are rejected. Auth is optional;
when needed, attach a `training_export_token` secret **inside Kaggle**. Only its
name goes into the notebook. Never paste a bearer value into code or this command.
The workstation builder does not access the URL, dataset, secret or model.

Text selectors are RFC6901 pointers relative to each retained `record.data`.
Select only genuine inference-time fields, never existing predictions, category
names or labels. Correction actor/label/source metadata cannot be selected as
classifier features. Taxonomy leaves become the runtime categories, with full
parent-label paths as descriptions. `correction_id` identifies the immutable
example; `label_id` remains its reviewed target. Abstentions, non-leaf labels,
missing selected text and contradictory duplicates are excluded with counts.
Human/AI reviewer-declared provenance is preserved, not upgraded to certified gold.

Exact normalized-text duplicates connect source groups **before** deduplication
or conflict removal, so removing a duplicate cannot conceal cross-source leakage.
The independent groups must permit all classes in all three partitions. Fixed
seeds 42/1042 split groups into train/calibration/test with predeclared minima
100/100/150; insufficient class coverage fails, without searching seeds. Hashes
of the original snapshot, derived inputs, taxonomy and split are all distinct
report bindings. Snapshot inspection history is unknown, not claimed untouched.
This path implements the generic learning workflow but does not supply an
independently certified test corpus or promote any model.

## Provenance and leakage controls

This run deliberately reuses the SHA-pinned Hong Kong publisher CSVs and the
original manufacturer-disjoint 406/102/155 split. Its test has already been
inspected. Labels are inherited publisher source-list labels, not independent
product-type annotations. The report is `training-comparison/v1`, always
`certification_eligible=false`, `test_previously_inspected=true`, and
`auto_accept_enabled=false`. Nothing modifies the release manifest or its gates.
Changing a source CSV, taxonomy, or split invalidates the bindings; sources and
the original split fail closed rather than silently shifting the experiment.
Non-Latin manufacturer names that the original ASCII normalizer grouped under
an empty string stay in one explicitly unresolved group. The historical split
is preserved; these rows are not claimed to represent distinct known vendors.

SetFit and TF-IDF fit only train rows. Laya uses the canonical upstream
`laya.train.train_model` RLCD trainer at a fixed Git commit, updating **both the
encoder and head**, with shuffled choice order and gradient checkpointing on one
T4. It uses the checkpoint's existing context/head budgets; it does not expand
the context as an unmeasured quality change. Calibration is the same separately
manufacturer-held-out partition for all candidates. Laya's native temperature
fit discards inherited option-bucket overrides before saving. A common external
diagnostic temperature is fitted on calibration predictions, never on test.
Option-order checks are reported, not used to select settings after seeing test.
The pinned Laya public API rounds probabilities to four decimal places. Its
adapter corrects total mass only within the theoretical half-unit-per-label
rounding bound; invalid distributions still fail strict downstream checks.

## Outputs and interpretation

Only `/kaggle/working/training-comparison.json` is an output. It contains input,
dataset, taxonomy, split, source-code, calibration and checkpoint digests;
per-candidate runtime/dependency versions; accuracy, macro/micro F1, Brier, ECE,
confusion matrices and ID-only probability predictions; and explicit failures.
Models, CSVs, training rows and checkpoints are under `/tmp` and are **not
published**. The checkpoint file manifest is metadata, not deployable weights.
Deployment/registry upload requires a separate remote-only, authorized workflow.

CPU inference is sequential, batch size one, four threads for every candidate.
CPU-serving RSS is sampled separately from the overall process high-water mark
that can include training. Trained neural checkpoints are reloaded after replacing
the candidate child with a fresh CPU process image; CUDA is hidden before any
model import. The parent timeout still owns the same PID, and checkpoint/split
hashes are verified again. Zero-shot candidates also run with CUDA hidden. Model
loading uses a cached remote checkpoint, not a
full Wasmer cold start or network download. GPU memory/training measurements are
separate; do not compare them to CPU latency as though the devices were equal.
Pinning library/model revisions improves reproducibility but does not make
publisher labels independent gold. New certification still needs the untouched,
independently reviewed corpus described in the main benchmark protocol.

Protocol unit tests use synthetic records, stdlib and the reference RFC8785
canonicalizer only; they never install a training dependency or load/download a
model. Run `pytest tests/test_training_protocol.py` locally. Reports under
`reports/training-comparison-kaggle-v*.json` record actual private notebook versions
and failures. Early reports with post-training RSS do **not** represent clean
CPU hosting memory and must not be used to claim a free-tier deployment fits.

## Recorded exploratory result

Private Kaggle notebook `138016586`, version 4 completed all five candidates.
The metrics-only [v4 report](reports/training-comparison-kaggle-v4.json) is the
clean CPU-serving measurement; it is not independent certification.

| Candidate | Macro-F1 | CPU p95, ms | Serving RSS, MiB |
| --- | ---: | ---: | ---: |
| Trained TF-IDF | 0.6213 | 1.12 | 193 |
| Trained SetFit/BGE-small | 0.7232 | 35.99 | 1,105 |
| GLiClass-edge (zero shot) | 0.0858 | 44.20 | 1,134 |
| Laya base | 0.5540 | 925.44 | 2,663 |
| Full fine-tuned Laya | 0.6535 | 908.43 | 2,647 |

SetFit is the strongest measured training candidate here; TF-IDF is the lowest
resource baseline. Laya fine-tuning improved over its base checkpoint, but
missed all 19 structural-fire-protection test examples. Its macro-F1 also changed
from 0.6165 in [v3](reports/training-comparison-kaggle-v3.json) to 0.6535 in v4 with
the same split, seed and hyperparameters; both reports are retained. GPU seed
configuration alone does not guarantee deterministic training, and v4 was run
to isolate CPU memory, not to search for a better test score. Reverse-label-order
disagreement on 30 test examples was 60% for GLiClass, 26.7% for Laya base and 20%
for fine-tuned Laya. These observations are specific to this taxonomy and adapter,
not universal judgments of the models. No candidate is activated or promoted.
