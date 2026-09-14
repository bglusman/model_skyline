from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "examples" / "local-runtime-frontiers" / "summarize_harbor_local_job.py"
SPEC = importlib.util.spec_from_file_location("summarize_harbor_local_job", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SUMMARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUMMARY)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _job(tmp_path: Path, *, with_ctrf: bool = True, reward: float = 1.0) -> Path:
    job = tmp_path / "job"
    trial = job / "fix-git__abc"
    _write_json(job / "config.json", {"job_name": "fixture"})
    _write_json(
        job / "lock.json",
        {
            "harbor": {"version": "0.23.0", "git_commit_hash": "d" * 40},
            "n_concurrent_trials": 1,
        },
    )
    _write_json(
        job / "result.json",
        {
            "id": "job-id",
            "started_at": "2026-09-14T00:00:00Z",
            "finished_at": "2026-09-14T00:01:00Z",
            "n_total_trials": 1,
            "stats": {
                "n_completed_trials": 1,
                "n_errored_trials": 0,
                "n_running_trials": 0,
                "n_pending_trials": 0,
                "n_cancelled_trials": 0,
            },
        },
    )
    _write_json(trial / "config.json", {"trial": "fixture"})
    _write_json(
        trial / "lock.json",
        {
            "task": {"name": "fix-git", "digest": "sha256:" + ("b" * 64)},
            "agent": {
                "name": "terminus-2",
                "model_name": "openai/local-model",
                "kwargs": {
                    "parser_name": "json",
                    "temperature": 1.0,
                    "max_turns": 40,
                    "enable_summarize": True,
                    "proactive_summarization_threshold": 8192,
                    "model_info": {
                        "max_input_tokens": 114688,
                        "max_output_tokens": 16384,
                    },
                    "llm_call_kwargs": {"top_p": 0.95},
                    "store_all_messages": True,
                },
            },
        },
    )
    _write_json(trial / "agent" / "trajectory.json", {"steps": []})
    (trial / "verifier").mkdir(parents=True, exist_ok=True)
    (trial / "verifier" / "reward.txt").write_text(f"{reward}\n", encoding="utf-8")
    (trial / "verifier" / "test-stdout.txt").write_text("pytest output\n", encoding="utf-8")
    if with_ctrf:
        _write_json(
            trial / "verifier" / "ctrf.json",
            {
                "results": {
                    "tool": {"name": "pytest", "version": "8.4.1"},
                    "summary": {
                        "tests": 2,
                        "passed": 2 if reward else 1,
                        "failed": 0 if reward else 1,
                        "skipped": 0,
                        "pending": 0,
                        "other": 0,
                    },
                    "tests": [{"status": "passed"}, {"status": "passed" if reward else "failed"}],
                }
            },
        )
    _write_json(
        trial / "result.json",
        {
            "id": "trial-id",
            "trial_name": "fix-git__abc",
            "task_name": "terminal-bench/fix-git",
            "task_checksum": "a" * 64,
            "config": {"agent": {"model_name": "openai/local-model"}},
            "agent_info": {
                "name": "terminus-2",
                "version": "2.0.0",
                "model_info": {"name": "local-model", "provider": "openai"},
            },
            "agent_result": {
                "n_input_tokens": 100,
                "n_cache_tokens": 50,
                "n_output_tokens": 25,
                "metadata": {
                    "n_episodes": 2,
                    "api_request_times_msec": [12.5, 13.5],
                    "all_messages": [
                        {
                            "role": "user",
                            "content": "Previous response had parsing errors:\nERROR: bad JSON",
                        }
                    ],
                },
            },
            "verifier_result": {"rewards": {"reward": reward}},
            "exception_info": None,
            "environment_setup": {
                "started_at": "2026-09-14T00:00:00Z",
                "finished_at": "2026-09-14T00:00:01Z",
            },
            "agent_setup": {
                "started_at": "2026-09-14T00:00:01Z",
                "finished_at": "2026-09-14T00:00:03Z",
            },
            "agent_execution": {
                "started_at": "2026-09-14T00:00:03Z",
                "finished_at": "2026-09-14T00:00:33Z",
            },
            "verifier": {
                "started_at": "2026-09-14T00:00:33Z",
                "finished_at": "2026-09-14T00:00:35Z",
            },
        },
    )
    return job


def test_summarizes_valid_job_without_prompts(tmp_path: Path) -> None:
    summary = SUMMARY.summarize_job(
        _job(tmp_path),
        expected_model="local-model",
        expected_tasks={"terminal-bench/fix-git"},
        expected_task_digests={"terminal-bench/fix-git": "sha256:" + ("b" * 64)},
    )

    assert summary["aggregate"] == {
        "valid_trials": 1,
        "invalid_trials": 0,
        "successes": "1.0",
        "success_percent": "100.0",
    }
    trial = summary["trials"][0]
    assert trial["ctrf"]["passed"] == 2
    assert trial["task_lock_digest"] == "sha256:" + ("b" * 64)
    assert trial["agent_configuration"]["max_input_tokens"] == 114688
    assert trial["timing_seconds"]["agent_execution"] == "30.0"
    assert trial["parser_feedback_events"] == {"errors": 1, "warnings": 0}
    assert "all_messages" not in json.dumps(summary)
    assert summary["contains_prompts_or_model_messages"] is False


def test_missing_ctrf_is_infrastructure_invalid_not_model_failure(tmp_path: Path) -> None:
    job = _job(tmp_path, with_ctrf=False, reward=0.0)

    with pytest.raises(SUMMARY.InvalidHarborTrial, match="ctrf.json"):
        SUMMARY.summarize_job(job)

    summary = SUMMARY.summarize_job(job, allow_invalid=True)
    assert summary["aggregate"]["valid_trials"] == 0
    assert summary["aggregate"]["invalid_trials"] == 1
    assert summary["aggregate"]["success_percent"] is None
    assert summary["invalid"][0]["reason"] == "missing regular file: ctrf.json"


def test_rejects_reward_that_disagrees_with_ctrf(tmp_path: Path) -> None:
    job = _job(tmp_path, reward=1.0)
    trial = next(child for child in job.iterdir() if child.is_dir())
    result_path = trial / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["verifier_result"]["rewards"]["reward"] = 0.0
    _write_json(result_path, result)
    (trial / "verifier" / "reward.txt").write_text("0\n", encoding="utf-8")

    with pytest.raises(SUMMARY.InvalidHarborTrial, match="disagrees with the CTRF"):
        SUMMARY.summarize_job(job)


def test_rejects_model_or_task_set_mismatch(tmp_path: Path) -> None:
    job = _job(tmp_path)

    with pytest.raises(SUMMARY.InvalidHarborTrial, match="--expected-model"):
        SUMMARY.summarize_job(job, expected_model="different-model")
    with pytest.raises(SUMMARY.InvalidHarborTrial, match="outside the expected task set"):
        SUMMARY.summarize_job(job, expected_tasks={"terminal-bench/other"})


def test_rejects_task_lock_digest_mismatch(tmp_path: Path) -> None:
    job = _job(tmp_path)

    with pytest.raises(SUMMARY.InvalidHarborTrial, match="does not match the expected digest"):
        SUMMARY.summarize_job(
            job,
            expected_tasks={"terminal-bench/fix-git"},
            expected_task_digests={"terminal-bench/fix-git": "sha256:" + ("c" * 64)},
        )
