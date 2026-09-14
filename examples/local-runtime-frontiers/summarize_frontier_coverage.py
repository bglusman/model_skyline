#!/usr/bin/env python3
"""Summarize exact and near membership across local two-axis frontiers."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TypedDict

from model_skyline.io import load_frontier_snapshot
from model_skyline.models import OfferingKey
from model_skyline.proximity import (
    MAX_RELATIVE_EPSILON,
    OfferingProximity,
    calculate_frontier_proximity,
)


class CoverageEntry(TypedDict):
    offering_id: str
    model_id: str
    provider: str
    quantization: str | None
    frontiers: list[str]
    near_frontiers: list[str]
    evaluated_frontiers: list[str]
    frontier_count: int
    near_frontier_count: int
    covered_frontiers: list[str]
    coverage_count: int


class FamilyCoverage(TypedDict):
    model_id: str
    frontiers: list[str]
    frontier_count: int
    near_frontiers: list[str]
    near_frontier_count: int
    covered_frontiers: list[str]
    coverage_count: int
    evaluated_frontiers: list[str]


def _assignment(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if not separator or not label or not path:
        raise argparse.ArgumentTypeError("frontiers must use LABEL=PATH")
    return label, Path(path)


def _near_epsilon(value: str) -> Decimal:
    try:
        epsilon = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("near epsilon must be a decimal") from exc
    if not epsilon.is_finite() or not 0 <= epsilon <= MAX_RELATIVE_EPSILON:
        raise argparse.ArgumentTypeError(
            f"near epsilon must be between 0 and {MAX_RELATIVE_EPSILON}"
        )
    exponent = epsilon.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -34:
        raise argparse.ArgumentTypeError("near epsilon cannot exceed 34 decimal places")
    return epsilon


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _new_coverage_entry(offering: OfferingKey) -> CoverageEntry:
    return {
        "offering_id": offering.offering_id,
        "model_id": offering.model_id,
        "provider": offering.provider,
        "quantization": offering.quantization,
        "frontiers": [],
        "near_frontiers": [],
        "evaluated_frontiers": [],
        "frontier_count": 0,
        "near_frontier_count": 0,
        "covered_frontiers": [],
        "coverage_count": 0,
    }


def _coverage_sort_key(item: CoverageEntry) -> tuple[int, int, str]:
    return (-item["coverage_count"], -item["frontier_count"], item["offering_id"])


def _family_sort_key(item: FamilyCoverage) -> tuple[int, int, str]:
    return (-item["coverage_count"], -item["frontier_count"], item["model_id"])


def _proximity_entry(item: OfferingProximity, *, membership: str) -> dict[str, object]:
    offering = item.offering
    return {
        "model_id": offering.model_id,
        "offering_id": offering.offering_id,
        "membership": membership,
        "minimal_relative_epsilon": _decimal(item.minimal_relative_epsilon),
        "axis_slacks": {
            slack.metric: {
                "normalized_dominance_slack": _decimal(slack.normalized_dominance_slack),
                "witness_offering_ids": list(slack.witness_offering_ids),
            }
            for slack in item.axis_slacks
        },
        "blocking_dominance_intervals": [
            {
                "dominator_offering_id": interval.dominator_offering_id,
                "enters_at_epsilon": _decimal(interval.enters_at_epsilon),
                "exits_at_epsilon": _decimal(interval.exits_at_epsilon),
            }
            for interval in item.blocking_dominance_intervals
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--frontier",
        action="append",
        type=_assignment,
        required=True,
        help="repeat LABEL=PATH for every position-specific frontier snapshot",
    )
    parser.add_argument(
        "--near-epsilon",
        type=_near_epsilon,
        default=Decimal("0.05"),
        help=(
            "maximum normalized relative dominance epsilon for advisory near membership "
            "(default: 0.05)"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    labels = [label for label, _ in args.frontier]
    if len(labels) != len(set(labels)):
        parser.error("frontier labels must be unique")

    offerings: dict[str, CoverageEntry] = {}
    family_exact: dict[str, set[str]] = defaultdict(set)
    family_near: dict[str, set[str]] = defaultdict(set)
    family_evaluated: dict[str, set[str]] = defaultdict(set)
    frontiers: list[dict[str, object]] = []
    for label, path in args.frontier:
        snapshot = load_frontier_snapshot(path)
        members: list[dict[str, str]] = []
        near_members: list[dict[str, object]] = []
        evaluated: list[dict[str, object]] = []
        proximity = calculate_frontier_proximity(snapshot)
        for item in proximity:
            offering = item.offering
            entry = offerings.setdefault(offering.offering_id, _new_coverage_entry(offering))
            entry["evaluated_frontiers"].append(label)
            family_evaluated[offering.model_id].add(label)
            if item.exact_member:
                membership = "exact"
                entry["frontiers"].append(label)
                family_exact[offering.model_id].add(label)
            elif item.minimal_relative_epsilon <= args.near_epsilon:
                membership = "near"
                entry["near_frontiers"].append(label)
                family_near[offering.model_id].add(label)
                near_members.append(_proximity_entry(item, membership=membership))
            else:
                membership = "dominated"
            evaluated.append(_proximity_entry(item, membership=membership))
        for member in snapshot.members:
            offering = member.offering
            members.append(
                {
                    "model_id": offering.model_id,
                    "offering_id": offering.offering_id,
                }
            )
        frontiers.append(
            {
                "label": label,
                "definition": snapshot.frontier_id,
                "snapshot_id": snapshot.snapshot_id,
                "members": members,
                "near_members": sorted(
                    near_members,
                    key=lambda item: (
                        Decimal(str(item["minimal_relative_epsilon"])),
                        str(item["offering_id"]),
                    ),
                ),
                "evaluated": sorted(
                    evaluated,
                    key=lambda item: (
                        {"exact": 0, "near": 1, "dominated": 2}[str(item["membership"])],
                        Decimal(str(item["minimal_relative_epsilon"])),
                        str(item["offering_id"]),
                    ),
                ),
                "evaluated_count": len(snapshot.evaluated),
                "rejected": [
                    {
                        "offering_id": item.offering_id,
                        "reasons": list(item.reasons),
                    }
                    for item in snapshot.rejected
                ],
                "rejected_count": len(snapshot.rejected),
            }
        )

    for entry in offerings.values():
        for field in ("frontiers", "near_frontiers", "evaluated_frontiers"):
            entry[field] = sorted(entry[field])
        exact_frontiers = entry["frontiers"]
        near_frontiers = entry["near_frontiers"]
        covered = sorted(set(exact_frontiers) | set(near_frontiers))
        entry["frontier_count"] = len(exact_frontiers)
        entry["near_frontier_count"] = len(near_frontiers)
        entry["covered_frontiers"] = covered
        entry["coverage_count"] = len(covered)

    exact_offerings = sorted(
        (item for item in offerings.values() if item["frontiers"]),
        key=_coverage_sort_key,
    )
    model_families: list[FamilyCoverage] = sorted(
        (
            {
                "model_id": model_id,
                "frontiers": sorted(family_exact[model_id]),
                "frontier_count": len(family_exact[model_id]),
                "near_frontiers": sorted(family_near[model_id] - family_exact[model_id]),
                "near_frontier_count": len(family_near[model_id] - family_exact[model_id]),
                "covered_frontiers": sorted(family_exact[model_id] | family_near[model_id]),
                "coverage_count": len(family_exact[model_id] | family_near[model_id]),
                "evaluated_frontiers": sorted(family_evaluated[model_id]),
            }
            for model_id in family_evaluated
        ),
        key=_family_sort_key,
    )

    payload = {
        "schema_version": "model-skyline/local-frontier-coverage/v2",
        "membership_semantics": (
            "Membership already incorporates each frontier axis's configured epsilon; "
            "advisory near membership is the smallest relative epsilon at which an eligible "
            "point ceases to be dominated under the core point/robust comparison. Rejected or "
            "absent offerings are unmeasured, never near. This report does not alter a frontier, "
            "rank routes, or add a synthetic score."
        ),
        "near_epsilon": _decimal(args.near_epsilon),
        "frontiers": frontiers,
        "exact_offerings": exact_offerings,
        "offering_coverage": sorted(
            offerings.values(),
            key=_coverage_sort_key,
        ),
        "model_families": model_families,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
