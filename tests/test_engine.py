from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from model_skyline.engine import FrontierEngine, dominates
from model_skyline.models import (
    ObservationCatalog,
    ObservationRequirements,
    ProjectConfig,
    SourceReference,
    WorkloadReference,
)

NOW = datetime(2026, 8, 29, 19, tzinfo=UTC)


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
