#!/usr/bin/env python3
"""Create a bounded, prompt-free audit summary of one local Harbor job."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "model-skyline/harbor-local-job-summary/v1"
MAX_JSON_BYTES = 64_000_000


class InvalidHarborTrial(ValueError):
    """A trial cannot contribute model-quality evidence."""


@dataclass(frozen=True, slots=True)
class PilotExpectation:
    model: str
    tasks: dict[str, str]
    agent: dict[str, str]
    agent_configuration: dict[str, Any]
    harbor: dict[str, str]
    concurrency: int
    quality_attributable_exceptions: frozenset[str]
    identity: dict[str, str]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise InvalidHarborTrial(f"missing regular file: {path.name}")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise InvalidHarborTrial(f"file exceeds {MAX_JSON_BYTES} bytes: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidHarborTrial(f"invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise InvalidHarborTrial(f"expected a JSON object: {path.name}")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise InvalidHarborTrial(f"missing regular file: {path.name}")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise InvalidHarborTrial(f"file exceeds {MAX_JSON_BYTES} bytes: {path.name}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise InvalidHarborTrial(f"invalid YAML: {path.name}") from exc
    if not isinstance(value, dict):
        raise InvalidHarborTrial(f"expected a YAML mapping: {path.name}")
    return value


def _is_sha256_digest(value: object, *, prefixed: bool) -> bool:
    if not isinstance(value, str):
        return False
    hex_digest = value.removeprefix("sha256:") if prefixed else value
    if prefixed and not value.startswith("sha256:"):
        return False
    return len(hex_digest) == 64 and all(
        character in "0123456789abcdef" for character in hex_digest
    )


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidHarborTrial(f"{field} must be a non-negative integer")
    return value


def _decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise InvalidHarborTrial(f"{field} must be a JSON number")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidHarborTrial(f"{field} must be a JSON number") from exc
    if not result.is_finite() or result < 0:
        raise InvalidHarborTrial(f"{field} must be finite and non-negative")
    return result


def _timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise InvalidHarborTrial(f"{field} must be an ISO 8601 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InvalidHarborTrial(f"{field} must be an ISO 8601 timestamp") from exc
    if timestamp.tzinfo is None:
        raise InvalidHarborTrial(f"{field} must include a timezone")
    return timestamp


def _duration_seconds(container: dict[str, Any], *, field: str) -> str:
    interval = container.get(field)
    if not isinstance(interval, dict):
        raise InvalidHarborTrial(f"{field} timing is missing")
    started = _timestamp(interval.get("started_at"), field=f"{field}.started_at")
    finished = _timestamp(interval.get("finished_at"), field=f"{field}.finished_at")
    if finished < started:
        raise InvalidHarborTrial(f"{field} finished before it started")
    return format(Decimal(str((finished - started).total_seconds())), "f")


def _ctrf_summary(path: Path) -> dict[str, int | str]:
    ctrf = _load_json(path)
    results = ctrf.get("results")
    if not isinstance(results, dict):
        raise InvalidHarborTrial("CTRF results object is missing")
    tool = results.get("tool")
    summary = results.get("summary")
    tests = results.get("tests")
    if not isinstance(tool, dict) or tool.get("name") != "pytest":
        raise InvalidHarborTrial("CTRF tool must be pytest")
    if not isinstance(summary, dict) or not isinstance(tests, list):
        raise InvalidHarborTrial("CTRF summary or tests are missing")
    counts = {
        name: _integer(summary.get(name), field=f"CTRF summary.{name}")
        for name in ("tests", "passed", "failed", "skipped", "pending", "other")
    }
    if counts["tests"] == 0:
        raise InvalidHarborTrial("CTRF contains no tests")
    if (
        sum(counts[name] for name in ("passed", "failed", "skipped", "pending", "other"))
        != counts["tests"]
    ):
        raise InvalidHarborTrial("CTRF summary counts do not add up")
    if len(tests) != counts["tests"]:
        raise InvalidHarborTrial("CTRF test array length does not match its summary")
    observed = {name: 0 for name in ("passed", "failed", "skipped", "pending", "other")}
    for test in tests:
        if not isinstance(test, dict) or test.get("status") not in observed:
            raise InvalidHarborTrial("CTRF test contains an unsupported status")
        observed[test["status"]] += 1
    if any(observed[name] != counts[name] for name in observed):
        raise InvalidHarborTrial("CTRF per-test statuses disagree with summary counts")
    return {"tool_version": str(tool.get("version", "unknown")), **counts}


def _reward(result: dict[str, Any], *, ctrf: dict[str, int | str], trial_dir: Path) -> Decimal:
    verifier = result.get("verifier_result")
    if not isinstance(verifier, dict) or not isinstance(verifier.get("rewards"), dict):
        raise InvalidHarborTrial("verifier result is missing")
    reward = _decimal(verifier["rewards"].get("reward"), field="verifier reward")
    if reward not in {Decimal(0), Decimal(1)}:
        raise InvalidHarborTrial("Terminal-Bench reward must be binary")
    expected = Decimal(int(ctrf["failed"] == 0 and ctrf["pending"] == 0 and ctrf["other"] == 0))
    if reward != expected:
        raise InvalidHarborTrial("reward disagrees with the CTRF outcome")
    reward_path = trial_dir / "verifier" / "reward.txt"
    if reward_path.is_symlink() or not reward_path.is_file():
        raise InvalidHarborTrial("verifier reward.txt is missing")
    try:
        text_reward = Decimal(reward_path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError, InvalidOperation) as exc:
        raise InvalidHarborTrial("verifier reward.txt is invalid") from exc
    if text_reward != reward:
        raise InvalidHarborTrial("reward.txt disagrees with result.json")
    return reward


def _parser_events(metadata: dict[str, Any]) -> dict[str, int]:
    messages = metadata.get("all_messages")
    if not isinstance(messages, list):
        return {"errors": 0, "warnings": 0}
    errors = 0
    warnings = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        errors += int(content.startswith("Previous response had parsing errors:"))
        warnings += int(content.startswith("Previous response had warnings:"))
    return {"errors": errors, "warnings": warnings}


def _agent_configuration(lock_agent: dict[str, Any]) -> dict[str, Any]:
    kwargs = lock_agent.get("kwargs")
    if not isinstance(kwargs, dict):
        raise InvalidHarborTrial("trial lock agent kwargs are missing")
    model_info = kwargs.get("model_info")
    llm_kwargs = kwargs.get("llm_call_kwargs")
    if not isinstance(model_info, dict) or not isinstance(llm_kwargs, dict):
        raise InvalidHarborTrial("trial lock model or LLM configuration is missing")
    for field in ("enable_summarize", "store_all_messages"):
        if not isinstance(kwargs.get(field), bool):
            raise InvalidHarborTrial(f"trial lock {field} must be boolean")
    parser_name = kwargs.get("parser_name")
    if not isinstance(parser_name, str) or not parser_name:
        raise InvalidHarborTrial("trial lock parser_name is missing")
    return {
        "parser": parser_name,
        "temperature": format(_decimal(kwargs.get("temperature"), field="temperature"), "f"),
        "top_p": format(_decimal(llm_kwargs.get("top_p"), field="top_p"), "f"),
        "max_turns": _integer(kwargs.get("max_turns"), field="max_turns"),
        "max_input_tokens": _integer(model_info.get("max_input_tokens"), field="max_input_tokens"),
        "max_output_tokens": _integer(
            model_info.get("max_output_tokens"), field="max_output_tokens"
        ),
        "summarization_enabled": kwargs["enable_summarize"],
        "proactive_summarization_free_tokens": _integer(
            kwargs.get("proactive_summarization_threshold"),
            field="proactive_summarization_threshold",
        ),
        "full_history_recorded": kwargs["store_all_messages"],
    }


def _protocol_expectation(
    protocol_path: Path,
    *,
    candidate_name: str,
    task_set_name: str,
) -> PilotExpectation:
    protocol = _load_yaml(protocol_path)
    protocol_root = protocol_path.resolve().parent
    if protocol.get("schema_version") != "model-skyline/local-quality-pilot/v1":
        raise InvalidHarborTrial("unsupported local quality pilot schema")
    candidates = protocol.get("candidates")
    task_sets = protocol.get("task_sets")
    harness = protocol.get("harness")
    if not isinstance(candidates, dict) or not isinstance(task_sets, dict):
        raise InvalidHarborTrial("pilot candidates or task sets are missing")
    if not isinstance(harness, dict):
        raise InvalidHarborTrial("pilot harness configuration is missing")
    candidate = candidates.get(candidate_name)
    task_set = task_sets.get(task_set_name)
    if not isinstance(candidate, dict):
        raise InvalidHarborTrial(f"unknown pilot candidate: {candidate_name}")
    if not isinstance(task_set, dict):
        raise InvalidHarborTrial(f"unknown pilot task set: {task_set_name}")
    model = candidate.get("route")
    if not isinstance(model, str) or not model:
        raise InvalidHarborTrial("pilot candidate route is missing")

    tasks = task_set.get("tasks")
    task_manifest_identity: dict[str, str] = {}
    if not isinstance(tasks, list):
        manifest_name = task_set.get("task_manifest")
        manifest_digest = task_set.get("task_manifest_sha256")
        if not isinstance(manifest_name, str) or not _is_sha256_digest(
            manifest_digest, prefixed=False
        ):
            raise InvalidHarborTrial(f"pilot task set has no exact manifest: {task_set_name}")
        manifest_path = (protocol_root / manifest_name).resolve()
        if not manifest_path.is_relative_to(protocol_root):
            raise InvalidHarborTrial("pilot task manifest escapes the protocol directory")
        if _sha256(manifest_path) != manifest_digest:
            raise InvalidHarborTrial("pilot task manifest does not match its pinned digest")
        manifest = _load_json(manifest_path)
        benchmark = protocol.get("benchmark")
        if (
            manifest.get("schema_version") != "model-skyline/harbor-task-manifest/v1"
            or not isinstance(benchmark, dict)
            or manifest.get("revision") != benchmark.get("revision")
            or task_set.get("source_revision") != benchmark.get("revision")
        ):
            raise InvalidHarborTrial("pilot task manifest benchmark identity does not match")
        tasks = manifest.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != task_set.get("expected_task_count"):
            raise InvalidHarborTrial("pilot task manifest count does not match")
        task_manifest_identity = {
            "task_manifest": manifest_name,
            "task_manifest_sha256": manifest_digest,
        }

    task_digests: dict[str, str] = {}
    seen_task_digests: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("name"), str):
            raise InvalidHarborTrial("pilot task name is missing")
        digest = task.get("digest")
        if not _is_sha256_digest(digest, prefixed=True):
            raise InvalidHarborTrial("pilot task digest is invalid")
        if task["name"] in task_digests:
            raise InvalidHarborTrial("pilot task names must be unique")
        if digest in seen_task_digests:
            raise InvalidHarborTrial("pilot task digests must be unique")
        task_digests[task["name"]] = digest
        seen_task_digests.add(digest)

    harness_name = harness.get("name")
    harness_version = harness.get("version")
    harbor_version = harness.get("harbor_version")
    harbor_revision = harness.get("harbor_revision")
    if not all(
        isinstance(value, str) and value
        for value in (harness_name, harness_version, harbor_version, harbor_revision)
    ):
        raise InvalidHarborTrial("pilot harness identity is incomplete")
    assert isinstance(harness_name, str)
    agent_name = harness_name.removeprefix("harbor/")
    for field in ("summarization_enabled", "store_all_messages"):
        if not isinstance(harness.get(field), bool):
            raise InvalidHarborTrial(f"pilot harness {field} must be boolean")
    exceptions = harness.get("quality_attributable_exceptions")
    if (
        not isinstance(exceptions, list)
        or any(not isinstance(exception, str) or not exception for exception in exceptions)
        or len(set(exceptions)) != len(exceptions)
    ):
        raise InvalidHarborTrial("pilot quality-attributable exceptions are invalid")

    profile_name = candidate.get("system_profile")
    if not isinstance(profile_name, str) or not profile_name:
        raise InvalidHarborTrial("pilot candidate system profile is missing")
    profile_path = (protocol_root / profile_name).resolve()
    if not profile_path.is_relative_to(protocol_root):
        raise InvalidHarborTrial("pilot system profile escapes the protocol directory")
    profile = _load_json(profile_path)
    if profile.get("served_model") != model:
        raise InvalidHarborTrial("pilot system profile and candidate route disagree")
    expected_profile_digest = candidate.get("system_profile_sha256")
    if not _is_sha256_digest(expected_profile_digest, prefixed=False):
        raise InvalidHarborTrial("pilot system profile digest is invalid")
    if _sha256(profile_path) != expected_profile_digest:
        raise InvalidHarborTrial("pilot system profile does not match its pinned digest")

    return PilotExpectation(
        model=model,
        tasks=task_digests,
        agent={"name": agent_name, "version": str(harness_version)},
        agent_configuration={
            "parser": harness.get("parser"),
            "temperature": format(_decimal(harness.get("temperature"), field="temperature"), "f"),
            "top_p": format(_decimal(harness.get("top_p"), field="top_p"), "f"),
            "max_turns": _integer(harness.get("max_turns"), field="max_turns"),
            "max_input_tokens": _integer(harness.get("max_input_tokens"), field="max_input_tokens"),
            "max_output_tokens": _integer(
                harness.get("max_output_tokens"), field="max_output_tokens"
            ),
            "summarization_enabled": harness.get("summarization_enabled"),
            "proactive_summarization_free_tokens": _integer(
                harness.get("proactive_summarization_free_tokens"),
                field="proactive_summarization_free_tokens",
            ),
            "full_history_recorded": harness.get("store_all_messages"),
        },
        harbor={"version": str(harbor_version), "git_commit_hash": str(harbor_revision)},
        concurrency=_integer(harness.get("concurrency"), field="concurrency"),
        quality_attributable_exceptions=frozenset(exceptions),
        identity={
            "protocol_sha256": _sha256(protocol_path),
            "candidate": candidate_name,
            "task_set": task_set_name,
            "system_profile": profile_name,
            "system_profile_sha256": expected_profile_digest,
            **task_manifest_identity,
        },
    )


def summarize_trial(
    trial_dir: Path,
    *,
    expected_model: str | None = None,
    expected_task_digests: dict[str, str] | None = None,
    quality_attributable_exceptions: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    result_path = trial_dir / "result.json"
    result = _load_json(result_path)
    exception_info = result.get("exception_info")
    exception_type: str | None = None
    if exception_info is not None:
        if not isinstance(exception_info, dict) or not isinstance(
            exception_info.get("exception_type"), str
        ):
            raise InvalidHarborTrial("Harbor recorded a malformed trial exception")
        exception_type = exception_info["exception_type"]
        if exception_type not in quality_attributable_exceptions:
            raise InvalidHarborTrial(f"infrastructure-invalid trial exception: {exception_type}")
    task_name = result.get("task_name")
    task_checksum = result.get("task_checksum")
    if not isinstance(task_name, str) or not task_name:
        raise InvalidHarborTrial("task_name is missing")
    if not _is_sha256_digest(task_checksum, prefixed=False):
        raise InvalidHarborTrial("task_checksum is not a SHA-256 digest")

    lock_path = trial_dir / "lock.json"
    trial_lock = _load_json(lock_path)
    task_lock = trial_lock.get("task")
    lock_agent = trial_lock.get("agent")
    if not isinstance(task_lock, dict) or not isinstance(lock_agent, dict):
        raise InvalidHarborTrial("trial lock task or agent is missing")
    lock_task_name = task_lock.get("name")
    if not isinstance(lock_task_name, str) or lock_task_name != task_name.rsplit("/", 1)[-1]:
        raise InvalidHarborTrial("trial lock task name does not match result task name")
    task_digest = task_lock.get("digest")
    if not _is_sha256_digest(task_digest, prefixed=True):
        raise InvalidHarborTrial("trial lock task digest is not a prefixed SHA-256 digest")
    if expected_task_digests is not None:
        expected_digest = expected_task_digests.get(task_name)
        if expected_digest is None:
            raise InvalidHarborTrial("trial task is outside the expected task digest set")
        if task_digest != expected_digest:
            raise InvalidHarborTrial("trial lock task digest does not match the expected digest")

    agent_info = result.get("agent_info")
    agent_result = result.get("agent_result")
    config = result.get("config")
    if not isinstance(agent_info, dict) or not isinstance(agent_result, dict):
        raise InvalidHarborTrial("agent result is missing")
    if not isinstance(config, dict) or not isinstance(config.get("agent"), dict):
        raise InvalidHarborTrial("agent config is missing")
    model_info = agent_info.get("model_info")
    if not isinstance(model_info, dict) or not isinstance(model_info.get("name"), str):
        raise InvalidHarborTrial("agent model identity is missing")
    model_name = model_info["name"]
    configured_model = config["agent"].get("model_name")
    if configured_model != f"openai/{model_name}":
        raise InvalidHarborTrial("configured and reported model identities disagree")
    if lock_agent.get("model_name") != configured_model:
        raise InvalidHarborTrial("trial lock and reported model identities disagree")
    if expected_model is not None and model_name != expected_model:
        raise InvalidHarborTrial("trial model does not match --expected-model")

    ctrf_path = trial_dir / "verifier" / "ctrf.json"
    ctrf = _ctrf_summary(ctrf_path)
    reward = _reward(result, ctrf=ctrf, trial_dir=trial_dir)
    metadata = agent_result.get("metadata")
    if not isinstance(metadata, dict):
        raise InvalidHarborTrial("agent metadata is missing")
    request_times = metadata.get("api_request_times_msec")
    if not isinstance(request_times, list):
        raise InvalidHarborTrial("agent API request timings are missing")
    api_times = [format(_decimal(value, field="API request time"), "f") for value in request_times]
    n_episodes = _integer(metadata.get("n_episodes"), field="n_episodes")
    if len(api_times) != n_episodes:
        raise InvalidHarborTrial("API request count does not match n_episodes")

    hashes: dict[str, str] = {
        "trial_result_sha256": _sha256(result_path),
        "trial_lock_sha256": _sha256(lock_path),
        "ctrf_sha256": _sha256(ctrf_path),
        "reward_sha256": _sha256(trial_dir / "verifier" / "reward.txt"),
    }
    for label, relative in (
        ("trial_config_sha256", Path("config.json")),
        ("trajectory_sha256", Path("agent/trajectory.json")),
        ("verifier_stdout_sha256", Path("verifier/test-stdout.txt")),
    ):
        path = trial_dir / relative
        if path.is_symlink() or not path.is_file():
            raise InvalidHarborTrial(f"audit artifact is missing: {relative}")
        hashes[label] = _sha256(path)

    return {
        "trial_id": result.get("id"),
        "trial_name": result.get("trial_name"),
        "task_name": task_name,
        "task_checksum": task_checksum,
        "task_lock_digest": task_digest,
        "model": model_name,
        "agent": {
            "name": agent_info.get("name"),
            "version": agent_info.get("version"),
        },
        "agent_configuration": _agent_configuration(lock_agent),
        "reward": format(reward, "f"),
        "quality_attributable_exception": exception_type,
        "tokens": {
            "input": _integer(agent_result.get("n_input_tokens"), field="input tokens"),
            "cache": _integer(agent_result.get("n_cache_tokens"), field="cache tokens"),
            "output": _integer(agent_result.get("n_output_tokens"), field="output tokens"),
        },
        "agent_episodes": n_episodes,
        "api_request_times_msec": api_times,
        "parser_feedback_events": _parser_events(metadata),
        "timing_seconds": {
            "environment_setup": _duration_seconds(result, field="environment_setup"),
            "agent_setup": _duration_seconds(result, field="agent_setup"),
            "agent_execution": _duration_seconds(result, field="agent_execution"),
            "verifier": _duration_seconds(result, field="verifier"),
        },
        "ctrf": ctrf,
        "audit": hashes,
    }


def summarize_job(
    job_dir: Path,
    *,
    expected_model: str | None = None,
    expected_tasks: set[str] | None = None,
    expected_task_digests: dict[str, str] | None = None,
    expected_agent: dict[str, str] | None = None,
    expected_agent_configuration: dict[str, Any] | None = None,
    expected_harbor: dict[str, str] | None = None,
    expected_concurrency: int | None = None,
    quality_attributable_exceptions: frozenset[str] = frozenset(),
    protocol_identity: dict[str, str] | None = None,
    allow_invalid: bool = False,
) -> dict[str, Any]:
    if job_dir.is_symlink() or not job_dir.is_dir():
        raise InvalidHarborTrial("job directory must be a regular directory")
    if expected_task_digests is not None:
        digest_tasks = set(expected_task_digests)
        if expected_tasks is None:
            expected_tasks = digest_tasks
        elif expected_tasks != digest_tasks:
            raise InvalidHarborTrial("expected task names and digest keys disagree")
    job_result_path = job_dir / "result.json"
    job_config_path = job_dir / "config.json"
    job_lock_path = job_dir / "lock.json"
    job_result = _load_json(job_result_path)
    _load_json(job_config_path)
    job_lock = _load_json(job_lock_path)
    if job_result.get("finished_at") is None:
        raise InvalidHarborTrial("job is not finished")
    total = _integer(job_result.get("n_total_trials"), field="n_total_trials")
    stats = job_result.get("stats")
    if not isinstance(stats, dict):
        raise InvalidHarborTrial("job stats are missing")
    if _integer(stats.get("n_completed_trials"), field="n_completed_trials") != total:
        raise InvalidHarborTrial("job does not have all trials completed")
    errored_trials = _integer(stats.get("n_errored_trials"), field="n_errored_trials")
    if any(
        _integer(stats.get(field), field=field)
        for field in ("n_running_trials", "n_pending_trials", "n_cancelled_trials")
    ):
        raise InvalidHarborTrial("job contains unfinished or cancelled trial states")
    harbor = job_lock.get("harbor")
    if not isinstance(harbor, dict):
        raise InvalidHarborTrial("job lock Harbor identity is missing")
    observed_harbor = {
        "version": harbor.get("version"),
        "git_commit_hash": harbor.get("git_commit_hash"),
    }
    if expected_harbor is not None and observed_harbor != expected_harbor:
        raise InvalidHarborTrial("job Harbor version or revision does not match the protocol")
    concurrency = _integer(job_lock.get("n_concurrent_trials"), field="n_concurrent_trials")
    if expected_concurrency is not None and concurrency != expected_concurrency:
        raise InvalidHarborTrial("job concurrency does not match the protocol")

    trial_dirs: list[Path] = []
    for child in job_dir.iterdir():
        if child.is_symlink():
            raise InvalidHarborTrial(f"job contains a symlinked entry: {child.name}")
        if child.is_dir():
            trial_dirs.append(child)
    trial_dirs.sort()
    if len(trial_dirs) != total:
        raise InvalidHarborTrial("trial directory count does not match n_total_trials")
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    for trial_dir in trial_dirs:
        try:
            trial = summarize_trial(
                trial_dir,
                expected_model=expected_model,
                expected_task_digests=expected_task_digests,
                quality_attributable_exceptions=quality_attributable_exceptions,
            )
            if expected_tasks is not None and trial["task_name"] not in expected_tasks:
                raise InvalidHarborTrial("trial task is outside the expected task set")
            if expected_agent is not None and trial["agent"] != expected_agent:
                raise InvalidHarborTrial("trial agent identity does not match the protocol")
            if (
                expected_agent_configuration is not None
                and trial["agent_configuration"] != expected_agent_configuration
            ):
                raise InvalidHarborTrial("trial agent configuration does not match the protocol")
            valid.append(trial)
        except InvalidHarborTrial as exc:
            if not allow_invalid:
                raise
            invalid.append({"trial_directory": trial_dir.name, "reason": str(exc)})
    observed_quality_exceptions = sum(
        trial["quality_attributable_exception"] is not None for trial in valid
    )
    if observed_quality_exceptions != errored_trials and not allow_invalid:
        raise InvalidHarborTrial("job error count does not match quality-attributable exceptions")
    if (
        expected_tasks is not None
        and {trial["task_name"] for trial in valid} != expected_tasks
        and not allow_invalid
    ):
        raise InvalidHarborTrial("valid trials do not exactly cover the expected task set")

    successes = sum(Decimal(trial["reward"]) for trial in valid)
    return {
        "schema_version": SCHEMA_VERSION,
        "job": {
            "id": job_result.get("id"),
            "started_at": job_result.get("started_at"),
            "finished_at": job_result.get("finished_at"),
            "job_result_sha256": _sha256(job_result_path),
            "job_config_sha256": _sha256(job_config_path),
            "job_lock_sha256": _sha256(job_lock_path),
            "harbor": observed_harbor,
            "concurrency": concurrency,
        },
        "expected": {
            "model": expected_model,
            "tasks": sorted(expected_tasks) if expected_tasks is not None else None,
            "task_digests": (
                dict(sorted(expected_task_digests.items()))
                if expected_task_digests is not None
                else None
            ),
            "quality_attributable_exceptions": sorted(quality_attributable_exceptions),
        },
        "protocol": protocol_identity,
        "aggregate": {
            "valid_trials": len(valid),
            "invalid_trials": len(invalid),
            "successes": format(successes, "f"),
            "success_percent": (format(successes * 100 / len(valid), "f") if valid else None),
        },
        "trials": valid,
        "invalid": invalid,
        "contains_prompts_or_model_messages": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-model")
    parser.add_argument("--expected-task", action="append", dest="expected_tasks")
    parser.add_argument(
        "--expected-task-digest",
        action="append",
        default=[],
        metavar="TASK=sha256:HEX",
        help="Require a task's durable Harbor lock digest; repeat for each task.",
    )
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--candidate")
    parser.add_argument("--task-set")
    parser.add_argument("--allow-invalid", action="store_true")
    args = parser.parse_args()
    protocol_args = (args.protocol, args.candidate, args.task_set)
    if any(protocol_args) and not all(protocol_args):
        parser.error("--protocol, --candidate, and --task-set must be provided together")
    if all(protocol_args) and (
        args.expected_model or args.expected_tasks or args.expected_task_digest
    ):
        parser.error("protocol selection cannot be combined with manual expectations")

    expectation: PilotExpectation | None = None
    if args.protocol is not None:
        try:
            expectation = _protocol_expectation(
                args.protocol,
                candidate_name=args.candidate,
                task_set_name=args.task_set,
            )
        except InvalidHarborTrial as exc:
            parser.error(str(exc))
    expected_task_digests: dict[str, str] = {}
    for item in args.expected_task_digest:
        task, separator, digest = item.partition("=")
        if not separator or not task or not _is_sha256_digest(digest, prefixed=True):
            parser.error("--expected-task-digest must be TASK=sha256:<64 hex chars>")
        expected_task_digests[task] = digest
    expected_tasks = set(args.expected_tasks) if args.expected_tasks else None
    if expected_task_digests and expected_tasks != set(expected_task_digests):
        parser.error("--expected-task and --expected-task-digest must name the same tasks")
    try:
        summary = summarize_job(
            args.job_directory,
            expected_model=expectation.model if expectation else args.expected_model,
            expected_tasks=set(expectation.tasks) if expectation else expected_tasks,
            expected_task_digests=(
                expectation.tasks if expectation else expected_task_digests or None
            ),
            expected_agent=expectation.agent if expectation else None,
            expected_agent_configuration=(expectation.agent_configuration if expectation else None),
            expected_harbor=expectation.harbor if expectation else None,
            expected_concurrency=expectation.concurrency if expectation else None,
            quality_attributable_exceptions=(
                expectation.quality_attributable_exceptions if expectation else frozenset()
            ),
            protocol_identity=expectation.identity if expectation else None,
            allow_invalid=args.allow_invalid,
        )
    except InvalidHarborTrial as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
