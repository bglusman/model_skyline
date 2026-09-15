#!/usr/bin/env python3
"""Benchmark one pinned Transformers ASR offering on the retained audio panel.

The harness intentionally measures complete-file transcription, matching the
MLX pilot clock. It performs one unscored warmup, synchronizes CUDA around each
timed request, never plays audio, and retains CPU/GPU memory instruments
separately so unlike memory accounting is not silently mixed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Protocol

import jiwer
import numpy as np
import psutil  # type: ignore[import-untyped]
import soundfile as sf  # type: ignore[import-not-found]
import torch  # type: ignore[import-not-found]
from huggingface_hub import snapshot_download
from transformers import (
    AutoModelForMultimodalLM,
    AutoModelForSpeechSeq2Seq,
    AutoModelForTDT,
    AutoProcessor,
)
from whisper_normalizer.english import (  # type: ignore[import-untyped]
    EnglishTextNormalizer,
)

NORM_VERSION = "open-asr-leaderboard-compatible-whisper-english-v1"


class ASRAdapter(Protocol):
    model: Any
    processor: Any

    def transcribe(self, path: Path, language: str | None, max_tokens: int) -> str: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _process_rss_mb() -> float:
    return float(psutil.Process().memory_info().rss) / (1024 * 1024)


def _gpu_identity() -> dict[str, Any]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    name, driver, memory_mib = [part.strip() for part in result.stdout.split(",")]
    major, minor = torch.cuda.get_device_capability(0)
    return {
        "name": name,
        "driver": driver,
        "memory_mib": int(memory_mib),
        "compute_capability": f"{major}.{minor}",
    }


def _read_audio(path: Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim != 1:
        raise ValueError(f"expected mono audio: {path}")
    return audio, int(sample_rate)


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)) and value:
        return str(value[0]).strip()
    return str(value).strip()


class Qwen3ASRAdapter:
    def __init__(self, model_id: str, revision: str, dtype: torch.dtype) -> None:
        self.processor = AutoProcessor.from_pretrained(  # type: ignore[no-untyped-call]
            model_id, revision=revision
        )
        self.model = AutoModelForMultimodalLM.from_pretrained(
            model_id,
            revision=revision,
            dtype=dtype,
            attn_implementation="sdpa",
        ).to("cuda")
        self.model.eval()

    def transcribe(self, path: Path, language: str | None, max_tokens: int) -> str:
        inputs = self.processor.apply_transcription_request(
            audio=[str(path)],
            language=language,
        ).to(self.model.device, self.model.dtype)
        prompt_tokens = inputs["input_ids"].shape[1]
        with torch.inference_mode():
            sequences = self.model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=False,
            )
        generated = sequences[:, prompt_tokens:]
        return _first_text(self.processor.batch_decode(generated, skip_special_tokens=True))


class ParakeetTDTAdapter:
    def __init__(self, model_id: str, revision: str, dtype: torch.dtype) -> None:
        self.processor = AutoProcessor.from_pretrained(  # type: ignore[no-untyped-call]
            model_id, revision=revision
        )
        self.model = AutoModelForTDT.from_pretrained(
            model_id,
            revision=revision,
            dtype=dtype,
        ).to("cuda")
        self.model.eval()

    def transcribe(self, path: Path, language: str | None, max_tokens: int) -> str:
        del language, max_tokens
        audio, sample_rate = _read_audio(path)
        inputs = self.processor(
            audio,
            sampling_rate=sample_rate,
            return_tensors="pt",
        ).to(device=self.model.device, dtype=self.model.dtype)
        with torch.inference_mode():
            output = self.model.generate(**inputs, return_dict_in_generate=True)
        return _first_text(self.processor.decode(output.sequences, skip_special_tokens=True))


class WhisperAdapter:
    def __init__(self, model_id: str, revision: str, dtype: torch.dtype) -> None:
        self.processor = AutoProcessor.from_pretrained(  # type: ignore[no-untyped-call]
            model_id, revision=revision
        )
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_id,
            revision=revision,
            dtype=dtype,
            attn_implementation="sdpa",
        ).to("cuda")
        self.model.eval()

    def transcribe(self, path: Path, language: str | None, max_tokens: int) -> str:
        audio, sample_rate = _read_audio(path)
        inputs = self.processor(
            audio,
            sampling_rate=sample_rate,
            return_tensors="pt",
        )
        input_features = inputs.input_features.to(
            device=self.model.device,
            dtype=self.model.dtype,
        )
        with torch.inference_mode():
            sequences = self.model.generate(
                input_features,
                language=language,
                task="transcribe",
                max_new_tokens=max_tokens,
                do_sample=False,
            )
        return _first_text(self.processor.batch_decode(sequences, skip_special_tokens=True))


def _load_adapter(family: str, model_id: str, revision: str, dtype: torch.dtype) -> ASRAdapter:
    if family == "qwen3-asr":
        return Qwen3ASRAdapter(model_id, revision, dtype)
    if family == "parakeet-tdt":
        return ParakeetTDTAdapter(model_id, revision, dtype)
    if family == "whisper":
        return WhisperAdapter(model_id, revision, dtype)
    raise ValueError(f"unsupported family: {family}")


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
    factors = [float(item["real_time_factor"]) for item in measurements]
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
        "real_time_factor_p50": _percentile(factors, 50),
        "empty_hypothesis_count": sum(
            not str(item["normalized_hypothesis"]) for item in measurements
        ),
        "process_rss_mb_max": max(
            (float(item["process_rss_mb_after"]) for item in measurements),
            default=0.0,
        ),
        "cuda_peak_allocated_gb_max": max(
            (float(item["cuda_peak_allocated_gb"]) for item in measurements),
            default=0.0,
        ),
        "cuda_peak_reserved_gb_max": max(
            (float(item["cuda_peak_reserved_gb"]) for item in measurements),
            default=0.0,
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=("qwen3-asr", "parakeet-tdt", "whisper"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--prompt-manifest", type=Path, required=True)
    parser.add_argument("--language")
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--dtype", choices=("bfloat16", "float16"), default="bfloat16")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.max_tokens <= 0:
        raise SystemExit("--max-tokens must be positive")
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required")
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
    dtype = getattr(torch, args.dtype)
    torch.cuda.reset_peak_memory_stats()
    load_started = time.monotonic()
    adapter = _load_adapter(args.family, args.model, args.model_revision, dtype)
    torch.cuda.synchronize()
    model_load_seconds = time.monotonic() - load_started
    resident_memory = {
        "process_rss_mb": round(_process_rss_mb(), 6),
        "cuda_allocated_gb": round(torch.cuda.memory_allocated() / 1e9, 6),
        "cuda_reserved_gb": round(torch.cuda.memory_reserved() / 1e9, 6),
    }

    warm_started = time.monotonic()
    adapter.transcribe(prompts[0][1], args.language, args.max_tokens)
    torch.cuda.synchronize()
    warmup_seconds = time.monotonic() - warm_started

    normalizer = EnglishTextNormalizer()
    measurements: list[dict[str, Any]] = []
    for item, audio_path in prompts:
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        started = time.monotonic()
        hypothesis = adapter.transcribe(audio_path, args.language, args.max_tokens)
        torch.cuda.synchronize()
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
                "cuda_peak_allocated_gb": round(torch.cuda.max_memory_allocated() / 1e9, 6),
                "cuda_peak_reserved_gb": round(torch.cuda.max_memory_reserved() / 1e9, 6),
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
            "resolved_revision": snapshot_path.name,
            "family": args.family,
            "runtime": f"Transformers {importlib.metadata.version('transformers')}",
            "transformers": importlib.metadata.version("transformers"),
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "hardware": _gpu_identity(),
            "os": platform.platform(),
            "requested_language": args.language,
            "language_argument": args.language,
            "max_tokens": args.max_tokens,
            "dtype": args.dtype,
            "attention": "sdpa for Qwen3-ASR and Whisper; native default for Parakeet TDT",
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
                "CUDA-synchronized wall time from reading one complete resident audio "
                "file through return of its final decoded transcript"
            ),
            "real_time_factor": "input audio seconds divided by inference wall seconds",
            "wer": (
                "corpus and per-domain WER after Whisper EnglishTextNormalizer; "
                "this 24-clip panel is not an Open ASR Leaderboard result"
            ),
            "memory_warning": (
                "CPU process RSS and PyTorch CUDA allocation/reservation peaks are "
                "separate instruments. Neither is mixed into the MLX RSS frontier."
            ),
        },
        "model_load_seconds": round(model_load_seconds, 6),
        "warmup_seconds": round(warmup_seconds, 6),
        "resident_memory": resident_memory,
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
