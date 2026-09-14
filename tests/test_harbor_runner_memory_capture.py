from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "examples" / "local-runtime-frontiers" / "capture_harbor_runner_memory.py"
SPEC = importlib.util.spec_from_file_location("capture_harbor_runner_memory", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CAPTURE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CAPTURE
SPEC.loader.exec_module(CAPTURE)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _finished_job(tmp_path: Path) -> Path:
    job = tmp_path / "job"
    _write_json(
        job / "lock.json",
        {
            "n_concurrent_trials": 1,
            "trials": [{"agent": {"model_name": "openai/local-model"}}],
        },
    )
    _write_json(
        job / "result.json",
        {
            "started_at": "2026-09-14T00:00:00Z",
            "finished_at": "2026-09-14T00:01:00Z",
        },
    )
    _write_json(
        job / "task__abc" / "result.json",
        {
            "task_name": "terminal-bench/task",
            "agent_execution": {
                "started_at": "2026-09-14T00:00:10Z",
                "finished_at": "2026-09-14T00:00:20Z",
            },
        },
    )
    return job


def test_finished_job_capture_is_prompt_free_and_marks_late_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        CAPTURE,
        "_matching_process_memory_bytes",
        lambda process_match: (1024, 2048, 1),
    )

    result = CAPTURE.capture(
        _finished_job(tmp_path),
        expected_model="local-model",
        process_match="local-server",
        interval_seconds=1,
        timeout_seconds=10,
        job_timezone=ZoneInfo("UTC"),
    )

    assert result["samples"][0]["rss_bytes"] == 1024
    assert result["samples"][0]["physical_footprint_bytes"] == 2048
    assert result["capture_started_before_agent_execution"] == {"terminal-bench/task": False}
    assert result["job_timestamp_timezone"] == "UTC"
    assert result["contains_prompts_or_model_messages"] is False
    assert "terminal output" not in json.dumps(result).lower()


def test_active_task_detection_rejects_symlink(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    active = job / "task__abc"
    _write_json(active / "lock.json", {"task": {"name": "task"}})
    assert CAPTURE._active_tasks(job) == ["task"]

    outside = tmp_path / "outside"
    outside.mkdir()
    (job / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(CAPTURE.MemoryCaptureError, match="symlinked entry"):
        CAPTURE._active_tasks(job)


def test_active_task_detection_ignores_trial_until_lock_exists(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    (job / "initializing-trial").mkdir()

    assert CAPTURE._active_tasks(job) == []


def test_wait_for_job_accepts_an_existing_lock(tmp_path: Path) -> None:
    job = tmp_path / "job"
    _write_json(job / "lock.json", {})

    CAPTURE._wait_for_job(job, timeout_seconds=0)


def test_naive_harbor_timestamp_requires_explicit_timezone() -> None:
    with pytest.raises(CAPTURE.MemoryCaptureError, match="must include a timezone"):
        CAPTURE._timestamp("2026-09-14T00:00:00", field="job.started_at")

    timestamp = CAPTURE._timestamp(
        "2026-09-14T00:00:00",
        field="job.started_at",
        naive_timezone=ZoneInfo("America/New_York"),
    )
    assert timestamp.utcoffset().total_seconds() == -4 * 60 * 60
