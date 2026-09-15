from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "voice-runtime-frontiers"
CAPTURE = EXAMPLE / "capture_service_memory.py"
CAPTURE_V2 = EXAMPLE / "capture_service_memory_v2.py"
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


def _load_capture_v2_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_service_memory_v2", CAPTURE_V2)
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


def test_v2_cuda_rows_are_strictly_parsed() -> None:
    module = _load_capture_v2_module()

    assert module._parse_cuda_process_rows("100, 2\n101, 3\n") == [
        (100, 2 * 1024 * 1024),
        (101, 3 * 1024 * 1024),
    ]
    with pytest.raises(module.CaptureError, match="non-numeric"):
        module._parse_cuda_process_rows("100, unavailable\n")


def test_v2_parses_linux_swap_used_bytes() -> None:
    module = _load_capture_v2_module()

    assert (
        module._parse_linux_swap_used_bytes(
            "MemTotal: 100 kB\nSwapTotal: 4096 kB\nSwapFree: 1024 kB\n"
        )
        == 3 * 1024 * 1024
    )
    with pytest.raises(module.CaptureError, match="missing swap totals"):
        module._parse_linux_swap_used_bytes("SwapTotal: 4096 kB\n")
    with pytest.raises(module.CaptureError, match="more free swap"):
        module._parse_linux_swap_used_bytes("SwapTotal: 1 kB\nSwapFree: 2 kB\n")


def test_v2_parses_darwin_swap_used_bytes() -> None:
    module = _load_capture_v2_module()

    assert module._parse_darwin_swap_used_bytes("total = 8.00G  used = 1.50G  free = 6.50G") == (
        1536 * 1024 * 1024
    )
    with pytest.raises(module.CaptureError, match="unparseable"):
        module._parse_darwin_swap_used_bytes("total = 8.00G")


def test_v2_rejects_cuda_memory_outside_selected_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_capture_v2_module()
    monkeypatch.setattr(
        module,
        "_query_cuda_process_rows",
        lambda: [(100, 2 * 1024 * 1024), (101, 3 * 1024 * 1024)],
    )

    with pytest.raises(module.CaptureError, match="1 unselected compute process"):
        module._cuda_process_memory_bytes({100})


def test_v2_accepts_only_selected_cuda_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_capture_v2_module()
    monkeypatch.setattr(
        module,
        "_query_cuda_process_rows",
        lambda: [(100, 2 * 1024 * 1024), (101, 3 * 1024 * 1024)],
    )

    assert module._cuda_process_memory_bytes({100, 101}) == 5 * 1024 * 1024


def test_v2_cuda_preflight_rejects_before_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_capture_v2_module()
    marker = tmp_path / "command-started"
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.v1, "_process_table", lambda: {})
    monkeypatch.setattr(module.v1, "_selected_pids", lambda *args, **kwargs: set())
    monkeypatch.setattr(module, "_host_swap_used_bytes", lambda: 0)

    def reject_unselected(_selected_pids: set[int]) -> int:
        raise module.CaptureError("unselected CUDA owner")

    monkeypatch.setattr(module, "_cuda_process_memory_bytes", reject_unselected)

    with pytest.raises(module.CaptureError, match="unselected CUDA owner"):
        module.capture(
            architecture="linux_split_cuda",
            hardware_label="test-gpu",
            offering_id="test/offering",
            process_label="marker",
            process_matches=(),
            root_pids=set(),
            interval_seconds=0.05,
            wait_for_process_seconds=1,
            timeout_seconds=1,
            stop_file=None,
            workload_captures=[],
            command=[sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
        )

    assert not marker.exists()


def test_v2_preserves_a_launched_commands_failure_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_capture_v2_module()
    process = module.v1.ProcessInfo
    tables = [{4321: process(4321, 1, 10, "test command")}, {}]

    class FinishedChild:
        pid = 4321
        returncode = 7
        poll_count = 0

        def poll(self) -> int | None:
            self.poll_count += 1
            return None if self.poll_count == 1 else self.returncode

    child = FinishedChild()
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(module.v1, "_process_table", lambda: tables.pop(0))
    monkeypatch.setattr(module, "_host_swap_used_bytes", lambda: 20)
    monkeypatch.setattr(
        module,
        "_sample",
        lambda **kwargs: {
            "elapsed_milliseconds": 0,
            "selected_process_count": 1,
            "rss_bytes": 10,
            "host_physical_bytes": 10,
            "device_memory_bytes": None,
            "combined_capacity_bytes": 10,
            "unselected_cuda_process_count": None,
            "unselected_cuda_memory_bytes": None,
            "host_swap_used_bytes": 20,
        },
    )

    payload, returncode = module.capture(
        architecture="apple_unified",
        hardware_label="test-mac",
        offering_id="test/offering",
        process_label="command",
        process_matches=(),
        root_pids=set(),
        interval_seconds=0.05,
        wait_for_process_seconds=1,
        timeout_seconds=1,
        stop_file=None,
        workload_captures=[],
        command=["test-command"],
    )

    assert payload["stop_reason"] == "launched command exited"
    assert payload["child_returncode"] == 7
    assert returncode == 7
    assert payload["summary"]["host_swap_delta_bytes"] == 0
    assert payload["summary"]["peak_host_swap_growth_bytes"] == 0


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
