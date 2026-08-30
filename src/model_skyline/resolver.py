from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from model_skyline.models import SelectionSnapshot
from model_skyline.selection import selection_hash


class ResolverError(RuntimeError):
    """No valid selection snapshot is available within the staleness policy."""


Loader = Callable[[str, str | None, float], tuple[Mapping[str, Any] | None, str | None]]
DEFAULT_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024


def _normalize_host(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("allowed_hosts must contain only strings")
    candidate = value.strip().rstrip(".").casefold()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if not candidate:
        raise ValueError("allowed_hosts cannot contain an empty hostname")
    try:
        return candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid allowed hostname {value!r}") from exc


def _normalize_allowed_hosts(values: Iterable[str] | None) -> frozenset[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        raise ValueError("allowed_hosts must be a collection of hostnames, not a string")
    return frozenset(_normalize_host(value) for value in values)


def _validate_source_policy(
    source: str,
    *,
    allow_insecure_http: bool,
    allow_local_file: bool,
    allowed_hosts: frozenset[str] | None,
) -> None:
    parsed = urlparse(source)
    scheme = parsed.scheme.casefold()
    if scheme in {"http", "https"}:
        if scheme == "http" and not allow_insecure_http:
            raise ValueError("plain HTTP selection sources require allow_insecure_http=True")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("selection source URLs cannot contain user information")
        try:
            hostname = parsed.hostname
        except ValueError as exc:
            raise ValueError("selection source URL has an invalid hostname or port") from exc
        if hostname is None:
            raise ValueError("network selection sources require a hostname")
        if allowed_hosts is not None and _normalize_host(hostname) not in allowed_hosts:
            raise ValueError(f"selection source host {hostname!r} is not allowed")
        return
    if scheme in {"", "file"}:
        if not allow_local_file:
            raise ValueError("local selection sources require allow_local_file=True")
        if scheme == "file" and parsed.netloc:
            raise ValueError("file selection sources cannot contain a remote host")
        if parsed.query or parsed.fragment:
            raise ValueError("file selection sources cannot contain a query or fragment")
        return
    raise ValueError(f"unsupported selection source scheme {parsed.scheme!r}")


def _bounded_file_bytes(path: Path, maximum: int) -> bytes:
    try:
        if path.stat().st_size > maximum:
            raise ResolverError(f"selection artifact exceeds {maximum} bytes")
        with path.open("rb") as stream:
            payload = stream.read(maximum + 1)
    except OSError as exc:
        raise ResolverError(f"cannot read local selection artifact: {exc}") from exc
    if len(payload) > maximum:
        raise ResolverError(f"selection artifact exceeds {maximum} bytes")
    return payload


class DynamicResolver:
    """Refresh and pin immutable model choices for agent work units.

    Call ``resolve()`` once at the beginning of a work unit and retain the
    returned snapshot for all of its requests and subagents. This prevents a
    live policy refresh from changing models halfway through a trajectory.
    The built-in loader enforces transport, host, local-file, and artifact-size
    policy. A custom loader is trusted to enforce equivalent limits.
    """

    def __init__(
        self,
        source: str | Path,
        *,
        expected_selection_id: str,
        expected_frontier_id: str | None = None,
        expected_workload_id: str | None = None,
        expected_workload_version: str | None = None,
        refresh_interval: timedelta = timedelta(minutes=1),
        stale_if_error: timedelta = timedelta(hours=1),
        max_clock_skew: timedelta = timedelta(minutes=5),
        timeout_seconds: float = 10.0,
        allow_insecure_http: bool = False,
        allow_local_file: bool = False,
        allowed_hosts: Iterable[str] | None = None,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
        loader: Loader | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if refresh_interval < timedelta(0):
            raise ValueError("refresh_interval cannot be negative")
        if stale_if_error < timedelta(0):
            raise ValueError("stale_if_error cannot be negative")
        if max_clock_skew < timedelta(0):
            raise ValueError("max_clock_skew cannot be negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if (
            isinstance(max_artifact_bytes, bool)
            or not isinstance(max_artifact_bytes, int)
            or max_artifact_bytes <= 0
        ):
            raise ValueError("max_artifact_bytes must be a positive integer")
        if not expected_selection_id:
            raise ValueError("expected_selection_id must be non-empty")
        if expected_workload_version is not None and expected_workload_id is None:
            raise ValueError("expected_workload_version requires expected_workload_id")
        self.source = str(source)
        self.allowed_hosts = _normalize_allowed_hosts(allowed_hosts)
        if loader is None:
            _validate_source_policy(
                self.source,
                allow_insecure_http=allow_insecure_http,
                allow_local_file=allow_local_file,
                allowed_hosts=self.allowed_hosts,
            )
        self.expected_selection_id = expected_selection_id
        self.expected_frontier_id = expected_frontier_id
        self.expected_workload_id = expected_workload_id
        self.expected_workload_version = expected_workload_version
        self.refresh_interval = refresh_interval
        self.stale_if_error = stale_if_error
        self.max_clock_skew = max_clock_skew
        self.timeout_seconds = timeout_seconds
        self.max_artifact_bytes = max_artifact_bytes
        self._loader: Loader
        if loader is None:

            def default_loader(
                source_value: str,
                etag: str | None,
                timeout: float,
            ) -> tuple[Mapping[str, Any] | None, str | None]:
                return self._load(
                    source_value,
                    etag,
                    timeout,
                    allow_insecure_http=allow_insecure_http,
                    allow_local_file=allow_local_file,
                    allowed_hosts=self.allowed_hosts,
                    max_artifact_bytes=max_artifact_bytes,
                )

            self._loader = default_loader
        else:
            self._loader = loader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._cached: SelectionSnapshot | None = None
        self._etag: str | None = None
        self._last_attempt: float | None = None

    @staticmethod
    def _load(
        source: str,
        etag: str | None,
        timeout_seconds: float,
        *,
        allow_insecure_http: bool = False,
        allow_local_file: bool = False,
        allowed_hosts: Iterable[str] | None = None,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> tuple[Mapping[str, Any] | None, str | None]:
        if (
            isinstance(max_artifact_bytes, bool)
            or not isinstance(max_artifact_bytes, int)
            or max_artifact_bytes <= 0
        ):
            raise ValueError("max_artifact_bytes must be a positive integer")
        normalized_hosts = _normalize_allowed_hosts(allowed_hosts)
        _validate_source_policy(
            source,
            allow_insecure_http=allow_insecure_http,
            allow_local_file=allow_local_file,
            allowed_hosts=normalized_hosts,
        )
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            headers = {"Accept": "application/json"}
            if etag:
                headers["If-None-Match"] = etag
            with httpx.stream(
                "GET",
                source,
                headers=headers,
                timeout=timeout_seconds,
                follow_redirects=False,
            ) as response:
                if response.status_code == 304:
                    return None, etag
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise ResolverError(
                            "selection source returned an invalid Content-Length"
                        ) from exc
                    if declared_size < 0 or declared_size > max_artifact_bytes:
                        raise ResolverError(
                            f"selection artifact exceeds {max_artifact_bytes} bytes"
                        )
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > max_artifact_bytes:
                        raise ResolverError(
                            f"selection artifact exceeds {max_artifact_bytes} bytes"
                        )
                next_etag = response.headers.get("etag")
            payload = json.loads(
                content,
                parse_float=Decimal,
                parse_constant=Decimal,
            )
        elif parsed.scheme in {"", "file"}:
            path = Path(parsed.path if parsed.scheme == "file" else source)
            payload = json.loads(
                _bounded_file_bytes(path, max_artifact_bytes),
                parse_float=Decimal,
                parse_constant=Decimal,
            )
            next_etag = None
        else:
            raise ResolverError(f"unsupported selection source scheme {parsed.scheme!r}")
        if not isinstance(payload, Mapping):
            raise ResolverError("selection artifact must be a JSON object")
        return payload, next_etag

    @staticmethod
    def _fresh(snapshot: SelectionSnapshot, now: datetime) -> bool:
        return now <= snapshot.valid_until

    def _stale_usable(self, snapshot: SelectionSnapshot, now: datetime) -> bool:
        return now <= snapshot.valid_until + self.stale_if_error

    def _validated(self, payload: Mapping[str, Any]) -> SelectionSnapshot:
        snapshot = SelectionSnapshot.model_validate(payload)
        expected = selection_hash(snapshot)
        if snapshot.snapshot_id != expected:
            raise ResolverError(
                f"selection snapshot hash mismatch: expected {expected}, "
                f"received {snapshot.snapshot_id}"
            )
        if snapshot.selection_id != self.expected_selection_id:
            raise ResolverError(
                f"selection identity mismatch: expected {self.expected_selection_id!r}, "
                f"received {snapshot.selection_id!r}"
            )
        if (
            self.expected_frontier_id is not None
            and snapshot.frontier_id != self.expected_frontier_id
        ):
            raise ResolverError(
                f"frontier identity mismatch: expected {self.expected_frontier_id!r}, "
                f"received {snapshot.frontier_id!r}"
            )
        if (
            self.expected_workload_id is not None
            and snapshot.workload.id != self.expected_workload_id
        ):
            raise ResolverError(
                f"workload identity mismatch: expected {self.expected_workload_id!r}, "
                f"received {snapshot.workload.id!r}"
            )
        if (
            self.expected_workload_version is not None
            and snapshot.workload.version != self.expected_workload_version
        ):
            raise ResolverError(
                "workload version mismatch: expected "
                f"{self.expected_workload_version!r}, received {snapshot.workload.version!r}"
            )
        return snapshot

    @staticmethod
    def _defensive_copy(snapshot: SelectionSnapshot) -> SelectionSnapshot:
        return snapshot.model_copy(deep=True)

    def resolve(self, *, force_refresh: bool = False) -> SelectionSnapshot:
        with self._lock:
            now = self._clock()
            if now.tzinfo is None:
                raise ResolverError("resolver clock must return a timezone-aware timestamp")
            monotonic_now = time.monotonic()
            refresh_due = (
                force_refresh
                or self._cached is None
                or self._last_attempt is None
                or (self._cached is not None and not self._fresh(self._cached, now))
                or monotonic_now - self._last_attempt >= self.refresh_interval.total_seconds()
            )
            if not refresh_due and self._cached is not None:
                if self._fresh(self._cached, now):
                    return self._defensive_copy(self._cached)
                raise ResolverError("cached selection expired")

            self._last_attempt = monotonic_now
            try:
                payload, etag = self._loader(self.source, self._etag, self.timeout_seconds)
                if payload is None:
                    if self._cached is None:
                        raise ResolverError(
                            "source returned not-modified without a cached snapshot"
                        )
                    candidate = self._cached
                else:
                    candidate = self._validated(payload)
                now = self._clock()
                if now.tzinfo is None:
                    raise ResolverError("resolver clock must return a timezone-aware timestamp")
                if candidate.generated_at > now + self.max_clock_skew:
                    raise ResolverError("published selection is generated in the future")
                if self._cached is not None and candidate.generated_at < self._cached.generated_at:
                    raise ResolverError("published selection would roll back the cached snapshot")
                if not self._fresh(candidate, now):
                    raise ResolverError("published selection expired")
            except Exception as exc:
                recovery_now = self._clock()
                if recovery_now.tzinfo is None:
                    raise ResolverError(
                        "resolver clock must return a timezone-aware timestamp"
                    ) from exc
                if self._cached is not None and self._stale_usable(self._cached, recovery_now):
                    return self._defensive_copy(self._cached)
                if isinstance(exc, ResolverError):
                    raise
                raise ResolverError(f"cannot refresh selection: {exc}") from exc

            self._cached = candidate
            self._etag = etag
            return self._defensive_copy(candidate)
