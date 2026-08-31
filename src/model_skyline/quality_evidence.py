"""Normalized quality evidence and exact offering reconciliation.

This module is intentionally transport-free.  Trusted adapters turn captured
leaderboard or evaluator output into :class:`QualityEvidenceSet` values; this
module only validates, content-addresses, and reconciles those values against an
operator-reviewed mapping.  In particular, it never treats a model label as a
routing instruction.

``QualityImportReport`` is a local audit/import structure, not a publication
projection.  Its rights scope answers whether a downstream full or derived
projection is permitted; the report itself intentionally retains labels,
claims, metadata, and review evidence that might not be redistributable.

Mapping expiry is not inferred from ``reviewed_at``.  Hosts must enforce any
mutable route-attestation validity clock separately until a typed validity
policy and expiry outcome become part of this contract.

Mutable aliases remain quarantined in this first contract.  A future external
identity-pin type may make one eligible, but an untyped digest or review note is
not accepted as a substitute.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from model_skyline.canonical import canonical_bytes, content_hash
from model_skyline.models import (
    CanonicalDecimal,
    CanonicalJsonObject,
    FrozenModel,
    OfferingKey,
    SafeCount,
    Sha256Digest,
)

MAX_EVIDENCE_ROWS = 10_000
MAX_RECONCILIATION_ENTRIES = 10_000
MAX_MEASUREMENTS = 128
MAX_COUNTS = 128
MAX_MODEL_CLAIMS = 128
MAX_JSON_BYTES = 1_000_000
MAX_JSON_NODES = 100_000
MAX_JSON_DEPTH = 32
MAX_RAW_SOURCE_BYTES = 64_000_000
MAX_TEXT = 2_048
MAX_LOCATOR = 4_096

EVIDENCE_SCHEMA_VERSION = "model-skyline/quality-evidence/v1alpha1"
RECONCILIATION_SCHEMA_VERSION = "model-skyline/quality-reconciliation/v1alpha1"
IMPORT_REPORT_SCHEMA_VERSION = "model-skyline/quality-import-report/v1alpha1"
QUALITY_DIGEST_VERSION = "model-skyline/quality-content/v1"

Identifier = Annotated[str, Field(min_length=1, max_length=512)]
ShortText = Annotated[str, Field(min_length=1, max_length=MAX_TEXT)]
Locator = Annotated[str, Field(min_length=1, max_length=MAX_LOCATOR)]


def _bounded_canonical_object(value: dict[str, Any]) -> dict[str, Any]:
    """Reject deceptively small-looking, deeply nested extension bags."""

    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ValueError(f"canonical JSON object exceeds {MAX_JSON_NODES} nodes")
        if depth > MAX_JSON_DEPTH:
            raise ValueError(f"canonical JSON object exceeds depth {MAX_JSON_DEPTH}")
        if isinstance(current, dict):
            stack.extend((key, depth + 1) for key in current)
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
    encoded = canonical_bytes(value)
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError(f"canonical JSON object exceeds {MAX_JSON_BYTES} bytes")
    return value


BoundedCanonicalObject = Annotated[
    CanonicalJsonObject,
    AfterValidator(_bounded_canonical_object),
]


class _QualityDomainModel(FrozenModel):
    """Ensure every independently hashed evidence domain is bounded as a whole."""

    @model_validator(mode="after")
    def canonical_domain_is_bounded(self) -> Self:
        if len(canonical_bytes(self.model_dump(mode="json"))) > MAX_JSON_BYTES:
            raise ValueError(f"quality identity domain exceeds {MAX_JSON_BYTES} bytes")
        return self


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


def _model_json(value: BaseModel | Mapping[str, Any]) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value)


class QualityDigestDomain(StrEnum):
    """Independent identity domains defined by ADR 0004."""

    RAW_AUDIT = "raw-audit"
    SOURCE_IDENTITY = "source-identity"
    SUBJECT_IDENTITY = "subject-identity"
    RESULT = "result"
    RIGHTS = "rights"


def quality_content_sha256(
    domain: QualityDigestDomain,
    value: BaseModel | Mapping[str, Any],
) -> str:
    """Return a domain-separated RFC 8785 SHA-256 digest.

    Decimal fields in ModelSkyline models are serialized as fixed-point strings
    before canonicalization.  The domain separator prevents equal JSON payloads
    in two evidence domains from sharing an identity.
    """

    payload = _model_json(value)
    if not isinstance(payload, dict):  # pragma: no cover - every accepted input is object-like
        raise TypeError("quality digest payload must be a JSON object")
    _bounded_canonical_object(payload)
    encoded = canonical_bytes(
        {
            "domain": domain.value,
            "payload": payload,
            "version": QUALITY_DIGEST_VERSION,
        }
    )
    if len(encoded) > MAX_JSON_BYTES + 1_024:
        raise ValueError("quality digest input exceeds the canonical byte limit")
    return hashlib.sha256(encoded).hexdigest()


def quality_content_id(
    domain: QualityDigestDomain,
    value: BaseModel | Mapping[str, Any],
) -> str:
    """Return the portable content identifier corresponding to a digest."""

    digest = quality_content_sha256(domain, value)
    return f"model-skyline:quality:{domain.value}:sha256:{digest}"


def quality_raw_sha256(raw: bytes) -> str:
    """Hash one bounded exact capture without decoding or executing it."""

    if not isinstance(raw, bytes):
        raise TypeError("raw quality evidence must be bytes")
    if len(raw) > MAX_RAW_SOURCE_BYTES:
        raise ValueError(f"raw quality evidence exceeds {MAX_RAW_SOURCE_BYTES} bytes")
    return hashlib.sha256(raw).hexdigest()


class QualityComponentIdentity(FrozenModel):
    """One versioned evaluator, scorer, protocol, dataset, or adapter component."""

    id: Identifier
    version: Identifier
    configuration: BoundedCanonicalObject = Field(default_factory=dict)


class QualityRawAudit(_QualityDomainModel):
    """Acquisition provenance; mappings do not bind it unless explicitly pinned."""

    source_locator: Locator
    raw_sha256: Sha256Digest
    retrieved_at: datetime
    upstream_revision: Identifier | None = None
    capture_method: Identifier
    parser_implementation: QualityComponentIdentity
    metadata: BoundedCanonicalObject = Field(default_factory=dict)

    _retrieved_at_utc = field_validator("retrieved_at")(_aware_utc)

    @property
    def content_sha256(self) -> str:
        return quality_content_sha256(QualityDigestDomain.RAW_AUDIT, self)

    @property
    def content_id(self) -> str:
        return quality_content_id(QualityDigestDomain.RAW_AUDIT, self)


class QualitySourceIdentity(_QualityDomainModel):
    """Semantic benchmark identity shared by every row in an evidence set."""

    source_id: Identifier
    source_version: Identifier
    benchmark: QualityComponentIdentity
    dataset: QualityComponentIdentity
    split: Identifier
    evaluator_harness: QualityComponentIdentity
    scorer: QualityComponentIdentity
    protocol: QualityComponentIdentity
    projection: QualityComponentIdentity
    scope: BoundedCanonicalObject = Field(min_length=1)

    @property
    def content_sha256(self) -> str:
        return quality_content_sha256(QualityDigestDomain.SOURCE_IDENTITY, self)

    @property
    def content_id(self) -> str:
        return quality_content_id(QualityDigestDomain.SOURCE_IDENTITY, self)


class QualityPublicationPermission(StrEnum):
    UNRESTRICTED = "unrestricted"
    DERIVED_ONLY = "derived_only"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


class QualityPublicationScope(StrEnum):
    INTERNAL = "internal"
    DERIVED = "derived"
    FULL = "full"


class QualityRights(_QualityDomainModel):
    """A reviewed rights assertion independent of score and route identity."""

    license_expression: ShortText
    terms_locator: Locator | None = None
    publication_permission: QualityPublicationPermission
    reviewed_at: datetime
    review_evidence: ShortText
    metadata: BoundedCanonicalObject = Field(default_factory=dict)

    _reviewed_at_utc = field_validator("reviewed_at")(_aware_utc)

    @property
    def content_sha256(self) -> str:
        return quality_content_sha256(QualityDigestDomain.RIGHTS, self)

    @property
    def content_id(self) -> str:
        return quality_content_id(QualityDigestDomain.RIGHTS, self)

    def permits(self, scope: QualityPublicationScope) -> bool:
        if scope is QualityPublicationScope.INTERNAL:
            return True
        if scope is QualityPublicationScope.DERIVED:
            return self.publication_permission in {
                QualityPublicationPermission.UNRESTRICTED,
                QualityPublicationPermission.DERIVED_ONLY,
            }
        return self.publication_permission is QualityPublicationPermission.UNRESTRICTED


class QualitySubjectKind(StrEnum):
    SINGLE_MODEL_SYSTEM = "single_model_system"
    COMPOSITE_SYSTEM = "composite_system"
    ROUTER_SYSTEM = "router_system"
    UNDISCLOSED_SYSTEM = "undisclosed_system"


class QualityRouteDisclosure(StrEnum):
    EXACT = "exact"
    MUTABLE_ALIAS = "mutable_alias"
    UNKNOWN = "unknown"


class QualityMappingRelationship(StrEnum):
    """What the reviewer is asserting about a subject and production target."""

    EXACT_SUBJECT_ROUTE = "exact_subject_route"
    REVIEWED_QUALITY_PROJECTION = "reviewed_quality_projection"


class QualityModelClaim(FrozenModel):
    """An upstream model claim, never an implicit :class:`OfferingKey`."""

    model_id: Identifier
    display_name: ShortText | None = None
    provider: Identifier | None = None
    endpoint: Identifier | None = None
    revision: Identifier | None = None
    reasoning_effort: Identifier | None = None
    claims: BoundedCanonicalObject = Field(default_factory=dict)


class QualitySubjectIdentity(_QualityDomainModel):
    """Stable row/system claims used for human route reconciliation.

    ``benchmark_agent`` identifies the submitted agent system.  The benchmark's
    evaluator harness lives in :class:`QualitySourceIdentity`; neither field is
    copied into ``OfferingKey.agent_harness`` by reconciliation.
    """

    row_id: Identifier
    kind: QualitySubjectKind
    system_label: ShortText
    model_claims: tuple[QualityModelClaim, ...] = Field(default=(), max_length=MAX_MODEL_CLAIMS)
    benchmark_agent: QualityComponentIdentity | None = None
    route_disclosure: QualityRouteDisclosure
    reasoning_claims: BoundedCanonicalObject = Field(default_factory=dict)
    attempt_claims: BoundedCanonicalObject = Field(default_factory=dict)

    @field_validator("model_claims")
    @classmethod
    def canonical_model_claim_order(
        cls, value: tuple[QualityModelClaim, ...]
    ) -> tuple[QualityModelClaim, ...]:
        identities = [canonical_bytes(item) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("model_claims must not contain duplicates")
        return tuple(item for _, item in sorted(zip(identities, value, strict=True)))

    @model_validator(mode="after")
    def claims_match_subject_kind(self) -> Self:
        if self.kind is QualitySubjectKind.SINGLE_MODEL_SYSTEM and len(self.model_claims) != 1:
            raise ValueError("single_model_system requires exactly one model claim")
        if self.kind is QualitySubjectKind.COMPOSITE_SYSTEM and not self.model_claims:
            raise ValueError("composite_system requires at least one model claim")
        if self.kind is QualitySubjectKind.UNDISCLOSED_SYSTEM and self.model_claims:
            raise ValueError("undisclosed_system cannot assert component model claims")
        return self

    @property
    def content_sha256(self) -> str:
        return quality_content_sha256(QualityDigestDomain.SUBJECT_IDENTITY, self)

    @property
    def content_id(self) -> str:
        return quality_content_id(QualityDigestDomain.SUBJECT_IDENTITY, self)


class QualityMeasurementRole(StrEnum):
    """Controls which evidence can be projected onto a reviewed production route."""

    QUALITY = "quality"
    COST = "cost"
    LATENCY = "latency"
    TOKEN_USAGE = "token_usage"
    OTHER = "other"


class QualityMeasurement(FrozenModel):
    id: Identifier
    role: QualityMeasurementRole
    value: CanonicalDecimal
    unit: Identifier
    lower: CanonicalDecimal | None = None
    upper: CanonicalDecimal | None = None
    sample_count: SafeCount | None = None

    @model_validator(mode="after")
    def bounds_contain_value(self) -> Self:
        if self.lower is not None and self.lower > self.value:
            raise ValueError("measurement lower cannot exceed value")
        if self.upper is not None and self.upper < self.value:
            raise ValueError("measurement upper cannot be below value")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("measurement lower cannot exceed upper")
        return self


class QualityCount(FrozenModel):
    id: Identifier
    role: QualityMeasurementRole
    value: SafeCount


class QualityResult(_QualityDomainModel):
    """A valid normalized result; adapter-rejected rows use ``QualityInvalidResult``."""

    primary_metric: Identifier
    measurements: tuple[QualityMeasurement, ...] = Field(min_length=1, max_length=MAX_MEASUREMENTS)
    counts: tuple[QualityCount, ...] = Field(default=(), max_length=MAX_COUNTS)
    observed_at: datetime
    metadata: BoundedCanonicalObject = Field(default_factory=dict)

    _observed_at_utc = field_validator("observed_at")(_aware_utc)

    @field_validator("measurements")
    @classmethod
    def canonical_measurement_order(
        cls, value: tuple[QualityMeasurement, ...]
    ) -> tuple[QualityMeasurement, ...]:
        counts = Counter(item.id for item in value)
        duplicates = sorted(item for item, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate measurement id {duplicates[0]!r}")
        return tuple(sorted(value, key=lambda item: item.id))

    @field_validator("counts")
    @classmethod
    def canonical_count_order(cls, value: tuple[QualityCount, ...]) -> tuple[QualityCount, ...]:
        counts = Counter(item.id for item in value)
        duplicates = sorted(item for item, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate count id {duplicates[0]!r}")
        return tuple(sorted(value, key=lambda item: item.id))

    @model_validator(mode="after")
    def primary_measurement_exists(self) -> Self:
        primary = next(
            (item for item in self.measurements if item.id == self.primary_metric),
            None,
        )
        if primary is None:
            raise ValueError("primary_metric must name one normalized measurement")
        if primary.role is not QualityMeasurementRole.QUALITY:
            raise ValueError("primary_metric must be a quality measurement")
        return self

    def quality_projection(self) -> QualityResult:
        """Return only quality-attributable fields for a mapped production target.

        Benchmark execution cost, latency, token usage, other counts, and free-form
        result metadata remain in the route-free evidence set.  They are never
        silently attributed to a reviewed production route.
        """

        return QualityResult(
            primary_metric=self.primary_metric,
            measurements=tuple(
                item for item in self.measurements if item.role is QualityMeasurementRole.QUALITY
            ),
            counts=tuple(
                item for item in self.counts if item.role is QualityMeasurementRole.QUALITY
            ),
            observed_at=self.observed_at,
            metadata={},
        )

    @property
    def content_sha256(self) -> str:
        return quality_content_sha256(QualityDigestDomain.RESULT, self)

    @property
    def content_id(self) -> str:
        return quality_content_id(QualityDigestDomain.RESULT, self)


class QualityInvalidResult(_QualityDomainModel):
    """A bounded quarantine record for a row that could not become a result."""

    code: Identifier
    detail: ShortText
    selected_value_sha256: Sha256Digest | None = None

    @property
    def content_sha256(self) -> str:
        return quality_content_sha256(QualityDigestDomain.RESULT, self)

    @property
    def content_id(self) -> str:
        return quality_content_id(QualityDigestDomain.RESULT, self)


class QualityEvidenceRow(FrozenModel):
    subject: QualitySubjectIdentity
    result: QualityResult | None = None
    invalid_result: QualityInvalidResult | None = None

    @model_validator(mode="after")
    def has_exactly_one_result_state(self) -> Self:
        if (self.result is None) == (self.invalid_result is None):
            raise ValueError("evidence row requires exactly one of result or invalid_result")
        return self

    @property
    def row_id(self) -> str:
        return self.subject.row_id

    @property
    def subject_identity_sha256(self) -> str:
        return self.subject.content_sha256

    @property
    def result_sha256(self) -> str:
        selected = self.result if self.result is not None else self.invalid_result
        assert selected is not None  # enforced by the model validator
        return selected.content_sha256


class QualityEvidenceSet(FrozenModel):
    """Route-free normalized evidence emitted by one trusted adapter projection."""

    schema_version: Literal["model-skyline/quality-evidence/v1alpha1"]
    raw_audit: QualityRawAudit
    source_identity: QualitySourceIdentity
    rights: QualityRights
    rows: tuple[QualityEvidenceRow, ...] = Field(default=(), max_length=MAX_EVIDENCE_ROWS)

    @field_validator("rows")
    @classmethod
    def rows_are_unique_and_canonical(
        cls, value: tuple[QualityEvidenceRow, ...]
    ) -> tuple[QualityEvidenceRow, ...]:
        counts = Counter(item.row_id for item in value)
        duplicates = sorted(item for item, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate quality evidence row {duplicates[0]!r}")
        return tuple(sorted(value, key=lambda item: item.row_id))

    @property
    def raw_audit_sha256(self) -> str:
        return self.raw_audit.content_sha256

    @property
    def source_identity_sha256(self) -> str:
        return self.source_identity.content_sha256

    @property
    def rights_sha256(self) -> str:
        return self.rights.content_sha256


_OFFERING_FIELDS = frozenset(OfferingKey.model_fields)


class QualityReconciliationEntry(FrozenModel):
    """One reviewed row-to-route assertion, independent of result and rights."""

    row_id: Identifier
    adapter_id: Identifier
    projection_version: Identifier
    expected_source_identity_sha256: Sha256Digest
    expected_subject_identity_sha256: Sha256Digest
    expected_raw_audit_sha256: Sha256Digest | None = None
    relationship: QualityMappingRelationship
    offering: OfferingKey
    review_evidence: ShortText
    reviewed_at: datetime

    _reviewed_at_utc = field_validator("reviewed_at")(_aware_utc)

    @field_validator("offering", mode="before")
    @classmethod
    def complete_offering_key_is_explicit(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("offering must be an object with every OfferingKey field")
        supplied = frozenset(value)
        missing = sorted(_OFFERING_FIELDS - supplied)
        extra = sorted(supplied - _OFFERING_FIELDS)
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if extra:
                details.append("unexpected: " + ", ".join(extra))
            raise ValueError(
                "offering must explicitly contain exactly every OfferingKey field; "
                + "; ".join(details)
            )
        return value


class QualityReconciliation(FrozenModel):
    """Operator-reviewed mappings; duplicate targets are quarantined in the report."""

    schema_version: Literal["model-skyline/quality-reconciliation/v1alpha1"]
    entries: tuple[QualityReconciliationEntry, ...] = Field(
        default=(), max_length=MAX_RECONCILIATION_ENTRIES
    )

    @field_validator("entries")
    @classmethod
    def rows_are_unique_and_canonical(
        cls, value: tuple[QualityReconciliationEntry, ...]
    ) -> tuple[QualityReconciliationEntry, ...]:
        counts = Counter(item.row_id for item in value)
        duplicates = sorted(item for item, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate quality reconciliation row {duplicates[0]!r}")
        return tuple(sorted(value, key=lambda item: item.row_id))

    @property
    def content_sha256(self) -> str:
        return content_hash(
            {
                "domain": "model-skyline/quality-reconciliation/v1",
                "payload": self.model_dump(mode="json"),
            }
        )


class QualityImportOutcome(StrEnum):
    MAPPED = "mapped"
    UNMAPPED = "unmapped"
    IDENTITY_DRIFT = "identity_drift"
    RESEARCH_ONLY_COMPOSITE = "research_only_composite"
    UNKNOWN_ROUTE = "unknown_route"
    MUTABLE_ALIAS = "mutable_alias"
    INVALID_RESULT = "invalid_result"
    DUPLICATE_TARGET = "duplicate_target"
    LICENSE_BLOCKED = "license_blocked"
    MISSING_REQUIRED_ROW = "missing_required_row"


class MappedQualityRow(FrozenModel):
    """A result that survived exact reconciliation and may feed an adapter catalog."""

    row_id: Identifier
    relationship: QualityMappingRelationship
    offering: OfferingKey
    source_identity: QualitySourceIdentity
    subject: QualitySubjectIdentity
    result: QualityResult
    source_identity_sha256: Sha256Digest
    subject_identity_sha256: Sha256Digest
    result_sha256: Sha256Digest
    evidence_result_sha256: Sha256Digest
    rights_sha256: Sha256Digest

    @model_validator(mode="after")
    def declared_digests_match_content(self) -> Self:
        expected = {
            "source_identity_sha256": self.source_identity.content_sha256,
            "subject_identity_sha256": self.subject.content_sha256,
            "result_sha256": self.result.content_sha256,
        }
        for field, digest in expected.items():
            if getattr(self, field) != digest:
                raise ValueError(f"{field} does not match mapped content")
        if self.row_id != self.subject.row_id:
            raise ValueError("mapped row_id must match subject row_id")
        if (
            self.relationship is QualityMappingRelationship.EXACT_SUBJECT_ROUTE
            and self.evidence_result_sha256 != self.result_sha256
        ):
            raise ValueError("exact_subject_route must preserve the complete evidence result")
        if self.relationship is QualityMappingRelationship.REVIEWED_QUALITY_PROJECTION:
            if self.result.metadata:
                raise ValueError("reviewed quality projections cannot retain result metadata")
            has_non_quality_measurement = any(
                item.role is not QualityMeasurementRole.QUALITY for item in self.result.measurements
            )
            has_non_quality_count = any(
                item.role is not QualityMeasurementRole.QUALITY for item in self.result.counts
            )
            if has_non_quality_measurement or has_non_quality_count:
                raise ValueError(
                    "reviewed quality projections can contain only quality-role fields"
                )
        return self


class QualityImportRecord(FrozenModel):
    row_id: Identifier
    outcome: QualityImportOutcome
    source_identity_sha256: Sha256Digest
    subject_identity_sha256: Sha256Digest | None = None
    evidence_result_sha256: Sha256Digest | None = None
    expected_source_identity_sha256: Sha256Digest | None = None
    expected_subject_identity_sha256: Sha256Digest | None = None
    relationship: QualityMappingRelationship | None = None
    offering: OfferingKey | None = None


class QualityImportReport(FrozenModel):
    """Deterministic local inventory for one reconciliation.

    This model is never a publication-safe projection.  In particular,
    ``publication_scope=derived`` only records the rights check applied before
    returning mapped rows; it does not redact subject or result metadata.
    """

    schema_version: Literal["model-skyline/quality-import-report/v1alpha1"]
    raw_audit_sha256: Sha256Digest
    source_identity_sha256: Sha256Digest
    rights_sha256: Sha256Digest
    reconciliation_sha256: Sha256Digest
    publication_scope: QualityPublicationScope
    publication_safe: Literal[False] = False
    records: tuple[QualityImportRecord, ...] = Field(max_length=MAX_EVIDENCE_ROWS * 2)
    mapped_rows: tuple[MappedQualityRow, ...] = Field(max_length=MAX_EVIDENCE_ROWS)

    @field_validator("records")
    @classmethod
    def records_are_unique_and_canonical(
        cls, value: tuple[QualityImportRecord, ...]
    ) -> tuple[QualityImportRecord, ...]:
        counts = Counter(item.row_id for item in value)
        duplicates = sorted(item for item, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate quality import record {duplicates[0]!r}")
        return tuple(sorted(value, key=lambda item: item.row_id))

    @field_validator("mapped_rows")
    @classmethod
    def mapped_rows_are_unique_and_canonical(
        cls, value: tuple[MappedQualityRow, ...]
    ) -> tuple[MappedQualityRow, ...]:
        row_counts = Counter(item.row_id for item in value)
        duplicate_rows = sorted(item for item, count in row_counts.items() if count > 1)
        if duplicate_rows:
            raise ValueError(f"duplicate mapped quality row {duplicate_rows[0]!r}")
        offering_ids = Counter(item.offering.offering_id for item in value)
        duplicate_ids = sorted(item for item, count in offering_ids.items() if count > 1)
        if duplicate_ids:
            raise ValueError(f"duplicate mapped offering_id {duplicate_ids[0]!r}")
        target_counts = Counter(canonical_bytes(item.offering) for item in value)
        if any(count > 1 for count in target_counts.values()):
            raise ValueError("mapped quality rows must have distinct complete OfferingKeys")
        return tuple(sorted(value, key=lambda item: item.row_id))

    @model_validator(mode="after")
    def mapped_records_match_rows(self) -> Self:
        records = {item.row_id: item for item in self.records}
        mapped = {item.row_id: item for item in self.mapped_rows}
        record_ids = {
            row_id
            for row_id, item in records.items()
            if item.outcome is QualityImportOutcome.MAPPED
        }
        mapped_ids = set(mapped)
        if record_ids != mapped_ids:
            raise ValueError("mapped outcome records must exactly match mapped_rows")
        if any(item.source_identity_sha256 != self.source_identity_sha256 for item in self.records):
            raise ValueError("import records must bind the report source identity")
        for row_id, mapped_row in mapped.items():
            record = records[row_id]
            if mapped_row.source_identity_sha256 != self.source_identity_sha256:
                raise ValueError("mapped rows must bind the report source identity")
            if mapped_row.rights_sha256 != self.rights_sha256:
                raise ValueError("mapped rows must bind the report rights identity")
            if (
                record.subject_identity_sha256 != mapped_row.subject_identity_sha256
                or record.evidence_result_sha256 != mapped_row.evidence_result_sha256
                or record.relationship is not mapped_row.relationship
                or record.offering != mapped_row.offering
            ):
                raise ValueError("mapped import record does not match mapped row evidence")
        return self

    @property
    def content_sha256(self) -> str:
        return content_hash(
            {
                "domain": "model-skyline/quality-import-report/v1",
                "payload": self.model_dump(mode="json"),
            }
        )


def _duplicate_target_rows(
    entries: tuple[QualityReconciliationEntry, ...],
) -> frozenset[str]:
    by_offering_id: dict[str, list[str]] = defaultdict(list)
    by_complete_target: dict[bytes, list[str]] = defaultdict(list)
    for entry in entries:
        by_offering_id[entry.offering.offering_id].append(entry.row_id)
        by_complete_target[canonical_bytes(entry.offering)].append(entry.row_id)
    duplicate_rows: set[str] = set()
    for groups in (by_offering_id.values(), by_complete_target.values()):
        for row_ids in groups:
            if len(row_ids) > 1:
                duplicate_rows.update(row_ids)
    return frozenset(duplicate_rows)


def _record(
    *,
    evidence: QualityEvidenceSet,
    row: QualityEvidenceRow | None,
    entry: QualityReconciliationEntry | None,
    outcome: QualityImportOutcome,
) -> QualityImportRecord:
    if row is None and entry is None:  # pragma: no cover - private caller invariant
        raise ValueError("quality import record requires a row or reconciliation entry")
    if row is not None:
        row_id = row.row_id
    else:
        assert entry is not None
        row_id = entry.row_id
    return QualityImportRecord(
        row_id=row_id,
        outcome=outcome,
        source_identity_sha256=evidence.source_identity_sha256,
        subject_identity_sha256=(row.subject_identity_sha256 if row is not None else None),
        evidence_result_sha256=(row.result_sha256 if row is not None else None),
        expected_source_identity_sha256=(
            entry.expected_source_identity_sha256 if entry is not None else None
        ),
        expected_subject_identity_sha256=(
            entry.expected_subject_identity_sha256 if entry is not None else None
        ),
        relationship=entry.relationship if entry is not None else None,
        offering=entry.offering if entry is not None else None,
    )


def reconcile_quality_evidence(
    evidence: QualityEvidenceSet,
    reconciliation: QualityReconciliation,
    *,
    publication_scope: QualityPublicationScope = QualityPublicationScope.INTERNAL,
) -> QualityImportReport:
    """Reconcile normalized evidence without guessing, I/O, or remote execution.

    Every evidence row and every still-required reviewed mapping produces exactly
    one typed record.  Only ``mapped`` records appear in ``mapped_rows``.
    Result-only and rights-only changes are not mapping identity dependencies;
    source, subject, adapter projection, and optional raw-audit pins are.
    """

    if not isinstance(evidence, QualityEvidenceSet):
        raise TypeError("evidence must be a QualityEvidenceSet")
    if not isinstance(reconciliation, QualityReconciliation):
        raise TypeError("reconciliation must be a QualityReconciliation")
    if not isinstance(publication_scope, QualityPublicationScope):
        raise TypeError("publication_scope must be a QualityPublicationScope")

    rows = {row.row_id: row for row in evidence.rows}
    entries = {entry.row_id: entry for entry in reconciliation.entries}
    duplicate_target_rows = _duplicate_target_rows(reconciliation.entries)
    records: list[QualityImportRecord] = []
    mapped_rows: list[MappedQualityRow] = []

    for row_id in sorted(rows.keys() | entries.keys()):
        row = rows.get(row_id)
        entry = entries.get(row_id)
        if row is None:
            assert entry is not None
            records.append(
                _record(
                    evidence=evidence,
                    row=None,
                    entry=entry,
                    outcome=QualityImportOutcome.MISSING_REQUIRED_ROW,
                )
            )
            continue

        if entry is not None:
            identity_drift = (
                entry.adapter_id != evidence.source_identity.projection.id
                or entry.projection_version != evidence.source_identity.projection.version
                or entry.expected_source_identity_sha256 != evidence.source_identity_sha256
                or entry.expected_subject_identity_sha256 != row.subject_identity_sha256
                or (
                    entry.expected_raw_audit_sha256 is not None
                    and entry.expected_raw_audit_sha256 != evidence.raw_audit_sha256
                )
            )
            if identity_drift:
                records.append(
                    _record(
                        evidence=evidence,
                        row=row,
                        entry=entry,
                        outcome=QualityImportOutcome.IDENTITY_DRIFT,
                    )
                )
                continue

        if row.invalid_result is not None:
            records.append(
                _record(
                    evidence=evidence,
                    row=row,
                    entry=entry,
                    outcome=QualityImportOutcome.INVALID_RESULT,
                )
            )
            continue

        if row.subject.kind is not QualitySubjectKind.SINGLE_MODEL_SYSTEM:
            records.append(
                _record(
                    evidence=evidence,
                    row=row,
                    entry=entry,
                    outcome=QualityImportOutcome.RESEARCH_ONLY_COMPOSITE,
                )
            )
            continue

        if row.subject.route_disclosure is QualityRouteDisclosure.MUTABLE_ALIAS:
            records.append(
                _record(
                    evidence=evidence,
                    row=row,
                    entry=entry,
                    outcome=QualityImportOutcome.MUTABLE_ALIAS,
                )
            )
            continue
        if row.subject.route_disclosure is QualityRouteDisclosure.UNKNOWN and (
            entry is None
            or entry.relationship is not QualityMappingRelationship.REVIEWED_QUALITY_PROJECTION
        ):
            records.append(
                _record(
                    evidence=evidence,
                    row=row,
                    entry=entry,
                    outcome=QualityImportOutcome.UNKNOWN_ROUTE,
                )
            )
            continue
        if entry is None:
            records.append(
                _record(
                    evidence=evidence,
                    row=row,
                    entry=None,
                    outcome=QualityImportOutcome.UNMAPPED,
                )
            )
            continue
        if row_id in duplicate_target_rows:
            records.append(
                _record(
                    evidence=evidence,
                    row=row,
                    entry=entry,
                    outcome=QualityImportOutcome.DUPLICATE_TARGET,
                )
            )
            continue
        if not evidence.rights.permits(publication_scope):
            records.append(
                _record(
                    evidence=evidence,
                    row=row,
                    entry=entry,
                    outcome=QualityImportOutcome.LICENSE_BLOCKED,
                )
            )
            continue

        assert row.result is not None
        mapped_result = (
            row.result
            if entry.relationship is QualityMappingRelationship.EXACT_SUBJECT_ROUTE
            else row.result.quality_projection()
        )
        mapped_rows.append(
            MappedQualityRow(
                row_id=row_id,
                relationship=entry.relationship,
                offering=entry.offering,
                source_identity=evidence.source_identity,
                subject=row.subject,
                result=mapped_result,
                source_identity_sha256=evidence.source_identity_sha256,
                subject_identity_sha256=row.subject_identity_sha256,
                result_sha256=mapped_result.content_sha256,
                evidence_result_sha256=row.result_sha256,
                rights_sha256=evidence.rights_sha256,
            )
        )
        records.append(
            _record(
                evidence=evidence,
                row=row,
                entry=entry,
                outcome=QualityImportOutcome.MAPPED,
            )
        )

    return QualityImportReport(
        schema_version=IMPORT_REPORT_SCHEMA_VERSION,
        raw_audit_sha256=evidence.raw_audit_sha256,
        source_identity_sha256=evidence.source_identity_sha256,
        rights_sha256=evidence.rights_sha256,
        reconciliation_sha256=reconciliation.content_sha256,
        publication_scope=publication_scope,
        publication_safe=False,
        records=tuple(records),
        mapped_rows=tuple(mapped_rows),
    )
