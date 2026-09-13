#!/usr/bin/env python3
"""measure-ingest.py — convert local-benchmark-tool output into ModelSkyline offerings.

PHILOSOPHY: use a runtime-native benchmark for the actual numbers and keep
warmup, repetitions, alternating run order, and thermal stabilization in that
benchmark workflow. This tool is ONLY the last mile: parse its output into
schema-valid offering JSON with the selected config embedded as provenance.

Supported inputs:
  --format llama-bench-json   (llama-bench -o json)
  --format mlx-lm-text        (mlx_lm.benchmark stdout)
  --format omlx-bench-json    (oMLX throughput benchmark results)
  --format ollama-bench-csv   (ollama bench -format csv)
  --format ds4-bench-csv      (ds4-bench --csv)

Recommended loop:
  1. Optimize: llama-bench-tuner (grid/Optuna over ngl/batch/fa) or ollama bench
  2. Ingest the winning run's output here
  3. Flip LOCAL_ENABLED in gen-all-frontiers.py
"""

import argparse
import csv
import json
import re
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path


def parse_llama_bench_json(path):
    data = json.loads(Path(path).read_text())
    rows = data if isinstance(data, list) else data.get("results", [])
    if not rows:
        sys.exit("no results in llama-bench JSON")

    # Current llama-bench emits one row for prompt processing and one for
    # generation, with throughput in avg_ts. Keep accepting the older tuner
    # summary shape below so retained benchmark artifacts remain ingestible.
    metric_fields = {
        "avg_ns",
        "avg_ts",
        "samples_ns",
        "samples_ts",
        "stddev_ns",
        "stddev_ts",
    }
    test_fields = metric_fields | {"n_prompt", "n_gen", "n_depth", "test_time"}

    def config_signature(row):
        return {key: value for key, value in row.items() if key not in test_fields}

    generation_rows = [row for row in rows if row.get("n_gen") and row.get("avg_ts")]
    if generation_rows:
        best = max(generation_rows, key=lambda row: float(row["avg_ts"]))
        prompt_rows = [row for row in rows if row.get("n_prompt") and row.get("avg_ts")]
        matching_prompts = [
            row for row in prompt_rows if config_signature(row) == config_signature(best)
        ]
        prompt = max(matching_prompts, key=lambda row: float(row["avg_ts"]), default=None)
        config = {key: value for key, value in best.items() if key not in metric_fields}
        if "model_filename" in config:
            config["model_filename"] = Path(config["model_filename"]).name
        if prompt:
            config["prefill_n_prompt"] = prompt["n_prompt"]
        return {
            "toks": float(best["avg_ts"]),
            "prefill_tok_s": float(prompt["avg_ts"]) if prompt else None,
            "stddev": float(best.get("stddev_ts", 0) or 0),
            "sample_count": len(best.get("samples_ts", [])) or 1,
            "prefill_sample_count": len(prompt.get("samples_ts", [])) or 1 if prompt else None,
            "config": config,
        }

    best = max(rows, key=lambda r: float(r.get("tg64") or r.get("tg128") or r.get("tg") or 0))
    tg_key = next((k for k in best if k.startswith("tg")), None)
    pp_key = next((k for k in best if k.startswith("pp")), None)
    return {
        "toks": float(best[tg_key]) if tg_key else 0.0,
        "prefill_tok_s": float(best[pp_key]) if pp_key else None,
        "stddev": float(best.get(tg_key + "+stddev", 0) or 0),
        "sample_count": 1,
        "config": {k: v for k, v in best.items() if k not in (tg_key, pp_key, "model_hash")},
    }


def parse_mlx_lm_text(path):
    text = Path(path).read_text()
    average = re.search(
        r"^Averages:\s+prompt_tps=([0-9.]+), generation_tps=([0-9.]+), "
        r"peak_memory=([0-9.]+)$",
        text,
        re.MULTILINE,
    )
    if not average:
        sys.exit("no MLX-LM Averages line in benchmark output")

    timing = re.search(
        r"Timing with prompt_tokens=(\d+), generation_tokens=(\d+), batch_size=(\d+)\.",
        text,
    )
    generation_trials = [
        float(value)
        for value in re.findall(r"^Trial \d+:.*generation_tps=([0-9.]+)", text, re.MULTILINE)
    ]
    config = {"peak_memory_gb": float(average.group(3))}
    if timing:
        config.update(
            {
                "prompt_tokens": int(timing.group(1)),
                "generation_tokens": int(timing.group(2)),
                "batch_size": int(timing.group(3)),
            }
        )
    return {
        "toks": float(average.group(2)),
        "prefill_tok_s": float(average.group(1)),
        "stddev": statistics.stdev(generation_trials) if len(generation_trials) > 1 else 0,
        "sample_count": len(generation_trials) or 1,
        "config": config,
    }


def parse_omlx_bench_json(path):
    payload = json.loads(Path(path).read_text())
    rows = payload.get("results", []) if isinstance(payload, dict) else payload
    if not rows:
        sys.exit("no results in oMLX benchmark JSON")

    generation_rows = [row for row in rows if row.get("gen_tps")]
    if not generation_rows:
        sys.exit("no generation throughput in oMLX benchmark JSON")
    best = max(generation_rows, key=lambda row: float(row["gen_tps"]))

    metric_fields = {
        "gen_tps",
        "processing_tps",
        "ttft_ms",
        "tpot_ms",
        "e2e_latency_s",
        "total_throughput",
        "peak_memory_bytes",
    }
    config = {key: value for key, value in best.items() if key not in metric_fields}
    if isinstance(payload, dict):
        for key in ("profile", "runtime", "quantization"):
            if payload.get(key) is not None:
                config[key] = payload[key]

    return {
        "toks": float(best["gen_tps"]),
        "prefill_tok_s": (float(best["processing_tps"]) if best.get("processing_tps") else None),
        "ttft_s": float(best["ttft_ms"]) / 1000 if best.get("ttft_ms") else None,
        "stddev": 0,
        "sample_count": 1,
        "prefill_sample_count": 1,
        "config": config,
    }


def parse_ollama_bench_csv(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("no rows in ollama bench CSV")
    # aggregate generate ns/token -> tok/s across epochs, report median
    gen = [1e9 / float(r["generate_ns/token"]) for r in rows if r.get("generate_ns/token")]
    ttft = [float(r["ttft_ns"]) / 1e9 for r in rows if r.get("ttft_ns")]
    prefill = [1e9 / float(r["prefill_ns/token"]) for r in rows if r.get("prefill_ns/token")]
    return {
        "toks": statistics.median(gen) if gen else None,
        "prefill_tok_s": statistics.median(prefill) if prefill else None,
        "ttft_s": statistics.median(ttft) if ttft else None,
        "stddev": (statistics.stdev(gen) if len(gen) > 1 else 0),
        "sample_count": len(gen) or 1,
        "config": {
            k: rows[0].get(k) for k in ("model", "quant", "params", "format") if rows[0].get(k)
        },
    }


def parse_ds4_bench_csv(path, context_tokens=None):
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("no rows in ds4-bench CSV")

    required = {"ctx_tokens", "prefill_tokens", "prefill_tps", "gen_tokens", "gen_tps"}
    missing = required - set(rows[0])
    if missing:
        sys.exit(f"missing ds4-bench columns: {', '.join(sorted(missing))}")

    if context_tokens is None:
        selected = max(rows, key=lambda row: int(row["ctx_tokens"]))
    else:
        selected = next(
            (row for row in rows if int(row["ctx_tokens"]) == context_tokens),
            None,
        )
        if selected is None:
            available = ", ".join(row["ctx_tokens"] for row in rows)
            sys.exit(f"ds4-bench context {context_tokens} not found; available: {available}")

    config = {key: int(selected[key]) for key in ("ctx_tokens", "prefill_tokens", "gen_tokens")}
    if selected.get("gen_first_ms"):
        config["gen_first_ms"] = float(selected["gen_first_ms"])
    if selected.get("gen_steady_tps"):
        config["gen_steady_tps"] = float(selected["gen_steady_tps"])
    if selected.get("kvcache_bytes"):
        config["kvcache_bytes"] = int(selected["kvcache_bytes"])

    return {
        "toks": float(selected["gen_tps"]),
        "prefill_tok_s": float(selected["prefill_tps"]),
        "stddev": 0,
        "sample_count": 1,
        "prefill_sample_count": 1,
        "config": config,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--format",
        choices=[
            "llama-bench-json",
            "mlx-lm-text",
            "omlx-bench-json",
            "ollama-bench-csv",
            "ds4-bench-csv",
        ],
        required=True,
    )
    ap.add_argument("--input", required=True, help="benchmark tool output file")
    ap.add_argument("--model", required=True, help="canonical model id, e.g. qwen3.8-flash-next")
    ap.add_argument(
        "--offering-id",
        required=True,
        help="runtime-specific id, e.g. qwen3.8-27b-llama-cpp-0.4.0-ud-q4-k-m",
    )
    ap.add_argument("--hw", required=True, help="hardware profile id, e.g. macbook-m5max-64")
    ap.add_argument(
        "--runtime", required=True, help='full stack string, e.g. "llama.cpp b6122 ngl=28 fa=1"'
    )
    ap.add_argument("--quant", default="")
    ap.add_argument(
        "--context-tokens",
        type=int,
        help="select one context frontier from ds4-bench CSV (default: largest)",
    )
    ap.add_argument("--workload", choices=["agent-chat", "coding-session"], default="agent-chat")
    ap.add_argument("--aa-index", type=int, help="current-scale AA Intelligence Index")
    ap.add_argument("--aa-source", help="provenance for quality axis, e.g. 'AA v4.1.1'")
    ap.add_argument(
        "--capability",
        action="append",
        choices=["tools", "images"],
        default=[],
        help="add only a capability verified through this runtime",
    )
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    if (args.aa_index is None) != (args.aa_source is None):
        ap.error("--aa-index and --aa-source must be supplied together")

    if args.format == "llama-bench-json":
        m = parse_llama_bench_json(args.input)
    elif args.format == "mlx-lm-text":
        m = parse_mlx_lm_text(args.input)
    elif args.format == "omlx-bench-json":
        m = parse_omlx_bench_json(args.input)
    elif args.format == "ds4-bench-csv":
        m = parse_ds4_bench_csv(args.input, args.context_tokens)
    else:
        m = parse_ollama_bench_csv(args.input)

    observed = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    signals = {
        "local_decode_tokens_per_second": {
            "value": f"{m['toks']:.2f}",
            "unit": "tokens/second",
            "sample_count": m["sample_count"],
            "observed_at": observed,
        },
    }
    if args.aa_index is not None:
        signals["aa_intelligence_index"] = {
            "value": str(args.aa_index),
            "unit": "index",
            "sample_count": 1,
            "observed_at": observed,
        }
    if m.get("prefill_tok_s"):
        signals["local_prefill_tokens_per_second"] = {
            "value": f"{m['prefill_tok_s']:.1f}",
            "unit": "tokens/second",
            "observed_at": observed,
        }
        signals["local_prefill_tokens_per_second"]["sample_count"] = m.get(
            "prefill_sample_count", m["sample_count"]
        )

    offering = {
        "offering_id": f"local-{args.hw}/{args.offering_id}",
        "model_id": args.model,
        "provider": f"local-{args.hw}",
        "endpoint": "local",
        "region": "home",
        "service_tier": "standard",
        "quantization": args.quant or m.get("config", {}).get("quant", ""),
        "agent_harness": f"brian-harness@1 [{args.runtime}]",
        "capabilities": ["text", *args.capability],
    }
    out = {
        "schema_version": "model-skyline/v1alpha1",
        "measured_at": observed,
        "measurement_tool": args.format,
        "measurement_config": m.get("config", {}),
        "stddev_tok_s": m.get("stddev"),
        "workload": args.workload,
        "offering": offering,
        "signals": signals,
        "default_source": {
            "id": f"brian-local-{args.hw}-v1",
            "version": "1",
            "license": "MIT (derived); upstream terms preserved",
            "methodology": (
                f"Local decode {m['toks']:.2f} tok/s on {args.hw} via {args.runtime}; "
                f"measured with {args.format} (benchmark orchestration remains the "
                f"source workflow's responsibility; config embedded in "
                f"agent_harness/measurement_config); "
                + (
                    f"quality axis: {args.aa_source}."
                    if args.aa_source
                    else "quality axis omitted pending verified evidence."
                )
            ),
        },
    }
    if args.aa_source:
        out["aa_source"] = args.aa_source
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, indent=2))
    print(f"offering: {offering['offering_id']}  decode={m['toks']:.2f} tok/s  -> {args.output}")
    if m.get("stddev") and m["toks"] and m["stddev"] / m["toks"] > 0.1:
        print("WARNING: stddev >10% of mean — thermal/load noise; consider more repetitions")


if __name__ == "__main__":
    main()
