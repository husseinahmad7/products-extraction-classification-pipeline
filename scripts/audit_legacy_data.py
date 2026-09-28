"""Reproducible structural audit plus explicit AI spot reviews; never produces gold labels."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


def audit_records(records, taxonomy, spot_review):
    nodes = {row["id"]: row for row in taxonomy}
    parents = {row["parent_id"] for row in taxonomy}
    leaves = set(nodes) - parents
    urls = Counter(row["source_url"] for row in records if row["source_url"])
    products = Counter((row["brand"], normalized(row["product_name"])) for row in records)
    names = defaultdict(list)
    for row in taxonomy:
        names[normalized(row["name"])].append(row["id"])
    review_map = {row["record_number"]: row for row in spot_review["records"]}
    if len(review_map) != len(spot_review["records"]):
        raise ValueError("duplicate reviewed record number")
    counters = Counter()
    output = []
    disagreements = []
    for number, row in enumerate(records, 1):
        issues = []
        label = row.get("type_id", "")
        if label not in nodes:
            issues.append("unknown_or_missing_predicted_id")
        elif label not in leaves:
            issues.append("predicted_id_is_not_leaf")
        elif row.get("classification_path") != nodes[label]["path"]:
            issues.append("predicted_path_mismatch")
        if not row.get("product_name") or not (
            row.get("short_description") or row.get("long_description")
        ):
            issues.append("missing_name_or_description")
        parsed = urlsplit(row.get("source_url", ""))
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            issues.append("invalid_source_url")
        if urls[row.get("source_url", "")] > 1:
            issues.append("duplicate_source_url")
        if products[row["brand"], normalized(row["product_name"])] > 1:
            issues.append("duplicate_brand_product_name")
        review = review_map.get(number)
        if review:
            if review["product_name"] != row["product_name"]:
                raise ValueError("review no longer matches input record order")
            proposed = review["proposed_type_id"]
            if proposed is not None and proposed not in leaves:
                raise ValueError("AI review proposed an unknown or non-leaf ID")
            if proposed and proposed != label:
                disagreements.append(
                    {
                        "record_number": number,
                        "product_name": row["product_name"],
                        "original_type_id": label,
                        "ai_proposed_type_id": proposed,
                        "reason": review["rationale"],
                    }
                )
        counters.update(issues)
        output.append(
            {
                "record_number": number,
                "record_sha256": hashlib.sha256(
                    json.dumps(row, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest(),
                "product_name": row["product_name"],
                "brand": row["brand"],
                "source_url": row.get("source_url"),
                "structural_issues": issues,
                "semantic_review": review or {"decision": "not_reviewed"},
                "certification_eligible": False,
            }
        )
    if any(number < 1 or number > len(records) for number in review_map):
        raise ValueError("review record number outside dataset")
    return {
        "schema_version": "legacy-audit/v1",
        "records": len(records),
        "vendors": dict(Counter(row["brand"] for row in records)),
        "source_hosts": dict(Counter(urlsplit(row["source_url"]).hostname for row in records)),
        "taxonomy_nodes": len(nodes),
        "taxonomy_leaves": len(leaves),
        "duplicate_taxonomy_names": {name: ids for name, ids in names.items() if len(ids) > 1},
        "structural_issue_counts": dict(counters),
        "unique_brand_product_names": len(products),
        "semantic_spot_reviewed": len(review_map),
        "not_semantically_reviewed": len(records) - len(review_map),
        "ai_review_decisions": dict(Counter(row["decision"] for row in review_map.values())),
        "ai_proposal_disagreements": disagreements,
        "certification_eligible": False,
        "blockers": [
            "Only two vendor/source groups; at least five independent groups are required.",
            "AI spot reviews are not independent human-reviewed ground truth.",
            "Duplicate products and ambiguous/missing taxonomy leaves need adjudication before splitting.",
        ],
    }, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=Path, default=Path("products_output.csv"))
    parser.add_argument("--taxonomy", type=Path, default=Path("classification_tree.csv"))
    parser.add_argument(
        "--reviews", type=Path, default=Path("benchmarks/review/ai-spot-review.json")
    )
    parser.add_argument("--report", type=Path, default=Path("benchmarks/reports/legacy-audit.json"))
    parser.add_argument(
        "--records", type=Path, default=Path("benchmarks/review/generated/record-audit.jsonl")
    )
    args = parser.parse_args()
    with args.products.open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    with args.taxonomy.open(encoding="utf-8-sig", newline="") as stream:
        taxonomy = list(csv.DictReader(stream))
    review = json.loads(args.reviews.read_text(encoding="utf-8"))
    report, rows = audit_records(records, taxonomy, review)
    report["input_sha256"] = {
        str(path): fingerprint(path) for path in [args.products, args.taxonomy, args.reviews]
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.records.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.records.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
