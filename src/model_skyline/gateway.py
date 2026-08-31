"""Gateway-neutral, signed model-selection protocol.

The wire format is deliberately independent of Python.  A small DSSE envelope
authenticates an RFC 8785 pointer; the pointer binds the exact bytes of an
existing publication manifest and SelectionSnapshot.  Semantic ModelSkyline
hashes are verified as a second, distinct integrity domain.

Cryptographic imports are lazy so schemas and protocol models remain available
from the base package.  Signing and verification require the ``gateway`` extra.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, Self, cast
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    ConfigDict,
    Field,
    ValidationError,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from model_skyline.canonical import canonical_bytes, content_hash
from model_skyline.models import (
    MAX_CAPABILITIES,
    MAX_SAFE_INTEGER,
    CapabilityName,
    FrozenModel,
    OfferingKey,
    PortablePublicationId,
    PositiveSafeCount,
    PublicationManifest,
    RelativeArtifactPath,
    SelectionSnapshot,
    Sha256Digest,
    WorkloadReference,
)
from model_skyline.publisher import publication_hash
from model_skyline.selection import selection_hash, selection_hash_matches

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

GATEWAY_POINTER_SCHEMA_VERSION: Literal["model-skyline/gateway-selection-pointer/v1alpha1"] = (
    "model-skyline/gateway-selection-pointer/v1alpha1"
)
GATEWAY_POINTER_KIND: Literal["gateway-selection-pointer"] = "gateway-selection-pointer"
GATEWAY_POINTER_PAYLOAD_TYPE = (
    "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+json"
)
GATEWAY_ENVELOPE_MEDIA_TYPE = (
    "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+dsse"
)
GATEWAY_PUBLICATION_MEDIA_TYPE = "application/json"
GATEWAY_SELECTION_MEDIA_TYPE = "application/json"
OFFERING_IDENTITY_PROFILE: Literal["model-skyline/offering-key/v1alpha1"] = (
    "model-skyline/offering-key/v1alpha1"
)
GATEWAY_SIGNATURE_ALGORITHM: Literal["Ed25519"] = "Ed25519"

MAX_GATEWAY_ENVELOPE_BYTES = 64 * 1024
MAX_GATEWAY_POINTER_BYTES = 32 * 1024
DEFAULT_MAX_GATEWAY_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_GATEWAY_ARTIFACT_BYTES = 10 * 1024 * 1024
MAX_GATEWAY_SIGNATURES = 16
MAX_GATEWAY_TRUSTED_KEYS = 32
MAX_GATEWAY_AUDIENCES = 16
MAX_GATEWAY_POINTER_LIFETIME_SECONDS = 31 * 24 * 60 * 60
MAX_GATEWAY_NOT_BEFORE_LEAD_SECONDS = 24 * 60 * 60
MAX_GATEWAY_JSON_DEPTH = 32
MAX_GATEWAY_JSON_VALUES = 100_000
MAX_GATEWAY_RUNTIME_CANDIDATES = 1_024

_STANDARD_BASE64_RE = re.compile(
    r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"
)
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_KEY_ID_RE = re.compile(r"^urn:ietf:params:oauth:jwk-thumbprint:sha-256:[A-Za-z0-9_-]{43}$")


class GatewayProtocolError(ValueError):
    """A gateway envelope or one of its bound artifacts is untrustworthy."""


class GatewayCryptoUnavailable(GatewayProtocolError):
    """The optional cryptographic implementation is not installed."""


class GatewaySequenceError(GatewayProtocolError):
    """A pointer attempts rollback or same-sequence equivocation."""


class GatewayExpiredError(GatewayProtocolError):
    """A pointer or its bound selection is past its hard expiry."""


class GatewayNotYetValidError(GatewayProtocolError):
    """An authenticated pointer is valid but not active yet."""


def _cryptography() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as exc:  # pragma: no cover - exercised in an isolated install smoke test
        raise GatewayCryptoUnavailable(
            "gateway signing requires `pip install model-skyline[gateway]`"
        ) from exc
    return (
        Ed25519PrivateKey,
        Ed25519PublicKey,
        InvalidSignature,
        UnsupportedAlgorithm,
        serialization,
    )


def _standard_base64_encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _standard_base64_decode(
    value: str,
    *,
    field: str,
    expected_bytes: int | None = None,
    maximum_bytes: int | None = None,
) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty Base64 string")
    if not _STANDARD_BASE64_RE.fullmatch(value):
        raise ValueError(f"{field} must be canonical padded RFC 4648 Base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{field} must be canonical padded RFC 4648 Base64") from exc
    if _standard_base64_encode(decoded) != value:
        raise ValueError(f"{field} must be canonical padded RFC 4648 Base64")
    if expected_bytes is not None and len(decoded) != expected_bytes:
        raise ValueError(f"{field} must encode exactly {expected_bytes} bytes")
    if maximum_bytes is not None and len(decoded) > maximum_bytes:
        raise ValueError(f"{field} exceeds the {maximum_bytes}-byte decoded limit")
    return decoded


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str, *, expected_bytes: int, field: str) -> bytes:
    if not isinstance(value, str) or not _BASE64URL_RE.fullmatch(value):
        raise ValueError(f"{field} must be unpadded Base64url")
    try:
        decoded = base64.b64decode(
            value.encode("ascii") + b"=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{field} must be unpadded Base64url") from exc
    if len(decoded) != expected_bytes or _base64url_encode(decoded) != value:
        raise ValueError(f"{field} must canonically encode exactly {expected_bytes} bytes")
    return decoded


def _validate_public_key(value: str) -> str:
    _base64url_decode(value, expected_bytes=32, field="public_key.x")
    return value


def _validate_key_id(value: str) -> str:
    if not _KEY_ID_RE.fullmatch(value):
        raise ValueError("keyid must be an RFC 9278 SHA-256 JWK thumbprint URI")
    return value


Ed25519PublicKeyValue = Annotated[
    str,
    Field(min_length=43, max_length=43),
    AfterValidator(_validate_public_key),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^[A-Za-z0-9_-]{43}$",
            "minLength": 43,
            "maxLength": 43,
            "contentEncoding": "base64url",
        }
    ),
]
GatewayKeyId = Annotated[
    str,
    Field(min_length=88, max_length=88),
    AfterValidator(_validate_key_id),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": (r"^urn:ietf:params:oauth:jwk-thumbprint:sha-256:[A-Za-z0-9_-]{43}$"),
            "minLength": 88,
            "maxLength": 88,
            "format": "uri",
        }
    ),
]


def _normalize_issuer(value: str) -> str:
    try:
        parsed_url = AnyHttpUrl(value)
        parsed = urlsplit(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("gateway issuer must be a valid HTTPS URL") from exc
    if parsed_url.scheme != "https":
        raise ValueError("gateway issuer must use HTTPS")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise ValueError("gateway issuer cannot contain user information")
    if parsed_url.query is not None or parsed_url.fragment is not None:
        raise ValueError("gateway issuer cannot contain a query or fragment")
    if parsed.path.endswith("/"):
        raise ValueError("gateway issuer cannot have a trailing slash")
    rendered = str(parsed_url)
    if parsed_url.path == "/":
        rendered = rendered[:-1]
    if rendered != value:
        raise ValueError(f"gateway issuer must use normalized form {rendered!r}")
    return rendered


GatewayIssuer = Annotated[
    str,
    Field(max_length=2083),
    AfterValidator(_normalize_issuer),
    WithJsonSchema(
        {
            "type": "string",
            "format": "uri",
            "pattern": r"^https://[^@?#]+[^/@?#]$",
            "maxLength": 2083,
        }
    ),
]


DsseSignatureValue = Annotated[
    str,
    Field(min_length=88, max_length=88),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^(?:[A-Za-z0-9+/]{4}){21}[A-Za-z0-9+/]{2}==$",
            "minLength": 88,
            "maxLength": 88,
            "contentEncoding": "base64",
        }
    ),
]
DssePayloadValue = Annotated[
    str,
    Field(min_length=4, max_length=4 * ((MAX_GATEWAY_POINTER_BYTES + 2) // 3)),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": (r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"),
            "minLength": 4,
            "maxLength": 4 * ((MAX_GATEWAY_POINTER_BYTES + 2) // 3),
            "contentEncoding": "base64",
        }
    ),
]


def _canonical_string_tuple(
    value: Any,
    *,
    field: str,
    minimum: int = 0,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be an array of strings")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must contain only strings")
    if not minimum <= len(value) <= maximum:
        raise ValueError(f"{field} must contain between {minimum} and {maximum} values")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicates")
    if list(value) != sorted(value):
        raise ValueError(f"{field} must be sorted")
    return tuple(value)


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("gateway timestamps must include a timezone")
    return value.astimezone(UTC).replace(microsecond=0)


GatewayTimestamp = Annotated[
    datetime,
    AfterValidator(_utc_timestamp),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:[0-5]\dZ$",
        },
        mode="serialization",
    ),
]


class GatewayFileReference(FrozenModel):
    path: RelativeArtifactPath
    length: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_GATEWAY_ARTIFACT_BYTES),
    ]
    sha256: Sha256Digest
    media_type: Literal["application/json"]


class GatewayPublicationReference(FrozenModel):
    publication_id: Sha256Digest
    file: GatewayFileReference

    @model_validator(mode="after")
    def file_path_matches_identity(self) -> Self:
        expected = f"publications/{self.publication_id}.json"
        if self.file.path != expected:
            raise ValueError(f"publication file path must be {expected!r}")
        return self


class GatewaySelectionReference(FrozenModel):
    schema_version: Literal["model-skyline/v1alpha1"]
    kind: Literal["selection"]
    snapshot_id: Sha256Digest
    policy_hash: Sha256Digest
    frontier_id: PortablePublicationId
    frontier_snapshot_id: Sha256Digest
    workload: WorkloadReference
    required_capabilities: tuple[CapabilityName, ...] = Field(
        default=(),
        max_length=MAX_CAPABILITIES,
        json_schema_extra={"uniqueItems": True},
    )
    offering_identity_profile: Literal["model-skyline/offering-key/v1alpha1"]
    file: GatewayFileReference

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def capabilities_are_canonical(cls, value: Any) -> tuple[str, ...]:
        return _canonical_string_tuple(
            value,
            field="required_capabilities",
            maximum=MAX_CAPABILITIES,
        )


class GatewaySelectionPointer(FrozenModel):
    schema_version: Literal["model-skyline/gateway-selection-pointer/v1alpha1"]
    kind: Literal["gateway-selection-pointer"]
    issuer: GatewayIssuer
    audience: tuple[PortablePublicationId, ...] = Field(
        min_length=1,
        max_length=MAX_GATEWAY_AUDIENCES,
        json_schema_extra={"uniqueItems": True},
    )
    channel: PortablePublicationId
    project_id: PortablePublicationId
    sequence: PositiveSafeCount
    issued_at: GatewayTimestamp
    not_before: GatewayTimestamp
    hard_expires_at: GatewayTimestamp
    publication: GatewayPublicationReference
    selection_id: PortablePublicationId
    selection: GatewaySelectionReference

    @field_validator("audience", mode="before")
    @classmethod
    def audiences_are_canonical(cls, value: Any) -> tuple[str, ...]:
        return _canonical_string_tuple(
            value,
            field="audience",
            minimum=1,
            maximum=MAX_GATEWAY_AUDIENCES,
        )

    @model_validator(mode="after")
    def pointer_is_coherent(self) -> Self:
        if self.issued_at > self.not_before:
            raise ValueError("issued_at cannot follow not_before")
        if self.not_before >= self.hard_expires_at:
            raise ValueError("hard_expires_at must follow not_before")
        if self.not_before - self.issued_at > timedelta(
            seconds=MAX_GATEWAY_NOT_BEFORE_LEAD_SECONDS
        ):
            raise ValueError("not_before lead exceeds 24 hours")
        if self.hard_expires_at - self.not_before > timedelta(
            seconds=MAX_GATEWAY_POINTER_LIFETIME_SECONDS
        ):
            raise ValueError("gateway pointer lifetime exceeds 31 days")
        expected = f"selections/{self.selection_id}/{self.selection.snapshot_id}.json"
        if self.selection.file.path != expected:
            raise ValueError(f"selection file path must be {expected!r}")
        return self


class DsseSignature(FrozenModel):
    keyid: GatewayKeyId
    sig: DsseSignatureValue

    @field_validator("sig")
    @classmethod
    def signature_is_canonical_base64(cls, value: str) -> str:
        _standard_base64_decode(value, field="sig", expected_bytes=64)
        return value


class DsseEnvelope(FrozenModel):
    model_config = ConfigDict(
        extra="allow",
        frozen=True,
        allow_inf_nan=False,
        populate_by_name=True,
    )

    payload_type: Literal[
        "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+json"
    ] = Field(alias="payloadType")
    payload: DssePayloadValue
    signatures: tuple[DsseSignature, ...] = Field(
        min_length=1,
        max_length=MAX_GATEWAY_SIGNATURES,
    )

    @field_validator("payload")
    @classmethod
    def payload_is_canonical_base64(cls, value: str) -> str:
        _standard_base64_decode(
            value,
            field="payload",
            maximum_bytes=MAX_GATEWAY_POINTER_BYTES,
        )
        return value


class GatewayPublicJwk(FrozenModel):
    crv: Literal["Ed25519"]
    kty: Literal["OKP"]
    x: Ed25519PublicKeyValue


class GatewayTrustedKey(FrozenModel):
    keyid: GatewayKeyId
    algorithm: Literal["Ed25519"]
    public_jwk: GatewayPublicJwk

    @model_validator(mode="after")
    def key_id_matches_jwk(self) -> Self:
        expected = gateway_key_id_from_jwk(self.public_jwk)
        if self.keyid != expected:
            raise ValueError(f"keyid must equal the JWK thumbprint URI {expected!r}")
        return self


class GatewayTargetBinding(FrozenModel):
    """Local-only binding; no remote endpoint or credential is accepted."""

    offering: OfferingKey
    target_id: PortablePublicationId
    target_revision: Sha256Digest
    capabilities: tuple[CapabilityName, ...] = Field(
        default=(),
        max_length=MAX_CAPABILITIES,
        json_schema_extra={"uniqueItems": True},
    )

    @field_validator("capabilities", mode="before")
    @classmethod
    def capabilities_are_canonical(cls, value: Any) -> tuple[str, ...]:
        return _canonical_string_tuple(
            value,
            field="target capabilities",
            maximum=MAX_CAPABILITIES,
        )


class GatewayTrustPolicy(FrozenModel):
    trust_namespace: PortablePublicationId
    issuer: GatewayIssuer
    audience: PortablePublicationId
    channel: PortablePublicationId
    project_id: PortablePublicationId
    trusted_keys: tuple[GatewayTrustedKey, ...] = Field(
        min_length=1,
        max_length=MAX_GATEWAY_TRUSTED_KEYS,
    )
    signature_threshold: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_GATEWAY_SIGNATURES),
    ] = 1
    expected_selection_id: PortablePublicationId
    expected_frontier_id: PortablePublicationId
    expected_workload: WorkloadReference
    expected_policy_hash: Sha256Digest | None = None
    required_capabilities: tuple[CapabilityName, ...] = Field(
        default=(),
        max_length=MAX_CAPABILITIES,
        json_schema_extra={"uniqueItems": True},
    )
    target_bindings: tuple[GatewayTargetBinding, ...] = Field(
        min_length=1,
        max_length=MAX_GATEWAY_RUNTIME_CANDIDATES,
    )
    minimum_sequence: PositiveSafeCount = 1
    max_future_skew_seconds: Annotated[int, Field(strict=True, ge=0, le=300)] = 300
    max_pointer_lifetime_seconds: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_GATEWAY_POINTER_LIFETIME_SECONDS),
    ] = 3600
    max_pointer_age_seconds: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_GATEWAY_POINTER_LIFETIME_SECONDS),
    ] = 86_400
    max_artifact_bytes: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_GATEWAY_ARTIFACT_BYTES),
    ] = DEFAULT_MAX_GATEWAY_ARTIFACT_BYTES
    max_candidates: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_GATEWAY_RUNTIME_CANDIDATES),
    ] = 128

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def capabilities_are_canonical(cls, value: Any) -> tuple[str, ...]:
        return _canonical_string_tuple(
            value,
            field="required_capabilities",
            maximum=MAX_CAPABILITIES,
        )

    @model_validator(mode="after")
    def trust_configuration_is_coherent(self) -> Self:
        key_ids = [item.keyid for item in self.trusted_keys]
        public_keys = [item.public_jwk.x for item in self.trusted_keys]
        if len(key_ids) != len(set(key_ids)) or len(public_keys) != len(set(public_keys)):
            raise ValueError("trusted keys must be distinct")
        if self.signature_threshold > len(self.trusted_keys):
            raise ValueError("signature_threshold exceeds the trusted key count")
        offering_keys = [canonical_bytes(item.offering) for item in self.target_bindings]
        target_ids = [item.target_id for item in self.target_bindings]
        if len(offering_keys) != len(set(offering_keys)):
            raise ValueError("target bindings must use distinct complete OfferingKeys")
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("each target_id must identify one complete OfferingKey")
        return self


class GatewaySequenceCheckpoint(FrozenModel):
    trust_namespace: PortablePublicationId
    issuer: GatewayIssuer
    audience: PortablePublicationId
    channel: PortablePublicationId
    sequence: PositiveSafeCount
    payload_sha256: Sha256Digest
    publication_artifact_sha256: Sha256Digest
    selection_artifact_sha256: Sha256Digest
    selection_snapshot_id: Sha256Digest
    target_bindings_sha256: Sha256Digest
    hard_expires_at: GatewayTimestamp


class AuthenticatedGatewayPointer(FrozenModel):
    pointer: GatewaySelectionPointer
    payload_sha256: Sha256Digest
    verified_key_ids: tuple[GatewayKeyId, ...] = Field(min_length=1)


class BoundGatewayTarget(FrozenModel):
    offering: OfferingKey
    target_id: PortablePublicationId
    target_revision: Sha256Digest
    capabilities: tuple[CapabilityName, ...]


class VerifiedGatewaySelection(FrozenModel):
    trust_namespace: PortablePublicationId
    configured_audience: PortablePublicationId
    authenticated_pointer: AuthenticatedGatewayPointer
    publication: PublicationManifest
    selection: SelectionSnapshot
    targets: tuple[BoundGatewayTarget, ...] = Field(
        min_length=1,
        max_length=MAX_GATEWAY_RUNTIME_CANDIDATES,
    )

    @property
    def pointer(self) -> GatewaySelectionPointer:
        return self.authenticated_pointer.pointer

    @property
    def checkpoint(self) -> GatewaySequenceCheckpoint:
        pointer = self.pointer
        return GatewaySequenceCheckpoint(
            trust_namespace=self.trust_namespace,
            issuer=pointer.issuer,
            audience=self.configured_audience,
            channel=pointer.channel,
            sequence=pointer.sequence,
            payload_sha256=self.authenticated_pointer.payload_sha256,
            publication_artifact_sha256=pointer.publication.file.sha256,
            selection_artifact_sha256=pointer.selection.file.sha256,
            selection_snapshot_id=pointer.selection.snapshot_id,
            target_bindings_sha256=target_bindings_hash(self.targets),
            hard_expires_at=pointer.hard_expires_at,
        )


class PinnedGatewayRoute(FrozenModel):
    trust_namespace: PortablePublicationId
    issuer: GatewayIssuer
    audience: PortablePublicationId
    channel: PortablePublicationId
    sequence: PositiveSafeCount
    payload_sha256: Sha256Digest
    selection_id: PortablePublicationId
    selection_snapshot_id: Sha256Digest
    policy_hash: Sha256Digest
    frontier_id: PortablePublicationId
    frontier_snapshot_id: Sha256Digest
    workload: WorkloadReference
    hard_expires_at: GatewayTimestamp
    admission_source: Literal["fresh", "last-known-good"] = "fresh"
    required_capabilities: tuple[CapabilityName, ...]
    targets: tuple[BoundGatewayTarget, ...] = Field(
        min_length=1,
        max_length=MAX_GATEWAY_RUNTIME_CANDIDATES,
    )


class GatewaySequenceStore(Protocol):
    """Atomically persist a fully verified installation before activation."""

    def accept(self, checkpoint: GatewaySequenceCheckpoint) -> None: ...


@dataclass(frozen=True, slots=True)
class StoredGatewayBundle:
    checkpoint: GatewaySequenceCheckpoint
    envelope_payload: bytes
    publication_payload: bytes
    selection_payload: bytes
    installed_at: datetime


class GatewayInstallationStore(Protocol):
    """Crash-consistent checkpoint plus exact LKG artifact storage."""

    def current(
        self,
        *,
        trust_namespace: str,
        issuer: str,
        audience: str,
        channel: str,
    ) -> GatewaySequenceCheckpoint | None: ...

    def load(
        self,
        *,
        trust_namespace: str,
        issuer: str,
        audience: str,
        channel: str,
    ) -> StoredGatewayBundle | None: ...

    def install(self, bundle: StoredGatewayBundle) -> None: ...


class InMemoryGatewaySequenceStore:
    """Ephemeral test store. Production consumers must use durable state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._checkpoints: dict[tuple[str, str, str, str], GatewaySequenceCheckpoint] = {}
        self._bundles: dict[tuple[str, str, str, str], StoredGatewayBundle] = {}

    @staticmethod
    def _key(checkpoint: GatewaySequenceCheckpoint) -> tuple[str, str, str, str]:
        return (
            checkpoint.trust_namespace,
            str(checkpoint.issuer),
            checkpoint.audience,
            checkpoint.channel,
        )

    def accept(self, checkpoint: GatewaySequenceCheckpoint) -> None:
        key = self._key(checkpoint)
        with self._lock:
            current = self._checkpoints.get(key)
            _check_sequence(checkpoint, current)
            if current is None or checkpoint.sequence > current.sequence:
                self._checkpoints[key] = checkpoint

    def current(
        self,
        *,
        trust_namespace: str,
        issuer: str,
        audience: str,
        channel: str,
    ) -> GatewaySequenceCheckpoint | None:
        with self._lock:
            return self._checkpoints.get((trust_namespace, issuer, audience, channel))

    def load(
        self,
        *,
        trust_namespace: str,
        issuer: str,
        audience: str,
        channel: str,
    ) -> StoredGatewayBundle | None:
        with self._lock:
            return self._bundles.get((trust_namespace, issuer, audience, channel))

    def install(self, bundle: StoredGatewayBundle) -> None:
        key = self._key(bundle.checkpoint)
        with self._lock:
            current = self._checkpoints.get(key)
            _check_sequence(bundle.checkpoint, current)
            self._checkpoints[key] = bundle.checkpoint
            self._bundles[key] = bundle


def _check_sequence(
    candidate: GatewaySequenceCheckpoint,
    current: GatewaySequenceCheckpoint | None,
) -> None:
    if current is None:
        return
    if candidate.sequence < current.sequence:
        raise GatewaySequenceError("gateway pointer sequence would roll back")
    if candidate.sequence == current.sequence:
        comparable = (
            "payload_sha256",
            "publication_artifact_sha256",
            "selection_artifact_sha256",
            "selection_snapshot_id",
            "target_bindings_sha256",
            "hard_expires_at",
        )
        if any(getattr(candidate, field) != getattr(current, field) for field in comparable):
            raise GatewaySequenceError("gateway pointer sequence would equivocate")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} is forbidden")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _check_json_complexity(value: Any) -> None:
    count = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        count += 1
        if count > MAX_GATEWAY_JSON_VALUES:
            raise ValueError(f"JSON exceeds the {MAX_GATEWAY_JSON_VALUES}-value limit")
        if depth > MAX_GATEWAY_JSON_DEPTH:
            raise ValueError(f"JSON exceeds the {MAX_GATEWAY_JSON_DEPTH}-level depth limit")
        if isinstance(current, dict):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)


def _strict_json_object(payload: bytes, *, maximum: int, label: str) -> dict[str, Any]:
    if not payload:
        raise GatewayProtocolError(f"{label} is empty")
    if len(payload) > maximum:
        raise GatewayProtocolError(f"{label} exceeds the {maximum}-byte limit")
    if payload.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        raise GatewayProtocolError(f"{label} cannot contain a byte-order mark")
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object,
        )
        if not isinstance(value, dict):
            raise ValueError("top-level value is not an object")
        _check_json_complexity(value)
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise GatewayProtocolError(f"{label} is not strict UTF-8 JSON: {exc}") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _artifact_bytes(
    payload: bytes,
    reference: GatewayFileReference,
    *,
    maximum: int,
    label: str,
) -> None:
    if reference.length > maximum:
        raise GatewayProtocolError(f"signed {label} length exceeds the local byte limit")
    if len(payload) != reference.length:
        raise GatewayProtocolError(f"{label} length does not match the signed pointer")
    if not hmac.compare_digest(_sha256(payload), reference.sha256):
        raise GatewayProtocolError(f"{label} digest does not match the signed pointer")


def dsse_pae(payload_type: str, payload: bytes) -> bytes:
    """Return DSSE v1 pre-authentication encoding for exact bytes."""

    try:
        encoded_type = payload_type.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise GatewayProtocolError("DSSE payload type is not valid UTF-8") from exc
    if b" " in str(len(encoded_type)).encode("ascii"):
        raise AssertionError("decimal length cannot contain spaces")
    return b" ".join(
        (
            b"DSSEv1",
            str(len(encoded_type)).encode("ascii"),
            encoded_type,
            str(len(payload)).encode("ascii"),
            payload,
        )
    )


def gateway_public_jwk(public_key: Ed25519PublicKey) -> GatewayPublicJwk:
    _, Ed25519PublicKeyType, _, _, serialization = _cryptography()
    if not isinstance(public_key, Ed25519PublicKeyType):
        raise GatewayProtocolError("gateway keys must be Ed25519 public keys")
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return GatewayPublicJwk(crv="Ed25519", kty="OKP", x=_base64url_encode(raw))


def gateway_key_id_from_jwk(jwk: GatewayPublicJwk) -> str:
    thumbprint = hashlib.sha256(canonical_bytes(jwk)).digest()
    return f"urn:ietf:params:oauth:jwk-thumbprint:sha-256:{_base64url_encode(thumbprint)}"


def gateway_key_id(public_key: Ed25519PublicKey) -> str:
    return gateway_key_id_from_jwk(gateway_public_jwk(public_key))


def load_ed25519_private_key(
    payload: bytes,
    *,
    password: bytes | None = None,
) -> Ed25519PrivateKey:
    Ed25519PrivateKeyType, _, _, UnsupportedAlgorithm, serialization = _cryptography()
    try:
        key = serialization.load_pem_private_key(payload, password=password)
    except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
        raise GatewayProtocolError("cannot load the Ed25519 private key") from exc
    if not isinstance(key, Ed25519PrivateKeyType):
        raise GatewayProtocolError("gateway signing requires an Ed25519 private key")
    return cast("Ed25519PrivateKey", key)


def load_ed25519_public_key(payload: bytes) -> Ed25519PublicKey:
    _, Ed25519PublicKeyType, _, UnsupportedAlgorithm, serialization = _cryptography()
    try:
        key = serialization.load_pem_public_key(payload)
    except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
        raise GatewayProtocolError("cannot load the Ed25519 public key") from exc
    if not isinstance(key, Ed25519PublicKeyType):
        raise GatewayProtocolError("gateway verification requires an Ed25519 public key")
    return cast("Ed25519PublicKey", key)


def trusted_gateway_key(public_key: Ed25519PublicKey) -> GatewayTrustedKey:
    jwk = gateway_public_jwk(public_key)
    return GatewayTrustedKey(
        keyid=gateway_key_id_from_jwk(jwk),
        algorithm=GATEWAY_SIGNATURE_ALGORITHM,
        public_jwk=jwk,
    )


def pointer_bytes(pointer: GatewaySelectionPointer) -> bytes:
    payload = canonical_bytes(pointer)
    if len(payload) > MAX_GATEWAY_POINTER_BYTES:
        raise GatewayProtocolError(
            f"gateway pointer exceeds the {MAX_GATEWAY_POINTER_BYTES}-byte limit"
        )
    return payload


def envelope_bytes(envelope: DsseEnvelope) -> bytes:
    payload = canonical_bytes(envelope.model_dump(mode="json", by_alias=True))
    if len(payload) > MAX_GATEWAY_ENVELOPE_BYTES:
        raise GatewayProtocolError(
            f"DSSE envelope exceeds the {MAX_GATEWAY_ENVELOPE_BYTES}-byte limit"
        )
    return payload


def sign_gateway_pointer(
    pointer: GatewaySelectionPointer,
    private_keys: Iterable[Ed25519PrivateKey],
) -> DsseEnvelope:
    Ed25519PrivateKeyType, _, _, _, _ = _cryptography()
    keys = tuple(private_keys)
    if not 1 <= len(keys) <= MAX_GATEWAY_SIGNATURES:
        raise GatewayProtocolError(f"gateway signing requires 1 to {MAX_GATEWAY_SIGNATURES} keys")
    payload = pointer_bytes(pointer)
    pae = dsse_pae(GATEWAY_POINTER_PAYLOAD_TYPE, payload)
    signatures: list[DsseSignature] = []
    for private_key in keys:
        if not isinstance(private_key, Ed25519PrivateKeyType):
            raise GatewayProtocolError("gateway signing requires Ed25519 private keys")
        public_key = private_key.public_key()
        signatures.append(
            DsseSignature(
                keyid=gateway_key_id(public_key),
                sig=_standard_base64_encode(private_key.sign(pae)),
            )
        )
    signatures.sort(key=lambda item: item.keyid)
    if len({item.keyid for item in signatures}) != len(signatures):
        raise GatewayProtocolError("gateway signing keys must be distinct")
    return DsseEnvelope(
        payload_type=GATEWAY_POINTER_PAYLOAD_TYPE,
        payload=_standard_base64_encode(payload),
        signatures=tuple(signatures),
    )


def parse_dsse_envelope(payload: bytes) -> DsseEnvelope:
    value = _strict_json_object(
        payload,
        maximum=MAX_GATEWAY_ENVELOPE_BYTES,
        label="DSSE envelope",
    )
    try:
        return DsseEnvelope.model_validate(value)
    except ValidationError as exc:
        raise GatewayProtocolError("DSSE envelope does not match the gateway profile") from exc


def dsse_payload_bytes(envelope: DsseEnvelope) -> bytes:
    """Decode one already-validated profile envelope exactly once."""

    return _standard_base64_decode(
        envelope.payload,
        field="payload",
        maximum_bytes=MAX_GATEWAY_POINTER_BYTES,
    )


def parse_gateway_pointer(payload: bytes) -> GatewaySelectionPointer:
    value = _strict_json_object(
        payload,
        maximum=MAX_GATEWAY_POINTER_BYTES,
        label="gateway pointer payload",
    )
    try:
        pointer = GatewaySelectionPointer.model_validate(value)
    except ValidationError as exc:
        raise GatewayProtocolError("gateway pointer does not match v1alpha1") from exc
    if canonical_bytes(pointer) != payload:
        raise GatewayProtocolError("gateway pointer payload is not RFC 8785 canonical JSON")
    return pointer


def parse_publication_artifact(
    payload: bytes,
    *,
    maximum: int = MAX_GATEWAY_ARTIFACT_BYTES,
) -> PublicationManifest:
    value = _strict_json_object(payload, maximum=maximum, label="publication artifact")
    try:
        publication = PublicationManifest.model_validate(value)
    except ValidationError as exc:
        raise GatewayProtocolError(
            "publication artifact is not a valid PublicationManifest"
        ) from exc
    if publication_hash(publication) != publication.publication_id:
        raise GatewayProtocolError("publication artifact semantic hash mismatch")
    return publication


def parse_selection_artifact(
    payload: bytes,
    *,
    maximum: int = MAX_GATEWAY_ARTIFACT_BYTES,
) -> SelectionSnapshot:
    value = _strict_json_object(payload, maximum=maximum, label="selection artifact")
    try:
        selection = SelectionSnapshot.model_validate(value)
    except ValidationError as exc:
        raise GatewayProtocolError("selection artifact is not a valid SelectionSnapshot") from exc
    if not selection_hash_matches(selection):
        raise GatewayProtocolError(
            f"selection snapshot hash mismatch: expected {selection_hash(selection)}, "
            f"received {selection.snapshot_id}"
        )
    return selection


def build_gateway_pointer(
    publication_payload: bytes,
    selection_payload: bytes,
    *,
    issuer: str,
    audience: Sequence[str],
    channel: str,
    sequence: int,
    selection_id: str,
    required_capabilities: Sequence[str] = (),
    issued_at: datetime,
    not_before: datetime | None = None,
    hard_expires_at: datetime | None = None,
) -> GatewaySelectionPointer:
    """Build a pointer from exact already-published artifact bytes."""

    publication = parse_publication_artifact(publication_payload)
    selection = parse_selection_artifact(selection_payload)
    published = next(
        (
            item
            for item in publication.selections
            if item.selection_id == selection_id and item.snapshot_id == selection.snapshot_id
        ),
        None,
    )
    if published is None:
        raise GatewayProtocolError("publication does not contain the requested selection")
    if published.snapshot.sha256 != _sha256(selection_payload):
        raise GatewayProtocolError("published selection digest does not match the supplied bytes")
    if published.snapshot.path != f"selections/{selection_id}/{selection.snapshot_id}.json":
        raise GatewayProtocolError("published selection path is not immutable and canonical")
    capabilities = tuple(sorted(required_capabilities))
    required = set(capabilities)
    for choice in selection.choices:
        missing = sorted(required - set(choice.offering.capabilities))
        if missing:
            raise GatewayProtocolError(
                f"offering {choice.offering.offering_id!r} lacks required capabilities: "
                + ", ".join(missing)
            )
    effective_expiry = hard_expires_at or selection.valid_until
    if effective_expiry > selection.valid_until:
        raise GatewayProtocolError("gateway pointer cannot outlive its selection snapshot")
    return GatewaySelectionPointer(
        schema_version=GATEWAY_POINTER_SCHEMA_VERSION,
        kind=GATEWAY_POINTER_KIND,
        issuer=issuer,
        audience=tuple(sorted(audience)),
        channel=channel,
        project_id=publication.project_id,
        sequence=sequence,
        issued_at=issued_at,
        not_before=not_before or issued_at,
        hard_expires_at=effective_expiry,
        publication=GatewayPublicationReference(
            publication_id=publication.publication_id,
            file=GatewayFileReference(
                path=f"publications/{publication.publication_id}.json",
                length=len(publication_payload),
                sha256=_sha256(publication_payload),
                media_type=GATEWAY_PUBLICATION_MEDIA_TYPE,
            ),
        ),
        selection_id=selection_id,
        selection=GatewaySelectionReference(
            schema_version="model-skyline/v1alpha1",
            kind="selection",
            snapshot_id=selection.snapshot_id,
            policy_hash=selection.policy_hash,
            frontier_id=selection.frontier_id,
            frontier_snapshot_id=selection.frontier_snapshot_id,
            workload=selection.workload,
            required_capabilities=capabilities,
            offering_identity_profile=OFFERING_IDENTITY_PROFILE,
            file=GatewayFileReference(
                path=published.snapshot.path,
                length=len(selection_payload),
                sha256=_sha256(selection_payload),
                media_type=GATEWAY_SELECTION_MEDIA_TYPE,
            ),
        ),
    )


def _trusted_public_key(value: GatewayTrustedKey) -> Ed25519PublicKey:
    _, Ed25519PublicKeyType, _, _, _ = _cryptography()
    return cast(
        "Ed25519PublicKey",
        Ed25519PublicKeyType.from_public_bytes(
            _base64url_decode(value.public_jwk.x, expected_bytes=32, field="public_jwk.x")
        ),
    )


def _verify_threshold(
    envelope: DsseEnvelope,
    payload: bytes,
    policy: GatewayTrustPolicy,
) -> tuple[str, ...]:
    _, _, InvalidSignature, _, _ = _cryptography()
    pae = dsse_pae(envelope.payload_type, payload)
    signatures = [
        _standard_base64_decode(item.sig, field="sig", expected_bytes=64)
        for item in envelope.signatures
    ]
    verified: list[str] = []
    # keyid is deliberately only a hint.  Every locally trusted key is tried,
    # and a public key can satisfy the threshold at most once.
    for trusted in policy.trusted_keys:
        public_key = _trusted_public_key(trusted)
        for signature in signatures:
            try:
                public_key.verify(signature, pae)
            except InvalidSignature:
                continue
            verified.append(trusted.keyid)
            break
    if len(verified) < policy.signature_threshold:
        raise GatewayProtocolError(
            "DSSE envelope does not satisfy the local distinct-key threshold"
        )
    return tuple(sorted(verified))


def _checkpoint_identity_matches(
    checkpoint: GatewaySequenceCheckpoint,
    policy: GatewayTrustPolicy,
    pointer: GatewaySelectionPointer,
) -> None:
    if (
        checkpoint.trust_namespace != policy.trust_namespace
        or checkpoint.issuer != pointer.issuer
        or checkpoint.audience != policy.audience
        or checkpoint.channel != pointer.channel
    ):
        raise GatewaySequenceError("gateway sequence checkpoint has the wrong trust identity")


def verify_gateway_envelope(
    envelope_payload: bytes,
    policy: GatewayTrustPolicy,
    *,
    now: datetime,
    checkpoint: GatewaySequenceCheckpoint | None = None,
) -> AuthenticatedGatewayPointer:
    instant = _utc_timestamp(now)
    envelope = parse_dsse_envelope(envelope_payload)
    payload = dsse_payload_bytes(envelope)
    verified_key_ids = _verify_threshold(envelope, payload, policy)
    # Parse only the exact bytes whose PAE was verified.
    pointer = parse_gateway_pointer(payload)
    if pointer.issuer != policy.issuer:
        raise GatewayProtocolError("gateway pointer issuer does not match local trust policy")
    if policy.audience not in pointer.audience:
        raise GatewayProtocolError("gateway pointer does not authorize the configured audience")
    if pointer.channel != policy.channel:
        raise GatewayProtocolError("gateway pointer channel does not match local trust policy")
    if pointer.project_id != policy.project_id:
        raise GatewayProtocolError("gateway pointer project does not match local trust policy")
    if pointer.selection_id != policy.expected_selection_id:
        raise GatewayProtocolError("gateway pointer selection id does not match local trust policy")
    if pointer.selection.frontier_id != policy.expected_frontier_id:
        raise GatewayProtocolError("gateway pointer frontier does not match local trust policy")
    if pointer.selection.workload != policy.expected_workload:
        raise GatewayProtocolError("gateway pointer workload does not match local trust policy")
    if (
        policy.expected_policy_hash is not None
        and pointer.selection.policy_hash != policy.expected_policy_hash
    ):
        raise GatewayProtocolError("gateway pointer policy hash does not match local trust policy")
    if pointer.selection.required_capabilities != policy.required_capabilities:
        raise GatewayProtocolError(
            "gateway pointer capabilities do not exactly match local trust policy"
        )
    if pointer.sequence < policy.minimum_sequence:
        raise GatewaySequenceError("gateway pointer sequence is below the local minimum")
    skew = timedelta(seconds=policy.max_future_skew_seconds)
    if pointer.issued_at > instant + skew:
        raise GatewayProtocolError("gateway pointer was issued implausibly in the future")
    if pointer.not_before > instant + skew:
        raise GatewayNotYetValidError("gateway pointer is not active yet")
    if instant >= pointer.hard_expires_at:
        raise GatewayExpiredError("gateway pointer reached its hard expiry")
    if instant - pointer.issued_at > timedelta(seconds=policy.max_pointer_age_seconds):
        raise GatewayProtocolError("gateway pointer exceeds the local maximum issued age")
    if pointer.hard_expires_at - pointer.not_before > timedelta(
        seconds=policy.max_pointer_lifetime_seconds
    ):
        raise GatewayProtocolError("gateway pointer exceeds the local maximum lifetime")

    payload_sha256 = _sha256(payload)
    if checkpoint is not None:
        _checkpoint_identity_matches(checkpoint, policy, pointer)
        if pointer.sequence < checkpoint.sequence:
            raise GatewaySequenceError("gateway pointer sequence would roll back")
        if pointer.sequence == checkpoint.sequence and payload_sha256 != checkpoint.payload_sha256:
            raise GatewaySequenceError("gateway pointer sequence would equivocate")
    return AuthenticatedGatewayPointer(
        pointer=pointer,
        payload_sha256=payload_sha256,
        verified_key_ids=verified_key_ids,
    )


def _published_selection_matches(
    pointer: GatewaySelectionPointer,
    publication: PublicationManifest,
) -> None:
    if publication.publication_id != pointer.publication.publication_id:
        raise GatewayProtocolError("publication identity does not match the signed pointer")
    if publication.project_id != pointer.project_id:
        raise GatewayProtocolError("publication project does not match the signed pointer")
    matches = [item for item in publication.selections if item.selection_id == pointer.selection_id]
    if len(matches) != 1:
        raise GatewayProtocolError("publication must contain exactly one bound selection")
    published = matches[0]
    reference = pointer.selection
    if (
        published.snapshot_id != reference.snapshot_id
        or published.frontier_id != reference.frontier_id
        or published.frontier_snapshot_id != reference.frontier_snapshot_id
        or published.snapshot.path != reference.file.path
        or published.snapshot.sha256 != reference.file.sha256
        or published.snapshot.media_type != reference.file.media_type
    ):
        raise GatewayProtocolError("publication selection entry does not match the signed pointer")


def _selection_matches_pointer(
    pointer: GatewaySelectionPointer,
    selection: SelectionSnapshot,
    *,
    now: datetime,
) -> None:
    reference = pointer.selection
    if selection.schema_version != reference.schema_version or selection.kind != reference.kind:
        raise GatewayProtocolError("selection artifact profile does not match the signed pointer")
    if selection.selection_id != pointer.selection_id:
        raise GatewayProtocolError("selection artifact id does not match the signed pointer")
    if selection.snapshot_id != reference.snapshot_id:
        raise GatewayProtocolError("selection artifact identity does not match the signed pointer")
    if selection.policy_hash != reference.policy_hash:
        raise GatewayProtocolError("selection artifact policy does not match the signed pointer")
    if selection.frontier_id != reference.frontier_id:
        raise GatewayProtocolError("selection artifact frontier does not match the signed pointer")
    if selection.frontier_snapshot_id != reference.frontier_snapshot_id:
        raise GatewayProtocolError(
            "selection artifact frontier snapshot does not match the signed pointer"
        )
    if selection.workload != reference.workload:
        raise GatewayProtocolError("selection artifact workload does not match the signed pointer")
    if pointer.hard_expires_at > selection.valid_until:
        raise GatewayProtocolError("gateway pointer outlives the selection artifact")
    if now >= selection.valid_until:
        raise GatewayExpiredError("selection artifact reached its hard expiry")


def _bind_targets(
    pointer: GatewaySelectionPointer,
    selection: SelectionSnapshot,
    policy: GatewayTrustPolicy,
) -> tuple[BoundGatewayTarget, ...]:
    if len(selection.choices) > policy.max_candidates:
        raise GatewayProtocolError("selection exceeds the local runtime candidate limit")
    bindings = {canonical_bytes(item.offering): item for item in policy.target_bindings}
    required = set(pointer.selection.required_capabilities)
    targets: list[BoundGatewayTarget] = []
    for choice in selection.choices:
        binding = bindings.get(canonical_bytes(choice.offering))
        if binding is None:
            raise GatewayProtocolError(
                f"offering {choice.offering.offering_id!r} has no exact local target binding"
            )
        missing_offering = sorted(required - set(choice.offering.capabilities))
        if missing_offering:
            raise GatewayProtocolError(
                f"offering {choice.offering.offering_id!r} lacks signed capabilities: "
                + ", ".join(missing_offering)
            )
        missing_target = sorted(required - set(binding.capabilities))
        if missing_target:
            raise GatewayProtocolError(
                f"target {binding.target_id!r} lacks signed capabilities: "
                + ", ".join(missing_target)
            )
        targets.append(
            BoundGatewayTarget(
                offering=choice.offering,
                target_id=binding.target_id,
                target_revision=binding.target_revision,
                capabilities=binding.capabilities,
            )
        )
    return tuple(targets)


def target_bindings_hash(targets: Sequence[BoundGatewayTarget]) -> str:
    return content_hash([item.model_dump(mode="json") for item in targets])


def verify_gateway_bundle(
    envelope_payload: bytes,
    publication_payload: bytes,
    selection_payload: bytes,
    policy: GatewayTrustPolicy,
    *,
    now: datetime,
    checkpoint: GatewaySequenceCheckpoint | None = None,
) -> VerifiedGatewaySelection:
    """Verify signatures, raw artifacts, semantics, and exact local bindings."""

    instant = _utc_timestamp(now)
    authenticated = verify_gateway_envelope(
        envelope_payload,
        policy,
        now=instant,
        checkpoint=checkpoint,
    )
    pointer = authenticated.pointer
    _artifact_bytes(
        publication_payload,
        pointer.publication.file,
        maximum=policy.max_artifact_bytes,
        label="publication artifact",
    )
    _artifact_bytes(
        selection_payload,
        pointer.selection.file,
        maximum=policy.max_artifact_bytes,
        label="selection artifact",
    )
    publication = parse_publication_artifact(
        publication_payload,
        maximum=policy.max_artifact_bytes,
    )
    selection = parse_selection_artifact(
        selection_payload,
        maximum=policy.max_artifact_bytes,
    )
    _published_selection_matches(pointer, publication)
    _selection_matches_pointer(pointer, selection, now=instant)
    targets = _bind_targets(pointer, selection, policy)
    verified = VerifiedGatewaySelection(
        trust_namespace=policy.trust_namespace,
        configured_audience=policy.audience,
        authenticated_pointer=authenticated,
        publication=publication,
        selection=selection,
        targets=targets,
    )
    if checkpoint is not None:
        _check_sequence(verified.checkpoint, checkpoint)
    return verified


def build_stored_gateway_bundle(
    verified: VerifiedGatewaySelection,
    *,
    envelope_payload: bytes,
    publication_payload: bytes,
    selection_payload: bytes,
    installed_at: datetime,
) -> StoredGatewayBundle:
    """Bind exact persisted bytes to a verified installation object."""

    instant = _utc_timestamp(installed_at)
    envelope = parse_dsse_envelope(envelope_payload)
    signed_payload = dsse_payload_bytes(envelope)
    if not hmac.compare_digest(
        _sha256(signed_payload),
        verified.authenticated_pointer.payload_sha256,
    ):
        raise GatewayProtocolError("stored envelope does not contain the verified pointer")
    _artifact_bytes(
        publication_payload,
        verified.pointer.publication.file,
        maximum=MAX_GATEWAY_ARTIFACT_BYTES,
        label="publication artifact",
    )
    _artifact_bytes(
        selection_payload,
        verified.pointer.selection.file,
        maximum=MAX_GATEWAY_ARTIFACT_BYTES,
        label="selection artifact",
    )
    return StoredGatewayBundle(
        checkpoint=verified.checkpoint,
        envelope_payload=bytes(envelope_payload),
        publication_payload=bytes(publication_payload),
        selection_payload=bytes(selection_payload),
        installed_at=instant,
    )


def pin_gateway_route(
    verified: VerifiedGatewaySelection,
    *,
    now: datetime,
    required_capabilities: Sequence[str] = (),
    minimum_headroom: timedelta = timedelta(0),
    admission_source: Literal["fresh", "last-known-good"] = "fresh",
) -> PinnedGatewayRoute:
    """Create an opaque no-widening route to retain for a complete work unit."""

    instant = _utc_timestamp(now)
    if minimum_headroom < timedelta(0):
        raise ValueError("minimum_headroom cannot be negative")
    required = _canonical_string_tuple(
        tuple(sorted(required_capabilities)),
        field="request required_capabilities",
        maximum=MAX_CAPABILITIES,
    )
    pointer = verified.pointer
    if instant + minimum_headroom >= pointer.hard_expires_at:
        raise GatewayExpiredError("selection lacks the required trajectory expiry headroom")
    requested = set(required)
    narrowed = tuple(
        target
        for target in verified.targets
        if requested <= set(target.offering.capabilities) and requested <= set(target.capabilities)
    )
    if not narrowed:
        raise GatewayProtocolError("no signed target satisfies the request capabilities")
    return PinnedGatewayRoute(
        trust_namespace=verified.trust_namespace,
        issuer=pointer.issuer,
        audience=verified.configured_audience,
        channel=pointer.channel,
        sequence=pointer.sequence,
        payload_sha256=verified.authenticated_pointer.payload_sha256,
        selection_id=pointer.selection_id,
        selection_snapshot_id=pointer.selection.snapshot_id,
        policy_hash=pointer.selection.policy_hash,
        frontier_id=pointer.selection.frontier_id,
        frontier_snapshot_id=pointer.selection.frontier_snapshot_id,
        workload=pointer.selection.workload,
        hard_expires_at=pointer.hard_expires_at,
        admission_source=admission_source,
        required_capabilities=required,
        targets=narrowed,
    )


def gateway_safe_integer_limit() -> int:
    """Expose the interoperable sequence bound for non-Python consumers."""

    return MAX_SAFE_INTEGER
