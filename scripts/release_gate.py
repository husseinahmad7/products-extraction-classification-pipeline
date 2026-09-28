"""Fail closed: a smoke test or a NOT_RUN benchmark cannot unlock a full release."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REQUIRED = {
    "live_html",
    "live_json",
    "live_browser",
    "live_pdf",
    "canonical_replay",
    "retained_oci_replay",
    "postgres_integration_verified",
    "artifact_quota_verified",
    "dashboard_e2e_verified",
    "source_pagination",
    "auth_profiles",
    "distributed_throttling",
    "multi_sample_compiler",
    "scheduler",
    "classifier_registry",
    "review_corrections",
    "backup_restore_verified",
    "security_isolation_verified",
}


def failures(manifest, report=None, report_digest=None):
    reasons = []
    if manifest.get("schema_version") != "release-gate/v1" or manifest.get("status") != "ready":
        reasons.append("release manifest is not ready")
    benchmark = manifest.get("classification_benchmark", {})
    if benchmark.get("status") != "completed":
        reasons.append("comparative classification benchmark has not completed")
    if not re.fullmatch(
        r"https://www\.kaggle\.com/code/[a-z0-9_-]+/[a-z0-9_-]+", benchmark.get("notebook") or ""
    ):
        reasons.append("missing Kaggle notebook provenance")
    if not isinstance(benchmark.get("version"), int) or benchmark["version"] < 1:
        reasons.append("missing immutable notebook version")
    if not re.fullmatch(r"[a-f0-9]{64}", benchmark.get("report_sha256") or ""):
        reasons.append("missing comparative report digest")
    if report is None:
        reasons.append("required comparative report artifact is missing")
    else:
        if benchmark.get("report_sha256") != report_digest:
            reasons.append("comparative report digest does not match retained bytes")
        if any(
            report.get(key) != value
            for key, value in {
                "schema_version": "classification-benchmark/v1",
                "kind": "comparative_certification",
                "status": "completed",
                "measured_on": "kaggle",
                "label_provenance": "human_reviewed",
            }.items()
        ):
            reasons.append("report is not an independent-gold comparative Kaggle benchmark")
        if report.get("notebook") != benchmark.get("notebook") or report.get(
            "notebook_version"
        ) != benchmark.get("version"):
            reasons.append("report notebook provenance does not match manifest")
        data = report.get("dataset", {})
        for key, minimum in {
            "labels": 400,
            "calibration": 100,
            "test": 150,
            "source_groups": 5,
        }.items():
            if not isinstance(data.get(key), int) or data[key] < minimum:
                reasons.append("insufficient benchmark dataset: " + key)
        if data.get("source_disjoint") is not True:
            reasons.append("benchmark source isolation is unverified")
        for key in ("sha256", "split_manifest_sha256"):
            if not re.fullmatch(r"[a-f0-9]{64}", data.get(key) or ""):
                reasons.append("missing dataset digest: " + key)
        for name in ("tfidf_retrieval", "tfidf_linear", "frozen_embeddings", "laya"):
            allowed = {"completed", "measured_failure"} if name == "laya" else {"completed"}
            if report.get("models", {}).get(name, {}).get("status") not in allowed:
                reasons.append("benchmark method not completed: " + name)
    for key in sorted(REQUIRED):
        if manifest.get("capabilities", {}).get(key) is not True:
            reasons.append("unverified release capability: " + key)
    return reasons


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", nargs="?", default="benchmarks/release-manifest.json")
    parser.add_argument("--expect-blocked", action="store_true")
    parser.add_argument("--report", default="benchmarks/reports/classification-comparison.json")
    args = parser.parse_args()
    report_path = Path(args.report)
    raw = report_path.read_bytes() if report_path.is_file() else None
    reasons = failures(
        json.loads(Path(args.manifest).read_text(encoding="utf-8")),
        json.loads(raw) if raw else None,
        hashlib.sha256(raw).hexdigest() if raw else None,
    )
    print(json.dumps({"allowed": not reasons, "reasons": reasons}, indent=2))
    sys.exit(0 if (bool(reasons) if args.expect_blocked else not reasons) else 1)
