#!/usr/bin/env python3
"""Measure the official Qwen3-TTS CUDA path without playing audio.

The official Python API returns complete waveforms, so this helper records
end-to-end synthesis throughput and explicitly does not claim TTFA.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
from qwen_tts import Qwen3TTSModel

AUDIBLE_RMS_THRESHOLD = 0.01
AUDIBLE_FRAME_SECONDS = 0.010
AUDIBLE_HOP_SECONDS = 0.001


def _first_audible_offset_ms(audio: np.ndarray, sample_rate: int) -> float | None:
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    )
    parser.add_argument(
        "--text",
        default="Hello. This is a resident local voice latency check.",
    )
    parser.add_argument("--speaker", default="Vivian")
    parser.add_argument("--language", default="English")
    parser.add_argument("--attention", default="sdpa")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument(
        "--non-streaming-mode",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be at least 1")
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required")

    load_started = time.monotonic()
    model = Qwen3TTSModel.from_pretrained(
        args.model,
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation=args.attention,
    )
    torch.cuda.synchronize()
    model_load_seconds = time.monotonic() - load_started

    measurements: list[dict[str, Any]] = []
    for repetition in range(1, args.repetitions + 1):
        torch.cuda.reset_peak_memory_stats()
        torch.manual_seed(repetition)
        started = time.monotonic()
        waveforms, sample_rate = model.generate_custom_voice(
            text=args.text,
            language=args.language,
            speaker=args.speaker,
            non_streaming_mode=args.non_streaming_mode,
        )
        torch.cuda.synchronize()
        wall_seconds = time.monotonic() - started
        audio = np.asarray(waveforms[0], dtype=np.float32).reshape(-1)
        audio_seconds = float(audio.size / sample_rate)
        measurements.append(
            {
                "repetition": repetition,
                "wall_seconds": round(wall_seconds, 6),
                "audio_seconds": round(audio_seconds, 6),
                "real_time_factor": round(audio_seconds / wall_seconds, 6),
                "leading_silence_ms": _first_audible_offset_ms(audio, sample_rate),
                "sample_rate_hz": int(sample_rate),
                "cuda_peak_allocated_gb": round(
                    torch.cuda.max_memory_allocated() / 1e9, 6
                ),
                "cuda_peak_reserved_gb": round(
                    torch.cuda.max_memory_reserved() / 1e9, 6
                ),
                "ttfa_ms": None,
            }
        )

    resident_rtf = [float(item["real_time_factor"]) for item in measurements[1:]]
    payload = {
        "schema": "model-skyline/experimental-qwen-tts-cuda-capture/v1alpha1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "offering": {
            "model": args.model,
            "runtime": f"qwen-tts {importlib.metadata.version('qwen-tts')}",
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "hardware": torch.cuda.get_device_name(),
            "compute_capability": ".".join(
                str(item) for item in torch.cuda.get_device_capability()
            ),
            "os": platform.platform(),
            "speaker": args.speaker,
            "language": args.language,
            "attention": args.attention,
            "non_streaming_mode": args.non_streaming_mode,
        },
        "workload": {
            "text": args.text,
            "text_character_count": len(args.text),
            "repetitions": args.repetitions,
            "process_model": "one model load followed by repeated requests",
        },
        "methodology": {
            "warning": (
                "The official API returns complete waveforms. These are synthesis "
                "throughput measurements, not time-to-first-audio measurements."
            ),
            "audible_rms_threshold": AUDIBLE_RMS_THRESHOLD,
            "audible_frame_seconds": AUDIBLE_FRAME_SECONDS,
            "audible_hop_seconds": AUDIBLE_HOP_SECONDS,
        },
        "model_load_seconds": round(model_load_seconds, 6),
        "measurements": measurements,
        "resident_summary_excluding_first_request": {
            "sample_count": len(resident_rtf),
            "real_time_factor_p50": _percentile(resident_rtf, 50),
            "real_time_factor_p05": _percentile(resident_rtf, 5),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
