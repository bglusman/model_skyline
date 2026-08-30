from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import Field, ValidationError, field_validator, model_validator

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT
from model_skyline.models import (
    CanonicalDecimal,
    FrozenModel,
    Observation,
    ObservationCatalog,
    OfferingObservation,
    SafeCount,
    SourceReference,
    WorkloadReference,
)
from model_skyline.trace_producers import ProducerKey, trusted_trace_producer

TRACE_COHERENCE_DECIMAL_PRECISION = 50
TRACE_SCHEMA_VERSION = "model-skyline/request-trace/v1alpha2"
LEGACY_TRACE_SCHEMA_VERSION = "model-skyline/request-trace/v1alpha1"
MAX_TRACE_JSONL_BYTES = 256 * 1024 * 1024
MAX_TRACE_PARQUET_BYTES = 1024 * 1024 * 1024
MAX_TRACE_JSONL_LINE_BYTES = 4 * 1024 * 1024
MAX_TRACE_JSONL_ROWS = 1_000_000
MAX_TRACE_JSON_DEPTH = 64
MAX_TRACE_JSON_NUMBER_LENGTH = 1024
MAX_TRACE_OFFERINGS = 10_000
MAX_TRACE_WORK_UNIT_GROUPS = 500_000
TRACE_DUCKDB_MEMORY_LIMIT = "256 MiB"
TRACE_DUCKDB_MAX_TEMP_DIRECTORY_SIZE = "512 MiB"
TRACE_DUCKDB_THREADS = 2
TRACE_RESULT_FETCH_BATCH_SIZE = 1_000
TRACE_PROVENANCE_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$"
TRACE_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _exact_decimal_sum(values: list[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = TRACE_COHERENCE_DECIMAL_PRECISION
        return sum(values, Decimal(0))


class RequestTrace(FrozenModel):
    """Canonical usage row consumed by the DuckDB work-unit aggregator."""

    schema_version: Literal["model-skyline/request-trace/v1alpha2"]
    timestamp: datetime
    workload_id: str = Field(min_length=1)
    workload_version: str = Field(min_length=1)
    work_unit_id: str = Field(min_length=1)
    offering_id: str = Field(min_length=1)
    request_id: str = Field(
        min_length=1,
        description=(
            "Provider request id for request observations; a local pseudonymous record id for "
            "aggregate observations."
        ),
    )
    attempt_id: str = Field(min_length=1)
    observation_unit: Literal["request", "attempt", "work_unit"] = Field(
        default="request",
        description="Granularity represented by this row.",
    )
    model_request_count: SafeCount | None = Field(
        default=None,
        description=(
            "Actual model requests represented by an aggregate row; unknown when omitted. "
            "Request rows implicitly represent one."
        ),
    )
    attempt_count: SafeCount | None = Field(
        default=None,
        description=(
            "Actual attempts represented by a work-unit row; unknown when omitted. "
            "Request and attempt rows derive attempts from attempt_id."
        ),
    )
    adapter_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
        description="Canonical adapter/projector identity when producer provenance is known.",
    )
    adapter_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
    )
    upstream_system: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
    )
    upstream_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
    )
    upstream_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    collector_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
    )
    collector_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
    )
    work_unit_success: CanonicalDecimal = Field(ge=0, le=1, max_digits=18, decimal_places=9)
    input_uncached_tokens: CanonicalDecimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=9
    )
    input_cache_read_tokens: CanonicalDecimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=9
    )
    input_cache_write_tokens: CanonicalDecimal | None = Field(
        default=None,
        ge=0,
        max_digits=38,
        decimal_places=9,
        description=(
            "All cache-write tokens when retention is not exposed upstream; mutually exclusive "
            "with retention-tier buckets."
        ),
    )
    input_cache_write_5m_tokens: CanonicalDecimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=9
    )
    input_cache_write_1h_tokens: CanonicalDecimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=9
    )
    cache_storage_token_hours: CanonicalDecimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=9
    )
    input_total_tokens: CanonicalDecimal | None = Field(
        default=None,
        ge=0,
        max_digits=38,
        decimal_places=9,
        description="Provider-reported input total including cache reads and writes.",
    )
    output_tokens: CanonicalDecimal | None = Field(
        default=None,
        ge=0,
        max_digits=38,
        decimal_places=9,
        description="Non-reasoning output tokens when the upstream split is known.",
    )
    reasoning_tokens: CanonicalDecimal | None = Field(
        default=None,
        ge=0,
        max_digits=38,
        decimal_places=9,
        description="Reasoning output tokens, disjoint from output_tokens.",
    )
    output_total_tokens: CanonicalDecimal | None = Field(
        default=None,
        ge=0,
        max_digits=38,
        decimal_places=9,
        description="Provider-reported output total including reasoning when applicable.",
    )
    tool_calls: CanonicalDecimal | None = Field(default=None, ge=0, max_digits=38, decimal_places=9)
    web_search_calls: CanonicalDecimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=9
    )
    sandbox_seconds: CanonicalDecimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=9
    )
    other_cost_usd: CanonicalDecimal | None = Field(
        default=None,
        ge=0,
        max_digits=38,
        decimal_places=12,
        description="Incremental non-token cost that is safe to add to reconstructed cost.",
    )
    estimated_total_cost_usd: CanonicalDecimal | None = Field(
        default=None,
        ge=0,
        max_digits=38,
        decimal_places=12,
        description=(
            "Client/runtime all-in cost estimate; alternative to reconstructed or billed cost."
        ),
    )
    provider_reported_total_cost_usd: CanonicalDecimal | None = Field(
        default=None,
        ge=0,
        max_digits=38,
        decimal_places=12,
        description=(
            "Provider/runtime-reported all-in cost not reconciled to an invoice; alternative "
            "to reconstructed, estimated, or billed cost."
        ),
    )
    billed_total_cost_usd: CanonicalDecimal | None = Field(
        default=None,
        ge=0,
        max_digits=38,
        decimal_places=12,
        description=(
            "Authoritative provider-billed all-in cost; alternative to reconstructed or "
            "estimated cost."
        ),
    )
    provider_marginal_cost_usd: CanonicalDecimal | None = Field(
        default=None,
        ge=0,
        max_digits=38,
        decimal_places=12,
        description=(
            "Provider marginal charge for the work unit, including an explicit zero for an "
            "included subscription call; not a total economic cost."
        ),
    )
    ttft_ms: CanonicalDecimal | None = Field(default=None, ge=0, max_digits=38, decimal_places=9)
    output_tokens_per_second: CanonicalDecimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=9
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def timestamp_has_canonical_input_type(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and TRACE_TIMESTAMP_PATTERN.fullmatch(value):
            return value
        raise ValueError("timestamp must be an RFC 3339 date-time or datetime object")

    @field_validator("timestamp")
    @classmethod
    def timestamp_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def request_scope_is_coherent(self) -> RequestTrace:
        producer_fields = (
            self.adapter_id,
            self.adapter_version,
            self.upstream_system,
            self.upstream_version,
            self.upstream_commit,
        )
        if any(value is not None for value in producer_fields) and not all(
            value is not None for value in producer_fields
        ):
            raise ValueError("trace producer provenance must be complete when present")
        if (self.collector_id is None) != (self.collector_version is None):
            raise ValueError("collector_id and collector_version must be supplied together")
        if self.collector_id is not None and self.adapter_id is None:
            raise ValueError("collector provenance requires producer provenance")
        if self.observation_unit == "request" and self.model_request_count not in {None, 1}:
            raise ValueError("request observations have an implicit model_request_count of one")
        if self.observation_unit != "work_unit" and self.attempt_count is not None:
            raise ValueError("attempt_count is only valid for work-unit observations")
        if self.observation_unit != "request" and (
            self.ttft_ms is not None or self.output_tokens_per_second is not None
        ):
            raise ValueError("request timing meters are only valid for request observations")
        model_token_meters = (
            self.input_uncached_tokens,
            self.input_cache_read_tokens,
            self.input_cache_write_tokens,
            self.input_cache_write_5m_tokens,
            self.input_cache_write_1h_tokens,
            self.input_total_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.output_total_tokens,
        )
        if self.model_request_count == 0 and any(
            value is not None and value != 0 for value in model_token_meters
        ):
            raise ValueError("zero model_request_count cannot carry nonzero model token usage")
        if self.attempt_count == 0 and (
            (self.model_request_count is not None and self.model_request_count > 0)
            or any(value is not None and value != 0 for value in model_token_meters)
        ):
            raise ValueError("zero attempt_count cannot carry model requests or token usage")
        if self.input_cache_write_tokens is not None and (
            self.input_cache_write_5m_tokens is not None
            or self.input_cache_write_1h_tokens is not None
        ):
            raise ValueError(
                "generic and retention-tier cache-write representations are mutually exclusive"
            )
        known_input_components = [
            value
            for value in (self.input_uncached_tokens, self.input_cache_read_tokens)
            if value is not None
        ]
        complete_cache_write: CanonicalDecimal | None = None
        if self.input_cache_write_tokens is not None:
            complete_cache_write = self.input_cache_write_tokens
            known_input_components.append(self.input_cache_write_tokens)
        elif (
            self.input_cache_write_5m_tokens is not None
            and self.input_cache_write_1h_tokens is not None
        ):
            complete_cache_write = _exact_decimal_sum(
                [self.input_cache_write_5m_tokens, self.input_cache_write_1h_tokens]
            )
            known_input_components.extend(
                (self.input_cache_write_5m_tokens, self.input_cache_write_1h_tokens)
            )
        else:
            known_input_components.extend(
                value
                for value in (
                    self.input_cache_write_5m_tokens,
                    self.input_cache_write_1h_tokens,
                )
                if value is not None
            )
        known_input_total = _exact_decimal_sum(known_input_components)
        if self.input_total_tokens is not None and self.input_total_tokens < known_input_total:
            raise ValueError("input_total_tokens cannot be below known input components")
        if (
            self.input_total_tokens is not None
            and self.input_uncached_tokens is not None
            and self.input_cache_read_tokens is not None
            and complete_cache_write is not None
            and self.input_total_tokens != known_input_total
        ):
            raise ValueError(
                "input_total_tokens must equal uncached input plus cache reads and writes"
            )
        known_output_total = _exact_decimal_sum(
            [value for value in (self.output_tokens, self.reasoning_tokens) if value is not None]
        )
        if self.output_total_tokens is not None and self.output_total_tokens < known_output_total:
            raise ValueError("output_total_tokens cannot be below known output components")
        if (
            self.output_tokens is not None
            and self.reasoning_tokens is not None
            and self.output_total_tokens is not None
            and self.output_total_tokens != known_output_total
        ):
            raise ValueError("output_total_tokens must equal output_tokens plus reasoning_tokens")
        return self


TRACE_COLUMNS = {
    "schema_version": "VARCHAR",
    "timestamp": "TIMESTAMPTZ",
    "workload_id": "VARCHAR",
    "workload_version": "VARCHAR",
    "work_unit_id": "VARCHAR",
    "offering_id": "VARCHAR",
    "request_id": "VARCHAR",
    "attempt_id": "VARCHAR",
    "observation_unit": "VARCHAR",
    "model_request_count": "BIGINT",
    "attempt_count": "BIGINT",
    "adapter_id": "VARCHAR",
    "adapter_version": "VARCHAR",
    "upstream_system": "VARCHAR",
    "upstream_version": "VARCHAR",
    "upstream_commit": "VARCHAR",
    "collector_id": "VARCHAR",
    "collector_version": "VARCHAR",
    "work_unit_success": "DECIMAL(18,9)",
    "input_uncached_tokens": "DECIMAL(38,9)",
    "input_cache_read_tokens": "DECIMAL(38,9)",
    "input_cache_write_tokens": "DECIMAL(38,9)",
    "input_cache_write_5m_tokens": "DECIMAL(38,9)",
    "input_cache_write_1h_tokens": "DECIMAL(38,9)",
    "cache_storage_token_hours": "DECIMAL(38,9)",
    "input_total_tokens": "DECIMAL(38,9)",
    "output_tokens": "DECIMAL(38,9)",
    "reasoning_tokens": "DECIMAL(38,9)",
    "output_total_tokens": "DECIMAL(38,9)",
    "tool_calls": "DECIMAL(38,9)",
    "web_search_calls": "DECIMAL(38,9)",
    "sandbox_seconds": "DECIMAL(38,9)",
    "other_cost_usd": "DECIMAL(38,12)",
    "estimated_total_cost_usd": "DECIMAL(38,12)",
    "provider_reported_total_cost_usd": "DECIMAL(38,12)",
    "billed_total_cost_usd": "DECIMAL(38,12)",
    "provider_marginal_cost_usd": "DECIMAL(38,12)",
    "ttft_ms": "DECIMAL(38,9)",
    "output_tokens_per_second": "DECIMAL(38,9)",
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
LEGACY_DEFAULT_ZERO_COLUMNS = frozenset(
    {
        "input_uncached_tokens",
        "input_cache_read_tokens",
        "input_cache_write_5m_tokens",
        "input_cache_write_1h_tokens",
        "cache_storage_token_hours",
        "output_tokens",
        "reasoning_tokens",
        "tool_calls",
        "web_search_calls",
        "sandbox_seconds",
        "other_cost_usd",
    }
)
LEGACY_TRACE_COLUMNS = (
    REQUIRED_TRACE_COLUMNS
    | LEGACY_DEFAULT_ZERO_COLUMNS
    | frozenset({"ttft_ms", "output_tokens_per_second"})
)
DECIMAL_SCALES = {
    "work_unit_success": 9,
    "input_uncached_tokens": 9,
    "input_cache_read_tokens": 9,
    "input_cache_write_tokens": 9,
    "input_cache_write_5m_tokens": 9,
    "input_cache_write_1h_tokens": 9,
    "cache_storage_token_hours": 9,
    "input_total_tokens": 9,
    "output_tokens": 9,
    "reasoning_tokens": 9,
    "output_total_tokens": 9,
    "tool_calls": 9,
    "web_search_calls": 9,
    "sandbox_seconds": 9,
    "other_cost_usd": 12,
    "estimated_total_cost_usd": 12,
    "provider_reported_total_cost_usd": 12,
    "billed_total_cost_usd": 12,
    "provider_marginal_cost_usd": 12,
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
    "input_cache_write_tokens": "tokens",
    "input_cache_write_5m_tokens": "tokens",
    "input_cache_write_1h_tokens": "tokens",
    "cache_storage_token_hours": "token_hours",
    "input_total_tokens": "tokens",
    "output_tokens": "tokens",
    "reasoning_tokens": "tokens",
    "output_total_tokens": "tokens",
    "tool_calls": "calls",
    "web_search_calls": "calls",
    "sandbox_seconds": "seconds",
    "other_cost_usd": "USD",
    "estimated_total_cost_usd": "USD",
    "provider_reported_total_cost_usd": "USD",
    "billed_total_cost_usd": "USD",
    "provider_marginal_cost_usd": "USD",
}


class TraceAggregationError(ValueError):
    pass


class _DuplicateTraceKey(ValueError):
    pass


class _InvalidTraceJson(ValueError):
    pass


def _reject_duplicate_trace_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateTraceKey
        result[key] = value
    return result


class TraceProvenance(FrozenModel):
    """Content-free producer identity retained with derived trace observations."""

    adapter_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
    )
    adapter_version: str = Field(
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
    )
    upstream_system: str = Field(
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
    )
    upstream_version: str = Field(
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
    )
    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    collector_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
    )
    collector_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=TRACE_PROVENANCE_IDENTIFIER_PATTERN,
    )

    @model_validator(mode="after")
    def collector_is_complete(self) -> TraceProvenance:
        if (self.collector_id is None) != (self.collector_version is None):
            raise ValueError("collector provenance must be complete")
        return self


class TraceSummary(FrozenModel):
    """Workload-bound aggregate observations derived from one immutable source file."""

    workload: WorkloadReference
    source: SourceReference
    provenance: tuple[TraceProvenance, ...] = ()
    producer_sources: tuple[SourceReference, ...] = ()
    offerings: dict[str, dict[str, Observation]]


@dataclass(frozen=True, slots=True)
class _TraceSnapshot:
    path: Path
    raw_sha256: str


@contextmanager
def _snapshot_trace_input(path: Path) -> Iterator[_TraceSnapshot]:
    """Copy one exact regular file into a private, metacharacter-free snapshot."""

    suffix = path.suffix.lower()
    is_parquet = suffix in {".parquet", ".pq"}
    byte_limit = MAX_TRACE_PARQUET_BYTES if is_parquet else MAX_TRACE_JSONL_BYTES
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise TraceAggregationError("cannot open trace input") from None
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise TraceAggregationError("trace input must be a regular file")
        if metadata.st_size > byte_limit:
            raise TraceAggregationError("trace input exceeds the byte limit")
        temporary = tempfile.TemporaryDirectory(prefix="model-skyline-trace-")
        snapshot_path = Path(temporary.name) / ("input.parquet" if is_parquet else "input.jsonl")
        digest = hashlib.sha256()
        consumed = 0
        with os.fdopen(descriptor, "rb", closefd=True) as source_stream:
            descriptor = -1
            with snapshot_path.open("xb") as snapshot_stream:
                while True:
                    chunk = source_stream.read(1024 * 1024)
                    if not chunk:
                        break
                    consumed += len(chunk)
                    if consumed > byte_limit:
                        raise TraceAggregationError("trace input exceeds the byte limit")
                    digest.update(chunk)
                    snapshot_stream.write(chunk)
        snapshot_path.chmod(0o400)
        yield _TraceSnapshot(path=snapshot_path, raw_sha256=digest.hexdigest())
    except OSError:
        raise TraceAggregationError("cannot snapshot trace input") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.cleanup()


def _bounded_json_integer(value: str) -> int:
    digits = value.removeprefix("-")
    if len(digits) > MAX_TRACE_JSON_NUMBER_LENGTH:
        raise _InvalidTraceJson
    try:
        return int(value)
    except ValueError:
        raise _InvalidTraceJson from None


def _bounded_json_decimal(value: str) -> Decimal:
    if len(value) > MAX_TRACE_JSON_NUMBER_LENGTH:
        raise _InvalidTraceJson
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise _InvalidTraceJson from None
    if not parsed.is_finite():
        raise _InvalidTraceJson
    return parsed


def _reject_nonstandard_json_number(_value: str) -> None:
    raise _InvalidTraceJson


def _validate_json_depth(text: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_TRACE_JSON_DEPTH:
                raise _InvalidTraceJson
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise _InvalidTraceJson


def _validate_json_lines(path: Path) -> tuple[int, str]:
    row_count = 0
    detected_schema: str | None = None
    try:
        with path.open("rb") as stream:
            line_number = 0
            while True:
                raw_line = stream.readline(MAX_TRACE_JSONL_LINE_BYTES + 1)
                if not raw_line:
                    break
                line_number += 1
                if len(raw_line) > MAX_TRACE_JSONL_LINE_BYTES:
                    raise TraceAggregationError(
                        f"trace JSONL line {line_number} exceeds the byte limit"
                    )
                if not raw_line.strip():
                    continue
                row_count += 1
                if row_count > MAX_TRACE_JSONL_ROWS:
                    raise TraceAggregationError("trace JSONL exceeds the row limit")
                try:
                    line = raw_line.decode("utf-8")
                    _validate_json_depth(line)
                    payload = json.loads(
                        line,
                        parse_int=_bounded_json_integer,
                        parse_float=_bounded_json_decimal,
                        parse_constant=_reject_nonstandard_json_number,
                        object_pairs_hook=_reject_duplicate_trace_keys,
                    )
                    if not isinstance(payload, dict):
                        raise _InvalidTraceJson
                    row_schema = payload.get("schema_version")
                    if row_schema is None:
                        row_schema = LEGACY_TRACE_SCHEMA_VERSION
                        if not set(payload).issubset(LEGACY_TRACE_COLUMNS):
                            raise _InvalidTraceJson
                        if any(
                            payload.get(name) is None
                            for name in LEGACY_DEFAULT_ZERO_COLUMNS & payload.keys()
                        ):
                            raise _InvalidTraceJson
                        payload = {
                            **{name: Decimal(0) for name in LEGACY_DEFAULT_ZERO_COLUMNS},
                            **payload,
                            "schema_version": TRACE_SCHEMA_VERSION,
                        }
                    elif row_schema != TRACE_SCHEMA_VERSION:
                        raise _InvalidTraceJson
                    if detected_schema is None:
                        detected_schema = row_schema
                    elif detected_schema != row_schema:
                        raise _InvalidTraceJson
                    RequestTrace.model_validate(payload)
                except ValidationError as exc:
                    messages = sorted(
                        {
                            str(error["msg"])
                            for error in exc.errors(
                                include_url=False,
                                include_context=False,
                                include_input=False,
                            )
                        }
                    )
                    detail = "; ".join(messages)
                    raise TraceAggregationError(
                        f"trace JSONL line {line_number} is not canonical: {detail}"
                    ) from None
                except UnicodeDecodeError:
                    raise TraceAggregationError(
                        f"trace JSONL line {line_number} is not valid UTF-8"
                    ) from None
                except (
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                    RecursionError,
                    MemoryError,
                    _DuplicateTraceKey,
                    _InvalidTraceJson,
                ):
                    raise TraceAggregationError(
                        f"trace JSONL line {line_number} is not valid canonical JSON"
                    ) from None
    except OSError:
        raise TraceAggregationError("cannot read trace input") from None
    if row_count == 0:
        raise TraceAggregationError("trace input contains no canonical usage rows")
    if detected_schema is None:
        raise TraceAggregationError("trace input has no detectable schema version")
    return row_count, detected_schema


def _canonical_parquet_relation(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
) -> tuple[Any, str]:
    relation = connection.read_parquet(str(path))
    columns = relation.columns
    column_types = {
        name: str(data_type) for name, data_type in zip(columns, relation.types, strict=True)
    }
    detected_schema = (
        TRACE_SCHEMA_VERSION if "schema_version" in columns else LEGACY_TRACE_SCHEMA_VERSION
    )
    allowed_columns = (
        set(TRACE_COLUMNS) if detected_schema == TRACE_SCHEMA_VERSION else set(LEGACY_TRACE_COLUMNS)
    )
    missing = sorted(REQUIRED_TRACE_COLUMNS - set(columns))
    extras = sorted(set(columns) - allowed_columns)
    if missing:
        raise TraceAggregationError(
            f"trace input is missing required columns: {', '.join(missing)}"
        )
    if extras:
        raise TraceAggregationError(
            f"trace input has {len(extras)} unknown column(s); names are not echoed"
        )
    if detected_schema == LEGACY_TRACE_SCHEMA_VERSION:
        nullable_legacy_meters = sorted(LEGACY_DEFAULT_ZERO_COLUMNS & set(columns))
        if nullable_legacy_meters:
            null_predicate = " OR ".join(f'"{name}" IS NULL' for name in nullable_legacy_meters)
            null_result = relation.filter(null_predicate).aggregate("count(*)").fetchone()
            if null_result is None or int(null_result[0]) != 0:
                raise TraceAggregationError(
                    "legacy trace input contains null in a default-zero meter"
                )

    projections: list[str] = []
    for name, target_type in TRACE_COLUMNS.items():
        source_type = column_types.get(name)
        if source_type is None:
            if name == "schema_version":
                projections.append(f"'{TRACE_SCHEMA_VERSION}'::{target_type} AS \"{name}\"")
            elif name == "observation_unit":
                projections.append(f"'request'::{target_type} AS \"{name}\"")
            elif (
                detected_schema == LEGACY_TRACE_SCHEMA_VERSION
                and name in LEGACY_DEFAULT_ZERO_COLUMNS
            ):
                projections.append(f'0::{target_type} AS "{name}"')
            else:
                projections.append(f'NULL::{target_type} AS "{name}"')
            continue
        if name == "timestamp":
            if source_type != "TIMESTAMP WITH TIME ZONE":
                raise TraceAggregationError(
                    f"trace column timestamp must be timezone-aware, got {source_type}"
                )
        elif target_type == "VARCHAR":
            if source_type != "VARCHAR":
                raise TraceAggregationError(
                    f"trace column {name} must be VARCHAR, got {source_type}"
                )
        elif name in {"model_request_count", "attempt_count"}:
            if source_type not in INTEGER_TYPES:
                raise TraceAggregationError(
                    f"trace column {name} must use an exact integer type, got {source_type}"
                )
        else:
            decimal_match = DECIMAL_TYPE_RE.fullmatch(source_type)
            if decimal_match is not None:
                source_scale = int(decimal_match.group(2))
                if source_scale > DECIMAL_SCALES[name]:
                    raise TraceAggregationError(
                        f"trace column {name} has scale {source_scale}; "
                        f"maximum is {DECIMAL_SCALES[name]}"
                    )
            elif source_type not in INTEGER_TYPES:
                raise TraceAggregationError(
                    f"trace column {name} must use an exact integer or DECIMAL type, "
                    f"got {source_type}; floating-point trace meters are not accepted"
                )
        if name == "observation_unit":
            projections.append(
                f'coalesce(CAST("{name}" AS {target_type}), \'__invalid_null__\') AS "{name}"'
            )
        else:
            projections.append(f'CAST("{name}" AS {target_type}) AS "{name}"')
    return relation.project(", ".join(projections)), detected_schema


def _legacy_json_projection(relation: Any) -> Any:
    projections: list[str] = []
    for name, target_type in TRACE_COLUMNS.items():
        if name == "schema_version":
            projections.append(f"'{TRACE_SCHEMA_VERSION}'::{target_type} AS \"{name}\"")
        elif name in LEGACY_DEFAULT_ZERO_COLUMNS:
            projections.append(f'coalesce("{name}", 0::{target_type}) AS "{name}"')
        else:
            projections.append(f'"{name}"')
    return relation.project(", ".join(projections))


def _relation(connection: duckdb.DuckDBPyConnection, path: Path) -> tuple[Any, str]:
    suffix = path.suffix.lower()
    try:
        if suffix in {".parquet", ".pq"}:
            return _canonical_parquet_relation(connection, path)
        _, detected_schema = _validate_json_lines(path)
        relation = connection.read_json(
            str(path),
            columns=TRACE_COLUMNS,
            format="newline_delimited",
        )
        if detected_schema == LEGACY_TRACE_SCHEMA_VERSION:
            relation = _legacy_json_projection(relation)
        return relation, detected_schema
    except duckdb.Error:
        raise TraceAggregationError("cannot read trace input") from None


AGGREGATION_SQL = """
WITH per_work_unit AS (
    SELECT
        workload_id,
        workload_version,
        offering_id,
        work_unit_id,
        CASE WHEN count(*) FILTER (
            WHERE coalesce(observation_unit, 'request') != 'request'
              AND model_request_count IS NULL
        ) = 0 THEN sum(
            CASE WHEN coalesce(observation_unit, 'request') = 'request'
                 THEN 1 ELSE model_request_count END
        )::DECIMAL(38,9) END AS request_count,
        CASE WHEN max(coalesce(observation_unit, 'request')) = 'work_unit'
             THEN max(attempt_count)::DECIMAL(38,9)
             ELSE count(DISTINCT attempt_id)::DECIMAL(38,9)
        END AS attempt_count,
        max(work_unit_success) AS success_weight,
        CASE WHEN count(input_uncached_tokens) = count(*)
             THEN sum(input_uncached_tokens) END AS input_uncached_tokens,
        CASE WHEN count(input_cache_read_tokens) = count(*)
             THEN sum(input_cache_read_tokens) END AS input_cache_read_tokens,
        CASE WHEN count(input_cache_write_tokens) = count(*)
             THEN sum(input_cache_write_tokens) END AS input_cache_write_tokens,
        CASE WHEN count(input_cache_write_5m_tokens) = count(*)
             THEN sum(input_cache_write_5m_tokens) END AS input_cache_write_5m_tokens,
        CASE WHEN count(input_cache_write_1h_tokens) = count(*)
             THEN sum(input_cache_write_1h_tokens) END AS input_cache_write_1h_tokens,
        CASE WHEN count(*) FILTER (
            WHERE (input_cache_write_tokens IS NOT NULL
                   AND input_cache_write_5m_tokens IS NULL
                   AND input_cache_write_1h_tokens IS NULL)
               OR (input_cache_write_tokens IS NULL
                   AND input_cache_write_5m_tokens IS NOT NULL
                   AND input_cache_write_1h_tokens IS NOT NULL)
        ) = count(*) THEN sum(
            coalesce(
                input_cache_write_tokens,
                input_cache_write_5m_tokens + input_cache_write_1h_tokens
            )
        ) END AS complete_cache_write_tokens,
        CASE WHEN count(cache_storage_token_hours) = count(*)
             THEN sum(cache_storage_token_hours) END AS cache_storage_token_hours,
        CASE WHEN count(input_total_tokens) = count(*)
             THEN sum(input_total_tokens) END AS input_total_tokens,
        CASE WHEN count(output_tokens) = count(*)
             THEN sum(output_tokens) END AS output_tokens,
        CASE WHEN count(reasoning_tokens) = count(*)
             THEN sum(reasoning_tokens) END AS reasoning_tokens,
        CASE WHEN count(output_total_tokens) = count(*)
             THEN sum(output_total_tokens) END AS output_total_tokens,
        CASE WHEN count(tool_calls) = count(*)
             THEN sum(tool_calls) END AS tool_calls,
        CASE WHEN count(web_search_calls) = count(*)
             THEN sum(web_search_calls) END AS web_search_calls,
        CASE WHEN count(sandbox_seconds) = count(*)
             THEN sum(sandbox_seconds) END AS sandbox_seconds,
        CASE WHEN count(other_cost_usd) = count(*)
             THEN sum(other_cost_usd) END AS other_cost_usd,
        CASE WHEN count(estimated_total_cost_usd) = count(*)
             THEN sum(estimated_total_cost_usd) END AS estimated_total_cost_usd,
        CASE WHEN count(provider_reported_total_cost_usd) = count(*)
             THEN sum(provider_reported_total_cost_usd)
        END AS provider_reported_total_cost_usd,
        CASE WHEN count(billed_total_cost_usd) = count(*)
             THEN sum(billed_total_cost_usd) END AS billed_total_cost_usd,
        CASE WHEN count(provider_marginal_cost_usd) = count(*)
             THEN sum(provider_marginal_cost_usd) END AS provider_marginal_cost_usd,
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
        CASE WHEN count(request_count) = count(*)
             THEN sum(request_count) END AS request_count_total,
        CASE WHEN count(attempt_count) = count(*)
             THEN sum(attempt_count) END AS attempt_count_total,
        CASE WHEN count(input_uncached_tokens) = count(*)
             THEN sum(input_uncached_tokens) END AS input_uncached_tokens_total,
        CASE WHEN count(input_cache_read_tokens) = count(*)
             THEN sum(input_cache_read_tokens) END AS input_cache_read_tokens_total,
        CASE WHEN count(input_cache_write_tokens) = count(*)
             THEN sum(input_cache_write_tokens) END AS input_cache_write_tokens_total,
        CASE WHEN count(input_cache_write_5m_tokens) = count(*)
             THEN sum(input_cache_write_5m_tokens) END AS input_cache_write_5m_tokens_total,
        CASE WHEN count(input_cache_write_1h_tokens) = count(*)
             THEN sum(input_cache_write_1h_tokens) END AS input_cache_write_1h_tokens_total,
        CASE WHEN count(complete_cache_write_tokens) = count(*)
             THEN sum(complete_cache_write_tokens) END AS complete_cache_write_tokens_total,
        CASE WHEN count(cache_storage_token_hours) = count(*)
             THEN sum(cache_storage_token_hours) END AS cache_storage_token_hours_total,
        CASE WHEN count(input_total_tokens) = count(*)
             THEN sum(input_total_tokens) END AS input_total_tokens_total,
        CASE WHEN count(output_tokens) = count(*)
             THEN sum(output_tokens) END AS output_tokens_total,
        CASE WHEN count(reasoning_tokens) = count(*)
             THEN sum(reasoning_tokens) END AS reasoning_tokens_total,
        CASE WHEN count(output_total_tokens) = count(*)
             THEN sum(output_total_tokens) END AS output_total_tokens_total,
        CASE WHEN count(tool_calls) = count(*)
             THEN sum(tool_calls) END AS tool_calls_total,
        CASE WHEN count(web_search_calls) = count(*)
             THEN sum(web_search_calls) END AS web_search_calls_total,
        CASE WHEN count(sandbox_seconds) = count(*)
             THEN sum(sandbox_seconds) END AS sandbox_seconds_total,
        CASE WHEN count(other_cost_usd) = count(*)
             THEN sum(other_cost_usd) END AS other_cost_usd_total,
        CASE WHEN count(estimated_total_cost_usd) = count(*)
             THEN sum(estimated_total_cost_usd) END AS estimated_total_cost_usd_total,
        CASE WHEN count(provider_reported_total_cost_usd) = count(*)
             THEN sum(provider_reported_total_cost_usd)
        END AS provider_reported_total_cost_usd_total,
        CASE WHEN count(billed_total_cost_usd) = count(*)
             THEN sum(billed_total_cost_usd) END AS billed_total_cost_usd_total,
        CASE WHEN count(provider_marginal_cost_usd) = count(*)
             THEN sum(provider_marginal_cost_usd) END AS provider_marginal_cost_usd_total
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
   OR schema_version IS NULL OR schema_version != 'model-skyline/request-trace/v1alpha2'
   OR workload_id IS NULL OR workload_id = ''
   OR workload_version IS NULL OR workload_version = ''
   OR work_unit_id IS NULL OR work_unit_id = ''
   OR offering_id IS NULL OR offering_id = ''
   OR request_id IS NULL OR request_id = ''
   OR attempt_id IS NULL OR attempt_id = ''
   OR regexp_matches(
       concat(
           schema_version, workload_id, workload_version, work_unit_id, offering_id,
           request_id, attempt_id, observation_unit,
           adapter_id, adapter_version, upstream_system, upstream_version,
           upstream_commit, collector_id, collector_version
       ),
       '[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F\\x7F-\\x9F\\x{FFFE}\\x{FFFF}]'
   )
   OR (observation_unit IS NOT NULL
       AND observation_unit NOT IN ('request', 'attempt', 'work_unit'))
   OR model_request_count < 0
   OR model_request_count > 9007199254740991
   OR attempt_count < 0
   OR attempt_count > 9007199254740991
   OR (coalesce(observation_unit, 'request') = 'request'
       AND model_request_count IS NOT NULL AND model_request_count != 1)
   OR (coalesce(observation_unit, 'request') != 'work_unit'
       AND attempt_count IS NOT NULL)
   OR (coalesce(observation_unit, 'request') != 'request'
       AND (ttft_ms IS NOT NULL OR output_tokens_per_second IS NOT NULL))
   OR ((adapter_id IS NULL)::INTEGER
       + (adapter_version IS NULL)::INTEGER
       + (upstream_system IS NULL)::INTEGER
       + (upstream_version IS NULL)::INTEGER
       + (upstream_commit IS NULL)::INTEGER) NOT IN (0, 5)
   OR (collector_id IS NULL) != (collector_version IS NULL)
   OR (collector_id IS NOT NULL AND adapter_id IS NULL)
   OR (upstream_commit IS NOT NULL
       AND NOT regexp_full_match(upstream_commit, '[0-9a-f]{40}'))
   OR (model_request_count = 0
       AND (
           coalesce(input_uncached_tokens, 0) > 0
        OR coalesce(input_cache_read_tokens, 0) > 0
        OR coalesce(input_cache_write_tokens, 0) > 0
        OR coalesce(input_cache_write_5m_tokens, 0) > 0
        OR coalesce(input_cache_write_1h_tokens, 0) > 0
        OR coalesce(input_total_tokens, 0) > 0
        OR coalesce(output_tokens, 0) > 0
        OR coalesce(reasoning_tokens, 0) > 0
        OR coalesce(output_total_tokens, 0) > 0
       ))
   OR (attempt_count = 0
       AND (
           coalesce(model_request_count, 0) > 0
        OR coalesce(input_uncached_tokens, 0) > 0
        OR coalesce(input_cache_read_tokens, 0) > 0
        OR coalesce(input_cache_write_tokens, 0) > 0
        OR coalesce(input_cache_write_5m_tokens, 0) > 0
        OR coalesce(input_cache_write_1h_tokens, 0) > 0
        OR coalesce(input_total_tokens, 0) > 0
        OR coalesce(output_tokens, 0) > 0
        OR coalesce(reasoning_tokens, 0) > 0
        OR coalesce(output_total_tokens, 0) > 0
       ))
   OR work_unit_success IS NULL OR work_unit_success < 0 OR work_unit_success > 1
   OR coalesce(input_uncached_tokens, 0) < 0
   OR coalesce(input_cache_read_tokens, 0) < 0
   OR coalesce(input_cache_write_tokens, 0) < 0
   OR coalesce(input_cache_write_5m_tokens, 0) < 0
   OR coalesce(input_cache_write_1h_tokens, 0) < 0
   OR (input_cache_write_tokens IS NOT NULL
       AND (input_cache_write_5m_tokens IS NOT NULL
            OR input_cache_write_1h_tokens IS NOT NULL))
   OR coalesce(cache_storage_token_hours, 0) < 0
   OR coalesce(input_total_tokens, 0) < 0
   OR (input_total_tokens IS NOT NULL
       AND input_total_tokens < coalesce(input_uncached_tokens, 0)
           + coalesce(input_cache_read_tokens, 0)
           + coalesce(input_cache_write_tokens, 0)
           + coalesce(input_cache_write_5m_tokens, 0)
           + coalesce(input_cache_write_1h_tokens, 0))
   OR (input_total_tokens IS NOT NULL
       AND input_uncached_tokens IS NOT NULL
       AND input_cache_read_tokens IS NOT NULL
       AND (
           (input_cache_write_tokens IS NOT NULL
            AND input_total_tokens != input_uncached_tokens
                + input_cache_read_tokens + input_cache_write_tokens)
        OR (input_cache_write_tokens IS NULL
            AND input_cache_write_5m_tokens IS NOT NULL
            AND input_cache_write_1h_tokens IS NOT NULL
            AND input_total_tokens != input_uncached_tokens + input_cache_read_tokens
                + input_cache_write_5m_tokens + input_cache_write_1h_tokens)
       ))
   OR coalesce(output_tokens, 0) < 0
   OR coalesce(reasoning_tokens, 0) < 0
   OR coalesce(output_total_tokens, 0) < 0
   OR (output_total_tokens IS NOT NULL
       AND output_total_tokens < coalesce(output_tokens, 0) + coalesce(reasoning_tokens, 0))
   OR (output_total_tokens IS NOT NULL
       AND output_tokens IS NOT NULL
       AND reasoning_tokens IS NOT NULL
       AND output_total_tokens != output_tokens + reasoning_tokens)
   OR coalesce(tool_calls, 0) < 0
   OR coalesce(web_search_calls, 0) < 0
   OR coalesce(sandbox_seconds, 0) < 0
   OR coalesce(other_cost_usd, 0) < 0
   OR coalesce(estimated_total_cost_usd, 0) < 0
   OR coalesce(provider_reported_total_cost_usd, 0) < 0
   OR coalesce(billed_total_cost_usd, 0) < 0
   OR coalesce(provider_marginal_cost_usd, 0) < 0
   OR coalesce(ttft_ms, 0) < 0
   OR coalesce(output_tokens_per_second, 0) < 0
"""


INCONSISTENT_SCOPES_SQL = """
SELECT count(*)
FROM (
    SELECT workload_id, workload_version, work_unit_id
    FROM request_traces
    GROUP BY workload_id, workload_version, work_unit_id
    HAVING count(DISTINCT coalesce(observation_unit, 'request')) != 1
       OR (max(coalesce(observation_unit, 'request')) = 'work_unit' AND count(*) != 1)
)
"""


MULTI_OFFERING_WORK_UNITS_SQL = """
SELECT count(*)
FROM (
    SELECT workload_id, workload_version, work_unit_id
    FROM request_traces
    GROUP BY workload_id, workload_version, work_unit_id
    HAVING count(DISTINCT offering_id) != 1
)
"""


DUPLICATE_ATTEMPT_AGGREGATES_SQL = """
SELECT count(*)
FROM (
    SELECT workload_id, workload_version, work_unit_id, attempt_id
    FROM request_traces
    WHERE coalesce(observation_unit, 'request') = 'attempt'
    GROUP BY workload_id, workload_version, work_unit_id, attempt_id
    HAVING count(*) > 1
)
"""


INCONSISTENT_OUTCOMES_SQL = """
SELECT count(*)
FROM (
    SELECT workload_id, workload_version, work_unit_id
    FROM request_traces
    GROUP BY workload_id, workload_version, work_unit_id
    HAVING min(work_unit_success) != max(work_unit_success)
)
"""


INCONSISTENT_PROVENANCE_COVERAGE_SQL = """
SELECT count(*)
FROM (
    SELECT workload_id, workload_version
    FROM request_traces
    GROUP BY workload_id, workload_version
    HAVING count(adapter_id) NOT IN (0, count(*))
)
"""


INCONSISTENT_OFFERING_PROVENANCE_SQL = """
SELECT count(*)
FROM (
    SELECT workload_id, workload_version, offering_id
    FROM request_traces
    WHERE adapter_id IS NOT NULL
    GROUP BY workload_id, workload_version, offering_id
    HAVING min(adapter_id) != max(adapter_id)
       OR min(adapter_version) != max(adapter_version)
       OR min(upstream_system) != max(upstream_system)
       OR min(upstream_version) != max(upstream_version)
       OR min(upstream_commit) != max(upstream_commit)
       OR count(collector_id) NOT IN (0, count(*))
       OR coalesce(min(collector_id), '') != coalesce(max(collector_id), '')
       OR coalesce(min(collector_version), '') != coalesce(max(collector_version), '')
)
"""


PROVENANCE_SQL = """
SELECT DISTINCT
    adapter_id,
    adapter_version,
    upstream_system,
    upstream_version,
    upstream_commit,
    collector_id,
    collector_version
FROM request_traces
WHERE adapter_id IS NOT NULL
ORDER BY
    adapter_id,
    adapter_version,
    upstream_system,
    upstream_version,
    upstream_commit,
    collector_id NULLS FIRST,
    collector_version NULLS FIRST
"""


DUPLICATE_REQUESTS_SQL = """
SELECT count(*)
FROM (
    SELECT offering_id, request_id
    FROM request_traces
    GROUP BY offering_id, request_id
    HAVING count(*) > 1
)
"""


TRACE_CARDINALITY_SQL = """
SELECT
    (SELECT count(*) FROM (
        SELECT offering_id
        FROM request_traces
        GROUP BY offering_id
    )) AS offering_count,
    (SELECT count(*) FROM (
        SELECT workload_id, workload_version, offering_id, work_unit_id
        FROM request_traces
        GROUP BY workload_id, workload_version, offering_id, work_unit_id
    )) AS work_unit_group_count
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
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=microseconds)
    except OverflowError:
        raise TraceAggregationError(f"aggregate {label} is outside the supported range") from None


def _provenance_key(item: TraceProvenance) -> ProducerKey:
    return (
        item.adapter_id,
        item.adapter_version,
        item.upstream_system,
        item.upstream_version,
        item.upstream_commit,
        item.collector_id,
        item.collector_version,
    )


def _trace_source_methodology(producer_sources: tuple[SourceReference, ...]) -> str:
    methodology = "Strict canonical request traces aggregated with DuckDB; failures are retained."
    if not producer_sources:
        return methodology
    producers = ", ".join(source.id for source in producer_sources)
    return f"{methodology} Reviewed producer sources: {producers}."


def _query_trace_snapshot(
    trace_path: Path,
    workload: WorkloadReference,
) -> tuple[
    str,
    tuple[TraceProvenance, ...],
    tuple[SourceReference, ...],
    list[str],
    list[tuple[Any, ...]],
    str,
]:
    with _snapshot_trace_input(trace_path) as snapshot:
        connection = duckdb.connect(database=":memory:")
        try:
            spill_directory = snapshot.path.parent / "duckdb-spill"
            connection.execute("SET temp_directory = ?", [str(spill_directory)])
            connection.execute("SET memory_limit = ?", [TRACE_DUCKDB_MEMORY_LIMIT])
            connection.execute(
                "SET max_temp_directory_size = ?",
                [TRACE_DUCKDB_MAX_TEMP_DIRECTORY_SIZE],
            )
            connection.execute("SET threads = ?", [TRACE_DUCKDB_THREADS])
            connection.execute("SET preserve_insertion_order = false")
            connection.execute("SET TimeZone='UTC'")
            relation, detected_schema = _relation(connection, snapshot.path)
            relation.create_view("request_traces")
            row_count_result = connection.sql("SELECT count(*) FROM request_traces").fetchone()
            if row_count_result is None or int(row_count_result[0]) == 0:
                raise TraceAggregationError("trace input contains no canonical usage rows")
            if int(row_count_result[0]) > MAX_TRACE_JSONL_ROWS:
                raise TraceAggregationError("trace input exceeds the row limit")
            cardinality_result = connection.sql(TRACE_CARDINALITY_SQL).fetchone()
            if cardinality_result is None:
                raise TraceAggregationError("trace cardinality validation returned no result")
            offering_count = int(cardinality_result[0])
            work_unit_group_count = int(cardinality_result[1])
            if offering_count > MAX_TRACE_OFFERINGS:
                raise TraceAggregationError("trace input exceeds the distinct offering limit")
            if work_unit_group_count > MAX_TRACE_WORK_UNIT_GROUPS:
                raise TraceAggregationError("trace input exceeds the work-unit group limit")
            invalid_result = connection.sql(TRACE_VALIDATION_SQL).fetchone()
            if invalid_result is None:
                raise TraceAggregationError("trace validation returned no result")
            invalid_rows = int(invalid_result[0])
            if invalid_rows:
                raise TraceAggregationError(
                    f"trace input contains {invalid_rows} invalid canonical usage rows"
                )
            multi_offering_result = connection.sql(MULTI_OFFERING_WORK_UNITS_SQL).fetchone()
            if multi_offering_result is None:
                raise TraceAggregationError("trace offering-scope validation returned no result")
            multi_offering = int(multi_offering_result[0])
            if multi_offering:
                raise TraceAggregationError(
                    f"trace input contains {multi_offering} work units spanning multiple offerings"
                )
            scope_result = connection.sql(INCONSISTENT_SCOPES_SQL).fetchone()
            if scope_result is None:
                raise TraceAggregationError("trace scope validation returned no result")
            inconsistent_scopes = int(scope_result[0])
            if inconsistent_scopes:
                raise TraceAggregationError(
                    f"trace input contains {inconsistent_scopes} work units with overlapping scopes"
                )
            attempt_result = connection.sql(DUPLICATE_ATTEMPT_AGGREGATES_SQL).fetchone()
            if attempt_result is None:
                raise TraceAggregationError("trace attempt validation returned no result")
            duplicate_attempts = int(attempt_result[0])
            if duplicate_attempts:
                raise TraceAggregationError(
                    f"trace input contains {duplicate_attempts} duplicate attempt aggregates"
                )
            inconsistent_result = connection.sql(INCONSISTENT_OUTCOMES_SQL).fetchone()
            if inconsistent_result is None:
                raise TraceAggregationError("trace outcome validation returned no result")
            inconsistent = int(inconsistent_result[0])
            if inconsistent:
                raise TraceAggregationError(
                    f"trace input contains {inconsistent} work units with inconsistent outcomes"
                )
            duplicate_result = connection.sql(DUPLICATE_REQUESTS_SQL).fetchone()
            if duplicate_result is None:
                raise TraceAggregationError("trace duplicate validation returned no result")
            duplicates = int(duplicate_result[0])
            if duplicates:
                raise TraceAggregationError(
                    f"trace input contains {duplicates} duplicate canonical requests"
                )
            provenance_result = connection.sql(INCONSISTENT_PROVENANCE_COVERAGE_SQL).fetchone()
            if provenance_result is None:
                raise TraceAggregationError("trace provenance validation returned no result")
            inconsistent_provenance = int(provenance_result[0])
            if inconsistent_provenance:
                raise TraceAggregationError(
                    "trace input mixes rows with and without producer provenance"
                )
            offering_provenance_result = connection.sql(
                INCONSISTENT_OFFERING_PROVENANCE_SQL
            ).fetchone()
            if offering_provenance_result is None:
                raise TraceAggregationError(
                    "trace offering-provenance validation returned no result"
                )
            inconsistent_offering_provenance = int(offering_provenance_result[0])
            if inconsistent_offering_provenance:
                raise TraceAggregationError(
                    "trace input maps one offering to multiple producer identities"
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
                    "trace input contains "
                    f"{mismatched_workloads} rows outside the expected workload"
                )
            raw_provenance = connection.sql(PROVENANCE_SQL).fetchall()
            try:
                provenance = tuple(
                    TraceProvenance(
                        adapter_id=str(row[0]),
                        adapter_version=str(row[1]),
                        upstream_system=str(row[2]),
                        upstream_version=str(row[3]),
                        upstream_commit=str(row[4]),
                        collector_id=str(row[5]) if row[5] is not None else None,
                        collector_version=str(row[6]) if row[6] is not None else None,
                    )
                    for row in raw_provenance
                )
            except ValidationError:
                raise TraceAggregationError(
                    "trace producer provenance violates the canonical contract"
                ) from None
            resolved_producers = [
                trusted_trace_producer(_provenance_key(item)) for item in provenance
            ]
            if any(producer is None for producer in resolved_producers):
                raise TraceAggregationError(
                    "trace producer provenance is not in the reviewed registry"
                )
            producer_sources = tuple(
                sorted(
                    (producer.source for producer in resolved_producers if producer is not None),
                    key=lambda source: source.id,
                )
            )
            result = connection.sql(AGGREGATION_SQL)
            columns = [description[0] for description in result.description]
            rows: list[tuple[Any, ...]] = []
            while batch := result.fetchmany(TRACE_RESULT_FETCH_BATCH_SIZE):
                rows.extend(batch)
                if len(rows) > MAX_TRACE_OFFERINGS:
                    raise TraceAggregationError("aggregate output exceeds the offering limit")
        except (duckdb.Error, OSError):
            raise TraceAggregationError("cannot aggregate trace input") from None
        finally:
            connection.close()
        return (
            snapshot.raw_sha256,
            provenance,
            producer_sources,
            columns,
            rows,
            detected_schema,
        )


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
    raw_sha256, provenance, producer_sources, columns, rows, detected_schema = (
        _query_trace_snapshot(trace_path, workload)
    )
    source = SourceReference(
        id=source_id or f"trace:sha256:{raw_sha256}",
        version=detected_schema,
        methodology=_trace_source_methodology(producer_sources),
        raw_sha256=raw_sha256,
        retrieved_at=now,
    )

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
            totals: dict[str, Decimal | None] = {}
            for quantity in QUANTITY_UNITS:
                raw_total = values.pop(f"{quantity}_total")
                if raw_total is None:
                    totals[quantity] = None
                    continue
                total = Decimal(str(raw_total))
                totals[quantity] = total
                derived[f"{quantity}_per_work_unit"] = total / work_unit_count
                if successful_work_units != 0:
                    derived[f"{quantity}_per_success"] = total / successful_work_units
            complete_cache_write_raw = values.pop("complete_cache_write_tokens_total")
            complete_cache_write = (
                Decimal(str(complete_cache_write_raw))
                if complete_cache_write_raw is not None
                else None
            )
            cache_components = (
                totals["input_uncached_tokens"],
                totals["input_cache_read_tokens"],
                complete_cache_write,
            )
            if all(value is not None for value in cache_components):
                cache_denominator = sum(
                    (value for value in cache_components if value is not None),
                    Decimal(0),
                )
            else:
                cache_denominator = None
            if cache_denominator is not None and cache_denominator != 0:
                cache_reads = totals["input_cache_read_tokens"]
                if cache_reads is None:
                    raise AssertionError("complete cache meters must include cache reads")
                derived["observed_cache_hit_rate"] = cache_reads / cache_denominator
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
            try:
                signals[signal] = Observation(
                    value=Decimal(str(value)),
                    unit=_unit_for_signal(signal),
                    sample_count=signal_sample_count,
                    observed_at=signal_observed_at.astimezone(UTC),
                    source=source,
                )
            except ValidationError:
                raise TraceAggregationError(
                    "aggregate output violates the canonical observation contract"
                ) from None
        summaries[offering_id] = signals
    try:
        return TraceSummary(
            workload=workload,
            source=source,
            provenance=provenance,
            producer_sources=producer_sources,
            offerings=summaries,
        )
    except ValidationError:
        raise TraceAggregationError(
            "aggregate output violates the canonical trace-summary contract"
        ) from None


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
            f"{len(unknown)} trace offering(s) are absent from the catalog; ids are not echoed"
        )
    offerings: list[OfferingObservation] = []
    for item in catalog.offerings:
        merged = {**item.signals, **summaries.get(item.offering.offering_id, {})}
        offerings.append(item.model_copy(update={"signals": merged}))
    return catalog.model_copy(update={"offerings": offerings})
