from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).parents[1]
BASE = ROOT / "examples" / "local-runtime-frontiers"
RAW_PREFIX = "qwen35-9b-q6k-5060"
MODEL_ID = "Qwen/Qwen3.5-9B"


def _load(relative_path: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((BASE / relative_path).read_text(encoding="utf-8")),
    )


def test_system_profile_pins_the_exact_q6_route() -> None:
    profile = _load("system-profiles/qwen35-9b-q6k-llamacpp-cuda-ctx128k-q8kv.json")
    artifact = profile["artifact"]
    runtime = profile["runtime"]

    assert profile["served_model"] == "qwen35-9b-q6k-ctx128k-q8kv-load-none"
    assert artifact["model_id"] == MODEL_ID
    assert artifact["quantization"] == "Q6_K"
    assert artifact["size_bytes"] == 7_359_230_048
    assert artifact["content_sha256"] == (
        "b4fed2983efc25bdc63c74f76bc5dfe8f3a434dfa45e1ba17aadecf19eb3161b"
    )
    assert runtime["context_capacity_tokens"] == 131_072
    assert runtime["kv_cache"] == "q8_0/q8_0"
    assert runtime["configuration"]["load_mode"] == "none"
    assert runtime["configuration"]["fit"] == "off"


def test_router_manifest_registers_the_validated_route_in_the_exclusive_group() -> None:
    router = cast(
        dict[str, Any],
        yaml.safe_load((BASE / "inference-vm-router" / "config.yaml").read_text(encoding="utf-8")),
    )
    model_id = "qwen35-9b-q6k-ctx128k-q8kv-load-none"
    model = router["models"][model_id]
    command = model["cmd"]

    assert router["sendLoadingState"] is False
    assert model["capabilities"]["context"] == 131_072
    assert model["ttl"] == 900
    assert "--ctx-size 131072" in command
    assert "--cache-type-k q8_0 --cache-type-v q8_0" in command
    assert "--fit off --load-mode none" in command
    group = router["routing"]["router"]["settings"]["groups"]["local-memory"]
    assert group["exclusive"] is True
    assert model_id in group["members"]


def test_tool_capture_separates_the_cache_miss_from_warm_repeats() -> None:
    capture = _load(f"raw/{RAW_PREFIX}-tool30-p2048-o256-ctx128k-q8kv-alias.json")
    rows = capture["results"]

    assert capture["model"] == "qwen35-9b-q6k-ctx128k-q8kv-load-none"
    assert [row["tool_correct"] for row in rows] == [True, True, True]
    assert [row["usage"]["prompt_tokens"] for row in rows] == [5_260] * 3
    assert [row["usage"]["prompt_tokens_details"]["cached_tokens"] for row in rows] == [
        0,
        5_256,
        5_256,
    ]


def test_126k_early_middle_and_late_retrieval_are_exact() -> None:
    captures = {
        "early": _load(
            f"raw/{RAW_PREFIX}-retrieval-early-r1-p126000-o64-ctx128k-q8kv-zero-hit-alias.json"
        ),
        "middle": _load(f"raw/{RAW_PREFIX}-retrieval-mid-r3-p126000-o64-ctx128k-q8kv-alias.json"),
        "late": _load(
            f"raw/{RAW_PREFIX}-retrieval-late-r1-p126000-o64-ctx128k-q8kv-zero-hit-alias.json"
        ),
    }

    for capture in captures.values():
        assert all(row["expected_content_exact"] is True for row in capture["results"])
        assert all(row["usage"]["prompt_tokens"] == 126_002 for row in capture["results"])
    assert captures["early"]["results"][0]["usage"]["prompt_tokens_details"]["cached_tokens"] == 0
    assert captures["late"]["results"][0]["usage"]["prompt_tokens_details"]["cached_tokens"] == 0
    assert [
        row["usage"]["prompt_tokens_details"]["cached_tokens"]
        for row in captures["middle"]["results"]
    ] == [0, 125_998, 125_998]


def test_service_memory_capture_binds_exact_workloads_and_components() -> None:
    capture = _load("raw/qwen35-9b-q6k-128k-q8kv-service-memory-v2-load-none-linuxswap-alias.json")
    summary = capture["summary"]

    assert capture["instrument"]["memory_architecture"] == "linux_split_cuda"
    assert capture["contains_prompts_or_model_messages"] is False
    assert capture["child_returncode"] == 0
    assert summary["peak_combined_capacity_bytes"] == 11_716_790_272
    assert summary["host_bytes_at_combined_peak"] == 1_967_130_624
    assert summary["device_bytes_at_combined_peak"] == 9_749_659_648
    assert summary["peak_host_swap_growth_bytes"] == 1_048_576
    assert summary["peak_combined_capacity_bytes"] == (
        summary["host_bytes_at_combined_peak"] + summary["device_bytes_at_combined_peak"]
    )
    for workload in capture["workload_captures"]:
        path = BASE / "raw" / workload["name"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == workload["sha256"]


def test_normalized_measurements_bind_their_raw_captures() -> None:
    for path in (BASE / "measurements").glob("qwen35-9b-q6k-5060-*.json"):
        measurement = json.loads(path.read_text(encoding="utf-8"))
        raw_path = BASE / measurement["provenance"]["raw_artifact_path"]
        assert (
            hashlib.sha256(raw_path.read_bytes()).hexdigest()
            == measurement["provenance"]["raw_sha256"]
        )
        service = measurement.get("service_memory")
        if service is not None:
            service_path = BASE / service["raw_artifact_path"]
            assert hashlib.sha256(service_path.read_bytes()).hexdigest() == service["raw_sha256"]
            assert service["workload_capture_sha256"] == measurement["provenance"]["raw_sha256"]


def test_generated_5060_model_frontiers_retain_the_real_tradeoffs() -> None:
    expectations = {
        "qwen35-laguna-5060-short-throughput-frontier.json": {
            MODEL_ID,
            "poolside/Laguna-XS-2.1",
        },
        "qwen35-laguna-5060-tool-miss-screen-frontier.json": {
            MODEL_ID,
            "poolside/Laguna-XS-2.1",
        },
        "qwen35-laguna-5060-validated-capacity-latency-screen-frontier.json": {
            MODEL_ID,
            "poolside/Laguna-XS-2.1",
        },
        "qwen35-laguna-5060-validated-capacity-service-memory-screen-frontier.json": {MODEL_ID},
    }
    for name, expected in expectations.items():
        frontier = _load(f"generated/{name}")
        assert {member["offering"]["model_id"] for member in frontier["members"]} == expected

    capacity = _load(
        "generated/qwen35-laguna-5060-validated-capacity-service-memory-screen-frontier.json"
    )
    laguna = next(
        point
        for point in capacity["evaluated"]
        if point["offering"]["model_id"].startswith("poolside/")
    )
    assert len(laguna["dominated_by"]) == 1
    assert MODEL_ID in laguna["dominated_by"][0]
    assert capacity["rejected"] == []


def test_published_qwen_5060_evidence_has_no_private_machine_paths() -> None:
    paths: list[Path] = []
    for directory in ("generated", "measurements", "raw", "system-profiles"):
        paths.extend((BASE / directory).glob("*qwen35*5060*.json"))

    assert paths
    for path in paths:
        serialized = path.read_text(encoding="utf-8")
        assert "192.168." not in serialized
        assert "/Users/" not in serialized
        assert "/root/" not in serialized
        assert "admin:admin" not in serialized
