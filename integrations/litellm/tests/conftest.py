from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from model_skyline.models import (
    AxisEstimate,
    InsufficientCandidates,
    ModelChoice,
    OfferingKey,
    SelectionSnapshot,
    WorkloadReference,
)
from model_skyline.selection import selection_hash

from model_skyline_litellm.models import (
    IntegrationConfig,
    OfferingBinding,
    TargetTemplate,
)

GENERATED_AT = datetime(2026, 8, 31, 20, tzinfo=UTC)
WORKLOAD = WorkloadReference(id="coding-agent", version="v1", unit="issue")


def offering_a(**updates: Any) -> OfferingKey:
    values: dict[str, Any] = {
        "offering_id": "provider-a/model-a@standard",
        "model_id": "model-a",
        "provider": "provider-a",
        "endpoint": "responses",
        "billing_mode": "managed",
        "region": "us",
        "service_tier": "standard",
        "quantization": "fp8",
        "reasoning_effort": "medium",
        "agent_harness": "coding-agent-v1",
        "capabilities": ("structured_output", "tools"),
    }
    values.update(updates)
    return OfferingKey.model_validate(values)


def offering_b() -> OfferingKey:
    return OfferingKey(
        offering_id="provider-b/model-b@standard",
        model_id="model-b",
        provider="provider-b",
        endpoint="chat-completions",
        billing_mode="managed",
        region="eu",
        service_tier="standard",
        quantization="bf16",
        reasoning_effort="low",
        agent_harness="coding-agent-v1",
        capabilities=("structured_output", "tools"),
    )


def choice(offering: OfferingKey, value: str) -> ModelChoice:
    return ModelChoice(
        offering=offering,
        axes={"quality": AxisEstimate(value=Decimal(value), unit="ratio")},
        metadata={"private_projection_input": "must-not-forward"},
    )


def make_selection(
    *choices: ModelChoice,
    generated_at: datetime = GENERATED_AT,
) -> SelectionSnapshot:
    snapshot = SelectionSnapshot(
        snapshot_id="0" * 64,
        policy_hash="1" * 64,
        frontier_snapshot_id="2" * 64,
        selection_id="coding-defaults",
        frontier_id="coding-value",
        workload=WORKLOAD,
        order_by="quality",
        requested_count=len(choices),
        max_per_provider=1,
        on_insufficient=InsufficientCandidates.RETURN_AVAILABLE,
        generated_at=generated_at,
        valid_until=generated_at + timedelta(hours=1),
        default=choices[0],
        fallbacks=choices[1:],
    )
    return snapshot.model_copy(update={"snapshot_id": selection_hash(snapshot)})


@pytest.fixture
def selection() -> SelectionSnapshot:
    return make_selection(choice(offering_a(), "0.9"), choice(offering_b(), "0.8"))


@pytest.fixture
def config() -> IntegrationConfig:
    first = offering_a()
    second = offering_b()
    return IntegrationConfig(
        stable_alias="skyline/coding",
        expected_selection_id="coding-defaults",
        expected_frontier_id="coding-value",
        expected_workload=WORKLOAD,
        max_candidates=2,
        targets={
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
        bindings=(
            OfferingBinding(offering=first, target_id="target-a"),
            OfferingBinding(offering=second, target_id="target-b"),
        ),
    )
