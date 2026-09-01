from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from model_skyline.canonical import content_hash
from model_skyline.cli import app
from model_skyline.engine import FrontierEngine, frontier_hash
from model_skyline.models import (
    AxisDescriptor,
    AxisEstimate,
    AxisEvidenceCandidate,
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
from model_skyline.quality_portfolio import (
    PortfolioComponent,
    PortfolioPolicy,
    build_portfolio,
    portfolio_policy_hash,
    verify_portfolio,
)

NOW = datetime(2026, 8, 31, 20, tzinfo=UTC)
runner = CliRunner()


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


def _source(
    source_id: str,
    digest_character: str,
    *,
    rights: bool = True,
) -> SourceReference:
    return SourceReference(
        id=source_id,
        version="commit-1",
        url=f"https://benchmarks.example/{source_id}.json",
        terms_url="https://benchmarks.example/terms" if rights else None,
        license="fixture-license" if rights else None,
        methodology=f"Pinned benchmark methodology for {source_id}.",
        raw_sha256=digest_character * 64,
        retrieved_at=NOW - timedelta(hours=1),
    )


def _workload(frontier_id: str, digest_character: str) -> WorkloadReference:
    return WorkloadReference(
        id=f"{frontier_id}-cohort",
        version=f"task-set-sha256:{digest_character * 64}",
        unit="task",
    )


def _frontier(
    frontier_id: str,
    metric: str,
    goal: Goal,
    source: SourceReference,
    offerings: tuple[OfferingKey, ...],
    scores: dict[str, Decimal],
    *,
    digest_character: str,
    observed_at: dict[str, datetime] | None = None,
    workload: WorkloadReference | None = None,
) -> FrontierSnapshot:
    quality_axis = AxisDescriptor(metric=metric, goal=goal, unit="percent")
    # The selected quality estimate deliberately survives even though every
    # route lacks the companion cost axis and is rejected from this frontier.
    companion_axis = AxisDescriptor(
        metric="cost_per_task",
        goal=Goal.MINIMIZE,
        unit="USD/task",
    )
    generated_at = NOW - timedelta(minutes=10)
    selected_workload = workload or _workload(frontier_id, digest_character)
    evidence_candidates: list[AxisEvidenceCandidate] = []
    for offering in offerings:
        estimate = (
            AxisEstimate(
                value=scores[offering.offering_id],
                unit="percent",
                lower=scores[offering.offering_id] - Decimal(1),
                upper=scores[offering.offering_id] + Decimal(1),
                dependencies=(f"signals.{metric}",),
                source_ids=(source.id,),
                sources=(source,),
                oldest_observed_at=(
                    observed_at.get(offering.offering_id, NOW - timedelta(hours=2))
                    if observed_at is not None
                    else NOW - timedelta(hours=2)
                ),
                minimum_sample_count=100,
            )
            if offering.offering_id in scores
            else None
        )
        evidence_candidates.append(
            AxisEvidenceCandidate(
                offering=offering,
                axes={metric: estimate} if estimate is not None else {},
            )
        )
    inventory = build_axis_evidence_inventory(
        config_hash=digest_character * 64,
        catalog_hash=digest_character * 64,
        generated_at=generated_at,
        workload=selected_workload,
        axes=(quality_axis, companion_axis),
        candidates=evidence_candidates,
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
        axes=(quality_axis, companion_axis),
        members=(),
        evaluated=(),
        rejected=tuple(
            RejectedOffering(offering_id=offering.offering_id, reasons=("cost missing",))
            for offering in offerings
        ),
        axis_evidence=inventory,
        public_release_blocked=True,
        sources=(source,),
        source_watermarks={source.id: NOW - timedelta(hours=2)},
    )
    return provisional.model_copy(update={"snapshot_id": frontier_hash(provisional)})


def _component(
    component_id: str,
    frontier_id: str,
    workload: WorkloadReference,
    metric: str,
    *,
    goal: Goal = Goal.MAXIMIZE,
    output_signal: str | None = None,
    max_age_seconds: int = 86_400,
) -> PortfolioComponent:
    return PortfolioComponent(
        component_id=component_id,
        frontier_id=frontier_id,
        workload=workload,
        quality_axis=AxisDescriptor(metric=metric, goal=goal, unit="percent"),
        output_signal=output_signal or f"{component_id}_score",
        max_age_seconds=max_age_seconds,
        correlation_group=f"{component_id}-family",
    )


def _base(policy: PortfolioPolicy, offerings: tuple[OfferingKey, ...]) -> ObservationCatalog:
    source = _source("base-cost", "c")
    return ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=policy.output_workload,
        offerings=[
            OfferingObservation(
                offering=offering,
                signals={
                    "cost_per_task": Observation(
                        value=Decimal(index),
                        unit="USD/task",
                        observed_at=NOW - timedelta(minutes=30),
                        source=source,
                    )
                },
                metadata={"base_note": offering.offering_id, "publication_safe": True},
                default_source=source,
            )
            for index, offering in enumerate(offerings, start=1)
        ],
    )


def _coverage_policy(
    coding_workload: WorkloadReference,
    reasoning_workload: WorkloadReference,
    *,
    max_age_seconds: int = 86_400,
) -> PortfolioPolicy:
    # Reverse input order to exercise canonical component ordering.
    return PortfolioPolicy(
        portfolio_id="general-agent-quality",
        version="2026-08-31",
        output_workload=WorkloadReference(
            id="general-agent-quality",
            version="2026-08-31",
            unit="portfolio-candidate",
        ),
        components=(
            _component(
                "reasoning",
                "reasoning-quality",
                reasoning_workload,
                "arc_score",
                output_signal="arc_agi_2_score",
                max_age_seconds=max_age_seconds,
            ),
            _component(
                "coding",
                "coding-quality",
                coding_workload,
                "swe_score",
                output_signal="swe_bench_score",
                max_age_seconds=max_age_seconds,
            ),
        ),
        required_component_ids=("coding",),
        minimum_measured_components=1,
        correlation_rationale="Coding and reasoning share model capability but not task rows.",
    )


def test_policy_is_stable_intent_and_rejects_volatile_frontier_fields() -> None:
    coding_workload = _workload("coding-quality", "a")
    reasoning_workload = _workload("reasoning-quality", "b")
    policy = _coverage_policy(coding_workload, reasoning_workload)

    assert tuple(component.component_id for component in policy.components) == (
        "coding",
        "reasoning",
    )
    assert (
        policy.model_dump()
        .keys()
        .isdisjoint({"frontier_snapshot_id", "config_hash", "catalog_hash", "generated_at"})
    )
    same_policy = policy.model_copy(update={"components": tuple(reversed(policy.components))})
    same_policy = PortfolioPolicy.model_validate(same_policy.model_dump(mode="json"))
    assert portfolio_policy_hash(same_policy) == portfolio_policy_hash(policy)

    payload = policy.model_dump(mode="json")
    payload["frontier_snapshot_id"] = "0" * 64
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PortfolioPolicy.model_validate(payload)


def test_coverage_portfolio_emits_separate_signals_and_visible_missingness() -> None:
    offerings = (_offering("a"), _offering("b"), _offering("c"))
    coding_source = _source("swe-bench", "a")
    reasoning_source = _source("arc-agi-2", "b")
    coding = _frontier(
        "coding-quality",
        "swe_score",
        Goal.MAXIMIZE,
        coding_source,
        offerings,
        {"a": Decimal(80), "b": Decimal(70)},
        digest_character="a",
    )
    reasoning = _frontier(
        "reasoning-quality",
        "arc_score",
        Goal.MAXIMIZE,
        reasoning_source,
        offerings,
        {"a": Decimal(60), "c": Decimal(90)},
        digest_character="b",
    )
    policy = _coverage_policy(coding.workload, reasoning.workload)
    base = _base(policy, offerings)

    result = build_portfolio(
        policy,
        {"coding": coding, "reasoning": reasoning},
        base,
        generated_at=NOW,
    )

    candidates = {
        candidate.offering.offering_id: candidate for candidate in result.snapshot.candidates
    }
    assert candidates["a"].component_failures == {}
    assert candidates["b"].component_failures == {"reasoning": ("missing",)}
    assert candidates["c"].component_failures == {"coding": ("missing",)}

    catalog = {item.offering.offering_id: item for item in result.catalog.offerings}
    assert set(catalog) == {"a", "b", "c"}
    assert set(catalog["a"].signals) == {
        "cost_per_task",
        "swe_bench_score",
        "arc_agi_2_score",
    }
    assert set(catalog["b"].signals) == {"cost_per_task", "swe_bench_score"}
    assert set(catalog["c"].signals) == {"cost_per_task"}
    assert catalog["a"].metadata["base_note"] == "a"
    assert catalog["a"].metadata["publication_safe"] is False
    quality_source = catalog["a"].signals["swe_bench_score"].source
    assert quality_source is not None
    assert quality_source.raw_sha256 == result.snapshot.quality_projection_sha256
    assert result.snapshot.base_catalog_hash == content_hash(base)
    assert result.snapshot.catalog_hash == content_hash(result.catalog)

    bindings = {binding.component_id: binding for binding in result.snapshot.bindings}
    assert bindings["coding"].sources == (coding_source,)
    assert bindings["coding"].sources[0].terms_url == coding_source.terms_url
    assert bindings["coding"].sources[0].retrieved_at == coding_source.retrieved_at

    conflicting_offering = base.offerings[0].model_copy(
        update={
            "signals": {
                **base.offerings[0].signals,
                "swe_bench_score": base.offerings[0].signals["cost_per_task"],
            }
        }
    )
    conflicting_base = base.model_copy(
        update={"offerings": [conflicting_offering, *base.offerings[1:]]}
    )
    with pytest.raises(ValueError, match="already contains portfolio output signals"):
        build_portfolio(
            policy,
            {"coding": coding, "reasoning": reasoning},
            conflicting_base,
            generated_at=NOW,
        )

    config = ProjectConfig.model_validate(
        {
            "schema_version": "model-skyline/v1alpha1",
            "workloads": {
                policy.output_workload.id: {
                    "unit": policy.output_workload.unit,
                    "version": policy.output_workload.version,
                    "harness": "portfolio-test-harness@1",
                    "cohort": "portfolio-test-cohort@1",
                }
            },
            "metrics": {
                "cost": {
                    "kind": "signal",
                    "signal": "cost_per_task",
                    "unit": "USD/task",
                },
                "quality": {
                    "kind": "signal",
                    "signal": "swe_bench_score",
                    "unit": "percent",
                },
            },
            "frontiers": {
                "value": {
                    "workload": policy.output_workload.id,
                    "axes": [
                        {"metric": "cost", "goal": "minimize"},
                        {"metric": "quality", "goal": "maximize"},
                    ],
                    "order_by": "cost",
                }
            },
        }
    )
    value_frontier = FrontierEngine().calculate(
        config,
        result.catalog,
        "value",
        generated_at=NOW,
    )
    assert {item.offering.offering_id for item in value_frontier.evaluated} == {"a", "b"}
    assert {item.offering_id for item in value_frontier.rejected} == {"c"}


def test_core_formula_combines_portfolio_signals_with_exact_decimal_math() -> None:
    offerings = (_offering("a"), _offering("b"))
    coding = _frontier(
        "coding-quality",
        "swe_score",
        Goal.MAXIMIZE,
        _source("swe-bench", "a"),
        offerings,
        {"a": Decimal(80), "b": Decimal(70)},
        digest_character="a",
    )
    efficiency = _frontier(
        "reasoning-quality",
        "error_rate",
        Goal.MINIMIZE,
        _source("reasoning-errors", "b"),
        offerings,
        {"a": Decimal(20), "b": Decimal(50)},
        digest_character="b",
    )
    policy = PortfolioPolicy(
        portfolio_id="formula-quality",
        version="1",
        output_workload=WorkloadReference(id="formula-quality", version="1", unit="candidate"),
        components=(
            _component(
                "coding",
                "coding-quality",
                coding.workload,
                "swe_score",
                output_signal="swe_bench_score",
            ),
            _component(
                "reasoning",
                "reasoning-quality",
                efficiency.workload,
                "error_rate",
                goal=Goal.MINIMIZE,
                output_signal="reasoning_error_rate",
            ),
        ),
        required_component_ids=("coding", "reasoning"),
        minimum_measured_components=2,
        correlation_rationale="Shared capability is represented without independence claims.",
    )
    base = _base(policy, offerings)

    result = build_portfolio(
        policy,
        {"coding": coding, "reasoning": efficiency},
        base,
        generated_at=NOW,
    )

    config = ProjectConfig.model_validate(
        {
            "schema_version": "model-skyline/v1alpha1",
            "workloads": {
                policy.output_workload.id: {
                    "unit": policy.output_workload.unit,
                    "version": policy.output_workload.version,
                    "harness": "portfolio-formula-harness@1",
                    "cohort": "portfolio-formula-cohort@1",
                }
            },
            "metrics": {
                "cost": {
                    "kind": "signal",
                    "signal": "cost_per_task",
                    "unit": "USD/task",
                },
                "quality_index": {
                    "kind": "formula",
                    "expression": (
                        "signals.swe_bench_score * 0.25 / 100 + "
                        "(100 - signals.reasoning_error_rate) * 0.75 / 100"
                    ),
                    "unit": "normalized_quality_score",
                },
            },
            "frontiers": {
                "value": {
                    "workload": policy.output_workload.id,
                    "axes": [
                        {"metric": "cost", "goal": "minimize"},
                        {"metric": "quality_index", "goal": "maximize"},
                    ],
                    "order_by": "cost",
                }
            },
        }
    )
    frontier = FrontierEngine().calculate(
        config,
        result.catalog,
        "value",
        generated_at=NOW,
    )
    quality_by_offering = {
        item.offering.offering_id: item.axes["quality_index"] for item in frontier.evaluated
    }
    assert quality_by_offering["a"].value == Decimal("0.8")
    assert quality_by_offering["b"].value == Decimal("0.55")
    assert quality_by_offering["a"].dependencies == (
        "signals.reasoning_error_rate",
        "signals.swe_bench_score",
    )


def test_freshness_uses_oldest_observation_not_refreshed_frontier_time() -> None:
    offerings = (_offering("fresh"), _offering("old"))
    observed = {
        "fresh": NOW - timedelta(hours=1),
        "old": NOW - timedelta(days=2),
    }
    coding = _frontier(
        "coding-quality",
        "swe_score",
        Goal.MAXIMIZE,
        _source("swe-bench", "a"),
        offerings,
        {"fresh": Decimal(80), "old": Decimal(90)},
        digest_character="a",
        observed_at=observed,
    )
    reasoning = _frontier(
        "reasoning-quality",
        "arc_score",
        Goal.MAXIMIZE,
        _source("arc-agi-2", "b"),
        offerings,
        {},
        digest_character="b",
    )
    policy = _coverage_policy(
        coding.workload,
        reasoning.workload,
        max_age_seconds=3_600 * 12,
    )
    base = _base(policy, offerings)

    result = build_portfolio(
        policy,
        {"coding": coding, "reasoning": reasoning},
        base,
        generated_at=NOW,
    )

    by_id = {candidate.offering.offering_id: candidate for candidate in result.snapshot.candidates}
    assert by_id["fresh"].component_failures == {"reasoning": ("missing",)}
    assert by_id["old"].component_failures["coding"] == ("evidence_stale",)
    assert result.snapshot.valid_until == observed["fresh"] + timedelta(hours=12)


def test_incomplete_rights_metadata_rejects_evidence_but_preserves_it_for_audit() -> None:
    offerings = (_offering("a"),)
    coding = _frontier(
        "coding-quality",
        "swe_score",
        Goal.MAXIMIZE,
        _source("swe-bench", "a"),
        offerings,
        {"a": Decimal(80)},
        digest_character="a",
    )
    unreviewed_source = _source("arc-agi-2", "b", rights=False)
    reasoning = _frontier(
        "reasoning-quality",
        "arc_score",
        Goal.MAXIMIZE,
        unreviewed_source,
        offerings,
        {"a": Decimal(60)},
        digest_character="b",
    )
    policy = _coverage_policy(coding.workload, reasoning.workload)
    base = _base(policy, offerings)

    result = build_portfolio(
        policy,
        {"coding": coding, "reasoning": reasoning},
        base,
        generated_at=NOW,
    )

    audit = result.snapshot.candidates[0]
    assert audit.component_failures["reasoning"] == ("source_rights_missing",)
    bindings = {item.component_id: item for item in result.snapshot.bindings}
    assert bindings["reasoning"].sources == (unreviewed_source,)
    assert set(result.catalog.offerings[0].signals) == {"cost_per_task", "swe_bench_score"}


def test_exact_offering_key_mismatch_is_not_silently_treated_as_missing() -> None:
    expected = _offering("a", provider="expected-provider")
    mismatched = _offering("a", provider="different-provider")
    coding = _frontier(
        "coding-quality",
        "swe_score",
        Goal.MAXIMIZE,
        _source("swe-bench", "a"),
        (mismatched,),
        {"a": Decimal(80)},
        digest_character="a",
    )
    reasoning = _frontier(
        "reasoning-quality",
        "arc_score",
        Goal.MAXIMIZE,
        _source("arc-agi-2", "b"),
        (expected,),
        {"a": Decimal(60)},
        digest_character="b",
    )
    policy = _coverage_policy(coding.workload, reasoning.workload)
    base = _base(policy, (expected,))

    with pytest.raises(ValueError, match="OfferingKey mismatch"):
        build_portfolio(
            policy,
            {"coding": coding, "reasoning": reasoning},
            base,
            generated_at=NOW,
        )


def test_companion_axis_or_config_change_rebinds_snapshot_not_quality_projection() -> None:
    offerings = (_offering("a"),)
    coding_source = _source("swe-bench", "a")
    reasoning_source = _source("arc-agi-2", "b")
    coding_v1 = _frontier(
        "coding-quality",
        "swe_score",
        Goal.MAXIMIZE,
        coding_source,
        offerings,
        {"a": Decimal(80)},
        digest_character="a",
    )
    coding_v2 = _frontier(
        "coding-quality",
        "swe_score",
        Goal.MAXIMIZE,
        coding_source,
        offerings,
        {"a": Decimal(80)},
        digest_character="c",
        workload=coding_v1.workload,
    )
    reasoning = _frontier(
        "reasoning-quality",
        "arc_score",
        Goal.MAXIMIZE,
        reasoning_source,
        offerings,
        {"a": Decimal(60)},
        digest_character="b",
    )
    policy = _coverage_policy(coding_v1.workload, reasoning.workload)
    base = _base(policy, offerings)

    first = build_portfolio(
        policy,
        {"coding": coding_v1, "reasoning": reasoning},
        base,
        generated_at=NOW,
    )
    second = build_portfolio(
        policy,
        {"coding": coding_v2, "reasoning": reasoning},
        base,
        generated_at=NOW,
    )

    assert first.snapshot.policy_hash == second.snapshot.policy_hash
    assert first.snapshot.snapshot_id != second.snapshot.snapshot_id
    assert first.snapshot.quality_projection_sha256 == second.snapshot.quality_projection_sha256
    assert content_hash(first.catalog) == content_hash(second.catalog)


def test_replay_verification_rejects_expired_or_tampered_inputs() -> None:
    offerings = (_offering("a"),)
    coding = _frontier(
        "coding-quality",
        "swe_score",
        Goal.MAXIMIZE,
        _source("swe-bench", "a"),
        offerings,
        {"a": Decimal(80)},
        digest_character="a",
    )
    reasoning = _frontier(
        "reasoning-quality",
        "arc_score",
        Goal.MAXIMIZE,
        _source("arc-agi-2", "b"),
        offerings,
        {"a": Decimal(60)},
        digest_character="b",
    )
    policy = _coverage_policy(coding.workload, reasoning.workload)
    base = _base(policy, offerings)
    result = build_portfolio(
        policy,
        {"coding": coding, "reasoning": reasoning},
        base,
        generated_at=NOW,
    )

    verify_portfolio(
        policy,
        {"coding": coding, "reasoning": reasoning},
        base,
        result.snapshot,
        now=NOW + timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="expired"):
        verify_portfolio(
            policy,
            {"coding": coding, "reasoning": reasoning},
            base,
            result.snapshot,
            now=result.snapshot.valid_until,
        )

    tampered = coding.model_copy(update={"config_hash": "f" * 64})
    with pytest.raises(ValueError, match="axis evidence inventory"):
        verify_portfolio(
            policy,
            {"coding": tampered, "reasoning": reasoning},
            base,
            result.snapshot,
            now=NOW + timedelta(minutes=1),
        )


def test_component_output_signals_are_formula_safe_and_unique() -> None:
    coding_workload = _workload("coding-quality", "a")
    reasoning_workload = _workload("reasoning-quality", "b")

    with pytest.raises(ValidationError, match="String should match pattern"):
        _component(
            "coding",
            "coding-quality",
            coding_workload,
            "swe_score",
            output_signal="coding/swe_score",
        )

    with pytest.raises(ValidationError, match="output signals must be unique"):
        PortfolioPolicy(
            portfolio_id="duplicate-signals",
            version="1",
            output_workload=WorkloadReference(id="portfolio", version="1", unit="candidate"),
            components=(
                _component(
                    "coding",
                    "coding-quality",
                    coding_workload,
                    "swe_score",
                    output_signal="benchmark_score",
                ),
                _component(
                    "reasoning",
                    "reasoning-quality",
                    reasoning_workload,
                    "arc_score",
                    output_signal="benchmark_score",
                ),
            ),
            required_component_ids=("coding", "reasoning"),
            minimum_measured_components=2,
            correlation_rationale="No independence claim.",
        )


def test_cli_builds_and_replays_portfolio_catalog(tmp_path: Path) -> None:
    offerings = (_offering("a"), _offering("b"))
    coding = _frontier(
        "coding-quality",
        "swe_score",
        Goal.MAXIMIZE,
        _source("swe-bench", "a"),
        offerings,
        {"a": Decimal(80), "b": Decimal(70)},
        digest_character="a",
    )
    reasoning = _frontier(
        "reasoning-quality",
        "arc_score",
        Goal.MAXIMIZE,
        _source("arc-agi-2", "b"),
        offerings,
        {"a": Decimal(60), "b": Decimal(75)},
        digest_character="b",
    )
    policy = _coverage_policy(coding.workload, reasoning.workload)
    base = _base(policy, offerings)

    inputs = {
        "policy": (tmp_path / "policy.json", policy),
        "base": (tmp_path / "base.json", base),
        "coding": (tmp_path / "coding.json", coding),
        "reasoning": (tmp_path / "reasoning.json", reasoning),
    }
    for path, model in inputs.values():
        path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    catalog_output = tmp_path / "portfolio-catalog.json"
    derivation_output = tmp_path / "portfolio-derivation.json"

    built = runner.invoke(
        app,
        [
            "build-quality-portfolio",
            str(inputs["policy"][0]),
            str(inputs["base"][0]),
            "--catalog-output",
            str(catalog_output),
            "--derivation-output",
            str(derivation_output),
            "--component-frontier",
            f"coding={inputs['coding'][0]}",
            "--component-frontier",
            f"reasoning={inputs['reasoning'][0]}",
            "--as-of",
            NOW.isoformat(),
        ],
    )

    assert built.exit_code == 0, built.output
    payload = ObservationCatalog.model_validate_json(catalog_output.read_text())
    assert "swe_bench_score" in payload.offerings[0].signals

    verified = runner.invoke(
        app,
        [
            "verify-quality-portfolio",
            str(inputs["policy"][0]),
            str(inputs["base"][0]),
            str(derivation_output),
            "--component-frontier",
            f"coding={inputs['coding'][0]}",
            "--component-frontier",
            f"reasoning={inputs['reasoning'][0]}",
            "--at",
            (NOW + timedelta(minutes=1)).isoformat(),
        ],
    )

    assert verified.exit_code == 0, verified.output
    assert "valid quality portfolio" in verified.output
