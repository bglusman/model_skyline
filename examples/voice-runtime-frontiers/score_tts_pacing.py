#!/usr/bin/env python3
"""Measure deterministic speaking-rate and pause proxies without playing audio.

These measurements can detect conspicuously fast output or missing pauses, but
they are not a naturalness score. Calibrate any admission threshold against a
matched human-speech control set before using it as a frontier gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
from whisper_normalizer.english import EnglishTextNormalizer

AUDIBLE_RMS_THRESHOLD = 0.01
AUDIBLE_FRAME_SECONDS = 0.010
AUDIBLE_HOP_SECONDS = 0.001
DEFAULT_MINIMUM_PAUSE_MS = 150.0
NORM_VERSION = "2"


def _case_wav_path(directory: Path, sequence: int, testcase_id: object) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(testcase_id)).strip("-.")
    return directory / f"{sequence:03d}-{safe_id or 'case'}.wav"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_pcm16_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1:
            raise ValueError(f"{path} is not mono")
        if source.getsampwidth() != 2:
            raise ValueError(f"{path} is not 16-bit PCM")
        if source.getcomptype() != "NONE":
            raise ValueError(f"{path} is compressed")
        sample_rate = source.getframerate()
        if sample_rate <= 0:
            raise ValueError(f"invalid sample rate in {path}")
        frames = source.readframes(source.getnframes())
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    return audio, sample_rate


def _audibility(audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, int, int]:
    frame_length = max(1, round(AUDIBLE_FRAME_SECONDS * sample_rate))
    hop_length = max(1, round(AUDIBLE_HOP_SECONDS * sample_rate))
    pad = frame_length // 2
    padded = np.pad(audio, pad, mode="edge")
    frames = np.lib.stride_tricks.sliding_window_view(padded, frame_length)[::hop_length]
    centered = frames - frames.mean(axis=1, keepdims=True)
    rms = np.sqrt(np.mean(centered * centered, axis=1))
    return rms > AUDIBLE_RMS_THRESHOLD, frame_length, hop_length


def _runs(values: np.ndarray) -> list[tuple[int, int]]:
    """Return half-open index ranges for true runs in a one-dimensional mask."""
    if values.ndim != 1:
        raise ValueError("run mask must be one-dimensional")
    padded = np.pad(values.astype(np.int8), (1, 1))
    changes = np.diff(padded)
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return round(float(np.percentile(np.asarray(values), percentile)), 6)


def _measure(
    path: Path,
    reference: str,
    normalizer: EnglishTextNormalizer,
    minimum_pause_ms: float,
) -> dict[str, Any]:
    audio, sample_rate = _read_pcm16_mono(path)
    if not audio.size:
        raise ValueError(f"{path} contains no samples")
    audible, frame_length, hop_length = _audibility(audio, sample_rate)
    audible_indices = np.flatnonzero(audible)
    if not audible_indices.size:
        raise ValueError(f"{path} contains no audible frames")

    first = int(audible_indices[0])
    last = int(audible_indices[-1])
    audible_span_seconds = (
        (last - first) * hop_length + frame_length
    ) / sample_rate
    interior = audible[first : last + 1]
    minimum_pause_frames = math.ceil(minimum_pause_ms / 1000.0 * sample_rate / hop_length)
    pauses_ms = [
        (end - start) * hop_length / sample_rate * 1000.0
        for start, end in _runs(~interior)
        if end - start >= minimum_pause_frames
    ]
    normalized_reference = str(normalizer(reference))
    word_count = len(normalized_reference.split())
    if not word_count:
        raise ValueError(f"{path} has an empty normalized reference")
    words_per_minute = word_count / audible_span_seconds * 60.0
    pause_seconds = sum(pauses_ms) / 1000.0
    return {
        "audio_sha256": _sha256(path),
        "sample_rate_hz": sample_rate,
        "audio_seconds": round(audio.size / sample_rate, 6),
        "audible_span_seconds": round(audible_span_seconds, 6),
        "normalized_reference": normalized_reference,
        "normalized_word_count": word_count,
        "words_per_minute": round(words_per_minute, 6),
        "audible_frame_fraction_within_span": round(float(interior.mean()), 6),
        "internal_pause_count": len(pauses_ms),
        "internal_pause_seconds": round(pause_seconds, 6),
        "internal_pause_fraction_of_span": round(
            pause_seconds / audible_span_seconds,
            6,
        ),
        "internal_pause_p50_ms": _percentile(pauses_ms, 50),
        "internal_pause_p95_ms": _percentile(pauses_ms, 95),
        "internal_pause_max_ms": None if not pauses_ms else round(max(pauses_ms), 6),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--prompt-manifest", type=Path, required=True)
    parser.add_argument(
        "--minimum-pause-ms",
        type=float,
        default=DEFAULT_MINIMUM_PAUSE_MS,
        help="Minimum continuous below-threshold interval counted as an internal pause.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not math.isfinite(args.minimum_pause_ms) or args.minimum_pause_ms <= 0:
        raise SystemExit("--minimum-pause-ms must be finite and positive")
    manifest_bytes = args.prompt_manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), list):
        raise SystemExit("--prompt-manifest must contain an object with an items array")

    normalizer = EnglishTextNormalizer()
    measurements: list[dict[str, Any]] = []
    audio_set_digest = hashlib.sha256()
    for sequence, item in enumerate(manifest["items"], start=2):
        if not isinstance(item, dict):
            raise SystemExit("every prompt-manifest item must be an object")
        testcase_id = item.get("testcase_id")
        transcript = item.get("transcript")
        if not isinstance(testcase_id, str) or not isinstance(transcript, str):
            raise SystemExit(
                "every prompt-manifest item needs string testcase_id and transcript"
            )
        audio_path = _case_wav_path(args.audio_dir, sequence, testcase_id)
        measured = _measure(
            audio_path,
            transcript,
            normalizer,
            args.minimum_pause_ms,
        )
        audio_set_digest.update(testcase_id.encode())
        audio_set_digest.update(bytes.fromhex(measured["audio_sha256"]))
        measured["testcase_id"] = testcase_id
        measurements.append(measured)

    total_words = sum(item["normalized_word_count"] for item in measurements)
    total_span = sum(item["audible_span_seconds"] for item in measurements)
    words_per_minute = [float(item["words_per_minute"]) for item in measurements]
    pause_fractions = [
        float(item["internal_pause_fraction_of_span"]) for item in measurements
    ]
    payload = {
        "schema": "model-skyline/experimental-tts-pacing-capture/v1alpha1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "manifest_id": manifest.get("id"),
            "manifest_version": manifest.get("version"),
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "audio_set_sha256": audio_set_digest.hexdigest(),
        },
        "methodology": {
            "normalization": "whisper_normalizer EnglishTextNormalizer",
            "normalization_version": NORM_VERSION,
            "audible_rms_threshold": AUDIBLE_RMS_THRESHOLD,
            "audible_frame_seconds": AUDIBLE_FRAME_SECONDS,
            "audible_hop_seconds": AUDIBLE_HOP_SECONDS,
            "minimum_internal_pause_ms": args.minimum_pause_ms,
            "speaking_rate": (
                "normalized reference words divided by the span from first to last "
                "audible frame"
            ),
            "pause_proxy": (
                "continuous below-threshold frame runs inside the audible span; "
                "not text-aligned punctuation pauses"
            ),
            "warning": (
                "Descriptive deterministic proxies only. Admission thresholds require "
                "matched human-speech calibration and do not establish naturalness."
            ),
        },
        "measurements": measurements,
        "summary": {
            "sample_count": len(measurements),
            "normalized_reference_word_count": total_words,
            "audible_span_seconds_total": round(total_span, 6),
            "corpus_words_per_minute": round(total_words / total_span * 60.0, 6),
            "words_per_minute_p50": _percentile(words_per_minute, 50),
            "words_per_minute_p95": _percentile(words_per_minute, 95),
            "internal_pause_count_total": sum(
                item["internal_pause_count"] for item in measurements
            ),
            "internal_pause_fraction_p50": _percentile(pause_fractions, 50),
            "internal_pause_fraction_p95": _percentile(pause_fractions, 95),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
