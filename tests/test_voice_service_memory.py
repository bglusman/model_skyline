from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "voice-runtime-frontiers"
CAPTURE = EXAMPLE / "capture_service_memory.py"
BUILDER = EXAMPLE / "build_tts_service_memory_panel.py"
MEMORY_RESULT = EXAMPLE / "raw" / "tts-service-memory-panel-v1-results.json"
CATALOG = EXAMPLE / "observations.json"
MEMORY_FRONTIER = EXAMPLE / "generated" / "tts-small-resident-intelligibility.json"


def _load_capture_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_service_memory", CAPTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_process_selection_includes_descendants_but_not_sampler_ancestors() -> None:
    module = _load_capture_module()
    process = module.ProcessInfo
    table = {
        1: process(1, 0, 1, "launchd"),
        10: process(10, 1, 10, "interactive shell with service-token argument"),
        11: process(11, 10, 10, "capture_service_memory.py --process-match service-token"),
        20: process(20, 1, 20, "python public-service-token-server"),
        21: process(21, 20, 21, "worker without copied command arguments"),
        30: process(30, 1, 30, "unrelated process"),
    }

    selected = module._selected_pids(
        table,
        root_pids=set(),
        process_matches=("public-service-token",),
        sampler_pid=11,
    )

    assert selected == {20, 21}


def test_combined_peak_uses_one_sample_and_retains_component_peaks() -> None:
    module = _load_capture_module()
    samples = [
        {
            "elapsed_milliseconds": 100,
            "selected_process_count": 2,
            "rss_bytes": 80,
            "host_physical_bytes": 10,
            "device_memory_bytes": 100,
            "combined_capacity_bytes": 110,
        },
        {
            "elapsed_milliseconds": 200,
            "selected_process_count": 3,
            "rss_bytes": 95,
            "host_physical_bytes": 90,
            "device_memory_bytes": 30,
            "combined_capacity_bytes": 120,
        },
    ]

    summary = module._summarize(samples)

    assert summary["peak_host_physical_bytes"] == 90
    assert summary["peak_device_memory_bytes"] == 100
    assert summary["peak_combined_capacity_bytes"] == 120
    assert summary["peak_combined_elapsed_milliseconds"] == 200
    assert summary["host_bytes_at_combined_peak"] == 90
    assert summary["device_bytes_at_combined_peak"] == 30


def test_published_service_memory_panel_replays_raw_hashes() -> None:
    result = subprocess.run(
        [sys.executable, str(BUILDER), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    panel = json.loads(MEMORY_RESULT.read_text(encoding="utf-8"))
    assert panel["panel"]["capture_harness"] == {
        "binding": "post-capture content digest of the unchanged sampler used for every row",
        "path": "capture_service_memory.py",
        "sha256": hashlib.sha256(CAPTURE.read_bytes()).hexdigest(),
    }
    assert panel["panel"]["seeds"] == [7, 1234, 2026]
    assert len(panel["offerings"]) == 5
    for offering in panel["offerings"]:
        memory = offering["memory_capture"]
        memory_path = EXAMPLE / memory["path"]
        assert hashlib.sha256(memory_path.read_bytes()).hexdigest() == memory["sha256"]
        assert [item["seed"] for item in offering["workload_sources"]] == [7, 1234, 2026]
        for source in offering["workload_sources"]:
            source_path = EXAMPLE / source["path"]
            assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source["sha256"]
        for path in [
            memory_path,
            *(EXAMPLE / item["path"] for item in offering["workload_sources"]),
        ]:
            serialized = path.read_text(encoding="utf-8")
            assert "192.168." not in serialized
            assert "/Users/" not in serialized
            assert "/root/" not in serialized


def test_published_service_memory_peaks_keep_split_components() -> None:
    panel = json.loads(MEMORY_RESULT.read_text(encoding="utf-8"))
    offerings = {item["slug"]: item for item in panel["offerings"]}

    mlx = offerings["qwen3-tts-1.7b-6bit-mlx-m5"]
    assert mlx["memory_architecture"] == "apple_unified"
    assert mlx["summary"]["peak_combined_capacity_bytes"] == 3_951_873_624
    assert mlx["summary"]["peak_device_memory_bytes"] is None

    nari = offerings["qwen3-tts-1.7b-bf16-nari-router-5060"]
    assert nari["memory_architecture"] == "linux_split_cuda"
    assert nari["summary"]["peak_combined_capacity_bytes"] == 9_728_091_136
    assert nari["summary"]["peak_host_physical_bytes"] == 5_508_613_120
    assert nari["summary"]["peak_device_memory_bytes"] == 6_905_921_536

    vllm = offerings["qwen3-tts-1.7b-bf16-vllm-omni-5060"]
    assert vllm["summary"]["peak_combined_capacity_bytes"] == 18_844_922_880
    assert vllm["summary"]["host_bytes_at_combined_peak"] == 7_312_684_032
    assert vllm["summary"]["device_bytes_at_combined_peak"] == 11_532_238_848


def test_service_memory_frontier_keeps_quality_gate_separate_from_axes() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    offerings = {item["offering"]["offering_id"]: item for item in catalog["offerings"]}
    nari_id = "self-hosted/qwen3-tts-1.7b-bf16-nari-consumer@rtx5060ti-16gb-vivian-en"
    assert offerings[nari_id]["signals"]["tts_peak_service_capacity_bytes"]["value"] == (
        "9728091136"
    )

    snapshot = json.loads(MEMORY_FRONTIER.read_text(encoding="utf-8"))
    assert [axis["metric"] for axis in snapshot["axes"]] == [
        "tts_corpus_wer",
        "tts_peak_service_capacity",
    ]
    assert {item["offering"]["offering_id"] for item in snapshot["members"]} == {
        nari_id,
        "self-hosted/qwen3-tts-1.7b-bf16-vllm-omni@rtx5060ti-16gb-vivian-en",
    }
    assert len(snapshot["rejected"]) == 3
    assert all("tts_invalid_case_rate" in item["reasons"][0] for item in snapshot["rejected"])
