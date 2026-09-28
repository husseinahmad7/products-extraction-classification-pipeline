"""Pure extraction and replay: this module has no network or database access."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

import regex
from bs4 import BeautifulSoup
from jsonschema import Draft202012Validator

from .contracts import ExtractionResult, FieldEvidence, Locator, Recipe, RecordEnvelope, Transform
from .errors import PipelineError
from .hashing import canonical, digest, object_hash
from .jsonutil import loads

MISSING = object()


def pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise PipelineError("INVALID_POINTER", "invalid JSON Pointer")
    return [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")]


def pointer_get(value: Any, pointer: str) -> Any:
    for part in pointer_parts(pointer):
        if isinstance(value, dict):
            value = value.get(part, MISSING)
        elif (
            isinstance(value, list)
            and part.isdigit()
            and str(int(part)) == part
            and int(part) < len(value)
        ):
            value = value[int(part)]
        else:
            return MISSING
    return value


def pointer_set(target: dict, pointer: str, value: Any) -> None:
    parts = pointer_parts(pointer)
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def json_path(value: Any, path: str) -> list[Any]:
    """Deliberately small read-only subset; no eval, filters, recursive descent."""
    if not re.fullmatch(r"\$(?:(?:\.[A-Za-z_][\w-]*)|(?:\[(?:\d+|\*)\]))*", path):
        raise PipelineError(
            "JSONPATH_UNSUPPORTED", "use root, child names, indices or array wildcards"
        )
    values = [value]
    for name, index in re.findall(r"\.([A-Za-z_][\w-]*)|\[(\d+|\*)\]", path):
        next_values = []
        for item in values:
            if name and isinstance(item, dict) and name in item:
                next_values.append(item[name])
            elif index == "*" and isinstance(item, list):
                next_values.extend(item)
            elif index and index != "*" and isinstance(item, list) and int(index) < len(item):
                next_values.append(item[int(index)])
        values = next_values
    return values


def locate(root: Any, locator: Locator) -> list[Any]:
    if locator.type == "json_pointer":
        value = pointer_get(root, locator.value)
        return [] if value is MISSING else [value]
    if locator.type == "json_path":
        return json_path(root, locator.value)
    if locator.type in {"css_text", "css_attr"}:
        try:
            nodes = root.select(locator.value)
        except Exception as exc:
            raise PipelineError("LOCATOR_INVALID", "invalid CSS selector") from exc
        if locator.type == "css_attr":
            return [node[locator.attribute] for node in nodes if node.has_attr(locator.attribute)]
        return [node.get_text(" ", strip=False) for node in nodes]
    if locator.type == "jsonld":
        values = []
        for script in root.select('script[type="application/ld+json"]'):
            try:
                payload = loads(script.string or "")
            except ValueError:
                continue
            candidates = payload if isinstance(payload, list) else [payload]
            for candidate in candidates:
                if isinstance(candidate, dict) and "@graph" in candidate:
                    candidates.extend(
                        candidate["@graph"] if isinstance(candidate["@graph"], list) else []
                    )
                if isinstance(candidate, dict):
                    value = pointer_get(
                        candidate,
                        locator.value if locator.value.startswith("/") else "/" + locator.value,
                    )
                    if value is not MISSING:
                        values.append(value)
        return values
    # PDF execution consumes canonical, pre-converted blocks. Conversion is separate.
    if locator.type == "labeled_value":
        return [
            block["value"]
            for block in root.get("blocks", [])
            if block.get("label") == locator.value and "value" in block
        ]
    raise PipelineError("CAPABILITY_UNSUPPORTED", "unsupported locator")


def transform(values: list[Any], rule: Transform, source_url: str) -> list[Any]:
    if rule.op == "unique":
        unique = {canonical(v): v for v in values}
        return [unique[k] for k in sorted(unique)]
    if rule.op == "sort":
        return sorted(values, key=canonical)
    if rule.op == "join":
        assert rule.separator is not None  # Validated by Transform.
        if not all(isinstance(v, str) for v in values):
            raise PipelineError("TRANSFORM_TYPE", "join requires strings")
        return [rule.separator.join(values)]
    result = []
    for value in values:
        if rule.op == "enum_map":
            assert rule.mapping is not None
            key = str(value)
            if key in rule.mapping:
                value = rule.mapping[key]
            elif rule.unknown == "null":
                value = None
            elif rule.unknown == "error":
                raise PipelineError("TRANSFORM_PARSE", "unmapped enum value")
            result.append(value)
            continue
        if not isinstance(value, str):
            raise PipelineError("TRANSFORM_TYPE", f"{rule.op} requires a string")
        if rule.op == "strip":
            value = value.strip()
        elif rule.op == "collapse_whitespace":
            value = " ".join(value.split())
        elif rule.op == "unicode_nfc":
            value = unicodedata.normalize("NFC", value)
        elif rule.op == "resolve_url":
            value = urljoin(source_url, value)
        elif rule.op == "parse_decimal":
            assert rule.decimal_separator is not None
            cleaned = (
                value.replace(rule.grouping_separator, "") if rule.grouping_separator else value
            )
            number = Decimal(cleaned.replace(rule.decimal_separator, "."))
            if not number.is_finite():
                raise ValueError("non-finite decimal")
            value = float(number)
        elif rule.op == "parse_date":
            assert rule.format is not None
            date = datetime.strptime(value, rule.format)
            value = (
                date.replace(tzinfo=UTC).isoformat() if date.tzinfo is None else date.isoformat()
            )
        elif rule.op == "regex_capture":
            match = regex.search(rule.pattern, value, timeout=0.05)
            if not match:
                raise PipelineError("TRANSFORM_PARSE", "regular expression did not match")
            value = match.group(rule.group)
        result.append(value)
    return result


def extract(
    snapshot: bytes, recipe: Recipe, workspace_id: str = "default", source_url: str = ""
) -> ExtractionResult:
    if len(snapshot) > recipe.limits.max_bytes:
        raise PipelineError("LIMIT_REACHED", "snapshot exceeds recipe byte budget")
    snapshot_hash = digest(snapshot)
    recipe_hash = object_hash(recipe.model_dump(mode="json"))
    try:
        text = snapshot.decode("utf-8")
        root = (
            BeautifulSoup(text, "html.parser")
            if recipe.mode in {"html", "browser"}
            else loads(text)
        )
        if recipe.record_scope.type == "container_selector":
            assert recipe.record_scope.value is not None
            roots = root.select(recipe.record_scope.value)
            selected = {id(r) for r in roots}
            if any(any(id(p) in selected for p in r.parents) for r in roots):
                raise PipelineError("RECORD_SCOPE_OVERLAP", "record containers overlap")
        elif recipe.record_scope.type == "items_path":
            assert recipe.record_scope.value is not None
            roots = json_path(root, recipe.record_scope.value)
        else:
            roots = [root]
    except (UnicodeError, ValueError, TypeError) as exc:
        raise PipelineError(
            "SNAPSHOT_INVALID", "snapshot is not a valid canonical artifact"
        ) from exc
    if not roots:
        raise PipelineError("RECORD_SCOPE_EMPTY", "no records matched the scope")
    if len(roots) > recipe.limits.max_records:
        raise PipelineError("LIMIT_REACHED", "record count exceeds recipe budget")
    validator = Draft202012Validator(recipe.target.definition)
    records: list[RecordEnvelope] = []
    quarantined: list[RecordEnvelope] = []
    for index, record_root in enumerate(roots):
        data: dict[str, Any] = {}
        evidence: list[FieldEvidence] = []
        errors: list[str] = []
        for path, field in sorted(recipe.fields.items()):
            raw: list[Any] = []
            error = None
            try:
                raw = locate(record_root, field.locator)
                values = raw.copy()
                if not values:
                    raise PipelineError("LOCATOR_ZERO", "no value matched")
                for operation in field.transforms:
                    values = transform(values, operation, source_url)
                if field.cardinality == "one" and len(values) != 1:
                    raise PipelineError("LOCATOR_MULTIPLE", "expected exactly one value")
                if any(v is None for v in values):
                    raise PipelineError("LOCATOR_NULL", "matched null is not a valid field value")
                pointer_set(data, path, values[0] if field.cardinality == "one" else values)
            except PipelineError as exc:
                error = exc.code
            except (ValueError, TypeError, ArithmeticError, IndexError, TimeoutError, regex.error):
                error = "TRANSFORM_PARSE"
            if error and field.required:
                errors.append(f"{path}:{error}")
            evidence.append(
                FieldEvidence(
                    path=path,
                    snapshot_hash=snapshot_hash,
                    source_locator=f"{recipe.record_scope.type}:{index}",
                    locator=field.locator,
                    raw_values=raw,
                    transforms=field.transforms,
                    outcome="missing" if error else "derived" if field.transforms else "observed",
                    error=error,
                )
            )
        errors.extend(
            f"/{'/'.join(str(p) for p in e.absolute_path)}:VALIDATION_FAILED"
            for e in sorted(validator.iter_errors(data), key=lambda e: str(e.path))
        )
        identity = [pointer_get(data, p) for p in recipe.identity_fields]
        record_id = None
        if any(v is MISSING or v is None or v == "" for v in identity):
            errors.append("IDENTITY_MISSING")
        else:
            record_id = object_hash([workspace_id, recipe.source_id, identity])
        envelope = RecordEnvelope(
            id=record_id,
            source_id=recipe.source_id,
            recipe_hash=recipe_hash,
            snapshot_hash=snapshot_hash,
            data=data,
            evidence=evidence,
            errors=sorted(set(errors)),
        )
        (quarantined if errors else records).append(envelope)
    # Conflicting identities quarantine all instances, never pick an arbitrary winner.
    grouped: dict[str, list[RecordEnvelope]] = {}
    for record in records:
        assert record.id is not None
        grouped.setdefault(record.id, []).append(record)
    records = []
    for group in grouped.values():
        if len({object_hash(r.data) for r in group}) > 1:
            quarantined.extend(
                r.model_copy(update={"errors": ["IDENTITY_CONFLICT"]}) for r in group
            )
        else:
            records.append(
                group[0].model_copy(update={"evidence": [e for r in group for e in r.evidence]})
            )
    records.sort(key=lambda r: r.id or "")
    payload: dict[str, Any] = {
        "records": [r.model_dump(mode="json") for r in records],
        "quarantined": [r.model_dump(mode="json") for r in quarantined],
        "recipe_hash": recipe_hash,
        "snapshot_hash": snapshot_hash,
    }
    return ExtractionResult(**payload, result_hash=object_hash(payload))


def replay(
    snapshot: bytes, recipe: Recipe, expected: ExtractionResult, **kwargs
) -> ExtractionResult:
    if digest(snapshot) != expected.snapshot_hash:
        raise PipelineError("SNAPSHOT_CORRUPT", "snapshot digest mismatch")
    actual = extract(snapshot, recipe, **kwargs)
    if actual.result_hash != expected.result_hash:
        raise PipelineError("REPLAY_MISMATCH", "record or evidence hashes differ")
    return actual


def extract_pages(
    pages: list[tuple[bytes, str]], recipe: Recipe, workspace_id: str = "default"
) -> ExtractionResult:
    """Combine page evidence deterministically and detect identity conflicts across pages."""
    if not pages or len(pages) > recipe.limits.max_pages:
        raise PipelineError("LIMIT_REACHED", "page count exceeds recipe budget")
    results = [
        extract(snapshot, recipe, workspace_id=workspace_id, source_url=url)
        for snapshot, url in pages
    ]
    records = [record for result in results for record in result.records]
    quarantined = [record for result in results for record in result.quarantined]
    if len(records) + len(quarantined) > recipe.limits.max_records:
        raise PipelineError("LIMIT_REACHED", "collection exceeds recipe record budget")
    grouped: dict[str, list[RecordEnvelope]] = {}
    for record in records:
        assert record.id is not None
        grouped.setdefault(record.id, []).append(record)
    records = []
    for group in grouped.values():
        if len({object_hash(record.data) for record in group}) > 1:
            quarantined.extend(
                record.model_copy(update={"errors": ["IDENTITY_CONFLICT"]}) for record in group
            )
        else:
            records.append(
                group[0].model_copy(
                    update={"evidence": [evidence for record in group for evidence in record.evidence]}
                )
            )
    records.sort(key=lambda record: record.id or "")
    manifest = [{"url": url, "snapshot_hash": digest(snapshot)} for snapshot, url in pages]
    payload: dict[str, Any] = {
        "records": [record.model_dump(mode="json") for record in records],
        "quarantined": [record.model_dump(mode="json") for record in quarantined],
        "recipe_hash": results[0].recipe_hash,
        "snapshot_hash": object_hash(manifest),
    }
    return ExtractionResult(**payload, result_hash=object_hash(payload))


def replay_pages(
    pages: list[tuple[bytes, str]], recipe: Recipe, expected: ExtractionResult, **kwargs
) -> ExtractionResult:
    actual = extract_pages(pages, recipe, **kwargs)
    if actual.snapshot_hash != expected.snapshot_hash:
        raise PipelineError("SNAPSHOT_CORRUPT", "page manifest digest mismatch")
    if actual.result_hash != expected.result_hash:
        raise PipelineError("REPLAY_MISMATCH", "record or evidence hashes differ")
    return actual


def semantic_diff(before: list[RecordEnvelope], after: list[RecordEnvelope]) -> dict:
    if any(r.id is None for r in before + after):
        raise PipelineError("IDENTITY_MISSING", "cannot diff unidentified records")
    left, right = {str(r.id): r.data for r in before}, {str(r.id): r.data for r in after}
    return {
        "added": sorted(right.keys() - left.keys()),
        "removed": sorted(left.keys() - right.keys()),
        "changed": [
            {"id": key, "before": left[key], "after": right[key]}
            for key in sorted(left.keys() & right.keys())
            if canonical(left[key]) != canonical(right[key])
        ],
    }
