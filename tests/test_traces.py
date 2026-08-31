from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

import model_skyline.traces as traces_module
from model_skyline.models import ObservationCatalog, WorkloadReference
from model_skyline.traces import TraceAggregationError, aggregate_traces, enrich_catalog

WORKLOAD = WorkloadReference(
    id="coding-session-v1",
    version="1.0.0",
    unit="successful_coding_session",
)
TRACE_V2 = "model-skyline/request-trace/v1alpha2"
TRACE_V3 = "model-skyline/request-trace/v1alpha3"


def _v2(row: dict[str, object]) -> dict[str, object]:
    return {"schema_version": TRACE_V2, **row}


def _v3(row: dict[str, object]) -> dict[str, object]:
    return {"schema_version": TRACE_V3, **row}


def test_trace_aggregation_retains_failed_work_units_and_cache_meters(tmp_path) -> None:
    path = tmp_path / "requests.jsonl"
    rows = [
        {
            "timestamp": "2026-08-29T18:00:00Z",
            "workload_id": "coding-session-v1",
            "workload_version": "1.0.0",
            "work_unit_id": "success",
            "offering_id": "provider/model@tier",
            "request_id": "r1",
            "attempt_id": "a1",
            "work_unit_success": 1,
            "input_uncached_tokens": 100,
            "input_cache_read_tokens": 1000,
            "input_cache_write_5m_tokens": 100,
            "input_cache_write_1h_tokens": 0,
            "output_tokens": 20,
            "tool_calls": 2,
            "other_cost_usd": 0,
            "ttft_ms": 100,
            "output_tokens_per_second": 50,
        },
        {
            "timestamp": "2026-08-29T18:01:00Z",
            "workload_id": "coding-session-v1",
            "workload_version": "1.0.0",
            "work_unit_id": "success",
            "offering_id": "provider/model@tier",
            "request_id": "r2",
            "attempt_id": "a1",
            "work_unit_success": 1,
            "input_uncached_tokens": 50,
            "input_cache_read_tokens": 500,
            "input_cache_write_5m_tokens": 0,
            "input_cache_write_1h_tokens": 0,
            "output_tokens": 10,
            "tool_calls": 1,
            "other_cost_usd": 0,
            "ttft_ms": 200,
            "output_tokens_per_second": 40,
        },
        {
            "timestamp": "2026-08-29T18:02:00Z",
            "workload_id": "coding-session-v1",
            "workload_version": "1.0.0",
            "work_unit_id": "failure",
            "offering_id": "provider/model@tier",
            "request_id": "r3",
            "attempt_id": "a2",
            "work_unit_success": 0,
            "input_uncached_tokens": 200,
            "input_cache_read_tokens": 0,
            "input_cache_write_5m_tokens": 200,
            "input_cache_write_1h_tokens": 0,
            "output_tokens": 30,
            "tool_calls": 3,
            "other_cost_usd": 0.25,
            "ttft_ms": 300,
            "output_tokens_per_second": 30,
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    summary = aggregate_traces(path, workload=WORKLOAD)
    signals = summary.offerings["provider/model@tier"]

    raw_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    assert summary.source.raw_sha256 == raw_sha256
    assert summary.source.id == f"trace:sha256:{raw_sha256}"
    assert summary.source.version == "model-skyline/request-trace/v1alpha1"
    assert signals["work_unit_count"].value == Decimal(2)
    assert signals["success_rate"].value == Decimal("0.5")
    assert signals["request_count_per_work_unit"].value == Decimal("1.5")
    assert signals["request_count_per_success"].value == Decimal(3)
    assert signals["input_uncached_tokens_per_success"].value == Decimal(350)
    assert signals["input_cache_read_tokens_per_success"].value == Decimal(1500)
    assert signals["other_cost_usd_per_success"].value == Decimal("0.25")
    assert signals["ttft_p50_ms"].value == Decimal(200)
    assert signals["ttft_p50_ms"].sample_count == 3
    assert signals["observed_cache_hit_rate"].value == Decimal(
        "0.6976744186046511627906976744186047"
    )


def test_legacy_jsonl_absent_meter_defaults_to_zero_but_explicit_null_is_rejected(
    tmp_path,
) -> None:
    path = tmp_path / "legacy-null.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "work_unit_success": 1,
        "input_uncached_tokens": None,
    }
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="not valid canonical JSON"):
        aggregate_traces(path, workload=WORKLOAD)


def test_aggregate_scope_preserves_known_totals_without_inventing_missing_meters(
    tmp_path,
) -> None:
    path = tmp_path / "aggregate.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "request_id": "aggregate-record-1",
        "attempt_id": "attempt-1",
        "observation_unit": "attempt",
        "work_unit_success": 1,
        "input_uncached_tokens": 100,
        "input_cache_read_tokens": 900,
        "input_cache_write_tokens": 50,
        "estimated_total_cost_usd": "1.25",
    }
    path.write_text(f"{json.dumps(_v2(row))}\n", encoding="utf-8")

    summary = aggregate_traces(path, workload=WORKLOAD)
    signals = summary.offerings["provider/model@tier"]

    assert "request_count_per_work_unit" not in signals
    assert "tool_calls_per_work_unit" not in signals
    assert "output_tokens_per_work_unit" not in signals
    assert signals["observed_cache_hit_rate"].value == Decimal(
        "0.8571428571428571428571428571428571"
    )
    assert signals["attempt_count_per_work_unit"].value == Decimal(1)
    assert signals["input_cache_write_tokens_per_work_unit"].value == Decimal(50)
    assert signals["estimated_total_cost_usd_per_work_unit"].value == Decimal("1.25")


def test_model_call_scope_groups_calls_into_distinct_attempts_without_inventing_requests(
    tmp_path,
) -> None:
    path = tmp_path / "model-calls.jsonl"
    base = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "observation_unit": "model_call",
        "work_unit_success": 1,
    }
    rows = [
        {
            **base,
            "request_id": "logical-call-1",
            "attempt_id": "attempt-1",
            "input_uncached_tokens": 10,
        },
        {
            **base,
            "request_id": "logical-call-2",
            "attempt_id": "attempt-1",
            "input_uncached_tokens": 20,
        },
        {
            **base,
            "request_id": "logical-call-3",
            "attempt_id": "attempt-2",
            "input_uncached_tokens": 30,
        },
    ]
    path.write_text("\n".join(json.dumps(_v3(row)) for row in rows) + "\n", encoding="utf-8")

    summary = aggregate_traces(path, workload=WORKLOAD)
    signals = summary.offerings["provider/model@tier"]

    assert "request_count_per_work_unit" not in signals
    assert signals["attempt_count_per_work_unit"].value == Decimal(2)
    assert signals["input_uncached_tokens_per_work_unit"].value == Decimal(60)
    assert summary.source.version == TRACE_V3


def test_aggregate_scope_uses_explicit_model_request_count(tmp_path) -> None:
    path = tmp_path / "aggregate-count.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "request_id": "aggregate-record-1",
        "attempt_id": "attempt-1",
        "observation_unit": "work_unit",
        "model_request_count": 7,
        "work_unit_success": 1,
    }
    path.write_text(f"{json.dumps(_v2(row))}\n", encoding="utf-8")

    summary = aggregate_traces(path, workload=WORKLOAD)
    signals = summary.offerings["provider/model@tier"]

    assert signals["request_count_per_work_unit"].value == Decimal(7)
    assert "attempt_count_per_work_unit" not in signals


def test_work_unit_scope_uses_explicit_attempt_count(tmp_path) -> None:
    path = tmp_path / "aggregate-attempt-count.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "request_id": "aggregate-record-1",
        "attempt_id": "aggregate-attempt-record-1",
        "observation_unit": "work_unit",
        "model_request_count": 7,
        "attempt_count": 2,
        "work_unit_success": 1,
    }
    path.write_text(f"{json.dumps(_v2(row))}\n", encoding="utf-8")

    summary = aggregate_traces(path, workload=WORKLOAD)
    signals = summary.offerings["provider/model@tier"]

    assert signals["attempt_count_per_work_unit"].value == Decimal(2)


def test_attempt_count_is_omitted_when_any_work_unit_aggregate_is_unknown(tmp_path) -> None:
    path = tmp_path / "partial-attempt-count.jsonl"
    base = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "offering_id": "provider/model@tier",
        "observation_unit": "work_unit",
        "work_unit_success": 1,
    }
    rows = [
        {
            **base,
            "work_unit_id": "unit-1",
            "request_id": "aggregate-record-1",
            "attempt_id": "aggregate-attempt-record-1",
            "attempt_count": 2,
        },
        {
            **base,
            "work_unit_id": "unit-2",
            "request_id": "aggregate-record-2",
            "attempt_id": "aggregate-attempt-record-2",
        },
    ]
    path.write_text("\n".join(json.dumps(_v2(row)) for row in rows) + "\n", encoding="utf-8")

    summary = aggregate_traces(path, workload=WORKLOAD)
    signals = summary.offerings["provider/model@tier"]

    assert "attempt_count_per_work_unit" not in signals


def test_trace_aggregation_rejects_duplicate_requests(tmp_path) -> None:
    path = tmp_path / "duplicates.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "work_unit_success": 1,
    }
    encoded = json.dumps(_v2(row))
    path.write_text(f"{encoded}\n{encoded}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="duplicate"):
        aggregate_traces(path, workload=WORKLOAD)


def test_request_ids_cannot_be_reused_across_work_units(tmp_path) -> None:
    path = tmp_path / "cross-unit-duplicate.jsonl"
    base = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "offering_id": "provider/model@tier",
        "request_id": "provider-request-1",
        "attempt_id": "attempt-1",
        "work_unit_success": 1,
    }
    rows = [
        {**base, "work_unit_id": "unit-1"},
        {**base, "work_unit_id": "unit-2", "attempt_id": "attempt-2"},
    ]
    path.write_text("\n".join(json.dumps(_v2(row)) for row in rows) + "\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="duplicate"):
        aggregate_traces(path, workload=WORKLOAD)


def test_trace_aggregation_rejects_duplicate_attempt_aggregates(tmp_path) -> None:
    path = tmp_path / "duplicate-attempts.jsonl"
    base = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "attempt_id": "attempt-1",
        "observation_unit": "attempt",
        "work_unit_success": 1,
    }
    rows = [
        {**base, "request_id": "aggregate-record-1"},
        {**base, "request_id": "aggregate-record-2"},
    ]
    path.write_text("\n".join(json.dumps(_v2(row)) for row in rows) + "\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="duplicate attempt aggregates"):
        aggregate_traces(path, workload=WORKLOAD)


def test_trace_aggregation_rejects_overlapping_observation_scopes(tmp_path) -> None:
    path = tmp_path / "mixed-scopes.jsonl"
    base = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "attempt_id": "attempt-1",
        "work_unit_success": 1,
    }
    rows = [
        {**base, "request_id": "request-1", "observation_unit": "request"},
        {**base, "request_id": "aggregate-record-1", "observation_unit": "attempt"},
    ]
    path.write_text("\n".join(json.dumps(_v2(row)) for row in rows) + "\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="overlapping scopes"):
        aggregate_traces(path, workload=WORKLOAD)


def test_trace_aggregation_rejects_wrong_workload(tmp_path) -> None:
    path = tmp_path / "wrong-workload.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": "research-report-v1",
        "workload_version": "1.0.0",
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "work_unit_success": 1,
    }
    path.write_text(f"{json.dumps(_v2(row))}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="outside"):
        aggregate_traces(path, workload=WORKLOAD)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"timestamp": "2026-08-29T18:00:00"}, "RFC 3339"),
        ({"unexpected": "field"}, "Extra"),
        ({"other_cost_usd": "0.1234567890123"}, "decimal places"),
        ({"model_request_count": 2}, "implicit model_request_count"),
        ({"attempt_count": 2}, "only valid for work-unit"),
        (
            {"observation_unit": "attempt", "ttft_ms": 10},
            "only valid for request observations",
        ),
        (
            {
                "observation_unit": "attempt",
                "model_request_count": 0,
                "input_total_tokens": 1,
            },
            "zero model_request_count",
        ),
        ({"observation_unit": "session"}, "request.*model_call.*attempt.*work_unit"),
        (
            {"input_cache_write_tokens": 1, "input_cache_write_5m_tokens": 1},
            "mutually exclusive",
        ),
        (
            {
                "input_uncached_tokens": 5,
                "input_cache_read_tokens": 3,
                "input_cache_write_tokens": 2,
                "input_total_tokens": 11,
            },
            "input_total_tokens",
        ),
        (
            {"input_uncached_tokens": 5, "input_total_tokens": 3},
            "below known input components",
        ),
        (
            {
                "input_uncached_tokens": 2,
                "input_cache_read_tokens": 2,
                "input_total_tokens": 3,
            },
            "below known input components",
        ),
        (
            {"output_tokens": 5, "output_total_tokens": 3},
            "below known output components",
        ),
        (
            {"reasoning_tokens": 5, "output_total_tokens": 3},
            "below known output components",
        ),
        (
            {
                "input_uncached_tokens": "12345678901234567890123456789",
                "input_cache_read_tokens": 2,
                "input_total_tokens": "12345678901234567890123456790",
            },
            "below known input components",
        ),
        (
            {"output_tokens": 5, "reasoning_tokens": 3, "output_total_tokens": 9},
            "output_total_tokens",
        ),
    ],
)
def test_jsonl_rows_are_strictly_validated(tmp_path, mutation, message) -> None:
    path = tmp_path / "invalid.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "work_unit_success": 1,
        **mutation,
    }
    path.write_text(f"{json.dumps(_v2(row))}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match=message):
        aggregate_traces(path, workload=WORKLOAD)


def test_v1alpha2_jsonl_rejects_v1alpha3_model_call_scope(tmp_path) -> None:
    path = tmp_path / "v2-model-call.jsonl"
    row = _v2(
        {
            "timestamp": "2026-08-29T18:00:00Z",
            "workload_id": WORKLOAD.id,
            "workload_version": WORKLOAD.version,
            "work_unit_id": "unit-1",
            "offering_id": "provider/model@tier",
            "request_id": "logical-call-1",
            "attempt_id": "attempt-1",
            "observation_unit": "model_call",
            "work_unit_success": 1,
        }
    )
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="require request-trace v1alpha3"):
        aggregate_traces(path, workload=WORKLOAD)


@pytest.mark.parametrize(
    "versions",
    [
        (TRACE_V2, TRACE_V3),
        (TRACE_V3, "model-skyline/request-trace/v1alpha4"),
    ],
    ids=["mixed-supported", "unsupported"],
)
def test_jsonl_rejects_mixed_or_unsupported_schema_versions(tmp_path, versions) -> None:
    path = tmp_path / "mixed-schema.jsonl"
    base = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "offering_id": "provider/model@tier",
        "attempt_id": "attempt-1",
        "work_unit_success": 1,
    }
    rows = [
        {
            "schema_version": version,
            **base,
            "work_unit_id": f"unit-{index}",
            "request_id": f"request-{index}",
        }
        for index, version in enumerate(versions, start=1)
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="not valid canonical JSON"):
        aggregate_traces(path, workload=WORKLOAD)


def test_trace_validation_errors_do_not_echo_rejected_values_or_paths(tmp_path) -> None:
    secret = "sk-" + "proj-synthetic-secret-must-not-leak"
    path = tmp_path / "private-filename-must-not-leak.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "work_unit_success": 1,
        "unexpected_private_value": secret,
    }
    path.write_text(f"{json.dumps(_v2(row))}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError) as captured:
        aggregate_traces(path, workload=WORKLOAD)

    message = str(captured.value)
    assert secret not in message
    assert path.name not in message


def test_reviewed_producer_registry_supplies_public_source_metadata(tmp_path) -> None:
    path = tmp_path / "reviewed-producer.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "observation_unit": "attempt",
        "adapter_id": "model-skyline/codex-exec-jsonl",
        "adapter_version": "1",
        "upstream_system": "openai/codex",
        "upstream_version": "0.144.2",
        "upstream_commit": "a6645b6b8a656360fa16fb7e1c6721d0697d3d6a",
        "work_unit_success": 1,
    }
    path.write_text(f"{json.dumps(_v2(row))}\n", encoding="utf-8")

    summary = aggregate_traces(path, workload=WORKLOAD)

    assert len(summary.producer_sources) == 1
    producer = summary.producer_sources[0]
    assert producer.id == "producer:openai-codex:0.144.2"
    assert producer.license == "Apache-2.0"
    assert producer.url is not None
    assert producer.terms_url is not None
    assert producer.id in (summary.source.methodology or "")


def test_one_offering_cannot_blend_multiple_reviewed_producer_versions(tmp_path) -> None:
    path = tmp_path / "mixed-reviewed-producers.jsonl"
    base = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "offering_id": "provider/model@tier",
        "observation_unit": "attempt",
        "adapter_id": "model-skyline/codex-exec-jsonl",
        "adapter_version": "1",
        "upstream_system": "openai/codex",
        "work_unit_success": 1,
    }
    rows = [
        {
            **base,
            "work_unit_id": "unit-144",
            "request_id": "request-144",
            "attempt_id": "attempt-144",
            "upstream_version": "0.144.2",
            "upstream_commit": "a6645b6b8a656360fa16fb7e1c6721d0697d3d6a",
        },
        {
            **base,
            "work_unit_id": "unit-151",
            "request_id": "request-151",
            "attempt_id": "attempt-151",
            "upstream_version": "0.151.0",
            "upstream_commit": "78c290807ce710180111df227df3b7a4fe845452",
        },
    ]
    path.write_text(
        "".join(f"{json.dumps(_v2(row))}\n" for row in rows),
        encoding="utf-8",
    )

    with pytest.raises(TraceAggregationError, match="multiple producer identities"):
        aggregate_traces(path, workload=WORKLOAD)


def test_unreviewed_producer_provenance_is_rejected_without_echoing_it(tmp_path) -> None:
    secret = "sk-" + "proj-synthetic-producer-secret"
    path = tmp_path / "unreviewed-producer.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "observation_unit": "attempt",
        "adapter_id": secret,
        "adapter_version": "1",
        "upstream_system": "private/system",
        "upstream_version": "1",
        "upstream_commit": "a6645b6b8a656360fa16fb7e1c6721d0697d3d6a",
        "work_unit_success": 1,
    }
    path.write_text(f"{json.dumps(_v2(row))}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="reviewed registry") as captured:
        aggregate_traces(path, workload=WORKLOAD)

    assert secret not in str(captured.value)


def test_duplicate_json_keys_are_rejected_without_echoing_values(tmp_path) -> None:
    secret = "sk-" + "proj-duplicate-secret-must-not-leak"
    path = tmp_path / "duplicate-private-name.jsonl"
    path.write_text(
        "{"
        f'"schema_version":"{TRACE_V2}",'
        '"timestamp":"2026-08-29T18:00:00Z",'
        f'"workload_id":"{WORKLOAD.id}",'
        f'"workload_version":"{WORKLOAD.version}",'
        '"work_unit_id":"unit-1",'
        '"offering_id":"provider/model@tier",'
        '"request_id":"request-1",'
        f'"request_id":"{secret}",'
        '"attempt_id":"attempt-1",'
        '"work_unit_success":1'
        "}\n",
        encoding="utf-8",
    )

    with pytest.raises(TraceAggregationError, match="not valid canonical JSON") as captured:
        aggregate_traces(path, workload=WORKLOAD)

    assert secret not in str(captured.value)
    assert path.name not in str(captured.value)


def _write_parquet_trace(
    path,
    *,
    other_cost_type: str,
    timestamp_type: str = "TIMESTAMPTZ",
    timestamp_value: str = "2026-08-29T18:00:00Z",
) -> None:
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            f"""
            CREATE TABLE traces (
                timestamp {timestamp_type},
                workload_id VARCHAR,
                workload_version VARCHAR,
                work_unit_id VARCHAR,
                offering_id VARCHAR,
                request_id VARCHAR,
                attempt_id VARCHAR,
                work_unit_success DECIMAL(18,9),
                other_cost_usd {other_cost_type}
            )
            """
        )
        for index in range(3):
            connection.execute(
                """
                INSERT INTO traces VALUES (
                    ?, ?, ?, 'unit-1',
                    'provider/model@tier', ?, 'attempt-1', 1, 0.1
                )
                """,
                [timestamp_value, WORKLOAD.id, WORKLOAD.version, f"request-{index}"],
            )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()


def _write_versioned_parquet_trace(
    path: Path,
    rows: list[tuple[str | None, str]],
) -> None:
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE traces (
                schema_version VARCHAR,
                timestamp TIMESTAMPTZ,
                workload_id VARCHAR,
                workload_version VARCHAR,
                work_unit_id VARCHAR,
                offering_id VARCHAR,
                request_id VARCHAR,
                attempt_id VARCHAR,
                observation_unit VARCHAR,
                work_unit_success DECIMAL(18,9),
                input_uncached_tokens DECIMAL(38,9)
            )
            """
        )
        for index, (schema_version, observation_unit) in enumerate(rows, start=1):
            connection.execute(
                """
                INSERT INTO traces VALUES (
                    ?, TIMESTAMPTZ '2026-08-29T18:00:00Z', ?, ?, 'unit-1',
                    'provider/model@tier', ?, 'attempt-1', ?, 1, 10
                )
                """,
                [
                    schema_version,
                    WORKLOAD.id,
                    WORKLOAD.version,
                    f"record-{index}",
                    observation_unit,
                ],
            )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()


def test_parquet_decimal_meters_remain_exact(tmp_path) -> None:
    path = tmp_path / "exact.parquet"
    _write_parquet_trace(path, other_cost_type="DECIMAL(10,2)")

    summary = aggregate_traces(path, workload=WORKLOAD)

    assert summary.offerings["provider/model@tier"]["other_cost_usd_per_success"].value == Decimal(
        "0.30"
    )


def test_parquet_floating_point_meters_are_rejected(tmp_path) -> None:
    path = tmp_path / "float.parquet"
    _write_parquet_trace(path, other_cost_type="DOUBLE")

    with pytest.raises(TraceAggregationError, match="floating-point"):
        aggregate_traces(path, workload=WORKLOAD)


def test_parquet_naive_timestamps_are_rejected(tmp_path) -> None:
    path = tmp_path / "naive.parquet"
    _write_parquet_trace(
        path,
        other_cost_type="DECIMAL(10,2)",
        timestamp_type="TIMESTAMP",
    )

    with pytest.raises(TraceAggregationError, match="timezone-aware"):
        aggregate_traces(path, workload=WORKLOAD)


def test_parquet_timestamps_outside_python_range_are_safely_rejected(tmp_path) -> None:
    path = tmp_path / "far-future.parquet"
    _write_parquet_trace(
        path,
        other_cost_type="DECIMAL(10,2)",
        timestamp_value="10000-01-01T00:00:00Z",
    )

    with pytest.raises(TraceAggregationError, match="outside the supported range"):
        aggregate_traces(path, workload=WORKLOAD)


def test_empty_parquet_is_rejected_like_empty_jsonl(tmp_path) -> None:
    path = tmp_path / "empty.parquet"
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE traces (
                timestamp TIMESTAMPTZ,
                workload_id VARCHAR,
                workload_version VARCHAR,
                work_unit_id VARCHAR,
                offering_id VARCHAR,
                request_id VARCHAR,
                attempt_id VARCHAR,
                work_unit_success DECIMAL(18,9)
            )
            """
        )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    with pytest.raises(TraceAggregationError, match="no canonical usage rows"):
        aggregate_traces(path, workload=WORKLOAD)


def test_v1alpha3_parquet_preserves_model_call_scope_and_version(tmp_path) -> None:
    path = tmp_path / "v3-model-call.parquet"
    _write_versioned_parquet_trace(path, [(TRACE_V3, "model_call")])

    summary = aggregate_traces(path, workload=WORKLOAD)
    signals = summary.offerings["provider/model@tier"]

    assert summary.source.version == TRACE_V3
    assert signals["attempt_count_per_work_unit"].value == Decimal(1)
    assert "request_count_per_work_unit" not in signals


def test_v1alpha2_parquet_rejects_v1alpha3_model_call_scope(tmp_path) -> None:
    path = tmp_path / "v2-model-call.parquet"
    _write_versioned_parquet_trace(path, [(TRACE_V2, "model_call")])

    with pytest.raises(TraceAggregationError, match="invalid canonical usage rows"):
        aggregate_traces(path, workload=WORKLOAD)


@pytest.mark.parametrize(
    "rows",
    [
        [(TRACE_V2, "request"), (TRACE_V3, "request")],
        [("model-skyline/request-trace/v1alpha4", "request")],
        [(None, "request")],
    ],
    ids=["mixed-supported", "unsupported", "null"],
)
def test_parquet_rejects_mixed_null_or_unsupported_schema_versions(tmp_path, rows) -> None:
    path = tmp_path / "invalid-schema.parquet"
    _write_versioned_parquet_trace(path, rows)

    with pytest.raises(
        TraceAggregationError,
        match="mixed, null, or unsupported schema version",
    ):
        aggregate_traces(path, workload=WORKLOAD)


def test_present_null_parquet_observation_unit_is_not_an_implicit_request(tmp_path) -> None:
    path = tmp_path / "null-scope.parquet"
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE traces AS SELECT
                ?::VARCHAR AS schema_version,
                TIMESTAMPTZ '2026-08-29T18:00:00Z' AS timestamp,
                ?::VARCHAR AS workload_id,
                ?::VARCHAR AS workload_version,
                'unit-1'::VARCHAR AS work_unit_id,
                'provider/model@tier'::VARCHAR AS offering_id,
                'request-1'::VARCHAR AS request_id,
                'attempt-1'::VARCHAR AS attempt_id,
                NULL::VARCHAR AS observation_unit,
                1::DECIMAL(18,9) AS work_unit_success
            """,
            [TRACE_V2, WORKLOAD.id, WORKLOAD.version],
        )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    with pytest.raises(TraceAggregationError, match="invalid canonical usage rows"):
        aggregate_traces(path, workload=WORKLOAD)


def test_legacy_parquet_explicit_null_default_zero_meter_is_rejected(tmp_path) -> None:
    path = tmp_path / "legacy-null-meter.parquet"
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE traces AS SELECT
                TIMESTAMPTZ '2026-08-29T18:00:00Z' AS timestamp,
                ?::VARCHAR AS workload_id,
                ?::VARCHAR AS workload_version,
                'unit-1'::VARCHAR AS work_unit_id,
                'provider/model@tier'::VARCHAR AS offering_id,
                'request-1'::VARCHAR AS request_id,
                'attempt-1'::VARCHAR AS attempt_id,
                1::DECIMAL(18,9) AS work_unit_success,
                NULL::DECIMAL(38,9) AS input_uncached_tokens
            """,
            [WORKLOAD.id, WORKLOAD.version],
        )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    with pytest.raises(TraceAggregationError, match="legacy trace input contains null"):
        aggregate_traces(path, workload=WORKLOAD)


def test_parquet_unknown_column_names_are_not_echoed(tmp_path) -> None:
    secret_column = "sk_proj_synthetic_column_secret"
    path = tmp_path / "extra.parquet"
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            f"""
            CREATE TABLE traces AS SELECT
                TIMESTAMPTZ '2026-08-29T18:00:00Z' AS timestamp,
                ?::VARCHAR AS workload_id,
                ?::VARCHAR AS workload_version,
                'unit-1'::VARCHAR AS work_unit_id,
                'provider/model@tier'::VARCHAR AS offering_id,
                'request-1'::VARCHAR AS request_id,
                'attempt-1'::VARCHAR AS attempt_id,
                1::DECIMAL(18,9) AS work_unit_success,
                'private'::VARCHAR AS {secret_column}
            """,
            [WORKLOAD.id, WORKLOAD.version],
        )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    with pytest.raises(TraceAggregationError, match="unknown column") as captured:
        aggregate_traces(path, workload=WORKLOAD)
    assert secret_column not in str(captured.value)


def test_trace_summary_cannot_enrich_a_different_workload(tmp_path) -> None:
    path = tmp_path / "requests.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": "provider/model@tier",
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "work_unit_success": 1,
    }
    path.write_text(f"{json.dumps(_v2(row))}\n", encoding="utf-8")
    summary = aggregate_traces(path, workload=WORKLOAD)
    catalog = ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=WorkloadReference(id="other", version="1", unit="task"),
        offerings=[],
    )

    with pytest.raises(TraceAggregationError, match="does not match"):
        enrich_catalog(catalog, summary)


def test_enrichment_does_not_echo_unknown_trace_offering_ids(tmp_path) -> None:
    secret = "sk-" + "proj-unknown-offering-must-not-leak"
    path = tmp_path / "unknown-offering.jsonl"
    row = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "unit-1",
        "offering_id": secret,
        "request_id": "request-1",
        "attempt_id": "attempt-1",
        "work_unit_success": 1,
    }
    path.write_text(f"{json.dumps(_v2(row))}\n", encoding="utf-8")
    summary = aggregate_traces(path, workload=WORKLOAD)
    catalog = ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=WORKLOAD,
        offerings=[],
    )

    with pytest.raises(TraceAggregationError, match="absent from the catalog") as captured:
        enrich_catalog(catalog, summary)
    assert secret not in str(captured.value)


def test_literal_glob_metacharacters_resolve_only_the_exact_trace_file(tmp_path) -> None:
    if os.name == "nt":
        pytest.skip("Windows filenames cannot contain every glob metacharacter")
    path = tmp_path / "trace[ab]*?.jsonl"
    decoy = tmp_path / "tracea-decoy.jsonl"
    row = _v2(
        {
            "timestamp": "2026-08-29T18:00:00Z",
            "workload_id": WORKLOAD.id,
            "workload_version": WORKLOAD.version,
            "work_unit_id": "unit-exact",
            "offering_id": "provider/model@exact",
            "request_id": "request-exact",
            "attempt_id": "attempt-exact",
            "work_unit_success": 1,
        }
    )
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")
    decoy.write_text("not canonical JSONL\n", encoding="utf-8")

    summary = aggregate_traces(path, workload=WORKLOAD)

    expected_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert set(summary.offerings) == {"provider/model@exact"}
    assert summary.source.raw_sha256 == expected_digest


def test_fifo_trace_input_is_rejected_without_blocking(tmp_path) -> None:
    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("FIFO nonblocking semantics are not available")
    path = tmp_path / "trace.fifo"
    os.mkfifo(path)
    worker = """
import sys
from model_skyline.models import WorkloadReference
from model_skyline.traces import TraceAggregationError, aggregate_traces

workload = WorkloadReference(
    id="coding-session-v1",
    version="1.0.0",
    unit="successful_coding_session",
)
try:
    aggregate_traces(sys.argv[1], workload=workload)
except TraceAggregationError as exc:
    raise SystemExit(0 if str(exc) == "trace input must be a regular file" else 2) from None
raise SystemExit(3)
"""

    completed = subprocess.run(
        [sys.executable, "-c", worker, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0, (completed.stdout, completed.stderr)


def test_device_trace_input_is_rejected_as_non_regular() -> None:
    device = Path(os.devnull)
    try:
        mode = device.stat().st_mode
    except OSError:
        pytest.skip("platform null device is not stat-able")
    if not stat.S_ISCHR(mode):
        pytest.skip("platform null device is not a character device")

    with pytest.raises(TraceAggregationError, match="regular file"):
        aggregate_traces(device, workload=WORKLOAD)


def test_duckdb_uses_private_bounded_spill_and_execution_settings(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "bounded.jsonl"
    row = _v2(
        {
            "timestamp": "2026-08-29T18:00:00Z",
            "workload_id": WORKLOAD.id,
            "workload_version": WORKLOAD.version,
            "work_unit_id": "unit-1",
            "offering_id": "provider/model@tier",
            "request_id": "request-1",
            "attempt_id": "attempt-1",
            "work_unit_success": 1,
        }
    )
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")
    observed: dict[str, object] = {}
    original_relation = traces_module._relation

    def inspect_settings(connection, snapshot_path):
        for setting in (
            "memory_limit",
            "max_temp_directory_size",
            "preserve_insertion_order",
            "temp_directory",
            "threads",
        ):
            observed[setting] = connection.execute(
                "SELECT current_setting(?)", [setting]
            ).fetchone()[0]
        observed["snapshot_parent"] = snapshot_path.parent
        return original_relation(connection, snapshot_path)

    monkeypatch.setattr(traces_module, "_relation", inspect_settings)

    aggregate_traces(path, workload=WORKLOAD)

    spill_directory = Path(str(observed["temp_directory"]))
    assert spill_directory.is_absolute()
    assert spill_directory == observed["snapshot_parent"] / "duckdb-spill"
    assert spill_directory.parent != Path.cwd()
    assert not spill_directory.exists()
    assert observed["memory_limit"] == "256.0 MiB"
    assert observed["max_temp_directory_size"] == "512.0 MiB"
    assert observed["threads"] == 2
    assert observed["preserve_insertion_order"] is False


@pytest.mark.parametrize(
    ("limit_name", "expected_message"),
    [
        ("MAX_TRACE_OFFERINGS", "distinct offering limit"),
        ("MAX_TRACE_WORK_UNIT_GROUPS", "work-unit group limit"),
    ],
)
def test_trace_cardinality_limits_fail_before_result_materialization(
    tmp_path,
    monkeypatch,
    limit_name,
    expected_message,
) -> None:
    path = tmp_path / "cardinality.jsonl"
    rows = [
        _v2(
            {
                "timestamp": "2026-08-29T18:00:00Z",
                "workload_id": WORKLOAD.id,
                "workload_version": WORKLOAD.version,
                "work_unit_id": f"unit-{index}",
                "offering_id": f"provider/model-{index}@tier",
                "request_id": f"request-{index}",
                "attempt_id": f"attempt-{index}",
                "work_unit_success": 1,
            }
        )
        for index in range(2)
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(traces_module, limit_name, 1)

    with pytest.raises(TraceAggregationError, match=expected_message):
        aggregate_traces(path, workload=WORKLOAD)


def test_huge_json_integer_is_rejected_without_echoing_value_or_path(tmp_path) -> None:
    path = tmp_path / "huge-private-integer.jsonl"
    huge_integer = "9" * 1025
    base = json.dumps(
        _v2(
            {
                "timestamp": "2026-08-29T18:00:00Z",
                "workload_id": WORKLOAD.id,
                "workload_version": WORKLOAD.version,
                "work_unit_id": "unit-1",
                "offering_id": "provider/model@tier",
                "request_id": "request-1",
                "attempt_id": "attempt-1",
                "work_unit_success": 1,
            }
        )
    )
    path.write_text(
        f'{base[:-1]}, "model_request_count": {huge_integer}}}\n',
        encoding="utf-8",
    )

    with pytest.raises(TraceAggregationError, match="not valid canonical JSON") as captured:
        aggregate_traces(path, workload=WORKLOAD)

    message = str(captured.value)
    assert huge_integer not in message
    assert path.name not in message


def test_excessively_nested_json_is_rejected_without_echoing_value_or_path(tmp_path) -> None:
    path = tmp_path / "deep-private-value.jsonl"
    marker: object = "SYNTHETIC_DEEP_MARKER"
    for _ in range(70):
        marker = [marker]
    row = _v2(
        {
            "timestamp": "2026-08-29T18:00:00Z",
            "workload_id": WORKLOAD.id,
            "workload_version": WORKLOAD.version,
            "work_unit_id": "unit-1",
            "offering_id": "provider/model@tier",
            "request_id": "request-1",
            "attempt_id": "attempt-1",
            "work_unit_success": 1,
            "nested": marker,
        }
    )
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="not valid canonical JSON") as captured:
        aggregate_traces(path, workload=WORKLOAD)

    message = str(captured.value)
    assert "SYNTHETIC_DEEP_MARKER" not in message
    assert path.name not in message


@pytest.mark.parametrize("timestamp", [1_788_123_456, "2026-08-29"])
def test_numeric_and_date_only_json_timestamps_are_rejected(tmp_path, timestamp) -> None:
    path = tmp_path / "invalid-timestamp.jsonl"
    row = _v2(
        {
            "timestamp": timestamp,
            "workload_id": WORKLOAD.id,
            "workload_version": WORKLOAD.version,
            "work_unit_id": "unit-1",
            "offering_id": "provider/model@tier",
            "request_id": "request-1",
            "attempt_id": "attempt-1",
            "work_unit_success": 1,
        }
    )
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="RFC 3339") as captured:
        aggregate_traces(path, workload=WORKLOAD)

    assert str(timestamp) not in str(captured.value)


@pytest.mark.parametrize(
    "outcomes",
    [(1, 1), (1, 0)],
    ids=["consistent-outcome", "contradictory-outcome"],
)
def test_one_work_unit_cannot_span_multiple_offerings(tmp_path, outcomes) -> None:
    path = tmp_path / "multiple-offerings.jsonl"
    base = {
        "timestamp": "2026-08-29T18:00:00Z",
        "workload_id": WORKLOAD.id,
        "workload_version": WORKLOAD.version,
        "work_unit_id": "shared-unit",
        "attempt_id": "attempt-1",
    }
    rows = [
        _v2(
            {
                **base,
                "offering_id": "provider/model-a@tier",
                "request_id": "request-a",
                "work_unit_success": outcomes[0],
            }
        ),
        _v2(
            {
                **base,
                "offering_id": "provider/model-b@tier",
                "request_id": "request-b",
                "work_unit_success": outcomes[1],
            }
        ),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="spanning multiple offerings"):
        aggregate_traces(path, workload=WORKLOAD)


def test_zero_model_call_work_unit_can_retain_non_model_usage_and_costs(tmp_path) -> None:
    path = tmp_path / "zero-model-calls.jsonl"
    row = _v2(
        {
            "timestamp": "2026-08-29T18:00:00Z",
            "workload_id": WORKLOAD.id,
            "workload_version": WORKLOAD.version,
            "work_unit_id": "tool-only-unit",
            "offering_id": "provider/no-model@tool-only",
            "request_id": "aggregate-record-1",
            "attempt_id": "attempt-1",
            "observation_unit": "work_unit",
            "model_request_count": 0,
            "attempt_count": 1,
            "work_unit_success": 1,
            "cache_storage_token_hours": "12.5",
            "tool_calls": 3,
            "web_search_calls": 2,
            "sandbox_seconds": 15,
            "other_cost_usd": "0.25",
            "billed_total_cost_usd": "1.75",
        }
    )
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")

    summary = aggregate_traces(path, workload=WORKLOAD)
    signals = summary.offerings["provider/no-model@tool-only"]

    assert signals["request_count_per_work_unit"].value == Decimal(0)
    assert signals["attempt_count_per_work_unit"].value == Decimal(1)
    assert signals["cache_storage_token_hours_per_work_unit"].value == Decimal("12.5")
    assert signals["tool_calls_per_work_unit"].value == Decimal(3)
    assert signals["billed_total_cost_usd_per_work_unit"].value == Decimal("1.75")
    assert "input_total_tokens_per_work_unit" not in signals
    assert "output_total_tokens_per_work_unit" not in signals


@pytest.mark.parametrize(
    "meter",
    [
        "input_uncached_tokens",
        "input_cache_read_tokens",
        "input_cache_write_tokens",
        "input_cache_write_5m_tokens",
        "input_cache_write_1h_tokens",
        "input_total_tokens",
        "output_tokens",
        "reasoning_tokens",
        "output_total_tokens",
    ],
)
def test_zero_model_call_work_unit_rejects_nonzero_model_tokens(tmp_path, meter) -> None:
    path = tmp_path / "impossible-zero-model-calls.jsonl"
    row = _v2(
        {
            "timestamp": "2026-08-29T18:00:00Z",
            "workload_id": WORKLOAD.id,
            "workload_version": WORKLOAD.version,
            "work_unit_id": "unit-1",
            "offering_id": "provider/model@tier",
            "request_id": "aggregate-record-1",
            "attempt_id": "attempt-1",
            "observation_unit": "work_unit",
            "model_request_count": 0,
            "attempt_count": 1,
            "work_unit_success": 0,
            meter: 1,
        }
    )
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="zero model_request_count"):
        aggregate_traces(path, workload=WORKLOAD)


def test_modern_parquet_preserves_scope_counts_and_token_totals(tmp_path) -> None:
    path = tmp_path / "modern-scope.parquet"
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE traces AS SELECT
                ?::VARCHAR AS schema_version,
                TIMESTAMPTZ '2026-08-29T18:00:00Z' AS timestamp,
                ?::VARCHAR AS workload_id,
                ?::VARCHAR AS workload_version,
                'unit-1'::VARCHAR AS work_unit_id,
                'provider/model@tier'::VARCHAR AS offering_id,
                'aggregate-record-1'::VARCHAR AS request_id,
                'attempt-record-1'::VARCHAR AS attempt_id,
                'work_unit'::VARCHAR AS observation_unit,
                7::BIGINT AS model_request_count,
                2::BIGINT AS attempt_count,
                1::DECIMAL(18,9) AS work_unit_success,
                100::DECIMAL(38,9) AS input_uncached_tokens,
                900::DECIMAL(38,9) AS input_cache_read_tokens,
                50::DECIMAL(38,9) AS input_cache_write_tokens,
                1050::DECIMAL(38,9) AS input_total_tokens,
                60::DECIMAL(38,9) AS output_tokens,
                40::DECIMAL(38,9) AS reasoning_tokens,
                100::DECIMAL(38,9) AS output_total_tokens
            """,
            [TRACE_V2, WORKLOAD.id, WORKLOAD.version],
        )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    summary = aggregate_traces(path, workload=WORKLOAD)
    signals = summary.offerings["provider/model@tier"]

    assert summary.source.version == TRACE_V2
    assert signals["request_count_per_work_unit"].value == Decimal(7)
    assert signals["attempt_count_per_work_unit"].value == Decimal(2)
    assert signals["input_total_tokens_per_work_unit"].value == Decimal(1050)
    assert signals["output_tokens_per_work_unit"].value == Decimal(60)
    assert signals["reasoning_tokens_per_work_unit"].value == Decimal(40)
    assert signals["output_total_tokens_per_work_unit"].value == Decimal(100)


def test_modern_parquet_preserves_request_timing(tmp_path) -> None:
    path = tmp_path / "modern-timing.parquet"
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE traces AS SELECT
                ?::VARCHAR AS schema_version,
                TIMESTAMPTZ '2026-08-29T18:00:00Z' AS timestamp,
                ?::VARCHAR AS workload_id,
                ?::VARCHAR AS workload_version,
                'unit-1'::VARCHAR AS work_unit_id,
                'provider/model@tier'::VARCHAR AS offering_id,
                'request-1'::VARCHAR AS request_id,
                'attempt-1'::VARCHAR AS attempt_id,
                'request'::VARCHAR AS observation_unit,
                1::BIGINT AS model_request_count,
                1::DECIMAL(18,9) AS work_unit_success,
                125::DECIMAL(38,9) AS ttft_ms,
                40::DECIMAL(38,9) AS output_tokens_per_second
            """,
            [TRACE_V2, WORKLOAD.id, WORKLOAD.version],
        )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    summary = aggregate_traces(path, workload=WORKLOAD)
    signals = summary.offerings["provider/model@tier"]

    assert signals["request_count_per_work_unit"].value == Decimal(1)
    assert signals["ttft_p50_ms"].value == Decimal(125)
    assert signals["output_tokens_per_second_p50"].value == Decimal(40)


def test_modern_parquet_control_characters_are_rejected_without_echo(tmp_path) -> None:
    path = tmp_path / "modern-control.parquet"
    controlled_value = "unit-safe-prefix\x01private-suffix"
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE traces AS SELECT
                ?::VARCHAR AS schema_version,
                TIMESTAMPTZ '2026-08-29T18:00:00Z' AS timestamp,
                ?::VARCHAR AS workload_id,
                ?::VARCHAR AS workload_version,
                ?::VARCHAR AS work_unit_id,
                'provider/model@tier'::VARCHAR AS offering_id,
                'request-1'::VARCHAR AS request_id,
                'attempt-1'::VARCHAR AS attempt_id,
                'request'::VARCHAR AS observation_unit,
                1::DECIMAL(18,9) AS work_unit_success
            """,
            [TRACE_V2, WORKLOAD.id, WORKLOAD.version, controlled_value],
        )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    with pytest.raises(TraceAggregationError, match="invalid canonical usage rows") as captured:
        aggregate_traces(path, workload=WORKLOAD)

    assert controlled_value not in str(captured.value)


@pytest.mark.parametrize(
    (
        "observation_unit",
        "model_request_count",
        "ttft_ms",
        "input_uncached_tokens",
        "input_total_tokens",
    ),
    [
        ("work_unit", 1, 10, None, None),
        ("request", 2, None, None, None),
        ("request", 1, None, 5, 3),
    ],
    ids=["aggregate-timing", "request-count", "input-total"],
)
def test_modern_parquet_enforces_scope_count_and_total_coherence(
    tmp_path,
    observation_unit,
    model_request_count,
    ttft_ms,
    input_uncached_tokens,
    input_total_tokens,
) -> None:
    path = tmp_path / "modern-invalid.parquet"
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE traces AS SELECT
                ?::VARCHAR AS schema_version,
                TIMESTAMPTZ '2026-08-29T18:00:00Z' AS timestamp,
                ?::VARCHAR AS workload_id,
                ?::VARCHAR AS workload_version,
                'unit-1'::VARCHAR AS work_unit_id,
                'provider/model@tier'::VARCHAR AS offering_id,
                'record-1'::VARCHAR AS request_id,
                'attempt-1'::VARCHAR AS attempt_id,
                ?::VARCHAR AS observation_unit,
                ?::BIGINT AS model_request_count,
                0::DECIMAL(18,9) AS work_unit_success,
                ?::DECIMAL(38,9) AS ttft_ms,
                ?::DECIMAL(38,9) AS input_uncached_tokens,
                ?::DECIMAL(38,9) AS input_total_tokens
            """,
            [
                TRACE_V2,
                WORKLOAD.id,
                WORKLOAD.version,
                observation_unit,
                model_request_count,
                ttft_ms,
                input_uncached_tokens,
                input_total_tokens,
            ],
        )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    with pytest.raises(TraceAggregationError, match="invalid canonical usage rows"):
        aggregate_traces(path, workload=WORKLOAD)
