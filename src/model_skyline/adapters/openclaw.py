"""Convert a content-free OpenClaw model-call projection to a canonical trace.

The adapter deliberately does not accept transcript entries or complete plugin
hook payloads.  Its input is a small, fail-closed projection of OpenClaw's
public diagnostic ``model.call.completed`` / ``model.call.error`` events.  A
trusted local collector must add the workload, offering, and judged work-unit
outcome before calling :func:`adapt_openclaw_event`.

OpenClaw keeps prompt/response/tool content in a separate private diagnostic
channel.  This module accepts none of those fields, nor session keys, paths,
environment values, raw errors, or cost estimates.

OpenClaw's ``timeToFirstByteMs`` means time to its first observable streamed
event, not necessarily time to the first token.  ``durationMs`` is full call
duration.  Both are coherence-checked but deliberately not mapped to canonical
``ttft_ms`` or output-token throughput.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from model_skyline.canonical import canonical_bytes
from model_skyline.models import CanonicalDecimal, FrozenModel, OfferingKey, SafeCount
from model_skyline.traces import RequestTrace

OPENCLAW_REVIEWED_COMMIT: Final = "2a6c333225e5c886bfd630e36037fb7b206408ef"
OPENCLAW_REVIEWED_VERSION: Final = "2026.8.1"
OPENCLAW_TRACE_SCHEMA_VERSION: Final = "model-skyline/openclaw-model-call/v1alpha2"
OPENCLAW_COLLECTOR_ID: Final = "model-skyline/openclaw-trusted-projector"
OPENCLAW_COLLECTOR_VERSION: Final = "1"
MIN_COLLECTOR_KEY_BYTES: Final = 16
OPENCLAW_PACKAGE_URL = (
    f"https://github.com/openclaw/openclaw/blob/{OPENCLAW_REVIEWED_COMMIT}/package.json"
)
OPENCLAW_DIAGNOSTIC_TYPES_URL = (
    "https://github.com/openclaw/openclaw/blob/"
    f"{OPENCLAW_REVIEWED_COMMIT}/src/infra/diagnostic-events.ts"
)
OPENCLAW_USAGE_NORMALIZATION_URL = (
    f"https://github.com/openclaw/openclaw/blob/{OPENCLAW_REVIEWED_COMMIT}/src/agents/usage.ts"
)
OPENCLAW_OTEL_DOCS_URL = (
    "https://github.com/openclaw/openclaw/blob/"
    f"{OPENCLAW_REVIEWED_COMMIT}/docs/gateway/opentelemetry.md"
)

_OPAQUE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$"
_WORKLOAD_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_OFFERING_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}$"
_PROVIDER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$"
_MODEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$"
_ERROR_CATEGORY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:^|[:._/@+-])(?:"
    r"sk-(?:proj-|ant-|live-)?|"
    r"gh[pousr]_|github_pat_|xox[baprs]-|AIza|hf_|npm_"
    r")[A-Za-z0-9_-]{8,}|(?:AKIA|ASIA)[A-Z0-9]{16}"
)

OpaqueIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=192, pattern=_OPAQUE_ID_PATTERN),
]
WorkloadIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=128, pattern=_WORKLOAD_ID_PATTERN),
]
OfferingIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=512, pattern=_OFFERING_ID_PATTERN),
]
ProviderIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=128, pattern=_PROVIDER_PATTERN),
]
ModelIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=256, pattern=_MODEL_PATTERN),
]
ErrorCategory = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=128, pattern=_ERROR_CATEGORY_PATTERN),
]


class OpenClawAdapterError(ValueError):
    """An OpenClaw event is unsupported, incomplete, or unsafe to normalize."""


def _safe_metadata_value(value: str) -> str:
    if "://" in value or "\\" in value or _CREDENTIAL_RE.search(value):
        raise ValueError("metadata must not contain URLs, paths, or credential-shaped values")
    if any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("metadata must not contain relative path segments")
    return value


class OpenClawUsage(FrozenModel):
    """Normalized buckets retained independently when present upstream."""

    input: SafeCount | None = None
    output: SafeCount | None = None
    cache_read: SafeCount | None = Field(default=None, alias="cacheRead")
    cache_write: SafeCount | None = Field(default=None, alias="cacheWrite")
    reasoning_tokens: SafeCount | None = Field(default=None, alias="reasoningTokens")

    @model_validator(mode="after")
    def reasoning_is_an_output_detail(self) -> OpenClawUsage:
        if (
            self.reasoning_tokens is not None
            and self.output is not None
            and self.reasoning_tokens > self.output
        ):
            raise ValueError("reasoningTokens cannot exceed output")
        return self


class _OpenClawModelCallBase(FrozenModel):
    ts: SafeCount
    seq: SafeCount
    run_id: OpaqueIdentifier = Field(alias="runId")
    call_id: OpaqueIdentifier = Field(alias="callId")
    provider: ProviderIdentifier
    model: ModelIdentifier
    api: ProviderIdentifier | None = None
    transport: ProviderIdentifier | None = None
    observation_unit: Literal["request"] = Field(alias="observationUnit")
    duration_ms: SafeCount = Field(alias="durationMs")
    time_to_first_byte_ms: SafeCount | None = Field(default=None, alias="timeToFirstByteMs")
    usage: OpenClawUsage | None = None

    @field_validator("run_id", "call_id", "provider", "model", "api", "transport")
    @classmethod
    def metadata_is_content_free(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_metadata_value(value)

    @model_validator(mode="after")
    def timing_is_coherent(self) -> _OpenClawModelCallBase:
        if self.time_to_first_byte_ms is not None and self.time_to_first_byte_ms > self.duration_ms:
            raise ValueError("timeToFirstByteMs cannot exceed durationMs")
        return self


class OpenClawModelCallCompleted(_OpenClawModelCallBase):
    type: Literal["model.call.completed"]


class OpenClawModelCallError(_OpenClawModelCallBase):
    type: Literal["model.call.error"]
    error_category: ErrorCategory = Field(alias="errorCategory")
    failure_kind: (
        Literal[
            "aborted",
            "connection_closed",
            "connection_reset",
            "terminated",
            "timeout",
        ]
        | None
    ) = Field(default=None, alias="failureKind")

    @field_validator("error_category")
    @classmethod
    def error_category_is_content_free(cls, value: str) -> str:
        return _safe_metadata_value(value)


OpenClawTerminalEvent = Annotated[
    OpenClawModelCallCompleted | OpenClawModelCallError,
    Field(discriminator="type"),
]


class OpenClawTraceEnvelope(FrozenModel):
    """Operator-enriched, content-free projection accepted by this adapter."""

    schema_version: Literal["model-skyline/openclaw-model-call/v1alpha2"]
    openclaw_version: Literal["2026.8.1"]
    collector_id: Literal["model-skyline/openclaw-trusted-projector"]
    collector_version: Literal["1"]
    collector_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    workload_id: WorkloadIdentifier
    workload_version: WorkloadIdentifier
    work_unit_id: OpaqueIdentifier
    work_unit_success: CanonicalDecimal = Field(ge=0, le=1, max_digits=18, decimal_places=9)
    event: OpenClawTerminalEvent

    @field_validator(
        "workload_id",
        "workload_version",
        "work_unit_id",
    )
    @classmethod
    def envelope_metadata_is_content_free(cls, value: str) -> str:
        return _safe_metadata_value(value)


def _validate_collector_key(collector_key: bytes) -> None:
    if not isinstance(collector_key, bytes):
        raise OpenClawAdapterError("collector_key must be bytes")
    if len(collector_key) < MIN_COLLECTOR_KEY_BYTES:
        raise OpenClawAdapterError(
            f"collector_key must contain at least {MIN_COLLECTOR_KEY_BYTES} bytes"
        )


def compute_openclaw_projection_signature(
    payload: Mapping[str, Any],
    *,
    collector_key: bytes,
) -> str:
    """Compute the domain-separated HMAC used by a trusted local projector.

    The projector must first verify OpenClaw's in-process object-identity
    provenance (trusted diagnostic metadata and ended core model lifecycle),
    remove private fields, then sign this exact safe envelope.  RFC 8785 makes
    the signature reproducible from TypeScript or another collector language.
    """

    _validate_collector_key(collector_key)
    try:
        envelope = OpenClawTraceEnvelope.model_validate(payload)
    except ValidationError:
        raise OpenClawAdapterError(
            "OpenClaw event failed content-free safe-envelope validation"
        ) from None
    return _projection_signature(envelope, collector_key=collector_key)


def _projection_signature(
    envelope: OpenClawTraceEnvelope,
    *,
    collector_key: bytes,
) -> str:
    material = envelope.model_dump(
        mode="json",
        by_alias=True,
        exclude={"collector_signature"},
    )
    return hmac.new(
        collector_key,
        b"model-skyline:openclaw-trusted-projector:v1\0" + canonical_bytes(material),
        hashlib.sha256,
    ).hexdigest()


def _event_timestamp(epoch_milliseconds: int) -> datetime:
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=epoch_milliseconds)
    except OverflowError:
        raise OpenClawAdapterError(
            "OpenClaw event timestamp is outside the supported range"
        ) from None


def _pseudonymous_identifier(
    kind: Literal["attempt", "request"],
    *parts: str,
    collector_key: bytes,
) -> str:
    material = bytearray(b"model-skyline:openclaw-trace-id:v1\0")
    for part in (OPENCLAW_TRACE_SCHEMA_VERSION, OPENCLAW_REVIEWED_VERSION, kind, *parts):
        encoded = part.encode("ascii")
        material.extend(len(encoded).to_bytes(4, "big"))
        material.extend(encoded)
    digest = hmac.new(collector_key, bytes(material), hashlib.sha256).hexdigest()
    return f"openclaw:{kind}:hmac-sha256:{digest}"


def _validated_route(
    offering: OfferingKey,
    event: _OpenClawModelCallBase,
    *,
    expected_api: str | None,
    expected_transport: str | None,
    route_details_attested: bool,
) -> OfferingKey:
    if not isinstance(offering, OfferingKey):
        raise OpenClawAdapterError("offering must be a validated OfferingKey")
    if (
        re.fullmatch(_OFFERING_ID_PATTERN, offering.offering_id) is None
        or re.fullmatch(_PROVIDER_PATTERN, offering.provider) is None
        or re.fullmatch(_MODEL_PATTERN, offering.model_id) is None
    ):
        raise OpenClawAdapterError("offering identity must be content-free") from None
    try:
        _safe_metadata_value(offering.offering_id)
    except ValueError:
        raise OpenClawAdapterError("offering identity must be content-free") from None
    if offering.provider != event.provider or offering.model_id != event.model:
        raise OpenClawAdapterError("OpenClaw runtime route does not match the offering identity")
    if event.api != expected_api or event.transport != expected_transport:
        raise OpenClawAdapterError("OpenClaw API/transport does not match the reviewed route")
    if offering.agent_harness != "openclaw":
        raise OpenClawAdapterError("OpenClaw events require an OpenClaw offering harness")
    unobservable_route_fields = (
        offering.endpoint,
        offering.billing_mode,
        offering.region,
        offering.service_tier,
        offering.quantization,
        offering.reasoning_effort,
    )
    if (
        any(value is not None for value in unobservable_route_fields)
        and route_details_attested is not True
    ):
        raise OpenClawAdapterError(
            "route_details_attested is required for offering fields absent from the event"
        )
    return offering


def adapt_openclaw_event(
    payload: Mapping[str, Any],
    *,
    offering: OfferingKey,
    collector_key: bytes,
    expected_api: str | None,
    expected_transport: str | None,
    route_details_attested: bool,
) -> RequestTrace:
    """Validate one safe request-level event and return one canonical trace row.

    ``observationUnit`` must explicitly be ``request``; OpenClaw's synthetic
    ``turn`` observations are aggregates and therefore cannot become canonical
    request rows. Missing usage or buckets stay unknown, so pre-usage failures
    remain in request/reliability counts without inventing zero cost.

    OpenClaw defines ``reasoningTokens`` as a detail within ``output``.  The
    canonical row separates the two to avoid double counting.  Run and call ids
    are domain-separated and hashed before they leave this adapter.  OpenClaw
    TTFB and call duration remain unmapped because neither is a canonical TTFT
    or steady-state token-throughput observation.
    """

    try:
        envelope = OpenClawTraceEnvelope.model_validate(payload)
    except ValidationError:
        # ValidationError normally includes the rejected input value.  Do not
        # chain or interpolate it: an unexpected field may itself hold prompt
        # text, a path, or a credential.
        raise OpenClawAdapterError(
            "OpenClaw event failed content-free safe-envelope validation"
        ) from None

    _validate_collector_key(collector_key)
    expected_signature = _projection_signature(envelope, collector_key=collector_key)
    if not hmac.compare_digest(envelope.collector_signature, expected_signature):
        raise OpenClawAdapterError("OpenClaw collector signature is invalid")
    event = envelope.event
    safe_offering = _validated_route(
        offering,
        event,
        expected_api=expected_api,
        expected_transport=expected_transport,
        route_details_attested=route_details_attested,
    )
    usage = event.usage
    visible_output_tokens = (
        usage.output - usage.reasoning_tokens
        if usage is not None and usage.output is not None and usage.reasoning_tokens is not None
        else None
    )
    return RequestTrace(
        schema_version="model-skyline/request-trace/v1alpha2",
        timestamp=_event_timestamp(event.ts),
        workload_id=envelope.workload_id,
        workload_version=envelope.workload_version,
        work_unit_id=envelope.work_unit_id,
        offering_id=safe_offering.offering_id,
        request_id=_pseudonymous_identifier(
            "request",
            event.run_id,
            event.call_id,
            collector_key=collector_key,
        ),
        attempt_id=_pseudonymous_identifier(
            "attempt",
            event.run_id,
            collector_key=collector_key,
        ),
        observation_unit="request",
        model_request_count=1,
        adapter_id="model-skyline/openclaw-model-call",
        adapter_version="1alpha2",
        upstream_system="openclaw/openclaw",
        upstream_version=OPENCLAW_REVIEWED_VERSION,
        upstream_commit=OPENCLAW_REVIEWED_COMMIT,
        collector_id=envelope.collector_id,
        collector_version=envelope.collector_version,
        work_unit_success=envelope.work_unit_success,
        input_uncached_tokens=(
            Decimal(usage.input) if usage is not None and usage.input is not None else None
        ),
        input_cache_read_tokens=(
            Decimal(usage.cache_read)
            if usage is not None and usage.cache_read is not None
            else None
        ),
        input_cache_write_tokens=(
            Decimal(usage.cache_write)
            if usage is not None and usage.cache_write is not None
            else None
        ),
        output_tokens=(
            Decimal(visible_output_tokens) if visible_output_tokens is not None else None
        ),
        reasoning_tokens=(
            Decimal(usage.reasoning_tokens)
            if usage is not None and usage.reasoning_tokens is not None
            else None
        ),
        output_total_tokens=(
            Decimal(usage.output) if usage is not None and usage.output is not None else None
        ),
    )
