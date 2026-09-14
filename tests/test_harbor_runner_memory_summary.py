from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "examples" / "local-runtime-frontiers" / "summarize_harbor_runner_memory.py"
SPEC = importlib.util.spec_from_file_location("summarize_harbor_runner_memory", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SUMMARY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SUMMARY
SPEC.loader.exec_module(SUMMARY)


def _capture(tmp_path: Path) -> Path:
    value = {
        "schema_version": "model-skyline/harbor-runner-memory/v1",
        "expected_model": "local-model",
        "process_match": "local-server",
        "job_lock_sha256": "a" * 64,
        "started_at": "2026-09-14T00:00:00Z",
        "finished_at": "2026-09-14T00:01:00Z",
        "sample_interval_seconds": "1.0",
        "job_timestamp_timezone": "UTC",
        "capture_started_after_job_start": False,
        "capture_started_before_agent_execution": {"terminal-bench/task": True},
        "task_name_form": "completed_result_name",
        "task_peaks": {
            "terminal-bench/task": {
                "rss_bytes": 4096,
                "physical_footprint_bytes": 8192,
                "samples": 2,
            }
        },
        "samples": [
            {
                "elapsed_milliseconds": 0,
                "active_tasks": ["terminal-bench/task"],
                "process_count": 1,
                "rss_bytes": 2048,
                "physical_footprint_bytes": 4096,
            },
            {
                "elapsed_milliseconds": 1000,
                "active_tasks": ["terminal-bench/task"],
                "process_count": 1,
                "rss_bytes": 4096,
                "physical_footprint_bytes": 8192,
            },
        ],
        "contains_prompts_or_model_messages": False,
        "ignored_source_only_field": "/private/machine/path",
    }
    path = tmp_path / "runner-memory.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_builds_compact_replayable_prompt_free_summary(tmp_path: Path) -> None:
    capture = _capture(tmp_path)

    result = SUMMARY.build_summary(capture)

    assert result["schema_version"] == "model-skyline/harbor-runner-memory-summary/v1"
    assert result["source_capture"] == {
        "schema_version": "model-skyline/harbor-runner-memory/v1",
        "raw_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        "raw_bytes": capture.stat().st_size,
    }
    assert result["sample_count"] == 2
    assert result["task_peaks"]["terminal-bench/task"]["physical_footprint_bytes"] == 8192
    assert "samples" not in result
    serialized = json.dumps(result)
    assert "/private/" not in serialized
    assert result["contains_prompts_or_model_messages"] is False


def test_rejects_task_peaks_that_do_not_replay(tmp_path: Path) -> None:
    capture = _capture(tmp_path)
    value = json.loads(capture.read_text(encoding="utf-8"))
    value["task_peaks"]["terminal-bench/task"]["rss_bytes"] = 1
    capture.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(SUMMARY.MemorySummaryError, match="do not replay"):
        SUMMARY.build_summary(capture)


def test_rejects_boolean_task_peak_values(tmp_path: Path) -> None:
    capture = _capture(tmp_path)
    value = json.loads(capture.read_text(encoding="utf-8"))
    value["task_peaks"]["terminal-bench/task"]["samples"] = True
    capture.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(SUMMARY.MemorySummaryError, match="non-negative integer"):
        SUMMARY.build_summary(capture)


def test_canonicalizes_legacy_basename_task_samples(tmp_path: Path) -> None:
    capture = _capture(tmp_path)
    value = json.loads(capture.read_text(encoding="utf-8"))
    value.pop("task_name_form")
    value["task_peaks"] = {"task": value["task_peaks"].pop("terminal-bench/task")}
    for sample in value["samples"]:
        sample["active_tasks"] = ["task"]
    capture.write_text(json.dumps(value), encoding="utf-8")

    result = SUMMARY.build_summary(capture)

    assert result["source_task_name_form"] == "legacy_trial_directory_basename"
    assert result["task_name_form"] == "completed_result_name"
    assert set(result["task_peaks"]) == {"terminal-bench/task"}


def test_rejects_unmarked_or_empty_source_capture(tmp_path: Path) -> None:
    capture = _capture(tmp_path)
    value = json.loads(capture.read_text(encoding="utf-8"))
    value["contains_prompts_or_model_messages"] = True
    capture.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(SUMMARY.MemorySummaryError, match="not marked prompt-free"):
        SUMMARY.build_summary(capture)


def test_cli_does_not_overwrite_source_capture(tmp_path: Path) -> None:
    capture = _capture(tmp_path)
    before = capture.read_bytes()

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--capture", str(capture), "--output", str(capture)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "must not overwrite" in result.stderr
    assert capture.read_bytes() == before
