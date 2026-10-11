"""Protocol tests are synthetic and never install/fetch/load a model."""

import copy
import hashlib
import importlib.util
import json
import runpy
from pathlib import Path

import pytest
import rfc8785

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
spec = importlib.util.spec_from_file_location(
    "training_protocol", BENCHMARKS / "training_protocol.py"
)
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)


def fixture_data():
    taxonomy = {"a": "Tools", "b": "Sealants"}
    rows, assignments = [], {}
    for part in protocol.PARTITIONS:
        for index in range(4):
            identifier = f"{part}-{index}"
            rows.append(
                {
                    "id": identifier,
                    "text": f"Unique {part} item {index}",
                    "label": "a" if index % 2 else "b",
                    "group": f"vendor-{part}",
                }
            )
            assignments[identifier] = part
    return rows, assignments, taxonomy


def predictions(rows, assignments, partition):
    return [
        {
            "id": row["id"],
            "probabilities": {label: 0.8 if label == row["label"] else 0.2 for label in ("a", "b")},
        }
        for row in rows
        if assignments[row["id"]] == partition
    ]


def test_hashes_are_order_independent_and_non_certifying():
    rows, assignments, taxonomy = fixture_data()
    first = protocol.validate_split(rows, assignments, taxonomy)
    second = protocol.validate_split(
        list(reversed(rows)),
        dict(reversed(list(assignments.items()))),
        dict(reversed(list(taxonomy.items()))),
    )
    assert first == second
    assert not first["certification_eligible"]
    assert first["test_previously_inspected"]


def test_legacy_non_latin_group_is_one_explicit_unresolved_group():
    assert protocol.explicit_legacy_group("") == protocol.explicit_legacy_group("")
    assert protocol.explicit_legacy_group("").strip()
    assert protocol.explicit_legacy_group("known vendor") == "known vendor"
    with pytest.raises(ValueError, match="string"):
        protocol.explicit_legacy_group(None)


@pytest.mark.parametrize("part", ["calibration", "test"])
def test_group_leakage_fails_for_all_partitions(part):
    rows, assignments, taxonomy = fixture_data()
    next(row for row in rows if assignments[row["id"]] == part)["group"] = "vendor-train"
    with pytest.raises(ValueError, match="Source-group leakage"):
        protocol.validate_split(rows, assignments, taxonomy)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("text", "Unique train item 0!", "deduplicated"),
        ("group", " ", "source group"),
        ("label", "unknown", "taxonomy"),
        ("id", "train-0", "unique"),
    ],
)
def test_invalid_rows_fail(field, value, match):
    rows, assignments, taxonomy = fixture_data()
    rows[-1][field] = value
    with pytest.raises(ValueError, match=match):
        protocol.validate_split(rows, assignments, taxonomy)


def test_no_unknown_assignment_or_partial_class_coverage():
    rows, assignments, taxonomy = fixture_data()
    with pytest.raises(ValueError, match="exactly"):
        protocol.validate_split(rows, {**assignments, "unknown": "train"}, taxonomy)
    for row in rows:
        if assignments[row["id"]] == "test":
            row["label"] = "a"
    with pytest.raises(ValueError, match="class must occur"):
        protocol.validate_split(rows, assignments, taxonomy)


def test_calibration_rejects_test_predictions_and_duplicates():
    rows, assignments, taxonomy = fixture_data()
    for invalid in (
        predictions(rows, assignments, "test"),
        predictions(rows, assignments, "calibration") * 2,
    ):
        with pytest.raises(ValueError, match="exactly calibration"):
            protocol.fit_diagnostic_calibration(
                rows, assignments, taxonomy, invalid, candidate="fixture"
            )


def test_calibration_binding_tampering_and_cross_model_use_fail():
    rows, assignments, taxonomy = fixture_data()
    policy = protocol.fit_diagnostic_calibration(
        rows,
        assignments,
        taxonomy,
        predictions(rows, assignments, "calibration"),
        candidate="fixture",
    )
    test = predictions(rows, assignments, "test")
    metrics = protocol.evaluate_diagnostic(
        rows, assignments, taxonomy, test, policy, candidate="fixture"
    )
    assert metrics["macro_f1"] == 1
    assert not metrics["auto_accept_enabled"]
    assert metrics["ece"] < 0.001
    with pytest.raises(ValueError, match="another candidate"):
        protocol.evaluate_diagnostic(rows, assignments, taxonomy, test, policy, candidate="other")
    with pytest.raises(ValueError, match="digest mismatch"):
        protocol.evaluate_diagnostic(
            rows, assignments, taxonomy, test, {**policy, "temperature": 9}, candidate="fixture"
        )
    rows[0]["text"] = "Changed evidence"
    with pytest.raises(ValueError, match="does not match"):
        protocol.evaluate_diagnostic(rows, assignments, taxonomy, test, policy, candidate="fixture")


@pytest.mark.parametrize(
    "vector", [{"a": 1}, {"a": -1, "b": 2}, {"a": float("nan"), "b": 1}, {"a": 1, "b": 1}]
)
def test_invalid_probability_vectors_fail(vector):
    with pytest.raises(ValueError):
        protocol.validate_probabilities(vector, ["a", "b"])


def test_public_api_rounding_has_a_bounded_separate_adapter():
    values = protocol.normalize_rounded_probabilities(
        {"a": 0.3333, "b": 0.3333, "c": 0.3333}, ["a", "b", "c"]
    )
    assert sum(values.values()) == pytest.approx(1)
    with pytest.raises(ValueError, match="sum to one"):
        protocol.validate_probabilities({"a": 0.3333, "b": 0.3333, "c": 0.3333}, ["a", "b", "c"])
    for invalid in (
        {"a": 0.4, "b": 0.4},
        {"a": -0.0001, "b": 1},
        {"a": 0.0, "b": 0.0},
        {"a": float("nan"), "b": 1},
    ):
        with pytest.raises(ValueError):
            protocol.normalize_rounded_probabilities(invalid, ["a", "b"])


def test_training_materialization_excludes_test_and_preserves_label_order():
    rows, assignments, taxonomy = fixture_data()
    training = protocol.typed_training_rows(rows, assignments, taxonomy, "train")
    assert len(training) == 4
    assert all("train" in row["state"]["name"] for row in training)
    with pytest.raises(ValueError, match="Only train or calibration"):
        protocol.typed_training_rows(rows, assignments, taxonomy, "test")
    flipped = protocol.benchmark_questions(taxonomy, ["b", "a"])
    assert list(flipped["category"]["criteria"]) == ["b", "a"]
    assert flipped["category"]["criteria"] == taxonomy
    with pytest.raises(ValueError, match="permutation"):
        protocol.benchmark_questions(taxonomy, ["a", "a"])


def test_generic_typed_input_is_not_product_specific():
    rows, assignments, taxonomy = fixture_data()
    training = protocol.typed_training_rows(
        rows,
        assignments,
        taxonomy,
        "train",
        state_field="text",
        instructions="Classify the record text using the taxonomy.",
    )
    assert set(training[0]["state"]) == {"text"}
    assert training[0]["questions"]["category"]["instructions"].startswith("Classify the record")
    with pytest.raises(ValueError, match="State input field"):
        protocol.typed_training_rows(
            rows, assignments, taxonomy, "train", state_field="../../escape"
        )
    with pytest.raises(ValueError, match="bounded non-empty"):
        protocol.benchmark_questions(taxonomy, instructions="")


def test_latency_nearest_rank_and_invalid_values():
    assert protocol.latency_summary(list(range(1, 101)))["p95_latency_ms"] == 95
    for invalid in ([], [-1], [float("inf")]):
        with pytest.raises(ValueError):
            protocol.latency_summary(invalid)


def test_runner_refuses_workstation_before_any_download():
    runner = BENCHMARKS / "kaggle_training_comparison.py"
    if Path("/kaggle/working").is_dir():
        pytest.skip("This refusal test is workstation-only")
    with pytest.raises(SystemExit, match="Remote-only"):
        runpy.run_path(str(runner))


def test_notebook_builder_bundles_code_not_model_data():
    builder_spec = importlib.util.spec_from_file_location(
        "training_notebook_builder", BENCHMARKS / "build_training_notebook.py"
    )
    builder = importlib.util.module_from_spec(builder_spec)
    builder_spec.loader.exec_module(builder)
    payload = builder.notebook_payload()
    assert payload["kernel_type"] == "notebook"
    notebook = json.loads(payload["text"])
    code = "".join(notebook["cells"][1]["source"])
    assert 'Path("/kaggle/working").is_dir()' in code
    assert "training_protocol.py" in code
    assert "kaggle_training_comparison.py" in code
    assert "certification_eligible" in notebook["cells"][0]["source"][0]


def export_fixture():
    taxonomy = {
        "id": "synthetic",
        "nodes": [
            {"id": "root", "label": "Materials", "parent_id": None},
            {"id": "a", "label": "Tools", "parent_id": "root"},
            {"id": "b", "label": "Sealants", "parent_id": "root"},
        ],
    }
    taxonomy_hash = hashlib.sha256(rfc8785.dumps(taxonomy)).hexdigest()
    export = {
        "schema_version": "training-dataset/v1",
        "taxonomy": taxonomy,
        "taxonomy_hash": taxonomy_hash,
        "certification_eligible": False,
        "auto_accept_enabled": False,
        "rows": [],
    }
    for index, (label, provenance) in enumerate((("a", "human"), ("a", "ai"), ("b", "human"))):
        record = {
            "id": f"record-{index}",
            "source_id": f"source-{index}",
            "data": {"name": f"Distinct item {index}"},
        }
        export["rows"].append(
            {
                "correction_id": f"correction-{index}",
                "label_id": label,
                "taxonomy_hash": taxonomy_hash,
                "record": record,
                "record_hash": hashlib.sha256(rfc8785.dumps(record)).hexdigest(),
                "source_group": record["source_id"],
                "provenance": provenance,
                "actor": "reviewer",
            }
        )
    return export


def parse_export(export, *, pointers=None, expected_dataset_hash=None, expected_taxonomy_hash=None):
    raw = rfc8785.dumps(export)
    return protocol.training_export_rows(
        raw,
        expected_dataset_hash=expected_dataset_hash or hashlib.sha256(raw).hexdigest(),
        expected_taxonomy_hash=expected_taxonomy_hash or export["taxonomy_hash"],
        text_pointers=pointers or ["/name"],
        canonicalize=rfc8785.dumps,
    )


def test_snapshot_targets_are_not_correction_identity_or_independent_gold():
    export = export_fixture()
    result = parse_export(export)
    assert [row["id"] for row in result["rows"]] == ["correction-0", "correction-1", "correction-2"]
    assert result["taxonomy"] == {"a": "Materials > Tools", "b": "Materials > Sealants"}
    assert result["audit"]["declared_provenance_counts"] == {"human": 2, "ai": 1}
    assert not result["certification_eligible"] and not result["auto_accept_enabled"]
    export["rows"][1]["correction_id"] = "correction-0"
    with pytest.raises(ValueError, match="correction IDs"):
        parse_export(export)


def test_snapshot_keeps_abstentions_and_internal_labels_in_exclusion_audit():
    export = export_fixture()
    export["rows"][0]["label_id"] = None
    export["rows"][1]["label_id"] = "root"
    result = parse_export(export)
    assert result["audit"]["excluded"] == {
        "explicit_abstention": 1,
        "non_leaf_label": 1,
        "same_label_duplicate_rows": 0,
    }
    assert len(result["rows"]) == 1


def test_snapshot_hash_and_reviewer_provenance_checks_fail_closed():
    original = export_fixture()
    with pytest.raises(ValueError, match="immutable dataset hash"):
        parse_export(original, expected_dataset_hash="0" * 64)
    with pytest.raises(ValueError, match="taxonomy hash"):
        parse_export(original, expected_taxonomy_hash="0" * 64)
    for change, match in (
        (lambda row: row["record"]["data"].update(name="Changed"), "record hash"),
        (lambda row: row.update(actor=""), "reviewer identity"),
        (lambda row: row.update(provenance="gold"), "provenance"),
        (lambda row: row.update(source_group="other"), "Source grouping"),
        (lambda row: row.update(taxonomy_hash="0" * 64), "another taxonomy"),
    ):
        export = copy.deepcopy(original)
        change(export["rows"][0])
        with pytest.raises(ValueError, match=match):
            parse_export(export)


def test_snapshot_duplicates_merge_source_groups_before_deduplication():
    export = export_fixture()
    export["rows"][1]["record"]["data"]["name"] = export["rows"][0]["record"]["data"]["name"]
    export["rows"][2]["record"]["source_id"] = "source-1"
    export["rows"][2]["source_group"] = "source-1"
    for row in export["rows"]:
        row["record_hash"] = hashlib.sha256(rfc8785.dumps(row["record"])).hexdigest()
    result = parse_export(export)
    assert len(result["rows"]) == 2
    assert {row["group"] for row in result["rows"]} == {"source-0"}
    assert result["audit"]["excluded"]["same_label_duplicate_rows"] == 1
    export["rows"][1]["label_id"] = "b"
    result = parse_export(export)
    assert result["audit"]["excluded"]["conflicting_duplicate_labels"] == 2


def test_input_selectors_cannot_read_correction_metadata():
    result = parse_export(export_fixture(), pointers=["/label_id", "/actor", "/record/name"])
    assert result["rows"] == []
    assert result["audit"]["excluded"]["missing_selected_input_text"] == 3
    with pytest.raises(ValueError, match="RFC6901"):
        parse_export(export_fixture(), pointers=["name"])
    assert protocol._pointer({"a/b": {"x~y": ["first", "second"]}}, "/a~1b/x~0y/1") == "second"
    assert protocol._pointer(["first", "second"], "/01") is None


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "http://api.example.com/export"},
        {"url": "https://user:password@api.example.com/export"},
        {"url": "https://api.example.com/export?token=value"},
        {"url": "https://api.example.com/export#fragment"},
        {"url": "https://127.0.0.1/export"},
        {"url": "https://localhost/export"},
        {"url": "https://api.example.com:8443/export"},
        {"bearer_secret_name": "Bearer pasted token"},
        {"text_pointers": [["nested-list"]]},
        {"text_pointers": ["/name", "/name"]},
    ],
)
def test_remote_export_configuration_rejects_unsafe_or_secret_bearing_inputs(changes):
    configuration = {
        "url": "https://api.example.com/v1/datasets/frozen/export",
        "dataset_hash": "a" * 64,
        "taxonomy_hash": "b" * 64,
        "text_pointers": ["/name"],
        "bearer_secret_name": "training_export_token",
    }
    with pytest.raises(ValueError):
        protocol.validate_export_configuration({**configuration, **changes})


def test_generic_notebook_configuration_contains_secret_name_not_secret_value():
    builder_spec = importlib.util.spec_from_file_location(
        "training_notebook_builder", BENCHMARKS / "build_training_notebook.py"
    )
    builder = importlib.util.module_from_spec(builder_spec)
    builder_spec.loader.exec_module(builder)
    configuration = {
        "url": "https://api.example.com/v1/datasets/frozen/export",
        "dataset_hash": "a" * 64,
        "taxonomy_hash": "b" * 64,
        "text_pointers": ["/name"],
        "bearer_secret_name": "training_export_token",
    }
    payload = builder.notebook_payload(configuration)
    notebook = json.loads(payload["text"])
    code = "".join(notebook["cells"][1]["source"])
    assert "EVIDENCE_TRAINING_EXPORT" in code and "training_export_token" in code
    assert "Reviewer-declared" in notebook["cells"][0]["source"][0]
    compile(code, "notebook", "exec")
