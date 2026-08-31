from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError as PydanticValidationError

from model_skyline.engine import FrontierEngine
from model_skyline.io import generated_schemas, public_schemas
from model_skyline.models import AxisEstimate, ObservationCatalog, ProjectConfig
from model_skyline.publisher import publish_project
from model_skyline.selection import select_models
from model_skyline.traces import RequestTrace

NOW = datetime(2026, 8, 29, 19, tzinfo=UTC)
TRACE_V2 = "model-skyline/request-trace/v1alpha2"
TRACE_V3 = "model-skyline/request-trace/v1alpha3"
SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas"


def _valid(schema: dict, payload: object) -> None:
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


def _trace_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": TRACE_V2,
        "timestamp": "2026-08-29T19:00:00Z",
        "workload_id": "coding-session-v1",
        "workload_version": "1.0.0",
        "work_unit_id": "session-1",
        "offering_id": "provider/model@region-tier",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "work_unit_success": "1",
    }
    payload.update(updates)
    return payload


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


def test_committed_publication_schemas_validate_publisher_artifacts(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    schemas = public_schemas()
    result = publish_project(
        example_config,
        [example_catalog],
        tmp_path.resolve() / "site",
        project_id="schema-test",
        generated_at=NOW,
    )
    history = result.manifest.frontiers[0].history

    _valid(
        schemas["publication-manifest.schema.json"],
        result.manifest.model_dump(mode="json"),
    )
    _valid(
        schemas["frontier-history.schema.json"],
        json.loads((tmp_path.resolve() / "site" / history.path).read_text()),
    )

    payload = result.manifest.model_dump(mode="json")
    for unsafe in ("a/../b.json", "a//b.json", "a/./b.json", r"a\b.json"):
        invalid = deepcopy(payload)
        invalid["frontiers"][0]["snapshot"]["path"] = unsafe
        with pytest.raises(JsonSchemaValidationError):
            _valid(schemas["publication-manifest.schema.json"], invalid)


def test_request_trace_schema_versions_are_distinct_and_v1alpha1_is_preserved() -> None:
    schemas = public_schemas()
    legacy = schemas["request-trace.schema.json"]
    previous = schemas["request-trace-v1alpha2.schema.json"]
    current = schemas["request-trace-v1alpha3.schema.json"]

    assert legacy["$id"] == "urn:model-skyline:schema:v1alpha1:request-trace"
    assert "schema_version" not in legacy["properties"]
    assert legacy["properties"]["input_uncached_tokens"]["default"] == "0"
    assert previous["$id"] == "urn:model-skyline:schema:v1alpha2:request-trace"
    assert previous["properties"]["schema_version"]["const"] == TRACE_V2
    assert "model_call" not in previous["properties"]["observation_unit"]["enum"]
    assert current["$id"] == "urn:model-skyline:schema:v1alpha3:request-trace"
    assert current["properties"]["schema_version"]["const"] == TRACE_V3
    assert "model_call" in current["properties"]["observation_unit"]["enum"]
    assert "schema_version" in current["required"]
    _valid(
        legacy,
        {key: value for key, value in _trace_payload().items() if key != "schema_version"},
    )


def test_released_request_trace_v1alpha2_schema_bytes_are_immutable() -> None:
    digest = hashlib.sha256(
        (SCHEMA_ROOT / "request-trace-v1alpha2.schema.json").read_bytes()
    ).hexdigest()

    assert digest == "405a150c126da7bd7b788f3fe9e2839f6e3e9327573e68248d47defcb3fc5b5b"


@pytest.mark.parametrize(
    "name",
    ["request-trace-v1alpha2.schema.json", "request-trace-v1alpha3.schema.json"],
)
def test_committed_request_trace_schemas_match_generator(name: str) -> None:
    assert public_schemas()[name] == generated_schemas()[name]


@pytest.mark.parametrize(
    "updates",
    [
        {},
        {"model_request_count": 1, "attempt_count": None},
        {"observation_unit": "attempt", "model_request_count": 2},
        {"observation_unit": "work_unit", "model_request_count": 2, "attempt_count": 1},
        {"input_cache_write_tokens": "4"},
        {"input_cache_write_5m_tokens": "1", "input_cache_write_1h_tokens": "3"},
        {
            "adapter_id": "model-skyline.codex",
            "adapter_version": "0.4.0",
            "upstream_system": "openai.codex",
            "upstream_version": "0.144.2",
            "upstream_commit": "0" * 40,
            "collector_id": "codex-jsonl",
            "collector_version": "0.4.0",
        },
        {
            "observation_unit": "work_unit",
            "model_request_count": 0,
            "attempt_count": 0,
            "input_total_tokens": "+000.000",
            "output_total_tokens": 0,
            "cache_storage_token_hours": "3",
            "tool_calls": "2",
            "other_cost_usd": "0.01",
        },
    ],
)
def test_request_trace_v1alpha2_schema_accepts_structurally_coherent_rows(
    updates: dict[str, object],
) -> None:
    _valid(public_schemas()["request-trace-v1alpha2.schema.json"], _trace_payload(**updates))


@pytest.mark.parametrize(
    "updates",
    [
        {"observation_unit": "model_call"},
        {"observation_unit": "model_call", "model_request_count": 2},
    ],
)
def test_request_trace_v1alpha3_schema_accepts_model_call_rows(
    updates: dict[str, object],
) -> None:
    _valid(
        public_schemas()["request-trace-v1alpha3.schema.json"],
        _trace_payload(schema_version=TRACE_V3, **updates),
    )


@pytest.mark.parametrize(
    "payload",
    [
        _trace_payload(schema_version="model-skyline/request-trace/v1alpha1"),
        {key: value for key, value in _trace_payload().items() if key != "schema_version"},
        _trace_payload(model_request_count=2),
        _trace_payload(attempt_count=1),
        _trace_payload(observation_unit="model_call"),
        _trace_payload(observation_unit="model_call", attempt_count=1),
        _trace_payload(observation_unit="model_call", ttft_ms="1"),
        _trace_payload(observation_unit="attempt", attempt_count=1),
        _trace_payload(observation_unit="attempt", ttft_ms="1"),
        _trace_payload(observation_unit="work_unit", output_tokens_per_second="1"),
        _trace_payload(input_cache_write_tokens="1", input_cache_write_5m_tokens="1"),
        _trace_payload(adapter_id="model-skyline.codex"),
        _trace_payload(
            adapter_id="model-skyline.codex",
            adapter_version="0.4.0",
            upstream_system="openai.codex",
            upstream_version="0.144.2",
            upstream_commit=None,
        ),
        _trace_payload(collector_id="codex-jsonl"),
        _trace_payload(
            adapter_id="model-skyline.codex",
            adapter_version="0.4.0",
            upstream_system="openai.codex",
            upstream_version="0.144.2",
            upstream_commit="0" * 40,
            collector_id="codex-jsonl",
        ),
        _trace_payload(
            observation_unit="work_unit",
            model_request_count=0,
            input_total_tokens="1",
        ),
        _trace_payload(
            observation_unit="work_unit",
            model_request_count=1,
            attempt_count=0,
        ),
        _trace_payload(
            observation_unit="work_unit",
            attempt_count=0,
            output_tokens="0.1",
        ),
    ],
)
def test_request_trace_v1alpha2_schema_rejects_structural_incoherence(
    payload: dict[str, object],
) -> None:
    with pytest.raises(JsonSchemaValidationError):
        _valid(public_schemas()["request-trace-v1alpha2.schema.json"], payload)


@pytest.mark.parametrize(
    "payload",
    [
        _trace_payload(schema_version=TRACE_V3, observation_unit="model_call", attempt_count=1),
        _trace_payload(schema_version=TRACE_V3, observation_unit="model_call", ttft_ms="1"),
        _trace_payload(schema_version=TRACE_V2, observation_unit="model_call"),
    ],
)
def test_request_trace_v1alpha3_schema_rejects_incoherent_or_mismatched_rows(
    payload: dict[str, object],
) -> None:
    with pytest.raises(JsonSchemaValidationError):
        _valid(public_schemas()["request-trace-v1alpha3.schema.json"], payload)


def test_request_trace_v1alpha2_schema_defers_decimal_arithmetic_to_semantic_validator() -> None:
    payload = _trace_payload(
        input_uncached_tokens="2",
        input_cache_read_tokens="0",
        input_cache_write_tokens="0",
        input_total_tokens="1",
    )

    _valid(public_schemas()["request-trace-v1alpha2.schema.json"], payload)
    with pytest.raises(PydanticValidationError, match="input_total_tokens"):
        RequestTrace.model_validate(payload)


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


def test_catalog_schema_keeps_new_billing_mode_optional_for_legacy_payloads(
    example_catalog: ObservationCatalog,
) -> None:
    schema = public_schemas()["observation-catalog.schema.json"]
    payload = example_catalog.model_dump(mode="json")
    for offering in payload["offerings"]:
        offering["offering"].pop("billing_mode")

    _valid(schema, payload)


def test_generated_config_schema_enforces_decimal_sign_constraints(
    example_config: ProjectConfig,
) -> None:
    schema = generated_schemas()["project-config.schema.json"]
    payload = example_config.model_dump(mode="json")
    payload["frontiers"]["coding-value"]["axes"][0]["epsilon_absolute"] = "-1"

    with pytest.raises(JsonSchemaValidationError):
        _valid(schema, payload)


@pytest.mark.parametrize("missing", [True, False], ids=["missing", "null"])
def test_config_schema_requires_cost_basis_for_usd_formulas(
    missing: bool,
    example_config: ProjectConfig,
) -> None:
    schema = generated_schemas()["project-config.schema.json"]
    payload = example_config.model_dump(mode="json")
    metric = payload["metrics"]["total_cost_per_success"]
    if missing:
        metric.pop("cost_basis")
    else:
        metric["cost_basis"] = None

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
    schema = generated_schemas()["request-trace-v1alpha2.schema.json"]
    payload = _trace_payload(work_unit_success=bad_success)

    with pytest.raises(JsonSchemaValidationError):
        _valid(schema, payload)


@pytest.mark.parametrize("bad_ttft", [-1, "-1"])
def test_generated_trace_schema_enforces_optional_decimal_sign(
    bad_ttft: object,
) -> None:
    schema = generated_schemas()["request-trace-v1alpha2.schema.json"]
    payload = _trace_payload(ttft_ms=bad_ttft)

    with pytest.raises(JsonSchemaValidationError):
        _valid(schema, payload)
