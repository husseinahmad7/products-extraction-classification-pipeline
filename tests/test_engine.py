import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from product_pipeline.classification import rank_candidates, wilson_lower
from product_pipeline.compiler import compile_snapshot
from product_pipeline.contracts import Recipe, TaxonomySpec, Transform
from product_pipeline.engine import (
    extract,
    json_path,
    pointer_get,
    pointer_set,
    replay,
    semantic_diff,
    transform,
)
from product_pipeline.errors import PipelineError
from product_pipeline.hashing import canonical, digest
from product_pipeline.storage import LocalStore


def test_determinism_and_replay(snapshot, recipe):
    first = extract(snapshot, recipe)
    assert len(first.records) == 2 and not first.quarantined
    assert replay(snapshot, recipe, first) == first
    assert all(e.snapshot_hash == digest(snapshot) for r in first.records for e in r.evidence)
    with pytest.raises(PipelineError, match="digest mismatch"):
        replay(snapshot + b" ", recipe, first)


def test_missing_required_is_quarantined(recipe):
    result = extract(b'{"items":[{"sku":"A"}]}', recipe)
    assert not result.records and result.quarantined
    assert "/name:LOCATOR_ZERO" in result.quarantined[0].errors


def test_null_is_not_absent(recipe):
    result = extract(b'{"items":[{"sku":"A","name":null}]}', recipe)
    assert "/name:LOCATOR_NULL" in result.quarantined[0].errors
    assert result.quarantined[0].evidence[0].raw_values == [None]


def test_optional_missing_has_evidence(recipe):
    result = extract(b'{"items":[{"sku":"A","name":"Drill"}]}', recipe)
    assert len(result.records) == 1
    assert next(e for e in result.records[0].evidence if e.path == "/price").outcome == "missing"


def test_duplicate_conflict_quarantines_all(recipe):
    result = extract(b'{"items":[{"sku":"A","name":"X"},{"sku":"A","name":"Y"}]}', recipe)
    assert not result.records and len(result.quarantined) == 2
    assert all("IDENTITY_CONFLICT" in r.errors for r in result.quarantined)


def test_equal_duplicates_preserve_all_evidence(recipe):
    result = extract(b'{"items":[{"sku":"A","name":"X"},{"sku":"A","name":"X"}]}', recipe)
    assert len(result.records) == 1 and len(result.records[0].evidence) == 6


@pytest.mark.parametrize("path", ["$..secret", "$[?(@.x)]", "$.x.length()", "$.__class__()"])
def test_jsonpath_rejects_expressions(path):
    with pytest.raises(PipelineError):
        json_path({}, path)


def test_diff_is_semantic(snapshot, recipe):
    before = extract(snapshot, recipe)
    after = extract(snapshot.replace(b"42", b"43"), recipe)
    diff = semantic_diff(before.records, after.records)
    assert not diff["added"] and not diff["removed"] and len(diff["changed"]) == 1


@settings(deadline=None)  # Correctness property, not a wall-clock performance benchmark.
@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1))
@example(key="\U00108a77/á")
def test_pointer_roundtrip(key):
    pointer = "/" + key.replace("~", "~0").replace("/", "~1")
    data = {}
    pointer_set(data, pointer, 4)
    assert pointer_get(data, pointer) == 4


@pytest.mark.parametrize(
    "op,values,expected",
    [
        ({"op": "strip"}, [" A "], ["A"]),
        ({"op": "collapse_whitespace"}, ["A\n B"], ["A B"]),
        ({"op": "unique"}, ["B", "A", "B"], ["A", "B"]),
        (
            {"op": "parse_decimal", "decimal_separator": ",", "grouping_separator": "."},
            ["1.234,50"],
            [1234.5],
        ),
        ({"op": "join", "separator": ","}, ["A", "B"], ["A,B"]),
        ({"op": "enum_map", "mapping": {"x": "y"}}, ["x"], ["y"]),
        ({"op": "regex_capture", "pattern": "SKU-(.*)"}, ["SKU-A"], ["A"]),
    ],
)
def test_transforms(op, values, expected):
    rule = Transform.model_validate(op)
    assert Transform.model_validate_json(rule.model_dump_json()) == rule
    assert transform(values, rule, "https://example.com") == expected


def test_mode_contract_and_unknown_fields(recipe):
    data = recipe.model_dump()
    data["arbitrary_code"] = "print('no')"
    with pytest.raises(ValidationError):
        Recipe.model_validate(data)
    with pytest.raises(ValidationError):
        Transform(op="strip", pattern=".*")


def test_html_browser_and_pdf_canonical(target):
    for mode in ("html", "browser", "pdf"):
        fields = {
            "/sku": {
                "required": True,
                "locator": {
                    "type": "labeled_value" if mode == "pdf" else "css_text",
                    "value": "sku" if mode == "pdf" else "[data-sku]",
                },
            },
            "/name": {
                "required": True,
                "locator": {
                    "type": "labeled_value" if mode == "pdf" else "css_text",
                    "value": "name" if mode == "pdf" else "h1",
                },
            },
        }
        spec = Recipe(
            source_id="test",
            mode=mode,
            record_scope={"type": "one_per_document" if mode == "pdf" else "one_per_resource"},
            fields=fields,
            identity_fields=["/sku"],
            target=target,
        )
        snapshot = (
            canonical(
                {
                    "blocks": [
                        {"label": "sku", "value": "A", "page": 1},
                        {"label": "name", "value": "Drill", "page": 1},
                    ]
                }
            )
            if mode == "pdf"
            else b"<h1>Drill</h1><span data-sku>A</span>"
        )
        result = extract(snapshot, spec)
        assert result.records[0].data == {"sku": "A", "name": "Drill"}
        assert replay(snapshot, spec, result) == result


def test_store_hash_verification_and_path_safety(tmp_path):
    store = LocalStore(tmp_path)
    key = store.put(b"evidence")
    assert store.put(b"evidence") == key
    assert store.get(key) == b"evidence"
    with pytest.raises(PipelineError):
        store.get("../secret")
    (tmp_path / key).write_bytes(b"tampered")
    with pytest.raises(PipelineError, match="mismatch"):
        store.get(key)


def test_compiler_is_review_only(source, target, snapshot):
    proposal = compile_snapshot(snapshot, source, target, ["/sku"])
    assert proposal["status"] == "review_required"
    assert proposal["valid_fraction"] == 1


def test_taxonomy_and_abstention():
    tree = TaxonomySpec(
        id="tools",
        nodes=[
            {"id": "root", "label": "Tools"},
            {"id": "drill", "label": "Drill", "parent_id": "root"},
            {"id": "saw", "label": "Saw", "parent_id": "root"},
        ],
    )
    result = rank_candidates("Cordless drill", tree)
    assert result["state"] == "abstained" and result["selected_path"] is None
    assert result["candidates"][0]["id"] == "drill"
    assert wilson_lower(100, 100) > 0.95
    assert wilson_lower(0, 0) == 0
    with pytest.raises(ValidationError):
        TaxonomySpec(
            id="bad",
            nodes=[
                {"id": "a", "label": "A", "parent_id": "b"},
                {"id": "b", "label": "B", "parent_id": "a"},
            ],
        )
