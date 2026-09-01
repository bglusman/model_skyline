"""Conservative accounting adapter for Claude Agent SDK result messages.

The reviewed upstream contract is ``claude-agent-sdk==0.2.148`` at the
dereferenced release commit below.  The adapter deliberately reads only the
typed ``ResultMessage`` accounting surface and a narrowly validated subset of
its nested ``model_usage`` metadata; it never reads or copies result text,
structured output, tool payloads, transcript paths, working directories,
session identifiers, or environment variables.

Official references:

* https://github.com/anthropics/claude-agent-sdk-python/blob/v0.2.148/src/claude_agent_sdk/types.py
* https://code.claude.com/docs/en/agent-sdk/cost-tracking

The pinned SDK's ``ModelUsage`` ``TypedDict`` does not declare ``costBasis``.
Claude Code's cost-tracking documentation says CLI versions 2.1.246 and newer
emit that key at runtime.  This adapter pins CLI 2.1.251 and validates
``costBasis`` when present as a runtime extension; it does not claim that the
SDK 0.2.148 static type guarantees the field.

``model_usage`` is cumulative for the query (or current streaming-input reset
segment), covers main-agent, subagent, and internal query-pipeline model calls,
and is keyed by model.  It is not a request event.  Callers must therefore
provide workload identity and outcome explicitly and attest that the supplied
message is the final cumulative result for the accounting segment.  SDK cost
fields are client estimates, so they map only to estimated total cost, never
authoritative billed cost.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from pydantic import ValidationError, model_validator

from model_skyline.models import FrozenModel, OfferingKey
from model_skyline.traces import RequestTrace

CLAUDE_AGENT_SDK_VERSION = "0.2.148"
CLAUDE_AGENT_SDK_COMMIT = "af5ff1b9f2f279575f89b78f17572c6e35fbc2b6"
CLAUDE_CODE_CLI_VERSION = "2.1.251"
CLAUDE_ADAPTER_VERSION = "2"
CLAUDE_AGENT_SDK_TYPES_URL = (
    "https://raw.githubusercontent.com/anthropics/claude-agent-sdk-python/"
    f"{CLAUDE_AGENT_SDK_COMMIT}/src/claude_agent_sdk/types.py"
)
CLAUDE_AGENT_SDK_COST_TRACKING_URL = "https://code.claude.com/docs/en/agent-sdk/cost-tracking"

_USD_QUANTUM = Decimal("0.000000000001")
_MODEL_USAGE_COUNT_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheCreationInputTokens",
    "webSearchRequests",
)
_MODEL_USAGE_CAPACITY_FIELDS = ("contextWindow", "maxOutputTokens")
_TERMINAL_RESULT_SUBTYPES = frozenset(
    {
        "success",
        "error_max_turns",
        "error_during_execution",
        "error_max_budget_usd",
        "error_max_structured_output_retries",
    }
)
_OPAQUE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,191}$")
_OFFERING_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}$")
_MODEL_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,255}$")
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:sk-(?:proj-|ant-|live-)?|gh[pousr]_|github_pat_|xox[baprs]-|AIza)"
    r"[A-Za-z0-9_-]{8,}"
)


class ClaudeAdapterError(ValueError):
    """A Claude result cannot be represented without inventing accounting data."""


class ClaudeResultMessageLike(Protocol):
    """Safe, dependency-free subset of the SDK's typed ``ResultMessage``."""

    @property
    def total_cost_usd(self) -> float | None: ...

    @property
    def model_usage(self) -> Mapping[str, Mapping[str, Any]] | None: ...

    @property
    def subtype(self) -> str: ...

    @property
    def is_error(self) -> bool: ...


class ClaudeRouteMapping(FrozenModel):
    """Whole-segment binding from Claude's route vocabulary to one offering.

    The attestation covers every main-agent, subagent, fallback, and internal
    model call represented by the cumulative result, including a stable
    provider and pricing basis for the entire accounting segment.  It also
    supplies the route identity when the SDK's optional ``canonicalModel`` or
    ``provider`` fields are absent.
    """

    offering: OfferingKey
    model_usage_key: str
    upstream_provider: str
    single_route_and_pricing_basis_attested: bool
    route_details_attested: bool

    @model_validator(mode="after")
    def route_is_content_free_and_explicit(self) -> ClaudeRouteMapping:
        _identifier(self.offering.offering_id, field="offering.offering_id", offering=True)
        _model_key(self.offering.model_id, field="offering.model_id")
        _identifier(self.offering.provider, field="offering.provider")
        _model_key(self.model_usage_key, field="model_usage_key")
        _model_key(self.upstream_provider, field="upstream_provider")
        if self.offering.agent_harness != "claude-agent-sdk":
            raise ValueError("Claude results require a Claude Agent SDK offering harness")
        if self.single_route_and_pricing_basis_attested is not True:
            raise ValueError(
                "single_route_and_pricing_basis_attested must explicitly cover the whole segment"
            )
        unobservable_route_fields = (
            self.offering.endpoint,
            self.offering.billing_mode,
            self.offering.region,
            self.offering.service_tier,
            self.offering.quantization,
            self.offering.reasoning_effort,
        )
        if (
            any(value is not None for value in unobservable_route_fields)
            and self.route_details_attested is not True
        ):
            raise ValueError(
                "route_details_attested is required for offering fields absent from Claude usage"
            )
        return self


@dataclass(frozen=True, slots=True)
class _ClaudeModelUsage:
    input_uncached_tokens: Decimal
    input_cache_read_tokens: Decimal
    input_cache_write_tokens: Decimal
    output_total_tokens: Decimal
    web_search_calls: Decimal
    cost_usd: Decimal


def _nonnegative_count(container: Mapping[str, Any], field: str) -> Decimal:
    if field not in container:
        raise ClaudeAdapterError(f"Claude model_usage is missing required field {field!r}")
    value = container[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ClaudeAdapterError(f"Claude model_usage field {field!r} must be an integer")
    if value < 0:
        raise ClaudeAdapterError(f"Claude model_usage field {field!r} cannot be negative")
    if len(str(value)) > 38:
        raise ClaudeAdapterError(f"Claude model_usage field {field!r} exceeds 38 digits")
    return Decimal(value)


def _positive_capacity(container: Mapping[str, Any], field: str) -> None:
    value = _nonnegative_count(container, field)
    if value == 0:
        raise ClaudeAdapterError(f"Claude model_usage field {field!r} must be positive")


def _usd(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClaudeAdapterError(f"Claude {field} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ClaudeAdapterError(f"Claude {field} must be finite")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - guarded primitive types
        raise ClaudeAdapterError(f"Claude {field} is not a valid decimal amount") from exc
    if not amount.is_finite() or amount < 0:
        raise ClaudeAdapterError(f"Claude {field} must be finite and nonnegative")
    try:
        return amount.quantize(_USD_QUANTUM, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise ClaudeAdapterError(f"Claude {field} exceeds supported USD precision") from exc


def _identifier(value: Any, *, field: str, offering: bool = False) -> str:
    pattern = _OFFERING_IDENTIFIER_RE if offering else _OPAQUE_IDENTIFIER_RE
    if (
        not isinstance(value, str)
        or pattern.fullmatch(value) is None
        or "://" in value
        or "\\" in value
        or _CREDENTIAL_RE.search(value) is not None
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise ClaudeAdapterError(f"{field} must be a content-free opaque identifier")
    return value


def _model_key(value: Any, *, field: str = "expected_model_key") -> str:
    if (
        not isinstance(value, str)
        or _MODEL_KEY_RE.fullmatch(value) is None
        or "://" in value
        or "\\" in value
        or _CREDENTIAL_RE.search(value) is not None
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise ClaudeAdapterError(f"{field} must be a content-free model identifier")
    return value


def _single_model_usage_entry(
    message: ClaudeResultMessageLike,
    *,
    route: ClaudeRouteMapping,
    require_priced_basis: bool = True,
) -> Mapping[str, Any] | None:
    model_usage = message.model_usage
    if model_usage is None:
        return None
    if not isinstance(model_usage, Mapping):
        raise ClaudeAdapterError("Claude ResultMessage.model_usage must be a mapping")
    if not model_usage:
        return None
    if len(model_usage) != 1:
        raise ClaudeAdapterError(
            "Claude ResultMessage uses multiple models; one offering cannot represent "
            "the aggregate without an explicit compound-offering policy"
        )
    model_key, raw_usage = next(iter(model_usage.items()))
    model_key = _model_key(model_key, field="Claude model_usage key")
    if model_key != route.model_usage_key:
        raise ClaudeAdapterError("Claude model_usage key does not match the reviewed route")
    if not isinstance(raw_usage, Mapping):
        raise ClaudeAdapterError("Claude model_usage entry must be a mapping")
    if "canonicalModel" in raw_usage:
        canonical_model = _model_key(raw_usage["canonicalModel"], field="canonicalModel")
        if canonical_model != route.offering.model_id:
            raise ClaudeAdapterError("Claude canonicalModel does not match the offering identity")
    if "provider" in raw_usage:
        provider = _model_key(raw_usage["provider"], field="provider")
        if provider != route.upstream_provider:
            raise ClaudeAdapterError("Claude model_usage provider does not match the route mapping")
    if require_priced_basis and "costBasis" in raw_usage:
        cost_basis = raw_usage["costBasis"]
        if not isinstance(cost_basis, str) or cost_basis not in {"list", "managed"}:
            raise ClaudeAdapterError("Claude model_usage costBasis is not a priced basis")
    return raw_usage


def _extract_single_model_usage(
    message: ClaudeResultMessageLike,
    *,
    route: ClaudeRouteMapping,
) -> _ClaudeModelUsage:
    raw_usage = _single_model_usage_entry(message, route=route)
    if raw_usage is None:
        raise ClaudeAdapterError("Claude ResultMessage.model_usage is required")

    counts = {field: _nonnegative_count(raw_usage, field) for field in _MODEL_USAGE_COUNT_FIELDS}
    for field in _MODEL_USAGE_CAPACITY_FIELDS:
        _positive_capacity(raw_usage, field)
    if "costUSD" not in raw_usage:
        raise ClaudeAdapterError("Claude model_usage is missing required field 'costUSD'")
    model_cost = _usd(raw_usage["costUSD"], field="model_usage costUSD")
    if message.total_cost_usd is None:
        raise ClaudeAdapterError("Claude ResultMessage.total_cost_usd is required")
    total_cost = _usd(message.total_cost_usd, field="ResultMessage.total_cost_usd")
    if model_cost != total_cost:
        raise ClaudeAdapterError(
            "Claude single-model costUSD does not match ResultMessage.total_cost_usd"
        )

    return _ClaudeModelUsage(
        input_uncached_tokens=counts["inputTokens"],
        input_cache_read_tokens=counts["cacheReadInputTokens"],
        input_cache_write_tokens=counts["cacheCreationInputTokens"],
        output_total_tokens=counts["outputTokens"],
        web_search_calls=counts["webSearchRequests"],
        cost_usd=model_cost,
    )


def adapt_claude_result(
    message: ClaudeResultMessageLike,
    *,
    sdk_version: str,
    claude_code_version: str,
    final_cumulative_result: bool,
    accounting_scope: Literal["single_query", "post_reset_segment"],
    timestamp: datetime,
    workload_id: str,
    workload_version: str,
    work_unit_id: str,
    route: ClaudeRouteMapping,
    result_id: str,
    attempt_id: str,
    work_unit_success: Decimal,
) -> RequestTrace:
    """Convert one final, single-model Claude result aggregate to a trace row.

    ``ResultMessage.model_usage`` is whole-query accounting rather than one
    model request, so the returned row is explicitly attempt-scoped and leaves
    ``model_request_count`` unknown.  ``outputTokens`` is mapped to the
    inclusive ``output_total_tokens`` meter because the result does not expose
    a trustworthy visible-output/reasoning split.  Aggregate cache creation is
    mapped to the retention-neutral cache-write meter; the typed SDK result
    does not break per-model writes into 5-minute and 1-hour buckets.

    The caller must supply identifiers, judged outcome, timestamp, installed
    SDK version, final-result attestation, and an explicitly attested route and
    pricing mapping.  Optional upstream route fields are cross-checked when
    present; the caller mapping binds the route when they are absent.  None are
    derived from Claude's session id, result content, tool payloads, or other
    potentially sensitive message fields.
    """

    if sdk_version != CLAUDE_AGENT_SDK_VERSION:
        raise ClaudeAdapterError(
            f"unsupported Claude Agent SDK version; expected {CLAUDE_AGENT_SDK_VERSION}"
        )
    if claude_code_version != CLAUDE_CODE_CLI_VERSION:
        raise ClaudeAdapterError(
            f"unsupported Claude Code CLI version; expected {CLAUDE_CODE_CLI_VERSION}"
        )
    if final_cumulative_result is not True:
        raise ClaudeAdapterError(
            "final_cumulative_result must explicitly attest the last result in the segment"
        )
    if accounting_scope not in {"single_query", "post_reset_segment"}:
        raise ClaudeAdapterError("accounting_scope must identify one complete accounting segment")
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ClaudeAdapterError("timestamp must be a timezone-aware datetime")
    try:
        offset = timestamp.utcoffset()
    except (OverflowError, ValueError) as exc:
        raise ClaudeAdapterError("timestamp has an invalid timezone offset") from exc
    if offset is None:
        raise ClaudeAdapterError("timestamp must be a timezone-aware datetime")
    if not isinstance(work_unit_success, Decimal):
        raise ClaudeAdapterError("work_unit_success must be an explicit Decimal outcome")
    if not work_unit_success.is_finite() or not Decimal(0) <= work_unit_success <= Decimal(1):
        raise ClaudeAdapterError("work_unit_success must be finite and between zero and one")

    safe_workload_id = _identifier(workload_id, field="workload_id")
    safe_workload_version = _identifier(workload_version, field="workload_version")
    safe_work_unit_id = _identifier(work_unit_id, field="work_unit_id")
    safe_result_id = _identifier(result_id, field="result_id")
    safe_attempt_id = _identifier(attempt_id, field="attempt_id")

    try:
        subtype = message.subtype
        is_error = message.is_error
    except AttributeError:
        raise ClaudeAdapterError("input must be a typed Claude ResultMessage") from None
    if not isinstance(subtype, str) or not isinstance(is_error, bool):
        raise ClaudeAdapterError("Claude ResultMessage terminal status is invalid")
    if subtype not in _TERMINAL_RESULT_SUBTYPES:
        raise ClaudeAdapterError("Claude ResultMessage terminal subtype is unsupported")
    if subtype != "success" and is_error is not True:
        raise ClaudeAdapterError("Claude error result subtype must set is_error true")

    if subtype == "error_during_execution":
        # Claude's documented crash result may zero every accounting field.  A
        # failure row is still valuable, but those zeroes are not measurements.
        # Validate any identity evidence that did survive so a conflicting or
        # multi-model crash cannot be attributed to the attested offering.
        try:
            _single_model_usage_entry(message, route=route, require_priced_basis=False)
        except AttributeError:
            raise ClaudeAdapterError("input must be a typed Claude ResultMessage") from None
        usage = None
        safe_offering = route.offering
    else:
        try:
            usage = _extract_single_model_usage(message, route=route)
        except AttributeError:
            raise ClaudeAdapterError("input must be a typed Claude ResultMessage") from None
        safe_offering = route.offering

    try:
        return RequestTrace(
            schema_version="model-skyline/request-trace/v1alpha2",
            timestamp=timestamp,
            workload_id=safe_workload_id,
            workload_version=safe_workload_version,
            work_unit_id=safe_work_unit_id,
            offering_id=safe_offering.offering_id,
            request_id=safe_result_id,
            attempt_id=safe_attempt_id,
            observation_unit="attempt",
            model_request_count=None,
            adapter_id="model-skyline/claude-agent-sdk-result",
            adapter_version=CLAUDE_ADAPTER_VERSION,
            upstream_system="anthropics/claude-agent-sdk-python+claude-code",
            upstream_version=(f"{CLAUDE_AGENT_SDK_VERSION}+cli.{CLAUDE_CODE_CLI_VERSION}"),
            upstream_commit=CLAUDE_AGENT_SDK_COMMIT,
            work_unit_success=work_unit_success,
            input_uncached_tokens=(usage.input_uncached_tokens if usage is not None else None),
            input_cache_read_tokens=(usage.input_cache_read_tokens if usage is not None else None),
            input_cache_write_tokens=(
                usage.input_cache_write_tokens if usage is not None else None
            ),
            output_total_tokens=(usage.output_total_tokens if usage is not None else None),
            web_search_calls=(usage.web_search_calls if usage is not None else None),
            estimated_total_cost_usd=(usage.cost_usd if usage is not None else None),
        )
    except ValidationError:
        raise ClaudeAdapterError(
            "Claude accounting row exceeds the canonical trace contract"
        ) from None
