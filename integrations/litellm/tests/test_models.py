from __future__ import annotations

import pytest
from conftest import WORKLOAD, offering_a, offering_b
from pydantic import ValidationError

from model_skyline_litellm.models import (
    IntegrationConfig,
    OfferingBinding,
    TargetTemplate,
)


def _config(**updates: object) -> IntegrationConfig:
    values: dict[str, object] = {
        "stable_alias": "skyline/coding",
        "expected_selection_id": "coding-defaults",
        "expected_frontier_id": "coding-value",
        "expected_workload": WORKLOAD,
        "targets": {
            "target-a": TargetTemplate(
                model="openai/fake-a",
                credential_name="fake-a-v1",
                revision="a" * 64,
            ),
            "target-b": TargetTemplate(
                model="openai/fake-b",
                credential_name="fake-b-v1",
                revision="b" * 64,
            ),
        },
        "bindings": (
            OfferingBinding(offering=offering_a(), target_id="target-a"),
            OfferingBinding(offering=offering_b(), target_id="target-b"),
        ),
    }
    values.update(updates)
    return IntegrationConfig.model_validate(values)


@pytest.mark.parametrize(
    "alias",
    ["Skyline/Coding", "/skyline/coding", "skyline//coding", "skyline/../coding"],
)
def test_rejects_unsafe_aliases(alias: str) -> None:
    with pytest.raises(ValidationError, match="stable_alias"):
        _config(stable_alias=alias)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "https://private.invalid/model"),
        ("credential_name", "sk-" + "ant-not-a-safe-credential-name"),
    ],
)
def test_target_template_rejects_routes_or_secret_shaped_values(field: str, value: str) -> None:
    data = {
        "model": "openai/fake-a",
        "credential_name": "fake-a-v1",
        "revision": "a" * 64,
    }
    data[field] = value
    with pytest.raises(ValidationError, match="content-free"):
        TargetTemplate.model_validate(data)


def test_rejects_duplicate_offerings_or_reused_targets() -> None:
    first = OfferingBinding(offering=offering_a(), target_id="target-a")
    with pytest.raises(ValidationError, match="distinct complete OfferingKey"):
        _config(bindings=(first, first))

    with pytest.raises(ValidationError, match="distinct target"):
        _config(
            bindings=(
                first,
                OfferingBinding(offering=offering_b(), target_id="target-a"),
            )
        )


def test_rejects_binding_to_unknown_target() -> None:
    with pytest.raises(ValidationError, match="configured target"):
        _config(bindings=(OfferingBinding(offering=offering_a(), target_id="missing"),))


def test_rejects_distinct_target_ids_with_the_same_execution_template() -> None:
    first = _config().targets["target-a"]
    with pytest.raises(ValidationError, match="distinct execution templates"):
        _config(targets={"target-a": first, "target-b": first})
