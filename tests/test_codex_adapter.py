from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from model_skyline.adapters import codex as codex_module
from model_skyline.adapters.codex import (
    CODEX_EVENTS_SOURCE_URLS,
    CODEX_REVIEWED_RELEASES,
    CodexAdapterError,
    adapt_codex_exec_jsonl,
)
from model_skyline.models import OfferingKey, WorkloadReference
from model_skyline.traces import aggregate_traces

TIMESTAMP = datetime(2026, 8, 30, 20, 0, tzinfo=UTC)
ITEM_SENTINEL = "PRIVATE_CODEX_ITEM_PAYLOAD_MUST_NOT_PERSIST"


def _events(version: str = "0.151.0") -> list[dict[str, Any]]:
    usage = {
        "input_tokens": 1000,
        "cached_input_tokens": 700,
        "output_tokens": 120,
        "reasoning_output_tokens": 20,
    }
    if version == "0.151.0":
        usage["cache_write_input_tokens"] = 100
    return [
        {"type": "thread.started", "thread_id": "raw-thread-id-never-retained"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "id": "item-1",
                "type": "agent_message",
                "text": ITEM_SENTINEL,
            },
        },
        {"type": "turn.completed", "usage": usage},
    ]


def _write(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(event, separators=(",", ":")) for event in events) + "\n",
        encoding="utf-8",
    )


def _adapt(path: Path, **overrides: Any):
    arguments: dict[str, Any] = {
        "codex_version": "0.151.0",
        "model_route_attested": True,
        "selected_provider": "openai",
        "selected_model": "gpt-5.3-codex",
        "route_details_attested": False,
        "timestamp": TIMESTAMP,
        "workload_id": "coding-agent",
        "workload_version": "v1",
        "work_unit_id": "issue-42",
        "offering": OfferingKey(
            offering_id="openai/gpt-5.3-codex@codex",
            model_id="gpt-5.3-codex",
            provider="openai",
            agent_harness="codex",
        ),
        "result_id": "codex-result-42",
        "attempt_id": "attempt-1",
        "work_unit_success": Decimal(1),
    }
    arguments.update(overrides)
    return adapt_codex_exec_jsonl(path, **arguments)


def test_current_reviewed_stream_maps_disjoint_usage_without_retaining_items(
    tmp_path: Path,
) -> None:
    path = tmp_path / "codex.jsonl"
    _write(path, _events())

    trace = _adapt(path)

    assert trace.observation_unit == "attempt"
    assert trace.model_request_count is None
    assert trace.input_total_tokens == Decimal(1000)
    assert trace.input_uncached_tokens == Decimal(200)
    assert trace.input_cache_read_tokens == Decimal(700)
    assert trace.input_cache_write_tokens == Decimal(100)
    assert trace.output_total_tokens == Decimal(120)
    assert trace.output_tokens == Decimal(100)
    assert trace.reasoning_tokens == Decimal(20)
    assert trace.tool_calls is None
    assert trace.ttft_ms is None
    assert trace.output_tokens_per_second is None
    serialized = trace.model_dump_json()
    assert ITEM_SENTINEL not in serialized
    assert "raw-thread-id-never-retained" not in serialized


def test_older_reviewed_stream_preserves_inclusive_input_without_guessing_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "codex-old.jsonl"
    _write(path, _events("0.144.2"))

    trace = _adapt(path, codex_version="0.144.2")

    assert trace.input_total_tokens == Decimal(1000)
    assert trace.input_cache_read_tokens == Decimal(700)
    assert trace.input_uncached_tokens is None
    assert trace.input_cache_write_tokens is None


def test_aggregate_turn_does_not_invent_model_request_count(tmp_path: Path) -> None:
    stream = tmp_path / "codex.jsonl"
    _write(stream, _events())
    trace = _adapt(stream)
    canonical = tmp_path / "canonical.jsonl"
    canonical.write_text(trace.model_dump_json() + "\n", encoding="utf-8")

    summary = aggregate_traces(
        canonical,
        workload=WorkloadReference(id="coding-agent", version="v1", unit="issue"),
        retrieved_at=TIMESTAMP,
    )
    signals = summary.offerings["openai/gpt-5.3-codex@codex"]

    assert "request_count_per_work_unit" not in signals
    assert signals["attempt_count_per_work_unit"].value == Decimal(1)
    assert signals["input_total_tokens_per_work_unit"].value == Decimal(1000)


def test_reviewed_release_sources_are_immutably_pinned() -> None:
    assert CODEX_REVIEWED_RELEASES == {
        "0.144.2": "a6645b6b8a656360fa16fb7e1c6721d0697d3d6a",
        "0.151.0": "78c290807ce710180111df227df3b7a4fe845452",
    }
    for version, commit in CODEX_REVIEWED_RELEASES.items():
        assert commit in CODEX_EVENTS_SOURCE_URLS[version]


def test_rejects_unreviewed_version_and_unattested_model(tmp_path: Path) -> None:
    path = tmp_path / "codex.jsonl"
    _write(path, _events())

    with pytest.raises(CodexAdapterError, match="unsupported Codex version"):
        _adapt(path, codex_version="0.152.0")
    with pytest.raises(CodexAdapterError, match="explicitly be true"):
        _adapt(path, model_route_attested=False)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"input_tokens": 799}, "cache input exceeds"),
        ({"output_tokens": 19}, "reasoning output exceeds"),
        ({"input_tokens": True}, "must be an integer"),
        ({"reasoning_output_tokens": -1}, "safe integer"),
    ],
)
def test_rejects_incoherent_or_untyped_usage(
    tmp_path: Path,
    mutation: dict[str, Any],
    message: str,
) -> None:
    path = tmp_path / "bad-usage.jsonl"
    events = _events()
    events[-1]["usage"].update(mutation)
    _write(path, events)

    with pytest.raises(CodexAdapterError, match=message):
        _adapt(path)


def test_rejects_schema_drift_and_events_after_completion_but_retains_failures(
    tmp_path: Path,
) -> None:
    drift = tmp_path / "drift.jsonl"
    events = _events()
    events[-1]["usage"]["new_counter"] = 1
    _write(drift, events)
    with pytest.raises(CodexAdapterError, match="reviewed schema"):
        _adapt(drift)

    failure = tmp_path / "failure.jsonl"
    _write(
        failure,
        [
            {"type": "thread.started", "thread_id": "thread"},
            {"type": "turn.started"},
            {"type": "turn.failed", "error": {"message": ITEM_SENTINEL}},
        ],
    )
    failed_trace = _adapt(failure, work_unit_success=Decimal(0))
    assert failed_trace.work_unit_success == 0
    assert failed_trace.input_total_tokens is None
    assert failed_trace.output_total_tokens is None
    assert ITEM_SENTINEL not in failed_trace.model_dump_json()

    route_failure = tmp_path / "route-failure.jsonl"
    _write(
        route_failure,
        [
            {"type": "thread.started", "thread_id": "thread"},
            {
                "type": "item.completed",
                "item": {"type": "error", "text": ITEM_SENTINEL},
            },
            {"type": "turn.started"},
            {"type": "error", "message": ITEM_SENTINEL},
            {"type": "turn.failed", "error": {"message": ITEM_SENTINEL}},
        ],
    )
    route_failure_trace = _adapt(route_failure, work_unit_success=Decimal(0))
    assert route_failure_trace.input_total_tokens is None
    assert route_failure_trace.output_total_tokens is None
    assert ITEM_SENTINEL not in route_failure_trace.model_dump_json()

    trailing = tmp_path / "trailing.jsonl"
    events = _events()
    events.append({"type": "item.completed", "item": {"private": ITEM_SENTINEL}})
    _write(trailing, events)
    with pytest.raises(CodexAdapterError, match="after turn completion"):
        _adapt(trailing)


def test_duplicate_json_keys_are_rejected_without_echoing_values(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.jsonl"
    path.write_text(
        '{"type":"thread.started","thread_id":"safe","thread_id":"' + ITEM_SENTINEL + '"}\n',
        encoding="utf-8",
    )

    with pytest.raises(CodexAdapterError, match="invalid event") as captured:
        _adapt(path)
    assert ITEM_SENTINEL not in str(captured.value)


def test_excessive_json_depth_is_rejected_as_an_invalid_event(tmp_path: Path) -> None:
    path = tmp_path / "deep.jsonl"
    nested = "[" * 65 + "0" + "]" * 65
    path.write_text(
        '{"type":"thread.started","thread_id":' + nested + "}\n",
        encoding="utf-8",
    )

    with pytest.raises(CodexAdapterError, match="invalid event"):
        _adapt(path)


def test_read_side_os_error_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "codex.jsonl"
    _write(path, _events())

    def fail_fdopen(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("private read failure")

    monkeypatch.setattr(codex_module.os, "fdopen", fail_fdopen)

    with pytest.raises(CodexAdapterError, match="cannot read Codex JSONL input") as captured:
        _adapt(path)
    assert "private read failure" not in str(captured.value)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_fifo_input_is_rejected_without_blocking(tmp_path: Path) -> None:
    path = tmp_path / "codex.fifo"
    os.mkfifo(path)

    with pytest.raises(CodexAdapterError, match="regular file"):
        _adapt(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selected_model", "https://provider.invalid/model"),
        ("workload_id", "../private"),
        ("result_id", "contains spaces"),
    ],
)
def test_requires_content_free_operator_metadata(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    path = tmp_path / "codex.jsonl"
    _write(path, _events())

    with pytest.raises(CodexAdapterError, match="content-free"):
        _adapt(path, **{field: value})


def test_binds_attested_route_to_offering_and_requires_narrow_field_attestation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "codex.jsonl"
    _write(path, _events())
    mismatched = OfferingKey(
        offering_id="openai/other@codex",
        model_id="other",
        provider="openai",
        agent_harness="codex",
    )
    with pytest.raises(CodexAdapterError, match="does not match"):
        _adapt(path, offering=mismatched)

    narrow = OfferingKey(
        offering_id="openai/gpt-5.3-codex@priority",
        model_id="gpt-5.3-codex",
        provider="openai",
        service_tier="priority",
        agent_harness="codex",
    )
    with pytest.raises(CodexAdapterError, match="route_details_attested"):
        _adapt(path, offering=narrow)
    trace = _adapt(path, offering=narrow, route_details_attested=True)
    assert trace.offering_id == narrow.offering_id
