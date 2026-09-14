"""Auditable paired-delta quality estimates for exact model offerings.

This module implements the conservative estimator contract described in
``docs/efficient-quality-estimation.md``.  It does not choose benchmark items
or manufacture a confidence interval: a versioned selection protocol supplies
the paired items, and a versioned uncertainty method supplies the paired-delta
bounds.  ModelSkyline deterministically replays the point estimate and widens
the interval by a held-out absolute-error bound before an estimate can enter a
catalog.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, canonical_bytes, content_hash
from model_skyline.models import (
    CanonicalDecimal,
    EvidenceTier,
    FrozenModel,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    PositiveSafeCount,
    Sha256Digest,
    SourceReference,
)
from model_skyline.quality_evidence import (
    MAX_QUALITY_ARTIFACT_BYTES,
    BoundedCanonicalObject,
    QualityComponentIdentity,
    QualityRawAudit,
    QualityRights,
    QualitySourceIdentity,
)

PAIRED_QUALITY_ESTIMATE_SCHEMA_VERSION = "model-skyline/paired-quality-estimate/v1alpha1"
MAX_PAIRED_ITEMS = 10_000

Identifier = Annotated[str, Field(min_length=1, max_length=512)]
PositiveDecimal = Annotated[CanonicalDecimal, Field(gt=0)]
Probability = Annotated[CanonicalDecimal, Field(gt=0, le=1)]
NonNegativeDecimal = Annotated[CanonicalDecimal, Field(ge=0)]


class PairedItemScore(FrozenModel):
    """One item scored under identical anchor and candidate semantics."""

    item_id: Identifier
    anchor_score: CanonicalDecimal
    candidate_score: CanonicalDecimal
    weight: PositiveDecimal = Decimal(1)
    task_group: Identifier | None = None
    seed: int | None = Field(default=None, strict=True, ge=0, le=(1 << 53) - 1)


class QualityEstimatorValidation(FrozenModel):
    """Held-out error added conservatively to sampling uncertainty."""

    protocol: QualityComponentIdentity
    training_population_sha256: Sha256Digest
    held_out_offering_count: PositiveSafeCount
    absolute_error_bound: NonNegativeDecimal
    coverage_probability: Probability
    validated_domain: BoundedCanonicalObject = Field(min_length=1)


class _PairedQualityEstimateContent(FrozenModel):
    schema_version: Literal["model-skyline/paired-quality-estimate/v1alpha1"] = (
        "model-skyline/paired-quality-estimate/v1alpha1"
    )
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    anchor_offering: OfferingKey
    candidate_offering: OfferingKey
    raw_audit: QualityRawAudit
    source_identity: QualitySourceIdentity
    rights: QualityRights
    metric_id: Identifier
    unit: Identifier
    metric_minimum: CanonicalDecimal
    metric_maximum: CanonicalDecimal
    full_anchor_score: CanonicalDecimal
    full_anchor_result_sha256: Sha256Digest
    subset_selection: QualityComponentIdentity
    estimator: QualityComponentIdentity
    selected_items_sha256: Sha256Digest
    items: tuple[PairedItemScore, ...] = Field(min_length=1, max_length=MAX_PAIRED_ITEMS)
    paired_delta: CanonicalDecimal
    paired_delta_lower: CanonicalDecimal
    paired_delta_upper: CanonicalDecimal
    validation: QualityEstimatorValidation
    estimated_value: CanonicalDecimal
    estimated_lower: CanonicalDecimal
    estimated_upper: CanonicalDecimal
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("items")
    @classmethod
    def canonical_item_order(
        cls, value: tuple[PairedItemScore, ...]
    ) -> tuple[PairedItemScore, ...]:
        duplicate_ids = sorted(
            item_id
            for item_id, count in Counter(item.item_id for item in value).items()
            if count > 1
        )
        if duplicate_ids:
            raise ValueError(f"duplicate paired item id {duplicate_ids[0]!r}")
        identities = [
            (item.item_id, item.task_group or "", -1 if item.seed is None else item.seed)
            for item in value
        ]
        return tuple(item for _, item in sorted(zip(identities, value, strict=True)))

    @model_validator(mode="after")
    def estimate_is_replayable(self) -> Self:
        if self.anchor_offering == self.candidate_offering:
            raise ValueError("anchor and candidate offerings must be distinct")
        if self.anchor_offering.offering_id == self.candidate_offering.offering_id:
            raise ValueError("anchor and candidate offering_id values must be distinct")
        if self.metric_minimum >= self.metric_maximum:
            raise ValueError("metric_minimum must be below metric_maximum")
        for label, score in (
            ("full_anchor_score", self.full_anchor_score),
            *((f"anchor score for {item.item_id!r}", item.anchor_score) for item in self.items),
            *(
                (f"candidate score for {item.item_id!r}", item.candidate_score)
                for item in self.items
            ),
        ):
            if not self.metric_minimum <= score <= self.metric_maximum:
                raise ValueError(f"{label} is outside the declared metric range")

        expected_items_sha256 = paired_item_set_sha256(self.items)
        if self.selected_items_sha256 != expected_items_sha256:
            raise ValueError("selected_items_sha256 does not match paired item identities")

        expected_delta = paired_weighted_delta(self.items)
        if self.paired_delta != expected_delta:
            raise ValueError("paired_delta does not match the weighted paired item scores")
        if not self.paired_delta_lower <= self.paired_delta <= self.paired_delta_upper:
            raise ValueError("paired-delta interval must contain paired_delta")

        expected_value, expected_lower, expected_upper = paired_estimate_values(
            full_anchor_score=self.full_anchor_score,
            paired_delta=self.paired_delta,
            paired_delta_lower=self.paired_delta_lower,
            paired_delta_upper=self.paired_delta_upper,
            validation_error=self.validation.absolute_error_bound,
            metric_minimum=self.metric_minimum,
            metric_maximum=self.metric_maximum,
        )
        if self.estimated_value != expected_value:
            raise ValueError("estimated_value does not match the replayed paired estimate")
        if self.estimated_lower != expected_lower or self.estimated_upper != expected_upper:
            raise ValueError("estimated interval does not match uncertainty plus validation error")
        if len(canonical_bytes(self.model_dump(mode="json"))) > MAX_QUALITY_ARTIFACT_BYTES:
            raise ValueError(
                f"paired quality estimate exceeds {MAX_QUALITY_ARTIFACT_BYTES} canonical bytes"
            )
        return self


class PairedQualityEstimate(_PairedQualityEstimateContent):
    """Self-hashed estimate with exact anchor, candidate, and benchmark identity."""

    estimate_id: Sha256Digest

    @model_validator(mode="after")
    def estimate_identity_is_valid(self) -> Self:
        if self.estimate_id != paired_quality_estimate_hash(self):
            raise ValueError("paired quality estimate hash mismatch")
        return self


def paired_item_set_sha256(items: tuple[PairedItemScore, ...]) -> str:
    """Hash item/seed/group identities independently of their observed scores."""

    canonical_items = sorted(
        items,
        key=lambda item: (
            item.item_id,
            item.task_group or "",
            -1 if item.seed is None else item.seed,
        ),
    )
    return content_hash(
        [
            {
                "item_id": item.item_id,
                "task_group": item.task_group,
                "seed": item.seed,
            }
            for item in canonical_items
        ]
    )


def paired_weighted_delta(items: tuple[PairedItemScore, ...]) -> Decimal:
    """Return ``weighted_mean(candidate_score - anchor_score)`` exactly."""

    with localcontext(POLICY_DECIMAL_CONTEXT):
        total_weight = sum((item.weight for item in items), start=Decimal(0))
        weighted_delta = sum(
            (item.weight * (item.candidate_score - item.anchor_score) for item in items),
            start=Decimal(0),
        )
        return weighted_delta / total_weight


def _clip(value: Decimal, minimum: Decimal, maximum: Decimal) -> Decimal:
    return max(minimum, min(maximum, value))


def paired_estimate_values(
    *,
    full_anchor_score: Decimal,
    paired_delta: Decimal,
    paired_delta_lower: Decimal,
    paired_delta_upper: Decimal,
    validation_error: Decimal,
    metric_minimum: Decimal,
    metric_maximum: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Replay the clipped point and conservative interval for an estimate."""

    with localcontext(POLICY_DECIMAL_CONTEXT):
        value = _clip(full_anchor_score + paired_delta, metric_minimum, metric_maximum)
        lower = _clip(
            full_anchor_score + paired_delta_lower - validation_error,
            metric_minimum,
            metric_maximum,
        )
        upper = _clip(
            full_anchor_score + paired_delta_upper + validation_error,
            metric_minimum,
            metric_maximum,
        )
    return value, lower, upper


def paired_quality_estimate_hash(estimate: PairedQualityEstimate) -> str:
    """Return the content identity of a paired quality estimate."""

    return content_hash(estimate.model_dump(mode="json", exclude={"estimate_id"}))


def build_paired_quality_estimate(
    *,
    anchor_offering: OfferingKey,
    candidate_offering: OfferingKey,
    raw_audit: QualityRawAudit,
    source_identity: QualitySourceIdentity,
    rights: QualityRights,
    metric_id: str,
    unit: str,
    metric_minimum: Decimal,
    metric_maximum: Decimal,
    full_anchor_score: Decimal,
    full_anchor_result_sha256: str,
    subset_selection: QualityComponentIdentity,
    estimator: QualityComponentIdentity,
    items: tuple[PairedItemScore, ...],
    paired_delta_lower: Decimal,
    paired_delta_upper: Decimal,
    validation: QualityEstimatorValidation,
    observed_at: datetime,
) -> PairedQualityEstimate:
    """Build a canonical estimate after deterministically deriving its values."""

    canonical_items = tuple(
        sorted(
            items,
            key=lambda item: (
                item.item_id,
                item.task_group or "",
                -1 if item.seed is None else item.seed,
            ),
        )
    )
    paired_delta = paired_weighted_delta(canonical_items)
    estimated_value, estimated_lower, estimated_upper = paired_estimate_values(
        full_anchor_score=full_anchor_score,
        paired_delta=paired_delta,
        paired_delta_lower=paired_delta_lower,
        paired_delta_upper=paired_delta_upper,
        validation_error=validation.absolute_error_bound,
        metric_minimum=metric_minimum,
        metric_maximum=metric_maximum,
    )
    content = _PairedQualityEstimateContent(
        anchor_offering=anchor_offering,
        candidate_offering=candidate_offering,
        raw_audit=raw_audit,
        source_identity=source_identity,
        rights=rights,
        metric_id=metric_id,
        unit=unit,
        metric_minimum=metric_minimum,
        metric_maximum=metric_maximum,
        full_anchor_score=full_anchor_score,
        full_anchor_result_sha256=full_anchor_result_sha256,
        subset_selection=subset_selection,
        estimator=estimator,
        selected_items_sha256=paired_item_set_sha256(canonical_items),
        items=canonical_items,
        paired_delta=paired_delta,
        paired_delta_lower=paired_delta_lower,
        paired_delta_upper=paired_delta_upper,
        validation=validation,
        estimated_value=estimated_value,
        estimated_lower=estimated_lower,
        estimated_upper=estimated_upper,
        observed_at=observed_at,
    )
    return PairedQualityEstimate(
        estimate_id=content_hash(content),
        **content.model_dump(),
    )


def _public_locator(locator: str | None) -> str | None:
    if locator is None:
        return None
    try:
        probe = SourceReference(id="paired-quality-locator-validation", url=locator)
    except ValueError:
        return None
    return str(probe.url) if probe.url is not None else None


def paired_quality_source_reference(estimate: PairedQualityEstimate) -> SourceReference:
    """Build a catalog source that binds the exact estimate artifact."""

    return SourceReference(
        id=f"{estimate.source_identity.source_id}:paired-quality-estimate",
        version=f"estimate-sha256:{estimate.estimate_id}",
        url=_public_locator(estimate.raw_audit.source_locator),
        terms_url=_public_locator(estimate.rights.terms_locator),
        license=estimate.rights.license_expression,
        methodology=(
            f"Paired additive delta using {estimate.estimator.id}/"
            f"{estimate.estimator.version}; selection {estimate.subset_selection.id}/"
            f"{estimate.subset_selection.version}; source identity sha256:"
            f"{estimate.source_identity.content_sha256}; held-out absolute error bound "
            f"{estimate.validation.absolute_error_bound} at coverage "
            f"{estimate.validation.coverage_probability}."
        ),
        raw_sha256=estimate.raw_audit.raw_sha256,
        retrieved_at=estimate.raw_audit.retrieved_at,
    )


def paired_quality_observation(estimate: PairedQualityEstimate) -> Observation:
    """Project the conservative lower bound as explicitly estimated evidence.

    The artifact retains ``estimated_value``.  Using the lower bound as the
    catalog point prevents a point-uncertainty frontier or selection policy
    from becoming optimistic merely because it explicitly accepted estimates.
    """

    return Observation(
        value=estimate.estimated_lower,
        unit=estimate.unit,
        evidence_tier=EvidenceTier.ESTIMATED,
        lower=estimate.estimated_lower,
        upper=estimate.estimated_upper,
        sample_count=len(estimate.items),
        observed_at=estimate.observed_at,
        source=paired_quality_source_reference(estimate),
    )


def apply_paired_quality_estimate(
    catalog: ObservationCatalog,
    estimate: PairedQualityEstimate,
    *,
    signal_id: str | None = None,
) -> ObservationCatalog:
    """Attach an estimate only to its exact candidate offering.

    The resulting catalog is deliberately marked non-public.  Publishing the
    derived estimate remains a separate rights-reviewed decision.
    """

    validated = PairedQualityEstimate.model_validate(estimate.model_dump(mode="json"))
    output_signal = signal_id or validated.metric_id
    if not output_signal:
        raise ValueError("quality estimate signal id must not be empty")

    matches = [
        offering
        for offering in catalog.offerings
        if offering.offering.offering_id == validated.candidate_offering.offering_id
    ]
    if len(matches) != 1:
        raise ValueError("catalog must contain the candidate offering_id exactly once")
    if matches[0].offering != validated.candidate_offering:
        raise ValueError("catalog candidate does not match the complete estimated OfferingKey")
    if output_signal in matches[0].signals:
        raise ValueError(f"catalog candidate already contains signal {output_signal!r}")

    offerings: list[OfferingObservation] = []
    for offering in catalog.offerings:
        if offering.offering != validated.candidate_offering:
            offerings.append(offering)
            continue
        signals = dict(offering.signals)
        signals[output_signal] = paired_quality_observation(validated)
        metadata: dict[str, Any] = dict(offering.metadata)
        estimates = metadata.get("paired_quality_estimates")
        if estimates is None:
            estimate_map: dict[str, Any] = {}
        elif isinstance(estimates, dict):
            estimate_map = dict(estimates)
        else:
            raise ValueError("paired_quality_estimates metadata must be an object")
        if output_signal in estimate_map:
            raise ValueError(f"paired quality metadata already contains {output_signal!r}")
        estimate_map[output_signal] = {
            "estimate_id": validated.estimate_id,
            "anchor_offering_id": validated.anchor_offering.offering_id,
            "full_anchor_result_sha256": validated.full_anchor_result_sha256,
            "selected_items_sha256": validated.selected_items_sha256,
            "evidence_tier": EvidenceTier.ESTIMATED.value,
        }
        metadata["paired_quality_estimates"] = estimate_map
        metadata["publication_safe"] = False
        offerings.append(
            offering.model_copy(
                update={
                    "signals": signals,
                    "metadata": metadata,
                }
            )
        )
    return ObservationCatalog(
        schema_version=catalog.schema_version,
        workload=catalog.workload,
        offerings=offerings,
    )
