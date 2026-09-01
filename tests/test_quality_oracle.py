from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from model_skyline.cli import app
from model_skyline.engine import FrontierEngine, frontier_hash
from model_skyline.io import (
    dump_json,
    load_catalog,
    load_quality_oracle_policy,
    load_quality_oracle_snapshot,
)
from model_skyline.models import (
    AxisDescriptor,
    AxisEstimate,
    AxisEvidenceCandidate,
    EvaluatedOffering,
    FrontierSnapshot,
    Goal,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    ProjectConfig,
    RejectedOffering,
    SourceReference,
    WorkloadReference,
    build_axis_evidence_inventory,
)
from model_skyline.quality_bundle import (
    QualityBundleComponent,
    QualityBundlePolicy,
    QualityBundleSnapshot,
    build_quality_bundle_snapshot,
)
from model_skyline.quality_oracle import (
    FixedMinMaxNormalization,
    QualityOracleComponent,
    QualityOracleComponentStatus,
    QualityOraclePolicy,
    QualityOracleSnapshot,
    build_fixed_min_max_normalization,
    build_quality_oracle_snapshot,
    enrich_catalog_with_quality_oracle,
    fixed_min_max_normalization_hash,
    quality_oracle_axis,
    quality_oracle_catalog,
    quality_oracle_policy_hash,
    quality_oracle_selected_quality_projection_hash,
    quality_oracle_snapshot_hash,
    quality_oracle_source_semantic_identity,
    quality_oracle_source_semantics,
    verify_quality_oracle_snapshot,
)

NOW = datetime(2026, 8, 31, 20, tzinfo=UTC)
RUNNER = CliRunner()


def _offering(offering_id: str, *, provider: str = "provider") -> OfferingKey:
    return OfferingKey(
        offering_id=offering_id,
        model_id=f"model-{offering_id}",
        provider=provider,
        endpoint="responses",
        billing_mode="list",
        region="us",
        service_tier="standard",
        quantization="native",
        reasoning_effort="medium",
        agent_harness=None,
        capabilities=("text", "tools"),
    )


def _source(source_id: str, digest_character: str) -> SourceReference:
    return SourceReference(
        id=source_id,
        version="commit-1",
        url=f"https://benchmarks.example/{source_id}.json",
        terms_url="https://benchmarks.example/terms",
        license="fixture-license",
        methodology=f"Pinned fixture methodology for {source_id}.",
        raw_sha256=digest_character * 64,
        retrieved_at=NOW - timedelta(hours=1),
    )


def _frontier(
    frontier_id: str,
    metric: str,
    goal: Goal,
    source: SourceReference,
    measured: list[tuple[OfferingKey, str, str | None, str | None, int]],
    *,
    digest_character: str,
    cost_value: str = "1",
    workload: WorkloadReference | None = None,
) -> FrontierSnapshot:
    quality_axis = AxisDescriptor(metric=metric, goal=goal, unit="percent")
    cost_axis = AxisDescriptor(metric="cost_per_task", goal=Goal.MINIMIZE, unit="USD/task")
    generated_at = NOW - timedelta(minutes=10)
    selected_workload = workload or WorkloadReference(
        id=f"{frontier_id}-cohort",
        version=f"task-set-sha256:{digest_character * 64}",
        unit="task",
    )
    evaluated = tuple(
        EvaluatedOffering(
            offering=offering,
            axes={
                metric: AxisEstimate(
                    value=Decimal(value),
                    unit="percent",
                    lower=Decimal(lower) if lower is not None else None,
                    upper=Decimal(upper) if upper is not None else None,
                    dependencies=(f"signals.{metric}",),
                    source_ids=(source.id,),
                    sources=(source,),
                    oldest_observed_at=NOW - timedelta(days=1),
                    minimum_sample_count=sample_count,
                ),
                "cost_per_task": AxisEstimate(value=Decimal(cost_value), unit="USD/task"),
            },
        )
        for offering, value, lower, upper, sample_count in measured
    )
    axis_evidence = build_axis_evidence_inventory(
        config_hash=digest_character * 64,
        catalog_hash=digest_character * 64,
        generated_at=generated_at,
        workload=selected_workload,
        axes=(quality_axis, cost_axis),
        candidates=tuple(
            AxisEvidenceCandidate(offering=item.offering, axes=item.axes) for item in evaluated
        ),
    )
    provisional = FrontierSnapshot(
        snapshot_id="0" * 64,
        config_hash=digest_character * 64,
        catalog_hash=digest_character * 64,
        engine_version="test",
        generated_at=generated_at,
        frontier_id=frontier_id,
        workload=selected_workload,
        order_by=metric,
        uncertainty="point",
        axes=(quality_axis, cost_axis),
        members=evaluated,
        evaluated=evaluated,
        axis_evidence=axis_evidence,
        sources=(source,),
        source_watermarks={source.id: NOW - timedelta(days=1)},
    )
    return provisional.model_copy(update={"snapshot_id": frontier_hash(provisional)})


def _bundle_component(component_id: str, frontier: FrontierSnapshot) -> QualityBundleComponent:
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


def _bundle_from_frontiers(
    frontiers: dict[str, FrontierSnapshot],
    candidates: tuple[OfferingKey, ...],
) -> QualityBundleSnapshot:
    policy = QualityBundlePolicy(
        bundle_id="general-agent-quality",
        version="2026-08-31",
        components=tuple(
            _bundle_component(component_id, frontier)
            for component_id, frontier in frontiers.items()
        ),
        required_component_ids=("coding", "reasoning"),
        minimum_measured_components=2,
    )
    return build_quality_bundle_snapshot(
        policy,
        frontiers,
        candidates,
        generated_at=NOW,
    )


def _frontier_with_quality_sources(
    frontier: FrontierSnapshot,
    sources: tuple[SourceReference, ...],
    *,
    digest_character: str,
) -> FrontierSnapshot:
    quality_metric = frontier.axes[0].metric
    source_ids = tuple(sorted(source.id for source in sources))
    evaluated = tuple(
        item.model_copy(
            update={
                "axes": {
                    **item.axes,
                    quality_metric: item.axes[quality_metric].model_copy(
                        update={"source_ids": source_ids, "sources": sources}
                    ),
                }
            }
        )
        for item in frontier.evaluated
    )
    axis_evidence = build_axis_evidence_inventory(
        config_hash=digest_character * 64,
        catalog_hash=digest_character * 64,
        generated_at=frontier.generated_at,
        workload=frontier.workload,
        axes=frontier.axes,
        candidates=tuple(
            AxisEvidenceCandidate(offering=item.offering, axes=item.axes) for item in evaluated
        ),
    )
    provisional = frontier.model_copy(
        update={
            "snapshot_id": "0" * 64,
            "config_hash": digest_character * 64,
            "catalog_hash": digest_character * 64,
            "members": evaluated,
            "evaluated": evaluated,
            "axis_evidence": axis_evidence,
            "sources": tuple(sorted(sources, key=lambda source: source.id)),
            "source_watermarks": {source.id: NOW - timedelta(days=1) for source in sources},
        }
    )
    return provisional.model_copy(update={"snapshot_id": frontier_hash(provisional)})


def _frontier_without_companion_axis(
    frontier: FrontierSnapshot,
    *,
    catalog_digest_character: str,
) -> FrontierSnapshot:
    quality_metric = frontier.axes[0].metric
    candidates = tuple(
        AxisEvidenceCandidate(
            offering=item.offering,
            axes={quality_metric: item.axes[quality_metric]},
        )
        for item in frontier.evaluated
    )
    catalog_hash = catalog_digest_character * 64
    axis_evidence = build_axis_evidence_inventory(
        config_hash=frontier.config_hash,
        catalog_hash=catalog_hash,
        generated_at=frontier.generated_at,
        workload=frontier.workload,
        axes=frontier.axes,
        candidates=candidates,
    )
    provisional = frontier.model_copy(
        update={
            "snapshot_id": "0" * 64,
            "catalog_hash": catalog_hash,
            "members": (),
            "evaluated": (),
            "rejected": tuple(
                RejectedOffering(
                    offering_id=candidate.offering.offering_id,
                    reasons=("cost_per_task: companion signal is missing",),
                )
                for candidate in candidates
            ),
            "axis_evidence": axis_evidence,
        }
    )
    return provisional.model_copy(update={"snapshot_id": frontier_hash(provisional)})


def _oracle_component(
    component_id: str,
    frontier: FrontierSnapshot,
    source: SourceReference,
    *,
    weight: str,
    correlation_group: str,
) -> QualityOracleComponent:
    return QualityOracleComponent(
        component_id=component_id,
        workload=frontier.workload,
        quality_axis=frontier.axes[0],
        source_semantics=quality_oracle_source_semantics((source,)),
        source_semantic_identity_sha256=quality_oracle_source_semantic_identity((source,)),
        normalization=build_fixed_min_max_normalization(
            reference_id=f"{component_id}-fixed-reference",
            reference_version="1",
            input_unit="percent",
            reference_min=Decimal("0"),
            reference_max=Decimal("100"),
            rationale="Fixed public benchmark scale; it is not candidate-relative.",
        ),
        weight=Decimal(weight),
        correlation_group=correlation_group,
        rationale=f"{component_id} is material to the declared composite workload.",
    )


@pytest.fixture
def oracle_inputs() -> tuple[
    QualityOraclePolicy,
    QualityBundleSnapshot,
    dict[str, FrontierSnapshot],
    dict[str, SourceReference],
]:
    a = _offering("a")
    b = _offering("b")
    c = _offering("c")
    sources = {
        "coding": _source("coding-feed", "a"),
        "reasoning": _source("reasoning-feed", "b"),
    }
    frontiers = {
        "coding": _frontier(
            "coding-quality",
            "coding_score",
            Goal.MAXIMIZE,
            sources["coding"],
            [
                (a, "80", "75", "85", 500),
                (b, "70", None, None, 500),
                (c, "110", None, None, 500),
            ],
            digest_character="1",
        ),
        "reasoning": _frontier(
            "reasoning-quality",
            "reasoning_error_rate",
            Goal.MINIMIZE,
            sources["reasoning"],
            [
                (a, "20", "15", "25", 50),
                (c, "10", None, None, 50),
            ],
            digest_character="2",
        ),
    }
    bundle_policy = QualityBundlePolicy(
        bundle_id="general-agent-quality",
        version="2026-08-31",
        components=tuple(
            _bundle_component(component_id, frontier)
            for component_id, frontier in frontiers.items()
        ),
        required_component_ids=("coding", "reasoning"),
        minimum_measured_components=2,
    )
    bundle = build_quality_bundle_snapshot(
        bundle_policy,
        frontiers,
        (c, b, a),
        generated_at=NOW,
    )
    oracle_policy = QualityOraclePolicy(
        oracle_id="general-agent-quality-index",
        version="1",
        composite_workload=WorkloadReference(
            id="general-agent-composite",
            version="1",
            unit="agent_session",
        ),
        quality_metric="general_agent_quality",
        quality_bundle_id=bundle_policy.bundle_id,
        quality_bundle_version=bundle_policy.version,
        components=(
            _oracle_component(
                "reasoning",
                frontiers["reasoning"],
                sources["reasoning"],
                weight="0.4",
                correlation_group="reasoning",
            ),
            _oracle_component(
                "coding",
                frontiers["coding"],
                sources["coding"],
                weight="0.6",
                correlation_group="coding",
            ),
        ),
        aggregation_rationale=(
            "Coding is weighted at sixty percent and reasoning at forty percent for this "
            "versioned agent-session workload."
        ),
        correlation_rationale=(
            "The benchmarks may share model capabilities; groups document reuse but are not "
            "claims of statistical independence."
        ),
    )
    return oracle_policy, bundle, frontiers, sources


def test_oracle_requires_all_components_and_rejects_out_of_reference(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, bundle, _frontiers, _sources = oracle_inputs
    snapshot = build_quality_oracle_snapshot(policy, bundle, generated_at=NOW)
    candidates = {candidate.offering.offering_id: candidate for candidate in snapshot.candidates}

    assert [component.component_id for component in policy.components] == ["coding", "reasoning"]
    assert candidates["a"].status == "scored"
    assert candidates["a"].estimate is not None
    assert candidates["a"].estimate.value == Decimal("0.80")
    assert candidates["a"].estimate.lower is None
    assert candidates["a"].estimate.upper is None
    assert candidates["a"].estimate.minimum_sample_count is None
    assert candidates["a"].components[0].raw_estimate is not None
    assert candidates["a"].components[0].raw_estimate.minimum_sample_count == 500
    assert candidates["b"].status == "rejected"
    assert candidates["b"].failed_component_ids == ("reasoning",)
    assert candidates["c"].status == "rejected"
    coding = candidates["c"].components[0]
    assert coding.status is QualityOracleComponentStatus.OUT_OF_REFERENCE
    assert coding.raw_estimate is not None
    assert coding.raw_estimate.value == Decimal("110")
    assert coding.quarantine_reason_codes == ("out_of_reference",)
    assert snapshot.snapshot_id == quality_oracle_snapshot_hash(snapshot)
    assert snapshot.policy_hash == quality_oracle_policy_hash(policy)


def test_oracle_catalog_is_an_engine_ready_quality_axis_with_full_provenance(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, bundle, _frontiers, _sources = oracle_inputs
    snapshot = build_quality_oracle_snapshot(policy, bundle, generated_at=NOW)
    catalog = quality_oracle_catalog(
        policy,
        bundle,
        snapshot,
        now=NOW + timedelta(minutes=1),
    )

    assert catalog.workload == policy.composite_workload
    assert [item.offering.offering_id for item in catalog.offerings] == ["a"]
    assert catalog.offerings[0].offering == snapshot.candidates[0].offering
    observation = catalog.offerings[0].signals[policy.quality_metric]
    assert observation.value == Decimal("0.80")
    assert observation.source is not None
    assert snapshot.snapshot_id in observation.source.version
    metadata = catalog.offerings[0].metadata
    assert metadata["publication_safe"] is False
    assert metadata["statistical_independence_assumed"] is False
    assert metadata["quality_bundle_snapshot_id"] == bundle.snapshot_id
    provenance = metadata["component_provenance"]
    assert isinstance(provenance, list)
    assert [item["component_id"] for item in provenance] == ["coding", "reasoning"]
    assert all(item["bundle_component"]["frontier_snapshot_id"] for item in provenance)
    assert all(item["quality_axis"]["metric"] for item in provenance)
    assert all(item["source_semantic_identity_sha256"] for item in provenance)
    assert all(item["source_capture_identity_sha256"] for item in provenance)
    assert all(item["sources"][0]["license"] == "fixture-license" for item in provenance)
    assert observation.source.license == "NOASSERTION"
    assert observation.source.raw_sha256 == snapshot.snapshot_id
    assert observation.source.retrieved_at == snapshot.generated_at
    assert observation.lower is None
    assert observation.upper is None
    assert observation.sample_count is None
    axis = quality_oracle_axis(policy)
    assert axis.metric == policy.quality_metric
    assert axis.goal is Goal.MAXIMIZE
    assert axis.unit == "normalized_quality_score"


def test_exact_catalog_enrichment_retains_rejections_for_engine_fail_closed(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, bundle, _frontiers, _sources = oracle_inputs
    snapshot = build_quality_oracle_snapshot(policy, bundle, generated_at=NOW)
    base = ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=policy.composite_workload,
        offerings=[
            OfferingObservation(
                offering=candidate.offering,
                signals={"total_cost": Observation(value=Decimal("1"), unit="USD/session")},
                metadata={"base_catalog": "fixture"},
            )
            for candidate in reversed(snapshot.candidates)
        ],
    )

    enriched = enrich_catalog_with_quality_oracle(
        base,
        policy,
        bundle,
        snapshot,
        now=NOW + timedelta(minutes=1),
    )
    by_id = {item.offering.offering_id: item for item in enriched.offerings}

    assert list(by_id) == ["a", "b", "c"]
    assert set(by_id["a"].signals) == {"general_agent_quality", "total_cost"}
    assert set(by_id["b"].signals) == {"total_cost"}
    assert set(by_id["c"].signals) == {"total_cost"}
    assert by_id["a"].metadata["quality_oracle_candidate_status"] == "scored"
    assert all(item.metadata["publication_safe"] is False for item in by_id.values())
    assert by_id["b"].metadata["quality_oracle_failed_component_ids"] == ["reasoning"]
    assert by_id["c"].metadata["quality_oracle_component_statuses"][0]["status"] == (
        "out_of_reference"
    )
    assert by_id["a"].offering == snapshot.candidates[0].offering

    config = ProjectConfig.model_validate(
        {
            "schema_version": "model-skyline/v1alpha1",
            "workloads": {
                policy.composite_workload.id: {
                    "unit": policy.composite_workload.unit,
                    "version": policy.composite_workload.version,
                    "harness": "operator-declared-composite-v1",
                    "cohort": "exact-oracle-and-cost-candidate-universe",
                }
            },
            "metrics": {
                policy.quality_metric: {
                    "kind": "signal",
                    "signal": policy.quality_metric,
                    "unit": policy.quality_unit,
                },
                "total_cost": {
                    "kind": "signal",
                    "signal": "total_cost",
                    "unit": "USD/session",
                },
            },
            "frontiers": {
                "value": {
                    "workload": policy.composite_workload.id,
                    "axes": [
                        {"metric": policy.quality_metric, "goal": "maximize"},
                        {"metric": "total_cost", "goal": "minimize"},
                    ],
                    "order_by": policy.quality_metric,
                }
            },
        }
    )
    frontier = FrontierEngine().calculate(config, enriched, "value", generated_at=NOW)
    assert [item.offering.offering_id for item in frontier.evaluated] == ["a"]
    assert [item.offering_id for item in frontier.rejected] == ["b", "c"]


def test_catalog_enrichment_refuses_identity_drift_and_overwrites(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, bundle, _frontiers, _sources = oracle_inputs
    snapshot = build_quality_oracle_snapshot(policy, bundle, generated_at=NOW)

    def catalog_for(offerings: list[OfferingObservation]) -> ObservationCatalog:
        return ObservationCatalog(
            schema_version="model-skyline/v1alpha1",
            workload=policy.composite_workload,
            offerings=offerings,
        )

    ordinary = [
        OfferingObservation(
            offering=candidate.offering,
            signals={"total_cost": Observation(value=Decimal("1"), unit="USD/session")},
        )
        for candidate in snapshot.candidates
    ]
    drifted = list(ordinary)
    drifted[0] = drifted[0].model_copy(
        update={"offering": drifted[0].offering.model_copy(update={"provider": "other"})}
    )
    with pytest.raises(ValueError, match="complete OfferingKey differs"):
        enrich_catalog_with_quality_oracle(
            catalog_for(drifted),
            policy,
            bundle,
            snapshot,
            now=NOW + timedelta(minutes=1),
        )

    with pytest.raises(ValueError, match="candidate universes must match exactly"):
        enrich_catalog_with_quality_oracle(
            catalog_for(ordinary[:-1]),
            policy,
            bundle,
            snapshot,
            now=NOW + timedelta(minutes=1),
        )

    conflicting = list(ordinary)
    conflicting[0] = conflicting[0].model_copy(
        update={
            "signals": {
                **conflicting[0].signals,
                policy.quality_metric: Observation(value=Decimal("0"), unit=policy.quality_unit),
            }
        }
    )
    with pytest.raises(ValueError, match="already contains"):
        enrich_catalog_with_quality_oracle(
            catalog_for(conflicting),
            policy,
            bundle,
            snapshot,
            now=NOW + timedelta(minutes=1),
        )

    claiming_publication = list(ordinary)
    claiming_publication[0] = claiming_publication[0].model_copy(
        update={"metadata": {"publication_safe": True}}
    )
    with pytest.raises(ValueError, match="cannot claim publication_safe"):
        enrich_catalog_with_quality_oracle(
            catalog_for(claiming_publication),
            policy,
            bundle,
            snapshot,
            now=NOW + timedelta(minutes=1),
        )


def test_oracle_is_deterministic_and_replay_verifiable(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, bundle, _frontiers, _sources = oracle_inputs
    first = build_quality_oracle_snapshot(policy, bundle, generated_at=NOW)
    reordered = QualityOraclePolicy.model_validate(
        {
            **policy.model_dump(mode="json"),
            "components": list(reversed(policy.model_dump(mode="json")["components"])),
        }
    )
    second = build_quality_oracle_snapshot(reordered, bundle, generated_at=NOW)

    assert first == second
    verify_quality_oracle_snapshot(policy, bundle, first, now=NOW + timedelta(minutes=1))

    tampered = first.model_dump(mode="json")
    tampered["candidates"][0]["estimate"]["value"] = "0.81"
    with pytest.raises(ValidationError, match="composite estimate|snapshot hash"):
        QualityOracleSnapshot.model_validate(tampered)

    tampered = first.model_dump(mode="json")
    tampered["candidates"][0]["components"][0]["weighted_contribution"] = "0.49"
    with pytest.raises(ValidationError, match="does not match policy arithmetic"):
        QualityOracleSnapshot.model_validate(tampered)


def test_oracle_rejects_source_or_frontier_semantic_mismatch(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, bundle, _frontiers, sources = oracle_inputs
    payload = policy.model_dump(mode="json")
    replacement_source = sources["coding"].model_copy(update={"version": "commit-2"})
    coding = payload["components"][0]
    coding["source_semantics"] = [
        item.model_dump(mode="json")
        for item in quality_oracle_source_semantics((replacement_source,))
    ]
    coding["source_semantic_identity_sha256"] = quality_oracle_source_semantic_identity(
        (replacement_source,)
    )
    mismatched_source = QualityOraclePolicy.model_validate(payload)
    with pytest.raises(ValueError, match="source semantics mismatch"):
        build_quality_oracle_snapshot(mismatched_source, bundle, generated_at=NOW)

    payload = policy.model_dump(mode="json")
    payload["components"][0]["quality_axis"]["goal"] = "minimize"
    wrong_direction = QualityOraclePolicy.model_validate(payload)
    with pytest.raises(ValueError, match="does not match bundle semantics"):
        build_quality_oracle_snapshot(wrong_direction, bundle, generated_at=NOW)


def test_policy_requires_fixed_complete_and_explicit_composite_semantics(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, _bundle, _frontiers, _sources = oracle_inputs

    payload = policy.model_dump(mode="json")
    payload["components"][0]["weight"] = "0.5"
    with pytest.raises(ValidationError, match="sum exactly to 1"):
        QualityOraclePolicy.model_validate(payload)

    payload = policy.model_dump(mode="json")
    payload["components"][0]["weight"] = "0.6000000000000000000000000000000000000001"
    with pytest.raises(ValidationError, match="sum exactly to 1"):
        QualityOraclePolicy.model_validate(payload)

    payload = policy.model_dump(mode="json")
    payload["components"] = payload["components"][:1]
    with pytest.raises(ValidationError):
        QualityOraclePolicy.model_validate(payload)

    payload = policy.model_dump(mode="json")
    payload["composite_workload"] = payload["components"][0]["workload"]
    with pytest.raises(ValidationError, match="distinct from every input"):
        QualityOraclePolicy.model_validate(payload)

    payload = policy.model_dump(mode="json")
    payload["components"][1]["normalization"] = payload["components"][0]["normalization"]
    with pytest.raises(ValidationError, match="reused normalization reference"):
        QualityOraclePolicy.model_validate(payload)

    payload["components"][1]["correlation_group"] = payload["components"][0]["correlation_group"]
    assert QualityOraclePolicy.model_validate(payload)


def test_component_source_identity_and_scale_are_fail_closed(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, _bundle, _frontiers, _sources = oracle_inputs
    payload = policy.model_dump(mode="json")
    payload["components"][0]["source_semantic_identity_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="semantic source identity mismatch"):
        QualityOraclePolicy.model_validate(payload)

    payload = policy.model_dump(mode="json")
    payload["components"][0]["normalization"]["reference_max"] = "0"
    with pytest.raises(ValidationError, match="must exceed|reference hash mismatch"):
        QualityOraclePolicy.model_validate(payload)

    payload = policy.model_dump(mode="json")
    payload["components"][0]["normalization"]["rationale"] = "Tampered rationale."
    with pytest.raises(ValidationError, match="reference hash mismatch"):
        QualityOraclePolicy.model_validate(payload)

    payload = policy.model_dump(mode="json")
    payload["components"][1]["source_semantics"][0]["id"] = payload["components"][0][
        "source_semantics"
    ][0]["id"]
    changed_semantics = quality_oracle_source_semantics((_sources["reasoning"],))[0].model_copy(
        update={"id": payload["components"][0]["source_semantics"][0]["id"]}
    )
    payload["components"][1]["source_semantic_identity_sha256"] = (
        quality_oracle_source_semantic_identity((changed_semantics,))
    )
    with pytest.raises(ValidationError, match="maps to different oracle semantics"):
        QualityOraclePolicy.model_validate(payload)


def test_normalization_reference_digest_covers_every_normative_field(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, _bundle, _frontiers, _sources = oracle_inputs
    reference = policy.components[0].normalization

    assert reference.reference_sha256 == fixed_min_max_normalization_hash(reference)
    for field, replacement in (
        ("reference_id", "different-reference"),
        ("reference_version", "2"),
        ("input_unit", "fraction"),
        ("reference_min", "1"),
        ("reference_max", "99"),
        ("rationale", "Different normative rationale."),
    ):
        payload = reference.model_dump(mode="json")
        payload[field] = replacement
        with pytest.raises(ValidationError, match="reference hash mismatch"):
            FixedMinMaxNormalization.model_validate(payload)


def test_selected_quality_projection_is_price_independent_but_evidence_sensitive(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, bundle, frontiers, sources = oracle_inputs
    offerings = {item.offering.offering_id: item.offering for item in frontiers["coding"].evaluated}
    candidates = tuple(candidate.offering for candidate in bundle.candidates)
    coding_rows = [
        (offerings["a"], "80", "75", "85", 500),
        (offerings["b"], "70", None, None, 500),
        (offerings["c"], "110", None, None, 500),
    ]

    price_frontiers = {
        **frontiers,
        "coding": _frontier(
            "coding-quality",
            "coding_score",
            Goal.MAXIMIZE,
            sources["coding"],
            coding_rows,
            digest_character="3",
            cost_value="2",
            workload=frontiers["coding"].workload,
        ),
    }
    price_bundle = _bundle_from_frontiers(price_frontiers, candidates)
    unavailable_price_frontiers = {
        **frontiers,
        "coding": _frontier_without_companion_axis(
            frontiers["coding"],
            catalog_digest_character="d",
        ),
    }
    unavailable_price_bundle = _bundle_from_frontiers(
        unavailable_price_frontiers,
        candidates,
    )
    baseline_projection = quality_oracle_selected_quality_projection_hash(bundle)
    assert quality_oracle_selected_quality_projection_hash(price_bundle) == baseline_projection
    assert (
        quality_oracle_selected_quality_projection_hash(unavailable_price_bundle)
        == baseline_projection
    )

    baseline_snapshot = build_quality_oracle_snapshot(policy, bundle, generated_at=NOW)
    price_snapshot = build_quality_oracle_snapshot(policy, price_bundle, generated_at=NOW)
    assert price_snapshot.policy_hash == baseline_snapshot.policy_hash
    assert price_snapshot.selected_quality_projection_sha256 == baseline_projection
    assert price_snapshot.snapshot_id != baseline_snapshot.snapshot_id
    unavailable_price_snapshot = build_quality_oracle_snapshot(
        policy,
        unavailable_price_bundle,
        generated_at=NOW,
    )
    assert unavailable_price_snapshot.selected_quality_projection_sha256 == baseline_projection
    assert unavailable_price_snapshot.snapshot_id != baseline_snapshot.snapshot_id
    with pytest.raises(ValueError, match="does not match its bound bundle"):
        verify_quality_oracle_snapshot(
            policy,
            price_bundle,
            baseline_snapshot,
            now=NOW + timedelta(minutes=1),
        )

    quality_frontiers = {
        **frontiers,
        "coding": _frontier(
            "coding-quality",
            "coding_score",
            Goal.MAXIMIZE,
            sources["coding"],
            [
                (offerings["a"], "81", "75", "85", 500),
                *coding_rows[1:],
            ],
            digest_character="4",
            workload=frontiers["coding"].workload,
        ),
    }
    status_frontiers = {
        **frontiers,
        "coding": _frontier(
            "coding-quality",
            "coding_score",
            Goal.MAXIMIZE,
            sources["coding"],
            [coding_rows[0], coding_rows[2]],
            digest_character="5",
            workload=frontiers["coding"].workload,
        ),
    }
    capture_only_sources = (
        sources["coding"].model_copy(update={"raw_sha256": "e" * 64}),
        sources["coding"].model_copy(
            update={"retrieved_at": sources["coding"].retrieved_at + timedelta(minutes=1)}
        ),
        SourceReference.model_validate(
            {
                **sources["coding"].model_dump(mode="json"),
                "license": "changed-rights",
                "terms_url": "https://benchmarks.example/changed-terms",
            }
        ),
    )
    for index, changed_capture_source in enumerate(capture_only_sources, start=6):
        source_frontiers = {
            **frontiers,
            "coding": _frontier(
                "coding-quality",
                "coding_score",
                Goal.MAXIMIZE,
                changed_capture_source,
                coding_rows,
                digest_character=str(index),
                workload=frontiers["coding"].workload,
            ),
        }
        source_bundle = _bundle_from_frontiers(source_frontiers, candidates)
        assert quality_oracle_selected_quality_projection_hash(source_bundle) == (
            baseline_projection
        )
        source_snapshot = build_quality_oracle_snapshot(
            policy,
            source_bundle,
            generated_at=NOW,
        )
        assert source_snapshot.snapshot_id != baseline_snapshot.snapshot_id
        assert source_snapshot.selected_quality_projection_sha256 == baseline_projection

    changed_semantic_source = sources["coding"].model_copy(update={"version": "commit-2"})
    semantic_source_frontiers = {
        **frontiers,
        "coding": _frontier(
            "coding-quality",
            "coding_score",
            Goal.MAXIMIZE,
            changed_semantic_source,
            coding_rows,
            digest_character="9",
            workload=frontiers["coding"].workload,
        ),
    }
    changed_offering = offerings["c"].model_copy(update={"provider": "other-provider"})
    changed_bundles = (
        _bundle_from_frontiers(quality_frontiers, candidates),
        _bundle_from_frontiers(status_frontiers, candidates),
        _bundle_from_frontiers(semantic_source_frontiers, candidates),
        _bundle_from_frontiers(
            frontiers,
            tuple(
                changed_offering if candidate.offering_id == "c" else candidate
                for candidate in candidates
            ),
        ),
    )
    assert all(
        quality_oracle_selected_quality_projection_hash(changed) != baseline_projection
        for changed in changed_bundles
    )


def test_verified_catalog_consumption_rejects_expiry_and_wrong_trust_roots(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, bundle, _frontiers, _sources = oracle_inputs
    snapshot = build_quality_oracle_snapshot(policy, bundle, generated_at=NOW)

    with pytest.raises(ValueError, match="expired"):
        quality_oracle_catalog(
            policy,
            bundle,
            snapshot,
            now=snapshot.valid_until,
        )

    wrong_policy = policy.model_copy(update={"version": "different-policy"})
    with pytest.raises(ValueError, match="does not match its bound bundle"):
        quality_oracle_catalog(
            wrong_policy,
            bundle,
            snapshot,
            now=NOW + timedelta(minutes=1),
        )


def test_equivalent_source_tuple_order_is_accepted_and_projection_stable(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, bundle, frontiers, sources = oracle_inputs
    extra = _source("aaa-extra-feed", "c")
    first_coding = _frontier_with_quality_sources(
        frontiers["coding"],
        (sources["coding"], extra),
        digest_character="7",
    )
    reversed_coding = _frontier_with_quality_sources(
        frontiers["coding"],
        (extra, sources["coding"]),
        digest_character="8",
    )
    candidates = tuple(candidate.offering for candidate in bundle.candidates)
    first_bundle = _bundle_from_frontiers({**frontiers, "coding": first_coding}, candidates)
    reversed_bundle = _bundle_from_frontiers(
        {**frontiers, "coding": reversed_coding},
        candidates,
    )
    assert quality_oracle_selected_quality_projection_hash(first_bundle) == (
        quality_oracle_selected_quality_projection_hash(reversed_bundle)
    )

    payload = policy.model_dump(mode="json")
    semantics = quality_oracle_source_semantics((sources["coding"], extra))
    payload["components"][0]["source_semantics"] = [
        source.model_dump(mode="json") for source in semantics
    ]
    payload["components"][0]["source_semantic_identity_sha256"] = (
        quality_oracle_source_semantic_identity(semantics)
    )
    two_source_policy = QualityOraclePolicy.model_validate(payload)
    snapshot = build_quality_oracle_snapshot(two_source_policy, first_bundle, generated_at=NOW)
    assert [source.id for source in snapshot.component_captures[0].sources] == [
        "aaa-extra-feed",
        "coding-feed",
    ]


def test_future_source_and_raw_evidence_reuse_fail_closed(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, bundle, frontiers, sources = oracle_inputs
    candidates = tuple(candidate.offering for candidate in bundle.candidates)
    future_source = sources["coding"].model_copy(
        update={"retrieved_at": NOW + timedelta(minutes=6)}
    )
    future_bundle = _bundle_from_frontiers(
        {
            **frontiers,
            "coding": _frontier_with_quality_sources(
                frontiers["coding"],
                (future_source,),
                digest_character="9",
            ),
        },
        candidates,
    )
    with pytest.raises(ValueError, match="future-dated"):
        build_quality_oracle_snapshot(policy, future_bundle, generated_at=NOW)

    reused_raw_source = sources["reasoning"].model_copy(
        update={"raw_sha256": sources["coding"].raw_sha256}
    )
    reused_bundle = _bundle_from_frontiers(
        {
            **frontiers,
            "reasoning": _frontier_with_quality_sources(
                frontiers["reasoning"],
                (reused_raw_source,),
                digest_character="f",
            ),
        },
        candidates,
    )
    with pytest.raises(ValueError, match="reused raw source artifact"):
        build_quality_oracle_snapshot(policy, reused_bundle, generated_at=NOW)


def test_bundle_and_oracle_require_unique_offering_ids(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
) -> None:
    policy, bundle, frontiers, _sources = oracle_inputs
    duplicate_id_candidates = (
        _offering("duplicate", provider="first"),
        _offering("duplicate", provider="second"),
    )
    with pytest.raises(ValueError, match="distinct offering_id"):
        _bundle_from_frontiers(frontiers, duplicate_id_candidates)

    snapshot = build_quality_oracle_snapshot(policy, bundle, generated_at=NOW)
    payload = snapshot.model_dump(mode="json")
    payload["candidates"][1]["offering"]["offering_id"] = payload["candidates"][0]["offering"][
        "offering_id"
    ]
    with pytest.raises(ValidationError, match="distinct offering_id"):
        QualityOracleSnapshot.model_validate(payload)


def test_quality_oracle_artifacts_round_trip_through_bounded_loaders(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
    tmp_path: Path,
) -> None:
    policy, bundle, _frontiers, _sources = oracle_inputs
    snapshot = build_quality_oracle_snapshot(policy, bundle, generated_at=NOW)
    policy_path = tmp_path / "quality-oracle-policy.json"
    snapshot_path = tmp_path / "quality-oracle-snapshot.json"
    policy_path.write_text(dump_json(policy), encoding="utf-8")
    snapshot_path.write_text(dump_json(snapshot), encoding="utf-8")

    assert load_quality_oracle_policy(policy_path) == policy
    assert load_quality_oracle_snapshot(snapshot_path) == snapshot


def test_quality_oracle_cli_builds_verifies_and_enriches_exact_catalog(
    oracle_inputs: tuple[
        QualityOraclePolicy,
        QualityBundleSnapshot,
        dict[str, FrontierSnapshot],
        dict[str, SourceReference],
    ],
    tmp_path: Path,
) -> None:
    policy, bundle, _frontiers, _sources = oracle_inputs
    policy_path = tmp_path / "policy.json"
    bundle_path = tmp_path / "bundle.json"
    snapshot_path = tmp_path / "oracle.json"
    policy_path.write_text(dump_json(policy), encoding="utf-8")
    bundle_path.write_text(dump_json(bundle), encoding="utf-8")

    built = RUNNER.invoke(
        app,
        [
            "build-quality-oracle",
            str(policy_path),
            str(bundle_path),
            "--as-of",
            NOW.isoformat(),
            "--output",
            str(snapshot_path),
        ],
    )
    assert built.exit_code == 0, built.output
    if os.name == "posix":
        assert stat.S_IMODE(snapshot_path.stat().st_mode) == 0o600
    refused_oracle_overwrite = RUNNER.invoke(
        app,
        [
            "build-quality-oracle",
            str(policy_path),
            str(bundle_path),
            "--as-of",
            NOW.isoformat(),
            "--output",
            str(snapshot_path),
        ],
    )
    assert refused_oracle_overwrite.exit_code == 2
    assert "overwrite" in refused_oracle_overwrite.output
    snapshot = load_quality_oracle_snapshot(snapshot_path)

    verified = RUNNER.invoke(
        app,
        [
            "verify-quality-oracle",
            str(policy_path),
            str(bundle_path),
            str(snapshot_path),
            "--at",
            (NOW + timedelta(minutes=1)).isoformat(),
        ],
    )
    assert verified.exit_code == 0, verified.output
    assert verified.output.strip() == "quality oracle derivation verified"

    cost_catalog = ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=policy.composite_workload,
        offerings=[
            OfferingObservation(
                offering=candidate.offering,
                signals={"total_cost": Observation(value=Decimal("1"), unit="USD/session")},
            )
            for candidate in snapshot.candidates
        ],
    )
    catalog_path = tmp_path / "cost-catalog.json"
    enriched_path = tmp_path / "enriched.json"
    catalog_path.write_text(dump_json(cost_catalog), encoding="utf-8")
    enriched = RUNNER.invoke(
        app,
        [
            "enrich-catalog-with-quality-oracle",
            str(catalog_path),
            str(policy_path),
            str(bundle_path),
            str(snapshot_path),
            "--at",
            (NOW + timedelta(minutes=1)).isoformat(),
            "--output",
            str(enriched_path),
        ],
    )
    assert enriched.exit_code == 0, enriched.output
    if os.name == "posix":
        assert stat.S_IMODE(enriched_path.stat().st_mode) == 0o600
    refused_enriched_overwrite = RUNNER.invoke(
        app,
        [
            "enrich-catalog-with-quality-oracle",
            str(catalog_path),
            str(policy_path),
            str(bundle_path),
            str(snapshot_path),
            "--at",
            (NOW + timedelta(minutes=1)).isoformat(),
            "--output",
            str(enriched_path),
        ],
    )
    assert refused_enriched_overwrite.exit_code == 2
    assert "overwrite" in refused_enriched_overwrite.output
    result = load_catalog(enriched_path)
    assert len(result.offerings) == 3
    assert policy.quality_metric in result.offerings[0].signals
    assert policy.quality_metric not in result.offerings[1].signals
