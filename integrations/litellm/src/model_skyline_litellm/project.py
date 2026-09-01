from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from model_skyline.canonical import canonical_bytes, content_hash
from model_skyline.models import SelectionSnapshot
from model_skyline.selection import selection_hash_matches

from model_skyline_litellm.models import (
    IntegrationConfig,
    PlannedDeployment,
    ProjectionPlan,
)

DEPLOYMENT_NAMESPACE = UUID("08e3d694-e2cb-4caa-abd4-e787f3fef211")
DEFAULT_MAX_CLOCK_SKEW = timedelta(minutes=5)


class ProjectionError(ValueError):
    """A selection cannot be projected without weakening its identity or freshness."""


def _owner_prefix(stable_alias: str) -> str:
    alias_digest = hashlib.sha256(stable_alias.encode("utf-8")).hexdigest()[:16]
    return f"msky-{alias_digest}-"


def project_selection(
    snapshot: SelectionSnapshot,
    config: IntegrationConfig,
    *,
    now: datetime,
    max_clock_skew: timedelta = DEFAULT_MAX_CLOCK_SKEW,
) -> ProjectionPlan:
    """Map one validated ordered selection to immutable LiteLLM deployment rows."""

    if now.tzinfo is None:
        raise ProjectionError("projection clock must include a timezone")
    if max_clock_skew < timedelta(0):
        raise ProjectionError("max_clock_skew cannot be negative")
    if not selection_hash_matches(snapshot):
        raise ProjectionError("selection snapshot hash does not match its content")
    if snapshot.selection_id != config.expected_selection_id:
        raise ProjectionError("selection identity does not match the integration pin")
    if snapshot.frontier_id != config.expected_frontier_id:
        raise ProjectionError("frontier identity does not match the integration pin")
    if snapshot.workload != config.expected_workload:
        raise ProjectionError("workload identity does not match the integration pin")
    if snapshot.generated_at > now + max_clock_skew:
        raise ProjectionError("selection snapshot is generated in the future")
    if now > snapshot.valid_until:
        raise ProjectionError("selection snapshot has expired")
    if len(snapshot.choices) > config.max_candidates:
        raise ProjectionError("selection exceeds the configured candidate cap")

    bindings = {canonical_bytes(binding.offering): binding for binding in config.bindings}
    resolved = []
    for rank, choice in enumerate(snapshot.choices, start=1):
        binding = bindings.get(canonical_bytes(choice.offering))
        if binding is None:
            raise ProjectionError("selection contains an offering without an exact local binding")
        target = config.targets[binding.target_id]
        offering_sha256 = content_hash(choice.offering)
        target_fingerprint = content_hash(target)
        resolved.append((rank, target, offering_sha256, target_fingerprint))

    projection_id = content_hash(
        {
            "schema_version": "model-skyline-litellm/projection/v1alpha1",
            "snapshot_id": snapshot.snapshot_id,
            "routes": [
                {
                    "offering_sha256": offering_sha256,
                    "target_fingerprint": target_fingerprint,
                }
                for _, _, offering_sha256, target_fingerprint in resolved
            ],
        }
    )
    owner_prefix = _owner_prefix(config.stable_alias)
    group_name = f"{owner_prefix}{snapshot.snapshot_id}-{projection_id}"
    deployments: list[PlannedDeployment] = []
    for rank, target, offering_sha256, target_fingerprint in resolved:
        deployment_id = uuid5(
            DEPLOYMENT_NAMESPACE,
            f"{group_name}:{offering_sha256}",
        )
        deployments.append(
            PlannedDeployment(
                deployment_id=deployment_id,
                group_name=group_name,
                rank=rank,
                offering_sha256=offering_sha256,
                target_fingerprint=target_fingerprint,
                snapshot_id=snapshot.snapshot_id,
                projection_id=projection_id,
                policy_hash=snapshot.policy_hash,
                frontier_snapshot_id=snapshot.frontier_snapshot_id,
                generated_at=snapshot.generated_at,
                valid_until=snapshot.valid_until,
                target=target,
            )
        )
    return ProjectionPlan(
        snapshot_id=snapshot.snapshot_id,
        projection_id=projection_id,
        group_name=group_name,
        owner_prefix=owner_prefix,
        stable_alias=config.stable_alias,
        generated_at=snapshot.generated_at,
        valid_until=snapshot.valid_until,
        deployments=tuple(deployments),
    )
