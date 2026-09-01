from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from model_skyline_litellm.api import AdminAPIError, AliasValue, LiteLLMAdminClient
from model_skyline_litellm.models import PlannedDeployment, ProjectionPlan


class ReconcileError(RuntimeError):
    """LiteLLM state cannot be proven to match the projection plan."""


class IndeterminateActivationError(ReconcileError):
    """An alias write may have committed but its complete state cannot be proven."""


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReconcileError(f"LiteLLM {field} has an invalid shape")
    return value


def _model_id(row: Mapping[str, Any]) -> str | None:
    model_info = row.get("model_info")
    if not isinstance(model_info, Mapping):
        return None
    value = model_info.get("id")
    return value if isinstance(value, str) else None


def _rows_for_group(
    rows: Sequence[Mapping[str, Any]],
    group_name: str,
) -> tuple[Mapping[str, Any], ...]:
    return tuple(row for row in rows if row.get("model_name") == group_name)


def _verify_row(row: Mapping[str, Any], expected: PlannedDeployment) -> None:
    if row.get("model_name") != expected.group_name:
        raise ReconcileError("LiteLLM deployment group does not match the staged plan")
    params = _mapping(row.get("litellm_params"), field="deployment parameters")
    if (
        params.get("model") != expected.target.model
        or params.get("litellm_credential_name") != expected.target.credential_name
        or isinstance(params.get("order"), bool)
        or params.get("order") != expected.rank
    ):
        raise ReconcileError("LiteLLM deployment parameters do not match the staged plan")
    model_info = _mapping(row.get("model_info"), field="deployment metadata")
    for key, value in expected.model_info().items():
        if model_info.get(key) != value:
            raise ReconcileError("LiteLLM deployment metadata does not match the staged plan")


def verify_staged(plan: ProjectionPlan, api: LiteLLMAdminClient) -> None:
    rows = api.list_models()
    group_rows = _rows_for_group(rows, plan.group_name)
    expected_by_id = {str(item.deployment_id): item for item in plan.deployments}
    actual_by_id: dict[str, Mapping[str, Any]] = {}
    for row in group_rows:
        model_id = _model_id(row)
        if model_id is None or model_id in actual_by_id:
            raise ReconcileError("LiteLLM managed group contains an invalid deployment identity")
        actual_by_id[model_id] = row
    if set(actual_by_id) != set(expected_by_id):
        raise ReconcileError("LiteLLM managed group is incomplete or contains an unexpected row")
    for model_id, expected in expected_by_id.items():
        _verify_row(actual_by_id[model_id], expected)


def _existing_exact_row(
    rows: Sequence[Mapping[str, Any]],
    expected: PlannedDeployment,
) -> Mapping[str, Any] | None:
    expected_id = str(expected.deployment_id)
    matching_id = tuple(row for row in rows if _model_id(row) == expected_id)
    exact_group = tuple(row for row in matching_id if row.get("model_name") == expected.group_name)
    if len(exact_group) > 1:
        raise ReconcileError("LiteLLM returned duplicate rows for one deterministic deployment")
    if exact_group:
        _verify_row(exact_group[0], expected)
        return exact_group[0]
    if matching_id:
        raise ReconcileError("deterministic deployment id is already bound to another group")
    return None


def stage(plan: ProjectionPlan, api: LiteLLMAdminClient, *, now: datetime) -> None:
    if now.tzinfo is None:
        raise ReconcileError("stage clock must include a timezone")
    if now > plan.valid_until:
        raise ReconcileError("selection expired before staging")
    rows = api.list_models()
    existing_group = _rows_for_group(rows, plan.group_name)
    expected_ids = {str(item.deployment_id) for item in plan.deployments}
    unexpected = {
        model_id
        for row in existing_group
        if (model_id := _model_id(row)) is None or model_id not in expected_ids
    }
    if unexpected:
        raise ReconcileError("managed group already contains an unexpected deployment")

    for deployment in plan.deployments:
        if _existing_exact_row(rows, deployment) is not None:
            continue
        try:
            api.create_model(deployment.create_payload())
        except AdminAPIError:
            # A write can commit before the management request fails. Read by
            # deterministic identity before deciding whether retry is safe.
            after_failure = api.list_models()
            if _existing_exact_row(after_failure, deployment) is None:
                raise ReconcileError(
                    "deployment creation failed and readback did not confirm it"
                ) from None
            rows = after_failure
        else:
            rows = api.list_models()
            if _existing_exact_row(rows, deployment) is None:
                raise ReconcileError("deployment creation was not visible on readback")
    verify_staged(plan, api)


def _router_settings(config: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(config.get("router_settings"), field="router settings")


def _aliases(config: Mapping[str, Any]) -> dict[str, AliasValue]:
    raw = _router_settings(config).get("model_group_alias", {})
    if not isinstance(raw, Mapping):
        raise ReconcileError("LiteLLM alias map has an invalid shape")
    aliases: dict[str, AliasValue] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, (str, Mapping)):
            raise ReconcileError("LiteLLM alias map has an invalid shape")
        aliases[key] = value if isinstance(value, str) else dict(value)
    return aliases


def _ensure_no_widening_fallbacks(config: Mapping[str, Any]) -> None:
    router = _router_settings(config)
    active = sorted(
        key
        for key, value in router.items()
        if (key == "fallbacks" or key.endswith("_fallbacks"))
        and value is not None
        and value != ()
        and value != []
    )
    if active:
        raise ReconcileError("LiteLLM global fallback configuration can widen the managed group")


def _group_generation(
    group_name: str,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[datetime, str]:
    group_rows = _rows_for_group(rows, group_name)
    if not group_rows:
        raise ReconcileError("current managed alias target is missing its deployment rows")
    generations: set[tuple[str, str]] = set()
    for row in group_rows:
        info = _mapping(row.get("model_info"), field="deployment metadata")
        generated_at = info.get("model_skyline_generated_at")
        snapshot_id = info.get("model_skyline_snapshot_id")
        if not isinstance(generated_at, str) or not isinstance(snapshot_id, str):
            raise ReconcileError("current managed alias target lacks generation evidence")
        generations.add((generated_at, snapshot_id))
    if len(generations) != 1:
        raise ReconcileError("current managed alias target has inconsistent generation evidence")
    generated_text, snapshot_id = next(iter(generations))
    try:
        generated_at = datetime.fromisoformat(generated_text.replace("Z", "+00:00"))
    except ValueError:
        raise ReconcileError(
            "current managed alias target has invalid generation evidence"
        ) from None
    if generated_at.tzinfo is None:
        raise ReconcileError("current managed alias generation lacks a timezone")
    return generated_at, snapshot_id


def activate(plan: ProjectionPlan, api: LiteLLMAdminClient, *, now: datetime) -> None:
    if now.tzinfo is None:
        raise ReconcileError("activation clock must include a timezone")
    if now > plan.valid_until:
        raise ReconcileError("selection expired before activation")
    verify_staged(plan, api)
    before_config = api.get_runtime_config()
    _ensure_no_widening_fallbacks(before_config)
    before_aliases = _aliases(before_config)
    current = before_aliases.get(plan.stable_alias)
    if current == plan.group_name:
        return
    if current is not None:
        if not isinstance(current, str):
            raise ReconcileError("stable alias uses an unmanaged LiteLLM alias object")
        if not current.startswith(plan.owner_prefix):
            raise ReconcileError("stable alias is owned by an unmanaged LiteLLM group")
        previous_time, previous_snapshot = _group_generation(current, api.list_models())
        if previous_time > plan.generated_at:
            raise ReconcileError("activation would roll back the managed alias generation")
        if previous_time == plan.generated_at and previous_snapshot != plan.snapshot_id:
            raise ReconcileError("activation would equivocate at one managed generation")

    # This second read narrows, but cannot eliminate, the documented lack of
    # compare-and-swap. Operators must serialize all router-settings writers.
    confirm_config = api.get_runtime_config()
    _ensure_no_widening_fallbacks(confirm_config)
    if _aliases(confirm_config) != before_aliases:
        raise ReconcileError("LiteLLM alias map changed during activation preflight")
    intended = {**before_aliases, plan.stable_alias: plan.group_name}
    try:
        api.update_aliases(intended)
    except AdminAPIError:
        try:
            observed = _aliases(api.get_runtime_config())
        except (AdminAPIError, ReconcileError):
            raise IndeterminateActivationError(
                "alias update failed and post-write state is unavailable"
            ) from None
        if observed == intended:
            return
        if observed == before_aliases:
            raise ReconcileError("alias update failed without changing the alias map") from None
        raise IndeterminateActivationError(
            "alias update failed and post-write state differs from both known maps"
        ) from None
    observed = _aliases(api.get_runtime_config())
    if observed != intended:
        raise IndeterminateActivationError("alias update readback does not match the intended map")
