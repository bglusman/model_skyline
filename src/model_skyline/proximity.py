"""Descriptive distance from eligible offerings to a two-axis Pareto frontier.

The calculations in this module do not alter frontier membership or selection.
They answer a narrower reporting question: what is the smallest relative axis
tolerance at which an eligible, currently dominated point is no longer
dominated?  Every comparison delegates to the core engine so point/robust
bounds, absolute tolerances, and Decimal arithmetic stay identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Literal

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, canonical_bytes
from model_skyline.engine import dominance_axis_relation, frontier_hash_matches
from model_skyline.models import (
    AxisDescriptor,
    EvaluatedOffering,
    FrontierSnapshot,
    Goal,
    OfferingKey,
    UncertaintyMode,
)

MAX_PROXIMITY_CANDIDATES = 128
MAX_PROXIMITY_EVIDENCE_REFERENCES = 32_768
MAX_RELATIVE_EPSILON = Decimal(2)
EPSILON_GRID_DECIMAL_PLACES = 34
EPSILON_GRID_DENOMINATOR = 10**EPSILON_GRID_DECIMAL_PLACES
MAX_EPSILON_GRID_INDEX = 2 * EPSILON_GRID_DENOMINATOR


@dataclass(frozen=True)
class DominanceInterval:
    """Half-open relative-epsilon interval over which one point dominates."""

    dominator_offering_id: str
    enters_at_epsilon: Decimal
    exits_at_epsilon: Decimal


@dataclass(frozen=True)
class ProximityAxisSlack:
    """Largest exit threshold on one axis among the connected blockers."""

    metric: str
    normalized_dominance_slack: Decimal
    witness_offering_ids: tuple[str, ...]


@dataclass(frozen=True)
class OfferingProximity:
    """Auditable descriptive proximity for one eligible evaluated offering."""

    offering: OfferingKey
    exact_member: bool
    minimal_relative_epsilon: Decimal
    axis_slacks: tuple[ProximityAxisSlack, ProximityAxisSlack]
    blocking_dominance_intervals: tuple[DominanceInterval, ...]


@dataclass(frozen=True)
class _IntervalCalculation:
    interval: DominanceInterval
    axis_exit_thresholds: tuple[Decimal, Decimal]
    dominator_sort_key: bytes


def _oriented_values(
    candidate: EvaluatedOffering,
    target: EvaluatedOffering,
    axis: AxisDescriptor,
    uncertainty: UncertaintyMode,
) -> tuple[Decimal, Decimal]:
    candidate_estimate = candidate.axes[axis.metric]
    target_estimate = target.axes[axis.metric]
    if uncertainty is UncertaintyMode.ROBUST:
        if candidate_estimate.lower is None or candidate_estimate.upper is None:
            raise ValueError("robust proximity requires candidate bounds")
        if target_estimate.lower is None or target_estimate.upper is None:
            raise ValueError("robust proximity requires target bounds")
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
    candidate: EvaluatedOffering,
    target: EvaluatedOffering,
    axis: AxisDescriptor,
    uncertainty: UncertaintyMode,
    *,
    transition: Literal["no_worse", "not_better"],
    algebraic_guess: Decimal,
) -> Decimal | None:
    """Find a transition on the same fixed Decimal grid used by the report."""

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
    candidate: EvaluatedOffering,
    target: EvaluatedOffering,
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
                entry = _first_grid_transition(
                    candidate,
                    target,
                    axis,
                    uncertainty,
                    transition="no_worse",
                    algebraic_guess=(-axis.epsilon_absolute - advantage) / scale,
                )
                if entry is None:
                    return None
                entry_thresholds.append(entry)
            else:
                entry_thresholds.append(Decimal(0))
            if better:
                if scale == 0:
                    raise AssertionError("a nonzero advantage must have a nonzero scale")
                exit_epsilon = _first_grid_transition(
                    candidate,
                    target,
                    axis,
                    uncertainty,
                    transition="not_better",
                    algebraic_guess=(advantage - axis.epsilon_absolute) / scale,
                )
                if exit_epsilon is None:
                    raise ValueError("proximity dominance persists beyond the epsilon grid")
                exit_thresholds.append(exit_epsilon)
            else:
                exit_thresholds.append(Decimal(0))
        enters_at = max(entry_thresholds)
        exits_at = max(exit_thresholds)
    if enters_at >= exits_at:
        return None
    return _IntervalCalculation(
        interval=DominanceInterval(
            dominator_offering_id=candidate.offering.offering_id,
            enters_at_epsilon=enters_at,
            exits_at_epsilon=exits_at,
        ),
        axis_exit_thresholds=(exit_thresholds[0], exit_thresholds[1]),
        dominator_sort_key=canonical_bytes(candidate.offering),
    )


def _candidate_proximity(
    target: EvaluatedOffering,
    candidates: tuple[EvaluatedOffering, ...],
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
            item.dominator_sort_key,
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
            item.interval.dominator_offering_id
            for item in blockers
            if maximum > 0 and item.axis_exit_thresholds[index] == maximum
        )
        slacks.append(
            ProximityAxisSlack(
                metric=axis.metric,
                normalized_dominance_slack=maximum,
                witness_offering_ids=witnesses,
            )
        )
    if max(item.normalized_dominance_slack for item in slacks) != cursor:
        raise AssertionError("axis proximity slacks do not derive the frontier distance")
    return OfferingProximity(
        offering=target.offering,
        exact_member=canonical_bytes(target.offering) in members,
        minimal_relative_epsilon=cursor,
        axis_slacks=(slacks[0], slacks[1]),
        blocking_dominance_intervals=tuple(item.interval for item in blockers),
    )


def calculate_frontier_proximity(
    frontier: FrontierSnapshot,
) -> tuple[OfferingProximity, ...]:
    """Return proximity for every eligible point in a verified frontier snapshot."""

    if not frontier_hash_matches(frontier):
        raise ValueError("source frontier snapshot hash mismatch")
    if len(frontier.evaluated) > MAX_PROXIMITY_CANDIDATES:
        raise ValueError(
            f"proximity candidate universe exceeds {MAX_PROXIMITY_CANDIDATES} offerings"
        )
    if any(axis.epsilon_absolute < 0 or axis.epsilon_relative < 0 for axis in frontier.axes):
        raise ValueError("source frontier tolerances cannot be negative")
    candidates = tuple(sorted(frontier.evaluated, key=lambda item: canonical_bytes(item.offering)))
    members = {canonical_bytes(item.offering) for item in frontier.members}
    result: list[OfferingProximity] = []
    evidence_references = 0
    for target in candidates:
        proximity = _candidate_proximity(
            target,
            candidates,
            members,
            frontier.axes,
            frontier.uncertainty,
        )
        evidence_references += len(proximity.blocking_dominance_intervals) + sum(
            len(item.witness_offering_ids) for item in proximity.axis_slacks
        )
        if evidence_references > MAX_PROXIMITY_EVIDENCE_REFERENCES:
            raise ValueError("proximity evidence exceeds the reference limit")
        result.append(proximity)
    return tuple(result)
