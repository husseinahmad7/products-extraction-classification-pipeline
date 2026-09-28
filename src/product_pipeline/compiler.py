"""API-free candidate compiler. Proposals always require operator approval."""

import difflib
import re

from bs4 import BeautifulSoup

from .contracts import FieldRule, Locator, Recipe, RecordScope, SourceSpec, TargetSchema, Transform
from .engine import extract
from .errors import PipelineError
from .jsonutil import loads


def schema_leaves(schema: dict, prefix: str = "", required: bool = True):
    forbidden = {"oneOf", "anyOf", "allOf", "patternProperties", "if", "then", "else"}
    if forbidden & schema.keys():
        raise PipelineError(
            "SCHEMA_UNSUPPORTED",
            "automatic compilation does not support conditional or union schemas",
        )
    if schema.get("type") == "object":
        for name, child in sorted(schema.get("properties", {}).items()):
            path = prefix + "/" + name.replace("~", "~0").replace("/", "~1")
            yield from schema_leaves(child, path, required and name in schema.get("required", []))
    elif schema.get("type") in {"string", "number", "integer", "boolean"}:
        yield prefix, schema, required
    elif schema.get("type") == "array" and schema.get("items", {}).get("type") in {
        "string",
        "number",
        "integer",
        "boolean",
    }:
        yield prefix, schema, required
    else:
        raise PipelineError(
            "SCHEMA_UNSUPPORTED", "compiler supports nested scalar leaves and scalar arrays only"
        )


def compile_snapshot(
    snapshot: bytes, source: SourceSpec, target: TargetSchema, identity_fields: list[str]
) -> dict:
    leaves = list(schema_leaves(target.definition))
    fields, warnings = {}, []
    scope = RecordScope(type="one_per_document" if source.mode == "pdf" else "one_per_resource")
    if source.mode == "json":
        root = loads(snapshot)
        if isinstance(root, list):
            scope = RecordScope(type="items_path", value="$[*]")
            sample = root[0] if root else {}
        else:
            arrays = [
                k for k, v in root.items() if isinstance(v, list) and v and isinstance(v[0], dict)
            ]
            if len(arrays) == 1 and re.fullmatch(r"[A-Za-z_][\w-]*", arrays[0]):
                scope = RecordScope(type="items_path", value=f"$.{arrays[0]}[*]")
                sample = root[arrays[0]][0]
            else:
                sample = root
        if not isinstance(sample, dict):
            raise PipelineError("COMPILER_ASSISTANCE_REQUIRED", "no object record scope was found")
        for path, definition, required in leaves:
            name = path.rsplit("/", 1)[-1]
            candidates = sorted(
                sample,
                key=lambda k: (
                    -difflib.SequenceMatcher(
                        None, k.lower().replace("_", ""), name.lower().replace("_", "")
                    ).ratio(),
                    k,
                ),
            )
            key = candidates[0] if candidates else None
            similarity = (
                difflib.SequenceMatcher(None, key.lower(), name.lower()).ratio() if key else 0
            )
            if key is None or similarity < 0.6:
                warnings.append(f"{path}: operator mapping required")
                continue
            locator = Locator(
                type="json_pointer", value="/" + key.replace("~", "~0").replace("/", "~1")
            )
            if definition["type"] == "array":
                if not re.fullmatch(r"[A-Za-z_][\w-]*", key):
                    warnings.append(f"{path}: scalar array key needs manual mapping")
                    continue
                locator = Locator(type="json_path", value=f"$.{key}[*]")
            fields[path] = FieldRule(
                locator=locator,
                required=required or path in identity_fields,
                cardinality="many" if definition["type"] == "array" else "one",
            )
    elif source.mode in {"html", "browser"}:
        soup = BeautifulSoup(snapshot, "html.parser")
        for path, definition, required in leaves:
            name = path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"[\w-]+", name):
                warnings.append(f"{path}: operator mapping required")
                continue
            selector = f'[itemprop="{name}"]'
            if not soup.select(selector):
                selector = "h1" if name in {"name", "title"} else f'[data-field="{name}"]'
            if not soup.select(selector):
                warnings.append(f"{path}: operator mapping required")
                continue
            transforms = [
                Transform(op="strip"),
                Transform(op="collapse_whitespace"),
                Transform(op="unicode_nfc"),
            ]
            if definition["type"] in {"number", "integer"}:
                transforms.append(Transform(op="parse_decimal", decimal_separator="."))
            fields[path] = FieldRule(
                locator=Locator(type="css_text", value=selector),
                required=required or path in identity_fields,
                transforms=transforms,
                cardinality="many" if definition["type"] == "array" else "one",
            )
    else:
        blocks = loads(snapshot).get("blocks", [])
        for path, _definition, required in leaves:
            name = path.rsplit("/", 1)[-1].replace("_", " ")
            labels = sorted({b.get("label", "") for b in blocks})
            matches = difflib.get_close_matches(name, labels, n=1, cutoff=0.6)
            if matches:
                fields[path] = FieldRule(
                    locator=Locator(type="labeled_value", value=matches[0]),
                    required=required or path in identity_fields,
                )
            else:
                warnings.append(f"{path}: operator mapping required")
    required_paths = {path for path, _, required in leaves if required} | set(identity_fields)
    if required_paths - fields.keys():
        return {
            "recipe": None,
            "warnings": warnings,
            "candidate_fields": {p: f.model_dump() for p, f in fields.items()},
            "status": "assistance_required",
        }
    recipe = Recipe(
        source_id=source.id,
        mode=source.mode,
        record_scope=scope,
        identity_fields=identity_fields,
        fields=fields,
        target=target,
        limits=source.limits,
    )
    result = extract(snapshot, recipe, source_url=source.url)
    total = len(result.records) + len(result.quarantined)
    return {
        "recipe": recipe.model_dump(mode="json"),
        "warnings": warnings,
        "status": "review_required",
        "sample_count": total,
        "valid_fraction": len(result.records) / total if total else 0,
        "note": "Single-capture assisted proposal; full multi-page perturbation gates are not yet implemented.",
    }
