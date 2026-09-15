#!/usr/bin/env python3
"""Pool matched TTS seed runs into one auditable observation per offering.

The input panel names every retained latency, WER, pacing, and completion
capture.  This script verifies their content hashes and cross-links, then
pools utterance-level measurements.  It never averages already-aggregated
medians or WER percentages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any

SCHEMA = "model-skyline/experimental-tts-seed-panel/v1alpha1"
MANIFEST_SHA256 = "e30909112f5fd886157008ca960c00d4f68f29de69b29d5913a5bf3383ad71ec"
WHISPER_REVISION = "624c19c9af5603fa73b83bce14d4aeea96156d18"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile of an empty collection")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _without_seed(offering: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in offering.items() if key != "seed"}


def _measurements(payload: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    values = payload.get("measurements")
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise ValueError(f"{path.name} needs an array of measurement objects")
    return values


def _by_testcase(payload: dict[str, Any], path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in _measurements(payload, path):
        testcase_id = item.get("testcase_id")
        if not isinstance(testcase_id, str) or testcase_id in result:
            raise ValueError(f"{path.name} has an invalid or duplicate testcase_id")
        result[testcase_id] = item
    return result


def _latency_key(measurements: list[dict[str, Any]], path: Path) -> str:
    for candidate in ("playback_ttfa_ms", "perceived_ttfa_ms"):
        if all(item.get(candidate) is not None for item in measurements):
            return candidate
    raise ValueError(f"{path.name} has no supported audible-latency measurement")


def _bootstrap_wer_interval(
    quality_runs: list[list[dict[str, Any]]],
    *,
    iterations: int,
    random_seed: int,
) -> tuple[float, float]:
    """Two-stage bootstrap: resample seeds, then prompts inside each seed."""
    if len(quality_runs) < 2:
        raise ValueError("a seed-panel interval needs at least two seeds")
    rng = random.Random(random_seed)
    estimates: list[float] = []
    for _ in range(iterations):
        errors = 0
        reference_words = 0
        for _selected_seed in range(len(quality_runs)):
            run = quality_runs[rng.randrange(len(quality_runs))]
            for _selected_prompt in range(len(run)):
                item = run[rng.randrange(len(run))]
                errors += int(item["substitutions"])
                errors += int(item["deletions"])
                errors += int(item["insertions"])
                reference_words += int(item["reference_words"])
        estimates.append(errors / reference_words * 100.0)
    return _percentile(estimates, 2.5), _percentile(estimates, 97.5)


def _validate_run(
    *,
    root: Path,
    expected_seed: int,
    run: dict[str, Any],
) -> dict[str, Any]:
    paths = {kind: root / str(run[kind]) for kind in ("latency", "quality", "pacing", "completion")}
    payloads = {kind: _load(path) for kind, path in paths.items()}
    digests = {kind: _sha256(path) for kind, path in paths.items()}
    latency = payloads["latency"]
    quality = payloads["quality"]
    pacing = payloads["pacing"]
    completion = payloads["completion"]

    offering = latency.get("offering")
    if not isinstance(offering, dict) or offering.get("seed") != expected_seed:
        raise ValueError(f"{paths['latency'].name} does not declare seed {expected_seed}")
    if latency.get("workload", {}).get("manifest_sha256") != MANIFEST_SHA256:
        raise ValueError(f"unexpected prompt manifest in {paths['latency'].name}")
    if quality.get("source", {}).get("manifest_sha256") != MANIFEST_SHA256:
        raise ValueError(f"unexpected prompt manifest in {paths['quality'].name}")
    if pacing.get("source", {}).get("manifest_sha256") != MANIFEST_SHA256:
        raise ValueError(f"unexpected prompt manifest in {paths['pacing'].name}")
    if quality.get("source", {}).get("latency_capture_sha256") != digests["latency"]:
        raise ValueError(f"{paths['quality'].name} does not bind its latency capture")
    if pacing.get("source", {}).get("audio_set_sha256") != quality.get("source", {}).get(
        "audio_set_sha256"
    ):
        raise ValueError(
            f"{paths['pacing'].name} and {paths['quality'].name} score different audio"
        )
    if completion.get("source", {}).get("wer_capture_sha256") != digests["quality"]:
        raise ValueError(f"{paths['completion'].name} does not bind its WER capture")
    if quality.get("instrument", {}).get("resolved_revision") != WHISPER_REVISION:
        raise ValueError(f"unexpected Whisper revision in {paths['quality'].name}")

    indexed = {kind: _by_testcase(payload, paths[kind]) for kind, payload in payloads.items()}
    # Latency captures retain a warmup; the three scorers do not.
    latency_scored = {
        key: item
        for key, item in indexed["latency"].items()
        if item.get("included_in_summary") is True
    }
    expected_ids = set(indexed["quality"])
    if (
        len(expected_ids) != 30
        or set(latency_scored) != expected_ids
        or set(indexed["pacing"]) != expected_ids
        or set(indexed["completion"]) != expected_ids
    ):
        raise ValueError(f"seed {expected_seed} does not contain the same 30 scored prompts")
    for testcase_id in expected_ids:
        audio_sha256 = indexed["quality"][testcase_id].get("audio_sha256")
        if (
            not isinstance(audio_sha256, str)
            or indexed["pacing"][testcase_id].get("audio_sha256") != audio_sha256
            or indexed["completion"][testcase_id].get("audio_sha256") != audio_sha256
        ):
            raise ValueError(f"seed {expected_seed} scorers disagree on audio for {testcase_id}")
    if quality.get("summary", {}).get("sample_count") != 30:
        raise ValueError(f"{paths['quality'].name} does not summarize 30 prompts")

    ordered_ids = sorted(expected_ids)
    latency_rows = [latency_scored[testcase_id] for testcase_id in ordered_ids]
    quality_rows = [indexed["quality"][testcase_id] for testcase_id in ordered_ids]
    pacing_rows = [indexed["pacing"][testcase_id] for testcase_id in ordered_ids]
    completion_rows = [indexed["completion"][testcase_id] for testcase_id in ordered_ids]
    key = _latency_key(latency_rows, paths["latency"])
    invalid_ids = {
        testcase_id
        for testcase_id in ordered_ids
        if latency_scored[testcase_id].get("suspect") is True
        or latency_scored[testcase_id].get("hit_token_cap") is True
        or not str(indexed["quality"][testcase_id].get("normalized_hypothesis", ""))
        or indexed["completion"][testcase_id].get("diagnostic_warning") is True
    }
    return {
        "seed": expected_seed,
        "identity": _without_seed(offering),
        "latency_key": key,
        "latency_rows": latency_rows,
        "quality_rows": quality_rows,
        "pacing_rows": pacing_rows,
        "completion_rows": completion_rows,
        "invalid_ids": sorted(invalid_ids),
        "sources": {
            kind: {
                "path": str(run[kind]),
                "sha256": digests[kind],
                "captured_at": payloads[kind].get("captured_at"),
            }
            for kind in ("latency", "quality", "pacing", "completion")
        },
        "audio_set_sha256": quality["source"]["audio_set_sha256"],
    }


def _aggregate_offering(
    *,
    root: Path,
    spec: dict[str, Any],
    expected_seeds: list[int],
    iterations: int,
    random_seed: int,
) -> dict[str, Any]:
    raw_runs = spec.get("runs")
    if not isinstance(raw_runs, list):
        raise ValueError(f"{spec.get('slug', 'offering')} needs a runs array")
    by_seed = {int(run["seed"]): run for run in raw_runs}
    if sorted(by_seed) != expected_seeds or len(by_seed) != len(raw_runs):
        raise ValueError(f"{spec.get('slug', 'offering')} does not match expected seeds")
    runs = [
        _validate_run(root=root, expected_seed=seed, run=by_seed[seed]) for seed in expected_seeds
    ]
    identity = runs[0]["identity"]
    if any(run["identity"] != identity for run in runs[1:]):
        raise ValueError(f"{spec.get('slug', 'offering')} changed identity between seeds")
    latency_key = runs[0]["latency_key"]
    if any(run["latency_key"] != latency_key for run in runs[1:]):
        raise ValueError(f"{spec.get('slug', 'offering')} changed latency metric between seeds")

    latency_rows = [item for run in runs for item in run["latency_rows"]]
    quality_rows = [item for run in runs for item in run["quality_rows"]]
    pacing_rows = [item for run in runs for item in run["pacing_rows"]]
    latencies = [float(item[latency_key]) for item in latency_rows]
    realtime_factors = [float(item["real_time_factor"]) for item in latency_rows]
    words_per_minute = [float(item["words_per_minute"]) for item in pacing_rows]
    pause_fractions = [float(item["internal_pause_fraction_of_span"]) for item in pacing_rows]
    total_errors = sum(
        int(item["substitutions"]) + int(item["deletions"]) + int(item["insertions"])
        for item in quality_rows
    )
    total_reference_words = sum(int(item["reference_words"]) for item in quality_rows)
    total_pacing_words = sum(int(item["normalized_word_count"]) for item in pacing_rows)
    total_audible_span = sum(float(item["audible_span_seconds"]) for item in pacing_rows)
    lower, upper = _bootstrap_wer_interval(
        [run["quality_rows"] for run in runs],
        iterations=iterations,
        random_seed=random_seed,
    )
    invalid_cases = [
        {"seed": run["seed"], "testcase_id": testcase_id}
        for run in runs
        for testcase_id in run["invalid_ids"]
    ]
    return {
        "slug": spec["slug"],
        "offering_id": spec["offering_id"],
        "offering_identity_without_seed": identity,
        "source_runs": [
            {
                "seed": run["seed"],
                "audio_set_sha256": run["audio_set_sha256"],
                "sources": run["sources"],
            }
            for run in runs
        ],
        "summary": {
            "seed_count": len(runs),
            "sample_count": len(latency_rows),
            "corpus_wer_percentage": round(total_errors / total_reference_words * 100.0, 6),
            "corpus_wer_percentage_lower": round(lower, 6),
            "corpus_wer_percentage_upper": round(upper, 6),
            "total_errors": total_errors,
            "total_reference_words": total_reference_words,
            "playback_ttfa_p50_ms": round(_percentile(latencies, 50), 3),
            "playback_ttfa_p95_ms": round(_percentile(latencies, 95), 3),
            "real_time_factor_p50": round(_percentile(realtime_factors, 50), 3),
            "invalid_case_count": len(invalid_cases),
            "invalid_case_percent": round(len(invalid_cases) / len(latency_rows) * 100.0, 6),
            "corpus_words_per_minute": round(total_pacing_words / total_audible_span * 60.0, 6),
            "words_per_minute_p50": round(_percentile(words_per_minute, 50), 6),
            "words_per_minute_p95": round(_percentile(words_per_minute, 95), 6),
            "internal_pause_fraction_p50": round(_percentile(pause_fractions, 50), 6),
        },
        "invalid_cases": invalid_cases,
        "per_seed": [
            {
                "seed": run["seed"],
                "corpus_wer_percentage": round(
                    sum(
                        int(item["substitutions"])
                        + int(item["deletions"])
                        + int(item["insertions"])
                        for item in run["quality_rows"]
                    )
                    / sum(int(item["reference_words"]) for item in run["quality_rows"])
                    * 100.0,
                    6,
                ),
                "invalid_case_count": len(run["invalid_ids"]),
            }
            for run in runs
        ],
    }


def _render(panel_path: Path) -> str:
    panel = _load(panel_path)
    root = panel_path.parent
    expected_seeds = sorted(int(seed) for seed in panel.get("seeds", []))
    if len(expected_seeds) < 2 or len(expected_seeds) != len(set(expected_seeds)):
        raise ValueError("panel seeds must contain at least two distinct integers")
    bootstrap = panel.get("bootstrap", {})
    iterations = int(bootstrap.get("iterations", 10_000))
    random_seed = int(bootstrap.get("random_seed", 0))
    if iterations < 1:
        raise ValueError("bootstrap iterations must be positive")
    offerings = panel.get("offerings")
    if not isinstance(offerings, list) or not offerings:
        raise ValueError("panel needs a non-empty offerings array")
    payload = {
        "schema": SCHEMA,
        "panel": {
            "id": panel["id"],
            "version": panel["version"],
            "seeds": expected_seeds,
            "prompts_per_seed": 30,
            "samples_per_offering": 30 * len(expected_seeds),
            "prompt_manifest_sha256": MANIFEST_SHA256,
            "quality_instrument_revision": WHISPER_REVISION,
        },
        "methodology": {
            "pooling": "utterance measurements pooled across matched seeds",
            "warning": "No metric is calculated by averaging per-seed medians or percentages.",
            "wer": "total word errors divided by total normalized reference words",
            "wer_interval": (
                "descriptive two-stage 95% bootstrap interval: resample seeds, then "
                "prompts within each selected seed"
            ),
            "bootstrap_iterations": iterations,
            "bootstrap_random_seed": random_seed,
            "uncertainty_scope": (
                "Only three synthesis seeds are represented; the interval is provisional."
            ),
        },
        "offerings": [
            _aggregate_offering(
                root=root,
                spec=spec,
                expected_seeds=expected_seeds,
                iterations=iterations,
                random_seed=random_seed,
            )
            for spec in offerings
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = _render(args.panel)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated seed panel: {args.output}")
        print(f"valid generated seed panel: {args.output}")
        return
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
