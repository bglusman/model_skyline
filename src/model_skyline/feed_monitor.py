"""Low-cadence semantic monitoring for supported public benchmark feeds."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

import httpx

from model_skyline.adapters.swe_bench import (
    DEFAULT_MAX_SOURCE_BYTES,
    SWE_BENCH_DEFAULT_HARNESS_VERSION,
    SWE_BENCH_WEBSITE_SHA256,
    SWE_BENCH_WEBSITE_URL,
    SweBenchCapture,
    SweBenchSourceIdentityMode,
    normalize_swe_bench_bytes,
)
from model_skyline.quality_evidence import quality_raw_sha256

SWE_BENCH_FILE_COMMITS_API: Final = (
    "https://api.github.com/repos/SWE-bench/swe-bench.github.io/commits"
    "?path=data%2Fleaderboards.json&per_page=1"
)
SWE_BENCH_RAW_AT_REVISION: Final = (
    "https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io/"
    "{revision}/data/leaderboards.json"
)
MAX_GITHUB_API_BYTES: Final = 256_000
MONITOR_TIMEOUT_SECONDS: Final = 30.0
SWE_BENCH_MONITORED_HARNESS_VERSION: Final = SWE_BENCH_DEFAULT_HARNESS_VERSION
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class FeedMonitorError(RuntimeError):
    """A supported feed could not be inspected without weakening its boundary."""


class SweBenchFeedChange(StrEnum):
    NONE = "none"
    RAW_ONLY = "raw_only"
    RESULT = "result_changed"
    SUBJECT = "subject_identity_changed"
    SUBJECT_SET = "subject_set_changed"
    SOURCE = "source_identity_changed"


@dataclass(frozen=True, slots=True)
class SweBenchFeedStatus:
    latest_file_revision: str
    retrieved_at: datetime
    raw_sha256: str
    pinned_raw_sha256: str
    source_identity_sha256: str
    change: SweBenchFeedChange
    rows_seen: int
    valid_rows: int
    invalid_rows: int
    invalid_reason_counts: tuple[tuple[str, int], ...]

    @property
    def semantic_change(self) -> bool:
        return self.change not in {SweBenchFeedChange.NONE, SweBenchFeedChange.RAW_ONLY}

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": "model-skyline/swe-bench-feed-status/v1alpha1",
            "feed": (f"swe-bench/bash-only/mini-swe-agent-{SWE_BENCH_MONITORED_HARNESS_VERSION}"),
            "latest_file_revision": self.latest_file_revision,
            "retrieved_at": self.retrieved_at.isoformat(),
            "raw_sha256": self.raw_sha256,
            "pinned_raw_sha256": self.pinned_raw_sha256,
            "source_identity_sha256": self.source_identity_sha256,
            "change": self.change.value,
            "semantic_change": self.semantic_change,
            "rows": {
                "seen": self.rows_seen,
                "valid": self.valid_rows,
                "invalid": self.invalid_rows,
                "invalid_reason_counts": dict(self.invalid_reason_counts),
            },
            "action": (
                "review_and_repin"
                if self.semantic_change
                else (
                    "optional_raw_pin_refresh"
                    if self.change is SweBenchFeedChange.RAW_ONLY
                    else "none"
                )
            ),
        }


class _DuplicateKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise _DuplicateKey
        value[key] = child
    return value


def _bounded_response(response: httpx.Response, *, maximum: int, label: str) -> bytes:
    if response.status_code < 200 or response.status_code >= 300:
        raise FeedMonitorError(f"{label} returned HTTP {response.status_code}")
    if response.headers.get("content-encoding", "identity").casefold() not in {
        "",
        "identity",
    }:
        raise FeedMonitorError(f"{label} returned compressed content")
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError:
            raise FeedMonitorError(f"{label} returned an invalid Content-Length") from None
        if declared_size < 0 or declared_size > maximum:
            raise FeedMonitorError(f"{label} exceeds the byte limit")
    content = bytearray()
    for chunk in response.iter_raw():
        content.extend(chunk)
        if len(content) > maximum:
            raise FeedMonitorError(f"{label} exceeds the byte limit")
    return bytes(content)


def _fetch(
    client: httpx.Client,
    url: str,
    *,
    maximum: int,
    label: str,
    headers: dict[str, str] | None = None,
) -> bytes:
    try:
        with client.stream("GET", url, headers=headers) as response:
            return _bounded_response(response, maximum=maximum, label=label)
    except FeedMonitorError:
        raise
    except httpx.HTTPError:
        raise FeedMonitorError(f"cannot fetch {label}") from None


def _latest_file_revision(client: httpx.Client, *, github_token: str | None) -> str:
    request_headers = (
        {"Authorization": f"Bearer {github_token}"} if github_token is not None else None
    )
    raw = _fetch(
        client,
        SWE_BENCH_FILE_COMMITS_API,
        maximum=MAX_GITHUB_API_BYTES,
        label="SWE-bench GitHub revision API",
        headers=request_headers,
    )
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey, ValueError):
        raise FeedMonitorError("SWE-bench GitHub revision response is invalid JSON") from None
    if (
        not isinstance(value, list)
        or len(value) != 1
        or not isinstance(value[0], dict)
        or not isinstance(value[0].get("sha"), str)
        or _COMMIT_RE.fullmatch(value[0]["sha"]) is None
    ):
        raise FeedMonitorError("SWE-bench GitHub revision response has an invalid shape")
    return str(value[0]["sha"])


def classify_swe_bench_change(
    pinned: SweBenchCapture,
    current: SweBenchCapture,
    *,
    raw_bytes_equal: bool,
) -> SweBenchFeedChange:
    """Classify the narrowest identity boundary affected by a feed refresh."""

    if raw_bytes_equal:
        return SweBenchFeedChange.NONE
    if pinned.evidence.source_identity_sha256 != current.evidence.source_identity_sha256:
        return SweBenchFeedChange.SOURCE
    pinned_rows = {row.row_id: row for row in pinned.evidence.rows}
    current_rows = {row.row_id: row for row in current.evidence.rows}
    if pinned_rows.keys() != current_rows.keys():
        return SweBenchFeedChange.SUBJECT_SET
    if any(
        pinned_rows[row_id].subject_identity_sha256 != current_rows[row_id].subject_identity_sha256
        for row_id in pinned_rows
    ):
        return SweBenchFeedChange.SUBJECT
    if any(
        pinned_rows[row_id].result_sha256 != current_rows[row_id].result_sha256
        for row_id in pinned_rows
    ):
        return SweBenchFeedChange.RESULT
    return SweBenchFeedChange.RAW_ONLY


def inspect_swe_bench_feed(
    *,
    github_token: str | None = None,
    retrieved_at: datetime | None = None,
    client: httpx.Client | None = None,
) -> SweBenchFeedStatus:
    """Inspect the latest official file without publishing or retaining its bytes."""

    timestamp = retrieved_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise FeedMonitorError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(UTC)
    headers = {
        "Accept": "application/vnd.github+json, application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "model-skyline-swe-bench-monitor/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owned_client = client is None
    selected_client = client or httpx.Client(
        timeout=httpx.Timeout(MONITOR_TIMEOUT_SECONDS),
        follow_redirects=False,
        trust_env=False,
        headers=headers,
    )
    context = selected_client if owned_client else nullcontext(selected_client)
    try:
        with context as active_client:
            revision = _latest_file_revision(active_client, github_token=github_token)
            current_url = SWE_BENCH_RAW_AT_REVISION.format(revision=revision)
            current_raw = _fetch(
                active_client,
                current_url,
                maximum=DEFAULT_MAX_SOURCE_BYTES,
                label="latest SWE-bench website data",
            )
            current_sha256 = quality_raw_sha256(current_raw)
            current = normalize_swe_bench_bytes(
                current_raw,
                retrieved_at=timestamp,
                source_locator=current_url,
                upstream_revision=revision,
                harness_version=SWE_BENCH_MONITORED_HARNESS_VERSION,
                source_identity_mode=SweBenchSourceIdentityMode.OFFICIAL_SEMANTIC,
            )
            if current_sha256 == SWE_BENCH_WEBSITE_SHA256:
                change = SweBenchFeedChange.NONE
            else:
                pinned_raw = _fetch(
                    active_client,
                    SWE_BENCH_WEBSITE_URL,
                    maximum=DEFAULT_MAX_SOURCE_BYTES,
                    label="pinned SWE-bench website data",
                )
                if quality_raw_sha256(pinned_raw) != SWE_BENCH_WEBSITE_SHA256:
                    raise FeedMonitorError("pinned SWE-bench source digest no longer verifies")
                pinned = normalize_swe_bench_bytes(
                    pinned_raw,
                    retrieved_at=timestamp,
                    source_locator=SWE_BENCH_WEBSITE_URL,
                    upstream_revision="pinned",
                    harness_version=SWE_BENCH_MONITORED_HARNESS_VERSION,
                    source_identity_mode=SweBenchSourceIdentityMode.OFFICIAL_SEMANTIC,
                )
                change = classify_swe_bench_change(
                    pinned,
                    current,
                    raw_bytes_equal=False,
                )
    except FeedMonitorError:
        raise
    except (OSError, TypeError, ValueError):
        raise FeedMonitorError("latest SWE-bench feed failed strict normalization") from None
    invalid_counts = Counter(
        row.invalid_result.code for row in current.evidence.rows if row.invalid_result is not None
    )
    return SweBenchFeedStatus(
        latest_file_revision=revision,
        retrieved_at=timestamp,
        raw_sha256=current_sha256,
        pinned_raw_sha256=SWE_BENCH_WEBSITE_SHA256,
        source_identity_sha256=current.evidence.source_identity_sha256,
        change=change,
        rows_seen=current.rows_seen,
        valid_rows=current.valid_rows,
        invalid_rows=current.invalid_rows,
        invalid_reason_counts=tuple(sorted(invalid_counts.items())),
    )


def github_token_from_environment() -> str | None:
    """Read the conventional workflow token without ever rendering it."""

    value = os.environ.get("GITHUB_TOKEN")
    return value if value else None
