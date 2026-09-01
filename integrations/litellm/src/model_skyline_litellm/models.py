from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from model_skyline.canonical import canonical_bytes
from model_skyline.models import FrozenModel, OfferingKey, Sha256Digest, WorkloadReference
from pydantic import Field, field_validator, model_validator

INTEGRATION_SCHEMA_VERSION = "model-skyline-litellm/v1alpha1"
MAX_BINDINGS = 256
MAX_CANDIDATES = 32

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$")
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:sk-(?:proj-|ant-|live-)?|gh[pousr]_|github_pat_|xox[baprs]-|AIza)"
    r"[A-Za-z0-9_-]{8,}"
)


def _content_free(value: str, *, field: str) -> str:
    if (
        _IDENTIFIER_RE.fullmatch(value) is None
        or "://" in value
        or "\\" in value
        or _CREDENTIAL_RE.search(value) is not None
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"{field} must be a content-free identifier")
    return value


class TargetTemplate(FrozenModel):
    """Operator-local LiteLLM template with no endpoint or secret-valued field."""

    model: str = Field(min_length=1, max_length=256)
    credential_name: str = Field(min_length=1, max_length=256)
    revision: Sha256Digest

    @model_validator(mode="after")
    def fields_are_content_free(self) -> Self:
        _content_free(self.model, field="target model")
        _content_free(self.credential_name, field="credential_name")
        return self


class OfferingBinding(FrozenModel):
    offering: OfferingKey
    target_id: str = Field(min_length=1, max_length=256)

    @field_validator("target_id")
    @classmethod
    def target_id_is_content_free(cls, value: str) -> str:
        return _content_free(value, field="target_id")


class IntegrationConfig(FrozenModel):
    schema_version: Literal["model-skyline-litellm/v1alpha1"] = "model-skyline-litellm/v1alpha1"
    stable_alias: str = Field(min_length=1, max_length=128)
    expected_selection_id: str = Field(min_length=1, max_length=256)
    expected_frontier_id: str = Field(min_length=1, max_length=256)
    expected_workload: WorkloadReference
    max_candidates: int = Field(default=8, strict=True, ge=1, le=MAX_CANDIDATES)
    targets: dict[str, TargetTemplate] = Field(min_length=1, max_length=MAX_BINDINGS)
    bindings: tuple[OfferingBinding, ...] = Field(min_length=1, max_length=MAX_BINDINGS)

    @field_validator("stable_alias")
    @classmethod
    def alias_is_safe(cls, value: str) -> str:
        if (
            _ALIAS_RE.fullmatch(value) is None
            or value.startswith("/")
            or value.endswith("/")
            or "//" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("stable_alias must be a safe lower-case model alias")
        return value

    @field_validator("expected_selection_id", "expected_frontier_id")
    @classmethod
    def expected_ids_are_content_free(cls, value: str) -> str:
        return _content_free(value, field="expected identity")

    @model_validator(mode="after")
    def bindings_are_exact_and_unambiguous(self) -> Self:
        for target_id in self.targets:
            _content_free(target_id, field="target key")
        binding_keys = [canonical_bytes(binding.offering) for binding in self.bindings]
        if len(binding_keys) != len(set(binding_keys)):
            raise ValueError("bindings must contain distinct complete OfferingKey values")
        target_ids = [binding.target_id for binding in self.bindings]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("each offering binding must use a distinct target")
        missing = sorted(set(target_ids) - set(self.targets))
        if missing:
            raise ValueError("every binding must reference a configured target")
        bound_targets = [canonical_bytes(self.targets[target_id]) for target_id in target_ids]
        if len(bound_targets) != len(set(bound_targets)):
            raise ValueError("bound targets must use distinct execution templates")
        return self


class PlannedDeployment(FrozenModel):
    deployment_id: UUID
    group_name: str
    rank: int = Field(strict=True, ge=1, le=MAX_CANDIDATES)
    offering_sha256: Sha256Digest
    target_fingerprint: Sha256Digest
    snapshot_id: Sha256Digest
    projection_id: Sha256Digest
    policy_hash: Sha256Digest
    frontier_snapshot_id: Sha256Digest
    generated_at: datetime
    valid_until: datetime
    target: TargetTemplate = Field(exclude=True, repr=False)

    def model_info(self) -> dict[str, Any]:
        return {
            "id": str(self.deployment_id),
            "model_skyline_snapshot_id": self.snapshot_id,
            "model_skyline_projection_id": self.projection_id,
            "model_skyline_policy_hash": self.policy_hash,
            "model_skyline_frontier_snapshot_id": self.frontier_snapshot_id,
            "model_skyline_offering_sha256": self.offering_sha256,
            "model_skyline_target_fingerprint": self.target_fingerprint,
            "model_skyline_rank": self.rank,
            "model_skyline_generated_at": self.generated_at.isoformat(),
            "model_skyline_valid_until": self.valid_until.isoformat(),
        }

    def create_payload(self) -> dict[str, Any]:
        """Return the private API payload; callers must never log it."""

        return {
            "model_name": self.group_name,
            "litellm_params": {
                "model": self.target.model,
                "litellm_credential_name": self.target.credential_name,
                "order": self.rank,
            },
            "model_info": self.model_info(),
        }


class ProjectionPlan(FrozenModel):
    snapshot_id: Sha256Digest
    projection_id: Sha256Digest
    group_name: str
    owner_prefix: str
    stable_alias: str
    generated_at: datetime
    valid_until: datetime
    deployments: tuple[PlannedDeployment, ...] = Field(min_length=1, max_length=MAX_CANDIDATES)

    @model_validator(mode="after")
    def deployments_are_coherent(self) -> Self:
        if not self.group_name.startswith(self.owner_prefix):
            raise ValueError("managed group does not match its owner prefix")
        if [item.rank for item in self.deployments] != list(range(1, len(self.deployments) + 1)):
            raise ValueError("planned deployment ranks must be consecutive")
        if len({item.deployment_id for item in self.deployments}) != len(self.deployments):
            raise ValueError("planned deployment ids must be unique")
        for deployment in self.deployments:
            if (
                deployment.group_name != self.group_name
                or deployment.snapshot_id != self.snapshot_id
                or deployment.projection_id != self.projection_id
                or deployment.generated_at != self.generated_at
                or deployment.valid_until != self.valid_until
            ):
                raise ValueError("planned deployment does not match its plan")
        return self

    def public_summary(self) -> dict[str, Any]:
        """Return content-free dry-run data, excluding routes and credentials."""

        return {
            "snapshot_id": self.snapshot_id,
            "projection_id": self.projection_id,
            "group_name": self.group_name,
            "stable_alias": self.stable_alias,
            "generated_at": self.generated_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "deployments": [
                {
                    "deployment_id": str(item.deployment_id),
                    "rank": item.rank,
                    "offering_sha256": item.offering_sha256,
                    "target_fingerprint": item.target_fingerprint,
                }
                for item in self.deployments
            ],
        }
