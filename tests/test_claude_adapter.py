from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from model_skyline.adapters.claude import (
    CLAUDE_AGENT_SDK_COMMIT,
    CLAUDE_AGENT_SDK_TYPES_URL,
    CLAUDE_AGENT_SDK_VERSION,
    CLAUDE_CODE_CLI_VERSION,
    ClaudeAdapterError,
    ClaudeRouteMapping,
    adapt_claude_result,
)
from model_skyline.models import OfferingKey, WorkloadReference
from model_skyline.traces import RequestTrace, aggregate_traces

FIXTURE = Path(__file__).parent / "fixtures" / "claude_result_single_model.json"
TIMESTAMP = datetime(2026, 8, 30, 15, 45, tzinfo=UTC)


@dataclass
class SyntheticResultMessage:
    subtype: str
    is_error: bool
    total_cost_usd: float | None
    model_usage: dict[str, dict[str, Any]] | None
    # These SDK fields are deliberately present so the retention test proves
    # the adapter does not copy them into the canonical row.
    session_id: str
    result: str | None
    structured_output: Any
    permission_denials: list[Any]
    usage: dict[str, Any] | None


def _message() -> SyntheticResultMessage:
    payload = cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))
    return SyntheticResultMessage(
        subtype=cast(str, payload["subtype"]),
        is_error=cast(bool, payload["is_error"]),
        total_cost_usd=cast(float, payload["total_cost_usd"]),
        model_usage=cast(dict[str, dict[str, Any]], payload["model_usage"]),
        session_id=cast(str, payload["session_id"]),
        result=cast(str | None, payload["result"]),
        structured_output=payload["structured_output"],
        permission_denials=cast(list[Any], payload["permission_denials"]),
        usage=cast(dict[str, Any], payload["usage"]),
    )


def _adapt_route(**overrides: Any) -> ClaudeRouteMapping:
    arguments: dict[str, Any] = {
        "offering": OfferingKey(
            offering_id="anthropic/claude-sonnet-4-6@first-party",
            model_id="claude-sonnet-4-6",
            provider="anthropic",
            agent_harness="claude-agent-sdk",
        ),
        "model_usage_key": "claude-sonnet-4-6",
        "upstream_provider": "firstParty",
        "single_route_and_pricing_basis_attested": True,
        "route_details_attested": False,
    }
    arguments.update(overrides)
    return ClaudeRouteMapping.model_validate(arguments)


def _adapt(message: SyntheticResultMessage | None = None, **overrides: Any) -> RequestTrace:
    arguments: dict[str, Any] = {
        "sdk_version": CLAUDE_AGENT_SDK_VERSION,
        "claude_code_version": CLAUDE_CODE_CLI_VERSION,
        "final_cumulative_result": True,
        "accounting_scope": "single_query",
        "timestamp": TIMESTAMP,
        "workload_id": "coding-agent",
        "workload_version": "v1",
        "work_unit_id": "issue-17",
        "route": _adapt_route(),
        "result_id": "result-17-attempt-2",
        "attempt_id": "attempt-2",
        "work_unit_success": Decimal("1"),
    }
    arguments.update(overrides)
    return adapt_claude_result(message or _message(), **arguments)


def test_maps_final_single_model_result_without_copying_sensitive_fields() -> None:
    trace = _adapt()

    assert trace.timestamp == TIMESTAMP
    assert trace.workload_id == "coding-agent"
    assert trace.workload_version == "v1"
    assert trace.work_unit_id == "issue-17"
    assert trace.offering_id == "anthropic/claude-sonnet-4-6@first-party"
    assert trace.request_id == "result-17-attempt-2"
    assert trace.attempt_id == "attempt-2"
    assert trace.observation_unit == "attempt"
    assert trace.model_request_count is None
    assert trace.work_unit_success == Decimal(1)
    assert trace.input_uncached_tokens == Decimal(7)
    assert trace.input_cache_read_tokens == Decimal(1200)
    assert trace.input_cache_write_tokens == Decimal(400)
    assert trace.output_total_tokens == Decimal(89)
    assert trace.web_search_calls == Decimal(2)
    assert trace.estimated_total_cost_usd == Decimal("0.123456789012")
    assert trace.upstream_version == f"{CLAUDE_AGENT_SDK_VERSION}+cli.{CLAUDE_CODE_CLI_VERSION}"

    # ResultMessage does not support these meters at this granularity.  None
    # means unknown; a zero would be an unsupported measurement claim.
    assert trace.input_cache_write_5m_tokens is None
    assert trace.input_cache_write_1h_tokens is None
    assert trace.cache_storage_token_hours is None
    assert trace.output_tokens is None
    assert trace.reasoning_tokens is None
    assert trace.tool_calls is None
    assert trace.sandbox_seconds is None
    assert trace.other_cost_usd is None
    assert trace.ttft_ms is None
    assert trace.output_tokens_per_second is None

    serialized = trace.model_dump_json()
    for forbidden in (
        "synthetic-session-content-must-not-persist",
        "SYNTHETIC_RESULT_CONTENT_MUST_NOT_PERSIST",
        "SYNTHETIC_STRUCTURED_OUTPUT_MUST_NOT_PERSIST",
        "/synthetic/private/path-must-not-persist",
        "permission_denials",
        "tool_input",
    ):
        assert forbidden not in serialized


def test_aggregate_row_preserves_usage_but_does_not_invent_request_count(tmp_path: Path) -> None:
    trace_path = tmp_path / "claude.jsonl"
    trace_path.write_text(_adapt().model_dump_json() + "\n", encoding="utf-8")

    summary = aggregate_traces(
        trace_path,
        workload=WorkloadReference(id="coding-agent", version="v1", unit="issue"),
        retrieved_at=TIMESTAMP,
    )
    signals = summary.offerings["anthropic/claude-sonnet-4-6@first-party"]

    assert "request_count_per_work_unit" not in signals
    assert signals["attempt_count_per_work_unit"].value == Decimal(1)
    assert signals["input_uncached_tokens_per_work_unit"].value == Decimal(7)
    assert signals["input_cache_write_tokens_per_work_unit"].value == Decimal(400)
    assert signals["output_total_tokens_per_work_unit"].value == Decimal(89)
    assert signals["estimated_total_cost_usd_per_work_unit"].value == Decimal("0.123456789012")
    assert "output_tokens_per_work_unit" not in signals
    assert "tool_calls_per_work_unit" not in signals


def test_reviewed_sdk_contract_is_immutably_pinned() -> None:
    assert CLAUDE_AGENT_SDK_VERSION == "0.2.148"
    assert CLAUDE_AGENT_SDK_COMMIT == "af5ff1b9f2f279575f89b78f17572c6e35fbc2b6"
    assert CLAUDE_AGENT_SDK_COMMIT in CLAUDE_AGENT_SDK_TYPES_URL
    assert CLAUDE_CODE_CLI_VERSION == "2.1.251"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workload_id", ""),
        ("workload_version", "../private"),
        ("work_unit_id", "/private/path"),
        ("result_id", "contains spaces"),
        ("attempt_id", ".."),
    ],
)
def test_requires_content_free_explicit_identifiers(field: str, value: str) -> None:
    overrides: dict[str, Any] = {field: value}
    with pytest.raises(ClaudeAdapterError, match="content-free opaque identifier"):
        _adapt(**overrides)


def test_requires_reviewed_sdk_finality_timezone_and_explicit_decimal_outcome() -> None:
    with pytest.raises(ClaudeAdapterError, match="unsupported Claude Agent SDK version"):
        _adapt(sdk_version="0.2.149")
    with pytest.raises(ClaudeAdapterError, match="unsupported Claude Code CLI version"):
        _adapt(claude_code_version="2.1.250")
    with pytest.raises(ClaudeAdapterError, match="last result"):
        _adapt(final_cumulative_result=False)
    with pytest.raises(ClaudeAdapterError, match="timezone-aware"):
        _adapt(timestamp=datetime(2026, 8, 30, 15, 45))
    with pytest.raises(ClaudeAdapterError, match="explicit Decimal"):
        _adapt(work_unit_success=1)
    with pytest.raises(ClaudeAdapterError, match="between zero and one"):
        _adapt(work_unit_success=Decimal("1.1"))


def test_fails_closed_for_multiple_models_and_model_identity_drift() -> None:
    message = _message()
    assert message.model_usage is not None
    message.model_usage["claude-haiku-4-5"] = deepcopy(message.model_usage["claude-sonnet-4-6"])
    with pytest.raises(ClaudeAdapterError, match="multiple models"):
        _adapt(message)

    route = _adapt_route(model_usage_key="claude-opus-4-6")
    with pytest.raises(ClaudeAdapterError, match="does not match the reviewed route"):
        _adapt(route=route)

    route = _adapt_route(upstream_provider="bedrock")
    with pytest.raises(ClaudeAdapterError, match="provider does not match"):
        _adapt(route=route)


def test_binds_canonical_route_to_offering_and_attests_narrow_fields() -> None:
    with pytest.raises(ValidationError, match="whole segment"):
        _adapt_route(single_route_and_pricing_basis_attested=False)

    mismatched = OfferingKey(
        offering_id="anthropic/claude-opus-4-6@first-party",
        model_id="claude-opus-4-6",
        provider="anthropic",
        agent_harness="claude-agent-sdk",
    )
    with pytest.raises(ClaudeAdapterError, match="canonicalModel does not match"):
        _adapt(route=_adapt_route(offering=mismatched))

    narrow = OfferingKey(
        offering_id="anthropic/claude-sonnet-4-6@priority",
        model_id="claude-sonnet-4-6",
        provider="anthropic",
        service_tier="priority",
        agent_harness="claude-agent-sdk",
    )
    with pytest.raises(ValidationError, match="route_details_attested"):
        _adapt_route(offering=narrow)
    assert (
        _adapt(route=_adapt_route(offering=narrow, route_details_attested=True)).offering_id
        == narrow.offering_id
    )


def test_terminal_status_is_bounded_and_crash_usage_remains_unknown() -> None:
    unknown = _message()
    unknown.subtype = "future_terminal_variant"
    with pytest.raises(ClaudeAdapterError, match="subtype is unsupported"):
        _adapt(unknown)

    incoherent = _message()
    incoherent.subtype = "error_max_turns"
    incoherent.is_error = False
    with pytest.raises(ClaudeAdapterError, match="must set is_error true"):
        _adapt(incoherent)

    crash = _message()
    crash.subtype = "error_during_execution"
    crash.is_error = True
    trace = _adapt(crash, work_unit_success=Decimal(0))
    assert trace.input_uncached_tokens is None
    assert trace.input_cache_read_tokens is None
    assert trace.input_cache_write_tokens is None
    assert trace.output_total_tokens is None
    assert trace.estimated_total_cost_usd is None

    api_error = _message()
    api_error.subtype = "success"
    api_error.is_error = True
    assert _adapt(api_error, work_unit_success=Decimal(0)).input_uncached_tokens == Decimal(7)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("inputTokens", True, "must be an integer"),
        ("outputTokens", 1.5, "must be an integer"),
        ("cacheReadInputTokens", -1, "cannot be negative"),
        ("cacheCreationInputTokens", 10**38, "exceeds 38 digits"),
        ("webSearchRequests", None, "must be an integer"),
        ("contextWindow", 0, "must be positive"),
        ("maxOutputTokens", -1, "cannot be negative"),
    ],
)
def test_rejects_malformed_typed_usage_counters(field: str, value: Any, message: str) -> None:
    result = _message()
    assert result.model_usage is not None
    result.model_usage["claude-sonnet-4-6"][field] = value
    with pytest.raises(ClaudeAdapterError, match=message):
        _adapt(result)


@pytest.mark.parametrize("value", [None, -1.0, float("nan"), float("inf"), True, "0.1"])
def test_rejects_missing_nonfinite_negative_or_untyped_total_cost(value: Any) -> None:
    result = _message()
    result.total_cost_usd = value
    with pytest.raises(ClaudeAdapterError, match="total_cost_usd"):
        _adapt(result)


def test_rejects_missing_usage_fields_and_cost_disagreement() -> None:
    missing_usage = _message()
    missing_usage.model_usage = None
    with pytest.raises(ClaudeAdapterError, match="model_usage is required"):
        _adapt(missing_usage)

    missing_field = _message()
    assert missing_field.model_usage is not None
    missing_field.model_usage["claude-sonnet-4-6"].pop("cacheReadInputTokens")
    with pytest.raises(ClaudeAdapterError, match="missing required field"):
        _adapt(missing_field)

    disagreement = _message()
    assert disagreement.model_usage is not None
    disagreement.model_usage["claude-sonnet-4-6"]["costUSD"] = 0.12
    with pytest.raises(ClaudeAdapterError, match="does not match"):
        _adapt(disagreement)
