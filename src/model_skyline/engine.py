from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Any

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, content_hash
from model_skyline.formula import (
    FormulaError,
    compile_formula,
    evaluate_formula,
    referenced_formula_paths,
)
from model_skyline.models import (
    AxisDescriptor,
    AxisEstimate,
    AxisEvidenceCandidate,
    CostFormulaBasis,
    EvaluatedOffering,
    FormulaMetric,
    FrontierAxis,
    FrontierDefinition,
    FrontierSnapshot,
    Goal,
    MetricDefinition,
    Observation,
    ObservationCatalog,
    ObservationRequirements,
    OfferingObservation,
    OracleMetric,
    ProjectConfig,
    RejectedOffering,
    SignalMetric,
    SourceReference,
    UncertaintyMode,
    WorkloadProfile,
    WorkloadReference,
    build_axis_evidence_inventory,
)
from model_skyline.oracles import OracleError, OracleRegistry
from model_skyline.version import VERSION


class EvaluationError(ValueError):
    """A candidate cannot be evaluated under the declared policy."""


_TOTAL_COST_SIGNAL_PREFIXES = {
    CostFormulaBasis.ESTIMATED_TOTAL: "estimated_total_cost_usd",
    CostFormulaBasis.PROVIDER_REPORTED_TOTAL: "provider_reported_total_cost_usd",
    CostFormulaBasis.BILLED_TOTAL: "billed_total_cost_usd",
    CostFormulaBasis.PROVIDER_MARGINAL: "provider_marginal_cost_usd",
}


def _cost_signal_basis(path: str) -> CostFormulaBasis | None:
    if not path.startswith("signals."):
        return None
    signal = path.removeprefix("signals.")
    for basis, prefix in _TOTAL_COST_SIGNAL_PREFIXES.items():
        if signal == prefix or signal.startswith(f"{prefix}_"):
            return basis
    return None


def validate_formula_cost_basis(metric_id: str, definition: FormulaMetric) -> None:
    """Reject a USD formula that omits or mixes mutually exclusive bill bases."""

    paths = referenced_formula_paths(definition.expression)
    signal_paths = {path for path in paths if path.startswith("signals.")}
    referenced_bases = {
        basis for path in signal_paths if (basis := _cost_signal_basis(path)) is not None
    }
    usd_signal_paths = {
        path for path in signal_paths if "usd" in path.removeprefix("signals.").lower()
    }
    basis = definition.cost_basis
    if basis is None:
        if usd_signal_paths or referenced_bases:
            raise ValueError(
                f"metric {metric_id!r} references cost signals but declares no cost_basis"
            )
        return
    if basis is CostFormulaBasis.RECONSTRUCTED_COMPONENTS:
        if referenced_bases:
            raise ValueError(
                f"metric {metric_id!r} declares reconstructed_components but references an "
                "alternative all-in cost basis"
            )
        return
    expected_prefix = _TOTAL_COST_SIGNAL_PREFIXES[basis]
    selected_paths = {
        path
        for path in signal_paths
        if (
            path.removeprefix("signals.") == expected_prefix
            or path.removeprefix("signals.").startswith(f"{expected_prefix}_")
        )
    }
    if not selected_paths:
        raise ValueError(
            f"metric {metric_id!r} declares {basis.value} but does not reference that cost basis"
        )
    if referenced_bases != {basis} or usd_signal_paths != selected_paths:
        raise ValueError(
            f"metric {metric_id!r} mixes {basis.value} with another cost basis or component"
        )


def _canonical_hash(value: Any, *, length: int = 64) -> str:
    return content_hash(value)[:length]


def catalog_hash(catalog: ObservationCatalog) -> str:
    """Hash a catalog independently of its non-semantic offering order."""

    payload = catalog.model_copy(
        update={
            "offerings": sorted(
                catalog.offerings,
                key=lambda item: item.offering.offering_id,
            )
        }
    ).model_dump(mode="json")
    for item in payload["offerings"]:
        offering = item["offering"]
        if offering.get("billing_mode") is None:
            offering.pop("billing_mode", None)
    return _canonical_hash(payload)


def frontier_hash(snapshot: FrontierSnapshot) -> str:
    """Recompute a frontier snapshot's content identity."""

    payload = snapshot.model_dump(mode="json", exclude={"snapshot_id"})
    for collection_name in ("members", "evaluated"):
        collection = payload[collection_name]
        for item in collection:
            offering = item["offering"]
            if offering.get("billing_mode") is None:
                offering.pop("billing_mode", None)
    axis_evidence = payload.get("axis_evidence")
    if axis_evidence is None:
        payload.pop("axis_evidence", None)
    else:
        for candidate in axis_evidence["candidates"]:
            offering = candidate["offering"]
            if offering.get("billing_mode") is None:
                offering.pop("billing_mode", None)
    if payload.get("public_release_blocked") is False:
        payload.pop("public_release_blocked", None)
    return _canonical_hash(payload)


def _explicit_null_billing_mode_frontier_hash(snapshot: FrontierSnapshot) -> str:
    """Recompute the v0.4.0 hash from its short-lived explicit-null encoding.

    Pydantic materializes a missing optional field as ``None``. Without this
    narrowly scoped compatibility candidate, v0.4.0 artifacts that included
    ``billing_mode: null`` would appear corrupt after restoring the intended
    absent/null equivalence.
    """

    payload = snapshot.model_dump(mode="json", exclude={"snapshot_id"})
    if payload.get("axis_evidence") is None:
        payload.pop("axis_evidence", None)
    if payload.get("public_release_blocked") is False:
        payload.pop("public_release_blocked", None)
    return _canonical_hash(payload)


def _current_explicit_null_billing_mode_frontier_hash(snapshot: FrontierSnapshot) -> str:
    """Hash current fields while retaining explicit-null billing modes."""

    return _canonical_hash(snapshot.model_dump(mode="json", exclude={"snapshot_id"}))


def frontier_hash_matches(snapshot: FrontierSnapshot) -> bool:
    """Accept the stable absent/null hash or v0.4.0's explicit-null encoding."""

    return snapshot.snapshot_id in {
        frontier_hash(snapshot),
        _explicit_null_billing_mode_frontier_hash(snapshot),
        _current_explicit_null_billing_mode_frontier_hash(snapshot),
    }


def _decimal_seconds(earlier: datetime, later: datetime) -> Decimal:
    delta = later - earlier
    whole_seconds = delta.days * 86_400 + delta.seconds
    return Decimal(whole_seconds) + Decimal(delta.microseconds) / Decimal(1_000_000)


def _observation_reason(
    observation: Observation,
    requirements: ObservationRequirements,
    *,
    source: SourceReference | None,
    generated_at: datetime,
    allow_unknown_age: bool,
    source_max_age_hours: Decimal | None = None,
) -> str | None:
    if requirements.require_source and source is None:
        return "observation source is required"
    if observation.observed_at is None and not allow_unknown_age:
        return "observation timestamp is required"
    age_hours: Decimal | None = None
    if observation.observed_at is not None:
        with localcontext(POLICY_DECIMAL_CONTEXT):
            age_hours = _decimal_seconds(observation.observed_at, generated_at) / Decimal(3600)
            future_limit = requirements.max_future_skew_minutes / Decimal(60)
        if age_hours < -future_limit:
            return (
                "observation is future-dated "
                f"({-age_hours:.2f}h ahead; {future_limit:.2f}h allowed)"
            )
    maximum_age = requirements.max_age_hours
    if source_max_age_hours is not None and (
        maximum_age is None or source_max_age_hours < maximum_age
    ):
        maximum_age = source_max_age_hours
    if maximum_age is not None and observation.observed_at is not None:
        if age_hours is None:
            raise AssertionError("timestamped observation must have an age")
        if age_hours > maximum_age:
            return f"observation is stale ({age_hours:.1f}h > {maximum_age}h)"
    if requirements.minimum_samples is not None:
        if observation.sample_count is None:
            return "observation sample_count is required"
        if observation.sample_count < requirements.minimum_samples:
            return (
                f"observation has {observation.sample_count} samples; "
                f"{requirements.minimum_samples} required"
            )
    if requirements.require_bounds and (observation.lower is None or observation.upper is None):
        return "observation confidence bounds are required"
    return None


def _point(item: EvaluatedOffering, metric: str) -> Decimal:
    return item.axes[metric].value


def _tolerance(axis: FrontierAxis, left: Decimal, right: Decimal) -> Decimal:
    return axis.epsilon_absolute + axis.epsilon_relative * max(abs(left), abs(right))


def dominance_axis_relation(
    candidate: EvaluatedOffering,
    other: EvaluatedOffering,
    axis: FrontierAxis | AxisDescriptor,
    uncertainty: UncertaintyMode,
    *,
    epsilon_relative: Decimal | None = None,
) -> tuple[bool, bool]:
    """Return ``(no_worse, meaningfully_better)`` for one exact axis.

    Proximity calculations call this with a candidate relative epsilon so their
    serialized interval boundaries use the identical Decimal operation order
    as frontier membership.
    """

    with localcontext(POLICY_DECIMAL_CONTEXT):
        candidate_estimate = candidate.axes[axis.metric]
        other_estimate = other.axes[axis.metric]
        if uncertainty is UncertaintyMode.ROBUST:
            if candidate_estimate.lower is None or candidate_estimate.upper is None:
                raise EvaluationError("robust dominance requires candidate confidence bounds")
            if other_estimate.lower is None or other_estimate.upper is None:
                raise EvaluationError("robust dominance requires comparison confidence bounds")
            if axis.goal is Goal.MINIMIZE:
                left, right = candidate_estimate.upper, other_estimate.lower
            else:
                left, right = candidate_estimate.lower, other_estimate.upper
        else:
            left, right = candidate_estimate.value, other_estimate.value

        # Policy comparisons use one symmetric Decimal34 representation of
        # each operand. Without this, an exact >34-digit value can be compared
        # against a rounded add/subtract result and violate the normalized
        # epsilon bound or make equivalent algebra depend on operand order.
        left, right = +left, +right
        relative = axis.epsilon_relative if epsilon_relative is None else epsilon_relative
        tolerance = axis.epsilon_absolute + relative * max(abs(left), abs(right))
        if axis.goal is Goal.MINIMIZE:
            return left <= right + tolerance, left < right - tolerance
        return left >= right - tolerance, left > right + tolerance


def dominates(
    candidate: EvaluatedOffering,
    other: EvaluatedOffering,
    axes: Iterable[FrontierAxis | AxisDescriptor],
    uncertainty: UncertaintyMode,
) -> bool:
    """Return whether candidate dominates other under point or robust intervals."""

    meaningfully_better = False
    for axis in axes:
        no_worse, better = dominance_axis_relation(candidate, other, axis, uncertainty)
        if not no_worse:
            return False
        meaningfully_better = meaningfully_better or better
    return meaningfully_better


def sort_offerings(
    offerings: Iterable[EvaluatedOffering],
    frontier: FrontierDefinition,
    *,
    order_by: str | None = None,
) -> tuple[EvaluatedOffering, ...]:
    primary_id = order_by or frontier.order_by
    axis_by_metric = {axis.metric: axis for axis in frontier.axes}
    if primary_id not in axis_by_metric:
        raise ValueError(f"order_by {primary_id!r} is not a frontier metric")
    primary = axis_by_metric[primary_id]
    secondary = next(axis for axis in frontier.axes if axis.metric != primary.metric)

    def preference(value: Decimal, goal: Goal) -> Decimal:
        return value if goal is Goal.MINIMIZE else -value

    with localcontext(POLICY_DECIMAL_CONTEXT):
        return tuple(
            sorted(
                offerings,
                key=lambda item: (
                    preference(_point(item, primary.metric), primary.goal),
                    preference(_point(item, secondary.metric), secondary.goal),
                    item.offering.offering_id,
                ),
            )
        )


class FrontierEngine:
    """Evaluate an immutable, explainable two-objective frontier snapshot."""

    def __init__(self, oracles: OracleRegistry | None = None) -> None:
        self.oracles = oracles or OracleRegistry()

    @staticmethod
    def _eligibility_reasons(
        offering: OfferingObservation,
        frontier: FrontierDefinition,
    ) -> list[str]:
        policy = frontier.eligibility
        key = offering.offering
        reasons: list[str] = []
        if policy.providers and key.provider not in policy.providers:
            reasons.append(f"provider {key.provider!r} is not eligible")
        if policy.regions and key.region not in policy.regions:
            reasons.append(f"region {key.region!r} is not eligible")
        missing = sorted(set(policy.required_capabilities) - set(key.capabilities))
        if missing:
            reasons.append(f"required capabilities are missing: {', '.join(missing)}")
        return reasons

    @staticmethod
    def _validate_observation(
        observation: Observation,
        definition: MetricDefinition,
        frontier: FrontierDefinition,
        generated_at: datetime,
        source: SourceReference | None,
    ) -> None:
        if observation.unit != definition.unit:
            raise EvaluationError(
                f"unit mismatch: observed {observation.unit!r}, expected {definition.unit!r}"
            )
        reason = _observation_reason(
            observation,
            definition.requirements,
            source=source,
            generated_at=generated_at,
            allow_unknown_age=frontier.eligibility.allow_unknown_age,
            source_max_age_hours=(
                frontier.eligibility.max_source_age_hours.get(source.id)
                if source is not None
                else None
            ),
        )
        if reason:
            raise EvaluationError(reason)

    def _signal_metric(
        self,
        offering: OfferingObservation,
        definition: SignalMetric,
        frontier: FrontierDefinition,
        generated_at: datetime,
    ) -> AxisEstimate:
        try:
            observation = offering.signals[definition.signal]
        except KeyError as exc:
            raise EvaluationError(f"signal {definition.signal!r} is missing") from exc
        source = observation.source or offering.default_source
        self._validate_observation(
            observation,
            definition,
            frontier,
            generated_at,
            source,
        )
        return AxisEstimate(
            value=observation.value,
            unit=definition.unit,
            lower=observation.lower,
            upper=observation.upper,
            dependencies=(f"signals.{definition.signal}",),
            source_ids=(source.id,) if source else (),
            sources=(source,) if source else (),
            oldest_observed_at=observation.observed_at,
            minimum_sample_count=observation.sample_count,
        )

    def _formula_metric(
        self,
        offering: OfferingObservation,
        definition: FormulaMetric,
        workload: WorkloadProfile,
        frontier: FrontierDefinition,
        generated_at: datetime,
    ) -> AxisEstimate:
        context: Mapping[str, Any] = {
            "signals": {key: value.value for key, value in offering.signals.items()},
            "workload": workload.variables,
            "metadata": offering.metadata,
        }
        try:
            result = evaluate_formula(definition.expression, context)
        except FormulaError as exc:
            raise EvaluationError(str(exc)) from exc
        used_observations: list[tuple[str, Observation, SourceReference | None]] = []
        for path in result.referenced_paths:
            if not path.startswith("signals."):
                continue
            signal_id = path.removeprefix("signals.")
            observation = offering.signals.get(signal_id)
            if observation is None:
                # A missing coalesce() branch is valid and is not used in the result.
                continue
            source = observation.source or offering.default_source
            reason = _observation_reason(
                observation,
                definition.requirements,
                source=source,
                generated_at=generated_at,
                allow_unknown_age=frontier.eligibility.allow_unknown_age,
                source_max_age_hours=(
                    frontier.eligibility.max_source_age_hours.get(source.id)
                    if source is not None
                    else None
                ),
            )
            if reason:
                raise EvaluationError(f"signal {signal_id!r}: {reason}")
            used_observations.append((path, observation, source))
        if frontier.uncertainty is UncertaintyMode.ROBUST:
            raise EvaluationError(
                "robust uncertainty for formula metrics requires precomputed interval support"
            )
        timestamps: list[datetime] = []
        sample_counts: list[int] = []
        timestamps_complete = bool(used_observations)
        sample_counts_complete = bool(used_observations)
        sources_by_hash: dict[str, SourceReference] = {}
        for _, observation, source in used_observations:
            if observation.observed_at is None:
                timestamps_complete = False
            else:
                timestamps.append(observation.observed_at)
            if observation.sample_count is None:
                sample_counts_complete = False
            else:
                sample_counts.append(observation.sample_count)
            if source is not None:
                sources_by_hash[content_hash(source)] = source

        if any(path.startswith("workload.") for path in result.referenced_paths):
            for source in workload.sources:
                sources_by_hash[content_hash(source)] = source
        if (
            any(path.startswith("metadata.") for path in result.referenced_paths)
            and offering.default_source is not None
        ):
            sources_by_hash[content_hash(offering.default_source)] = offering.default_source

        sources = tuple(sources_by_hash[key] for key in sorted(sources_by_hash))
        source_ids = tuple(sorted({source.id for source in sources}))
        return AxisEstimate(
            value=result.value,
            unit=definition.unit,
            dependencies=tuple(sorted(result.referenced_paths)),
            source_ids=source_ids,
            sources=sources,
            oldest_observed_at=min(timestamps) if timestamps_complete else None,
            minimum_sample_count=min(sample_counts) if sample_counts_complete else None,
        )

    def _oracle_metric(
        self,
        metric_id: str,
        offering: OfferingObservation,
        definition: OracleMetric,
        workload_id: str,
        workload: WorkloadProfile,
        frontier: FrontierDefinition,
        generated_at: datetime,
    ) -> AxisEstimate:
        try:
            observation = self.oracles.evaluate(
                name=definition.oracle,
                version=definition.oracle_version,
                offering=offering,
                workload_id=workload_id,
                workload=workload,
                options=definition.options,
            )
        except OracleError as exc:
            raise EvaluationError(
                f"oracle {definition.oracle!r} version {definition.oracle_version!r} failed"
            ) from exc
        self._validate_observation(
            observation,
            definition,
            frontier,
            generated_at,
            observation.source,
        )
        return AxisEstimate(
            value=observation.value,
            unit=definition.unit,
            lower=observation.lower,
            upper=observation.upper,
            dependencies=(f"oracle.{definition.oracle}@{definition.oracle_version}",),
            source_ids=(observation.source.id,) if observation.source else (),
            sources=(observation.source,) if observation.source else (),
            oldest_observed_at=observation.observed_at,
            minimum_sample_count=observation.sample_count,
        )

    def _metric(
        self,
        metric_id: str,
        offering: OfferingObservation,
        definition: MetricDefinition,
        workload_id: str,
        workload: WorkloadProfile,
        frontier: FrontierDefinition,
        generated_at: datetime,
    ) -> AxisEstimate:
        if isinstance(definition, SignalMetric):
            return self._signal_metric(offering, definition, frontier, generated_at)
        if isinstance(definition, FormulaMetric):
            return self._formula_metric(offering, definition, workload, frontier, generated_at)
        return self._oracle_metric(
            metric_id,
            offering,
            definition,
            workload_id,
            workload,
            frontier,
            generated_at,
        )

    @staticmethod
    def _watermarks(catalog: ObservationCatalog) -> dict[str, datetime]:
        watermarks: dict[str, datetime] = {}
        for offering in catalog.offerings:
            for observation in offering.signals.values():
                source = observation.source or offering.default_source
                if source is None or observation.observed_at is None:
                    continue
                current = watermarks.get(source.id)
                if current is None or observation.observed_at > current:
                    watermarks[source.id] = observation.observed_at
        return watermarks

    @staticmethod
    def _sources(
        catalog: ObservationCatalog,
        axis_evidence: Iterable[AxisEvidenceCandidate],
        workload: WorkloadProfile,
    ) -> tuple[SourceReference, ...]:
        by_hash: dict[str, SourceReference] = {}
        for workload_source in workload.sources:
            by_hash[content_hash(workload_source)] = workload_source
        for offering in catalog.offerings:
            candidates = [offering.default_source]
            candidates.extend(observation.source for observation in offering.signals.values())
            for source in candidates:
                if source is not None:
                    by_hash[content_hash(source)] = source
        for candidate in axis_evidence:
            for estimate in candidate.axes.values():
                for source in estimate.sources:
                    by_hash[content_hash(source)] = source
        return tuple(by_hash[key] for key in sorted(by_hash))

    @staticmethod
    def _validate_source_contract(
        catalog: ObservationCatalog,
        workload: WorkloadProfile,
        frontier_id: str,
        frontier: FrontierDefinition,
    ) -> None:
        """Fail closed on ambiguous descriptors or misspelled freshness source IDs."""

        descriptors: dict[str, SourceReference] = {}
        candidates: list[SourceReference | None] = list(workload.sources)
        for offering in catalog.offerings:
            candidates.append(offering.default_source)
            candidates.extend(observation.source for observation in offering.signals.values())
        for source in candidates:
            if source is None:
                continue
            existing = descriptors.get(source.id)
            if existing is not None and existing != source:
                raise ValueError(
                    f"source id {source.id!r} maps to different descriptors across the "
                    "frontier workload and observation catalog"
                )
            descriptors[source.id] = source

        unknown = sorted(set(frontier.eligibility.max_source_age_hours) - descriptors.keys())
        if unknown:
            rendered = ", ".join(repr(source_id) for source_id in unknown)
            raise ValueError(
                f"frontier {frontier_id!r} source age limit references unknown source "
                f"id(s): {rendered}; declare each source in the workload or catalog"
            )

    @staticmethod
    def _effective_policy(
        config: ProjectConfig,
        frontier_id: str,
        frontier: FrontierDefinition,
        workload_id: str,
        workload: WorkloadProfile,
    ) -> dict[str, Any]:
        workload_policy = workload.model_dump(
            mode="json",
            exclude={"sources": {"__all__": {"retrieved_at"}}},
        )
        frontier_policy = frontier.model_dump(mode="json")
        eligibility = frontier_policy["eligibility"]
        if not eligibility["max_source_age_hours"]:
            # The field was added in v0.6. Preserve v0.5 policy identities when
            # the new behavior is unused; an explicit empty map is semantically
            # identical to an omitted map.
            eligibility.pop("max_source_age_hours")
        return {
            "schema_version": config.schema_version,
            "frontier_id": frontier_id,
            "frontier": frontier_policy,
            "workload_id": workload_id,
            # Acquisition time is volatile provenance, not an evaluation-policy input.
            # Source identity, version, digest, licensing, URLs, and methodology remain.
            "workload": workload_policy,
            "metrics": {
                axis.metric: config.metrics[axis.metric].model_dump(mode="json")
                for axis in frontier.axes
            },
        }

    def calculate(
        self,
        config: ProjectConfig,
        catalog: ObservationCatalog,
        frontier_id: str,
        *,
        generated_at: datetime | None = None,
    ) -> FrontierSnapshot:
        now = generated_at or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("generated_at must include a timezone")
        try:
            frontier = config.frontiers[frontier_id]
        except KeyError as exc:
            raise ValueError(f"unknown frontier {frontier_id!r}") from exc
        workload_id = frontier.workload
        workload = config.workloads[workload_id]
        for axis in frontier.axes:
            definition = config.metrics[axis.metric]
            if isinstance(definition, FormulaMetric):
                try:
                    compile_formula(definition.expression)
                except FormulaError as exc:
                    raise ValueError(
                        f"metric {axis.metric!r} has an invalid formula: {exc}"
                    ) from exc
                validate_formula_cost_basis(axis.metric, definition)
        expected_workload = WorkloadReference(
            id=workload_id,
            version=workload.version,
            unit=workload.unit,
        )
        if catalog.workload != expected_workload:
            raise ValueError(
                "observation catalog workload does not match the frontier workload: "
                f"catalog={catalog.workload.id}@{catalog.workload.version} "
                f"frontier={workload_id}@{workload.version}"
            )
        self._validate_source_contract(catalog, workload, frontier_id, frontier)

        accepted: list[EvaluatedOffering] = []
        rejected: list[RejectedOffering] = []
        axis_evidence_candidates: list[AxisEvidenceCandidate] = []
        for offering in catalog.offerings:
            reasons = self._eligibility_reasons(offering, frontier)
            estimates: dict[str, AxisEstimate] = {}
            if not reasons:
                for axis in frontier.axes:
                    try:
                        estimate = self._metric(
                            axis.metric,
                            offering,
                            config.metrics[axis.metric],
                            workload_id,
                            workload,
                            frontier,
                            now,
                        )
                        if frontier.uncertainty is UncertaintyMode.ROBUST and (
                            estimate.lower is None or estimate.upper is None
                        ):
                            raise EvaluationError("robust uncertainty requires confidence bounds")
                        estimates[axis.metric] = estimate
                    except EvaluationError as exc:
                        reasons.append(f"{axis.metric}: {exc}")
            axis_evidence_candidates.append(
                AxisEvidenceCandidate(
                    offering=offering.offering,
                    axes=estimates,
                )
            )
            if reasons:
                rejected.append(
                    RejectedOffering(
                        offering_id=offering.offering.offering_id,
                        reasons=tuple(reasons),
                    )
                )
                continue
            metadata = {
                field: offering.metadata[field]
                for field in frontier.metadata_fields
                if field in offering.metadata
            }
            accepted.append(
                EvaluatedOffering(
                    offering=offering.offering,
                    axes=estimates,
                    metadata=metadata,
                )
            )

        with_dominators: list[EvaluatedOffering] = []
        for other in accepted:
            dominators = tuple(
                sorted(
                    candidate.offering.offering_id
                    for candidate in accepted
                    if candidate is not other
                    and dominates(candidate, other, frontier.axes, frontier.uncertainty)
                )
            )
            with_dominators.append(other.model_copy(update={"dominated_by": dominators}))
        evaluated = sort_offerings(with_dominators, frontier)
        members = tuple(item for item in evaluated if not item.dominated_by)

        axes = tuple(
            AxisDescriptor(
                metric=axis.metric,
                goal=axis.goal,
                unit=config.metrics[axis.metric].unit,
                epsilon_absolute=axis.epsilon_absolute,
                epsilon_relative=axis.epsilon_relative,
            )
            for axis in frontier.axes
        )
        if len(axes) != 2:
            raise AssertionError("validated frontier must have two axes")
        config_digest = _canonical_hash(
            self._effective_policy(
                config,
                frontier_id,
                frontier,
                workload_id,
                workload,
            )
        )
        catalog_digest = catalog_hash(catalog)
        axis_evidence = build_axis_evidence_inventory(
            config_hash=config_digest,
            catalog_hash=catalog_digest,
            generated_at=now,
            workload=expected_workload,
            axes=axes,
            candidates=axis_evidence_candidates,
        )
        snapshot = FrontierSnapshot(
            snapshot_id="pending",
            config_hash=config_digest,
            catalog_hash=catalog_digest,
            engine_version=VERSION,
            generated_at=now,
            frontier_id=frontier_id,
            workload=expected_workload,
            order_by=frontier.order_by,
            uncertainty=frontier.uncertainty,
            axes=axes,
            members=members,
            evaluated=evaluated,
            rejected=tuple(sorted(rejected, key=lambda item: item.offering_id)),
            axis_evidence=axis_evidence,
            public_release_blocked=any(
                offering.metadata.get("publication_safe") is False for offering in catalog.offerings
            ),
            sources=self._sources(catalog, axis_evidence_candidates, workload),
            source_watermarks=self._watermarks(catalog),
        )
        snapshot_id = frontier_hash(snapshot)
        return snapshot.model_copy(update={"snapshot_id": snapshot_id})
