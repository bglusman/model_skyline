"""Reproducible local-runtime measurement records and catalog projection.

The record keeps the hardware, exact model artifact, runtime/configuration,
workload position, run conditions, and raw-result provenance separate.  This
prevents a fast result on one quantization or cache profile from being silently
attached to another.  Model quality is deliberately absent: reviewed quality
evidence joins through ModelSkyline's exact OfferingKey reconciliation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from statistics import median
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, content_hash
from model_skyline.models import (
    MAX_CAPABILITIES,
    CanonicalDecimal,
    CanonicalJsonObject,
    CapabilityName,
    FrozenModel,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    PositiveSafeCount,
    PublicSourceUrl,
    RelativeArtifactPath,
    SafeCount,
    Sha256Digest,
    SourceReference,
    WorkloadReference,
)

LOCAL_MEASUREMENT_SCHEMA_VERSION = "model-skyline/local-measurement/v1alpha1"
MAX_LOCAL_REPETITIONS = 128

Identifier = Annotated[str, Field(min_length=1, max_length=512)]
ShortText = Annotated[str, Field(min_length=1, max_length=2_048)]
MetricSamples = Annotated[tuple[CanonicalDecimal, ...], Field(min_length=1, max_length=128)]
TokenCountSamples = Annotated[
    tuple[SafeCount, ...], Field(min_length=1, max_length=MAX_LOCAL_REPETITIONS)
]
CpuCoreGroups = Annotated[dict[Identifier, PositiveSafeCount], Field(min_length=1, max_length=16)]
Capabilities = Annotated[tuple[CapabilityName, ...], Field(max_length=MAX_CAPABILITIES)]


class LocalEvidenceStatus(StrEnum):
    provisional = "provisional"
    reviewed = "reviewed"


class LocalBenchmarkKind(StrEnum):
    short_decode = "short_decode"
    prompt_processing = "prompt_processing"
    long_context_retrieval = "long_context_retrieval"
    agent_integration = "agent_integration"
    prefix_cache = "prefix_cache"
    cold_start = "cold_start"


class LocalRunnerState(StrEnum):
    warm = "warm"
    cold_model_load = "cold_model_load"
    post_idle_expiry = "post_idle_expiry"


class LocalPrefixCacheState(StrEnum):
    disabled = "disabled"
    miss = "miss"
    warm = "warm"
    mixed = "mixed"


class LocalMetricName(StrEnum):
    prompt_tokens_per_second = "prompt_tokens_per_second"
    decode_tokens_per_second = "decode_tokens_per_second"
    time_to_first_token_seconds = "time_to_first_token_seconds"
    time_to_first_semantic_event_seconds = "time_to_first_semantic_event_seconds"
    end_to_end_seconds = "end_to_end_seconds"
    cold_load_seconds = "cold_load_seconds"
    peak_process_rss_bytes = "peak_process_rss_bytes"
    peak_process_physical_footprint_bytes = "peak_process_physical_footprint_bytes"
    peak_metal_active_bytes = "peak_metal_active_bytes"
    swap_delta_bytes = "swap_delta_bytes"
    speculative_acceptance_percent = "speculative_acceptance_percent"
    prefix_cache_hit_tokens = "prefix_cache_hit_tokens"


_METRIC_UNITS = {
    LocalMetricName.prompt_tokens_per_second: "token/s",
    LocalMetricName.decode_tokens_per_second: "token/s",
    LocalMetricName.time_to_first_token_seconds: "s",
    LocalMetricName.time_to_first_semantic_event_seconds: "s",
    LocalMetricName.end_to_end_seconds: "s",
    LocalMetricName.cold_load_seconds: "s",
    LocalMetricName.peak_process_rss_bytes: "byte",
    LocalMetricName.peak_process_physical_footprint_bytes: "byte",
    LocalMetricName.peak_metal_active_bytes: "byte",
    LocalMetricName.swap_delta_bytes: "byte",
    LocalMetricName.speculative_acceptance_percent: "percent",
    LocalMetricName.prefix_cache_hit_tokens: "token",
}


class LocalHardwareIdentity(FrozenModel):
    hardware_id: Identifier
    manufacturer: Identifier
    machine_model: Identifier
    chip: Identifier
    architecture: Identifier
    memory_bytes: PositiveSafeCount
    cpu_cores: PositiveSafeCount
    cpu_core_groups: CpuCoreGroups
    gpu_cores: PositiveSafeCount
    os_name: Identifier
    os_version: Identifier
    power_source: Literal["ac", "battery", "unknown"]
    power_mode: Identifier
    negotiated_adapter_watts: SafeCount | None = None
    metadata: CanonicalJsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def core_breakdown_is_consistent(self) -> Self:
        if sum(self.cpu_core_groups.values()) != self.cpu_cores:
            raise ValueError("named CPU core groups must sum to cpu_cores")
        return self


class LocalArtifactIdentity(FrozenModel):
    model_id: Identifier
    checkpoint: Identifier
    revision: Identifier
    format: Identifier
    quantization: Identifier
    size_bytes: PositiveSafeCount
    content_sha256: Sha256Digest
    source_url: PublicSourceUrl | None = None
    license: ShortText | None = None
    metadata: CanonicalJsonObject = Field(default_factory=dict)


class LocalRuntimeIdentity(FrozenModel):
    runtime_id: Identifier
    version: Identifier | None = None
    commit: Identifier | None = None
    backend: Identifier
    context_capacity_tokens: PositiveSafeCount
    kv_cache: Identifier
    prefix_cache_enabled: bool
    speculative_method: Identifier | None = None
    agent_harness: Identifier | None = None
    configuration: CanonicalJsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def has_immutable_runtime_reference(self) -> Self:
        if self.version is None and self.commit is None:
            raise ValueError("runtime identity requires a version or commit")
        return self


class LocalBenchmarkWorkload(FrozenModel):
    reference: WorkloadReference
    kind: LocalBenchmarkKind
    input_definition_sha256: Sha256Digest
    requested_input_tokens: PositiveSafeCount
    max_output_tokens: PositiveSafeCount
    repetitions: Annotated[int, Field(strict=True, ge=1, le=MAX_LOCAL_REPETITIONS)]
    warmup_repetitions: Annotated[int, Field(strict=True, ge=0, le=MAX_LOCAL_REPETITIONS)] = 0
    concurrency: PositiveSafeCount = 1
    runner_state: LocalRunnerState = LocalRunnerState.warm
    prefix_cache_state: LocalPrefixCacheState = LocalPrefixCacheState.disabled
    position: CanonicalJsonObject = Field(default_factory=dict)


class LocalMetricSeries(FrozenModel):
    unit: Identifier
    values: MetricSamples


class LocalCheckResult(FrozenModel):
    passed: SafeCount
    total: PositiveSafeCount

    @model_validator(mode="after")
    def passed_does_not_exceed_total(self) -> Self:
        if self.passed > self.total:
            raise ValueError("passed checks cannot exceed total checks")
        return self


class LocalIntegrityEvidence(FrozenModel):
    retrieval: LocalCheckResult | None = None
    tool_calls: LocalCheckResult | None = None
    tool_argument_parsing: LocalCheckResult | None = None
    structured_output: LocalCheckResult | None = None


class LocalPerformanceEvidence(FrozenModel):
    actual_input_tokens: PositiveSafeCount
    output_token_counts: TokenCountSamples
    metrics: dict[LocalMetricName, LocalMetricSeries] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def metric_units_and_ranges_are_valid(self) -> Self:
        for name, series in self.metrics.items():
            expected = _METRIC_UNITS[name]
            if series.unit != expected:
                raise ValueError(f"{name.value} must use unit {expected!r}")
            if name is not LocalMetricName.swap_delta_bytes and any(
                value < 0 for value in series.values
            ):
                raise ValueError(f"{name.value} values cannot be negative")
            if name is LocalMetricName.speculative_acceptance_percent and any(
                value > 100 for value in series.values
            ):
                raise ValueError("speculative acceptance cannot exceed 100 percent")
        return self


class LocalMeasurementProvenance(FrozenModel):
    tool: Identifier
    tool_version: Identifier
    command_sha256: Sha256Digest
    raw_artifact_path: RelativeArtifactPath
    raw_sha256: Sha256Digest
    captured_at: datetime
    methodology: ShortText
    source_url: PublicSourceUrl | None = None
    license: ShortText | None = None

    @field_validator("captured_at")
    @classmethod
    def captured_at_is_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("captured_at must include a timezone")
        return value.astimezone(UTC)


class LocalMeasurementRecord(FrozenModel):
    schema_version: Literal["model-skyline/local-measurement/v1alpha1"]
    measurement_id: Identifier
    status: LocalEvidenceStatus
    started_at: datetime
    completed_at: datetime
    hardware: LocalHardwareIdentity
    artifact: LocalArtifactIdentity
    runtime: LocalRuntimeIdentity
    workload: LocalBenchmarkWorkload
    performance: LocalPerformanceEvidence | None = None
    integrity: LocalIntegrityEvidence | None = None
    capabilities: Capabilities = ()
    provenance: LocalMeasurementProvenance
    notes: ShortText | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def timestamps_are_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("measurement timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("capabilities", mode="before")
    @classmethod
    def canonical_capabilities(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("capabilities must be an array of strings")
        if any(not isinstance(item, str) for item in value):
            raise ValueError("capabilities must contain only strings")
        if len(value) != len(set(value)):
            raise ValueError("capabilities must not contain duplicates")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def evidence_and_workload_are_consistent(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.performance is None and self.integrity is None:
            raise ValueError("a local measurement requires performance or integrity evidence")
        if self.performance is not None:
            if self.performance.actual_input_tokens > self.runtime.context_capacity_tokens:
                raise ValueError("actual input tokens exceed runtime context capacity")
            if len(self.performance.output_token_counts) != self.workload.repetitions:
                raise ValueError("output token counts must match workload repetitions")
            if any(
                count > self.workload.max_output_tokens
                for count in self.performance.output_token_counts
            ):
                raise ValueError("actual output tokens exceed the requested maximum")
            for series in self.performance.metrics.values():
                if len(series.values) != self.workload.repetitions:
                    raise ValueError("every metric series must match workload repetitions")
        checks_by_capability = {
            "tools": None if self.integrity is None else self.integrity.tool_calls,
            "structured-output": (
                None if self.integrity is None else self.integrity.structured_output
            ),
            "long-context": None if self.integrity is None else self.integrity.retrieval,
        }
        for capability, checks in checks_by_capability.items():
            if checks is not None and capability not in self.capabilities:
                raise ValueError(f"integrity evidence for {capability!r} requires that capability")
        return self


def local_offering_key(record: LocalMeasurementRecord) -> OfferingKey:
    """Build the complete route key for one exact local system profile."""

    identity = content_hash(
        {
            "hardware": record.hardware.model_dump(mode="json", exclude={"metadata"}),
            "artifact": record.artifact.model_dump(
                mode="json", exclude={"source_url", "license", "metadata"}
            ),
            "runtime": record.runtime.model_dump(mode="json"),
            "capabilities": list(record.capabilities),
        }
    )
    readable = (
        f"local/{record.hardware.hardware_id}/{record.artifact.model_id}"
        f"@{record.artifact.format}-{record.artifact.quantization}-"
        f"{record.runtime.runtime_id}-{identity[:16]}"
    )
    return OfferingKey(
        offering_id=readable,
        model_id=record.artifact.model_id,
        provider=f"local:{record.hardware.hardware_id}",
        endpoint=None,
        billing_mode="owned_hardware",
        region="local",
        service_tier=record.hardware.power_mode,
        quantization=record.artifact.quantization,
        reasoning_effort=None,
        agent_harness=record.runtime.agent_harness,
        capabilities=record.capabilities,
    )


def _source(record: LocalMeasurementRecord) -> SourceReference:
    provenance = record.provenance
    return SourceReference(
        id=f"local-measurement:{record.measurement_id}",
        version=f"{provenance.tool}@{provenance.tool_version}",
        url=provenance.source_url,
        license=provenance.license,
        methodology=provenance.methodology,
        raw_sha256=provenance.raw_sha256,
        retrieved_at=provenance.captured_at,
    )


def _observation(
    values: tuple[Decimal, ...],
    *,
    unit: str,
    record: LocalMeasurementRecord,
    source: SourceReference,
) -> Observation:
    return Observation(
        value=median(values),
        lower=min(values),
        upper=max(values),
        unit=unit,
        sample_count=len(values),
        observed_at=record.completed_at,
        source=source,
    )


def _percent(checks: LocalCheckResult) -> Decimal:
    # Never inherit a caller's mutable process-wide Decimal context.  Local
    # projections use the same fixed arithmetic policy as frontier evaluation.
    with localcontext(POLICY_DECIMAL_CONTEXT):
        return Decimal(100) * Decimal(checks.passed) / Decimal(checks.total)


_NON_COMPARISON_POSITION_FIELDS = frozenset(
    {
        # These describe what the response/runtime exposed, not the request.
        "decode_rate_source",
        "finish_reasons",
        "time_to_first_token_source",
        # These describe how an already byte-identical prompt was constructed
        # or counted. The input-definition and user-prompt digests remain exact.
        "construction_input_tokens",
        "token_count_url",
    }
)


def _comparison_position(record: LocalMeasurementRecord) -> CanonicalJsonObject:
    return {
        key: value
        for key, value in record.workload.position.items()
        if key not in _NON_COMPARISON_POSITION_FIELDS
    }


def _is_uncached(record: LocalMeasurementRecord) -> bool:
    state = record.workload.prefix_cache_state
    if state is LocalPrefixCacheState.disabled:
        return True
    if state is not LocalPrefixCacheState.miss:
        return False
    if record.performance is None:
        return False
    cache_hits = record.performance.metrics.get(LocalMetricName.prefix_cache_hit_tokens)
    return cache_hits is not None and all(value == 0 for value in cache_hits.values)


def build_local_catalog(
    records: Iterable[LocalMeasurementRecord],
    *,
    workload: WorkloadReference | None = None,
    cache_cohort: Literal["exact", "uncached"] = "exact",
) -> ObservationCatalog:
    """Project comparable local records into an ordinary ObservationCatalog."""

    materialized = tuple(records)
    if not materialized:
        raise ValueError("at least one local measurement record is required")
    reference = workload or materialized[0].workload.reference
    if cache_cohort == "uncached" and any(not _is_uncached(record) for record in materialized):
        raise ValueError(
            "the uncached cohort requires cache-disabled or zero-hit cache-miss records"
        )
    if cache_cohort not in {"exact", "uncached"}:
        raise ValueError("cache_cohort must be 'exact' or 'uncached'")
    cache_identity = (
        materialized[0].workload.prefix_cache_state if cache_cohort == "exact" else "uncached"
    )
    workload_identity = (
        None if workload is not None else materialized[0].workload.reference,
        materialized[0].workload.kind,
        materialized[0].workload.input_definition_sha256,
        materialized[0].workload.requested_input_tokens,
        materialized[0].workload.max_output_tokens,
        materialized[0].workload.concurrency,
        materialized[0].workload.runner_state,
        cache_identity,
        _comparison_position(materialized[0]),
    )
    seen_measurements: set[str] = set()
    seen_offerings: set[str] = set()
    offerings: list[OfferingObservation] = []
    for record in materialized:
        candidate_identity = (
            None if workload is not None else record.workload.reference,
            record.workload.kind,
            record.workload.input_definition_sha256,
            record.workload.requested_input_tokens,
            record.workload.max_output_tokens,
            record.workload.concurrency,
            record.workload.runner_state,
            record.workload.prefix_cache_state if cache_cohort == "exact" else "uncached",
            _comparison_position(record),
        )
        if candidate_identity != workload_identity:
            raise ValueError("local records must describe the same workload position")
        if record.measurement_id in seen_measurements:
            raise ValueError(f"duplicate measurement_id {record.measurement_id!r}")
        seen_measurements.add(record.measurement_id)
        offering = local_offering_key(record)
        if offering.offering_id in seen_offerings:
            raise ValueError("one catalog cannot contain duplicate exact local offerings")
        seen_offerings.add(offering.offering_id)
        source = _source(record)
        signals: dict[str, Observation] = {}
        if record.performance is not None:
            for metric_name, series in record.performance.metrics.items():
                signals[f"local_{metric_name.value}"] = _observation(
                    series.values,
                    unit=series.unit,
                    record=record,
                    source=source,
                )
        if record.integrity is not None:
            check_signals = {
                "local_retrieval_success_percent": record.integrity.retrieval,
                "local_tool_call_success_percent": record.integrity.tool_calls,
                "local_tool_argument_parse_success_percent": (
                    record.integrity.tool_argument_parsing
                ),
                "local_structured_output_success_percent": record.integrity.structured_output,
            }
            for signal_name, checks in check_signals.items():
                if checks is not None:
                    signals[signal_name] = Observation(
                        value=_percent(checks),
                        unit="percent",
                        sample_count=checks.total,
                        observed_at=record.completed_at,
                        source=source,
                    )
            retrieval = record.integrity.retrieval
            if (
                retrieval is not None
                and retrieval.passed == retrieval.total
                and record.performance is not None
            ):
                signals["local_validated_context_tokens"] = Observation(
                    value=record.performance.actual_input_tokens,
                    unit="token",
                    sample_count=retrieval.total,
                    observed_at=record.completed_at,
                    source=source,
                )
        offerings.append(
            OfferingObservation(
                offering=offering,
                signals=signals,
                metadata={
                    "local_measurement_id": record.measurement_id,
                    "local_evidence_status": record.status.value,
                    "hardware": record.hardware.model_dump(mode="json"),
                    "artifact": record.artifact.model_dump(mode="json"),
                    "runtime": record.runtime.model_dump(mode="json"),
                    "runtime_identity_sha256": content_hash(record.runtime),
                    "workload": record.workload.model_dump(mode="json"),
                    "local_cache_comparison_cohort": cache_cohort,
                    "raw_artifact_path": record.provenance.raw_artifact_path,
                },
                default_source=source,
            )
        )
    return ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=reference,
        offerings=sorted(offerings, key=lambda item: item.offering.offering_id),
    )


def _capacity_protocol(record: LocalMeasurementRecord) -> tuple[object, ...]:
    position = record.workload.position
    return (
        record.workload.kind,
        record.workload.max_output_tokens,
        record.workload.concurrency,
        record.workload.runner_state,
        position.get("mode"),
        position.get("system_prompt_sha256"),
        position.get("tool_schema_sha256"),
        position.get("tool_count"),
        position.get("tool_choice"),
        position.get("thinking_mode"),
        position.get("sampling"),
        position.get("api"),
        position.get("retrieval_character_fraction"),
    )


def build_local_capacity_catalog(
    records: Iterable[LocalMeasurementRecord],
    *,
    workload: WorkloadReference,
) -> ObservationCatalog:
    """Roll an uncached retrieval ladder up to validated capacity and footprint."""

    materialized = tuple(records)
    if not materialized:
        raise ValueError("at least one local retrieval measurement record is required")
    measurement_ids = [record.measurement_id for record in materialized]
    if len(measurement_ids) != len(set(measurement_ids)):
        raise ValueError("capacity records must have unique measurement_id values")
    if any(
        record.workload.kind is not LocalBenchmarkKind.long_context_retrieval
        for record in materialized
    ):
        raise ValueError("capacity records must be long-context retrieval measurements")
    if any(not _is_uncached(record) for record in materialized):
        raise ValueError(
            "capacity records require cache-disabled or proven zero-hit cache-miss requests"
        )
    protocol = _capacity_protocol(materialized[0])
    if any(_capacity_protocol(record) != protocol for record in materialized[1:]):
        raise ValueError("capacity records must use one retrieval protocol")

    grouped: dict[str, list[LocalMeasurementRecord]] = defaultdict(list)
    for record in materialized:
        grouped[local_offering_key(record).offering_id].append(record)

    offerings: list[OfferingObservation] = []
    for offering_id, candidates in sorted(grouped.items()):
        passing = [
            record
            for record in candidates
            if record.integrity is not None
            and record.integrity.retrieval is not None
            and record.integrity.retrieval.passed == record.integrity.retrieval.total
            and record.performance is not None
        ]
        if not passing:
            continue
        selected = max(
            passing,
            key=lambda record: (
                record.performance.actual_input_tokens if record.performance else 0,
                record.completed_at,
                record.measurement_id,
            ),
        )
        assert selected.performance is not None
        assert selected.integrity is not None
        assert selected.integrity.retrieval is not None
        footprint = selected.performance.metrics.get(
            LocalMetricName.peak_process_physical_footprint_bytes
        )
        if footprint is None:
            raise ValueError(
                f"capacity record {selected.measurement_id!r} is missing sampled physical footprint"
            )
        source = _source(selected)
        signals = {
            "local_validated_context_tokens": Observation(
                value=selected.performance.actual_input_tokens,
                unit="token",
                sample_count=selected.integrity.retrieval.total,
                observed_at=selected.completed_at,
                source=source,
            ),
            "local_peak_process_physical_footprint_bytes": _observation(
                footprint.values,
                unit="byte",
                record=selected,
                source=source,
            ),
        }
        offering = local_offering_key(selected)
        assert offering.offering_id == offering_id
        attempted = sorted(
            (
                {
                    "actual_input_tokens": (
                        record.performance.actual_input_tokens
                        if record.performance is not None
                        else None
                    ),
                    "measurement_id": record.measurement_id,
                    "passed": (
                        record.integrity is not None
                        and record.integrity.retrieval is not None
                        and record.integrity.retrieval.passed == record.integrity.retrieval.total
                    ),
                }
                for record in candidates
            ),
            key=lambda item: (item["actual_input_tokens"] or 0, item["measurement_id"]),
        )
        offerings.append(
            OfferingObservation(
                offering=offering,
                signals=signals,
                metadata={
                    "local_measurement_id": selected.measurement_id,
                    "local_evidence_status": selected.status.value,
                    "hardware": selected.hardware.model_dump(mode="json"),
                    "artifact": selected.artifact.model_dump(mode="json"),
                    "runtime": selected.runtime.model_dump(mode="json"),
                    "runtime_identity_sha256": content_hash(selected.runtime),
                    "workload": selected.workload.model_dump(mode="json"),
                    "raw_artifact_path": selected.provenance.raw_artifact_path,
                    "capacity_ladder": attempted,
                    "local_cache_comparison_cohort": "uncached",
                },
                default_source=source,
            )
        )
    if not offerings:
        raise ValueError("no offering passed any retrieval position")
    return ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=workload,
        offerings=offerings,
    )
