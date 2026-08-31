from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from model_skyline.canonical import content_hash
from model_skyline.engine import FrontierEngine
from model_skyline.models import ObservationCatalog, ProjectConfig

NOW = datetime(2026, 8, 29, 19, tzinfo=UTC)


def _pre_v06_effective_policy(
    config: ProjectConfig,
    frontier_id: str,
) -> dict[str, Any]:
    """Reconstruct the policy payload emitted before source-age overrides existed."""

    frontier = config.frontiers[frontier_id]
    workload_id = frontier.workload
    workload = config.workloads[workload_id]
    frontier_policy = frontier.model_dump(mode="json")
    assert frontier_policy["eligibility"].pop("max_source_age_hours") == {}
    return {
        "schema_version": config.schema_version,
        "frontier_id": frontier_id,
        "frontier": frontier_policy,
        "workload_id": workload_id,
        "workload": workload.model_dump(
            mode="json",
            exclude={"sources": {"__all__": {"retrieved_at"}}},
        ),
        "metrics": {
            axis.metric: config.metrics[axis.metric].model_dump(mode="json")
            for axis in frontier.axes
        },
    }


def test_empty_source_age_map_preserves_pre_v06_config_hash(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )

    assert snapshot.config_hash == content_hash(
        _pre_v06_effective_policy(example_config, "coding-value")
    )


def test_configured_source_age_map_is_bound_into_config_hash(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    source = example_catalog.offerings[0].default_source
    assert source is not None
    frontier = example_config.frontiers["coding-value"]
    eligibility = frontier.eligibility.model_copy(
        update={"max_source_age_hours": {source.id: Decimal("48")}}
    )
    configured_frontier = frontier.model_copy(update={"eligibility": eligibility})
    configured = example_config.model_copy(
        update={
            "frontiers": {
                **example_config.frontiers,
                "coding-value": configured_frontier,
            }
        }
    )

    baseline = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    snapshot = FrontierEngine().calculate(
        configured,
        example_catalog,
        "coding-value",
        generated_at=NOW,
    )
    expected_policy = _pre_v06_effective_policy(example_config, "coding-value")
    expected_policy["frontier"]["eligibility"]["max_source_age_hours"] = {source.id: "48"}

    assert snapshot.config_hash != baseline.config_hash
    assert snapshot.config_hash == content_hash(expected_policy)
