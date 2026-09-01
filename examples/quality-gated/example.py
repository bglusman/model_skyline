#!/usr/bin/env python3
"""Run a synthetic three-benchmark quality-gated selection entirely in memory.

Every value and organization name in this example is fictional. The three
benchmark components are operator-declared distinct workloads; using separate
IDs and snapshots is not evidence that their measurements are statistically
independent.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from model_skyline.engine import FrontierEngine
from model_skyline.models import (
    FrontierSnapshot,
    ObservationCatalog,
    OfferingKey,
    ProjectConfig,
)
from model_skyline.quality_bundle import (
    QualityBundlePolicy,
    build_quality_bundle_snapshot,
    verify_quality_bundle_snapshot,
)
from model_skyline.quality_selection import (
    build_quality_gated_selection_snapshot,
    verify_quality_gated_selection_snapshot,
)
from model_skyline.selection_overlap import (
    CrossFrontierSelectionPolicy,
    FrontierPriorityGroup,
    SecondaryFrontierInput,
    SecondaryFrontierReference,
    build_frontier_proximity_snapshot,
)

BASE_TIME = datetime(2026, 8, 31, 18, tzinfo=UTC)
OBSERVED_AT = BASE_TIME - timedelta(minutes=20)
SELECTION_TIME = BASE_TIME + timedelta(minutes=1)
VERIFY_TIME = SELECTION_TIME + timedelta(minutes=1)

PRIMARY_FRONTIER_ID = "synthetic-cost-performance"
SELECTION_ID = "synthetic-agent-defaults"

ECONOMY = OfferingKey(
    offering_id="fictional/economy@us-standard",
    model_id="economy-2026-08",
    provider="fictional",
    endpoint="responses",
    billing_mode="list",
    region="us",
    service_tier="standard",
    quantization="hosted-native",
    reasoning_effort="medium",
    agent_harness="synthetic-general-agent@1",
    capabilities=("text", "tools"),
)
BALANCED = OfferingKey(
    offering_id="imaginary/balanced@us-standard",
    model_id="balanced-2026-08",
    provider="imaginary",
    endpoint="responses",
    billing_mode="list",
    region="us",
    service_tier="standard",
    quantization="hosted-native",
    reasoning_effort="medium",
    agent_harness="synthetic-general-agent@1",
    capabilities=("text", "tools"),
)
EXPERT = OfferingKey(
    offering_id="example/expert@us-standard",
    model_id="expert-2026-08",
    provider="example",
    endpoint="responses",
    billing_mode="list",
    region="us",
    service_tier="standard",
    quantization="hosted-native",
    reasoning_effort="medium",
    agent_harness="synthetic-general-agent@1",
    capabilities=("text", "tools"),
)
OFFERINGS = (ECONOMY, BALANCED, EXPERT)


def _signal_metric(signal: str, unit: str) -> dict[str, str]:
    return {"kind": "signal", "signal": signal, "unit": unit}


def _frontier(
    workload: str,
    quality_metric: str,
    cost_metric: str,
) -> dict[str, object]:
    return {
        "workload": workload,
        "axes": [
            {"metric": quality_metric, "goal": "maximize"},
            {"metric": cost_metric, "goal": "minimize"},
        ],
        "order_by": cost_metric,
        "uncertainty": "point",
    }


def _config() -> ProjectConfig:
    workload_specs = {
        "synthetic-agent-workunit-v1": (
            "agent_workunit",
            "synthetic-primary-harness@1",
            "Synthetic end-to-end agent workload used only by this example.",
        ),
        "synthetic-code-repair-v1": (
            "code_repair_case",
            "synthetic-code-repair-harness@1",
            "Fictional code-repair benchmark component.",
        ),
        "synthetic-reasoning-v1": (
            "reasoning_case",
            "synthetic-reasoning-harness@1",
            "Fictional reasoning benchmark component.",
        ),
        "synthetic-tool-use-v1": (
            "tool_use_case",
            "synthetic-tool-use-harness@1",
            "Fictional tool-use benchmark component.",
        ),
    }
    workloads = {
        workload_id: {
            "unit": unit,
            "version": "1",
            "harness": harness,
            "cohort": "synthetic-three-route-fixture@1",
            "benchmark": workload_id,
            "description": description,
            "assumptions": {
                "fixture_data": "synthetic",
                "independence": "not-established",
            },
        }
        for workload_id, (unit, harness, description) in workload_specs.items()
    }
    metrics = {
        "synthetic_workunits_per_minute": _signal_metric(
            "synthetic_workunits_per_minute", "workunits/minute"
        ),
        "synthetic_total_cost_per_workunit": _signal_metric(
            "synthetic_total_cost_per_workunit", "USD/workunit"
        ),
        "synthetic_code_repair_score": _signal_metric("synthetic_code_repair_score", "ratio"),
        "synthetic_code_repair_run_cost": _signal_metric(
            "synthetic_code_repair_run_cost", "USD/run"
        ),
        "synthetic_reasoning_score": _signal_metric("synthetic_reasoning_score", "ratio"),
        "synthetic_reasoning_run_cost": _signal_metric("synthetic_reasoning_run_cost", "USD/run"),
        "synthetic_tool_use_score": _signal_metric("synthetic_tool_use_score", "ratio"),
        "synthetic_tool_use_run_cost": _signal_metric("synthetic_tool_use_run_cost", "USD/run"),
    }
    frontiers = {
        PRIMARY_FRONTIER_ID: _frontier(
            "synthetic-agent-workunit-v1",
            "synthetic_workunits_per_minute",
            "synthetic_total_cost_per_workunit",
        ),
        "synthetic-code-repair": _frontier(
            "synthetic-code-repair-v1",
            "synthetic_code_repair_score",
            "synthetic_code_repair_run_cost",
        ),
        "synthetic-reasoning": _frontier(
            "synthetic-reasoning-v1",
            "synthetic_reasoning_score",
            "synthetic_reasoning_run_cost",
        ),
        "synthetic-tool-use": _frontier(
            "synthetic-tool-use-v1",
            "synthetic_tool_use_score",
            "synthetic_tool_use_run_cost",
        ),
    }
    return ProjectConfig.model_validate(
        {
            "schema_version": "model-skyline/v1alpha1",
            "workloads": workloads,
            "metrics": metrics,
            "frontiers": frontiers,
            "selections": {
                SELECTION_ID: {
                    "frontier": PRIMARY_FRONTIER_ID,
                    "count": 2,
                    "order_by": "synthetic_total_cost_per_workunit",
                    "snapshot_ttl_seconds": 3600,
                    "on_insufficient": "error",
                }
            },
        }
    )


def _catalog(
    workload_id: str,
    workload_unit: str,
    signals: Mapping[OfferingKey, Mapping[str, tuple[str, str]]],
) -> ObservationCatalog:
    source = {
        "id": f"{workload_id}-fixture",
        "version": "1",
        "license": "CC0-1.0",
        "methodology": (
            "Synthetic values created for the ModelSkyline quality-gate example; "
            "not empirical model claims."
        ),
        "retrieved_at": OBSERVED_AT,
    }
    return ObservationCatalog.model_validate(
        {
            "schema_version": "model-skyline/v1alpha1",
            "workload": {"id": workload_id, "version": "1", "unit": workload_unit},
            "offerings": [
                {
                    "offering": offering.model_dump(mode="json"),
                    "default_source": source,
                    "metadata": {"fixture": "synthetic"},
                    "signals": {
                        signal_id: {
                            "value": value,
                            "unit": unit,
                            "sample_count": 100,
                            "observed_at": OBSERVED_AT,
                        }
                        for signal_id, (value, unit) in offering_signals.items()
                    },
                }
                for offering, offering_signals in signals.items()
            ],
        }
    )


def _catalogs() -> dict[str, ObservationCatalog]:
    # ECONOMY is intentionally absent from synthetic-tool-use-v1. There is no
    # model-name matching: every present record carries the exact same complete
    # OfferingKey used by the primary catalog.
    return {
        PRIMARY_FRONTIER_ID: _catalog(
            "synthetic-agent-workunit-v1",
            "agent_workunit",
            {
                ECONOMY: {
                    "synthetic_workunits_per_minute": ("100", "workunits/minute"),
                    "synthetic_total_cost_per_workunit": ("0.10", "USD/workunit"),
                },
                BALANCED: {
                    "synthetic_workunits_per_minute": ("70", "workunits/minute"),
                    "synthetic_total_cost_per_workunit": ("1.00", "USD/workunit"),
                },
                EXPERT: {
                    "synthetic_workunits_per_minute": ("90", "workunits/minute"),
                    "synthetic_total_cost_per_workunit": ("2.00", "USD/workunit"),
                },
            },
        ),
        "synthetic-code-repair": _catalog(
            "synthetic-code-repair-v1",
            "code_repair_case",
            {
                ECONOMY: {
                    "synthetic_code_repair_score": ("0.99", "ratio"),
                    "synthetic_code_repair_run_cost": ("0.10", "USD/run"),
                },
                BALANCED: {
                    "synthetic_code_repair_score": ("0.85", "ratio"),
                    "synthetic_code_repair_run_cost": ("1.00", "USD/run"),
                },
                EXPERT: {
                    "synthetic_code_repair_score": ("0.90", "ratio"),
                    "synthetic_code_repair_run_cost": ("1.20", "USD/run"),
                },
            },
        ),
        "synthetic-reasoning": _catalog(
            "synthetic-reasoning-v1",
            "reasoning_case",
            {
                ECONOMY: {
                    "synthetic_reasoning_score": ("0.60", "ratio"),
                    "synthetic_reasoning_run_cost": ("0.20", "USD/run"),
                },
                BALANCED: {
                    "synthetic_reasoning_score": ("0.88", "ratio"),
                    "synthetic_reasoning_run_cost": ("1.10", "USD/run"),
                },
                EXPERT: {
                    "synthetic_reasoning_score": ("0.92", "ratio"),
                    "synthetic_reasoning_run_cost": ("1.00", "USD/run"),
                },
            },
        ),
        "synthetic-tool-use": _catalog(
            "synthetic-tool-use-v1",
            "tool_use_case",
            {
                BALANCED: {
                    "synthetic_tool_use_score": ("0.90", "ratio"),
                    "synthetic_tool_use_run_cost": ("0.90", "USD/run"),
                },
                EXPERT: {
                    "synthetic_tool_use_score": ("0.94", "ratio"),
                    "synthetic_tool_use_run_cost": ("0.95", "USD/run"),
                },
            },
        ),
    }


def _component(
    component_id: str,
    frontier: FrontierSnapshot,
    quality_metric: str,
) -> dict[str, object]:
    return {
        "component_id": component_id,
        "frontier_id": frontier.frontier_id,
        "frontier_snapshot_id": frontier.snapshot_id,
        "frontier_snapshot_hash": frontier.snapshot_id,
        "config_hash": frontier.config_hash,
        "catalog_hash": frontier.catalog_hash,
        "workload": frontier.workload.model_dump(mode="json"),
        "axes": [axis.model_dump(mode="json") for axis in frontier.axes],
        "quality_metric": quality_metric,
        "max_age_seconds": 3600,
    }


def main() -> None:
    config = _config()
    catalogs = _catalogs()
    expected_identities = frozenset(OFFERINGS)
    assert (
        frozenset(row.offering for row in catalogs[PRIMARY_FRONTIER_ID].offerings)
        == expected_identities
    )
    assert all(
        frozenset(row.offering for row in catalog.offerings) <= expected_identities
        for catalog in catalogs.values()
    )
    engine = FrontierEngine()
    generated_at = {
        PRIMARY_FRONTIER_ID: BASE_TIME - timedelta(minutes=5),
        "synthetic-code-repair": BASE_TIME - timedelta(minutes=10),
        "synthetic-reasoning": BASE_TIME - timedelta(minutes=9),
        "synthetic-tool-use": BASE_TIME - timedelta(minutes=8),
    }
    frontiers = {
        frontier_id: engine.calculate(
            config,
            catalog,
            frontier_id,
            generated_at=generated_at[frontier_id],
        )
        for frontier_id, catalog in catalogs.items()
    }
    primary = frontiers[PRIMARY_FRONTIER_ID]
    component_frontiers = {
        "code-repair": frontiers["synthetic-code-repair"],
        "reasoning": frontiers["synthetic-reasoning"],
        "tool-use": frontiers["synthetic-tool-use"],
    }
    quality_metrics = {
        "code-repair": "synthetic_code_repair_score",
        "reasoning": "synthetic_reasoning_score",
        "tool-use": "synthetic_tool_use_score",
    }
    quality_policy = QualityBundlePolicy.model_validate(
        {
            "bundle_id": "synthetic-general-agent-quality",
            "version": "1",
            "components": [
                _component(component_id, frontier, quality_metrics[component_id])
                for component_id, frontier in component_frontiers.items()
            ],
            "required_component_ids": list(component_frontiers),
            "minimum_measured_components": 3,
        }
    )
    quality_candidates = tuple(item.offering for item in primary.evaluated)
    quality_bundle = build_quality_bundle_snapshot(
        quality_policy,
        component_frontiers,
        quality_candidates,
        generated_at=BASE_TIME,
    )

    secondary_inputs: dict[str, SecondaryFrontierInput] = {}
    references: list[SecondaryFrontierReference] = []
    for frontier in component_frontiers.values():
        proximity = build_frontier_proximity_snapshot(frontier)
        secondary_inputs[frontier.snapshot_id] = SecondaryFrontierInput(
            frontier=frontier,
            proximity=proximity,
        )
        references.append(
            SecondaryFrontierReference(
                frontier_id=frontier.frontier_id,
                frontier_snapshot_id=frontier.snapshot_id,
                frontier_snapshot_hash=frontier.snapshot_id,
                proximity_snapshot_id=proximity.snapshot_id,
                max_age_seconds=3600,
            )
        )
    overlap_policy = CrossFrontierSelectionPolicy(
        priority_groups=(
            FrontierPriorityGroup(
                name="operator-declared-quality-benchmarks",
                frontiers=tuple(references),
            ),
        )
    )
    selection = build_quality_gated_selection_snapshot(
        config,
        quality_policy,
        quality_bundle,
        primary,
        SELECTION_ID,
        overlap_policy,
        secondary_inputs,
        generated_at=SELECTION_TIME,
    )

    # Replay all exact source inputs. The gated verifier also rebuilds the
    # bundle, every feasible frontier/proximity sidecar, and the final ranking.
    verify_quality_bundle_snapshot(
        quality_policy,
        component_frontiers,
        quality_candidates,
        quality_bundle,
        now=VERIFY_TIME,
    )
    verify_quality_gated_selection_snapshot(
        config,
        quality_policy,
        component_frontiers,
        quality_candidates,
        quality_bundle,
        primary,
        selection,
        SELECTION_ID,
        overlap_policy,
        secondary_inputs,
        now=VERIFY_TIME,
    )

    coverage = {item.offering: item for item in quality_bundle.candidates}
    assert coverage[ECONOMY].missing_component_ids == ("tool-use",)
    assert not coverage[ECONOMY].eligible
    assert coverage[BALANCED].eligible and coverage[EXPERT].eligible
    assert ECONOMY not in {item.offering for item in selection.gated_primary_frontier.evaluated}
    assert selection.default.offering == EXPERT
    assert [choice.offering for choice in selection.choices] == [EXPERT, BALANCED]
    for component_id, frontier in component_frontiers.items():
        quality_metric = quality_metrics[component_id]
        score_by_offering = {
            item.offering: item.axes[quality_metric].value for item in frontier.evaluated
        }
        assert score_by_offering[EXPERT] > score_by_offering[BALANCED]
    assert (
        selection.default.axes["synthetic_total_cost_per_workunit"].value
        > selection.fallbacks[0].axes["synthetic_total_cost_per_workunit"].value
    )

    print("SYNTHETIC DATA ONLY — no values below are empirical model claims.")
    print(
        "The three components are operator-declared distinct workloads; "
        "this is not proof of statistical independence."
    )
    print("\nQuality coverage (exact complete OfferingKey equality):")
    for coverage_record in quality_bundle.candidates:
        statuses = ", ".join(
            f"{component.component_id}={component.status.value}"
            for component in coverage_record.components
        )
        print(
            f"  {coverage_record.offering.offering_id}: {statuses}; "
            f"eligible={str(coverage_record.eligible).lower()}"
        )

    print("\nSource primary cost/performance members:")
    for member in primary.members:
        cost = member.axes["synthetic_total_cost_per_workunit"].value
        performance = member.axes["synthetic_workunits_per_minute"].value
        print(
            f"  {member.offering.offering_id}: "
            f"cost={format(cost, 'f')}, performance={format(performance, 'f')}"
        )

    print("\nQuality-gated overlap ranking:")
    for rank_record in selection.selection.ranked_candidates:
        exact = rank_record.priority_groups[0].exact_memberships
        cost = rank_record.axes["synthetic_total_cost_per_workunit"].value
        print(
            f"  {rank_record.rank}. {rank_record.offering.offering_id}: "
            f"exact quality frontiers={exact}/3, primary cost={format(cost, 'f')}"
        )
    print(
        "\nResult: the missing tool-use measurement excludes the economy route; "
        "three-frontier overlap selects expert over the cheaper balanced route."
    )
    print("Verification: quality bundle and source-backed gated selection both passed.")


if __name__ == "__main__":
    main()
