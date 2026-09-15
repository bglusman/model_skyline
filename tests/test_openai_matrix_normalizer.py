from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from model_skyline.io import load_local_measurement

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "examples" / "local-runtime-frontiers" / "normalize_openai_matrix.py"
CAPTURE = (
    ROOT / "examples" / "local-runtime-frontiers" / "raw" / "ornith-omlx-prefix20k-cache-ssd.json"
)
HARDWARE = ROOT / "examples" / "local-runtime-frontiers" / "hardware" / "macbook-m5max-64.json"
SPEC = importlib.util.spec_from_file_location("normalize_openai_matrix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
NORMALIZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NORMALIZER)
MATRIX_SCRIPT = ROOT / "examples" / "local-runtime-frontiers" / "openai_matrix.py"
MATRIX_SPEC = importlib.util.spec_from_file_location("openai_matrix", MATRIX_SCRIPT)
assert MATRIX_SPEC is not None and MATRIX_SPEC.loader is not None
MATRIX = importlib.util.module_from_spec(MATRIX_SPEC)
MATRIX_SPEC.loader.exec_module(MATRIX)
QWEN_RAW = (
    ROOT
    / "examples"
    / "local-runtime-frontiers"
    / "raw"
    / "qwen35-9b-q6k-5060-retrieval-mid-r3-p126000-o64-ctx128k-q8kv-alias.json"
)
QWEN_MEMORY = (
    ROOT
    / "examples"
    / "local-runtime-frontiers"
    / "raw"
    / "qwen35-9b-q6k-128k-q8kv-service-memory-v2-load-none-linuxswap-alias.json"
)


def test_matrix_disables_keepalive_reuse_between_large_streams() -> None:
    assert MATRIX.HTTP_MAX_KEEPALIVE_CONNECTIONS == 0


def test_extracts_dflash_acceptance_from_nested_runtime_stats() -> None:
    rows = [
        {
            "runtime_stats_before": {
                "models": [
                    {
                        "engine": {
                            "speculation": {
                                "last": {"acceptance_ratio": Decimal("0.5")},
                                "totals": {"requests": 3},
                            }
                        }
                    }
                ]
            },
            "runtime_stats_after": {
                "models": [
                    {
                        "engine": {
                            "speculation": {
                                "last": {"acceptance_ratio": Decimal("0.625")},
                                "totals": {"requests": 4},
                            }
                        }
                    }
                ]
            },
        }
    ]

    assert NORMALIZER._speculative_acceptance(rows) == [Decimal("62.500")]


def test_rejects_stale_dflash_acceptance_snapshot() -> None:
    rows = [
        {
            "runtime_stats_before": {
                "speculation": {
                    "last": {"acceptance_ratio": Decimal("0.5")},
                    "totals": {"requests": 3},
                }
            },
            "runtime_stats_after": {
                "speculation": {
                    "last": {"acceptance_ratio": Decimal("0.5")},
                    "totals": {"requests": 3},
                }
            },
        }
    ]

    with pytest.raises(ValueError, match="did not advance exactly once"):
        NORMALIZER._speculative_acceptance(rows)


def test_retrieval_prompt_is_deterministic_unique_and_positioned() -> None:
    prompt, expected = MATRIX._retrieval_prompt(32_768, Decimal("0.9"))
    repeated_prompt, repeated_expected = MATRIX._retrieval_prompt(32_768, Decimal("0.9"))

    assert (prompt, expected) == (repeated_prompt, repeated_expected)
    assert len(prompt) == 32_768 * 4
    assert prompt.count(expected) == 1
    assert abs(prompt.index(expected) / len(prompt) - 0.9) < 0.01


def test_retrieval_prompt_calibrates_to_a_tokenizer_count_target() -> None:
    def count_prompt(value: str) -> int:
        return len(value) // 5

    prompt, expected, count = MATRIX._calibrate_retrieval_prompt(
        32_768, Decimal("0.5"), count_prompt
    )

    assert count == 32_768
    assert count_prompt(prompt[:-1]) < 32_768
    assert prompt.count(expected) == 1
    assert abs(prompt.index(expected) / len(prompt) - 0.5) < 0.01


def test_retrieval_integrity_separates_value_recovery_from_exact_answer() -> None:
    rows = [
        {
            "expected_content_present": True,
            "expected_content_exact": True,
            "expected_content_sha256": "a" * 64,
        },
        {
            "expected_content_present": True,
            "expected_content_exact": False,
            "expected_content_sha256": "a" * 64,
        },
    ]

    integrity = NORMALIZER._retrieval_integrity(rows)

    assert integrity["retrieval"] == {"passed": 1, "total": 2}
    assert integrity["retrieval_value"] == {"passed": 2, "total": 2}


def test_retrieval_integrity_accepts_legacy_exact_only_rows() -> None:
    rows = [{"expected_content_exact": True, "expected_content_sha256": "a" * 64}]

    integrity = NORMALIZER._retrieval_integrity(rows)

    assert integrity["retrieval"] == {"passed": 1, "total": 1}
    assert integrity["retrieval_value"] == {"passed": 1, "total": 1}


def test_retrieval_integrity_rejects_impossible_exact_without_value() -> None:
    rows = [
        {
            "expected_content_present": False,
            "expected_content_exact": True,
            "expected_content_sha256": "a" * 64,
        }
    ]

    with pytest.raises(ValueError, match="must contain"):
        NORMALIZER._retrieval_integrity(rows)


def test_input_definition_hash_covers_system_prompt_and_tool_schema() -> None:
    prompt = MATRIX._prefix("tool", 512)
    tool_hash = MATRIX._canonical_sha256(MATRIX._input_definition(prompt, "tool"))
    prose_hash = MATRIX._canonical_sha256(MATRIX._input_definition(prompt, "prose"))

    assert tool_hash != prose_hash
    assert tool_hash == MATRIX._canonical_sha256(MATRIX._input_definition(prompt, "tool"))


def test_input_definition_hash_covers_thinking_mode() -> None:
    prompt = MATRIX._prefix("prose", 512)
    disabled = MATRIX._input_definition(prompt, "prose", thinking_mode="disabled")
    enabled = MATRIX._input_definition(prompt, "prose", thinking_mode="enabled")

    assert disabled["chat_template_kwargs"] == {"enable_thinking": False}
    assert enabled["chat_template_kwargs"] == {"enable_thinking": True}
    assert MATRIX._canonical_sha256(disabled) != MATRIX._canonical_sha256(enabled)


def test_input_definition_hash_covers_custom_system_prompt() -> None:
    prompt = MATRIX._prefix("prose", 512)
    default = MATRIX._input_definition(prompt, "prose")
    custom = MATRIX._input_definition(
        prompt,
        "prose",
        system_prompt="Reasoning strength: low.",
    )

    assert default["messages"][0]["content"] == MATRIX.DEFAULT_SYSTEM_PROMPT
    assert custom["messages"][0]["content"] == "Reasoning strength: low."
    assert MATRIX._canonical_sha256(default) != MATRIX._canonical_sha256(custom)


def test_tool_matrix_can_model_a_realistic_selection_surface() -> None:
    tools = MATRIX._tool_definition(30)
    forced = MATRIX._input_definition("probe", "tool", 30, "forced")
    automatic = MATRIX._input_definition("probe", "tool", 30, "auto")

    assert len(tools) == 30
    assert len({tool["function"]["name"] for tool in tools}) == 30
    assert forced["tool_choice"]["function"]["name"] == "lookup_fixture"
    assert automatic["tool_choice"] == "auto"
    assert MATRIX._canonical_sha256(forced) != MATRIX._canonical_sha256(automatic)


def test_runtime_output_ceiling_is_checked() -> None:
    runtime = NORMALIZER.LocalRuntimeIdentity.model_validate(
        {
            "runtime_id": "oMLX",
            "version": "0.6.4",
            "backend": "MLX/Metal",
            "context_capacity_tokens": 262_144,
            "kv_cache": "f16",
            "prefix_cache_enabled": False,
            "configuration": {"max_output_tokens": 16_384},
        }
    )

    assert NORMALIZER._configured_max_output(runtime) == 16_384


def test_server_timing_wins_when_tool_stream_is_buffered() -> None:
    rows = [
        {
            "ttft_seconds": Decimal("6.62"),
            "end_to_end_seconds": Decimal("7.31"),
            "decode_tokens_per_second": Decimal("90000"),
            "usage": {
                "time_to_first_token": Decimal("5.99"),
                "generation_tokens_per_second": Decimal("67.05"),
            },
        }
    ]

    metrics, ttft_source, decode_source = NORMALIZER._timing_metrics(rows, mode="tool")

    assert metrics["time_to_first_token_seconds"]["values"] == [Decimal("5.99")]
    assert metrics["decode_tokens_per_second"]["values"] == [Decimal("67.05")]
    assert ttft_source == "server-reported"
    assert decode_source == "server-reported"


def test_buffered_tool_stream_does_not_publish_client_pseudo_throughput() -> None:
    rows = [
        {
            "ttft_seconds": Decimal("6.62"),
            "end_to_end_seconds": Decimal("6.63"),
            "decode_tokens_per_second": Decimal("90000"),
            "usage": {},
        }
    ]

    metrics, ttft_source, decode_source = NORMALIZER._timing_metrics(rows, mode="tool")

    assert "time_to_first_token_seconds" not in metrics
    assert "decode_tokens_per_second" not in metrics
    assert metrics["time_to_first_semantic_event_seconds"]["values"] == [Decimal("6.62")]
    assert ttft_source == "unavailable"
    assert decode_source == "unavailable"


def test_client_decode_rate_excludes_the_first_timed_token() -> None:
    assert MATRIX._client_decode_rate(21, 2.0, 3.0) == 20.0
    assert MATRIX._client_decode_rate(1, 2.0, 3.0) is None
    assert MATRIX._client_decode_rate(21, 3.0, 3.0) is None


def test_linux_swap_capture_parses_used_bytes() -> None:
    assert MATRIX._parse_linux_swap_used_bytes(
        "MemTotal: 100 kB\nSwapTotal: 4096 kB\nSwapFree: 1024 kB\n"
    ) == (3 * 1024 * 1024)
    assert MATRIX._parse_linux_swap_used_bytes("SwapTotal: 4096 kB\n") is None
    assert MATRIX._parse_linux_swap_used_bytes("SwapTotal: 1 kB\nSwapFree: 2 kB\n") is None


def test_linux_host_state_retains_swap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MATRIX.sys, "platform", "linux")
    monkeypatch.setattr(MATRIX, "_swap_used_bytes", lambda: 42)

    assert MATRIX._host_state() == {"swap_used_bytes": 42}


def test_runtime_memory_finds_omlx_active_memory_pressure() -> None:
    stats = {
        "active_models": {
            "memory_pressure": {
                "enabled": True,
                "current_bytes": 47_125_083_168,
            }
        }
    }

    assert MATRIX._runtime_memory_bytes(stats) == 47_125_083_168


def test_service_memory_evidence_binds_workload_and_keeps_split_components() -> None:
    evidence = NORMALIZER._service_memory_evidence(
        QWEN_MEMORY,
        workload_capture=QWEN_RAW,
        hardware_id="inference-vm-rtx5060ti16",
        raw_artifact_path=(
            "raw/qwen35-9b-q6k-128k-q8kv-service-memory-v2-load-none-linuxswap-alias.json"
        ),
    )

    assert evidence["peak_service_capacity_bytes"] == 11_716_790_272
    assert evidence["accelerator_bytes_at_service_peak"] == 9_749_659_648
    assert evidence["host_bytes_at_service_peak"] == 1_967_130_624
    assert evidence["peak_host_swap_growth_bytes"] == 1_048_576
    assert evidence["memory_architecture"] == "linux_split_cuda"


def test_service_memory_evidence_rejects_an_unbound_workload() -> None:
    with pytest.raises(ValueError, match="does not bind"):
        NORMALIZER._service_memory_evidence(
            QWEN_MEMORY,
            workload_capture=CAPTURE,
            hardware_id="inference-vm-rtx5060ti16",
            raw_artifact_path="raw/qwen-memory.json",
        )


def test_normalizer_splits_prefix_cache_miss_and_warm_positions(tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": "model-skyline/local-system-profile/v1",
                "served_model": "ornith-1.5-35b-a3b-oq4e-mtp:baseline-f16kv",
                "artifact": {
                    "model_id": "ornith-ai/Ornith-1.5-35B-A3B",
                    "checkpoint": "Ornith-1.5-35B-A3B",
                    "revision": "fb9644b83ce290da1f2cb6139f04632c085fc1d5",
                    "format": "mlx-safetensors",
                    "quantization": "oQ4e",
                    "size_bytes": 20_900_622_336,
                    "content_sha256": "a" * 64,
                    "source_url": ("https://huggingface.co/mlx-works/Ornith-1.5-35B-A3B-oQ4e-mtp"),
                    "license": "MIT",
                    "metadata": {"digest_scheme": "test-fixture"},
                },
                "runtime": {
                    "runtime_id": "oMLX",
                    "version": "0.6.4",
                    "commit": None,
                    "backend": "MLX/Metal",
                    "context_capacity_tokens": 262_144,
                    "kv_cache": "f16",
                    "prefix_cache_enabled": True,
                    "speculative_method": None,
                    "agent_harness": "model-skyline/openai-matrix@v1",
                    "configuration": {"profile": "baseline-f16kv"},
                },
                "capabilities": ["text", "tools"],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "records"

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--capture",
            str(CAPTURE),
            "--hardware",
            str(HARDWARE),
            "--system-profile",
            str(profile),
            "--output-directory",
            str(output),
            "--raw-artifact-path",
            "raw/ornith-omlx-prefix20k-cache-ssd.json",
            "--measurement-prefix",
            "ornith-omlx-cache",
            "--kind",
            "prefix_cache",
        ],
        check=True,
    )

    miss = load_local_measurement(output / "ornith-omlx-cache-p20000-o64-miss.json")
    warm = load_local_measurement(output / "ornith-omlx-cache-p20000-o64-warm.json")
    assert miss.workload.repetitions == 1
    assert warm.workload.repetitions == 1
    assert miss.performance is not None
    assert warm.performance is not None
    miss_metrics = cast(dict[str, Any], miss.performance.metrics)
    warm_metrics = cast(dict[str, Any], warm.performance.metrics)
    assert miss_metrics["prefix_cache_hit_tokens"].values == (Decimal("0"),)
    assert warm_metrics["prefix_cache_hit_tokens"].values == (Decimal("16384"),)
    assert miss.performance.output_token_counts == (64,)
    assert warm_metrics["time_to_first_token_seconds"].values[0] < 1
