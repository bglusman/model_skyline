#!/usr/bin/env python3
"""measure-ingest.py — convert local-benchmark-tool output into ModelSkyline offerings.

PHILOSOPHY: measurement methodology is a solved problem — use llama-bench,
ollama bench, or llm-inference-benchmark for the actual numbers (they handle
warmup, repetitions, percentiles, thermal stabilization properly). This tool
is ONLY the last mile: parse their output -> schema-valid offering JSON with
the winning config embedded as provenance.

Supported inputs:
  --format llama-bench-json   (llama-bench -o json)
  --format ollama-bench-csv   (ollama bench -format csv)

Recommended loop:
  1. Optimize: llama-bench-tuner (grid/Optuna over ngl/batch/fa) or ollama bench
  2. Ingest the winning run's output here
  3. Flip LOCAL_ENABLED in gen-all-frontiers.py
"""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_llama_bench_json(path):
    data = json.loads(Path(path).read_text())
    rows = data if isinstance(data, list) else data.get("results", [])
    if not rows:
        sys.exit("no results in llama-bench JSON")
    best = max(rows, key=lambda r: float(r.get("tg64") or r.get("tg128") or r.get("tg") or 0))
    tg_key = next((k for k in best if k.startswith("tg")), None)
    pp_key = next((k for k in best if k.startswith("pp")), None)
    return {
        "toks": float(best[tg_key]) if tg_key else 0.0,
        "prefill_tok_s": float(best[pp_key]) if pp_key else None,
        "stddev": float(best.get(tg_key + "+stddev", 0) or 0),
        "config": {k: v for k, v in best.items() if k not in (tg_key, pp_key, "model_hash")},
    }


def parse_ollama_bench_csv(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("no rows in ollama bench CSV")
    # aggregate generate ns/token -> tok/s across epochs, report median
    import statistics
    gen = [1e9 / float(r["generate_ns/token"]) for r in rows if r.get("generate_ns/token")]
    ttft = [float(r["ttft_ns"]) / 1e9 for r in rows if r.get("ttft_ns")]
    prefill = [1e9 / float(r["prefill_ns/token"]) for r in rows if r.get("prefill_ns/token")]
    return {
        "toks": statistics.median(gen) if gen else None,
        "prefill_tok_s": statistics.median(prefill) if prefill else None,
        "ttft_s": statistics.median(ttft) if ttft else None,
        "stddev": (statistics.stdev(gen) if len(gen) > 1 else 0),
        "config": {k: rows[0].get(k) for k in ("model", "quant", "params", "format") if rows[0].get(k)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=["llama-bench-json", "ollama-bench-csv"], required=True)
    ap.add_argument("--input", required=True, help="benchmark tool output file")
    ap.add_argument("--model", required=True, help="canonical model id, e.g. qwen3.8-flash-next")
    ap.add_argument("--hw", required=True, help="hardware profile id, e.g. brian-m5-macbook-48")
    ap.add_argument("--runtime", required=True, help='full stack string, e.g. "llama.cpp b6122 ngl=28 fa=1"')
    ap.add_argument("--quant", default="")
    ap.add_argument("--workload", choices=["agent-chat", "coding-session"], default="agent-chat")
    ap.add_argument("--aa-index", type=int, required=True, help="current-scale AA Intelligence Index")
    ap.add_argument("--aa-source", required=True, help="provenance for quality axis, e.g. 'AA v4.1.1'")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    if args.format == "llama-bench-json":
        m = parse_llama_bench_json(args.input)
    else:
        m = parse_ollama_bench_csv(args.input)

    observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signals = {
        "aa_intelligence_index": {"value": str(args.aa_index), "unit": "index",
                                  "sample_count": 1, "observed_at": observed},
        "local_decode_tokens_per_second": {"value": f"{m['toks']:.2f}", "unit": "tokens/second",
                                           "observed_at": observed},
    }
    if m.get("prefill_tok_s"):
        signals["local_prefill_tokens_per_second"] = {
            "value": f"{m['prefill_tok_s']:.1f}", "unit": "tokens/second", "observed_at": observed}

    offering = {
        "offering_id": f"local-{args.hw}/{args.model}",
        "model_id": args.model,
        "provider": f"local-{args.hw}",
        "endpoint": "local",
        "region": "home",
        "service_tier": "standard",
        "quantization": args.quant or m.get("config", {}).get("quant", ""),
        "agent_harness": f"brian-harness@1 [{args.runtime}]",
        "capabilities": ["text", "tools"],
    }
    out = {
        "schema_version": "model-skyline/v1alpha1",
        "measured_at": observed,
        "measurement_tool": args.format,
        "measurement_config": m.get("config", {}),
        "stddev_tok_s": m.get("stddev"),
        "aa_source": args.aa_source,
        "workload": args.workload,
        "offering": offering,
        "signals": signals,
        "default_source": {
            "id": f"brian-local-{args.hw}-v1", "version": "1",
            "license": "MIT (derived); upstream terms preserved",
            "methodology": (f"Local decode {m['toks']:.2f} tok/s on {args.hw} via {args.runtime}; "
                            f"measured with {args.format} (warmup+repetitions handled by tool; "
                            f"config embedded in agent_harness/measurement_config); "
                            f"quality axis: {args.aa_source}."),
        },
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, indent=2))
    print(f"offering: {offering['offering_id']}  decode={m['toks']:.2f} tok/s  -> {args.output}")
    if m.get("stddev") and m["toks"] and m["stddev"] / m["toks"] > 0.1:
        print("WARNING: stddev >10% of mean — thermal/load noise; consider more repetitions")


if __name__ == "__main__":
    main()
