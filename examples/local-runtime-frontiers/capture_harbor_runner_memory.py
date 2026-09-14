#!/usr/bin/env python3
"""Capture prompt-free macOS process-memory samples for one serial Harbor job."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = "model-skyline/harbor-runner-memory/v1"
MAX_JSON_BYTES = 64_000_000
MAX_SAMPLES = 100_000


class MemoryCaptureError(ValueError):
    """A memory capture cannot be produced safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MemoryCaptureError(f"missing regular file: {path.name}")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise MemoryCaptureError(f"file exceeds {MAX_JSON_BYTES} bytes: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemoryCaptureError(f"invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise MemoryCaptureError(f"expected a JSON object: {path.name}")
    return value


def _physical_footprint_bytes(pid: int) -> int | None:
    if sys.platform != "darwin":
        return None
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pid_rusage = libproc.proc_pid_rusage
        proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        proc_pid_rusage.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(512)
        if proc_pid_rusage(pid, 4, buffer) != 0:
            return None
        # RUSAGE_INFO_V4. ri_phys_footprint is the eighth uint64 after the UUID.
        return int.from_bytes(buffer.raw[72:80], byteorder=sys.byteorder)
    except (AttributeError, OSError):
        return None


def _matching_process_memory_bytes(literal: str) -> tuple[int, int | None, int]:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,rss=,command="],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0, None, 0
    rss_bytes = 0
    footprints: list[int | None] = []
    for line in completed.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3 or literal not in fields[2]:
            continue
        try:
            pid = int(fields[0])
            rss_kib = int(fields[1])
        except ValueError:
            continue
        if pid == os.getpid() or "capture_harbor_runner_memory.py" in fields[2]:
            continue
        rss_bytes += rss_kib * 1024
        footprints.append(_physical_footprint_bytes(pid))
    physical = (
        sum(value for value in footprints if value is not None)
        if footprints and all(value is not None for value in footprints)
        else None
    )
    return rss_bytes, physical, len(footprints)


def _active_tasks(job_dir: Path) -> list[str]:
    tasks: list[str] = []
    for child in job_dir.iterdir():
        if child.is_symlink():
            raise MemoryCaptureError(f"job contains a symlinked entry: {child.name}")
        if (
            not child.is_dir()
            or (child / "result.json").is_file()
            or not (child / "lock.json").is_file()
        ):
            continue
        lock = _load_json(child / "lock.json")
        task = lock.get("task")
        if not isinstance(task, dict) or not isinstance(task.get("name"), str):
            raise MemoryCaptureError("active trial task identity is missing")
        tasks.append(task["name"])
    return sorted(tasks)


def _validate_job(job_dir: Path, *, expected_model: str) -> str:
    if job_dir.is_symlink() or not job_dir.is_dir():
        raise MemoryCaptureError("job directory must be a regular directory")
    lock_path = job_dir / "lock.json"
    lock = _load_json(lock_path)
    if lock.get("n_concurrent_trials") != 1:
        raise MemoryCaptureError("memory capture requires serial Harbor execution")
    trials = lock.get("trials")
    if not isinstance(trials, list) or not trials:
        raise MemoryCaptureError("job lock has no trials")
    for trial in trials:
        agent = trial.get("agent") if isinstance(trial, dict) else None
        if not isinstance(agent, dict) or agent.get("model_name") != f"openai/{expected_model}":
            raise MemoryCaptureError("job lock model does not match --expected-model")
    return _sha256(lock_path)


def _wait_for_job(job_dir: Path, *, timeout_seconds: float) -> None:
    started_ns = time.monotonic_ns()
    while not (job_dir.is_dir() and (job_dir / "lock.json").is_file()):
        if (time.monotonic_ns() - started_ns) / 1_000_000_000 > timeout_seconds:
            raise MemoryCaptureError("timed out waiting for the Harbor job lock")
        time.sleep(0.1)


def _timestamp(value: object, *, field: str, naive_timezone: ZoneInfo | None = None) -> datetime:
    if not isinstance(value, str):
        raise MemoryCaptureError(f"{field} must be an ISO 8601 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MemoryCaptureError(f"{field} must be an ISO 8601 timestamp") from exc
    if result.tzinfo is None:
        if naive_timezone is None:
            raise MemoryCaptureError(f"{field} must include a timezone")
        result = result.replace(tzinfo=naive_timezone)
    return result


def _task_coverage(
    job_dir: Path,
    *,
    capture_started_at: datetime,
    job_timezone: ZoneInfo,
) -> dict[str, bool]:
    coverage: dict[str, bool] = {}
    for child in job_dir.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        result = _load_json(child / "result.json")
        task_name = result.get("task_name")
        execution = result.get("agent_execution")
        if not isinstance(task_name, str) or not isinstance(execution, dict):
            raise MemoryCaptureError("completed trial task or timing is missing")
        started_at = _timestamp(
            execution.get("started_at"),
            field=f"{task_name}.agent_execution.started_at",
            naive_timezone=job_timezone,
        )
        coverage[task_name] = capture_started_at <= started_at
    return dict(sorted(coverage.items()))


def capture(
    job_dir: Path,
    *,
    expected_model: str,
    process_match: str,
    interval_seconds: float,
    timeout_seconds: float,
    job_timezone: ZoneInfo,
) -> dict[str, Any]:
    job_lock_sha256 = _validate_job(job_dir, expected_model=expected_model)
    started_at = datetime.now(UTC)
    started_ns = time.monotonic_ns()
    samples: list[dict[str, Any]] = []
    while True:
        rss, physical, process_count = _matching_process_memory_bytes(process_match)
        samples.append(
            {
                "elapsed_milliseconds": (time.monotonic_ns() - started_ns) // 1_000_000,
                "active_tasks": _active_tasks(job_dir),
                "process_count": process_count,
                "rss_bytes": rss,
                "physical_footprint_bytes": physical,
            }
        )
        if len(samples) >= MAX_SAMPLES:
            raise MemoryCaptureError(f"capture exceeds {MAX_SAMPLES} samples")
        result = _load_json(job_dir / "result.json")
        if result.get("finished_at") is not None:
            break
        if (time.monotonic_ns() - started_ns) / 1_000_000_000 > timeout_seconds:
            raise MemoryCaptureError("capture timed out before the Harbor job finished")
        time.sleep(interval_seconds)

    task_peaks: dict[str, dict[str, int | None]] = {}
    for sample in samples:
        for task in sample["active_tasks"]:
            peak = task_peaks.setdefault(
                task,
                {"rss_bytes": 0, "physical_footprint_bytes": None, "samples": 0},
            )
            peak["samples"] = int(peak["samples"] or 0) + 1
            peak["rss_bytes"] = max(int(peak["rss_bytes"] or 0), sample["rss_bytes"])
            physical = sample["physical_footprint_bytes"]
            if physical is not None:
                peak["physical_footprint_bytes"] = max(
                    int(peak["physical_footprint_bytes"] or 0), physical
                )

    job_result = _load_json(job_dir / "result.json")
    job_started_at = _timestamp(
        job_result.get("started_at"),
        field="job.started_at",
        naive_timezone=job_timezone,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "expected_model": expected_model,
        "process_match": process_match,
        "job_lock_sha256": job_lock_sha256,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sample_interval_seconds": str(interval_seconds),
        "job_timestamp_timezone": job_timezone.key,
        "capture_started_after_job_start": started_at > job_started_at,
        "capture_started_before_agent_execution": _task_coverage(
            job_dir,
            capture_started_at=started_at,
            job_timezone=job_timezone,
        ),
        "task_peaks": task_peaks,
        "samples": samples,
        "contains_prompts_or_model_messages": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-directory", type=Path, required=True)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--process-match", required=True)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=21_600.0)
    parser.add_argument("--wait-for-job-seconds", type=float, default=300.0)
    parser.add_argument(
        "--job-timezone",
        required=True,
        help="IANA timezone for Harbor 0.23's timezone-naive job timestamps",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.interval_seconds < 0.1 or args.interval_seconds > 60:
        parser.error("--interval-seconds must be between 0.1 and 60")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.wait_for_job_seconds < 0:
        parser.error("--wait-for-job-seconds must be non-negative")
    try:
        job_timezone = ZoneInfo(args.job_timezone)
        _wait_for_job(args.job_directory, timeout_seconds=args.wait_for_job_seconds)
        result = capture(
            args.job_directory,
            expected_model=args.expected_model,
            process_match=args.process_match,
            interval_seconds=args.interval_seconds,
            timeout_seconds=args.timeout_seconds,
            job_timezone=job_timezone,
        )
    except (MemoryCaptureError, ZoneInfoNotFoundError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
