#!/usr/bin/env python3
"""Compare two TTS offerings on the same prompt-by-seed cells.

The aggregate frontier points answer which offerings are non-dominated.  This
companion report answers how large the measured difference between two
frontier residents is.  It verifies the aggregate result and every raw capture
before calculating candidate-minus-reference deltas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from aggregate_tts_seed_panel import _load, _percentile, _render, _validate_run

SCHEMA = "model-skyline/experimental-tts-paired-comparison/v1alpha1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validated_runs(
    *, root: Path, spec: dict[str, Any], seeds: list[int]
) -> dict[int, dict[str, Any]]:
    raw_runs = spec.get("runs")
    if not isinstance(raw_runs, list):
        raise ValueError(f"{spec.get('slug', 'offering')} needs a runs array")
    by_seed = {int(run["seed"]): run for run in raw_runs}
    if sorted(by_seed) != seeds or len(by_seed) != len(raw_runs):
        raise ValueError(f"{spec.get('slug', 'offering')} does not match panel seeds")
    validated = {
        seed: _validate_run(root=root, expected_seed=seed, run=by_seed[seed]) for seed in seeds
    }
    identities = [run["identity"] for run in validated.values()]
    if any(identity != identities[0] for identity in identities[1:]):
        raise ValueError(f"{spec.get('slug', 'offering')} changed identity between seeds")
    return validated


def _grid(runs: dict[int, dict[str, Any]], kind: str) -> dict[tuple[int, str], dict[str, Any]]:
    rows: dict[tuple[int, str], dict[str, Any]] = {}
    for seed, run in runs.items():
        for row in run[f"{kind}_rows"]:
            key = (seed, str(row["testcase_id"]))
            if key in rows:
                raise ValueError(f"duplicate comparison cell {key}")
            rows[key] = row
    return rows


def _quality_errors(row: dict[str, Any]) -> int:
    return int(row["substitutions"]) + int(row["deletions"]) + int(row["insertions"])


def _wer(rows: list[dict[str, Any]]) -> float:
    errors = sum(_quality_errors(row) for row in rows)
    reference_words = sum(int(row["reference_words"]) for row in rows)
    return errors / reference_words * 100.0


def _round_interval(values: list[float], digits: int) -> dict[str, float]:
    return {
        "lower": round(_percentile(values, 2.5), digits),
        "upper": round(_percentile(values, 97.5), digits),
    }


def _metric(
    *,
    estimate: float,
    bootstrap_values: list[float],
    digits: int,
    unit: str,
    candidate_better_when: str,
) -> dict[str, Any]:
    if candidate_better_when == "lower":
        better = sum(value < 0 for value in bootstrap_values)
    elif candidate_better_when == "higher":
        better = sum(value > 0 for value in bootstrap_values)
    else:
        raise ValueError(f"unsupported comparison direction {candidate_better_when!r}")
    return {
        "candidate_minus_reference": round(estimate, digits),
        "descriptive_95_percent_interval": _round_interval(bootstrap_values, digits),
        "unit": unit,
        "candidate_better_when": candidate_better_when,
        "bootstrap_fraction_candidate_better": round(better / len(bootstrap_values), 6),
    }


def _comparison(
    *,
    spec: dict[str, Any],
    offerings: dict[str, dict[str, Any]],
    root: Path,
    seeds: list[int],
    iterations: int,
    random_seed: int,
) -> dict[str, Any]:
    candidate_spec = offerings[str(spec["candidate"])]
    reference_spec = offerings[str(spec["reference"])]
    candidate_runs = _validated_runs(root=root, spec=candidate_spec, seeds=seeds)
    reference_runs = _validated_runs(root=root, spec=reference_spec, seeds=seeds)

    candidate_quality = _grid(candidate_runs, "quality")
    reference_quality = _grid(reference_runs, "quality")
    candidate_latency = _grid(candidate_runs, "latency")
    reference_latency = _grid(reference_runs, "latency")
    cell_keys = set(candidate_quality)
    if not cell_keys or any(
        set(grid) != cell_keys for grid in (reference_quality, candidate_latency, reference_latency)
    ):
        raise ValueError(f"{spec['id']} does not have a complete matched cell grid")
    testcase_ids = sorted({testcase_id for _seed, testcase_id in cell_keys})
    expected_cells = {(seed, testcase_id) for seed in seeds for testcase_id in testcase_ids}
    if cell_keys != expected_cells:
        raise ValueError(f"{spec['id']} is not a complete seed-by-prompt crossing")
    for key in sorted(cell_keys):
        if candidate_quality[key]["reference_words"] != reference_quality[key]["reference_words"]:
            raise ValueError(f"{spec['id']} has different reference word counts at {key}")

    candidate_latency_keys = {run["latency_key"] for run in candidate_runs.values()}
    reference_latency_keys = {run["latency_key"] for run in reference_runs.values()}
    if len(candidate_latency_keys) != 1 or candidate_latency_keys != reference_latency_keys:
        raise ValueError(f"{spec['id']} does not use one common audible-latency metric")
    latency_key = candidate_latency_keys.pop()

    ordered_keys = sorted(cell_keys)
    candidate_quality_rows = [candidate_quality[key] for key in ordered_keys]
    reference_quality_rows = [reference_quality[key] for key in ordered_keys]
    candidate_latencies = [float(candidate_latency[key][latency_key]) for key in ordered_keys]
    reference_latencies = [float(reference_latency[key][latency_key]) for key in ordered_keys]
    candidate_rtfs = [float(candidate_latency[key]["real_time_factor"]) for key in ordered_keys]
    reference_rtfs = [float(reference_latency[key]["real_time_factor"]) for key in ordered_keys]

    point = {
        "wer": _wer(candidate_quality_rows) - _wer(reference_quality_rows),
        "ttfa_p50": _percentile(candidate_latencies, 50) - _percentile(reference_latencies, 50),
        "ttfa_p95": _percentile(candidate_latencies, 95) - _percentile(reference_latencies, 95),
        "rtf_p50": _percentile(candidate_rtfs, 50) - _percentile(reference_rtfs, 50),
    }

    rng = random.Random(random_seed)
    bootstraps: dict[str, list[float]] = {key: [] for key in point}
    for _ in range(iterations):
        selected_seeds = [seeds[rng.randrange(len(seeds))] for _ in seeds]
        selected_testcases = [testcase_ids[rng.randrange(len(testcase_ids))] for _ in testcase_ids]
        selected_keys = [
            (seed, testcase_id) for seed in selected_seeds for testcase_id in selected_testcases
        ]
        cand_quality = [candidate_quality[key] for key in selected_keys]
        ref_quality = [reference_quality[key] for key in selected_keys]
        cand_latency = [float(candidate_latency[key][latency_key]) for key in selected_keys]
        ref_latency = [float(reference_latency[key][latency_key]) for key in selected_keys]
        cand_rtfs = [float(candidate_latency[key]["real_time_factor"]) for key in selected_keys]
        ref_rtfs = [float(reference_latency[key]["real_time_factor"]) for key in selected_keys]
        bootstraps["wer"].append(_wer(cand_quality) - _wer(ref_quality))
        bootstraps["ttfa_p50"].append(_percentile(cand_latency, 50) - _percentile(ref_latency, 50))
        bootstraps["ttfa_p95"].append(_percentile(cand_latency, 95) - _percentile(ref_latency, 95))
        bootstraps["rtf_p50"].append(_percentile(cand_rtfs, 50) - _percentile(ref_rtfs, 50))

    error_differences = [
        _quality_errors(candidate_quality[key]) - _quality_errors(reference_quality[key])
        for key in ordered_keys
    ]
    audible_differences = [
        float(candidate_latency[key][latency_key]) - float(reference_latency[key][latency_key])
        for key in ordered_keys
    ]
    return {
        "id": spec["id"],
        "candidate": {
            "slug": candidate_spec["slug"],
            "offering_id": candidate_spec["offering_id"],
        },
        "reference": {
            "slug": reference_spec["slug"],
            "offering_id": reference_spec["offering_id"],
        },
        "matched_cells": len(ordered_keys),
        "metrics": {
            "corpus_wer_percentage_points": _metric(
                estimate=point["wer"],
                bootstrap_values=bootstraps["wer"],
                digits=6,
                unit="percentage points",
                candidate_better_when="lower",
            ),
            "playback_ttfa_p50_ms": _metric(
                estimate=point["ttfa_p50"],
                bootstrap_values=bootstraps["ttfa_p50"],
                digits=3,
                unit="milliseconds",
                candidate_better_when="lower",
            ),
            "playback_ttfa_p95_ms": _metric(
                estimate=point["ttfa_p95"],
                bootstrap_values=bootstraps["ttfa_p95"],
                digits=3,
                unit="milliseconds",
                candidate_better_when="lower",
            ),
            "realtime_factor_p50": _metric(
                estimate=point["rtf_p50"],
                bootstrap_values=bootstraps["rtf_p50"],
                digits=3,
                unit="ratio",
                candidate_better_when="higher",
            ),
        },
        "cell_counts": {
            "candidate_fewer_word_errors": sum(value < 0 for value in error_differences),
            "same_word_errors": sum(value == 0 for value in error_differences),
            "candidate_more_word_errors": sum(value > 0 for value in error_differences),
            "candidate_faster_first_audio": sum(value < 0 for value in audible_differences),
            "same_first_audio": sum(value == 0 for value in audible_differences),
            "candidate_slower_first_audio": sum(value > 0 for value in audible_differences),
        },
    }


def _render_comparisons(panel_path: Path, aggregate_path: Path) -> str:
    panel = _load(panel_path)
    aggregate_rendered = _render(panel_path)
    if (
        not aggregate_path.exists()
        or aggregate_path.read_text(encoding="utf-8") != aggregate_rendered
    ):
        raise ValueError(f"stale or mismatched aggregate result: {aggregate_path}")
    seeds = sorted(int(seed) for seed in panel.get("seeds", []))
    bootstrap = panel.get("bootstrap", {})
    iterations = int(bootstrap.get("iterations", 10_000))
    random_seed = int(bootstrap.get("random_seed", 0))
    offering_specs = panel.get("offerings")
    if not isinstance(offering_specs, list):
        raise ValueError("panel needs an offerings array")
    offerings = {str(spec["slug"]): spec for spec in offering_specs}
    comparison_specs = panel.get("paired_comparisons")
    if not isinstance(comparison_specs, list) or not comparison_specs:
        raise ValueError("panel needs a non-empty paired_comparisons array")
    comparison_ids = [str(spec["id"]) for spec in comparison_specs]
    if len(comparison_ids) != len(set(comparison_ids)):
        raise ValueError("paired comparison ids must be unique")
    for spec in comparison_specs:
        if spec.get("candidate") not in offerings or spec.get("reference") not in offerings:
            raise ValueError(f"{spec.get('id', 'comparison')} names an unknown offering")
        if spec["candidate"] == spec["reference"]:
            raise ValueError(f"{spec['id']} must compare two different offerings")

    payload = {
        "schema": SCHEMA,
        "panel": {
            "id": panel["id"],
            "version": panel["version"],
            "definition_sha256": _sha256(panel_path),
            "aggregate_result_sha256": _sha256(aggregate_path),
            "seeds": seeds,
            "prompts_per_seed": 30,
            "matched_cells_per_comparison": len(seeds) * 30,
        },
        "methodology": {
            "delta_sign": "candidate minus reference",
            "pairing": "same prompt identifier and requested synthesis seed",
            "bootstrap": (
                "descriptive crossed bootstrap: resample seeds and prompt identifiers, "
                "then evaluate their full crossing while preserving offering pairs"
            ),
            "bootstrap_iterations": iterations,
            "bootstrap_random_seed": random_seed,
            "warning": (
                "Only three seed labels are represented. Matching a seed label controls "
                "the requested setting but does not imply identical random draws across runtimes."
            ),
            "interpretation": (
                "Bootstrap fractions are descriptive resampling support, not posterior "
                "probabilities or a pass/fail significance test."
            ),
        },
        "comparisons": [
            _comparison(
                spec=spec,
                offerings=offerings,
                root=panel_path.parent,
                seeds=seeds,
                iterations=iterations,
                random_seed=random_seed,
            )
            for spec in comparison_specs
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--aggregate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = _render_comparisons(args.panel, args.aggregate)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated paired comparison: {args.output}")
        print(f"valid generated paired comparison: {args.output}")
        return
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
