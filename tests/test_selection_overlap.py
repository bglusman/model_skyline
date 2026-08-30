from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import ValidationError

from model_skyline.engine import dominates, frontier_hash
from model_skyline.io import public_schemas
from model_skyline.models import (
    AxisDescriptor,
    AxisEstimate,
    EvaluatedOffering,
    FrontierSnapshot,
    Goal,
    OfferingKey,
    ProjectConfig,
    UncertaintyMode,
    WorkloadReference,
)
from model_skyline.selection_overlap import (
    CrossFrontierSelectionPolicy,
    FrontierPriorityGroup,
    SecondaryFrontierInput,
    SecondaryFrontierReference,
    _dominance_interval,
    build_frontier_proximity_snapshot,
    frontier_proximity_hash,
    generated_overlap_schemas,
    multi_frontier_selection_hash,
    select_models_across_frontiers,
    verify_multi_frontier_selection_snapshot,
)

NOW = datetime(2026, 8, 29, 19, tzinfo=UTC)


def _offering(
    name: str,
    *,
    provider: str | None = None,
    billing_mode: str = "list",
) -> OfferingKey:
    return OfferingKey(
        offering_id=name,
        model_id=f"model-{name}",
        provider=provider or f"provider-{name}",
        endpoint="responses",
        billing_mode=billing_mode,
        region="us",
        service_tier="standard",
        quantization="hosted",
        reasoning_effort="medium",
        agent_harness="test-agent",
        capabilities=("tools",),
    )


def _snapshot(
    frontier_id: str,
    values: list[tuple[OfferingKey, Decimal | str, Decimal | str]],
    *,
    member_ids: set[str],
    goals: tuple[Goal, Goal] = (Goal.MINIMIZE, Goal.MINIMIZE),
    absolute_epsilons: tuple[Decimal | str, Decimal | str] = ("0", "0"),
    relative_epsilons: tuple[Decimal | str, Decimal | str] = ("0", "0"),
    uncertainty: UncertaintyMode = UncertaintyMode.POINT,
    bounds: dict[str, tuple[Decimal | str, Decimal | str, Decimal | str, Decimal | str]]
    | None = None,
    workload_id: str = "primary-workload",
    generated_at: datetime = NOW,
) -> FrontierSnapshot:
    axes = (
        AxisDescriptor(
            metric="x",
            goal=goals[0],
            unit="x-unit",
            epsilon_absolute=absolute_epsilons[0],
            epsilon_relative=relative_epsilons[0],
        ),
        AxisDescriptor(
            metric="y",
            goal=goals[1],
            unit="y-unit",
            epsilon_absolute=absolute_epsilons[1],
            epsilon_relative=relative_epsilons[1],
        ),
    )
    first_member = next(iter(sorted(member_ids)))
    evaluated: list[EvaluatedOffering] = []
    for offering, x_value, y_value in values:
        item_bounds = (bounds or {}).get(offering.offering_id)
        x_lower, x_upper, y_lower, y_upper = (
            item_bounds if item_bounds is not None else (None, None, None, None)
        )
        evaluated.append(
            EvaluatedOffering(
                offering=offering,
                axes={
                    "x": AxisEstimate(
                        value=x_value,
                        unit="x-unit",
                        lower=x_lower,
                        upper=x_upper,
                    ),
                    "y": AxisEstimate(
                        value=y_value,
                        unit="y-unit",
                        lower=y_lower,
                        upper=y_upper,
                    ),
                },
                dominated_by=() if offering.offering_id in member_ids else (first_member,),
            )
        )
    members = tuple(item for item in evaluated if item.offering.offering_id in member_ids)
    provisional = FrontierSnapshot(
        snapshot_id="pending",
        config_hash="1" * 64,
        catalog_hash="2" * 64,
        engine_version="test",
        generated_at=generated_at,
        frontier_id=frontier_id,
        workload=WorkloadReference(
            id=workload_id,
            version="1",
            unit="work-unit",
        ),
        order_by="x",
        uncertainty=uncertainty,
        axes=axes,
        members=members,
        evaluated=tuple(evaluated),
    )
    return provisional.model_copy(update={"snapshot_id": frontier_hash(provisional)})


def _reference(
    snapshot: FrontierSnapshot,
    *,
    near_epsilon: Decimal | str | None = None,
    max_age_seconds: int = 3600,
) -> tuple[SecondaryFrontierReference, SecondaryFrontierInput]:
    proximity = build_frontier_proximity_snapshot(snapshot)
    reference = SecondaryFrontierReference(
        frontier_id=snapshot.frontier_id,
        frontier_snapshot_id=snapshot.snapshot_id,
        frontier_snapshot_hash=snapshot.snapshot_id,
        proximity_snapshot_id=proximity.snapshot_id,
        near_epsilon=near_epsilon,
        max_age_seconds=max_age_seconds,
    )
    return reference, SecondaryFrontierInput(frontier=snapshot, proximity=proximity)


def _policy(
    *groups: tuple[str, tuple[SecondaryFrontierReference, ...]],
) -> CrossFrontierSelectionPolicy:
    return CrossFrontierSelectionPolicy(
        priority_groups=tuple(
            FrontierPriorityGroup(name=name, frontiers=frontiers) for name, frontiers in groups
        )
    )


def _selection_config(
    config: ProjectConfig,
    *,
    count: int = 1,
    max_per_provider: int | None = None,
) -> ProjectConfig:
    selection = config.selections["coding-agent-defaults"].model_copy(
        update={"count": count, "max_per_provider": max_per_provider, "order_by": "x"}
    )
    return config.model_copy(update={"selections": {"coding-agent-defaults": selection}})


def test_proximity_emits_worst_axis_slack_and_exact_minimal_epsilon() -> None:
    a = _offering("a")
    b = _offering("b")
    duplicate = _offering("duplicate")
    snapshot = _snapshot(
        "secondary",
        [(a, "1", "1"), (b, "2", "4"), (duplicate, "1", "1")],
        member_ids={"a", "duplicate"},
    )

    proximity = build_frontier_proximity_snapshot(snapshot)
    by_id = {item.offering.offering_id: item for item in proximity.candidates}

    assert by_id["a"].minimal_relative_epsilon == 0
    assert by_id["duplicate"].minimal_relative_epsilon == 0
    # The first grid value at which Decimal34's strict comparison flips can be
    # just below the algebraic ratio because the engine rounds tolerance math.
    assert by_id["b"].minimal_relative_epsilon == Decimal("0.7499999999999999999999999999999999")
    assert [item.normalized_dominance_slack for item in by_id["b"].axis_slacks] == [
        Decimal("0.4999999999999999999999999999999998"),
        Decimal("0.7499999999999999999999999999999999"),
    ]
    assert {item.offering.offering_id for item in proximity.candidates if item.exact_member} == {
        "a",
        "duplicate",
    }
    assert proximity.snapshot_id == frontier_proximity_hash(proximity)


def test_proximity_follows_connected_dominance_intervals() -> None:
    baseline = _offering("baseline")
    later = _offering("later")
    target = _offering("target")
    snapshot = _snapshot(
        "secondary",
        [
            (baseline, "9", "9"),
            (later, "8", "10.5"),
            (target, "10", "10"),
        ],
        member_ids={"baseline", "later"},
    )

    proximity = build_frontier_proximity_snapshot(snapshot)
    target_proximity = next(
        item for item in proximity.candidates if item.offering.offering_id == "target"
    )

    # The baseline dominator exits at .1, while another interval starts before
    # then and keeps the target dominated until .2. This is the true first
    # epsilon at which no candidate dominates, not merely the baseline gap.
    assert target_proximity.minimal_relative_epsilon == Decimal("0.2")
    assert [
        item.dominator.offering_id for item in target_proximity.blocking_dominance_intervals
    ] == ["baseline", "later"]


def test_repeating_ratio_endpoints_match_core_dominance_exactly() -> None:
    dominator = _offering("dominator")
    target = _offering("target")
    snapshot = _snapshot(
        "repeating-ratio",
        [(dominator, "-2", "-3"), (target, "-1", "1")],
        member_ids={"dominator"},
    )

    proximity = build_frontier_proximity_snapshot(snapshot)
    result = next(item for item in proximity.candidates if item.offering == target)
    interval = result.blocking_dominance_intervals[0]
    evaluated = {item.offering.offering_id: item for item in snapshot.evaluated}
    candidate = evaluated[interval.dominator.offering_id]
    other = evaluated[target.offering_id]

    def at(epsilon: Decimal) -> bool:
        axes = tuple(
            axis.model_copy(update={"epsilon_relative": epsilon}) for axis in snapshot.axes
        )
        return dominates(candidate, other, axes, snapshot.uncertainty)

    with localcontext() as context:
        context.prec = 80
        before_exit = interval.exits_at_epsilon - Decimal("1e-34")

    assert at(interval.enters_at_epsilon)
    assert at(before_exit)
    assert not at(interval.exits_at_epsilon)


def test_entry_boundary_search_handles_coarse_final_addition_rounding() -> None:
    baseline = _offering("baseline")
    later = _offering("later")
    target = _offering("target")
    snapshot = _snapshot(
        "entry-rounding",
        [
            (baseline, "-20", "-19.1"),
            (later, "-19", "-20"),
            (target, "-20", "-18"),
        ],
        member_ids={"baseline", "later"},
    )

    proximity = build_frontier_proximity_snapshot(snapshot)
    result = next(item for item in proximity.candidates if item.offering == target)
    interval = next(item for item in result.blocking_dominance_intervals if item.dominator == later)
    evaluated = {item.offering.offering_id: item for item in snapshot.evaluated}
    grid_step = Decimal("1e-34")
    axes_at_entry = tuple(
        axis.model_copy(update={"epsilon_relative": interval.enters_at_epsilon})
        for axis in snapshot.axes
    )
    with localcontext() as context:
        context.prec = 80
        before_entry = interval.enters_at_epsilon - grid_step
    axes_before_entry = tuple(
        axis.model_copy(update={"epsilon_relative": before_entry}) for axis in snapshot.axes
    )

    assert interval.enters_at_epsilon == Decimal("0.0499999999999999999999999999999998")
    assert not dominates(
        evaluated[later.offering_id],
        evaluated[target.offering_id],
        axes_before_entry,
        snapshot.uncertainty,
    )
    assert dominates(
        evaluated[later.offering_id],
        evaluated[target.offering_id],
        axes_at_entry,
        snapshot.uncertainty,
    )


def test_high_precision_operands_are_symmetrically_decimal34_for_policy() -> None:
    a = _offering("a")
    b = _offering("b")
    extra_precision = "1." + "0" * 33 + "1"
    snapshot = _snapshot(
        "high-precision-exit",
        [(a, f"-{extra_precision}", "0"), (b, "1", "0")],
        member_ids={"a"},
    )
    proximity = build_frontier_proximity_snapshot(snapshot)
    result = next(item for item in proximity.candidates if item.offering == b)

    expected_exit = Decimal("1.9999999999999999999999999999999995")
    assert result.minimal_relative_epsilon == expected_exit
    assert result.blocking_dominance_intervals[0].exits_at_epsilon == expected_exit


_GRID_STEP = Decimal("1e-34")
_PROPERTY_VALUE = st.decimals(
    min_value=Decimal("-20"),
    max_value=Decimal("20"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)
_PROPERTY_WIDTH = st.decimals(
    min_value=Decimal(0),
    max_value=Decimal("4"),
    places=3,
    allow_nan=False,
    allow_infinity=False,
)
_PROPERTY_ABSOLUTE_EPSILON = st.decimals(
    min_value=Decimal(0),
    max_value=Decimal("3"),
    places=3,
    allow_nan=False,
    allow_infinity=False,
)
_GOAL_PAIRS = st.sampled_from(
    [
        (Goal.MINIMIZE, Goal.MINIMIZE),
        (Goal.MINIMIZE, Goal.MAXIMIZE),
        (Goal.MAXIMIZE, Goal.MINIMIZE),
        (Goal.MAXIMIZE, Goal.MAXIMIZE),
    ]
)
_BOUNDED_ESTIMATE = st.tuples(
    _PROPERTY_VALUE,
    _PROPERTY_WIDTH,
    _PROPERTY_WIDTH,
).map(lambda values: (values[0], values[0] - values[1], values[0] + values[2]))


def _assert_pair_interval_matches_core(
    candidate: EvaluatedOffering,
    target: EvaluatedOffering,
    axes: tuple[AxisDescriptor, AxisDescriptor],
    uncertainty: UncertaintyMode,
) -> None:
    calculation = _dominance_interval(candidate, target, axes, uncertainty)

    def at(epsilon: Decimal) -> bool:
        evaluated_axes = tuple(
            axis.model_copy(update={"epsilon_relative": epsilon}) for axis in axes
        )
        return dominates(candidate, target, evaluated_axes, uncertainty)

    sampled_epsilons = tuple(map(Decimal, ("0", "0.125", "0.5", "1", "1.5", "2")))
    if calculation is None:
        assert not any(at(epsilon) for epsilon in sampled_epsilons)
        return

    entry = calculation.interval.enters_at_epsilon
    exit_epsilon = calculation.interval.exits_at_epsilon
    with localcontext() as context:
        context.prec = 80
        before_entry = entry - _GRID_STEP
        before_exit = exit_epsilon - _GRID_STEP

    if entry > 0:
        assert not at(before_entry)
    assert at(entry)
    assert at(before_exit)
    assert not at(exit_epsilon)
    for epsilon in sampled_epsilons:
        assert at(epsilon) is (entry <= epsilon < exit_epsilon)


@given(
    candidate_x=_PROPERTY_VALUE,
    candidate_y=_PROPERTY_VALUE,
    target_x=_PROPERTY_VALUE,
    target_y=_PROPERTY_VALUE,
    absolute_x=_PROPERTY_ABSOLUTE_EPSILON,
    absolute_y=_PROPERTY_ABSOLUTE_EPSILON,
    goals=_GOAL_PAIRS,
)
@settings(max_examples=100, deadline=None)
def test_point_interval_endpoints_match_core_dominance_property(
    candidate_x: Decimal,
    candidate_y: Decimal,
    target_x: Decimal,
    target_y: Decimal,
    absolute_x: Decimal,
    absolute_y: Decimal,
    goals: tuple[Goal, Goal],
) -> None:
    candidate = EvaluatedOffering(
        offering=_offering("property-candidate"),
        axes={
            "x": AxisEstimate(value=candidate_x, unit="x-unit"),
            "y": AxisEstimate(value=candidate_y, unit="y-unit"),
        },
    )
    target = EvaluatedOffering(
        offering=_offering("property-target"),
        axes={
            "x": AxisEstimate(value=target_x, unit="x-unit"),
            "y": AxisEstimate(value=target_y, unit="y-unit"),
        },
    )
    axes = (
        AxisDescriptor(
            metric="x",
            goal=goals[0],
            unit="x-unit",
            epsilon_absolute=absolute_x,
        ),
        AxisDescriptor(
            metric="y",
            goal=goals[1],
            unit="y-unit",
            epsilon_absolute=absolute_y,
        ),
    )

    _assert_pair_interval_matches_core(candidate, target, axes, UncertaintyMode.POINT)


@given(
    candidate_x=_BOUNDED_ESTIMATE,
    candidate_y=_BOUNDED_ESTIMATE,
    target_x=_BOUNDED_ESTIMATE,
    target_y=_BOUNDED_ESTIMATE,
    absolute_x=_PROPERTY_ABSOLUTE_EPSILON,
    absolute_y=_PROPERTY_ABSOLUTE_EPSILON,
    goals=_GOAL_PAIRS,
)
@settings(max_examples=75, deadline=None)
def test_robust_interval_endpoints_match_core_dominance_property(
    candidate_x: tuple[Decimal, Decimal, Decimal],
    candidate_y: tuple[Decimal, Decimal, Decimal],
    target_x: tuple[Decimal, Decimal, Decimal],
    target_y: tuple[Decimal, Decimal, Decimal],
    absolute_x: Decimal,
    absolute_y: Decimal,
    goals: tuple[Goal, Goal],
) -> None:
    def estimate(values: tuple[Decimal, Decimal, Decimal], unit: str) -> AxisEstimate:
        value, lower, upper = values
        return AxisEstimate(value=value, lower=lower, upper=upper, unit=unit)

    candidate = EvaluatedOffering(
        offering=_offering("robust-property-candidate"),
        axes={
            "x": estimate(candidate_x, "x-unit"),
            "y": estimate(candidate_y, "y-unit"),
        },
    )
    target = EvaluatedOffering(
        offering=_offering("robust-property-target"),
        axes={
            "x": estimate(target_x, "x-unit"),
            "y": estimate(target_y, "y-unit"),
        },
    )
    axes = (
        AxisDescriptor(
            metric="x",
            goal=goals[0],
            unit="x-unit",
            epsilon_absolute=absolute_x,
        ),
        AxisDescriptor(
            metric="y",
            goal=goals[1],
            unit="y-unit",
            epsilon_absolute=absolute_y,
        ),
    )

    _assert_pair_interval_matches_core(candidate, target, axes, UncertaintyMode.ROBUST)


def test_proximity_supports_mixed_goals_negative_values_and_absolute_epsilon() -> None:
    a = _offering("a")
    b = _offering("b")
    snapshot = _snapshot(
        "secondary",
        [(a, "-2", "0"), (b, "-1", "-1")],
        member_ids={"a"},
        goals=(Goal.MINIMIZE, Goal.MAXIMIZE),
        absolute_epsilons=("0.2", "0.1"),
    )

    proximity = build_frontier_proximity_snapshot(snapshot)
    result = next(item for item in proximity.candidates if item.offering == b)

    assert [item.normalized_dominance_slack for item in result.axis_slacks] == [
        Decimal("0.3999999999999999999999999999999998"),
        Decimal("0.9"),
    ]
    assert result.minimal_relative_epsilon == Decimal("0.9")


def test_exact_membership_is_distinct_from_zero_relative_distance() -> None:
    a = _offering("a")
    b = _offering("b")
    snapshot = _snapshot(
        "relative-tolerance-secondary",
        [(a, "1", "1"), (b, "2", "4")],
        # Under the source snapshot's configured relative tolerance B may be a
        # member, while its fixed-absolute first-entry distance remains .75.
        member_ids={"a", "b"},
        relative_epsilons=("0.8", "0.8"),
    )

    proximity = build_frontier_proximity_snapshot(snapshot)
    result = next(item for item in proximity.candidates if item.offering == b)

    assert result.exact_member
    assert result.minimal_relative_epsilon == Decimal("0.7499999999999999999999999999999999")


def test_robust_proximity_uses_pessimistic_and_optimistic_bounds() -> None:
    a = _offering("a")
    b = _offering("b")
    snapshot = _snapshot(
        "robust-secondary",
        [(a, "1", "4"), (b, "2", "2")],
        member_ids={"a"},
        goals=(Goal.MINIMIZE, Goal.MAXIMIZE),
        uncertainty=UncertaintyMode.ROBUST,
        bounds={
            "a": ("0.8", "1.2", "3.8", "4.2"),
            "b": ("1.8", "2.2", "1.8", "2.2"),
        },
    )

    proximity = build_frontier_proximity_snapshot(snapshot)
    result = next(item for item in proximity.candidates if item.offering == b)

    assert result.minimal_relative_epsilon == Decimal("0.4210526315789473684210526315789473")


def test_priority_group_beats_larger_raw_overlap_count(
    example_config: ProjectConfig,
) -> None:
    a = _offering("a")
    b = _offering("b")
    primary = _snapshot(
        "coding-value",
        [(a, "2", "2"), (b, "1", "3")],
        member_ids={"a", "b"},
    )
    high = _snapshot(
        "high-priority",
        [(a, "1", "1"), (b, "2", "2")],
        member_ids={"a"},
        workload_id="cross-workload-high",
    )
    low_one = _snapshot(
        "low-one",
        [(a, "2", "2"), (b, "1", "1")],
        member_ids={"b"},
        workload_id="cross-workload-low-one",
    )
    low_two = _snapshot(
        "low-two",
        [(a, "3", "3"), (b, "1", "1")],
        member_ids={"b"},
        workload_id="cross-workload-low-two",
    )
    high_ref, high_input = _reference(high)
    low_one_ref, low_one_input = _reference(low_one)
    low_two_ref, low_two_input = _reference(low_two)
    policy = _policy(
        ("must-have", (high_ref,)),
        ("nice-to-have", (low_one_ref, low_two_ref)),
    )

    result = select_models_across_frontiers(
        _selection_config(example_config),
        primary,
        "coding-agent-defaults",
        policy,
        {
            high.snapshot_id: high_input,
            low_one.snapshot_id: low_one_input,
            low_two.snapshot_id: low_two_input,
        },
    )

    # A has one overlap and B has two, but A's overlap is in the higher-priority
    # group. Priority groups are lexicographic; raw global coverage is not.
    assert result.default.offering == a
    assert [item.offering.offering_id for item in result.ranked_candidates] == ["a", "b"]
    assert result.snapshot_id == multi_frontier_selection_hash(result)
    assert result.workload.id == "primary-workload"


def test_exact_offering_key_includes_billing_mode_and_missing_ranks_last(
    example_config: ProjectConfig,
) -> None:
    exact = _offering("exact", billing_mode="list")
    route_mismatch = _offering("mismatch", billing_mode="list")
    other_route = _offering("mismatch", billing_mode="managed")
    witness = _offering("witness")
    primary = _snapshot(
        "coding-value",
        [(exact, "2", "2"), (route_mismatch, "1", "3")],
        member_ids={"exact", "mismatch"},
    )
    secondary = _snapshot(
        "secondary",
        [(exact, "2", "2"), (other_route, "3", "3"), (witness, "1", "1")],
        member_ids={"witness"},
    )
    reference, secondary_input = _reference(secondary)

    result = select_models_across_frontiers(
        _selection_config(example_config),
        primary,
        "coding-agent-defaults",
        _policy(("secondary", (reference,))),
        {secondary.snapshot_id: secondary_input},
    )

    # Both primary candidates have zero exact/near count. The exact route is
    # measured and therefore ranks before the same offering_id on another
    # billing route, which is explicitly missing rather than distance zero.
    assert result.default.offering == exact
    evidence = {
        item.offering.offering_id: item.priority_groups[0].frontiers[0]
        for item in result.ranked_candidates
    }
    assert evidence["exact"].measured
    assert not evidence["mismatch"].measured


def test_overlap_reranks_before_provider_diversity(
    example_config: ProjectConfig,
) -> None:
    a = _offering("a", provider="shared")
    b = _offering("b", provider="shared")
    c = _offering("c", provider="independent")
    primary = _snapshot(
        "coding-value",
        [(a, "1", "3"), (b, "2", "2"), (c, "3", "1")],
        member_ids={"a", "b", "c"},
    )
    secondary = _snapshot(
        "secondary",
        [(a, "1", "1"), (b, "2", "2"), (c, "3", "3")],
        member_ids={"a"},
    )
    reference, secondary_input = _reference(secondary, near_epsilon="1")

    result = select_models_across_frontiers(
        _selection_config(example_config, count=2, max_per_provider=1),
        primary,
        "coding-agent-defaults",
        _policy(("secondary", (reference,))),
        {secondary.snapshot_id: secondary_input},
    )

    assert [item.offering.offering_id for item in result.ranked_candidates] == [
        "a",
        "b",
        "c",
    ]
    assert [item.offering.offering_id for item in result.choices] == ["a", "c"]


@given(
    first=st.decimals(min_value=0, max_value=2, places=3, allow_nan=False),
    second=st.decimals(min_value=0, max_value=2, places=3, allow_nan=False),
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_near_membership_is_monotone_in_policy_epsilon(
    first: Decimal,
    second: Decimal,
    example_config: ProjectConfig,
) -> None:
    low, high = sorted((first, second))
    target = _offering("target")
    other = _offering("other")
    witness = _offering("witness")
    primary = _snapshot(
        "coding-value",
        [(target, "1", "2"), (other, "2", "1")],
        member_ids={"target", "other"},
    )
    secondary = _snapshot(
        "secondary",
        [(target, "2", "4"), (witness, "1", "1")],
        member_ids={"witness"},
    )
    proximity = build_frontier_proximity_snapshot(secondary)

    def membership(epsilon: Decimal) -> bool:
        reference = SecondaryFrontierReference(
            frontier_id=secondary.frontier_id,
            frontier_snapshot_id=secondary.snapshot_id,
            frontier_snapshot_hash=secondary.snapshot_id,
            proximity_snapshot_id=proximity.snapshot_id,
            near_epsilon=epsilon,
            max_age_seconds=3600,
        )
        result = select_models_across_frontiers(
            _selection_config(example_config),
            primary,
            "coding-agent-defaults",
            _policy(("secondary", (reference,))),
            {
                secondary.snapshot_id: SecondaryFrontierInput(
                    frontier=secondary,
                    proximity=proximity,
                )
            },
        )
        target_rank = next(item for item in result.ranked_candidates if item.offering == target)
        return target_rank.priority_groups[0].frontiers[0].near_member

    assert not membership(low) or membership(high)


def test_canonical_policy_decimal_and_repeated_builds_have_stable_hashes(
    example_config: ProjectConfig,
) -> None:
    a = _offering("a")
    b = _offering("b")
    primary = _snapshot(
        "coding-value",
        [(a, "1", "2"), (b, "2", "1")],
        member_ids={"a", "b"},
    )
    secondary = _snapshot(
        "secondary",
        [(a, "1", "1"), (b, "2", "2")],
        member_ids={"a"},
    )
    first_ref, first_input = _reference(secondary, near_epsilon="0.050")
    second_ref, second_input = _reference(secondary, near_epsilon="0.05")

    first = select_models_across_frontiers(
        _selection_config(example_config),
        primary,
        "coding-agent-defaults",
        _policy(("secondary", (first_ref,))),
        {secondary.snapshot_id: first_input},
    )
    second = select_models_across_frontiers(
        _selection_config(example_config),
        primary,
        "coding-agent-defaults",
        _policy(("secondary", (second_ref,))),
        {secondary.snapshot_id: second_input},
    )

    assert first.policy_hash == second.policy_hash
    assert first.snapshot_id == second.snapshot_id
    assert (
        build_frontier_proximity_snapshot(secondary).snapshot_id == first_ref.proximity_snapshot_id
    )


def test_secondary_hash_freshness_and_exact_input_set_are_enforced(
    example_config: ProjectConfig,
) -> None:
    a = _offering("a")
    primary = _snapshot("coding-value", [(a, "1", "1")], member_ids={"a"})
    secondary = _snapshot(
        "secondary",
        [(a, "1", "1")],
        member_ids={"a"},
        generated_at=NOW - timedelta(seconds=60),
    )
    reference, secondary_input = _reference(secondary, max_age_seconds=60)
    policy = _policy(("secondary", (reference,)))

    with pytest.raises(ValueError, match="stale"):
        select_models_across_frontiers(
            _selection_config(example_config),
            primary,
            "coding-agent-defaults",
            policy,
            {secondary.snapshot_id: secondary_input},
        )
    with pytest.raises(ValueError, match="exactly match"):
        select_models_across_frontiers(
            _selection_config(example_config),
            primary,
            "coding-agent-defaults",
            policy,
            {},
        )

    tampered = secondary.model_copy(update={"engine_version": "tampered"})
    with pytest.raises(ValueError, match="hash mismatch"):
        select_models_across_frontiers(
            _selection_config(example_config),
            primary,
            "coding-agent-defaults",
            policy,
            {
                secondary.snapshot_id: SecondaryFrontierInput(
                    frontier=tampered,
                    proximity=secondary_input.proximity,
                )
            },
        )


def test_policy_rejects_duplicate_frontier_counting_and_unbounded_near_epsilon() -> None:
    a = _offering("a")
    secondary = _snapshot("secondary", [(a, "1", "1")], member_ids={"a"})
    reference, _ = _reference(secondary)

    with pytest.raises(ValidationError, match="only once"):
        _policy(("one", (reference,)), ("two", (reference,)))
    with pytest.raises(ValidationError):
        SecondaryFrontierReference(
            **{
                **reference.model_dump(mode="python"),
                "near_epsilon": Decimal("2.01"),
            }
        )


def test_generated_overlap_schemas_validate_public_artifacts(
    example_config: ProjectConfig,
) -> None:
    a = _offering("a")
    primary = _snapshot("coding-value", [(a, "1", "1")], member_ids={"a"})
    secondary = _snapshot("secondary", [(a, "1", "1")], member_ids={"a"})
    reference, secondary_input = _reference(secondary, near_epsilon="0.05")
    proximity = secondary_input.proximity
    selection = select_models_across_frontiers(
        _selection_config(example_config),
        primary,
        "coding-agent-defaults",
        _policy(("secondary", (reference,))),
        {secondary.snapshot_id: secondary_input},
    )
    generated = generated_overlap_schemas()
    packaged = public_schemas()
    schemas = {name: packaged[name] for name in generated}

    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    assert schemas == generated
    Draft202012Validator(schemas["frontier-proximity.schema.json"]).validate(
        proximity.model_dump(mode="json")
    )
    Draft202012Validator(schemas["multi-frontier-selection-snapshot.schema.json"]).validate(
        selection.model_dump(mode="json")
    )


def test_epsilon_schema_accepts_only_canonical_grid_strings() -> None:
    schemas = generated_overlap_schemas()
    near_schema = schemas["multi-frontier-selection-snapshot.schema.json"]["$defs"][
        "SecondaryFrontierReference"
    ]["properties"]["near_epsilon"]
    exit_schema = schemas["frontier-proximity.schema.json"]["$defs"]["DominanceInterval"][
        "properties"
    ]["exits_at_epsilon"]
    near_validator = Draft202012Validator(near_schema)
    exit_validator = Draft202012Validator(exit_schema)

    canonical = [
        None,
        "0",
        "0.5",
        "1",
        "1.1234567890123456789012345678901234",
        "2",
    ]
    noncanonical_or_off_grid = [
        ".5",
        "+0.5",
        "00.5",
        "0.50",
        "1.0",
        "2.0",
        "2.0001",
        "0.12345678901234567890123456789012345",
        "1e-1",
        "-0",
    ]

    assert all(near_validator.is_valid(value) for value in canonical)
    assert not any(near_validator.is_valid(value) for value in noncanonical_or_off_grid)
    assert not exit_validator.is_valid("0")
    assert exit_validator.is_valid("0." + "0" * 33 + "1")


def test_snapshot_rejects_a_non_greedy_diverse_choice_prefix(
    example_config: ProjectConfig,
) -> None:
    a = _offering("a", provider="shared")
    b = _offering("b", provider="shared")
    c = _offering("c", provider="independent")
    primary = _snapshot(
        "coding-value",
        [(a, "1", "3"), (b, "2", "2"), (c, "3", "1")],
        member_ids={"a", "b", "c"},
    )
    secondary = _snapshot(
        "secondary",
        [(a, "1", "1"), (b, "2", "2"), (c, "3", "3")],
        member_ids={"a"},
    )
    reference, secondary_input = _reference(secondary, near_epsilon="1")
    result = select_models_across_frontiers(
        _selection_config(example_config, count=2, max_per_provider=1),
        primary,
        "coding-agent-defaults",
        _policy(("secondary", (reference,))),
        {secondary.snapshot_id: secondary_input},
    )

    tampered = result.model_dump(mode="python")
    # [b, c] is ordered and provider-diverse, but the deterministic greedy
    # prefix of [a, b, c] is [a, c].
    tampered["default"] = result.ranked_candidates[1].choice.model_dump(mode="python")
    tampered["fallbacks"] = [result.ranked_candidates[2].choice.model_dump(mode="python")]
    with pytest.raises(ValidationError, match="greedy diverse ranked prefix"):
        type(result).model_validate(tampered)


def test_source_backed_selection_verifier_rejects_validly_rehashed_fabrication(
    example_config: ProjectConfig,
) -> None:
    a = _offering("a")
    b = _offering("b")
    primary = _snapshot(
        "coding-value",
        [(a, "1", "2"), (b, "2", "1")],
        member_ids={"a", "b"},
    )
    secondary = _snapshot(
        "secondary",
        [(a, "1", "1"), (b, "2", "2")],
        member_ids={"a"},
    )
    reference, secondary_input = _reference(secondary)
    config = _selection_config(example_config)
    inputs = {secondary.snapshot_id: secondary_input}
    policy = _policy(("secondary", (reference,)))
    result = select_models_across_frontiers(
        config,
        primary,
        "coding-agent-defaults",
        policy,
        inputs,
    )
    verify_multi_frontier_selection_snapshot(
        config,
        primary,
        result,
        "coding-agent-defaults",
        policy,
        inputs,
        now=result.valid_until,
    )
    with pytest.raises(ValueError, match="expired"):
        verify_multi_frontier_selection_snapshot(
            config,
            primary,
            result,
            "coding-agent-defaults",
            policy,
            inputs,
            now=result.valid_until + timedelta(microseconds=1),
        )
    with pytest.raises(ValueError, match="future-dated"):
        verify_multi_frontier_selection_snapshot(
            config,
            primary,
            result,
            "coding-agent-defaults",
            policy,
            inputs,
            now=NOW - timedelta(minutes=5, seconds=1),
        )
    with pytest.raises(ValueError, match="timezone"):
        verify_multi_frontier_selection_snapshot(
            config,
            primary,
            result,
            "coding-agent-defaults",
            policy,
            inputs,
            now=NOW.replace(tzinfo=None),
        )
    with pytest.raises(ValueError, match="cannot be negative"):
        verify_multi_frontier_selection_snapshot(
            config,
            primary,
            result,
            "coding-agent-defaults",
            policy,
            inputs,
            now=NOW,
            max_clock_skew=timedelta(microseconds=-1),
        )
    verify_multi_frontier_selection_snapshot(
        config,
        primary,
        result,
        "coding-agent-defaults",
        policy,
        inputs,
        now=NOW,
    )

    ranked = list(result.ranked_candidates)
    first = ranked[0]
    fabricated_axes = dict(first.axes)
    fabricated_axes["x"] = fabricated_axes["x"].model_copy(update={"value": Decimal("999")})
    ranked[0] = first.model_copy(update={"axes": fabricated_axes})
    provisional = result.model_copy(
        update={
            "ranked_candidates": tuple(ranked),
            "default": ranked[0].choice,
        }
    )
    fabricated = type(result).model_validate(provisional.model_dump(mode="python"))
    fabricated = fabricated.model_copy(
        update={"snapshot_id": multi_frontier_selection_hash(fabricated)}
    )

    with pytest.raises(ValueError, match="bound source snapshots"):
        verify_multi_frontier_selection_snapshot(
            config,
            primary,
            fabricated,
            "coding-agent-defaults",
            policy,
            inputs,
            now=NOW,
        )
