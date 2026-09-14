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


def _memory_capture(tmp_path: Path, summary: dict[str, object]) -> Path:
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
    path = tmp_path / "memory.json"
    _write_json(path, value)
    return path


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
