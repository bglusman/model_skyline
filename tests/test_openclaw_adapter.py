from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from model_skyline.adapters.openclaw import (
    OPENCLAW_ADAPTER_VERSION,
    OPENCLAW_ATTEMPT_SETUP_URL,
    OPENCLAW_ATTEMPT_STREAM_URL,
    OPENCLAW_COLLECTOR_ID,
    OPENCLAW_COLLECTOR_VERSION,
    OPENCLAW_DIAGNOSTIC_TYPES_URL,
    OPENCLAW_MODEL_LIFECYCLE_URL,
    OPENCLAW_MODEL_OBSERVATION_URL,
    OPENCLAW_REVIEWED_COMMIT,
    OPENCLAW_REVIEWED_VERSION,
    OPENCLAW_TRACE_SCHEMA_VERSION,
    OpenClawAdapterError,
    OpenClawTraceEnvelope,
    adapt_openclaw_event,
    compute_openclaw_projection_signature,
)
from model_skyline.models import OfferingKey, WorkloadReference
from model_skyline.traces import TraceAggregationError, aggregate_traces

OFFERING = OfferingKey(
    offering_id="anthropic/claude-test@direct-us",
    model_id="claude-test",
    provider="anthropic",
    agent_harness="openclaw",
)
COLLECTOR_KEY = b"synthetic-openclaw-collector-key"


def _payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": OPENCLAW_TRACE_SCHEMA_VERSION,
        "openclaw_version": OPENCLAW_REVIEWED_VERSION,
        "collector_id": OPENCLAW_COLLECTOR_ID,
        "collector_version": OPENCLAW_COLLECTOR_VERSION,
        "collector_signature": "0" * 64,
        "workload_id": "coding-issue",
        "workload_version": "1.2.0",
        "work_unit_id": "synthetic-case-17",
        "work_unit_success": "1",
        "runAttempt": 1,
        "segmentEventsComplete": True,
        "usageComplete": True,
        "event": {
            "type": "model.call.completed",
            "ts": 1_788_123_456_789,
            "seq": 41,
            "runId": "run-synthetic-17",
            "callId": "run-synthetic-17:model-call:2",
            "provider": "anthropic",
            "model": "claude-test",
            "observationUnit": "request",
            "durationMs": 2_500,
            "timeToFirstByteMs": 275,
            "usage": {
                "input": 1_250,
                "output": 160,
                "cacheRead": 8_000,
                "cacheWrite": 0,
                "reasoningTokens": 40,
            },
        },
    }
    payload["collector_signature"] = compute_openclaw_projection_signature(
        payload,
        collector_key=COLLECTOR_KEY,
    )
    return payload


def _adapt(payload: dict[str, Any] | None = None, **overrides: Any):
    arguments: dict[str, Any] = {
        "offering": OFFERING,
        "collector_key": COLLECTOR_KEY,
        "expected_api": None,
        "expected_transport": None,
        "route_details_attested": False,
    }
    arguments.update(overrides)
    return adapt_openclaw_event(payload if payload is not None else _payload(), **arguments)


def _resign(payload: dict[str, Any]) -> None:
    payload["collector_signature"] = compute_openclaw_projection_signature(
        payload,
        collector_key=COLLECTOR_KEY,
    )


def test_model_call_event_maps_attested_complete_usage_without_raw_ids() -> None:
    payload = _payload()

    trace = _adapt(payload)

    assert trace.timestamp == datetime(2026, 8, 30, 20, 57, 36, 789000, tzinfo=UTC)
    assert trace.workload_id == "coding-issue"
    assert trace.workload_version == "1.2.0"
    assert trace.work_unit_id == "synthetic-case-17"
    assert trace.offering_id == "anthropic/claude-test@direct-us"
    assert trace.observation_unit == "model_call"
    assert trace.adapter_version == OPENCLAW_ADAPTER_VERSION
    assert trace.model_request_count is None
    assert trace.work_unit_success == Decimal(1)
    assert trace.input_uncached_tokens == Decimal(1_250)
    assert trace.input_cache_read_tokens == Decimal(8_000)
    assert trace.input_cache_write_tokens == Decimal(0)
    # OpenClaw reasoningTokens is a subset of output; canonical buckets are disjoint.
    assert trace.output_tokens == Decimal(120)
    assert trace.reasoning_tokens == Decimal(40)
    assert trace.output_total_tokens == Decimal(160)
    # OpenClaw reports TTFB, not TTFT, so timing is not relabeled.
    assert trace.ttft_ms is None
    assert trace.output_tokens_per_second is None
    assert trace.tool_calls is None
    assert trace.other_cost_usd is None
    assert trace.estimated_total_cost_usd is None
    assert trace.billed_total_cost_usd is None
    assert trace.request_id.startswith("openclaw:model-call:hmac-sha256:")
    assert trace.attempt_id.startswith("openclaw:attempt:hmac-sha256:")
    serialized = str(trace.model_dump(mode="json"))
    assert "run-synthetic-17" not in serialized
    assert "model-call:2" not in serialized


def test_pseudonymous_ids_are_stable_and_domain_separated() -> None:
    first = _adapt()
    second = _adapt()

    assert first.request_id == second.request_id
    assert first.attempt_id == second.attempt_id
    assert first.request_id != first.attempt_id

    rotated_key = b"rotated-synthetic-openclaw-key"
    rotated_payload = _payload()
    rotated_payload["collector_signature"] = compute_openclaw_projection_signature(
        rotated_payload,
        collector_key=rotated_key,
    )
    rotated = _adapt(rotated_payload, collector_key=rotated_key)
    assert rotated.request_id != first.request_id
    assert rotated.attempt_id != first.attempt_id


def test_reused_upstream_ids_are_scoped_to_the_signed_work_unit(tmp_path: Path) -> None:
    first_payload = _payload()
    second_payload = _payload()
    second_payload["work_unit_id"] = "synthetic-case-18"
    _resign(second_payload)

    first = _adapt(first_payload)
    second = _adapt(second_payload)

    assert first.request_id != second.request_id
    assert first.attempt_id != second.attempt_id

    trace_path = tmp_path / "openclaw-reused-upstream-ids.jsonl"
    trace_path.write_text(
        "\n".join(json.dumps(trace.model_dump(mode="json")) for trace in (first, second)) + "\n",
        encoding="utf-8",
    )
    summary = aggregate_traces(
        trace_path,
        workload=WorkloadReference(id="coding-issue", version="1.2.0", unit="coding_issue"),
    )
    signals = summary.offerings["anthropic/claude-test@direct-us"]
    assert "request_count_per_work_unit" not in signals
    assert signals["attempt_count_per_work_unit"].value == Decimal(1)


def test_exact_model_call_replay_keeps_stable_id_and_is_rejected(tmp_path: Path) -> None:
    first = _adapt()
    replay = _adapt()
    assert first.request_id == replay.request_id

    trace_path = tmp_path / "openclaw-exact-replay.jsonl"
    trace_path.write_text(
        first.model_dump_json() + "\n" + replay.model_dump_json() + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TraceAggregationError, match="duplicate"):
        aggregate_traces(
            trace_path,
            workload=WorkloadReference(id="coding-issue", version="1.2.0", unit="coding_issue"),
        )


@pytest.mark.parametrize("value", [False, 0, 1, "true", None])
def test_segment_completeness_must_be_explicit_literal_true(value: Any) -> None:
    payload = _payload()
    payload["segmentEventsComplete"] = value

    with pytest.raises(OpenClawAdapterError, match="safe-envelope"):
        _adapt(payload)


def test_incomplete_usage_must_be_omitted_and_stays_unknown() -> None:
    payload = _payload()
    payload["usageComplete"] = False
    del payload["event"]["usage"]
    _resign(payload)

    trace = _adapt(payload)

    assert trace.observation_unit == "model_call"
    assert trace.model_request_count is None
    assert trace.input_uncached_tokens is None
    assert trace.output_total_tokens is None


def test_incomplete_usage_cannot_publish_latest_response_meters() -> None:
    payload = _payload()
    payload["usageComplete"] = False

    with pytest.raises(OpenClawAdapterError, match="safe-envelope"):
        _adapt(payload)


@pytest.mark.parametrize("field", ["segmentEventsComplete", "usageComplete"])
def test_completeness_attestations_are_required(field: str) -> None:
    payload = _payload()
    del payload[field]

    with pytest.raises(OpenClawAdapterError, match="safe-envelope"):
        _adapt(payload)


def test_signed_envelope_is_parsed_once_before_route_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    original = OpenClawTraceEnvelope.model_validate
    calls = 0

    def tracked(value: Any, *args: Any, **kwargs: Any) -> OpenClawTraceEnvelope:
        nonlocal calls
        calls += 1
        if calls > 1:
            payload["event"]["model"] = "mutated-after-signature-check"
        return original(value, *args, **kwargs)

    monkeypatch.setattr(OpenClawTraceEnvelope, "model_validate", tracked)

    trace = _adapt(payload)

    assert calls == 1
    assert trace.offering_id == OFFERING.offering_id


def test_unknown_retention_cache_writes_use_only_the_generic_meter() -> None:
    payload = _payload()
    payload["event"]["usage"]["cacheWrite"] = 375
    _resign(payload)

    trace = _adapt(payload)

    assert trace.input_cache_write_tokens == Decimal(375)
    assert trace.input_cache_write_5m_tokens is None
    assert trace.input_cache_write_1h_tokens is None


def test_adapter_output_preserves_usage_without_inventing_provider_request_count(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["event"]["usage"]["cacheWrite"] = 375
    _resign(payload)
    trace = _adapt(payload)
    trace_path = tmp_path / "openclaw-safe.jsonl"
    trace_path.write_text(
        json.dumps(trace.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    summary = aggregate_traces(
        trace_path,
        workload=WorkloadReference(id="coding-issue", version="1.2.0", unit="coding_issue"),
    )

    signals = summary.offerings["anthropic/claude-test@direct-us"]
    assert "request_count_per_work_unit" not in signals
    assert signals["input_cache_write_tokens_per_work_unit"].value == Decimal(375)
    assert signals["output_total_tokens_per_work_unit"].value == Decimal(160)


def test_two_calls_in_one_attempt_share_attempt_identity(tmp_path: Path) -> None:
    first_payload = _payload()
    second_payload = _payload()
    second_payload["event"]["callId"] = "run-synthetic-17:model-call:3"
    second_payload["event"]["seq"] = 42
    _resign(second_payload)

    first = _adapt(first_payload)
    second = _adapt(second_payload)

    assert first.request_id != second.request_id
    assert first.attempt_id == second.attempt_id

    trace_path = tmp_path / "openclaw-two-calls.jsonl"
    trace_path.write_text(
        "\n".join(json.dumps(trace.model_dump(mode="json")) for trace in (first, second)) + "\n",
        encoding="utf-8",
    )

    summary = aggregate_traces(
        trace_path,
        workload=WorkloadReference(id="coding-issue", version="1.2.0", unit="coding_issue"),
    )

    signals = summary.offerings["anthropic/claude-test@direct-us"]
    assert "request_count_per_work_unit" not in signals
    assert signals["attempt_count_per_work_unit"].value == Decimal(1)


def test_retry_with_reused_call_id_has_distinct_request_and_attempt_ids(tmp_path: Path) -> None:
    first_payload = _payload()
    retry_payload = _payload()
    retry_payload["runAttempt"] = 2
    retry_payload["event"]["seq"] = 42
    _resign(retry_payload)

    first = _adapt(first_payload)
    retry = _adapt(retry_payload)

    assert first.request_id != retry.request_id
    assert first.attempt_id != retry.attempt_id

    trace_path = tmp_path / "openclaw-retry.jsonl"
    trace_path.write_text(
        "\n".join(json.dumps(trace.model_dump(mode="json")) for trace in (first, retry)) + "\n",
        encoding="utf-8",
    )
    summary = aggregate_traces(
        trace_path,
        workload=WorkloadReference(id="coding-issue", version="1.2.0", unit="coding_issue"),
    )
    signals = summary.offerings["anthropic/claude-test@direct-us"]
    assert "request_count_per_work_unit" not in signals
    assert signals["attempt_count_per_work_unit"].value == Decimal(2)


def test_known_undercounting_adapter_version_is_rejected(tmp_path: Path) -> None:
    legacy = _adapt().model_copy(update={"adapter_version": "1alpha2"})
    trace_path = tmp_path / "openclaw-legacy-adapter.jsonl"
    trace_path.write_text(legacy.model_dump_json() + "\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="not in the reviewed registry"):
        aggregate_traces(
            trace_path,
            workload=WorkloadReference(
                id="coding-issue",
                version="1.2.0",
                unit="coding_issue",
            ),
        )


@pytest.mark.parametrize("run_attempt", [0, -1, 1.5, True, None])
def test_attempt_ordinal_must_be_a_positive_safe_integer(run_attempt: Any) -> None:
    payload = _payload()
    payload["runAttempt"] = run_attempt

    with pytest.raises(OpenClawAdapterError, match="safe-envelope"):
        _adapt(payload)


def test_error_terminal_is_supported_when_usage_is_complete() -> None:
    payload = _payload()
    payload["work_unit_success"] = "0"
    payload["event"]["type"] = "model.call.error"
    payload["event"]["errorCategory"] = "provider_timeout"
    payload["event"]["failureKind"] = "timeout"
    _resign(payload)

    trace = _adapt(payload)

    assert trace.work_unit_success == Decimal(0)
    assert trace.input_uncached_tokens == Decimal(1_250)


@pytest.mark.parametrize(
    "field",
    [
        "assistantTexts",
        "contextTokenBudget",
        "env",
        "lastAssistant",
        "messages",
        "prompt",
        "promptStats",
        "requestPayloadBytes",
        "responseStreamBytes",
        "sessionId",
        "sessionKey",
        "toolArgs",
        "trace",
        "upstreamRequestIdHash",
        "workspaceDir",
    ],
)
def test_content_and_context_fields_are_rejected_without_echo(field: str) -> None:
    payload = _payload()
    sensitive_marker = "SENSITIVE_MARKER_MUST_NOT_ESCAPE"
    payload["event"][field] = sensitive_marker

    with pytest.raises(OpenClawAdapterError) as caught:
        _adapt(payload)

    assert sensitive_marker not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "unknown_attributes"),
    [
        ("input", ("input_uncached_tokens",)),
        ("output", ("output_tokens", "output_total_tokens")),
        ("cacheRead", ("input_cache_read_tokens",)),
        ("cacheWrite", ("input_cache_write_tokens",)),
        ("reasoningTokens", ("output_tokens", "reasoning_tokens")),
    ],
)
def test_partial_complete_usage_preserves_call_and_keeps_missing_buckets_unknown(
    field: str,
    unknown_attributes: tuple[str, ...],
) -> None:
    payload = _payload()
    del payload["event"]["usage"][field]
    _resign(payload)

    trace = _adapt(payload)

    assert trace.model_request_count is None
    for attribute in unknown_attributes:
        assert getattr(trace, attribute) is None


def test_pre_usage_error_preserves_model_call_without_fabricated_meters() -> None:
    payload = _payload()
    payload["work_unit_success"] = "0"
    payload["event"].update(
        {
            "type": "model.call.error",
            "errorCategory": "connection_reset",
            "failureKind": "connection_reset",
        }
    )
    del payload["event"]["usage"]
    _resign(payload)

    trace = _adapt(payload)

    assert trace.model_request_count is None
    assert trace.work_unit_success == 0
    assert trace.input_uncached_tokens is None
    assert trace.output_total_tokens is None


@pytest.mark.parametrize("observation_unit", ["turn", None])
def test_non_request_or_implicit_observation_unit_fails_closed(
    observation_unit: str | None,
) -> None:
    payload = _payload()
    if observation_unit is None:
        del payload["event"]["observationUnit"]
    else:
        payload["event"]["observationUnit"] = observation_unit

    with pytest.raises(OpenClawAdapterError, match="safe-envelope"):
        _adapt(payload)


@pytest.mark.parametrize("version", ["2026.7.1-2", "2099.1.1"])
def test_unreviewed_openclaw_version_fails_closed(version: str) -> None:
    payload = _payload()
    payload["openclaw_version"] = version

    with pytest.raises(OpenClawAdapterError, match="safe-envelope"):
        _adapt(payload)


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        (("event", "durationMs"), 100),
        (("event", "usage", "output"), 39),
    ],
)
def test_incoherent_timing_and_reasoning_fail_closed(
    mutation: tuple[str, ...],
    value: int,
) -> None:
    payload = deepcopy(_payload())
    target = payload
    for key in mutation[:-1]:
        target = target[key]
    target[mutation[-1]] = value

    with pytest.raises(OpenClawAdapterError, match="safe-envelope"):
        _adapt(payload)


def test_unknown_usage_shape_fails_closed() -> None:
    payload = _payload()
    payload["event"]["usage"]["total"] = 9_410

    with pytest.raises(OpenClawAdapterError, match="safe-envelope"):
        _adapt(payload)


def test_credential_shaped_metadata_is_rejected_without_echo() -> None:
    payload = _payload()
    synthetic_credential = "sk-" + "proj-" + ("A" * 24)
    payload["event"]["callId"] = synthetic_credential

    with pytest.raises(OpenClawAdapterError) as caught:
        _adapt(payload)

    assert synthetic_credential not in str(caught.value)


@pytest.mark.parametrize(
    "unsafe_offering_id",
    [
        "https://private.example/model",
        "sk-proj-" + ("A" * 24),
    ],
)
def test_unsafe_offering_identity_is_rejected_without_echo(
    unsafe_offering_id: str,
) -> None:
    offering = OFFERING.model_copy(update={"offering_id": unsafe_offering_id})

    with pytest.raises(OpenClawAdapterError, match="content-free") as caught:
        _adapt(offering=offering)

    assert unsafe_offering_id not in str(caught.value)


def test_source_pin_is_full_commit_and_used_by_primary_type_url() -> None:
    assert len(OPENCLAW_REVIEWED_COMMIT) == 40
    assert OPENCLAW_REVIEWED_COMMIT in OPENCLAW_DIAGNOSTIC_TYPES_URL
    assert OPENCLAW_REVIEWED_COMMIT in OPENCLAW_ATTEMPT_SETUP_URL
    assert OPENCLAW_REVIEWED_COMMIT in OPENCLAW_ATTEMPT_STREAM_URL
    assert OPENCLAW_REVIEWED_COMMIT in OPENCLAW_MODEL_LIFECYCLE_URL
    assert OPENCLAW_REVIEWED_COMMIT in OPENCLAW_MODEL_OBSERVATION_URL


def test_requires_trusted_collector_and_binds_full_runtime_route() -> None:
    with pytest.raises(OpenClawAdapterError, match="signature is invalid"):
        _adapt(collector_key=b"different-synthetic-collector-key")

    tampered = _payload()
    tampered["event"]["model"] = "tampered-model"
    with pytest.raises(OpenClawAdapterError, match="signature is invalid"):
        _adapt(tampered)

    payload = _payload()
    payload["event"]["api"] = "anthropic-messages"
    payload["event"]["transport"] = "sse"
    _resign(payload)
    trace = _adapt(
        payload,
        expected_api="anthropic-messages",
        expected_transport="sse",
    )
    assert trace.offering_id == OFFERING.offering_id
    with pytest.raises(OpenClawAdapterError, match="API/transport"):
        _adapt(payload, expected_api="responses", expected_transport="sse")

    mismatched = OfferingKey(
        offering_id="openai/other@openclaw",
        model_id="other",
        provider="openai",
        agent_harness="openclaw",
    )
    with pytest.raises(OpenClawAdapterError, match="does not match"):
        _adapt(offering=mismatched)
