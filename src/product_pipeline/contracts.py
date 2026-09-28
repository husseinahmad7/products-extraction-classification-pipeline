"""Language-neutral v1 contracts. Unknown fields fail closed."""

from __future__ import annotations

import re
from typing import Any, Literal, Self
from urllib.parse import parse_qsl, urlsplit

from jsonschema import Draft202012Validator
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from .jsonutil import bounded_tree


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Limits(Contract):
    max_pages: int = Field(default=100, ge=1, le=10000)
    max_records: int = Field(default=5000, ge=1, le=1000000)
    max_bytes: int = Field(default=10_000_000, ge=1, le=100_000_000)
    deadline_seconds: int = Field(default=300, ge=1, le=3600)
    request_timeout_seconds: int = Field(default=20, ge=1, le=120)


class Navigation(Contract):
    """Follow one next link or discover bounded detail links from each page."""

    kind: Literal["html_next", "html_links", "json_next", "json_links"]
    value: str = Field(min_length=1, max_length=2000)
    attribute: str = Field(default="href", min_length=1, max_length=128)

    @model_validator(mode="after")
    def parameters(self) -> Self:
        if self.kind.startswith("json_") and self.attribute != "href":
            raise ValueError("attribute applies only to HTML navigation")
        if self.kind.startswith("json_") and not self.value.startswith("/"):
            raise ValueError("JSON navigation requires a JSON Pointer")
        return self


class SourceSpec(Contract):
    schema_version: Literal["source/v1"] = "source/v1"
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    url: str
    allowed_domains: list[str] = Field(min_length=1)
    mode: Literal["html", "json", "browser", "pdf"] = "html"
    limits: Limits = Field(default_factory=Limits)
    robots_policy: Literal["respect"] = "respect"
    navigation: Navigation | None = None
    auth_ref: str | None = Field(default=None, pattern=r"^env:[A-Z][A-Z0-9_]{0,127}$")
    min_interval_seconds: float = Field(default=1.0, ge=0.1, le=3600)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("URL must be HTTP(S), without credentials or fragment")
        if any(
            re.sub(r"[^a-z]", "", key.lower())
            in {
                "key",
                "apikey",
                "token",
                "accesstoken",
                "secret",
                "signature",
                "password",
                "authorization",
            }
            for key, _ in parse_qsl(parsed.query)
        ):
            raise ValueError("secret query parameters are unsupported")
        return value

    @field_validator("allowed_domains")
    @classmethod
    def valid_domains(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            normalized = value.encode("idna").decode().lower().rstrip(".")
            if not normalized or any(c in normalized for c in "/:@* "):
                raise ValueError("domains must be exact hostnames, without wildcards")
            result.append(normalized)
        return sorted(set(result))

    @model_validator(mode="after")
    def domain_in_scope(self) -> Self:
        host = urlsplit(self.url).hostname
        if host is None:
            raise ValueError("source must have a hostname")
        if host.encode("idna").decode().lower().rstrip(".") not in self.allowed_domains:
            raise ValueError("source host must be explicitly allowed")
        if self.auth_ref and urlsplit(self.url).scheme != "https":
            raise ValueError("authenticated sources require HTTPS")
        if self.navigation and (
            self.mode not in {"html", "json"} or self.navigation.kind.split("_", 1)[0] != self.mode
        ):
            raise ValueError("navigation kind must match an HTML or JSON source")
        return self


def walk_schema(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_schema(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_schema(child)


class TargetSchema(Contract):
    schema_version: Literal["target/v1"] = "target/v1"
    id: str = Field(min_length=1, max_length=128)
    definition: dict[str, Any]

    @field_validator("definition")
    @classmethod
    def valid_schema(cls, value: dict) -> dict:
        bounded_tree(value, max_depth=24, max_nodes=2000)
        Draft202012Validator.check_schema(value)
        if value.get("type") != "object":
            raise ValueError("record schema root must be an object")
        for node in walk_schema(value):
            if "$ref" in node or "$dynamicRef" in node:
                raise ValueError("references are not supported; inline the schema")
            if "pattern" in node or "patternProperties" in node:
                raise ValueError(
                    "schema regex is unsupported; use the time-bounded regex_capture transform"
                )
        return value


class Locator(Contract):
    type: Literal["css_text", "css_attr", "json_pointer", "json_path", "jsonld", "labeled_value"]
    value: str = Field(min_length=1, max_length=2000)
    attribute: str | None = None

    @model_validator(mode="after")
    def parameters(self) -> Self:
        if (self.type == "css_attr") != (self.attribute is not None):
            raise ValueError("attribute is required only for css_attr")
        if self.type == "json_pointer" and self.value != "/" and not self.value.startswith("/"):
            raise ValueError("invalid JSON Pointer")
        return self


class Transform(Contract):
    op: Literal[
        "strip",
        "collapse_whitespace",
        "unicode_nfc",
        "join",
        "parse_decimal",
        "parse_date",
        "resolve_url",
        "enum_map",
        "unique",
        "sort",
        "regex_capture",
    ]
    separator: str | None = None
    decimal_separator: str | None = None
    grouping_separator: str | None = None
    format: str | None = None
    mapping: dict[str, Any] | None = None
    unknown: Literal["error", "keep", "null"] = "error"
    pattern: str | None = None
    group: int | str = 1

    def allowed_parameters(self) -> set[str]:
        return {"op"} | {
            "join": {"separator"},
            "parse_decimal": {"decimal_separator", "grouping_separator"},
            "parse_date": {"format"},
            "enum_map": {"mapping", "unknown"},
            "regex_capture": {"pattern", "group"},
        }.get(self.op, set())

    @model_serializer
    def serialize_parameters(self) -> dict[str, Any]:
        # Only emit parameters belonging to this operation. Emitting unrelated
        # default fields makes a valid recipe fail its own strict decoder.
        return {name: getattr(self, name) for name in self.allowed_parameters()}

    @model_validator(mode="after")
    def parameters(self) -> Self:
        required = {
            "join": "separator",
            "parse_decimal": "decimal_separator",
            "parse_date": "format",
            "enum_map": "mapping",
            "regex_capture": "pattern",
        }
        if self.op in required and getattr(self, required[self.op]) is None:
            raise ValueError(f"{required[self.op]} required for {self.op}")
        if self.model_fields_set - self.allowed_parameters():
            raise ValueError(f"unexpected parameters for {self.op}")
        if self.op == "regex_capture" and (
            "(?" in (self.pattern or "")
            or any(f"\\{n}" in (self.pattern or "") for n in range(1, 10))
        ):
            raise ValueError("lookaround and backreferences are unsupported")
        if self.decimal_separator is not None and self.decimal_separator not in {".", ","}:
            raise ValueError("decimal separator must be '.' or ','")
        if self.grouping_separator == self.decimal_separator and self.decimal_separator is not None:
            raise ValueError("decimal and grouping separators must differ")
        return self


class FieldRule(Contract):
    locator: Locator
    cardinality: Literal["one", "many"] = "one"
    transforms: list[Transform] = Field(default_factory=list, max_length=20)
    required: bool = False


class RecordScope(Contract):
    type: Literal["one_per_resource", "container_selector", "items_path", "one_per_document"] = (
        "one_per_resource"
    )
    value: str | None = None

    @model_validator(mode="after")
    def parameters(self) -> Self:
        if (self.type in {"container_selector", "items_path"}) != (self.value is not None):
            raise ValueError("scope value is required only for repeated scopes")
        return self


class Recipe(Contract):
    schema_version: Literal["recipe/v1"] = "recipe/v1"
    source_id: str
    mode: Literal["html", "json", "browser", "pdf"]
    record_scope: RecordScope = Field(default_factory=RecordScope)
    identity_fields: list[str] = Field(min_length=1)
    fields: dict[str, FieldRule] = Field(min_length=1)
    target: TargetSchema
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def compatibility(self) -> Self:
        if len(self.fields) > 256:
            raise ValueError("recipes support at most 256 field rules")
        allowed = {
            "html": {"css_text", "css_attr", "jsonld"},
            "browser": {"css_text", "css_attr", "jsonld"},
            "json": {"json_pointer", "json_path"},
            "pdf": {"labeled_value"},
        }[self.mode]
        scopes = {
            "html": {"one_per_resource", "container_selector"},
            "browser": {"one_per_resource", "container_selector"},
            "json": {"one_per_resource", "items_path"},
            "pdf": {"one_per_document"},
        }[self.mode]
        if self.record_scope.type not in scopes:
            raise ValueError("record scope incompatible with acquisition mode")
        for pointer, rule in self.fields.items():
            if len(pointer) > 1024 or pointer.count("/") > 24 or re.search(r"~(?![01])", pointer):
                raise ValueError("invalid or excessively deep field pointer")
            if not pointer.startswith("/") or pointer.endswith("/") or "//" in pointer:
                raise ValueError("field path must be a nonempty JSON Pointer")
            if any(other.startswith(pointer + "/") for other in self.fields):
                raise ValueError("field paths must not overlap")
            if rule.locator.type not in allowed:
                raise ValueError("locator incompatible with acquisition mode")
        if any(p not in self.fields or not self.fields[p].required for p in self.identity_fields):
            raise ValueError("identity fields must reference required field rules")
        return self


class TaxonomyNode(Contract):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=512)
    parent_id: str | None = None
    aliases: list[str] = Field(default_factory=list)


class TaxonomySpec(Contract):
    schema_version: Literal["taxonomy/v1"] = "taxonomy/v1"
    id: str = Field(min_length=1, max_length=128)
    nodes: list[TaxonomyNode] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def tree(self) -> Self:
        by_id = {n.id: n for n in self.nodes}
        if len(by_id) != len(self.nodes):
            raise ValueError("duplicate taxonomy IDs")
        for node in self.nodes:
            seen: set[str] = set()
            current: TaxonomyNode | None = node
            while current is not None:
                if len(seen) >= 64:
                    raise ValueError("taxonomy exceeds maximum depth of 64")
                if current.id in seen:
                    raise ValueError("taxonomy cycle")
                seen.add(current.id)
                if current.parent_id is not None and current.parent_id not in by_id:
                    raise ValueError("unknown parent")
                current = by_id.get(current.parent_id) if current.parent_id is not None else None
        return self


class FieldEvidence(Contract):
    path: str
    snapshot_hash: str
    source_locator: str
    locator: Locator
    raw_values: list[Any]
    transforms: list[Transform]
    outcome: Literal["observed", "derived", "missing"]
    error: str | None = None


class RecordEnvelope(Contract):
    id: str | None
    source_id: str
    recipe_hash: str
    snapshot_hash: str
    data: dict[str, Any]
    evidence: list[FieldEvidence]
    errors: list[str] = Field(default_factory=list)


class ExtractionResult(Contract):
    records: list[RecordEnvelope]
    quarantined: list[RecordEnvelope]
    recipe_hash: str
    snapshot_hash: str
    result_hash: str
