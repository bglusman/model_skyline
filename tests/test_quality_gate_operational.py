from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from model_skyline.cli import app
from model_skyline.engine import FrontierEngine, dominates, frontier_hash
from model_skyline.io import (
    dump_json,
    generated_schemas,
    load_cross_frontier_selection_policy,
    load_frontier_proximity_snapshot,
    load_quality_gated_selection_snapshot,
)
from model_skyline.models import (
    FrontierSnapshot,
    ObservationCatalog,
    ProjectConfig,
)
from model_skyline.quality_bundle import (
    QualityBundleComponent,
    QualityBundlePolicy,
    build_quality_bundle_snapshot,
)
from model_skyline.quality_selection import (
    QualityGatedSelectionSnapshot,
    build_quality_gated_selection_snapshot,
)
from model_skyline.resolver import DynamicResolver, ResolverError
from model_skyline.selection import select_models
from model_skyline.selection_overlap import (
    CrossFrontierSelectionPolicy,
    FrontierPriorityGroup,
    SecondaryFrontierInput,
    SecondaryFrontierReference,
    build_frontier_proximity_snapshot,
)

NOW = datetime(2026, 8, 31, 20, tzinfo=UTC)
COMPONENT_TIME = NOW - timedelta(minutes=10)
PRIMARY_TIME = NOW - timedelta(minutes=5)
BUNDLE_TIME = NOW - timedelta(minutes=2)
EXCLUDED_ID = "fastcloud/legacy-mid@us-standard"
runner = CliRunner()


def _without_offering(snapshot: FrontierSnapshot, offering_id: str) -> FrontierSnapshot:
    remaining = tuple(
        item for item in snapshot.evaluated if item.offering.offering_id != offering_id
    )
    evaluated = tuple(
        item.model_copy(
            update={
                "dominated_by": tuple(
                    sorted(
                        candidate.offering.offering_id
                        for candidate in remaining
                        if candidate is not item
                        and dominates(
                            candidate,
                            item,
                            snapshot.axes,
                            snapshot.uncertainty,
                        )
                    )
                )
            }
        )
        for item in remaining
    )
    provisional = snapshot.model_copy(
        update={
            "snapshot_id": "0" * 64,
            "evaluated": evaluated,
            "members": tuple(item for item in evaluated if not item.dominated_by),
        }
    )
    hashed = provisional.model_copy(update={"snapshot_id": frontier_hash(provisional)})
    return FrontierSnapshot.model_validate(hashed.model_dump(mode="json"))


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
        quality_metric="coding_session_success",
        max_age_seconds=3600,
    )


def _operational_artifacts(
    config: ProjectConfig,
    catalog: ObservationCatalog,
) -> SimpleNamespace:
    primary = FrontierEngine().calculate(
        config,
        catalog,
        "coding-value",
        generated_at=PRIMARY_TIME,
    )
    first = FrontierEngine().calculate(
        config,
        catalog,
        "coding-responsiveness",
        generated_at=COMPONENT_TIME,
    )
    clone = _without_offering(first, EXCLUDED_ID).model_copy(
        update={
            "snapshot_id": "0" * 64,
            "frontier_id": "coding-quality-independent",
        }
    )
    second = FrontierSnapshot.model_validate(
        clone.model_copy(update={"snapshot_id": frontier_hash(clone)}).model_dump(mode="json")
    )
    component_frontiers = {"benchmark-a": first, "benchmark-b": second}
    quality_policy = QualityBundlePolicy(
        bundle_id="coding-quality-bundle",
        version="1",
        components=tuple(
            _component(component_id, frontier)
            for component_id, frontier in component_frontiers.items()
        ),
        required_component_ids=("benchmark-a", "benchmark-b"),
        minimum_measured_components=2,
    )
    bundle = build_quality_bundle_snapshot(
        quality_policy,
        component_frontiers,
        (item.offering for item in primary.evaluated),
        generated_at=BUNDLE_TIME,
    )

    inputs: dict[str, SecondaryFrontierInput] = {}
    references: list[SecondaryFrontierReference] = []
    for frontier in component_frontiers.values():
        proximity = build_frontier_proximity_snapshot(frontier)
        inputs[frontier.snapshot_id] = SecondaryFrontierInput(
            frontier=frontier,
            proximity=proximity,
        )
        references.append(
            SecondaryFrontierReference(
                frontier_id=frontier.frontier_id,
                frontier_snapshot_id=frontier.snapshot_id,
                frontier_snapshot_hash=frontier.snapshot_id,
                proximity_snapshot_id=proximity.snapshot_id,
                near_epsilon="0.1",
                max_age_seconds=3600,
            )
        )
    overlap_policy = CrossFrontierSelectionPolicy(
        priority_groups=(FrontierPriorityGroup(name="quality", frontiers=tuple(references)),)
    )
    selection = build_quality_gated_selection_snapshot(
        config,
        quality_policy,
        bundle,
        primary,
        "coding-agent-defaults",
        overlap_policy,
        inputs,
        generated_at=NOW,
    )
    return SimpleNamespace(
        primary=primary,
        component_frontiers=component_frontiers,
        quality_policy=quality_policy,
        bundle=bundle,
        overlap_policy=overlap_policy,
        inputs=inputs,
        selection=selection,
    )


def _rebuilt_quality_selection(
    config: ProjectConfig,
    artifacts: SimpleNamespace,
    *,
    version: str,
    bundle_generated_at: datetime,
    selection_generated_at: datetime,
) -> SimpleNamespace:
    policy = QualityBundlePolicy.model_validate(
        artifacts.quality_policy.model_copy(update={"version": version}).model_dump(mode="json")
    )
    bundle = build_quality_bundle_snapshot(
        policy,
        artifacts.component_frontiers,
        (item.offering for item in artifacts.primary.evaluated),
        generated_at=bundle_generated_at,
    )
    selection = build_quality_gated_selection_snapshot(
        config,
        policy,
        bundle,
        artifacts.primary,
        "coding-agent-defaults",
        artifacts.overlap_policy,
        artifacts.inputs,
        generated_at=selection_generated_at,
    )
    return SimpleNamespace(policy=policy, bundle=bundle, selection=selection)


def test_resolver_accepts_and_pins_quality_gated_selection(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    artifacts = _operational_artifacts(example_config, example_catalog)
    resolver = DynamicResolver(
        "memory://quality-selection",
        expected_selection_id="coding-agent-defaults",
        expected_frontier_id="coding-value",
        expected_workload_id="coding-session-v1",
        expected_workload_version="1.0.0",
        expected_quality_bundle_id="coding-quality-bundle",
        expected_quality_bundle_version="1",
        expected_quality_bundle_policy_hash=artifacts.bundle.policy_hash,
        loader=lambda *_args: (artifacts.selection.model_dump(mode="json"), None),
        clock=lambda: NOW + timedelta(minutes=1),
    )

    resolved = resolver.resolve()

    assert isinstance(resolved, QualityGatedSelectionSnapshot)
    assert resolved.default == resolved.selection.default
    assert resolved.quality_bundle_id == "coding-quality-bundle"


def test_resolver_quality_bundle_pin_rejects_wrong_bundle_and_plain_selection(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    artifacts = _operational_artifacts(example_config, example_catalog)
    wrong_pin = DynamicResolver(
        "memory://quality-selection",
        expected_selection_id="coding-agent-defaults",
        expected_quality_bundle_id="other-bundle",
        loader=lambda *_args: (artifacts.selection.model_dump(mode="json"), None),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    with pytest.raises(ResolverError, match="quality bundle identity mismatch") as error:
        wrong_pin.resolve()
    assert "other-bundle" not in str(error.value)

    unpinned = DynamicResolver(
        "memory://quality-selection",
        expected_selection_id="coding-agent-defaults",
        loader=lambda *_args: (artifacts.selection.model_dump(mode="json"), None),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    with pytest.raises(ResolverError, match="requires expected_quality_bundle_id"):
        unpinned.resolve()

    for pin, expected_message in (
        ({"expected_quality_bundle_version": "other-version"}, "version"),
        ({"expected_quality_bundle_policy_hash": "0" * 64}, "policy hash"),
    ):
        mismatched = DynamicResolver(
            "memory://quality-selection",
            expected_selection_id="coding-agent-defaults",
            expected_quality_bundle_id="coding-quality-bundle",
            loader=lambda *_args: (artifacts.selection.model_dump(mode="json"), None),
            clock=lambda: NOW + timedelta(minutes=1),
            **pin,
        )
        with pytest.raises(ResolverError, match=expected_message):
            mismatched.resolve()

    with pytest.raises(ValueError, match="require expected_quality_bundle_id"):
        DynamicResolver(
            "memory://quality-selection",
            expected_selection_id="coding-agent-defaults",
            expected_quality_bundle_version="1",
            loader=lambda *_args: (artifacts.selection.model_dump(mode="json"), None),
        )

    plain = select_models(example_config, artifacts.primary, "coding-agent-defaults")
    requires_gate = DynamicResolver(
        "memory://plain-selection",
        expected_selection_id="coding-agent-defaults",
        expected_quality_bundle_id="coding-quality-bundle",
        loader=lambda *_args: (plain.model_dump(mode="json"), None),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    with pytest.raises(ResolverError, match="not quality-gated"):
        requires_gate.resolve()


def test_resolver_rejects_quality_wrapper_and_bundle_rollback_or_equivocation(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    artifacts = _operational_artifacts(example_config, example_catalog)
    wrapper_rollback = _rebuilt_quality_selection(
        example_config,
        artifacts,
        version="1",
        bundle_generated_at=BUNDLE_TIME,
        selection_generated_at=NOW - timedelta(minutes=1),
    ).selection
    wrapper_equivocation = _rebuilt_quality_selection(
        example_config,
        artifacts,
        version="2",
        bundle_generated_at=BUNDLE_TIME,
        selection_generated_at=NOW,
    ).selection
    bundle_rollback = _rebuilt_quality_selection(
        example_config,
        artifacts,
        version="1",
        bundle_generated_at=BUNDLE_TIME - timedelta(minutes=1),
        selection_generated_at=NOW + timedelta(minutes=1),
    ).selection
    bundle_equivocation = _rebuilt_quality_selection(
        example_config,
        artifacts,
        version="2",
        bundle_generated_at=BUNDLE_TIME,
        selection_generated_at=NOW + timedelta(minutes=1),
    ).selection

    for candidate, expected_message in (
        (wrapper_rollback, "selection would roll back"),
        (wrapper_equivocation, "selection would equivocate"),
        (bundle_rollback, "quality bundle would roll back"),
        (bundle_equivocation, "quality bundle would equivocate"),
    ):
        payloads = iter((artifacts.selection, candidate))
        resolver = DynamicResolver(
            "memory://quality-selection",
            expected_selection_id="coding-agent-defaults",
            expected_quality_bundle_id="coding-quality-bundle",
            refresh_interval=timedelta(0),
            loader=lambda *_args, stream=payloads: (
                next(stream).model_dump(mode="json"),
                None,
            ),
            clock=lambda: NOW + timedelta(minutes=2),
        )
        assert resolver.resolve().snapshot_id == artifacts.selection.snapshot_id
        with pytest.raises(ResolverError, match=expected_message):
            resolver.resolve(force_refresh=True)


def test_resolver_never_extends_quality_evidence_deadline_with_stale_if_error(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    artifacts = _operational_artifacts(example_config, example_catalog)
    calls = 0
    clock = [NOW + timedelta(minutes=1)]

    def loader(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return artifacts.selection.model_dump(mode="json"), None
        raise OSError("temporary outage")

    resolver = DynamicResolver(
        "memory://quality-selection",
        expected_selection_id="coding-agent-defaults",
        expected_quality_bundle_id="coding-quality-bundle",
        refresh_interval=timedelta(0),
        stale_if_error=timedelta(days=1),
        loader=loader,
        clock=lambda: clock[0],
    )
    assert resolver.resolve().snapshot_id == artifacts.selection.snapshot_id

    clock[0] = artifacts.selection.valid_until - timedelta(seconds=1)
    assert resolver.resolve(force_refresh=True).snapshot_id == artifacts.selection.snapshot_id

    clock[0] = artifacts.selection.valid_until
    with pytest.raises(ResolverError, match="refresh"):
        resolver.resolve(force_refresh=True)


def test_quality_operational_loaders_and_schemas(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    artifacts = _operational_artifacts(example_config, example_catalog)
    policy_path = tmp_path / "overlap-policy.json"
    proximity_path = tmp_path / "proximity.json"
    selection_path = tmp_path / "quality-selection.json"
    first_input = next(iter(artifacts.inputs.values()))
    policy_path.write_text(dump_json(artifacts.overlap_policy), encoding="utf-8")
    proximity_path.write_text(dump_json(first_input.proximity), encoding="utf-8")
    selection_path.write_text(dump_json(artifacts.selection), encoding="utf-8")

    assert load_cross_frontier_selection_policy(policy_path) == artifacts.overlap_policy
    assert load_frontier_proximity_snapshot(proximity_path) == first_input.proximity
    assert load_quality_gated_selection_snapshot(selection_path) == artifacts.selection

    schemas = generated_schemas()
    for name, value in (
        (
            "cross-frontier-selection-policy.schema.json",
            artifacts.overlap_policy,
        ),
        (
            "quality-gated-selection-snapshot.schema.json",
            artifacts.selection,
        ),
    ):
        schema = schemas[name]
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value.model_dump(mode="json"))


def test_cli_builds_bundle_sidecars_and_quality_gated_selection(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    artifacts = _operational_artifacts(example_config, example_catalog)
    config_path = Path(__file__).parents[1] / "examples" / "coding-session" / "frontier.yaml"
    primary_path = tmp_path / "primary.json"
    quality_policy_path = tmp_path / "quality-policy.json"
    overlap_policy_path = tmp_path / "overlap-policy.json"
    bundle_path = tmp_path / "bundle.json"
    selection_path = tmp_path / "selection.json"
    primary_path.write_text(dump_json(artifacts.primary), encoding="utf-8")
    quality_policy_path.write_text(dump_json(artifacts.quality_policy), encoding="utf-8")
    overlap_policy_path.write_text(dump_json(artifacts.overlap_policy), encoding="utf-8")

    component_paths: dict[str, Path] = {}
    proximity_paths: list[Path] = []
    for component_id, frontier in artifacts.component_frontiers.items():
        frontier_path = tmp_path / f"{component_id}.json"
        proximity_path = tmp_path / f"{component_id}-proximity.json"
        frontier_path.write_text(dump_json(frontier), encoding="utf-8")
        component_paths[component_id] = frontier_path
        proximity_result = runner.invoke(
            app,
            [
                "build-frontier-proximity",
                str(frontier_path),
                "--output",
                str(proximity_path),
            ],
        )
        assert proximity_result.exit_code == 0, proximity_result.output
        proximity_paths.append(proximity_path)

    bundle_arguments = [
        "build-quality-bundle",
        str(quality_policy_path),
        "--candidate-frontier",
        str(primary_path),
        "--as-of",
        BUNDLE_TIME.isoformat(),
        "--output",
        str(bundle_path),
    ]
    for component_id, path in component_paths.items():
        bundle_arguments.extend(("--component-frontier", f"{component_id}={path}"))
    bundle_result = runner.invoke(app, bundle_arguments)
    assert bundle_result.exit_code == 0, bundle_result.output
    bundle_payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    excluded = next(
        item
        for item in bundle_payload["candidates"]
        if item["offering"]["offering_id"] == EXCLUDED_ID
    )
    assert excluded["missing_component_ids"] == ["benchmark-b"]
    assert excluded["eligible"] is False

    selection_arguments = [
        "select-quality-gated",
        str(config_path),
        str(primary_path),
        str(quality_policy_path),
        str(bundle_path),
        str(overlap_policy_path),
        "coding-agent-defaults",
        "--as-of",
        NOW.isoformat(),
        "--output",
        str(selection_path),
    ]
    for path in component_paths.values():
        selection_arguments.extend(("--secondary-frontier", str(path)))
    for path in proximity_paths:
        selection_arguments.extend(("--proximity", str(path)))
    selection_result = runner.invoke(app, selection_arguments)
    assert selection_result.exit_code == 0, selection_result.output
    payload = json.loads(selection_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "quality-gated-selection"
    assert payload["quality_bundle_id"] == "coding-quality-bundle"
    assert payload["quality_bundle_snapshot_id"] == bundle_payload["snapshot_id"]

    verify_arguments = [
        "verify-quality-gated-selection",
        str(config_path),
        str(quality_policy_path),
        str(bundle_path),
        str(primary_path),
        str(selection_path),
        str(overlap_policy_path),
        "coding-agent-defaults",
        "--as-of",
        (NOW + timedelta(seconds=1)).isoformat(),
    ]
    for component_id, path in component_paths.items():
        verify_arguments.extend(("--component-frontier", f"{component_id}={path}"))
        verify_arguments.extend(("--secondary-frontier", str(path)))
    for path in proximity_paths:
        verify_arguments.extend(("--proximity", str(path)))
    verify_result = runner.invoke(app, verify_arguments)
    assert verify_result.exit_code == 0, verify_result.output
    assert f"valid quality-gated selection {payload['snapshot_id']}" in verify_result.output
