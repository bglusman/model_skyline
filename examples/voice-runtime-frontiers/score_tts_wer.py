#!/usr/bin/env python3
"""Score retained TTS WAV files with one pinned local ASR instrument.

This is an intelligibility proxy, not a naturalness or speaker-similarity
score.  It applies CoVAL normalization version 2 and corpus-level WER, while
recording that the transcription instrument is local Whisper rather than
CoVAL's hosted ``whisper-1`` service.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import platform
import re
import time
import wave
from pathlib import Path
from typing import Any

import jiwer
import mlx.core as mx
from huggingface_hub import snapshot_download
from mlx_audio.stt.utils import load_model
from whisper_normalizer.english import EnglishTextNormalizer

NORM_VERSION = "2"
METHODOLOGY_REFERENCE = (
    "https://github.com/coval-ai/benchmarks/blob/"
    "c9786d181776ba393e85718e26e6b9f67fe19f7c/"
    "runner/src/coval_bench/metrics/wer.py"
)


def _case_wav_path(directory: Path, sequence: int, testcase_id: object) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(testcase_id)).strip("-.")
    return directory / f"{sequence:03d}-{safe_id or 'case'}.wav"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        if rate <= 0:
            raise ValueError(f"invalid sample rate in {path}")
        return source.getnframes() / rate


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


def _transcribe(model: Any, path: Path, language: str | None) -> str:
    signature = inspect.signature(model.generate)
    kwargs: dict[str, Any] = {}
    if language is not None and "language" in signature.parameters:
        kwargs["language"] = language
    if "verbose" in signature.parameters:
        kwargs["verbose"] = False
    return _extract_text(model.generate(str(path), **kwargs))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--prompt-manifest", type=Path, required=True)
    parser.add_argument(
        "--asr-model",
        default="mlx-community/whisper-large-v3-turbo-asr-fp16",
    )
    parser.add_argument("--asr-revision")
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--latency-capture",
        type=Path,
        help="Capture created with the same synthesis run, retained by SHA-256.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    manifest_bytes = args.prompt_manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), list):
        raise SystemExit("--prompt-manifest must contain an object with an items array")

    prompts: list[tuple[str, str, Path]] = []
    for index, item in enumerate(manifest["items"], start=2):
        if not isinstance(item, dict):
            raise SystemExit("every prompt-manifest item must be an object")
        testcase_id = item.get("testcase_id")
        transcript = item.get("transcript")
        if not isinstance(testcase_id, str) or not isinstance(transcript, str):
            raise SystemExit(
                "every prompt-manifest item needs string testcase_id and transcript"
            )
        audio_path = _case_wav_path(args.audio_dir, index, testcase_id)
        if not audio_path.is_file():
            raise SystemExit(f"missing synthesized audio: {audio_path}")
        prompts.append((testcase_id, transcript, audio_path))
    if not prompts:
        raise SystemExit("--prompt-manifest must contain at least one item")

    snapshot_path = Path(
        snapshot_download(repo_id=args.asr_model, revision=args.asr_revision)
    ).resolve()
    resolved_revision = snapshot_path.name
    load_started = time.monotonic()
    model = load_model(str(snapshot_path))
    model_load_seconds = time.monotonic() - load_started

    # Pay compilation and first-use costs outside the scored pass.
    warm_started = time.monotonic()
    _transcribe(model, prompts[0][2], args.language)
    instrument_warm_seconds = time.monotonic() - warm_started

    normalizer = EnglishTextNormalizer()
    measurements: list[dict[str, Any]] = []
    audio_set_digest = hashlib.sha256()
    total_substitutions = 0
    total_deletions = 0
    total_insertions = 0
    total_reference_words = 0
    mx.reset_peak_memory()

    for testcase_id, reference, audio_path in prompts:
        audio_sha256 = _sha256(audio_path)
        audio_set_digest.update(testcase_id.encode())
        audio_set_digest.update(bytes.fromhex(audio_sha256))
        started = time.monotonic()
        hypothesis = _transcribe(model, audio_path, args.language)
        wall_seconds = time.monotonic() - started
        normalized_reference = str(normalizer(reference))
        normalized_hypothesis = str(normalizer(hypothesis))
        aligned = jiwer.process_words(normalized_reference, normalized_hypothesis)
        total_substitutions += int(aligned.substitutions)
        total_deletions += int(aligned.deletions)
        total_insertions += int(aligned.insertions)
        total_reference_words += len(normalized_reference.split())
        measurements.append(
            {
                "testcase_id": testcase_id,
                "audio_filename": audio_path.name,
                "audio_sha256": audio_sha256,
                "audio_seconds": round(_wav_duration_seconds(audio_path), 6),
                "transcription_wall_seconds": round(wall_seconds, 6),
                "reference": reference,
                "hypothesis": hypothesis,
                "normalized_reference": normalized_reference,
                "normalized_hypothesis": normalized_hypothesis,
                "wer_percentage": round(float(aligned.wer) * 100.0, 6),
                "substitutions": int(aligned.substitutions),
                "deletions": int(aligned.deletions),
                "insertions": int(aligned.insertions),
                "reference_words": len(normalized_reference.split()),
            }
        )

    total_errors = total_substitutions + total_deletions + total_insertions
    corpus_wer = total_errors / total_reference_words if total_reference_words else 0.0
    payload = {
        "schema": "model-skyline/experimental-tts-wer-capture/v1alpha1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "instrument": {
            "model": args.asr_model,
            "resolved_revision": resolved_revision,
            "runtime": f"mlx-audio {importlib.metadata.version('mlx-audio')}",
            "mlx": importlib.metadata.version("mlx"),
            "jiwer": importlib.metadata.version("jiwer"),
            "whisper_normalizer": importlib.metadata.version("whisper-normalizer"),
            "hardware": platform.processor() or platform.machine(),
            "os": platform.platform(),
            "language": args.language,
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
            "metric": "corpus word error rate",
            "normalization": "whisper_normalizer EnglishTextNormalizer",
            "normalization_version": NORM_VERSION,
            "reference": METHODOLOGY_REFERENCE,
            "disclosure": (
                "Local MLX Whisper is the fixed measurement instrument. This is not "
                "numerically interchangeable with CoVAL's hosted whisper-1 instrument."
            ),
            "scope": "intelligibility proxy only; not naturalness or speaker similarity",
        },
        "model_load_seconds": round(model_load_seconds, 6),
        "instrument_warm_seconds": round(instrument_warm_seconds, 6),
        "measurements": measurements,
        "summary": {
            "sample_count": len(measurements),
            "corpus_wer_percentage": round(corpus_wer * 100.0, 6),
            "total_reference_words": total_reference_words,
            "total_substitutions": total_substitutions,
            "total_deletions": total_deletions,
            "total_insertions": total_insertions,
            "total_errors": total_errors,
            "empty_hypothesis_count": sum(
                not item["normalized_hypothesis"] for item in measurements
            ),
            "mlx_peak_memory_gb": round(float(mx.get_peak_memory()) / 1e9, 6),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
