#!/usr/bin/env python3
"""Measure a resident LoudKit TTS engine without saving or playing audio.

LoudKit yields complete synthesis windows rather than codec frames.  For a
short, single-window prompt, first-result latency therefore includes generation
of that whole window.  Recording that distinction keeps this capture honest
when it is compared with frame-streaming runtimes.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import resource
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

import loudkit
import numpy as np

AUDIBLE_RMS_THRESHOLD = 0.01
AUDIBLE_FRAME_SECONDS = 0.010
AUDIBLE_HOP_SECONDS = 0.001
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
    """Find the first audible 10 ms frame using CoVAL's public threshold."""
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


def _max_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes; Linux and most other Unix systems report KiB.
    return value if platform.system() == "Darwin" else value * 1024


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def _case_wav_path(directory: Path, sequence: int, testcase_id: object) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(testcase_id)).strip("-.")
    return directory / f"{sequence:03d}-{safe_id or 'case'}.wav"


def _run_once(
    engine: loudkit.Engine,
    *,
    text: str,
    voice: loudkit.VoiceProfile,
    language: str,
    seed: int,
) -> tuple[dict[str, Any], np.ndarray, int]:
    started = time.monotonic()
    first_result_at: float | None = None
    chunks: list[np.ndarray] = []
    sample_rate: int | None = None
    stage_seconds = {"tokens": 0.0, "mel": 0.0, "audio": 0.0}
    hit_token_cap = False
    suspect = False

    for result in engine.stream(
        text,
        voice,
        seed=seed,
        language=language,
        latency_mode=True,
    ):
        if first_result_at is None:
            first_result_at = time.monotonic()
        if sample_rate is None:
            sample_rate = int(result.sample_rate)
        elif sample_rate != result.sample_rate:
            raise RuntimeError("LoudKit changed sample rate within one response")
        chunks.append(np.asarray(result.audio, dtype=np.float32).reshape(-1))
        stage_seconds["tokens"] += float(result.timings.tokens)
        stage_seconds["mel"] += float(result.timings.mel)
        stage_seconds["audio"] += float(result.timings.audio)
        hit_token_cap = hit_token_cap or bool(result.hit_token_cap)
        suspect = suspect or bool(result.suspect)

    finished = time.monotonic()
    if first_result_at is None or sample_rate is None or not chunks:
        raise RuntimeError("LoudKit returned no audio results")

    audio = np.concatenate(chunks)
    first_result_ms = (first_result_at - started) * 1000.0
    leading_silence_ms = _first_audible_offset_ms(audio, sample_rate)
    perceived_ttfa_ms = (
        None if leading_silence_ms is None else first_result_ms + leading_silence_ms
    )
    wall_seconds = finished - started
    audio_seconds = float(audio.size / sample_rate)
    measurement = {
        "first_result_arrival_ms": round(first_result_ms, 3),
        "leading_silence_ms": (
            None if leading_silence_ms is None else round(leading_silence_ms, 3)
        ),
        "perceived_ttfa_ms": (
            None if perceived_ttfa_ms is None else round(perceived_ttfa_ms, 3)
        ),
        "wall_seconds": round(wall_seconds, 6),
        "audio_seconds": round(audio_seconds, 6),
        "real_time_factor": round(audio_seconds / wall_seconds, 6),
        "result_count": len(chunks),
        "sample_rate_hz": sample_rate,
        "stage_seconds": {key: round(value, 6) for key, value in stage_seconds.items()},
        "hit_token_cap": hit_token_cap,
        "suspect": suspect,
        "process_peak_rss_bytes": _max_rss_bytes(),
    }
    return measurement, audio, sample_rate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="loudreader/loudr-1-turbo")
    parser.add_argument("--voice", default="joe")
    parser.add_argument("--language", default="en")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--text",
        default="Hello. This is a resident local voice latency check.",
    )
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument(
        "--prompt-manifest",
        type=Path,
        help="Run one unscored warmup followed by every {testcase_id, transcript} item.",
    )
    parser.add_argument(
        "--source-revision",
        help="Optional exact LoudKit source revision for editable/source installs.",
    )
    parser.add_argument("--output", type=Path, required=True)
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

    load_started = time.monotonic()
    engine = loudkit.load(args.model)
    voice = engine.voice(args.voice)
    model_load_seconds = time.monotonic() - load_started
    warm_started = time.monotonic()
    engine.warm(voice)
    engine_warm_seconds = time.monotonic() - warm_started

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
    for sequence, item in enumerate(work_items, start=1):
        measurement, audio, sample_rate = _run_once(
            engine,
            text=str(item["text"]),
            voice=voice,
            language=args.language,
            seed=args.seed,
        )
        measurement["sequence"] = sequence
        measurement["testcase_id"] = item["testcase_id"]
        measurement["text_character_count"] = len(str(item["text"]))
        measurement["included_in_summary"] = item["included_in_summary"]
        measurements.append(measurement)
        if args.save_wav_dir is not None:
            _write_wav(
                _case_wav_path(args.save_wav_dir, sequence, item["testcase_id"]),
                audio,
                sample_rate,
            )

    resident = [item for item in measurements if item["included_in_summary"]]
    ttfa_values = [
        float(item["perceived_ttfa_ms"])
        for item in resident
        if item["perceived_ttfa_ms"] is not None
    ]
    rtf_values = [float(item["real_time_factor"]) for item in resident]
    payload = {
        "schema": "model-skyline/experimental-loudkit-tts-capture/v1alpha1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "offering": {
            "model": args.model,
            "runtime": f"loudkit {importlib.metadata.version('loudkit')}",
            "source_revision": args.source_revision,
            "backend": engine.backend,
            "engine": engine.describe(),
            "checkpoint_sha256": engine.checkpoint_sha256,
            "hardware": _hardware_name(),
            "os": platform.platform(),
            "voice": args.voice,
            "language": args.language,
            "seed": args.seed,
        },
        "workload": (
            {
                "text": args.text,
                "text_character_count": len(args.text),
                "repetitions": args.repetitions,
                "process_model": "one model load and warm, then repeated requests",
            }
            if manifest_payload is None
            else {
                "manifest_id": manifest_payload.get("id"),
                "manifest_version": manifest_payload.get("version"),
                "manifest_sha256": manifest_digest,
                "prompt_count": len(work_items) - 1,
                "process_model": (
                    "one model load and engine warm, one unscored prompt warmup, then "
                    "one request per prompt"
                ),
            }
        ),
        "methodology": {
            "first_audio": "first complete LoudKit Result yielded in memory",
            "perceived_ttfa": "first result arrival plus leading-silence offset",
            "streaming_granularity": (
                "complete synthesis windows; a short single-window prompt is not "
                "codec-frame streaming"
            ),
            "audible_rms_threshold": AUDIBLE_RMS_THRESHOLD,
            "audible_frame_seconds": AUDIBLE_FRAME_SECONDS,
            "audible_hop_seconds": AUDIBLE_HOP_SECONDS,
            "metric_reference": METRIC_REFERENCE,
            "warning": "Component latency smoke only; no listening or intelligibility score.",
        },
        "model_load_seconds": round(model_load_seconds, 6),
        "engine_warm_seconds": round(engine_warm_seconds, 6),
        "measurements": measurements,
        "resident_summary_excluding_first_request": {
            "sample_count": len(resident),
            "perceived_ttfa_p50_ms": _percentile(ttfa_values, 50),
            "perceived_ttfa_p95_ms": _percentile(ttfa_values, 95),
            "real_time_factor_p50": _percentile(rtf_values, 50),
            "real_time_factor_p05": _percentile(rtf_values, 5),
            "process_peak_rss_bytes_max": max(
                (int(item["process_peak_rss_bytes"]) for item in resident),
                default=None,
            ),
            "token_cap_case_count": sum(bool(item["hit_token_cap"]) for item in resident),
            "suspect_case_count": sum(bool(item["suspect"]) for item in resident),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
