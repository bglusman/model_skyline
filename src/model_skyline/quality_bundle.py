from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from model_skyline.canonical import canonical_bytes, content_hash
from model_skyline.engine import frontier_hash_matches
from model_skyline.models import (
    MAX_SELECTION_CANDIDATES,
    AxisDescriptor,
    AxisEstimate,
    FrontierSnapshot,
    FrozenModel,
    OfferingKey,
    PortablePublicationId,
    SafeCount,
    Sha256Digest,
    SnapshotTtlSeconds,
    WorkloadReference,
)
from model_skyline.quality_evidence import MAX_QUALITY_ARTIFACT_BYTES
from model_skyline.selection_overlap import MAX_OFFERING_IDENTITY_BYTES

MAX_QUALITY_BUNDLE_COMPONENTS = 4
MIN_QUALITY_BUNDLE_COMPONENTS = 2
QUALITY_BUNDLE_SCHEMA_VERSION: Literal["model-skyline/quality-bundle-policy/v1alpha1"] = (
    "model-skyline/quality-bundle-policy/v1alpha1"
)
QUALITY_BUNDLE_SNAPSHOT_SCHEMA_VERSION: Literal[
    "model-skyline/quality-bundle-snapshot/v1alpha1"
] = "model-skyline/quality-bundle-snapshot/v1alpha1"

ComponentId = Annotated[str, Field(min_length=1, max_length=128)]
MetricId = Annotated[str, Field(min_length=1, max_length=256)]
QuarantineReasonCode = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$"),
]


def _offering_identity(offering: OfferingKey) -> bytes:
    """Match every OfferingKey field exactly, as ADR 0002 requires."""

    return canonical_bytes(offering)


def _canonical_reason_codes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("quarantine reasons must be a non-empty array")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("quarantine reasons must contain only strings")
    if len(value) != len(set(value)):
        raise ValueError("quarantine reasons must not contain duplicates")
    return tuple(sorted(value))


def _require_quality_bundle_artifact_bound(artifact: FrozenModel) -> None:
    """Keep canonical and supported pretty-JSON forms inside the input cap."""

    if len(canonical_bytes(artifact.model_dump(mode="json"))) > MAX_QUALITY_ARTIFACT_BYTES:
        raise ValueError(
            f"quality bundle artifact exceeds {MAX_QUALITY_ARTIFACT_BYTES} canonical bytes"
        )
    if len(artifact.model_dump_json(indent=2).encode("utf-8")) + 1 > (MAX_QUALITY_ARTIFACT_BYTES):
        raise ValueError(
            f"quality bundle artifact exceeds {MAX_QUALITY_ARTIFACT_BYTES} serialized bytes"
        )


class QualityBundleComponent(FrozenModel):
    """One exact frontier declared as a distinct quality component by policy."""

    component_id: ComponentId
    frontier_id: str = Field(min_length=1, max_length=256)
    frontier_snapshot_id: Sha256Digest
    frontier_snapshot_hash: Sha256Digest
    config_hash: Sha256Digest
    catalog_hash: Sha256Digest
    workload: WorkloadReference
    axes: tuple[AxisDescriptor, AxisDescriptor]
    quality_metric: MetricId
    max_age_seconds: SnapshotTtlSeconds
    evidence_valid_until: datetime | None = None

    @field_validator("evidence_valid_until")
    @classmethod
    def evidence_deadline_is_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("component evidence_valid_until must include a timezone")
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def binding_is_coherent(self) -> Self:
        if self.frontier_snapshot_id != self.frontier_snapshot_hash:
            raise ValueError("component frontier snapshot id and hash must match")
        metric_ids = tuple(axis.metric for axis in self.axes)
        if len(set(metric_ids)) != 2:
            raise ValueError("component axes must identify two distinct metrics")
        if self.quality_metric not in metric_ids:
            raise ValueError("component quality_metric must identify one bound frontier axis")
        return self


class QualityBundlePolicy(FrozenModel):
    """Hard coverage policy over two to four separate quality frontiers."""

    schema_version: Literal["model-skyline/quality-bundle-policy/v1alpha1"] = (
        QUALITY_BUNDLE_SCHEMA_VERSION
    )
    kind: Literal["quality-bundle-policy"] = "quality-bundle-policy"
    bundle_id: PortablePublicationId
    version: str = Field(min_length=1, max_length=128)
    strategy: Literal["separate-frontiers"] = "separate-frontiers"
    components: tuple[QualityBundleComponent, ...] = Field(
        min_length=MIN_QUALITY_BUNDLE_COMPONENTS,
        max_length=MAX_QUALITY_BUNDLE_COMPONENTS,
    )
    required_component_ids: tuple[ComponentId, ...] = Field(min_length=1, max_length=4)
    minimum_measured_components: Annotated[int, Field(strict=True, ge=1, le=4)]

    @field_validator("components")
    @classmethod
    def components_are_canonical(
        cls,
        value: tuple[QualityBundleComponent, ...],
    ) -> tuple[QualityBundleComponent, ...]:
        # Separate-frontier coverage is a set policy. Component order carries
        # no priority or aggregation meaning, so it must not perturb hashes.
        return tuple(sorted(value, key=lambda component: component.component_id))

    @field_validator("required_component_ids")
    @classmethod
    def required_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(value))

    @model_validator(mode="after")
    def component_policy_is_coherent(self) -> Self:
        component_ids = tuple(component.component_id for component in self.components)
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("quality bundle component ids must be unique")
        frontier_ids = tuple(component.frontier_id for component in self.components)
        if len(frontier_ids) != len(set(frontier_ids)):
            raise ValueError("quality bundle frontier ids must be unique")
        snapshot_ids = tuple(component.frontier_snapshot_id for component in self.components)
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("quality bundle frontier snapshots must be unique")
        required = self.required_component_ids
        if len(required) != len(set(required)):
            raise ValueError("required quality component ids must be unique")
        unknown = sorted(set(required) - set(component_ids))
        if unknown:
            raise ValueError(
                f"required quality component {unknown[0]!r} is not declared in the bundle"
            )
        if self.minimum_measured_components > len(self.components):
            raise ValueError("minimum measured components exceeds the bundle component count")
        if self.minimum_measured_components < len(required):
            raise ValueError(
                "minimum measured components cannot be smaller than the required component set"
            )
        _require_quality_bundle_artifact_bound(self)
        return self


class QualityCoverageStatus(StrEnum):
    MEASURED = "measured"
    MISSING = "missing"
    QUARANTINED = "quarantined"


class QualityBundleQuarantine(FrozenModel):
    """Candidate-scoped quarantine supplied by an evidence reconciliation layer."""

    offering: OfferingKey
    reason_codes: tuple[QuarantineReasonCode, ...] = Field(min_length=1, max_length=32)

    @field_validator("reason_codes", mode="before")
    @classmethod
    def reasons_are_canonical(cls, value: Any) -> tuple[str, ...]:
        return _canonical_reason_codes(value)


class QualityComponentCoverage(FrozenModel):
    component_id: ComponentId
    frontier_snapshot_id: Sha256Digest
    quality_metric: MetricId
    status: QualityCoverageStatus
    estimate: AxisEstimate | None = None
    quarantine_reason_codes: tuple[QuarantineReasonCode, ...] = Field(
        default=(),
        max_length=32,
    )

    @field_validator("quarantine_reason_codes", mode="before")
    @classmethod
    def reasons_are_canonical(cls, value: Any) -> tuple[str, ...]:
        if value in ((), []):
            return ()
        return _canonical_reason_codes(value)

    @model_validator(mode="after")
    def status_is_coherent(self) -> Self:
        if self.status is QualityCoverageStatus.MEASURED:
            if self.estimate is None:
                raise ValueError("measured component coverage requires an estimate")
            if self.quarantine_reason_codes:
                raise ValueError("measured component coverage cannot have quarantine reasons")
        elif self.status is QualityCoverageStatus.QUARANTINED:
            if self.estimate is not None:
                raise ValueError("quarantined component coverage cannot have an estimate")
            if not self.quarantine_reason_codes:
                raise ValueError("quarantined component coverage requires reason codes")
        elif self.estimate is not None or self.quarantine_reason_codes:
            raise ValueError("missing component coverage cannot carry evidence")
        return self


class QualityBundleCandidateCoverage(FrozenModel):
    offering: OfferingKey
    components: tuple[QualityComponentCoverage, ...] = Field(
        min_length=MIN_QUALITY_BUNDLE_COMPONENTS,
        max_length=MAX_QUALITY_BUNDLE_COMPONENTS,
    )
    measured_component_count: SafeCount
    missing_component_ids: tuple[ComponentId, ...] = Field(default=(), max_length=4)
    quarantined_component_ids: tuple[ComponentId, ...] = Field(default=(), max_length=4)
    failed_required_component_ids: tuple[ComponentId, ...] = Field(default=(), max_length=4)
    eligible: bool

    @model_validator(mode="after")
    def local_counts_are_coherent(self) -> Self:
        component_ids = tuple(component.component_id for component in self.components)
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("candidate coverage component ids must be unique")
        measured = sum(
            component.status is QualityCoverageStatus.MEASURED for component in self.components
        )
        missing = tuple(
            component.component_id
            for component in self.components
            if component.status is QualityCoverageStatus.MISSING
        )
        quarantined = tuple(
            component.component_id
            for component in self.components
            if component.status is QualityCoverageStatus.QUARANTINED
        )
        if self.measured_component_count != measured:
            raise ValueError("candidate measured component count does not match its evidence")
        if self.missing_component_ids != missing:
            raise ValueError("candidate missing component ids do not match its evidence")
        if self.quarantined_component_ids != quarantined:
            raise ValueError("candidate quarantined component ids do not match its evidence")
        if len(self.failed_required_component_ids) != len(set(self.failed_required_component_ids)):
            raise ValueError("candidate failed required component ids must be unique")
        if not set(self.failed_required_component_ids) <= set(component_ids):
            raise ValueError("candidate failed required component ids must name bundle components")
        return self


class _QualityBundleSnapshotContent(FrozenModel):
    schema_version: Literal["model-skyline/quality-bundle-snapshot/v1alpha1"] = (
        QUALITY_BUNDLE_SNAPSHOT_SCHEMA_VERSION
    )
    kind: Literal["quality-bundle-snapshot"] = "quality-bundle-snapshot"
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    policy_hash: Sha256Digest
    generated_at: datetime
    valid_until: datetime
    policy: QualityBundlePolicy
    candidate_universe_hash: Sha256Digest
    candidates: tuple[QualityBundleCandidateCoverage, ...] = Field(
        min_length=1,
        max_length=MAX_SELECTION_CANDIDATES,
    )

    @field_validator("generated_at", "valid_until")
    @classmethod
    def timestamps_have_timezones(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("quality bundle timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def content_is_coherent(self) -> Self:
        if self.valid_until <= self.generated_at:
            raise ValueError("quality bundle valid_until must follow generated_at")
        if self.policy_hash != quality_bundle_policy_hash(self.policy):
            raise ValueError("quality bundle policy hash mismatch")
        policy_component_ids = tuple(component.component_id for component in self.policy.components)
        required = set(self.policy.required_component_ids)
        identities = [_offering_identity(candidate.offering) for candidate in self.candidates]
        if any(len(identity) > MAX_OFFERING_IDENTITY_BYTES for identity in identities):
            raise ValueError("quality bundle OfferingKey identity exceeds the byte limit")
        if len(identities) != len(set(identities)):
            raise ValueError("quality bundle candidates must have distinct OfferingKeys")
        if identities != sorted(identities):
            raise ValueError("quality bundle candidates must be canonically ordered")
        expected_universe_hash = content_hash(
            [candidate.offering.model_dump(mode="json") for candidate in self.candidates]
        )
        if self.candidate_universe_hash != expected_universe_hash:
            raise ValueError("quality bundle candidate universe hash mismatch")
        component_by_id = {
            component.component_id: component for component in self.policy.components
        }
        for candidate in self.candidates:
            if tuple(item.component_id for item in candidate.components) != policy_component_ids:
                raise ValueError("candidate coverage must follow bundle component order")
            for evidence in candidate.components:
                binding = component_by_id[evidence.component_id]
                if evidence.frontier_snapshot_id != binding.frontier_snapshot_id:
                    raise ValueError("candidate coverage frontier snapshot mismatch")
                if evidence.quality_metric != binding.quality_metric:
                    raise ValueError("candidate coverage quality metric mismatch")
            failed_required = tuple(
                evidence.component_id
                for evidence in candidate.components
                if evidence.component_id in required
                and evidence.status is not QualityCoverageStatus.MEASURED
            )
            if candidate.failed_required_component_ids != failed_required:
                raise ValueError("candidate required-component failures do not match evidence")
            expected_eligibility = (
                not failed_required
                and candidate.measured_component_count >= self.policy.minimum_measured_components
            )
            if candidate.eligible != expected_eligibility:
                raise ValueError("candidate eligibility does not match hard coverage policy")
        _require_quality_bundle_artifact_bound(self)
        return self


class QualityBundleSnapshot(_QualityBundleSnapshotContent):
    """Immutable coverage matrix over exact component frontier snapshots."""

    snapshot_id: Sha256Digest

    @model_validator(mode="after")
    def snapshot_hash_is_valid(self) -> Self:
        if self.snapshot_id != quality_bundle_snapshot_hash(self):
            raise ValueError("quality bundle snapshot hash mismatch")
        return self


def quality_bundle_policy_hash(policy: QualityBundlePolicy) -> str:
    return content_hash(policy)


def quality_bundle_snapshot_hash(snapshot: QualityBundleSnapshot) -> str:
    return content_hash(snapshot.model_dump(mode="json", exclude={"snapshot_id"}))


def _validated_component_frontiers(
    policy: QualityBundlePolicy,
    component_frontiers: Mapping[str, FrontierSnapshot],
    *,
    generated_at: datetime,
) -> tuple[dict[str, dict[bytes, AxisEstimate]], datetime]:
    expected_ids = {component.component_id for component in policy.components}
    if set(component_frontiers) != expected_ids:
        raise ValueError("component frontier inputs must exactly match bundle component ids")
    measurements: dict[str, dict[bytes, AxisEstimate]] = {}
    freshness_deadlines: list[datetime] = []
    for component in policy.components:
        supplied_frontier = component_frontiers[component.component_id]
        # model_copy/model_construct bypass Pydantic validation. Detach and
        # revalidate before any dictionary projection could collapse duplicate
        # or otherwise incoherent evaluated offerings.
        frontier = FrontierSnapshot.model_validate(supplied_frontier.model_dump(mode="json"))
        if not frontier_hash_matches(frontier):
            raise ValueError(f"quality component {component.component_id!r} frontier hash mismatch")
        if frontier.frontier_id != component.frontier_id:
            raise ValueError(
                f"quality component {component.component_id!r} frontier identity mismatch"
            )
        if frontier.snapshot_id != component.frontier_snapshot_id:
            raise ValueError(
                f"quality component {component.component_id!r} frontier snapshot mismatch"
            )
        if frontier.snapshot_id != component.frontier_snapshot_hash:
            raise ValueError(
                f"quality component {component.component_id!r} frontier content hash mismatch"
            )
        if frontier.config_hash != component.config_hash:
            raise ValueError(f"quality component {component.component_id!r} config hash mismatch")
        if frontier.catalog_hash != component.catalog_hash:
            raise ValueError(f"quality component {component.component_id!r} catalog hash mismatch")
        if frontier.workload != component.workload:
            raise ValueError(f"quality component {component.component_id!r} workload mismatch")
        if frontier.axes != component.axes:
            raise ValueError(f"quality component {component.component_id!r} metric axes mismatch")
        if component.quality_metric not in {axis.metric for axis in frontier.axes}:
            raise ValueError(
                f"quality component {component.component_id!r} quality metric mismatch"
            )
        if frontier.generated_at > generated_at:
            raise ValueError(f"quality component {component.component_id!r} is future-dated")
        frontier_generated_at = frontier.generated_at.astimezone(UTC)
        deadline = frontier_generated_at + timedelta(seconds=component.max_age_seconds)
        if component.evidence_valid_until is not None:
            if component.evidence_valid_until <= frontier_generated_at:
                raise ValueError(
                    f"quality component {component.component_id!r} evidence validity does not "
                    "follow frontier generation"
                )
            deadline = min(deadline, component.evidence_valid_until)
        if generated_at >= deadline:
            raise ValueError(f"quality component {component.component_id!r} is stale")
        freshness_deadlines.append(deadline)
        measurements[component.component_id] = {
            _offering_identity(item.offering): item.axes[component.quality_metric]
            for item in frontier.evaluated
        }
    return measurements, min(freshness_deadlines)


def _validated_candidates(candidates: Iterable[OfferingKey]) -> tuple[OfferingKey, ...]:
    values = tuple(candidates)
    if not values:
        raise ValueError("quality bundle candidate universe cannot be empty")
    if len(values) > MAX_SELECTION_CANDIDATES:
        raise ValueError("quality bundle candidate universe exceeds the candidate limit")
    identities = [_offering_identity(offering) for offering in values]
    if any(len(identity) > MAX_OFFERING_IDENTITY_BYTES for identity in identities):
        raise ValueError("quality bundle OfferingKey identity exceeds the byte limit")
    if len(identities) != len(set(identities)):
        raise ValueError("quality bundle candidates must have distinct complete OfferingKeys")
    return tuple(offering for _, offering in sorted(zip(identities, values, strict=True)))


def _validated_quarantines(
    policy: QualityBundlePolicy,
    candidates: tuple[OfferingKey, ...],
    quarantines: Mapping[str, Iterable[QualityBundleQuarantine]] | None,
) -> dict[str, dict[bytes, tuple[str, ...]]]:
    supplied = quarantines or {}
    known_component_ids = {component.component_id for component in policy.components}
    unknown_components = sorted(set(supplied) - known_component_ids)
    if unknown_components:
        raise ValueError(
            f"quarantine references unknown quality component {unknown_components[0]!r}"
        )
    candidate_identities = {_offering_identity(offering) for offering in candidates}
    result: dict[str, dict[bytes, tuple[str, ...]]] = {}
    for component in policy.components:
        component_quarantines: dict[bytes, tuple[str, ...]] = {}
        for quarantine in supplied.get(component.component_id, ()):
            identity = _offering_identity(quarantine.offering)
            if identity not in candidate_identities:
                raise ValueError("quarantine OfferingKey is outside the candidate universe")
            if identity in component_quarantines:
                raise ValueError("a candidate cannot be quarantined twice in one component")
            component_quarantines[identity] = quarantine.reason_codes
        result[component.component_id] = component_quarantines
    return result


def build_quality_bundle_snapshot(
    policy: QualityBundlePolicy,
    component_frontiers: Mapping[str, FrontierSnapshot],
    candidates: Iterable[OfferingKey],
    *,
    generated_at: datetime,
    quarantines: Mapping[str, Iterable[QualityBundleQuarantine]] | None = None,
) -> QualityBundleSnapshot:
    """Build a deterministic hard-coverage matrix without aggregating scores."""

    if generated_at.tzinfo is None:
        raise ValueError("quality bundle generation time must include a timezone")
    generated_at = generated_at.astimezone(UTC)
    canonical_candidates = _validated_candidates(candidates)
    measurements, valid_until = _validated_component_frontiers(
        policy,
        component_frontiers,
        generated_at=generated_at,
    )
    quarantine_maps = _validated_quarantines(policy, canonical_candidates, quarantines)
    required = set(policy.required_component_ids)
    coverage: list[QualityBundleCandidateCoverage] = []
    for offering in canonical_candidates:
        identity = _offering_identity(offering)
        component_coverage: list[QualityComponentCoverage] = []
        for component in policy.components:
            estimate = measurements[component.component_id].get(identity)
            quarantine_reasons = quarantine_maps[component.component_id].get(identity)
            if estimate is not None and quarantine_reasons is not None:
                raise ValueError("a candidate cannot be both measured and quarantined")
            if estimate is not None:
                status = QualityCoverageStatus.MEASURED
                reasons: tuple[str, ...] = ()
            elif quarantine_reasons is not None:
                status = QualityCoverageStatus.QUARANTINED
                reasons = quarantine_reasons
            else:
                status = QualityCoverageStatus.MISSING
                reasons = ()
            component_coverage.append(
                QualityComponentCoverage(
                    component_id=component.component_id,
                    frontier_snapshot_id=component.frontier_snapshot_id,
                    quality_metric=component.quality_metric,
                    status=status,
                    estimate=estimate,
                    quarantine_reason_codes=reasons,
                )
            )
        measured_count = sum(
            item.status is QualityCoverageStatus.MEASURED for item in component_coverage
        )
        missing_ids = tuple(
            item.component_id
            for item in component_coverage
            if item.status is QualityCoverageStatus.MISSING
        )
        quarantined_ids = tuple(
            item.component_id
            for item in component_coverage
            if item.status is QualityCoverageStatus.QUARANTINED
        )
        failed_required_ids = tuple(
            item.component_id
            for item in component_coverage
            if item.component_id in required and item.status is not QualityCoverageStatus.MEASURED
        )
        eligible = not failed_required_ids and measured_count >= policy.minimum_measured_components
        coverage.append(
            QualityBundleCandidateCoverage(
                offering=offering,
                components=tuple(component_coverage),
                measured_component_count=measured_count,
                missing_component_ids=missing_ids,
                quarantined_component_ids=quarantined_ids,
                failed_required_component_ids=failed_required_ids,
                eligible=eligible,
            )
        )
    candidate_universe_hash = content_hash(
        [item.offering.model_dump(mode="json") for item in coverage]
    )
    content = _QualityBundleSnapshotContent(
        policy_hash=quality_bundle_policy_hash(policy),
        generated_at=generated_at,
        valid_until=valid_until,
        policy=policy,
        candidate_universe_hash=candidate_universe_hash,
        candidates=tuple(coverage),
    )
    return QualityBundleSnapshot(
        snapshot_id=content_hash(content),
        **content.model_dump(),
    )


def _validated_snapshot(snapshot: QualityBundleSnapshot) -> QualityBundleSnapshot:
    return QualityBundleSnapshot.model_validate(snapshot.model_dump(mode="json"))


def eligible_quality_bundle_candidates(
    snapshot: QualityBundleSnapshot,
    *,
    now: datetime,
) -> tuple[QualityBundleCandidateCoverage, ...]:
    """Return only hard-eligible records; the snapshot retains all diagnostics."""

    validated = _validated_snapshot(snapshot)
    if now.tzinfo is None:
        raise ValueError("quality bundle evaluation time must include a timezone")
    if now < validated.generated_at:
        raise ValueError("quality bundle snapshot is future-dated")
    if now >= validated.valid_until:
        raise ValueError("quality bundle snapshot is stale")
    return tuple(candidate for candidate in validated.candidates if candidate.eligible)


def verify_quality_bundle_snapshot(
    policy: QualityBundlePolicy,
    component_frontiers: Mapping[str, FrontierSnapshot],
    candidates: Iterable[OfferingKey],
    snapshot: QualityBundleSnapshot,
    *,
    now: datetime,
    quarantines: Mapping[str, Iterable[QualityBundleQuarantine]] | None = None,
) -> None:
    """Regenerate a bundle from exact source frontiers before trusting it."""

    validated = _validated_snapshot(snapshot)
    expected = build_quality_bundle_snapshot(
        policy,
        component_frontiers,
        candidates,
        generated_at=validated.generated_at,
        quarantines=quarantines,
    )
    if validated != expected:
        raise ValueError("quality bundle snapshot does not match its source frontiers")
    eligible_quality_bundle_candidates(validated, now=now)
