"""Machine-readable compatibility profile for native gateway consumers.

The compatibility manifest is an index over the exact public schemas and
conformance fixture bytes needed to implement gateway-pointer ``v1alpha1`` in
another language.  It is deliberately not a trust root or a signed routing
artifact: consumers must acquire a release through a trusted channel and then
apply the authentication and durable installation protocol in ADR 0003.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, Field, ValidationError, field_validator, model_validator

from model_skyline.canonical import canonical_bytes
from model_skyline.gateway import (
    DEFAULT_MAX_GATEWAY_ARTIFACT_BYTES,
    GATEWAY_ENVELOPE_MEDIA_TYPE,
    GATEWAY_POINTER_PAYLOAD_TYPE,
    GATEWAY_PUBLICATION_MEDIA_TYPE,
    GATEWAY_SELECTION_MEDIA_TYPE,
    MAX_GATEWAY_ARTIFACT_BYTES,
    MAX_GATEWAY_AUDIENCES,
    MAX_GATEWAY_ENVELOPE_BYTES,
    MAX_GATEWAY_JSON_DEPTH,
    MAX_GATEWAY_JSON_VALUES,
    MAX_GATEWAY_NOT_BEFORE_LEAD_SECONDS,
    MAX_GATEWAY_POINTER_BYTES,
    MAX_GATEWAY_POINTER_LIFETIME_SECONDS,
    MAX_GATEWAY_RUNTIME_CANDIDATES,
    MAX_GATEWAY_SIGNATURES,
    MAX_GATEWAY_TRUSTED_KEYS,
    OFFERING_IDENTITY_PROFILE,
)
from model_skyline.models import FrozenModel, PositiveSafeCount, Sha256Digest

GATEWAY_COMPATIBILITY_SCHEMA_VERSION: Literal[
    "model-skyline/gateway-consumer-compatibility/v1alpha1"
] = "model-skyline/gateway-consumer-compatibility/v1alpha1"
GATEWAY_COMPATIBILITY_KIND: Literal["gateway-consumer-compatibility"] = (
    "gateway-consumer-compatibility"
)
GATEWAY_COMPATIBILITY_PROFILE: Literal["gateway-pointer/v1alpha1"] = "gateway-pointer/v1alpha1"
GATEWAY_COMPATIBILITY_MEDIA_TYPE = (
    "application/vnd.model-skyline.gateway-consumer-compatibility.v1alpha1+json"
)

MAX_GATEWAY_COMPATIBILITY_BYTES = 256 * 1024
MAX_GATEWAY_COMPATIBILITY_RESOURCES = 64

_RESOURCE_PATH_PATTERN = (
    r"^(?:schemas/[a-z0-9][a-z0-9._-]{0,254}\.schema\.json|"
    r"conformance/gateway-pointer/v1alpha1/"
    r"(?:artifacts|intermediate|invalid|keys|valid)/"
    r"[a-z0-9][a-z0-9._-]{0,254}\.(?:bin|json))$"
)


class GatewayCompatibilityError(ValueError):
    """A compatibility manifest or one of its indexed resources is invalid."""


def _safe_resource_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
        or "\\" in value
        or "//" in value
    ):
        raise ValueError("compatibility resource path must be a canonical relative path")
    return value


GatewayCompatibilityResourcePath = Annotated[
    str,
    Field(min_length=1, max_length=512, pattern=_RESOURCE_PATH_PATTERN),
    AfterValidator(_safe_resource_path),
]
GatewayCompatibilityResourceRole = Literal[
    "accepted-vector",
    "bound-artifact",
    "fixture-expectation",
    "fixture-policy",
    "intermediate",
    "protocol-schema",
    "public-test-key",
    "rejected-vector",
]
GatewayCompatibilityMediaType = Literal[
    "application/json",
    "application/octet-stream",
    "application/schema+json",
    "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+json",
    "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+dsse",
]


class GatewayCompatibilityResource(FrozenModel):
    resource_id: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9.-]*$"),
    ]
    role: GatewayCompatibilityResourceRole
    path: GatewayCompatibilityResourcePath
    media_type: GatewayCompatibilityMediaType
    length: PositiveSafeCount
    sha256: Sha256Digest


class GatewayCompatibilityMediaTypes(FrozenModel):
    compatibility_manifest: Literal[
        "application/vnd.model-skyline.gateway-consumer-compatibility.v1alpha1+json"
    ]
    pointer_payload: Literal[
        "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+json"
    ]
    envelope: Literal["application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+dsse"]
    publication: Literal["application/json"]
    selection: Literal["application/json"]


class GatewayCompatibilityCryptography(FrozenModel):
    dsse_version: Literal["1.0.2"]
    signature_algorithm: Literal["Ed25519"]
    canonical_json: Literal["RFC8785"]
    base64: Literal["RFC4648-standard-padded"]
    content_digest: Literal["sha256"]
    key_id: Literal["RFC7638-sha256-jwk-thumbprint-rfc9278-uri"]


class GatewayCompatibilityFeatures(FrozenModel):
    supported_selection_kinds: tuple[Literal["selection"], ...] = Field(
        min_length=1,
        max_length=1,
        json_schema_extra={"uniqueItems": True},
    )
    durable_sequence_checkpoint: Literal[True]
    same_sequence_equivocation_rejection: Literal[True]
    semantic_artifact_hash_verification: Literal[True]
    exact_complete_offering_binding: Literal[True]
    immutable_target_revision_binding: Literal[True]
    work_unit_route_pinning: Literal[True]
    supports_quality_gated_selection: Literal[False]
    permits_remote_routing_material: Literal[False]


class GatewayCompatibilityLimits(FrozenModel):
    envelope_bytes: Literal[65536]
    pointer_bytes: Literal[32768]
    default_artifact_bytes: Literal[2097152]
    maximum_artifact_bytes: Literal[10485760]
    signatures: Literal[16]
    trusted_keys: Literal[32]
    audiences: Literal[16]
    pointer_lifetime_seconds: Literal[2678400]
    not_before_lead_seconds: Literal[86400]
    json_depth: Literal[32]
    json_values: Literal[100000]
    runtime_candidates: Literal[1024]


class GatewayConsumerCompatibility(FrozenModel):
    schema_version: Literal["model-skyline/gateway-consumer-compatibility/v1alpha1"]
    kind: Literal["gateway-consumer-compatibility"]
    profile: Literal["gateway-pointer/v1alpha1"]
    status: Literal["alpha"]
    offering_identity_profile: Literal["model-skyline/offering-key/v1alpha1"]
    media_types: GatewayCompatibilityMediaTypes
    cryptography: GatewayCompatibilityCryptography
    features: GatewayCompatibilityFeatures
    limits: GatewayCompatibilityLimits
    resources: tuple[GatewayCompatibilityResource, ...] = Field(
        min_length=1,
        max_length=MAX_GATEWAY_COMPATIBILITY_RESOURCES,
    )
    resource_set_sha256: Sha256Digest

    @field_validator("resources", mode="before")
    @classmethod
    def resources_are_a_canonical_sequence(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            raise ValueError("resources must be an array")
        return value

    @model_validator(mode="after")
    def resource_index_is_coherent(self) -> Self:
        resource_ids = [resource.resource_id for resource in self.resources]
        if resource_ids != sorted(resource_ids):
            raise ValueError("compatibility resources must be sorted by resource_id")
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("compatibility resource IDs must be unique")
        paths = [resource.path for resource in self.resources]
        if len(paths) != len(set(paths)):
            raise ValueError("compatibility resource paths must be unique")
        expected_profile = [
            (spec.resource_id, spec.role, spec.path, spec.media_type)
            for spec in GATEWAY_COMPATIBILITY_RESOURCE_SPECS
        ]
        actual_profile = [
            (resource.resource_id, resource.role, resource.path, resource.media_type)
            for resource in self.resources
        ]
        if actual_profile != expected_profile:
            raise ValueError("compatibility resources do not match the v1alpha1 profile")
        expected_digest = gateway_compatibility_resource_set_hash(self.resources)
        if self.resource_set_sha256 != expected_digest:
            raise ValueError("resource_set_sha256 does not match the canonical resource index")
        return self


@dataclass(frozen=True)
class _ResourceSpec:
    resource_id: str
    role: GatewayCompatibilityResourceRole
    path: str
    media_type: GatewayCompatibilityMediaType


_RESOURCE_SPECS = (
    _ResourceSpec(
        "artifact.publication",
        "bound-artifact",
        "conformance/gateway-pointer/v1alpha1/artifacts/publication.json",
        "application/json",
    ),
    _ResourceSpec(
        "artifact.selection",
        "bound-artifact",
        "conformance/gateway-pointer/v1alpha1/artifacts/selection.json",
        "application/json",
    ),
    _ResourceSpec(
        "intermediate.dsse-pae",
        "intermediate",
        "conformance/gateway-pointer/v1alpha1/intermediate/dsse-pae.bin",
        "application/octet-stream",
    ),
    _ResourceSpec(
        "intermediate.jwk-thumbprint-input",
        "intermediate",
        "conformance/gateway-pointer/v1alpha1/intermediate/jwk-thumbprint-input.json",
        "application/json",
    ),
    _ResourceSpec(
        "intermediate.pointer",
        "intermediate",
        "conformance/gateway-pointer/v1alpha1/intermediate/pointer.json",
        "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+json",
    ),
    _ResourceSpec(
        "intermediate.publication-hash-input",
        "intermediate",
        "conformance/gateway-pointer/v1alpha1/intermediate/publication-hash-input.json",
        "application/json",
    ),
    _ResourceSpec(
        "intermediate.selection-hash-input",
        "intermediate",
        "conformance/gateway-pointer/v1alpha1/intermediate/selection-hash-input.json",
        "application/json",
    ),
    *(
        _ResourceSpec(
            f"invalid.{name.removesuffix('.dsse.json')}",
            "rejected-vector",
            f"conformance/gateway-pointer/v1alpha1/invalid/{name}",
            "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+dsse",
        )
        for name in (
            "duplicate-json-member.dsse.json",
            "duplicate-key-threshold.dsse.json",
            "expired.dsse.json",
            "payload-bit-flip.dsse.json",
            "raw-digest-vs-snapshot-id-swap.dsse.json",
            "rollback.dsse.json",
            "same-sequence-different-payload.dsse.json",
            "unsorted-audience.dsse.json",
            "unsorted-required-capabilities.dsse.json",
            "wrong-payload-type.dsse.json",
        )
    ),
    _ResourceSpec(
        "key.one-public-jwk",
        "public-test-key",
        "conformance/gateway-pointer/v1alpha1/keys/key-1.public.jwk.json",
        "application/json",
    ),
    _ResourceSpec(
        "key.two-public-jwk",
        "public-test-key",
        "conformance/gateway-pointer/v1alpha1/keys/key-2.public.jwk.json",
        "application/json",
    ),
    _ResourceSpec(
        "schema.compatibility",
        "protocol-schema",
        "schemas/gateway-consumer-compatibility.schema.json",
        "application/schema+json",
    ),
    _ResourceSpec(
        "schema.envelope",
        "protocol-schema",
        "schemas/gateway-selection-envelope.schema.json",
        "application/schema+json",
    ),
    _ResourceSpec(
        "schema.pointer",
        "protocol-schema",
        "schemas/gateway-selection-pointer.schema.json",
        "application/schema+json",
    ),
    _ResourceSpec(
        "schema.publication",
        "protocol-schema",
        "schemas/publication-manifest.schema.json",
        "application/schema+json",
    ),
    _ResourceSpec(
        "schema.selection",
        "protocol-schema",
        "schemas/selection-snapshot.schema.json",
        "application/schema+json",
    ),
    _ResourceSpec(
        "schema.trust-policy",
        "protocol-schema",
        "schemas/gateway-trust-policy.schema.json",
        "application/schema+json",
    ),
    _ResourceSpec(
        "valid.envelope",
        "accepted-vector",
        "conformance/gateway-pointer/v1alpha1/valid/envelope.dsse.json",
        "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+dsse",
    ),
    _ResourceSpec(
        "valid.expected",
        "fixture-expectation",
        "conformance/gateway-pointer/v1alpha1/valid/expected.json",
        "application/json",
    ),
    _ResourceSpec(
        "valid.payload",
        "accepted-vector",
        "conformance/gateway-pointer/v1alpha1/valid/payload.json",
        "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+json",
    ),
    _ResourceSpec(
        "valid.pinned-route",
        "fixture-expectation",
        "conformance/gateway-pointer/v1alpha1/valid/pinned-route.json",
        "application/json",
    ),
    _ResourceSpec(
        "valid.rotation-same-payload",
        "accepted-vector",
        "conformance/gateway-pointer/v1alpha1/valid/rotation-same-payload.dsse.json",
        "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+dsse",
    ),
    _ResourceSpec(
        "valid.threshold-2-of-2",
        "accepted-vector",
        "conformance/gateway-pointer/v1alpha1/valid/threshold-2-of-2.dsse.json",
        "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+dsse",
    ),
    _ResourceSpec(
        "valid.trust-policy",
        "fixture-policy",
        "conformance/gateway-pointer/v1alpha1/valid/trust-policy.json",
        "application/json",
    ),
    _ResourceSpec(
        "valid.trust-policy-threshold-2",
        "fixture-policy",
        "conformance/gateway-pointer/v1alpha1/valid/trust-policy-threshold-2.json",
        "application/json",
    ),
)

GATEWAY_COMPATIBILITY_RESOURCE_SPECS = tuple(
    sorted(_RESOURCE_SPECS, key=lambda spec: spec.resource_id)
)


def gateway_compatibility_resource_set_hash(
    resources: tuple[GatewayCompatibilityResource, ...],
) -> str:
    """Hash the canonical resource index; this is a version label, not authentication."""

    payload = [resource.model_dump(mode="json") for resource in resources]
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _read_bounded_regular_file(path: Path, maximum: int) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise GatewayCompatibilityError("cannot open compatibility resource") from None
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise GatewayCompatibilityError("compatibility resource is not a regular file")
            if before.st_size > maximum:
                raise GatewayCompatibilityError(
                    f"compatibility resource exceeds the {maximum}-byte limit"
                )
            raw = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
    except GatewayCompatibilityError:
        raise
    except OSError:
        raise GatewayCompatibilityError("cannot read compatibility resource") from None
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or len(raw) != before.st_size:
        raise GatewayCompatibilityError("compatibility resource changed while reading")
    if len(raw) > maximum:
        raise GatewayCompatibilityError(f"compatibility resource exceeds the {maximum}-byte limit")
    return raw


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} is forbidden")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _preflight_json_structure(payload: bytes) -> None:
    """Bound JSON nesting and structural work before allocating decoded containers."""

    depth = 0
    structural_tokens = 0
    in_string = False
    escaped = False
    quote = 0x22
    backslash = 0x5C
    openers = b"{["
    closers = b"}]"
    structural = b"{}[],:"

    for byte in payload:
        if in_string:
            if escaped:
                escaped = False
            elif byte == backslash:
                escaped = True
            elif byte == quote:
                in_string = False
            continue
        if byte == quote:
            in_string = True
            continue
        if byte in openers:
            depth += 1
            if depth > MAX_GATEWAY_JSON_DEPTH:
                raise GatewayCompatibilityError(
                    "gateway compatibility manifest exceeds the "
                    f"{MAX_GATEWAY_JSON_DEPTH}-level JSON depth limit"
                )
        elif byte in closers and depth:
            depth -= 1
        if byte in structural:
            structural_tokens += 1
            if structural_tokens > MAX_GATEWAY_JSON_VALUES:
                raise GatewayCompatibilityError(
                    "gateway compatibility manifest exceeds the "
                    f"{MAX_GATEWAY_JSON_VALUES}-token JSON structure limit"
                )


def _check_json_complexity(value: Any) -> None:
    count = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        count += 1
        if count > MAX_GATEWAY_JSON_VALUES:
            raise GatewayCompatibilityError(
                "gateway compatibility manifest exceeds the "
                f"{MAX_GATEWAY_JSON_VALUES}-value JSON limit"
            )
        if depth > MAX_GATEWAY_JSON_DEPTH:
            raise GatewayCompatibilityError(
                "gateway compatibility manifest exceeds the "
                f"{MAX_GATEWAY_JSON_DEPTH}-level JSON depth limit"
            )
        if isinstance(current, dict):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)


def parse_gateway_consumer_compatibility(payload: bytes) -> GatewayConsumerCompatibility:
    """Strictly parse one bounded compatibility manifest."""

    if not payload:
        raise GatewayCompatibilityError("gateway compatibility manifest is empty")
    if len(payload) > MAX_GATEWAY_COMPATIBILITY_BYTES:
        raise GatewayCompatibilityError(
            "gateway compatibility manifest exceeds the "
            f"{MAX_GATEWAY_COMPATIBILITY_BYTES}-byte limit"
        )
    _preflight_json_structure(payload)
    try:
        text = payload.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        raise GatewayCompatibilityError(
            "gateway compatibility manifest is not strict JSON"
        ) from None
    if not isinstance(value, dict):
        raise GatewayCompatibilityError("gateway compatibility manifest must be a JSON object")
    _check_json_complexity(value)
    try:
        return GatewayConsumerCompatibility.model_validate(value)
    except ValidationError:
        raise GatewayCompatibilityError(
            "gateway compatibility manifest does not match the v1alpha1 profile"
        ) from None


def load_gateway_consumer_compatibility(path: str | Path) -> GatewayConsumerCompatibility:
    """Load a compatibility manifest from a bounded regular file."""

    source = Path(path)
    return parse_gateway_consumer_compatibility(
        _read_bounded_regular_file(source, MAX_GATEWAY_COMPATIBILITY_BYTES)
    )


def _resource_bytes_for_build(
    spec: _ResourceSpec,
    *,
    schema_root: Path,
    conformance_root: Path,
) -> bytes:
    schema_prefix = "schemas/"
    conformance_prefix = "conformance/gateway-pointer/v1alpha1/"
    if spec.path.startswith(schema_prefix):
        path = schema_root / spec.path.removeprefix(schema_prefix)
    elif spec.path.startswith(conformance_prefix):
        path = conformance_root / spec.path.removeprefix(conformance_prefix)
    else:  # pragma: no cover - impossible unless the fixed profile is edited incorrectly
        raise GatewayCompatibilityError(f"unsupported compatibility resource path {spec.path!r}")
    return _read_bounded_regular_file(path, MAX_GATEWAY_ARTIFACT_BYTES)


def build_gateway_consumer_compatibility(
    *,
    schema_root: str | Path,
    conformance_root: str | Path,
) -> GatewayConsumerCompatibility:
    """Build the deterministic compatibility index from exact local fixture bytes."""

    schemas = Path(schema_root)
    conformance = Path(conformance_root)
    resources = tuple(
        GatewayCompatibilityResource(
            resource_id=spec.resource_id,
            role=spec.role,
            path=spec.path,
            media_type=spec.media_type,
            length=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        for spec in GATEWAY_COMPATIBILITY_RESOURCE_SPECS
        for raw in (
            _resource_bytes_for_build(
                spec,
                schema_root=schemas,
                conformance_root=conformance,
            ),
        )
    )
    return GatewayConsumerCompatibility(
        schema_version=GATEWAY_COMPATIBILITY_SCHEMA_VERSION,
        kind=GATEWAY_COMPATIBILITY_KIND,
        profile=GATEWAY_COMPATIBILITY_PROFILE,
        status="alpha",
        offering_identity_profile=OFFERING_IDENTITY_PROFILE,
        media_types=GatewayCompatibilityMediaTypes(
            compatibility_manifest=GATEWAY_COMPATIBILITY_MEDIA_TYPE,
            pointer_payload=GATEWAY_POINTER_PAYLOAD_TYPE,
            envelope=GATEWAY_ENVELOPE_MEDIA_TYPE,
            publication=GATEWAY_PUBLICATION_MEDIA_TYPE,
            selection=GATEWAY_SELECTION_MEDIA_TYPE,
        ),
        cryptography=GatewayCompatibilityCryptography(
            dsse_version="1.0.2",
            signature_algorithm="Ed25519",
            canonical_json="RFC8785",
            base64="RFC4648-standard-padded",
            content_digest="sha256",
            key_id="RFC7638-sha256-jwk-thumbprint-rfc9278-uri",
        ),
        features=GatewayCompatibilityFeatures(
            supported_selection_kinds=("selection",),
            durable_sequence_checkpoint=True,
            same_sequence_equivocation_rejection=True,
            semantic_artifact_hash_verification=True,
            exact_complete_offering_binding=True,
            immutable_target_revision_binding=True,
            work_unit_route_pinning=True,
            supports_quality_gated_selection=False,
            permits_remote_routing_material=False,
        ),
        limits=GatewayCompatibilityLimits(
            envelope_bytes=MAX_GATEWAY_ENVELOPE_BYTES,
            pointer_bytes=MAX_GATEWAY_POINTER_BYTES,
            default_artifact_bytes=DEFAULT_MAX_GATEWAY_ARTIFACT_BYTES,
            maximum_artifact_bytes=MAX_GATEWAY_ARTIFACT_BYTES,
            signatures=MAX_GATEWAY_SIGNATURES,
            trusted_keys=MAX_GATEWAY_TRUSTED_KEYS,
            audiences=MAX_GATEWAY_AUDIENCES,
            pointer_lifetime_seconds=MAX_GATEWAY_POINTER_LIFETIME_SECONDS,
            not_before_lead_seconds=MAX_GATEWAY_NOT_BEFORE_LEAD_SECONDS,
            json_depth=MAX_GATEWAY_JSON_DEPTH,
            json_values=MAX_GATEWAY_JSON_VALUES,
            runtime_candidates=MAX_GATEWAY_RUNTIME_CANDIDATES,
        ),
        resources=resources,
        resource_set_sha256=gateway_compatibility_resource_set_hash(resources),
    )


def verify_gateway_consumer_compatibility(
    manifest: GatewayConsumerCompatibility,
    *,
    content_root: str | Path,
) -> None:
    """Verify every indexed resource below a trusted package or repository root."""

    root = Path(content_root)
    for resource in manifest.resources:
        raw = _read_bounded_regular_file(root / resource.path, MAX_GATEWAY_ARTIFACT_BYTES)
        if len(raw) != resource.length:
            raise GatewayCompatibilityError(
                f"compatibility resource length mismatch for {resource.resource_id}"
            )
        if not hashlib.sha256(raw).hexdigest() == resource.sha256:
            raise GatewayCompatibilityError(
                f"compatibility resource digest mismatch for {resource.resource_id}"
            )
