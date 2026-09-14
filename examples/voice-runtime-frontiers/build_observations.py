#!/usr/bin/env python3
"""Build the experimental TTS observation catalog from retained raw captures."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
WORKLOAD = {
    "id": "coval-tts-v1-local-whisper-pilot",
    "version": "1.2.0+local-whisper-pilot.1",
    "unit": "utterance",
}
MANIFEST_SHA256 = "e30909112f5fd886157008ca960c00d4f68f29de69b29d5913a5bf3383ad71ec"
WHISPER_REVISION = "624c19c9af5603fa73b83bce14d4aeea96156d18"

SPECS: tuple[dict[str, Any], ...] = (
    {
        "slug": "qwen3-tts-1.7b-6bit-mlx-m5",
        "latency": "qwen3-tts-1.7b-6bit-mlx-m5-seed1234-coval-tts-v1.json",
        "quality": "qwen3-tts-1.7b-6bit-mlx-m5-seed1234-coval-tts-v1-wer.json",
        "pacing": "qwen3-tts-1.7b-6bit-mlx-m5-seed1234-coval-tts-v1-pacing.json",
        "offering_id": "self-hosted/qwen3-tts-1.7b-6bit-mlx@m5-max-64gb-vivian-en-seed1234",
        "model_id": "Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "endpoint": "in-process-mlx",
        "quantization": "6bit",
        "hardware": "Apple M5 Max 64 GB",
        "artifact": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit",
        "artifact_revision": "1c6c0ff58c43afa8df571facde2efa077efd85e2",
        "revision_provenance": "post-capture local cache inspection",
        "runtime": "mlx-audio 0.5.4 / MLX 0.32.2",
        "runtime_config": "streaming_interval=0.32s; guarded_1920_sample_bootstrap_suppression",
        "voice": "Vivian; English",
        "seed": 1234,
        "p50_key": "perceived_ttfa_p50_ms",
        "p95_key": "perceived_ttfa_p95_ms",
        "wer_interval": ("4.126", "12.500"),
        "invalid_count": 0,
    },
    {
        "slug": "qwen3-tts-1.7b-bf16-vllm-omni-5060",
        "latency": "qwen3-tts-1.7b-bf16-vllm-omni-5060-seed1234-coval-tts-v1.json",
        "quality": "qwen3-tts-1.7b-bf16-vllm-omni-5060-seed1234-coval-tts-v1-wer.json",
        "pacing": (
            "qwen3-tts-1.7b-bf16-vllm-omni-5060-seed1234-"
            "coval-tts-v1-pacing.json"
        ),
        "offering_id": (
            "self-hosted/qwen3-tts-1.7b-bf16-vllm-omni"
            "@rtx5060ti-16gb-vivian-en-seed1234"
        ),
        "model_id": "Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "endpoint": "openai-compatible-pcm-http",
        "quantization": "bf16",
        "hardware": "NVIDIA GeForce RTX 5060 Ti 16 GB",
        "artifact": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "artifact_revision": "0c0e3051f131929182e2c023b9537f8b1c68adfe",
        "revision_provenance": "post-capture server cache inspection",
        "runtime": "vLLM-Omni 0.26.0",
        "runtime_config": (
            "max_model_len=2048; VLLM_USE_FLASHINFER_SAMPLER=0; "
            "guarded_1920_sample_bootstrap_suppression"
        ),
        "voice": "Vivian; English",
        "seed": 1234,
        "p50_key": "playback_ttfa_p50_ms",
        "p95_key": "playback_ttfa_p95_ms",
        "wer_interval": ("2.740", "13.178"),
        "invalid_count": 0,
    },
    {
        "slug": "loudr-1-turbo-loudkit-m5",
        "latency": "loudr-1-turbo-loudkit-m5-seed7-coval-tts-v1.json",
        "quality": "loudr-1-turbo-loudkit-m5-seed7-coval-tts-v1-wer.json",
        "pacing": "loudr-1-turbo-loudkit-m5-seed7-coval-tts-v1-pacing.json",
        "offering_id": "self-hosted/loudr-1-turbo-loudkit@m5-max-64gb-joe-en-seed7",
        "model_id": "loudr-1-turbo",
        "endpoint": "in-process-loudkit",
        "quantization": "mixed-fp16-fp32",
        "hardware": "Apple M5 Max 64 GB",
        "artifact": "loudreader/loudr-1-turbo",
        "artifact_revision": (
            "checkpoint-sha256:"
            "590dcf9e1dcec31c54650ee139f345b248bdd7ba11288def664c5f1508f33593"
        ),
        "revision_provenance": "capture-time checkpoint digest",
        "runtime": "loudkit 0.1.1 @ 7a17fa2351e59978b34d5aac07bae6349a7ca5d9",
        "runtime_config": "generator=cpu; renderer=mps; deterministic",
        "voice": "joe; English",
        "seed": 7,
        "p50_key": "perceived_ttfa_p50_ms",
        "p95_key": "perceived_ttfa_p95_ms",
        "wer_interval": ("4.960", "25.586"),
        "invalid_count": 0,
    },
    {
        "slug": "loudr-1-turbo-loudkit-5060",
        "latency": "loudr-1-turbo-loudkit-5060-seed7-coval-tts-v1.json",
        "quality": "loudr-1-turbo-loudkit-5060-seed7-coval-tts-v1-wer.json",
        "pacing": "loudr-1-turbo-loudkit-5060-seed7-coval-tts-v1-pacing.json",
        "offering_id": "self-hosted/loudr-1-turbo-loudkit@rtx5060ti-16gb-joe-en-seed7",
        "model_id": "loudr-1-turbo",
        "endpoint": "in-process-loudkit",
        "quantization": "mixed-fp16-fp32",
        "hardware": "NVIDIA GeForce RTX 5060 Ti 16 GB",
        "artifact": "loudreader/loudr-1-turbo",
        "artifact_revision": (
            "checkpoint-sha256:"
            "590dcf9e1dcec31c54650ee139f345b248bdd7ba11288def664c5f1508f33593"
        ),
        "revision_provenance": "capture-time checkpoint digest",
        "runtime": "loudkit 0.1.1 @ 7a17fa2351e59978b34d5aac07bae6349a7ca5d9",
        "runtime_config": "cuda; sdpa; deterministic ragged vocoder",
        "voice": "joe; English",
        "seed": 7,
        "p50_key": "perceived_ttfa_p50_ms",
        "p95_key": "perceived_ttfa_p95_ms",
        "wer_interval": ("4.382", "28.493"),
        "invalid_count": 1,
    },
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decimal(value: Any) -> str:
    return format(Decimal(str(value)), "f")


def _source(slug: str, kind: str, path: Path, methodology: str) -> dict[str, Any]:
    return {
        "id": f"voice-pilot-{slug}-{kind}",
        "version": "1",
        "methodology": methodology,
        "raw_sha256": _sha256(path),
    }


def _observation(
    value: Any,
    unit: str,
    observed_at: str,
    source: dict[str, Any],
    *,
    lower: str | None = None,
    upper: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "value": _decimal(value),
        "unit": unit,
        "sample_count": 30,
        "observed_at": observed_at,
        "source": source,
    }
    if lower is not None:
        result["lower"] = lower
    if upper is not None:
        result["upper"] = upper
    return result


def _offering(spec: dict[str, Any]) -> dict[str, Any]:
    latency_path = RAW / spec["latency"]
    quality_path = RAW / spec["quality"]
    pacing_path = RAW / spec["pacing"]
    latency = _load(latency_path)
    quality = _load(quality_path)
    pacing = _load(pacing_path)
    latency_digest = _sha256(latency_path)
    if quality["source"]["latency_capture_sha256"] != latency_digest:
        raise ValueError(f"{quality_path.name} does not bind {latency_path.name}")
    if latency["workload"]["manifest_sha256"] != MANIFEST_SHA256:
        raise ValueError(f"unexpected prompt manifest in {latency_path.name}")
    if quality["source"]["manifest_sha256"] != MANIFEST_SHA256:
        raise ValueError(f"unexpected prompt manifest in {quality_path.name}")
    if pacing["source"]["manifest_sha256"] != MANIFEST_SHA256:
        raise ValueError(f"unexpected prompt manifest in {pacing_path.name}")
    if pacing["source"]["audio_set_sha256"] != quality["source"]["audio_set_sha256"]:
        raise ValueError(f"{pacing_path.name} and {quality_path.name} score different audio")
    summary = latency["resident_summary_excluding_first_request"]
    quality_summary = quality["summary"]
    if (
        summary["sample_count"] != 30
        or quality_summary["sample_count"] != 30
        or pacing["summary"]["sample_count"] != 30
    ):
        raise ValueError(f"{spec['slug']} does not contain exactly 30 scored cases")
    if quality["instrument"]["resolved_revision"] != WHISPER_REVISION:
        raise ValueError(f"unexpected Whisper revision in {quality_path.name}")

    latency_source = _source(
        spec["slug"],
        "latency",
        latency_path,
        "Resident local synthesis capture; one unscored warmup followed by 30 prompts.",
    )
    quality_source = _source(
        spec["slug"],
        "wer",
        quality_path,
        "Saved audio scored by pinned local MLX Whisper with CoVAL normalization v2.",
    )
    pacing_source = _source(
        spec["slug"],
        "pacing",
        pacing_path,
        (
            "Deterministic reference-word rate and energy-pause proxies from the same "
            "saved audio; no naturalness threshold is asserted."
        ),
    )
    invalid_percent = Decimal(spec["invalid_count"]) * Decimal(100) / Decimal(30)
    wer_lower, wer_upper = spec["wer_interval"]
    return {
        "offering": {
            "offering_id": spec["offering_id"],
            "model_id": spec["model_id"],
            "provider": "self-hosted",
            "endpoint": spec["endpoint"],
            "service_tier": "resident",
            "quantization": spec["quantization"],
            "agent_harness": "model-skyline-voice-pilot@1",
            "capabilities": ["audio", "streaming", "tts"],
        },
        "metadata": {
            "hardware": spec["hardware"],
            "artifact": spec["artifact"],
            "artifact_revision": spec["artifact_revision"],
            "artifact_revision_provenance": spec["revision_provenance"],
            "runtime": spec["runtime"],
            "runtime_config": spec["runtime_config"],
            "voice": spec["voice"],
            "synthesis_seed": spec["seed"],
            "quality_scope": "local Whisper corpus WER; not naturalness or speaker identity",
            "pilot": "one fixed seed per exact offering",
            "latency_capture": f"raw/{spec['latency']}",
            "quality_capture": f"raw/{spec['quality']}",
            "pacing_capture": f"raw/{spec['pacing']}",
            "audio_set_sha256": quality["source"]["audio_set_sha256"],
        },
        "signals": {
            "tts_corpus_wer_percent": _observation(
                quality_summary["corpus_wer_percentage"],
                "percent",
                quality["captured_at"],
                quality_source,
                lower=wer_lower,
                upper=wer_upper,
            ),
            "tts_playback_ttfa_p50_ms": _observation(
                summary[spec["p50_key"]],
                "milliseconds",
                latency["captured_at"],
                latency_source,
            ),
            "tts_playback_ttfa_p95_ms": _observation(
                summary[spec["p95_key"]],
                "milliseconds",
                latency["captured_at"],
                latency_source,
            ),
            "tts_realtime_factor_p50": _observation(
                summary["real_time_factor_p50"],
                "ratio",
                latency["captured_at"],
                latency_source,
            ),
            "tts_invalid_case_percent": _observation(
                invalid_percent,
                "percent",
                latency["captured_at"],
                latency_source,
            ),
            "tts_corpus_words_per_minute": _observation(
                pacing["summary"]["corpus_words_per_minute"],
                "words/minute",
                pacing["captured_at"],
                pacing_source,
            ),
            "tts_words_per_minute_p95": _observation(
                pacing["summary"]["words_per_minute_p95"],
                "words/minute",
                pacing["captured_at"],
                pacing_source,
            ),
            "tts_internal_pause_fraction_p50": _observation(
                pacing["summary"]["internal_pause_fraction_p50"],
                "ratio",
                pacing["captured_at"],
                pacing_source,
            ),
        },
    }


def _render() -> str:
    catalog = {
        "schema_version": "model-skyline/v1alpha1",
        "workload": WORKLOAD,
        "offerings": [_offering(spec) for spec in SPECS],
    }
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "observations.json")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if --output is not byte-identical to the generated catalog.",
    )
    args = parser.parse_args()
    rendered = _render()
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated catalog: {args.output}")
        print(f"valid generated catalog: {args.output}")
        return
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
