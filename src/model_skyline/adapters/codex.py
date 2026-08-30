"""Conservative accounting adapter for ``codex exec --json`` streams.

The adapter consumes the machine-readable JSONL stream without retaining item
payloads, thread ids, prompts, responses, commands, paths, tool arguments, or
errors.  Only the terminal turn-level usage object becomes a canonical trace.

Reviewed upstream contracts:

* Codex 0.144.2 at ``a6645b6b8a656360fa16fb7e1c6721d0697d3d6a``.
* Codex 0.151.0 at ``78c290807ce710180111df227df3b7a4fe845452``.
* ``sdk/typescript/src/events.ts`` and ``codex-rs/exec/src/exec_events.rs``
  at those dereferenced release commits.

``turn.completed.usage`` is cumulative for an agent turn and can cover several
model calls, so the emitted row is attempt-scoped and leaves model-request
count unknown.  Version 0.144.2 does not expose prompt-cache writes.  For that
version the inclusive input total and cache reads are preserved, while
uncached input and cache writes remain unknown rather than being guessed.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from model_skyline.models import MAX_SAFE_INTEGER, OfferingKey
from model_skyline.traces import RequestTrace

CODEX_REVIEWED_RELEASES: Final[dict[str, str]] = {
    "0.144.2": "a6645b6b8a656360fa16fb7e1c6721d0697d3d6a",
    "0.151.0": "78c290807ce710180111df227df3b7a4fe845452",
}
CODEX_EVENTS_SOURCE_PATH: Final = "sdk/typescript/src/events.ts"
CODEX_EVENTS_SOURCE_URLS: Final[dict[str, str]] = {
    version: (f"https://github.com/openai/codex/blob/{commit}/{CODEX_EVENTS_SOURCE_PATH}")
    for version, commit in CODEX_REVIEWED_RELEASES.items()
}
MAX_CODEX_JSONL_BYTES: Final = 64 * 1024 * 1024
MAX_CODEX_JSONL_LINE_BYTES: Final = 16 * 1024 * 1024
MAX_CODEX_JSONL_EVENTS: Final = 100_000
MAX_CODEX_JSON_DEPTH: Final = 64

_OPAQUE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,191}$")
_OFFERING_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}$")
_MODEL_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,255}$")
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:sk-(?:proj-|ant-|live-)?|gh[pousr]_|github_pat_|xox[baprs]-|AIza|hf_|npm_)"
    r"[A-Za-z0-9_-]{8,}|(?:AKIA|ASIA)[A-Z0-9]{16}"
)
_ITEM_EVENT_TYPES = frozenset({"item.started", "item.updated", "item.completed"})
_USAGE_FIELDS_WITH_CACHE_WRITE = frozenset(
    {
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    }
)
_USAGE_FIELDS_WITHOUT_CACHE_WRITE = _USAGE_FIELDS_WITH_CACHE_WRITE - {"cache_write_input_tokens"}


class CodexAdapterError(ValueError):
    """A Codex stream is unsupported, incomplete, or unsafe to normalize."""


class _DuplicateJsonKey(ValueError):
    pass


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
            if depth > MAX_CODEX_JSON_DEPTH:
                raise ValueError
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError


def _safe_identifier(value: Any, *, field: str, offering: bool = False) -> str:
    pattern = _OFFERING_IDENTIFIER_RE if offering else _OPAQUE_IDENTIFIER_RE
    if (
        not isinstance(value, str)
        or pattern.fullmatch(value) is None
        or "://" in value
        or "\\" in value
        or _CREDENTIAL_RE.search(value) is not None
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise CodexAdapterError(f"{field} must be a content-free opaque identifier")
    return value


def _safe_model_identifier(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _MODEL_IDENTIFIER_RE.fullmatch(value) is None
        or "://" in value
        or "\\" in value
        or _CREDENTIAL_RE.search(value) is not None
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise CodexAdapterError("selected_model must be a content-free model identifier")
    return value


def _validated_offering(
    offering: OfferingKey,
    *,
    selected_provider: str,
    selected_model: str,
    route_details_attested: bool,
) -> OfferingKey:
    if not isinstance(offering, OfferingKey):
        raise CodexAdapterError("offering must be a validated OfferingKey")
    _safe_identifier(offering.offering_id, field="offering.offering_id", offering=True)
    safe_provider = _safe_identifier(selected_provider, field="selected_provider")
    safe_model = _safe_model_identifier(selected_model)
    if offering.provider != safe_provider or offering.model_id != safe_model:
        raise CodexAdapterError("selected Codex route does not match the offering identity")
    if offering.agent_harness != "codex":
        raise CodexAdapterError("Codex traces require a Codex offering harness")
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
        raise CodexAdapterError(
            "route_details_attested is required for offering fields absent from Codex JSONL"
        )
    return offering


def _count(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CodexAdapterError(f"Codex usage field {field!r} must be an integer")
    if not 0 <= value <= MAX_SAFE_INTEGER:
        raise CodexAdapterError(f"Codex usage field {field!r} must be a nonnegative safe integer")
    return int(value)


def _parse_json_line(raw_line: bytes) -> Mapping[str, Any]:
    try:
        text = raw_line.decode("utf-8")
        _validate_json_depth(text)
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_number,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
        ValueError,
        RecursionError,
        MemoryError,
    ):
        raise CodexAdapterError("Codex JSONL contains an invalid event") from None
    if not isinstance(value, Mapping):
        raise CodexAdapterError("Codex JSONL events must be objects")
    return value


def _read_events(path: Path) -> list[Mapping[str, Any]]:
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
        raise CodexAdapterError("cannot open Codex JSONL input") from None
    events: list[Mapping[str, Any]] = []
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CodexAdapterError("Codex JSONL input must be a regular file")
        if metadata.st_size > MAX_CODEX_JSONL_BYTES:
            raise CodexAdapterError("Codex JSONL input exceeds the byte limit")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            consumed = 0
            while True:
                raw_line = stream.readline(MAX_CODEX_JSONL_LINE_BYTES + 1)
                if not raw_line:
                    break
                consumed += len(raw_line)
                if consumed > MAX_CODEX_JSONL_BYTES:
                    raise CodexAdapterError("Codex JSONL input exceeds the byte limit")
                if len(raw_line) > MAX_CODEX_JSONL_LINE_BYTES:
                    raise CodexAdapterError("Codex JSONL event exceeds the line limit")
                if not raw_line.strip():
                    continue
                events.append(_parse_json_line(raw_line))
                if len(events) > MAX_CODEX_JSONL_EVENTS:
                    raise CodexAdapterError("Codex JSONL input exceeds the event limit")
    except OSError:
        raise CodexAdapterError("cannot read Codex JSONL input") from None
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
    if not events:
        raise CodexAdapterError("Codex JSONL contains no events")
    return events


def _terminal_usage(
    events: list[Mapping[str, Any]],
    *,
    codex_version: str,
) -> dict[str, int] | None:
    state = "initial"
    usage: dict[str, int] | None = None
    for event in events:
        event_type = event.get("type")
        if not isinstance(event_type, str):
            raise CodexAdapterError("Codex JSONL event type is missing or invalid")
        if state == "initial":
            if event_type != "thread.started" or set(event) != {"type", "thread_id"}:
                raise CodexAdapterError("Codex JSONL must begin with thread.started")
            thread_id = event["thread_id"]
            if not isinstance(thread_id, str) or not 1 <= len(thread_id) <= 512:
                raise CodexAdapterError("Codex thread.started has an invalid identifier")
            state = "thread"
            continue
        if state == "thread":
            if event_type in _ITEM_EVENT_TYPES:
                if set(event) != {"type", "item"} or not isinstance(event["item"], Mapping):
                    raise CodexAdapterError("Codex JSONL contains a malformed item event")
                # Route-validation failures can emit a content-bearing item
                # before turn.started.  It is deliberately ignored.
                continue
            if event_type == "error":
                if set(event) != {"type", "message"} or not isinstance(event["message"], str):
                    raise CodexAdapterError("Codex JSONL contains a malformed error event")
                state = "failed"
                continue
            if event_type != "turn.started" or set(event) != {"type"}:
                raise CodexAdapterError("Codex thread must contain exactly one turn")
            state = "turn"
            continue
        if state in {"complete", "failed"}:
            raise CodexAdapterError("Codex JSONL contains events after turn completion")
        if state == "turn_error":
            if event_type != "turn.failed":
                raise CodexAdapterError("Codex error event must be followed by turn.failed")
            if set(event) != {"type", "error"} or not isinstance(event["error"], Mapping):
                raise CodexAdapterError("Codex JSONL contains a malformed failed-turn event")
            error = event["error"]
            if set(error) != {"message"} or not isinstance(error["message"], str):
                raise CodexAdapterError("Codex JSONL contains a malformed failed-turn event")
            state = "failed"
            continue
        if event_type == "turn.failed":
            if set(event) != {"type", "error"} or not isinstance(event["error"], Mapping):
                raise CodexAdapterError("Codex JSONL contains a malformed failed-turn event")
            error = event["error"]
            if set(error) != {"message"} or not isinstance(error["message"], str):
                raise CodexAdapterError("Codex JSONL contains a malformed failed-turn event")
            state = "failed"
            continue
        if event_type == "error":
            if set(event) != {"type", "message"} or not isinstance(event["message"], str):
                raise CodexAdapterError("Codex JSONL contains a malformed error event")
            state = "turn_error"
            continue
        if event_type in _ITEM_EVENT_TYPES:
            if set(event) != {"type", "item"} or not isinstance(event["item"], Mapping):
                raise CodexAdapterError("Codex JSONL contains a malformed item event")
            continue
        if event_type != "turn.completed" or set(event) != {"type", "usage"}:
            raise CodexAdapterError("Codex JSONL contains an unsupported event")
        raw_usage = event["usage"]
        if not isinstance(raw_usage, Mapping):
            raise CodexAdapterError("Codex turn.completed usage must be an object")
        expected_fields = (
            _USAGE_FIELDS_WITHOUT_CACHE_WRITE
            if codex_version == "0.144.2"
            else _USAGE_FIELDS_WITH_CACHE_WRITE
        )
        if set(raw_usage) != expected_fields:
            raise CodexAdapterError("Codex turn.completed usage does not match the reviewed schema")
        usage = {field: _count(raw_usage[field], field=field) for field in expected_fields}
        state = "complete"
    if state == "failed":
        return None
    if state != "complete" or usage is None:
        raise CodexAdapterError("Codex JSONL ended before turn completion")
    return usage


def adapt_codex_exec_jsonl(
    path: str | Path,
    *,
    codex_version: str,
    model_route_attested: bool,
    selected_provider: str,
    selected_model: str,
    route_details_attested: bool,
    timestamp: datetime,
    workload_id: str,
    workload_version: str,
    work_unit_id: str,
    offering: OfferingKey,
    result_id: str,
    attempt_id: str,
    work_unit_success: Decimal,
) -> RequestTrace:
    """Convert one completed ``codex exec --json`` turn to an aggregate row.

    The caller must attest that ``selected_provider``/``selected_model`` were
    the run's only model route and provide the exact corresponding
    :class:`~model_skyline.models.OfferingKey`.  Codex's stream does not expose
    that route, timestamp, workload identity, or judged work-unit outcome.
    Caller-supplied record identifiers must be local pseudonyms and must not be
    raw Codex thread ids.  Narrow route fields absent from JSONL require a
    separate explicit attestation.
    """

    if codex_version not in CODEX_REVIEWED_RELEASES:
        raise CodexAdapterError("unsupported Codex version")
    if model_route_attested is not True:
        raise CodexAdapterError("model_route_attested must explicitly be true")
    safe_offering = _validated_offering(
        offering,
        selected_provider=selected_provider,
        selected_model=selected_model,
        route_details_attested=route_details_attested,
    )
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise CodexAdapterError("timestamp must be a timezone-aware datetime")
    try:
        offset = timestamp.utcoffset()
    except (OverflowError, ValueError) as exc:
        raise CodexAdapterError("timestamp has an invalid timezone offset") from exc
    if offset is None:
        raise CodexAdapterError("timestamp must be a timezone-aware datetime")
    if not isinstance(work_unit_success, Decimal):
        raise CodexAdapterError("work_unit_success must be an explicit Decimal outcome")
    if not work_unit_success.is_finite() or not Decimal(0) <= work_unit_success <= Decimal(1):
        raise CodexAdapterError("work_unit_success must be finite and between zero and one")

    safe_workload_id = _safe_identifier(workload_id, field="workload_id")
    safe_workload_version = _safe_identifier(workload_version, field="workload_version")
    safe_work_unit_id = _safe_identifier(work_unit_id, field="work_unit_id")
    safe_result_id = _safe_identifier(result_id, field="result_id")
    safe_attempt_id = _safe_identifier(attempt_id, field="attempt_id")

    usage = _terminal_usage(_read_events(Path(path)), codex_version=codex_version)
    input_total = usage["input_tokens"] if usage is not None else None
    cache_read = usage["cached_input_tokens"] if usage is not None else None
    output_total = usage["output_tokens"] if usage is not None else None
    reasoning = usage["reasoning_output_tokens"] if usage is not None else None
    if reasoning is not None and output_total is not None and reasoning > output_total:
        raise CodexAdapterError("Codex reasoning output exceeds total output")

    cache_write: int | None = usage.get("cache_write_input_tokens") if usage is not None else None
    input_uncached: int | None = None
    if cache_write is not None and cache_read is not None and input_total is not None:
        if cache_read + cache_write > input_total:
            raise CodexAdapterError("Codex cache input exceeds total input")
        input_uncached = input_total - cache_read - cache_write
    elif cache_read is not None and input_total is not None and cache_read > input_total:
        raise CodexAdapterError("Codex cached input exceeds total input")

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
            adapter_id="model-skyline/codex-exec-jsonl",
            adapter_version="1",
            upstream_system="openai/codex",
            upstream_version=codex_version,
            upstream_commit=CODEX_REVIEWED_RELEASES[codex_version],
            work_unit_success=work_unit_success,
            input_uncached_tokens=(Decimal(input_uncached) if input_uncached is not None else None),
            input_cache_read_tokens=(Decimal(cache_read) if cache_read is not None else None),
            input_cache_write_tokens=(Decimal(cache_write) if cache_write is not None else None),
            input_total_tokens=(Decimal(input_total) if input_total is not None else None),
            output_tokens=(
                Decimal(output_total - reasoning)
                if output_total is not None and reasoning is not None
                else None
            ),
            reasoning_tokens=(Decimal(reasoning) if reasoning is not None else None),
            output_total_tokens=(Decimal(output_total) if output_total is not None else None),
        )
    except ValidationError:
        raise CodexAdapterError(
            "Codex accounting row exceeds the canonical trace contract"
        ) from None
