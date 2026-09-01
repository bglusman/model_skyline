from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import pytest
from conftest import GENERATED_AT, choice, make_selection, offering_a, offering_b
from model_skyline.models import SelectionSnapshot

from model_skyline_litellm.api import AdminAPIError, AliasValue
from model_skyline_litellm.models import IntegrationConfig, PlannedDeployment, ProjectionPlan
from model_skyline_litellm.project import project_selection
from model_skyline_litellm.reconcile import (
    IndeterminateActivationError,
    ReconcileError,
    activate,
    stage,
    verify_staged,
)


def _row(deployment: PlannedDeployment) -> dict[str, Any]:
    payload = deployment.create_payload()
    payload["model_info"] = {**payload["model_info"], "db_model": True}
    return payload


class FakeAPI:
    def __init__(self) -> None:
        self.rows: list[Mapping[str, Any]] = []
        self.aliases: dict[str, AliasValue] = {}
        self.router_extras: dict[str, Any] = {}
        self.create_calls: list[Mapping[str, Any]] = []
        self.alias_calls: list[dict[str, AliasValue]] = []
        self.fail_create_at: int | None = None
        self.commit_failed_create = False
        self.fail_alias = False
        self.commit_failed_alias = False

    def list_models(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.rows)

    def create_model(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.create_calls.append(payload)
        if self.fail_create_at == len(self.create_calls):
            if self.commit_failed_create:
                self.rows.append(dict(payload))
            raise AdminAPIError("content-free test failure")
        self.rows.append(dict(payload))
        return {"ok": True}

    def get_runtime_config(self) -> Mapping[str, Any]:
        return {
            "router_settings": {
                **self.router_extras,
                "model_group_alias": dict(self.aliases),
            }
        }

    def update_aliases(self, aliases: Mapping[str, AliasValue]) -> Mapping[str, Any]:
        intended = dict(aliases)
        self.alias_calls.append(intended)
        if self.fail_alias:
            if self.commit_failed_alias:
                self.aliases = intended
            raise AdminAPIError("content-free test failure")
        self.aliases = intended
        return {"ok": True}


def _plan(selection: SelectionSnapshot, config: IntegrationConfig) -> ProjectionPlan:
    return project_selection(selection, config, now=selection.generated_at)


def test_stage_creates_in_order_verifies_and_is_idempotent(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    plan = _plan(selection, config)
    api = FakeAPI()

    stage(plan, api, now=GENERATED_AT)
    verify_staged(plan, api)
    stage(plan, api, now=GENERATED_AT)

    assert len(api.create_calls) == 2
    assert [call["litellm_params"]["order"] for call in api.create_calls] == [1, 2]
    assert api.alias_calls == []


def test_stage_retains_distinct_local_target_revisions(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    first = _plan(selection, config)
    targets = dict(config.targets)
    targets["target-a"] = targets["target-a"].model_copy(update={"revision": "c" * 64})
    second = _plan(selection, config.model_copy(update={"targets": targets}))
    api = FakeAPI()

    stage(first, api, now=GENERATED_AT)
    stage(second, api, now=GENERATED_AT)

    assert first.group_name != second.group_name
    assert len(api.rows) == 4
    verify_staged(first, api)
    verify_staged(second, api)


def test_stage_recovers_commit_then_failure_by_deterministic_readback(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    plan = _plan(selection, config)
    api = FakeAPI()
    api.fail_create_at = 1
    api.commit_failed_create = True

    stage(plan, api, now=GENERATED_AT)

    assert len(api.rows) == 2
    verify_staged(plan, api)


def test_partial_stage_failure_never_changes_alias(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    plan = _plan(selection, config)
    api = FakeAPI()
    api.fail_create_at = 2

    with pytest.raises(ReconcileError, match="readback"):
        stage(plan, api, now=GENERATED_AT)

    assert len(api.rows) == 1
    assert api.aliases == {}
    assert api.alias_calls == []


def test_stage_rejects_conflicting_id_and_unexpected_group_member(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    plan = _plan(selection, config)
    api = FakeAPI()
    conflict = _row(plan.deployments[0])
    conflict["model_name"] = "someone-elses-group"
    api.rows.append(conflict)
    with pytest.raises(ReconcileError, match="another group"):
        stage(plan, api, now=GENERATED_AT)

    api = FakeAPI()
    unexpected = _row(plan.deployments[0])
    unexpected["model_info"] = {**unexpected["model_info"], "id": "unexpected"}
    api.rows.append(unexpected)
    with pytest.raises(ReconcileError, match="unexpected"):
        stage(plan, api, now=GENERATED_AT)


def test_activate_preserves_complete_alias_map_and_is_idempotent(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    plan = _plan(selection, config)
    api = FakeAPI()
    api.rows.extend(_row(item) for item in plan.deployments)
    api.aliases = {
        "unrelated": "unrelated-group",
        "hidden": {"model": "hidden-group", "hidden": True},
    }

    activate(plan, api, now=GENERATED_AT)
    activate(plan, api, now=GENERATED_AT)

    assert api.aliases == {
        "unrelated": "unrelated-group",
        "hidden": {"model": "hidden-group", "hidden": True},
        plan.stable_alias: plan.group_name,
    }
    assert api.alias_calls == [api.aliases]


def test_activate_rejects_global_fallbacks_and_unmanaged_takeover(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    plan = _plan(selection, config)
    api = FakeAPI()
    api.rows.extend(_row(item) for item in plan.deployments)
    api.router_extras["fallbacks"] = [{"other": ["wider"]}]
    with pytest.raises(ReconcileError, match="widen"):
        activate(plan, api, now=GENERATED_AT)

    api.router_extras.clear()
    api.aliases[plan.stable_alias] = "unmanaged"
    with pytest.raises(ReconcileError, match="unmanaged"):
        activate(plan, api, now=GENERATED_AT)

    api.aliases[plan.stable_alias] = {"model": plan.group_name, "hidden": True}
    with pytest.raises(ReconcileError, match="alias object"):
        activate(plan, api, now=GENERATED_AT)


def test_activate_recovers_timeout_after_commit_but_not_unknown_state(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    plan = _plan(selection, config)
    api = FakeAPI()
    api.rows.extend(_row(item) for item in plan.deployments)
    api.fail_alias = True
    api.commit_failed_alias = True

    activate(plan, api, now=GENERATED_AT)
    assert api.aliases[plan.stable_alias] == plan.group_name

    class UnknownAPI(FakeAPI):
        reads = 0

        def get_runtime_config(self) -> Mapping[str, Any]:
            self.reads += 1
            if self.reads >= 3:
                raise AdminAPIError("unavailable")
            return super().get_runtime_config()

    unknown = UnknownAPI()
    unknown.rows.extend(_row(item) for item in plan.deployments)
    unknown.fail_alias = True
    with pytest.raises(IndeterminateActivationError, match="unavailable"):
        activate(plan, unknown, now=GENERATED_AT)


def test_activate_rejects_rollback_and_same_time_equivocation(
    selection: SelectionSnapshot,
    config: IntegrationConfig,
) -> None:
    plan = _plan(selection, config)
    api = FakeAPI()
    api.rows.extend(_row(item) for item in plan.deployments)

    newer_selection = make_selection(
        choice(offering_b(), "0.95"),
        choice(offering_a(), "0.90"),
        generated_at=GENERATED_AT + timedelta(minutes=10),
    )
    newer_plan = project_selection(
        newer_selection,
        config,
        now=newer_selection.generated_at,
    )
    api.rows.extend(_row(item) for item in newer_plan.deployments)
    api.aliases[plan.stable_alias] = newer_plan.group_name

    with pytest.raises(ReconcileError, match="roll back"):
        activate(plan, api, now=GENERATED_AT)

    equivocation = make_selection(
        choice(offering_b(), "0.95"),
        choice(offering_a(), "0.90"),
        generated_at=GENERATED_AT,
    )
    equivocation_plan = project_selection(equivocation, config, now=GENERATED_AT)
    api = FakeAPI()
    api.rows.extend(_row(item) for item in plan.deployments)
    api.rows.extend(_row(item) for item in equivocation_plan.deployments)
    api.aliases[plan.stable_alias] = equivocation_plan.group_name

    with pytest.raises(ReconcileError, match="equivocate"):
        activate(plan, api, now=GENERATED_AT)
