#!/usr/bin/env python3
"""Bootstrap paired uncertainty for comparable local ASR captures.

Cases are resampled with replacement within each source domain, preserving the
pairing between model offerings. This quantifies pilot uncertainty without
pretending repeated words inside one utterance are independent samples.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(values: np.ndarray, percentile: float) -> float:
    return round(float(np.percentile(values, percentile)), 6)


def _offering_id(capture: dict[str, Any]) -> str:
    offering = capture["offering"]
    return f"{offering['model']}|{offering['runtime']}|{offering['hardware']}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", type=Path, nargs="+")
    parser.add_argument("--resamples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260914)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if len(args.captures) < 2:
        raise SystemExit("provide at least two captures")
    if args.resamples <= 0:
        raise SystemExit("--resamples must be positive")
    loaded = [(path, json.loads(path.read_bytes())) for path in args.captures]
    expected_workload = loaded[0][1]["workload"]
    expected_cases = [item["testcase_id"] for item in loaded[0][1]["measurements"]]
    for path, capture in loaded:
        if capture.get("schema") != "model-skyline/experimental-local-asr-capture/v1alpha1":
            raise SystemExit(f"unsupported capture schema: {path}")
        workload = capture.get("workload", {})
        if (
            workload.get("manifest_sha256") != expected_workload["manifest_sha256"]
            or workload.get("audio_set_sha256") != expected_workload["audio_set_sha256"]
        ):
            raise SystemExit(f"capture workload mismatch: {path}")
        cases = [item["testcase_id"] for item in capture["measurements"]]
        if cases != expected_cases:
            raise SystemExit(f"capture case order mismatch: {path}")

    offering_ids = [_offering_id(capture) for _, capture in loaded]
    if len(set(offering_ids)) != len(offering_ids):
        raise SystemExit("capture offering identities are not unique")
    domains: defaultdict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(loaded[0][1]["measurements"]):
        domains[str(item["domain"])].append(index)
    rng = np.random.default_rng(args.seed)
    bootstrap_indices = np.empty(
        (args.resamples, len(expected_cases)),
        dtype=np.int64,
    )
    cursor = 0
    for domain in sorted(domains):
        indices = np.asarray(domains[domain], dtype=np.int64)
        width = len(indices)
        bootstrap_indices[:, cursor : cursor + width] = rng.choice(
            indices,
            size=(args.resamples, width),
            replace=True,
        )
        cursor += width

    bootstrap: dict[str, dict[str, np.ndarray]] = {}
    offerings: dict[str, Any] = {}
    for (path, capture), offering_id in zip(loaded, offering_ids, strict=True):
        measurements = capture["measurements"]
        errors = np.asarray([item["errors"] for item in measurements], dtype=np.float64)
        words = np.asarray(
            [item["reference_words"] for item in measurements],
            dtype=np.float64,
        )
        latencies = np.asarray(
            [item["final_latency_ms"] for item in measurements],
            dtype=np.float64,
        )
        sampled_errors = errors[bootstrap_indices].sum(axis=1)
        sampled_words = words[bootstrap_indices].sum(axis=1)
        sampled_wer = sampled_errors / sampled_words * 100.0
        sampled_p50 = np.percentile(latencies[bootstrap_indices], 50, axis=1)
        sampled_p95 = np.percentile(latencies[bootstrap_indices], 95, axis=1)
        bootstrap[offering_id] = {
            "wer": sampled_wer,
            "p50": sampled_p50,
            "p95": sampled_p95,
        }
        offerings[offering_id] = {
            "capture": str(path),
            "capture_sha256": _sha256(path),
            "model": capture["offering"]["model"],
            "runtime": capture["offering"]["runtime"],
            "hardware": capture["offering"]["hardware"],
            "point": {
                "corpus_wer_percentage": capture["summary"]["corpus_wer_percentage"],
                "final_latency_p50_ms": capture["summary"]["final_latency_p50_ms"],
                "final_latency_p95_ms": capture["summary"]["final_latency_p95_ms"],
            },
            "interval_95": {
                "corpus_wer_percentage": [
                    _percentile(sampled_wer, 2.5),
                    _percentile(sampled_wer, 97.5),
                ],
                "final_latency_p50_ms": [
                    _percentile(sampled_p50, 2.5),
                    _percentile(sampled_p50, 97.5),
                ],
                "final_latency_p95_ms": [
                    _percentile(sampled_p95, 2.5),
                    _percentile(sampled_p95, 97.5),
                ],
            },
        }

    comparisons: list[dict[str, Any]] = []
    for left, right in itertools.combinations(offering_ids, 2):
        differences = {
            metric: bootstrap[left][metric] - bootstrap[right][metric]
            for metric in ("wer", "p50", "p95")
        }
        comparisons.append(
            {
                "left": left,
                "right": right,
                "definition": "left minus right; negative favors left",
                "interval_95": {
                    "corpus_wer_percentage_points": [
                        _percentile(differences["wer"], 2.5),
                        _percentile(differences["wer"], 97.5),
                    ],
                    "final_latency_p50_ms": [
                        _percentile(differences["p50"], 2.5),
                        _percentile(differences["p50"], 97.5),
                    ],
                    "final_latency_p95_ms": [
                        _percentile(differences["p95"], 2.5),
                        _percentile(differences["p95"], 97.5),
                    ],
                },
            }
        )

    payload = {
        "schema": "model-skyline/experimental-local-asr-bootstrap/v1alpha1",
        "workload": {
            "manifest_sha256": expected_workload["manifest_sha256"],
            "audio_set_sha256": expected_workload["audio_set_sha256"],
            "sample_count": len(expected_cases),
            "domains": {domain: len(indices) for domain, indices in sorted(domains.items())},
        },
        "methodology": {
            "resamples": args.resamples,
            "seed": args.seed,
            "unit": "utterance, sampled with replacement within each source domain",
            "pairing": "the same sampled case indices are used for every offering",
            "warning": (
                "Intervals describe this small frozen panel. They do not account for "
                "dataset selection, runtime-version, or hardware variation."
            ),
        },
        "offerings": offerings,
        "paired_comparisons": comparisons,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value["interval_95"] for key, value in offerings.items()}, indent=2))


if __name__ == "__main__":
    main()
