#!/usr/bin/env python3
"""Build an exact local quality catalog from prompt-free Harbor pilot summaries."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
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
MEMORY_SUMMARY_SCHEMA = "model-skyline/harbor-runner-memory-summary/v1"
MAX_INPUT_BYTES = 64_000_000
TIMING_PHASES = ("environment_setup", "agent_setup", "agent_execution", "verifier")


class PilotCatalogError(ValueError):
    """The supplied pilot evidence cannot produce a trustworthy catalog."""


@dataclass(frozen=True)
class PilotRun:
    path: Path
    summary: dict[str, Any]
    trials: list[dict[str, Any]]
    finished_at: datetime
    job_id: str
    job_lock_sha256: str
    raw_sha256: str


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


def _sha256_string(value: object, *, field: str) -> str:
    digest = _string(value, field=field)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise PilotCatalogError(f"{field} must be a lowercase SHA-256 digest")
    return digest


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


def _quality_bundle_source(candidate_name: str, runs: list[PilotRun]) -> SourceReference:
    if len(runs) == 1:
        run = runs[0]
        return _quality_source(run.summary, run.path, observed_at=run.finished_at)
    manifest = {
        "schema_version": "model-skyline/harbor-local-summary-bundle/v1",
        "candidate": candidate_name,
        "runs": [
            {
                "job_id": run.job_id,
                "job_lock_sha256": run.job_lock_sha256,
                "raw_sha256": run.raw_sha256,
            }
            for run in runs
        ],
    }
    digest = content_hash(manifest)
    return SourceReference(
        id=f"local-quality-pilot:{candidate_name}:repeat-bundle:{digest[:24]}",
        version="model-skyline/harbor-local-summary-bundle/v1",
        license="CC0-1.0",
        methodology=(
            f"Canonical manifest of {len(runs)} prompt-free, serial Harbor Terminus-2 jobs "
            "over the same pinned task set and exact offering. Verifier rewards and latency "
            "are pooled across equal attempts per task; quality bounds retain the per-run range, "
            "and p95-latency bounds enclose both that range and the pooled p95. Token totals are "
            "means per complete task-set run so candidates remain comparable as repetition "
            "count grows."
        ),
        raw_sha256=digest,
        retrieved_at=max(run.finished_at for run in runs),
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
    memory_schema = memory.get("schema_version")
    if memory_schema not in {MEMORY_SCHEMA, MEMORY_SUMMARY_SCHEMA}:
        raise PilotCatalogError("unsupported runner-memory schema_version")
    if memory.get("contains_prompts_or_model_messages") is not False:
        raise PilotCatalogError("runner-memory capture is not marked prompt-free")
    source_capture: dict[str, Any] | None = None
    source_task_name_form: str | None = None
    if memory_schema == MEMORY_SUMMARY_SCHEMA:
        source_capture = _mapping(memory.get("source_capture"), field="memory.source_capture")
        if source_capture.get("schema_version") != MEMORY_SCHEMA:
            raise PilotCatalogError("runner-memory summary has an unsupported source schema")
        _sha256_string(source_capture.get("raw_sha256"), field="memory.source_capture.raw_sha256")
        source_bytes = _integer(
            source_capture.get("raw_bytes"), field="memory.source_capture.raw_bytes"
        )
        assert source_bytes is not None
        if source_bytes <= 0:
            raise PilotCatalogError("runner-memory summary source byte count must be positive")
        summary_sample_count = _integer(memory.get("sample_count"), field="memory.sample_count")
        assert summary_sample_count is not None
        if summary_sample_count <= 0:
            raise PilotCatalogError("runner-memory summary sample_count must be positive")
        if memory.get("task_name_form") != "completed_result_name":
            raise PilotCatalogError("runner-memory summary task names must be canonical")
        source_task_name_form = _string(
            memory.get("source_task_name_form"), field="memory.source_task_name_form"
        )
        if source_task_name_form not in {
            "completed_result_name",
            "legacy_trial_directory_basename",
        }:
            raise PilotCatalogError("runner-memory summary has an unsupported source name form")
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
        "memory_evidence_schema_version": memory_schema,
        "process_match": memory.get("process_match"),
        "sample_interval_seconds": memory.get("sample_interval_seconds"),
        "covered_task_count": len(values),
        "sample_count": sample_total,
        "legacy_basename_task_peaks": used_legacy_basename,
    }
    if source_capture is not None:
        metadata["source_capture_sha256"] = source_capture["raw_sha256"]
        metadata["source_capture_bytes"] = source_capture["raw_bytes"]
        metadata["source_task_name_form"] = source_task_name_form
    if reasons or len(values) != len(tasks):
        return None, metadata
    observed_at = _timestamp(
        memory.get("finished_at"), field="memory.finished_at", naive_timezone=timezone
    )
    source = SourceReference(
        id=f"local-quality-memory:{_sha256(path)[:24]}",
        version=str(memory_schema),
        license="CC0-1.0",
        methodology=(
            "Kernel-accounted macOS physical-footprint task peaks from the exact local runner "
            "during every task's complete agent-execution interval. Compact summaries replay "
            "their peaks from the source samples and retain that capture's SHA-256 digest."
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


def _aggregate_memory_signals(
    entries: list[tuple[Observation | None, dict[str, Any]]],
) -> tuple[Observation | None, dict[str, Any]]:
    eligible = [signal for signal, _ in entries if signal is not None]
    captures = [metadata for _, metadata in entries]
    reasons = [
        reason for metadata in captures for reason in metadata.get("ineligibility_reasons", [])
    ]
    metadata = {
        "eligible": len(eligible) == len(entries) and bool(entries),
        "ineligibility_reasons": reasons,
        "run_count": len(entries),
        "eligible_run_count": len(eligible),
        "captures": captures,
    }
    if not metadata["eligible"]:
        return None, metadata
    if len(eligible) == 1:
        return eligible[0], captures[0]

    sources = [signal.source for signal in eligible]
    if any(source is None or source.raw_sha256 is None for source in sources):
        raise PilotCatalogError("eligible memory observations must have content-addressed sources")
    manifest = {
        "schema_version": "model-skyline/harbor-runner-memory-bundle/v1",
        "captures": [
            {
                "id": source.id,
                "raw_sha256": source.raw_sha256,
            }
            for source in sources
            if source is not None
        ],
    }
    digest = content_hash(manifest)
    observed_at_values = [signal.observed_at for signal in eligible]
    if any(value is None for value in observed_at_values):
        raise PilotCatalogError("eligible memory observations must have timestamps")
    observed_at = max(value for value in observed_at_values if value is not None)
    source = SourceReference(
        id=f"local-quality-memory:repeat-bundle:{digest[:24]}",
        version="model-skyline/harbor-runner-memory-bundle/v1",
        license="CC0-1.0",
        methodology=(
            "Canonical manifest of job-matched macOS physical-footprint captures. The value is "
            "the maximum task peak across every fully covered repetition."
        ),
        raw_sha256=digest,
        retrieved_at=observed_at,
    )
    peak_values = [signal.value for signal in eligible]
    return (
        Observation(
            value=max(peak_values),
            lower=min(peak_values),
            upper=max(peak_values),
            unit="byte",
            sample_count=sum(signal.sample_count or 0 for signal in eligible),
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
    hardware = LocalHardwareIdentity.model_validate(_load_json(hardware_path))
    hardware_sha256 = _sha256(hardware_path)
    harness_identity = _harness_identity(harness)
    candidates = _mapping(protocol.get("candidates"), field="protocol.candidates")

    memory_by_job: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in memory_paths:
        memory = _load_json(path)
        job_lock = _sha256_string(memory.get("job_lock_sha256"), field="memory.job_lock_sha256")
        if job_lock in memory_by_job:
            raise PilotCatalogError(f"duplicate runner-memory capture for job {job_lock!r}")
        memory_by_job[job_lock] = (memory, path)

    summaries_by_candidate: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    seen_summary_hashes: set[str] = set()
    seen_job_ids: set[str] = set()
    seen_job_locks: set[str] = set()
    for path in summary_paths:
        summary = _load_json(path)
        summary_protocol = _mapping(summary.get("protocol"), field="summary.protocol")
        candidate_name = _string(summary_protocol.get("candidate"), field="protocol.candidate")
        summary_hash = _sha256(path)
        job = _mapping(summary.get("job"), field="summary.job")
        job_id = _string(job.get("id"), field="summary.job.id")
        job_lock = _sha256_string(job.get("job_lock_sha256"), field="summary.job.job_lock_sha256")
        if summary_hash in seen_summary_hashes:
            raise PilotCatalogError("summary inputs must have unique content digests")
        if job_id in seen_job_ids or job_lock in seen_job_locks:
            raise PilotCatalogError("summary inputs must describe distinct Harbor jobs")
        seen_summary_hashes.add(summary_hash)
        seen_job_ids.add(job_id)
        seen_job_locks.add(job_lock)
        summaries_by_candidate.setdefault(candidate_name, []).append((path, summary))

    repetition_counts = {len(values) for values in summaries_by_candidate.values()}
    if len(repetition_counts) != 1:
        raise PilotCatalogError("every candidate must have the same number of task-set repetitions")
    attempts_per_task = repetition_counts.pop()
    base_workload_version = _string(
        task_set.get("workload_version"), field="task_set.workload_version"
    )
    workload = WorkloadReference(
        id=_string(task_set.get("measured_workload_name"), field="task_set.measured_workload_name"),
        version=(
            base_workload_version
            if attempts_per_task == 1
            else f"{base_workload_version}+attempts-{attempts_per_task}"
        ),
        unit=_string(task_set.get("workload_unit"), field="task_set.workload_unit"),
    )

    offerings: list[OfferingObservation] = []
    seen_offerings: set[str] = set()
    for candidate_name in sorted(summaries_by_candidate):
        candidate = _mapping(candidates.get(candidate_name), field=f"candidates.{candidate_name}")
        runs: list[PilotRun] = []
        for path, summary in summaries_by_candidate[candidate_name]:
            trials = _validate_summary(
                summary,
                protocol_sha256=protocol_sha256,
                candidate_name=candidate_name,
                candidate=candidate,
                task_set_name=task_set_name,
                expected_tasks=expected_tasks,
                harness=harness,
            )
            job = _mapping(summary.get("job"), field="summary.job")
            runs.append(
                PilotRun(
                    path=path,
                    summary=summary,
                    trials=trials,
                    finished_at=_timestamp(
                        job.get("finished_at"),
                        field="summary.job.finished_at",
                        naive_timezone=timezone,
                    ),
                    job_id=_string(job.get("id"), field="summary.job.id"),
                    job_lock_sha256=_sha256_string(
                        job.get("job_lock_sha256"), field="summary.job.job_lock_sha256"
                    ),
                    raw_sha256=_sha256(path),
                )
            )
        runs.sort(key=lambda run: (run.finished_at, run.job_id, run.raw_sha256))
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

        finished_at = max(run.finished_at for run in runs)
        source = _quality_bundle_source(candidate_name, runs)
        run_wall_seconds = [[_trial_wall_seconds(trial) for trial in run.trials] for run in runs]
        trials = [trial for run in runs for trial in run.trials]
        rewards = [_decimal(trial.get("reward"), field="trial.reward") for trial in trials]
        wall_seconds = [duration for values in run_wall_seconds for duration in values]
        successful_wall = [
            duration for duration, reward in zip(wall_seconds, rewards, strict=True) if reward == 1
        ]
        run_token_totals: list[tuple[int, int, int, int]] = []
        run_success_percents: list[Decimal] = []
        for run in runs:
            run_rewards = [
                _decimal(trial.get("reward"), field="trial.reward") for trial in run.trials
            ]
            run_successes = sum(run_rewards, start=Decimal(0))
            with localcontext(POLICY_DECIMAL_CONTEXT):
                run_success_percents.append(run_successes / Decimal(len(run.trials)) * Decimal(100))
            run_input = sum(
                _integer(
                    _mapping(trial.get("tokens"), field="trial.tokens").get("input"),
                    field="tokens.input",
                )
                for trial in run.trials
            )
            run_cache = sum(
                _integer(
                    _mapping(trial.get("tokens"), field="trial.tokens").get("cache"),
                    field="tokens.cache",
                )
                for trial in run.trials
            )
            run_output = sum(
                _integer(
                    _mapping(trial.get("tokens"), field="trial.tokens").get("output"),
                    field="tokens.output",
                )
                for trial in run.trials
            )
            run_incomplete = sum(
                _integer(
                    trial.get("incomplete_api_requests"),
                    field="trial.incomplete_api_requests",
                )
                for trial in run.trials
            )
            if run_cache > run_input:
                raise PilotCatalogError("cached input tokens exceed total input tokens")
            run_token_totals.append((run_input, run_cache, run_output, run_incomplete))
        total_input = sum(value[0] for value in run_token_totals)
        total_cache = sum(value[1] for value in run_token_totals)
        total_output = sum(value[2] for value in run_token_totals)
        incomplete_api_requests = sum(value[3] for value in run_token_totals)
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
            mean_uncached_input = Decimal(total_input - total_cache) / Decimal(len(runs))
            mean_output = Decimal(total_output) / Decimal(len(runs))
            cache_percent_by_run = [
                Decimal(cache) / Decimal(input_tokens) * Decimal(100)
                if input_tokens
                else Decimal(0)
                for input_tokens, cache, _, _ in run_token_totals
            ]
        quality_bounds = (
            {
                "lower": min(run_success_percents),
                "upper": max(run_success_percents),
            }
            if len(runs) > 1
            else {}
        )
        pooled_p95_wall = _quantile_cont(wall_seconds, Decimal("0.95"))
        if len(runs) > 1:
            run_p95_walls = [_quantile_cont(values, Decimal("0.95")) for values in run_wall_seconds]
            wall_lower = min([pooled_p95_wall, *run_p95_walls])
            wall_upper = max([pooled_p95_wall, *run_p95_walls])
        else:
            wall_lower = min(wall_seconds)
            wall_upper = max(wall_seconds)
        signals = {
            "local_pilot_task_success_percent": Observation(
                value=success_percent,
                unit="percent",
                sample_count=len(trials),
                observed_at=finished_at,
                source=source,
                **quality_bounds,
            ),
            "local_pilot_p95_task_wall_seconds": Observation(
                value=pooled_p95_wall,
                lower=wall_lower,
                upper=wall_upper,
                unit="s",
                sample_count=len(wall_seconds),
                observed_at=finished_at,
                source=source,
            ),
        }
        if incomplete_api_requests == 0:
            uncached_by_run = [value[0] - value[1] for value in run_token_totals]
            output_by_run = [value[2] for value in run_token_totals]
            uncached_bounds = (
                {"lower": min(uncached_by_run), "upper": max(uncached_by_run)}
                if len(runs) > 1
                else {}
            )
            cache_bounds = (
                {"lower": min(cache_percent_by_run), "upper": max(cache_percent_by_run)}
                if len(runs) > 1
                else {}
            )
            output_bounds = (
                {"lower": min(output_by_run), "upper": max(output_by_run)} if len(runs) > 1 else {}
            )
            signals["local_pilot_total_uncached_input_tokens"] = Observation(
                value=mean_uncached_input,
                unit="token",
                sample_count=len(trials),
                observed_at=finished_at,
                source=source,
                **uncached_bounds,
            )
            signals["local_pilot_cache_reuse_percent"] = Observation(
                value=cache_reuse_percent,
                unit="percent",
                sample_count=len(trials),
                observed_at=finished_at,
                source=source,
                **cache_bounds,
            )
            signals["local_pilot_total_output_tokens"] = Observation(
                value=mean_output,
                unit="token",
                sample_count=len(trials),
                observed_at=finished_at,
                source=source,
                **output_bounds,
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
        if any(run.job_lock_sha256 in memory_by_job for run in runs):
            memory_entries: list[tuple[Observation | None, dict[str, Any]]] = []
            for run in runs:
                memory_entry = memory_by_job.pop(run.job_lock_sha256, None)
                if memory_entry is None:
                    memory_entries.append(
                        (
                            None,
                            {
                                "eligible": False,
                                "ineligibility_reasons": [
                                    f"no_memory_capture_for_job:{run.job_lock_sha256}"
                                ],
                            },
                        )
                    )
                    continue
                memory_entries.append(
                    _memory_signal(
                        memory_entry[0],
                        memory_entry[1],
                        summary=run.summary,
                        route=route,
                        tasks=set(expected_tasks),
                        timezone=timezone,
                    )
                )
            memory_signal, memory_metadata = _aggregate_memory_signals(memory_entries)
            if memory_signal is not None:
                signals["local_peak_process_physical_footprint_bytes"] = memory_signal

        summary_hashes = [run.raw_sha256 for run in runs]
        summary_files = [run.path.name for run in runs]
        job_ids = [run.job_id for run in runs]
        job_locks = [run.job_lock_sha256 for run in runs]
        token_accounting = {
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
        }
        pilot_metadata = {
            "candidate": candidate_name,
            "role": candidate.get("role"),
            "protocol_sha256": protocol_sha256,
            "system_profile_sha256": profile_sha256,
        }
        if len(runs) == 1:
            pilot_metadata.update(
                {
                    "summary_sha256": summary_hashes[0],
                    "summary_file": summary_files[0],
                    "job_id": job_ids[0],
                    "job_lock_sha256": job_locks[0],
                }
            )
        else:
            token_accounting["axis_aggregation"] = "mean_per_task_set_run"
            pilot_metadata.update(
                {
                    "run_count": len(runs),
                    "summary_sha256s": summary_hashes,
                    "summary_files": summary_files,
                    "job_ids": job_ids,
                    "job_lock_sha256s": job_locks,
                }
            )
        pilot_metadata.update(
            {
                "successes": str(successes),
                "task_count": len(trials),
                "token_accounting": token_accounting,
                "memory": memory_metadata,
            }
        )
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
                "attempts_per_task": attempts_per_task,
                "quantile_method": "Hyndman-Fan type 7 continuous",
                "p95_includes_quality_attributable_failures": True,
                "harness": harness,
            },
            "pilot": pilot_metadata,
        }
        offerings.append(
            OfferingObservation(
                offering=offering,
                signals=signals,
                metadata=metadata,
                default_source=source,
            )
        )
    if memory_by_job:
        raise PilotCatalogError(
            "runner-memory capture does not match the summary jobs: "
            + ", ".join(sorted(memory_by_job))
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
