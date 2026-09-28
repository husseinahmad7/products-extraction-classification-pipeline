"""Offline benchmark integrity and selective-classification metrics. No model loader."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from .classification import wilson_lower
from .contracts import Contract, TaxonomySpec
from .hashing import object_hash


class Label(Contract):
    id: str
    text: str = Field(min_length=1, max_length=100000)
    source_group: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    split: Literal["train", "calibration", "test"]
    label: str
    reviewed_by: str = Field(min_length=1)
    reviewed_at: str = Field(min_length=1)
    provenance: Literal["human_reviewed"] = "human_reviewed"


class BenchmarkDataset(Contract):
    schema_version: Literal["classification-dataset/v1"] = "classification-dataset/v1"
    id: str
    taxonomy: TaxonomySpec
    labels: list[Label]
    declared_coverage: float = Field(default=0.70, gt=0, le=1)
    certified_leaves: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def integrity(self):
        leaves = {n.id for n in self.taxonomy.nodes} - {n.parent_id for n in self.taxonomy.nodes}
        if not set(self.certified_leaves) <= leaves:
            raise ValueError("certified subset contains non-leaf or unknown taxonomy IDs")
        if len(self.labels) < 400:
            raise ValueError("at least 400 human-reviewed labels are required")
        counts = Counter(row.split for row in self.labels)
        if counts["calibration"] < 100 or counts["test"] < 150 or not counts["train"]:
            raise ValueError(
                "need training labels, at least 100 calibration and 150 untouched test labels"
            )
        groups: dict[str, str] = {}
        contents: dict[str, str] = {}
        identifiers: set[str] = set()
        for row in self.labels:
            if row.id in identifiers:
                raise ValueError("duplicate label ID")
            identifiers.add(row.id)
            if row.label not in leaves:
                raise ValueError("label is not a taxonomy leaf")
            if row.source_group in groups and groups[row.source_group] != row.split:
                raise ValueError("source group leakage across splits")
            groups[row.source_group] = row.split
            normalized = " ".join(re.findall(r"\w+", row.text.casefold()))
            key = object_hash(normalized)
            if key in contents:
                raise ValueError("duplicate normalized text; deduplicate before splitting")
            contents[key] = row.split
        if len(groups) < 5:
            raise ValueError("at least five independent source groups are required")
        return self


class Prediction(Contract):
    id: str
    probabilities: dict[str, float]
    latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def valid_probabilities(self):
        if not self.probabilities or any(
            not math.isfinite(p) or not 0 <= p <= 1 for p in self.probabilities.values()
        ):
            raise ValueError("invalid probability distribution")
        if abs(sum(self.probabilities.values()) - 1) > 0.002:
            raise ValueError("probabilities must sum to one")
        return self


def rescale(probabilities: dict[str, float], temperature: float) -> dict[str, float]:
    logits = {
        key: math.log(max(value, 1e-12)) / temperature for key, value in probabilities.items()
    }
    maximum = max(logits.values())
    values = {key: math.exp(value - maximum) for key, value in logits.items()}
    total = sum(values.values())
    return {key: value / total for key, value in values.items()}


def winner(values: dict[str, float]) -> str:
    return min(values, key=lambda key: (-values[key], key))


def fit_policy(dataset: BenchmarkDataset, predictions: list[Prediction]) -> dict:
    calibration = {row.id: row for row in dataset.labels if row.split == "calibration"}
    if set(calibration) != {row.id for row in predictions} or len(predictions) != len(calibration):
        raise ValueError("policy fitting requires exactly calibration predictions, never test")
    leaves = {n.id for n in dataset.taxonomy.nodes} - {n.parent_id for n in dataset.taxonomy.nodes}
    if any(set(p.probabilities) != leaves for p in predictions):
        raise ValueError("prediction probabilities must cover exactly the taxonomy leaves")
    temperatures = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0]
    temperature = min(
        temperatures,
        key=lambda t: sum(
            -math.log(max(rescale(p.probabilities, t).get(calibration[p.id].label, 0), 1e-12))
            for p in predictions
        ),
    )
    rows = [(p, rescale(p.probabilities, temperature)) for p in predictions]
    threshold = None
    for candidate in [i / 100 for i in range(50, 101)]:
        accepted = [
            (p, probs)
            for p, probs in rows
            if winner(probs) in dataset.certified_leaves and max(probs.values()) >= candidate
        ]
        correct = sum(winner(probs) == calibration[p.id].label for p, probs in accepted)
        if (
            len(accepted) >= 100
            and wilson_lower(correct, len(accepted)) >= 0.95
            and len(accepted) / len(calibration) >= dataset.declared_coverage
        ):
            threshold = candidate
            break
    return {
        "temperature": temperature,
        "threshold": threshold,
        "certified_leaves": dataset.certified_leaves,
        "coverage_target": dataset.declared_coverage,
        "dataset_hash": object_hash(dataset.model_dump(mode="json")),
        "fitted_split": "calibration",
    }


def evaluate(dataset: BenchmarkDataset, predictions: list[Prediction], policy: dict) -> dict:
    test = {row.id: row for row in dataset.labels if row.split == "test"}
    if (
        policy["dataset_hash"] != object_hash(dataset.model_dump(mode="json"))
        or policy.get("fitted_split") != "calibration"
    ):
        raise ValueError("policy was not frozen for this dataset")
    if set(test) != {row.id for row in predictions} or len(predictions) != len(test):
        raise ValueError("evaluate exactly the untouched test split")
    leaves = {n.id for n in dataset.taxonomy.nodes} - {n.parent_id for n in dataset.taxonomy.nodes}
    if any(set(p.probabilities) != leaves for p in predictions):
        raise ValueError("prediction probabilities must cover exactly the taxonomy leaves")
    parents = {n.id: n.parent_id for n in dataset.taxonomy.nodes}

    def path(key):
        result = [key]
        while parents[result[-1]] is not None:
            result.append(parents[result[-1]])
        return result[::-1]

    tp: Counter[str] = Counter()
    fp: Counter[str] = Counter()
    fn: Counter[str] = Counter()
    bins: list[list[tuple[float, int]]] = [[] for _ in range(10)]
    correct = accepted = accepted_correct = 0
    brier = distance = ancestor = 0.0
    for prediction in predictions:
        expected = test[prediction.id].label
        probabilities = rescale(prediction.probabilities, policy["temperature"])
        chosen = winner(probabilities)
        confidence = probabilities[chosen]
        hit = chosen == expected
        correct += hit
        tp[expected] += hit
        fp[chosen] += not hit
        fn[expected] += not hit
        bins[min(9, int(confidence * 10))].append((confidence, int(hit)))
        brier += sum((value - int(key == expected)) ** 2 for key, value in probabilities.items())
        actual_path, expected_path = path(chosen), path(expected)
        shared = 0
        for left, right in zip(actual_path, expected_path, strict=False):
            if left != right:
                break
            shared += 1
        distance += len(actual_path) + len(expected_path) - 2 * shared
        ancestor += shared / len(expected_path)
        if (
            policy["threshold"] is not None
            and confidence >= policy["threshold"]
            and chosen in policy["certified_leaves"]
        ):
            accepted += 1
            accepted_correct += hit
    count = len(test)
    classes = set(row.label for row in test.values()) | {
        winner(p.probabilities) for p in predictions
    }
    macro = sum(2 * tp[key] / (2 * tp[key] + fp[key] + fn[key]) for key in classes) / len(classes)
    ece = sum(
        abs(sum(p for p, _ in group) / len(group) - sum(h for _, h in group) / len(group))
        * len(group)
        / count
        for group in bins
        if group
    )
    latency = sorted(p.latency_ms for p in predictions)
    lower = wilson_lower(accepted_correct, accepted)
    coverage = accepted / count
    certified = accepted >= 100 and lower >= 0.95 and coverage >= policy["coverage_target"]
    return {
        "test_examples": count,
        "leaf_accuracy": correct / count,
        "macro_f1": macro,
        "micro_f1": correct / count,
        "ancestor_path_accuracy": ancestor / count,
        "tree_distance": distance / count,
        "brier": brier / count,
        "ece": ece,
        "accepted": accepted,
        "accepted_correct": accepted_correct,
        "wilson_lower_95": lower,
        "coverage": coverage,
        "correct_auto_accept_coverage": accepted_correct / count,
        "selective_risk": 1 - accepted_correct / accepted if accepted else None,
        "p50_latency_ms": latency[math.ceil(count * 0.5) - 1],
        "p95_latency_ms": latency[math.ceil(count * 0.95) - 1],
        "sequential_throughput_per_second": count * 1000 / max(sum(latency), 1e-9),
        "auto_accept_enabled": certified,
    }


def laya_promotion(laya: dict, baseline: dict, *, fits_t4: bool, apache_compatible: bool) -> bool:
    improvement = laya["macro_f1"] - baseline["macro_f1"]
    calibration_gain = baseline["ece"] > 0 and laya["ece"] <= baseline["ece"] * 0.75
    base_coverage = baseline["correct_auto_accept_coverage"]
    coverage_gain = (
        base_coverage > 0 and laya["correct_auto_accept_coverage"] >= base_coverage * 1.25
    )
    return (
        fits_t4
        and apache_compatible
        and laya["p95_latency_ms"] <= 2 * baseline["p95_latency_ms"]
        and (improvement >= 0.03 or (improvement >= -0.01 and (calibration_gain or coverage_gain)))
    )
