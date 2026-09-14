from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from model_skyline.adapters import claude_code as claude_code_module
from model_skyline.adapters.claude_code import (
    CLAUDE_CODE_ADAPTER_VERSION,
    CLAUDE_CODE_HEADLESS_URL,
    CLAUDE_CODE_RELEASE_URLS,
    CLAUDE_CODE_REVIEWED_RELEASES,
    ClaudeCodeAdapterError,
    ClaudeCodeRouteMapping,
    adapt_claude_code_result_json,
)
from model_skyline.models import OfferingKey, WorkloadReference
from model_skyline.traces import RequestTrace, aggregate_traces

FIXTURE = Path(__file__).parent / "fixtures" / "claude_code_result_single_model_synthetic.json"
TIMESTAMP = datetime(2026, 9, 1, 1, 30, tzinfo=UTC)
PRIVATE_SENTINELS = (
    "SYNTHETIC_CLAUDE_CODE_SESSION_MUST_NOT_PERSIST",
    "SYNTHETIC_CLAUDE_CODE_UUID_MUST_NOT_PERSIST",
    "SYNTHETIC_CLAUDE_CODE_RESULT_MUST_NOT_PERSIST",
    "SYNTHETIC_CLAUDE_CODE_STRUCTURED_OUTPUT_MUST_NOT_PERSIST",
    "/synthetic/private/claude-code-path-must-not-persist",
)


def _payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def _route(**overrides: Any) -> ClaudeCodeRouteMapping:
    arguments: dict[str, Any] = {
        "offering": OfferingKey(
            offering_id="anthropic/claude-sonnet-4-6@claude-code-first-party",
            model_id="claude-sonnet-4-6",
            provider="anthropic",
            agent_harness="claude-code",
        ),
        "model_usage_key": "claude-sonnet-4-6",
        "upstream_provider": "firstParty",
        "expected_usage_service_tier": "standard",
        "expected_usage_inference_geo": "",
        "expected_usage_speed": "standard",
        "single_route_and_pricing_basis_attested": True,
        "route_details_attested": False,
    }
    arguments.update(overrides)
    return ClaudeCodeRouteMapping.model_validate(arguments)


def _adapt(path: Path, **overrides: Any) -> RequestTrace:
    arguments: dict[str, Any] = {
        "claude_code_version": "2.1.220",
        "final_cumulative_result": True,
        "accounting_scope": "single_print_invocation",
        "timestamp": TIMESTAMP,
        "workload_id": "coding-agent",
        "workload_version": "v1",
        "work_unit_id": "issue-220",
        "route": _route(),
        "result_id": "claude-code-result-220",
        "attempt_id": "attempt-1",
        "work_unit_success": Decimal(1),
    }
    arguments.update(overrides)
    return adapt_claude_code_result_json(path, **arguments)


def test_maps_private_cli_result_without_retaining_content(tmp_path: Path) -> None:
    path = tmp_path / "claude-result.json"
    _write(path, _payload())

    trace = _adapt(path)

    assert trace.schema_version == "model-skyline/request-trace/v1alpha2"
    assert trace.observation_unit == "attempt"
    assert trace.model_request_count is None
    assert trace.offering_id == "anthropic/claude-sonnet-4-6@claude-code-first-party"
    assert trace.input_uncached_tokens == Decimal(7)
    assert trace.input_cache_read_tokens == Decimal(1200)
    assert trace.input_cache_write_tokens == Decimal(400)
    assert trace.output_total_tokens == Decimal(89)
    assert trace.web_search_calls == Decimal(2)
    assert trace.estimated_total_cost_usd == Decimal("0.123456789012")
    assert trace.input_total_tokens is None
    assert trace.output_tokens is None
    assert trace.reasoning_tokens is None
    assert trace.ttft_ms is None
    assert trace.output_tokens_per_second is None
    serialized = trace.model_dump_json()
    for sentinel in PRIVATE_SENTINELS:
        assert sentinel not in serialized


def test_cli_aggregate_uses_the_trusted_non_sdk_producer(tmp_path: Path) -> None:
    private = tmp_path / "private.json"
    _write(private, _payload())
    canonical = tmp_path / "canonical.jsonl"
    canonical.write_text(_adapt(private).model_dump_json() + "\n", encoding="utf-8")

    summary = aggregate_traces(
        canonical,
        workload=WorkloadReference(id="coding-agent", version="v1", unit="issue"),
        retrieved_at=TIMESTAMP,
    )

    signals = summary.offerings["anthropic/claude-sonnet-4-6@claude-code-first-party"]
    assert signals["estimated_total_cost_usd_per_work_unit"].value == Decimal("0.123456789012")
    assert signals["input_cache_read_tokens_per_work_unit"].value == Decimal(1200)
    assert "request_count_per_work_unit" not in signals
    assert len(summary.producer_sources) == 1
    producer = summary.producer_sources[0]
    assert producer.id == "producer:anthropic-claude-code:2.1.220"
    assert producer.license == "LicenseRef-Anthropic-Commercial-Terms"
    assert "releases/tag/v2.1.220" in str(producer.url)


def test_reviewed_cli_identity_and_public_references_are_pinned() -> None:
    assert CLAUDE_CODE_REVIEWED_RELEASES == {"2.1.220": "4073f59596e272f39393db4f96abc5f4b10eff21"}
    assert CLAUDE_CODE_ADAPTER_VERSION == "1"
    assert "v2.1.220" in CLAUDE_CODE_RELEASE_URLS["2.1.220"]
    assert CLAUDE_CODE_HEADLESS_URL == "https://code.claude.com/docs/en/headless"


def test_rejects_unreviewed_version_scope_and_nonfinal_result(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    _write(path, _payload())

    with pytest.raises(ClaudeCodeAdapterError, match="unsupported Claude Code version"):
        _adapt(path, claude_code_version="2.1.221")
    with pytest.raises(ClaudeCodeAdapterError, match="terminal invocation"):
        _adapt(path, final_cumulative_result=False)
    with pytest.raises(ClaudeCodeAdapterError, match="one print invocation"):
        _adapt(path, accounting_scope="resumed_session")


def test_route_requires_exact_harness_and_narrow_field_attestations() -> None:
    wrong_harness = OfferingKey(
        offering_id="anthropic/claude-sonnet-4-6@sdk",
        model_id="claude-sonnet-4-6",
        provider="anthropic",
        agent_harness="claude-agent-sdk",
    )
    with pytest.raises(ValidationError, match="Claude Code offering harness"):
        _route(offering=wrong_harness)
    with pytest.raises(ValidationError, match="whole invocation"):
        _route(single_route_and_pricing_basis_attested=False)

    narrow = OfferingKey(
        offering_id="anthropic/claude-sonnet-4-6@priority",
        model_id="claude-sonnet-4-6",
        provider="anthropic",
        service_tier="priority",
        agent_harness="claude-code",
    )
    with pytest.raises(ValidationError, match="route_details_attested"):
        _route(offering=narrow)
    assert _route(offering=narrow, route_details_attested=True).offering == narrow


def test_optional_output_identity_is_bound_by_the_attested_route(tmp_path: Path) -> None:
    payload = _payload()
    entry = payload["modelUsage"]["claude-sonnet-4-6"]
    entry.pop("canonicalModel")
    entry.pop("provider")
    path = tmp_path / "identity-omitted.json"
    _write(path, payload)

    trace = _adapt(path)
    assert trace.offering_id == _route().offering.offering_id


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("service_tier", "priority", "service_tier does not match"),
        ("inference_geo", "us-east", "inference_geo does not match"),
        ("speed", "fast", "speed does not match"),
    ],
)
def test_usage_route_dimensions_must_match_the_explicit_mapping(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    payload = _payload()
    payload["usage"][field] = value
    path = tmp_path / "usage-route-drift.json"
    _write(path, payload)

    with pytest.raises(ClaudeCodeAdapterError, match=message):
        _adapt(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_top_level_usage_field",
        "missing_speed",
        "extra_server_tool_field",
        "nonempty_iterations",
        "retention_specific_cache_value",
        "nonzero_web_fetch",
    ],
)
def test_complete_reviewed_usage_shape_fails_closed_on_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = _payload()
    usage = payload["usage"]
    if mutation == "extra_top_level_usage_field":
        usage["future_field"] = PRIVATE_SENTINELS[2]
    elif mutation == "missing_speed":
        usage.pop("speed")
    elif mutation == "extra_server_tool_field":
        usage["server_tool_use"]["future_tool"] = 1
    elif mutation == "nonempty_iterations":
        usage["iterations"] = [{"private": PRIVATE_SENTINELS[2]}]
    elif mutation == "retention_specific_cache_value":
        usage["cache_creation"]["ephemeral_1h_input_tokens"] = 1
    else:
        usage["server_tool_use"]["web_fetch_requests"] = 1
    path = tmp_path / "usage-schema-drift.json"
    _write(path, payload)

    with pytest.raises(ClaudeCodeAdapterError) as captured:
        _adapt(path)
    assert PRIVATE_SENTINELS[2] not in str(captured.value)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("canonicalModel", "claude-opus-4-6", "canonicalModel does not match"),
        ("provider", "bedrock", "provider does not match"),
    ],
)
def test_rejects_emitted_model_or_provider_drift(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    payload = _payload()
    payload["modelUsage"]["claude-sonnet-4-6"][field] = value
    path = tmp_path / "identity-drift.json"
    _write(path, payload)

    with pytest.raises(ClaudeCodeAdapterError, match=message):
        _adapt(path)


def test_rejects_multiple_models_cost_disagreement_and_counter_disagreement(
    tmp_path: Path,
) -> None:
    multiple = _payload()
    multiple["modelUsage"]["claude-opus-4-6"] = deepcopy(
        multiple["modelUsage"]["claude-sonnet-4-6"]
    )
    path = tmp_path / "multiple.json"
    _write(path, multiple)
    with pytest.raises(ClaudeCodeAdapterError, match="multiple models"):
        _adapt(path)

    cost = _payload()
    cost["modelUsage"]["claude-sonnet-4-6"]["costUSD"] = 0.12
    _write(path, cost)
    with pytest.raises(ClaudeCodeAdapterError, match="does not match total_cost_usd"):
        _adapt(path)

    counters = _payload()
    counters["usage"]["cache_read_input_tokens"] = 1199
    _write(path, counters)
    with pytest.raises(ClaudeCodeAdapterError, match="aggregate usage does not match"):
        _adapt(path)


@pytest.mark.parametrize(
    ("container", "field", "value", "message"),
    [
        ("model", "inputTokens", True, "must be an integer"),
        ("model", "outputTokens", -1, "safe integer"),
        ("model", "contextWindow", 0, "must be positive"),
        ("model", "costUSD", "0.1", "must be a JSON number"),
        ("usage", "input_tokens", 1.5, "must be an integer"),
        ("result", "total_cost_usd", "0", "must be a JSON number"),
    ],
)
def test_rejects_malformed_accounting_without_echoing_payload(
    tmp_path: Path,
    container: str,
    field: str,
    value: Any,
    message: str,
) -> None:
    payload = _payload()
    if container == "model":
        payload["modelUsage"]["claude-sonnet-4-6"][field] = value
    elif container == "usage":
        payload["usage"][field] = value
    else:
        payload[field] = value
    payload["result"] = PRIVATE_SENTINELS[2]
    path = tmp_path / "bad-accounting.json"
    _write(path, payload)

    with pytest.raises(ClaudeCodeAdapterError, match=message) as captured:
        _adapt(path)
    assert PRIVATE_SENTINELS[2] not in str(captured.value)


def test_real_exercised_api_failure_shape_retains_unknown_not_false_zero(
    tmp_path: Path,
) -> None:
    # Content-free shape observed from installed 2.1.220. Result text and both
    # upstream identifiers are replaced with synthetic fixture values.
    payload = _payload()
    payload["is_error"] = True
    payload["api_error_status"] = 500
    payload["terminal_reason"] = "api_error"
    payload["modelUsage"] = {}
    payload["total_cost_usd"] = 0
    for field in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        payload["usage"][field] = 0
    payload["usage"]["server_tool_use"]["web_search_requests"] = 0
    path = tmp_path / "api-failure.json"
    _write(path, payload)

    trace = _adapt(path, work_unit_success=Decimal(0))
    assert trace.input_uncached_tokens is None
    assert trace.input_cache_read_tokens is None
    assert trace.input_cache_write_tokens is None
    assert trace.output_total_tokens is None
    assert trace.estimated_total_cost_usd is None


def test_success_subtype_api_error_with_nonempty_accounting_is_retained(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["is_error"] = True
    payload["api_error_status"] = 500
    payload["terminal_reason"] = "api_error"
    path = tmp_path / "metered-api-error.json"
    _write(path, payload)

    trace = _adapt(path, work_unit_success=Decimal(0))
    assert trace.input_uncached_tokens == Decimal(7)
    assert trace.estimated_total_cost_usd == Decimal("0.123456789012")


def test_execution_crash_validates_surviving_identity_but_discards_zeroes(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["subtype"] = "error_during_execution"
    payload["is_error"] = True
    payload["errors"] = [PRIVATE_SENTINELS[2]]
    payload.pop("result")
    payload.pop("structured_output")
    payload["modelUsage"]["claude-sonnet-4-6"]["provider"] = "bedrock"
    path = tmp_path / "crash.json"
    _write(path, payload)

    with pytest.raises(ClaudeCodeAdapterError, match="provider does not match"):
        _adapt(path, work_unit_success=Decimal(0))

    payload["modelUsage"] = {}
    payload["total_cost_usd"] = 0
    for field in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        payload["usage"][field] = 0
    payload["usage"]["server_tool_use"]["web_search_requests"] = 0
    _write(path, payload)
    trace = _adapt(path, work_unit_success=Decimal(0))
    assert trace.input_uncached_tokens is None
    assert trace.estimated_total_cost_usd is None
    assert PRIVATE_SENTINELS[2] not in trace.model_dump_json()


def test_schema_drift_duplicate_keys_and_excessive_depth_fail_closed(tmp_path: Path) -> None:
    drift = _payload()
    drift["future_private_field"] = PRIVATE_SENTINELS[2]
    path = tmp_path / "drift.json"
    _write(path, drift)
    with pytest.raises(ClaudeCodeAdapterError, match="reviewed schema") as captured:
        _adapt(path)
    assert PRIVATE_SENTINELS[2] not in str(captured.value)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"type":"result","type":"' + PRIVATE_SENTINELS[2] + '"}',
        encoding="utf-8",
    )
    with pytest.raises(ClaudeCodeAdapterError, match="invalid result") as captured:
        _adapt(duplicate)
    assert PRIVATE_SENTINELS[2] not in str(captured.value)

    deep = tmp_path / "deep.json"
    deep.write_text("[" * 65 + "0" + "]" * 65, encoding="utf-8")
    with pytest.raises(ClaudeCodeAdapterError, match="nesting limit"):
        _adapt(deep)


def test_structural_token_limit_fails_before_json_tree_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "wide.json"
    path.write_bytes(b"[" + b"0," * 100_001 + b"0]")

    def loads_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("json.loads must not run after the structural limit is exceeded")

    monkeypatch.setattr(claude_code_module.json, "loads", loads_must_not_run)
    with pytest.raises(ClaudeCodeAdapterError, match="structural token limit"):
        _adapt(path)


def test_read_errors_and_non_regular_inputs_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "result.json"
    _write(path, _payload())

    def fail_fdopen(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("PRIVATE_READ_FAILURE")

    monkeypatch.setattr(claude_code_module.os, "fdopen", fail_fdopen)
    with pytest.raises(ClaudeCodeAdapterError, match="cannot read Claude Code") as captured:
        _adapt(path)
    assert "PRIVATE_READ_FAILURE" not in str(captured.value)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    path = tmp_path / "claude-result.fifo"
    os.mkfifo(path)
    with pytest.raises(ClaudeCodeAdapterError, match="regular file"):
        _adapt(path)


def test_symlink_input_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "private-result.json"
    _write(target, _payload())
    link = tmp_path / "result-link.json"
    link.symlink_to(target)
    with pytest.raises(ClaudeCodeAdapterError, match="cannot open"):
        _adapt(link)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workload_id", "../private"),
        ("workload_version", "contains spaces"),
        ("result_id", "sk-proj-secretsecretsecret"),
        ("attempt_id", "https://private.invalid/id"),
    ],
)
def test_requires_content_free_operator_identifiers(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    path = tmp_path / "result.json"
    _write(path, _payload())
    with pytest.raises(ClaudeCodeAdapterError, match="content-free"):
        _adapt(path, **{field: value})
