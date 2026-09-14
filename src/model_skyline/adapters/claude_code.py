"""Private-file adapter for Claude Code ``--output-format json`` results.

Claude Code's JSON result contains response text, session identifiers,
permission-denial tool inputs, and sometimes structured output.  This adapter
parses that private file only far enough to validate the reviewed terminal
shape and returns a content-free aggregate accounting trace.  It never copies
those content-bearing fields into the canonical row or into an exception.

The accepted contract is deliberately narrow:

* Claude Code ``2.1.220`` (embedded build SHA
  ``4073f59596e272f39393db4f96abc5f4b10eff21``);
* one complete ``claude -p --output-format json`` result object; and
* one model-usage key for the whole invocation, with an explicit route and
  pricing-basis attestation.

The release's embedded result schema and a locally exercised failure result
use camel-case ``modelUsage`` even though the Python Agent SDK exposes
``model_usage``.  The two formats must not be silently interchanged.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import ValidationError, model_validator

from model_skyline.models import MAX_SAFE_INTEGER, FrozenModel, OfferingKey
from model_skyline.traces import RequestTrace

CLAUDE_CODE_REVIEWED_RELEASES: Final[dict[str, str]] = {
    "2.1.220": "4073f59596e272f39393db4f96abc5f4b10eff21",
}
CLAUDE_CODE_HEADLESS_URL: Final = "https://code.claude.com/docs/en/headless"
CLAUDE_CODE_RELEASE_URLS: Final[dict[str, str]] = {
    version: f"https://github.com/anthropics/claude-code/releases/tag/v{version}"
    for version in CLAUDE_CODE_REVIEWED_RELEASES
}
MAX_CLAUDE_CODE_JSON_BYTES: Final = 64 * 1024 * 1024
MAX_CLAUDE_CODE_JSON_DEPTH: Final = 64
_USD_QUANTUM: Final = Decimal("0.000000000001")

_OPAQUE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,191}$")
_OFFERING_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}$")
_MODEL_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,255}$")
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:sk-(?:proj-|ant-|live-)?|gh[pousr]_|github_pat_|xox[baprs]-|AIza|hf_|npm_)"
    r"[A-Za-z0-9_-]{8,}|(?:AKIA|ASIA)[A-Z0-9]{16}"
)
_TERMINAL_RESULT_SUBTYPES = frozenset(
    {
        "success",
        "error_max_turns",
        "error_during_execution",
        "error_max_budget_usd",
        "error_max_structured_output_retries",
    }
)
_COMMON_REQUIRED_FIELDS = frozenset(
    {
        "type",
        "subtype",
        "duration_ms",
        "duration_api_ms",
        "is_error",
        "num_turns",
        "stop_reason",
        "total_cost_usd",
        "usage",
        "modelUsage",
        "permission_denials",
        "uuid",
        "session_id",
    }
)
_SUCCESS_REQUIRED_FIELDS = _COMMON_REQUIRED_FIELDS | {"result"}
_ERROR_REQUIRED_FIELDS = _COMMON_REQUIRED_FIELDS | {"errors"}
_OPTIONAL_RESULT_FIELDS = frozenset(
    {
        "api_error_status",
        "structured_output",
        "deferred_tool_use",
        "terminal_reason",
        "fast_mode_state",
        "fast_mode_disabled_reason",
        "origin",
        "parent_tool_use_id",
        "priority",
        "shouldQuery",
        "timestamp",
        "ttft_ms",
        "ttft_stream_ms",
        "time_to_request_ms",
        "user_message_uuid",
        "request_sent_wall_ms",
        "time_to_request_from_spawn_ms",
        "warm_spare_claimed",
        "time_origin_ms",
    }
)
_USAGE_COUNT_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
_MODEL_USAGE_COUNT_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheCreationInputTokens",
    "webSearchRequests",
)
_MODEL_USAGE_CAPACITY_FIELDS = ("contextWindow", "maxOutputTokens")
_MODEL_USAGE_OPTIONAL_IDENTITY_FIELDS = frozenset({"canonicalModel", "provider"})
_MODEL_USAGE_FIELDS = (
    frozenset(_MODEL_USAGE_COUNT_FIELDS)
    | frozenset(_MODEL_USAGE_CAPACITY_FIELDS)
    | {"costUSD"}
    | _MODEL_USAGE_OPTIONAL_IDENTITY_FIELDS
)


class ClaudeCodeAdapterError(ValueError):
    """A Claude Code JSON result is unsupported, incomplete, or unsafe."""


class _DuplicateJsonKey(ValueError):
    pass


class ClaudeCodeRouteMapping(FrozenModel):
    """Bind one whole Claude Code invocation to an exact offering.

    ``canonical_model`` and ``upstream_provider`` are reviewed operator input.
    Claude Code 2.1.220 may omit either value from a ``modelUsage`` entry.  If
    it does, ``unreported_identity_attested`` must explicitly cover the missing
    identity; when present, the emitted value must match exactly.
    """

    offering: OfferingKey
    model_usage_key: str
    canonical_model: str
    upstream_provider: str
    single_route_and_pricing_basis_attested: bool
    unreported_identity_attested: bool
    route_details_attested: bool

    @model_validator(mode="after")
    def route_is_content_free_and_explicit(self) -> ClaudeCodeRouteMapping:
        _identifier(self.offering.offering_id, field="offering.offering_id", offering=True)
        _model_key(self.offering.model_id, field="offering.model_id")
        _identifier(self.offering.provider, field="offering.provider")
        _model_key(self.model_usage_key, field="model_usage_key")
        _model_key(self.canonical_model, field="canonical_model")
        _model_key(self.upstream_provider, field="upstream_provider")
        if self.offering.agent_harness != "claude-code":
            raise ValueError("Claude Code results require a Claude Code offering harness")
        if self.offering.model_id != self.canonical_model:
            raise ValueError("canonical_model must match the offering identity")
        if self.single_route_and_pricing_basis_attested is not True:
            raise ValueError(
                "single_route_and_pricing_basis_attested must cover the whole invocation"
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
                "route_details_attested is required for offering fields absent from Claude Code"
            )
        return self


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonstandard_number(_value: str) -> None:
    raise ValueError


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
            if depth > MAX_CLAUDE_CODE_JSON_DEPTH:
                raise ValueError
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError


def _read_result(path: Path) -> Mapping[str, Any]:
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
        raise ClaudeCodeAdapterError("cannot open Claude Code JSON input") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ClaudeCodeAdapterError("Claude Code JSON input must be a regular file")
        if metadata.st_size > MAX_CLAUDE_CODE_JSON_BYTES:
            raise ClaudeCodeAdapterError("Claude Code JSON input exceeds the byte limit")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            raw = stream.read(MAX_CLAUDE_CODE_JSON_BYTES + 1)
    except OSError:
        raise ClaudeCodeAdapterError("cannot read Claude Code JSON input") from None
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
    if len(raw) > MAX_CLAUDE_CODE_JSON_BYTES:
        raise ClaudeCodeAdapterError("Claude Code JSON input exceeds the byte limit")
    try:
        text = raw.decode("utf-8")
        _validate_json_depth(text)
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=Decimal,
            parse_constant=_reject_nonstandard_number,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
        ValueError,
        RecursionError,
        MemoryError,
        InvalidOperation,
    ):
        raise ClaudeCodeAdapterError("Claude Code JSON contains an invalid result") from None
    if not isinstance(value, Mapping):
        raise ClaudeCodeAdapterError("Claude Code JSON result must be an object")
    return value


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
        raise ClaudeCodeAdapterError(f"{field} must be a content-free opaque identifier")
    return value


def _model_key(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or _MODEL_KEY_RE.fullmatch(value) is None
        or "://" in value
        or "\\" in value
        or _CREDENTIAL_RE.search(value) is not None
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise ClaudeCodeAdapterError(f"{field} must be a content-free model identifier")
    return value


def _count(value: Any, *, field: str, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ClaudeCodeAdapterError(f"Claude Code field {field!r} must be an integer")
    if not 0 <= value <= MAX_SAFE_INTEGER:
        raise ClaudeCodeAdapterError(
            f"Claude Code field {field!r} must be a nonnegative safe integer"
        )
    if positive and value == 0:
        raise ClaudeCodeAdapterError(f"Claude Code field {field!r} must be positive")
    return Decimal(value)


def _usd(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ClaudeCodeAdapterError(f"Claude Code {field} must be a JSON number")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - primitive types are guarded
        raise ClaudeCodeAdapterError(f"Claude Code {field} is not a valid amount") from exc
    if not amount.is_finite() or amount < 0:
        raise ClaudeCodeAdapterError(f"Claude Code {field} must be finite and nonnegative")
    try:
        return amount.quantize(_USD_QUANTUM, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise ClaudeCodeAdapterError(
            f"Claude Code {field} exceeds supported USD precision"
        ) from exc


def _validate_terminal_shape(result: Mapping[str, Any]) -> str:
    subtype = result.get("subtype")
    if subtype not in _TERMINAL_RESULT_SUBTYPES:
        raise ClaudeCodeAdapterError("Claude Code result subtype is unsupported")
    assert isinstance(subtype, str)
    required = _SUCCESS_REQUIRED_FIELDS if subtype == "success" else _ERROR_REQUIRED_FIELDS
    allowed = required | _OPTIONAL_RESULT_FIELDS
    if set(result) - allowed or not required.issubset(result):
        raise ClaudeCodeAdapterError("Claude Code result does not match the reviewed schema")
    if result["type"] != "result":
        raise ClaudeCodeAdapterError("Claude Code JSON is not a terminal result")
    if not isinstance(result["is_error"], bool):
        raise ClaudeCodeAdapterError("Claude Code result status is invalid")
    if subtype != "success" and result["is_error"] is not True:
        raise ClaudeCodeAdapterError("Claude Code error result must set is_error true")
    duration = _count(result["duration_ms"], field="duration_ms")
    api_duration = _count(result["duration_api_ms"], field="duration_api_ms")
    if api_duration > duration:
        raise ClaudeCodeAdapterError("Claude Code API duration exceeds total duration")
    _count(result["num_turns"], field="num_turns")
    if result["stop_reason"] is not None and not isinstance(result["stop_reason"], str):
        raise ClaudeCodeAdapterError("Claude Code stop_reason is invalid")
    if not isinstance(result["usage"], Mapping):
        raise ClaudeCodeAdapterError("Claude Code usage must be an object")
    if not isinstance(result["modelUsage"], Mapping):
        raise ClaudeCodeAdapterError("Claude Code modelUsage must be an object")
    if not isinstance(result["permission_denials"], list):
        raise ClaudeCodeAdapterError("Claude Code permission_denials must be an array")
    if not isinstance(result["uuid"], str) or not isinstance(result["session_id"], str):
        raise ClaudeCodeAdapterError("Claude Code result identifiers are invalid")
    terminal_text = result["result"] if subtype == "success" else result["errors"]
    if subtype == "success" and not isinstance(terminal_text, str):
        raise ClaudeCodeAdapterError("Claude Code result text is invalid")
    if subtype != "success" and (
        not isinstance(terminal_text, list)
        or any(not isinstance(item, str) for item in terminal_text)
    ):
        raise ClaudeCodeAdapterError("Claude Code result errors are invalid")
    return subtype


def _core_usage(result: Mapping[str, Any]) -> dict[str, Decimal]:
    usage = result["usage"]
    assert isinstance(usage, Mapping)  # validated by _validate_terminal_shape
    if not set(_USAGE_COUNT_FIELDS).issubset(usage):
        raise ClaudeCodeAdapterError("Claude Code usage is missing required counters")
    return {field: _count(usage[field], field=f"usage.{field}") for field in _USAGE_COUNT_FIELDS}


def _model_usage(
    result: Mapping[str, Any],
    *,
    route: ClaudeCodeRouteMapping,
) -> tuple[dict[str, Decimal], Decimal]:
    model_usage = result["modelUsage"]
    assert isinstance(model_usage, Mapping)  # validated by _validate_terminal_shape
    if len(model_usage) != 1:
        raise ClaudeCodeAdapterError(
            "Claude Code result uses multiple models; one offering cannot represent the "
            "aggregate without a compound-offering policy"
        )
    model_key, entry = next(iter(model_usage.items()))
    safe_model_key = _model_key(model_key, field="Claude Code modelUsage key")
    if safe_model_key != route.model_usage_key:
        raise ClaudeCodeAdapterError("Claude Code modelUsage key does not match the route")
    if not isinstance(entry, Mapping) or set(entry) != _MODEL_USAGE_FIELDS & set(entry):
        raise ClaudeCodeAdapterError("Claude Code modelUsage entry has unsupported fields")
    required = _MODEL_USAGE_FIELDS - _MODEL_USAGE_OPTIONAL_IDENTITY_FIELDS
    if not required.issubset(entry):
        raise ClaudeCodeAdapterError("Claude Code modelUsage is missing required fields")

    counts = {
        field: _count(entry[field], field=f"modelUsage.{field}")
        for field in _MODEL_USAGE_COUNT_FIELDS
    }
    for field in _MODEL_USAGE_CAPACITY_FIELDS:
        _count(entry[field], field=f"modelUsage.{field}", positive=True)
    model_cost = _usd(entry["costUSD"], field="modelUsage.costUSD")
    total_cost = _usd(result["total_cost_usd"], field="total_cost_usd")
    if model_cost != total_cost:
        raise ClaudeCodeAdapterError(
            "Claude Code single-model cost does not match total_cost_usd"
        )

    canonical_model = entry.get("canonicalModel")
    provider = entry.get("provider")
    if (
        canonical_model is None or provider is None
    ) and route.unreported_identity_attested is not True:
        raise ClaudeCodeAdapterError(
            "unreported_identity_attested is required when Claude Code omits identity"
        )
    if (
        canonical_model is not None
        and _model_key(canonical_model, field="canonicalModel") != route.canonical_model
    ):
        raise ClaudeCodeAdapterError("Claude Code canonicalModel does not match the route")
    if (
        provider is not None
        and _model_key(provider, field="provider") != route.upstream_provider
    ):
        raise ClaudeCodeAdapterError("Claude Code provider does not match the route")

    core = _core_usage(result)
    pairs = (
        ("input_tokens", "inputTokens"),
        ("output_tokens", "outputTokens"),
        ("cache_read_input_tokens", "cacheReadInputTokens"),
        ("cache_creation_input_tokens", "cacheCreationInputTokens"),
    )
    if any(core[aggregate] != counts[per_model] for aggregate, per_model in pairs):
        raise ClaudeCodeAdapterError(
            "Claude Code aggregate usage does not match the single modelUsage entry"
        )
    return counts, model_cost


def adapt_claude_code_result_json(
    path: str | Path,
    *,
    claude_code_version: str,
    final_cumulative_result: bool,
    accounting_scope: Literal["single_print_invocation"],
    timestamp: datetime,
    workload_id: str,
    workload_version: str,
    work_unit_id: str,
    route: ClaudeCodeRouteMapping,
    result_id: str,
    attempt_id: str,
    work_unit_success: Decimal,
) -> RequestTrace:
    """Convert one private Claude Code JSON result into a canonical trace.

    The returned row is attempt-scoped because ``modelUsage`` can cover many
    model requests, subagents, retries, or internal calls.  The raw result must
    remain private; callers must provide local pseudonyms rather than reusing
    its session or UUID fields.
    """

    if claude_code_version not in CLAUDE_CODE_REVIEWED_RELEASES:
        raise ClaudeCodeAdapterError("unsupported Claude Code version")
    if final_cumulative_result is not True:
        raise ClaudeCodeAdapterError(
            "final_cumulative_result must explicitly attest the terminal invocation result"
        )
    if accounting_scope != "single_print_invocation":
        raise ClaudeCodeAdapterError("accounting_scope must identify one print invocation")
    if not isinstance(route, ClaudeCodeRouteMapping):
        raise ClaudeCodeAdapterError("route must be a validated ClaudeCodeRouteMapping")
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ClaudeCodeAdapterError("timestamp must be a timezone-aware datetime")
    try:
        offset = timestamp.utcoffset()
    except (OverflowError, ValueError) as exc:
        raise ClaudeCodeAdapterError("timestamp has an invalid timezone offset") from exc
    if offset is None:
        raise ClaudeCodeAdapterError("timestamp must be a timezone-aware datetime")
    if not isinstance(work_unit_success, Decimal):
        raise ClaudeCodeAdapterError("work_unit_success must be an explicit Decimal outcome")
    if not work_unit_success.is_finite() or not Decimal(0) <= work_unit_success <= Decimal(1):
        raise ClaudeCodeAdapterError(
            "work_unit_success must be finite and between zero and one"
        )

    safe_workload_id = _identifier(workload_id, field="workload_id")
    safe_workload_version = _identifier(workload_version, field="workload_version")
    safe_work_unit_id = _identifier(work_unit_id, field="work_unit_id")
    safe_result_id = _identifier(result_id, field="result_id")
    safe_attempt_id = _identifier(attempt_id, field="attempt_id")

    result = _read_result(Path(path))
    subtype = _validate_terminal_shape(result)
    model_usage = result["modelUsage"]
    assert isinstance(model_usage, Mapping)
    accounting_unavailable = subtype == "error_during_execution" or (
        subtype == "success" and result["is_error"] is True and not model_usage
    )
    if accounting_unavailable:
        counts = None
        cost = None
    else:
        counts, cost = _model_usage(result, route=route)

    try:
        return RequestTrace(
            schema_version="model-skyline/request-trace/v1alpha2",
            timestamp=timestamp,
            workload_id=safe_workload_id,
            workload_version=safe_workload_version,
            work_unit_id=safe_work_unit_id,
            offering_id=route.offering.offering_id,
            request_id=safe_result_id,
            attempt_id=safe_attempt_id,
            observation_unit="attempt",
            model_request_count=None,
            adapter_id="model-skyline/claude-code-result-json",
            adapter_version="1",
            upstream_system="anthropics/claude-code",
            upstream_version=claude_code_version,
            upstream_commit=CLAUDE_CODE_REVIEWED_RELEASES[claude_code_version],
            work_unit_success=work_unit_success,
            input_uncached_tokens=(counts["inputTokens"] if counts is not None else None),
            input_cache_read_tokens=(
                counts["cacheReadInputTokens"] if counts is not None else None
            ),
            input_cache_write_tokens=(
                counts["cacheCreationInputTokens"] if counts is not None else None
            ),
            output_total_tokens=(counts["outputTokens"] if counts is not None else None),
            web_search_calls=(counts["webSearchRequests"] if counts is not None else None),
            estimated_total_cost_usd=cost,
        )
    except ValidationError:
        raise ClaudeCodeAdapterError(
            "Claude Code accounting row exceeds the canonical trace contract"
        ) from None
