from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from model_skyline.engine import FrontierEngine
from model_skyline.io import load_config
from model_skyline.models import UncertaintyMode

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "local-runtime-frontiers"
SCRIPT = EXAMPLE / "normalize_harbor_pilot.py"
PROTOCOL = EXAMPLE / "harbor-quality-pilot.yaml"
HARDWARE = EXAMPLE / "hardware" / "macbook-m5max-64.json"
SMOKE = EXAMPLE / "raw" / "harbor-smoke-ornith15-baseline-f16kv-fix-git-summary.json"
SPEC = importlib.util.spec_from_file_location("normalize_harbor_pilot", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
NORMALIZER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = NORMALIZER
SPEC.loader.exec_module(NORMALIZER)


def test_repeated_frontiers_require_bounds_and_robust_dominance() -> None:
    config = load_config(EXAMPLE / "harbor-repeated-frontiers.yaml")

    assert set(config.frontiers) == {
        "repeated-local-agent-quality-latency",
        "repeated-local-agent-quality-memory",
        "repeated-local-agent-quality-cache-efficiency",
    }
    assert all(
        frontier.uncertainty is UncertaintyMode.ROBUST for frontier in config.frontiers.values()
    )
    assert all(
        metric.requirements.require_bounds and metric.requirements.minimum_samples == 25
        for metric in config.metrics.values()
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _pilot_summary(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    protocol = yaml.safe_load(PROTOCOL.read_text(encoding="utf-8"))
    task_set = protocol["task_sets"]["pilot_5"]
    candidate = protocol["candidates"]["ornith15_baseline"]
    smoke = json.loads(SMOKE.read_text(encoding="utf-8"))
    tasks = task_set["tasks"]
    walls = [10, 20, 30, 40, 50]
    trials = []
    for index, (task, wall) in enumerate(zip(tasks, walls, strict=True)):
        trial = deepcopy(smoke["trials"][0])
        trial["task_name"] = task["name"]
        trial["task_lock_digest"] = task["digest"]
        trial["trial_id"] = f"trial-{index}"
        trial["trial_name"] = f"task-{index}"
        trial["reward"] = "1.0" if index < 3 else "0.0"
        trial["timing_seconds"] = {
            "environment_setup": "1",
            "agent_setup": "1",
            "agent_execution": str(wall - 3),
            "verifier": "1",
        }
        trial["tokens"] = {"input": 1000, "cache": 100, "output": 10}
        trials.append(trial)
    smoke["aggregate"] = {
        "invalid_trials": 0,
        "valid_trials": 5,
        "successes": "3.0",
        "success_percent": "60.0",
    }
    smoke["expected"] = {
        "model": candidate["route"],
        "quality_attributable_exceptions": protocol["harness"]["quality_attributable_exceptions"],
        "tasks": [task["name"] for task in tasks],
        "task_digests": {task["name"]: task["digest"] for task in tasks},
    }
    smoke["job"]["id"] = "pilot-job"
    smoke["job"]["job_lock_sha256"] = "e" * 64
    smoke["job"]["finished_at"] = "2026-09-13T22:00:00"
    smoke["protocol"] = {
        "candidate": "ornith15_baseline",
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "system_profile": candidate["system_profile"],
        "system_profile_sha256": candidate["system_profile_sha256"],
        "task_set": "pilot_5",
    }
    smoke["trials"] = trials
    path = tmp_path / "summary.json"
    _write_json(path, smoke)
    return path, smoke


def _memory_capture(
    tmp_path: Path, summary: dict[str, object], *, filename: str = "memory.json"
) -> Path:
    protocol = yaml.safe_load(PROTOCOL.read_text(encoding="utf-8"))
    tasks = [task["name"] for task in protocol["task_sets"]["pilot_5"]["tasks"]]
    job = summary["job"]
    expected = summary["expected"]
    assert isinstance(job, dict) and isinstance(expected, dict)
    value = {
        "schema_version": "model-skyline/harbor-runner-memory/v1",
        "expected_model": expected["model"],
        "process_match": "omlx-server",
        "job_lock_sha256": job["job_lock_sha256"],
        "started_at": "2026-09-14T01:00:00Z",
        "finished_at": "2026-09-14T02:01:00Z",
        "sample_interval_seconds": "1.0",
        "job_timestamp_timezone": "America/New_York",
        "capture_started_after_job_start": False,
        "capture_started_before_agent_execution": dict.fromkeys(tasks, True),
        "task_peaks": {
            task: {
                "rss_bytes": 10_000 + index,
                "physical_footprint_bytes": 20_000 + index * 1_000,
                "samples": 10,
            }
            for index, task in enumerate(tasks)
        },
        "samples": [],
        "contains_prompts_or_model_messages": False,
    }
    path = tmp_path / filename
    _write_json(path, value)
    return path


def _repeat_summary(
    tmp_path: Path,
    summary: dict[str, object],
    *,
    successes: int,
    run_index: int = 1,
    filename: str = "summary-repeat.json",
) -> tuple[Path, dict[str, object]]:
    repeated = deepcopy(summary)
    job = repeated["job"]
    trials = repeated["trials"]
    assert isinstance(job, dict) and isinstance(trials, list)
    job["id"] = f"pilot-job-repeat-{run_index}"
    job["job_lock_sha256"] = format(run_index, "x") * 64
    job["finished_at"] = "2026-09-13T23:00:00"
    for index, trial in enumerate(trials):
        assert isinstance(trial, dict)
        trial["trial_id"] = f"repeat-{run_index}-trial-{index}"
        trial["trial_name"] = f"repeat-{run_index}-task-{index}"
        trial["reward"] = "1.0" if index < successes else "0.0"
        trial["tokens"] = {"input": 2000, "cache": 500, "output": 20}
    repeated["aggregate"] = {
        "invalid_trials": 0,
        "valid_trials": 5,
        "successes": f"{successes}.0",
        "success_percent": f"{successes * 20}.0",
    }
    path = tmp_path / filename
    _write_json(path, repeated)
    return path, repeated


def test_builds_exact_quality_latency_cache_and_memory_catalog(tmp_path: Path) -> None:
    summary_path, summary = _pilot_summary(tmp_path)
    memory_path = _memory_capture(tmp_path, summary)

    catalog = NORMALIZER.build_catalog(
        protocol_path=PROTOCOL,
        hardware_path=HARDWARE,
        task_set_name="pilot_5",
        summary_paths=[summary_path],
        memory_paths=[memory_path],
    )

    assert catalog.workload.id == "terminal-bench-2.1-local-pilot-5@7131e437"
    offering = catalog.offerings[0]
    assert offering.offering.agent_harness.startswith("harbor/terminus-2@2.0.0+")
    assert offering.signals["local_pilot_task_success_percent"].value == 60
    assert offering.signals["local_pilot_p95_task_wall_seconds"].value == 48
    assert offering.signals["local_pilot_p95_successful_task_wall_seconds"].value == 29
    assert offering.signals["local_pilot_total_uncached_input_tokens"].value == 4500
    assert offering.signals["local_pilot_cache_reuse_percent"].value == 10
    assert offering.signals["local_peak_process_physical_footprint_bytes"].value == 24_000
    assert offering.metadata["pilot"]["token_accounting"]["eligible"] is True
    assert offering.metadata["pilot"]["memory"]["eligible"] is True

    config = load_config(EXAMPLE / "frontiers.yaml")
    generated_at = datetime(2026, 9, 14, 3, tzinfo=UTC)
    for frontier_id in (
        "local-agent-quality-latency",
        "local-agent-quality-memory",
        "local-agent-quality-cache-efficiency",
    ):
        snapshot = FrontierEngine().calculate(
            config, catalog, frontier_id, generated_at=generated_at
        )
        assert [member.offering for member in snapshot.members] == [offering.offering]


def test_partial_memory_capture_is_retained_but_not_emitted_as_an_axis(tmp_path: Path) -> None:
    summary_path, summary = _pilot_summary(tmp_path)
    memory_path = _memory_capture(tmp_path, summary)
    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    first_task = next(iter(memory["capture_started_before_agent_execution"]))
    memory["capture_started_before_agent_execution"][first_task] = False
    _write_json(memory_path, memory)

    catalog = NORMALIZER.build_catalog(
        protocol_path=PROTOCOL,
        hardware_path=HARDWARE,
        task_set_name="pilot_5",
        summary_paths=[summary_path],
        memory_paths=[memory_path],
    )

    offering = catalog.offerings[0]
    assert "local_peak_process_physical_footprint_bytes" not in offering.signals
    assert offering.metadata["pilot"]["memory"]["eligible"] is False


def test_equal_repetitions_are_pooled_with_run_ranges_and_fair_token_axes(
    tmp_path: Path,
) -> None:
    summary_path, summary = _pilot_summary(tmp_path)
    repeat_path, repeated = _repeat_summary(tmp_path, summary, successes=4)
    memory_path = _memory_capture(tmp_path, summary, filename="memory-first.json")
    repeat_memory_path = _memory_capture(tmp_path, repeated, filename="memory-repeat.json")
    repeat_memory = json.loads(repeat_memory_path.read_text(encoding="utf-8"))
    for peak in repeat_memory["task_peaks"].values():
        peak["physical_footprint_bytes"] += 10_000
    _write_json(repeat_memory_path, repeat_memory)

    catalog = NORMALIZER.build_catalog(
        protocol_path=PROTOCOL,
        hardware_path=HARDWARE,
        task_set_name="pilot_5",
        summary_paths=[repeat_path, summary_path],
        memory_paths=[repeat_memory_path, memory_path],
    )

    assert catalog.workload.version.endswith("+attempts-2")
    offering = catalog.offerings[0]
    quality = offering.signals["local_pilot_task_success_percent"]
    assert (quality.value, quality.lower, quality.upper, quality.sample_count) == (70, 60, 80, 10)
    wall = offering.signals["local_pilot_p95_task_wall_seconds"]
    assert (wall.value, wall.lower, wall.upper, wall.sample_count) == (50, 48, 50, 10)
    uncached = offering.signals["local_pilot_total_uncached_input_tokens"]
    assert (uncached.value, uncached.lower, uncached.upper) == (6000, 4500, 7500)
    cache = offering.signals["local_pilot_cache_reuse_percent"]
    assert (cache.value, cache.lower, cache.upper) == (20, 10, 25)
    output = offering.signals["local_pilot_total_output_tokens"]
    assert (output.value, output.lower, output.upper) == (75, 50, 100)
    memory = offering.signals["local_peak_process_physical_footprint_bytes"]
    assert (memory.value, memory.lower, memory.upper, memory.sample_count) == (
        34_000,
        24_000,
        34_000,
        10,
    )
    assert offering.metadata["workload"]["attempts_per_task"] == 2
    assert offering.metadata["pilot"]["run_count"] == 2
    assert len(offering.metadata["pilot"]["summary_sha256s"]) == 2
    assert (
        offering.metadata["pilot"]["token_accounting"]["axis_aggregation"]
        == "mean_per_task_set_run"
    )
    assert "summary_sha256" not in offering.metadata["pilot"]


def test_five_run_catalog_is_eligible_for_robust_repeated_frontier(tmp_path: Path) -> None:
    summary_path, summary = _pilot_summary(tmp_path)
    summary_paths = [summary_path]
    for run_index, successes in enumerate((4, 3, 4, 3), start=1):
        path, _ = _repeat_summary(
            tmp_path,
            summary,
            successes=successes,
            run_index=run_index,
            filename=f"summary-repeat-{run_index}.json",
        )
        summary_paths.append(path)

    catalog = NORMALIZER.build_catalog(
        protocol_path=PROTOCOL,
        hardware_path=HARDWARE,
        task_set_name="pilot_5",
        summary_paths=summary_paths,
        memory_paths=[],
    )

    assert catalog.workload.version.endswith("+attempts-5")
    quality = catalog.offerings[0].signals["local_pilot_task_success_percent"]
    assert (quality.value, quality.lower, quality.upper, quality.sample_count) == (
        68,
        60,
        80,
        25,
    )
    snapshot = FrontierEngine().calculate(
        load_config(EXAMPLE / "harbor-repeated-frontiers.yaml"),
        catalog,
        "repeated-local-agent-quality-latency",
        generated_at=datetime(2026, 9, 14, 3, tzinfo=UTC),
    )
    assert [member.offering for member in snapshot.members] == [catalog.offerings[0].offering]


def test_candidates_must_have_equal_repetition_counts(tmp_path: Path) -> None:
    summary_path, summary = _pilot_summary(tmp_path)
    repeat_path, _ = _repeat_summary(tmp_path, summary, successes=1)
    other = deepcopy(summary)
    protocol = yaml.safe_load(PROTOCOL.read_text(encoding="utf-8"))
    candidate = protocol["candidates"]["qwen38_baseline"]
    other["protocol"]["candidate"] = "qwen38_baseline"
    other["protocol"]["system_profile"] = candidate["system_profile"]
    other["protocol"]["system_profile_sha256"] = candidate["system_profile_sha256"]
    other["job"]["id"] = "other-candidate-job"
    other["job"]["job_lock_sha256"] = "a" * 64
    other_path = tmp_path / "other-summary.json"
    _write_json(other_path, other)

    with pytest.raises(NORMALIZER.PilotCatalogError, match="same number"):
        NORMALIZER.build_catalog(
            protocol_path=PROTOCOL,
            hardware_path=HARDWARE,
            task_set_name="pilot_5",
            summary_paths=[summary_path, repeat_path, other_path],
            memory_paths=[],
        )


def test_incomplete_api_request_makes_exact_token_demand_ineligible(tmp_path: Path) -> None:
    summary_path, summary = _pilot_summary(tmp_path)
    trials = summary["trials"]
    assert isinstance(trials, list) and isinstance(trials[0], dict)
    trials[0]["incomplete_api_requests"] = 1
    _write_json(summary_path, summary)

    catalog = NORMALIZER.build_catalog(
        protocol_path=PROTOCOL,
        hardware_path=HARDWARE,
        task_set_name="pilot_5",
        summary_paths=[summary_path],
        memory_paths=[],
    )

    offering = catalog.offerings[0]
    assert "local_pilot_total_uncached_input_tokens" not in offering.signals
    assert "local_pilot_cache_reuse_percent" not in offering.signals
    assert "local_pilot_total_output_tokens" not in offering.signals
    assert offering.metadata["pilot"]["token_accounting"] == {
        "eligible": False,
        "ineligibility_reasons": ["incomplete_api_requests:1"],
        "incomplete_api_requests": 1,
        "recorded_input_tokens_lower_bound": 5000,
        "recorded_cache_tokens_lower_bound": 500,
        "recorded_uncached_input_tokens_lower_bound": 4500,
        "recorded_output_tokens_lower_bound": 50,
    }
    snapshot = FrontierEngine().calculate(
        load_config(EXAMPLE / "frontiers.yaml"),
        catalog,
        "local-agent-quality-cache-efficiency",
        generated_at=datetime(2026, 9, 14, 3, tzinfo=UTC),
    )
    assert snapshot.members == ()


def test_memory_capture_must_match_summary_job_lock(tmp_path: Path) -> None:
    summary_path, summary = _pilot_summary(tmp_path)
    memory_path = _memory_capture(tmp_path, summary)
    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    memory["job_lock_sha256"] = "f" * 64
    _write_json(memory_path, memory)

    with pytest.raises(NORMALIZER.PilotCatalogError, match="does not match the summary job"):
        NORMALIZER.build_catalog(
            protocol_path=PROTOCOL,
            hardware_path=HARDWARE,
            task_set_name="pilot_5",
            summary_paths=[summary_path],
            memory_paths=[memory_path],
        )


def test_full_task_manifest_is_available_to_normalization() -> None:
    protocol = yaml.safe_load(PROTOCOL.read_text(encoding="utf-8"))

    tasks = NORMALIZER._task_digests(EXAMPLE, protocol["task_sets"]["all_89"])

    assert len(tasks) == 89
    assert tasks["terminal-bench/fix-git"].startswith("sha256:")


def test_legacy_basename_memory_peaks_map_to_unique_completed_tasks(tmp_path: Path) -> None:
    summary_path, summary = _pilot_summary(tmp_path)
    memory_path = _memory_capture(tmp_path, summary)
    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    memory["task_peaks"] = {
        task.rsplit("/", 1)[-1]: peak for task, peak in memory["task_peaks"].items()
    }
    _write_json(memory_path, memory)

    catalog = NORMALIZER.build_catalog(
        protocol_path=PROTOCOL,
        hardware_path=HARDWARE,
        task_set_name="pilot_5",
        summary_paths=[summary_path],
        memory_paths=[memory_path],
    )

    offering = catalog.offerings[0]
    assert offering.signals["local_peak_process_physical_footprint_bytes"].value == 24_000
    assert offering.metadata["pilot"]["memory"]["legacy_basename_task_peaks"] is True
