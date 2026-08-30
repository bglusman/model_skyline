from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from model_skyline.canonical import canonical_bytes

SCHEMA_VERSION = "model-skyline/v1alpha1"
CANONICAL_DECIMAL_PATTERN = r"^[+-]?\d+(?:\.\d+)?$"
FORBIDDEN_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]")
MAX_DECIMAL_INPUT_LENGTH = 1024
MAX_DECIMAL_SIGNIFICANT_DIGITS = 1024
# These limits align with the canonical string contract: any ordinary fixed-
# point scalar up to 1 KiB remains representable, while exponent expansion and
# hashing work stay bounded. Policy arithmetic still uses a 34-digit context.
MAX_DECIMAL_PLACES = 1024
MAX_DECIMAL_ADJUSTED_EXPONENT = 1024
MAX_DECIMAL_SERIALIZED_LENGTH = 1024
MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_SELECTION_CANDIDATES = 10_000
MAX_SNAPSHOT_TTL_SECONDS = 31_536_000
MAX_CAPABILITIES = 128
CAPABILITY_NAME_PATTERN = r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$"
PUBLIC_SOURCE_URL_PATTERN = r"^https?://[^@?#]+$"
PORTABLE_PUBLICATION_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
RELATIVE_ARTIFACT_PATH_PATTERN = (
    r"^(?:[a-z0-9][a-z0-9._-]{0,254}/)*"
    r"[a-z0-9][a-z0-9._-]{0,254}\.(?:csv|json|txt|xml)$"
)


def _bounded_decimal_input(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("decimal values cannot be booleans")
    if isinstance(value, str) and len(value) > MAX_DECIMAL_INPUT_LENGTH:
        raise ValueError(f"decimal input exceeds {MAX_DECIMAL_INPUT_LENGTH} characters")
    if isinstance(value, int) and value.bit_length() > 4096:
        raise ValueError("decimal integer input is too large")
    return value


def _bounded_canonical_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("decimal values must be finite")

    sign, raw_digits, raw_exponent = value.as_tuple()
    if not isinstance(raw_exponent, int):  # finite Decimals always have an integer exponent
        raise ValueError("decimal exponent must be an integer")
    digits = list(raw_digits)
    exponent = raw_exponent
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1

    normalized = Decimal(0) if not any(digits) else Decimal((sign, tuple(digits), exponent))

    if len(digits) > MAX_DECIMAL_SIGNIFICANT_DIGITS:
        raise ValueError(f"decimal exceeds {MAX_DECIMAL_SIGNIFICANT_DIGITS} significant digits")
    if max(-exponent, 0) > MAX_DECIMAL_PLACES:
        raise ValueError(f"decimal exceeds {MAX_DECIMAL_PLACES} decimal places")
    if abs(normalized.adjusted()) > MAX_DECIMAL_ADJUSTED_EXPONENT:
        raise ValueError(
            f"decimal adjusted exponent exceeds {MAX_DECIMAL_ADJUSTED_EXPONENT} in magnitude"
        )
    if len(format(normalized, "f")) > MAX_DECIMAL_SERIALIZED_LENGTH:
        raise ValueError(f"canonical decimal exceeds {MAX_DECIMAL_SERIALIZED_LENGTH} characters")
    return normalized


CanonicalDecimal = Annotated[
    Decimal,
    BeforeValidator(_bounded_decimal_input),
    AfterValidator(_bounded_canonical_decimal),
    PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json"),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": CANONICAL_DECIMAL_PATTERN,
            "maxLength": MAX_DECIMAL_SERIALIZED_LENGTH,
        },
        mode="serialization",
    ),
]

SafeCount = Annotated[int, Field(strict=True, ge=0, le=MAX_SAFE_INTEGER)]
PositiveSafeCount = Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)]
SelectionCandidateCount = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_SELECTION_CANDIDATES),
]
SnapshotTtlSeconds = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_SNAPSHOT_TTL_SECONDS),
]
CapabilityName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=CAPABILITY_NAME_PATTERN),
]
PortablePublicationId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=PORTABLE_PUBLICATION_ID_PATTERN),
]
Sha256Digest = Annotated[str, Field(pattern=SHA256_PATTERN)]


def _safe_relative_artifact_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
    ):
        raise ValueError("artifact path must contain only safe relative path segments")
    if "\\" in value or "//" in value:
        raise ValueError("artifact path must use canonical forward-slash separators")
    return value


RelativeArtifactPath = Annotated[
    str,
    Field(min_length=1, max_length=512, pattern=RELATIVE_ARTIFACT_PATH_PATTERN),
    AfterValidator(_safe_relative_artifact_path),
]


def _canonical_json_object(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    canonical_bytes(value)
    return value


def _canonicalize_json_numbers(value: Any) -> Any:
    """Keep arbitrary JSON bags exact and hashable across language runtimes."""

    if isinstance(value, Decimal):
        return format(_bounded_canonical_decimal(value), "f")
    if isinstance(value, float):
        return format(_bounded_canonical_decimal(Decimal(str(value))), "f")
    if isinstance(value, dict):
        return {key: _canonicalize_json_numbers(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize_json_numbers(child) for child in value]
    return value


CanonicalJsonObject = Annotated[
    dict[str, JsonValue],
    BeforeValidator(_canonicalize_json_numbers),
    AfterValidator(_canonical_json_object),
]


def _contains_forbidden_text(value: Any) -> bool:
    if isinstance(value, str):
        return FORBIDDEN_TEXT_RE.search(value) is not None
    if isinstance(value, dict):
        return any(
            _contains_forbidden_text(key) or _contains_forbidden_text(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_forbidden_text(child) for child in value)
    return False


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def text_is_safe_for_public_artifacts(self) -> Self:
        if any(_contains_forbidden_text(value) for value in self.__dict__.values()):
            raise ValueError("text contains forbidden XML or terminal control characters")
        return self


class FrozenModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Goal(StrEnum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class UncertaintyMode(StrEnum):
    POINT = "point"
    ROBUST = "robust"


def _public_source_url(value: AnyHttpUrl) -> AnyHttpUrl:
    if value.username is not None or value.password is not None:
        raise ValueError("public source URLs cannot contain user information")
    if value.query is not None:
        raise ValueError("public source URLs cannot contain a query string")
    if value.fragment is not None:
        raise ValueError("public source URLs cannot contain a fragment")
    return value


PublicSourceUrl = Annotated[
    AnyHttpUrl,
    AfterValidator(_public_source_url),
    WithJsonSchema(
        {
            "type": "string",
            "format": "uri",
            "pattern": PUBLIC_SOURCE_URL_PATTERN,
            "maxLength": 2083,
        }
    ),
]


class SourceReference(FrozenModel):
    id: str = Field(min_length=1)
    version: str | None = None
    url: PublicSourceUrl | None = None
    terms_url: PublicSourceUrl | None = None
    license: str | None = None
    methodology: str | None = None
    raw_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    retrieved_at: datetime | None = None

    @field_validator("retrieved_at")
    @classmethod
    def timestamp_has_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("retrieved_at must include a timezone")
        return value


def _ensure_consistent_source_ids(
    sources: Iterable[SourceReference | None],
    *,
    scope: str,
) -> None:
    descriptors: dict[str, SourceReference] = {}
    for source in sources:
        if source is None:
            continue
        existing = descriptors.get(source.id)
        if existing is not None and existing != source:
            raise ValueError(f"source id {source.id!r} maps to multiple descriptors in {scope}")
        descriptors[source.id] = source


class WorkloadReference(FrozenModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    unit: str = Field(min_length=1)


class Observation(FrozenModel):
    """One value with enough context to decide whether it can be trusted."""

    value: CanonicalDecimal
    unit: str = Field(min_length=1)
    lower: CanonicalDecimal | None = None
    upper: CanonicalDecimal | None = None
    sample_count: SafeCount | None = None
    observed_at: datetime | None = None
    source: SourceReference | None = None

    @field_validator("observed_at")
    @classmethod
    def observed_at_has_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return value

    @model_validator(mode="after")
    def bounds_contain_value(self) -> Self:
        if self.lower is not None and self.lower > self.value:
            raise ValueError("lower cannot exceed value")
        if self.upper is not None and self.upper < self.value:
            raise ValueError("upper cannot be below value")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("lower cannot exceed upper")
        return self


class OfferingKey(FrozenModel):
    """Identity grain for a routable candidate, narrower than model name."""

    offering_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    endpoint: str | None = None
    region: str | None = None
    service_tier: str | None = None
    quantization: str | None = None
    reasoning_effort: str | None = None
    agent_harness: str | None = None
    capabilities: tuple[CapabilityName, ...] = Field(default=(), max_length=MAX_CAPABILITIES)

    @field_validator("capabilities", mode="before")
    @classmethod
    def canonical_capabilities(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("capabilities must be an array of strings")
        if any(not isinstance(item, str) for item in value):
            raise ValueError("capabilities must contain only strings")
        if len(value) != len(set(value)):
            raise ValueError("capabilities must not contain duplicates")
        return tuple(sorted(value))


class OfferingObservation(FrozenModel):
    offering: OfferingKey
    signals: dict[str, Observation]
    metadata: CanonicalJsonObject = Field(default_factory=dict)
    default_source: SourceReference | None = None

    @model_validator(mode="after")
    def source_ids_are_consistent(self) -> Self:
        _ensure_consistent_source_ids(
            [self.default_source, *(observation.source for observation in self.signals.values())],
            scope=f"offering {self.offering.offering_id!r}",
        )
        return self


class ObservationCatalog(StrictModel):
    schema_version: Literal["model-skyline/v1alpha1"]
    workload: WorkloadReference
    offerings: list[OfferingObservation]

    @model_validator(mode="after")
    def offering_ids_are_unique(self) -> Self:
        values = [item.offering.offering_id for item in self.offerings]
        duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
        if duplicates:
            raise ValueError(f"offering_id values must be unique: {', '.join(duplicates)}")
        _ensure_consistent_source_ids(
            (
                source
                for offering in self.offerings
                for source in (
                    offering.default_source,
                    *(observation.source for observation in offering.signals.values()),
                )
            ),
            scope="observation catalog",
        )
        return self


class WorkloadProfile(StrictModel):
    unit: str = Field(min_length=1)
    version: str = Field(min_length=1)
    harness: str = Field(min_length=1)
    cohort: str = Field(min_length=1)
    benchmark: str | None = None
    budget: dict[str, CanonicalDecimal] = Field(default_factory=dict)
    description: str | None = None
    variables: dict[str, CanonicalDecimal] = Field(default_factory=dict)
    assumptions: CanonicalJsonObject = Field(default_factory=dict)
    sources: list[SourceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def source_ids_are_consistent(self) -> Self:
        _ensure_consistent_source_ids(self.sources, scope="workload profile")
        return self


class ObservationRequirements(StrictModel):
    max_age_hours: CanonicalDecimal | None = Field(default=None, gt=0)
    max_future_skew_minutes: CanonicalDecimal = Field(default=Decimal(5), ge=0)
    minimum_samples: PositiveSafeCount | None = None
    require_bounds: bool = False
    require_source: bool = False


class MetricBase(StrictModel):
    unit: str = Field(min_length=1)
    description: str | None = None
    requirements: ObservationRequirements = Field(default_factory=ObservationRequirements)


class SignalMetric(MetricBase):
    kind: Literal["signal"]
    signal: str = Field(min_length=1)


class FormulaMetric(MetricBase):
    kind: Literal["formula"]
    expression: str = Field(min_length=1)


class OracleMetric(MetricBase):
    kind: Literal["oracle"]
    oracle: str = Field(min_length=1)
    oracle_version: str = Field(min_length=1)
    options: CanonicalJsonObject = Field(default_factory=dict)


MetricDefinition = Annotated[
    SignalMetric | FormulaMetric | OracleMetric,
    Field(discriminator="kind"),
]


class FrontierAxis(StrictModel):
    metric: str = Field(min_length=1)
    goal: Goal
    epsilon_absolute: CanonicalDecimal = Field(default=Decimal(0), ge=0)
    epsilon_relative: CanonicalDecimal = Field(default=Decimal(0), ge=0)


class EligibilityPolicy(StrictModel):
    providers: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    allow_unknown_age: bool = True


class FrontierDefinition(StrictModel):
    workload: str = Field(min_length=1)
    axes: list[FrontierAxis] = Field(min_length=2, max_length=2)
    order_by: str = Field(min_length=1)
    uncertainty: UncertaintyMode = UncertaintyMode.POINT
    eligibility: EligibilityPolicy = Field(default_factory=EligibilityPolicy)
    metadata_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_axis_pair(self) -> Self:
        metric_ids = [axis.metric for axis in self.axes]
        if len(set(metric_ids)) != 2:
            raise ValueError("frontier axes must reference two distinct metrics")
        if self.order_by not in metric_ids:
            raise ValueError("order_by must reference one of the two frontier metrics")
        return self


class InsufficientCandidates(StrEnum):
    ERROR = "error"
    RETURN_AVAILABLE = "return_available"


class SelectionDefinition(StrictModel):
    frontier: str = Field(min_length=1)
    strategy: Literal["lexicographic"] = "lexicographic"
    count: SelectionCandidateCount = 3
    order_by: str | None = None
    max_per_provider: SelectionCandidateCount | None = None
    snapshot_ttl_seconds: SnapshotTtlSeconds = 3600
    on_insufficient: InsufficientCandidates = InsufficientCandidates.RETURN_AVAILABLE


class ProjectConfig(StrictModel):
    schema_version: Literal["model-skyline/v1alpha1"]
    workloads: dict[str, WorkloadProfile]
    metrics: dict[str, MetricDefinition]
    frontiers: dict[str, FrontierDefinition]
    selections: dict[str, SelectionDefinition] = Field(default_factory=dict)

    @model_validator(mode="after")
    def references_exist(self) -> Self:
        for frontier_id, frontier in self.frontiers.items():
            if frontier.workload not in self.workloads:
                raise ValueError(
                    f"frontier {frontier_id!r} references unknown workload {frontier.workload!r}"
                )
            for axis in frontier.axes:
                if axis.metric not in self.metrics:
                    raise ValueError(
                        f"frontier {frontier_id!r} references unknown metric {axis.metric!r}"
                    )
        for selection_id, selection in self.selections.items():
            if selection.frontier not in self.frontiers:
                raise ValueError(
                    f"selection {selection_id!r} references unknown frontier {selection.frontier!r}"
                )
            if selection.order_by is not None:
                frontier = self.frontiers[selection.frontier]
                if selection.order_by not in {axis.metric for axis in frontier.axes}:
                    raise ValueError(
                        f"selection {selection_id!r} order_by is not a frontier metric"
                    )
        _ensure_consistent_source_ids(
            (source for workload in self.workloads.values() for source in workload.sources),
            scope="project workloads",
        )
        return self


class AxisDescriptor(FrozenModel):
    metric: str
    goal: Goal
    unit: str
    epsilon_absolute: CanonicalDecimal = Decimal(0)
    epsilon_relative: CanonicalDecimal = Decimal(0)


class AxisEstimate(FrozenModel):
    value: CanonicalDecimal
    unit: str
    lower: CanonicalDecimal | None = None
    upper: CanonicalDecimal | None = None
    dependencies: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    sources: tuple[SourceReference, ...] = ()
    oldest_observed_at: datetime | None = None
    minimum_sample_count: SafeCount | None = None

    @field_validator("oldest_observed_at")
    @classmethod
    def oldest_observed_at_has_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("oldest_observed_at must include a timezone")
        return value

    @model_validator(mode="after")
    def estimate_is_coherent(self) -> Self:
        if self.lower is not None and self.lower > self.value:
            raise ValueError("axis lower bound cannot exceed value")
        if self.upper is not None and self.upper < self.value:
            raise ValueError("axis upper bound cannot be below value")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("axis lower bound cannot exceed upper bound")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("axis dependencies must be unique")
        expected_source_ids = tuple(sorted({source.id for source in self.sources}))
        if self.source_ids != expected_source_ids:
            raise ValueError("source_ids must match the embedded axis sources")
        _ensure_consistent_source_ids(self.sources, scope="axis estimate")
        return self


class EvaluatedOffering(FrozenModel):
    offering: OfferingKey
    axes: dict[str, AxisEstimate]
    metadata: CanonicalJsonObject = Field(default_factory=dict)
    dominated_by: tuple[str, ...] = ()


class RejectedOffering(FrozenModel):
    offering_id: str
    reasons: tuple[str, ...]


class FrontierSnapshot(FrozenModel):
    schema_version: Literal["model-skyline/v1alpha1"] = "model-skyline/v1alpha1"
    kind: Literal["frontier"] = "frontier"
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    snapshot_id: str
    config_hash: str
    catalog_hash: str
    engine_version: str
    generated_at: datetime
    frontier_id: str
    workload: WorkloadReference
    order_by: str
    uncertainty: UncertaintyMode
    axes: tuple[AxisDescriptor, AxisDescriptor]
    members: tuple[EvaluatedOffering, ...]
    evaluated: tuple[EvaluatedOffering, ...]
    rejected: tuple[RejectedOffering, ...] = ()
    sources: tuple[SourceReference, ...] = ()
    source_watermarks: dict[str, datetime] = Field(default_factory=dict)

    @field_validator("generated_at")
    @classmethod
    def generated_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("generated_at must include a timezone")
        return value

    @field_validator("source_watermarks")
    @classmethod
    def source_watermarks_have_timezones(cls, value: dict[str, datetime]) -> dict[str, datetime]:
        if any(timestamp.tzinfo is None for timestamp in value.values()):
            raise ValueError("source watermarks must include timezones")
        return value

    @model_validator(mode="after")
    def snapshot_is_coherent(self) -> Self:
        axis_ids = {axis.metric for axis in self.axes}
        if len(axis_ids) != 2:
            raise ValueError("snapshot axes must be distinct")
        if self.order_by not in axis_ids:
            raise ValueError("order_by must reference a snapshot axis")
        evaluated_ids = [item.offering.offering_id for item in self.evaluated]
        member_ids = [item.offering.offering_id for item in self.members]
        rejected_ids = [item.offering_id for item in self.rejected]
        if len(evaluated_ids) != len(set(evaluated_ids)):
            raise ValueError("evaluated offering ids must be unique")
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("frontier member offering ids must be unique")
        if len(rejected_ids) != len(set(rejected_ids)):
            raise ValueError("rejected offering ids must be unique")
        if set(evaluated_ids) & set(rejected_ids):
            raise ValueError("an offering cannot be both evaluated and rejected")
        evaluated_by_id = {item.offering.offering_id: item for item in self.evaluated}
        for item in (*self.evaluated, *self.members):
            if set(item.axes) != axis_ids:
                raise ValueError("every evaluated offering must contain exactly the two axes")
            for axis in self.axes:
                if item.axes[axis.metric].unit != axis.unit:
                    raise ValueError("axis estimate unit does not match its descriptor")
        for member in self.members:
            evaluated = evaluated_by_id.get(member.offering.offering_id)
            if evaluated != member:
                raise ValueError("every member must be the identical evaluated offering")
            if member.dominated_by:
                raise ValueError("a frontier member cannot have dominators")
        expected_members = {
            item.offering.offering_id for item in self.evaluated if not item.dominated_by
        }
        if set(member_ids) != expected_members:
            raise ValueError("members must be exactly the evaluated non-dominated offerings")
        evaluated_id_set = set(evaluated_ids)
        artifact_sources = list(self.sources)
        snapshot_source_ids = {source.id for source in self.sources}
        for item in self.evaluated:
            if item.offering.offering_id in item.dominated_by:
                raise ValueError("an offering cannot dominate itself")
            if not set(item.dominated_by) <= evaluated_id_set:
                raise ValueError("dominance explanations must reference evaluated offerings")
            for estimate in item.axes.values():
                artifact_sources.extend(estimate.sources)
                if any(source not in self.sources for source in estimate.sources):
                    raise ValueError("axis sources must be present in snapshot sources")
        _ensure_consistent_source_ids(artifact_sources, scope="frontier snapshot")
        if not set(self.source_watermarks) <= snapshot_source_ids:
            raise ValueError("source watermarks must reference snapshot sources")
        return self


class ModelChoice(FrozenModel):
    offering: OfferingKey
    axes: dict[str, AxisEstimate]
    metadata: CanonicalJsonObject = Field(default_factory=dict)


class SelectionSnapshot(FrozenModel):
    schema_version: Literal["model-skyline/v1alpha1"] = "model-skyline/v1alpha1"
    kind: Literal["selection"] = "selection"
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    snapshot_id: str
    policy_hash: str
    frontier_snapshot_id: str
    selection_id: str
    frontier_id: str
    workload: WorkloadReference
    strategy: Literal["lexicographic"] = "lexicographic"
    order_by: str
    requested_count: SelectionCandidateCount
    max_per_provider: SelectionCandidateCount | None = None
    on_insufficient: InsufficientCandidates
    generated_at: datetime
    valid_until: datetime
    default: ModelChoice
    fallbacks: tuple[ModelChoice, ...] = ()

    @field_validator("generated_at", "valid_until")
    @classmethod
    def selection_timestamps_have_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("selection timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validity_follows_generation(self) -> Self:
        if self.valid_until <= self.generated_at:
            raise ValueError("valid_until must follow generated_at")
        return self

    @model_validator(mode="after")
    def choices_are_coherent(self) -> Self:
        choices = self.choices
        offering_ids = [choice.offering.offering_id for choice in choices]
        if len(offering_ids) != len(set(offering_ids)):
            raise ValueError("selection choices must have distinct offering ids")
        if len(choices) > self.requested_count:
            raise ValueError("selection contains more choices than requested")
        if (
            self.on_insufficient is InsufficientCandidates.ERROR
            and len(choices) != self.requested_count
        ):
            raise ValueError("strict selection must contain the requested number of choices")
        if self.max_per_provider is not None:
            provider_counts = Counter(choice.offering.provider for choice in choices)
            if any(count > self.max_per_provider for count in provider_counts.values()):
                raise ValueError("selection exceeds max_per_provider")
        if any(self.order_by not in choice.axes for choice in choices):
            raise ValueError("every selection choice must contain the ordering axis")
        _ensure_consistent_source_ids(
            (
                source
                for choice in choices
                for estimate in choice.axes.values()
                for source in estimate.sources
            ),
            scope="selection snapshot",
        )
        return self

    @property
    def choices(self) -> tuple[ModelChoice, ...]:
        return (self.default, *self.fallbacks)


class PublishedFile(FrozenModel):
    """Digest-addressed file reference relative to a publication root."""

    path: RelativeArtifactPath
    sha256: Sha256Digest
    media_type: str = Field(min_length=1, max_length=128)


class PublishedCatalog(FrozenModel):
    workload: WorkloadReference
    catalog_hash: Sha256Digest


class PublicationPolicy(FrozenModel):
    """The operator's explicit redistribution decision for this publication."""

    public: bool = False
    allowed_licenses: tuple[str, ...] = ()
    authorized_source_ids: tuple[str, ...] = ()

    @field_validator("allowed_licenses", "authorized_source_ids", mode="before")
    @classmethod
    def values_are_sorted_unique(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise ValueError("publication policy lists must contain non-empty strings")
        if len(value) != len(set(value)):
            raise ValueError("publication policy lists must not contain duplicates")
        return tuple(sorted(value))


class FrontierHistoryEntry(FrozenModel):
    snapshot_id: Sha256Digest
    generated_at: datetime
    workload: WorkloadReference
    config_hash: Sha256Digest
    catalog_hash: Sha256Digest
    axis_hash: Sha256Digest
    view_hash: Sha256Digest
    snapshot: PublishedFile

    @field_validator("generated_at")
    @classmethod
    def generated_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("generated_at must include a timezone")
        return value


class FrontierHistory(FrozenModel):
    schema_version: Literal["model-skyline/v1alpha1"] = "model-skyline/v1alpha1"
    kind: Literal["frontier-history"] = "frontier-history"
    frontier_id: PortablePublicationId
    entries: tuple[FrontierHistoryEntry, ...]

    @model_validator(mode="after")
    def entries_are_newest_first_and_unique(self) -> Self:
        ids = [entry.snapshot_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("frontier history snapshot ids must be unique")
        order = [(entry.generated_at, entry.snapshot_id) for entry in self.entries]
        if order != sorted(order, reverse=True):
            raise ValueError("frontier history entries must be newest first")
        for entry in self.entries:
            expected = f"frontiers/{self.frontier_id}/{entry.snapshot_id}.json"
            if entry.snapshot.path != expected:
                raise ValueError(f"history snapshot path must be {expected!r}")
        return self


class PublishedFrontier(FrozenModel):
    """Immutable files for one frontier in a committed publication set.

    Conventional ``latest.json``, ``table.csv``, ``table.txt``, ``history.json``,
    and feed paths are mutable discovery aliases and deliberately do not appear
    in a historical manifest.
    """

    frontier_id: PortablePublicationId
    snapshot_id: Sha256Digest
    snapshot: PublishedFile
    csv: PublishedFile
    table: PublishedFile
    history: PublishedFile
    feed: PublishedFile

    @model_validator(mode="after")
    def files_match_identity(self) -> Self:
        expected = {
            "snapshot": f"frontiers/{self.frontier_id}/{self.snapshot_id}.json",
            "csv": f"frontiers/{self.frontier_id}/{self.snapshot_id}.csv",
            "table": f"frontiers/{self.frontier_id}/{self.snapshot_id}.txt",
            "history": (f"frontiers/{self.frontier_id}/history-{self.history.sha256}.json"),
            "feed": f"feeds/{self.frontier_id}/{self.feed.sha256}.xml",
        }
        for field_name, expected_path in expected.items():
            if getattr(self, field_name).path != expected_path:
                raise ValueError(f"{field_name} path must be {expected_path!r}")
        return self


class PublishedSelection(FrozenModel):
    """Immutable file for one agent route in a committed publication set."""

    selection_id: PortablePublicationId
    snapshot_id: Sha256Digest
    frontier_id: PortablePublicationId
    frontier_snapshot_id: Sha256Digest
    snapshot: PublishedFile

    @model_validator(mode="after")
    def files_match_identity(self) -> Self:
        expected_snapshot = f"selections/{self.selection_id}/{self.snapshot_id}.json"
        if self.snapshot.path != expected_snapshot:
            raise ValueError(f"snapshot path must be {expected_snapshot!r}")
        return self


class PublicationManifest(FrozenModel):
    """Commit marker for one internally consistent multi-artifact publication."""

    schema_version: Literal["model-skyline/v1alpha1"] = "model-skyline/v1alpha1"
    kind: Literal["publication"] = "publication"
    hash_algorithm: Literal["sha256-rfc8785-v1"] = "sha256-rfc8785-v1"
    publication_id: Sha256Digest
    project_id: PortablePublicationId
    previous_publication_id: Sha256Digest | None = None
    project_hash: Sha256Digest
    generated_at: datetime
    catalogs: tuple[PublishedCatalog, ...] = Field(min_length=1)
    policy: PublicationPolicy = Field(default_factory=PublicationPolicy)
    frontiers: tuple[PublishedFrontier, ...] = Field(min_length=1)
    selections: tuple[PublishedSelection, ...] = ()

    @field_validator("generated_at")
    @classmethod
    def generated_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("generated_at must include a timezone")
        return value

    @model_validator(mode="after")
    def entries_are_coherent(self) -> Self:
        catalog_workloads = [entry.workload.id for entry in self.catalogs]
        frontier_ids = [entry.frontier_id for entry in self.frontiers]
        selection_ids = [entry.selection_id for entry in self.selections]
        if len(catalog_workloads) != len(set(catalog_workloads)):
            raise ValueError("published catalog workload ids must be unique")
        if len(frontier_ids) != len(set(frontier_ids)):
            raise ValueError("published frontier ids must be unique")
        if len(selection_ids) != len(set(selection_ids)):
            raise ValueError("published selection ids must be unique")
        frontier_snapshots = {entry.frontier_id: entry.snapshot_id for entry in self.frontiers}
        for selection in self.selections:
            expected_snapshot = frontier_snapshots.get(selection.frontier_id)
            if expected_snapshot is None:
                raise ValueError("published selections must reference a published frontier")
            if selection.frontier_snapshot_id != expected_snapshot:
                raise ValueError(
                    "published selection must reference the current published frontier snapshot"
                )
        return self
