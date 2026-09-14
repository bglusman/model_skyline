from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from model_skyline.engine import frontier_hash
from model_skyline.models import (
    AxisDescriptor,
    AxisEstimate,
    EvaluatedOffering,
    FrontierSnapshot,
    Goal,
    OfferingKey,
    UncertaintyMode,
    WorkloadReference,
)
from model_skyline.proximity import calculate_frontier_proximity


def _offering(name: str) -> OfferingKey:
    return OfferingKey(
        offering_id=name,
        model_id=f"model-{name}",
        provider="local:test",
        billing_mode="owned_hardware",
        quantization="test",
    )


def _snapshot(
    values: list[tuple[str, str, str]],
    *,
    members: set[str],
    goals: tuple[Goal, Goal] = (Goal.MINIMIZE, Goal.MINIMIZE),
    absolute_epsilons: tuple[str, str] = ("0", "0"),
    relative_epsilons: tuple[str, str] = ("0", "0"),
    uncertainty: UncertaintyMode = UncertaintyMode.POINT,
    bounds: dict[str, tuple[str, str, str, str]] | None = None,
) -> FrontierSnapshot:
    axes = (
        AxisDescriptor(
            metric="x",
            goal=goals[0],
            unit="x",
            epsilon_absolute=absolute_epsilons[0],
            epsilon_relative=relative_epsilons[0],
        ),
        AxisDescriptor(
            metric="y",
            goal=goals[1],
            unit="y",
            epsilon_absolute=absolute_epsilons[1],
            epsilon_relative=relative_epsilons[1],
        ),
    )
    first_member = min(members)
    evaluated = []
    for name, x, y in values:
        item_bounds = (bounds or {}).get(name)
        x_lower, x_upper, y_lower, y_upper = (
            item_bounds if item_bounds is not None else (None, None, None, None)
        )
        evaluated.append(
            EvaluatedOffering(
                offering=_offering(name),
                axes={
                    "x": AxisEstimate(
                        value=x,
                        unit="x",
                        lower=x_lower,
                        upper=x_upper,
                    ),
                    "y": AxisEstimate(
                        value=y,
                        unit="y",
                        lower=y_lower,
                        upper=y_upper,
                    ),
                },
                dominated_by=() if name in members else (first_member,),
            )
        )
    member_values = tuple(item for item in evaluated if item.offering.offering_id in members)
    provisional = FrontierSnapshot(
        snapshot_id="0" * 64,
        config_hash="1" * 64,
        catalog_hash="2" * 64,
        engine_version="test",
        generated_at=datetime(2026, 9, 14, tzinfo=UTC),
        frontier_id="test-frontier",
        workload=WorkloadReference(id="test-workload", version="1", unit="task"),
        order_by="x",
        uncertainty=uncertainty,
        axes=axes,
        members=member_values,
        evaluated=tuple(evaluated),
    )
    return provisional.model_copy(update={"snapshot_id": frontier_hash(provisional)})


def test_proximity_uses_exact_decimal_transition_and_witnesses() -> None:
    snapshot = _snapshot(
        [("a", "1", "1"), ("b", "2", "4")],
        members={"a"},
    )

    by_id = {item.offering.offering_id: item for item in calculate_frontier_proximity(snapshot)}

    assert by_id["a"].exact_member is True
    assert by_id["a"].minimal_relative_epsilon == 0
    assert by_id["b"].exact_member is False
    assert by_id["b"].minimal_relative_epsilon == Decimal("0.7499999999999999999999999999999999")
    assert [item.normalized_dominance_slack for item in by_id["b"].axis_slacks] == [
        Decimal("0.4999999999999999999999999999999998"),
        Decimal("0.7499999999999999999999999999999999"),
    ]
    assert {
        interval.dominator_offering_id for interval in by_id["b"].blocking_dominance_intervals
    } == {"a"}


def test_proximity_follows_connected_non_monotone_dominance_intervals() -> None:
    snapshot = _snapshot(
        [("baseline", "9", "9"), ("later", "8", "10.5"), ("target", "10", "10")],
        members={"baseline", "later"},
    )

    target = next(
        item
        for item in calculate_frontier_proximity(snapshot)
        if item.offering.offering_id == "target"
    )

    assert target.minimal_relative_epsilon == Decimal("0.2")
    assert [interval.dominator_offering_id for interval in target.blocking_dominance_intervals] == [
        "baseline",
        "later",
    ]


def test_robust_proximity_uses_pessimistic_and_optimistic_bounds() -> None:
    snapshot = _snapshot(
        [("a", "1", "1"), ("b", "2", "2")],
        members={"a"},
        uncertainty=UncertaintyMode.ROBUST,
        bounds={
            "a": ("0.8", "1.2", "0.8", "1.2"),
            "b": ("1.8", "2.2", "1.8", "2.2"),
        },
    )

    target = next(
        item for item in calculate_frontier_proximity(snapshot) if item.offering.offering_id == "b"
    )

    assert target.minimal_relative_epsilon == Decimal("0.3333333333333333333333333333333331")


def test_proximity_supports_mixed_goals_negative_values_and_absolute_epsilon() -> None:
    snapshot = _snapshot(
        [("a", "-2", "0"), ("b", "-1", "-1")],
        members={"a"},
        goals=(Goal.MINIMIZE, Goal.MAXIMIZE),
        absolute_epsilons=("0.2", "0.1"),
    )

    target = next(
        item for item in calculate_frontier_proximity(snapshot) if item.offering.offering_id == "b"
    )

    assert [item.normalized_dominance_slack for item in target.axis_slacks] == [
        Decimal("0.3999999999999999999999999999999998"),
        Decimal("0.9"),
    ]
    assert target.minimal_relative_epsilon == Decimal("0.9")


def test_exact_membership_is_separate_from_zero_relative_distance() -> None:
    snapshot = _snapshot(
        [("a", "1", "1"), ("b", "2", "4")],
        members={"a", "b"},
        relative_epsilons=("0.8", "0.8"),
    )

    target = next(
        item for item in calculate_frontier_proximity(snapshot) if item.offering.offering_id == "b"
    )

    assert target.exact_member is True
    assert target.minimal_relative_epsilon == Decimal("0.7499999999999999999999999999999999")


def test_proximity_rejects_a_tampered_snapshot_hash() -> None:
    snapshot = _snapshot([("a", "1", "1")], members={"a"})

    with pytest.raises(ValueError, match="hash mismatch"):
        calculate_frontier_proximity(snapshot.model_copy(update={"snapshot_id": "f" * 64}))
