from __future__ import annotations

import hashlib
import json
import os
import stat
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import (  # type: ignore[import-untyped]
    ValidationError as JsonSchemaValidationError,
)
from pydantic import ValidationError as PydanticValidationError
from typer.testing import CliRunner

from model_skyline.adapters.harbor import (
    PARSER_VERSION,
    HarborAdapterError,
    harbor_value_sha256,
    import_harbor_terminal_bench,
    import_harbor_terminal_bench_bytes,
    inspect_harbor_terminal_bench_bytes,
    load_harbor_terminal_bench_mapping,
    normalize_harbor_terminal_bench_bytes,
    write_harbor_terminal_bench_import,
)
from model_skyline.cli import app
from model_skyline.io import (
    generated_schemas,
    load_catalog,
    load_config,
    load_quality_evidence,
    load_quality_import_report,
)

RETRIEVED_AT = datetime(2026, 8, 31, 12, tzinfo=UTC)
BOARD_ID = "11111111-1111-4111-8111-111111111111"
PACKAGE_ID = "22222222-2222-4222-8222-222222222222"
DATASET_ID = "33333333-3333-4333-8333-333333333333"
ROW_ID = "44444444-4444-4444-8444-444444444444"
RUNNER = CliRunner()


def _leaderboard_value() -> dict[str, Any]:
    return {
        "leaderboard": {
            "id": BOARD_ID,
            "package_id": PACKAGE_ID,
            "package": "terminal-bench/terminal-bench",
            "dataset_version_ids": [DATASET_ID],
            "name": "4-0-0",
            "title": "Terminal-Bench 4.0 synthetic fixture",
            "description": "Synthetic adapter test; not an upstream result.",
            "metadata_schema": {
                "type": "object",
                "required": [
                    "agent_display",
                    "agent_org",
                    "date",
                    "display_date",
                    "model_display",
                    "model_org",
                    "reasoning_effort",
                ],
                "properties": {
                    "agent_display": {
                        "type": "object",
                        "required": ["url", "label"],
                        "properties": {
                            "url": {"type": "string", "format": "uri", "minLength": 1},
                            "label": {"type": "string", "minLength": 1},
                        },
                        "additionalProperties": False,
                    },
                    "agent_org": {
                        "type": "object",
                        "required": ["url", "label"],
                        "properties": {
                            "url": {"type": "string", "format": "uri", "minLength": 1},
                            "label": {"type": "string", "minLength": 1},
                        },
                        "additionalProperties": False,
                    },
                    "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
                    "display_date": {
                        "type": "string",
                        "pattern": "^[A-Za-z]{3} [0-9]{1,2}, [0-9]{4}$",
                        "minLength": 11,
                        "maxLength": 12,
                    },
                    "model_display": {
                        "type": "object",
                        "required": ["url", "label"],
                        "properties": {
                            "url": {"type": "string", "format": "uri", "minLength": 1},
                            "label": {"type": "string", "minLength": 1},
                        },
                        "additionalProperties": False,
                    },
                    "model_org": {
                        "type": "object",
                        "required": ["url", "label"],
                        "properties": {
                            "url": {"type": "string", "format": "uri", "minLength": 1},
                            "label": {"type": "string", "minLength": 1},
                        },
                        "additionalProperties": False,
                    },
                    "reasoning_effort": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "metrics_schema": {
                "type": "object",
                "required": [
                    "accuracy",
                    "accuracy_ci95_half_width",
                    "display_accuracy",
                    "display_cost",
                    "display_total_tokens",
                    "total_tokens",
                    "total_cost_usd",
                    "n_trials",
                ],
                "properties": {
                    "accuracy": {"type": "number", "minimum": 0, "maximum": 100},
                    "accuracy_ci95_half_width": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "display_accuracy": {"type": "string"},
                    "display_cost": {"type": "string"},
                    "display_total_tokens": {"type": "string"},
                    "total_tokens": {"type": "number", "minimum": 0},
                    "total_cost_usd": {"type": "number", "minimum": 0},
                    "n_trials": {"type": "number", "minimum": 0},
                    "successes": {"type": "number", "minimum": 0},
                    "pass_at_2": {"type": "number", "minimum": 0, "maximum": 1},
                    "pass_at_3": {"type": "number", "minimum": 0, "maximum": 1},
                    "pass_at_4": {"type": "number", "minimum": 0, "maximum": 1},
                    "pass_at_5": {"type": "number", "minimum": 0, "maximum": 1},
                    "uncached_input_tokens": {"type": "number", "minimum": 0},
                    "cached_input_tokens": {"type": "number", "minimum": 0},
                    "output_tokens": {"type": "number", "minimum": 0},
                    "avg_trial_duration_sec": {"type": "number", "minimum": 0},
                },
                "additionalProperties": False,
            },
            "columns": [
                {
                    "id": "accuracy",
                    "type": "number",
                    "header": "Accuracy",
                    "accessor": "metrics.accuracy",
                },
                {
                    "id": "date",
                    "type": "date",
                    "align": "right",
                    "header": "Release Date",
                    "accessor": "metadata.date",
                    "display_type": "text",
                    "display_accessor": "metadata.display_date",
                },
            ],
            "rank_by": [{"accessor": "metrics.accuracy", "direction": "desc"}],
            "visibility": "public",
            "created_by": "55555555-5555-4555-8555-555555555555",
            "created_at": "2026-08-01T00:00:00+00:00",
            "updated_at": "2026-08-30T00:00:00+00:00",
        },
        "rows": [
            {
                "id": ROW_ID,
                "leaderboard_id": BOARD_ID,
                "rank": 1,
                "metadata": {
                    "date": "2026-07-01",
                    "display_date": "Jul 1, 2026",
                    "agent_display": {
                        "url": "https://agent.example.com/product",
                        "label": "Benchmark Agent Alpha",
                    },
                    "agent_org": {
                        "url": "https://agent-org.example.com",
                        "label": "Agent Organization",
                    },
                    "model_display": {
                        "url": "https://model.example.com/model-a",
                        "label": "Model A display name",
                    },
                    "model_org": {
                        "url": "https://model-org.example.com",
                        "label": "Model Organization",
                    },
                    "reasoning_effort": "high",
                },
                "metrics": {
                    "accuracy": 50.0,
                    "accuracy_ci95_half_width": 10.0,
                    "display_accuracy": "50.0%",
                    "display_cost": "$101.00",
                    "display_total_tokens": "1,100",
                    "n_trials": 10,
                    "successes": 5,
                    "pass_at_2": 0.7,
                    "total_tokens": 1100,
                    "uncached_input_tokens": 1000,
                    # Deliberately overlaps uncached input, as audited TB 4.0 rows do.
                    "cached_input_tokens": 900,
                    "output_tokens": 100,
                    "total_cost_usd": 101.0,
                    "avg_trial_duration_sec": 12.5,
                },
                "status": "display",
                "created_at": "2026-08-29T00:00:00+00:00",
                "updated_at": "2026-08-30T00:00:00+00:00",
                "n_trials": 10,
            }
        ],
    }


def _source_bytes(value: dict[str, Any] | None = None) -> bytes:
    return json.dumps(value or _leaderboard_value(), separators=(",", ":")).encode()


def _base_config_value() -> dict[str, Any]:
    return {
        "schema_version": "model-skyline/harbor-terminal-bench-import-config/v1alpha1",
        "source_url": ("https://www.harborframework.com/docs/hosted-harbor/cli-leaderboards"),
        "methodology_url": "https://www.tbench.ai/docs",
        "capture_tool": "harbor",
        "capture_tool_version": "0.22.0",
        "publication_scope": "internal",
        "rights": {
            "license_expression": "NOASSERTION",
            "terms_locator": "https://hub.harborframework.com/terms",
            "publication_permission": "unknown",
            "reviewed_at": "2026-08-31T11:00:00Z",
            "review_evidence": (
                "Synthetic fixture is for local tests; upstream redistribution is unasserted."
            ),
            "metadata": {"fixture": True},
        },
        "reconciliation": {
            "schema_version": "model-skyline/quality-reconciliation/v1alpha1",
            "entries": [],
        },
    }


def _config_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def _reviewed_config_value(
    raw: bytes,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = deepcopy(base or _base_config_value())
    inventory = inspect_harbor_terminal_bench_bytes(
        raw,
        _config_bytes(value),
        retrieved_at=RETRIEVED_AT,
    )
    row = inventory["rows"][0]
    value["reconciliation"]["entries"] = [
        {
            "row_id": row["row_id"],
            "adapter_id": "harbor-terminal-bench",
            "projection_version": PARSER_VERSION,
            "expected_source_identity_sha256": inventory["source_identity_sha256"],
            "expected_subject_identity_sha256": row["subject_identity_sha256"],
            "expected_raw_audit_sha256": None,
            "relationship": "reviewed_quality_projection",
            "offering": {
                "offering_id": "provider-a/model-a@production-route",
                "model_id": "model-a-2026-07",
                "provider": "provider-a",
                "endpoint": "https://api.provider-a.example.com/v1",
                "billing_mode": "standard",
                "region": "us",
                "service_tier": None,
                "quantization": None,
                # Production configuration, not Benchmark Agent Alpha.
                "reasoning_effort": None,
                "agent_harness": "production-agent-runtime/v2",
                "capabilities": ["tools"],
            },
            "review_evidence": (
                "Synthetic operator review projects quality from this exact subject."
            ),
            "reviewed_at": "2026-08-31T13:00:00Z",
        }
    ]
    return value


def _reviewed_config_bytes(raw: bytes) -> bytes:
    return _config_bytes(_reviewed_config_value(raw))


def test_normalizes_generic_evidence_and_projects_only_quality() -> None:
    raw = _source_bytes()
    config = _reviewed_config_bytes(raw)
    normalized = normalize_harbor_terminal_bench_bytes(
        raw,
        config,
        retrieved_at=RETRIEVED_AT,
    )
    result = import_harbor_terminal_bench_bytes(
        raw,
        config,
        retrieved_at=RETRIEVED_AT,
    )

    assert normalized.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert normalized.evidence.source_identity.projection.id == "harbor-terminal-bench"
    assert normalized.evidence.source_identity.projection.version == PARSER_VERSION
    assert normalized.evidence.source_identity.scope["release_date_column_sha256"]
    assert normalized.evidence.rows[0].subject.model_claims[0].revision == "2026-07-01"
    evidence_result = normalized.evidence.rows[0].result
    assert evidence_result is not None
    roles = {item.id: item.role.value for item in evidence_result.measurements}
    assert roles["terminal_bench_accuracy"] == "quality"
    assert roles["harbor_reported_total_cost_usd"] == "cost"
    assert roles["harbor_reported_cached_input_tokens"] == "token_usage"
    assert roles["harbor_avg_trial_duration_seconds"] == "latency"

    assert len(result.catalog.offerings) == 1
    offering = result.catalog.offerings[0]
    assert offering.offering.agent_harness == "production-agent-runtime/v2"
    assert set(offering.signals) == {"terminal_bench_accuracy", "pass_at_2"}
    assert offering.signals["terminal_bench_accuracy"].value == Decimal("0.5")
    assert offering.signals["terminal_bench_accuracy"].lower == Decimal("0.4")
    assert offering.signals["terminal_bench_accuracy"].upper == Decimal("0.6")
    assert offering.signals["terminal_bench_accuracy"].sample_count == 10
    assert offering.signals["pass_at_2"].sample_count is None
    assert result.config.metrics["pass_at_2"].requirements.minimum_samples is None
    assert "harbor_reported_cost_per_trial_usd" not in offering.signals
    assert offering.metadata["quality_only_projection"] is True
    assert offering.metadata["route_specific_telemetry_projected"] is False
    assert result.config.frontiers == {}
    assert result.report.records[0].outcome.value == "mapped"
    assert result.report.mapped_rows[0].relationship.value == "reviewed_quality_projection"


def test_result_only_change_reuses_reconciliation_and_updates_quality() -> None:
    original_raw = _source_bytes()
    config = _reviewed_config_bytes(original_raw)
    original = import_harbor_terminal_bench_bytes(
        original_raw,
        config,
        retrieved_at=RETRIEVED_AT,
    )

    changed_value = _leaderboard_value()
    changed_row = changed_value["rows"][0]
    changed_row["metrics"]["accuracy"] = 60.0
    changed_row["metrics"]["successes"] = 6
    changed_row["metrics"]["total_cost_usd"] = 120.0
    changed_row["metrics"]["cached_input_tokens"] = 5000
    changed_row["updated_at"] = "2026-08-30T01:00:00+00:00"
    changed_value["leaderboard"]["columns"][0]["header"] = "Score"
    changed_raw = _source_bytes(changed_value)
    changed = import_harbor_terminal_bench_bytes(
        changed_raw,
        config,
        retrieved_at=RETRIEVED_AT,
    )

    assert changed.raw_sha256 != original.raw_sha256
    assert changed.source_identity_sha256 == original.source_identity_sha256
    assert changed.subject_identity_sha256 == original.subject_identity_sha256
    assert changed.result_sha256 != original.result_sha256
    assert changed.report.records[0].outcome.value == "mapped"
    assert changed.catalog.offerings[0].signals["terminal_bench_accuracy"].value == Decimal("0.6")
    assert "harbor_reported_cost_per_trial_usd" not in changed.catalog.offerings[0].signals


def test_subject_and_source_drift_are_typed_not_silently_remapped() -> None:
    raw = _source_bytes()
    config = _reviewed_config_bytes(raw)

    changed_subject = _leaderboard_value()
    changed_subject["rows"][0]["metadata"]["date"] = "2026-07-02"
    changed_subject["rows"][0]["metadata"]["display_date"] = "Jul 2, 2026"
    subject_result = import_harbor_terminal_bench_bytes(
        _source_bytes(changed_subject),
        config,
        retrieved_at=RETRIEVED_AT,
        allow_partial=True,
    )
    assert subject_result.catalog.offerings == []
    assert subject_result.report.records[0].outcome.value == "identity_drift"

    changed_source = _leaderboard_value()
    changed_source["leaderboard"]["metrics_schema"]["properties"]["accuracy"]["maximum"] = 99
    source_result = import_harbor_terminal_bench_bytes(
        _source_bytes(changed_source),
        config,
        retrieved_at=RETRIEVED_AT,
        allow_partial=True,
    )
    assert source_result.catalog.offerings == []
    assert source_result.report.records[0].outcome.value == "identity_drift"


def test_reviewed_row_failure_is_fail_closed_unless_partial_is_explicit() -> None:
    raw = _source_bytes()
    config = _reviewed_config_bytes(raw)
    changed_subject = _leaderboard_value()
    changed_subject["rows"][0]["metadata"]["model_display"]["label"] = "Renamed model"
    changed_raw = _source_bytes(changed_subject)

    with pytest.raises(HarborAdapterError, match="reviewed Harbor row.*did not map"):
        import_harbor_terminal_bench_bytes(
            changed_raw,
            config,
            retrieved_at=RETRIEVED_AT,
        )

    partial = import_harbor_terminal_bench_bytes(
        changed_raw,
        config,
        retrieved_at=RETRIEVED_AT,
        allow_partial=True,
    )
    assert partial.allow_partial is True
    assert partial.catalog.offerings == []
    assert partial.report.records[0].outcome.value == "identity_drift"

    with pytest.raises(HarborAdapterError, match="allow_partial must be a boolean"):
        import_harbor_terminal_bench_bytes(
            raw,
            config,
            retrieved_at=RETRIEVED_AT,
            allow_partial=1,  # type: ignore[arg-type]
        )


def test_import_rejects_an_unreviewed_bootstrap_config_by_default() -> None:
    raw = _source_bytes()
    bootstrap = _config_bytes(_base_config_value())

    with pytest.raises(HarborAdapterError, match="at least one reviewed reconciliation"):
        import_harbor_terminal_bench_bytes(
            raw,
            bootstrap,
            retrieved_at=RETRIEVED_AT,
        )

    partial = import_harbor_terminal_bench_bytes(
        raw,
        bootstrap,
        retrieved_at=RETRIEVED_AT,
        allow_partial=True,
    )
    assert partial.catalog.offerings == []
    assert partial.report.records[0].outcome.value == "unknown_route"


def test_hidden_result_is_invalid_evidence_and_never_a_score() -> None:
    raw = _source_bytes()
    config = _reviewed_config_bytes(raw)
    hidden_value = _leaderboard_value()
    hidden_value["rows"][0]["status"] = "hide"

    hidden = import_harbor_terminal_bench_bytes(
        _source_bytes(hidden_value),
        config,
        retrieved_at=RETRIEVED_AT,
        allow_partial=True,
    )

    assert hidden.catalog.offerings == []
    assert hidden.report.records[0].outcome.value == "invalid_result"
    assert hidden.evidence.rows[0].result is None
    assert hidden.evidence.rows[0].invalid_result is not None


def test_config_requires_complete_offering_and_sanitizes_errors() -> None:
    raw = _source_bytes()
    incomplete = _reviewed_config_value(raw)
    del incomplete["reconciliation"]["entries"][0]["offering"]["region"]
    with pytest.raises(HarborAdapterError, match="invalid Harbor import configuration"):
        import_harbor_terminal_bench_bytes(
            raw,
            _config_bytes(incomplete),
            retrieved_at=RETRIEVED_AT,
        )

    unsafe = _reviewed_config_value(raw)
    unsafe["source_url"] = "https://user:do-not-log@example.test/capture"
    with pytest.raises(HarborAdapterError) as caught:
        import_harbor_terminal_bench_bytes(
            raw,
            _config_bytes(unsafe),
            retrieved_at=RETRIEVED_AT,
        )
    assert "do-not-log" not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("source_url", "https://127.0.0.1/do-not-log"),
        ("source_url", "https://localhost/do-not-log"),
        ("source_url", "https://münich.example/do-not-log"),
        ("source_url", "https://public.example:99999/do-not-log"),
        (
            "endpoint",
            "https://user:do-not-log@api.provider-a.example.com/v1",
        ),
        (
            "endpoint",
            "https://api.provider-a.example.com/v1?token=do-not-log",
        ),
    ],
)
def test_config_rejects_unsafe_urls_without_echoing_values(
    field: str,
    unsafe_value: str,
) -> None:
    raw = _source_bytes()
    config = _reviewed_config_value(raw)
    if field == "endpoint":
        config["reconciliation"]["entries"][0]["offering"][field] = unsafe_value
    else:
        config[field] = unsafe_value

    with pytest.raises(HarborAdapterError) as caught:
        import_harbor_terminal_bench_bytes(
            raw,
            _config_bytes(config),
            retrieved_at=RETRIEVED_AT,
        )
    assert "do-not-log" not in str(caught.value)


def test_config_is_frozen_and_typed_instances_are_revalidated() -> None:
    raw = _source_bytes()
    loaded = load_harbor_terminal_bench_mapping(_reviewed_config_bytes(raw))[0]

    with pytest.raises(PydanticValidationError):
        loaded.source_url = "https://public.example/changed"  # type: ignore[misc]

    forged = loaded.model_copy(
        update={"source_url": "https://user:do-not-log@public.example/capture"}
    )
    with pytest.raises(HarborAdapterError) as caught:
        normalize_harbor_terminal_bench_bytes(
            raw,
            forged,
            retrieved_at=RETRIEVED_AT,
        )
    assert "do-not-log" not in str(caught.value)


def test_generated_import_config_schema_exposes_security_invariants() -> None:
    raw = _source_bytes()
    value = _reviewed_config_value(raw)
    schema = generated_schemas()["harbor-terminal-bench-import-config.schema.json"]
    validator = Draft202012Validator(schema)

    validator.validate(value)
    unsafe_source = deepcopy(value)
    unsafe_source["source_url"] = "http://example.test/source"
    malformed_source = deepcopy(value)
    malformed_source["source_url"] = "https:///not-a-host"
    unicode_source = deepcopy(value)
    unicode_source["source_url"] = "https://münich.example/source"
    missing_terms = deepcopy(value)
    missing_terms["rights"]["terms_locator"] = None
    no_tools = deepcopy(value)
    no_tools["reconciliation"]["entries"][0]["offering"]["capabilities"] = []
    no_production_harness = deepcopy(value)
    no_production_harness["reconciliation"]["entries"][0]["offering"]["agent_harness"] = None
    duplicate_tools = deepcopy(value)
    duplicate_tools["reconciliation"]["entries"][0]["offering"]["capabilities"] = [
        "tools",
        "tools",
    ]
    embedded_raw = deepcopy(value)
    embedded_raw["raw_leaderboard_payload"] = {}
    for invalid in (
        unsafe_source,
        malformed_source,
        unicode_source,
        missing_terms,
        no_tools,
        no_production_harness,
        duplicate_tools,
        embedded_raw,
    ):
        with pytest.raises(JsonSchemaValidationError):
            validator.validate(invalid)
    assert "semantic validation" in schema["$comment"]


def test_rejects_unsafe_claim_urls_and_unknown_statuses() -> None:
    config = _config_bytes(_base_config_value())
    unsafe_row = _leaderboard_value()
    unsafe_row["rows"][0]["metadata"]["agent_display"]["url"] = "http://agent.example.test/product"
    with pytest.raises(HarborAdapterError, match="safe public URL"):
        normalize_harbor_terminal_bench_bytes(
            _source_bytes(unsafe_row),
            config,
            retrieved_at=RETRIEVED_AT,
        )

    pending = _leaderboard_value()
    pending["rows"][0]["status"] = "pending"
    with pytest.raises(HarborAdapterError, match="complete/quarantined status"):
        normalize_harbor_terminal_bench_bytes(
            _source_bytes(pending),
            config,
            retrieved_at=RETRIEVED_AT,
        )

    impossible_row_time = _leaderboard_value()
    impossible_row_time["rows"][0]["created_at"] = "2099-01-01T00:00:00+00:00"
    with pytest.raises(HarborAdapterError, match="created_at is later than updated_at"):
        normalize_harbor_terminal_bench_bytes(
            _source_bytes(impossible_row_time),
            config,
            retrieved_at=RETRIEVED_AT,
        )

    impossible_board_time = _leaderboard_value()
    impossible_board_time["leaderboard"]["created_at"] = "2099-01-01T00:00:00+00:00"
    with pytest.raises(HarborAdapterError, match="created_at is later than updated_at"):
        normalize_harbor_terminal_bench_bytes(
            _source_bytes(impossible_board_time),
            config,
            retrieved_at=RETRIEVED_AT,
        )


def test_rejects_duplicate_json_oversize_and_incoherent_metrics() -> None:
    raw = _source_bytes()
    config = _reviewed_config_bytes(raw)
    with pytest.raises(HarborAdapterError, match="duplicate JSON object key"):
        import_harbor_terminal_bench_bytes(
            b'{"leaderboard":{},"leaderboard":{},"rows":[]}',
            config,
            retrieved_at=RETRIEVED_AT,
        )

    secret_key = "do-not-log"
    duplicate_secret = (
        '{"leaderboard":{},"rows":[],"' + secret_key + '":1,"' + secret_key + '":2}'
    ).encode()
    with pytest.raises(HarborAdapterError) as caught:
        import_harbor_terminal_bench_bytes(
            duplicate_secret,
            config,
            retrieved_at=RETRIEVED_AT,
        )
    assert secret_key not in str(caught.value)
    assert "sha256=" in str(caught.value)

    extreme_exponent = raw.replace(b'"minimum":0', b'"minimum":0e-1000000', 1)
    with pytest.raises(HarborAdapterError, match="canonical bounds"):
        import_harbor_terminal_bench_bytes(
            extreme_exponent,
            config,
            retrieved_at=RETRIEVED_AT,
        )
    with pytest.raises(HarborAdapterError, match="byte limit"):
        import_harbor_terminal_bench_bytes(
            raw,
            config,
            retrieved_at=RETRIEVED_AT,
            max_bytes=len(raw) - 1,
        )

    incoherent = _leaderboard_value()
    incoherent["rows"][0]["metrics"]["accuracy"] = 99
    with pytest.raises(HarborAdapterError, match="incoherent with successes/n_trials"):
        import_harbor_terminal_bench_bytes(
            _source_bytes(incoherent),
            config,
            retrieved_at=RETRIEVED_AT,
        )


def test_schema_and_row_display_fields_are_bounded_and_type_checked() -> None:
    config = _config_bytes(_base_config_value())
    unsafe_schema = _leaderboard_value()
    unsafe_schema["leaderboard"]["metadata_schema"]["properties"]["display_date"]["pattern"] = (
        "safe\u202eunsafe"
    )
    with pytest.raises(HarborAdapterError, match="pattern must be bounded text"):
        normalize_harbor_terminal_bench_bytes(
            _source_bytes(unsafe_schema),
            config,
            retrieved_at=RETRIEVED_AT,
        )

    invalid_display = _leaderboard_value()
    invalid_display["rows"][0]["metrics"]["display_accuracy"] = {"value": "50%"}
    with pytest.raises(HarborAdapterError, match="must be safe JSON text"):
        normalize_harbor_terminal_bench_bytes(
            _source_bytes(invalid_display),
            config,
            retrieved_at=RETRIEVED_AT,
        )

    secret_key = "api-key-SENSITIVE-DO-NOT-ECHO"
    secret_field = _leaderboard_value()
    secret_field["leaderboard"]["metadata_schema"]["properties"][secret_key] = {"type": "string"}
    secret_field["rows"][0]["metadata"][secret_key] = {"not": "text"}
    with pytest.raises(HarborAdapterError) as caught:
        normalize_harbor_terminal_bench_bytes(
            _source_bytes(secret_field),
            config,
            retrieved_at=RETRIEVED_AT,
        )
    assert secret_key not in str(caught.value)


def test_typed_hashing_and_decimal_context_are_deterministic() -> None:
    assert harbor_value_sha256(Decimal("0")) != harbor_value_sha256("0")
    raw = _source_bytes()
    config = _reviewed_config_bytes(raw)
    with localcontext() as context:
        context.prec = 6
        low_precision = import_harbor_terminal_bench_bytes(
            raw,
            config,
            retrieved_at=RETRIEVED_AT,
        )
    with localcontext() as context:
        context.prec = 50
        high_precision = import_harbor_terminal_bench_bytes(
            raw,
            config,
            retrieved_at=RETRIEVED_AT,
        )

    assert low_precision.result_sha256 == high_precision.result_sha256
    assert low_precision.catalog == high_precision.catalog


def test_local_import_writes_six_file_private_audit_bundle(tmp_path: Path) -> None:
    raw = _source_bytes()
    config = _reviewed_config_bytes(raw)
    snapshot = tmp_path / "capture.json"
    config_path = tmp_path / "reviewed-config.json"
    snapshot.write_bytes(raw)
    config_path.write_bytes(config)

    result = import_harbor_terminal_bench(
        snapshot,
        config_path,
        retrieved_at=RETRIEVED_AT,
    )
    output = tmp_path / "project"
    targets = write_harbor_terminal_bench_import(result, output)

    assert {target.name for target in targets} == {
        "observations.json",
        "frontier.yaml",
        "mapping.json",
        "evidence.json",
        "import-report.json",
        "import.json",
    }
    catalog = load_catalog(output / "observations.json")
    project = load_config(output / "frontier.yaml")
    evidence = load_quality_evidence(output / "evidence.json")
    report = load_quality_import_report(output / "import-report.json")
    manifest = json.loads((output / "import.json").read_text(encoding="utf-8"))
    assert project.workloads[catalog.workload.id].harness.startswith(
        "terminal-bench-harbor-submission/"
    )
    assert set(catalog.offerings[0].signals) == {
        "terminal_bench_accuracy",
        "pass_at_2",
    }
    assert evidence.raw_audit.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert report.records[0].outcome.value == "mapped"
    assert manifest["output_sha256"] == {
        filename: hashlib.sha256((output / filename).read_bytes()).hexdigest()
        for filename in (
            "evidence.json",
            "frontier.yaml",
            "import-report.json",
            "mapping.json",
            "observations.json",
        )
    }
    if os.name == "posix":
        assert stat.S_IMODE(output.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(target.stat().st_mode) == 0o600 for target in targets)


@pytest.mark.skipif(os.name != "posix", reason="mkfifo is POSIX-only")
def test_snapshot_fifo_is_rejected_without_waiting_for_a_writer(tmp_path: Path) -> None:
    fifo = tmp_path / "capture.fifo"
    os.mkfifo(fifo)
    config_path = tmp_path / "config.json"
    config_path.write_bytes(_config_bytes(_base_config_value()))

    with pytest.raises(HarborAdapterError, match="not a regular file"):
        import_harbor_terminal_bench(
            fifo,
            config_path,
            retrieved_at=RETRIEVED_AT,
        )


def test_cli_inspects_and_imports_the_reviewed_bundle(tmp_path: Path) -> None:
    raw = _source_bytes()
    snapshot = tmp_path / "capture.json"
    bootstrap = tmp_path / "bootstrap.json"
    inventory_path = tmp_path / "inventory.json"
    reviewed = tmp_path / "reviewed.json"
    output = tmp_path / "project"
    snapshot.write_bytes(raw)
    bootstrap.write_bytes(_config_bytes(_base_config_value()))
    reviewed.write_bytes(_reviewed_config_bytes(raw))

    inspected = RUNNER.invoke(
        app,
        [
            "inspect-harbor-terminal-bench",
            str(snapshot),
            str(bootstrap),
            "--retrieved-at",
            RETRIEVED_AT.isoformat(),
            "--output",
            str(inventory_path),
        ],
    )
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inventory_path.read_text(encoding="utf-8"))["rows"][0]["row_id"] == ROW_ID
    if os.name == "posix":
        assert stat.S_IMODE(inventory_path.stat().st_mode) == 0o600

    original_inventory = inventory_path.read_bytes()
    refused = RUNNER.invoke(
        app,
        [
            "inspect-harbor-terminal-bench",
            str(snapshot),
            str(bootstrap),
            "--retrieved-at",
            RETRIEVED_AT.isoformat(),
            "--output",
            str(inventory_path),
        ],
    )
    assert refused.exit_code == 2
    assert "refusing to overwrite" in refused.output
    assert inventory_path.read_bytes() == original_inventory

    overwritten = RUNNER.invoke(
        app,
        [
            "inspect-harbor-terminal-bench",
            str(snapshot),
            str(bootstrap),
            "--retrieved-at",
            RETRIEVED_AT.isoformat(),
            "--output",
            str(inventory_path),
            "--overwrite",
        ],
    )
    assert overwritten.exit_code == 0, overwritten.output
    if os.name == "posix":
        assert stat.S_IMODE(inventory_path.stat().st_mode) == 0o600

    imported = RUNNER.invoke(
        app,
        [
            "import-harbor-terminal-bench",
            str(output),
            str(snapshot),
            str(reviewed),
            "--retrieved-at",
            RETRIEVED_AT.isoformat(),
        ],
    )
    assert imported.exit_code == 0, imported.output
    assert "1 mapped Terminal-Bench rows" in imported.output
    assert (output / "import-report.json").is_file()


def test_cli_requires_explicit_allow_partial_for_failed_review(tmp_path: Path) -> None:
    original_raw = _source_bytes()
    changed = _leaderboard_value()
    changed["rows"][0]["metadata"]["model_display"]["label"] = "Renamed model"
    snapshot = tmp_path / "capture.json"
    reviewed = tmp_path / "reviewed.json"
    output = tmp_path / "project"
    snapshot.write_bytes(_source_bytes(changed))
    reviewed.write_bytes(_reviewed_config_bytes(original_raw))
    arguments = [
        "import-harbor-terminal-bench",
        str(output),
        str(snapshot),
        str(reviewed),
        "--retrieved-at",
        RETRIEVED_AT.isoformat(),
    ]

    refused = RUNNER.invoke(app, arguments)
    assert refused.exit_code == 2
    assert "did not map" in refused.output
    assert not output.exists()

    allowed = RUNNER.invoke(app, [*arguments, "--allow-partial"])
    assert allowed.exit_code == 0, allowed.output
    assert "0 mapped Terminal-Bench rows" in allowed.output
    assert (
        load_quality_import_report(output / "import-report.json").records[0].outcome.value
        == "identity_drift"
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink safety")
def test_cli_inspection_output_does_not_follow_symlink(tmp_path: Path) -> None:
    snapshot = tmp_path / "capture.json"
    config = tmp_path / "bootstrap.json"
    victim = tmp_path / "victim.json"
    output = tmp_path / "inventory.json"
    snapshot.write_bytes(_source_bytes())
    config.write_bytes(_config_bytes(_base_config_value()))
    victim.write_text("unchanged\n", encoding="utf-8")
    output.symlink_to(victim)

    result = RUNNER.invoke(
        app,
        [
            "inspect-harbor-terminal-bench",
            str(snapshot),
            str(config),
            "--retrieved-at",
            RETRIEVED_AT.isoformat(),
            "--output",
            str(output),
            "--overwrite",
        ],
    )

    assert result.exit_code == 2
    assert "symbolic link" in result.output
    assert victim.read_text(encoding="utf-8") == "unchanged\n"
    assert output.is_symlink()


def test_catalog_order_is_independent_of_reconciliation_order() -> None:
    value = _leaderboard_value()
    second = deepcopy(value["rows"][0])
    second["id"] = "66666666-6666-4666-8666-666666666666"
    second["metadata"]["model_display"] = {
        "url": "https://model.example.com/model-b",
        "label": "Model B display name",
    }
    second["metrics"]["accuracy"] = 40.0
    second["metrics"]["successes"] = 4
    value["rows"].append(second)
    raw = _source_bytes(value)

    base = _base_config_value()
    inventory = inspect_harbor_terminal_bench_bytes(
        raw,
        _config_bytes(base),
        retrieved_at=RETRIEVED_AT,
    )
    config = _reviewed_config_value(raw, base=base)
    first_entry = config["reconciliation"]["entries"][0]
    second_inventory = next(row for row in inventory["rows"] if row["row_id"] == second["id"])
    second_entry = deepcopy(first_entry)
    second_entry["row_id"] = second_inventory["row_id"]
    second_entry["expected_subject_identity_sha256"] = second_inventory["subject_identity_sha256"]
    second_entry["offering"]["offering_id"] = "provider-a/model-b@production-route"
    second_entry["offering"]["model_id"] = "model-b-2026-07"
    config["reconciliation"]["entries"] = [second_entry, first_entry]

    result = import_harbor_terminal_bench_bytes(
        raw,
        _config_bytes(config),
        retrieved_at=RETRIEVED_AT,
    )
    assert [item.offering.offering_id for item in result.catalog.offerings] == [
        "provider-a/model-a@production-route",
        "provider-a/model-b@production-route",
    ]
