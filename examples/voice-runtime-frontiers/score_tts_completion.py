#!/usr/bin/env python3
"""Find obvious completion and repetition anomalies in a retained TTS WER capture.

This scorer never plays audio and does not run another model. It derives
auditable warning signals from the exact reference and ASR hypothesis already
retained by ``score_tts_wer.py``. The signals are diagnostic until calibrated;
they are not naturalness scores or frontier admission gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"\w+(?:['’-]\w+)*|[^\w\s]+", re.UNICODE)
LEXICAL_RE = re.compile(r"\w", re.UNICODE)
TERMINAL_REPEAT_WARNING_THRESHOLD = 4
LEXICAL_REPEAT_WARNING_THRESHOLD = 3
MAX_NGRAM_WIDTH = 8


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tokens(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_RE.findall(text)]


def _terminal_repeat(tokens: list[str]) -> tuple[str | None, int]:
    if not tokens:
        return None, 0
    terminal = tokens[-1]
    count = 1
    for token in reversed(tokens[:-1]):
        if token != terminal:
            break
        count += 1
    return terminal, count


def _max_consecutive_lexical_ngram_repeat(
    tokens: list[str],
) -> tuple[list[str], int]:
    lexical = [token for token in tokens if LEXICAL_RE.search(token)]
    best_ngram: list[str] = []
    best_count = 1 if lexical else 0
    for width in range(1, min(MAX_NGRAM_WIDTH, len(lexical)) + 1):
        for start in range(0, len(lexical) - width + 1):
            ngram = lexical[start : start + width]
            count = 1
            cursor = start + width
            while lexical[cursor : cursor + width] == ngram:
                count += 1
                cursor += width
            if count >= 2 and (
                count > best_count
                or (count == best_count and len(ngram) > len(best_ngram))
            ):
                best_ngram = ngram
                best_count = count
    return best_ngram, best_count


def _max_repeat_count_for_ngram(tokens: list[str], ngram: list[str]) -> int:
    if not ngram:
        return 0
    lexical = [token for token in tokens if LEXICAL_RE.search(token)]
    width = len(ngram)
    best_count = 0
    for start in range(0, len(lexical) - width + 1):
        if lexical[start : start + width] != ngram:
            continue
        count = 1
        cursor = start + width
        while lexical[cursor : cursor + width] == ngram:
            count += 1
            cursor += width
        best_count = max(best_count, count)
    return best_count


def _measurement(item: dict[str, Any]) -> dict[str, Any]:
    testcase_id = item.get("testcase_id")
    reference = item.get("reference")
    hypothesis = item.get("hypothesis")
    normalized_reference = item.get("normalized_reference")
    normalized_hypothesis = item.get("normalized_hypothesis")
    if (
        not isinstance(testcase_id, str)
        or not isinstance(reference, str)
        or not isinstance(hypothesis, str)
        or not isinstance(normalized_reference, str)
        or not isinstance(normalized_hypothesis, str)
    ):
        raise ValueError("WER measurements need string text and testcase fields")

    reference_tokens = _tokens(reference)
    hypothesis_tokens = _tokens(hypothesis)
    reference_terminal, reference_terminal_count = _terminal_repeat(reference_tokens)
    hypothesis_terminal, hypothesis_terminal_count = _terminal_repeat(hypothesis_tokens)
    reference_ngram, reference_ngram_count = _max_consecutive_lexical_ngram_repeat(
        reference_tokens
    )
    hypothesis_ngram, hypothesis_ngram_count = _max_consecutive_lexical_ngram_repeat(
        hypothesis_tokens
    )
    reference_hypothesis_ngram_count = _max_repeat_count_for_ngram(
        reference_tokens,
        hypothesis_ngram,
    )
    reference_words = normalized_reference.split()
    hypothesis_words = normalized_hypothesis.split()
    terminal_repeat_excess = max(
        0,
        hypothesis_terminal_count
        - (reference_terminal_count if hypothesis_terminal == reference_terminal else 0),
    )
    lexical_repeat_excess = max(
        0,
        (
            hypothesis_ngram_count - reference_hypothesis_ngram_count
            if hypothesis_ngram_count >= 2
            else 0
        ),
    )
    terminal_warning = (
        hypothesis_terminal_count >= TERMINAL_REPEAT_WARNING_THRESHOLD
        and terminal_repeat_excess > 0
    )
    lexical_warning = (
        hypothesis_ngram_count >= LEXICAL_REPEAT_WARNING_THRESHOLD
        and lexical_repeat_excess > 0
    )
    return {
        "testcase_id": testcase_id,
        "audio_filename": item.get("audio_filename"),
        "audio_sha256": item.get("audio_sha256"),
        "audio_seconds": item.get("audio_seconds"),
        "reference_word_count": len(reference_words),
        "hypothesis_word_count": len(hypothesis_words),
        "hypothesis_to_reference_word_ratio": (
            None
            if not reference_words
            else round(len(hypothesis_words) / len(reference_words), 6)
        ),
        "reference_terminal_token": reference_terminal,
        "reference_terminal_token_repeat_count": reference_terminal_count,
        "hypothesis_terminal_token": hypothesis_terminal,
        "hypothesis_terminal_token_repeat_count": hypothesis_terminal_count,
        "terminal_token_repeat_excess": terminal_repeat_excess,
        "reference_max_repeated_lexical_ngram": reference_ngram,
        "reference_max_consecutive_lexical_ngram_repeats": reference_ngram_count,
        "reference_repeat_count_for_hypothesis_ngram": (
            reference_hypothesis_ngram_count
        ),
        "hypothesis_max_repeated_lexical_ngram": hypothesis_ngram,
        "hypothesis_max_consecutive_lexical_ngram_repeats": hypothesis_ngram_count,
        "lexical_ngram_repeat_excess": lexical_repeat_excess,
        "asr_terminal_repetition_warning": terminal_warning,
        "asr_lexical_loop_warning": lexical_warning,
        "diagnostic_warning": terminal_warning or lexical_warning,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wer-capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    wer_capture = json.loads(args.wer_capture.read_bytes())
    if wer_capture.get("schema") != "model-skyline/experimental-tts-wer-capture/v1alpha1":
        raise SystemExit("--wer-capture has an unsupported schema")
    source = wer_capture.get("source")
    raw_measurements = wer_capture.get("measurements")
    if not isinstance(source, dict) or not isinstance(raw_measurements, list):
        raise SystemExit("--wer-capture is missing source or measurements")
    try:
        measurements = [_measurement(item) for item in raw_measurements]
    except (AttributeError, TypeError, ValueError) as error:
        raise SystemExit(f"invalid WER measurement: {error}") from error

    payload = {
        "schema": "model-skyline/experimental-tts-completion-capture/v1alpha1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "manifest_id": source.get("manifest_id"),
            "manifest_version": source.get("manifest_version"),
            "manifest_sha256": source.get("manifest_sha256"),
            "audio_set_sha256": source.get("audio_set_sha256"),
            "wer_capture_sha256": _sha256(args.wer_capture),
        },
        "methodology": {
            "tokenization": (
                "case-folded Unicode word tokens and contiguous non-word, "
                "non-whitespace tokens"
            ),
            "terminal_repetition_warning": (
                "ASR hypothesis ends in at least four identical tokens and repeats "
                "that token more often than the reference"
            ),
            "lexical_loop_warning": (
                "ASR hypothesis contains at least three adjacent repetitions of a "
                "one-to-eight-word sequence and exceeds the reference repeat count"
            ),
            "warning": (
                "ASR-derived deterministic diagnostics only. They can find obvious "
                "loops but cannot prove acoustic completion or naturalness, and no "
                "frontier gate is applied yet."
            ),
        },
        "measurements": measurements,
        "summary": {
            "sample_count": len(measurements),
            "diagnostic_warning_count": sum(
                bool(item["diagnostic_warning"]) for item in measurements
            ),
            "terminal_repetition_warning_count": sum(
                bool(item["asr_terminal_repetition_warning"])
                for item in measurements
            ),
            "lexical_loop_warning_count": sum(
                bool(item["asr_lexical_loop_warning"]) for item in measurements
            ),
            "hypothesis_to_reference_word_ratio_min": min(
                (
                    float(item["hypothesis_to_reference_word_ratio"])
                    for item in measurements
                    if item["hypothesis_to_reference_word_ratio"] is not None
                ),
                default=None,
            ),
            "hypothesis_to_reference_word_ratio_max": max(
                (
                    float(item["hypothesis_to_reference_word_ratio"])
                    for item in measurements
                    if item["hypothesis_to_reference_word_ratio"] is not None
                ),
                default=None,
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
