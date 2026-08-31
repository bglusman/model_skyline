from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from model_skyline.canonical import content_hash
from model_skyline.engine import (
    FrontierEngine,
    catalog_hash,
    dominates,
    frontier_hash,
    frontier_hash_matches,
)
from model_skyline.models import (
    CostFormulaBasis,
    FormulaMetric,
    FrontierSnapshot,
    Observation,
    ObservationCatalog,
    ObservationRequirements,
    OracleMetric,
    ProjectConfig,
    SourceReference,
    WorkloadReference,
)
from model_skyline.oracles import OracleContext, OracleRegistry

NOW = datetime(2026, 8, 29, 19, tzinfo=UTC)


@pytest.mark.parametrize(
    ("basis", "expression", "message"),
    [
        (
            CostFormulaBasis.RECONSTRUCTED_COMPONENTS,
            "signals.estimated_total_cost_usd_per_success",
            "alternative all-in cost basis",
        ),
        (
            CostFormulaBasis.BILLED_TOTAL,
            ("signals.billed_total_cost_usd_per_success + signals.other_cost_usd_per_success"),
            "mixes billed_total",
        ),
        (
            CostFormulaBasis.ESTIMATED_TOTAL,
            "signals.billed_total_cost_usd_per_success",
            "does not reference that cost basis",
        ),
    ],
)
def test_cost_formula_bases_cannot_overlap(
    basis: CostFormulaBasis,
    expression: str,
    message: str,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    metric = FormulaMetric(
        kind="formula",
        unit="USD/success",
        expression=expression,
        cost_basis=basis,
    )
    config = example_config.model_copy(
        update={
            "metrics": {**example_config.metrics, "total_cost_per_success": metric},
        }
    )

    with pytest.raises(ValueError, match=message):
        FrontierEngine().calculate(
            config,
            example_catalog,
            "coding-value",
            generated_at=NOW,
        )


def test_example_frontier_and_dominance_explanation(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )

    assert [item.offering.offering_id for item in snapshot.members] == [
        "fastcloud/quick-small@us-standard",
        "balancedai/mid-agent@us-standard",
        "qualityworks/large-reasoner@us-priority",
    ]
    dominated = next(
        item
        for item in snapshot.evaluated
        if item.offering.offering_id == "fastcloud/legacy-mid@us-standard"
    )
    assert dominated.dominated_by == ("balancedai/mid-agent@us-standard",)
    assert snapshot.config_hash
    assert snapshot.snapshot_id


def test_benchmark_harness_is_independent_of_production_route_harness(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    first = example_catalog.offerings[0]
    production_route = first.model_copy(
        update={
            "offering": first.offering.model_copy(
                update={"agent_harness": "production-agent-runtime/v2"}
            )
        }
    )
    catalog = example_catalog.model_copy(
        update={"offerings": [production_route, *example_catalog.offerings[1:]]}
    )

    snapshot = FrontierEngine().calculate(
        example_config,
        catalog,
        "coding-value",
        generated_at=NOW,
    )

    assert production_route.offering.offering_id not in {
        rejection.offering_id for rejection in snapshot.rejected
    }


@given(order=st.permutations((0, 1, 2, 3)))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_frontier_is_invariant_to_catalog_order(
    order: list[int],
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    expected = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    reordered = example_catalog.model_copy(
        update={"offerings": [example_catalog.offerings[index] for index in order]}
    )
    snapshot = FrontierEngine().calculate(
        example_config,
        reordered,
        "coding-value",
        generated_at=NOW,
    )

    assert [item.offering.offering_id for item in snapshot.members] == [
        "fastcloud/quick-small@us-standard",
        "balancedai/mid-agent@us-standard",
        "qualityworks/large-reasoner@us-priority",
    ]
    assert snapshot.catalog_hash == expected.catalog_hash
    assert snapshot.snapshot_id == expected.snapshot_id


def test_absent_and_null_billing_mode_have_stable_hash_compatibility(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    assert snapshot.snapshot_id == frontier_hash(snapshot)

    pre_field_payload = snapshot.model_dump(mode="json")
    for collection_name in ("members", "evaluated"):
        for item in pre_field_payload[collection_name]:
            if item["offering"]["billing_mode"] is None:
                item["offering"].pop("billing_mode")
    restored = FrontierSnapshot.model_validate(pre_field_payload)
    assert restored.snapshot_id == snapshot.snapshot_id
    assert frontier_hash_matches(restored)

    explicit_null_payload = snapshot.model_dump(mode="json", exclude={"snapshot_id"})
    explicit_null_hash = content_hash(explicit_null_payload)
    assert explicit_null_hash != snapshot.snapshot_id
    assert frontier_hash_matches(snapshot.model_copy(update={"snapshot_id": explicit_null_hash}))

    catalog_payload = example_catalog.model_dump(mode="json")
    catalog_payload["offerings"].sort(key=lambda item: item["offering"]["offering_id"])
    for item in catalog_payload["offerings"]:
        if item["offering"]["billing_mode"] is None:
            item["offering"].pop("billing_mode")
    assert catalog_hash(example_catalog) == content_hash(catalog_payload)

    routed_catalog = example_catalog.model_copy(deep=True)
    first = routed_catalog.offerings[0]
    routed_catalog.offerings[0] = first.model_copy(
        update={"offering": first.offering.model_copy(update={"billing_mode": "managed"})}
    )
    routed_snapshot = FrontierEngine().calculate(
        example_config,
        routed_catalog,
        "coding-value",
        generated_at=NOW,
    )
    assert catalog_hash(routed_catalog) != catalog_hash(example_catalog)
    assert routed_snapshot.snapshot_id != snapshot.snapshot_id


def test_no_frontier_member_is_dominated(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    frontier = example_config.frontiers["coding-value"]

    for member in snapshot.members:
        assert not any(
            dominates(other, member, frontier.axes, frontier.uncertainty)
            for other in snapshot.evaluated
            if other.offering.offering_id != member.offering.offering_id
        )


def test_missing_axis_signal_is_rejected_with_reason(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    first = example_catalog.offerings[0]
    changed = first.model_copy(
        update={
            "signals": {key: value for key, value in first.signals.items() if key != "success_rate"}
        }
    )
    catalog = example_catalog.model_copy(
        update={"offerings": [changed, *example_catalog.offerings[1:]]}
    )

    snapshot = FrontierEngine().calculate(
        example_config,
        catalog,
        "coding-value",
        generated_at=NOW,
    )

    rejected = next(
        item for item in snapshot.rejected if item.offering_id == first.offering.offering_id
    )
    assert "signal 'success_rate' is missing" in rejected.reasons[0]


def test_catalog_is_bound_to_exact_workload(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    wrong = example_catalog.model_copy(
        update={
            "workload": WorkloadReference(
                id="research-report-v1",
                version="1.0.0",
                unit="completed_report",
            )
        }
    )

    with pytest.raises(ValueError, match="catalog workload"):
        FrontierEngine().calculate(
            example_config,
            wrong,
            "coding-value",
            generated_at=NOW,
        )


def test_axis_artifacts_preserve_formula_dependencies_and_sources(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    estimate = snapshot.members[0].axes["total_cost_per_success"]

    assert "signals.input_cache_read_tokens_per_success" in estimate.dependencies
    assert estimate.source_ids == ("model-skyline-synthetic-coding-fixture",)
    assert estimate.sources[0].license == "CC0-1.0"
    assert snapshot.sources == estimate.sources
    assert len(snapshot.catalog_hash) == 64


def test_catalog_rejects_one_source_id_with_multiple_descriptors(
    example_catalog: ObservationCatalog,
) -> None:
    payload = example_catalog.model_dump(mode="json")
    payload["offerings"][1]["default_source"]["version"] = "conflicting-version"

    with pytest.raises(ValidationError, match="multiple descriptors"):
        ObservationCatalog.model_validate(payload)


def test_frontier_snapshot_rejects_one_source_id_with_multiple_descriptors(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    payload = snapshot.model_dump(mode="json")
    conflicting = {**payload["sources"][0], "version": "conflicting-version"}
    payload["sources"].append(conflicting)

    with pytest.raises(ValidationError, match="multiple descriptors"):
        type(snapshot).model_validate(payload)


class _LeakyOracle:
    def evaluate(self, context: OracleContext):
        raise RuntimeError("request failed: https://user:password@internal.example/?api_key=secret")


class _PinnedQualityOracle:
    def evaluate(self, context: OracleContext) -> Observation:
        assert context.workload_id == "coding-session-v1"
        assert context.options["split"] == "verified"
        return Observation(
            value=Decimal("0.82"),
            unit="ratio",
            lower=Decimal("0.77"),
            upper=Decimal("0.86"),
            sample_count=200,
            observed_at=NOW - timedelta(hours=1),
            source=SourceReference(
                id="pinned-quality-benchmark",
                version="result-sha256:" + "a" * 64,
                license="MIT",
                methodology="Pinned fixture benchmark, harness, scorer, and result digest.",
            ),
        )


def test_hosted_oracle_preserves_quality_evidence_and_policy_identity(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    requirements = ObservationRequirements(
        max_age_hours=Decimal("2"),
        minimum_samples=100,
        require_bounds=True,
        require_source=True,
    )
    metric = OracleMetric(
        kind="oracle",
        oracle="pinned-quality",
        oracle_version="1",
        options={"split": "verified"},
        unit="ratio",
        requirements=requirements,
    )
    config = example_config.model_copy(
        update={
            "metrics": {
                **example_config.metrics,
                "coding_session_success": metric,
            }
        }
    )
    registry = OracleRegistry()
    registry.register("pinned-quality", "1", _PinnedQualityOracle())

    snapshot = FrontierEngine(registry).calculate(
        config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )

    estimate = snapshot.evaluated[0].axes["coding_session_success"]
    assert estimate.value == Decimal("0.82")
    assert estimate.lower == Decimal("0.77")
    assert estimate.upper == Decimal("0.86")
    assert estimate.minimum_sample_count == 200
    assert estimate.oldest_observed_at == NOW - timedelta(hours=1)
    assert estimate.dependencies == ("oracle.pinned-quality@1",)
    assert estimate.source_ids == ("pinned-quality-benchmark",)

    revised_metric = metric.model_copy(
        update={"oracle_version": "2", "options": {"split": "verified", "seed": "7"}}
    )
    revised_config = config.model_copy(
        update={
            "metrics": {
                **config.metrics,
                "coding_session_success": revised_metric,
            }
        }
    )
    registry.register("pinned-quality", "2", _PinnedQualityOracle())
    revised = FrontierEngine(registry).calculate(
        revised_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    assert snapshot.config_hash != revised.config_hash


def test_published_oracle_rejection_does_not_expose_exception_details(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    metric = OracleMetric(
        kind="oracle",
        oracle="private-judge",
        oracle_version="1",
        unit="ratio",
    )
    config = example_config.model_copy(
        update={
            "metrics": {
                **example_config.metrics,
                "coding_session_success": metric,
            }
        }
    )
    registry = OracleRegistry()
    registry.register("private-judge", "1", _LeakyOracle())

    snapshot = FrontierEngine(registry).calculate(
        config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    reasons = [reason for rejected in snapshot.rejected for reason in rejected.reasons]

    assert reasons
    assert all("oracle 'private-judge' version '1' failed" in reason for reason in reasons)
    assert all("password" not in reason and "api_key" not in reason for reason in reasons)


def test_formula_provenance_includes_workload_and_metadata_sources(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    workload_source = SourceReference(
        id="coding-workload-study",
        version="2026-08",
        license="CC-BY-4.0",
    )
    workload = example_config.workloads["coding-session-v1"].model_copy(
        update={
            "variables": {"cost_multiplier": Decimal("2")},
            "sources": [workload_source],
        }
    )
    metric = example_config.metrics["total_cost_per_success"].model_copy(
        update={
            "expression": (
                "signals.other_cost_usd_per_success * workload.cost_multiplier "
                "+ metadata.context_window * 0"
            ),
            "requirements": ObservationRequirements(),
        }
    )
    config = example_config.model_copy(
        update={
            "workloads": {"coding-session-v1": workload},
            "metrics": {**example_config.metrics, "total_cost_per_success": metric},
        }
    )

    snapshot = FrontierEngine().calculate(
        config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    evaluated = next(
        item
        for item in snapshot.evaluated
        if item.offering.offering_id == "fastcloud/quick-small@us-standard"
    )
    estimate = evaluated.axes["total_cost_per_success"]

    assert estimate.dependencies == (
        "metadata.context_window",
        "signals.other_cost_usd_per_success",
        "workload.cost_multiplier",
    )
    assert estimate.source_ids == (
        "coding-workload-study",
        "model-skyline-synthetic-coding-fixture",
    )
    assert workload_source in estimate.sources
    assert example_catalog.offerings[0].default_source in estimate.sources


def test_formula_provenance_propagates_unknown_observation_completeness(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    metric = example_config.metrics["total_cost_per_success"].model_copy(
        update={
            "expression": ("signals.other_cost_usd_per_success + signals.request_usd"),
            "requirements": ObservationRequirements(),
        }
    )
    frontier = example_config.frontiers["coding-value"]
    eligibility = frontier.eligibility.model_copy(update={"allow_unknown_age": True})
    config = example_config.model_copy(
        update={
            "metrics": {**example_config.metrics, "total_cost_per_success": metric},
            "frontiers": {
                **example_config.frontiers,
                "coding-value": frontier.model_copy(update={"eligibility": eligibility}),
            },
        }
    )
    first = example_catalog.offerings[0]
    complete = first.signals["other_cost_usd_per_success"].model_copy(update={"sample_count": 12})
    incomplete = first.signals["request_usd"].model_copy(
        update={"observed_at": None, "sample_count": None}
    )
    changed = first.model_copy(
        update={
            "signals": {
                **first.signals,
                "other_cost_usd_per_success": complete,
                "request_usd": incomplete,
            }
        }
    )
    catalog = example_catalog.model_copy(
        update={"offerings": [changed, *example_catalog.offerings[1:]]}
    )

    snapshot = FrontierEngine().calculate(
        config,
        catalog,
        "coding-value",
        generated_at=NOW,
    )
    evaluated = next(
        item
        for item in snapshot.evaluated
        if item.offering.offering_id == first.offering.offering_id
    )
    estimate = evaluated.axes["total_cost_per_success"]

    assert estimate.oldest_observed_at is None
    assert estimate.minimum_sample_count is None


def test_future_dated_observation_is_rejected(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    first = example_catalog.offerings[0]
    success = first.signals["success_rate"].model_copy(
        update={"observed_at": NOW + timedelta(hours=1)}
    )
    changed = first.model_copy(update={"signals": {**first.signals, "success_rate": success}})
    catalog = example_catalog.model_copy(
        update={"offerings": [changed, *example_catalog.offerings[1:]]}
    )

    snapshot = FrontierEngine().calculate(
        example_config,
        catalog,
        "coding-value",
        generated_at=NOW,
    )

    rejection = next(
        item for item in snapshot.rejected if item.offering_id == first.offering.offering_id
    )
    assert any("future-dated" in reason for reason in rejection.reasons)


def test_unknown_age_policy_requires_timestamp_without_max_age(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    metric = example_config.metrics["coding_session_success"].model_copy(
        update={"requirements": ObservationRequirements()}
    )
    config = example_config.model_copy(
        update={"metrics": {**example_config.metrics, "coding_session_success": metric}}
    )
    first = example_catalog.offerings[0]
    success = first.signals["success_rate"].model_copy(update={"observed_at": None})
    changed = first.model_copy(update={"signals": {**first.signals, "success_rate": success}})
    catalog = example_catalog.model_copy(
        update={"offerings": [changed, *example_catalog.offerings[1:]]}
    )

    snapshot = FrontierEngine().calculate(
        config,
        catalog,
        "coding-value",
        generated_at=NOW,
    )

    rejection = next(
        item for item in snapshot.rejected if item.offering_id == first.offering.offering_id
    )
    assert any("timestamp is required" in reason for reason in rejection.reasons)


@pytest.mark.parametrize(
    ("metric_max_age", "source_max_age"),
    [(Decimal("1"), Decimal("24")), (Decimal("24"), Decimal("1"))],
)
def test_observation_freshness_uses_stricter_metric_or_source_limit(
    metric_max_age: Decimal,
    source_max_age: Decimal,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    source = SourceReference(id="quality-benchmark", version="2026-08-29")
    first = example_catalog.offerings[0]
    success = first.signals["success_rate"].model_copy(
        update={"observed_at": NOW - timedelta(hours=2), "source": source}
    )
    changed = first.model_copy(update={"signals": {**first.signals, "success_rate": success}})
    catalog = example_catalog.model_copy(
        update={"offerings": [changed, *example_catalog.offerings[1:]]}
    )

    success_metric = example_config.metrics["coding_session_success"]
    requirements = success_metric.requirements.model_copy(update={"max_age_hours": metric_max_age})
    frontier = example_config.frontiers["coding-value"]
    eligibility = frontier.eligibility.model_copy(
        update={"max_source_age_hours": {source.id: source_max_age}}
    )
    config = example_config.model_copy(
        update={
            "metrics": {
                **example_config.metrics,
                "coding_session_success": success_metric.model_copy(
                    update={"requirements": requirements}
                ),
            },
            "frontiers": {
                **example_config.frontiers,
                "coding-value": frontier.model_copy(update={"eligibility": eligibility}),
            },
        }
    )

    snapshot = FrontierEngine().calculate(
        config,
        catalog,
        "coding-value",
        generated_at=NOW,
    )

    rejection = next(
        item for item in snapshot.rejected if item.offering_id == first.offering.offering_id
    )
    assert any("stale (2.0h > 1h)" in reason for reason in rejection.reasons)


def test_formula_dependency_honors_its_source_freshness_limit(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    source = SourceReference(id="pricing-feed", version="2026-08-29")
    first = example_catalog.offerings[0]
    input_price = first.signals["input_uncached_usd_per_million"].model_copy(
        update={"observed_at": NOW - timedelta(hours=2), "source": source}
    )
    changed = first.model_copy(
        update={
            "signals": {
                **first.signals,
                "input_uncached_usd_per_million": input_price,
            }
        }
    )
    catalog = example_catalog.model_copy(
        update={"offerings": [changed, *example_catalog.offerings[1:]]}
    )
    frontier = example_config.frontiers["coding-value"]
    eligibility = frontier.eligibility.model_copy(
        update={"max_source_age_hours": {source.id: Decimal("1")}}
    )
    config = example_config.model_copy(
        update={
            "frontiers": {
                **example_config.frontiers,
                "coding-value": frontier.model_copy(update={"eligibility": eligibility}),
            }
        }
    )

    snapshot = FrontierEngine().calculate(
        config,
        catalog,
        "coding-value",
        generated_at=NOW,
    )

    rejection = next(
        item for item in snapshot.rejected if item.offering_id == first.offering.offering_id
    )
    assert any(
        "signal 'input_uncached_usd_per_million': observation is stale (2.0h > 1h)" in reason
        for reason in rejection.reasons
    )


def test_source_freshness_does_not_affect_unrelated_sources_or_metrics(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    latency_source = SourceReference(id="latency-feed", version="2026-08-29")
    first = example_catalog.offerings[0]
    ttft = first.signals["ttft_p95_ms"].model_copy(
        update={"observed_at": NOW - timedelta(hours=2), "source": latency_source}
    )
    changed = first.model_copy(update={"signals": {**first.signals, "ttft_p95_ms": ttft}})
    catalog = example_catalog.model_copy(
        update={"offerings": [changed, *example_catalog.offerings[1:]]}
    )
    frontiers = {}
    for frontier_id in ("coding-value", "coding-responsiveness"):
        frontier = example_config.frontiers[frontier_id]
        eligibility = frontier.eligibility.model_copy(
            update={"max_source_age_hours": {latency_source.id: Decimal("1")}}
        )
        frontiers[frontier_id] = frontier.model_copy(update={"eligibility": eligibility})
    config = example_config.model_copy(
        update={"frontiers": {**example_config.frontiers, **frontiers}}
    )

    value_snapshot = FrontierEngine().calculate(
        config,
        catalog,
        "coding-value",
        generated_at=NOW,
    )
    responsive_snapshot = FrontierEngine().calculate(
        config,
        catalog,
        "coding-responsiveness",
        generated_at=NOW,
    )

    assert first.offering.offering_id in {
        item.offering.offering_id for item in value_snapshot.evaluated
    }
    rejection = next(
        item
        for item in responsive_snapshot.rejected
        if item.offering_id == first.offering.offering_id
    )
    assert any(
        "ttft_p95: observation is stale (2.0h > 1h)" in reason for reason in rejection.reasons
    )


def test_unknown_source_freshness_override_fails_closed(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    quality_source = SourceReference(id="quality-benchmark", version="2026-08-29")
    first = example_catalog.offerings[0]
    success = first.signals["success_rate"].model_copy(
        update={"observed_at": NOW - timedelta(hours=2), "source": quality_source}
    )
    changed = first.model_copy(update={"signals": {**first.signals, "success_rate": success}})
    catalog = example_catalog.model_copy(
        update={"offerings": [changed, *example_catalog.offerings[1:]]}
    )
    success_metric = example_config.metrics["coding_session_success"]
    requirements = success_metric.requirements.model_copy(update={"max_age_hours": Decimal("24")})
    frontier = example_config.frontiers["coding-value"]
    eligibility = frontier.eligibility.model_copy(
        update={"max_source_age_hours": {"unrelated-pricing-feed": Decimal("1")}}
    )
    config = example_config.model_copy(
        update={
            "metrics": {
                **example_config.metrics,
                "coding_session_success": success_metric.model_copy(
                    update={"requirements": requirements}
                ),
            },
            "frontiers": {
                **example_config.frontiers,
                "coding-value": frontier.model_copy(update={"eligibility": eligibility}),
            },
        }
    )

    with pytest.raises(ValueError, match="source age limit references unknown source"):
        FrontierEngine().calculate(
            config,
            catalog,
            "coding-value",
            generated_at=NOW,
        )


def test_workload_and_catalog_source_descriptors_must_agree(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    catalog_source = example_catalog.offerings[0].default_source
    assert catalog_source is not None
    workload = example_config.workloads["coding-session-v1"].model_copy(
        update={
            "sources": [
                catalog_source.model_copy(update={"version": "conflicting-workload-version"})
            ]
        }
    )
    config = example_config.model_copy(update={"workloads": {"coding-session-v1": workload}})

    with pytest.raises(ValueError, match="different descriptors"):
        FrontierEngine().calculate(
            config,
            example_catalog,
            "coding-value",
            generated_at=NOW,
        )


def test_engine_is_independent_of_ambient_decimal_context(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    artifacts = []
    for precision in (3, 10, 34):
        with localcontext() as context:
            context.prec = precision
            artifacts.append(
                FrontierEngine()
                .calculate(
                    example_config,
                    example_catalog,
                    "coding-value",
                    generated_at=NOW,
                )
                .model_dump(mode="json")
            )

    assert artifacts[0] == artifacts[1] == artifacts[2]


def test_frontier_config_hash_ignores_unrelated_selection_changes(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    original = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    definition = example_config.selections["coding-agent-defaults"].model_copy(update={"count": 2})
    changed = example_config.model_copy(
        update={"selections": {"coding-agent-defaults": definition}}
    )
    recalculated = FrontierEngine().calculate(
        changed,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )

    assert original.config_hash == recalculated.config_hash


def test_frontier_config_hash_ignores_only_workload_source_retrieval_time(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    source = SourceReference(
        id="coding-workload-study",
        version="2026-08",
        url="https://example.test/coding-workload-study.json",
        license="CC-BY-4.0",
        methodology="Pinned workload study fixture.",
        raw_sha256="a" * 64,
        retrieved_at=NOW,
    )
    workload = example_config.workloads["coding-session-v1"].model_copy(
        update={"sources": [source]}
    )
    config = example_config.model_copy(update={"workloads": {"coding-session-v1": workload}})
    original = FrontierEngine().calculate(
        config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )

    refreshed_source = source.model_copy(update={"retrieved_at": NOW + timedelta(minutes=5)})
    refreshed_workload = workload.model_copy(update={"sources": [refreshed_source]})
    refreshed_config = config.model_copy(
        update={"workloads": {"coding-session-v1": refreshed_workload}}
    )
    refreshed = FrontierEngine().calculate(
        refreshed_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )

    assert original.config_hash == refreshed.config_hash
    assert original.catalog_hash == refreshed.catalog_hash
    assert original.snapshot_id != refreshed.snapshot_id
    refreshed_provenance = next(
        item for item in refreshed.sources if item.id == "coding-workload-study"
    )
    assert refreshed_provenance.retrieved_at == NOW + timedelta(minutes=5)


@pytest.mark.parametrize(
    "source_update",
    [
        {"id": "revised-workload-study"},
        {"version": "2026-09"},
        {"url": "https://example.test/revised-study.json"},
        {"terms_url": "https://example.test/revised-terms"},
        {"license": "ODC-BY-1.0"},
        {"methodology": "Revised pinned workload study fixture."},
        {"raw_sha256": "b" * 64},
    ],
    ids=("id", "version", "url", "terms-url", "license", "methodology", "raw-digest"),
)
def test_frontier_config_hash_binds_workload_source_semantic_fields(
    source_update: dict[str, object],
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    source = SourceReference(
        id="coding-workload-study",
        version="2026-08",
        url="https://example.test/coding-workload-study.json",
        terms_url="https://example.test/terms",
        license="CC-BY-4.0",
        methodology="Pinned workload study fixture.",
        raw_sha256="a" * 64,
        retrieved_at=NOW,
    )
    workload = example_config.workloads["coding-session-v1"].model_copy(
        update={"sources": [source]}
    )
    config = example_config.model_copy(update={"workloads": {"coding-session-v1": workload}})
    original = FrontierEngine().calculate(
        config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )

    changed_source = SourceReference.model_validate(
        {**source.model_dump(mode="json"), **source_update}
    )
    changed_workload = workload.model_copy(update={"sources": [changed_source]})
    changed_config = config.model_copy(
        update={"workloads": {"coding-session-v1": changed_workload}}
    )
    changed = FrontierEngine().calculate(
        changed_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )

    assert original.config_hash != changed.config_hash


def test_catalog_rejects_non_finite_metadata(
    example_catalog: ObservationCatalog,
) -> None:
    payload = example_catalog.model_dump(mode="json")
    payload["offerings"][0]["metadata"]["bad"] = float("nan")

    with pytest.raises(ValidationError, match="finite"):
        ObservationCatalog.model_validate(payload)


@pytest.mark.parametrize(
    "bad_value",
    ["bad\x01id", "bad\x1b[31mid"],
)
def test_catalog_rejects_xml_and_terminal_controls(
    bad_value: str,
    example_catalog: ObservationCatalog,
) -> None:
    payload = example_catalog.model_dump(mode="json")
    payload["offerings"][0]["offering"]["offering_id"] = bad_value

    with pytest.raises(ValidationError, match="control"):
        ObservationCatalog.model_validate(payload)


def test_catalog_rejects_json_numbers_outside_rfc8785_domain(
    example_catalog: ObservationCatalog,
) -> None:
    payload = example_catalog.model_dump(mode="json")
    payload["offerings"][0]["metadata"]["unsafe_integer"] = 2**60

    with pytest.raises(ValidationError, match="canonical JSON"):
        ObservationCatalog.model_validate(payload)
