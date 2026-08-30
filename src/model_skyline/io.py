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
    FrontierSnapshot,
    ObservationCatalog,
    ProjectConfig,
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
        value = yaml.load(_read(path), Loader=_DecimalSafeLoader)
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
    "request-trace.schema.json": "urn:model-skyline:schema:v1alpha1:request-trace",
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


def generated_schemas() -> dict[str, dict[str, Any]]:
    """Generate candidate schemas from models for maintainer review."""

    from model_skyline.traces import RequestTrace

    generated = {
        "project-config.schema.json": ProjectConfig.model_json_schema(mode="validation"),
        "observation-catalog.schema.json": ObservationCatalog.model_json_schema(mode="validation"),
        "frontier-snapshot.schema.json": FrontierSnapshot.model_json_schema(mode="serialization"),
        "selection-snapshot.schema.json": SelectionSnapshot.model_json_schema(mode="serialization"),
        "request-trace.schema.json": RequestTrace.model_json_schema(mode="validation"),
    }
    result: dict[str, dict[str, Any]] = {}
    for name, schema in generated.items():
        _normalize_schema(schema)
        result[name] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": SCHEMA_IDS[name],
            **schema,
        }
    return result
