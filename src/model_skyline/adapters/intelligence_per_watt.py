"""Import reviewed Intelligence Per Watt accuracy artifacts.

The upstream artifact may contain prompts, answers, and model responses.  This
adapter deliberately projects only aggregate accuracy and telemetry statistics
into an :class:`ObservationCatalog`; callers must keep the raw artifact private.
An explicit binding supplies the complete local offering and workload identity
that the upstream aggregate alone cannot prove.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Final, Literal, Self

from pydantic import Field, field_validator, model_validator

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, content_hash
from model_skyline.local_measurements import (
    LocalArtifactIdentity,
    LocalHardwareIdentity,
    LocalRuntimeIdentity,
    local_system_offering_key,
)
from model_skyline.models import (
    CanonicalJsonObject,
    FrozenModel,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    PositiveSafeCount,
    Sha256Digest,
    SourceReference,
    WorkloadReference,
)

IPW_IMPORT_SCHEMA_VERSION: Final = "model-skyline/ipw-accuracy-import/v1alpha1"
IPW_ADAPTER_ID: Final = "model-skyline/intelligence-per-watt-accuracy"
IPW_ADAPTER_VERSION: Final = "1"
IPW_REVIEWED_REVISION: Final = "645f68a65f5bb9b0296bc2840474ef86744af3c2"
IPW_REPOSITORY_URL: Final = "https://github.com/HazyResearch/intelligence-per-watt"
MAX_IPW_ARTIFACT_BYTES: Final = 64_000_000


class IntelligencePerWattAdapterError(ValueError):
    """An upstream artifact or its exact local binding is not publishable."""


class IntelligencePerWattRunIdentity(FrozenModel):
    dataset_id: str = Field(min_length=1, max_length=512)
    dataset_revision: str = Field(min_length=1, max_length=512)
    task_manifest_sha256: Sha256Digest
    task_set_kind: Literal["full", "screen"]
    attempts_per_task: PositiveSafeCount = 1
    concurrency: PositiveSafeCount = 1
    configuration_sha256: Sha256Digest


def intelligence_per_watt_workload_version(run: IntelligencePerWattRunIdentity) -> str:
    """Bind the public workload identity to every comparability-critical run field."""

    return f"ipw-run-sha256:{content_hash(run.model_dump(mode='json'))}"


class IntelligencePerWattImportBinding(FrozenModel):
    schema_version: Literal["model-skyline/ipw-accuracy-import/v1alpha1"]
    upstream_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    expected_model: str = Field(min_length=1, max_length=512)
    expected_energy_basis: Literal["gpu", "soc"]
    observed_at: datetime
    workload_id: str = Field(min_length=1, max_length=512)
    workload_unit: str = Field(min_length=1, max_length=512)
    run: IntelligencePerWattRunIdentity
    hardware: LocalHardwareIdentity
    artifact: LocalArtifactIdentity
    runtime: LocalRuntimeIdentity
    offering: OfferingKey
    metadata: CanonicalJsonObject = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def reviewed_exact_local_binding(self) -> Self:
        if self.upstream_revision != IPW_REVIEWED_REVISION:
            raise ValueError("upstream_revision is not the reviewed Intelligence Per Watt revision")
        offering = self.offering
        if not offering.provider.startswith("local:"):
            raise ValueError("Intelligence Per Watt bindings require a local provider")
        if offering.billing_mode != "owned_hardware":
            raise ValueError("Intelligence Per Watt bindings require owned_hardware billing")
        if offering.quantization is None:
            raise ValueError("Intelligence Per Watt bindings require an exact quantization")
        if offering.service_tier is None:
            raise ValueError("Intelligence Per Watt bindings require an exact power service tier")
        if "text" not in offering.capabilities:
            raise ValueError("Intelligence Per Watt bindings require the text capability")
        if self.hardware.manufacturer.casefold() == "apple" and self.expected_energy_basis != "soc":
            raise ValueError("Apple Silicon Intelligence Per Watt bindings require the soc basis")
        expected_offering = local_system_offering_key(
            hardware=self.hardware,
            artifact=self.artifact,
            runtime=self.runtime,
            capabilities=offering.capabilities,
        )
        if offering != expected_offering:
            raise ValueError(
                "offering does not match the exact hardware, artifact, runtime, and capabilities"
            )
        return self


def _object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise IntelligencePerWattAdapterError(f"{field} must be an object")
    return value


def _array(value: object, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise IntelligencePerWattAdapterError(f"{field} must be an array")
    return value


def _count(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**53 - 1:
        raise IntelligencePerWattAdapterError(f"{field} must be a non-negative safe integer")
    return value


def _decimal(
    value: object,
    *,
    field: str,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise IntelligencePerWattAdapterError(f"{field} must be a finite number")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise IntelligencePerWattAdapterError(f"{field} must be a finite number") from exc
    if not result.is_finite():
        raise IntelligencePerWattAdapterError(f"{field} must be a finite number")
    if minimum is not None and result < minimum:
        raise IntelligencePerWattAdapterError(f"{field} is below its accepted minimum")
    if maximum is not None and result > maximum:
        raise IntelligencePerWattAdapterError(f"{field} exceeds its accepted maximum")
    return result


def _positive_decimal(value: object, *, field: str) -> Decimal:
    result = _decimal(value, field=field, minimum=Decimal(0))
    if result == 0:
        raise IntelligencePerWattAdapterError(f"{field} must be positive")
    return result


def _stats(
    value: object,
    *,
    field: str,
    expected_count: int,
) -> tuple[Decimal, Decimal, Decimal]:
    stats = _object(value, field=field)
    count = _count(stats.get("count"), field=f"{field}.count")
    if count != expected_count:
        raise IntelligencePerWattAdapterError(f"{field}.count does not cover every scored item")
    average = _positive_decimal(stats.get("avg"), field=f"{field}.avg")
    minimum = _positive_decimal(stats.get("min"), field=f"{field}.min")
    maximum = _positive_decimal(stats.get("max"), field=f"{field}.max")
    if not minimum <= average <= maximum:
        raise IntelligencePerWattAdapterError(f"{field} statistics are not ordered")
    return average, minimum, maximum


def _close(left: Decimal, right: Decimal) -> bool:
    with localcontext(POLICY_DECIMAL_CONTEXT):
        scale = max(abs(left), abs(right), Decimal(1))
        return abs(left - right) <= scale * Decimal("0.000000001")


def _read_json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_IPW_ARTIFACT_BYTES:
        raise IntelligencePerWattAdapterError(
            f"{label} exceeds the {MAX_IPW_ARTIFACT_BYTES}-byte input limit"
        )

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise IntelligencePerWattAdapterError(f"{label} contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        decoded = json.loads(
            raw,
            parse_float=Decimal,
            parse_int=int,
            object_pairs_hook=unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntelligencePerWattAdapterError(f"{label} is not valid UTF-8 JSON") from exc
    return _object(decoded, field=label)


def _read_regular_file(path: str | Path, *, label: str) -> bytes:
    """Read one stable, bounded regular file without following a final symlink."""

    source = Path(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise IntelligencePerWattAdapterError(f"cannot open {label} {source}: {exc}") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise IntelligencePerWattAdapterError(f"{label} must be a regular file")
            if before.st_size > MAX_IPW_ARTIFACT_BYTES:
                raise IntelligencePerWattAdapterError(
                    f"{label} exceeds the {MAX_IPW_ARTIFACT_BYTES}-byte input limit"
                )
            raw = handle.read(MAX_IPW_ARTIFACT_BYTES + 1)
            after = os.fstat(handle.fileno())
    except IntelligencePerWattAdapterError:
        raise
    except OSError as exc:
        raise IntelligencePerWattAdapterError(f"cannot read {label} {source}: {exc}") from exc

    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or len(raw) != before.st_size:
        raise IntelligencePerWattAdapterError(f"{label} changed while it was being read")
    if len(raw) > MAX_IPW_ARTIFACT_BYTES:
        raise IntelligencePerWattAdapterError(
            f"{label} exceeds the {MAX_IPW_ARTIFACT_BYTES}-byte input limit"
        )
    return raw


def load_intelligence_per_watt_binding(
    path: str | Path,
) -> IntelligencePerWattImportBinding:
    return IntelligencePerWattImportBinding.model_validate(
        _read_json_bytes(_read_regular_file(path, label="binding"), label="binding")
    )


def normalize_intelligence_per_watt_accuracy_bytes(
    raw: bytes,
    *,
    binding: IntelligencePerWattImportBinding,
    retrieved_at: datetime,
) -> ObservationCatalog:
    """Project a private upstream ``analysis/accuracy.json`` to a safe catalog."""

    if retrieved_at.tzinfo is None:
        raise IntelligencePerWattAdapterError("retrieved_at must include a timezone")
    retrieved_at = retrieved_at.astimezone(UTC)
    if binding.observed_at > retrieved_at:
        raise IntelligencePerWattAdapterError("observed_at cannot be later than retrieved_at")

    root = _read_json_bytes(raw, label="accuracy artifact")
    if root.get("analysis") != "accuracy":
        raise IntelligencePerWattAdapterError("artifact is not an IPW accuracy analysis")
    summary = _object(root.get("summary"), field="summary")
    model = summary.get("model")
    if model != binding.expected_model:
        raise IntelligencePerWattAdapterError("summary model does not match the exact binding")

    correct = _count(summary.get("correct"), field="summary.correct")
    incorrect = _count(summary.get("incorrect"), field="summary.incorrect")
    total_scored = _count(summary.get("total_scored"), field="summary.total_scored")
    if total_scored == 0 or total_scored != correct + incorrect:
        raise IntelligencePerWattAdapterError("summary scored counts are inconsistent")
    for name in ("unevaluated", "failed", "skipped_empty_responses"):
        if _count(summary.get(name), field=f"summary.{name}") != 0:
            raise IntelligencePerWattAdapterError(
                "accuracy evidence cannot exclude unevaluated, failed, or empty responses"
            )

    accuracy = _decimal(
        summary.get("accuracy"),
        field="summary.accuracy",
        minimum=Decimal(0),
        maximum=Decimal(1),
    )
    with localcontext(POLICY_DECIMAL_CONTEXT):
        expected_accuracy = Decimal(correct) / Decimal(total_scored)
    if not _close(accuracy, expected_accuracy):
        raise IntelligencePerWattAdapterError("summary accuracy does not match scored counts")

    basis = summary.get("energy_basis")
    if basis != binding.expected_energy_basis:
        raise IntelligencePerWattAdapterError("energy basis does not match the exact binding")
    energy_samples = _count(summary.get("energy_sample_count"), field="summary.energy_sample_count")
    power_samples = _count(summary.get("power_sample_count"), field="summary.power_sample_count")
    if energy_samples != total_scored or power_samples != total_scored:
        raise IntelligencePerWattAdapterError("energy and power must cover every scored item")

    coverage = _object(summary.get("telemetry_coverage"), field="summary.telemetry_coverage")
    if _count(
        coverage.get("records_energy_short_of_power_x_time"),
        field="summary.telemetry_coverage.records_energy_short_of_power_x_time",
    ):
        raise IntelligencePerWattAdapterError("telemetry contains truncated energy windows")

    data = _object(root.get("data"), field="data")
    records_by_model = _object(data.get("records"), field="data.records")
    records = _array(
        records_by_model.get(binding.expected_model),
        field="data.records[model]",
    )
    if len(records) != total_scored:
        raise IntelligencePerWattAdapterError("private record count does not match scored count")
    efficiency_by_model = _object(data.get("efficiency"), field="data.efficiency")
    efficiency = _object(
        efficiency_by_model.get(binding.expected_model),
        field="data.efficiency[model]",
    )
    energy_stats = _object(efficiency.get("energy"), field="data.efficiency[model].energy")
    power_stats = _object(efficiency.get("power"), field="data.efficiency[model].power")
    if _count(
        energy_stats.get("imputed_count"),
        field="data.efficiency[model].energy.imputed_count",
    ):
        raise IntelligencePerWattAdapterError("imputed energy is not measured energy evidence")
    if _count(
        energy_stats.get("zero_values"),
        field="data.efficiency[model].energy.zero_values",
    ) or _count(
        power_stats.get("zero_values"),
        field="data.efficiency[model].power.zero_values",
    ):
        raise IntelligencePerWattAdapterError("non-positive telemetry values are not admissible")

    energy_avg, energy_min, energy_max = _stats(
        energy_stats,
        field="data.efficiency[model].energy",
        expected_count=total_scored,
    )
    power_avg, power_min, power_max = _stats(
        power_stats,
        field="data.efficiency[model].power",
        expected_count=total_scored,
    )
    energy_accuracy = _decimal(
        energy_stats.get("accuracy"),
        field="data.efficiency[model].energy.accuracy",
        minimum=Decimal(0),
        maximum=Decimal(1),
    )
    power_accuracy = _decimal(
        power_stats.get("accuracy"),
        field="data.efficiency[model].power.accuracy",
        minimum=Decimal(0),
        maximum=Decimal(1),
    )
    if not _close(energy_accuracy, expected_accuracy) or not _close(
        power_accuracy, expected_accuracy
    ):
        raise IntelligencePerWattAdapterError("telemetry-covered accuracy is inconsistent")
    derived_power_samples = _count(
        power_stats.get("derived_power_samples"),
        field="data.efficiency[model].power.derived_power_samples",
    )
    power_metric_samples = _count(
        power_stats.get("power_metric_samples"),
        field="data.efficiency[model].power.power_metric_samples",
    )
    if derived_power_samples + power_metric_samples != total_scored:
        raise IntelligencePerWattAdapterError("power provenance does not cover every scored item")
    energy_total = _positive_decimal(
        energy_stats.get("total"), field="data.efficiency[model].energy.total"
    )
    if not _close(energy_total, energy_avg * total_scored):
        raise IntelligencePerWattAdapterError("energy total does not match its average and count")
    summary_energy = _positive_decimal(
        summary.get("avg_per_query_energy_joules"),
        field="summary.avg_per_query_energy_joules",
    )
    summary_power = _positive_decimal(
        summary.get("avg_per_query_power_watts"),
        field="summary.avg_per_query_power_watts",
    )
    if not _close(summary_energy, energy_avg) or not _close(summary_power, power_avg):
        raise IntelligencePerWattAdapterError("summary and detailed telemetry statistics disagree")

    with localcontext(POLICY_DECIMAL_CONTEXT):
        accuracy_percent = expected_accuracy * Decimal(100)
        intelligence_per_joule = expected_accuracy / energy_avg
        intelligence_per_watt = expected_accuracy / power_avg
        ipj_lower = expected_accuracy / energy_max
        ipj_upper = expected_accuracy / energy_min
        ipw_lower = expected_accuracy / power_max
        ipw_upper = expected_accuracy / power_min

    reported_ipj_value = summary.get("intelligence_per_joule")
    reported_ipw_value = summary.get("intelligence_per_watt")
    if expected_accuracy == 0:
        reported_ipj_value = 0 if reported_ipj_value is None else reported_ipj_value
        reported_ipw_value = 0 if reported_ipw_value is None else reported_ipw_value
    reported_ipj = _decimal(
        reported_ipj_value,
        field="summary.intelligence_per_joule",
        minimum=Decimal(0),
    )
    reported_ipw = _decimal(
        reported_ipw_value,
        field="summary.intelligence_per_watt",
        minimum=Decimal(0),
    )
    if not _close(reported_ipj, intelligence_per_joule) or not _close(
        reported_ipw, intelligence_per_watt
    ):
        raise IntelligencePerWattAdapterError("reported IPJ/IPW does not replay from raw axes")
    detailed_ipj_value = efficiency.get("intelligence_per_joule")
    detailed_ipw_value = efficiency.get("intelligence_per_watt")
    if expected_accuracy == 0:
        detailed_ipj_value = 0 if detailed_ipj_value is None else detailed_ipj_value
        detailed_ipw_value = 0 if detailed_ipw_value is None else detailed_ipw_value
    detailed_ipj = _decimal(
        detailed_ipj_value,
        field="data.efficiency[model].intelligence_per_joule",
        minimum=Decimal(0),
    )
    detailed_ipw = _decimal(
        detailed_ipw_value,
        field="data.efficiency[model].intelligence_per_watt",
        minimum=Decimal(0),
    )
    if not _close(detailed_ipj, intelligence_per_joule) or not _close(
        detailed_ipw, intelligence_per_watt
    ):
        raise IntelligencePerWattAdapterError("detailed IPJ/IPW does not replay from raw axes")

    raw_sha256 = hashlib.sha256(raw).hexdigest()
    source = SourceReference(
        id=f"intelligence-per-watt:{binding.upstream_revision}:{raw_sha256[:24]}",
        version=f"{IPW_ADAPTER_ID}@{IPW_ADAPTER_VERSION}",
        url=f"{IPW_REPOSITORY_URL}/tree/{binding.upstream_revision}",
        license="Apache-2.0 code; operator-owned derived run statistics",
        methodology=(
            "Aggregate projection of the official Intelligence Per Watt accuracy analysis. "
            "The raw artifact remains private because it may contain prompts and model answers. "
            "Only complete scored cohorts with measured, non-imputed, non-truncated telemetry "
            "and one explicit energy basis are admitted."
        ),
        raw_sha256=raw_sha256,
        retrieved_at=retrieved_at,
    )

    def observation(value: Decimal, *, unit: str) -> Observation:
        return Observation(
            value=value,
            unit=unit,
            sample_count=total_scored,
            observed_at=binding.observed_at,
            source=source,
        )

    signals = {
        "ipw_accuracy_percent": observation(accuracy_percent, unit="percent"),
        "ipw_average_energy_joules_per_item": observation(
            energy_avg,
            unit="J/item",
        ),
        "ipw_average_power_watts": observation(
            power_avg,
            unit="W",
        ),
        "ipw_intelligence_per_joule": observation(
            intelligence_per_joule,
            unit="accuracy/J",
        ),
        "ipw_intelligence_per_watt": observation(
            intelligence_per_watt,
            unit="accuracy/W",
        ),
    }
    metadata = {
        "adapter": {
            "id": IPW_ADAPTER_ID,
            "version": IPW_ADAPTER_VERSION,
            "upstream_revision": binding.upstream_revision,
        },
        "run": binding.run.model_dump(mode="json"),
        "hardware": binding.hardware.model_dump(mode="json"),
        "artifact": binding.artifact.model_dump(mode="json"),
        "runtime": binding.runtime.model_dump(mode="json"),
        "energy_basis": basis,
        "telemetry_sample_ranges": {
            "energy_joules_per_item": {"minimum": energy_min, "maximum": energy_max},
            "power_watts": {"minimum": power_min, "maximum": power_max},
            "intelligence_per_joule": {"minimum": ipj_lower, "maximum": ipj_upper},
            "intelligence_per_watt": {"minimum": ipw_lower, "maximum": ipw_upper},
        },
        "telemetry_coverage": coverage,
        "raw_artifact_publication_safe": False,
        "binding_metadata": binding.metadata,
    }
    return ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=WorkloadReference(
            id=binding.workload_id,
            version=intelligence_per_watt_workload_version(binding.run),
            unit=binding.workload_unit,
        ),
        offerings=(
            OfferingObservation(
                offering=binding.offering,
                signals=signals,
                metadata=metadata,
                default_source=source,
            ),
        ),
    )


def normalize_intelligence_per_watt_accuracy_file(
    path: str | Path,
    *,
    binding: IntelligencePerWattImportBinding,
    retrieved_at: datetime,
) -> ObservationCatalog:
    return normalize_intelligence_per_watt_accuracy_bytes(
        _read_regular_file(path, label="accuracy artifact"),
        binding=binding,
        retrieved_at=retrieved_at,
    )


__all__ = [
    "IPW_ADAPTER_ID",
    "IPW_ADAPTER_VERSION",
    "IPW_IMPORT_SCHEMA_VERSION",
    "IPW_REVIEWED_REVISION",
    "IntelligencePerWattAdapterError",
    "IntelligencePerWattImportBinding",
    "IntelligencePerWattRunIdentity",
    "intelligence_per_watt_workload_version",
    "load_intelligence_per_watt_binding",
    "normalize_intelligence_per_watt_accuracy_bytes",
    "normalize_intelligence_per_watt_accuracy_file",
]
