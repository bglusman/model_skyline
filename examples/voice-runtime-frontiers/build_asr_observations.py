#!/usr/bin/env python3
"""Build ASR observation catalogs from the retained two-Mac pilot captures."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
MANIFEST = HERE / "prompts" / "local-asr-pilot-v1.json"
WORKLOAD = {"id": "local-asr-pilot-v1", "version": "1.0.0", "unit": "utterance"}
SAMPLE_COUNT = 24

MODELS: tuple[dict[str, str], ...] = (
    {
        "slug": "qwen3-asr-06b-8bit",
        "artifact": "mlx-community/Qwen3-ASR-0.6B-8bit",
        "model_id": "Qwen3-ASR-0.6B",
        "revision": "89e96d92ba34aca20b3e29fb10cc284097d1219f",
        "quantization": "8bit",
    },
    {
        "slug": "qwen3-asr-17b-8bit",
        "artifact": "mlx-community/Qwen3-ASR-1.7B-8bit",
        "model_id": "Qwen3-ASR-1.7B",
        "revision": "a8379a2e2f9e313c9292cdf1af4055ab56d50d55",
        "quantization": "8bit",
    },
    {
        "slug": "whisper-large-v3-turbo-fp16",
        "artifact": "mlx-community/whisper-large-v3-turbo-asr-fp16",
        "model_id": "whisper-large-v3-turbo",
        "revision": "624c19c9af5603fa73b83bce14d4aeea96156d18",
        "quantization": "fp16",
    },
    {
        "slug": "parakeet-tdt-06b-v3-fp16",
        "artifact": "mlx-community/parakeet-tdt-0.6b-v3",
        "model_id": "parakeet-tdt-0.6b-v3",
        "revision": "ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15",
        "quantization": "fp16",
    },
)

HARDWARE: dict[str, dict[str, str]] = {
    "m1": {
        "capture_token": "m1",
        "capture_name": "Apple M1 Max",
        "metadata_name": "Apple M1 Max 64 GB",
        "offering_name": "m1-max-64gb",
        "provider": "local:m1-max-64gb",
        "bootstrap": "m1-local-asr-pilot-v1-bootstrap.json",
    },
    "m5": {
        "capture_token": "m5",
        "capture_name": "Apple M5 Max",
        "metadata_name": "Apple M5 Max 64 GB",
        "offering_name": "m5-max-64gb",
        "provider": "local:m5-max-64gb",
        "bootstrap": "m5-local-asr-pilot-v1-bootstrap.json",
    },
}


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decimal(value: Any) -> str:
    return format(Decimal(str(value)), "f")


def _source(slug: str, machine: str, path: Path) -> dict[str, Any]:
    return {
        "id": f"local-asr-pilot-{slug}-{machine}",
        "version": "1",
        "methodology": (
            "Resident in-process MLX-Audio transcription; one unscored warmup, "
            "then 24 complete audio files sequentially at batch size one."
        ),
        "raw_sha256": _sha256(path),
    }


def _observation(
    value: Any,
    unit: str,
    observed_at: str,
    source: dict[str, Any],
    *,
    lower: Any | None = None,
    upper: Any | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "value": _decimal(value),
        "unit": unit,
        "sample_count": SAMPLE_COUNT,
        "observed_at": observed_at,
        "source": source,
    }
    if lower is not None:
        result["lower"] = _decimal(lower)
    if upper is not None:
        result["upper"] = _decimal(upper)
    return result


def _bootstrap_entry(bootstrap: dict[str, Any], capture: dict[str, Any]) -> dict[str, Any]:
    offering = capture["offering"]
    key = f"{offering['model']}|{offering['runtime']}|{offering['hardware']}"
    try:
        entry = bootstrap["offerings"][key]
    except KeyError as error:
        raise ValueError(f"bootstrap is missing {key}") from error
    if not isinstance(entry, dict):
        raise ValueError(f"bootstrap entry for {key} is not an object")
    return entry


def _offering(spec: dict[str, str], machine: str, machine_spec: dict[str, str]) -> dict[str, Any]:
    filename = f"{spec['slug']}-mlx-{machine_spec['capture_token']}-local-asr-pilot-v1.json"
    capture_path = RAW / filename
    bootstrap_path = RAW / machine_spec["bootstrap"]
    capture = _load(capture_path)
    bootstrap = _load(bootstrap_path)
    manifest_sha = _sha256(MANIFEST)

    if capture.get("schema") != "model-skyline/experimental-local-asr-capture/v1alpha1":
        raise ValueError(f"unsupported capture schema in {capture_path.name}")
    if capture["workload"]["manifest_sha256"] != manifest_sha:
        raise ValueError(f"unexpected manifest in {capture_path.name}")
    if capture["summary"]["sample_count"] != SAMPLE_COUNT:
        raise ValueError(f"unexpected sample count in {capture_path.name}")
    exact = capture["offering"]
    if exact["model"] != spec["artifact"]:
        raise ValueError(f"unexpected model in {capture_path.name}")
    if exact["resolved_revision"] != spec["revision"]:
        raise ValueError(f"unexpected revision in {capture_path.name}")
    if exact["hardware"] != machine_spec["capture_name"]:
        raise ValueError(f"unexpected hardware in {capture_path.name}")
    interval = _bootstrap_entry(bootstrap, capture)
    if interval["capture_sha256"] != _sha256(capture_path):
        raise ValueError(f"stale bootstrap binding for {capture_path.name}")

    observed_at = capture["captured_at"]
    summary = capture["summary"]
    confidence = interval["interval_95"]
    source = _source(spec["slug"], machine, capture_path)
    empty_percent = Decimal(summary["empty_hypothesis_count"]) * Decimal(100)
    empty_percent /= Decimal(SAMPLE_COUNT)
    offering_id = f"self-hosted/{spec['slug']}-mlx@{machine_spec['offering_name']}-en"
    return {
        "offering": {
            "offering_id": offering_id,
            "model_id": spec["model_id"],
            "provider": machine_spec["provider"],
            "endpoint": "in-process-mlx",
            "service_tier": "resident",
            "quantization": spec["quantization"],
            "agent_harness": "model-skyline-local-asr-pilot@1",
            "capabilities": ["asr", "audio", "stt"],
        },
        "metadata": {
            "hardware": machine_spec["metadata_name"],
            "artifact": exact["model"],
            "artifact_revision": exact["resolved_revision"],
            "runtime": f"MLX-Audio {exact['mlx_audio']} / MLX {exact['mlx']}",
            "runtime_config": (
                f"batch_size=1; max_tokens={exact['max_tokens']}; "
                f"requested_language={exact['requested_language']}; "
                f"language_argument={exact['language_argument']!r}"
            ),
            "quality_scope": (
                "24-utterance English pilot across meeting, financial-call, "
                "difficult audiobook, and non-US political speech"
            ),
            "latency_scope": (
                "complete audio file already available to final transcript return; "
                "excludes end-of-turn detection and live-stream capture"
            ),
            "capture": f"raw/{filename}",
            "bootstrap": f"raw/{machine_spec['bootstrap']}",
            "bootstrap_sha256": _sha256(bootstrap_path),
            "manifest_sha256": manifest_sha,
            "audio_set_sha256": capture["workload"]["audio_set_sha256"],
        },
        "signals": {
            "asr_corpus_wer_percent": _observation(
                summary["corpus_wer_percentage"],
                "percent",
                observed_at,
                source,
                lower=confidence["corpus_wer_percentage"][0],
                upper=confidence["corpus_wer_percentage"][1],
            ),
            "asr_final_latency_p50_ms": _observation(
                summary["final_latency_p50_ms"],
                "milliseconds",
                observed_at,
                source,
                lower=confidence["final_latency_p50_ms"][0],
                upper=confidence["final_latency_p50_ms"][1],
            ),
            "asr_final_latency_p95_ms": _observation(
                summary["final_latency_p95_ms"],
                "milliseconds",
                observed_at,
                source,
                lower=confidence["final_latency_p95_ms"][0],
                upper=confidence["final_latency_p95_ms"][1],
            ),
            "asr_realtime_factor": _observation(
                summary["real_time_factor_corpus"], "ratio", observed_at, source
            ),
            "asr_process_rss_peak_mb": _observation(
                summary["process_rss_mb_max"], "megabytes", observed_at, source
            ),
            "asr_empty_hypothesis_percent": _observation(
                empty_percent, "percent", observed_at, source
            ),
        },
    }


def _render(hardware: str) -> str:
    machines = HARDWARE if hardware == "all" else {hardware: HARDWARE[hardware]}
    catalog = {
        "schema_version": "model-skyline/v1alpha1",
        "workload": WORKLOAD,
        "offerings": [
            _offering(spec, machine, machine_spec)
            for machine, machine_spec in machines.items()
            for spec in MODELS
        ],
    }
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hardware", choices=("all", "m1", "m5"), default="all")
    parser.add_argument("--output", type=Path, default=HERE / "asr-observations.json")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if --output is not byte-identical to the generated catalog.",
    )
    args = parser.parse_args()
    rendered = _render(args.hardware)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated catalog: {args.output}")
        print(f"valid generated catalog: {args.output}")
        return
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
