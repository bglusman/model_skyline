#!/usr/bin/env python3
"""Measure an OpenAI-compatible streaming TTS endpoint without playing audio.

The endpoint must return little-endian, signed 16-bit, mono PCM.  This helper
keeps one HTTP client alive across requests so the resident summary does not
mix a new TCP connection into every measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import statistics
import struct
import time
import wave
from pathlib import Path
from typing import Any

import httpx

AUDIBLE_RMS_THRESHOLD = 0.01
AUDIBLE_FRAME_SECONDS = 0.010
AUDIBLE_HOP_SECONDS = 0.001
QWEN_FIXED_BOOTSTRAP_SAMPLES = 1_920
METRIC_REFERENCE = (
    "https://github.com/coval-ai/benchmarks/blob/"
    "c9786d181776ba393e85718e26e6b9f67fe19f7c/"
    "runner/src/coval_bench/metrics/ttfa.py"
)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    fraction = position - lower
    return round(ordered[lower] * (1 - fraction) + ordered[upper] * fraction, 3)


def _first_audible_offset_samples(pcm: bytes, sample_rate: int) -> int | None:
    """Find the first audible centered-RMS frame using CoVAL's public rule."""
    if len(pcm) % 2:
        raise ValueError("PCM response has an odd byte count")
    samples = [sample[0] / 32768.0 for sample in struct.iter_unpack("<h", pcm)]
    if not samples:
        return None
    frame_length = max(1, round(AUDIBLE_FRAME_SECONDS * sample_rate))
    hop_length = max(1, round(AUDIBLE_HOP_SECONDS * sample_rate))
    pad = frame_length // 2
    padded = [samples[0]] * pad + samples + [samples[-1]] * pad
    for start in range(0, len(samples), hop_length):
        frame = padded[start : start + frame_length]
        if len(frame) < frame_length:
            break
        mean = statistics.fmean(frame)
        rms = math.sqrt(statistics.fmean((sample - mean) ** 2 for sample in frame))
        if rms > AUDIBLE_RMS_THRESHOLD:
            return start
    return None


def _centered_rms(pcm: bytes) -> float:
    samples = [sample[0] / 32768.0 for sample in struct.iter_unpack("<h", pcm)]
    mean = statistics.fmean(samples)
    return math.sqrt(statistics.fmean((sample - mean) ** 2 for sample in samples))


def _playback_time_ms(
    *,
    chunks: list[bytes],
    arrivals: list[float],
    audible_sample: int,
    started: float,
    sample_rate: int,
) -> float:
    """Map an audible sample to wall time, pausing playback on buffer underruns."""
    consumed_samples = 0
    playback_cursor = started
    for chunk, arrived_at in zip(chunks, arrivals, strict=True):
        chunk_samples = len(chunk) // 2
        chunk_playback_started = max(arrived_at, playback_cursor)
        if audible_sample < consumed_samples + chunk_samples:
            offset = (audible_sample - consumed_samples) / sample_rate
            return (chunk_playback_started + offset - started) * 1000.0
        consumed_samples += chunk_samples
        playback_cursor = chunk_playback_started + chunk_samples / sample_rate
    raise RuntimeError("audible sample falls outside the streamed PCM response")


def _write_pcm_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm)


def _case_wav_path(directory: Path, sequence: int, testcase_id: object) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(testcase_id)).strip("-.")
    return directory / f"{sequence:03d}-{safe_id or 'case'}.wav"


def _run_once(
    client: httpx.Client,
    *,
    endpoint: str,
    request_body: dict[str, Any],
    sample_rate: int,
    suppress_fixed_bootstrap: bool,
) -> tuple[dict[str, Any], bytes]:
    started = time.monotonic()
    first_body_at: float | None = None
    chunks: list[bytes] = []
    chunk_arrivals: list[float] = []
    content_type: str | None = None
    with client.stream("POST", endpoint, json=request_body) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type")
        for chunk in response.iter_raw():
            if not chunk:
                continue
            if first_body_at is None:
                first_body_at = time.monotonic()
            chunks.append(chunk)
            chunk_arrivals.append(time.monotonic())
    finished = time.monotonic()
    if first_body_at is None:
        raise RuntimeError("TTS endpoint returned no response body")

    raw_pcm = b"".join(chunks)
    delivered_chunks = chunks
    delivered_arrivals = chunk_arrivals
    bootstrap_suppressed = False
    if suppress_fixed_bootstrap:
        bootstrap_bytes = QWEN_FIXED_BOOTSTRAP_SAMPLES * 2
        if len(raw_pcm) < bootstrap_bytes:
            raise RuntimeError("response is shorter than one Qwen bootstrap frame")
        bootstrap_rms = _centered_rms(raw_pcm[:bootstrap_bytes])
        if bootstrap_rms > AUDIBLE_RMS_THRESHOLD:
            raise RuntimeError(
                f"refusing to suppress an audible Qwen bootstrap frame (RMS {bootstrap_rms:.6f})"
            )
        delivered_chunks = []
        delivered_arrivals = []
        remaining = bootstrap_bytes
        for chunk, arrived_at in zip(chunks, chunk_arrivals, strict=True):
            if remaining >= len(chunk):
                remaining -= len(chunk)
                continue
            delivered_chunks.append(chunk[remaining:])
            delivered_arrivals.append(arrived_at)
            remaining = 0
        if not delivered_chunks:
            raise RuntimeError("response contains no audio after the Qwen bootstrap frame")
        bootstrap_suppressed = True

    pcm = b"".join(delivered_chunks)
    delivery_at = delivered_arrivals[0]
    first_body_ms = (first_body_at - started) * 1000.0
    first_deliverable_ms = (delivery_at - started) * 1000.0
    audible_sample = _first_audible_offset_samples(pcm, sample_rate)
    leading_silence_ms = None if audible_sample is None else audible_sample / sample_rate * 1000.0
    coval_ttfa_ms = (
        None if leading_silence_ms is None else first_deliverable_ms + leading_silence_ms
    )
    playback_ttfa_ms = (
        None
        if audible_sample is None
        else _playback_time_ms(
            chunks=delivered_chunks,
            arrivals=delivered_arrivals,
            audible_sample=audible_sample,
            started=started,
            sample_rate=sample_rate,
        )
    )
    pre_audible_buffer_delay_ms = (
        None
        if coval_ttfa_ms is None or playback_ttfa_ms is None
        else max(0.0, playback_ttfa_ms - coval_ttfa_ms)
    )
    wall_seconds = finished - started
    audio_seconds = len(pcm) / 2 / sample_rate
    measurement = {
        "first_body_byte_ms": round(first_body_ms, 3),
        "first_deliverable_audio_byte_ms": round(first_deliverable_ms, 3),
        "leading_silence_ms": (
            None if leading_silence_ms is None else round(leading_silence_ms, 3)
        ),
        "coval_ttfa_ms": (None if coval_ttfa_ms is None else round(coval_ttfa_ms, 3)),
        "playback_ttfa_ms": (None if playback_ttfa_ms is None else round(playback_ttfa_ms, 3)),
        "pre_audible_buffer_delay_ms": (
            None if pre_audible_buffer_delay_ms is None else round(pre_audible_buffer_delay_ms, 3)
        ),
        "wall_seconds": round(wall_seconds, 6),
        "audio_seconds": round(audio_seconds, 6),
        "real_time_factor": round(audio_seconds / wall_seconds, 6),
        "body_chunk_count": len(chunks),
        "delivered_chunk_count": len(delivered_chunks),
        "raw_response_bytes": len(raw_pcm),
        "delivered_response_bytes": len(pcm),
        "content_type": content_type,
        "fixed_bootstrap_suppressed": bootstrap_suppressed,
    }
    return measurement, pcm


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--endpoint-label",
        help=(
            "Public-safe endpoint label retained in the capture instead of the "
            "actual network address."
        ),
    )
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--model-revision",
        help="Immutable server-side model revision, recorded but not inferred by the client.",
    )
    parser.add_argument("--provider", default="self-hosted")
    parser.add_argument("--runtime-label", required=True)
    parser.add_argument("--server-hardware", required=True)
    parser.add_argument(
        "--text",
        default="Hello. This is a resident local voice latency check.",
    )
    parser.add_argument("--voice", default="vivian")
    parser.add_argument("--language", default="English")
    parser.add_argument("--sample-rate", type=int, default=24_000)
    parser.add_argument("--repetitions", type=int, default=6)
    parser.add_argument(
        "--stream-format",
        choices=("audio", "sse", "omit"),
        default="audio",
        help="OpenAI-compatible stream_format value, or omit for strict servers.",
    )
    parser.add_argument(
        "--non-streaming-mode",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Optional server-specific generation mode; omitted unless explicitly set.",
    )
    parser.add_argument(
        "--prompt-manifest",
        type=Path,
        help="Run one unscored warmup followed by every {testcase_id, transcript} item.",
    )
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument(
        "--suppress-fixed-bootstrap",
        action="store_true",
        help=(
            "Drop Qwen's first 1,920-sample frame only when its centered RMS is "
            "below the same audibility threshold used for TTFA."
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--save-wav-dir",
        type=Path,
        help="Optionally retain every delivered case for a separate quality pass.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be at least 1")
    if args.sample_rate < 1:
        raise SystemExit("--sample-rate must be positive")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be finite and positive")

    endpoint = args.base_url.rstrip("/") + "/v1/audio/speech"
    request_body = {
        "input": args.text,
        "model": args.model,
        "voice": args.voice,
        "language": args.language,
        "response_format": "pcm",
        "stream": True,
        "seed": args.seed,
    }
    if args.stream_format != "omit":
        request_body["stream_format"] = args.stream_format
    if args.non_streaming_mode is not None:
        request_body["non_streaming_mode"] = args.non_streaming_mode
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
    with httpx.Client(timeout=args.timeout_seconds) as client:
        for sequence, item in enumerate(work_items, start=1):
            request_body["input"] = item["text"]
            measurement, pcm = _run_once(
                client,
                endpoint=endpoint,
                request_body=request_body,
                sample_rate=args.sample_rate,
                suppress_fixed_bootstrap=args.suppress_fixed_bootstrap,
            )
            measurement["sequence"] = sequence
            measurement["testcase_id"] = item["testcase_id"]
            measurement["text_character_count"] = len(str(item["text"]))
            measurement["included_in_summary"] = item["included_in_summary"]
            measurements.append(measurement)
            if args.save_wav_dir is not None:
                _write_pcm_wav(
                    _case_wav_path(args.save_wav_dir, sequence, item["testcase_id"]),
                    pcm,
                    args.sample_rate,
                )

    warm_measurements = [item for item in measurements if item["included_in_summary"]]
    coval_ttfa_values = [
        float(item["coval_ttfa_ms"])
        for item in warm_measurements
        if item["coval_ttfa_ms"] is not None
    ]
    playback_ttfa_values = [
        float(item["playback_ttfa_ms"])
        for item in warm_measurements
        if item["playback_ttfa_ms"] is not None
    ]
    rtf_values = [float(item["real_time_factor"]) for item in warm_measurements]
    buffer_delays = [
        float(item["pre_audible_buffer_delay_ms"])
        for item in warm_measurements
        if item["pre_audible_buffer_delay_ms"] is not None
    ]
    payload = {
        "schema": "model-skyline/experimental-openai-tts-capture/v1alpha1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "harness": {
            "script": Path(__file__).name,
            "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "offering": {
            "model": args.model,
            "model_revision": args.model_revision,
            "provider": args.provider,
            "runtime": args.runtime_label,
            "server_hardware": args.server_hardware,
            "endpoint": args.endpoint_label or endpoint,
            "client_hardware": platform.processor() or platform.machine(),
            "client_os": platform.platform(),
            "voice": args.voice,
            "language": args.language,
            "seed": args.seed,
            "suppress_fixed_bootstrap": args.suppress_fixed_bootstrap,
            "request_stream_format": (None if args.stream_format == "omit" else args.stream_format),
            "request_non_streaming_mode": args.non_streaming_mode,
        },
        "workload": (
            {
                "text": args.text,
                "text_character_count": len(args.text),
                "repetitions": args.repetitions,
                "process_model": "one persistent HTTP client and a resident service",
            }
            if manifest_payload is None
            else {
                "manifest_id": manifest_payload.get("id"),
                "manifest_version": manifest_payload.get("version"),
                "manifest_sha256": manifest_digest,
                "prompt_count": len(work_items) - 1,
                "process_model": (
                    "one persistent HTTP client, one unscored warmup, then one request per prompt"
                ),
            }
        ),
        "methodology": {
            "first_audio": "first non-empty HTTP response-body bytes observed by client",
            "coval_ttfa": (
                "first deliverable body arrival plus leading-silence offset; assumes "
                "continuous playback"
            ),
            "playback_ttfa": (
                "wall time when the first audible sample can play; pauses the PCM "
                "clock when a later HTTP chunk arrives after the buffer drains"
            ),
            "audio_contract": "little-endian signed 16-bit mono PCM",
            "sample_rate_hz": args.sample_rate,
            "audible_rms_threshold": AUDIBLE_RMS_THRESHOLD,
            "audible_frame_seconds": AUDIBLE_FRAME_SECONDS,
            "audible_hop_seconds": AUDIBLE_HOP_SECONDS,
            "metric_reference": METRIC_REFERENCE,
            "fixed_bootstrap_samples": QWEN_FIXED_BOOTSTRAP_SAMPLES,
            "suppression_guard": ("first-frame centered RMS must not exceed audible_rms_threshold"),
            "network_scope": "client-observed; includes network and HTTP overhead",
            "warning": "Component latency smoke only; no listening or intelligibility score.",
        },
        "measurements": measurements,
        "resident_summary_excluding_first_request": {
            "sample_count": len(warm_measurements),
            "coval_ttfa_p50_ms": _percentile(coval_ttfa_values, 50),
            "coval_ttfa_p95_ms": _percentile(coval_ttfa_values, 95),
            "playback_ttfa_p50_ms": _percentile(playback_ttfa_values, 50),
            "playback_ttfa_p95_ms": _percentile(playback_ttfa_values, 95),
            "pre_audible_buffer_delay_case_count": sum(delay > 1.0 for delay in buffer_delays),
            "pre_audible_buffer_delay_p95_ms": _percentile(buffer_delays, 95),
            "real_time_factor_p50": _percentile(rtf_values, 50),
            "real_time_factor_p05": _percentile(rtf_values, 5),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
