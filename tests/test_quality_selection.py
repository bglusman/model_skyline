from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from model_skyline.canonical import content_hash
from model_skyline.engine import dominates, frontier_hash
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
from model_skyline.quality_bundle import (
    QualityBundleComponent,
    QualityBundlePolicy,
    QualityBundleSnapshot,
    build_quality_bundle_snapshot,
)
from model_skyline.quality_selection import (
    QualityGatedSelectionSnapshot,
    build_quality_gated_selection_snapshot,
    quality_gated_selection_hash,
    verify_quality_gated_selection_snapshot,
)
from model_skyline.selection_overlap import (
    CrossFrontierSelectionPolicy,
    FrontierPriorityGroup,
    SecondaryFrontierInput,
    SecondaryFrontierReference,
    build_frontier_proximity_snapshot,
)

NOW = datetime(2026, 8, 31, 20, tzinfo=UTC)


def _offering(name: str) -> OfferingKey:
    return OfferingKey(
        offering_id=name,
        model_id=f"model-{name}",
        provider=f"provider-{name}",
        endpoint="responses",
        billing_mode="list",
        region="us-east",
        service_tier="standard",
        quantization="hosted-native",
        reasoning_effort="medium",
        agent_harness="quality-selection-test",
        capabilities=("tools",),
    )


def _frontier(
    frontier_id: str,
    first_metric: str,
    second_metric: str,
    rows: tuple[tuple[OfferingKey, str, str], ...],
    *,
    generated_at: datetime,
    workload_id: str | None = None,
) -> FrontierSnapshot:
    axes = (
        AxisDescriptor(metric=first_metric, goal=Goal.MAXIMIZE, unit="ratio"),
        AxisDescriptor(metric=second_metric, goal=Goal.MINIMIZE, unit="USD/task"),
    )
    bare = tuple(
        EvaluatedOffering(
            offering=offering,
            axes={
                first_metric: AxisEstimate(value=first, unit="ratio"),
                second_metric: AxisEstimate(value=second, unit="USD/task"),
            },
        )
        for offering, first, second in rows
    )
    evaluated = tuple(
        other.model_copy(
            update={
                "dominated_by": tuple(
                    sorted(
                        candidate.offering.offering_id
                        for candidate in bare
                        if candidate is not other
                        and dominates(candidate, other, axes, UncertaintyMode.POINT)
                    )
                )
            }
        )
        for other in bare
    )
    provisional = FrontierSnapshot(
        snapshot_id="0" * 64,
        config_hash="1" * 64,
        catalog_hash="2" * 64,
        engine_version="quality-selection-test",
        generated_at=generated_at,
        frontier_id=frontier_id,
        workload=WorkloadReference(
            id=workload_id or f"{frontier_id}-workload",
            version="1",
            unit="task",
        ),
        order_by=second_metric,
        uncertainty="point",
        axes=axes,
        members=tuple(item for item in evaluated if not item.dominated_by),
        evaluated=evaluated,
    )
    return provisional.model_copy(update={"snapshot_id": frontier_hash(provisional)})


def _component(component_id: str, frontier: FrontierSnapshot) -> QualityBundleComponent:
    return QualityBundleComponent(
        component_id=component_id,
        frontier_id=frontier.frontier_id,
        frontier_snapshot_id=frontier.snapshot_id,
        frontier_snapshot_hash=frontier.snapshot_id,
        config_hash=frontier.config_hash,
        catalog_hash=frontier.catalog_hash,
        workload=frontier.workload,
        axes=frontier.axes,
        quality_metric=frontier.axes[0].metric,
        max_age_seconds=3600,
    )


def _secondary(
    frontier: FrontierSnapshot,
) -> tuple[SecondaryFrontierReference, SecondaryFrontierInput]:
    proximity = build_frontier_proximity_snapshot(frontier)
    return (
        SecondaryFrontierReference(
            frontier_id=frontier.frontier_id,
            frontier_snapshot_id=frontier.snapshot_id,
            frontier_snapshot_hash=frontier.snapshot_id,
            proximity_snapshot_id=proximity.snapshot_id,
            max_age_seconds=3600,
        ),
        SecondaryFrontierInput(frontier=frontier, proximity=proximity),
    )


def _config() -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "schema_version": "model-skyline/v1alpha1",
            "workloads": {
                "primary-workload": {
                    "unit": "task",
                    "version": "1",
                    "harness": "quality-selection-test",
                    "cohort": "all-primary-routes",
                }
            },
            "metrics": {
                "primary_quality": {
                    "kind": "signal",
                    "signal": "primary_quality",
                    "unit": "ratio",
                },
                "primary_cost": {
                    "kind": "signal",
                    "signal": "primary_cost",
                    "unit": "USD/task",
                },
            },
            "frontiers": {
                "primary": {
                    "workload": "primary-workload",
                    "axes": [
                        {"metric": "primary_quality", "goal": "maximize"},
                        {"metric": "primary_cost", "goal": "minimize"},
                    ],
                    "order_by": "primary_cost",
                }
            },
            "selections": {
                "agent-defaults": {
                    "frontier": "primary",
                    "count": 2,
                    "order_by": "primary_cost",
                    "snapshot_ttl_seconds": 1800,
                    "on_insufficient": "error",
                }
            },
        }
    )


@dataclass(frozen=True)
class _Scenario:
    cheap: OfferingKey
    alpha: OfferingKey
    beta: OfferingKey
    config: ProjectConfig
    quality_policy: QualityBundlePolicy
    component_frontiers: dict[str, FrontierSnapshot]
    quality_candidates: tuple[OfferingKey, ...]
    quality_bundle: QualityBundleSnapshot
    source_primary: FrontierSnapshot
    overlap_policy: CrossFrontierSelectionPolicy
    secondary_inputs: dict[str, SecondaryFrontierInput]
    result: QualityGatedSelectionSnapshot


def _scenario() -> _Scenario:
    cheap = _offering("cheap-ineligible-dominator")
    alpha = _offering("alpha")
    beta = _offering("beta")
    quality_one = _frontier(
        "quality-one",
        "quality_one_score",
        "quality_one_cost",
        (
            (cheap, "0.99", "0.10"),
            (alpha, "0.85", "1.00"),
            (beta, "0.90", "1.20"),
        ),
        generated_at=NOW - timedelta(minutes=10),
    )
    quality_two = _frontier(
        "quality-two",
        "quality_two_score",
        "quality_two_cost",
        (
            (alpha, "0.88", "1.10"),
            (beta, "0.92", "1.00"),
        ),
        generated_at=NOW - timedelta(minutes=9),
    )
    component_frontiers = {"one": quality_one, "two": quality_two}
    quality_policy = QualityBundlePolicy(
        bundle_id="general-agent-quality",
        version="1",
        components=tuple(
            _component(component_id, frontier)
            for component_id, frontier in component_frontiers.items()
        ),
        required_component_ids=("one", "two"),
        minimum_measured_components=2,
    )
    quality_candidates = (cheap, alpha, beta)
    quality_bundle = build_quality_bundle_snapshot(
        quality_policy,
        component_frontiers,
        quality_candidates,
        generated_at=NOW,
    )
    source_primary = _frontier(
        "primary",
        "primary_quality",
        "primary_cost",
        (
            (cheap, "1.00", "0.10"),
            (alpha, "0.80", "1.00"),
            (beta, "0.90", "2.00"),
        ),
        generated_at=NOW - timedelta(minutes=5),
        workload_id="primary-workload",
    )
    one_reference, one_input = _secondary(quality_one)
    two_reference, two_input = _secondary(quality_two)
    overlap_policy = CrossFrontierSelectionPolicy(
        priority_groups=(
            FrontierPriorityGroup(
                name="quality-benchmarks",
                frontiers=(one_reference, two_reference),
            ),
        )
    )
    secondary_inputs = {
        quality_one.snapshot_id: one_input,
        quality_two.snapshot_id: two_input,
    }
    config = _config()
    result = build_quality_gated_selection_snapshot(
        config,
        quality_policy,
        quality_bundle,
        source_primary,
        "agent-defaults",
        overlap_policy,
        secondary_inputs,
        generated_at=NOW + timedelta(minutes=1),
    )
    return _Scenario(
        cheap=cheap,
        alpha=alpha,
        beta=beta,
        config=config,
        quality_policy=quality_policy,
        component_frontiers=component_frontiers,
        quality_candidates=quality_candidates,
        quality_bundle=quality_bundle,
        source_primary=source_primary,
        overlap_policy=overlap_policy,
        secondary_inputs=secondary_inputs,
        result=result,
    )


def _verify(scenario: _Scenario, *, now: datetime) -> None:
    verify_quality_gated_selection_snapshot(
        scenario.config,
        scenario.quality_policy,
        scenario.component_frontiers,
        scenario.quality_candidates,
        scenario.quality_bundle,
        scenario.source_primary,
        scenario.result,
        "agent-defaults",
        scenario.overlap_policy,
        scenario.secondary_inputs,
        now=now,
    )


def test_gate_recomputes_pareto_before_overlap_selection() -> None:
    scenario = _scenario()
    result = scenario.result

    assert scenario.source_primary.members[0].offering == scenario.cheap
    assert {item.offering for item in result.gated_primary_frontier.members} == {
        scenario.alpha,
        scenario.beta,
    }
    assert all(item.offering != scenario.cheap for item in result.gated_primary_frontier.evaluated)
    assert all(not item.dominated_by for item in result.gated_primary_frontier.members)
    assert scenario.cheap not in {item.offering for item in result.selection.ranked_candidates}
    assert scenario.cheap not in {choice.offering for choice in result.choices}
    # The excluded cheap route dominates both eligible routes in the original
    # first benchmark. Recomputing the secondary feasible set makes both exact
    # members there. The second benchmark then prefers beta, reversing the
    # primary cost order (alpha before beta) in the final default.
    assert scenario.component_frontiers["one"].members[0].offering == scenario.cheap
    evidence_by_offering = {
        candidate.offering: candidate for candidate in result.selection.ranked_candidates
    }
    assert evidence_by_offering[scenario.alpha].priority_groups[0].exact_memberships == 1
    assert evidence_by_offering[scenario.beta].priority_groups[0].exact_memberships == 2
    assert [candidate.offering for candidate in result.selection.ranked_candidates] == [
        scenario.beta,
        scenario.alpha,
    ]
    assert result.default.offering == scenario.beta
    assert scenario.source_primary.order_by == "primary_cost"
    assert scenario.source_primary.evaluated[1].offering == scenario.alpha
    assert scenario.source_primary.evaluated[2].offering == scenario.beta
    assert (
        scenario.source_primary.evaluated[1].axes["primary_cost"].value
        < scenario.source_primary.evaluated[2].axes["primary_cost"].value
    )
    assert result.gated_primary_frontier.config_hash != scenario.source_primary.config_hash
    assert result.gated_primary_frontier.catalog_hash != scenario.source_primary.catalog_hash
    assert result.quality_bundle_id == scenario.quality_policy.bundle_id
    assert result.selection_id == "agent-defaults"
    assert result.frontier_id == "primary"
    assert result.default == result.selection.default
    assert result.fallbacks == result.selection.fallbacks
    assert result.snapshot_id == quality_gated_selection_hash(result)
    assert result.valid_until == min(
        scenario.quality_bundle.valid_until,
        result.source_primary_valid_until,
        result.selection.valid_until,
    )
    assert result.quality_bundle_generated_at > result.source_primary_generated_at
    _verify(scenario, now=NOW + timedelta(minutes=2))


def test_gate_requires_complete_quality_coverage_records_for_primary_universe() -> None:
    scenario = _scenario()
    incomplete_bundle = build_quality_bundle_snapshot(
        scenario.quality_policy,
        scenario.component_frontiers,
        (scenario.alpha, scenario.beta),
        generated_at=NOW,
    )

    with pytest.raises(ValueError, match="does not cover every source primary offering"):
        build_quality_gated_selection_snapshot(
            scenario.config,
            scenario.quality_policy,
            incomplete_bundle,
            scenario.source_primary,
            "agent-defaults",
            scenario.overlap_policy,
            scenario.secondary_inputs,
            generated_at=NOW + timedelta(minutes=1),
        )


def test_gate_rejects_rehashed_bundle_that_forges_missing_component_measurement() -> None:
    scenario = _scenario()
    payload = scenario.quality_bundle.model_dump(mode="json")
    cheap_coverage = next(
        candidate
        for candidate in payload["candidates"]
        if candidate["offering"]["offering_id"] == scenario.cheap.offering_id
    )
    cheap_second = next(
        component
        for component in cheap_coverage["components"]
        if component["component_id"] == "two"
    )
    alpha_coverage = next(
        candidate
        for candidate in payload["candidates"]
        if candidate["offering"]["offering_id"] == scenario.alpha.offering_id
    )
    alpha_second = next(
        component
        for component in alpha_coverage["components"]
        if component["component_id"] == "two"
    )

    # The second source frontier has no cheap route. Forge a self-consistent,
    # content-addressed bundle that claims the route received alpha's measurement.
    cheap_second["status"] = "measured"
    cheap_second["estimate"] = alpha_second["estimate"]
    cheap_coverage["measured_component_count"] = 2
    cheap_coverage["missing_component_ids"] = []
    cheap_coverage["failed_required_component_ids"] = []
    cheap_coverage["eligible"] = True
    payload["snapshot_id"] = content_hash(
        {key: value for key, value in payload.items() if key != "snapshot_id"}
    )
    forged_bundle = QualityBundleSnapshot.model_validate(payload)

    # A one-choice policy makes the pre-fix behavior observable: the forged
    # cheap route dominates both legitimate primary routes and becomes default.
    config_payload = scenario.config.model_dump(mode="json")
    config_payload["selections"]["agent-defaults"]["count"] = 1
    single_choice_config = ProjectConfig.model_validate(config_payload)

    with pytest.raises(ValueError, match="does not match its source frontiers"):
        build_quality_gated_selection_snapshot(
            single_choice_config,
            scenario.quality_policy,
            forged_bundle,
            scenario.source_primary,
            "agent-defaults",
            scenario.overlap_policy,
            scenario.secondary_inputs,
            generated_at=NOW + timedelta(minutes=1),
        )


def test_gate_requires_every_quality_component_in_overlap_policy() -> None:
    scenario = _scenario()
    first_reference = scenario.overlap_policy.priority_groups[0].frontiers[0]
    incomplete_policy = CrossFrontierSelectionPolicy(
        priority_groups=(
            FrontierPriorityGroup(name="quality-benchmarks", frontiers=(first_reference,)),
        )
    )

    with pytest.raises(ValueError, match="omits quality component 'two'"):
        build_quality_gated_selection_snapshot(
            scenario.config,
            scenario.quality_policy,
            scenario.quality_bundle,
            scenario.source_primary,
            "agent-defaults",
            incomplete_policy,
            {
                first_reference.frontier_snapshot_id: scenario.secondary_inputs[
                    first_reference.frontier_snapshot_id
                ]
            },
            generated_at=NOW + timedelta(minutes=1),
        )


def test_source_backed_verifier_rejects_bundle_source_and_identity_tampering() -> None:
    scenario = _scenario()

    tampered_bundle = scenario.quality_bundle.model_copy(update={"snapshot_id": "f" * 64})
    with pytest.raises(ValueError, match="snapshot hash mismatch"):
        verify_quality_gated_selection_snapshot(
            scenario.config,
            scenario.quality_policy,
            scenario.component_frontiers,
            scenario.quality_candidates,
            tampered_bundle,
            scenario.source_primary,
            scenario.result,
            "agent-defaults",
            scenario.overlap_policy,
            scenario.secondary_inputs,
            now=NOW + timedelta(minutes=1),
        )

    changed_source = scenario.source_primary.model_copy(update={"engine_version": "other"})
    changed_source = changed_source.model_copy(
        update={"snapshot_id": frontier_hash(changed_source)}
    )
    with pytest.raises(ValueError, match="different source primary frontier"):
        verify_quality_gated_selection_snapshot(
            scenario.config,
            scenario.quality_policy,
            scenario.component_frontiers,
            scenario.quality_candidates,
            scenario.quality_bundle,
            changed_source,
            scenario.result,
            "agent-defaults",
            scenario.overlap_policy,
            scenario.secondary_inputs,
            now=NOW + timedelta(minutes=1),
        )

    provisional = scenario.result.model_copy(
        update={"quality_bundle_id": "different-quality-bundle"}
    )
    fabricated = QualityGatedSelectionSnapshot.model_validate(
        provisional.model_dump(mode="json")
        | {
            "snapshot_id": content_hash(
                provisional.model_dump(mode="json", exclude={"snapshot_id"})
            )
        }
    )
    with pytest.raises(ValueError, match="quality bundle identity mismatch"):
        verify_quality_gated_selection_snapshot(
            scenario.config,
            scenario.quality_policy,
            scenario.component_frontiers,
            scenario.quality_candidates,
            scenario.quality_bundle,
            scenario.source_primary,
            fabricated,
            "agent-defaults",
            scenario.overlap_policy,
            scenario.secondary_inputs,
            now=NOW + timedelta(minutes=1),
        )


def test_builder_rejects_stale_primary_and_future_inputs() -> None:
    scenario = _scenario()

    with pytest.raises(ValueError, match="source primary frontier is stale"):
        build_quality_gated_selection_snapshot(
            scenario.config,
            scenario.quality_policy,
            scenario.quality_bundle,
            scenario.source_primary,
            "agent-defaults",
            scenario.overlap_policy,
            scenario.secondary_inputs,
            generated_at=scenario.result.source_primary_valid_until,
        )
    with pytest.raises(ValueError, match="source primary frontier is future-dated"):
        build_quality_gated_selection_snapshot(
            scenario.config,
            scenario.quality_policy,
            scenario.quality_bundle,
            scenario.source_primary,
            "agent-defaults",
            scenario.overlap_policy,
            scenario.secondary_inputs,
            generated_at=scenario.source_primary.generated_at - timedelta(microseconds=1),
        )
    with pytest.raises(ValueError, match="quality bundle snapshot is future-dated"):
        build_quality_gated_selection_snapshot(
            scenario.config,
            scenario.quality_policy,
            scenario.quality_bundle,
            scenario.source_primary,
            "agent-defaults",
            scenario.overlap_policy,
            scenario.secondary_inputs,
            generated_at=scenario.quality_bundle.generated_at - timedelta(microseconds=1),
        )


def test_verifier_rejects_expired_and_future_artifacts() -> None:
    scenario = _scenario()

    _verify(
        scenario,
        now=scenario.result.generated_at - timedelta(minutes=4),
    )
    with pytest.raises(ValueError, match="expired"):
        _verify(scenario, now=scenario.result.valid_until)
    with pytest.raises(ValueError, match="future-dated"):
        _verify(
            scenario,
            now=scenario.result.generated_at - timedelta(minutes=5, microseconds=1),
        )
