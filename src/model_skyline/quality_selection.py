from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from model_skyline.canonical import canonical_bytes, content_hash
from model_skyline.engine import dominates, frontier_hash, frontier_hash_matches
from model_skyline.models import (
    EvaluatedOffering,
    FrontierSnapshot,
    FrozenModel,
    ModelChoice,
    OfferingKey,
    PortablePublicationId,
    ProjectConfig,
    RejectedOffering,
    Sha256Digest,
    WorkloadReference,
)
from model_skyline.quality_bundle import (
    QualityBundlePolicy,
    QualityBundleQuarantine,
    QualityBundleSnapshot,
    QualityCoverageStatus,
    eligible_quality_bundle_candidates,
    verify_quality_bundle_snapshot,
)
from model_skyline.selection_overlap import (
    CrossFrontierSelectionPolicy,
    FrontierPriorityGroup,
    FrontierProximitySnapshot,
    MultiFrontierSelectionSnapshot,
    SecondaryFrontierInput,
    SecondaryFrontierReference,
    _validate_secondary_input,
    build_frontier_proximity_snapshot,
    multi_frontier_selection_hash,
    select_models_across_frontiers,
    verify_multi_frontier_selection_snapshot,
)

QUALITY_GATE_ALGORITHM_VERSION: Literal["exact-offering-hard-gate-pareto-v1"] = (
    "exact-offering-hard-gate-pareto-v1"
)
QUALITY_GATED_SELECTION_SCHEMA_VERSION: Literal[
    "model-skyline/quality-gated-selection/v1alpha1"
] = "model-skyline/quality-gated-selection/v1alpha1"


def _offering_identity(offering: OfferingKey) -> bytes:
    """Retain every route-specific field when applying the quality gate."""

    return canonical_bytes(offering)


def _validated_config(config: ProjectConfig) -> ProjectConfig:
    return ProjectConfig.model_validate(config.model_dump(mode="json"))


def _validated_frontier(snapshot: FrontierSnapshot, *, label: str) -> FrontierSnapshot:
    validated = FrontierSnapshot.model_validate(snapshot.model_dump(mode="json"))
    if not frontier_hash_matches(validated):
        raise ValueError(f"{label} frontier snapshot hash mismatch")
    return validated


def _validated_bundle(snapshot: QualityBundleSnapshot) -> QualityBundleSnapshot:
    return QualityBundleSnapshot.model_validate(snapshot.model_dump(mode="json"))


def _validated_overlap_policy(
    policy: CrossFrontierSelectionPolicy,
) -> CrossFrontierSelectionPolicy:
    return CrossFrontierSelectionPolicy.model_validate(policy.model_dump(mode="json"))


def _validated_secondary_inputs(
    values: Mapping[str, SecondaryFrontierInput],
) -> dict[str, SecondaryFrontierInput]:
    result: dict[str, SecondaryFrontierInput] = {}
    for snapshot_id, value in values.items():
        if not isinstance(snapshot_id, str) or not isinstance(value, SecondaryFrontierInput):
            raise ValueError("secondary inputs must map snapshot ids to frontier inputs")
        result[snapshot_id] = SecondaryFrontierInput(
            frontier=FrontierSnapshot.model_validate(value.frontier.model_dump(mode="json")),
            proximity=FrontierProximitySnapshot.model_validate(
                value.proximity.model_dump(mode="json")
            ),
        )
    return result


def _derived_config_hash(
    source_frontier: FrontierSnapshot,
    quality_bundle: QualityBundleSnapshot,
) -> str:
    return content_hash(
        {
            "algorithm": QUALITY_GATE_ALGORITHM_VERSION,
            "source_frontier_snapshot_id": source_frontier.snapshot_id,
            "source_frontier_config_hash": source_frontier.config_hash,
            "quality_bundle_snapshot_id": quality_bundle.snapshot_id,
            "quality_bundle_policy_hash": quality_bundle.policy_hash,
        }
    )


def _derived_catalog_hash(
    source_frontier: FrontierSnapshot,
    quality_bundle: QualityBundleSnapshot,
    eligible: tuple[EvaluatedOffering, ...],
) -> str:
    return content_hash(
        {
            "algorithm": QUALITY_GATE_ALGORITHM_VERSION,
            "source_frontier_snapshot_id": source_frontier.snapshot_id,
            "source_frontier_catalog_hash": source_frontier.catalog_hash,
            "quality_bundle_snapshot_id": quality_bundle.snapshot_id,
            "quality_bundle_candidate_universe_hash": quality_bundle.candidate_universe_hash,
            "eligible_offerings": [item.offering.model_dump(mode="json") for item in eligible],
        }
    )


def _build_gated_frontier(
    source_frontier: FrontierSnapshot,
    quality_bundle: QualityBundleSnapshot,
    eligible_identities: frozenset[bytes],
    *,
    generated_at: datetime,
    require_complete_coverage: bool,
    require_nonempty: bool,
    label: str,
) -> FrontierSnapshot:
    if require_complete_coverage:
        bundle_candidate_identities = {
            _offering_identity(candidate.offering) for candidate in quality_bundle.candidates
        }
        if any(
            _offering_identity(item.offering) not in bundle_candidate_identities
            for item in source_frontier.evaluated
        ):
            raise ValueError(
                "quality bundle candidate universe does not cover every source primary offering"
            )
    eligible = tuple(
        item
        for item in source_frontier.evaluated
        if _offering_identity(item.offering) in eligible_identities
    )
    if require_nonempty and not eligible:
        raise ValueError(f"quality gate leaves the {label} frontier with no eligible offerings")

    recomputed: list[EvaluatedOffering] = []
    for other in eligible:
        dominators = tuple(
            sorted(
                candidate.offering.offering_id
                for candidate in eligible
                if candidate is not other
                and dominates(
                    candidate,
                    other,
                    source_frontier.axes,
                    source_frontier.uncertainty,
                )
            )
        )
        recomputed.append(other.model_copy(update={"dominated_by": dominators}))
    evaluated = tuple(recomputed)
    members = tuple(item for item in evaluated if not item.dominated_by)

    retained_identities = {_offering_identity(item.offering) for item in eligible}
    quality_rejections = tuple(
        RejectedOffering(
            offering_id=item.offering.offering_id,
            reasons=("quality bundle hard-eligibility gate excluded exact OfferingKey",),
        )
        for item in source_frontier.evaluated
        if _offering_identity(item.offering) not in retained_identities
    )
    rejected = tuple(
        sorted(
            (*source_frontier.rejected, *quality_rejections),
            key=lambda item: item.offering_id,
        )
    )

    provisional = FrontierSnapshot(
        snapshot_id="0" * 64,
        config_hash=_derived_config_hash(source_frontier, quality_bundle),
        catalog_hash=_derived_catalog_hash(source_frontier, quality_bundle, eligible),
        engine_version=(f"{source_frontier.engine_version}+{QUALITY_GATE_ALGORITHM_VERSION}"),
        generated_at=generated_at,
        frontier_id=source_frontier.frontier_id,
        workload=source_frontier.workload,
        order_by=source_frontier.order_by,
        uncertainty=source_frontier.uncertainty,
        axes=source_frontier.axes,
        members=members,
        evaluated=evaluated,
        rejected=rejected,
        public_release_blocked=source_frontier.public_release_blocked,
        sources=source_frontier.sources,
        source_watermarks=source_frontier.source_watermarks,
    )
    return provisional.model_copy(update={"snapshot_id": frontier_hash(provisional)})


def _validate_quality_components_are_ranked(
    quality_bundle: QualityBundleSnapshot,
    source_primary: FrontierSnapshot,
    overlap_policy: CrossFrontierSelectionPolicy,
) -> None:
    references = {
        reference.frontier_snapshot_id: reference
        for group in overlap_policy.priority_groups
        for reference in group.frontiers
    }
    for component in quality_bundle.policy.components:
        if component.frontier_snapshot_id == source_primary.snapshot_id:
            if (
                component.frontier_id != source_primary.frontier_id
                or component.frontier_snapshot_hash != source_primary.snapshot_id
                or component.config_hash != source_primary.config_hash
                or component.catalog_hash != source_primary.catalog_hash
                or component.workload != source_primary.workload
                or component.axes != source_primary.axes
            ):
                raise ValueError(
                    f"quality component {component.component_id!r} does not match the "
                    "source primary frontier"
                )
            continue
        reference = references.get(component.frontier_snapshot_id)
        if reference is None:
            raise ValueError(f"overlap policy omits quality component {component.component_id!r}")
        if (
            reference.frontier_id != component.frontier_id
            or reference.frontier_snapshot_hash != component.frontier_snapshot_hash
        ):
            raise ValueError(
                f"overlap policy binding for quality component "
                f"{component.component_id!r} does not match its frontier"
            )


def _quality_component_frontiers(
    quality_policy: QualityBundlePolicy,
    source_primary: FrontierSnapshot,
    overlap_policy: CrossFrontierSelectionPolicy,
    secondary_inputs: Mapping[str, SecondaryFrontierInput],
    *,
    generated_at: datetime,
) -> dict[str, FrontierSnapshot]:
    """Resolve every policy component to an exact, source-validated frontier."""

    by_snapshot_id: dict[str, FrontierSnapshot] = {source_primary.snapshot_id: source_primary}
    references = (
        reference for group in overlap_policy.priority_groups for reference in group.frontiers
    )
    for reference in references:
        try:
            source_input = secondary_inputs[reference.frontier_snapshot_id]
        except KeyError as exc:
            raise ValueError(
                f"missing source frontier for quality component reference "
                f"{reference.frontier_snapshot_id!r}"
            ) from exc
        _validate_secondary_input(reference, source_input, generated_at=generated_at)
        by_snapshot_id[reference.frontier_snapshot_id] = source_input.frontier

    result: dict[str, FrontierSnapshot] = {}
    for component in quality_policy.components:
        try:
            result[component.component_id] = by_snapshot_id[component.frontier_snapshot_id]
        except KeyError as exc:
            raise ValueError(
                f"quality component {component.component_id!r} has no bound source frontier"
            ) from exc
    return result


def _bundle_declared_quarantines(
    quality_bundle: QualityBundleSnapshot,
) -> dict[str, tuple[QualityBundleQuarantine, ...]]:
    """Recreate only exclusionary quarantine inputs for source coverage replay.

    A bundle cannot use these self-declared records to promote a route: both
    missing and quarantined coverage are unmeasured. Full provenance replay
    still requires the independently supplied quarantine records accepted by
    ``verify_quality_gated_selection_snapshot``.
    """

    values: dict[str, list[QualityBundleQuarantine]] = {
        component.component_id: [] for component in quality_bundle.policy.components
    }
    for candidate in quality_bundle.candidates:
        for coverage in candidate.components:
            if coverage.status is QualityCoverageStatus.QUARANTINED:
                values[coverage.component_id].append(
                    QualityBundleQuarantine(
                        offering=candidate.offering,
                        reason_codes=coverage.quarantine_reason_codes,
                    )
                )
    return {component_id: tuple(items) for component_id, items in values.items()}


def _verify_bundle_positive_coverage(
    quality_policy: QualityBundlePolicy,
    quality_bundle: QualityBundleSnapshot,
    source_primary: FrontierSnapshot,
    overlap_policy: CrossFrontierSelectionPolicy,
    secondary_inputs: Mapping[str, SecondaryFrontierInput],
    *,
    generated_at: datetime,
) -> None:
    """Reject fabricated measured coverage before it can influence routing."""

    if quality_bundle.policy != quality_policy:
        raise ValueError("quality bundle does not match the expected quality policy")
    component_frontiers = _quality_component_frontiers(
        quality_policy,
        source_primary,
        overlap_policy,
        secondary_inputs,
        generated_at=generated_at,
    )
    verify_quality_bundle_snapshot(
        quality_policy,
        component_frontiers,
        (candidate.offering for candidate in quality_bundle.candidates),
        quality_bundle,
        now=generated_at,
        quarantines=_bundle_declared_quarantines(quality_bundle),
    )


def _build_gated_secondary_inputs(
    overlap_policy: CrossFrontierSelectionPolicy,
    secondary_inputs: Mapping[str, SecondaryFrontierInput],
    quality_bundle: QualityBundleSnapshot,
    eligible_identities: frozenset[bytes],
    *,
    generated_at: datetime,
) -> tuple[
    CrossFrontierSelectionPolicy,
    dict[str, SecondaryFrontierInput],
    datetime,
]:
    """Validate source sidecars, then rebuild every secondary feasible set."""

    references = tuple(
        reference for group in overlap_policy.priority_groups for reference in group.frontiers
    )
    expected_ids = {reference.frontier_snapshot_id for reference in references}
    if set(secondary_inputs) != expected_ids:
        raise ValueError("secondary input keys must exactly match the source overlap policy")

    derived_references: dict[str, SecondaryFrontierReference] = {}
    derived_inputs: dict[str, SecondaryFrontierInput] = {}
    source_deadlines: list[datetime] = []
    for reference in references:
        source_input = secondary_inputs[reference.frontier_snapshot_id]
        _validate_secondary_input(reference, source_input, generated_at=generated_at)
        source_deadlines.append(
            source_input.frontier.generated_at.astimezone(UTC)
            + timedelta(seconds=reference.max_age_seconds)
        )
        gated_frontier = _build_gated_frontier(
            source_input.frontier,
            quality_bundle,
            eligible_identities,
            generated_at=generated_at,
            require_complete_coverage=False,
            require_nonempty=False,
            label=f"secondary {reference.frontier_id!r}",
        )
        gated_proximity = build_frontier_proximity_snapshot(gated_frontier)
        derived_reference = reference.model_copy(
            update={
                "frontier_snapshot_id": gated_frontier.snapshot_id,
                "frontier_snapshot_hash": gated_frontier.snapshot_id,
                "proximity_snapshot_id": gated_proximity.snapshot_id,
            }
        )
        derived_references[reference.frontier_snapshot_id] = derived_reference
        derived_inputs[gated_frontier.snapshot_id] = SecondaryFrontierInput(
            frontier=gated_frontier,
            proximity=gated_proximity,
        )

    derived_policy = CrossFrontierSelectionPolicy(
        priority_groups=tuple(
            FrontierPriorityGroup(
                name=group.name,
                frontiers=tuple(
                    derived_references[reference.frontier_snapshot_id]
                    for reference in group.frontiers
                ),
            )
            for group in overlap_policy.priority_groups
        )
    )
    return derived_policy, derived_inputs, min(source_deadlines)


class _QualityGatedSelectionContent(FrozenModel):
    schema_version: Literal["model-skyline/quality-gated-selection/v1alpha1"] = (
        QUALITY_GATED_SELECTION_SCHEMA_VERSION
    )
    kind: Literal["quality-gated-selection"] = "quality-gated-selection"
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    algorithm_version: Literal["exact-offering-hard-gate-pareto-v1"] = (
        QUALITY_GATE_ALGORITHM_VERSION
    )
    quality_bundle_id: PortablePublicationId
    quality_bundle_version: str = Field(min_length=1, max_length=128)
    quality_bundle_snapshot_id: Sha256Digest
    quality_bundle_snapshot_hash: Sha256Digest
    quality_bundle_policy_hash: Sha256Digest
    quality_bundle_generated_at: datetime
    quality_bundle_valid_until: datetime
    source_primary_frontier_snapshot_id: Sha256Digest
    source_primary_frontier_snapshot_hash: Sha256Digest
    source_primary_generated_at: datetime
    source_primary_valid_until: datetime
    source_overlap_policy_hash: Sha256Digest
    source_overlap_policy: CrossFrontierSelectionPolicy
    source_secondary_valid_until: datetime
    gated_primary_frontier_snapshot_id: Sha256Digest
    gated_primary_frontier_snapshot_hash: Sha256Digest
    selection_snapshot_id: Sha256Digest
    selection_snapshot_hash: Sha256Digest
    generated_at: datetime
    valid_until: datetime
    gated_primary_frontier: FrontierSnapshot
    selection: MultiFrontierSelectionSnapshot

    @field_validator(
        "quality_bundle_generated_at",
        "quality_bundle_valid_until",
        "source_primary_generated_at",
        "source_primary_valid_until",
        "source_secondary_valid_until",
        "generated_at",
        "valid_until",
    )
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("quality-gated selection timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bindings_are_coherent(self) -> Self:
        if self.quality_bundle_snapshot_id != self.quality_bundle_snapshot_hash:
            raise ValueError("quality bundle snapshot id and hash must match")
        if self.source_primary_frontier_snapshot_id != self.source_primary_frontier_snapshot_hash:
            raise ValueError("source primary frontier snapshot id and hash must match")
        if self.gated_primary_frontier_snapshot_id != self.gated_primary_frontier_snapshot_hash:
            raise ValueError("gated primary frontier snapshot id and hash must match")
        if self.selection_snapshot_id != self.selection_snapshot_hash:
            raise ValueError("selection snapshot id and hash must match")
        if self.source_overlap_policy_hash != content_hash(self.source_overlap_policy):
            raise ValueError("source overlap policy hash mismatch")
        if self.quality_bundle_valid_until <= self.quality_bundle_generated_at:
            raise ValueError("quality bundle validity must follow its generation time")
        if self.source_primary_valid_until <= self.source_primary_generated_at:
            raise ValueError("source primary validity must follow its generation time")
        if self.quality_bundle_generated_at > self.generated_at:
            raise ValueError("quality bundle cannot postdate selection derivation")
        if self.source_primary_generated_at > self.generated_at:
            raise ValueError("source primary frontier cannot postdate selection derivation")
        if self.generated_at >= self.source_primary_valid_until:
            raise ValueError("source primary frontier is stale at selection derivation")
        if self.generated_at >= self.source_secondary_valid_until:
            raise ValueError("a source secondary frontier is stale at selection derivation")
        if self.valid_until <= self.generated_at:
            raise ValueError("quality-gated selection validity must follow generation")

        gated = self.gated_primary_frontier
        if not frontier_hash_matches(gated):
            raise ValueError("embedded gated primary frontier hash mismatch")
        if gated.snapshot_id != self.gated_primary_frontier_snapshot_id:
            raise ValueError("embedded gated primary frontier binding mismatch")
        if gated.generated_at != self.generated_at:
            raise ValueError("gated primary frontier generation time mismatch")

        selection = self.selection
        if selection.snapshot_id != multi_frontier_selection_hash(selection):
            raise ValueError("embedded selection snapshot hash mismatch")
        if selection.snapshot_id != self.selection_snapshot_id:
            raise ValueError("embedded selection snapshot binding mismatch")
        if selection.primary_frontier_snapshot_id != gated.snapshot_id:
            raise ValueError("selection does not bind the gated primary frontier")
        if selection.primary_frontier_snapshot_hash != gated.snapshot_id:
            raise ValueError("selection primary frontier content hash mismatch")
        if selection.generated_at != self.generated_at:
            raise ValueError("selection generation time mismatch")
        if selection.frontier_id != gated.frontier_id or selection.workload != gated.workload:
            raise ValueError("selection primary frontier identity mismatch")
        source_groups = self.source_overlap_policy.priority_groups
        derived_groups = selection.policy.priority_groups
        if tuple(group.name for group in source_groups) != tuple(
            group.name for group in derived_groups
        ):
            raise ValueError("derived overlap policy priority groups do not match the source")
        for source_group, derived_group in zip(source_groups, derived_groups, strict=True):
            if len(source_group.frontiers) != len(derived_group.frontiers):
                raise ValueError("derived overlap policy frontier count does not match the source")
            for source_reference, derived_reference in zip(
                source_group.frontiers,
                derived_group.frontiers,
                strict=True,
            ):
                if (
                    source_reference.frontier_id != derived_reference.frontier_id
                    or source_reference.near_epsilon != derived_reference.near_epsilon
                    or source_reference.max_age_seconds != derived_reference.max_age_seconds
                ):
                    raise ValueError("derived overlap policy semantics do not match the source")
        if self.valid_until != min(
            self.quality_bundle_valid_until,
            self.source_primary_valid_until,
            self.source_secondary_valid_until,
            selection.valid_until,
        ):
            raise ValueError("quality-gated validity must be the earliest bound deadline")
        return self

    @property
    def selection_id(self) -> str:
        return self.selection.selection_id

    @property
    def frontier_id(self) -> str:
        return self.selection.frontier_id

    @property
    def workload(self) -> WorkloadReference:
        return self.selection.workload

    @property
    def default(self) -> ModelChoice:
        return self.selection.default

    @property
    def fallbacks(self) -> tuple[ModelChoice, ...]:
        return self.selection.fallbacks

    @property
    def choices(self) -> tuple[ModelChoice, ...]:
        return self.selection.choices


class QualityGatedSelectionSnapshot(_QualityGatedSelectionContent):
    """Content-addressed selection over a quality-gated, recomputed frontier."""

    snapshot_id: Sha256Digest

    @model_validator(mode="after")
    def snapshot_hash_is_valid(self) -> Self:
        if self.snapshot_id != quality_gated_selection_hash(self):
            raise ValueError("quality-gated selection snapshot hash mismatch")
        return self


def quality_gated_selection_hash(snapshot: QualityGatedSelectionSnapshot) -> str:
    return content_hash(snapshot.model_dump(mode="json", exclude={"snapshot_id"}))


def build_quality_gated_selection_snapshot(
    config: ProjectConfig,
    quality_policy: QualityBundlePolicy,
    quality_bundle: QualityBundleSnapshot,
    source_primary: FrontierSnapshot,
    selection_id: str,
    overlap_policy: CrossFrontierSelectionPolicy,
    secondary_inputs: Mapping[str, SecondaryFrontierInput],
    *,
    generated_at: datetime,
) -> QualityGatedSelectionSnapshot:
    """Gate exact routes, rebuild Pareto membership, then apply overlap ranking."""

    validated_config = _validated_config(config)
    validated_quality_policy = QualityBundlePolicy.model_validate(
        quality_policy.model_dump(mode="json")
    )
    validated_bundle = _validated_bundle(quality_bundle)
    validated_primary = _validated_frontier(source_primary, label="source primary")
    validated_policy = _validated_overlap_policy(overlap_policy)
    validated_secondary = _validated_secondary_inputs(secondary_inputs)
    if generated_at.tzinfo is None:
        raise ValueError("quality-gated selection generation time must include a timezone")
    generated_at = generated_at.astimezone(UTC)
    if validated_primary.generated_at > generated_at:
        raise ValueError("source primary frontier is future-dated")
    if validated_bundle.generated_at > generated_at:
        raise ValueError("quality bundle snapshot is future-dated")
    try:
        selection_definition = validated_config.selections[selection_id]
    except KeyError as exc:
        raise ValueError(f"unknown selection {selection_id!r}") from exc
    source_primary_valid_until = validated_primary.generated_at.astimezone(UTC) + timedelta(
        seconds=selection_definition.snapshot_ttl_seconds
    )
    if generated_at >= source_primary_valid_until:
        raise ValueError("source primary frontier is stale at selection derivation")

    _validate_quality_components_are_ranked(
        validated_bundle,
        validated_primary,
        validated_policy,
    )
    _verify_bundle_positive_coverage(
        validated_quality_policy,
        validated_bundle,
        validated_primary,
        validated_policy,
        validated_secondary,
        generated_at=generated_at,
    )
    eligible_identities = frozenset(
        _offering_identity(candidate.offering)
        for candidate in eligible_quality_bundle_candidates(
            validated_bundle,
            now=generated_at,
        )
    )
    gated_primary = _build_gated_frontier(
        validated_primary,
        validated_bundle,
        eligible_identities,
        generated_at=generated_at,
        require_complete_coverage=True,
        require_nonempty=True,
        label="primary",
    )
    gated_policy, gated_secondary, source_secondary_valid_until = _build_gated_secondary_inputs(
        validated_policy,
        validated_secondary,
        validated_bundle,
        eligible_identities,
        generated_at=generated_at,
    )
    selection = select_models_across_frontiers(
        validated_config,
        gated_primary,
        selection_id,
        gated_policy,
        gated_secondary,
    )
    content = _QualityGatedSelectionContent(
        quality_bundle_id=validated_bundle.policy.bundle_id,
        quality_bundle_version=validated_bundle.policy.version,
        quality_bundle_snapshot_id=validated_bundle.snapshot_id,
        quality_bundle_snapshot_hash=validated_bundle.snapshot_id,
        quality_bundle_policy_hash=validated_bundle.policy_hash,
        quality_bundle_generated_at=validated_bundle.generated_at,
        quality_bundle_valid_until=validated_bundle.valid_until,
        source_primary_frontier_snapshot_id=validated_primary.snapshot_id,
        source_primary_frontier_snapshot_hash=validated_primary.snapshot_id,
        source_primary_generated_at=validated_primary.generated_at,
        source_primary_valid_until=source_primary_valid_until,
        source_overlap_policy_hash=content_hash(validated_policy),
        source_overlap_policy=validated_policy,
        source_secondary_valid_until=source_secondary_valid_until,
        gated_primary_frontier_snapshot_id=gated_primary.snapshot_id,
        gated_primary_frontier_snapshot_hash=gated_primary.snapshot_id,
        selection_snapshot_id=selection.snapshot_id,
        selection_snapshot_hash=selection.snapshot_id,
        generated_at=generated_at,
        valid_until=min(
            validated_bundle.valid_until,
            source_primary_valid_until,
            source_secondary_valid_until,
            selection.valid_until,
        ),
        gated_primary_frontier=gated_primary,
        selection=selection,
    )
    return QualityGatedSelectionSnapshot(
        snapshot_id=content_hash(content),
        **content.model_dump(),
    )


def verify_quality_gated_selection_snapshot(
    config: ProjectConfig,
    quality_policy: QualityBundlePolicy,
    component_frontiers: Mapping[str, FrontierSnapshot],
    quality_candidates: Iterable[OfferingKey],
    quality_bundle: QualityBundleSnapshot,
    source_primary: FrontierSnapshot,
    snapshot: QualityGatedSelectionSnapshot,
    expected_selection_id: str,
    expected_overlap_policy: CrossFrontierSelectionPolicy,
    secondary_inputs: Mapping[str, SecondaryFrontierInput],
    *,
    now: datetime,
    quarantines: Mapping[str, Iterable[QualityBundleQuarantine]] | None = None,
    max_clock_skew: timedelta = timedelta(minutes=5),
) -> None:
    """Regenerate and verify every quality, frontier, and selection dependency."""

    if now.tzinfo is None:
        raise ValueError("quality-gated selection verification time must include a timezone")
    now = now.astimezone(UTC)
    if max_clock_skew < timedelta(0):
        raise ValueError("quality-gated selection future skew cannot be negative")

    validated = QualityGatedSelectionSnapshot.model_validate(snapshot.model_dump(mode="json"))
    if validated.generated_at > now + max_clock_skew:
        raise ValueError("quality-gated selection is future-dated")
    if now >= validated.valid_until:
        raise ValueError("quality-gated selection has expired")

    validated_config = _validated_config(config)
    validated_policy = QualityBundlePolicy.model_validate(quality_policy.model_dump(mode="json"))
    validated_bundle = _validated_bundle(quality_bundle)
    validated_primary = _validated_frontier(source_primary, label="source primary")
    validated_overlap = _validated_overlap_policy(expected_overlap_policy)
    validated_secondary = _validated_secondary_inputs(secondary_inputs)

    if validated.quality_bundle_snapshot_id != validated_bundle.snapshot_id:
        raise ValueError("quality-gated selection references a different quality bundle")
    if validated.quality_bundle_id != validated_bundle.policy.bundle_id:
        raise ValueError("quality-gated selection quality bundle identity mismatch")
    if validated.quality_bundle_version != validated_bundle.policy.version:
        raise ValueError("quality-gated selection quality bundle version mismatch")
    if validated.quality_bundle_policy_hash != validated_bundle.policy_hash:
        raise ValueError("quality-gated selection quality policy hash mismatch")
    if validated.source_primary_frontier_snapshot_id != validated_primary.snapshot_id:
        raise ValueError("quality-gated selection references a different source primary frontier")
    if validated.source_overlap_policy != validated_overlap:
        raise ValueError("quality-gated selection source overlap policy mismatch")
    if validated.source_overlap_policy_hash != content_hash(validated_overlap):
        raise ValueError("quality-gated selection source overlap policy hash mismatch")

    verify_quality_bundle_snapshot(
        validated_policy,
        component_frontiers,
        quality_candidates,
        validated_bundle,
        now=validated.generated_at,
        quarantines=quarantines,
    )
    expected = build_quality_gated_selection_snapshot(
        validated_config,
        validated_policy,
        validated_bundle,
        validated_primary,
        expected_selection_id,
        validated_overlap,
        validated_secondary,
        generated_at=validated.generated_at,
    )
    if validated.snapshot_id != quality_gated_selection_hash(validated):
        raise ValueError("quality-gated selection snapshot hash mismatch")
    if validated != expected:
        raise ValueError("quality-gated selection does not match its bound source snapshots")

    eligible_identities = frozenset(
        _offering_identity(candidate.offering)
        for candidate in eligible_quality_bundle_candidates(
            validated_bundle,
            now=validated.generated_at,
        )
    )
    gated_policy, gated_secondary, _source_secondary_valid_until = _build_gated_secondary_inputs(
        validated_overlap,
        validated_secondary,
        validated_bundle,
        eligible_identities,
        generated_at=validated.generated_at,
    )
    verify_multi_frontier_selection_snapshot(
        validated_config,
        expected.gated_primary_frontier,
        validated.selection,
        expected_selection_id,
        gated_policy,
        gated_secondary,
        now=now,
        max_clock_skew=max_clock_skew,
    )
