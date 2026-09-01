from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self, cast

from pydantic import Field, JsonValue, field_validator, model_validator

from model_skyline.canonical import canonical_bytes, content_hash
from model_skyline.engine import frontier_hash_matches
from model_skyline.models import (
    MAX_SELECTION_CANDIDATES,
    AxisDescriptor,
    AxisEstimate,
    FrontierSnapshot,
    FrozenModel,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    PortablePublicationId,
    Sha256Digest,
    SnapshotTtlSeconds,
    SourceReference,
    WorkloadReference,
)

MAX_OFFERING_IDENTITY_BYTES = 2_048
COMPONENT_PROJECTION_DOMAIN = "model-skyline/quality-portfolio-component-projection/v1"
PORTFOLIO_PROJECTION_DOMAIN = "model-skyline/quality-portfolio-projection/v1"

OutputSignal = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"),
]
ShortText = Annotated[str, Field(min_length=1, max_length=2_048)]
ReasonCode = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$"),
]
FailureReasons = Annotated[tuple[ReasonCode, ...], Field(min_length=1, max_length=32)]


def _identity(offering: OfferingKey) -> bytes:
    return canonical_bytes(offering)


def _sources(values: Iterable[SourceReference]) -> tuple[SourceReference, ...]:
    result: dict[str, SourceReference] = {}
    for source in values:
        previous = result.get(source.id)
        if previous is not None and previous != source:
            raise ValueError(f"source id {source.id!r} maps to different descriptors")
        result[source.id] = source
    return tuple(result[source_id] for source_id in sorted(result))


class PortfolioComponent(FrozenModel):
    component_id: PortablePublicationId
    frontier_id: str = Field(min_length=1, max_length=256)
    workload: WorkloadReference
    quality_axis: AxisDescriptor
    output_signal: OutputSignal
    max_age_seconds: SnapshotTtlSeconds
    correlation_group: PortablePublicationId


class PortfolioPolicy(FrozenModel):
    schema_version: Literal["model-skyline/quality-portfolio-policy/v1alpha1"] = (
        "model-skyline/quality-portfolio-policy/v1alpha1"
    )
    kind: Literal["quality-portfolio-policy"] = "quality-portfolio-policy"
    portfolio_id: PortablePublicationId
    version: str = Field(min_length=1, max_length=128)
    output_workload: WorkloadReference
    components: tuple[PortfolioComponent, ...] = Field(min_length=2, max_length=4)
    required_component_ids: tuple[PortablePublicationId, ...] = Field(min_length=1, max_length=4)
    minimum_measured_components: Annotated[int, Field(strict=True, ge=1, le=4)]
    correlation_rationale: ShortText
    statistical_independence_assumed: Literal[False] = False

    @field_validator("components")
    @classmethod
    def sort_components(
        cls, value: tuple[PortfolioComponent, ...]
    ) -> tuple[PortfolioComponent, ...]:
        return tuple(sorted(value, key=lambda item: item.component_id))

    @field_validator("required_component_ids")
    @classmethod
    def sort_required(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(value))

    @model_validator(mode="after")
    def coherent(self) -> Self:
        component_ids = tuple(item.component_id for item in self.components)
        frontier_ids = tuple(item.frontier_id for item in self.components)
        required = self.required_component_ids
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("component ids must be unique")
        if len(frontier_ids) != len(set(frontier_ids)):
            raise ValueError("frontier ids must be unique")
        output_signals = tuple(item.output_signal for item in self.components)
        if len(output_signals) != len(set(output_signals)):
            raise ValueError("component output signals must be unique")
        if len(required) != len(set(required)) or not set(required) <= set(component_ids):
            raise ValueError("required component ids must be unique and declared")
        if not len(required) <= self.minimum_measured_components <= len(self.components):
            raise ValueError("minimum measured coverage is inconsistent")
        if any(item.workload == self.output_workload for item in self.components):
            raise ValueError("output workload must differ from benchmark workloads")
        workload_groups: dict[bytes, set[str]] = {}
        for item in self.components:
            workload_groups.setdefault(canonical_bytes(item.workload), set()).add(
                item.correlation_group
            )
        if any(len(groups) > 1 for groups in workload_groups.values()):
            raise ValueError("a reused workload must use one correlation group")

        return self


class PortfolioCandidateAudit(FrozenModel):
    offering: OfferingKey
    component_failures: dict[PortablePublicationId, FailureReasons] = Field(default_factory=dict)


class PortfolioFrontierBinding(FrozenModel):
    component_id: PortablePublicationId
    frontier_snapshot_id: Sha256Digest
    config_hash: Sha256Digest
    catalog_hash: Sha256Digest
    axis_evidence_inventory_id: Sha256Digest
    generated_at: datetime
    quality_projection_sha256: Sha256Digest
    sources: tuple[SourceReference, ...] = Field(default=(), max_length=512)


class _PortfolioDerivationContent(FrozenModel):
    schema_version: Literal["model-skyline/quality-portfolio-derivation/v1alpha1"] = (
        "model-skyline/quality-portfolio-derivation/v1alpha1"
    )
    kind: Literal["quality-portfolio-derivation"] = "quality-portfolio-derivation"
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    algorithm_version: Literal["portfolio-coverage-v1"] = "portfolio-coverage-v1"
    policy_hash: Sha256Digest
    generated_at: datetime
    valid_until: datetime
    bindings: tuple[PortfolioFrontierBinding, ...] = Field(min_length=2, max_length=4)
    base_catalog_hash: Sha256Digest
    quality_projection_sha256: Sha256Digest
    catalog_hash: Sha256Digest
    candidates: tuple[PortfolioCandidateAudit, ...] = Field(
        min_length=1, max_length=MAX_SELECTION_CANDIDATES
    )

    @field_validator("generated_at", "valid_until")
    @classmethod
    def utc_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("portfolio timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.valid_until <= self.generated_at:
            raise ValueError("portfolio validity must follow generation")
        if self.quality_projection_sha256 != _portfolio_projection_hash(self.bindings):
            raise ValueError("portfolio quality projection hash mismatch")
        return self


class PortfolioDerivationSnapshot(_PortfolioDerivationContent):
    snapshot_id: Sha256Digest

    @model_validator(mode="after")
    def valid_hash(self) -> Self:
        if self.snapshot_id != portfolio_snapshot_hash(self):
            raise ValueError("portfolio derivation hash mismatch")
        return self


@dataclass(frozen=True, slots=True)
class PortfolioBuild:
    snapshot: PortfolioDerivationSnapshot
    catalog: ObservationCatalog


@dataclass(frozen=True, slots=True)
class _Evaluation:
    estimate: AxisEstimate | None = None
    deadline: datetime | None = None
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Result:
    offering: OfferingKey
    evaluations: Mapping[str, _Evaluation]
    eligible: bool


def portfolio_policy_hash(policy: PortfolioPolicy) -> str:
    return content_hash(policy)


def portfolio_snapshot_hash(snapshot: PortfolioDerivationSnapshot) -> str:
    return content_hash(snapshot.model_dump(mode="json", exclude={"snapshot_id"}))


def portfolio_component_signal_id(component: PortfolioComponent) -> str:
    return component.output_signal


def _base_catalog(
    policy: PortfolioPolicy, value: ObservationCatalog
) -> tuple[ObservationCatalog, tuple[OfferingKey, ...]]:
    catalog = ObservationCatalog.model_validate(value.model_dump(mode="json"))
    if catalog.workload != policy.output_workload:
        raise ValueError("base catalog workload does not match portfolio output workload")
    if not 1 <= len(catalog.offerings) <= MAX_SELECTION_CANDIDATES:
        raise ValueError("base catalog is empty or exceeds the candidate limit")
    result = tuple(item.offering for item in catalog.offerings)
    identities = [_identity(item) for item in result]
    if any(len(item) > MAX_OFFERING_IDENTITY_BYTES for item in identities):
        raise ValueError("OfferingKey identity exceeds the byte limit")
    if len(identities) != len(set(identities)):
        raise ValueError("base catalog candidates must have distinct OfferingKeys")
    candidates = tuple(item for _, item in sorted(zip(identities, result, strict=True)))
    return catalog, candidates


def _frontiers(
    policy: PortfolioPolicy,
    supplied: Mapping[str, FrontierSnapshot],
    candidates: tuple[OfferingKey, ...],
    generated_at: datetime,
) -> dict[str, tuple[FrontierSnapshot, Mapping[bytes, AxisEstimate]]]:
    if set(supplied) != {item.component_id for item in policy.components}:
        raise ValueError("frontier inputs must exactly match portfolio components")
    candidate_identities = {_identity(item) for item in candidates}
    result: dict[str, tuple[FrontierSnapshot, Mapping[bytes, AxisEstimate]]] = {}
    for component in policy.components:
        frontier = FrontierSnapshot.model_validate(
            supplied[component.component_id].model_dump(mode="json")
        )
        if not frontier_hash_matches(frontier):
            raise ValueError(f"component {component.component_id!r} frontier hash mismatch")
        if frontier.generated_at.astimezone(UTC) > generated_at:
            raise ValueError(f"component {component.component_id!r} is future-dated")
        if frontier.frontier_id != component.frontier_id or frontier.workload != component.workload:
            raise ValueError(f"component {component.component_id!r} semantics mismatch")
        if component.quality_axis not in frontier.axes:
            raise ValueError(f"component {component.component_id!r} quality axis mismatch")
        if frontier.axis_evidence is None:
            raise ValueError(f"component {component.component_id!r} lacks axis evidence")
        by_id = {item.offering.offering_id: item for item in frontier.axis_evidence.candidates}
        for candidate in candidates:
            found = by_id.get(candidate.offering_id)
            if found is not None and found.offering != candidate:
                raise ValueError(
                    f"component {component.component_id!r} OfferingKey mismatch for "
                    f"offering_id {candidate.offering_id!r}"
                )
        result[component.component_id] = (
            frontier,
            {
                _identity(item.offering): item.axes[component.quality_axis.metric]
                for item in frontier.axis_evidence.candidates
                if _identity(item.offering) in candidate_identities
                and component.quality_axis.metric in item.axes
            },
        )
    return result


def _evaluate(
    component: PortfolioComponent,
    estimate: AxisEstimate | None,
    generated_at: datetime,
    max_clock_skew: timedelta,
) -> _Evaluation:
    if estimate is None:
        return _Evaluation(failures=("missing",))
    failures: set[str] = set()
    if not estimate.sources:
        failures.add("source_missing")
    for source in estimate.sources:
        if not source.version:
            failures.add("source_version_missing")
        if not source.methodology:
            failures.add("source_methodology_missing")
        if source.raw_sha256 is None:
            failures.add("source_raw_digest_missing")
        if not source.license and source.terms_url is None:
            failures.add("source_rights_missing")
        if source.retrieved_at is None:
            failures.add("source_retrieved_at_missing")
        elif source.retrieved_at.astimezone(UTC) > generated_at + max_clock_skew:
            failures.add("source_retrieved_at_future")
    if failures:
        return _Evaluation(estimate=estimate, failures=tuple(sorted(failures)))
    if estimate.oldest_observed_at is None:
        return _Evaluation(estimate=estimate, failures=("observed_at_missing",))
    observed_at = estimate.oldest_observed_at.astimezone(UTC)
    deadline = observed_at + timedelta(seconds=component.max_age_seconds)
    if observed_at > generated_at + max_clock_skew:
        return _Evaluation(estimate, deadline, failures=("observed_at_future",))
    if generated_at >= deadline:
        return _Evaluation(estimate, deadline, failures=("evidence_stale",))
    return _Evaluation(estimate, deadline)


def _component_projection(component: PortfolioComponent, results: tuple[_Result, ...]) -> str:
    rows: list[dict[str, object]] = []
    for result in results:
        evaluation = result.evaluations[component.component_id]
        rows.append(
            {
                "offering": result.offering.model_dump(mode="json"),
                "estimate": (
                    evaluation.estimate.model_dump(mode="json")
                    if evaluation.estimate is not None
                    else None
                ),
                "failures": evaluation.failures,
            }
        )
    return content_hash(
        {
            "domain": COMPONENT_PROJECTION_DOMAIN,
            "component": component.model_dump(mode="json"),
            "candidates": rows,
        }
    )


def _portfolio_projection_hash(bindings: tuple[PortfolioFrontierBinding, ...]) -> str:
    return content_hash(
        {
            "domain": PORTFOLIO_PROJECTION_DOMAIN,
            "components": [
                [item.component_id, item.quality_projection_sha256] for item in bindings
            ],
        }
    )


def _catalog(
    policy: PortfolioPolicy,
    base_catalog: ObservationCatalog,
    results: tuple[_Result, ...],
    projection: str,
    valid_until: datetime,
) -> ObservationCatalog:
    reserved_signals = {component.output_signal for component in policy.components}
    conflicts = sorted(
        {
            signal_id
            for offering in base_catalog.offerings
            for signal_id in offering.signals
            if signal_id in reserved_signals
        }
    )
    if conflicts:
        raise ValueError(
            "base catalog already contains portfolio output signals: "
            + ", ".join(repr(item) for item in conflicts)
        )
    source_retrieved_at = max(
        source.retrieved_at
        for result in results
        if result.eligible
        for evaluation in result.evaluations.values()
        if not evaluation.failures and evaluation.estimate is not None
        for source in evaluation.estimate.sources
        if source.retrieved_at is not None
    )
    source = SourceReference(
        id=f"quality-portfolio:{policy.portfolio_id}",
        version=f"{policy.version}/projection:{projection}",
        license="NOASSERTION",
        methodology=(
            "Derived benchmark portfolio; exact inputs, rights, and failures are in the "
            "derivation snapshot. Scalar combinations belong in ordinary FormulaMetric "
            "configuration; statistical independence is not assumed."
        ),
        raw_sha256=projection,
        retrieved_at=source_retrieved_at,
    )
    base_by_identity = {_identity(item.offering): item for item in base_catalog.offerings}
    offerings: list[OfferingObservation] = []
    for result in results:
        base = base_by_identity[_identity(result.offering)]
        signals = dict(base.signals)
        measured: list[str] = []
        for component in policy.components:
            evaluation = result.evaluations[component.component_id]
            if evaluation.failures:
                continue
            estimate = evaluation.estimate
            if estimate is None:  # pragma: no cover - builder invariant
                raise ValueError("measured component lacks an estimate")
            measured.append(component.component_id)
            signal_id = portfolio_component_signal_id(component)
            if not result.eligible:
                continue
            signals[signal_id] = Observation(
                value=estimate.value,
                unit=estimate.unit,
                lower=estimate.lower,
                upper=estimate.upper,
                sample_count=estimate.minimum_sample_count,
                observed_at=estimate.oldest_observed_at,
                source=source,
            )
        metadata = dict(base.metadata)
        if "quality_portfolio" in metadata:
            raise ValueError("base catalog metadata already contains quality_portfolio")
        base_publication_safe = metadata.get("publication_safe")
        metadata["publication_safe"] = False
        portfolio_metadata: dict[str, JsonValue] = {
            "policy_hash": portfolio_policy_hash(policy),
            "quality_projection_sha256": projection,
            "valid_until": valid_until.isoformat(),
            "eligible": result.eligible,
            "component_failures": {
                key: list(value.failures)
                for key, value in result.evaluations.items()
                if value.failures
            },
            "measured_component_ids": cast(JsonValue, measured),
            "correlation_groups": {
                item.component_id: item.correlation_group
                for item in policy.components
                if item.component_id in measured
            },
            "statistical_independence_assumed": False,
            "base_publication_safe": base_publication_safe,
        }
        metadata["quality_portfolio"] = portfolio_metadata
        offerings.append(
            OfferingObservation(
                offering=result.offering,
                signals=signals,
                default_source=base.default_source,
                metadata=metadata,
            )
        )
    return ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=base_catalog.workload,
        offerings=offerings,
    )


def build_portfolio(
    policy: PortfolioPolicy,
    component_frontiers: Mapping[str, FrontierSnapshot],
    base_catalog: ObservationCatalog,
    *,
    generated_at: datetime,
    max_clock_skew: timedelta = timedelta(minutes=5),
) -> PortfolioBuild:
    policy = PortfolioPolicy.model_validate(policy.model_dump(mode="json"))
    if generated_at.tzinfo is None or max_clock_skew < timedelta(0):
        raise ValueError("generation requires a timezone and nonnegative future skew")
    generated_at = generated_at.astimezone(UTC)
    base_catalog, candidates = _base_catalog(policy, base_catalog)
    captures = _frontiers(policy, component_frontiers, candidates, generated_at)
    required = set(policy.required_component_ids)
    results: list[_Result] = []
    for offering in candidates:
        identity = _identity(offering)
        evaluations = {
            component.component_id: _evaluate(
                component,
                captures[component.component_id][1].get(identity),
                generated_at,
                max_clock_skew,
            )
            for component in policy.components
        }
        measured = {key for key, value in evaluations.items() if not value.failures}
        eligible = required <= measured and len(measured) >= policy.minimum_measured_components
        results.append(_Result(offering, evaluations, eligible))
    results_tuple = tuple(results)
    if not any(item.eligible for item in results_tuple):
        raise ValueError("no candidate meets portfolio coverage")
    valid_until = min(
        evaluation.deadline
        for result in results_tuple
        if result.eligible
        for evaluation in result.evaluations.values()
        if not evaluation.failures and evaluation.deadline is not None
    )

    bindings: list[PortfolioFrontierBinding] = []
    for component in policy.components:
        frontier = captures[component.component_id][0]
        inventory = frontier.axis_evidence
        if inventory is None:  # pragma: no cover - validated above
            raise ValueError("frontier lacks axis evidence")
        bindings.append(
            PortfolioFrontierBinding(
                component_id=component.component_id,
                frontier_snapshot_id=frontier.snapshot_id,
                config_hash=frontier.config_hash,
                catalog_hash=frontier.catalog_hash,
                axis_evidence_inventory_id=inventory.inventory_id,
                generated_at=frontier.generated_at,
                quality_projection_sha256=_component_projection(component, results_tuple),
                sources=_sources(
                    source
                    for result in results_tuple
                    for estimate in (result.evaluations[component.component_id].estimate,)
                    if estimate is not None
                    for source in estimate.sources
                ),
            )
        )
    bindings_tuple = tuple(bindings)
    projection = _portfolio_projection_hash(bindings_tuple)
    catalog = _catalog(policy, base_catalog, results_tuple, projection, valid_until)
    audits = tuple(
        PortfolioCandidateAudit(
            offering=result.offering,
            component_failures={
                component_id: evaluation.failures
                for component_id, evaluation in result.evaluations.items()
                if evaluation.failures
            },
        )
        for result in results_tuple
    )
    content = _PortfolioDerivationContent(
        policy_hash=portfolio_policy_hash(policy),
        generated_at=generated_at,
        valid_until=valid_until,
        bindings=bindings_tuple,
        base_catalog_hash=content_hash(base_catalog),
        quality_projection_sha256=projection,
        catalog_hash=content_hash(catalog),
        candidates=audits,
    )
    snapshot = PortfolioDerivationSnapshot(
        snapshot_id=content_hash(content), **content.model_dump()
    )
    return PortfolioBuild(snapshot, catalog)


def verify_portfolio(
    policy: PortfolioPolicy,
    component_frontiers: Mapping[str, FrontierSnapshot],
    base_catalog: ObservationCatalog,
    snapshot: PortfolioDerivationSnapshot,
    *,
    now: datetime,
    max_clock_skew: timedelta = timedelta(minutes=5),
) -> None:
    if now.tzinfo is None or max_clock_skew < timedelta(0):
        raise ValueError("verification requires a timezone and nonnegative future skew")
    now = now.astimezone(UTC)
    snapshot = PortfolioDerivationSnapshot.model_validate(snapshot.model_dump(mode="json"))
    if snapshot.generated_at > now + max_clock_skew:
        raise ValueError("quality portfolio derivation is future-dated")
    if now >= snapshot.valid_until:
        raise ValueError("quality portfolio derivation has expired")
    expected = build_portfolio(
        policy,
        component_frontiers,
        base_catalog,
        generated_at=snapshot.generated_at,
        max_clock_skew=max_clock_skew,
    ).snapshot
    if snapshot != expected:
        raise ValueError("portfolio derivation does not match source frontiers")
