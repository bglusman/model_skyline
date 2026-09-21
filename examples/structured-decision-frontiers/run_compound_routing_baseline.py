#!/usr/bin/env python3
"""Run the transparent policy control for the compound-routing stress screen."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def classify(state: dict[str, Any]) -> str:
    """Choose the cheapest route allowed by the explicit packet constraints."""

    if (
        state["authority"] != "authorized"
        or not state["evidence_complete"]
        or not state["capability_available"]
    ):
        return "human_review"

    if state["action_mode"] == "irreversible_mutation":
        return "human_review"

    needs_verifier = (
        state["action_mode"] == "reversible_mutation"
        or state["verification_required"]
        or state["consequence"] == "high"
    )
    if needs_verifier:
        return "model_plus_verifier" if state["verifier_available"] else "human_review"

    if state["hard_rule_available"]:
        return "deterministic"

    specialist_usable = (
        state["specialist_available"]
        and state["specialist_in_scope"]
        and state["specialist_context_supported"]
        and state["specialist_language_supported"]
        and state["reasoning_depth"] == "bounded"
    )
    if specialist_usable:
        return "local_specialist"

    remote_usable = (
        state["remote_allowed"]
        and state["remote_available"]
        and state["data_sensitivity"] == "public"
    )
    return "remote_model" if remote_usable else "local_general"


def run(suite_path: Path) -> dict[str, Any]:
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    by_route: Counter[str] = Counter()
    by_stratum: Counter[str] = Counter()
    for case in suite["cases"]:
        predicted = classify(case["state"])
        correct = predicted == case["expected"]
        unsafe = predicted in case["unsafe_predictions"]
        by_route[case["expected"]] += 1
        by_stratum[case["stratum"]] += 1
        rows.append(
            {
                "case_id": case["case_id"],
                "contrast_set": case["contrast_set"],
                "expected": case["expected"],
                "predicted": predicted,
                "correct": correct,
                "unsafe": unsafe,
            }
        )
    return {
        "suite_id": suite["suite_id"],
        "suite_version": suite["suite_version"],
        "policy_id": "compound-routing-transparent-control-v1",
        "case_count": len(rows),
        "contrast_set_count": len({row["contrast_set"] for row in rows}),
        "correct_count": sum(row["correct"] for row in rows),
        "unsafe_count": sum(row["unsafe"] for row in rows),
        "expected_route_counts": dict(sorted(by_route.items())),
        "stratum_counts": dict(sorted(by_stratum.items())),
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
