"""Opt-in scalar quality oracles over exact benchmark bundle evidence.

The long-lived oracle policy contains only benchmark semantics and operator
governance choices.  Volatile frontier, catalog, bundle-snapshot, retrieval,
raw-artifact, and rights bindings live in each replayable oracle snapshot.

Only fixed-reference min-max normalization is supported.  Candidate-relative
normalization is intentionally absent because adding or removing a model would
otherwise change every score.  All components are required and out-of-range
values are rejected rather than clamped.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, canonical_bytes, content_hash
from model_skyline.models import (
    MAX_DECIMAL_SIGNIFICANT_DIGITS,
    MAX_SELECTION_CANDIDATES,
    AxisDescriptor,
    AxisEstimate,
    CanonicalDecimal,
    FrozenModel,
    Goal,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    PortablePublicationId,
    PublicSourceUrl,
    Sha256Digest,
    SourceReference,
    WorkloadReference,
    bounded_canonical_decimal,
)
from model_skyline.quality_bundle import (
    MAX_QUALITY_BUNDLE_COMPONENTS,
    MIN_QUALITY_BUNDLE_COMPONENTS,
    ComponentId,
    QualityBundleCandidateCoverage,
    QualityBundleComponent,
    QualityBundleSnapshot,
    QualityComponentCoverage,
    QualityCoverageStatus,
    QuarantineReasonCode,
)
from model_skyline.quality_evidence import MAX_QUALITY_ARTIFACT_BYTES, ShortText
from model_skyline.selection_overlap import MAX_OFFERING_IDENTITY_BYTES

QUALITY_ORACLE_POLICY_SCHEMA_VERSION: Literal["model-skyline/quality-oracle-policy/v1alpha1"] = (
    "model-skyline/quality-oracle-policy/v1alpha1"
)
QUALITY_ORACLE_SNAPSHOT_SCHEMA_VERSION: Literal[
    "model-skyline/quality-oracle-snapshot/v1alpha1"
] = "model-skyline/quality-oracle-snapshot/v1alpha1"
QUALITY_ORACLE_ALGORITHM_VERSION: Literal["weighted-fixed-min-max-decimal-v1"] = (
    "weighted-fixed-min-max-decimal-v1"
)
NORMALIZATION_REFERENCE_SCHEMA_VERSION: Literal[
    "model-skyline/fixed-min-max-normalization-reference/v1alpha1"
] = "model-skyline/fixed-min-max-normalization-reference/v1alpha1"
SELECTED_QUALITY_PROJECTION_DOMAIN = "model-skyline/selected-quality-projection/v1"
SELECTED_QUALITY_COMPONENT_PROJECTION_DOMAIN = (
    "model-skyline/selected-quality-component-projection/v1"
)

NormalizedScore = Annotated[CanonicalDecimal, Field(ge=0, le=1)]
PositiveWeight = Annotated[CanonicalDecimal, Field(gt=0, le=1)]
CorrelationGroup = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$"),
]


def _offering_identity(offering: OfferingKey) -> bytes:
    return canonical_bytes(offering)


def _require_artifact_bound(artifact: FrozenModel) -> None:
    if len(canonical_bytes(artifact.model_dump(mode="json"))) > MAX_QUALITY_ARTIFACT_BYTES:
        raise ValueError(
            f"quality oracle artifact exceeds {MAX_QUALITY_ARTIFACT_BYTES} canonical bytes"
        )
    if len(artifact.model_dump_json(indent=2).encode("utf-8")) + 1 > MAX_QUALITY_ARTIFACT_BYTES:
        raise ValueError(
            f"quality oracle artifact exceeds {MAX_QUALITY_ARTIFACT_BYTES} serialized bytes"
        )


def _canonical_sources(sources: Iterable[SourceReference]) -> tuple[SourceReference, ...]:
    values = tuple(sources)
    if not values:
        raise ValueError("a quality oracle component requires at least one exact source")
    counts = Counter(source.id for source in values)
    duplicates = sorted(source_id for source_id, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate quality oracle source id {duplicates[0]!r}")
    for source in values:
        if len(source.id) > 512:
            raise ValueError("quality oracle source id exceeds 512 characters")
        if not source.version:
            raise ValueError(f"quality oracle source {source.id!r} requires a version")
        if len(source.version) > 512:
            raise ValueError("quality oracle source version exceeds 512 characters")
        if source.raw_sha256 is None:
            raise ValueError(f"quality oracle source {source.id!r} requires raw_sha256")
        if not source.methodology:
            raise ValueError(f"quality oracle source {source.id!r} requires methodology")
        if len(source.methodology) > 4_096:
            raise ValueError("quality oracle source methodology exceeds 4096 characters")
        if source.retrieved_at is None:
            raise ValueError(f"quality oracle source {source.id!r} requires retrieved_at")
    return tuple(sorted(values, key=lambda source: source.id))


class QualityOracleSourceSemantic(FrozenModel):
    """Stable benchmark-source identity, excluding capture and rights state."""

    id: str = Field(min_length=1, max_length=512)
    version: str = Field(min_length=1, max_length=512)
    url: PublicSourceUrl | None = None
    methodology: str = Field(min_length=1, max_length=4_096)


def _canonical_source_semantics(
    semantics: Iterable[QualityOracleSourceSemantic],
) -> tuple[QualityOracleSourceSemantic, ...]:
    values = tuple(semantics)
    if not values:
        raise ValueError("a quality oracle component requires source semantics")
    counts = Counter(source.id for source in values)
    duplicates = sorted(source_id for source_id, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate quality oracle source semantic id {duplicates[0]!r}")
    return tuple(sorted(values, key=lambda source: source.id))


def quality_oracle_source_semantics(
    sources: Iterable[SourceReference],
) -> tuple[QualityOracleSourceSemantic, ...]:
    """Project full source captures to stable benchmark semantics."""

    return _canonical_source_semantics(
        QualityOracleSourceSemantic(
            id=source.id,
            version=source.version or "",
            url=source.url,
            methodology=source.methodology or "",
        )
        for source in _canonical_sources(sources)
    )


def quality_oracle_source_semantic_identity(
    sources: Iterable[SourceReference] | Iterable[QualityOracleSourceSemantic],
) -> str:
    """Hash only source fields that define benchmark semantics."""

    values = tuple(sources)
    if not values:
        raise ValueError("a quality oracle component requires source semantics")
    first = values[0]
    if isinstance(first, SourceReference):
        semantics = quality_oracle_source_semantics(
            source for source in values if isinstance(source, SourceReference)
        )
        if len(semantics) != len(values):
            raise ValueError("quality oracle source identity inputs must use one descriptor type")
    else:
        if not all(isinstance(source, QualityOracleSourceSemantic) for source in values):
            raise ValueError("quality oracle source identity inputs must use one descriptor type")
        semantics = _canonical_source_semantics(
            source for source in values if isinstance(source, QualityOracleSourceSemantic)
        )
    return content_hash(
        {
            "domain": "model-skyline/quality-oracle-source-semantic-identity/v1",
            "sources": [source.model_dump(mode="json") for source in semantics],
        }
    )


def _source_capture_identity(
    domain: str,
    sources: Iterable[SourceReference],
    fields: tuple[str, ...] | None,
) -> str:
    canonical = _canonical_sources(sources)
    if fields is None:
        payload = [source.model_dump(mode="json") for source in canonical]
    else:
        payload = [
            {field: source.model_dump(mode="json").get(field) for field in fields}
            for source in canonical
        ]
    return content_hash({"domain": domain, "sources": payload})


def quality_oracle_source_raw_identity(sources: Iterable[SourceReference]) -> str:
    return _source_capture_identity(
        "model-skyline/quality-oracle-source-raw-identity/v1",
        sources,
        ("id", "raw_sha256"),
    )


def quality_oracle_source_retrieval_identity(sources: Iterable[SourceReference]) -> str:
    return _source_capture_identity(
        "model-skyline/quality-oracle-source-retrieval-identity/v1",
        sources,
        ("id", "retrieved_at"),
    )


def quality_oracle_source_rights_identity(sources: Iterable[SourceReference]) -> str:
    return _source_capture_identity(
        "model-skyline/quality-oracle-source-rights-identity/v1",
        sources,
        ("id", "terms_url", "license"),
    )


def quality_oracle_source_capture_identity(sources: Iterable[SourceReference]) -> str:
    return _source_capture_identity(
        "model-skyline/quality-oracle-source-capture-identity/v1",
        sources,
        None,
    )


def quality_oracle_source_identity(sources: Iterable[SourceReference]) -> str:
    """Backward-compatible name for the exact capture identity."""

    return quality_oracle_source_capture_identity(sources)


class _FixedMinMaxNormalizationContent(FrozenModel):
    schema_version: Literal["model-skyline/fixed-min-max-normalization-reference/v1alpha1"] = (
        NORMALIZATION_REFERENCE_SCHEMA_VERSION
    )
    kind: Literal["fixed-min-max-normalization-reference"] = "fixed-min-max-normalization-reference"
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    reference_id: PortablePublicationId
    reference_version: str = Field(min_length=1, max_length=128)
    input_unit: str = Field(min_length=1, max_length=128)
    reference_min: CanonicalDecimal
    reference_max: CanonicalDecimal
    out_of_reference: Literal["reject"] = "reject"
    rationale: ShortText

    @model_validator(mode="after")
    def range_is_nonempty(self) -> Self:
        if self.reference_max <= self.reference_min:
            raise ValueError("normalization reference_max must exceed reference_min")
        return self


class FixedMinMaxNormalization(_FixedMinMaxNormalizationContent):
    """A typed, self-hashed, operator-authored normalization reference."""

    reference_sha256: Sha256Digest

    @model_validator(mode="after")
    def reference_hash_is_valid(self) -> Self:
        if self.reference_sha256 != fixed_min_max_normalization_hash(self):
            raise ValueError("normalization reference hash mismatch")
        return self


def fixed_min_max_normalization_hash(reference: FixedMinMaxNormalization) -> str:
    return content_hash(
        {
            "domain": "model-skyline/fixed-min-max-normalization-reference/v1",
            "reference": reference.model_dump(mode="json", exclude={"reference_sha256"}),
        }
    )


def build_fixed_min_max_normalization(
    *,
    reference_id: str,
    reference_version: str,
    input_unit: str,
    reference_min: Decimal,
    reference_max: Decimal,
    rationale: str,
) -> FixedMinMaxNormalization:
    """Build the canonical typed normalization artifact and its digest."""

    content = _FixedMinMaxNormalizationContent(
        reference_id=reference_id,
        reference_version=reference_version,
        input_unit=input_unit,
        reference_min=reference_min,
        reference_max=reference_max,
        rationale=rationale,
    )
    digest = content_hash(
        {
            "domain": "model-skyline/fixed-min-max-normalization-reference/v1",
            "reference": content.model_dump(mode="json"),
        }
    )
    return FixedMinMaxNormalization(reference_sha256=digest, **content.model_dump())


class QualityOracleComponent(FrozenModel):
    """Stable semantics and governance for one selected benchmark metric."""

    component_id: ComponentId
    workload: WorkloadReference
    quality_axis: AxisDescriptor
    source_semantics: tuple[QualityOracleSourceSemantic, ...] = Field(min_length=1, max_length=64)
    source_semantic_identity_sha256: Sha256Digest
    normalization: FixedMinMaxNormalization
    weight: PositiveWeight
    correlation_group: CorrelationGroup
    rationale: ShortText

    @field_validator("source_semantics")
    @classmethod
    def source_semantics_are_canonical(
        cls,
        value: tuple[QualityOracleSourceSemantic, ...],
    ) -> tuple[QualityOracleSourceSemantic, ...]:
        return _canonical_source_semantics(value)

    @model_validator(mode="after")
    def binding_is_coherent(self) -> Self:
        if (
            len(self.workload.id) > 512
            or len(self.workload.version) > 512
            or len(self.workload.unit) > 128
        ):
            raise ValueError("quality oracle component workload identity is too long")
        if self.quality_axis.unit != self.normalization.input_unit:
            raise ValueError("quality unit must match the normalization input unit")
        if self.source_semantic_identity_sha256 != quality_oracle_source_semantic_identity(
            self.source_semantics
        ):
            raise ValueError("quality oracle component semantic source identity mismatch")
        return self


class QualityOraclePolicy(FrozenModel):
    """Stable opt-in policy for one versioned composite quality workload."""

    schema_version: Literal["model-skyline/quality-oracle-policy/v1alpha1"] = (
        QUALITY_ORACLE_POLICY_SCHEMA_VERSION
    )
    kind: Literal["quality-oracle-policy"] = "quality-oracle-policy"
    oracle_id: PortablePublicationId
    version: str = Field(min_length=1, max_length=128)
    composite_workload: WorkloadReference
    quality_metric: str = Field(min_length=1, max_length=256)
    quality_unit: Literal["normalized_quality_score"] = "normalized_quality_score"
    quality_bundle_id: PortablePublicationId
    quality_bundle_version: str = Field(min_length=1, max_length=128)
    strategy: Literal["weighted-fixed-min-max"] = "weighted-fixed-min-max"
    missing_signal_policy: Literal["reject-candidate"] = "reject-candidate"
    algorithm_version: Literal["weighted-fixed-min-max-decimal-v1"] = (
        QUALITY_ORACLE_ALGORITHM_VERSION
    )
    components: tuple[QualityOracleComponent, ...] = Field(
        min_length=MIN_QUALITY_BUNDLE_COMPONENTS,
        max_length=MAX_QUALITY_BUNDLE_COMPONENTS,
    )
    aggregation_rationale: ShortText
    correlation_rationale: ShortText
    statistical_independence_assumed: Literal[False] = False

    @field_validator("components")
    @classmethod
    def components_are_canonical(
        cls,
        value: tuple[QualityOracleComponent, ...],
    ) -> tuple[QualityOracleComponent, ...]:
        return tuple(sorted(value, key=lambda component: component.component_id))

    @model_validator(mode="after")
    def policy_is_coherent(self) -> Self:
        component_ids = tuple(component.component_id for component in self.components)
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("quality oracle component ids must be unique")
        if any(component.workload == self.composite_workload for component in self.components):
            raise ValueError(
                "composite workload must be distinct from every input benchmark workload"
            )
        with localcontext() as context:
            context.prec = MAX_DECIMAL_SIGNIFICANT_DIGITS * len(self.components) + 16
            total_weight = sum((component.weight for component in self.components), Decimal(0))
        if total_weight != Decimal(1):
            raise ValueError("quality oracle component weights must sum exactly to 1")

        for owner, label in (
            (
                (
                    (
                        component.source_semantic_identity_sha256,
                        component.correlation_group,
                    )
                    for component in self.components
                ),
                "source semantic identity",
            ),
            (
                (
                    (component.normalization.reference_sha256, component.correlation_group)
                    for component in self.components
                ),
                "normalization reference",
            ),
        ):
            groups: dict[str, set[str]] = {}
            for identity, correlation_group in owner:
                groups.setdefault(identity, set()).add(correlation_group)
            if any(len(correlation_groups) > 1 for correlation_groups in groups.values()):
                raise ValueError(f"a reused {label} must use one correlation group")

        source_by_id: dict[str, QualityOracleSourceSemantic] = {}
        source_owners: dict[bytes, set[str]] = {}
        workload_owners: dict[bytes, set[str]] = {}
        for component in self.components:
            workload_owners.setdefault(canonical_bytes(component.workload), set()).add(
                component.correlation_group
            )
            for source in component.source_semantics:
                existing_source = source_by_id.get(source.id)
                if existing_source is not None and existing_source != source:
                    raise ValueError(f"source id {source.id!r} maps to different oracle semantics")
                source_by_id[source.id] = source
                source_owners.setdefault(canonical_bytes(source), set()).add(
                    component.correlation_group
                )
        if any(len(groups) > 1 for groups in source_owners.values()):
            raise ValueError("a reused semantic source must use one correlation group")
        if any(len(groups) > 1 for groups in workload_owners.values()):
            raise ValueError("a reused benchmark workload must use one correlation group")

        references: dict[str, FixedMinMaxNormalization] = {}
        for component in self.components:
            digest = component.normalization.reference_sha256
            existing = references.get(digest)
            if existing is not None and existing != component.normalization:
                raise ValueError(
                    "one normalization reference digest cannot describe different scales"
                )
            references[digest] = component.normalization
        _require_artifact_bound(self)
        return self


class QualityOracleComponentStatus(StrEnum):
    MEASURED = "measured"
    MISSING = "missing"
    QUARANTINED = "quarantined"
    OUT_OF_REFERENCE = "out_of_reference"


class QualityOracleComponentEvaluation(FrozenModel):
    """Candidate-level value with exact capture and component provenance."""

    component_id: ComponentId
    frontier_snapshot_id: Sha256Digest
    quality_metric: str = Field(min_length=1, max_length=256)
    source_semantic_identity_sha256: Sha256Digest
    source_capture_identity_sha256: Sha256Digest
    status: QualityOracleComponentStatus
    raw_estimate: AxisEstimate | None = None
    normalized_value: NormalizedScore | None = None
    normalized_lower: NormalizedScore | None = None
    normalized_upper: NormalizedScore | None = None
    weighted_contribution: NormalizedScore | None = None
    weighted_lower: NormalizedScore | None = None
    weighted_upper: NormalizedScore | None = None
    quarantine_reason_codes: tuple[QuarantineReasonCode, ...] = Field(default=(), max_length=32)

    @field_validator("quarantine_reason_codes", mode="before")
    @classmethod
    def reasons_are_canonical(cls, value: object) -> tuple[str, ...]:
        if value in ((), []):
            return ()
        if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
            raise ValueError("oracle quarantine reasons must be an array of strings")
        if len(value) != len(set(value)):
            raise ValueError("oracle quarantine reasons must not contain duplicates")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def state_is_coherent(self) -> Self:
        numeric = (
            self.normalized_value,
            self.normalized_lower,
            self.normalized_upper,
            self.weighted_contribution,
            self.weighted_lower,
            self.weighted_upper,
        )
        if self.status is QualityOracleComponentStatus.MEASURED:
            if self.raw_estimate is None:
                raise ValueError("measured oracle component requires a raw estimate")
            if self.normalized_value is None or self.weighted_contribution is None:
                raise ValueError("measured oracle component requires normalized values")
            if self.quarantine_reason_codes:
                raise ValueError("measured oracle component cannot have quarantine reasons")
            if (self.normalized_lower is None) != (self.normalized_upper is None):
                raise ValueError("normalized component bounds must be complete or absent")
            if (self.weighted_lower is None) != (self.weighted_upper is None):
                raise ValueError("weighted component bounds must be complete or absent")
            if (self.normalized_lower is None) != (self.weighted_lower is None):
                raise ValueError("normalized and weighted bounds must have matching completeness")
            if (
                self.normalized_lower is not None
                and self.normalized_upper is not None
                and not self.normalized_lower <= self.normalized_value <= self.normalized_upper
            ):
                raise ValueError("normalized component bounds must contain its value")
            if (
                self.weighted_lower is not None
                and self.weighted_upper is not None
                and not self.weighted_lower <= self.weighted_contribution <= self.weighted_upper
            ):
                raise ValueError("weighted component bounds must contain its contribution")
        elif self.status is QualityOracleComponentStatus.QUARANTINED:
            if self.raw_estimate is not None or any(value is not None for value in numeric):
                raise ValueError("quarantined oracle component cannot carry measurements")
            if not self.quarantine_reason_codes:
                raise ValueError("quarantined oracle component requires reason codes")
        elif self.status is QualityOracleComponentStatus.OUT_OF_REFERENCE:
            if self.raw_estimate is None or any(value is not None for value in numeric):
                raise ValueError("out-of-reference oracle component requires only its raw estimate")
            if self.quarantine_reason_codes != ("out_of_reference",):
                raise ValueError(
                    "out-of-reference oracle component requires its canonical reason code"
                )
        else:
            if self.raw_estimate is not None or any(value is not None for value in numeric):
                raise ValueError("missing oracle component cannot carry measurements")
            if self.quarantine_reason_codes:
                raise ValueError("missing oracle component cannot carry quarantine reasons")
        return self


class QualityOracleComponentCapture(FrozenModel):
    """Volatile exact bundle, frontier, source, retrieval, and rights binding."""

    component_id: ComponentId
    bundle_component: QualityBundleComponent
    selected_quality_projection_sha256: Sha256Digest
    source_semantic_identity_sha256: Sha256Digest
    source_raw_identity_sha256: Sha256Digest
    source_retrieval_identity_sha256: Sha256Digest
    source_rights_identity_sha256: Sha256Digest
    source_capture_identity_sha256: Sha256Digest
    sources: tuple[SourceReference, ...] = Field(min_length=1, max_length=64)

    @field_validator("sources")
    @classmethod
    def sources_are_canonical(
        cls,
        value: tuple[SourceReference, ...],
    ) -> tuple[SourceReference, ...]:
        return _canonical_sources(value)

    @model_validator(mode="after")
    def capture_is_coherent(self) -> Self:
        if self.component_id != self.bundle_component.component_id:
            raise ValueError("oracle component capture id does not match its bundle component")
        identities = (
            (
                self.source_semantic_identity_sha256,
                quality_oracle_source_semantic_identity(self.sources),
                "semantic",
            ),
            (
                self.source_raw_identity_sha256,
                quality_oracle_source_raw_identity(self.sources),
                "raw",
            ),
            (
                self.source_retrieval_identity_sha256,
                quality_oracle_source_retrieval_identity(self.sources),
                "retrieval",
            ),
            (
                self.source_rights_identity_sha256,
                quality_oracle_source_rights_identity(self.sources),
                "rights",
            ),
            (
                self.source_capture_identity_sha256,
                quality_oracle_source_capture_identity(self.sources),
                "capture",
            ),
        )
        for actual, expected, label in identities:
            if actual != expected:
                raise ValueError(f"quality oracle component {label} source identity mismatch")
        return self


class QualityOracleCandidate(FrozenModel):
    offering: OfferingKey
    status: Literal["scored", "rejected"]
    components: tuple[QualityOracleComponentEvaluation, ...] = Field(
        min_length=MIN_QUALITY_BUNDLE_COMPONENTS,
        max_length=MAX_QUALITY_BUNDLE_COMPONENTS,
    )
    failed_component_ids: tuple[ComponentId, ...] = Field(default=(), max_length=4)
    estimate: AxisEstimate | None = None

    @model_validator(mode="after")
    def status_is_coherent(self) -> Self:
        component_ids = tuple(component.component_id for component in self.components)
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("candidate oracle components must be unique")
        failures = tuple(
            component.component_id
            for component in self.components
            if component.status is not QualityOracleComponentStatus.MEASURED
        )
        if self.failed_component_ids != failures:
            raise ValueError("candidate failed component ids do not match its evidence")
        if self.status == "scored":
            if failures or self.estimate is None:
                raise ValueError("scored candidate requires every component and an estimate")
        elif not failures or self.estimate is not None:
            raise ValueError("rejected candidate requires a failed component and no estimate")
        return self


def _component_axis(binding: QualityBundleComponent) -> AxisDescriptor:
    return next(axis for axis in binding.axes if axis.metric == binding.quality_metric)


def _axis_estimate_projection(estimate: AxisEstimate) -> dict[str, object]:
    payload = estimate.model_dump(mode="json")
    payload["dependencies"] = sorted(estimate.dependencies)
    payload["source_ids"] = sorted(estimate.source_ids)
    payload.pop("sources")
    payload["source_semantics"] = [
        source.model_dump(mode="json")
        for source in quality_oracle_source_semantics(estimate.sources)
    ]
    return payload


def _bundle_component_projection_content(
    bundle: QualityBundleSnapshot,
    binding: QualityBundleComponent,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for candidate in bundle.candidates:
        coverage = next(
            item for item in candidate.components if item.component_id == binding.component_id
        )
        rows.append(
            {
                "offering": candidate.offering.model_dump(mode="json"),
                "status": coverage.status.value,
                "estimate": (
                    _axis_estimate_projection(coverage.estimate)
                    if coverage.estimate is not None
                    else None
                ),
                "quarantine_reason_codes": list(coverage.quarantine_reason_codes),
            }
        )
    return {
        "domain": SELECTED_QUALITY_COMPONENT_PROJECTION_DOMAIN,
        "component_id": binding.component_id,
        "workload": binding.workload.model_dump(mode="json"),
        "quality_axis": _component_axis(binding).model_dump(mode="json"),
        "candidates": rows,
    }


def _selected_quality_component_projection_hashes(
    bundle: QualityBundleSnapshot,
) -> dict[str, str]:
    return {
        component.component_id: content_hash(
            _bundle_component_projection_content(bundle, component)
        )
        for component in bundle.policy.components
    }


def _selected_quality_projection_hash_from_components(
    *,
    quality_bundle_id: str,
    quality_bundle_version: str,
    component_hashes: Mapping[str, str],
) -> str:
    return content_hash(
        {
            "domain": SELECTED_QUALITY_PROJECTION_DOMAIN,
            "quality_bundle_id": quality_bundle_id,
            "quality_bundle_version": quality_bundle_version,
            "components": [
                {
                    "component_id": component_id,
                    "selected_quality_projection_sha256": component_hashes[component_id],
                }
                for component_id in sorted(component_hashes)
            ],
        }
    )


def quality_oracle_selected_quality_component_projection_hashes(
    quality_bundle: QualityBundleSnapshot,
) -> dict[str, str]:
    """Hash only each bundle component's selected quality evidence and statuses."""

    bundle = QualityBundleSnapshot.model_validate(quality_bundle.model_dump(mode="json"))
    return _selected_quality_component_projection_hashes(bundle)


def quality_oracle_selected_quality_projection_hash(
    quality_bundle: QualityBundleSnapshot,
) -> str:
    """Return the price/companion-axis-independent selected-quality projection hash."""

    bundle = QualityBundleSnapshot.model_validate(quality_bundle.model_dump(mode="json"))
    return _selected_quality_projection_hash_from_components(
        quality_bundle_id=bundle.policy.bundle_id,
        quality_bundle_version=bundle.policy.version,
        component_hashes=_selected_quality_component_projection_hashes(bundle),
    )


def _oracle_component_projection_content(
    component: QualityOracleComponent,
    candidates: tuple[QualityOracleCandidate, ...],
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for candidate in candidates:
        evaluation = next(
            item for item in candidate.components if item.component_id == component.component_id
        )
        status = (
            QualityCoverageStatus.MEASURED.value
            if evaluation.status
            in (
                QualityOracleComponentStatus.MEASURED,
                QualityOracleComponentStatus.OUT_OF_REFERENCE,
            )
            else evaluation.status.value
        )
        reasons = (
            []
            if evaluation.status is QualityOracleComponentStatus.OUT_OF_REFERENCE
            else list(evaluation.quarantine_reason_codes)
        )
        rows.append(
            {
                "offering": candidate.offering.model_dump(mode="json"),
                "status": status,
                "estimate": (
                    _axis_estimate_projection(evaluation.raw_estimate)
                    if evaluation.raw_estimate is not None
                    else None
                ),
                "quarantine_reason_codes": reasons,
            }
        )
    return {
        "domain": SELECTED_QUALITY_COMPONENT_PROJECTION_DOMAIN,
        "component_id": component.component_id,
        "workload": component.workload.model_dump(mode="json"),
        "quality_axis": component.quality_axis.model_dump(mode="json"),
        "candidates": rows,
    }


class _QualityOracleSnapshotContent(FrozenModel):
    schema_version: Literal["model-skyline/quality-oracle-snapshot/v1alpha1"] = (
        QUALITY_ORACLE_SNAPSHOT_SCHEMA_VERSION
    )
    kind: Literal["quality-oracle-snapshot"] = "quality-oracle-snapshot"
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    policy_hash: Sha256Digest
    policy: QualityOraclePolicy
    quality_bundle_policy_hash: Sha256Digest
    quality_bundle_snapshot_id: Sha256Digest
    quality_bundle_snapshot_hash: Sha256Digest
    quality_bundle_generated_at: datetime
    quality_bundle_valid_until: datetime
    quality_bundle_candidate_universe_hash: Sha256Digest
    selected_quality_projection_sha256: Sha256Digest
    component_captures: tuple[QualityOracleComponentCapture, ...] = Field(
        min_length=MIN_QUALITY_BUNDLE_COMPONENTS,
        max_length=MAX_QUALITY_BUNDLE_COMPONENTS,
    )
    generated_at: datetime
    valid_until: datetime
    candidate_universe_hash: Sha256Digest
    candidates: tuple[QualityOracleCandidate, ...] = Field(
        min_length=1,
        max_length=MAX_SELECTION_CANDIDATES,
    )

    @field_validator(
        "quality_bundle_generated_at",
        "quality_bundle_valid_until",
        "generated_at",
        "valid_until",
    )
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("quality oracle timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("component_captures")
    @classmethod
    def captures_are_canonical(
        cls,
        value: tuple[QualityOracleComponentCapture, ...],
    ) -> tuple[QualityOracleComponentCapture, ...]:
        return tuple(sorted(value, key=lambda capture: capture.component_id))

    @model_validator(mode="after")
    def content_is_coherent(self) -> Self:
        if self.policy_hash != quality_oracle_policy_hash(self.policy):
            raise ValueError("quality oracle policy hash mismatch")
        if self.quality_bundle_snapshot_id != self.quality_bundle_snapshot_hash:
            raise ValueError("quality bundle snapshot id and hash must match")
        if self.quality_bundle_generated_at > self.generated_at:
            raise ValueError("quality oracle cannot predate its quality bundle")
        if self.quality_bundle_valid_until != self.valid_until:
            raise ValueError("quality oracle validity must match its quality bundle")
        if self.valid_until <= self.generated_at:
            raise ValueError("quality oracle valid_until must follow generated_at")
        if self.quality_bundle_candidate_universe_hash != self.candidate_universe_hash:
            raise ValueError("quality oracle candidate universe must match its quality bundle")

        identities = [_offering_identity(candidate.offering) for candidate in self.candidates]
        if any(len(identity) > MAX_OFFERING_IDENTITY_BYTES for identity in identities):
            raise ValueError("quality oracle OfferingKey identity exceeds the byte limit")
        if len(identities) != len(set(identities)):
            raise ValueError("quality oracle candidates require distinct complete OfferingKeys")
        offering_ids = [candidate.offering.offering_id for candidate in self.candidates]
        if len(offering_ids) != len(set(offering_ids)):
            raise ValueError("quality oracle candidates require distinct offering_id values")
        if identities != sorted(identities):
            raise ValueError("quality oracle candidates must be canonically ordered")
        if self.candidate_universe_hash != content_hash(
            [candidate.offering.model_dump(mode="json") for candidate in self.candidates]
        ):
            raise ValueError("quality oracle candidate universe hash mismatch")
        if not any(candidate.status == "scored" for candidate in self.candidates):
            raise ValueError("quality oracle requires at least one fully measured candidate")

        policy_ids = tuple(component.component_id for component in self.policy.components)
        capture_ids = tuple(capture.component_id for capture in self.component_captures)
        if capture_ids != policy_ids:
            raise ValueError("quality oracle captures must exactly match policy components")
        captures = {capture.component_id: capture for capture in self.component_captures}
        bindings = {component.component_id: component for component in self.policy.components}
        component_hashes: dict[str, str] = {}
        for component_id, binding in bindings.items():
            capture = captures[component_id]
            bundle_binding = capture.bundle_component
            if (
                bundle_binding.workload != binding.workload
                or _component_axis(bundle_binding) != binding.quality_axis
            ):
                raise ValueError(
                    f"quality oracle component {component_id!r} semantic capture mismatch"
                )
            if capture.source_semantic_identity_sha256 != (binding.source_semantic_identity_sha256):
                raise ValueError(
                    f"quality oracle component {component_id!r} source semantic mismatch"
                )
            expected_projection = content_hash(
                _oracle_component_projection_content(binding, self.candidates)
            )
            if capture.selected_quality_projection_sha256 != expected_projection:
                raise ValueError(
                    f"quality oracle component {component_id!r} selected-quality projection "
                    "mismatch"
                )
            component_hashes[component_id] = expected_projection

        expected_global_projection = _selected_quality_projection_hash_from_components(
            quality_bundle_id=self.policy.quality_bundle_id,
            quality_bundle_version=self.policy.quality_bundle_version,
            component_hashes=component_hashes,
        )
        if self.selected_quality_projection_sha256 != expected_global_projection:
            raise ValueError("quality oracle selected-quality projection mismatch")

        for candidate in self.candidates:
            if tuple(component.component_id for component in candidate.components) != policy_ids:
                raise ValueError("candidate components must follow quality oracle policy order")
            for evaluation in candidate.components:
                binding = bindings[evaluation.component_id]
                capture = captures[evaluation.component_id]
                if (
                    evaluation.frontier_snapshot_id != capture.bundle_component.frontier_snapshot_id
                    or evaluation.quality_metric != binding.quality_axis.metric
                    or evaluation.source_semantic_identity_sha256
                    != binding.source_semantic_identity_sha256
                    or evaluation.source_capture_identity_sha256
                    != capture.source_capture_identity_sha256
                ):
                    raise ValueError("candidate component provenance does not match its capture")
                if evaluation.status is QualityOracleComponentStatus.MEASURED:
                    if evaluation.raw_estimate is None:  # pragma: no cover - model invariant
                        raise ValueError("measured candidate component has no raw estimate")
                    if evaluation != _measured_evaluation(
                        binding,
                        capture,
                        evaluation.raw_estimate,
                    ):
                        raise ValueError(
                            "candidate normalized component does not match policy arithmetic"
                        )
                elif evaluation.status is QualityOracleComponentStatus.OUT_OF_REFERENCE:
                    if evaluation.raw_estimate is None:  # pragma: no cover - model invariant
                        raise ValueError("out-of-reference component has no raw estimate")
                    try:
                        _measured_evaluation(binding, capture, evaluation.raw_estimate)
                    except _OutOfReferenceError:
                        expected_out_of_reference = _out_of_reference_evaluation(
                            binding,
                            capture,
                            evaluation.raw_estimate,
                        )
                    else:
                        raise ValueError(
                            "out-of-reference component is inside its normalization reference"
                        )
                    if evaluation != expected_out_of_reference:
                        raise ValueError("out-of-reference component is not canonical")
            if candidate.status == "scored":
                expected = _composite_estimate(self.policy, candidate.components)
                if candidate.estimate != expected:
                    raise ValueError("candidate composite estimate does not match its components")

        for component_id, capture in captures.items():
            source_sets = {
                canonical_bytes(
                    [
                        source.model_dump(mode="json")
                        for source in _canonical_sources(evaluation.raw_estimate.sources)
                    ]
                )
                for candidate in self.candidates
                for evaluation in candidate.components
                if evaluation.component_id == component_id and evaluation.raw_estimate is not None
            }
            expected_sources = canonical_bytes(
                [source.model_dump(mode="json") for source in capture.sources]
            )
            if source_sets != {expected_sources}:
                raise ValueError(
                    f"quality oracle component {component_id!r} exact source capture mismatch"
                )
        _validate_raw_source_reuse(self.policy, self.component_captures)
        _require_artifact_bound(self)
        return self


class QualityOracleSnapshot(_QualityOracleSnapshotContent):
    """Content-addressed scalar quality index with exact replay captures."""

    snapshot_id: Sha256Digest

    @model_validator(mode="after")
    def snapshot_hash_is_valid(self) -> Self:
        if self.snapshot_id != quality_oracle_snapshot_hash(self):
            raise ValueError("quality oracle snapshot hash mismatch")
        return self


def quality_oracle_policy_hash(policy: QualityOraclePolicy) -> str:
    return content_hash(policy)


def quality_oracle_snapshot_hash(snapshot: QualityOracleSnapshot) -> str:
    return content_hash(snapshot.model_dump(mode="json", exclude={"snapshot_id"}))


def _validate_policy_bundle_binding(
    policy: QualityOraclePolicy,
    bundle: QualityBundleSnapshot,
) -> None:
    if policy.quality_bundle_id != bundle.policy.bundle_id:
        raise ValueError("quality oracle binds a different quality bundle id")
    if policy.quality_bundle_version != bundle.policy.version:
        raise ValueError("quality oracle binds a different quality bundle version")
    bundle_components = {
        component.component_id: component for component in bundle.policy.components
    }
    policy_components = {component.component_id: component for component in policy.components}
    if set(policy_components) != set(bundle_components):
        raise ValueError("quality oracle components must exactly match every bundle component")
    for component_id, component in policy_components.items():
        binding = bundle_components[component_id]
        if component.workload != binding.workload or component.quality_axis != _component_axis(
            binding
        ):
            raise ValueError(
                f"quality oracle component {component_id!r} does not match bundle semantics"
            )


def _validate_source_times(
    sources: Iterable[SourceReference],
    *,
    generated_at: datetime,
    max_clock_skew: timedelta,
) -> None:
    if max_clock_skew < timedelta(0):
        raise ValueError("quality oracle future skew cannot be negative")
    deadline = generated_at + max_clock_skew
    for source in sources:
        if source.retrieved_at is None:  # pragma: no cover - canonical-source invariant
            raise ValueError(f"quality oracle source {source.id!r} requires retrieved_at")
        if source.retrieved_at.astimezone(UTC) > deadline:
            raise ValueError(f"quality oracle source {source.id!r} is future-dated")


def _component_sources_from_bundle(
    bundle: QualityBundleSnapshot,
    component_id: str,
) -> tuple[SourceReference, ...]:
    expected: tuple[SourceReference, ...] | None = None
    for candidate in bundle.candidates:
        coverage = next(item for item in candidate.components if item.component_id == component_id)
        if coverage.estimate is None:
            continue
        actual = _canonical_sources(coverage.estimate.sources)
        if expected is None:
            expected = actual
        elif actual != expected:
            raise ValueError(f"quality oracle component {component_id!r} exact sources mismatch")
    if expected is None:
        raise ValueError(f"quality oracle component {component_id!r} has no measured source")
    return expected


def _build_component_captures(
    policy: QualityOraclePolicy,
    bundle: QualityBundleSnapshot,
    *,
    generated_at: datetime,
    max_clock_skew: timedelta,
) -> tuple[QualityOracleComponentCapture, ...]:
    bundle_components = {
        component.component_id: component for component in bundle.policy.components
    }
    projection_hashes = _selected_quality_component_projection_hashes(bundle)
    captures: list[QualityOracleComponentCapture] = []
    source_by_id: dict[str, SourceReference] = {}
    for component in policy.components:
        sources = _component_sources_from_bundle(bundle, component.component_id)
        semantics = quality_oracle_source_semantics(sources)
        if semantics != component.source_semantics:
            raise ValueError(
                f"quality oracle component {component.component_id!r} source semantics mismatch"
            )
        _validate_source_times(
            sources,
            generated_at=generated_at,
            max_clock_skew=max_clock_skew,
        )
        for source in sources:
            existing = source_by_id.get(source.id)
            if existing is not None and existing != source:
                raise ValueError(f"source id {source.id!r} maps to different oracle captures")
            source_by_id[source.id] = source
        captures.append(
            QualityOracleComponentCapture(
                component_id=component.component_id,
                bundle_component=bundle_components[component.component_id],
                selected_quality_projection_sha256=projection_hashes[component.component_id],
                source_semantic_identity_sha256=quality_oracle_source_semantic_identity(sources),
                source_raw_identity_sha256=quality_oracle_source_raw_identity(sources),
                source_retrieval_identity_sha256=quality_oracle_source_retrieval_identity(sources),
                source_rights_identity_sha256=quality_oracle_source_rights_identity(sources),
                source_capture_identity_sha256=quality_oracle_source_capture_identity(sources),
                sources=sources,
            )
        )
    result = tuple(captures)
    _validate_raw_source_reuse(policy, result)
    return result


def _validate_raw_source_reuse(
    policy: QualityOraclePolicy,
    captures: tuple[QualityOracleComponentCapture, ...],
) -> None:
    groups_by_component = {
        component.component_id: component.correlation_group for component in policy.components
    }
    raw_owners: dict[str, set[str]] = {}
    for capture in captures:
        correlation_group = groups_by_component[capture.component_id]
        for source in capture.sources:
            if source.raw_sha256 is None:  # pragma: no cover - canonical-source invariant
                raise ValueError(f"quality oracle source {source.id!r} requires raw_sha256")
            raw_owners.setdefault(source.raw_sha256, set()).add(correlation_group)
    if any(len(groups) > 1 for groups in raw_owners.values()):
        raise ValueError("a reused raw source artifact must use one correlation group")


class _OutOfReferenceError(ValueError):
    """A valid measurement is outside the operator-declared scale."""


def _normalize_value(
    value: Decimal,
    component: QualityOracleComponent,
    *,
    label: str,
) -> Decimal:
    normalization = component.normalization
    if value < normalization.reference_min or value > normalization.reference_max:
        raise _OutOfReferenceError(
            f"quality oracle component {component.component_id!r} {label} is outside its "
            "normalization reference"
        )
    with localcontext(POLICY_DECIMAL_CONTEXT):
        span = normalization.reference_max - normalization.reference_min
        if component.quality_axis.goal is Goal.MAXIMIZE:
            normalized = (value - normalization.reference_min) / span
        else:
            normalized = (normalization.reference_max - value) / span
    return bounded_canonical_decimal(normalized)


def _measured_evaluation(
    component: QualityOracleComponent,
    capture: QualityOracleComponentCapture,
    estimate: AxisEstimate,
) -> QualityOracleComponentEvaluation:
    if estimate.unit != component.quality_axis.unit:
        raise ValueError(
            f"quality oracle component {component.component_id!r} estimate unit mismatch"
        )
    canonical_estimate_sources = _canonical_sources(estimate.sources)
    if canonical_estimate_sources != capture.sources:
        raise ValueError(
            f"quality oracle component {component.component_id!r} exact sources mismatch"
        )
    if quality_oracle_source_semantics(canonical_estimate_sources) != component.source_semantics:
        raise ValueError(
            f"quality oracle component {component.component_id!r} semantic sources mismatch"
        )
    normalized = _normalize_value(estimate.value, component, label="value")
    normalized_lower: Decimal | None = None
    normalized_upper: Decimal | None = None
    if estimate.lower is not None and estimate.upper is not None:
        first = _normalize_value(estimate.lower, component, label="lower bound")
        second = _normalize_value(estimate.upper, component, label="upper bound")
        normalized_lower, normalized_upper = min(first, second), max(first, second)
    with localcontext(POLICY_DECIMAL_CONTEXT):
        contribution = bounded_canonical_decimal(component.weight * normalized)
        weighted_lower = (
            bounded_canonical_decimal(component.weight * normalized_lower)
            if normalized_lower is not None
            else None
        )
        weighted_upper = (
            bounded_canonical_decimal(component.weight * normalized_upper)
            if normalized_upper is not None
            else None
        )
    return QualityOracleComponentEvaluation(
        component_id=component.component_id,
        frontier_snapshot_id=capture.bundle_component.frontier_snapshot_id,
        quality_metric=component.quality_axis.metric,
        source_semantic_identity_sha256=component.source_semantic_identity_sha256,
        source_capture_identity_sha256=capture.source_capture_identity_sha256,
        status=QualityOracleComponentStatus.MEASURED,
        raw_estimate=estimate,
        normalized_value=normalized,
        normalized_lower=normalized_lower,
        normalized_upper=normalized_upper,
        weighted_contribution=contribution,
        weighted_lower=weighted_lower,
        weighted_upper=weighted_upper,
    )


def _failed_evaluation(
    component: QualityOracleComponent,
    capture: QualityOracleComponentCapture,
    coverage: QualityComponentCoverage,
) -> QualityOracleComponentEvaluation:
    return QualityOracleComponentEvaluation(
        component_id=component.component_id,
        frontier_snapshot_id=capture.bundle_component.frontier_snapshot_id,
        quality_metric=component.quality_axis.metric,
        source_semantic_identity_sha256=component.source_semantic_identity_sha256,
        source_capture_identity_sha256=capture.source_capture_identity_sha256,
        status=QualityOracleComponentStatus(coverage.status.value),
        quarantine_reason_codes=coverage.quarantine_reason_codes,
    )


def _out_of_reference_evaluation(
    component: QualityOracleComponent,
    capture: QualityOracleComponentCapture,
    estimate: AxisEstimate,
) -> QualityOracleComponentEvaluation:
    return QualityOracleComponentEvaluation(
        component_id=component.component_id,
        frontier_snapshot_id=capture.bundle_component.frontier_snapshot_id,
        quality_metric=component.quality_axis.metric,
        source_semantic_identity_sha256=component.source_semantic_identity_sha256,
        source_capture_identity_sha256=capture.source_capture_identity_sha256,
        status=QualityOracleComponentStatus.OUT_OF_REFERENCE,
        raw_estimate=estimate,
        quarantine_reason_codes=("out_of_reference",),
    )


def _composite_estimate(
    policy: QualityOraclePolicy,
    components: tuple[QualityOracleComponentEvaluation, ...],
) -> AxisEstimate:
    if any(
        component.status is not QualityOracleComponentStatus.MEASURED for component in components
    ):
        raise ValueError("cannot aggregate an incomplete quality oracle candidate")
    contributions = [component.weighted_contribution for component in components]
    if any(value is None for value in contributions):  # pragma: no cover - model invariant
        raise ValueError("measured component has no weighted contribution")
    with localcontext(POLICY_DECIMAL_CONTEXT):
        value = bounded_canonical_decimal(
            sum((item for item in contributions if item is not None), Decimal(0))
        )

    source_by_id: dict[str, SourceReference] = {}
    raw_estimates: list[AxisEstimate] = []
    for component in components:
        if component.raw_estimate is None:  # pragma: no cover - model invariant
            raise ValueError("measured component has no raw estimate")
        raw_estimates.append(component.raw_estimate)
        for source in _canonical_sources(component.raw_estimate.sources):
            existing = source_by_id.get(source.id)
            if existing is not None and existing != source:
                raise ValueError(
                    f"source id {source.id!r} maps to different descriptors across components"
                )
            source_by_id[source.id] = source
    sources = tuple(source_by_id[source_id] for source_id in sorted(source_by_id))
    complete_timestamps = all(estimate.oldest_observed_at is not None for estimate in raw_estimates)
    return AxisEstimate(
        value=value,
        unit=policy.quality_unit,
        dependencies=tuple(
            f"components.{component.component_id}.{component.quality_axis.metric}"
            for component in policy.components
        ),
        source_ids=tuple(source.id for source in sources),
        sources=sources,
        oldest_observed_at=(
            min(
                estimate.oldest_observed_at
                for estimate in raw_estimates
                if estimate.oldest_observed_at is not None
            )
            if complete_timestamps
            else None
        ),
        # Bounds and sample counts from heterogeneous benchmarks do not have a
        # valid composite interpretation in v1alpha1. Component values retain
        # the complete original fields in the snapshot.
        lower=None,
        upper=None,
        minimum_sample_count=None,
    )


def _build_candidate(
    policy: QualityOraclePolicy,
    captures: Mapping[str, QualityOracleComponentCapture],
    candidate: QualityBundleCandidateCoverage,
) -> QualityOracleCandidate:
    coverage_by_id = {component.component_id: component for component in candidate.components}
    evaluations: list[QualityOracleComponentEvaluation] = []
    for component in policy.components:
        coverage = coverage_by_id[component.component_id]
        capture = captures[component.component_id]
        if coverage.status is QualityCoverageStatus.MEASURED:
            if coverage.estimate is None:  # pragma: no cover - bundle model invariant
                raise ValueError("measured quality bundle component has no estimate")
            try:
                evaluations.append(_measured_evaluation(component, capture, coverage.estimate))
            except _OutOfReferenceError:
                evaluations.append(
                    _out_of_reference_evaluation(component, capture, coverage.estimate)
                )
        else:
            evaluations.append(_failed_evaluation(component, capture, coverage))
    values = tuple(evaluations)
    failed = tuple(
        component.component_id
        for component in values
        if component.status is not QualityOracleComponentStatus.MEASURED
    )
    return QualityOracleCandidate(
        offering=candidate.offering,
        status="rejected" if failed else "scored",
        components=values,
        failed_component_ids=failed,
        estimate=None if failed else _composite_estimate(policy, values),
    )


def build_quality_oracle_snapshot(
    policy: QualityOraclePolicy,
    quality_bundle: QualityBundleSnapshot,
    *,
    generated_at: datetime,
    max_clock_skew: timedelta = timedelta(minutes=5),
) -> QualityOracleSnapshot:
    """Build a deterministic scalar index from an already reconciled bundle."""

    validated_policy = QualityOraclePolicy.model_validate(policy.model_dump(mode="json"))
    validated_bundle = QualityBundleSnapshot.model_validate(quality_bundle.model_dump(mode="json"))
    if generated_at.tzinfo is None:
        raise ValueError("quality oracle generation time must include a timezone")
    if max_clock_skew < timedelta(0):
        raise ValueError("quality oracle future skew cannot be negative")
    generated_at = generated_at.astimezone(UTC)
    if generated_at < validated_bundle.generated_at:
        raise ValueError("quality oracle cannot predate its quality bundle")
    if generated_at >= validated_bundle.valid_until:
        raise ValueError("quality bundle is stale at quality oracle generation")
    _validate_policy_bundle_binding(validated_policy, validated_bundle)
    component_captures = _build_component_captures(
        validated_policy,
        validated_bundle,
        generated_at=generated_at,
        max_clock_skew=max_clock_skew,
    )
    captures_by_id = {capture.component_id: capture for capture in component_captures}
    candidates = tuple(
        _build_candidate(validated_policy, captures_by_id, candidate)
        for candidate in validated_bundle.candidates
    )
    if not any(candidate.status == "scored" for candidate in candidates):
        raise ValueError("quality oracle has no candidate with all required benchmark signals")
    content = _QualityOracleSnapshotContent(
        policy_hash=quality_oracle_policy_hash(validated_policy),
        policy=validated_policy,
        quality_bundle_policy_hash=validated_bundle.policy_hash,
        quality_bundle_snapshot_id=validated_bundle.snapshot_id,
        quality_bundle_snapshot_hash=validated_bundle.snapshot_id,
        quality_bundle_generated_at=validated_bundle.generated_at,
        quality_bundle_valid_until=validated_bundle.valid_until,
        quality_bundle_candidate_universe_hash=validated_bundle.candidate_universe_hash,
        selected_quality_projection_sha256=(
            quality_oracle_selected_quality_projection_hash(validated_bundle)
        ),
        component_captures=component_captures,
        generated_at=generated_at,
        valid_until=validated_bundle.valid_until,
        candidate_universe_hash=validated_bundle.candidate_universe_hash,
        candidates=candidates,
    )
    return QualityOracleSnapshot(
        snapshot_id=content_hash(content),
        **content.model_dump(),
    )


def verify_quality_oracle_snapshot(
    policy: QualityOraclePolicy,
    quality_bundle: QualityBundleSnapshot,
    snapshot: QualityOracleSnapshot,
    *,
    now: datetime,
    max_clock_skew: timedelta = timedelta(minutes=5),
) -> None:
    """Replay a scalar oracle snapshot against trusted policy and exact bundle."""

    if now.tzinfo is None:
        raise ValueError("quality oracle verification time must include a timezone")
    if max_clock_skew < timedelta(0):
        raise ValueError("quality oracle future skew cannot be negative")
    now = now.astimezone(UTC)
    validated = QualityOracleSnapshot.model_validate(snapshot.model_dump(mode="json"))
    if validated.generated_at > now + max_clock_skew:
        raise ValueError("quality oracle snapshot is future-dated")
    if now >= validated.valid_until:
        raise ValueError("quality oracle snapshot has expired")
    expected = build_quality_oracle_snapshot(
        policy,
        quality_bundle,
        generated_at=validated.generated_at,
        max_clock_skew=max_clock_skew,
    )
    if validated != expected:
        raise ValueError("quality oracle snapshot does not match its bound bundle")


def quality_oracle_axis(policy: QualityOraclePolicy) -> AxisDescriptor:
    """Return the quality side of a cost/quality frontier definition."""

    validated = QualityOraclePolicy.model_validate(policy.model_dump(mode="json"))
    return AxisDescriptor(
        metric=validated.quality_metric,
        goal=Goal.MAXIMIZE,
        unit=validated.quality_unit,
    )


def _component_provenance(snapshot: QualityOracleSnapshot) -> list[dict[str, object]]:
    captures = {capture.component_id: capture for capture in snapshot.component_captures}
    return [
        {
            "component_id": component.component_id,
            "workload": component.workload.model_dump(mode="json"),
            "quality_axis": component.quality_axis.model_dump(mode="json"),
            "source_semantics": [
                source.model_dump(mode="json") for source in component.source_semantics
            ],
            "source_semantic_identity_sha256": component.source_semantic_identity_sha256,
            "normalization_reference": component.normalization.model_dump(mode="json"),
            "weight": format(component.weight, "f"),
            "correlation_group": component.correlation_group,
            "bundle_component": captures[component.component_id].bundle_component.model_dump(
                mode="json"
            ),
            "selected_quality_projection_sha256": captures[
                component.component_id
            ].selected_quality_projection_sha256,
            "source_raw_identity_sha256": captures[
                component.component_id
            ].source_raw_identity_sha256,
            "source_retrieval_identity_sha256": captures[
                component.component_id
            ].source_retrieval_identity_sha256,
            "source_rights_identity_sha256": captures[
                component.component_id
            ].source_rights_identity_sha256,
            "source_capture_identity_sha256": captures[
                component.component_id
            ].source_capture_identity_sha256,
            "sources": [
                source.model_dump(mode="json")
                for source in captures[component.component_id].sources
            ],
            "source_rights": [
                {
                    "id": source.id,
                    "license": source.license or "NOASSERTION",
                    "terms_url": str(source.terms_url) if source.terms_url is not None else None,
                }
                for source in captures[component.component_id].sources
            ],
        }
        for component in snapshot.policy.components
    ]


def _quality_oracle_catalog_from_verified(
    snapshot: QualityOracleSnapshot,
) -> ObservationCatalog:
    policy = snapshot.policy
    derived_source = SourceReference(
        id=f"quality-oracle:{policy.oracle_id}",
        version=f"{policy.version}/snapshot:{snapshot.snapshot_id}",
        license="NOASSERTION",
        methodology=(
            "Operator-authored weighted fixed-reference quality index. All two-to-four "
            "components are required; heterogeneous component bounds and sample counts are "
            "not aggregated; no statistical independence is assumed. Derived artifact "
            f"sha256:{snapshot.snapshot_id}; valid until {snapshot.valid_until.isoformat()}. "
            "Redistribution requires separate review of every embedded component source."
        ),
        raw_sha256=snapshot.snapshot_id,
        retrieved_at=snapshot.generated_at,
    )
    component_provenance = _component_provenance(snapshot)
    offerings: list[OfferingObservation] = []
    for candidate in snapshot.candidates:
        if candidate.status != "scored" or candidate.estimate is None:
            continue
        offerings.append(
            OfferingObservation(
                offering=candidate.offering,
                signals={
                    policy.quality_metric: Observation(
                        value=candidate.estimate.value,
                        unit=candidate.estimate.unit,
                        lower=None,
                        upper=None,
                        sample_count=None,
                        observed_at=candidate.estimate.oldest_observed_at,
                        source=derived_source,
                    )
                },
                metadata={
                    "quality_oracle_snapshot_id": snapshot.snapshot_id,
                    "quality_oracle_policy_hash": snapshot.policy_hash,
                    "quality_oracle_valid_until": snapshot.valid_until.isoformat(),
                    "quality_bundle_snapshot_id": snapshot.quality_bundle_snapshot_id,
                    "quality_bundle_policy_hash": snapshot.quality_bundle_policy_hash,
                    "selected_quality_projection_sha256": (
                        snapshot.selected_quality_projection_sha256
                    ),
                    "publication_safe": False,
                    "statistical_independence_assumed": False,
                    "component_provenance": component_provenance,
                    "component_values": [
                        {
                            "component_id": component.component_id,
                            "source_semantic_identity_sha256": (
                                component.source_semantic_identity_sha256
                            ),
                            "source_capture_identity_sha256": (
                                component.source_capture_identity_sha256
                            ),
                            "normalized_value": format(component.normalized_value, "f"),
                            "weighted_contribution": format(
                                component.weighted_contribution,
                                "f",
                            ),
                            "raw_lower": (
                                format(component.raw_estimate.lower, "f")
                                if component.raw_estimate is not None
                                and component.raw_estimate.lower is not None
                                else None
                            ),
                            "raw_upper": (
                                format(component.raw_estimate.upper, "f")
                                if component.raw_estimate is not None
                                and component.raw_estimate.upper is not None
                                else None
                            ),
                            "raw_sample_count": (
                                component.raw_estimate.minimum_sample_count
                                if component.raw_estimate is not None
                                else None
                            ),
                        }
                        for component in candidate.components
                        if component.normalized_value is not None
                        and component.weighted_contribution is not None
                    ],
                },
                default_source=derived_source,
            )
        )
    offerings.sort(key=lambda item: _offering_identity(item.offering))
    return ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=policy.composite_workload,
        offerings=offerings,
    )


def quality_oracle_catalog(
    policy: QualityOraclePolicy,
    quality_bundle: QualityBundleSnapshot,
    snapshot: QualityOracleSnapshot,
    *,
    now: datetime,
    max_clock_skew: timedelta = timedelta(minutes=5),
) -> ObservationCatalog:
    """Replay-verify trusted inputs, then project scored quality observations."""

    verify_quality_oracle_snapshot(
        policy,
        quality_bundle,
        snapshot,
        now=now,
        max_clock_skew=max_clock_skew,
    )
    validated = QualityOracleSnapshot.model_validate(snapshot.model_dump(mode="json"))
    return _quality_oracle_catalog_from_verified(validated)


def enrich_catalog_with_quality_oracle(
    catalog: ObservationCatalog,
    policy: QualityOraclePolicy,
    quality_bundle: QualityBundleSnapshot,
    snapshot: QualityOracleSnapshot,
    *,
    now: datetime,
    max_clock_skew: timedelta = timedelta(minutes=5),
) -> ObservationCatalog:
    """Replay-verify and attach quality to an exact composite-workload catalog."""

    verify_quality_oracle_snapshot(
        policy,
        quality_bundle,
        snapshot,
        now=now,
        max_clock_skew=max_clock_skew,
    )
    validated_catalog = ObservationCatalog.model_validate(catalog.model_dump(mode="json"))
    validated_snapshot = QualityOracleSnapshot.model_validate(snapshot.model_dump(mode="json"))
    validated_policy = validated_snapshot.policy
    if validated_catalog.workload != validated_policy.composite_workload:
        raise ValueError("catalog workload does not match the quality oracle composite workload")
    if any(
        validated_policy.quality_metric in offering.signals
        for offering in validated_catalog.offerings
    ):
        raise ValueError("catalog already contains the quality oracle metric")

    candidates_by_identity = {
        _offering_identity(candidate.offering): candidate
        for candidate in validated_snapshot.candidates
    }
    catalog_by_identity = {
        _offering_identity(offering.offering): offering for offering in validated_catalog.offerings
    }
    if set(candidates_by_identity) != set(catalog_by_identity):
        candidates_by_id = {
            candidate.offering.offering_id: candidate.offering
            for candidate in validated_snapshot.candidates
        }
        for offering in validated_catalog.offerings:
            expected = candidates_by_id.get(offering.offering.offering_id)
            if expected is not None and expected != offering.offering:
                raise ValueError(
                    "catalog offering_id matches an oracle candidate but its complete "
                    "OfferingKey differs"
                )
        raise ValueError("catalog and quality oracle candidate universes must match exactly")

    score_catalog = _quality_oracle_catalog_from_verified(validated_snapshot)
    scores_by_identity = {
        _offering_identity(offering.offering): offering for offering in score_catalog.offerings
    }
    global_metadata = dict(score_catalog.offerings[0].metadata)
    global_metadata.pop("component_values", None)
    reserved_metadata = {
        *global_metadata,
        "component_values",
        "quality_oracle_candidate_status",
        "quality_oracle_failed_component_ids",
        "quality_oracle_component_statuses",
    }
    enriched: list[OfferingObservation] = []
    for identity, base in catalog_by_identity.items():
        candidate = candidates_by_identity[identity]
        conflicting_metadata = sorted(
            (set(base.metadata) & reserved_metadata) - {"publication_safe"}
        )
        if conflicting_metadata:
            raise ValueError(
                f"catalog metadata key {conflicting_metadata[0]!r} is reserved by the "
                "quality oracle projection"
            )
        if base.metadata.get("publication_safe") not in {None, False}:
            raise ValueError(
                "catalog metadata cannot claim publication_safe before oracle enrichment"
            )
        metadata = dict(base.metadata)
        metadata.update(global_metadata)
        metadata["quality_oracle_candidate_status"] = candidate.status
        metadata["quality_oracle_failed_component_ids"] = list(candidate.failed_component_ids)
        metadata["quality_oracle_component_statuses"] = [
            {
                "component_id": component.component_id,
                "status": component.status.value,
                "reason_codes": list(component.quarantine_reason_codes),
            }
            for component in candidate.components
        ]
        metadata["component_values"] = [
            {
                "component_id": component.component_id,
                "source_semantic_identity_sha256": component.source_semantic_identity_sha256,
                "source_capture_identity_sha256": component.source_capture_identity_sha256,
                "normalized_value": format(component.normalized_value, "f"),
                "weighted_contribution": format(component.weighted_contribution, "f"),
                "raw_lower": (
                    format(component.raw_estimate.lower, "f")
                    if component.raw_estimate is not None
                    and component.raw_estimate.lower is not None
                    else None
                ),
                "raw_upper": (
                    format(component.raw_estimate.upper, "f")
                    if component.raw_estimate is not None
                    and component.raw_estimate.upper is not None
                    else None
                ),
                "raw_sample_count": (
                    component.raw_estimate.minimum_sample_count
                    if component.raw_estimate is not None
                    else None
                ),
            }
            for component in candidate.components
            if component.normalized_value is not None
            and component.weighted_contribution is not None
        ]
        signals = dict(base.signals)
        score = scores_by_identity.get(identity)
        if score is not None:
            signals[validated_policy.quality_metric] = score.signals[
                validated_policy.quality_metric
            ]
        enriched.append(
            OfferingObservation(
                offering=base.offering,
                signals=signals,
                metadata=metadata,
                default_source=base.default_source,
            )
        )
    enriched.sort(key=lambda item: _offering_identity(item.offering))
    return ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=validated_catalog.workload,
        offerings=enriched,
    )
