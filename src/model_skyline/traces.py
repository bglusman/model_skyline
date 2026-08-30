from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Self

import duckdb
from pydantic import Field, ValidationError, field_validator, model_validator

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT
from model_skyline.models import (
    CanonicalDecimal,
    FrozenModel,
    Observation,
    ObservationCatalog,
    OfferingObservation,
    SourceReference,
    WorkloadReference,
)

TRACE_CLASS_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]*(/[a-z0-9][a-z0-9_-]*){0,4}$"
MAX_TRACE_CLASS_ID_LENGTH = 128

TraceClassId = Annotated[
    str,
    Field(pattern=TRACE_CLASS_ID_PATTERN, max_length=MAX_TRACE_CLASS_ID_LENGTH),
]


class TraceClassificationMethod(StrEnum):
    """How a trace's task class was decided (ADR 0002)."""

    HARNESS_TAG = "harness_tag"
    OPERATOR = "operator"
    REGISTERED_CLASSIFIER = "registered_classifier"
    ORACLE = "oracle"


class TraceClassificationSource(FrozenModel):
    """Versioned provenance for one task-class decision.

    Deterministic classifiers are registered code referenced by ``id`` and
    ``version`` (with ``sha256`` digest when available); model-based
    classifiers must be versioned oracles behind the oracle boundary. Only
    code and version identifiers are recorded here — never executable logic.
    """

    method: TraceClassificationMethod
    id: str | None = Field(default=None, min_length=1)
    version: str | None = Field(default=None, min_length=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def versioned_methods_require_identity(self) -> Self:
        if self.method in {
            TraceClassificationMethod.REGISTERED_CLASSIFIER,
            TraceClassificationMethod.ORACLE,
        }:
            missing = sorted(
                name
                for name, value in (("id", self.id), ("version", self.version))
                if value is None
            )
            if missing:
                raise ValueError(
                    f"source method {self.method.value!r} requires: {', '.join(missing)}"
                )
        return self


class TraceClassification(FrozenModel):
    """Optional task-class annotation on a request trace (ADR 0002).

    The object is present-or-absent as a unit; inside it every member is
    required so no incoherent partial state (source without class, class
    without confidence) can be expressed.
    """

    class_id: TraceClassId
    source: TraceClassificationSource
    confidence: CanonicalDecimal = Field(ge=0, le=1, max_digits=18, decimal_places=9)


class RequestTrace(FrozenModel):
    """Canonical request-level trace row consumed by the DuckDB aggregator."""

    timestamp: datetime
    workload_id: str = Field(min_length=1)
    workload_version: str = Field(min_length=1)
    work_unit_id: str = Field(min_length=1)
    offering_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    work_unit_success: CanonicalDecimal = Field(ge=0, le=1, max_digits=18, decimal_places=9)
    input_uncached_tokens: CanonicalDecimal = Field(
        default=Decimal(0), ge=0, max_digits=38, decimal_places=9
    )
    input_cache_read_tokens: CanonicalDecimal = Field(
        default=Decimal(0), ge=0, max_digits=38, decimal_places=9
    )
    input_cache_write_5m_tokens: CanonicalDecimal = Field(
        default=Decimal(0), ge=0, max_digits=38, decimal_places=9
    )
    input_cache_write_1h_tokens: CanonicalDecimal = Field(
        default=Decimal(0), ge=0, max_digits=38, decimal_places=9
    )
    cache_storage_token_hours: CanonicalDecimal = Field(
        default=Decimal(0), ge=0, max_digits=38, decimal_places=9
    )
    output_tokens: CanonicalDecimal = Field(
        default=Decimal(0), ge=0, max_digits=38, decimal_places=9
    )
    reasoning_tokens: CanonicalDecimal = Field(
        default=Decimal(0), ge=0, max_digits=38, decimal_places=9
    )
    tool_calls: CanonicalDecimal = Field(default=Decimal(0), ge=0, max_digits=38, decimal_places=9)
    web_search_calls: CanonicalDecimal = Field(
        default=Decimal(0), ge=0, max_digits=38, decimal_places=9
    )
    sandbox_seconds: CanonicalDecimal = Field(
        default=Decimal(0), ge=0, max_digits=38, decimal_places=9
    )
    other_cost_usd: CanonicalDecimal = Field(
        default=Decimal(0), ge=0, max_digits=38, decimal_places=12
    )
    ttft_ms: CanonicalDecimal | None = Field(default=None, ge=0, max_digits=38, decimal_places=9)
    output_tokens_per_second: CanonicalDecimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=9
    )
    trace_classification: TraceClassification | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value


TRACE_CLASSIFICATION_STRUCT = (
    'STRUCT(class_id VARCHAR, "source" STRUCT("method" VARCHAR, id VARCHAR, '
    '"version" VARCHAR, sha256 VARCHAR), confidence DECIMAL(18,9))'
)
# DuckDB renders reserved member names with quotes; the schema of an accepted
# Parquet STRUCT may only differ from the canonical form in the precision and
# scale of the confidence member (scale at most 9, like every other meter).
TRACE_CLASSIFICATION_STRUCT_RE = re.compile(
    r'^STRUCT\(class_id VARCHAR, "source" STRUCT\("method" VARCHAR, id VARCHAR, '
    r'"version" VARCHAR, sha256 VARCHAR\), confidence DECIMAL\((\d+),(\d+)\)\)$'
)

TRACE_COLUMNS = {
    "timestamp": "TIMESTAMPTZ",
    "workload_id": "VARCHAR",
    "workload_version": "VARCHAR",
    "work_unit_id": "VARCHAR",
    "offering_id": "VARCHAR",
    "request_id": "VARCHAR",
    "attempt_id": "VARCHAR",
    "work_unit_success": "DECIMAL(18,9)",
    "input_uncached_tokens": "DECIMAL(38,9)",
    "input_cache_read_tokens": "DECIMAL(38,9)",
    "input_cache_write_5m_tokens": "DECIMAL(38,9)",
    "input_cache_write_1h_tokens": "DECIMAL(38,9)",
    "cache_storage_token_hours": "DECIMAL(38,9)",
    "output_tokens": "DECIMAL(38,9)",
    "reasoning_tokens": "DECIMAL(38,9)",
    "tool_calls": "DECIMAL(38,9)",
    "web_search_calls": "DECIMAL(38,9)",
    "sandbox_seconds": "DECIMAL(38,9)",
    "other_cost_usd": "DECIMAL(38,12)",
    "ttft_ms": "DECIMAL(38,9)",
    "output_tokens_per_second": "DECIMAL(38,9)",
    "trace_classification": TRACE_CLASSIFICATION_STRUCT,
}

REQUIRED_TRACE_COLUMNS = frozenset(
    {
        "timestamp",
        "workload_id",
        "workload_version",
        "work_unit_id",
        "offering_id",
        "request_id",
        "attempt_id",
        "work_unit_success",
    }
)
DECIMAL_SCALES = {
    "work_unit_success": 9,
    "input_uncached_tokens": 9,
    "input_cache_read_tokens": 9,
    "input_cache_write_5m_tokens": 9,
    "input_cache_write_1h_tokens": 9,
    "cache_storage_token_hours": 9,
    "output_tokens": 9,
    "reasoning_tokens": 9,
    "tool_calls": 9,
    "web_search_calls": 9,
    "sandbox_seconds": 9,
    "other_cost_usd": 12,
    "ttft_ms": 9,
    "output_tokens_per_second": 9,
}
DECIMAL_TYPE_RE = re.compile(r"^DECIMAL\((\d+),(\d+)\)$")
INTEGER_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
    }
)

QUANTITY_UNITS = {
    "request_count": "requests",
    "attempt_count": "attempts",
    "input_uncached_tokens": "tokens",
    "input_cache_read_tokens": "tokens",
    "input_cache_write_5m_tokens": "tokens",
    "input_cache_write_1h_tokens": "tokens",
    "cache_storage_token_hours": "token_hours",
    "output_tokens": "tokens",
    "reasoning_tokens": "tokens",
    "tool_calls": "calls",
    "web_search_calls": "calls",
    "sandbox_seconds": "seconds",
    "other_cost_usd": "USD",
}


class TraceAggregationError(ValueError):
    pass


class TraceSummary(FrozenModel):
    """Workload-bound aggregate observations derived from one immutable source file."""

    workload: WorkloadReference
    source: SourceReference
    offerings: dict[str, dict[str, Observation]]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_json_lines(path: Path) -> int:
    row_count = 0
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                row_count += 1
                try:
                    payload = json.loads(
                        line,
                        parse_float=Decimal,
                        parse_constant=Decimal,
                    )
                    RequestTrace.model_validate(payload)
                except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                    raise TraceAggregationError(
                        f"{path}:{line_number} is not a canonical request trace: {exc}"
                    ) from exc
    except UnicodeDecodeError as exc:
        raise TraceAggregationError(f"{path} is not valid UTF-8 JSONL: {exc}") from exc
    if row_count == 0:
        raise TraceAggregationError(f"{path} contains no canonical request traces")
    return row_count


def _canonical_parquet_relation(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
) -> Any:
    relation = connection.read_parquet(str(path))
    columns = relation.columns
    column_types = {
        name: str(data_type) for name, data_type in zip(columns, relation.types, strict=True)
    }
    missing = sorted(REQUIRED_TRACE_COLUMNS - set(columns))
    extras = sorted(set(columns) - set(TRACE_COLUMNS))
    if missing:
        raise TraceAggregationError(
            f"{path} is missing required trace columns: {', '.join(missing)}"
        )
    if extras:
        raise TraceAggregationError(f"{path} has unknown trace columns: {', '.join(extras)}")

    projections: list[str] = []
    for name, target_type in TRACE_COLUMNS.items():
        source_type = column_types.get(name)
        if source_type is None:
            projections.append(f'NULL::{target_type} AS "{name}"')
            continue
        if name == "timestamp":
            if source_type != "TIMESTAMP WITH TIME ZONE":
                raise TraceAggregationError(
                    f"{path} column timestamp must be timezone-aware, got {source_type}"
                )
        elif target_type == "VARCHAR":
            if source_type != "VARCHAR":
                raise TraceAggregationError(
                    f"{path} column {name} must be VARCHAR, got {source_type}"
                )
        elif name == "trace_classification":
            struct_match = TRACE_CLASSIFICATION_STRUCT_RE.fullmatch(source_type or "")
            if struct_match is None:
                raise TraceAggregationError(
                    f"{path} column {name} must be the canonical classification "
                    f"STRUCT, got {source_type}"
                )
            if int(struct_match.group(2)) > 9:
                raise TraceAggregationError(
                    f"{path} column {name} confidence member has scale {struct_match.group(2)}; "
                    f"maximum is 9"
                )
        else:
            decimal_match = DECIMAL_TYPE_RE.fullmatch(source_type)
            if decimal_match is not None:
                source_scale = int(decimal_match.group(2))
                if source_scale > DECIMAL_SCALES[name]:
                    raise TraceAggregationError(
                        f"{path} column {name} has scale {source_scale}; "
                        f"maximum is {DECIMAL_SCALES[name]}"
                    )
            elif source_type not in INTEGER_TYPES:
                raise TraceAggregationError(
                    f"{path} column {name} must use an exact integer or DECIMAL type, "
                    f"got {source_type}; floating-point trace meters are not accepted"
                )
        projections.append(f'CAST("{name}" AS {target_type}) AS "{name}"')
    return relation.project(", ".join(projections))


def _relation(connection: duckdb.DuckDBPyConnection, path: Path) -> Any:
    suffix = path.suffix.lower()
    try:
        if suffix in {".parquet", ".pq"}:
            return _canonical_parquet_relation(connection, path)
        _validate_json_lines(path)
        return connection.read_json(
            str(path),
            columns=TRACE_COLUMNS,
            format="newline_delimited",
        )
    except duckdb.Error as exc:
        raise TraceAggregationError(f"cannot read trace file {path}: {exc}") from exc


AGGREGATION_SQL = """
WITH per_work_unit AS (
    SELECT
        workload_id,
        workload_version,
        offering_id,
        work_unit_id,
        count(*)::DECIMAL(38,9) AS request_count,
        count(DISTINCT attempt_id)::DECIMAL(38,9) AS attempt_count,
        max(work_unit_success) AS success_weight,
        sum(coalesce(input_uncached_tokens, 0)) AS input_uncached_tokens,
        sum(coalesce(input_cache_read_tokens, 0)) AS input_cache_read_tokens,
        sum(coalesce(input_cache_write_5m_tokens, 0)) AS input_cache_write_5m_tokens,
        sum(coalesce(input_cache_write_1h_tokens, 0)) AS input_cache_write_1h_tokens,
        sum(coalesce(cache_storage_token_hours, 0)) AS cache_storage_token_hours,
        sum(coalesce(output_tokens, 0)) AS output_tokens,
        sum(coalesce(reasoning_tokens, 0)) AS reasoning_tokens,
        sum(coalesce(tool_calls, 0)) AS tool_calls,
        sum(coalesce(web_search_calls, 0)) AS web_search_calls,
        sum(coalesce(sandbox_seconds, 0)) AS sandbox_seconds,
        sum(coalesce(other_cost_usd, 0)) AS other_cost_usd,
        max(timestamp) AS observed_at
    FROM request_traces
    GROUP BY workload_id, workload_version, offering_id, work_unit_id
),
per_offering AS (
    SELECT
        workload_id,
        workload_version,
        offering_id,
        count(*) AS work_unit_count,
        sum(success_weight) AS successful_work_units,
        epoch_us(max(observed_at)) AS observed_at_epoch_us,
        sum(request_count) AS request_count_total,
        sum(attempt_count) AS attempt_count_total,
        sum(input_uncached_tokens) AS input_uncached_tokens_total,
        sum(input_cache_read_tokens) AS input_cache_read_tokens_total,
        sum(input_cache_write_5m_tokens) AS input_cache_write_5m_tokens_total,
        sum(input_cache_write_1h_tokens) AS input_cache_write_1h_tokens_total,
        sum(cache_storage_token_hours) AS cache_storage_token_hours_total,
        sum(output_tokens) AS output_tokens_total,
        sum(reasoning_tokens) AS reasoning_tokens_total,
        sum(tool_calls) AS tool_calls_total,
        sum(web_search_calls) AS web_search_calls_total,
        sum(sandbox_seconds) AS sandbox_seconds_total,
        sum(other_cost_usd) AS other_cost_usd_total
    FROM per_work_unit
    GROUP BY workload_id, workload_version, offering_id
),
latency AS (
    SELECT
        workload_id,
        workload_version,
        offering_id,
        median(ttft_ms) FILTER (WHERE ttft_ms IS NOT NULL) AS ttft_p50_ms,
        quantile_cont(ttft_ms, 0.95) FILTER (WHERE ttft_ms IS NOT NULL) AS ttft_p95_ms,
        count(ttft_ms) FILTER (WHERE ttft_ms IS NOT NULL) AS ttft_sample_count,
        epoch_us(max(timestamp) FILTER (WHERE ttft_ms IS NOT NULL))
            AS ttft_observed_at_epoch_us,
        median(output_tokens_per_second)
            FILTER (WHERE output_tokens_per_second IS NOT NULL)
            AS output_tokens_per_second_p50,
        count(output_tokens_per_second)
            FILTER (WHERE output_tokens_per_second IS NOT NULL)
            AS output_tokens_per_second_sample_count,
        epoch_us(max(timestamp) FILTER (WHERE output_tokens_per_second IS NOT NULL))
            AS output_tokens_per_second_observed_at_epoch_us
    FROM request_traces
    GROUP BY workload_id, workload_version, offering_id
)
SELECT per_offering.*, latency.ttft_p50_ms, latency.ttft_p95_ms,
       latency.ttft_sample_count, latency.ttft_observed_at_epoch_us,
       latency.output_tokens_per_second_p50,
       latency.output_tokens_per_second_sample_count,
       latency.output_tokens_per_second_observed_at_epoch_us
FROM per_offering
LEFT JOIN latency USING (workload_id, workload_version, offering_id)
ORDER BY offering_id
"""


TRACE_VALIDATION_SQL = """
SELECT count(*)
FROM request_traces
WHERE timestamp IS NULL
   OR workload_id IS NULL OR workload_id = ''
   OR workload_version IS NULL OR workload_version = ''
   OR work_unit_id IS NULL OR work_unit_id = ''
   OR offering_id IS NULL OR offering_id = ''
   OR request_id IS NULL OR request_id = ''
   OR attempt_id IS NULL OR attempt_id = ''
   OR work_unit_success IS NULL OR work_unit_success < 0 OR work_unit_success > 1
   OR (trace_classification IS NOT NULL
       AND (trace_classification.class_id IS NULL
            OR trace_classification."source" IS NULL
            OR trace_classification."source"."method" IS NULL
            OR trace_classification.confidence IS NULL
            OR trace_classification.confidence < 0
            OR trace_classification.confidence > 1))
   OR coalesce(input_uncached_tokens, 0) < 0
   OR coalesce(input_cache_read_tokens, 0) < 0
   OR coalesce(input_cache_write_5m_tokens, 0) < 0
   OR coalesce(input_cache_write_1h_tokens, 0) < 0
   OR coalesce(cache_storage_token_hours, 0) < 0
   OR coalesce(output_tokens, 0) < 0
   OR coalesce(reasoning_tokens, 0) < 0
   OR coalesce(tool_calls, 0) < 0
   OR coalesce(web_search_calls, 0) < 0
   OR coalesce(sandbox_seconds, 0) < 0
   OR coalesce(other_cost_usd, 0) < 0
   OR coalesce(ttft_ms, 0) < 0
   OR coalesce(output_tokens_per_second, 0) < 0
"""


INCONSISTENT_OUTCOMES_SQL = """
SELECT count(*)
FROM (
    SELECT workload_id, workload_version, offering_id, work_unit_id
    FROM request_traces
    GROUP BY workload_id, workload_version, offering_id, work_unit_id
    HAVING min(work_unit_success) != max(work_unit_success)
)
"""


DUPLICATE_REQUESTS_SQL = """
SELECT count(*)
FROM (
    SELECT workload_id, workload_version, offering_id, work_unit_id, request_id
    FROM request_traces
    GROUP BY workload_id, workload_version, offering_id, work_unit_id, request_id
    HAVING count(*) > 1
)
"""


def _unit_for_signal(signal: str) -> str:
    if signal in {"success_rate", "observed_cache_hit_rate"}:
        return "ratio"
    if signal.startswith("ttft_"):
        return "milliseconds"
    if signal == "output_tokens_per_second_p50":
        return "tokens/second"
    if signal in {"work_unit_count", "successful_work_units"}:
        return "work_units"
    for quantity, unit in QUANTITY_UNITS.items():
        if signal == f"{quantity}_per_work_unit":
            return f"{unit}/work_unit"
        if signal == f"{quantity}_per_success":
            return f"{unit}/success"
    raise TraceAggregationError(f"no canonical unit for aggregate signal {signal!r}")


def _from_epoch_microseconds(value: Any, label: str) -> datetime:
    try:
        microseconds = int(value)
    except (TypeError, ValueError) as exc:
        raise TraceAggregationError(f"aggregate {label} is not an epoch timestamp") from exc
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=microseconds)


def aggregate_traces(
    path: str | Path,
    *,
    workload: WorkloadReference,
    source_id: str | None = None,
    retrieved_at: datetime | None = None,
) -> TraceSummary:
    """Aggregate request rows without dropping failed attempts or work units."""

    trace_path = Path(path)
    now = retrieved_at or datetime.now(UTC)
    raw_sha256 = _sha256_file(trace_path)
    source = SourceReference(
        id=source_id or f"trace:sha256:{raw_sha256}",
        methodology=(
            "Strict canonical request traces aggregated with DuckDB; failures are retained."
        ),
        raw_sha256=raw_sha256,
        retrieved_at=now,
    )
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute("SET TimeZone='UTC'")
        relation = _relation(connection, trace_path)
        relation.create_view("request_traces")
        invalid_result = connection.sql(TRACE_VALIDATION_SQL).fetchone()
        if invalid_result is None:
            raise TraceAggregationError("trace validation returned no result")
        invalid_rows = int(invalid_result[0])
        if invalid_rows:
            raise TraceAggregationError(
                f"{trace_path} contains {invalid_rows} invalid canonical trace rows"
            )
        inconsistent_result = connection.sql(INCONSISTENT_OUTCOMES_SQL).fetchone()
        if inconsistent_result is None:
            raise TraceAggregationError("trace outcome validation returned no result")
        inconsistent = int(inconsistent_result[0])
        if inconsistent:
            raise TraceAggregationError(
                f"{trace_path} contains {inconsistent} work units with inconsistent outcomes"
            )
        duplicate_result = connection.sql(DUPLICATE_REQUESTS_SQL).fetchone()
        if duplicate_result is None:
            raise TraceAggregationError("trace duplicate validation returned no result")
        duplicates = int(duplicate_result[0])
        if duplicates:
            raise TraceAggregationError(
                f"{trace_path} contains {duplicates} duplicate canonical requests"
            )
        workload_result = connection.execute(
            """
            SELECT count(*)
            FROM request_traces
            WHERE workload_id != ? OR workload_version != ?
            """,
            [workload.id, workload.version],
        ).fetchone()
        if workload_result is None:
            raise TraceAggregationError("trace workload validation returned no result")
        mismatched_workloads = int(workload_result[0])
        if mismatched_workloads:
            raise TraceAggregationError(
                f"{trace_path} contains {mismatched_workloads} rows outside "
                f"{workload.id}@{workload.version}"
            )
        result = connection.sql(AGGREGATION_SQL)
        columns = [description[0] for description in result.description]
        rows = result.fetchall()
    except duckdb.Error as exc:
        raise TraceAggregationError(f"cannot aggregate {trace_path}: {exc}") from exc
    finally:
        connection.close()

    summaries: dict[str, dict[str, Observation]] = {}
    for row in rows:
        values = dict(zip(columns, row, strict=True))
        offering_id = str(values.pop("offering_id"))
        values.pop("workload_id")
        values.pop("workload_version")
        observed_at = _from_epoch_microseconds(values.pop("observed_at_epoch_us"), "observed_at")
        sample_count = int(values.pop("work_unit_count"))
        successful_work_units = Decimal(str(values.pop("successful_work_units")))
        work_unit_count = Decimal(sample_count)
        ttft_sample_count = int(values.pop("ttft_sample_count"))
        ttft_observed_at_value = values.pop("ttft_observed_at_epoch_us")
        ttft_observed_at = (
            _from_epoch_microseconds(ttft_observed_at_value, "ttft_observed_at")
            if ttft_observed_at_value is not None
            else None
        )
        throughput_sample_count = int(values.pop("output_tokens_per_second_sample_count"))
        throughput_observed_at_value = values.pop("output_tokens_per_second_observed_at_epoch_us")
        throughput_observed_at = (
            _from_epoch_microseconds(throughput_observed_at_value, "throughput_observed_at")
            if throughput_observed_at_value is not None
            else None
        )

        with localcontext(POLICY_DECIMAL_CONTEXT):
            derived: dict[str, Decimal | datetime | None] = {
                "work_unit_count": work_unit_count,
                "successful_work_units": successful_work_units,
                "success_rate": successful_work_units / work_unit_count,
                "ttft_p50_ms": values.pop("ttft_p50_ms"),
                "ttft_p95_ms": values.pop("ttft_p95_ms"),
                "output_tokens_per_second_p50": values.pop("output_tokens_per_second_p50"),
            }
            totals: dict[str, Decimal] = {}
            for quantity in QUANTITY_UNITS:
                total = Decimal(str(values.pop(f"{quantity}_total")))
                totals[quantity] = total
                derived[f"{quantity}_per_work_unit"] = total / work_unit_count
                if successful_work_units != 0:
                    derived[f"{quantity}_per_success"] = total / successful_work_units
            cache_denominator = (
                totals["input_uncached_tokens"]
                + totals["input_cache_read_tokens"]
                + totals["input_cache_write_5m_tokens"]
                + totals["input_cache_write_1h_tokens"]
            )
            if cache_denominator != 0:
                derived["observed_cache_hit_rate"] = (
                    totals["input_cache_read_tokens"] / cache_denominator
                )
        if values:
            raise TraceAggregationError(f"unhandled aggregate columns: {', '.join(sorted(values))}")
        signals: dict[str, Observation] = {}
        for signal, value in derived.items():
            if value is None:
                continue
            signal_sample_count = sample_count
            signal_observed_at: datetime | None = observed_at
            if signal.startswith("ttft_"):
                signal_sample_count = ttft_sample_count
                signal_observed_at = ttft_observed_at
            elif signal == "output_tokens_per_second_p50":
                signal_sample_count = throughput_sample_count
                signal_observed_at = throughput_observed_at
            if not isinstance(signal_observed_at, datetime):
                raise TraceAggregationError(
                    f"aggregate signal {signal!r} has no timezone-aware watermark"
                )
            signals[signal] = Observation(
                value=Decimal(str(value)),
                unit=_unit_for_signal(signal),
                sample_count=signal_sample_count,
                observed_at=signal_observed_at.astimezone(UTC),
                source=source,
            )
        summaries[offering_id] = signals
    if _sha256_file(trace_path) != raw_sha256:
        raise TraceAggregationError(f"{trace_path} changed while it was being aggregated")
    return TraceSummary(workload=workload, source=source, offerings=summaries)


def enrich_catalog(
    catalog: ObservationCatalog,
    summary: TraceSummary,
) -> ObservationCatalog:
    if summary.workload != catalog.workload:
        raise TraceAggregationError(
            "trace summary workload does not match the observation catalog workload: "
            f"{summary.workload.id}@{summary.workload.version} != "
            f"{catalog.workload.id}@{catalog.workload.version}"
        )
    summaries = summary.offerings
    known = {item.offering.offering_id for item in catalog.offerings}
    unknown = sorted(set(summaries) - known)
    if unknown:
        raise TraceAggregationError(
            f"trace offerings are absent from the catalog: {', '.join(unknown)}"
        )
    offerings: list[OfferingObservation] = []
    for item in catalog.offerings:
        merged = {**item.signals, **summaries.get(item.offering.offering_id, {})}
        offerings.append(item.model_copy(update={"signals": merged}))
    return catalog.model_copy(update={"offerings": offerings})
