from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, Field, field_validator, model_validator

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, canonical_bytes, content_hash
from model_skyline.engine import dominance_axis_relation, frontier_hash_matches
from model_skyline.models import (
    MAX_SELECTION_CANDIDATES,
    AxisDescriptor,
    AxisEstimate,
    CanonicalDecimal,
    CanonicalJsonObject,
    FrontierSnapshot,
    FrozenModel,
    Goal,
    InsufficientCandidates,
    ModelChoice,
    OfferingKey,
    PositiveSafeCount,
    ProjectConfig,
    SafeCount,
    SelectionCandidateCount,
    Sha256Digest,
    SnapshotTtlSeconds,
    UncertaintyMode,
    WorkloadReference,
)

PROXIMITY_ALGORITHM_VERSION: Literal["dominance-interval-epsilon-grid-34-v1"] = (
    "dominance-interval-epsilon-grid-34-v1"
)
MAX_PRIORITY_GROUPS = 32
MAX_SECONDARY_FRONTIERS = 128
MAX_PROXIMITY_CANDIDATES = 128
MAX_PROXIMITY_EVIDENCE_REFERENCES = 32_768
MAX_SELECTION_FRONTIER_EVIDENCE = 32_768
MAX_OFFERING_IDENTITY_BYTES = 2_048
MAX_NEAR_EPSILON = Decimal(2)
EPSILON_GRID_DECIMAL_PLACES = 34
EPSILON_GRID_DENOMINATOR = 10**EPSILON_GRID_DECIMAL_PLACES
MAX_EPSILON_GRID_INDEX = 2 * EPSILON_GRID_DENOMINATOR
PROXIMITY_SCHEMA_ID = "urn:model-skyline:schema:v1alpha1:frontier-proximity"
MULTI_FRONTIER_SELECTION_SCHEMA_ID = (
    "urn:model-skyline:schema:v1alpha1:multi-frontier-selection-snapshot"
)


def _epsilon_is_on_grid(value: Decimal) -> Decimal:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -EPSILON_GRID_DECIMAL_PLACES:
        raise ValueError(
            f"proximity epsilon cannot exceed {EPSILON_GRID_DECIMAL_PLACES} decimal places"
        )
    return value


GridEpsilon = Annotated[CanonicalDecimal, AfterValidator(_epsilon_is_on_grid)]
NearEpsilon = Annotated[
    GridEpsilon,
    Field(ge=0, le=MAX_NEAR_EPSILON),
]


def _hash(value: Any) -> str:
    return content_hash(value)


def _offering_sort_key(offering: OfferingKey) -> bytes:
    return canonical_bytes(offering)


def _offering_identity(offering: OfferingKey) -> bytes:
    """Return the canonical identity of every OfferingKey field.

    In particular, this deliberately does not match on ``offering_id`` alone.
    Provider route, endpoint, billing mode, region, tier, quantization, reasoning
    effort, harness, and capabilities all remain part of cross-frontier identity.
    """

    return canonical_bytes(offering)


class ProximityAxisSlack(FrozenModel):
    metric: str = Field(min_length=1)
    normalized_dominance_slack: GridEpsilon = Field(ge=0, le=MAX_NEAR_EPSILON)
    witnesses: tuple[OfferingKey, ...] = Field(default=(), max_length=MAX_PROXIMITY_CANDIDATES)

    @model_validator(mode="after")
    def witnesses_are_canonical(self) -> Self:
        if self.normalized_dominance_slack == 0 and self.witnesses:
            raise ValueError("a zero axis slack cannot have witnesses")
        identities = [_offering_identity(item) for item in self.witnesses]
        if len(identities) != len(set(identities)):
            raise ValueError("axis slack witnesses must be unique")
        if identities != sorted(identities):
            raise ValueError("axis slack witnesses must be canonically ordered")
        return self


class DominanceInterval(FrozenModel):
    dominator: OfferingKey
    enters_at_epsilon: GridEpsilon = Field(ge=0, le=MAX_NEAR_EPSILON)
    exits_at_epsilon: GridEpsilon = Field(gt=0, le=MAX_NEAR_EPSILON)

    @model_validator(mode="after")
    def interval_is_nonempty(self) -> Self:
        if self.enters_at_epsilon >= self.exits_at_epsilon:
            raise ValueError("a dominance interval must be non-empty")
        return self


class OfferingProximity(FrozenModel):
    offering: OfferingKey
    exact_member: bool
    axis_slacks: tuple[ProximityAxisSlack, ProximityAxisSlack]
    blocking_dominance_intervals: tuple[DominanceInterval, ...] = Field(
        default=(),
        max_length=MAX_PROXIMITY_CANDIDATES,
    )
    minimal_relative_epsilon: GridEpsilon = Field(ge=0, le=MAX_NEAR_EPSILON)

    @model_validator(mode="after")
    def proximity_is_coherent(self) -> Self:
        metrics = [item.metric for item in self.axis_slacks]
        if len(set(metrics)) != 2:
            raise ValueError("proximity axis slacks must reference two distinct metrics")
        expected = max(item.normalized_dominance_slack for item in self.axis_slacks)
        if self.minimal_relative_epsilon != expected:
            raise ValueError("minimal_relative_epsilon must equal the worst axis slack")
        intervals = list(self.blocking_dominance_intervals)
        interval_keys = [
            (
                item.enters_at_epsilon,
                item.exits_at_epsilon,
                _offering_sort_key(item.dominator),
            )
            for item in intervals
        ]
        if interval_keys != sorted(interval_keys):
            raise ValueError("blocking dominance intervals must be canonically ordered")
        if len({_offering_identity(item.dominator) for item in intervals}) != len(intervals):
            raise ValueError("blocking dominance intervals must have unique dominators")
        if self.minimal_relative_epsilon == 0 and intervals:
            raise ValueError("a zero-distance offering cannot have blocking intervals")
        if self.minimal_relative_epsilon > 0:
            if not intervals:
                raise ValueError("a positive-distance offering requires blocking intervals")
            if max(item.exits_at_epsilon for item in intervals) != self.minimal_relative_epsilon:
                raise ValueError("blocking intervals must derive minimal_relative_epsilon")
        return self


class FrontierProximitySnapshot(FrozenModel):
    schema_version: Literal["model-skyline/proximity/v1alpha1"] = "model-skyline/proximity/v1alpha1"
    kind: Literal["frontier-proximity"] = "frontier-proximity"
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    snapshot_id: Sha256Digest
    algorithm_version: Literal["dominance-interval-epsilon-grid-34-v1"] = (
        PROXIMITY_ALGORITHM_VERSION
    )
    source_frontier_id: str = Field(min_length=1)
    source_frontier_snapshot_id: Sha256Digest
    source_frontier_snapshot_hash: Sha256Digest
    candidate_universe_hash: Sha256Digest
    member_universe_hash: Sha256Digest
    generated_at: datetime
    workload: WorkloadReference
    uncertainty: UncertaintyMode
    axes: tuple[AxisDescriptor, AxisDescriptor]
    candidates: tuple[OfferingProximity, ...] = Field(max_length=MAX_PROXIMITY_CANDIDATES)

    @field_validator("generated_at")
    @classmethod
    def generated_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("generated_at must include a timezone")
        return value

    @model_validator(mode="after")
    def sidecar_is_coherent(self) -> Self:
        if self.source_frontier_snapshot_id != self.source_frontier_snapshot_hash:
            raise ValueError("source frontier snapshot id and hash must match")
        axis_metrics = [axis.metric for axis in self.axes]
        if len(set(axis_metrics)) != 2:
            raise ValueError("proximity axes must be distinct")
        candidates = list(self.candidates)
        identities = [_offering_identity(item.offering) for item in candidates]
        if any(len(identity) > MAX_OFFERING_IDENTITY_BYTES for identity in identities):
            raise ValueError("proximity OfferingKey identity exceeds the byte limit")
        if len(identities) != len(set(identities)):
            raise ValueError("proximity candidate OfferingKeys must be unique")
        if identities != sorted(identities):
            raise ValueError("proximity candidates must be canonically ordered")
        candidate_identities = set(identities)
        for candidate in candidates:
            candidate_identity = _offering_identity(candidate.offering)
            referenced = [interval.dominator for interval in candidate.blocking_dominance_intervals]
            referenced.extend(
                witness for axis_slack in candidate.axis_slacks for witness in axis_slack.witnesses
            )
            if any(_offering_identity(item) not in candidate_identities for item in referenced):
                raise ValueError("proximity witnesses must belong to the candidate universe")
            if any(_offering_identity(item) == candidate_identity for item in referenced):
                raise ValueError("an offering cannot be its own proximity witness")
        evidence_references = sum(
            len(candidate.blocking_dominance_intervals)
            + sum(len(axis_slack.witnesses) for axis_slack in candidate.axis_slacks)
            for candidate in candidates
        )
        if evidence_references > MAX_PROXIMITY_EVIDENCE_REFERENCES:
            raise ValueError("proximity evidence exceeds the reference limit")
        if any(
            tuple(item.metric for item in candidate.axis_slacks) != tuple(axis_metrics)
            for candidate in candidates
        ):
            raise ValueError("candidate axis slacks must follow the sidecar axis order")
        expected_candidates = _hash(
            [candidate.offering.model_dump(mode="json") for candidate in candidates]
        )
        if self.candidate_universe_hash != expected_candidates:
            raise ValueError("candidate_universe_hash does not match candidates")
        expected_members = _hash(
            [
                candidate.offering.model_dump(mode="json")
                for candidate in candidates
                if candidate.exact_member
            ]
        )
        if self.member_universe_hash != expected_members:
            raise ValueError("member_universe_hash does not match exact members")
        return self


class SecondaryFrontierReference(FrozenModel):
    frontier_id: str = Field(min_length=1)
    frontier_snapshot_id: Sha256Digest
    frontier_snapshot_hash: Sha256Digest
    proximity_snapshot_id: Sha256Digest
    near_epsilon: NearEpsilon | None = None
    max_age_seconds: SnapshotTtlSeconds

    @model_validator(mode="after")
    def content_identity_is_exact(self) -> Self:
        if self.frontier_snapshot_id != self.frontier_snapshot_hash:
            raise ValueError("frontier snapshot id and hash must match")
        return self


class FrontierPriorityGroup(FrozenModel):
    name: str = Field(min_length=1, max_length=128)
    frontiers: tuple[SecondaryFrontierReference, ...] = Field(
        min_length=1,
        max_length=MAX_SECONDARY_FRONTIERS,
    )

    @model_validator(mode="after")
    def frontiers_are_unique(self) -> Self:
        ids = [item.frontier_id for item in self.frontiers]
        if len(ids) != len(set(ids)):
            raise ValueError("a priority group cannot repeat a frontier_id")
        return self


class CrossFrontierSelectionPolicy(FrozenModel):
    strategy: Literal["priority-group-overlap"] = "priority-group-overlap"
    priority_groups: tuple[FrontierPriorityGroup, ...] = Field(
        min_length=1,
        max_length=MAX_PRIORITY_GROUPS,
    )

    @model_validator(mode="after")
    def groups_and_frontiers_are_unique(self) -> Self:
        names = [group.name for group in self.priority_groups]
        if len(names) != len(set(names)):
            raise ValueError("priority group names must be unique")
        frontier_ids = [
            reference.frontier_id for group in self.priority_groups for reference in group.frontiers
        ]
        if len(frontier_ids) > MAX_SECONDARY_FRONTIERS:
            raise ValueError(
                f"a policy cannot reference more than {MAX_SECONDARY_FRONTIERS} frontiers"
            )
        if len(frontier_ids) != len(set(frontier_ids)):
            raise ValueError("each secondary frontier_id may appear only once in a policy")
        snapshot_ids = [
            reference.frontier_snapshot_id
            for group in self.priority_groups
            for reference in group.frontiers
        ]
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("each secondary frontier snapshot may appear only once in a policy")
        return self


class FrontierRankEvidence(FrozenModel):
    frontier_id: str = Field(min_length=1)
    frontier_snapshot_id: Sha256Digest
    proximity_snapshot_id: Sha256Digest
    measured: bool
    exact_member: bool
    near_member: bool
    minimal_relative_epsilon: GridEpsilon | None = Field(
        default=None,
        ge=0,
        le=MAX_NEAR_EPSILON,
    )
    axis_slacks: tuple[ProximityAxisSlack, ProximityAxisSlack] | tuple[()] = ()

    @model_validator(mode="after")
    def membership_is_coherent(self) -> Self:
        if not self.measured:
            if self.exact_member or self.near_member:
                raise ValueError("an unmeasured offering cannot be exact or near")
            if self.minimal_relative_epsilon is not None or self.axis_slacks:
                raise ValueError("an unmeasured offering cannot carry proximity values")
            return self
        if self.minimal_relative_epsilon is None or len(self.axis_slacks) != 2:
            raise ValueError("a measured offering requires distance and two axis slacks")
        if self.exact_member and self.near_member:
            raise ValueError("near_member counts only non-exact near membership")
        return self


class PriorityGroupRankEvidence(FrozenModel):
    name: str = Field(min_length=1, max_length=128)
    exact_memberships: SafeCount
    near_memberships: SafeCount
    frontiers: tuple[FrontierRankEvidence, ...] = Field(
        min_length=1,
        max_length=MAX_SECONDARY_FRONTIERS,
    )

    @model_validator(mode="after")
    def counts_match_evidence(self) -> Self:
        if self.exact_memberships != sum(item.exact_member for item in self.frontiers):
            raise ValueError("exact membership count does not match frontier evidence")
        if self.near_memberships != sum(item.near_member for item in self.frontiers):
            raise ValueError("near membership count does not match frontier evidence")
        return self


class CandidateRankEvidence(FrozenModel):
    rank: PositiveSafeCount
    primary_rank: PositiveSafeCount
    offering: OfferingKey
    axes: dict[str, AxisEstimate]
    metadata: CanonicalJsonObject = Field(default_factory=dict)
    priority_groups: tuple[PriorityGroupRankEvidence, ...] = Field(max_length=MAX_PRIORITY_GROUPS)

    @property
    def choice(self) -> ModelChoice:
        return ModelChoice(offering=self.offering, axes=self.axes, metadata=self.metadata)


class MultiFrontierSelectionSnapshot(FrozenModel):
    schema_version: Literal["model-skyline/selection-overlap/v1alpha1"] = (
        "model-skyline/selection-overlap/v1alpha1"
    )
    kind: Literal["multi-frontier-selection"] = "multi-frontier-selection"
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    snapshot_id: Sha256Digest
    policy_hash: Sha256Digest
    primary_frontier_snapshot_id: Sha256Digest
    primary_frontier_snapshot_hash: Sha256Digest
    selection_id: str = Field(min_length=1)
    frontier_id: str = Field(min_length=1)
    workload: WorkloadReference
    strategy: Literal["priority-group-overlap"] = "priority-group-overlap"
    order_by: str = Field(min_length=1)
    requested_count: SelectionCandidateCount
    max_per_provider: SelectionCandidateCount | None = None
    on_insufficient: InsufficientCandidates
    generated_at: datetime
    valid_until: datetime
    policy: CrossFrontierSelectionPolicy
    ranked_candidates: tuple[CandidateRankEvidence, ...] = Field(
        min_length=1,
        max_length=MAX_SELECTION_CANDIDATES,
    )
    default: ModelChoice
    fallbacks: tuple[ModelChoice, ...] = Field(
        default=(),
        max_length=MAX_SELECTION_CANDIDATES - 1,
    )

    @field_validator("generated_at", "valid_until")
    @classmethod
    def timestamps_have_timezones(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("selection timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def snapshot_is_coherent(self) -> Self:
        if self.primary_frontier_snapshot_id != self.primary_frontier_snapshot_hash:
            raise ValueError("primary frontier snapshot id and hash must match")
        if self.valid_until <= self.generated_at:
            raise ValueError("valid_until must follow generated_at")
        if self.strategy != self.policy.strategy:
            raise ValueError("selection strategy must match its embedded policy")
        ranked = list(self.ranked_candidates)
        if [item.rank for item in ranked] != list(range(1, len(ranked) + 1)):
            raise ValueError("ranked candidates must have contiguous one-based ranks")
        if sorted(item.primary_rank for item in ranked) != list(range(1, len(ranked) + 1)):
            raise ValueError("primary ranks must be a one-based permutation")
        identities = [_offering_identity(item.offering) for item in ranked]
        if any(len(identity) > MAX_OFFERING_IDENTITY_BYTES for identity in identities):
            raise ValueError("ranked candidate OfferingKey identity exceeds the byte limit")
        if len(identities) != len(set(identities)):
            raise ValueError("ranked candidate OfferingKeys must be unique")
        group_names = tuple(group.name for group in self.policy.priority_groups)
        for item in ranked:
            if tuple(group.name for group in item.priority_groups) != group_names:
                raise ValueError("rank evidence must follow policy priority-group order")
            for group_evidence, group_policy in zip(
                item.priority_groups,
                self.policy.priority_groups,
                strict=True,
            ):
                evidence_bindings = tuple(
                    (
                        frontier.frontier_id,
                        frontier.frontier_snapshot_id,
                        frontier.proximity_snapshot_id,
                    )
                    for frontier in group_evidence.frontiers
                )
                policy_bindings = tuple(
                    (
                        reference.frontier_id,
                        reference.frontier_snapshot_id,
                        reference.proximity_snapshot_id,
                    )
                    for reference in group_policy.frontiers
                )
                if evidence_bindings != policy_bindings:
                    raise ValueError("rank evidence must follow policy frontier order")
                for frontier_evidence, reference in zip(
                    group_evidence.frontiers,
                    group_policy.frontiers,
                    strict=True,
                ):
                    expected_near = (
                        frontier_evidence.measured
                        and not frontier_evidence.exact_member
                        and reference.near_epsilon is not None
                        and frontier_evidence.minimal_relative_epsilon is not None
                        and frontier_evidence.minimal_relative_epsilon <= reference.near_epsilon
                    )
                    if frontier_evidence.near_member != expected_near:
                        raise ValueError("near membership does not match policy epsilon")
        evidence_count = sum(
            len(group.frontiers) for item in ranked for group in item.priority_groups
        )
        if evidence_count > MAX_SELECTION_FRONTIER_EVIDENCE:
            raise ValueError("selection frontier evidence exceeds the artifact limit")
        if ranked != sorted(ranked, key=_embedded_rank_key):
            raise ValueError("ranked candidates do not follow their embedded evidence")
        choices = self.choices
        if len(choices) > self.requested_count:
            raise ValueError("selection contains more choices than requested")
        if (
            self.on_insufficient is InsufficientCandidates.ERROR
            and len(choices) != self.requested_count
        ):
            raise ValueError("strict selection must contain the requested number of choices")
        choice_identities = [_offering_identity(item.offering) for item in choices]
        if len(choice_identities) != len(set(choice_identities)):
            raise ValueError("selection choices must have distinct OfferingKeys")
        expected_ranked_choices = _select_diverse(
            ranked,
            count=self.requested_count,
            max_per_provider=self.max_per_provider,
        )
        expected_choices = tuple(item.choice for item in expected_ranked_choices)
        if choices != expected_choices:
            raise ValueError("selection choices must be the greedy diverse ranked prefix")
        if any(self.order_by not in item.axes for item in ranked):
            raise ValueError("every ranked candidate must contain the primary ordering axis")
        return self

    @property
    def choices(self) -> tuple[ModelChoice, ...]:
        return (self.default, *self.fallbacks)


@dataclass(frozen=True)
class SecondaryFrontierInput:
    frontier: FrontierSnapshot
    proximity: FrontierProximitySnapshot


@dataclass(frozen=True)
class _IntervalCalculation:
    interval: DominanceInterval
    axis_exit_thresholds: tuple[Decimal, Decimal]


def _oriented_values(
    candidate: Any,
    target: Any,
    axis: AxisDescriptor,
    uncertainty: UncertaintyMode,
) -> tuple[Decimal, Decimal]:
    candidate_estimate = candidate.axes[axis.metric]
    target_estimate = target.axes[axis.metric]
    if uncertainty is UncertaintyMode.ROBUST:
        if candidate_estimate.lower is None or candidate_estimate.upper is None:
            raise ValueError("robust proximity requires candidate confidence bounds")
        if target_estimate.lower is None or target_estimate.upper is None:
            raise ValueError("robust proximity requires target confidence bounds")
        if axis.goal is Goal.MINIMIZE:
            return +candidate_estimate.upper, +target_estimate.lower
        return +candidate_estimate.lower, +target_estimate.upper
    return +candidate_estimate.value, +target_estimate.value


def _advantage(left: Decimal, right: Decimal, goal: Goal) -> Decimal:
    return right - left if goal is Goal.MINIMIZE else left - right


def _epsilon_from_grid_index(index: int) -> Decimal:
    if not 0 <= index <= MAX_EPSILON_GRID_INDEX:
        raise ValueError("epsilon grid index is outside the supported range")
    return Decimal(f"{index}e-{EPSILON_GRID_DECIMAL_PLACES}")


def _grid_index_at_or_above(value: Decimal) -> int:
    with localcontext() as context:
        context.prec = 80
        scaled = value * Decimal(EPSILON_GRID_DENOMINATOR)
        index: int = int(scaled.to_integral_value(rounding=ROUND_CEILING))
    return int(min(max(index, 0), MAX_EPSILON_GRID_INDEX))


def _first_grid_transition(
    candidate: Any,
    target: Any,
    axis: AxisDescriptor,
    uncertainty: UncertaintyMode,
    *,
    transition: Literal["no_worse", "not_better"],
    algebraic_guess: Decimal,
) -> Decimal | None:
    """Find the first 34-place epsilon-grid value at one monotone transition.

    Solving the normalized ratio and rounding once is insufficient for a
    repeating quotient: the core engine multiplies and adds under Decimal34,
    so a rounded endpoint can lie on the wrong side of its strict comparison.
    Search a fixed, language-neutral grid around the algebraic estimate and
    verify every decision with the shared core axis predicate. Exponential
    bracketing plus integer bisection avoids an unbounded walk across Decimal
    ULPs when addition rounds at a much coarser magnitude than epsilon.
    """

    def reached(index: int) -> bool:
        no_worse, better = dominance_axis_relation(
            candidate,
            target,
            axis,
            uncertainty,
            epsilon_relative=_epsilon_from_grid_index(index),
        )
        return no_worse if transition == "no_worse" else not better

    if reached(0):
        return Decimal(0)
    start = _grid_index_at_or_above(algebraic_guess)
    if reached(start):
        high = start
        step = 1
        low = max(0, high - step)
        while low > 0 and reached(low):
            high = low
            step *= 2
            low = max(0, high - step)
    else:
        low = start
        step = 1
        high = min(MAX_EPSILON_GRID_INDEX, low + step)
        while high < MAX_EPSILON_GRID_INDEX and not reached(high):
            low = high
            step *= 2
            high = min(MAX_EPSILON_GRID_INDEX, low + step)
        if not reached(high):
            return None
    while high - low > 1:
        middle = (low + high) // 2
        if reached(middle):
            high = middle
        else:
            low = middle
    return _epsilon_from_grid_index(high)


def _dominance_interval(
    candidate: Any,
    target: Any,
    axes: tuple[AxisDescriptor, AxisDescriptor],
    uncertainty: UncertaintyMode,
) -> _IntervalCalculation | None:
    entry_thresholds: list[Decimal] = []
    exit_thresholds: list[Decimal] = []
    with localcontext(POLICY_DECIMAL_CONTEXT):
        for axis in axes:
            left, right = _oriented_values(candidate, target, axis, uncertainty)
            advantage = _advantage(left, right, axis.goal)
            scale = max(abs(left), abs(right))
            no_worse, better = dominance_axis_relation(
                candidate,
                target,
                axis,
                uncertainty,
                epsilon_relative=Decimal(0),
            )
            if not no_worse:
                if scale == 0:
                    raise AssertionError("a zero-scale axis cannot be meaningfully worse")
                entry_threshold = _first_grid_transition(
                    candidate,
                    target,
                    axis,
                    uncertainty,
                    transition="no_worse",
                    algebraic_guess=(-axis.epsilon_absolute - advantage) / scale,
                )
                if entry_threshold is None:
                    return None
                entry_thresholds.append(entry_threshold)
            else:
                entry_thresholds.append(Decimal(0))
            if better:
                if scale == 0:
                    raise AssertionError("a nonzero advantage must have a nonzero scale")
                exit_threshold = _first_grid_transition(
                    candidate,
                    target,
                    axis,
                    uncertainty,
                    transition="not_better",
                    algebraic_guess=(advantage - axis.epsilon_absolute) / scale,
                )
                if exit_threshold is None:
                    raise ValueError("proximity dominance persists beyond the epsilon grid")
                exit_thresholds.append(exit_threshold)
            else:
                exit_thresholds.append(Decimal(0))
        entry = max(entry_thresholds)
        exit_epsilon = max(exit_thresholds)
    if entry >= exit_epsilon:
        return None
    return _IntervalCalculation(
        interval=DominanceInterval(
            dominator=candidate.offering,
            enters_at_epsilon=entry,
            exits_at_epsilon=exit_epsilon,
        ),
        axis_exit_thresholds=(exit_thresholds[0], exit_thresholds[1]),
    )


def _candidate_proximity(
    target: Any,
    candidates: Sequence[Any],
    members: set[bytes],
    axes: tuple[AxisDescriptor, AxisDescriptor],
    uncertainty: UncertaintyMode,
) -> OfferingProximity:
    calculations = [
        calculation
        for candidate in candidates
        if candidate is not target
        and (calculation := _dominance_interval(candidate, target, axes, uncertainty)) is not None
    ]
    calculations.sort(
        key=lambda item: (
            item.interval.enters_at_epsilon,
            item.interval.exits_at_epsilon,
            _offering_sort_key(item.interval.dominator),
        )
    )

    cursor = Decimal(0)
    blockers: list[_IntervalCalculation] = []
    for calculation in calculations:
        if calculation.interval.enters_at_epsilon > cursor:
            break
        blockers.append(calculation)
        cursor = max(cursor, calculation.interval.exits_at_epsilon)

    slacks: list[ProximityAxisSlack] = []
    for index, axis in enumerate(axes):
        maximum = max(
            (item.axis_exit_thresholds[index] for item in blockers),
            default=Decimal(0),
        )
        witnesses = tuple(
            sorted(
                (
                    item.interval.dominator
                    for item in blockers
                    if maximum > 0 and item.axis_exit_thresholds[index] == maximum
                ),
                key=_offering_sort_key,
            )
        )
        slacks.append(
            ProximityAxisSlack(
                metric=axis.metric,
                normalized_dominance_slack=maximum,
                witnesses=witnesses,
            )
        )
    return OfferingProximity(
        offering=target.offering,
        exact_member=_offering_identity(target.offering) in members,
        axis_slacks=(slacks[0], slacks[1]),
        blocking_dominance_intervals=tuple(item.interval for item in blockers),
        minimal_relative_epsilon=cursor,
    )


def build_frontier_proximity_snapshot(
    frontier: FrontierSnapshot,
) -> FrontierProximitySnapshot:
    """Build an immutable distance sidecar without changing frontier membership."""

    if not frontier_hash_matches(frontier):
        raise ValueError("source frontier snapshot hash mismatch")
    actual_hash = frontier.snapshot_id
    if len(frontier.evaluated) > MAX_PROXIMITY_CANDIDATES:
        raise ValueError(
            f"proximity candidate universe exceeds {MAX_PROXIMITY_CANDIDATES} offerings"
        )
    if any(axis.epsilon_absolute < 0 or axis.epsilon_relative < 0 for axis in frontier.axes):
        raise ValueError("source frontier tolerances cannot be negative")
    candidates = tuple(
        sorted(frontier.evaluated, key=lambda item: _offering_sort_key(item.offering))
    )
    if any(
        len(_offering_identity(item.offering)) > MAX_OFFERING_IDENTITY_BYTES for item in candidates
    ):
        raise ValueError("proximity OfferingKey identity exceeds the byte limit")
    members = {_offering_identity(item.offering) for item in frontier.members}
    proximity_values: list[OfferingProximity] = []
    evidence_references = 0
    for target in candidates:
        candidate_proximity = _candidate_proximity(
            target, candidates, members, frontier.axes, frontier.uncertainty
        )
        evidence_references += len(candidate_proximity.blocking_dominance_intervals) + sum(
            len(axis_slack.witnesses) for axis_slack in candidate_proximity.axis_slacks
        )
        if evidence_references > MAX_PROXIMITY_EVIDENCE_REFERENCES:
            raise ValueError("proximity evidence exceeds the reference limit")
        proximity_values.append(candidate_proximity)
    proximity = tuple(proximity_values)
    candidate_universe_hash = _hash([item.offering.model_dump(mode="json") for item in proximity])
    member_universe_hash = _hash(
        [item.offering.model_dump(mode="json") for item in proximity if item.exact_member]
    )
    provisional = FrontierProximitySnapshot(
        snapshot_id="0" * 64,
        source_frontier_id=frontier.frontier_id,
        source_frontier_snapshot_id=frontier.snapshot_id,
        source_frontier_snapshot_hash=actual_hash,
        candidate_universe_hash=candidate_universe_hash,
        member_universe_hash=member_universe_hash,
        generated_at=frontier.generated_at,
        workload=frontier.workload,
        uncertainty=frontier.uncertainty,
        axes=frontier.axes,
        candidates=proximity,
    )
    return provisional.model_copy(update={"snapshot_id": frontier_proximity_hash(provisional)})


def frontier_proximity_hash(snapshot: FrontierProximitySnapshot) -> str:
    return _hash(snapshot.model_dump(mode="json", exclude={"snapshot_id"}))


def multi_frontier_selection_hash(snapshot: MultiFrontierSelectionSnapshot) -> str:
    return _hash(snapshot.model_dump(mode="json", exclude={"snapshot_id"}))


def multi_frontier_policy_hash(
    config: ProjectConfig,
    selection_id: str,
    policy: CrossFrontierSelectionPolicy,
) -> str:
    """Bind an overlap policy to one trusted static selection definition."""

    try:
        definition = config.selections[selection_id]
    except KeyError as exc:
        raise ValueError(f"unknown selection {selection_id!r}") from exc
    return _hash(
        {
            "selection_id": selection_id,
            "definition": definition.model_dump(mode="json"),
            "overlap": policy.model_dump(mode="json"),
        }
    )


def generated_overlap_schemas() -> dict[str, dict[str, Any]]:
    """Generate the two additive, language-neutral overlap artifact contracts."""

    values = {
        "frontier-proximity.schema.json": (
            PROXIMITY_SCHEMA_ID,
            FrontierProximitySnapshot.model_json_schema(mode="serialization"),
        ),
        "multi-frontier-selection-snapshot.schema.json": (
            MULTI_FRONTIER_SELECTION_SCHEMA_ID,
            MultiFrontierSelectionSnapshot.model_json_schema(mode="serialization"),
        ),
    }
    result: dict[str, dict[str, Any]] = {}
    for name, (schema_id, schema) in values.items():
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": schema_id,
            **schema,
        }
        _constrain_serialized_epsilon(schema)
        _add_serialized_evidence_conditionals(schema)
        result[name] = schema
    return result


def _constrain_serialized_epsilon(value: Any) -> None:
    """Constrain canonical serialized grid values hidden by the Decimal adapter."""

    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            for name in (
                "normalized_dominance_slack",
                "enters_at_epsilon",
                "exits_at_epsilon",
                "minimal_relative_epsilon",
                "near_epsilon",
            ):
                field_schema = properties.get(name)
                if not isinstance(field_schema, dict):
                    continue
                nullable = field_schema.get("anyOf")
                candidates = nullable if isinstance(nullable, list) else [field_schema]
                for candidate in candidates:
                    if not isinstance(candidate, dict) or candidate.get("type") != "string":
                        continue
                    canonical_values = (
                        rf"(?:0|1|2|[01]\.\d{{0,{EPSILON_GRID_DECIMAL_PLACES - 1}}}[1-9])"
                    )
                    if name == "exits_at_epsilon":
                        canonical_values = (
                            rf"(?:1|2|[01]\.\d{{0,{EPSILON_GRID_DECIMAL_PLACES - 1}}}[1-9])"
                        )
                    candidate["pattern"] = rf"^{canonical_values}$"
                    candidate["maxLength"] = EPSILON_GRID_DECIMAL_PLACES + 2
        for child in value.values():
            _constrain_serialized_epsilon(child)
    elif isinstance(value, list):
        for child in value:
            _constrain_serialized_epsilon(child)


def _add_serialized_evidence_conditionals(schema: dict[str, Any]) -> None:
    """Expose semantic measured/exact invariants to non-Python consumers."""

    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return
    rank_evidence = definitions.get("FrontierRankEvidence")
    if isinstance(rank_evidence, dict):
        rank_evidence["allOf"] = [
            {
                "if": {"properties": {"measured": {"const": False}}},
                "then": {
                    "properties": {
                        "exact_member": {"const": False},
                        "near_member": {"const": False},
                        "minimal_relative_epsilon": {"type": "null"},
                        "axis_slacks": {"maxItems": 0},
                    }
                },
            },
            {
                "if": {"properties": {"measured": {"const": True}}},
                "then": {
                    "properties": {
                        "minimal_relative_epsilon": {"type": "string"},
                        "axis_slacks": {"minItems": 2, "maxItems": 2},
                    }
                },
            },
            {
                "if": {"properties": {"exact_member": {"const": True}}},
                "then": {
                    "properties": {
                        "measured": {"const": True},
                        "near_member": {"const": False},
                    }
                },
            },
            {
                "if": {"properties": {"near_member": {"const": True}}},
                "then": {
                    "properties": {
                        "measured": {"const": True},
                        "exact_member": {"const": False},
                    }
                },
            },
        ]


def _validate_secondary_input(
    reference: SecondaryFrontierReference,
    value: SecondaryFrontierInput,
    *,
    generated_at: datetime,
) -> None:
    frontier = value.frontier
    proximity = value.proximity
    if not frontier_hash_matches(frontier):
        raise ValueError(f"secondary frontier {reference.frontier_id!r} hash mismatch")
    actual_frontier_hash = frontier.snapshot_id
    if reference.frontier_id != frontier.frontier_id:
        raise ValueError(f"secondary frontier {reference.frontier_id!r} identity mismatch")
    if reference.frontier_snapshot_id != frontier.snapshot_id:
        raise ValueError(f"secondary frontier {reference.frontier_id!r} snapshot mismatch")
    if reference.frontier_snapshot_hash != actual_frontier_hash:
        raise ValueError(f"secondary frontier {reference.frontier_id!r} content hash mismatch")
    if proximity.snapshot_id != frontier_proximity_hash(proximity):
        raise ValueError(f"secondary frontier {reference.frontier_id!r} proximity hash mismatch")
    if reference.proximity_snapshot_id != proximity.snapshot_id:
        raise ValueError(
            f"secondary frontier {reference.frontier_id!r} proximity snapshot mismatch"
        )
    if proximity.source_frontier_id != frontier.frontier_id:
        raise ValueError("proximity sidecar references a different frontier")
    if proximity.source_frontier_snapshot_id != frontier.snapshot_id:
        raise ValueError("proximity sidecar references a different frontier snapshot")
    if proximity.source_frontier_snapshot_hash != actual_frontier_hash:
        raise ValueError("proximity sidecar references different frontier content")
    expected_candidates = tuple(
        sorted(frontier.evaluated, key=lambda item: _offering_sort_key(item.offering))
    )
    expected_candidate_hash = _hash(
        [item.offering.model_dump(mode="json") for item in expected_candidates]
    )
    if proximity.candidate_universe_hash != expected_candidate_hash:
        raise ValueError("proximity sidecar candidate universe mismatch")
    expected_members = tuple(
        sorted(frontier.members, key=lambda item: _offering_sort_key(item.offering))
    )
    expected_member_hash = _hash(
        [item.offering.model_dump(mode="json") for item in expected_members]
    )
    if proximity.member_universe_hash != expected_member_hash:
        raise ValueError("proximity sidecar member universe mismatch")
    if (
        proximity.generated_at != frontier.generated_at
        or proximity.workload != frontier.workload
        or proximity.uncertainty != frontier.uncertainty
        or proximity.axes != frontier.axes
    ):
        raise ValueError("proximity sidecar policy binding does not match its frontier")
    if proximity != build_frontier_proximity_snapshot(frontier):
        raise ValueError("proximity sidecar does not match the declared algorithm output")
    if frontier.generated_at > generated_at:
        raise ValueError(f"secondary frontier {reference.frontier_id!r} is future-dated")
    if generated_at - frontier.generated_at >= timedelta(seconds=reference.max_age_seconds):
        raise ValueError(f"secondary frontier {reference.frontier_id!r} is stale")


def _primary_sort_key(snapshot: FrontierSnapshot, order_by: str, item: Any) -> tuple[Any, ...]:
    axes = {axis.metric: axis for axis in snapshot.axes}
    if order_by not in axes:
        raise ValueError(f"selection order_by {order_by!r} is not a primary frontier axis")
    primary = axes[order_by]
    secondary = next(axis for axis in snapshot.axes if axis.metric != order_by)

    def preference(value: Decimal, goal: Goal) -> Decimal:
        return value if goal is Goal.MINIMIZE else -value

    with localcontext(POLICY_DECIMAL_CONTEXT):
        return (
            preference(item.axes[primary.metric].value, primary.goal),
            preference(item.axes[secondary.metric].value, secondary.goal),
            item.offering.offering_id,
            _offering_sort_key(item.offering),
        )


def _rank_key(
    evidence: tuple[PriorityGroupRankEvidence, ...],
    primary_key: tuple[Any, ...],
) -> tuple[Any, ...]:
    key: list[Any] = []
    for group in evidence:
        key.extend((-group.exact_memberships, -group.near_memberships))
        for frontier in group.frontiers:
            if frontier.measured:
                if frontier.minimal_relative_epsilon is None:
                    raise AssertionError("measured evidence requires distance")
                key.extend((0, frontier.minimal_relative_epsilon))
            else:
                # Never encode a missing candidate as zero distance.
                key.extend((1, Decimal(0)))
    key.extend(primary_key)
    return tuple(key)


def _embedded_rank_key(item: CandidateRankEvidence) -> tuple[Any, ...]:
    """Reconstruct the rerank key from evidence carried by the artifact.

    ``primary_rank`` is a lossless ordinal encoding of the primary frontier's
    full deterministic sort key. Source-backed verification separately proves
    that ordinal and all evidence against the bound frontier snapshots.
    """

    key: list[Any] = []
    for group in item.priority_groups:
        key.extend((-group.exact_memberships, -group.near_memberships))
        for frontier in group.frontiers:
            if frontier.measured:
                if frontier.minimal_relative_epsilon is None:
                    raise AssertionError("measured evidence requires distance")
                key.extend((0, frontier.minimal_relative_epsilon))
            else:
                key.extend((1, Decimal(0)))
    key.append(item.primary_rank)
    return tuple(key)


def _select_diverse(
    members: Iterable[Any],
    *,
    count: int,
    max_per_provider: int | None,
) -> tuple[Any, ...]:
    selected: list[Any] = []
    provider_counts: dict[str, int] = {}
    for member in members:
        provider = member.offering.provider
        current = provider_counts.get(provider, 0)
        if max_per_provider is not None and current >= max_per_provider:
            continue
        selected.append(member)
        provider_counts[provider] = current + 1
        if len(selected) == count:
            break
    return tuple(selected)


def select_models_across_frontiers(
    config: ProjectConfig,
    primary: FrontierSnapshot,
    selection_id: str,
    policy: CrossFrontierSelectionPolicy,
    secondary_inputs: Mapping[str, SecondaryFrontierInput],
) -> MultiFrontierSelectionSnapshot:
    """Re-rank primary members by priority-group overlap, then apply diversity."""

    try:
        definition = config.selections[selection_id]
    except KeyError as exc:
        raise ValueError(f"unknown selection {selection_id!r}") from exc
    if definition.frontier != primary.frontier_id:
        raise ValueError(
            f"selection {selection_id!r} expects frontier {definition.frontier!r}, "
            f"not {primary.frontier_id!r}"
        )
    if not frontier_hash_matches(primary):
        raise ValueError("primary frontier snapshot hash mismatch")
    actual_primary_hash = primary.snapshot_id
    if len(primary.members) > MAX_SELECTION_CANDIDATES:
        raise ValueError("primary frontier exceeds the selection candidate limit")
    primary_identities = [_offering_identity(item.offering) for item in primary.members]
    if any(len(identity) > MAX_OFFERING_IDENTITY_BYTES for identity in primary_identities):
        raise ValueError("primary member OfferingKey identity exceeds the byte limit")
    references = tuple(
        reference for group in policy.priority_groups for reference in group.frontiers
    )
    if len(primary.members) * len(references) > MAX_SELECTION_FRONTIER_EVIDENCE:
        raise ValueError("selection frontier evidence exceeds the artifact limit")
    if any(reference.frontier_id == primary.frontier_id for reference in references):
        raise ValueError("the primary frontier is implicit and cannot be a secondary input")
    expected_ids = {reference.frontier_snapshot_id for reference in references}
    if set(secondary_inputs) != expected_ids:
        raise ValueError("secondary input keys must exactly match policy frontier snapshot ids")
    for reference in references:
        _validate_secondary_input(
            reference,
            secondary_inputs[reference.frontier_snapshot_id],
            generated_at=primary.generated_at,
        )

    entry_maps = {
        reference.frontier_snapshot_id: {
            _offering_identity(item.offering): item
            for item in secondary_inputs[reference.frontier_snapshot_id].proximity.candidates
        }
        for reference in references
    }
    order_by = definition.order_by or primary.order_by
    primary_ordered = tuple(
        sorted(primary.members, key=lambda item: _primary_sort_key(primary, order_by, item))
    )
    primary_ranks = {
        _offering_identity(item.offering): rank
        for rank, item in enumerate(primary_ordered, start=1)
    }
    evidence_by_identity: dict[bytes, tuple[PriorityGroupRankEvidence, ...]] = {}
    for item in primary.members:
        identity = _offering_identity(item.offering)
        group_evidence: list[PriorityGroupRankEvidence] = []
        for group in policy.priority_groups:
            frontier_evidence: list[FrontierRankEvidence] = []
            for reference in group.frontiers:
                proximity = entry_maps[reference.frontier_snapshot_id].get(identity)
                if proximity is None:
                    frontier_evidence.append(
                        FrontierRankEvidence(
                            frontier_id=reference.frontier_id,
                            frontier_snapshot_id=reference.frontier_snapshot_id,
                            proximity_snapshot_id=reference.proximity_snapshot_id,
                            measured=False,
                            exact_member=False,
                            near_member=False,
                        )
                    )
                    continue
                near = (
                    not proximity.exact_member
                    and reference.near_epsilon is not None
                    and proximity.minimal_relative_epsilon <= reference.near_epsilon
                )
                frontier_evidence.append(
                    FrontierRankEvidence(
                        frontier_id=reference.frontier_id,
                        frontier_snapshot_id=reference.frontier_snapshot_id,
                        proximity_snapshot_id=reference.proximity_snapshot_id,
                        measured=True,
                        exact_member=proximity.exact_member,
                        near_member=near,
                        minimal_relative_epsilon=proximity.minimal_relative_epsilon,
                        axis_slacks=proximity.axis_slacks,
                    )
                )
            group_evidence.append(
                PriorityGroupRankEvidence(
                    name=group.name,
                    exact_memberships=sum(value.exact_member for value in frontier_evidence),
                    near_memberships=sum(value.near_member for value in frontier_evidence),
                    frontiers=tuple(frontier_evidence),
                )
            )
        evidence_by_identity[identity] = tuple(group_evidence)

    reranked = tuple(
        sorted(
            primary.members,
            key=lambda item: _rank_key(
                evidence_by_identity[_offering_identity(item.offering)],
                _primary_sort_key(primary, order_by, item),
            ),
        )
    )
    ranked_candidates = tuple(
        CandidateRankEvidence(
            rank=rank,
            primary_rank=primary_ranks[_offering_identity(item.offering)],
            offering=item.offering,
            axes=item.axes,
            metadata=item.metadata,
            priority_groups=evidence_by_identity[_offering_identity(item.offering)],
        )
        for rank, item in enumerate(reranked, start=1)
    )
    selected = _select_diverse(
        reranked,
        count=definition.count,
        max_per_provider=definition.max_per_provider,
    )
    if not selected:
        raise ValueError("primary frontier has no selectable members")
    if (
        len(selected) < definition.count
        and definition.on_insufficient is InsufficientCandidates.ERROR
    ):
        raise ValueError(
            f"selection requires {definition.count} candidates but only {len(selected)} satisfy it"
        )
    choices = tuple(
        ModelChoice(offering=item.offering, axes=item.axes, metadata=item.metadata)
        for item in selected
    )
    freshness_deadlines = [
        secondary_inputs[reference.frontier_snapshot_id].frontier.generated_at
        + timedelta(seconds=reference.max_age_seconds)
        for reference in references
    ]
    valid_until = min(
        primary.generated_at + timedelta(seconds=definition.snapshot_ttl_seconds),
        *freshness_deadlines,
    )
    provisional = MultiFrontierSelectionSnapshot(
        snapshot_id="0" * 64,
        policy_hash=multi_frontier_policy_hash(config, selection_id, policy),
        primary_frontier_snapshot_id=primary.snapshot_id,
        primary_frontier_snapshot_hash=actual_primary_hash,
        selection_id=selection_id,
        frontier_id=primary.frontier_id,
        workload=primary.workload,
        order_by=order_by,
        requested_count=definition.count,
        max_per_provider=definition.max_per_provider,
        on_insufficient=definition.on_insufficient,
        generated_at=primary.generated_at,
        valid_until=valid_until,
        policy=policy,
        ranked_candidates=ranked_candidates,
        default=choices[0],
        fallbacks=choices[1:],
    )
    return provisional.model_copy(
        update={"snapshot_id": multi_frontier_selection_hash(provisional)}
    )


def verify_multi_frontier_selection_snapshot(
    config: ProjectConfig,
    primary: FrontierSnapshot,
    snapshot: MultiFrontierSelectionSnapshot,
    expected_selection_id: str,
    expected_policy: CrossFrontierSelectionPolicy,
    secondary_inputs: Mapping[str, SecondaryFrontierInput],
    *,
    now: datetime,
    max_clock_skew: timedelta = timedelta(minutes=5),
) -> None:
    """Verify a selection hash and regenerate it from every bound source artifact.

    JSON Schema and Pydantic validation establish structural self-consistency.
    Agent consumers should call this source-backed verifier with their current
    trusted clock before trusting a downloaded selection for routing decisions.
    """

    if now.tzinfo is None:
        raise ValueError("selection verification time must include a timezone")
    if max_clock_skew < timedelta(0):
        raise ValueError("selection verification future skew cannot be negative")
    if snapshot.generated_at > now + max_clock_skew:
        raise ValueError("multi-frontier selection is future-dated")
    if now > snapshot.valid_until:
        raise ValueError("multi-frontier selection has expired")
    if snapshot.selection_id != expected_selection_id:
        raise ValueError("multi-frontier selection identity does not match the trusted selection")
    if snapshot.policy != expected_policy:
        raise ValueError("multi-frontier selection policy does not match the trusted policy")
    expected_policy_hash = multi_frontier_policy_hash(
        config,
        expected_selection_id,
        expected_policy,
    )
    if snapshot.policy_hash != expected_policy_hash:
        raise ValueError("multi-frontier selection policy hash mismatch")
    if snapshot.snapshot_id != multi_frontier_selection_hash(snapshot):
        raise ValueError("multi-frontier selection snapshot hash mismatch")
    expected = select_models_across_frontiers(
        config,
        primary,
        expected_selection_id,
        expected_policy,
        secondary_inputs,
    )
    if snapshot != expected:
        raise ValueError("multi-frontier selection does not match its bound source snapshots")
