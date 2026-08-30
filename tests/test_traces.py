from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import duckdb
import pytest
from pydantic import ValidationError

from model_skyline.models import ObservationCatalog, WorkloadReference
from model_skyline.traces import (
    TRACE_CLASSIFICATION_STRUCT,
    RequestTrace,
    TraceAggregationError,
    aggregate_traces,
    enrich_catalog,
)

WORKLOAD = WorkloadReference(
    id="coding-session-v1",
    version="1.0.0",
    unit="successful_coding_session",
)


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
            "output_tokens": 20,
            "tool_calls": 2,
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
            "output_tokens": 10,
            "tool_calls": 1,
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
            "input_cache_write_5m_tokens": 200,
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
    path.write_text(f"{json.dumps(row)}\n{json.dumps(row)}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="duplicate"):
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
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="outside"):
        aggregate_traces(path, workload=WORKLOAD)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"timestamp": "2026-08-29T18:00:00"}, "timezone"),
        ({"unexpected": "field"}, "extra"),
        ({"other_cost_usd": "0.1234567890123"}, "decimal places"),
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
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match=message):
        aggregate_traces(path, workload=WORKLOAD)


def _write_parquet_trace(
    path,
    *,
    other_cost_type: str,
    timestamp_type: str = "TIMESTAMPTZ",
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
                    '2026-08-29T18:00:00Z', ?, ?, 'unit-1',
                    'provider/model@tier', ?, 'attempt-1', 1, 0.1
                )
                """,
                [WORKLOAD.id, WORKLOAD.version, f"request-{index}"],
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
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")
    summary = aggregate_traces(path, workload=WORKLOAD)
    catalog = ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=WorkloadReference(id="other", version="1", unit="task"),
        offerings=[],
    )

    with pytest.raises(TraceAggregationError, match="does not match"):
        enrich_catalog(catalog, summary)


# --- task-classification contract (ADR 0002, issue #1 rulings) ---


CLASSIFICATION = {
    "class_id": "openclaw/coding/repo-change",
    "source": {
        "method": "registered_classifier",
        "id": "openclaw-task-classifier",
        "version": "1.2.0",
        "sha256": "a" * 64,
    },
    "confidence": "0.5",
}


def _classification_row(**overrides) -> dict:
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
    row.update(overrides)
    return row


def test_classification_object_validates_and_defaults_absent() -> None:
    unclassified = RequestTrace.model_validate(_classification_row())
    assert unclassified.trace_classification is None

    classified = RequestTrace.model_validate(
        _classification_row(trace_classification=CLASSIFICATION)
    )
    assert classified.trace_classification is not None
    assert classified.trace_classification.class_id == "openclaw/coding/repo-change"
    assert classified.trace_classification.source.method.value == "registered_classifier"
    assert classified.trace_classification.confidence == Decimal("0.5")


def test_classification_confidence_boundaries_and_canonicalization() -> None:
    for confidence, expected in (
        ("0", Decimal("0")),
        ("1", Decimal("1")),
        ("0.500000", Decimal("0.5")),
    ):
        payload = dict(CLASSIFICATION, confidence=confidence)
        trace = RequestTrace.model_validate(_classification_row(trace_classification=payload))
        assert trace.trace_classification is not None
        assert trace.trace_classification.confidence == expected

    operator_payload = {
        "class_id": "openclaw/ops",
        "source": {"method": "operator"},
        "confidence": "1",
    }
    trace = RequestTrace.model_validate(_classification_row(trace_classification=operator_payload))
    assert trace.trace_classification is not None
    assert trace.trace_classification.confidence == Decimal("1")


@pytest.mark.parametrize(
    "confidence",
    ["NaN", "Infinity", "-Infinity", "-0.000000001", "1.000000001", "2"],
)
def test_classification_confidence_out_of_range_rejected(confidence) -> None:
    payload = dict(CLASSIFICATION, confidence=confidence)
    with pytest.raises(ValidationError):
        RequestTrace.model_validate(_classification_row(trace_classification=payload))


@pytest.mark.parametrize(
    "class_id",
    [
        "Openclaw/coding",
        "openclaw/Coding",
        "/openclaw/coding",
        "openclaw/",
        "openclaw//coding",
        "openclaw/coding/repo/change/two/three",
        "",
        "x" * 129,
    ],
)
def test_classification_class_id_pattern_rejected(class_id) -> None:
    payload = dict(CLASSIFICATION, class_id=class_id)
    with pytest.raises(ValidationError):
        RequestTrace.model_validate(_classification_row(trace_classification=payload))


def test_classification_class_id_accepts_namespaced_shapes() -> None:
    for class_id in ("openclaw/coding", "openclaw/coding/repo-change", "m" * 128):
        payload = dict(CLASSIFICATION, class_id=class_id)
        trace = RequestTrace.model_validate(_classification_row(trace_classification=payload))
        assert trace.trace_classification is not None
        assert trace.trace_classification.class_id == class_id


@pytest.mark.parametrize(
    "method, expects_identity",
    [
        ("harness_tag", False),
        ("operator", False),
        ("registered_classifier", True),
        ("oracle", True),
    ],
)
def test_classification_source_identity_rules(method, expects_identity) -> None:
    bare = {"method": method}
    if expects_identity:
        with pytest.raises(ValidationError, match="requires"):
            RequestTrace.model_validate(
                _classification_row(trace_classification=dict(CLASSIFICATION, source=bare))
            )
        identified = {"method": method, "id": "classifier", "version": "2.0.0", "sha256": "b" * 64}
        trace = RequestTrace.model_validate(
            _classification_row(trace_classification=dict(CLASSIFICATION, source=identified))
        )
        assert trace.trace_classification is not None
        assert trace.trace_classification.source.sha256 == "b" * 64
    else:
        trace = RequestTrace.model_validate(
            _classification_row(trace_classification=dict(CLASSIFICATION, source=bare))
        )
        assert trace.trace_classification is not None
        assert trace.trace_classification.source.id is None


def test_classification_source_sha256_pattern_rejected() -> None:
    payload = dict(CLASSIFICATION)
    payload["source"] = dict(CLASSIFICATION["source"], sha256="ZZ")
    with pytest.raises(ValidationError):
        RequestTrace.model_validate(_classification_row(trace_classification=payload))


@pytest.mark.parametrize("missing", ["class_id", "source", "confidence"])
def test_classification_partial_object_rejected(missing) -> None:
    payload = {k: v for k, v in CLASSIFICATION.items() if k != missing}
    with pytest.raises(ValidationError):
        RequestTrace.model_validate(_classification_row(trace_classification=payload))


def test_classification_forbids_unknown_members() -> None:
    payload = dict(CLASSIFICATION, rationale="why")
    with pytest.raises(ValidationError):
        RequestTrace.model_validate(_classification_row(trace_classification=payload))


def test_jsonl_round_trip_preserves_classification(tmp_path) -> None:
    path = tmp_path / "classified.jsonl"
    rows = [
        _classification_row(
            request_id="request-1",
            trace_classification=CLASSIFICATION,
        ),
        _classification_row(request_id="request-2", work_unit_id="unit-2"),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    summary = aggregate_traces(path, workload=WORKLOAD)

    assert summary.offerings["provider/model@tier"]["successful_work_units"].value == Decimal("2")


def test_parquet_round_trip_preserves_classification(tmp_path) -> None:
    parquet = tmp_path / "classified.parquet"
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            f"""
            CREATE TABLE traces (
                timestamp TIMESTAMPTZ,
                workload_id VARCHAR,
                workload_version VARCHAR,
                work_unit_id VARCHAR,
                offering_id VARCHAR,
                request_id VARCHAR,
                attempt_id VARCHAR,
                work_unit_success DECIMAL(18,9),
                trace_classification {TRACE_CLASSIFICATION_STRUCT}
            )
            """
        )
        connection.execute(
            """
            INSERT INTO traces VALUES (
                '2026-08-29T18:00:00Z', ?, ?, 'unit-1', 'provider/model@tier',
                'request-1', 'attempt-1', 1,
                {'class_id': 'openclaw/coding', source: {'method': 'operator'},
                 confidence: 0.5::DECIMAL(18,9)}
            )
            """,
            [WORKLOAD.id, WORKLOAD.version],
        )
        connection.execute(
            """
            INSERT INTO traces VALUES (
                '2026-08-29T18:01:00Z', ?, ?, 'unit-2', 'provider/model@tier',
                'request-2', 'attempt-1', 1, NULL
            )
            """,
            [WORKLOAD.id, WORKLOAD.version],
        )
        connection.execute(f"COPY traces TO '{parquet}' (FORMAT PARQUET)")
    finally:
        connection.close()

    summary = aggregate_traces(parquet, workload=WORKLOAD)

    assert summary.offerings["provider/model@tier"]["successful_work_units"].value == Decimal("2")


def test_parquet_classification_confidence_scale_capped(tmp_path) -> None:
    path = tmp_path / "wide-scale.parquet"
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
                work_unit_success DECIMAL(18,9),
                trace_classification STRUCT(
                    class_id VARCHAR,
                    "source" STRUCT(
                        "method" VARCHAR, id VARCHAR, "version" VARCHAR, sha256 VARCHAR
                    ),
                    confidence DECIMAL(18,12)
                )
            )
            """
        )
        connection.execute(
            """
            INSERT INTO traces VALUES (
                '2026-08-29T18:00:00Z', ?, ?, 'unit-1', 'provider/model@tier',
                'request-1', 'attempt-1', 1, NULL
            )
            """,
            [WORKLOAD.id, WORKLOAD.version],
        )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    with pytest.raises(TraceAggregationError, match="scale"):
        aggregate_traces(path, workload=WORKLOAD)


def test_parquet_non_struct_classification_rejected(tmp_path) -> None:
    path = tmp_path / "wrong-type.parquet"
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
                work_unit_success DECIMAL(18,9),
                trace_classification VARCHAR
            )
            """
        )
        connection.execute(
            """
            INSERT INTO traces VALUES (
                '2026-08-29T18:00:00Z', ?, ?, 'unit-1', 'provider/model@tier',
                'request-1', 'attempt-1', 1, 'openclaw/coding'
            )
            """,
            [WORKLOAD.id, WORKLOAD.version],
        )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    with pytest.raises(TraceAggregationError, match="canonical classification"):
        aggregate_traces(path, workload=WORKLOAD)


def test_jsonl_partial_classification_row_rejected(tmp_path) -> None:
    path = tmp_path / "partial.jsonl"
    row = _classification_row(
        trace_classification={"class_id": "openclaw/coding", "confidence": "0.5"}
    )
    path.write_text(f"{json.dumps(row)}\n", encoding="utf-8")

    with pytest.raises(TraceAggregationError, match="not a canonical request trace"):
        aggregate_traces(path, workload=WORKLOAD)


def test_parquet_partial_classification_row_rejected(tmp_path) -> None:
    path = tmp_path / "partial.parquet"
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            f"""
            CREATE TABLE traces (
                timestamp TIMESTAMPTZ,
                workload_id VARCHAR,
                workload_version VARCHAR,
                work_unit_id VARCHAR,
                offering_id VARCHAR,
                request_id VARCHAR,
                attempt_id VARCHAR,
                work_unit_success DECIMAL(18,9),
                trace_classification {TRACE_CLASSIFICATION_STRUCT}
            )
            """
        )
        connection.execute(
            """
            INSERT INTO traces VALUES (
                '2026-08-29T18:00:00Z', ?, ?, 'unit-1', 'provider/model@tier',
                'request-1', 'attempt-1', 1,
                {'class_id': 'openclaw/coding', source: NULL, confidence: 0.5::DECIMAL(18,9)}
            )
            """,
            [WORKLOAD.id, WORKLOAD.version],
        )
        connection.execute(f"COPY traces TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    with pytest.raises(TraceAggregationError, match="invalid canonical trace rows"):
        aggregate_traces(path, workload=WORKLOAD)
