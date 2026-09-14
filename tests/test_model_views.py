from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from model_skyline.catalog_composition import compose_catalogs
from model_skyline.cli import app
from model_skyline.engine import FrontierEngine
from model_skyline.io import (
    dump_json,
    load_catalog,
    load_config,
    load_model_frontier_view_policy,
    load_model_frontier_view_snapshot,
)
from model_skyline.model_views import (
    BalancedModelSelection,
    ModelEnvironmentOffering,
    ModelFrontierViewError,
    ModelFrontierViewPolicy,
    ModelViewEnvironment,
    build_model_frontier_view,
    model_frontier_view_hash,
)
from model_skyline.models import (
    FrontierAxis,
    FrontierDefinition,
    Goal,
    MetricDefinition,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    ProjectConfig,
    SignalMetric,
    WorkloadProfile,
    WorkloadReference,
)

NOW = datetime(2026, 9, 14, 15, 30, tzinfo=UTC)
ROOT = Path(__file__).parents[1]
LOCAL = ROOT / "examples" / "local-runtime-frontiers"


def _offering(model: str, provider: str, quality: str, cost: str) -> OfferingObservation:
    return OfferingObservation(
        offering=OfferingKey(
            offering_id=f"{provider}/{model}",
            model_id=model,
            provider=provider,
        ),
        signals={
            "quality": Observation(value=Decimal(quality), unit="percent", observed_at=NOW),
            "cost": Observation(value=Decimal(cost), unit="USD", observed_at=NOW),
        },
    )


def _snapshot():
    workload = WorkloadReference(id="work", version="1", unit="task")
    catalog = ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=workload,
        offerings=[
            _offering("A", "p1", "90", "10"),
            _offering("A", "p2", "50", "10"),
            _offering("B", "p1", "80", "20"),
            _offering("B", "p2", "80", "1"),
            _offering("C", "p1", "100", "9"),
            _offering("C", "p2", "0", "100"),
        ],
    )
    metrics: dict[str, MetricDefinition] = {
        "quality": SignalMetric(kind="signal", signal="quality", unit="percent"),
        "cost": SignalMetric(kind="signal", signal="cost", unit="USD"),
    }
    config = ProjectConfig(
        schema_version="model-skyline/v1alpha1",
        workloads={
            "work": WorkloadProfile(
                unit="task", version="1", harness="synthetic@1", cohort="paired-providers"
            )
        },
        metrics=metrics,
        frontiers={
            "value": FrontierDefinition(
                workload="work",
                axes=[
                    FrontierAxis(metric="quality", goal=Goal.MAXIMIZE),
                    FrontierAxis(metric="cost", goal=Goal.MINIMIZE),
                ],
                order_by="quality",
            )
        },
    )
    return FrontierEngine().calculate(config, catalog, "value", generated_at=NOW)


def _policy(snapshot_id: str) -> ModelFrontierViewPolicy:
    return ModelFrontierViewPolicy(
        schema_version="model-skyline/model-frontier-view-policy/v1alpha1",
        policy_id="two-provider-example",
        source_snapshot_id=snapshot_id,
        environments=(
            ModelViewEnvironment(environment_id="provider-1", provider="p1"),
            ModelViewEnvironment(environment_id="provider-2", provider="p2"),
        ),
        models=tuple(
            BalancedModelSelection(
                model_id=model,
                offerings=(
                    ModelEnvironmentOffering(
                        environment_id="provider-1", offering_id=f"p1/{model}"
                    ),
                    ModelEnvironmentOffering(
                        environment_id="provider-2", offering_id=f"p2/{model}"
                    ),
                ),
            )
            for model in ("A", "B", "C")
        ),
    )


def test_best_available_and_balanced_average_are_distinct_honest_views() -> None:
    snapshot = _snapshot()
    view = build_model_frontier_view(_policy(snapshot.snapshot_id), snapshot)

    assert [item.model_id for item in view.best_available.members] == ["C", "B"]
    assert [item.offerings[0].offering.offering_id for item in view.best_available.members] == [
        "p1/C",
        "p2/B",
    ]

    assert [item.model_id for item in view.balanced_average.members] == ["B", "A"]
    points = {item.model_id: item for item in view.balanced_average.evaluated}
    assert points["A"].axes["quality"].value == Decimal("70")
    assert points["A"].axes["cost"].value == Decimal("10")
    assert points["B"].axes["quality"].environment_values == {
        "provider-1": Decimal("80"),
        "provider-2": Decimal("80"),
    }
    assert points["B"].axes["cost"].value == Decimal("10.5")
    assert points["C"].dominated_by == ("A", "B")
    assert model_frontier_view_hash(view) == view.view_id


def test_policy_requires_the_same_environment_panel_for_every_model() -> None:
    snapshot = _snapshot()
    payload = _policy(snapshot.snapshot_id).model_dump(mode="json")
    payload["models"][0]["offerings"].pop()

    with pytest.raises(ValidationError, match="does not match the environment panel"):
        ModelFrontierViewPolicy.model_validate(payload)


def test_balanced_average_rejects_missing_or_wrong_provider_offerings() -> None:
    snapshot = _snapshot()
    missing_payload = _policy(snapshot.snapshot_id).model_dump(mode="json")
    missing_payload["models"][0]["offerings"][0]["offering_id"] = "p1/missing"

    with pytest.raises(ModelFrontierViewError, match="not eligible and evaluated"):
        build_model_frontier_view(ModelFrontierViewPolicy.model_validate(missing_payload), snapshot)

    provider_payload = _policy(snapshot.snapshot_id).model_dump(mode="json")
    provider_payload["models"][0]["offerings"][0]["offering_id"] = "p2/A"
    provider_payload["models"][0]["offerings"][1]["offering_id"] = "p1/A"

    with pytest.raises(ModelFrontierViewError, match="provider does not match"):
        build_model_frontier_view(
            ModelFrontierViewPolicy.model_validate(provider_payload), snapshot
        )


def test_cli_writes_a_replayable_model_frontier_view(tmp_path) -> None:
    snapshot = _snapshot()
    policy = _policy(snapshot.snapshot_id)
    policy_path = tmp_path / "policy.json"
    snapshot_path = tmp_path / "frontier.json"
    output_path = tmp_path / "model-view.json"
    policy_path.write_text(policy.model_dump_json(indent=2) + "\n", encoding="utf-8")
    snapshot_path.write_text(dump_json(snapshot), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "model-frontier-view",
            str(policy_path),
            str(snapshot_path),
            "--format",
            "json",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    loaded = load_model_frontier_view_snapshot(output_path)
    assert loaded.view_id == model_frontier_view_hash(loaded)
    assert [item.model_id for item in loaded.balanced_average.members] == ["B", "A"]

    table = CliRunner().invoke(
        app,
        ["model-frontier-view", str(policy_path), str(snapshot_path)],
    )
    assert table.exit_code == 0, table.output
    assert "Best available (one real tested implementation)" in table.output
    assert "Balanced average (same environments, equal weight)" in table.output
    assert "p1" in table.output


def test_published_two_mac_model_view_rebuilds_from_exact_catalogs() -> None:
    generated = LOCAL / "generated"
    catalog = compose_catalogs(
        (
            load_catalog(generated / "qwen38-exact-cross-mac-short-throughput-catalog.json"),
            load_catalog(generated / "short-throughput-catalog.json"),
        )
    )
    assert dump_json(catalog) == (
        generated / "cross-mac-two-model-short-throughput-catalog.json"
    ).read_text(encoding="utf-8")

    snapshot = FrontierEngine().calculate(
        load_config(LOCAL / "frontiers.yaml"),
        catalog,
        "short-throughput-envelope",
        generated_at=NOW,
    )
    assert dump_json(snapshot) == (
        generated / "cross-mac-two-model-short-throughput-frontier.json"
    ).read_text(encoding="utf-8")

    policy = load_model_frontier_view_policy(
        LOCAL / "cross-mac-short-throughput-model-view-policy.json"
    )
    view = build_model_frontier_view(policy, snapshot)
    expected = load_model_frontier_view_snapshot(
        generated / "cross-mac-two-model-short-throughput-model-view.json"
    )
    assert dump_json(view) == dump_json(expected)
    assert [item.model_id for item in view.best_available.members] == [
        "ornith-ai/Ornith-1.5-35B-A3B"
    ]
    assert [item.model_id for item in view.balanced_average.members] == [
        "ornith-ai/Ornith-1.5-35B-A3B"
    ]
