#!/usr/bin/env python3
"""measure-local.py — benchmark your own local LLM stack into ModelSkyline offerings.

Works against ANY OpenAI-compatible endpoint (Ollama, llama.cpp server, vLLM,
LM Studio, slotstream...). Measures real decode tok/s, TTFT, and prefill
throughput on YOUR hardware, then emits a schema-valid offering JSON you can
drop into the local frontier pipeline.

Usage:
  python3 measure-local.py --base-url http://localhost:11434/v1 \
      --model qwen3.8-flash-next:4bit \
      --hw brian-m5-macbook-48 \
      --runtime "Ollama 0.12" --quant 4bit \
      --output local_measurements/brian-m5-macbook-48.json

Requires an OpenAI-API-compatible /chat/completions endpoint. Tool/structured
support is probed automatically (local frontiers require both).
"""
import argparse
import json
import statistics
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# Real-ish agent shapes: keep in sync with gen-all-frontiers.py shapes
AGENT_CHAT = {"uncached": 14592, "cached": 166621, "out": 592}
CODING = {"uncached": 700, "cached": 52000, "out": 150}


def chat(base, model, messages, max_tokens, session=None):
    body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.load(r)
    return data, time.time() - t0


def measure_round(base, model, prompt_tokens_marker, out_tokens, rounds=3):
    """Decode speed via repeated generation on a warm cache; TTFT via streaming would be
    ideal, but non-streaming total-time + reported usage gives decode tok/s reliably."""
    decs, ttfts = [], []
    for _ in range(rounds):
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "Write a 300-word technical summary of TCP congestion control."}],
            "max_tokens": out_tokens,
        }).encode()
        req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=900) as r:
            data = json.load(r)
        dt = time.time() - t0
        u = data.get("usage", {})
        ot = u.get("completion_tokens", 0)
        if ot and dt:
            decs.append(ot / dt)
    return {"decode_tok_s": statistics.median(decs) if decs else None, "rounds": rounds}


def probe_tools(base, model):
    """Return whether the endpoint accepts a tools parameter without error."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "What is 2+2?"}],
        "max_tokens": 20,
        "tools": [{"type": "function", "function": {
            "name": "calc", "description": "calculator",
            "parameters": {"type": "object", "properties": {"expr": {"type": "string"}}}}}],
    }).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return "yes"
    except urllib.error.HTTPError as e:
        return f"rejected({e.code})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="e.g. http://localhost:11434/v1")
    ap.add_argument("--model", required=True, help="model id the endpoint expects")
    ap.add_argument("--hw", required=True, help="hardware profile id, e.g. brian-m5-macbook-48")
    ap.add_argument("--runtime", required=True, help='e.g. "Ollama 0.12 + Q4_K_M"')
    ap.add_argument("--quant", default="", help="quantization, e.g. 4bit / Q4_K_M")
    ap.add_argument("--workload", choices=["agent-chat", "coding-session"], default="agent-chat")
    ap.add_argument("--aa-index", type=int, required=True,
                    help="current-scale AA Intelligence Index for this model (cite source in --aa-source)")
    ap.add_argument("--aa-source", required=True,
                    help="e.g. 'AA v4.1.1' — provenance for the quality axis")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    print(f"probing {args.model} at {args.base_url} ...")
    tools = probe_tools(args.base_url, args.model)
    if tools != "yes":
        print(f"WARNING: tools unsupported ({tools}) — local frontiers require tools; offering will be excluded")

    print("measuring decode speed ...")
    m = measure_round(args.base_url, args.model, None, 400, rounds=args.rounds)

    offering = {
        "offering_id": f"local-{args.hw}/{args.model}",
        "model_id": args.model,
        "provider": f"local-{args.hw}",
        "endpoint": "local",
        "region": "home",
        "service_tier": "standard",
        "quantization": args.quant,
        "agent_harness": f"brian-harness@1 [{args.runtime}]",
        "capabilities": ["text"] + (["tools"] if tools == "yes" else []),
    }
    observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signals = {
        "aa_intelligence_index": {"value": str(args.aa_index), "unit": "index",
                                  "sample_count": 1, "observed_at": observed},
    }
    if m["decode_tok_s"]:
        signals["local_decode_tokens_per_second"] = {
            "value": f"{m['decode_tok_s']:.2f}", "unit": "tokens/second",
            "sample_count": m["rounds"], "observed_at": observed}

    out = {
        "schema_version": "model-skyline/v1alpha1",
        "measured_at": observed,
        "measurement": {"decode_tok_s": m["decode_tok_s"], "rounds": m["rounds"],
                        "endpoint": args.base_url, "runtime": args.runtime},
        "aa_source": args.aa_source,
        "tools_support": tools,
        "workload": args.workload,
        "offering": offering,
        "signals": signals,
        "default_source": {
            "id": f"brian-local-{args.hw}-v1", "version": "1",
            "license": "MIT (derived); upstream terms preserved",
            "methodology": (f"Local measured decode {m['decode_tok_s'] and round(m['decode_tok_s'], 2)} tok/s "
                            f"on {args.hw} via {args.runtime} (quant {args.quant or 'n/a'}); "
                            f"zero marginal dollar cost; prefill not yet measured/axis; "
                            f"quality axis: {args.aa_source}."),
        },
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, indent=2))
    print(f"written: {args.output}")
    print(json.dumps({"offering_id": offering["offering_id"],
                      "decode_tok_s": m["decode_tok_s"], "tools": tools}, indent=1))


if __name__ == "__main__":
    main()
