import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "examples" / "subscription-relative-real" / "measure-ingest.py"
SPEC = importlib.util.spec_from_file_location("measure_ingest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MEASURE_INGEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MEASURE_INGEST)


def test_parse_current_llama_bench_json(tmp_path: Path) -> None:
    benchmark = tmp_path / "llama.json"
    benchmark.write_text(
        json.dumps(
            [
                {
                    "build_commit": "5266f24da",
                    "n_batch": 2048,
                    "n_prompt": 2048,
                    "n_gen": 0,
                    "avg_ts": 628.564741,
                    "stddev_ts": 23.331286,
                    "samples_ts": [655.231, 618.556, 611.908],
                },
                {
                    "build_commit": "5266f24da",
                    "n_batch": 2048,
                    "n_prompt": 0,
                    "n_gen": 128,
                    "avg_ts": 25.548063,
                    "stddev_ts": 0.722935,
                    "samples_ts": [26.223, 25.636, 24.7852],
                },
            ]
        )
    )

    parsed = MEASURE_INGEST.parse_llama_bench_json(benchmark)

    assert parsed["toks"] == 25.548063
    assert parsed["prefill_tok_s"] == 628.564741
    assert parsed["stddev"] == 0.722935
    assert parsed["sample_count"] == 3
    assert parsed["prefill_sample_count"] == 3
    assert parsed["config"]["prefill_n_prompt"] == 2048


def test_llama_prefill_is_paired_with_winning_generation_config(tmp_path: Path) -> None:
    benchmark = tmp_path / "llama.json"
    benchmark.write_text(
        json.dumps(
            [
                {"n_batch": 512, "n_prompt": 2048, "n_gen": 0, "avg_ts": 900.0},
                {"n_batch": 512, "n_prompt": 0, "n_gen": 128, "avg_ts": 20.0},
                {"n_batch": 2048, "n_prompt": 2048, "n_gen": 0, "avg_ts": 700.0},
                {"n_batch": 2048, "n_prompt": 0, "n_gen": 128, "avg_ts": 30.0},
            ]
        )
    )

    parsed = MEASURE_INGEST.parse_llama_bench_json(benchmark)

    assert parsed["toks"] == 30.0
    assert parsed["prefill_tok_s"] == 700.0
    assert parsed["config"]["n_batch"] == 2048


def test_parse_mlx_lm_text(tmp_path: Path) -> None:
    benchmark = tmp_path / "mlx.txt"
    benchmark.write_text(
        """Running warmup..
Timing with prompt_tokens=2048, generation_tokens=128, batch_size=1.
Trial 1:  prompt_tps=700.000, generation_tps=30.000, peak_memory=18.500, total_time=8.0
Trial 2:  prompt_tps=710.000, generation_tps=31.000, peak_memory=18.500, total_time=8.0
Trial 3:  prompt_tps=720.000, generation_tps=32.000, peak_memory=18.500, total_time=8.0
Averages: prompt_tps=710.000, generation_tps=31.000, peak_memory=18.500
"""
    )

    parsed = MEASURE_INGEST.parse_mlx_lm_text(benchmark)

    assert parsed["toks"] == 31.0
    assert parsed["prefill_tok_s"] == 710.0
    assert parsed["stddev"] == 1.0
    assert parsed["sample_count"] == 3
    assert parsed["config"] == {
        "peak_memory_gb": 18.5,
        "prompt_tokens": 2048,
        "generation_tokens": 128,
        "batch_size": 1,
    }


def test_parse_omlx_bench_json(tmp_path: Path) -> None:
    benchmark = tmp_path / "omlx.json"
    benchmark.write_text(
        json.dumps(
            {
                "profile": "baseline-f16kv",
                "runtime": "oMLX 0.6.4; MLX 0.32.2",
                "quantization": "oQ4e",
                "results": [
                    {
                        "test_type": "single",
                        "pp": 4369,
                        "requested_pp": 4096,
                        "tg": 512,
                        "ttft_ms": 1483.2,
                        "tpot_ms": 7.62,
                        "gen_tps": 131.5,
                        "processing_tps": 2945.6,
                        "e2e_latency_s": 5.376,
                        "total_throughput": 907.9,
                        "peak_memory_bytes": None,
                        "prompt_tokens": 4369,
                        "completion_tokens": 512,
                        "cached_tokens": 0,
                    }
                ],
            }
        )
    )

    parsed = MEASURE_INGEST.parse_omlx_bench_json(benchmark)

    assert parsed["toks"] == 131.5
    assert parsed["prefill_tok_s"] == 2945.6
    assert parsed["ttft_s"] == 1.4832
    assert parsed["sample_count"] == 1
    assert parsed["config"] == {
        "test_type": "single",
        "pp": 4369,
        "requested_pp": 4096,
        "tg": 512,
        "prompt_tokens": 4369,
        "completion_tokens": 512,
        "cached_tokens": 0,
        "profile": "baseline-f16kv",
        "runtime": "oMLX 0.6.4; MLX 0.32.2",
        "quantization": "oQ4e",
    }


def test_parse_ds4_bench_csv_selects_requested_context(tmp_path: Path) -> None:
    benchmark = tmp_path / "ds4.csv"
    benchmark.write_text(
        "ctx_tokens,prefill_tokens,prefill_tps,gen_tokens,gen_tps,"
        "gen_first_ms,gen_steady_tokens,gen_steady_tps,kvcache_bytes\n"
        "8192,8192,310.25,128,39.50,26.4,127,40.10,136750476\n"
        "65536,32768,300.75,128,37.25,29.8,127,37.80,925000000\n"
    )

    parsed = MEASURE_INGEST.parse_ds4_bench_csv(benchmark, 65536)

    assert parsed["toks"] == 37.25
    assert parsed["prefill_tok_s"] == 300.75
    assert parsed["sample_count"] == 1
    assert parsed["config"] == {
        "ctx_tokens": 65536,
        "prefill_tokens": 32768,
        "gen_tokens": 128,
        "gen_first_ms": 29.8,
        "gen_steady_tps": 37.8,
        "kvcache_bytes": 925000000,
    }


def test_parse_ds4_bench_csv_defaults_to_largest_context(tmp_path: Path) -> None:
    benchmark = tmp_path / "ds4.csv"
    benchmark.write_text(
        "ctx_tokens,prefill_tokens,prefill_tps,gen_tokens,gen_tps\n"
        "65536,32768,300.75,128,37.25\n"
        "8192,8192,310.25,128,39.50\n"
    )

    parsed = MEASURE_INGEST.parse_ds4_bench_csv(benchmark)

    assert parsed["config"]["ctx_tokens"] == 65536


def test_cli_keeps_unverified_quality_out_and_runtime_in_identity(tmp_path: Path) -> None:
    benchmark = tmp_path / "mlx.txt"
    benchmark.write_text(
        """Timing with prompt_tokens=2048, generation_tokens=128, batch_size=1.
Trial 1:  prompt_tps=700.000, generation_tps=30.000, peak_memory=18.500, total_time=8.0
Averages: prompt_tps=700.000, generation_tps=30.000, peak_memory=18.500
"""
    )
    output = tmp_path / "measurement.json"

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--format",
            "mlx-lm-text",
            "--input",
            str(benchmark),
            "--model",
            "qwen3.8-27b",
            "--offering-id",
            "qwen3.8-27b-mlx-lm-4bit",
            "--hw",
            "macbook-m5max-64",
            "--runtime",
            "MLX-LM 0.31.3; MLX 0.32.2",
            "--quant",
            "4bit",
            "--capability",
            "tools",
            "--output",
            str(output),
        ],
        check=True,
    )

    result = json.loads(output.read_text())
    assert result["offering"]["offering_id"] == ("local-macbook-m5max-64/qwen3.8-27b-mlx-lm-4bit")
    assert result["offering"]["capabilities"] == ["text", "tools"]
    assert "aa_source" not in result
    assert "aa_intelligence_index" not in result["signals"]
