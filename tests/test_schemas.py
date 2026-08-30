from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from model_skyline.engine import FrontierEngine
from model_skyline.io import generated_schemas, public_schemas
from model_skyline.models import AxisEstimate, ObservationCatalog, ProjectConfig
from model_skyline.selection import select_models

NOW = datetime(2026, 8, 29, 19, tzinfo=UTC)


def _valid(schema: dict, payload: object) -> None:
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


def test_committed_schemas_validate_real_public_artifacts(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    schemas = public_schemas()
    frontier = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    selection = select_models(
        example_config,
        frontier,
        "coding-agent-defaults",
    )

    _valid(schemas["project-config.schema.json"], example_config.model_dump(mode="json"))
    _valid(
        schemas["observation-catalog.schema.json"],
        example_catalog.model_dump(mode="json"),
    )
    _valid(schemas["frontier-snapshot.schema.json"], frontier.model_dump(mode="json"))
    _valid(schemas["selection-snapshot.schema.json"], selection.model_dump(mode="json"))
    assert all(schema["$id"].startswith("urn:model-skyline:schema:") for schema in schemas.values())


def test_decimal_artifact_serialization_is_fixed_point() -> None:
    estimate = AxisEstimate(value=Decimal("1E+6"), unit="tokens")

    assert estimate.model_dump(mode="json")["value"] == "1000000"


def test_catalog_schema_and_runtime_accept_high_precision_bounded_decimals(
    example_catalog: ObservationCatalog,
) -> None:
    schema = public_schemas()["observation-catalog.schema.json"]
    payload = example_catalog.model_dump(mode="json")
    value = "0.123456789012345678901234567890123456789"
    payload["offerings"][0]["signals"]["success_rate"]["value"] = value

    _valid(schema, payload)
    loaded = ObservationCatalog.model_validate(payload)

    assert loaded.offerings[0].signals["success_rate"].value == Decimal(value)


def test_generated_config_schema_enforces_decimal_sign_constraints(
    example_config: ProjectConfig,
) -> None:
    schema = generated_schemas()["project-config.schema.json"]
    payload = example_config.model_dump(mode="json")
    payload["frontiers"]["coding-value"]["axes"][0]["epsilon_absolute"] = "-1"

    with pytest.raises(JsonSchemaValidationError):
        _valid(schema, payload)


def test_generated_catalog_schema_rejects_secret_bearing_source_urls(
    example_catalog: ObservationCatalog,
) -> None:
    schema = generated_schemas()["observation-catalog.schema.json"]
    payload = example_catalog.model_dump(mode="json")
    payload["offerings"][0]["default_source"]["url"] = "https://example.test/source?api_key=secret"

    with pytest.raises(JsonSchemaValidationError):
        _valid(schema, payload)


@pytest.mark.parametrize("max_age", [0, "0", "0.0"])
def test_generated_config_schema_enforces_optional_decimal_constraints(
    example_config: ProjectConfig,
    max_age: object,
) -> None:
    schema = generated_schemas()["project-config.schema.json"]
    payload = example_config.model_dump(mode="json")
    payload["metrics"]["total_cost_per_success"]["requirements"]["max_age_hours"] = max_age

    with pytest.raises(JsonSchemaValidationError):
        _valid(schema, payload)


@pytest.mark.parametrize("bad_success", ["-1", "2", "0.1234567891"])
def test_generated_trace_schema_matches_decimal_runtime_constraints(
    bad_success: str,
) -> None:
    schema = generated_schemas()["request-trace.schema.json"]
    payload = {
        "timestamp": "2026-08-29T19:00:00Z",
        "workload_id": "coding-session-v1",
        "workload_version": "1.0.0",
        "work_unit_id": "session-1",
        "offering_id": "provider/model@region-tier",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "work_unit_success": bad_success,
    }

    with pytest.raises(JsonSchemaValidationError):
        _valid(schema, payload)


@pytest.mark.parametrize("bad_ttft", [-1, "-1"])
def test_generated_trace_schema_enforces_optional_decimal_sign(
    bad_ttft: object,
) -> None:
    schema = generated_schemas()["request-trace.schema.json"]
    payload = {
        "timestamp": "2026-08-29T19:00:00Z",
        "workload_id": "coding-session-v1",
        "workload_version": "1.0.0",
        "work_unit_id": "session-1",
        "offering_id": "provider/model@region-tier",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "work_unit_success": "1",
        "ttft_ms": bad_ttft,
    }

    with pytest.raises(JsonSchemaValidationError):
        _valid(schema, payload)
