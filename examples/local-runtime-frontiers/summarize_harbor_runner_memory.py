#!/usr/bin/env python3
"""Validate and compact a prompt-free Harbor runner-memory capture."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CAPTURE_SCHEMA = "model-skyline/harbor-runner-memory/v1"
SUMMARY_SCHEMA = "model-skyline/harbor-runner-memory-summary/v1"
MAX_JSON_BYTES = 64_000_000
MAX_SAMPLES = 100_000


class MemorySummaryError(ValueError):
    """A compact memory summary cannot be produced safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MemorySummaryError("input must be a regular non-symlink file")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise MemorySummaryError(f"input exceeds {MAX_JSON_BYTES} bytes")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemorySummaryError("input is not valid JSON") from exc
    if not isinstance(value, dict):
        raise MemorySummaryError("input must contain a JSON object")
    return value


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise MemorySummaryError(f"{field} must be an object with string keys")
    return value


def _array(value: object, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise MemorySummaryError(f"{field} must be an array")
    return value


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MemorySummaryError(f"{field} must be a non-empty string")
    return value


def _boolean(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise MemorySummaryError(f"{field} must be a boolean")
    return value


def _integer(value: object, *, field: str, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MemorySummaryError(f"{field} must be a non-negative integer")
    return value


def _sha256_string(value: object, *, field: str) -> str:
    digest = _string(value, field=field)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise MemorySummaryError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _timestamp(value: object, *, field: str) -> str:
    raw = _string(value, field=field)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MemorySummaryError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise MemorySummaryError(f"{field} must include a timezone")
    return raw


def _decimal_string(value: object, *, field: str) -> str:
    raw = _string(value, field=field)
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise MemorySummaryError(f"{field} must be a finite positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise MemorySummaryError(f"{field} must be a finite positive decimal")
    return raw


def _validated_peaks(
    capture: dict[str, Any], *, completed_tasks: set[str]
) -> tuple[dict[str, dict[str, int | None]], int, str]:
    by_basename: dict[str, str] = {}
    for task in completed_tasks:
        basename = task.rsplit("/", 1)[-1]
        if basename in by_basename:
            raise MemorySummaryError("completed task basenames must be unique")
        by_basename[basename] = task
    used_legacy_basename = False

    def canonical_task_name(task: str) -> str:
        nonlocal used_legacy_basename
        if task in completed_tasks:
            return task
        canonical = by_basename.get(task)
        if canonical is None:
            raise MemorySummaryError("sample active task has no completed task")
        used_legacy_basename = True
        return canonical

    samples = _array(capture.get("samples"), field="samples")
    if not samples or len(samples) > MAX_SAMPLES:
        raise MemorySummaryError(f"samples must contain between 1 and {MAX_SAMPLES} entries")
    peaks: dict[str, dict[str, int | None]] = {}
    prior_elapsed = -1
    for index, value in enumerate(samples):
        sample = _mapping(value, field=f"samples[{index}]")
        elapsed = _integer(sample.get("elapsed_milliseconds"), field=f"samples[{index}].elapsed")
        assert elapsed is not None
        if elapsed < prior_elapsed:
            raise MemorySummaryError("sample elapsed times must be non-decreasing")
        prior_elapsed = elapsed
        active = _array(sample.get("active_tasks"), field=f"samples[{index}].active_tasks")
        if any(not isinstance(task, str) or not task for task in active):
            raise MemorySummaryError("sample active task names must be non-empty strings")
        canonical_active = [canonical_task_name(task) for task in active]
        if len(canonical_active) != len(set(canonical_active)):
            raise MemorySummaryError("sample active tasks must be unique completed tasks")
        _integer(sample.get("process_count"), field=f"samples[{index}].process_count")
        rss = _integer(sample.get("rss_bytes"), field=f"samples[{index}].rss_bytes")
        physical = _integer(
            sample.get("physical_footprint_bytes"),
            field=f"samples[{index}].physical_footprint_bytes",
            allow_none=True,
        )
        assert rss is not None
        for task in canonical_active:
            peak = peaks.setdefault(
                task,
                {"rss_bytes": 0, "physical_footprint_bytes": None, "samples": 0},
            )
            peak["samples"] = int(peak["samples"] or 0) + 1
            peak["rss_bytes"] = max(int(peak["rss_bytes"] or 0), rss)
            if physical is not None:
                peak["physical_footprint_bytes"] = max(
                    int(peak["physical_footprint_bytes"] or 0), physical
                )

    recorded_raw = _mapping(capture.get("task_peaks"), field="task_peaks")
    recorded: dict[str, Any] = {}
    for task, peak in recorded_raw.items():
        canonical = canonical_task_name(task)
        if canonical in recorded:
            raise MemorySummaryError("task_peaks contain duplicate canonical tasks")
        recorded[canonical] = peak
    if dict(sorted(recorded.items())) != dict(sorted(peaks.items())):
        raise MemorySummaryError("task_peaks do not replay from the source samples")
    declared_name_form = capture.get("task_name_form")
    if declared_name_form not in {None, "completed_result_name"}:
        raise MemorySummaryError("unsupported task_name_form")
    if declared_name_form == "completed_result_name" and used_legacy_basename:
        raise MemorySummaryError("task_name_form disagrees with source task names")
    source_name_form = (
        "legacy_trial_directory_basename" if used_legacy_basename else "completed_result_name"
    )
    return dict(sorted(peaks.items())), len(samples), source_name_form


def build_summary(capture_path: Path) -> dict[str, Any]:
    capture = _load_json(capture_path)
    if capture.get("schema_version") != CAPTURE_SCHEMA:
        raise MemorySummaryError("unsupported source capture schema_version")
    if capture.get("contains_prompts_or_model_messages") is not False:
        raise MemorySummaryError("source capture is not marked prompt-free")

    coverage_raw = _mapping(
        capture.get("capture_started_before_agent_execution"),
        field="capture_started_before_agent_execution",
    )
    if not coverage_raw or any(not isinstance(value, bool) for value in coverage_raw.values()):
        raise MemorySummaryError("task coverage must contain boolean values")
    coverage = dict(sorted(coverage_raw.items()))
    peaks, sample_count, source_task_name_form = _validated_peaks(
        capture, completed_tasks=set(coverage)
    )

    timezone = _string(capture.get("job_timestamp_timezone"), field="job_timestamp_timezone")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise MemorySummaryError("job_timestamp_timezone must be an IANA timezone") from exc

    return {
        "schema_version": SUMMARY_SCHEMA,
        "source_capture": {
            "schema_version": CAPTURE_SCHEMA,
            "raw_sha256": _sha256(capture_path),
            "raw_bytes": capture_path.stat().st_size,
        },
        "expected_model": _string(capture.get("expected_model"), field="expected_model"),
        "process_match": _string(capture.get("process_match"), field="process_match"),
        "job_lock_sha256": _sha256_string(capture.get("job_lock_sha256"), field="job_lock_sha256"),
        "started_at": _timestamp(capture.get("started_at"), field="started_at"),
        "finished_at": _timestamp(capture.get("finished_at"), field="finished_at"),
        "sample_interval_seconds": _decimal_string(
            capture.get("sample_interval_seconds"), field="sample_interval_seconds"
        ),
        "sample_count": sample_count,
        "job_timestamp_timezone": timezone,
        "capture_started_after_job_start": _boolean(
            capture.get("capture_started_after_job_start"),
            field="capture_started_after_job_start",
        ),
        "capture_started_before_agent_execution": coverage,
        "source_task_name_form": source_task_name_form,
        "task_name_form": "completed_result_name",
        "task_peaks": peaks,
        "contains_prompts_or_model_messages": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = build_summary(args.capture)
    except (OSError, MemorySummaryError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
