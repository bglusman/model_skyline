#!/usr/bin/env python3
"""Probe long-form TTS audio for unexpected speaker changes without playback.

The diarizer is a fallible measurement instrument, not ground truth. Its
thresholds must pass matched same-speaker and different-speaker controls before
this output can become a frontier admission gate.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, TypedDict

import mlx.core as mx
import numpy as np
from huggingface_hub import try_to_load_from_cache
from mlx_audio.vad import load

DEFAULT_MODEL = "mlx-community/diar_streaming_sortformer_4spk-v2.1-fp16"
DEFAULT_REVISION = "e23e6404bd9859e93edbf94a740eb1c7fc58f12e"


class SegmentRecord(TypedDict):
    start_seconds: float
    end_seconds: float
    speaker: int


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


def _case_wav_path(directory: Path, sequence: int, testcase_id: object) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(testcase_id)).strip("-.")
    return directory / f"{sequence:03d}-{safe_id or 'case'}.wav"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _union_duration(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    total = 0.0
    start, end = ordered[0]
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return total + end - start


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return round(float(np.percentile(np.asarray(values), percentile)), 6)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--prompt-manifest", type=Path, required=True)
    parser.add_argument("--diarizer-model", default=DEFAULT_MODEL)
    parser.add_argument("--diarizer-revision", default=DEFAULT_REVISION)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--minimum-segment-seconds", type=float, default=0.24)
    parser.add_argument("--merge-gap-seconds", type=float, default=0.16)
    parser.add_argument(
        "--latency-capture",
        type=Path,
        help="Synthesis capture created with the same audio, retained by SHA-256.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not 0 < args.threshold < 1:
        raise SystemExit("--threshold must be between zero and one")
    if args.minimum_segment_seconds < 0 or args.merge_gap_seconds < 0:
        raise SystemExit("segment duration and merge gap must be non-negative")
    manifest_bytes = args.prompt_manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), list):
        raise SystemExit("--prompt-manifest must contain an object with an items array")

    load_started = time.monotonic()
    model = load(args.diarizer_model, revision=args.diarizer_revision)
    model_load_seconds = time.monotonic() - load_started
    cached_config = try_to_load_from_cache(
        repo_id=args.diarizer_model,
        filename="config.json",
        revision=args.diarizer_revision,
    )
    if not isinstance(cached_config, str):
        raise SystemExit("loaded diarizer did not leave a cached config.json")
    resolved_revision = Path(cached_config).parent.name

    mx.reset_peak_memory()
    audio_set_digest = hashlib.sha256()
    measurements: list[dict[str, Any]] = []
    for sequence, item in enumerate(manifest["items"], start=2):
        if not isinstance(item, dict) or not isinstance(item.get("testcase_id"), str):
            raise SystemExit("every prompt-manifest item needs a string testcase_id")
        testcase_id = item["testcase_id"]
        audio_path = _case_wav_path(args.audio_dir, sequence, testcase_id)
        audio_sha256 = _sha256(audio_path)
        audio_set_digest.update(testcase_id.encode())
        audio_set_digest.update(bytes.fromhex(audio_sha256))

        started = time.monotonic()
        result = model.generate(
            str(audio_path),
            threshold=args.threshold,
            min_duration=args.minimum_segment_seconds,
            merge_gap=args.merge_gap_seconds,
            verbose=False,
        )
        wall_seconds = time.monotonic() - started
        segments: list[SegmentRecord] = [
            {
                "start_seconds": round(float(segment.start), 6),
                "end_seconds": round(float(segment.end), 6),
                "speaker": int(segment.speaker),
            }
            for segment in result.segments
        ]
        duration_by_speaker: defaultdict[int, float] = defaultdict(float)
        for segment in segments:
            duration_by_speaker[segment["speaker"]] += (
                segment["end_seconds"] - segment["start_seconds"]
            )
        primary_speaker = (
            None
            if not duration_by_speaker
            else max(duration_by_speaker, key=duration_by_speaker.__getitem__)
        )
        all_intervals = [
            (segment["start_seconds"], segment["end_seconds"])
            for segment in segments
        ]
        secondary_intervals = [
            (segment["start_seconds"], segment["end_seconds"])
            for segment in segments
            if segment["speaker"] != primary_speaker
        ]
        activity_seconds = _union_duration(all_intervals)
        secondary_seconds = _union_duration(secondary_intervals)
        labels = [segment["speaker"] for segment in segments]
        speaker_change_count = sum(
            current != previous
            for previous, current in zip(labels, labels[1:], strict=False)
        )
        measurements.append(
            {
                "testcase_id": testcase_id,
                "audio_sha256": audio_sha256,
                "inference_wall_seconds": round(wall_seconds, 6),
                "num_speakers": int(result.num_speakers),
                "unexpected_speaker_count": max(0, int(result.num_speakers) - 1),
                "primary_speaker": primary_speaker,
                "speaker_change_count": speaker_change_count,
                "detected_activity_seconds": round(activity_seconds, 6),
                "secondary_speaker_seconds": round(secondary_seconds, 6),
                "secondary_speaker_fraction": (
                    None
                    if not activity_seconds
                    else round(secondary_seconds / activity_seconds, 6)
                ),
                "segments": segments,
            }
        )

    secondary_fractions = [
        float(item["secondary_speaker_fraction"])
        for item in measurements
        if item["secondary_speaker_fraction"] is not None
    ]
    payload = {
        "schema": "model-skyline/experimental-tts-speaker-drift-capture/v1alpha1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "instrument": {
            "model": args.diarizer_model,
            "requested_revision": args.diarizer_revision,
            "resolved_revision": resolved_revision,
            "runtime": f"mlx-audio {importlib.metadata.version('mlx-audio')}",
            "mlx": importlib.metadata.version("mlx"),
            "hardware": _hardware_name(),
            "os": platform.platform(),
        },
        "source": {
            "manifest_id": manifest.get("id"),
            "manifest_version": manifest.get("version"),
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "audio_set_sha256": audio_set_digest.hexdigest(),
            "latency_capture_sha256": (
                None if args.latency_capture is None else _sha256(args.latency_capture)
            ),
        },
        "methodology": {
            "threshold": args.threshold,
            "minimum_segment_seconds": args.minimum_segment_seconds,
            "merge_gap_seconds": args.merge_gap_seconds,
            "primary_speaker": "speaker label with the greatest summed segment duration",
            "unexpected_speaker": "any detected label other than the primary speaker",
            "calibration_status": (
                "pilot settings distinguish local same-voice concatenations from one "
                "deliberately mixed Qwen-Vivian/LoudKit-Joe control; matched human "
                "controls are still required before use as an admission gate"
            ),
            "warning": (
                "Diarizer output is a fallible proxy. It does not prove speaker identity "
                "or naturalness, and no frontier gate is applied yet."
            ),
        },
        "model_load_seconds": round(model_load_seconds, 6),
        "mlx_peak_memory_gb": round(float(mx.get_peak_memory()) / 1e9, 6),
        "measurements": measurements,
        "summary": {
            "sample_count": len(measurements),
            "cases_with_unexpected_speaker": sum(
                item["unexpected_speaker_count"] > 0 for item in measurements
            ),
            "unexpected_speaker_count_max": max(
                (item["unexpected_speaker_count"] for item in measurements),
                default=0,
            ),
            "speaker_change_count_total": sum(
                item["speaker_change_count"] for item in measurements
            ),
            "secondary_speaker_fraction_p50": _percentile(secondary_fractions, 50),
            "secondary_speaker_fraction_p95": _percentile(secondary_fractions, 95),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
