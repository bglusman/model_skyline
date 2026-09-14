#!/usr/bin/env python3
"""Measure resident MLX TTS latency without playing audio.

This is an experimental capture helper, not a model-quality benchmark. It keeps
one model loaded across repetitions so cold compilation and warm requests remain
visible as different measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import re
import resource
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

import mlx.core as mx
import numpy as np
from huggingface_hub import snapshot_download
from mlx_audio.tts.utils import load_model

AUDIBLE_RMS_THRESHOLD = 0.01
AUDIBLE_FRAME_SECONDS = 0.010
AUDIBLE_HOP_SECONDS = 0.001
QWEN_FIXED_BOOTSTRAP_SAMPLES = 1_920
METRIC_REFERENCE = (
    "https://github.com/coval-ai/benchmarks/blob/"
    "c9786d181776ba393e85718e26e6b9f67fe19f7c/"
    "runner/src/coval_bench/metrics/ttfa.py"
)


def _hardware_name() -> str:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return platform.processor() or "unknown"
    return result.stdout.strip()


def _first_audible_offset_ms(audio: np.ndarray, sample_rate: int) -> float | None:
    """Find the first audible 10 ms frame using CoVAL's published threshold."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if samples.size == 0:
        return None
    frame_length = max(1, round(AUDIBLE_FRAME_SECONDS * sample_rate))
    hop_length = max(1, round(AUDIBLE_HOP_SECONDS * sample_rate))
    pad = frame_length // 2
    padded = np.pad(samples, pad, mode="edge")
    frames = np.lib.stride_tricks.sliding_window_view(padded, frame_length)[::hop_length]
    centered = frames - frames.mean(axis=1, keepdims=True)
    rms = np.sqrt(np.mean(centered * centered, axis=1))
    audible = np.flatnonzero(rms > AUDIBLE_RMS_THRESHOLD)
    if not audible.size:
        return None
    return float(audible[0] * hop_length / sample_rate * 1000.0)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return round(float(np.percentile(np.asarray(values), percentile)), 3)


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def _case_wav_path(directory: Path, sequence: int, testcase_id: object) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(testcase_id)).strip("-.")
    return directory / f"{sequence:03d}-{safe_id or 'case'}.wav"


def _run_once(
    model: Any,
    *,
    text: str,
    speaker: str,
    language: str,
    streaming_interval: float,
    suppress_fixed_bootstrap: bool,
    seed: int,
) -> tuple[dict[str, Any], np.ndarray, int]:
    mx.reset_peak_memory()
    mx.random.seed(seed)
    started = time.monotonic()
    first_chunk_at: float | None = None
    chunks: list[np.ndarray] = []
    sample_rate: int | None = None
    bootstrap_suppressed = False
    first_raw_chunk = True

    for result in model.generate_custom_voice(
        text=text,
        speaker=speaker,
        language=language,
        stream=True,
        streaming_interval=streaming_interval,
        verbose=False,
    ):
        chunk_arrived_at = time.monotonic()
        chunk = np.asarray(result.audio, dtype=np.float32).reshape(-1)
        if sample_rate is None:
            sample_rate = int(result.sample_rate)
        if suppress_fixed_bootstrap and first_raw_chunk:
            if chunk.size < QWEN_FIXED_BOOTSTRAP_SAMPLES:
                raise RuntimeError("first chunk is shorter than one Qwen bootstrap frame")
            bootstrap = chunk[:QWEN_FIXED_BOOTSTRAP_SAMPLES]
            centered = bootstrap - bootstrap.mean()
            bootstrap_rms = float(np.sqrt(np.mean(centered * centered)))
            if bootstrap_rms > AUDIBLE_RMS_THRESHOLD:
                raise RuntimeError(
                    "refusing to suppress an audible Qwen bootstrap frame "
                    f"(RMS {bootstrap_rms:.6f})"
                )
            chunk = chunk[QWEN_FIXED_BOOTSTRAP_SAMPLES:]
            bootstrap_suppressed = True
        first_raw_chunk = False
        if chunk.size:
            if first_chunk_at is None:
                first_chunk_at = chunk_arrived_at
            chunks.append(chunk)

    finished = time.monotonic()
    if first_chunk_at is None or sample_rate is None or not chunks:
        raise RuntimeError("TTS model returned no audio chunks")

    audio = np.concatenate(chunks)
    arrival_ms = (first_chunk_at - started) * 1000.0
    leading_silence_ms = _first_audible_offset_ms(audio, sample_rate)
    perceived_ttfa_ms = (
        None if leading_silence_ms is None else arrival_ms + leading_silence_ms
    )
    audio_seconds = float(audio.size / sample_rate)
    wall_seconds = finished - started
    measurement = {
        "first_chunk_arrival_ms": round(arrival_ms, 3),
        "leading_silence_ms": (
            None if leading_silence_ms is None else round(leading_silence_ms, 3)
        ),
        "perceived_ttfa_ms": (
            None if perceived_ttfa_ms is None else round(perceived_ttfa_ms, 3)
        ),
        "wall_seconds": round(wall_seconds, 6),
        "audio_seconds": round(audio_seconds, 6),
        "real_time_factor": round(audio_seconds / wall_seconds, 6),
        "chunk_count": len(chunks),
        "sample_rate_hz": sample_rate,
        "fixed_bootstrap_suppressed": bootstrap_suppressed,
        "seed": seed,
        "mlx_peak_memory_gb": round(float(mx.get_peak_memory()) / 1e9, 6),
    }
    return measurement, audio, sample_rate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default="mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
    )
    parser.add_argument(
        "--model-revision",
        help="Optional immutable Hugging Face revision to load and record.",
    )
    parser.add_argument(
        "--text",
        default="Hello. This is a resident local voice latency check.",
    )
    parser.add_argument("--speaker", default="Vivian")
    parser.add_argument("--language", default="English")
    parser.add_argument("--streaming-interval", type=float, default=0.32)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument(
        "--prompt-manifest",
        type=Path,
        help="Run one unscored warmup followed by every {testcase_id, transcript} item.",
    )
    parser.add_argument(
        "--suppress-fixed-bootstrap",
        action="store_true",
        help=(
            "Drop Qwen's first 1,920-sample frame only when its centered RMS is "
            "below the same audibility threshold used for TTFA."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--save-last-wav", type=Path)
    parser.add_argument(
        "--save-wav-dir",
        type=Path,
        help="Optionally retain every generated case for a separate quality pass.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be at least 1")
    if not math.isfinite(args.streaming_interval) or args.streaming_interval <= 0:
        raise SystemExit("--streaming-interval must be finite and positive")

    load_started = time.monotonic()
    model = load_model(args.model, revision=args.model_revision)
    model_load_seconds = time.monotonic() - load_started
    resolved_revision: str | None = None
    if not Path(args.model).exists():
        resolved_path = Path(
            snapshot_download(
                repo_id=args.model,
                revision=args.model_revision,
                local_files_only=True,
            )
        )
        resolved_revision = resolved_path.name

    manifest_payload: dict[str, Any] | None = None
    manifest_digest: str | None = None
    if args.prompt_manifest is None:
        work_items = [
            {
                "testcase_id": f"repetition-{repetition}",
                "text": args.text,
                "included_in_summary": repetition > 1,
            }
            for repetition in range(1, args.repetitions + 1)
        ]
    else:
        manifest_bytes = args.prompt_manifest.read_bytes()
        loaded_manifest = json.loads(manifest_bytes)
        if not isinstance(loaded_manifest, dict) or not isinstance(
            loaded_manifest.get("items"), list
        ):
            raise SystemExit("--prompt-manifest must contain an object with an items array")
        manifest_payload = loaded_manifest
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        prompts: list[dict[str, str]] = []
        for item in loaded_manifest["items"]:
            if not isinstance(item, dict):
                raise SystemExit("every prompt-manifest item must be an object")
            testcase_id = item.get("testcase_id")
            transcript = item.get("transcript")
            if not isinstance(testcase_id, str) or not isinstance(transcript, str):
                raise SystemExit(
                    "every prompt-manifest item needs string testcase_id and transcript"
                )
            prompts.append({"testcase_id": testcase_id, "text": transcript})
        if not prompts:
            raise SystemExit("--prompt-manifest must contain at least one item")
        work_items = [
            {
                "testcase_id": "warmup",
                "text": prompts[0]["text"],
                "included_in_summary": False,
            },
            *[
                {
                    "testcase_id": prompt["testcase_id"],
                    "text": prompt["text"],
                    "included_in_summary": True,
                }
                for prompt in prompts
            ],
        ]

    measurements: list[dict[str, Any]] = []
    last_audio: np.ndarray | None = None
    last_sample_rate: int | None = None
    for sequence, item in enumerate(work_items, start=1):
        result, last_audio, last_sample_rate = _run_once(
            model,
            text=str(item["text"]),
            speaker=args.speaker,
            language=args.language,
            streaming_interval=args.streaming_interval,
            suppress_fixed_bootstrap=args.suppress_fixed_bootstrap,
            seed=args.seed,
        )
        result["sequence"] = sequence
        result["testcase_id"] = item["testcase_id"]
        result["text_character_count"] = len(str(item["text"]))
        result["included_in_summary"] = item["included_in_summary"]
        measurements.append(result)
        if args.save_wav_dir is not None:
            _write_wav(
                _case_wav_path(args.save_wav_dir, sequence, item["testcase_id"]),
                last_audio,
                last_sample_rate,
            )

    if args.save_last_wav is not None:
        assert last_audio is not None and last_sample_rate is not None
        _write_wav(args.save_last_wav, last_audio, last_sample_rate)

    resident_ttfa = [
        float(item["perceived_ttfa_ms"])
        for item in measurements
        if item["included_in_summary"]
        if item["perceived_ttfa_ms"] is not None
    ]
    resident_rtf = [
        float(item["real_time_factor"])
        for item in measurements
        if item["included_in_summary"]
    ]
    resident_peak_memory = [
        float(item["mlx_peak_memory_gb"])
        for item in measurements
        if item["included_in_summary"]
    ]
    payload = {
        "schema": "model-skyline/experimental-mlx-tts-capture/v1alpha1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "offering": {
            "model": args.model,
            "requested_revision": args.model_revision,
            "resolved_revision": resolved_revision,
            "runtime": f"mlx-audio {importlib.metadata.version('mlx-audio')}",
            "mlx": importlib.metadata.version("mlx"),
            "hardware": _hardware_name(),
            "os": platform.platform(),
            "speaker": args.speaker,
            "language": args.language,
            "streaming_interval_seconds": args.streaming_interval,
            "suppress_fixed_bootstrap": args.suppress_fixed_bootstrap,
            "seed": args.seed,
        },
        "workload": (
            {
                "text": args.text,
                "text_character_count": len(args.text),
                "repetitions": args.repetitions,
                "process_model": "one model load followed by repeated requests",
            }
            if manifest_payload is None
            else {
                "manifest_id": manifest_payload.get("id"),
                "manifest_version": manifest_payload.get("version"),
                "manifest_sha256": manifest_digest,
                "prompt_count": len(work_items) - 1,
                "process_model": (
                    "one model load, one unscored warmup, then one request per prompt"
                ),
            }
        ),
        "methodology": {
            "first_audio": "first yielded in-memory audio chunk",
            "perceived_ttfa": "first chunk arrival plus leading-silence offset",
            "audible_rms_threshold": AUDIBLE_RMS_THRESHOLD,
            "audible_frame_seconds": AUDIBLE_FRAME_SECONDS,
            "audible_hop_seconds": AUDIBLE_HOP_SECONDS,
            "metric_reference": METRIC_REFERENCE,
            "fixed_bootstrap_samples": QWEN_FIXED_BOOTSTRAP_SAMPLES,
            "suppression_guard": (
                "first-frame centered RMS must not exceed audible_rms_threshold"
            ),
            "warning": "Component latency smoke only; no listening or intelligibility score.",
        },
        "model_load_seconds": round(model_load_seconds, 6),
        "measurements": measurements,
        "resident_summary_excluding_first_request": {
            "sample_count": len(resident_ttfa),
            "perceived_ttfa_p50_ms": _percentile(resident_ttfa, 50),
            "perceived_ttfa_p95_ms": _percentile(resident_ttfa, 95),
            "real_time_factor_p50": _percentile(resident_rtf, 50),
            "real_time_factor_p05": _percentile(resident_rtf, 5),
            "mlx_peak_memory_gb_max": (
                None if not resident_peak_memory else max(resident_peak_memory)
            ),
        },
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
