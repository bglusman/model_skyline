from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from model_skyline.canonical import content_hash
from model_skyline.engine import frontier_hash
from model_skyline.models import (
    AxisDescriptor,
    AxisEstimate,
    EvaluatedOffering,
    FrontierSnapshot,
    Goal,
    OfferingKey,
    RejectedOffering,
    WorkloadReference,
)
from model_skyline.quality_bundle import (
    QualityBundleComponent,
    QualityBundlePolicy,
    QualityBundleQuarantine,
    QualityBundleSnapshot,
    QualityCoverageStatus,
    build_quality_bundle_snapshot,
    eligible_quality_bundle_candidates,
    quality_bundle_policy_hash,
    quality_bundle_snapshot_hash,
    verify_quality_bundle_snapshot,
)

NOW = datetime(2026, 8, 31, 18, tzinfo=UTC)
QUALITY_AXIS = AxisDescriptor(metric="quality", goal=Goal.MAXIMIZE, unit="ratio")
COST_AXIS = AxisDescriptor(metric="cost", goal=Goal.MINIMIZE, unit="USD/task")
AXES = (QUALITY_AXIS, COST_AXIS)


def _offering(
    offering_id: str,
    *,
    provider: str = "provider",
    service_tier: str | None = None,
    agent_harness: str | None = None,
) -> OfferingKey:
    return OfferingKey(
        offering_id=offering_id,
        model_id=f"model-{offering_id}",
        provider=provider,
        endpoint="responses",
        billing_mode="list",
        region="us",
        service_tier=service_tier,
        quantization="native",
        reasoning_effort="medium",
        agent_harness=agent_harness,
        capabilities=("text",),
    )


def _frontier(
    frontier_id: str,
    measured: list[tuple[OfferingKey, str]],
    *,
    generated_at: datetime = NOW - timedelta(minutes=10),
    workload: WorkloadReference | None = None,
    axes: tuple[AxisDescriptor, AxisDescriptor] = AXES,
    config_hash: str = "1" * 64,
    catalog_hash: str = "2" * 64,
) -> FrontierSnapshot:
    workload = workload or WorkloadReference(
        id=f"{frontier_id}-workload",
        version="1",
        unit="task",
    )
    quality_metric = axes[0].metric
    paired_metric = axes[1].metric
    evaluated = tuple(
        EvaluatedOffering(
            offering=offering,
            axes={
                quality_metric: AxisEstimate(value=Decimal(value), unit=axes[0].unit),
                paired_metric: AxisEstimate(value=Decimal(index + 1), unit=axes[1].unit),
            },
        )
        for index, (offering, value) in enumerate(measured)
    )
    provisional = FrontierSnapshot(
        snapshot_id="0" * 64,
        config_hash=config_hash,
        catalog_hash=catalog_hash,
        engine_version="test",
        generated_at=generated_at,
        frontier_id=frontier_id,
        workload=workload,
        order_by=quality_metric,
        uncertainty="point",
        axes=axes,
        members=evaluated,
        evaluated=evaluated,
    )
    return provisional.model_copy(update={"snapshot_id": frontier_hash(provisional)})


def _component(
    component_id: str,
    frontier: FrontierSnapshot,
    *,
    max_age_seconds: int = 3600,
    evidence_valid_until: datetime | None = None,
) -> QualityBundleComponent:
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
        max_age_seconds=max_age_seconds,
        evidence_valid_until=evidence_valid_until,
    )


def _policy(
    frontiers: dict[str, FrontierSnapshot],
    *,
    required: tuple[str, ...],
    minimum: int,
    max_age_seconds: int = 3600,
) -> QualityBundlePolicy:
    return QualityBundlePolicy(
        bundle_id="general-quality",
        version="1",
        components=tuple(
            _component(component_id, frontier, max_age_seconds=max_age_seconds)
            for component_id, frontier in frontiers.items()
        ),
        required_component_ids=required,
        minimum_measured_components=minimum,
    )


def _coverage_by_id(snapshot: QualityBundleSnapshot) -> dict[str, Any]:
    return {candidate.offering.offering_id: candidate for candidate in snapshot.candidates}


def _rehash_frontier(frontier: FrontierSnapshot, **updates: object) -> FrontierSnapshot:
    provisional = frontier.model_copy(update={**updates, "snapshot_id": "0" * 64})
    return provisional.model_copy(update={"snapshot_id": frontier_hash(provisional)})


def _policy_with_component(
    policy: QualityBundlePolicy,
    index: int,
    replacement: QualityBundleComponent,
) -> QualityBundlePolicy:
    payload = policy.model_dump(mode="json")
    payload["components"][index] = replacement.model_dump(mode="json")
    return QualityBundlePolicy.model_validate(payload)


def test_bundle_enforces_required_components_and_minimum_coverage() -> None:
    a = _offering("a")
    b = _offering("b")
    c = _offering("c")
    frontiers = {
        "coding": _frontier("coding-quality", [(a, "0.9"), (b, "0.8")]),
        "reasoning": _frontier("reasoning-quality", [(a, "0.7"), (c, "0.6")]),
        "tools": _frontier("tools-quality", [(a, "0.8"), (b, "0.7"), (c, "0.9")]),
    }
    policy = _policy(
        frontiers,
        required=("coding", "reasoning"),
        minimum=3,
    )

    snapshot = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [c, a, b],
        generated_at=NOW,
    )
    coverage = _coverage_by_id(snapshot)

    assert [candidate.offering.offering_id for candidate in snapshot.candidates] == [
        "a",
        "b",
        "c",
    ]
    assert coverage["a"].eligible
    assert coverage["a"].measured_component_count == 3
    assert not coverage["b"].eligible
    assert coverage["b"].missing_component_ids == ("reasoning",)
    assert coverage["b"].failed_required_component_ids == ("reasoning",)
    assert not coverage["c"].eligible
    assert coverage["c"].failed_required_component_ids == ("coding",)
    assert [item.offering for item in eligible_quality_bundle_candidates(snapshot, now=NOW)] == [a]
    assert snapshot.policy_hash == quality_bundle_policy_hash(policy)
    assert snapshot.snapshot_id == quality_bundle_snapshot_hash(snapshot)


def test_minimum_measured_count_is_a_hard_gate_beyond_required_components() -> None:
    a = _offering("a")
    b = _offering("b")
    frontiers = {
        "coding": _frontier("coding-quality", [(a, "0.9"), (b, "0.8")]),
        "reasoning": _frontier("reasoning-quality", [(a, "0.7"), (b, "0.7")]),
        "tools": _frontier("tools-quality", [(a, "0.8")]),
        "research": _frontier("research-quality", []),
    }
    policy = _policy(frontiers, required=("coding",), minimum=3)

    snapshot = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [a, b],
        generated_at=NOW,
    )
    coverage = _coverage_by_id(snapshot)

    assert coverage["a"].eligible
    assert coverage["a"].measured_component_count == 3
    assert not coverage["b"].eligible
    assert coverage["b"].measured_component_count == 2
    assert not coverage["b"].failed_required_component_ids


def test_quarantine_is_distinct_from_missing_and_blocks_required_coverage() -> None:
    a = _offering("a")
    frontiers = {
        "coding": _frontier("coding-quality", [(a, "0.9")]),
        "tools": _frontier("tools-quality", []),
    }
    policy = _policy(frontiers, required=("coding", "tools"), minimum=2)
    quarantine = QualityBundleQuarantine(
        offering=a,
        reason_codes=("mutable-alias", "identity-drift"),
    )

    snapshot = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [a],
        generated_at=NOW,
        quarantines={"tools": (quarantine,)},
    )
    candidate = snapshot.candidates[0]
    tool_evidence = candidate.components[1]

    assert tool_evidence.status is QualityCoverageStatus.QUARANTINED
    assert tool_evidence.quarantine_reason_codes == ("identity-drift", "mutable-alias")
    assert candidate.quarantined_component_ids == ("tools",)
    assert not candidate.missing_component_ids
    assert candidate.failed_required_component_ids == ("tools",)
    assert not candidate.eligible
    assert not eligible_quality_bundle_candidates(snapshot, now=NOW)


def test_cross_component_matching_uses_every_offering_key_field() -> None:
    measured = _offering("shared", service_tier="standard", agent_harness=None)
    different_tier = measured.model_copy(update={"service_tier": "priority"})
    different_harness = measured.model_copy(update={"agent_harness": "benchmark-agent@1"})
    frontiers = {
        "coding": _frontier("coding-quality", [(measured, "0.9")]),
        "tools": _frontier("tools-quality", [(measured, "0.8")]),
    }
    policy = _policy(frontiers, required=("coding", "tools"), minimum=2)

    snapshot = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [different_tier, measured, different_harness],
        generated_at=NOW,
    )
    by_identity = {candidate.offering: candidate for candidate in snapshot.candidates}

    assert by_identity[measured].eligible
    assert by_identity[different_tier].missing_component_ids == ("coding", "tools")
    assert by_identity[different_harness].missing_component_ids == ("coding", "tools")


@pytest.mark.parametrize("count", [1, 5])
def test_policy_requires_two_to_four_components(count: int) -> None:
    offering = _offering("a")
    frontiers = {
        f"component-{index}": _frontier(f"frontier-{index}", [(offering, "0.5")])
        for index in range(count)
    }

    with pytest.raises(ValidationError):
        _policy(frontiers, required=("component-0",), minimum=1)


def test_policy_rejects_duplicates_unknown_requirements_and_scalar_strategy() -> None:
    offering = _offering("a")
    first = _frontier("first", [(offering, "0.5")])
    second = _frontier("second", [(offering, "0.6")])
    first_component = _component("first", first)
    second_component = _component("second", second)
    common = {
        "bundle_id": "general-quality",
        "version": "1",
        "components": (first_component, second_component),
        "required_component_ids": ("first",),
        "minimum_measured_components": 1,
    }

    with pytest.raises(ValidationError, match="component ids must be unique"):
        QualityBundlePolicy.model_validate(
            {**common, "components": (first_component, first_component)}
        )
    with pytest.raises(ValidationError, match="required quality component ids must be unique"):
        QualityBundlePolicy.model_validate(
            {
                **common,
                "required_component_ids": ("first", "first"),
                "minimum_measured_components": 2,
            }
        )
    with pytest.raises(ValidationError, match="is not declared"):
        QualityBundlePolicy.model_validate({**common, "required_component_ids": ("unknown",)})
    with pytest.raises(ValidationError, match="cannot be smaller"):
        QualityBundlePolicy.model_validate(
            {
                **common,
                "required_component_ids": ("first", "second"),
                "minimum_measured_components": 1,
            }
        )
    with pytest.raises(ValidationError):
        QualityBundlePolicy.model_validate({**common, "strategy": "weighted-average"})


def test_component_binding_rejects_source_hash_and_identity_tampering() -> None:
    offering = _offering("a")
    frontiers = {
        "coding": _frontier("coding-quality", [(offering, "0.9")]),
        "tools": _frontier("tools-quality", [(offering, "0.8")]),
    }
    policy = _policy(frontiers, required=("coding",), minimum=1)

    tampered = frontiers["coding"].model_copy(update={"catalog_hash": "f" * 64})
    with pytest.raises(ValueError, match="frontier hash mismatch"):
        build_quality_bundle_snapshot(
            policy,
            {**frontiers, "coding": tampered},
            [offering],
            generated_at=NOW,
        )

    with pytest.raises(ValueError, match="exactly match"):
        build_quality_bundle_snapshot(
            policy,
            {"coding": frontiers["coding"]},
            [offering],
            generated_at=NOW,
        )


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({"config_hash": "a" * 64}, "config hash mismatch"),
        ({"catalog_hash": "b" * 64}, "catalog hash mismatch"),
        (
            {"workload": WorkloadReference(id="different", version="1", unit="task")},
            "workload mismatch",
        ),
        (
            {
                "axes": (
                    AxisDescriptor(metric="quality", goal=Goal.MAXIMIZE, unit="percent"),
                    COST_AXIS,
                )
            },
            "metric axes mismatch",
        ),
    ],
)
def test_component_binds_config_catalog_workload_and_metric_identity(
    update: dict[str, object],
    expected: str,
) -> None:
    offering = _offering("a")
    coding = _frontier("coding-quality", [(offering, "0.9")])
    tools = _frontier("tools-quality", [(offering, "0.8")])
    frontiers = {"coding": coding, "tools": tools}
    policy = _policy(frontiers, required=("coding",), minimum=1)
    changed = _rehash_frontier(coding, **update)
    original_binding = policy.components[0]
    changed_binding = original_binding.model_copy(
        update={
            "frontier_snapshot_id": changed.snapshot_id,
            "frontier_snapshot_hash": changed.snapshot_id,
        }
    )
    changed_policy = _policy_with_component(policy, 0, changed_binding)

    with pytest.raises(ValueError, match=expected):
        build_quality_bundle_snapshot(
            changed_policy,
            {"coding": changed, "tools": tools},
            [offering],
            generated_at=NOW,
        )


def test_component_rejects_frontier_and_snapshot_reference_mismatches() -> None:
    offering = _offering("a")
    coding = _frontier("coding-quality", [(offering, "0.9")])
    tools = _frontier("tools-quality", [(offering, "0.8")])
    frontiers = {"coding": coding, "tools": tools}
    policy = _policy(frontiers, required=("coding",), minimum=1)

    wrong_id = policy.components[0].model_copy(update={"frontier_id": "other"})
    wrong_id_policy = _policy_with_component(policy, 0, wrong_id)
    with pytest.raises(ValueError, match="frontier identity mismatch"):
        build_quality_bundle_snapshot(
            wrong_id_policy,
            frontiers,
            [offering],
            generated_at=NOW,
        )

    changed_snapshot = policy.components[0].model_copy(
        update={
            "frontier_snapshot_id": "a" * 64,
            "frontier_snapshot_hash": "a" * 64,
        }
    )
    changed_snapshot_policy = _policy_with_component(policy, 0, changed_snapshot)
    with pytest.raises(ValueError, match="frontier snapshot mismatch"):
        build_quality_bundle_snapshot(
            changed_snapshot_policy,
            frontiers,
            [offering],
            generated_at=NOW,
        )


def test_freshness_rejects_future_and_boundary_stale_components() -> None:
    offering = _offering("a")
    stale = _frontier(
        "coding-quality",
        [(offering, "0.9")],
        generated_at=NOW - timedelta(hours=1),
    )
    other = _frontier("tools-quality", [(offering, "0.8")])
    stale_frontiers = {"coding": stale, "tools": other}
    stale_policy = _policy(
        stale_frontiers,
        required=("coding",),
        minimum=1,
        max_age_seconds=3600,
    )
    with pytest.raises(ValueError, match="is stale"):
        build_quality_bundle_snapshot(
            stale_policy,
            stale_frontiers,
            [offering],
            generated_at=NOW,
        )

    future = _frontier(
        "coding-quality",
        [(offering, "0.9")],
        generated_at=NOW + timedelta(seconds=1),
    )
    future_frontiers = {"coding": future, "tools": other}
    future_policy = _policy(future_frontiers, required=("coding",), minimum=1)
    with pytest.raises(ValueError, match="future-dated"):
        build_quality_bundle_snapshot(
            future_policy,
            future_frontiers,
            [offering],
            generated_at=NOW,
        )

    with pytest.raises(ValueError, match="include a timezone"):
        build_quality_bundle_snapshot(
            future_policy,
            future_frontiers,
            [offering],
            generated_at=NOW.replace(tzinfo=None),
        )


def test_quarantine_and_candidate_inputs_are_exact_and_unambiguous() -> None:
    offering = _offering("a")
    outside = _offering("outside")
    frontiers = {
        "coding": _frontier("coding-quality", [(offering, "0.9")]),
        "tools": _frontier("tools-quality", []),
    }
    policy = _policy(frontiers, required=("coding",), minimum=1)

    with pytest.raises(ValueError, match="distinct complete OfferingKeys"):
        build_quality_bundle_snapshot(
            policy,
            frontiers,
            [offering, offering],
            generated_at=NOW,
        )
    with pytest.raises(ValueError, match="unknown quality component"):
        build_quality_bundle_snapshot(
            policy,
            frontiers,
            [offering],
            generated_at=NOW,
            quarantines={
                "unknown": (
                    QualityBundleQuarantine(
                        offering=offering,
                        reason_codes=("unmapped",),
                    ),
                )
            },
        )
    with pytest.raises(ValueError, match="outside the candidate universe"):
        build_quality_bundle_snapshot(
            policy,
            frontiers,
            [offering],
            generated_at=NOW,
            quarantines={
                "tools": (
                    QualityBundleQuarantine(
                        offering=outside,
                        reason_codes=("unmapped",),
                    ),
                )
            },
        )
    with pytest.raises(ValueError, match="both measured and quarantined"):
        build_quality_bundle_snapshot(
            policy,
            frontiers,
            [offering],
            generated_at=NOW,
            quarantines={
                "coding": (
                    QualityBundleQuarantine(
                        offering=offering,
                        reason_codes=("identity-drift",),
                    ),
                )
            },
        )


def test_snapshot_hash_rejects_tampering_and_source_verification_rebuilds_evidence() -> None:
    offering = _offering("a")
    frontiers = {
        "coding": _frontier("coding-quality", [(offering, "0.9")]),
        "tools": _frontier("tools-quality", [(offering, "0.8")]),
    }
    policy = _policy(frontiers, required=("coding", "tools"), minimum=2)
    snapshot = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [offering],
        generated_at=NOW,
    )

    tampered = snapshot.model_dump(mode="json")
    tampered["candidates"][0]["components"][0]["estimate"]["value"] = "0.123"
    with pytest.raises(ValidationError, match="snapshot hash mismatch"):
        QualityBundleSnapshot.model_validate(tampered)

    forged = tampered
    forged["snapshot_id"] = content_hash(
        {key: value for key, value in forged.items() if key != "snapshot_id"}
    )
    structurally_valid = QualityBundleSnapshot.model_validate(forged)
    with pytest.raises(ValueError, match="does not match its source frontiers"):
        verify_quality_bundle_snapshot(
            policy,
            frontiers,
            [offering],
            structurally_valid,
            now=NOW,
        )

    verify_quality_bundle_snapshot(
        policy,
        frontiers,
        [offering],
        snapshot,
        now=NOW,
    )


def test_eligible_helper_enforces_snapshot_time_bounds() -> None:
    offering = _offering("a")
    frontiers = {
        "coding": _frontier("coding-quality", [(offering, "0.9")]),
        "tools": _frontier("tools-quality", [(offering, "0.8")]),
    }
    policy = _policy(frontiers, required=("coding",), minimum=1)
    snapshot = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [offering],
        generated_at=NOW,
    )

    with pytest.raises(ValueError, match="future-dated"):
        eligible_quality_bundle_candidates(snapshot, now=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="is stale"):
        eligible_quality_bundle_candidates(snapshot, now=snapshot.valid_until)
    with pytest.raises(ValueError, match="include a timezone"):
        eligible_quality_bundle_candidates(snapshot, now=NOW.replace(tzinfo=None))


def test_candidate_order_is_canonical_and_hash_stable() -> None:
    a = _offering("a")
    b = _offering("b")
    frontiers = {
        "coding": _frontier("coding-quality", [(a, "0.9"), (b, "0.8")]),
        "tools": _frontier("tools-quality", [(a, "0.8"), (b, "0.7")]),
    }
    policy = _policy(frontiers, required=("coding",), minimum=1)

    first = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [a, b],
        generated_at=NOW,
    )
    second = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [b, a],
        generated_at=NOW,
    )

    assert first == second
    assert first.snapshot_id == second.snapshot_id


def test_policy_component_and_required_order_are_canonical_sets() -> None:
    offering = _offering("a")
    frontiers = {
        "coding": _frontier("coding-quality", [(offering, "0.9")]),
        "tools": _frontier("tools-quality", [(offering, "0.8")]),
    }
    coding = _component("coding", frontiers["coding"])
    tools = _component("tools", frontiers["tools"])
    first = QualityBundlePolicy(
        bundle_id="general-quality",
        version="1",
        components=(coding, tools),
        required_component_ids=("coding", "tools"),
        minimum_measured_components=2,
    )
    second = QualityBundlePolicy(
        bundle_id="general-quality",
        version="1",
        components=(tools, coding),
        required_component_ids=("tools", "coding"),
        minimum_measured_components=2,
    )

    assert first == second
    assert tuple(component.component_id for component in first.components) == (
        "coding",
        "tools",
    )
    assert first.required_component_ids == ("coding", "tools")
    assert quality_bundle_policy_hash(first) == quality_bundle_policy_hash(second)


def test_equivalent_generation_instants_normalize_to_one_snapshot_hash() -> None:
    offering = _offering("a")
    frontiers = {
        "coding": _frontier("coding-quality", [(offering, "0.9")]),
        "tools": _frontier("tools-quality", [(offering, "0.8")]),
    }
    policy = _policy(frontiers, required=("coding",), minimum=1)
    offset_now = NOW.astimezone(timezone(timedelta(hours=-6)))

    utc_snapshot = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [offering],
        generated_at=NOW,
    )
    offset_snapshot = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [offering],
        generated_at=offset_now,
    )

    assert offset_snapshot.generated_at.tzinfo is UTC
    assert utc_snapshot == offset_snapshot
    assert utc_snapshot.snapshot_id == offset_snapshot.snapshot_id


def test_explicit_evidence_deadline_limits_snapshot_freshness() -> None:
    offering = _offering("a")
    frontiers = {
        "coding": _frontier("coding-quality", [(offering, "0.9")]),
        "tools": _frontier("tools-quality", [(offering, "0.8")]),
    }
    evidence_deadline = NOW + timedelta(minutes=5)
    components = tuple(
        _component(
            component_id,
            frontier,
            evidence_valid_until=(
                evidence_deadline.astimezone(timezone(timedelta(hours=-6)))
                if component_id == "coding"
                else None
            ),
        )
        for component_id, frontier in frontiers.items()
    )
    policy = QualityBundlePolicy(
        bundle_id="general-quality",
        version="1",
        components=components,
        required_component_ids=("coding",),
        minimum_measured_components=1,
    )

    snapshot = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [offering],
        generated_at=NOW,
    )

    assert policy.components[0].evidence_valid_until == evidence_deadline
    assert snapshot.valid_until == evidence_deadline
    with pytest.raises(ValueError, match="is stale"):
        eligible_quality_bundle_candidates(snapshot, now=evidence_deadline)

    invalid_component = _component(
        "coding",
        frontiers["coding"],
        evidence_valid_until=frontiers["coding"].generated_at,
    )
    invalid_policy = _policy_with_component(policy, 0, invalid_component)
    with pytest.raises(ValueError, match="validity does not follow frontier generation"):
        build_quality_bundle_snapshot(
            invalid_policy,
            frontiers,
            [offering],
            generated_at=NOW,
        )


def test_frontier_rejection_is_missing_without_exact_quarantine_identity() -> None:
    offering = _offering("a")
    rejected = _rehash_frontier(
        _frontier("tools-quality", []),
        rejected=(RejectedOffering(offering_id=offering.offering_id, reasons=("no score",)),),
    )
    frontiers = {
        "coding": _frontier("coding-quality", [(offering, "0.9")]),
        "tools": rejected,
    }
    policy = _policy(frontiers, required=("coding",), minimum=1)

    snapshot = build_quality_bundle_snapshot(
        policy,
        frontiers,
        [offering],
        generated_at=NOW,
    )
    candidate = snapshot.candidates[0]

    assert candidate.components[1].status is QualityCoverageStatus.MISSING
    assert candidate.missing_component_ids == ("tools",)
    assert not candidate.quarantined_component_ids
