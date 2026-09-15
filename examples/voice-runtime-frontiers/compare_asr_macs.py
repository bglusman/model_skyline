#!/usr/bin/env python3
"""Compare exact paired ASR offerings across two Apple Silicon machines."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ratio(numerator: Any, denominator: Any) -> str:
    return format(Decimal(str(numerator)) / Decimal(str(denominator)), ".6f")


def _delta(left: Any, right: Any) -> str:
    return format(Decimal(str(left)) - Decimal(str(right)), ".6f")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m1", type=Path, nargs="+", required=True)
    parser.add_argument("--m5", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if len(args.m1) != len(args.m5):
        raise SystemExit("--m1 and --m5 must contain the same number of captures")
    m1_by_model = {
        payload["offering"]["model"]: (path, payload)
        for path in args.m1
        for payload in [_load(path)]
    }
    m5_by_model = {
        payload["offering"]["model"]: (path, payload)
        for path in args.m5
        for payload in [_load(path)]
    }
    if set(m1_by_model) != set(m5_by_model):
        raise SystemExit("the two capture sets do not contain the same models")

    pairs: list[dict[str, Any]] = []
    for model in sorted(m1_by_model):
        m1_path, m1 = m1_by_model[model]
        m5_path, m5 = m5_by_model[model]
        if m1["offering"]["hardware"] != "Apple M1 Max":
            raise SystemExit(f"unexpected M1 hardware in {m1_path}")
        if m5["offering"]["hardware"] != "Apple M5 Max":
            raise SystemExit(f"unexpected M5 hardware in {m5_path}")
        identity_fields = (
            "model",
            "requested_revision",
            "resolved_revision",
            "runtime",
            "mlx_audio",
            "mlx",
            "requested_language",
            "language_argument",
            "max_tokens",
        )
        for field in identity_fields:
            if m1["offering"].get(field) != m5["offering"].get(field):
                raise SystemExit(f"{model} differs across machines on {field}")
        for field in ("manifest_sha256", "audio_set_sha256", "normalization"):
            if m1["workload"][field] != m5["workload"][field]:
                raise SystemExit(f"{model} workload differs across machines on {field}")
        m1_cases = {item["testcase_id"]: item for item in m1["measurements"]}
        m5_cases = {item["testcase_id"]: item for item in m5["measurements"]}
        if set(m1_cases) != set(m5_cases):
            raise SystemExit(f"{model} does not contain the same case IDs")
        transcript_differences = [
            {
                "testcase_id": testcase_id,
                "m1_normalized_hypothesis": m1_cases[testcase_id]["normalized_hypothesis"],
                "m5_normalized_hypothesis": m5_cases[testcase_id]["normalized_hypothesis"],
            }
            for testcase_id in sorted(m1_cases)
            if m1_cases[testcase_id]["normalized_hypothesis"]
            != m5_cases[testcase_id]["normalized_hypothesis"]
        ]
        s1, s5 = m1["summary"], m5["summary"]
        pairs.append(
            {
                "model": model,
                "artifact_revision": m1["offering"]["resolved_revision"],
                "runtime": m1["offering"]["runtime"],
                "m1_capture": str(m1_path),
                "m1_capture_sha256": _sha256(m1_path),
                "m5_capture": str(m5_path),
                "m5_capture_sha256": _sha256(m5_path),
                "point": {
                    "m1_corpus_wer_percentage": s1["corpus_wer_percentage"],
                    "m5_corpus_wer_percentage": s5["corpus_wer_percentage"],
                    "m5_minus_m1_wer_percentage_points": _delta(
                        s5["corpus_wer_percentage"], s1["corpus_wer_percentage"]
                    ),
                    "m5_speedup_final_latency_p50": _ratio(
                        s1["final_latency_p50_ms"], s5["final_latency_p50_ms"]
                    ),
                    "m5_speedup_final_latency_p95": _ratio(
                        s1["final_latency_p95_ms"], s5["final_latency_p95_ms"]
                    ),
                    "m5_speedup_corpus_realtime_factor": _ratio(
                        s5["real_time_factor_corpus"], s1["real_time_factor_corpus"]
                    ),
                    "m5_to_m1_process_rss_peak_ratio": _ratio(
                        s5["process_rss_mb_max"], s1["process_rss_mb_max"]
                    ),
                },
                "normalized_transcript_difference_count": len(transcript_differences),
                "normalized_transcript_differences": transcript_differences,
            }
        )

    payload = {
        "schema": "model-skyline/experimental-local-asr-hardware-comparison/v1alpha1",
        "methodology": {
            "control": (
                "same artifact revision, runtime versions, settings, normalized "
                "audio hashes, manifest order, and batch size; hardware and OS differ"
            ),
            "speedup": (
                "M1 latency divided by M5 latency, or M5 realtime factor divided "
                "by M1 realtime factor; values above one favor M5"
            ),
            "quality_warning": (
                "One-word transcript differences can arise from non-bitwise-identical "
                "Metal execution. The 24-case quality intervals, not these tiny point "
                "differences, determine whether quality is statistically resolved."
            ),
        },
        "pairs": pairs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps([{"model": pair["model"], **pair["point"]} for pair in pairs], indent=2))


if __name__ == "__main__":
    main()
