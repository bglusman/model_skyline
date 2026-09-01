from __future__ import annotations

import hashlib
import json
import os
import stat
from copy import deepcopy
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from pydantic import BaseModel, ValidationError
from yaml.nodes import ScalarNode

from model_skyline.models import (
    MAX_DECIMAL_INPUT_LENGTH,
    FrontierHistory,
    FrontierSnapshot,
    ObservationCatalog,
    ProjectConfig,
    PublicationManifest,
    SelectionSnapshot,
)
from model_skyline.quality_evidence import (
    MAX_QUALITY_ARTIFACT_BYTES,
    QualityEvidenceSet,
    QualityImportReport,
    QualityReconciliation,
)
from model_skyline.quality_portfolio import PortfolioDerivationSnapshot, PortfolioPolicy


class InputError(ValueError):
    """A configuration or artifact file cannot be loaded or validated."""


ModelT = TypeVar("ModelT", bound=BaseModel)

# The public quality contracts permit 10,000 evidence rows.  Two million
# container tokens leaves room for those rows to carry realistically rich
# measurements while rejecting flat-container allocation attacks well before
# the 64 MB byte limit is reached.  Contract models impose tighter limits on
# extension-bag depth after parsing; this separate limit protects json.loads.
MAX_QUALITY_JSON_NESTING_DEPTH = 64
MAX_QUALITY_JSON_STRUCTURAL_TOKENS = 2_000_000
MAX_QUALITY_JSON_NUMBER_CHARACTERS = MAX_DECIMAL_INPUT_LENGTH


class _DecimalSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that never routes decimal scalars through float."""


def _construct_yaml_decimal(loader: yaml.SafeLoader, node: ScalarNode) -> Decimal:
    value = loader.construct_scalar(node).replace("_", "")
    special = {
        ".inf": "Infinity",
        "+.inf": "Infinity",
        "-.inf": "-Infinity",
        ".nan": "NaN",
    }
    return Decimal(special.get(value.lower(), value))


_DecimalSafeLoader.add_constructor(
    "tag:yaml.org,2002:float",
    _construct_yaml_decimal,
)


def _read(path: str | Path) -> str:
    source = Path(path)
    try:
        return source.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"cannot read {source}: {exc}") from exc


def _read_bounded_regular_file(path: str | Path, maximum: int) -> bytes:
    source = Path(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise InputError(f"cannot open quality artifact {source}: {exc}") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise InputError(f"quality artifact is not a regular file: {source}")
            if before.st_size > maximum:
                raise InputError(f"quality artifact exceeds the {maximum}-byte input limit")
            raw = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
    except InputError:
        raise
    except OSError as exc:
        raise InputError(f"cannot read quality artifact {source}: {exc}") from exc
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or len(raw) != before.st_size:
        raise InputError(f"quality artifact changed while it was being read: {source}")
    if len(raw) > maximum:
        raise InputError(f"quality artifact exceeds the {maximum}-byte input limit")
    return raw


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
            raise InputError(
                f"JSON artifact contains duplicate JSON key length={len(key)} sha256={digest}"
            )
        result[key] = value
    return result


def _preflight_json_structure(raw: bytes) -> None:
    """Bound container allocation before decoding JSON into Python objects.

    ASCII JSON punctuation cannot occur inside a multibyte UTF-8 code point, so
    scanning bytes avoids allocating a decoded string for inputs that already
    exceed these limits.  This is deliberately not a JSON syntax validator;
    ``json.loads`` remains responsible for syntax after the resource checks.
    """

    depth = 0
    structural_tokens = 0
    in_string = False
    escaped = False
    in_number = False
    number_characters = 0

    quote = 0x22
    backslash = 0x5C
    openers = b"{["
    closers = b"}]"
    structural = b"{}[],:"
    number_start = b"-0123456789"
    number_body = b"+-.0123456789Ee"

    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == backslash:
                escaped = True
            elif byte == quote:
                in_string = False
            continue

        if byte == quote:
            in_string = True
            continue
        if in_number:
            if byte in number_body:
                number_characters += 1
                if number_characters > MAX_QUALITY_JSON_NUMBER_CHARACTERS:
                    raise InputError(
                        "cannot parse quality artifact JSON: numeric token exceeds the "
                        f"{MAX_QUALITY_JSON_NUMBER_CHARACTERS}-character limit"
                    )
                continue
            in_number = False
            number_characters = 0
        if byte in number_start:
            in_number = True
            number_characters = 1
            continue
        if byte in openers:
            depth += 1
            if depth > MAX_QUALITY_JSON_NESTING_DEPTH:
                raise InputError(
                    "cannot parse quality artifact JSON: nesting exceeds the "
                    f"{MAX_QUALITY_JSON_NESTING_DEPTH}-level limit"
                )
        elif byte in closers and depth:
            # Malformed or mismatched delimiters are left to json.loads.  Never
            # let an unmatched closer make depth negative during this preflight.
            depth -= 1

        if byte in structural:
            structural_tokens += 1
            if structural_tokens > MAX_QUALITY_JSON_STRUCTURAL_TOKENS:
                raise InputError(
                    "cannot parse quality artifact JSON: structure exceeds the "
                    f"{MAX_QUALITY_JSON_STRUCTURAL_TOKENS}-token limit"
                )


def _load_quality_json(path: str | Path) -> Any:
    raw = _read_bounded_regular_file(path, MAX_QUALITY_ARTIFACT_BYTES)
    _preflight_json_structure(raw)
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            parse_float=Decimal,
            parse_constant=Decimal,
            object_pairs_hook=_unique_json_object,
        )
    except InputError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise InputError(f"cannot parse quality artifact JSON {path}: {exc}") from exc


def _validate(model: type[ModelT], value: Any, path: str | Path) -> ModelT:
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise InputError(f"{path} does not match {model.__name__}:\n{exc}") from exc


def _validate_sensitive(model: type[ModelT], value: Any, path: str | Path) -> ModelT:
    """Validate a local audit artifact without echoing its rejected contents."""

    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise InputError(f"{path} does not match {model.__name__}") from exc


def load_config(path: str | Path) -> ProjectConfig:
    try:
        # This SafeLoader subclass only replaces float construction with exact Decimal.
        loader = _DecimalSafeLoader(_read(path))
        try:
            value = loader.get_single_data()
        finally:
            loader.dispose()
    except yaml.YAMLError as exc:
        raise InputError(f"cannot parse YAML {path}: {exc}") from exc
    return _validate(ProjectConfig, value, path)


def load_catalog(path: str | Path) -> ObservationCatalog:
    try:
        value = json.loads(_read(path), parse_float=Decimal, parse_constant=Decimal)
    except json.JSONDecodeError as exc:
        raise InputError(f"cannot parse JSON {path}: {exc}") from exc
    return _validate(ObservationCatalog, value, path)


def load_frontier_snapshot(path: str | Path) -> FrontierSnapshot:
    try:
        value = json.loads(_read(path), parse_float=Decimal, parse_constant=Decimal)
    except json.JSONDecodeError as exc:
        raise InputError(f"cannot parse JSON {path}: {exc}") from exc
    return _validate(FrontierSnapshot, value, path)


def load_selection_snapshot(path: str | Path) -> SelectionSnapshot:
    try:
        value = json.loads(_read(path), parse_float=Decimal, parse_constant=Decimal)
    except json.JSONDecodeError as exc:
        raise InputError(f"cannot parse JSON {path}: {exc}") from exc
    return _validate(SelectionSnapshot, value, path)


def load_publication_manifest(path: str | Path) -> PublicationManifest:
    try:
        value = json.loads(_read(path), parse_float=Decimal, parse_constant=Decimal)
    except json.JSONDecodeError as exc:
        raise InputError(f"cannot parse JSON {path}: {exc}") from exc
    return _validate(PublicationManifest, value, path)


def load_frontier_history(path: str | Path) -> FrontierHistory:
    try:
        value = json.loads(_read(path), parse_float=Decimal, parse_constant=Decimal)
    except json.JSONDecodeError as exc:
        raise InputError(f"cannot parse JSON {path}: {exc}") from exc
    return _validate(FrontierHistory, value, path)


def load_quality_evidence(path: str | Path) -> QualityEvidenceSet:
    return _validate_sensitive(QualityEvidenceSet, _load_quality_json(path), path)


def load_quality_reconciliation(path: str | Path) -> QualityReconciliation:
    return _validate_sensitive(QualityReconciliation, _load_quality_json(path), path)


def load_quality_import_report(path: str | Path) -> QualityImportReport:
    return _validate_sensitive(QualityImportReport, _load_quality_json(path), path)


def load_portfolio_policy(path: str | Path) -> PortfolioPolicy:
    return _validate_sensitive(PortfolioPolicy, _load_quality_json(path), path)


def load_portfolio_derivation(path: str | Path) -> PortfolioDerivationSnapshot:
    return _validate_sensitive(PortfolioDerivationSnapshot, _load_quality_json(path), path)


def dump_json(model: BaseModel) -> str:
    return model.model_dump_json(indent=2) + "\n"


def public_schemas() -> dict[str, dict[str, Any]]:
    """Load the versioned schemas shipped with this exact package release."""

    result: dict[str, dict[str, Any]] = {}
    source_tree = Path(__file__).resolve().parents[2] / "schemas"
    for name in SCHEMA_IDS:
        if source_tree.is_dir():
            payload = (source_tree / name).read_text(encoding="utf-8")
        else:
            payload = files("model_skyline").joinpath("schemas", name).read_text(encoding="utf-8")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise InputError(f"packaged schema {name} is not a JSON object")
        result[name] = value
    return result


SCHEMA_IDS = {
    "project-config.schema.json": "urn:model-skyline:schema:v1alpha1:project-config",
    "observation-catalog.schema.json": ("urn:model-skyline:schema:v1alpha1:observation-catalog"),
    "frontier-snapshot.schema.json": ("urn:model-skyline:schema:v1alpha1:frontier-snapshot"),
    "selection-snapshot.schema.json": ("urn:model-skyline:schema:v1alpha1:selection-snapshot"),
    "publication-manifest.schema.json": ("urn:model-skyline:schema:v1alpha1:publication-manifest"),
    "frontier-history.schema.json": "urn:model-skyline:schema:v1alpha1:frontier-history",
    "request-trace.schema.json": "urn:model-skyline:schema:v1alpha1:request-trace",
    "request-trace-v1alpha2.schema.json": "urn:model-skyline:schema:v1alpha2:request-trace",
    "request-trace-v1alpha3.schema.json": "urn:model-skyline:schema:v1alpha3:request-trace",
    "harbor-terminal-bench-import-config.schema.json": (
        "urn:model-skyline:schema:v1alpha1:harbor-terminal-bench-import-config"
    ),
    "quality-evidence.schema.json": "urn:model-skyline:schema:v1alpha1:quality-evidence",
    "quality-reconciliation.schema.json": (
        "urn:model-skyline:schema:v1alpha1:quality-reconciliation"
    ),
    "quality-import-report.schema.json": (
        "urn:model-skyline:schema:v1alpha1:quality-import-report"
    ),
    "quality-portfolio-policy.schema.json": (
        "urn:model-skyline:schema:v1alpha1:quality-portfolio-policy"
    ),
    "quality-portfolio-derivation.schema.json": (
        "urn:model-skyline:schema:v1alpha1:quality-portfolio-derivation"
    ),
}


def _normalize_schema(value: Any) -> None:
    if isinstance(value, dict):
        variants = value.get("anyOf")
        if isinstance(variants, list):
            number = next(
                (
                    item
                    for item in variants
                    if isinstance(item, dict) and item.get("type") == "number"
                ),
                None,
            )
            string = next(
                (
                    item
                    for item in variants
                    if isinstance(item, dict)
                    and item.get("type") == "string"
                    and isinstance(item.get("pattern"), str)
                ),
                None,
            )
            if number is not None and string is not None:
                # Pydantic emits constraints applied to an optional Decimal as
                # non-standard sibling keywords (``gt``, ``ge``, ...). Move
                # them onto the numeric branch before mirroring them into the
                # string branch below.
                for source_key, target_key in (
                    ("gt", "exclusiveMinimum"),
                    ("ge", "minimum"),
                    ("lt", "exclusiveMaximum"),
                    ("le", "maximum"),
                ):
                    if source_key in value:
                        number[target_key] = value.pop(source_key)
                pattern = string["pattern"]
                guards = ""
                minimum = number.get("minimum")
                exclusive_minimum = number.get("exclusiveMinimum")
                maximum = number.get("maximum")
                if minimum == 0 or exclusive_minimum == 0:
                    guards += r"(?!-)"
                if exclusive_minimum == 0:
                    guards += r"(?![+]?0*(?:\.0*)?$)"
                if minimum == 0 and maximum == 1:
                    guards += r"(?=[+]?(?:0*(?:\.\d*)?|0*1(?:\.0*)?)$)"
                pattern_body = pattern.removeprefix("^").removesuffix("$")
                string["pattern"] = f"^{guards}(?:{pattern_body})$"
                string["maxLength"] = min(
                    string.get("maxLength", MAX_DECIMAL_INPUT_LENGTH),
                    MAX_DECIMAL_INPUT_LENGTH,
                )
        for child in value.values():
            _normalize_schema(child)
    elif isinstance(value, list):
        for child in value:
            _normalize_schema(child)


def _non_null_property(name: str) -> dict[str, Any]:
    return {
        "properties": {name: {"not": {"type": "null"}}},
        "required": [name],
    }


def _request_trace_conditionals(
    schema: dict[str, Any],
    *,
    allow_model_call: bool,
) -> None:
    """Add cross-field structure that Pydantic cannot emit as JSON Schema."""

    producer_fields = (
        "adapter_id",
        "adapter_version",
        "upstream_system",
        "upstream_version",
        "upstream_commit",
    )
    collector_fields = ("collector_id", "collector_version")
    model_token_meters = (
        "input_uncached_tokens",
        "input_cache_read_tokens",
        "input_cache_write_tokens",
        "input_cache_write_5m_tokens",
        "input_cache_write_1h_tokens",
        "input_total_tokens",
        "output_tokens",
        "reasoning_tokens",
        "output_total_tokens",
    )
    null_or_zero_decimal = {
        "anyOf": [
            {"type": "null"},
            {"const": 0},
            {"pattern": r"^[+]?(?:0+(?:\.0*)?|\.0+)$", "type": "string"},
        ]
    }
    null_or_zero_count = {"anyOf": [{"type": "null"}, {"const": 0}]}
    non_null_producer = {"anyOf": [_non_null_property(name) for name in producer_fields]}
    non_null_collector = {"anyOf": [_non_null_property(name) for name in collector_fields]}
    producer_required = {
        "properties": {name: {"not": {"type": "null"}} for name in producer_fields},
        "required": list(producer_fields),
    }
    collector_required = {
        "properties": {name: {"not": {"type": "null"}} for name in collector_fields},
        "required": list(collector_fields),
    }

    schema["$comment"] = (
        "This schema enforces row-local structural invariants. Consumers MUST also run the "
        "ModelSkyline RequestTrace semantic validator: JSON Schema cannot express exact Decimal "
        "arithmetic between input/output totals and their components, and file-level aggregation "
        "adds cross-row identity, scope, outcome, offering, timestamp, and provenance checks."
    )
    aggregate_units = ["attempt"]
    non_request_units = ["attempt", "work_unit"]
    if allow_model_call:
        aggregate_units.insert(0, "model_call")
        non_request_units.insert(0, "model_call")
    aggregate_unit_schema: dict[str, Any] = (
        {"enum": aggregate_units} if allow_model_call else {"const": "attempt"}
    )
    schema["allOf"] = [
        {
            "if": {
                "anyOf": [
                    {"not": {"required": ["observation_unit"]}},
                    {
                        "properties": {"observation_unit": {"const": "request"}},
                        "required": ["observation_unit"],
                    },
                ]
            },
            "then": {
                "properties": {
                    "attempt_count": {"type": "null"},
                    "model_request_count": {"anyOf": [{"type": "null"}, {"const": 1}]},
                }
            },
        },
        {
            "if": {
                "properties": {"observation_unit": aggregate_unit_schema},
                "required": ["observation_unit"],
            },
            "then": {"properties": {"attempt_count": {"type": "null"}}},
        },
        {
            "if": {
                "properties": {"observation_unit": {"enum": non_request_units}},
                "required": ["observation_unit"],
            },
            "then": {
                "properties": {
                    "output_tokens_per_second": {"type": "null"},
                    "ttft_ms": {"type": "null"},
                }
            },
        },
        {
            "if": {
                "properties": {"model_request_count": {"const": 0}},
                "required": ["model_request_count"],
            },
            "then": {"properties": {name: null_or_zero_decimal for name in model_token_meters}},
        },
        {
            "if": {
                "properties": {"attempt_count": {"const": 0}},
                "required": ["attempt_count"],
            },
            "then": {
                "properties": {
                    "model_request_count": null_or_zero_count,
                    **{name: null_or_zero_decimal for name in model_token_meters},
                }
            },
        },
        {
            "if": _non_null_property("input_cache_write_tokens"),
            "then": {
                "properties": {
                    "input_cache_write_1h_tokens": {"type": "null"},
                    "input_cache_write_5m_tokens": {"type": "null"},
                }
            },
        },
        {
            "if": {
                "anyOf": [
                    _non_null_property("input_cache_write_5m_tokens"),
                    _non_null_property("input_cache_write_1h_tokens"),
                ]
            },
            "then": {"properties": {"input_cache_write_tokens": {"type": "null"}}},
        },
        {"if": non_null_producer, "then": producer_required},
        {
            "if": non_null_collector,
            "then": {"allOf": [collector_required, producer_required]},
        },
    ]


def _configure_request_trace_schema(
    schema: dict[str, Any],
    *,
    version: Literal["v1alpha2", "v1alpha3"],
) -> None:
    properties = schema["properties"]
    schema_version = properties["schema_version"]
    schema_version.clear()
    schema_version.update(
        {
            "const": f"model-skyline/request-trace/{version}",
            "title": "Schema Version",
            "type": "string",
        }
    )
    allow_model_call = version == "v1alpha3"
    if not allow_model_call:
        properties["observation_unit"]["description"] = "Granularity represented by this row."
        properties["observation_unit"]["enum"] = ["request", "attempt", "work_unit"]
        properties["model_request_count"]["description"] = (
            "Actual model requests represented by an aggregate row; unknown when omitted. "
            "Request rows implicitly represent one."
        )
        properties["attempt_count"]["description"] = (
            "Actual attempts represented by a work-unit row; unknown when omitted. "
            "Request and attempt rows derive attempts from attempt_id."
        )
    _request_trace_conditionals(schema, allow_model_call=allow_model_call)


def _project_config_conditionals(schema: dict[str, Any]) -> None:
    """Mirror FormulaMetric's USD accounting-basis invariant in JSON Schema."""

    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return
    formula_metric = definitions.get("FormulaMetric")
    if not isinstance(formula_metric, dict):
        return
    formula_metric["allOf"] = [
        {
            "if": {
                "properties": {
                    "unit": {
                        "anyOf": [
                            {"const": "USD"},
                            {"pattern": r"^USD/", "type": "string"},
                        ]
                    }
                },
                "required": ["unit"],
            },
            "then": {
                "properties": {"cost_basis": {"not": {"type": "null"}}},
                "required": ["cost_basis"],
            },
        }
    ]


def _quality_evidence_conditionals(schema: dict[str, Any]) -> None:
    """Mirror evidence row and subject-kind invariants in JSON Schema."""

    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return
    evidence_row = definitions.get("QualityEvidenceRow")
    if isinstance(evidence_row, dict):
        evidence_row["oneOf"] = [
            {
                "properties": {
                    "result": {"not": {"type": "null"}},
                    "invalid_result": {"type": "null"},
                },
                "required": ["result"],
            },
            {
                "properties": {
                    "result": {"type": "null"},
                    "invalid_result": {"not": {"type": "null"}},
                },
                "required": ["invalid_result"],
            },
        ]
    subject = definitions.get("QualitySubjectIdentity")
    if isinstance(subject, dict):
        subject["allOf"] = [
            {
                "if": {
                    "properties": {"kind": {"const": "single_model_system"}},
                    "required": ["kind"],
                },
                "then": {
                    "properties": {"model_claims": {"minItems": 1, "maxItems": 1}},
                    "required": ["model_claims"],
                },
            },
            {
                "if": {
                    "properties": {"kind": {"const": "composite_system"}},
                    "required": ["kind"],
                },
                "then": {
                    "properties": {"model_claims": {"minItems": 1}},
                    "required": ["model_claims"],
                },
            },
            {
                "if": {
                    "properties": {"kind": {"const": "undisclosed_system"}},
                    "required": ["kind"],
                },
                "then": {"properties": {"model_claims": {"maxItems": 0}}},
            },
        ]


def _quality_complete_offering_key(schema: dict[str, Any]) -> None:
    """Require every complete OfferingKey field in quality wire artifacts."""

    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return
    offering = definitions.get("OfferingKey")
    if not isinstance(offering, dict):
        return
    properties = offering.get("properties")
    if isinstance(properties, dict):
        offering["required"] = list(properties)


def _harbor_import_config_conditionals(schema: dict[str, Any]) -> None:
    """Expose adapter URL, review-text, and Terminal-Bench target invariants."""

    safe_text = (
        r"^[^\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f"
        r"\u202a-\u202e\u2066-\u2069\ud800-\udfff\ufffe\uffff]+$"
    )
    safe_https = (
        r"^https://(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"
        r"|\[[0-9A-Fa-f:]+\])(?::[0-9]{1,5})?"
        r"(?:/[^\u0000-\u0020\u007f-\u009f?#]*)?$"
    )
    properties = schema.get("properties")
    definitions = schema.get("$defs")
    if not isinstance(properties, dict) or not isinstance(definitions, dict):
        return
    for field in ("source_url", "methodology_url"):
        value = properties.get(field)
        if isinstance(value, dict):
            value.update({"format": "uri", "pattern": safe_https})
    capture_version = properties.get("capture_tool_version")
    if isinstance(capture_version, dict):
        capture_version["pattern"] = safe_text

    rights = definitions.get("QualityRights")
    if isinstance(rights, dict):
        rights_properties = rights.get("properties")
        if isinstance(rights_properties, dict):
            rights_properties["terms_locator"] = {
                "format": "uri",
                "maxLength": 4096,
                "minLength": 1,
                "pattern": safe_https,
                "type": "string",
            }
            for field in ("license_expression", "review_evidence"):
                value = rights_properties.get(field)
                if isinstance(value, dict):
                    value["pattern"] = safe_text
        required = rights.get("required")
        if isinstance(required, list) and "terms_locator" not in required:
            required.append("terms_locator")

    entry = definitions.get("QualityReconciliationEntry")
    if isinstance(entry, dict):
        entry_properties = entry.get("properties")
        if isinstance(entry_properties, dict):
            review_evidence = entry_properties.get("review_evidence")
            if isinstance(review_evidence, dict):
                review_evidence["pattern"] = safe_text

    offering = definitions.get("OfferingKey")
    if not isinstance(offering, dict):
        return
    offering_properties = offering.get("properties")
    if not isinstance(offering_properties, dict):
        return
    provider = offering_properties.get("provider")
    if isinstance(provider, dict):
        provider["not"] = {"const": "unknown"}
    agent_harness = offering_properties.get("agent_harness")
    if isinstance(agent_harness, dict):
        agent_harness["not"] = {"type": "null"}
    capabilities = offering_properties.get("capabilities")
    if isinstance(capabilities, dict):
        capabilities["contains"] = {"const": "tools"}
        capabilities["minContains"] = 1
        capabilities["uniqueItems"] = True
    for value in offering_properties.values():
        if not isinstance(value, dict):
            continue
        if value.get("type") == "string":
            value.setdefault("pattern", safe_text)
        variants = value.get("anyOf")
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict) and variant.get("type") == "string":
                    variant.setdefault("pattern", safe_text)


def _portfolio_policy_conditionals(schema: dict[str, Any]) -> None:
    """Expose straightforward set semantics to non-Python validators."""

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    for field in ("components", "required_component_ids"):
        value = properties.get(field)
        if isinstance(value, dict):
            value["uniqueItems"] = True


def generated_schemas() -> dict[str, dict[str, Any]]:
    """Generate candidate schemas from models for maintainer review."""

    from model_skyline.adapters.harbor import HarborTerminalBenchImportConfig
    from model_skyline.traces import RequestTrace

    request_trace_schema = RequestTrace.model_json_schema(mode="validation")
    generated = {
        "project-config.schema.json": ProjectConfig.model_json_schema(mode="validation"),
        "observation-catalog.schema.json": ObservationCatalog.model_json_schema(mode="validation"),
        "frontier-snapshot.schema.json": FrontierSnapshot.model_json_schema(mode="serialization"),
        "selection-snapshot.schema.json": SelectionSnapshot.model_json_schema(mode="serialization"),
        "publication-manifest.schema.json": PublicationManifest.model_json_schema(
            mode="serialization"
        ),
        "frontier-history.schema.json": FrontierHistory.model_json_schema(mode="serialization"),
        "request-trace-v1alpha2.schema.json": deepcopy(request_trace_schema),
        "request-trace-v1alpha3.schema.json": deepcopy(request_trace_schema),
        "harbor-terminal-bench-import-config.schema.json": (
            HarborTerminalBenchImportConfig.model_json_schema(mode="validation")
        ),
        "quality-evidence.schema.json": QualityEvidenceSet.model_json_schema(mode="validation"),
        "quality-reconciliation.schema.json": QualityReconciliation.model_json_schema(
            mode="validation"
        ),
        "quality-import-report.schema.json": QualityImportReport.model_json_schema(
            mode="serialization"
        ),
        "quality-portfolio-policy.schema.json": PortfolioPolicy.model_json_schema(
            mode="validation"
        ),
        "quality-portfolio-derivation.schema.json": (
            PortfolioDerivationSnapshot.model_json_schema(mode="serialization")
        ),
    }
    result: dict[str, dict[str, Any]] = {}
    for name, schema in generated.items():
        _normalize_schema(schema)
        if name == "project-config.schema.json":
            _project_config_conditionals(schema)
        if name == "quality-evidence.schema.json":
            _quality_evidence_conditionals(schema)
        if name.startswith("quality-"):
            _quality_complete_offering_key(schema)
        if name == "harbor-terminal-bench-import-config.schema.json":
            _quality_complete_offering_key(schema)
            _harbor_import_config_conditionals(schema)
        if name == "quality-portfolio-policy.schema.json":
            _portfolio_policy_conditionals(schema)
        if name == "request-trace-v1alpha2.schema.json":
            _configure_request_trace_schema(schema, version="v1alpha2")
        if name == "request-trace-v1alpha3.schema.json":
            _configure_request_trace_schema(schema, version="v1alpha3")
        generated_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": SCHEMA_IDS[name],
            **schema,
        }
        if name == "harbor-terminal-bench-import-config.schema.json":
            generated_schema["$comment"] = (
                "This configuration contains reviewed acquisition provenance, rights, and "
                "exact row reconciliation only; raw Harbor leaderboard bytes are a separate "
                "bounded input. Structural validation does not attest capture authenticity, "
                "publication rights, or route identity, and no configured command is executed. "
                "Consumers MUST run ModelSkyline semantic validation, which additionally rejects "
                "private or special-use URL hosts, invalid ports, duplicate set members, unsafe "
                "nested text, incomplete OfferingKeys, and inconsistent reconciliation data."
            )
        if name == "quality-import-report.schema.json":
            generated_schema["$comment"] = (
                "This local audit report is intentionally not publication-safe. Its "
                "publication_safe field is always false; consumers must create a separately "
                "reviewed derived or full publication projection under the source rights. "
                "JSON Schema does not enforce unique row or offering identities, content "
                "digests, or the reconciliation-derived outcome inventory; run the "
                "ModelSkyline semantic validator before use."
            )
        if name == "quality-evidence.schema.json":
            generated_schema["$comment"] = (
                "Normalized evidence may still contain restricted labels, claims, locators, "
                "and metadata and is not automatically publication-safe. One rights assertion "
                "covers every row; adapters must split mixed-rights inputs into separate sets. "
                "JSON Schema does not enforce unique row IDs, canonical ordering, content "
                "digests, or whole-artifact byte limits; run the ModelSkyline semantic validator."
            )
        if name == "quality-reconciliation.schema.json":
            generated_schema["$comment"] = (
                "Structural validation does not prove a benchmark row belongs to a route. "
                "A reviewer must verify the source and subject identity digests, relationship, "
                "complete OfferingKey, and review evidence; fuzzy or alias matching is forbidden. "
                "JSON Schema does not enforce unique row IDs, canonical ordering, or artifact "
                "limits; run the ModelSkyline semantic validator."
            )
        if name == "quality-portfolio-policy.schema.json":
            generated_schema["$comment"] = (
                "This stable policy contains operator intent only; exact frontier snapshots, "
                "catalog/config hashes, retrievals, rights, and candidate failures belong in "
                "the derivation lock. JSON Schema cannot enforce distinct component/frontier "
                "IDs, unique output signals, required coverage, or correlation grouping; run "
                "ModelSkyline semantic validation."
            )
        if name == "quality-portfolio-derivation.schema.json":
            generated_schema["$comment"] = (
                "This compact lock does not duplicate selected AxisEstimate values. Consumers "
                "must replay it against the trusted policy, base ObservationCatalog, and exact "
                "frontier artifacts before routing; its catalog hash binds the enriched output."
            )
        result[name] = generated_schema
    return result
