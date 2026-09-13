from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

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


def test_extracts_dflash_acceptance_from_nested_runtime_stats() -> None:
    rows = [
        {
            "runtime_stats_after": {
                "models": [
                    {"engine": {"speculation": {"last": {"acceptance_ratio": Decimal("0.625")}}}}
                ]
            }
        }
    ]

    assert NORMALIZER._speculative_acceptance(rows) == [Decimal("62.500")]


def test_retrieval_prompt_is_deterministic_unique_and_positioned() -> None:
    prompt, expected = MATRIX._retrieval_prompt(32_768, Decimal("0.9"))
    repeated_prompt, repeated_expected = MATRIX._retrieval_prompt(32_768, Decimal("0.9"))

    assert (prompt, expected) == (repeated_prompt, repeated_expected)
    assert len(prompt) == 32_768 * 4
    assert prompt.count(expected) == 1
    assert abs(prompt.index(expected) / len(prompt) - 0.9) < 0.01


def test_retrieval_integrity_requires_an_exact_final_answer() -> None:
    rows = [
        {"expected_content_exact": True, "expected_content_sha256": "a" * 64},
        {"expected_content_exact": False, "expected_content_sha256": "a" * 64},
    ]

    assert NORMALIZER._retrieval_integrity(rows)["retrieval"] == {"passed": 1, "total": 2}


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
    assert miss.performance.metrics["prefix_cache_hit_tokens"].values == (0,)
    assert warm.performance.metrics["prefix_cache_hit_tokens"].values == (16384,)
    assert miss.performance.output_token_counts == (64,)
    assert warm.performance.metrics["time_to_first_token_seconds"].values[0] < 1
