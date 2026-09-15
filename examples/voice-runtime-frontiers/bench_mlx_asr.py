#!/usr/bin/env python3
"""Benchmark one MLX speech-to-text offering on a retained audio panel.

The harness performs one unscored warmup, then transcribes every clip
sequentially without playback. It records exact model and workload provenance,
corpus/domain WER, end-of-speech-to-final-text latency, throughput, and memory
instruments. Memory is descriptive until another runtime provides a comparable
whole-service measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import os
import platform
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import jiwer
import mlx.core as mx
import numpy as np
from huggingface_hub import snapshot_download
from mlx_audio.stt.utils import load_model  # type: ignore[import-untyped]
from whisper_normalizer.english import (  # type: ignore[import-untyped]
    EnglishTextNormalizer,
)

NORM_VERSION = "open-asr-leaderboard-compatible-whisper-english-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _hardware_name() -> str:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return platform.processor() or platform.machine()
    return result.stdout.strip()


def _process_rss_mb() -> float:
    result = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(os.getpid())],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip()) / 1024.0


def _extract_text(result: Any) -> str:
    if hasattr(result, "text"):
        return str(result.text).strip()
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict) and "text" in result:
        return str(result["text"]).strip()
    if inspect.isgenerator(result) or hasattr(result, "__iter__"):
        parts: list[str] = []
        for item in result:
            if hasattr(item, "text"):
                parts.append(str(item.text))
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    return str(result).strip()


def _transcribe(
    model: Any,
    path: Path,
    language_argument: str | None,
    max_tokens: int,
) -> str:
    signature = inspect.signature(model.generate)
    kwargs: dict[str, Any] = {}
    if language_argument is not None:
        kwargs["language"] = language_argument
    if "verbose" in signature.parameters:
        kwargs["verbose"] = False
    if "max_tokens" in signature.parameters:
        kwargs["max_tokens"] = max_tokens
    elif "max_new_tokens" in signature.parameters:
        kwargs["max_new_tokens"] = max_tokens
    return _extract_text(model.generate(str(path), **kwargs))


def _resolve_language_argument(model: Any, requested: str | None) -> str | None:
    """Translate the user-facing language into the loaded backend's API.

    MLX-Audio deliberately presents one loader for several independently
    implemented model families. Qwen3-ASR accepts names such as ``English``;
    Whisper accepts language codes such as ``en``; and Parakeet's current
    ``generate`` signature performs its own handling without a language
    argument. Treating those interfaces as identical can silently change
    decoding rather than raising an error.
    """
    if requested is None:
        return None
    signature = inspect.signature(model.generate)
    if "language" not in signature.parameters:
        return None
    module = type(model).__module__.lower()
    if ".whisper" in module and requested.lower() == "english":
        return "en"
    return requested


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return round(float(np.percentile(np.asarray(values), percentile)), 6)


def _error_counts(reference: str, hypothesis: str) -> dict[str, int | float]:
    aligned = jiwer.process_words(reference, hypothesis)
    return {
        "substitutions": int(aligned.substitutions),
        "deletions": int(aligned.deletions),
        "insertions": int(aligned.insertions),
        "errors": int(aligned.substitutions + aligned.deletions + aligned.insertions),
        "reference_words": len(reference.split()),
        "wer_percentage": round(float(aligned.wer) * 100.0, 6),
    }


def _aggregate(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    total_errors = sum(int(item["errors"]) for item in measurements)
    total_words = sum(int(item["reference_words"]) for item in measurements)
    total_audio = sum(float(item["audio_seconds"]) for item in measurements)
    total_wall = sum(float(item["final_latency_ms"]) / 1000.0 for item in measurements)
    latencies = [float(item["final_latency_ms"]) for item in measurements]
    real_time_factors = [float(item["real_time_factor"]) for item in measurements]
    return {
        "sample_count": len(measurements),
        "total_reference_words": total_words,
        "total_errors": total_errors,
        "corpus_wer_percentage": (
            None if not total_words else round(total_errors / total_words * 100.0, 6)
        ),
        "audio_seconds_total": round(total_audio, 6),
        "inference_wall_seconds_total": round(total_wall, 6),
        "final_latency_p50_ms": _percentile(latencies, 50),
        "final_latency_p95_ms": _percentile(latencies, 95),
        "real_time_factor_corpus": (None if not total_wall else round(total_audio / total_wall, 6)),
        "real_time_factor_p50": _percentile(real_time_factors, 50),
        "empty_hypothesis_count": sum(
            not str(item["normalized_hypothesis"]) for item in measurements
        ),
        "process_rss_mb_max": max(
            (float(item["process_rss_mb_after"]) for item in measurements),
            default=0.0,
        ),
        "mlx_peak_memory_gb_max": max(
            (float(item["mlx_peak_memory_gb"]) for item in measurements),
            default=0.0,
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--runtime-label", default="mlx-audio")
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--prompt-manifest", type=Path, required=True)
    parser.add_argument("--language")
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.max_tokens <= 0:
        raise SystemExit("--max-tokens must be positive")
    manifest_bytes = args.prompt_manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get(
        "schema"
    ) != "model-skyline/local-asr-prompt-manifest/v1alpha1" or not isinstance(
        manifest.get("items"), list
    ):
        raise SystemExit("--prompt-manifest is not a supported local ASR panel")

    prompts: list[tuple[dict[str, Any], Path]] = []
    audio_set_digest = hashlib.sha256()
    for item in manifest["items"]:
        if not isinstance(item, dict):
            raise SystemExit("every manifest item must be an object")
        filename = item.get("audio_filename")
        expected_sha = item.get("audio_sha256")
        reference = item.get("reference")
        if not all(isinstance(value, str) for value in (filename, expected_sha, reference)):
            raise SystemExit("manifest item is missing audio filename, hash, or reference")
        audio_path = args.audio_dir / filename
        actual_sha = _sha256(audio_path)
        if actual_sha != expected_sha:
            raise SystemExit(f"audio hash mismatch: {audio_path}")
        audio_set_digest.update(str(item.get("testcase_id")).encode())
        audio_set_digest.update(bytes.fromhex(actual_sha))
        prompts.append((item, audio_path))
    if not prompts:
        raise SystemExit("prompt manifest contains no cases")

    snapshot_path = Path(
        snapshot_download(repo_id=args.model, revision=args.model_revision)
    ).resolve()
    resolved_revision = snapshot_path.name
    load_started = time.monotonic()
    model = load_model(args.model, revision=args.model_revision)
    model_load_seconds = time.monotonic() - load_started
    language_argument = _resolve_language_argument(model, args.language)
    resident_rss_mb = _process_rss_mb()
    resident_mlx_active_gb = float(mx.get_active_memory()) / 1e9
    resident_mlx_cache_gb = float(mx.get_cache_memory()) / 1e9

    warm_started = time.monotonic()
    _transcribe(model, prompts[0][1], language_argument, args.max_tokens)
    warmup_seconds = time.monotonic() - warm_started

    normalizer = EnglishTextNormalizer()
    measurements: list[dict[str, Any]] = []
    for item, audio_path in prompts:
        mx.reset_peak_memory()
        started = time.monotonic()
        hypothesis = _transcribe(model, audio_path, language_argument, args.max_tokens)
        wall_seconds = time.monotonic() - started
        normalized_reference = str(normalizer(item["reference"]))
        normalized_hypothesis = str(normalizer(hypothesis))
        counts = _error_counts(normalized_reference, normalized_hypothesis)
        audio_seconds = float(item["audio_seconds"])
        measurements.append(
            {
                "testcase_id": item.get("testcase_id"),
                "domain": item.get("domain"),
                "source_row_id": item.get("source_row_id"),
                "audio_filename": audio_path.name,
                "audio_sha256": item["audio_sha256"],
                "audio_seconds": audio_seconds,
                "reference": item["reference"],
                "hypothesis": hypothesis,
                "normalized_reference": normalized_reference,
                "normalized_hypothesis": normalized_hypothesis,
                "final_latency_ms": round(wall_seconds * 1000.0, 6),
                "real_time_factor": round(audio_seconds / wall_seconds, 6),
                "process_rss_mb_after": round(_process_rss_mb(), 6),
                "mlx_active_memory_gb_after": round(float(mx.get_active_memory()) / 1e9, 6),
                "mlx_cache_memory_gb_after": round(float(mx.get_cache_memory()) / 1e9, 6),
                "mlx_peak_memory_gb": round(float(mx.get_peak_memory()) / 1e9, 6),
                **counts,
            }
        )

    by_domain: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for measurement in measurements:
        by_domain[str(measurement["domain"])].append(measurement)
    payload = {
        "schema": "model-skyline/experimental-local-asr-capture/v1alpha1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "offering": {
            "model": args.model,
            "requested_revision": args.model_revision,
            "resolved_revision": resolved_revision,
            "runtime": args.runtime_label,
            "mlx_audio": importlib.metadata.version("mlx-audio"),
            "mlx": importlib.metadata.version("mlx"),
            "hardware": _hardware_name(),
            "os": platform.platform(),
            "requested_language": args.language,
            "language_argument": language_argument,
            "max_tokens": args.max_tokens,
        },
        "workload": {
            "manifest_id": manifest.get("id"),
            "manifest_version": manifest.get("version"),
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "audio_set_sha256": audio_set_digest.hexdigest(),
            "sample_count": len(prompts),
            "normalization": NORM_VERSION,
        },
        "methodology": {
            "warmup": "first manifest clip, unscored, after model load",
            "request_order": "manifest order, sequential, batch size one",
            "latency": (
                "wall time from invocation on a complete resident audio file until "
                "the final transcript object returns"
            ),
            "real_time_factor": "input audio seconds divided by inference wall seconds",
            "wer": (
                "corpus and per-domain WER after Whisper EnglishTextNormalizer; "
                "compatible in shape with Open ASR Leaderboard normalization but "
                "this 24-clip panel is not a leaderboard result"
            ),
            "memory_warning": (
                "MLX allocation counters and process RSS are retained separately. "
                "Do not compare either to CUDA allocation-only peaks as whole-service "
                "memory."
            ),
        },
        "model_load_seconds": round(model_load_seconds, 6),
        "warmup_seconds": round(warmup_seconds, 6),
        "resident_memory": {
            "process_rss_mb": round(resident_rss_mb, 6),
            "mlx_active_memory_gb": round(resident_mlx_active_gb, 6),
            "mlx_cache_memory_gb": round(resident_mlx_cache_gb, 6),
        },
        "measurements": measurements,
        "summary": _aggregate(measurements),
        "domain_summaries": {
            domain: _aggregate(domain_measurements)
            for domain, domain_measurements in sorted(by_domain.items())
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
