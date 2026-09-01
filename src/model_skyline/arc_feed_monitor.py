"""Fail-closed monitoring for the official ARC-AGI-2 result dataset head.

The monitor deliberately reads only the public Hugging Face dataset metadata
API.  A new repository head is a review signal, never permission to reuse the
pinned adapter's semantic assumptions.
"""

from __future__ import annotations

import json
import re
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final

import httpx

from model_skyline.adapters.arc_agi import ARC_AGI_HF_DATASET_ID, ARC_AGI_HF_REVISION

ARC_AGI_HF_HEAD_API: Final = "https://huggingface.co/api/datasets/arcprize/arc_agi_v2_public_eval"
ARC_AGI_FEED_MONITOR_VERSION: Final = "1"
MAX_HEAD_METADATA_BYTES: Final = 1_000_000
MAX_JSON_DEPTH: Final = 24
MAX_JSON_NODES: Final = 100_000
MAX_JSON_STRING_LENGTH: Final = 65_536
DEFAULT_TIMEOUT_SECONDS: Final = 30.0
MAX_TIMEOUT_SECONDS: Final = 60.0

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_CONTENT_LENGTH_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")


class ArcAgiFeedMonitorError(RuntimeError):
    """The ARC-AGI-2 head could not be inspected within the trust boundary."""


class ArcAgiFeedState(StrEnum):
    """Whether the public dataset still points at the reviewed revision."""

    PINNED = "pinned"
    REVIEW_REQUIRED = "review_required"


@dataclass(frozen=True, slots=True)
class ArcAgiFeedStatus:
    """Compact metadata-only result of an ARC-AGI-2 head inspection."""

    observed_revision: str
    pinned_revision: str
    observed_last_modified: datetime
    retrieved_at: datetime
    state: ArcAgiFeedState

    @property
    def review_required(self) -> bool:
        return self.state is ArcAgiFeedState.REVIEW_REQUIRED

    def document(self) -> dict[str, Any]:
        """Render a publication-safe status without upstream paths or row labels."""

        return {
            "schema_version": "model-skyline/arc-agi-2-feed-status/v1alpha1",
            "feed": ARC_AGI_HF_DATASET_ID,
            "retrieved_at": self.retrieved_at.isoformat(),
            "observed_revision": self.observed_revision,
            "pinned_revision": self.pinned_revision,
            "observed_last_modified": self.observed_last_modified.isoformat(),
            "state": self.state.value,
            "review_required": self.review_required,
            "action": "manual_adapter_review" if self.review_required else "none",
            "different_head_policy": "no_automatic_semantic_reuse",
        }


class _DuplicateJsonKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError


def _parse_decimal(value: str) -> Decimal:
    if len(value) > 1_024:
        raise ValueError
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError from exc


def _parse_integer(value: str) -> int:
    if len(value) > 1_024:
        raise ValueError
    return int(value)


def _preflight_json(raw: bytes) -> None:
    if len(raw) > MAX_HEAD_METADATA_BYTES:
        raise ArcAgiFeedMonitorError("ARC-AGI head metadata exceeds the byte limit")
    depth = 0
    nodes = 1
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in {0x7B, 0x5B}:
            depth += 1
            nodes += 1
            if depth > MAX_JSON_DEPTH:
                raise ArcAgiFeedMonitorError("ARC-AGI head metadata exceeds the JSON nesting limit")
        elif byte in {0x3A, 0x2C}:
            nodes += 1
            if nodes > MAX_JSON_NODES:
                raise ArcAgiFeedMonitorError(
                    "ARC-AGI head metadata exceeds the JSON structural token limit"
                )
        elif byte in {0x7D, 0x5D}:
            depth -= 1
            if depth < 0:
                break


def _validate_json_shape(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ArcAgiFeedMonitorError(
                "ARC-AGI head metadata exceeds the JSON structural token limit"
            )
        if depth > MAX_JSON_DEPTH:
            raise ArcAgiFeedMonitorError("ARC-AGI head metadata exceeds the JSON nesting limit")
        if isinstance(current, dict):
            for key, child in current.items():
                if len(key) > MAX_JSON_STRING_LENGTH:
                    raise ArcAgiFeedMonitorError(
                        "ARC-AGI head metadata contains an oversized object key"
                    )
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, str) and len(current) > MAX_JSON_STRING_LENGTH:
            raise ArcAgiFeedMonitorError("ARC-AGI head metadata contains an oversized string")


def _decode_head_metadata(raw: bytes) -> dict[str, Any]:
    _preflight_json(raw)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_parse_decimal,
            parse_int=_parse_integer,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
        ValueError,
        RecursionError,
        MemoryError,
    ):
        raise ArcAgiFeedMonitorError(
            "ARC-AGI head metadata is not valid bounded duplicate-key-free JSON"
        ) from None
    _validate_json_shape(value)
    if not isinstance(value, dict):
        raise ArcAgiFeedMonitorError("ARC-AGI head metadata must be an object")
    return value


def _bounded_response(response: httpx.Response) -> bytes:
    if response.history or response.is_redirect:
        raise ArcAgiFeedMonitorError("ARC-AGI head API redirected")
    if response.status_code != 200:
        raise ArcAgiFeedMonitorError(f"ARC-AGI head API returned HTTP {response.status_code}")
    if response.headers.get("content-encoding", "identity").casefold() not in {
        "",
        "identity",
    }:
        raise ArcAgiFeedMonitorError("ARC-AGI head API returned compressed content")
    media_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
    if media_type != "application/json":
        raise ArcAgiFeedMonitorError("ARC-AGI head API returned an unexpected media type")
    declared = response.headers.get("content-length")
    if declared is not None:
        if _CONTENT_LENGTH_RE.fullmatch(declared) is None:
            raise ArcAgiFeedMonitorError("ARC-AGI head API returned an invalid Content-Length")
        if int(declared) > MAX_HEAD_METADATA_BYTES:
            raise ArcAgiFeedMonitorError("ARC-AGI head metadata exceeds the byte limit")
    content = bytearray()
    for chunk in response.iter_raw():
        content.extend(chunk)
        if len(content) > MAX_HEAD_METADATA_BYTES:
            raise ArcAgiFeedMonitorError("ARC-AGI head metadata exceeds the byte limit")
    return bytes(content)


def _timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArcAgiFeedMonitorError("timeout_seconds must be a number")
    if not 0 < value <= MAX_TIMEOUT_SECONDS:
        raise ArcAgiFeedMonitorError(
            f"timeout_seconds must be greater than zero and at most {MAX_TIMEOUT_SECONDS}"
        )
    return float(value)


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ArcAgiFeedMonitorError("ARC-AGI head lastModified must be a bounded timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ArcAgiFeedMonitorError("ARC-AGI head lastModified is invalid") from None
    if parsed.tzinfo is None:
        raise ArcAgiFeedMonitorError("ARC-AGI head lastModified must include a timezone")
    return parsed.astimezone(UTC)


def inspect_arc_agi_feed(
    *,
    retrieved_at: datetime | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> ArcAgiFeedStatus:
    """Inspect the public dataset head without fetching result or attempt files."""

    timeout = _timeout(timeout_seconds)
    timestamp = retrieved_at or datetime.now(UTC)
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ArcAgiFeedMonitorError("retrieved_at must be a timezone-aware datetime")
    timestamp = timestamp.astimezone(UTC)
    owned_client = client is None
    selected_client = client or httpx.Client(
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
        trust_env=False,
    )
    context = selected_client if owned_client else nullcontext(selected_client)
    try:
        with (
            context as active_client,
            active_client.stream(
                "GET",
                ARC_AGI_HF_HEAD_API,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": (
                        f"model-skyline/{ARC_AGI_FEED_MONITOR_VERSION} arc-agi-feed-monitor"
                    ),
                },
                timeout=timeout,
            ) as response,
        ):
            raw = _bounded_response(response)
    except ArcAgiFeedMonitorError:
        raise
    except httpx.HTTPError:
        raise ArcAgiFeedMonitorError("cannot fetch ARC-AGI head metadata") from None

    value = _decode_head_metadata(raw)
    if value.get("id") != ARC_AGI_HF_DATASET_ID or value.get("author") != "arcprize":
        raise ArcAgiFeedMonitorError("ARC-AGI head metadata identifies another dataset")
    if value.get("private") is not False or value.get("gated") is not False:
        raise ArcAgiFeedMonitorError("ARC-AGI head dataset is no longer public and ungated")
    if value.get("disabled") is not False:
        raise ArcAgiFeedMonitorError("ARC-AGI head dataset is disabled")
    revision = value.get("sha")
    if not isinstance(revision, str) or _COMMIT_RE.fullmatch(revision) is None:
        raise ArcAgiFeedMonitorError("ARC-AGI head revision is invalid")
    last_modified = _timestamp(value.get("lastModified"))
    if last_modified > timestamp:
        raise ArcAgiFeedMonitorError("ARC-AGI head lastModified is in the future")
    state = (
        ArcAgiFeedState.PINNED
        if revision == ARC_AGI_HF_REVISION
        else ArcAgiFeedState.REVIEW_REQUIRED
    )
    return ArcAgiFeedStatus(
        observed_revision=revision,
        pinned_revision=ARC_AGI_HF_REVISION,
        observed_last_modified=last_modified,
        retrieved_at=timestamp,
        state=state,
    )


def check_arc_agi_feed_live(
    *,
    retrieved_at: datetime | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ArcAgiFeedStatus:
    """Run the hardened network check used by scheduled monitoring jobs."""

    return inspect_arc_agi_feed(
        retrieved_at=retrieved_at,
        timeout_seconds=timeout_seconds,
    )
