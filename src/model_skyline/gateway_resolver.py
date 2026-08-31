"""Fail-closed refresh and trajectory pinning for signed gateway selections."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import Field

from model_skyline.gateway import (
    GATEWAY_ENVELOPE_MEDIA_TYPE,
    GATEWAY_PUBLICATION_MEDIA_TYPE,
    GATEWAY_SELECTION_MEDIA_TYPE,
    MAX_GATEWAY_ENVELOPE_BYTES,
    GatewayExpiredError,
    GatewayInstallationStore,
    GatewayNotYetValidError,
    GatewayProtocolError,
    GatewaySequenceCheckpoint,
    GatewaySequenceError,
    GatewayTrustPolicy,
    PinnedGatewayRoute,
    StoredGatewayBundle,
    VerifiedGatewaySelection,
    build_stored_gateway_bundle,
    dsse_payload_bytes,
    parse_dsse_envelope,
    parse_gateway_pointer,
    pin_gateway_route,
    verify_gateway_bundle,
    verify_gateway_envelope,
)
from model_skyline.models import FrozenModel, PortablePublicationId, Sha256Digest


class GatewayResolverError(RuntimeError):
    """A signed route cannot be admitted safely."""


class GatewayTransportError(GatewayResolverError):
    """A retryable fetch failure occurred before authenticated bytes arrived."""


class _LocalTargetBindingsChanged(GatewayProtocolError):
    """Stored signed state is sound but cannot be routed by the new local policy."""


@dataclass(frozen=True, slots=True)
class GatewayFetchResult:
    payload: bytes | None
    etag: str | None = None


class GatewayByteFetcher(Protocol):
    def fetch(
        self,
        url: str,
        *,
        expected_media_type: str,
        maximum_bytes: int,
        timeout_seconds: float,
        etag: str | None = None,
    ) -> GatewayFetchResult: ...


class HttpxGatewayFetcher:
    """Bounded exact-byte HTTPS client with redirects and compression disabled."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def fetch(
        self,
        url: str,
        *,
        expected_media_type: str,
        maximum_bytes: int,
        timeout_seconds: float,
        etag: str | None = None,
    ) -> GatewayFetchResult:
        headers = {
            "Accept": expected_media_type,
            "Accept-Encoding": "identity",
        }
        if etag is not None:
            headers["If-None-Match"] = etag
        try:
            with (
                httpx.Client(
                    transport=self.transport,
                    timeout=httpx.Timeout(timeout_seconds),
                    follow_redirects=False,
                ) as client,
                client.stream("GET", url, headers=headers) as response,
            ):
                if response.status_code == 304:
                    return GatewayFetchResult(payload=None, etag=etag)
                if response.status_code in {408, 425, 429} or response.status_code >= 500:
                    raise GatewayTransportError(
                        f"gateway source returned retryable HTTP {response.status_code}"
                    )
                if 300 <= response.status_code < 400:
                    raise GatewayProtocolError("gateway source attempted an HTTP redirect")
                if response.status_code < 200 or response.status_code >= 300:
                    raise GatewayProtocolError(
                        f"gateway source returned HTTP {response.status_code}"
                    )
                if response.status_code == 206 or response.headers.get("content-range"):
                    raise GatewayProtocolError("gateway source returned a partial representation")
                content_encoding = response.headers.get("content-encoding", "identity").casefold()
                if content_encoding not in {"", "identity"}:
                    raise GatewayProtocolError("gateway source returned compressed content")
                media_type = response.headers.get("content-type", "").split(";", 1)[0]
                if media_type.strip().casefold() != expected_media_type.casefold():
                    raise GatewayProtocolError(
                        f"gateway source returned unexpected media type {media_type!r}"
                    )
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        declared_size = int(declared)
                    except ValueError as exc:
                        raise GatewayProtocolError(
                            "gateway source returned an invalid Content-Length"
                        ) from exc
                    if declared_size < 0 or declared_size > maximum_bytes:
                        raise GatewayProtocolError(
                            f"gateway source exceeds the {maximum_bytes}-byte limit"
                        )
                content = bytearray()
                for chunk in response.iter_raw():
                    content.extend(chunk)
                    if len(content) > maximum_bytes:
                        raise GatewayProtocolError(
                            f"gateway source exceeds the {maximum_bytes}-byte limit"
                        )
                next_etag = _strong_etag(response.headers.get("etag"))
                return GatewayFetchResult(payload=bytes(content), etag=next_etag)
        except GatewayProtocolError:
            raise
        except GatewayTransportError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise GatewayTransportError(
                f"cannot fetch gateway source ({type(exc).__name__})"
            ) from exc


def _strong_etag(value: str | None) -> str | None:
    if value is None or value.startswith("W/") or len(value) > 256:
        return None
    if not (value.startswith('"') and value.endswith('"')):
        return None
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return None
    return value


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("gateway URL has an invalid port") from exc
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), port


def _validate_pointer_source(source: str, issuer: str) -> str:
    parsed = urlsplit(source)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("signed gateway pointer source must be an HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("signed gateway pointer source cannot contain user information")
    if parsed.query or parsed.fragment:
        raise ValueError("signed gateway pointer source cannot contain a query or fragment")
    if (
        "\\" in parsed.path
        or "%" in parsed.path
        or "//" in parsed.path
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise ValueError("signed gateway pointer source has a non-canonical path")
    if _origin(source) != _origin(issuer):
        raise ValueError("signed gateway pointer source must share the configured issuer origin")
    issuer_path = urlsplit(issuer).path.rstrip("/")
    if issuer_path and not parsed.path.startswith(f"{issuer_path}/"):
        raise ValueError("signed gateway pointer source must be below the issuer path")
    return source


def _artifact_url(issuer: str, path: str) -> str:
    # RelativeArtifactPath already excludes traversal, backslashes, queries,
    # fragments, empty segments, and absolute paths.
    return f"{issuer}/{path}"


class GatewayResolverState(StrEnum):
    EMPTY = "empty"
    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    EXPIRED = "expired"


class GatewayResolverStatus(FrozenModel):
    state: GatewayResolverState
    trust_namespace: PortablePublicationId
    audience: PortablePublicationId
    channel: PortablePublicationId
    sequence: int | None = Field(default=None, ge=1)
    payload_sha256: Sha256Digest | None = None
    hard_expires_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_class: Literal["transport", "not-yet-valid", "security", "expired"] | None = None
    last_error: str | None = Field(default=None, max_length=512)


class SignedGatewayResolver:
    """Refresh signed control-plane state and pin one route per new work unit.

    Installed bytes and the monotonic sequence are committed by ``store`` before
    the new route becomes visible.  A caller must retain the returned
    ``PinnedGatewayRoute`` for the complete trajectory and pass only its target
    IDs to the data plane.
    """

    def __init__(
        self,
        source: str,
        *,
        policy: GatewayTrustPolicy,
        store: GatewayInstallationStore,
        refresh_interval: timedelta = timedelta(minutes=1),
        timeout_seconds: float = 15.0,
        fetcher: GatewayByteFetcher | None = None,
        clock: Callable[[], datetime] | None = None,
        allow_unexpired_lkg_after_security_error: bool = False,
    ) -> None:
        if refresh_interval < timedelta(seconds=30):
            raise ValueError("refresh_interval must be at least 30 seconds")
        if not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 0 and 60 seconds")
        self.source = _validate_pointer_source(source, policy.issuer)
        self.policy = policy
        self.store = store
        self.refresh_interval = refresh_interval
        self.timeout_seconds = timeout_seconds
        self.fetcher = fetcher or HttpxGatewayFetcher()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.allow_unexpired_lkg_after_security_error = allow_unexpired_lkg_after_security_error
        self._lock = threading.RLock()
        self._active: VerifiedGatewaySelection | None = None
        self._stored: StoredGatewayBundle | None = None
        self._etag: str | None = None
        self._last_attempt_monotonic: float | None = None
        self._last_attempt_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error_class: (
            Literal["transport", "not-yet-valid", "security", "expired"] | None
        ) = None
        self._last_error: str | None = None
        self._blocked = False
        self._admission_source: Literal["fresh", "last-known-good"] = "last-known-good"
        self._last_observed_at: datetime | None = None
        self._load_installed()

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise GatewayResolverError("resolver clock must return a timezone-aware timestamp")
        instant = value.astimezone(UTC)
        if self._last_observed_at is not None and instant < self._last_observed_at:
            self._blocked = True
            raise GatewayResolverError("resolver clock moved backward")
        self._last_observed_at = instant
        return instant

    def _identity(self) -> dict[str, str]:
        return {
            "trust_namespace": self.policy.trust_namespace,
            "issuer": self.policy.issuer,
            "audience": self.policy.audience,
            "channel": self.policy.channel,
        }

    def _load_installed(self) -> None:
        stored = self.store.load(**self._identity())
        if stored is None:
            return
        now = self._now()
        if now < stored.installed_at:
            self._blocked = True
            raise GatewayResolverError("resolver clock precedes the durable installation time")
        try:
            verified = self._verify_stored(stored, now=now)
        except _LocalTargetBindingsChanged:
            # Preserve the authenticated remote sequence floor, but never
            # retarget its LKG bytes under a different local revision. A higher
            # signed sequence can now be fetched and installed with this policy.
            self._stored = stored
            self._last_error_class = "security"
            self._last_error = "local target bindings changed; a higher signed sequence is required"
            return
        except GatewayProtocolError as exc:
            raise GatewayResolverError(f"installed gateway state is untrustworthy: {exc}") from exc
        if now >= verified.pointer.hard_expires_at or now >= verified.selection.valid_until:
            self._stored = stored
            self._last_error_class = "expired"
            self._last_error = "installed selection reached its hard expiry"
            self._blocked = True
            return
        self._active = verified
        self._stored = stored
        self._last_success_at = stored.installed_at

    @staticmethod
    def _same_stored_bytes(left: StoredGatewayBundle, right: StoredGatewayBundle) -> bool:
        return (
            left.checkpoint == right.checkpoint
            and left.envelope_payload == right.envelope_payload
            and left.publication_payload == right.publication_payload
            and left.selection_payload == right.selection_payload
            and left.target_bindings_payload == right.target_bindings_payload
        )

    def _verify_stored(
        self,
        stored: StoredGatewayBundle,
        *,
        now: datetime,
        checkpoint: GatewaySequenceCheckpoint | None = None,
    ) -> VerifiedGatewaySelection:
        """Verify persisted bytes, then derive and exactly match their checkpoint."""

        try:
            verified = verify_gateway_bundle(
                stored.envelope_payload,
                stored.publication_payload,
                stored.selection_payload,
                self.policy,
                now=now,
                checkpoint=checkpoint,
            )
        except GatewayExpiredError:
            # Expired bytes still carry the anti-rollback floor. Authenticate
            # and bind that floor at a time when the signed generation was
            # active; it remains ineligible for admission at ``now``.
            envelope = parse_dsse_envelope(stored.envelope_payload)
            pointer = parse_gateway_pointer(dsse_payload_bytes(envelope))
            historical_time = max(pointer.issued_at, pointer.not_before)
            verified = verify_gateway_bundle(
                stored.envelope_payload,
                stored.publication_payload,
                stored.selection_payload,
                self.policy,
                now=historical_time,
                checkpoint=checkpoint,
            )
        if verified.checkpoint != stored.checkpoint:
            comparable = (
                "trust_namespace",
                "issuer",
                "audience",
                "channel",
                "sequence",
                "payload_sha256",
                "publication_artifact_sha256",
                "selection_artifact_sha256",
                "selection_snapshot_id",
                "hard_expires_at",
            )
            if all(
                getattr(verified.checkpoint, field) == getattr(stored.checkpoint, field)
                for field in comparable
            ):
                raise _LocalTargetBindingsChanged
            raise GatewaySequenceError(
                "stored checkpoint is not exactly derived from its verified signed bundle"
            )
        return verified

    def _synchronize_durable(self, now: datetime) -> bool:
        """Adopt a concurrently installed generation before admission.

        The durable read is the admission linearization point. A generation
        committed after this read applies to the next work-unit admission.
        """

        stored = self.store.load(**self._identity())
        if stored is None:
            if self._stored is not None or self._active is not None:
                raise GatewayProtocolError("durable gateway installation disappeared")
            return False
        if self._stored is not None and self._same_stored_bytes(stored, self._stored):
            return False
        baseline = None if self._stored is None else self._stored.checkpoint
        verified = self._verify_stored(stored, now=now, checkpoint=baseline)
        if now >= verified.pointer.hard_expires_at or now >= verified.selection.valid_until:
            raise GatewayExpiredError("durable gateway installation reached its hard expiry")
        self._stored = stored
        self._active = verified
        self._last_success_at = stored.installed_at
        self._last_error_class = None
        self._last_error = None
        self._blocked = False
        self._admission_source = "fresh"
        return True

    def _checkpoint(self) -> GatewaySequenceCheckpoint | None:
        return self.store.current(**self._identity())

    def _artifact_payload(
        self,
        *,
        path: str,
        digest: str,
        expected_media_type: str,
        stored_payload: bytes | None,
    ) -> bytes:
        if stored_payload is not None and hashlib.sha256(stored_payload).hexdigest() == digest:
            return stored_payload
        result = self.fetcher.fetch(
            _artifact_url(self.policy.issuer, path),
            expected_media_type=expected_media_type,
            maximum_bytes=self.policy.max_artifact_bytes,
            timeout_seconds=self.timeout_seconds,
        )
        if result.payload is None:
            raise GatewayProtocolError("immutable gateway artifact returned not-modified")
        return result.payload

    def _refresh(self, now: datetime) -> VerifiedGatewaySelection:
        envelope_result = self.fetcher.fetch(
            self.source,
            expected_media_type=GATEWAY_ENVELOPE_MEDIA_TYPE,
            maximum_bytes=MAX_GATEWAY_ENVELOPE_BYTES,
            timeout_seconds=self.timeout_seconds,
            etag=self._etag,
        )
        if envelope_result.payload is None:
            if self._stored is None:
                raise GatewayProtocolError("pointer returned not-modified without cached bytes")
            envelope_payload = self._stored.envelope_payload
        else:
            envelope_payload = envelope_result.payload
        authenticated = verify_gateway_envelope(
            envelope_payload,
            self.policy,
            now=now,
            checkpoint=self._checkpoint(),
        )
        pointer = authenticated.pointer
        stored_publication = None if self._stored is None else self._stored.publication_payload
        stored_selection = None if self._stored is None else self._stored.selection_payload
        publication_payload = self._artifact_payload(
            path=pointer.publication.file.path,
            digest=pointer.publication.file.sha256,
            expected_media_type=GATEWAY_PUBLICATION_MEDIA_TYPE,
            stored_payload=stored_publication,
        )
        selection_payload = self._artifact_payload(
            path=pointer.selection.file.path,
            digest=pointer.selection.file.sha256,
            expected_media_type=GATEWAY_SELECTION_MEDIA_TYPE,
            stored_payload=stored_selection,
        )
        commit_time = self._now()
        verified = verify_gateway_bundle(
            envelope_payload,
            publication_payload,
            selection_payload,
            self.policy,
            now=commit_time,
            checkpoint=self._checkpoint(),
        )
        bundle = build_stored_gateway_bundle(
            verified,
            envelope_payload=envelope_payload,
            publication_payload=publication_payload,
            selection_payload=selection_payload,
            installed_at=commit_time,
        )
        # This commit is the linearization point.  Do not expose ``verified``
        # before the durable checkpoint and exact LKG bytes exist together.
        self.store.install(bundle)
        self._stored = bundle
        self._active = verified
        self._etag = envelope_result.etag
        self._last_success_at = commit_time
        self._last_error_class = None
        self._last_error = None
        self._blocked = False
        self._admission_source = "fresh"
        return verified

    def _active_usable(self, now: datetime, minimum_headroom: timedelta) -> bool:
        return (
            self._active is not None
            and now + minimum_headroom < self._active.pointer.hard_expires_at
            and now < self._active.selection.valid_until
        )

    def resolve(
        self,
        *,
        required_capabilities: Sequence[str] = (),
        minimum_headroom: timedelta = timedelta(0),
        force_refresh: bool = False,
    ) -> PinnedGatewayRoute:
        with self._lock:
            now = self._now()
            monotonic_now = time.monotonic()
            refresh_due = (
                force_refresh
                or self._active is None
                or self._last_attempt_monotonic is None
                or monotonic_now - self._last_attempt_monotonic
                >= self.refresh_interval.total_seconds()
            )
            if refresh_due:
                self._last_attempt_monotonic = monotonic_now
                self._last_attempt_at = now
                try:
                    self._refresh(now)
                except GatewayTransportError as exc:
                    now = self._now()
                    if self._blocked:
                        if not (
                            self._last_error_class == "security"
                            and self.allow_unexpired_lkg_after_security_error
                            and self._active_usable(now, minimum_headroom)
                        ):
                            raise GatewayResolverError(
                                "signed gateway admissions remain blocked"
                            ) from exc
                    else:
                        self._last_error_class = "transport"
                        self._last_error = str(exc)[:512]
                    if not self._active_usable(now, minimum_headroom):
                        raise
                    self._admission_source = "last-known-good"
                except GatewayNotYetValidError as exc:
                    now = self._now()
                    if self._blocked:
                        if not (
                            self._last_error_class == "security"
                            and self.allow_unexpired_lkg_after_security_error
                            and self._active_usable(now, minimum_headroom)
                        ):
                            raise GatewayResolverError(
                                "signed gateway admissions remain blocked"
                            ) from exc
                    else:
                        self._last_error_class = "not-yet-valid"
                        self._last_error = str(exc)[:512]
                    if not self._active_usable(now, minimum_headroom):
                        raise GatewayResolverError(str(exc)) from exc
                    self._admission_source = "last-known-good"
                except GatewayExpiredError as exc:
                    self._last_error_class = "expired"
                    self._last_error = str(exc)[:512]
                    self._blocked = True
                    raise GatewayResolverError(str(exc)) from exc
                except GatewayProtocolError as exc:
                    now = self._now()
                    if self._blocked and self._last_error_class != "security":
                        raise GatewayResolverError(
                            "signed gateway admissions remain blocked"
                        ) from exc
                    self._last_error_class = "security"
                    self._last_error = str(exc)[:512]
                    self._blocked = True
                    if not (
                        self.allow_unexpired_lkg_after_security_error
                        and self._active_usable(now, minimum_headroom)
                    ):
                        raise GatewayResolverError(
                            f"signed gateway refresh failed closed: {exc}"
                        ) from exc
                    self._admission_source = "last-known-good"
            elif self._blocked and (
                self._last_error_class != "security"
                or not self.allow_unexpired_lkg_after_security_error
            ):
                raise GatewayResolverError("signed gateway admissions remain blocked")
            now = self._now()
            try:
                self._synchronize_durable(now)
            except GatewayExpiredError as exc:
                self._last_error_class = "expired"
                self._last_error = str(exc)[:512]
                self._blocked = True
                raise GatewayResolverError(str(exc)) from exc
            except GatewayProtocolError as exc:
                self._last_error_class = "security"
                self._last_error = str(exc)[:512]
                self._blocked = True
                raise GatewayResolverError(
                    f"durable gateway synchronization failed closed: {exc}"
                ) from exc
            # Durable reads and verification are untrusted-duration work. Pin
            # against a fresh clock value so they cannot cross hard expiry.
            now = self._now()
            if self._active is None:
                raise GatewayResolverError("no verified gateway selection is installed")
            try:
                return pin_gateway_route(
                    self._active,
                    now=now,
                    required_capabilities=required_capabilities,
                    minimum_headroom=minimum_headroom,
                    admission_source=self._admission_source,
                )
            except GatewayProtocolError as exc:
                raise GatewayResolverError(str(exc)) from exc

    def status(self) -> GatewayResolverStatus:
        with self._lock:
            now = self._now()
            active = self._active
            if active is None:
                state = (
                    GatewayResolverState.EXPIRED
                    if self._last_error_class == "expired"
                    else GatewayResolverState.EMPTY
                )
            elif now >= active.pointer.hard_expires_at:
                state = GatewayResolverState.EXPIRED
            elif self._blocked:
                state = GatewayResolverState.BLOCKED
            elif self._last_error_class is not None:
                state = GatewayResolverState.DEGRADED
            else:
                state = GatewayResolverState.READY
            return GatewayResolverStatus(
                state=state,
                trust_namespace=self.policy.trust_namespace,
                audience=self.policy.audience,
                channel=self.policy.channel,
                sequence=None if active is None else active.pointer.sequence,
                payload_sha256=(
                    None if active is None else active.authenticated_pointer.payload_sha256
                ),
                hard_expires_at=None if active is None else active.pointer.hard_expires_at,
                last_attempt_at=self._last_attempt_at,
                last_success_at=self._last_success_at,
                last_error_class=self._last_error_class,
                last_error=self._last_error,
            )
