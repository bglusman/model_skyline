from __future__ import annotations

from datetime import UTC, datetime, timedelta

from model_skyline.engine import frontier_hash
from model_skyline.models import (
    AxisDescriptor,
    AxisEstimate,
    EvaluatedOffering,
    FrontierSnapshot,
    Goal,
    OfferingKey,
    ProjectConfig,
    WorkloadReference,
)
from model_skyline.quality_bundle import (
    QualityBundleComponent,
    QualityBundlePolicy,
    build_quality_bundle_snapshot,
    eligible_quality_bundle_candidates,
)
from model_skyline.selection_overlap import (
    CrossFrontierSelectionPolicy,
    FrontierPriorityGroup,
    SecondaryFrontierInput,
    SecondaryFrontierReference,
    build_frontier_proximity_snapshot,
    select_models_across_frontiers,
)

NOW = datetime(2026, 8, 31, 20, tzinfo=UTC)


def _offering(offering_id: str, *, service_tier: str) -> OfferingKey:
    """Return a complete production-route identity, not a benchmark model label."""

    return OfferingKey(
        offering_id=offering_id,
        model_id=f"model-{offering_id}",
        provider="example-provider",
        endpoint="responses",
        billing_mode="list",
        region="us-east",
        service_tier=service_tier,
        quantization="hosted-native",
        reasoning_effort="medium",
        agent_harness="gateway-policy-v1",
        capabilities=("structured_output", "tools"),
    )


def _frontier(
    frontier_id: str,
    quality_metric: str,
    cost_metric: str,
    rows: tuple[tuple[OfferingKey, str, str, tuple[str, ...]], ...],
    *,
    generated_at: datetime,
) -> FrontierSnapshot:
    axes = (
        AxisDescriptor(metric=quality_metric, goal=Goal.MAXIMIZE, unit="ratio"),
        AxisDescriptor(metric=cost_metric, goal=Goal.MINIMIZE, unit="USD/task"),
    )
    evaluated = tuple(
        EvaluatedOffering(
            offering=offering,
            axes={
                quality_metric: AxisEstimate(value=quality, unit="ratio"),
                cost_metric: AxisEstimate(value=cost, unit="USD/task"),
            },
            dominated_by=dominated_by,
        )
        for offering, quality, cost, dominated_by in rows
    )
    provisional = FrontierSnapshot(
        snapshot_id="0" * 64,
        config_hash="1" * 64,
        catalog_hash="2" * 64,
        engine_version="integration-test",
        generated_at=generated_at,
        frontier_id=frontier_id,
        workload=WorkloadReference(
            id=f"{frontier_id}-workload",
            version="1",
            unit="task",
        ),
        order_by=cost_metric,
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
    *,
    near_epsilon: str | None = None,
) -> tuple[SecondaryFrontierReference, SecondaryFrontierInput]:
    proximity = build_frontier_proximity_snapshot(frontier)
    return (
        SecondaryFrontierReference(
            frontier_id=frontier.frontier_id,
            frontier_snapshot_id=frontier.snapshot_id,
            frontier_snapshot_hash=frontier.snapshot_id,
            proximity_snapshot_id=proximity.snapshot_id,
            near_epsilon=near_epsilon,
            max_age_seconds=3600,
        ),
        SecondaryFrontierInput(frontier=frontier, proximity=proximity),
    )


def _selection_config() -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "schema_version": "model-skyline/v1alpha1",
            "workloads": {
                "quality-gated-workload": {
                    "unit": "task",
                    "version": "1",
                    "harness": "selection-policy-v1",
                    "cohort": "quality-eligible-candidates-v1",
                }
            },
            "metrics": {
                "selection_quality": {
                    "kind": "signal",
                    "signal": "selection_quality",
                    "unit": "ratio",
                },
                "selection_cost": {
                    "kind": "signal",
                    "signal": "selection_cost",
                    "unit": "USD/task",
                },
            },
            "frontiers": {
                "quality-gated-candidates": {
                    "workload": "quality-gated-workload",
                    "axes": [
                        {"metric": "selection_quality", "goal": "maximize"},
                        {"metric": "selection_cost", "goal": "minimize"},
                    ],
                    "order_by": "selection_cost",
                }
            },
            "selections": {
                "agent-defaults": {
                    "frontier": "quality-gated-candidates",
                    "count": 2,
                    "order_by": "selection_cost",
                    "snapshot_ttl_seconds": 3600,
                    "on_insufficient": "error",
                }
            },
        }
    )


def test_quality_coverage_gates_candidates_before_overlap_proximity_ranking() -> None:
    critical_member = _offering("critical-member", service_tier="priority")
    broad_member = _offering("broad-member", service_tier="standard")
    sparse_but_cheap = _offering("sparse-but-cheap", service_tier="batch")

    # These are independent quality/cost snapshots. sparse_but_cheap is measured
    # on two benchmarks but absent from tau2, while the other routes have all
    # three exact full-OfferingKey measurements.
    swe = _frontier(
        "swe-bench-quality-cost",
        "swe_quality",
        "swe_cost",
        (
            (critical_member, "0.90", "1.00", ()),
            (broad_member, "0.80", "1.10", (critical_member.offering_id,)),
            (sparse_but_cheap, "0.95", "1.20", ()),
        ),
        generated_at=NOW - timedelta(minutes=5),
    )
    harbor = _frontier(
        "harbor-quality-cost",
        "harbor_quality",
        "harbor_cost",
        (
            (broad_member, "0.90", "1.00", ()),
            (critical_member, "0.80", "1.20", (broad_member.offering_id,)),
            (sparse_but_cheap, "0.70", "1.30", (broad_member.offering_id,)),
        ),
        generated_at=NOW - timedelta(minutes=4),
    )
    tau2 = _frontier(
        "tau2-quality-cost",
        "tau2_quality",
        "tau2_cost",
        (
            (broad_member, "0.92", "1.00", ()),
            (critical_member, "0.85", "1.10", (broad_member.offering_id,)),
        ),
        generated_at=NOW - timedelta(minutes=3),
    )
    component_frontiers = {"swe": swe, "harbor": harbor, "tau2": tau2}
    bundle_policy = QualityBundlePolicy(
        bundle_id="general-agent-quality",
        version="1",
        components=tuple(
            _component(component_id, frontier)
            for component_id, frontier in component_frontiers.items()
        ),
        required_component_ids=("swe", "harbor", "tau2"),
        minimum_measured_components=3,
    )
    bundle = build_quality_bundle_snapshot(
        bundle_policy,
        component_frontiers,
        (sparse_but_cheap, broad_member, critical_member),
        generated_at=NOW,
    )

    eligible_records = eligible_quality_bundle_candidates(bundle, now=NOW)
    eligible_offerings = tuple(record.offering for record in eligible_records)
    assert set(eligible_offerings) == {critical_member, broad_member}
    sparse_coverage = next(
        record for record in bundle.candidates if record.offering == sparse_but_cheap
    )
    assert not sparse_coverage.eligible
    assert sparse_coverage.failed_required_component_ids == ("tau2",)

    # Build the primary candidate frontier from the hard-gated exact identities.
    # The excluded route would otherwise have the best primary price and score.
    primary_values = {
        critical_member: ("0.90", "1.10"),
        broad_member: ("0.85", "1.00"),
        sparse_but_cheap: ("0.99", "0.10"),
    }
    primary = _frontier(
        "quality-gated-candidates",
        "selection_quality",
        "selection_cost",
        tuple((offering, *primary_values[offering], ()) for offering in eligible_offerings),
        generated_at=NOW,
    )

    swe_reference, swe_input = _secondary(swe, near_epsilon="0.2")
    harbor_reference, harbor_input = _secondary(harbor)
    tau2_reference, tau2_input = _secondary(tau2)
    overlap_policy = CrossFrontierSelectionPolicy(
        priority_groups=(
            FrontierPriorityGroup(name="critical", frontiers=(swe_reference,)),
            FrontierPriorityGroup(
                name="supporting",
                frontiers=(harbor_reference, tau2_reference),
            ),
        )
    )
    secondary_inputs = {
        swe.snapshot_id: swe_input,
        harbor.snapshot_id: harbor_input,
        tau2.snapshot_id: tau2_input,
    }
    selection = select_models_across_frontiers(
        _selection_config(),
        primary,
        "agent-defaults",
        overlap_policy,
        secondary_inputs,
    )

    assert {record.offering for record in selection.ranked_candidates} == set(eligible_offerings)
    assert sparse_but_cheap not in {record.offering for record in selection.ranked_candidates}
    assert [record.offering for record in selection.ranked_candidates] == [
        critical_member,
        broad_member,
    ]
    assert [choice.offering for choice in selection.choices] == [
        critical_member,
        broad_member,
    ]

    critical_evidence, broad_evidence = selection.ranked_candidates
    assert critical_evidence.priority_groups[0].exact_memberships == 1
    assert broad_evidence.priority_groups[0].near_memberships == 1
    assert critical_evidence.priority_groups[1].exact_memberships == 0
    assert broad_evidence.priority_groups[1].exact_memberships == 2
    # Priority groups are lexicographic: one exact critical membership wins
    # before the supporting group's two exact memberships are considered.
    assert selection.default.offering == critical_member
