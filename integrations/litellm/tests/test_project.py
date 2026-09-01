from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import GENERATED_AT, choice, make_selection, offering_a
from model_skyline.models import SelectionSnapshot
from model_skyline.selection import selection_hash

from model_skyline_litellm.models import IntegrationConfig
from model_skyline_litellm.project import ProjectionError, project_selection


def test_projects_exact_order_to_deterministic_private_rows(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    first = project_selection(selection, config, now=GENERATED_AT + timedelta(minutes=1))
    second = project_selection(selection, config, now=GENERATED_AT + timedelta(minutes=1))

    assert first == second
    assert [item.rank for item in first.deployments] == [1, 2]
    assert first.group_name.endswith(f"{selection.snapshot_id}-{first.projection_id}")
    assert first.group_name.startswith(first.owner_prefix)
    assert first.deployments[0].create_payload()["litellm_params"] == {
        "model": "openai/fake-a",
        "litellm_credential_name": "fake-a-v1",
        "order": 1,
    }
    assert first.deployments[1].create_payload()["litellm_params"]["order"] == 2

    dry_run = json.dumps(first.public_summary(), sort_keys=True)
    for forbidden in (
        "openai/fake-a",
        "fake-a-v1",
        "private_projection_input",
        "quality",
        "responses",
    ):
        assert forbidden not in dry_run


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("model_id", "model-a-next"),
        ("provider", "provider-z"),
        ("endpoint", "chat-completions"),
        ("billing_mode", "direct"),
        ("region", "eu"),
        ("service_tier", "priority"),
        ("quantization", "bf16"),
        ("reasoning_effort", "high"),
        ("agent_harness", "coding-agent-v2"),
        ("capabilities", ("tools",)),
    ],
)
def test_every_complete_offering_field_participates_in_binding(
    field: str,
    replacement: object,
    config: IntegrationConfig,
) -> None:
    mutated = offering_a(**{field: replacement})
    snapshot = make_selection(choice(mutated, "0.9"))

    with pytest.raises(ProjectionError, match="exact local binding"):
        project_selection(snapshot, config, now=GENERATED_AT + timedelta(minutes=1))


def test_rejects_hash_identity_time_and_capacity_failures(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    tampered = selection.model_copy(update={"snapshot_id": "f" * 64})
    with pytest.raises(ProjectionError, match="hash"):
        project_selection(tampered, config, now=GENERATED_AT)

    wrong_identity = config.model_copy(update={"expected_frontier_id": "other"})
    with pytest.raises(ProjectionError, match="frontier identity"):
        project_selection(selection, wrong_identity, now=GENERATED_AT)

    with pytest.raises(ProjectionError, match="expired"):
        project_selection(selection, config, now=selection.valid_until + timedelta(microseconds=1))

    future = make_selection(
        selection.default,
        generated_at=GENERATED_AT + timedelta(minutes=6),
    )
    with pytest.raises(ProjectionError, match="future"):
        project_selection(future, config, now=GENERATED_AT)

    cap = config.model_copy(update={"max_candidates": 1})
    with pytest.raises(ProjectionError, match="candidate cap"):
        project_selection(selection, cap, now=GENERATED_AT)


def test_rehashing_does_not_make_wrong_selection_identity_acceptable(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    changed = selection.model_copy(update={"selection_id": "other", "snapshot_id": "0" * 64})
    changed = changed.model_copy(update={"snapshot_id": selection_hash(changed)})

    with pytest.raises(ProjectionError, match="selection identity"):
        project_selection(changed, config, now=GENERATED_AT)


def test_payload_contains_only_local_route_and_content_free_evidence(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    plan = project_selection(selection, config, now=GENERATED_AT)
    payload = plan.deployments[0].create_payload()
    serialized = json.dumps(payload, sort_keys=True)

    assert set(payload) == {"model_name", "litellm_params", "model_info"}
    assert "private_projection_input" not in serialized
    assert "quality" not in serialized
    assert "source" not in serialized
    assert selection.default.offering.offering_id not in serialized
    assert payload["model_info"]["model_skyline_rank"] == 1


def test_rotated_local_target_gets_a_new_immutable_projection(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    first = project_selection(selection, config, now=GENERATED_AT)
    targets = dict(config.targets)
    targets["target-a"] = targets["target-a"].model_copy(update={"revision": "c" * 64})
    rotated = config.model_copy(update={"targets": targets})

    second = project_selection(selection, rotated, now=GENERATED_AT)

    assert second.snapshot_id == first.snapshot_id
    assert second.projection_id != first.projection_id
    assert second.group_name != first.group_name
    assert second.deployments[0].deployment_id != first.deployments[0].deployment_id


def test_generated_model_skyline_snapshots_project_without_reinterpreting_order() -> None:
    fixtures = Path(__file__).parent / "fixtures"
    config = IntegrationConfig.model_validate_json(
        (fixtures / "bindings.json").read_text(encoding="utf-8")
    )
    selection_a = SelectionSnapshot.model_validate_json(
        (fixtures / "selection-a.json").read_text(encoding="utf-8")
    )
    selection_b = SelectionSnapshot.model_validate_json(
        (fixtures / "selection-b.json").read_text(encoding="utf-8")
    )

    plan_a = project_selection(selection_a, config, now=selection_a.generated_at)
    plan_b = project_selection(selection_b, config, now=selection_b.generated_at)

    assert [item.target.model for item in plan_a.deployments] == [
        "openai/fake-a",
        "openai/fake-b",
    ]
    assert [item.target.model for item in plan_b.deployments] == [
        "openai/fake-b",
        "openai/fake-a",
    ]
