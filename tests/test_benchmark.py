import pytest
from pydantic import ValidationError

from product_pipeline.benchmark import (
    BenchmarkDataset,
    Prediction,
    evaluate,
    fit_policy,
    laya_promotion,
)


def dataset():
    labels = []
    for i in range(450):
        group = i // 75
        labels.append(
            {
                "id": str(i),
                "text": f"Unique human-reviewed fixture {i}",
                "source_group": f"source-{group}",
                "source_url": f"https://example.org/{i}",
                "split": "train" if group < 2 else "calibration" if group < 4 else "test",
                "label": "tool" if i % 2 else "sealant",
                "reviewed_by": "fixture-reviewer",
                "reviewed_at": "2026-09-26",
            }
        )
    return {
        "id": "synthetic-test-only",
        "taxonomy": {
            "id": "t",
            "nodes": [{"id": "tool", "label": "Tool"}, {"id": "sealant", "label": "Sealant"}],
        },
        "certified_leaves": ["tool", "sealant"],
        "labels": labels,
    }


def predictions(data, split):
    return [
        Prediction(
            id=row.id,
            probabilities={
                "tool": 0.99 if row.label == "tool" else 0.01,
                "sealant": 0.99 if row.label == "sealant" else 0.01,
            },
            latency_ms=5,
        )
        for row in data.labels
        if row.split == split
    ]


def test_split_integrity_and_certification():
    data = BenchmarkDataset.model_validate(dataset())
    policy = fit_policy(data, predictions(data, "calibration"))
    metrics = evaluate(data, predictions(data, "test"), policy)
    assert metrics["auto_accept_enabled"] and metrics["leaf_accuracy"] == 1
    assert metrics["wilson_lower_95"] >= 0.95


def test_test_labels_cannot_fit_thresholds():
    data = BenchmarkDataset.model_validate(dataset())
    with pytest.raises(ValueError, match="calibration"):
        fit_policy(data, predictions(data, "test"))


def test_ai_labels_are_not_accepted_as_certification_gold():
    data = dataset()
    data["labels"][0]["provenance"] = "ai_reviewed"
    with pytest.raises(ValidationError):
        BenchmarkDataset.model_validate(data)


@pytest.mark.parametrize("case", ["group", "duplicate", "count", "label"])
def test_leakage_and_invalid_data_fail(case):
    data = dataset()
    if case == "group":
        data["labels"][-1]["source_group"] = "source-0"
    elif case == "duplicate":
        data["labels"][-1]["text"] = data["labels"][0]["text"]
    elif case == "count":
        data["labels"] = data["labels"][:100]
    else:
        data["labels"][-1]["label"] = "invented"
    with pytest.raises(ValidationError):
        BenchmarkDataset.model_validate(data)


def test_no_release_promotion_from_quality_alone():
    baseline = {
        "macro_f1": 0.8,
        "ece": 0.1,
        "correct_auto_accept_coverage": 0.7,
        "p95_latency_ms": 10,
    }
    laya = {**baseline, "macro_f1": 0.85, "p95_latency_ms": 30}
    assert not laya_promotion(laya, baseline, fits_t4=True, apache_compatible=True)


def test_calibration_requires_complete_taxonomy_distribution():
    data = BenchmarkDataset.model_validate(dataset())
    invalid = [
        Prediction(id=p.id, probabilities={"invented": 1.0}, latency_ms=1)
        for p in predictions(data, "calibration")
    ]
    with pytest.raises(ValueError, match="exactly the taxonomy leaves"):
        fit_policy(data, invalid)


def test_incorrect_auto_accepts_do_not_count_toward_laya_promotion():
    baseline = {
        "macro_f1": 0.8,
        "ece": 0.1,
        "correct_auto_accept_coverage": 0.7,
        "p95_latency_ms": 10,
    }
    laya = {**baseline, "coverage": 1.0, "correct_auto_accept_coverage": 0.69}
    assert not laya_promotion(laya, baseline, fits_t4=True, apache_compatible=True)
