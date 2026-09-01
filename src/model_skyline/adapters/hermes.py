"""Read-only, route-bound Hermes Agent aggregate-usage import.

The reviewed Hermes surfaces are work-unit aggregates, not request events:

* ``hermes -z --usage-file`` JSON; and
* schema-v26 SQLite ``sessions`` plus the authoritative
  ``session_model_usage`` ledger.

The SQLite importer supports the ledger-complete subset of schema v26: it sums
every main and auxiliary ledger task, while requiring one exact
model/provider/base-URL route and one consistently present or absent billing
mode.  Upstream ``absolute=True`` session counter replacements do not write a
matching ledger row and are therefore rejected rather than guessed.  The
usage-report importer requires a separate operator attestation because that
report exposes only the final model/provider and cannot prove that fallback or
auxiliary calls stayed on the same route.  Neither importer reads transcript
content.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from model_skyline.models import (
    MAX_SAFE_INTEGER,
    CanonicalDecimal,
    FrozenModel,
    OfferingKey,
    WorkloadReference,
)
from model_skyline.traces import RequestTrace

HERMES_AGENT_COMMIT = "4f22543509d1b91dc45bcb369447126c5eb14fb7"
HERMES_AGENT_VERSION = "0.20.6"
HERMES_SESSION_SCHEMA_VERSION = 26
HERMES_AGENT_LICENSE = "MIT"
HERMES_ADAPTER_VERSION = "2"

_HERMES_SOURCE_ROOT = f"https://github.com/NousResearch/hermes-agent/blob/{HERMES_AGENT_COMMIT}"
HERMES_USAGE_REPORT_SOURCE_URL = f"{_HERMES_SOURCE_ROOT}/hermes_cli/oneshot.py"
HERMES_USAGE_NORMALIZATION_SOURCE_URL = f"{_HERMES_SOURCE_ROOT}/agent/usage_pricing.py"
HERMES_SESSION_SCHEMA_SOURCE_URL = f"{_HERMES_SOURCE_ROOT}/hermes_state_common.py"
HERMES_SESSION_STORAGE_SOURCE_URL = (
    f"{_HERMES_SOURCE_ROOT}/website/docs/developer-guide/session-storage.md"
)
HERMES_ROUTE_IDENTITY_SOURCE_URL = f"{_HERMES_SOURCE_ROOT}/hermes_cli/route_identity.py"

DEFAULT_MAX_USAGE_REPORT_BYTES = 1_000_000
MAX_USAGE_REPORT_BYTES = 16_000_000
MAX_HERMES_STATE_DATABASE_BYTES = 256 * 1024 * 1024
MAX_HERMES_LEDGER_ROWS = 100_000
MAX_HERMES_SQLITE_VM_STEPS = 10_000_000
MAX_HERMES_SQLITE_BACKUP_SECONDS = 30
HERMES_SQLITE_BACKUP_PAGES = 256
MIN_IDENTITY_KEY_BYTES = 16
MAX_SESSION_ID_LENGTH = 1024
_HERMES_COST_ADAPTER: TypeAdapter[Decimal] = TypeAdapter(
    Annotated[
        CanonicalDecimal,
        Field(ge=0, max_digits=38, decimal_places=12),
    ]
)

_WORKLOAD_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OFFERING_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}$")
_ROUTE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,511}$")
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:^|[:._/@+-])(?:"
    r"sk-(?:proj-|ant-|live-)?|"
    r"gh[pousr]_|github_pat_|xox[baprs]-|AIza|hf_|npm_"
    r")[A-Za-z0-9_-]{8,}|(?:AKIA|ASIA)[A-Z0-9]{16}"
)

_REQUIRED_SESSION_COLUMNS = frozenset(
    {
        "id",
        "ended_at",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "tool_call_count",
        "api_call_count",
        "estimated_cost_usd",
        "actual_cost_usd",
        "cost_status",
        "cost_source",
    }
)
_REQUIRED_ROUTE_COLUMNS = frozenset(
    {
        "session_id",
        "model",
        "billing_provider",
        "billing_base_url",
        "billing_mode",
        "task",
        "api_call_count",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "estimated_cost_usd",
        "actual_cost_usd",
        "cost_status",
        "cost_source",
    }
)

_PROVIDER_REPORTED_SOURCES = frozenset({"provider_cost_api", "provider_generation_api"})
_ESTIMATED_SOURCES = frozenset(
    {
        "provider_models_api",
        "official_docs_snapshot",
        "user_override",
        "custom_contract",
    }
)


class HermesAdapterError(ValueError):
    """A Hermes aggregate cannot be imported without inventing semantics."""


class _DuplicateJsonKey(ValueError):
    pass


def _safe_public_identifier(value: str, field: str, *, offering: bool = False) -> str:
    pattern = _OFFERING_IDENTIFIER_RE if offering else _WORKLOAD_IDENTIFIER_RE
    if (
        pattern.fullmatch(value) is None
        or "://" in value
        or "\\" in value
        or _CREDENTIAL_RE.search(value) is not None
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"{field} must be a content-free public identifier")
    return value


def _safe_route_identifier(value: str, field: str) -> str:
    if (
        _ROUTE_IDENTIFIER_RE.fullmatch(value) is None
        or "://" in value
        or "\\" in value
        or _CREDENTIAL_RE.search(value) is not None
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"{field} must be a content-free route identifier")
    return value


def _safe_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("billing_base_url must be a safe absolute HTTP URL") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "?" in value
        or "#" in value
        or "\\" in value
        or any(ord(character) <= 0x20 for character in value)
        or parsed.query
        or parsed.fragment
        or port is not None
        and not 1 <= port <= 65535
        or _CREDENTIAL_RE.search(value) is not None
    ):
        raise ValueError("billing_base_url must be a safe absolute HTTP URL")
    return value


def _canonical_hermes_base_url(value: str) -> str:
    """Match the safe subset of reviewed Hermes route identity equivalence."""

    _safe_base_url(value)
    parsed = urlsplit(value)
    hostname = parsed.hostname
    if hostname is None:  # Kept local even though _safe_base_url rejects this shape.
        raise ValueError("billing_base_url must be a safe absolute HTTP URL")

    scheme = parsed.scheme.lower()
    if "%" in hostname:
        address, zone = hostname.split("%", 1)
        host = f"{address.lower()}%{zone}"
    else:
        host = hostname.lower()
    if parsed.netloc.startswith("[") or ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        host = f"{host}:{port}"

    path = parsed.path[:-1] if parsed.path.endswith("/") else parsed.path
    return urlunsplit((scheme, host, path, "", ""))


class HermesRouteMapping(FrozenModel):
    """Reviewed exact route represented by one canonical offering."""

    offering: OfferingKey
    model: str = Field(strict=True, min_length=1, max_length=512)
    billing_provider: str = Field(strict=True, min_length=1, max_length=512)
    billing_base_url: str = Field(strict=True, min_length=1, max_length=2048)
    billing_mode: str | None = Field(strict=True, min_length=1, max_length=512)
    usage_report_single_route_attested: bool
    service_tier_fulfilled_attested: bool
    route_details_attested: bool

    @model_validator(mode="after")
    def offering_is_the_reviewed_route(self) -> HermesRouteMapping:
        _safe_public_identifier(self.offering.offering_id, "offering.offering_id", offering=True)
        _safe_route_identifier(self.model, "model")
        _safe_route_identifier(self.billing_provider, "billing_provider")
        if self.billing_mode is not None:
            _safe_route_identifier(self.billing_mode, "billing_mode")
        _safe_base_url(self.billing_base_url)
        if self.offering.agent_harness != "hermes-agent":
            raise ValueError("Hermes aggregates require a Hermes Agent offering harness")
        if self.offering.model_id != self.model:
            raise ValueError("Hermes model must match the offering identity")
        if self.offering.provider != self.billing_provider:
            raise ValueError("Hermes billing provider must match the offering identity")
        if self.offering.endpoint != self.billing_base_url:
            raise ValueError("Hermes billing base URL must match the offering endpoint")
        if self.offering.billing_mode != self.billing_mode:
            raise ValueError("Hermes billing mode must match the offering identity")
        if (
            self.offering.service_tier is not None
            and self.service_tier_fulfilled_attested is not True
        ):
            raise ValueError(
                "service_tier_fulfilled_attested is required for a tiered Hermes offering"
            )
        unobservable_fields = (
            self.offering.region,
            self.offering.quantization,
            self.offering.reasoning_effort,
        )
        if (
            any(value is not None for value in unobservable_fields)
            and not self.route_details_attested
        ):
            raise ValueError(
                "route_details_attested is required for offering fields absent from Hermes usage"
            )
        return self


class HermesSessionMapping(FrozenModel):
    """Operator-reviewed meaning for exactly one opaque Hermes session."""

    session_id: str = Field(strict=True, min_length=1, max_length=MAX_SESSION_ID_LENGTH)
    hermes_version: Literal["0.20.6"]
    workload: WorkloadReference
    route: HermesRouteMapping
    work_unit_success: CanonicalDecimal = Field(
        ge=0,
        le=1,
        max_digits=18,
        decimal_places=9,
    )

    @model_validator(mode="after")
    def public_metadata_is_content_free(self) -> Self:
        _safe_public_identifier(self.workload.id, "workload.id")
        _safe_public_identifier(self.workload.version, "workload.version")
        return self


def _validate_identity_key(identity_key: bytes) -> None:
    if not isinstance(identity_key, bytes):
        raise HermesAdapterError("identity_key must be bytes")
    if len(identity_key) < MIN_IDENTITY_KEY_BYTES:
        raise HermesAdapterError(
            f"identity_key must contain at least {MIN_IDENTITY_KEY_BYTES} bytes"
        )


def _opaque_ids(session_id: str, identity_key: bytes) -> tuple[str, str, str]:
    _validate_identity_key(identity_key)
    digest = hmac.new(
        identity_key,
        b"model-skyline:hermes-session:v1\0" + session_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return (
        f"hermes-work-unit-{digest}",
        f"hermes-attempt-{digest}",
        f"hermes-aggregate-{digest}",
    )


def _optional_nonnegative_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise HermesAdapterError(f"Hermes {field} must be an integer or null")
    if not 0 <= value <= MAX_SAFE_INTEGER:
        raise HermesAdapterError(f"Hermes {field} must be a non-negative safe integer")
    return int(value)


def _required_nonnegative_int(value: Any, field: str) -> int:
    parsed = _optional_nonnegative_int(value, field)
    if parsed is None:
        raise HermesAdapterError(f"Hermes {field} cannot be null")
    return parsed


def _optional_nonnegative_decimal(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (bool, float)):
        raise HermesAdapterError(
            f"Hermes {field} must be supplied without binary floating-point conversion"
        )
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise HermesAdapterError(f"Hermes {field} is not a decimal value") from None
    if not parsed.is_finite() or parsed < 0:
        raise HermesAdapterError(f"Hermes {field} must be finite and non-negative")
    try:
        return _HERMES_COST_ADAPTER.validate_python(parsed)
    except ValidationError:
        raise HermesAdapterError(f"Hermes {field} exceeds the canonical cost bounds") from None


def _timezone_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HermesAdapterError(f"{field} must include a timezone")
    return value.astimezone(UTC)


def _epoch_timestamp(value: Any) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HermesAdapterError("Hermes ended_at must be a Unix timestamp")
    if not math.isfinite(value):
        raise HermesAdapterError("Hermes ended_at must be finite")
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise HermesAdapterError(
            "Hermes ended_at is outside the supported timestamp range"
        ) from None


@dataclass(frozen=True, slots=True)
class _CostMeters:
    basis: Literal["provider_reported", "estimated", "included", "unknown"]
    amount: Decimal | None


def _cost_meters(
    *,
    estimated: Decimal | None,
    actual: Decimal | None,
    status: Any,
    source: Any,
    allow_unprovenanced_estimate: bool = False,
) -> _CostMeters:
    if status in {None, "unknown"}:
        if (
            status is None
            and source is None
            and allow_unprovenanced_estimate
            and estimated is not None
            and estimated > 0
            and actual in {None, 0}
        ):
            # Hermes auxiliary accounting names this meter explicitly but
            # currently omits status/source on its ledger row.
            return _CostMeters("estimated", estimated)
        if source not in {None, "none"} or estimated not in {None, 0} or actual not in {None, 0}:
            raise HermesAdapterError("Hermes unknown cost status contradicts its cost values")
        return _CostMeters("unknown", None)
    if not isinstance(status, str) or not isinstance(source, str):
        raise HermesAdapterError("Hermes cost status/source is invalid")
    if status == "actual":
        if source not in _PROVIDER_REPORTED_SOURCES or actual is None:
            raise HermesAdapterError("Hermes actual cost has no provider-reported source")
        return _CostMeters("provider_reported", actual)
    if status == "estimated":
        if source not in _ESTIMATED_SOURCES or estimated is None or actual not in {None, 0}:
            raise HermesAdapterError("Hermes estimated cost has contradictory provenance")
        return _CostMeters("estimated", estimated)
    if status == "included":
        if source != "none" or estimated not in {None, 0} or actual not in {None, 0}:
            raise HermesAdapterError(
                "Hermes included cost must be an explicit zero marginal charge"
            )
        return _CostMeters("included", Decimal(0))
    raise HermesAdapterError("Hermes cost status is unsupported")


def _combine_cost_meters(rows: list[_CostMeters]) -> _CostMeters:
    if not rows:
        return _CostMeters("unknown", None)
    bases = {row.basis for row in rows}
    if "unknown" in bases:
        return _CostMeters("unknown", None)
    amounts = [row.amount for row in rows]
    if any(amount is None for amount in amounts):
        return _CostMeters("unknown", None)
    with localcontext() as context:
        # At most 100k canonical 38-digit inputs need 43 integer digits. Keep
        # additional headroom so aggregation cannot silently use ambient prec=28.
        context.prec = 50
        total = sum((amount for amount in amounts if amount is not None), Decimal(0))
    if bases == {"included"}:
        return _CostMeters("included", total)
    if bases == {"provider_reported"}:
        return _CostMeters("provider_reported", total)
    if bases <= {"provider_reported", "estimated"}:
        # A total containing any estimated component is itself an estimate,
        # even when the remaining components are provider-reported.
        return _CostMeters("estimated", total)
    return _CostMeters("unknown", None)


def _split_output_tokens(
    output_total: int | None,
    reasoning: int | None,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if output_total is not None and reasoning is not None and reasoning > output_total:
        raise HermesAdapterError("Hermes reasoning_tokens cannot exceed output_tokens")
    total_decimal = Decimal(output_total) if output_total is not None else None
    reasoning_decimal = Decimal(reasoning) if reasoning is not None else None
    visible_decimal = (
        Decimal(output_total - reasoning)
        if output_total is not None and reasoning is not None
        else None
    )
    return visible_decimal, reasoning_decimal, total_decimal


def _trace_from_aggregate(
    *,
    mapping: HermesSessionMapping,
    identity_key: bytes,
    timestamp: datetime,
    model_request_count: int | None,
    input_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
    output_tokens: int | None,
    reasoning_tokens: int | None,
    tool_calls: int | None,
    cost: _CostMeters,
) -> RequestTrace:
    work_unit_id, attempt_id, request_id = _opaque_ids(mapping.session_id, identity_key)
    visible_output, reasoning_output, total_output = _split_output_tokens(
        output_tokens,
        reasoning_tokens,
    )
    cost_amount = cost.amount
    try:
        return RequestTrace(
            schema_version="model-skyline/request-trace/v1alpha2",
            timestamp=_timezone_aware(timestamp, "timestamp"),
            workload_id=mapping.workload.id,
            workload_version=mapping.workload.version,
            work_unit_id=work_unit_id,
            offering_id=mapping.route.offering.offering_id,
            request_id=request_id,
            attempt_id=attempt_id,
            observation_unit="work_unit",
            model_request_count=model_request_count,
            adapter_id="model-skyline/hermes-agent-aggregate",
            adapter_version=HERMES_ADAPTER_VERSION,
            upstream_system="nousresearch/hermes-agent",
            upstream_version=HERMES_AGENT_VERSION,
            upstream_commit=HERMES_AGENT_COMMIT,
            work_unit_success=mapping.work_unit_success,
            input_uncached_tokens=(Decimal(input_tokens) if input_tokens is not None else None),
            input_cache_read_tokens=(
                Decimal(cache_read_tokens) if cache_read_tokens is not None else None
            ),
            input_cache_write_tokens=(
                Decimal(cache_write_tokens) if cache_write_tokens is not None else None
            ),
            output_tokens=visible_output,
            reasoning_tokens=reasoning_output,
            output_total_tokens=total_output,
            tool_calls=Decimal(tool_calls) if tool_calls is not None else None,
            estimated_total_cost_usd=(cost_amount if cost.basis == "estimated" else None),
            provider_reported_total_cost_usd=(
                cost_amount if cost.basis == "provider_reported" else None
            ),
            provider_marginal_cost_usd=(cost_amount if cost.basis == "included" else None),
        )
    except ValidationError:
        raise HermesAdapterError("Hermes aggregate exceeds the canonical trace contract") from None


def _reject_constant(_value: str) -> None:
    raise ValueError


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _load_usage_report(path: Path, max_bytes: int) -> dict[str, Any]:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise HermesAdapterError("max_bytes must be an integer")
    if not 1 <= max_bytes <= MAX_USAGE_REPORT_BYTES:
        raise HermesAdapterError(f"max_bytes must be between 1 and {MAX_USAGE_REPORT_BYTES}")
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
        raise HermesAdapterError("cannot read the Hermes usage report") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise HermesAdapterError("Hermes usage report must be a regular file")
        if metadata.st_size > max_bytes:
            raise HermesAdapterError(f"Hermes usage report exceeds {max_bytes} bytes")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            raw = stream.read(max_bytes + 1)
    except OSError:
        raise HermesAdapterError("cannot read the Hermes usage report") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > max_bytes:
        raise HermesAdapterError(f"Hermes usage report exceeds {max_bytes} bytes")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        raise HermesAdapterError("invalid Hermes usage report JSON") from None
    if not isinstance(payload, dict):
        raise HermesAdapterError("Hermes usage report must be a JSON object")
    return payload


def import_hermes_usage_report(
    path: str | Path,
    *,
    mapping: HermesSessionMapping,
    observed_at: datetime,
    identity_key: bytes,
    max_bytes: int = DEFAULT_MAX_USAGE_REPORT_BYTES,
) -> RequestTrace:
    """Import one official ``hermes -z --usage-file`` work-unit aggregate."""

    payload = _load_usage_report(Path(path), max_bytes)
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise HermesAdapterError("Hermes usage report has no non-empty session_id")
    if session_id != mapping.session_id:
        raise HermesAdapterError("Hermes usage report does not match the reviewed session mapping")
    route = mapping.route
    if route.usage_report_single_route_attested is not True:
        raise HermesAdapterError(
            "usage_report_single_route_attested must cover fallback and auxiliary calls"
        )
    if payload.get("model") != route.model or payload.get("provider") != route.billing_provider:
        raise HermesAdapterError("Hermes usage report route does not match the reviewed offering")
    reported_tier = payload.get("service_tier")
    if reported_tier != route.offering.service_tier:
        raise HermesAdapterError("Hermes requested service tier does not match the offering")
    if reported_tier is not None and route.service_tier_fulfilled_attested is not True:
        raise HermesAdapterError(
            "service_tier_fulfilled_attested is required because the report records only intent"
        )

    input_tokens = _optional_nonnegative_int(payload.get("input_tokens"), "input_tokens")
    cache_read_tokens = _optional_nonnegative_int(
        payload.get("cache_read_tokens"), "cache_read_tokens"
    )
    cache_write_tokens = _optional_nonnegative_int(
        payload.get("cache_write_tokens"), "cache_write_tokens"
    )
    output_tokens = _optional_nonnegative_int(payload.get("output_tokens"), "output_tokens")
    reasoning_tokens = _optional_nonnegative_int(
        payload.get("reasoning_tokens"), "reasoning_tokens"
    )
    api_calls = _optional_nonnegative_int(payload.get("api_calls"), "api_calls")
    total_tokens = _optional_nonnegative_int(payload.get("total_tokens"), "total_tokens")
    exclusive_token_buckets = (
        input_tokens,
        cache_read_tokens,
        cache_write_tokens,
        output_tokens,
    )
    if total_tokens is not None and all(value is not None for value in exclusive_token_buckets):
        expected_total = sum(value for value in exclusive_token_buckets if value is not None)
        if total_tokens != expected_total:
            raise HermesAdapterError(
                "Hermes total_tokens disagrees with its mutually exclusive usage buckets"
            )
    amount = _optional_nonnegative_decimal(payload.get("estimated_cost_usd"), "estimated_cost_usd")
    cost = _cost_meters(
        estimated=amount,
        actual=amount if payload.get("cost_status") == "actual" else None,
        status=payload.get("cost_status"),
        source=payload.get("cost_source"),
    )
    return _trace_from_aggregate(
        mapping=mapping,
        identity_key=identity_key,
        timestamp=observed_at,
        model_request_count=api_calls,
        input_tokens=input_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        tool_calls=None,
        cost=cost,
    )


@contextmanager
def _snapshot_state_database(path: Path) -> Iterator[Path]:
    """Back up one bounded, read-only SQLite state into a private exact snapshot.

    The SQLite online-backup API is required here rather than a byte copy: a
    completed Hermes session may have committed pages that are still present
    only in the source WAL.  The source descriptor pins the reviewed regular
    file identity while the read-only SQLite transaction supplies one coherent
    view of the main database and any committed WAL frames.
    """

    path = Path(os.path.abspath(path))

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
        raise HermesAdapterError("cannot open the Hermes state database") from None
    temporary: tempfile.TemporaryDirectory[str] | None = None
    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise HermesAdapterError("Hermes state database must be a regular file")
        if metadata.st_size > MAX_HERMES_STATE_DATABASE_BYTES:
            raise HermesAdapterError("Hermes state database exceeds the byte limit")
        _validate_sqlite_source_family(path, metadata)

        temporary = tempfile.TemporaryDirectory(prefix="model-skyline-hermes-")
        target = Path(temporary.name) / "state.db"

        encoded_path = quote(path.as_posix(), safe="/:")
        source = sqlite3.connect(
            f"file:{encoded_path}?mode=ro",
            uri=True,
            timeout=5,
        )
        source.execute("PRAGMA query_only = ON")
        source.execute("PRAGMA trusted_schema = OFF")
        source.execute("BEGIN")
        source.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
        _validate_sqlite_source_family(path, metadata)

        page_size_result = source.execute("PRAGMA page_size").fetchone()
        page_count_result = source.execute("PRAGMA page_count").fetchone()
        if page_size_result is None or page_count_result is None:
            raise HermesAdapterError("cannot size the Hermes state database")
        page_size = int(page_size_result[0])
        page_count = int(page_count_result[0])
        if (
            page_size <= 0
            or page_count < 0
            or page_size * page_count > MAX_HERMES_STATE_DATABASE_BYTES
        ):
            raise HermesAdapterError("Hermes state database exceeds the byte limit")

        destination = sqlite3.connect(target, timeout=5)
        deadline = time.monotonic() + MAX_HERMES_SQLITE_BACKUP_SECONDS

        def backup_progress(_status: int, _remaining: int, total: int) -> None:
            if total < 0 or total * page_size > MAX_HERMES_STATE_DATABASE_BYTES:
                raise HermesAdapterError("Hermes state database exceeds the byte limit")
            if time.monotonic() > deadline:
                raise HermesAdapterError("Hermes state database backup exceeded the time limit")

        source.backup(
            destination,
            pages=HERMES_SQLITE_BACKUP_PAGES,
            progress=backup_progress,
            sleep=0.01,
        )
        destination.commit()
        destination.close()
        destination = None
        source.rollback()
        source.close()
        source = None
        _validate_sqlite_source_family(path, metadata)
        if target.stat().st_size > MAX_HERMES_STATE_DATABASE_BYTES:
            raise HermesAdapterError("Hermes state database exceeds the byte limit")
        target.chmod(0o600)
        yield target
    except sqlite3.Error:
        raise HermesAdapterError("cannot snapshot the Hermes state database") from None
    except OSError:
        raise HermesAdapterError("cannot snapshot the Hermes state database") from None
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            try:
                source.rollback()
            finally:
                source.close()
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.cleanup()


def _validate_sqlite_source_family(path: Path, pinned: os.stat_result) -> None:
    """Reject replacement, special, symlinked, or oversized SQLite source files."""

    try:
        current = path.stat(follow_symlinks=False)
    except OSError:
        raise HermesAdapterError("Hermes state database changed while being snapshotted") from None
    if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
        pinned.st_dev,
        pinned.st_ino,
    ):
        raise HermesAdapterError("Hermes state database changed while being snapshotted")

    total_bytes = current.st_size
    for suffix in ("-wal", "-shm", "-journal"):
        companion = path.with_name(f"{path.name}{suffix}")
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
            companion_descriptor = os.open(companion, flags)
        except FileNotFoundError:
            continue
        except OSError:
            raise HermesAdapterError("Hermes SQLite companion file is unsafe") from None
        try:
            companion_metadata = os.fstat(companion_descriptor)
            if not stat.S_ISREG(companion_metadata.st_mode):
                raise HermesAdapterError("Hermes SQLite companion file must be regular")
            total_bytes += companion_metadata.st_size
        finally:
            os.close(companion_descriptor)
    if total_bytes > MAX_HERMES_STATE_DATABASE_BYTES:
        raise HermesAdapterError("Hermes state database exceeds the byte limit")


def _read_only_connection(path: Path) -> sqlite3.Connection:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise HermesAdapterError("cannot resolve the Hermes state database") from None
    if not resolved.is_file():
        raise HermesAdapterError("Hermes state database must be a regular file")
    encoded_path = quote(resolved.as_posix(), safe="/:")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{encoded_path}?mode=ro&immutable=1",
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("BEGIN")
        steps = 0

        def progress_handler() -> int:
            nonlocal steps
            steps += 1_000
            return int(steps > MAX_HERMES_SQLITE_VM_STEPS)

        connection.set_progress_handler(progress_handler, 1_000)
    except sqlite3.Error:
        if connection is not None:
            connection.close()
        raise HermesAdapterError("cannot open the Hermes state database read-only") from None
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return frozenset(str(row[1]) for row in rows)


def _validate_session_schema(connection: sqlite3.Connection) -> None:
    try:
        versions = connection.execute("SELECT version FROM schema_version").fetchall()
    except sqlite3.Error:
        raise HermesAdapterError("Hermes state database has no readable schema version") from None
    if len(versions) != 1 or versions[0][0] != HERMES_SESSION_SCHEMA_VERSION:
        raise HermesAdapterError(
            "unsupported Hermes session schema; "
            f"expected exactly version {HERMES_SESSION_SCHEMA_VERSION}"
        )
    if _REQUIRED_SESSION_COLUMNS - _table_columns(
        connection, "sessions"
    ) or _REQUIRED_ROUTE_COLUMNS - _table_columns(connection, "session_model_usage"):
        raise HermesAdapterError("Hermes state database is missing required aggregate columns")


@dataclass(frozen=True, slots=True)
class _LedgerRow:
    model: str
    billing_provider: str
    billing_base_url: str
    billing_mode: str | None
    task: str
    api_call_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int
    cost: _CostMeters


def _ledger_rows(connection: sqlite3.Connection, session_id: str) -> list[_LedgerRow]:
    cursor = connection.execute(
        """
        SELECT
            model, billing_provider, billing_base_url, billing_mode, task,
            api_call_count, input_tokens, output_tokens, cache_read_tokens,
            cache_write_tokens, reasoning_tokens,
            CAST(estimated_cost_usd AS TEXT) AS estimated_cost_text,
            CAST(actual_cost_usd AS TEXT) AS actual_cost_text,
            cost_status, cost_source
        FROM session_model_usage
        WHERE session_id = ?
        ORDER BY model, billing_provider, billing_base_url, billing_mode, task
        """,
        (session_id,),
    )
    raw_rows = cursor.fetchmany(MAX_HERMES_LEDGER_ROWS + 1)
    if len(raw_rows) > MAX_HERMES_LEDGER_ROWS:
        raise HermesAdapterError("Hermes session usage exceeds the ledger row limit")
    rows: list[_LedgerRow] = []
    for raw in raw_rows:
        raw_base_url = raw["billing_base_url"]
        if not isinstance(raw_base_url, str):
            raise HermesAdapterError("Hermes billing_base_url must be text")
        try:
            billing_base_url = _canonical_hermes_base_url(raw_base_url)
        except ValueError:
            raise HermesAdapterError("Hermes billing_base_url is not a safe route URL") from None
        raw_billing_mode = raw["billing_mode"]
        if not isinstance(raw_billing_mode, str):
            raise HermesAdapterError("Hermes billing_mode must be text")
        billing_mode = None if raw_billing_mode == "" else raw_billing_mode
        integers = [
            _required_nonnegative_int(raw[name], name)
            for name in (
                "api_call_count",
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "reasoning_tokens",
            )
        ]
        estimated = _optional_nonnegative_decimal(raw["estimated_cost_text"], "estimated_cost_usd")
        actual = _optional_nonnegative_decimal(raw["actual_cost_text"], "actual_cost_usd")
        rows.append(
            _LedgerRow(
                model=str(raw["model"]),
                billing_provider=str(raw["billing_provider"]),
                billing_base_url=billing_base_url,
                billing_mode=billing_mode,
                task=str(raw["task"]),
                api_call_count=integers[0],
                input_tokens=integers[1],
                output_tokens=integers[2],
                cache_read_tokens=integers[3],
                cache_write_tokens=integers[4],
                reasoning_tokens=integers[5],
                cost=_cost_meters(
                    estimated=estimated,
                    actual=actual,
                    status=raw["cost_status"],
                    source=raw["cost_source"],
                    allow_unprovenanced_estimate=bool(raw["task"]),
                ),
            )
        )
    return rows


def _sum_counter(rows: list[_LedgerRow], field: str) -> int:
    return sum(int(getattr(row, field)) for row in rows)


def _validate_ledger_route(rows: list[_LedgerRow], route: HermesRouteMapping) -> None:
    if not rows:
        raise HermesAdapterError("Hermes session has no authoritative model-usage ledger")
    routes = {
        (row.model, row.billing_provider, row.billing_base_url, row.billing_mode) for row in rows
    }
    if len(routes) != 1:
        raise HermesAdapterError("Hermes session spans multiple billing routes")
    only_route = next(iter(routes))
    if any(not value for value in only_route[:3]):
        raise HermesAdapterError("Hermes session usage has an incomplete billing route")
    if only_route != (
        route.model,
        route.billing_provider,
        _canonical_hermes_base_url(route.billing_base_url),
        route.billing_mode,
    ):
        raise HermesAdapterError("Hermes ledger route does not match the reviewed offering")


def _validate_main_summary(session: sqlite3.Row, main_rows: list[_LedgerRow]) -> None:
    mapping = {
        "api_call_count": "api_call_count",
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cache_read_tokens": "cache_read_tokens",
        "cache_write_tokens": "cache_write_tokens",
        "reasoning_tokens": "reasoning_tokens",
    }
    for session_field, ledger_field in mapping.items():
        session_value = _optional_nonnegative_int(session[session_field], session_field)
        if session_value is None or session_value != _sum_counter(main_rows, ledger_field):
            raise HermesAdapterError(
                "Hermes session summary does not reconcile with the main-loop ledger"
            )


def _import_hermes_session_snapshot(
    path: str | Path,
    *,
    mapping: HermesSessionMapping,
    identity_key: bytes,
) -> RequestTrace:
    _validate_identity_key(identity_key)
    connection = _read_only_connection(Path(path))
    try:
        _validate_session_schema(connection)
        session = connection.execute(
            """
            SELECT
                ended_at, input_tokens, output_tokens, cache_read_tokens,
                cache_write_tokens, reasoning_tokens, tool_call_count,
                api_call_count, CAST(estimated_cost_usd AS TEXT) AS estimated_cost_text,
                CAST(actual_cost_usd AS TEXT) AS actual_cost_text, cost_status, cost_source
            FROM sessions
            WHERE id = ?
            """,
            (mapping.session_id,),
        ).fetchone()
        if session is None:
            raise HermesAdapterError("the reviewed Hermes session was not found")
        if session["ended_at"] is None:
            raise HermesAdapterError("active Hermes sessions cannot be imported")
        rows = _ledger_rows(connection, mapping.session_id)
        _validate_ledger_route(rows, mapping.route)
        main_rows = [row for row in rows if row.task == ""]
        _validate_main_summary(session, main_rows)

        # Validate the legacy sessions cost summary, but never use it as the
        # aggregate authority because it omits auxiliary task rows.
        session_cost = _cost_meters(
            estimated=_optional_nonnegative_decimal(
                session["estimated_cost_text"], "estimated_cost_usd"
            ),
            actual=_optional_nonnegative_decimal(session["actual_cost_text"], "actual_cost_usd"),
            status=session["cost_status"],
            source=session["cost_source"],
        )
        if session_cost != _combine_cost_meters([row.cost for row in main_rows]):
            raise HermesAdapterError(
                "Hermes session cost summary does not reconcile with the main-loop ledger"
            )

        tool_calls = _optional_nonnegative_int(session["tool_call_count"], "tool_call_count")
        return _trace_from_aggregate(
            mapping=mapping,
            identity_key=identity_key,
            timestamp=_epoch_timestamp(session["ended_at"]),
            model_request_count=_sum_counter(rows, "api_call_count"),
            input_tokens=_sum_counter(rows, "input_tokens"),
            cache_read_tokens=_sum_counter(rows, "cache_read_tokens"),
            cache_write_tokens=_sum_counter(rows, "cache_write_tokens"),
            output_tokens=_sum_counter(rows, "output_tokens"),
            reasoning_tokens=_sum_counter(rows, "reasoning_tokens"),
            tool_calls=tool_calls,
            cost=_combine_cost_meters([row.cost for row in rows]),
        )
    except sqlite3.Error:
        raise HermesAdapterError("cannot read the Hermes session aggregate") from None
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def import_hermes_session(
    path: str | Path,
    *,
    mapping: HermesSessionMapping,
    identity_key: bytes,
) -> RequestTrace:
    """Import a completed, ledger-complete v26 session from an exact snapshot.

    Sessions containing counters introduced only through upstream
    ``absolute=True`` updates are outside this strict subset because schema v26
    does not preserve an attributable ledger row for that residual. Committed
    WAL state is captured through a bounded, read-only SQLite online backup.
    """

    if (
        mapping.route.offering.service_tier is not None
        and mapping.route.service_tier_fulfilled_attested is not True
    ):
        raise HermesAdapterError(
            "service_tier_fulfilled_attested is required for a tiered Hermes session"
        )
    with _snapshot_state_database(Path(path)) as snapshot:
        return _import_hermes_session_snapshot(
            snapshot,
            mapping=mapping,
            identity_key=identity_key,
        )
