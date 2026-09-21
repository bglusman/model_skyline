#!/usr/bin/env python3
"""Run the transparent hard gate for the media sync safety screen."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _both(record_a: dict[str, Any], record_b: dict[str, Any], field: str) -> tuple[Any, Any]:
    return record_a.get(field), record_b.get(field)


def classify(state: dict[str, Any]) -> str:
    """Return only decisions justified by deterministic catalog evidence."""
    record_a = state["record_a"]
    record_b = state["record_b"]
    defects = set(state.get("known_metadata_defects", []))
    if defects:
        return "abstain"

    contained_a = set(record_a.get("contained_work_ids", []))
    contained_b = set(record_b.get("contained_work_ids", []))
    if len(contained_a) > 1 or len(contained_b) > 1:
        return "abstain"

    work_a, work_b = _both(record_a, record_b, "work_id")
    if work_a and work_b and work_a != work_b:
        return "separate"

    language_a, language_b = _both(record_a, record_b, "language")
    if language_a and language_b and language_a != language_b:
        return "separate"

    abridged_a, abridged_b = _both(record_a, record_b, "abridged")
    if abridged_a is True or abridged_b is True:
        return "separate"

    coverage_a = record_a.get("coverage")
    coverage_b = record_b.get("coverage")
    if coverage_a is not None and coverage_b is not None and coverage_a != coverage_b:
        return "separate"
    kind_a, kind_b = _both(record_a, record_b, "coverage_kind")
    if kind_a and kind_b and kind_a != kind_b:
        return "separate"

    series_a, series_b = _both(record_a, record_b, "series")
    number_a, number_b = _both(record_a, record_b, "series_number")
    if (
        series_a
        and series_a == series_b
        and number_a is not None
        and number_b is not None
        and number_a != number_b
    ):
        return "separate"

    hash_a, hash_b = _both(record_a, record_b, "file_sha256")
    if hash_a and hash_a == hash_b:
        return "link"

    isbn_a, isbn_b = _both(record_a, record_b, "isbn13")
    if isbn_a and isbn_a == isbn_b:
        return "link"

    if work_a and work_a == work_b:
        format_a, format_b = _both(record_a, record_b, "format")
        both_text_formats = format_a in {"epub", "pdf", "azw3", "mobi"} and format_b in {
            "epub",
            "pdf",
            "azw3",
            "mobi",
        }
        identity_complete = language_a is not None and language_b is not None
        content_complete = both_text_formats or (abridged_a is not None and abridged_b is not None)
        if identity_complete and content_complete:
            return "link"

    return "abstain"


def run(suite_path: Path) -> dict[str, Any]:
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    by_stratum: Counter[str] = Counter()
    correct_by_stratum: Counter[str] = Counter()
    for case in suite["cases"]:
        predicted = classify(case["state"])
        correct = predicted == case["expected"]
        by_stratum[case["stratum"]] += 1
        correct_by_stratum[case["stratum"]] += int(correct)
        rows.append(
            {
                "case_id": case["case_id"],
                "expected": case["expected"],
                "predicted": predicted,
                "correct": correct,
                "unsafe": case["expected"] == "abstain" and predicted != "abstain",
            }
        )
    correct_count = sum(row["correct"] for row in rows)
    handled_count = sum(row["predicted"] != "abstain" for row in rows)
    return {
        "suite_id": suite["suite_id"],
        "suite_version": suite["suite_version"],
        "policy_id": "media-sync-deterministic-hard-gate-v1",
        "case_count": len(rows),
        "correct_count": correct_count,
        "accuracy": correct_count / len(rows),
        "handled_count": handled_count,
        "handled_share": handled_count / len(rows),
        "unsafe_count": sum(row["unsafe"] for row in rows),
        "by_stratum": {
            stratum: {
                "case_count": count,
                "correct_count": correct_by_stratum[stratum],
            }
            for stratum, count in sorted(by_stratum.items())
        },
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.suite)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
