"""Model-family views over exact offering frontiers.

The core frontier remains offering-specific.  This module adds two publication
views without weakening that identity: a best-available projection of real
frontier members and a balanced average over an explicit provider panel.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, localcontext
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, content_hash
from model_skyline.engine import dominates
from model_skyline.models import (
    AxisDescriptor,
    AxisEstimate,
    CanonicalDecimal,
    EvaluatedOffering,
    FrontierSnapshot,
    FrozenModel,
    OfferingKey,
    PortablePublicationId,
    Sha256Digest,
    StrictModel,
    UncertaintyMode,
    WorkloadReference,
)


class ModelFrontierViewError(ValueError):
    """A model view cannot be derived from the declared exact evidence."""


class ModelViewEnvironment(FrozenModel):
    """One equally weighted provider or local-host slot in a balanced panel."""

    environment_id: PortablePublicationId
    provider: str = Field(min_length=1)


class ModelEnvironmentOffering(FrozenModel):
    """The one real offering selected for a model in one environment."""

    environment_id: PortablePublicationId
    offering_id: str = Field(min_length=1)


class BalancedModelSelection(FrozenModel):
    """A complete environment panel for one model family."""

    model_id: str = Field(min_length=1)
    offerings: tuple[ModelEnvironmentOffering, ...] = Field(min_length=1)

    @field_validator("offerings")
    @classmethod
    def canonical_offerings(
        cls, value: tuple[ModelEnvironmentOffering, ...]
    ) -> tuple[ModelEnvironmentOffering, ...]:
        environment_ids = [item.environment_id for item in value]
        if len(environment_ids) != len(set(environment_ids)):
            raise ValueError("a model may select only one offering per environment")
        offering_ids = [item.offering_id for item in value]
        if len(offering_ids) != len(set(offering_ids)):
            raise ValueError("a balanced model row must use distinct offering ids")
        return tuple(sorted(value, key=lambda item: item.environment_id))


class ModelFrontierViewPolicy(StrictModel):
    """Data-only policy for best-available and balanced-average model views."""

    schema_version: Literal["model-skyline/model-frontier-view-policy/v1alpha1"]
    policy_id: PortablePublicationId
    source_snapshot_id: Sha256Digest
    aggregation: Literal["arithmetic_mean"] = "arithmetic_mean"
    environments: tuple[ModelViewEnvironment, ...] = Field(min_length=2)
    models: tuple[BalancedModelSelection, ...] = Field(min_length=2)

    @field_validator("environments")
    @classmethod
    def canonical_environments(
        cls, value: tuple[ModelViewEnvironment, ...]
    ) -> tuple[ModelViewEnvironment, ...]:
        environment_ids = [item.environment_id for item in value]
        providers = [item.provider for item in value]
        if len(environment_ids) != len(set(environment_ids)):
            raise ValueError("environment ids must be unique")
        if len(providers) != len(set(providers)):
            raise ValueError("environment providers must be unique")
        return tuple(sorted(value, key=lambda item: item.environment_id))

    @field_validator("models")
    @classmethod
    def canonical_models(
        cls, value: tuple[BalancedModelSelection, ...]
    ) -> tuple[BalancedModelSelection, ...]:
        model_ids = [item.model_id for item in value]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("balanced model ids must be unique")
        return tuple(sorted(value, key=lambda item: item.model_id))

    @model_validator(mode="after")
    def every_model_has_the_same_panel(self) -> Self:
        expected = {item.environment_id for item in self.environments}
        for model in self.models:
            actual = {item.environment_id for item in model.offerings}
            if actual != expected:
                missing = sorted(expected - actual)
                extra = sorted(actual - expected)
                raise ValueError(
                    f"model {model.model_id!r} does not match the environment panel; "
                    f"missing={missing}, extra={extra}"
                )
        return self


class BestAvailableOffering(FrozenModel):
    """One real implementation that survives the exact offering frontier."""

    offering: OfferingKey
    axes: dict[str, AxisEstimate]


class BestAvailableModel(FrozenModel):
    """A model family with one or more real best-available frontier points."""

    model_id: str = Field(min_length=1)
    offerings: tuple[BestAvailableOffering, ...] = Field(min_length=1)


class BestAvailableModelFrontier(FrozenModel):
    """Model labels projected from the unchanged exact offering frontier."""

    reduction: Literal["any_exact_member"] = "any_exact_member"
    members: tuple[BestAvailableModel, ...]


class BalancedAxisValue(FrozenModel):
    """One equally weighted arithmetic mean with every input value retained."""

    value: CanonicalDecimal
    unit: str = Field(min_length=1)
    lower: CanonicalDecimal | None = None
    upper: CanonicalDecimal | None = None
    environment_values: dict[PortablePublicationId, CanonicalDecimal] = Field(min_length=2)

    @model_validator(mode="after")
    def bounds_contain_value(self) -> Self:
        if self.lower is not None and self.lower > self.value:
            raise ValueError("balanced lower bound cannot exceed value")
        if self.upper is not None and self.upper < self.value:
            raise ValueError("balanced upper bound cannot be below value")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("balanced lower bound cannot exceed upper bound")
        return self


class BalancedModelPoint(FrozenModel):
    """One model-family point averaged from the declared complete panel."""

    model_id: str = Field(min_length=1)
    axes: dict[str, BalancedAxisValue]
    offerings: tuple[ModelEnvironmentOffering, ...]
    dominated_by: tuple[str, ...] = ()


class BalancedAverageModelFrontier(FrozenModel):
    """A two-axis frontier over equally covered model-family averages."""

    aggregation: Literal["arithmetic_mean"] = "arithmetic_mean"
    panel_rule: Literal["same_environments_equal_weight"] = "same_environments_equal_weight"
    environments: tuple[ModelViewEnvironment, ...]
    members: tuple[BalancedModelPoint, ...]
    evaluated: tuple[BalancedModelPoint, ...]


class _ModelFrontierViewContent(FrozenModel):
    schema_version: Literal["model-skyline/model-frontier-view/v1alpha1"] = (
        "model-skyline/model-frontier-view/v1alpha1"
    )
    kind: Literal["model-frontier-view"] = "model-frontier-view"
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    policy_id: PortablePublicationId
    policy_sha256: Sha256Digest
    source_snapshot_id: Sha256Digest
    frontier_id: str = Field(min_length=1)
    workload: WorkloadReference
    order_by: str = Field(min_length=1)
    uncertainty: UncertaintyMode
    axes: tuple[AxisDescriptor, AxisDescriptor]
    best_available: BestAvailableModelFrontier
    balanced_average: BalancedAverageModelFrontier


class ModelFrontierViewSnapshot(_ModelFrontierViewContent):
    """Self-hashed model-level presentation derived from one exact snapshot."""

    view_id: Sha256Digest

    @model_validator(mode="after")
    def view_hash_is_valid(self) -> Self:
        if self.view_id != model_frontier_view_hash(self):
            raise ValueError("model frontier view hash mismatch")
        return self


def model_frontier_view_policy_hash(policy: ModelFrontierViewPolicy) -> str:
    """Return the canonical identity of a model-view policy."""

    return content_hash(policy.model_dump(mode="json"))


def model_frontier_view_hash(snapshot: ModelFrontierViewSnapshot) -> str:
    """Return the canonical content identity of a model-frontier view."""

    return content_hash(snapshot.model_dump(mode="json", exclude={"view_id"}))


def _mean(values: list[Decimal]) -> Decimal:
    with localcontext(POLICY_DECIMAL_CONTEXT):
        return sum(values, start=Decimal(0)) / Decimal(len(values))


def _balanced_axis(
    estimates: dict[str, AxisEstimate], descriptor: AxisDescriptor
) -> BalancedAxisValue:
    values = {environment_id: estimate.value for environment_id, estimate in estimates.items()}
    lowers = [estimate.lower for estimate in estimates.values()]
    uppers = [estimate.upper for estimate in estimates.values()]
    return BalancedAxisValue(
        value=_mean(list(values.values())),
        unit=descriptor.unit,
        lower=(
            _mean([value for value in lowers if value is not None])
            if all(value is not None for value in lowers)
            else None
        ),
        upper=(
            _mean([value for value in uppers if value is not None])
            if all(value is not None for value in uppers)
            else None
        ),
        environment_values=dict(sorted(values.items())),
    )


def _as_evaluated(point: BalancedModelPoint) -> EvaluatedOffering:
    axes = {
        metric: AxisEstimate(
            value=value.value,
            unit=value.unit,
            lower=value.lower,
            upper=value.upper,
        )
        for metric, value in point.axes.items()
    }
    return EvaluatedOffering(
        offering=OfferingKey(
            offering_id=f"model-view/{point.model_id}",
            model_id=point.model_id,
            provider="model-view:balanced-average",
        ),
        axes=axes,
    )


def _preference(value: Decimal, descriptor: AxisDescriptor) -> Decimal:
    return value if descriptor.goal.value == "minimize" else -value


def _sort_balanced_points(
    points: list[BalancedModelPoint],
    axes: tuple[AxisDescriptor, AxisDescriptor],
    order_by: str,
) -> tuple[BalancedModelPoint, ...]:
    primary = next(axis for axis in axes if axis.metric == order_by)
    secondary = next(axis for axis in axes if axis.metric != order_by)
    return tuple(
        sorted(
            points,
            key=lambda point: (
                _preference(point.axes[primary.metric].value, primary),
                _preference(point.axes[secondary.metric].value, secondary),
                point.model_id,
            ),
        )
    )


def build_model_frontier_view(
    policy: ModelFrontierViewPolicy,
    snapshot: FrontierSnapshot,
) -> ModelFrontierViewSnapshot:
    """Build both model views while retaining every selected exact offering."""

    if snapshot.snapshot_id != policy.source_snapshot_id:
        raise ModelFrontierViewError("source snapshot id does not match the policy")

    best_grouped: dict[str, list[BestAvailableOffering]] = defaultdict(list)
    best_order: list[str] = []
    for item in snapshot.members:
        if item.offering.model_id not in best_grouped:
            best_order.append(item.offering.model_id)
        best_grouped[item.offering.model_id].append(
            BestAvailableOffering(offering=item.offering, axes=item.axes)
        )
    best_available = BestAvailableModelFrontier(
        members=tuple(
            BestAvailableModel(model_id=model_id, offerings=tuple(best_grouped[model_id]))
            for model_id in best_order
        )
    )

    evaluated_by_id = {item.offering.offering_id: item for item in snapshot.evaluated}
    environment_by_id = {item.environment_id: item for item in policy.environments}
    balanced_points: list[BalancedModelPoint] = []
    for model in policy.models:
        selected = []
        for selection in model.offerings:
            evaluated_offering = evaluated_by_id.get(selection.offering_id)
            if evaluated_offering is None:
                raise ModelFrontierViewError(
                    f"balanced offering {selection.offering_id!r} is not eligible and evaluated"
                )
            if evaluated_offering.offering.model_id != model.model_id:
                raise ModelFrontierViewError(
                    f"balanced offering {selection.offering_id!r} belongs to "
                    f"{evaluated_offering.offering.model_id!r}, not {model.model_id!r}"
                )
            expected_provider = environment_by_id[selection.environment_id].provider
            if evaluated_offering.offering.provider != expected_provider:
                raise ModelFrontierViewError(
                    f"balanced offering {selection.offering_id!r} provider does not match "
                    f"environment {selection.environment_id!r}"
                )
            selected.append((selection, evaluated_offering))

        point_axes = {
            descriptor.metric: _balanced_axis(
                {
                    selection.environment_id: item.axes[descriptor.metric]
                    for selection, item in selected
                },
                descriptor,
            )
            for descriptor in snapshot.axes
        }
        balanced_points.append(
            BalancedModelPoint(
                model_id=model.model_id,
                axes=point_axes,
                offerings=model.offerings,
            )
        )

    compared = {point.model_id: _as_evaluated(point) for point in balanced_points}
    evaluated_points = []
    for point in balanced_points:
        candidate = compared[point.model_id]
        dominated_by = tuple(
            sorted(
                other_id
                for other_id, other in compared.items()
                if other_id != point.model_id
                and dominates(other, candidate, snapshot.axes, snapshot.uncertainty)
            )
        )
        evaluated_points.append(point.model_copy(update={"dominated_by": dominated_by}))
    evaluated = _sort_balanced_points(evaluated_points, snapshot.axes, snapshot.order_by)
    members = tuple(point for point in evaluated if not point.dominated_by)
    balanced_average = BalancedAverageModelFrontier(
        environments=policy.environments,
        members=members,
        evaluated=evaluated,
    )

    content = _ModelFrontierViewContent(
        policy_id=policy.policy_id,
        policy_sha256=model_frontier_view_policy_hash(policy),
        source_snapshot_id=snapshot.snapshot_id,
        frontier_id=snapshot.frontier_id,
        workload=snapshot.workload,
        order_by=snapshot.order_by,
        uncertainty=snapshot.uncertainty,
        axes=snapshot.axes,
        best_available=best_available,
        balanced_average=balanced_average,
    )
    payload = content.model_dump()
    return ModelFrontierViewSnapshot(
        view_id=content_hash(content.model_dump(mode="json")),
        **payload,
    )


def _plain_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "No eligible model members."
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def line(values: list[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(values))

    separator = "-+-".join("-" * width for width in widths)
    return "\n".join((line(headers), separator, *(line(row) for row in rows)))


def render_model_frontier_table(snapshot: ModelFrontierViewSnapshot) -> str:
    """Render the two model views without leading with exact offering details."""

    axis_headers = [
        f"{axis.metric} {'↓' if axis.goal.value == 'minimize' else '↑'}" for axis in snapshot.axes
    ]
    best_rows = [
        [
            member.model_id,
            *(format(offering.axes[axis.metric].value, "f") for axis in snapshot.axes),
            offering.offering.provider,
        ]
        for member in snapshot.best_available.members
        for offering in member.offerings
    ]
    average_rows = [
        [
            member.model_id,
            *(format(member.axes[axis.metric].value, "f") for axis in snapshot.axes),
            str(len(snapshot.balanced_average.environments)),
        ]
        for member in snapshot.balanced_average.members
    ]
    return (
        "Best available (one real tested implementation)\n"
        + _plain_table(["model", *axis_headers, "provider / local host"], best_rows)
        + "\n\nBalanced average (same environments, equal weight)\n"
        + _plain_table(["model", *axis_headers, "environments"], average_rows)
        + "\n"
    )
