#!/usr/bin/env python3
"""Build an exact local quality catalog from prompt-free Harbor pilot summaries."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal, localcontext
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, content_hash
from model_skyline.io import dump_json
from model_skyline.local_measurements import (
    LocalArtifactIdentity,
    LocalHardwareIdentity,
    LocalRuntimeIdentity,
    local_system_offering_key,
)
from model_skyline.models import (
    Observation,
    ObservationCatalog,
    OfferingObservation,
    SourceReference,
    WorkloadReference,
)

SUMMARY_SCHEMA = "model-skyline/harbor-local-job-summary/v1"
MEMORY_SCHEMA = "model-skyline/harbor-runner-memory/v1"
MAX_INPUT_BYTES = 64_000_000
TIMING_PHASES = ("environment_setup", "agent_setup", "agent_execution", "verifier")


class PilotCatalogError(ValueError):
    """The supplied pilot evidence cannot produce a trustworthy catalog."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PilotCatalogError(f"input must be a regular file: {path.name}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise PilotCatalogError(f"input exceeds {MAX_INPUT_BYTES} bytes: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PilotCatalogError(f"invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise PilotCatalogError(f"expected a JSON object: {path.name}")
    return value


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise PilotCatalogError(f"{field} must be an object with string keys")
    return value


def _array(value: object, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise PilotCatalogError(f"{field} must be an array")
    return value


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PilotCatalogError(f"{field} must be a non-empty string")
    return value


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PilotCatalogError(f"{field} must be a non-negative integer")
    return value


def _decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise PilotCatalogError(f"{field} must be a decimal string or number")
    try:
        result = Decimal(value)
    except Exception as exc:
        raise PilotCatalogError(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise PilotCatalogError(f"{field} must be a finite decimal")
    return result


def _timestamp(value: object, *, field: str, naive_timezone: ZoneInfo) -> datetime:
    raw = _string(value, field=field)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PilotCatalogError(f"{field} must be an ISO 8601 timestamp") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=naive_timezone)
    return result.astimezone(UTC)


def _load_protocol(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PilotCatalogError("protocol must be a regular file")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PilotCatalogError("invalid pilot protocol YAML") from exc
    protocol = _mapping(value, field="protocol")
    if protocol.get("schema_version") != "model-skyline/local-quality-pilot/v1":
        raise PilotCatalogError("unsupported pilot protocol schema_version")
    return protocol


def _task_digests(protocol_root: Path, task_set: dict[str, Any]) -> dict[str, str]:
    if task_set.get("evidence_tier") != "measured":
        raise PilotCatalogError("quality catalogs require a measured task set")
    tasks = task_set.get("tasks")
    if not isinstance(tasks, list):
        manifest_name = _string(task_set.get("task_manifest"), field="task_set.task_manifest")
        manifest_input = protocol_root / manifest_name
        manifest_path = manifest_input.resolve()
        if (
            not manifest_path.is_relative_to(protocol_root.resolve())
            or manifest_input.is_symlink()
            or not manifest_input.is_file()
            or _sha256(manifest_input) != task_set.get("task_manifest_sha256")
        ):
            raise PilotCatalogError("task manifest does not match its pinned identity")
        tasks = _array(_load_json(manifest_input).get("tasks"), field="task_manifest.tasks")
    result: dict[str, str] = {}
    for index, value in enumerate(tasks):
        task = _mapping(value, field=f"task_set.tasks[{index}]")
        name = _string(task.get("name"), field=f"task_set.tasks[{index}].name")
        digest = _string(task.get("digest"), field=f"task_set.tasks[{index}].digest")
        if name in result or digest in result.values():
            raise PilotCatalogError("task names and digests must be unique")
        result[name] = digest
    if not result:
        raise PilotCatalogError("task set must not be empty")
    return result


def _quantile_cont(values: list[Decimal], quantile: Decimal) -> Decimal:
    """Return the deterministic Hyndman-Fan type-7 continuous quantile."""

    if not values:
        raise PilotCatalogError("a quantile requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    with localcontext(POLICY_DECIMAL_CONTEXT):
        position = Decimal(len(ordered) - 1) * quantile
        lower_index = int(position.to_integral_value(rounding=ROUND_FLOOR))
        fraction = position - Decimal(lower_index)
        return ordered[lower_index] + fraction * (
            ordered[min(lower_index + 1, len(ordered) - 1)] - ordered[lower_index]
        )


def _trial_wall_seconds(trial: dict[str, Any]) -> Decimal:
    timing = _mapping(trial.get("timing_seconds"), field="trial.timing_seconds")
    with localcontext(POLICY_DECIMAL_CONTEXT):
        values = [
            _decimal(timing.get(phase), field=f"trial.timing_seconds.{phase}")
            for phase in TIMING_PHASES
        ]
        if any(value < 0 for value in values):
            raise PilotCatalogError("trial timing values must be non-negative")
        return sum(values, start=Decimal(0))


def _harness_identity(harness: dict[str, Any]) -> str:
    name = _string(harness.get("name"), field="harness.name")
    version = _string(harness.get("version"), field="harness.version")
    harbor_version = _string(harness.get("harbor_version"), field="harness.harbor_version")
    return f"{name}@{version}+harbor@{harbor_version}+harness-sha256:{content_hash(harness)[:16]}"


def _load_profile(
    protocol_root: Path,
    candidate: dict[str, Any],
    *,
    harness_identity: str,
) -> tuple[LocalArtifactIdentity, LocalRuntimeIdentity, tuple[str, ...], str]:
    profile_name = _string(candidate.get("system_profile"), field="candidate.system_profile")
    profile_input = protocol_root / profile_name
    profile_path = profile_input.resolve()
    if not profile_path.is_relative_to(protocol_root.resolve()):
        raise PilotCatalogError("system profile escapes the protocol directory")
    expected_digest = _string(
        candidate.get("system_profile_sha256"), field="candidate.system_profile_sha256"
    )
    if profile_input.is_symlink() or not profile_input.is_file():
        raise PilotCatalogError("system profile must be a regular non-symlink file")
    if _sha256(profile_input) != expected_digest:
        raise PilotCatalogError("system profile does not match its pinned digest")
    profile = _load_json(profile_input)
    if profile.get("schema_version") != "model-skyline/local-system-profile/v1":
        raise PilotCatalogError("unsupported local system profile schema_version")
    route = _string(candidate.get("route"), field="candidate.route")
    if profile.get("served_model") != route:
        raise PilotCatalogError("candidate route and system profile disagree")
    raw_capabilities = _array(profile.get("capabilities"), field="profile.capabilities")
    if any(not isinstance(value, str) or not value for value in raw_capabilities):
        raise PilotCatalogError("profile capabilities must be non-empty strings")
    capabilities = tuple(sorted(raw_capabilities))
    artifact = LocalArtifactIdentity.model_validate(profile.get("artifact"))
    base_runtime = LocalRuntimeIdentity.model_validate(profile.get("runtime"))
    runtime = base_runtime.model_copy(update={"agent_harness": harness_identity})
    return artifact, runtime, capabilities, expected_digest


def _quality_source(
    summary: dict[str, Any], path: Path, *, observed_at: datetime
) -> SourceReference:
    protocol = _mapping(summary.get("protocol"), field="summary.protocol")
    job = _mapping(summary.get("job"), field="summary.job")
    return SourceReference(
        id=(
            f"local-quality-pilot:{_string(protocol.get('candidate'), field='protocol.candidate')}"
            f":{_string(job.get('id'), field='job.id')}"
        ),
        version=SUMMARY_SCHEMA,
        license="CC0-1.0",
        methodology=(
            "One serial Harbor Terminus-2 attempt per task on the exact pinned task set; "
            "verifier-backed rewards, complete task wall time, and recorded complete-request "
            "token accounting were normalized from the prompt-free summary. Exact token-demand "
            "signals are emitted only when the harness reports no incomplete API request."
        ),
        raw_sha256=_sha256(path),
        retrieved_at=observed_at,
    )


def _validate_summary(
    summary: dict[str, Any],
    *,
    protocol_sha256: str,
    candidate_name: str,
    candidate: dict[str, Any],
    task_set_name: str,
    expected_tasks: dict[str, str],
    harness: dict[str, Any],
) -> list[dict[str, Any]]:
    if summary.get("schema_version") != SUMMARY_SCHEMA:
        raise PilotCatalogError("unsupported Harbor summary schema_version")
    if summary.get("contains_prompts_or_model_messages") is not False:
        raise PilotCatalogError("summary is not marked prompt-free")
    summary_protocol = _mapping(summary.get("protocol"), field="summary.protocol")
    if (
        summary_protocol.get("protocol_sha256") != protocol_sha256
        or summary_protocol.get("candidate") != candidate_name
        or summary_protocol.get("task_set") != task_set_name
        or summary_protocol.get("system_profile") != candidate.get("system_profile")
        or summary_protocol.get("system_profile_sha256") != candidate.get("system_profile_sha256")
    ):
        raise PilotCatalogError("summary protocol identity does not match")
    expected = _mapping(summary.get("expected"), field="summary.expected")
    route = _string(candidate.get("route"), field="candidate.route")
    if expected.get("model") != route:
        raise PilotCatalogError("summary model does not match candidate route")
    expected_task_names = {
        _string(value, field="summary.expected.tasks[]")
        for value in _array(expected.get("tasks"), field="summary.expected.tasks")
    }
    observed_task_digests = _mapping(
        expected.get("task_digests"), field="summary.expected.task_digests"
    )
    if expected_task_names != set(expected_tasks) or observed_task_digests != expected_tasks:
        raise PilotCatalogError("summary task set does not match the protocol")
    aggregate = _mapping(summary.get("aggregate"), field="summary.aggregate")
    if aggregate.get("invalid_trials") != 0 or aggregate.get("valid_trials") != len(expected_tasks):
        raise PilotCatalogError("summary must contain one valid trial per expected task")
    job = _mapping(summary.get("job"), field="summary.job")
    harbor = _mapping(job.get("harbor"), field="summary.job.harbor")
    if (
        job.get("concurrency") != 1
        or harbor.get("version") != harness.get("harbor_version")
        or harbor.get("git_commit_hash") != harness.get("harbor_revision")
    ):
        raise PilotCatalogError("summary Harbor identity or concurrency does not match")
    trials = [
        _mapping(value, field=f"summary.trials[{index}]")
        for index, value in enumerate(_array(summary.get("trials"), field="summary.trials"))
    ]
    if len(trials) != len(expected_tasks):
        raise PilotCatalogError("summary trial count does not match the task set")
    seen: set[str] = set()
    successes = Decimal(0)
    for trial in trials:
        task_name = _string(trial.get("task_name"), field="trial.task_name")
        if task_name in seen or trial.get("task_lock_digest") != expected_tasks.get(task_name):
            raise PilotCatalogError("summary trial task identity is duplicate or incorrect")
        seen.add(task_name)
        if trial.get("model") != route:
            raise PilotCatalogError("summary trial model does not match candidate route")
        reward = _decimal(trial.get("reward"), field=f"{task_name}.reward")
        if reward not in {Decimal(0), Decimal(1)}:
            raise PilotCatalogError("pilot rewards must be binary")
        successes += reward
        _integer(
            trial.get("incomplete_api_requests"),
            field=f"{task_name}.incomplete_api_requests",
        )
        _trial_wall_seconds(trial)
    if seen != set(expected_tasks):
        raise PilotCatalogError("summary trials do not cover the exact task set")
    with localcontext(POLICY_DECIMAL_CONTEXT):
        success_percent = successes / Decimal(len(trials)) * Decimal(100)
    if (
        _decimal(aggregate.get("successes"), field="aggregate.successes") != successes
        or _decimal(aggregate.get("success_percent"), field="aggregate.success_percent")
        != success_percent
    ):
        raise PilotCatalogError("summary aggregate does not match trial rewards")
    return trials


def _memory_signal(
    memory: dict[str, Any],
    path: Path,
    *,
    summary: dict[str, Any],
    route: str,
    tasks: set[str],
    timezone: ZoneInfo,
) -> tuple[Observation | None, dict[str, Any]]:
    if memory.get("schema_version") != MEMORY_SCHEMA:
        raise PilotCatalogError("unsupported runner-memory schema_version")
    if memory.get("contains_prompts_or_model_messages") is not False:
        raise PilotCatalogError("runner-memory capture is not marked prompt-free")
    job = _mapping(summary.get("job"), field="summary.job")
    if (
        memory.get("expected_model") != route
        or memory.get("job_lock_sha256") != job.get("job_lock_sha256")
        or memory.get("job_timestamp_timezone") != timezone.key
    ):
        raise PilotCatalogError("runner-memory capture does not match the summary job")
    coverage = _mapping(
        memory.get("capture_started_before_agent_execution"),
        field="memory.capture_started_before_agent_execution",
    )
    if set(coverage) != tasks or any(not isinstance(value, bool) for value in coverage.values()):
        raise PilotCatalogError("runner-memory coverage does not match the exact task set")
    peaks = _mapping(memory.get("task_peaks"), field="memory.task_peaks")
    expected_basenames = {task.rsplit("/", 1)[-1]: task for task in tasks}
    if len(expected_basenames) != len(tasks):
        raise PilotCatalogError("task basenames are not unique")
    reasons: list[str] = []
    values: list[Decimal] = []
    sample_total = 0
    used_legacy_basename = False
    for task in sorted(tasks):
        if coverage[task] is not True:
            reasons.append(f"capture_started_after_agent_execution:{task}")
            continue
        peak = peaks.get(task)
        if peak is None:
            peak = peaks.get(task.rsplit("/", 1)[-1])
            used_legacy_basename = peak is not None or used_legacy_basename
        if not isinstance(peak, dict):
            reasons.append(f"missing_task_peak:{task}")
            continue
        samples = peak.get("samples")
        physical = peak.get("physical_footprint_bytes")
        if isinstance(samples, bool) or not isinstance(samples, int) or samples <= 0:
            reasons.append(f"no_task_samples:{task}")
            continue
        if isinstance(physical, bool) or not isinstance(physical, int) or physical <= 0:
            reasons.append(f"no_physical_footprint:{task}")
            continue
        sample_total += samples
        values.append(Decimal(physical))
    metadata = {
        "eligible": not reasons and len(values) == len(tasks),
        "ineligibility_reasons": reasons,
        "memory_capture_sha256": _sha256(path),
        "memory_capture_file": path.name,
        "process_match": memory.get("process_match"),
        "sample_interval_seconds": memory.get("sample_interval_seconds"),
        "covered_task_count": len(values),
        "sample_count": sample_total,
        "legacy_basename_task_peaks": used_legacy_basename,
    }
    if reasons or len(values) != len(tasks):
        return None, metadata
    observed_at = _timestamp(
        memory.get("finished_at"), field="memory.finished_at", naive_timezone=timezone
    )
    source = SourceReference(
        id=f"local-quality-memory:{_sha256(path)[:24]}",
        version=MEMORY_SCHEMA,
        license="CC0-1.0",
        methodology=(
            "Kernel-accounted macOS physical footprint sampled from the exact local runner "
            "during every task's complete agent-execution interval."
        ),
        raw_sha256=_sha256(path),
        retrieved_at=observed_at,
    )
    return (
        Observation(
            value=max(values),
            lower=min(values),
            upper=max(values),
            unit="byte",
            sample_count=len(values),
            observed_at=observed_at,
            source=source,
        ),
        metadata,
    )


def build_catalog(
    *,
    protocol_path: Path,
    hardware_path: Path,
    task_set_name: str,
    summary_paths: list[Path],
    memory_paths: list[Path],
) -> ObservationCatalog:
    if not summary_paths:
        raise PilotCatalogError("at least one --summary is required")
    protocol = _load_protocol(protocol_path)
    protocol_sha256 = _sha256(protocol_path)
    root = protocol_path.resolve().parent
    harness = _mapping(protocol.get("harness"), field="protocol.harness")
    timezone_name = _string(harness.get("execution_timezone"), field="harness.execution_timezone")
    timezone = ZoneInfo(timezone_name)
    task_sets = _mapping(protocol.get("task_sets"), field="protocol.task_sets")
    task_set = _mapping(task_sets.get(task_set_name), field=f"task_sets.{task_set_name}")
    expected_tasks = _task_digests(root, task_set)
    workload = WorkloadReference(
        id=_string(task_set.get("measured_workload_name"), field="task_set.measured_workload_name"),
        version=_string(task_set.get("workload_version"), field="task_set.workload_version"),
        unit=_string(task_set.get("workload_unit"), field="task_set.workload_unit"),
    )
    hardware = LocalHardwareIdentity.model_validate(_load_json(hardware_path))
    hardware_sha256 = _sha256(hardware_path)
    harness_identity = _harness_identity(harness)
    candidates = _mapping(protocol.get("candidates"), field="protocol.candidates")

    memory_by_model: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in memory_paths:
        memory = _load_json(path)
        model = _string(memory.get("expected_model"), field="memory.expected_model")
        if model in memory_by_model:
            raise PilotCatalogError(f"duplicate runner-memory capture for model {model!r}")
        memory_by_model[model] = (memory, path)

    offerings: list[OfferingObservation] = []
    seen_candidates: set[str] = set()
    seen_offerings: set[str] = set()
    for path in summary_paths:
        summary = _load_json(path)
        summary_protocol = _mapping(summary.get("protocol"), field="summary.protocol")
        candidate_name = _string(summary_protocol.get("candidate"), field="protocol.candidate")
        if candidate_name in seen_candidates:
            raise PilotCatalogError(f"duplicate summary for candidate {candidate_name!r}")
        seen_candidates.add(candidate_name)
        candidate = _mapping(candidates.get(candidate_name), field=f"candidates.{candidate_name}")
        trials = _validate_summary(
            summary,
            protocol_sha256=protocol_sha256,
            candidate_name=candidate_name,
            candidate=candidate,
            task_set_name=task_set_name,
            expected_tasks=expected_tasks,
            harness=harness,
        )
        artifact, runtime, capabilities, profile_sha256 = _load_profile(
            root, candidate, harness_identity=harness_identity
        )
        offering = local_system_offering_key(
            hardware=hardware,
            artifact=artifact,
            runtime=runtime,
            capabilities=capabilities,
        )
        if offering.offering_id in seen_offerings:
            raise PilotCatalogError("summaries map to a duplicate exact local offering")
        seen_offerings.add(offering.offering_id)

        finished_at = _timestamp(
            _mapping(summary.get("job"), field="summary.job").get("finished_at"),
            field="summary.job.finished_at",
            naive_timezone=timezone,
        )
        source = _quality_source(summary, path, observed_at=finished_at)
        rewards = [_decimal(trial.get("reward"), field="trial.reward") for trial in trials]
        wall_seconds = [_trial_wall_seconds(trial) for trial in trials]
        successful_wall = [
            duration for duration, reward in zip(wall_seconds, rewards, strict=True) if reward == 1
        ]
        total_input = sum(
            _integer(
                _mapping(trial.get("tokens"), field="trial.tokens").get("input"),
                field="tokens.input",
            )
            for trial in trials
        )
        total_cache = sum(
            _integer(
                _mapping(trial.get("tokens"), field="trial.tokens").get("cache"),
                field="tokens.cache",
            )
            for trial in trials
        )
        total_output = sum(
            _integer(
                _mapping(trial.get("tokens"), field="trial.tokens").get("output"),
                field="tokens.output",
            )
            for trial in trials
        )
        incomplete_api_requests = sum(
            _integer(
                trial.get("incomplete_api_requests"),
                field="trial.incomplete_api_requests",
            )
            for trial in trials
        )
        if total_cache > total_input:
            raise PilotCatalogError("cached input tokens exceed total input tokens")
        successes = sum(rewards, start=Decimal(0))
        with localcontext(POLICY_DECIMAL_CONTEXT):
            success_percent = successes / Decimal(len(trials)) * Decimal(100)
            cache_reuse_percent = (
                Decimal(total_cache) / Decimal(total_input) * Decimal(100)
                if total_input
                else Decimal(0)
            )
        signals = {
            "local_pilot_task_success_percent": Observation(
                value=success_percent,
                unit="percent",
                sample_count=len(trials),
                observed_at=finished_at,
                source=source,
            ),
            "local_pilot_p95_task_wall_seconds": Observation(
                value=_quantile_cont(wall_seconds, Decimal("0.95")),
                lower=min(wall_seconds),
                upper=max(wall_seconds),
                unit="s",
                sample_count=len(wall_seconds),
                observed_at=finished_at,
                source=source,
            ),
        }
        if incomplete_api_requests == 0:
            signals["local_pilot_total_uncached_input_tokens"] = Observation(
                value=total_input - total_cache,
                unit="token",
                sample_count=len(trials),
                observed_at=finished_at,
                source=source,
            )
            signals["local_pilot_cache_reuse_percent"] = Observation(
                value=cache_reuse_percent,
                unit="percent",
                sample_count=len(trials),
                observed_at=finished_at,
                source=source,
            )
            signals["local_pilot_total_output_tokens"] = Observation(
                value=total_output,
                unit="token",
                sample_count=len(trials),
                observed_at=finished_at,
                source=source,
            )
        if successful_wall:
            signals["local_pilot_p95_successful_task_wall_seconds"] = Observation(
                value=_quantile_cont(successful_wall, Decimal("0.95")),
                lower=min(successful_wall),
                upper=max(successful_wall),
                unit="s",
                sample_count=len(successful_wall),
                observed_at=finished_at,
                source=source,
            )

        route = _string(candidate.get("route"), field="candidate.route")
        memory_metadata: dict[str, Any] = {
            "eligible": False,
            "ineligibility_reasons": ["no_memory_capture"],
        }
        memory_entry = memory_by_model.pop(route, None)
        if memory_entry is not None:
            memory_signal, memory_metadata = _memory_signal(
                memory_entry[0],
                memory_entry[1],
                summary=summary,
                route=route,
                tasks=set(expected_tasks),
                timezone=timezone,
            )
            if memory_signal is not None:
                signals["local_peak_process_physical_footprint_bytes"] = memory_signal

        metadata = {
            "local_evidence_status": "provisional",
            "hardware": hardware.model_dump(mode="json"),
            "hardware_sha256": hardware_sha256,
            "artifact": artifact.model_dump(mode="json"),
            "runtime": runtime.model_dump(mode="json"),
            "runtime_identity_sha256": content_hash(runtime),
            "workload": {
                "reference": workload.model_dump(mode="json"),
                "task_set": task_set_name,
                "task_digests": expected_tasks,
                "attempts_per_task": 1,
                "quantile_method": "Hyndman-Fan type 7 continuous",
                "p95_includes_quality_attributable_failures": True,
                "harness": harness,
            },
            "pilot": {
                "candidate": candidate_name,
                "role": candidate.get("role"),
                "protocol_sha256": protocol_sha256,
                "system_profile_sha256": profile_sha256,
                "summary_sha256": _sha256(path),
                "summary_file": path.name,
                "job_id": _mapping(summary.get("job"), field="summary.job").get("id"),
                "job_lock_sha256": _mapping(summary.get("job"), field="summary.job").get(
                    "job_lock_sha256"
                ),
                "successes": str(successes),
                "task_count": len(trials),
                "token_accounting": {
                    "eligible": incomplete_api_requests == 0,
                    "ineligibility_reasons": (
                        []
                        if incomplete_api_requests == 0
                        else [f"incomplete_api_requests:{incomplete_api_requests}"]
                    ),
                    "incomplete_api_requests": incomplete_api_requests,
                    "recorded_input_tokens_lower_bound": total_input,
                    "recorded_cache_tokens_lower_bound": total_cache,
                    "recorded_uncached_input_tokens_lower_bound": total_input - total_cache,
                    "recorded_output_tokens_lower_bound": total_output,
                },
                "memory": memory_metadata,
            },
        }
        offerings.append(
            OfferingObservation(
                offering=offering,
                signals=signals,
                metadata=metadata,
                default_source=source,
            )
        )
    if memory_by_model:
        raise PilotCatalogError(
            "runner-memory capture has no matching summary: " + ", ".join(sorted(memory_by_model))
        )
    return ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=workload,
        offerings=sorted(offerings, key=lambda value: value.offering.offering_id),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--hardware", type=Path, required=True)
    parser.add_argument("--task-set", required=True)
    parser.add_argument("--summary", type=Path, action="append", required=True)
    parser.add_argument("--memory-capture", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        catalog = build_catalog(
            protocol_path=args.protocol,
            hardware_path=args.hardware,
            task_set_name=args.task_set,
            summary_paths=args.summary,
            memory_paths=args.memory_capture,
        )
    except (OSError, ValueError, ZoneInfoNotFoundError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(dump_json(catalog), encoding="utf-8")


if __name__ == "__main__":
    main()
