from __future__ import annotations

import json
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any, TypeVar

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


class InputError(ValueError):
    """A configuration or artifact file cannot be loaded or validated."""


ModelT = TypeVar("ModelT", bound=BaseModel)


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


def _validate(model: type[ModelT], value: Any, path: str | Path) -> ModelT:
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise InputError(f"{path} does not match {model.__name__}:\n{exc}") from exc


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
    "frontier-proximity.schema.json": "urn:model-skyline:schema:v1alpha1:frontier-proximity",
    "multi-frontier-selection-snapshot.schema.json": (
        "urn:model-skyline:schema:v1alpha1:multi-frontier-selection-snapshot"
    ),
    "request-trace.schema.json": "urn:model-skyline:schema:v1alpha1:request-trace",
    "request-trace-v1alpha2.schema.json": "urn:model-skyline:schema:v1alpha2:request-trace",
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


def _request_trace_v1alpha2_conditionals(schema: dict[str, Any]) -> None:
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
                "properties": {"observation_unit": {"const": "attempt"}},
                "required": ["observation_unit"],
            },
            "then": {"properties": {"attempt_count": {"type": "null"}}},
        },
        {
            "if": {
                "properties": {"observation_unit": {"enum": ["attempt", "work_unit"]}},
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


def generated_schemas() -> dict[str, dict[str, Any]]:
    """Generate candidate schemas from models for maintainer review."""

    from model_skyline.selection_overlap import generated_overlap_schemas
    from model_skyline.traces import RequestTrace

    generated = {
        "project-config.schema.json": ProjectConfig.model_json_schema(mode="validation"),
        "observation-catalog.schema.json": ObservationCatalog.model_json_schema(mode="validation"),
        "frontier-snapshot.schema.json": FrontierSnapshot.model_json_schema(mode="serialization"),
        "selection-snapshot.schema.json": SelectionSnapshot.model_json_schema(mode="serialization"),
        "publication-manifest.schema.json": PublicationManifest.model_json_schema(
            mode="serialization"
        ),
        "frontier-history.schema.json": FrontierHistory.model_json_schema(mode="serialization"),
        "request-trace-v1alpha2.schema.json": RequestTrace.model_json_schema(mode="validation"),
    }
    result: dict[str, dict[str, Any]] = {}
    for name, schema in generated.items():
        _normalize_schema(schema)
        if name == "project-config.schema.json":
            _project_config_conditionals(schema)
        if name == "request-trace-v1alpha2.schema.json":
            _request_trace_v1alpha2_conditionals(schema)
        result[name] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": SCHEMA_IDS[name],
            **schema,
        }
    result.update(generated_overlap_schemas())
    return result
