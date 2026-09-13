#!/usr/bin/env python3
"""Normalize a captured llama-bench result into the public local contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from model_skyline.canonical import content_hash
from model_skyline.io import dump_json
from model_skyline.local_measurements import LocalHardwareIdentity, LocalMeasurementRecord


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_configuration(row: dict[str, Any], runtime_sha256: str) -> dict[str, Any]:
    excluded = {
        "avg_ns",
        "avg_ts",
        "backends",
        "build_commit",
        "build_number",
        "cpu_info",
        "gpu_info",
        "model_filename",
        "model_n_params",
        "model_size",
        "model_type",
        "n_depth",
        "n_gen",
        "n_prompt",
        "samples_ns",
        "samples_ts",
        "stddev_ns",
        "stddev_ts",
        "test_time",
    }
    return {
        "runtime_binary_sha256": runtime_sha256,
        **{key: value for key, value in row.items() if key not in excluded},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--hardware", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-artifact-path", required=True)
    parser.add_argument("--measurement-id", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-source-url", required=True)
    parser.add_argument("--model-license", required=True)
    parser.add_argument("--context-capacity", type=int, required=True)
    parser.add_argument("--agent-harness", default="model-skyline/llama-bench@v1")
    args = parser.parse_args()

    capture = json.loads(args.capture.read_text(encoding="utf-8"), parse_float=Decimal)
    hardware = LocalHardwareIdentity.model_validate_json(args.hardware.read_text(encoding="utf-8"))
    if capture.get("schema_version") != "model-skyline/raw-llama-bench/v1":
        parser.error("unsupported capture schema_version")
    if capture.get("host", {}).get("host_id") != hardware.hardware_id:
        parser.error("capture host_id does not match hardware profile")
    rows = capture.get("results")
    if not isinstance(rows, list):
        parser.error("capture results must be an array")
    prompts = [row for row in rows if row.get("n_prompt") and not row.get("n_gen")]
    generations = [row for row in rows if row.get("n_gen") and not row.get("n_prompt")]
    if len(prompts) != 1 or len(generations) != 1:
        parser.error("capture must have exactly one prompt and one generation row")
    prompt = prompts[0]
    generation = generations[0]
    prompt_samples = prompt.get("samples_ts")
    generation_samples = generation.get("samples_ts")
    if not isinstance(prompt_samples, list) or not isinstance(generation_samples, list):
        parser.error("capture rows must retain samples_ts")
    if len(prompt_samples) != len(generation_samples) or not prompt_samples:
        parser.error("prompt and generation sample counts must match and be non-empty")
    if prompt.get("build_commit") != generation.get("build_commit"):
        parser.error("capture rows disagree on build commit")

    artifact = capture["artifact"]
    runtime = capture["runtime"]
    invocation = capture["invocation"]
    input_definition_sha256 = content_hash(
        {
            "generator": "llama-bench/synthetic-token-stream",
            "build_commit": prompt["build_commit"],
            "n_prompt": prompt["n_prompt"],
            "n_gen": generation["n_gen"],
        }
    )
    record = LocalMeasurementRecord.model_validate(
        {
            "schema_version": "model-skyline/local-measurement/v1alpha1",
            "measurement_id": args.measurement_id,
            "status": "provisional",
            "started_at": capture["started_at"],
            "completed_at": capture["captured_at"],
            "hardware": hardware.model_dump(mode="json"),
            "artifact": {
                "model_id": args.model_id,
                "checkpoint": args.checkpoint,
                "revision": args.model_revision,
                "format": "gguf",
                "quantization": "Q4_K_M",
                "size_bytes": artifact["size_bytes"],
                "content_sha256": artifact["sha256"],
                "source_url": args.model_source_url,
                "license": args.model_license,
                "metadata": {"filename": artifact["filename"]},
            },
            "runtime": {
                "runtime_id": "llama.cpp",
                "version": f"build-{prompt['build_number']}",
                "commit": prompt["build_commit"],
                "backend": prompt["backends"],
                "context_capacity_tokens": args.context_capacity,
                "kv_cache": f"{generation['type_k']}/{generation['type_v']}",
                "prefix_cache_enabled": False,
                "speculative_method": None,
                "agent_harness": args.agent_harness,
                "configuration": _runtime_configuration(generation, runtime["sha256"]),
            },
            "workload": {
                "reference": {
                    "id": "llama-bench-pp2048-tg512",
                    "version": "llama.cpp@5266f24da/model-skyline@v1",
                    "unit": "benchmark_run",
                },
                "kind": "short_decode",
                "input_definition_sha256": input_definition_sha256,
                "requested_input_tokens": prompt["n_prompt"],
                "max_output_tokens": generation["n_gen"],
                "repetitions": len(generation_samples),
                "warmup_repetitions": 1,
                "concurrency": 1,
                "runner_state": "warm",
                "prefix_cache_state": "disabled",
                "position": {
                    "batch": generation["n_batch"],
                    "micro_batch": generation["n_ubatch"],
                    "threads": generation["n_threads"],
                },
            },
            "performance": {
                "actual_input_tokens": prompt["n_prompt"],
                "output_token_counts": [generation["n_gen"]] * len(generation_samples),
                "metrics": {
                    "prompt_tokens_per_second": {
                        "unit": "token/s",
                        "values": prompt_samples,
                    },
                    "decode_tokens_per_second": {
                        "unit": "token/s",
                        "values": generation_samples,
                    },
                },
            },
            "integrity": None,
            "capabilities": ["text"],
            "provenance": {
                "tool": "llama-bench",
                "tool_version": f"build-{prompt['build_number']}",
                "command_sha256": invocation["command_sha256"],
                "raw_artifact_path": args.raw_artifact_path,
                "raw_sha256": _sha256(args.capture),
                "captured_at": capture["captured_at"],
                "methodology": (
                    "llama-bench built-in warmup followed by five serial prompt-processing "
                    "and decode repetitions; exact command and per-repetition samples retained."
                ),
                "source_url": None,
                "license": "CC0-1.0",
            },
            "notes": "Provisional cross-machine microbenchmark; no model-quality claim.",
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(dump_json(record), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
