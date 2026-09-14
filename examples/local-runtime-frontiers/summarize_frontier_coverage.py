#!/usr/bin/env python3
"""Summarize exact and near membership across local two-axis frontiers."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TypedDict

import yaml

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
    rejected_frontiers: list[str]
    attempted_frontiers: list[str]
    attempted_frontier_count: int
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
    frontiers_with_rejected_offerings: list[str]
    rejected_only_frontiers: list[str]
    attempted_frontiers: list[str]
    attempted_frontier_count: int


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


def _percentage(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        raise ValueError("percentage denominator must be positive")
    value = Decimal(numerator * 100) / Decimal(denominator)
    return _decimal(value.quantize(Decimal("0.01")))


def _load_candidate_population(path: Path, labels: set[str]) -> dict[str, object]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("candidate population must be an object")
    if payload.get("schema_version") != ("model-skyline/local-frontier-candidate-population/v1"):
        raise ValueError("unsupported candidate population schema_version")
    population_id = payload.get("population_id")
    scope = payload.get("scope")
    candidates = payload.get("candidates")
    if not isinstance(population_id, str) or not population_id:
        raise ValueError("candidate population_id must be a non-empty string")
    if not isinstance(scope, dict) or not scope:
        raise ValueError("candidate population scope must be a non-empty object")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in scope.items()):
        raise ValueError("candidate population scope keys and values must be strings")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidate population candidates must be a non-empty list")

    seen_model_ids: set[str] = set()
    normalized: list[dict[str, object]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate {index} must be an object")
        model_id = candidate.get("model_id")
        role = candidate.get("role")
        required_frontiers = candidate.get("required_frontiers")
        sources = candidate.get("sources")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError(f"candidate {index} model_id must be a non-empty string")
        if model_id in seen_model_ids:
            raise ValueError(f"candidate model_id is duplicated: {model_id}")
        seen_model_ids.add(model_id)
        if not isinstance(role, str) or not role:
            raise ValueError(f"candidate {model_id} role must be a non-empty string")
        if (
            not isinstance(required_frontiers, list)
            or not required_frontiers
            or not all(isinstance(label, str) and label for label in required_frontiers)
        ):
            raise ValueError(
                f"candidate {model_id} required_frontiers must be a non-empty string list"
            )
        if len(required_frontiers) != len(set(required_frontiers)):
            raise ValueError(f"candidate {model_id} repeats a required frontier")
        unknown = set(required_frontiers) - labels
        if unknown:
            raise ValueError(f"candidate {model_id} names unknown frontiers: {sorted(unknown)}")
        if (
            not isinstance(sources, list)
            or not sources
            or not all(
                isinstance(source, str) and source.startswith(("https://", "http://"))
                for source in sources
            )
        ):
            raise ValueError(f"candidate {model_id} sources must be non-empty HTTP URLs")
        normalized.append(
            {
                "model_id": model_id,
                "role": role,
                "required_frontiers": sorted(required_frontiers),
                "sources": sources,
            }
        )
    return {
        "population_id": population_id,
        "scope": scope,
        "candidates": normalized,
    }


def _rejected_model_id(
    offering_id: str,
    offering_models: dict[str, str],
    candidate_model_ids: set[str],
) -> str | None:
    known = offering_models.get(offering_id)
    if known is not None:
        return known
    matches = [model_id for model_id in candidate_model_ids if f"/{model_id}@" in offering_id]
    if len(matches) == 1:
        return matches[0]
    return None


def _new_coverage_entry(offering: OfferingKey) -> CoverageEntry:
    return {
        "offering_id": offering.offering_id,
        "model_id": offering.model_id,
        "provider": offering.provider,
        "quantization": offering.quantization,
        "frontiers": [],
        "near_frontiers": [],
        "evaluated_frontiers": [],
        "rejected_frontiers": [],
        "attempted_frontiers": [],
        "attempted_frontier_count": 0,
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
        "--candidate-population",
        type=Path,
        help=(
            "optional nominated model-family population; reports attempted and missing "
            "frontier cells without treating missing evidence as dominance"
        ),
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
    try:
        candidate_population = (
            _load_candidate_population(args.candidate_population, set(labels))
            if args.candidate_population is not None
            else None
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        parser.error(str(exc))
    candidate_model_ids = (
        {str(candidate["model_id"]) for candidate in candidate_population["candidates"]}
        if candidate_population is not None
        else set()
    )

    offerings: dict[str, CoverageEntry] = {}
    family_exact: dict[str, set[str]] = defaultdict(set)
    family_near: dict[str, set[str]] = defaultdict(set)
    family_evaluated: dict[str, set[str]] = defaultdict(set)
    family_rejected: dict[str, set[str]] = defaultdict(set)
    rejected_by_offering: dict[str, set[str]] = defaultdict(set)
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
        for rejection in snapshot.rejected:
            rejected_by_offering[rejection.offering_id].add(label)
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

    offering_models = {
        offering_id: str(entry["model_id"]) for offering_id, entry in offerings.items()
    }
    for offering_id, rejected_frontiers in rejected_by_offering.items():
        model_id = _rejected_model_id(
            offering_id,
            offering_models,
            candidate_model_ids,
        )
        if model_id is not None:
            family_rejected[model_id].update(rejected_frontiers)
        entry = offerings.get(offering_id)
        if entry is not None:
            entry["rejected_frontiers"] = sorted(rejected_frontiers)

    for entry in offerings.values():
        for field in (
            "frontiers",
            "near_frontiers",
            "evaluated_frontiers",
            "rejected_frontiers",
        ):
            entry[field] = sorted(entry[field])
        exact_frontiers = entry["frontiers"]
        near_frontiers = entry["near_frontiers"]
        covered = sorted(set(exact_frontiers) | set(near_frontiers))
        attempted = sorted(set(entry["evaluated_frontiers"]) | set(entry["rejected_frontiers"]))
        entry["frontier_count"] = len(exact_frontiers)
        entry["near_frontier_count"] = len(near_frontiers)
        entry["covered_frontiers"] = covered
        entry["coverage_count"] = len(covered)
        entry["attempted_frontiers"] = attempted
        entry["attempted_frontier_count"] = len(attempted)

    exact_offerings = sorted(
        (item for item in offerings.values() if item["frontiers"]),
        key=_coverage_sort_key,
    )
    observed_model_ids = set(family_evaluated) | set(family_rejected)
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
                "frontiers_with_rejected_offerings": sorted(family_rejected[model_id]),
                "rejected_only_frontiers": sorted(
                    family_rejected[model_id] - family_evaluated[model_id]
                ),
                "attempted_frontiers": sorted(
                    family_evaluated[model_id] | family_rejected[model_id]
                ),
                "attempted_frontier_count": len(
                    family_evaluated[model_id] | family_rejected[model_id]
                ),
            }
            for model_id in observed_model_ids
        ),
        key=_family_sort_key,
    )

    population_report: dict[str, object] | None = None
    if candidate_population is not None:
        candidate_rows: list[dict[str, object]] = []
        required_cell_count = 0
        attempted_cell_count = 0
        eligible_cell_count = 0
        exact_member_cell_count = 0
        for candidate in candidate_population["candidates"]:
            model_id = str(candidate["model_id"])
            required = set(candidate["required_frontiers"])
            exact = family_exact[model_id] & required
            near = (family_near[model_id] - exact) & required
            eligible = family_evaluated[model_id] & required
            rejected_offerings = family_rejected[model_id] & required
            rejected_only = rejected_offerings - eligible
            attempted = eligible | rejected_offerings
            dominated = eligible - exact - near
            missing = required - attempted
            required_cell_count += len(required)
            attempted_cell_count += len(attempted)
            eligible_cell_count += len(eligible)
            exact_member_cell_count += len(exact)
            candidate_rows.append(
                {
                    **candidate,
                    "exact_frontiers": sorted(exact),
                    "near_frontiers": sorted(near),
                    "dominated_frontiers": sorted(dominated),
                    "eligible_evaluated_frontiers": sorted(eligible),
                    "frontiers_with_rejected_offerings": sorted(rejected_offerings),
                    "rejected_only_frontiers": sorted(rejected_only),
                    "attempted_frontiers": sorted(attempted),
                    "unattempted_frontiers": sorted(missing),
                    "required_frontier_count": len(required),
                    "attempted_frontier_count": len(attempted),
                    "attempted_percent": _percentage(len(attempted), len(required)),
                }
            )
        population_report = {
            "population_id": candidate_population["population_id"],
            "scope": candidate_population["scope"],
            "candidate_count": len(candidate_rows),
            "required_cell_count": required_cell_count,
            "attempted_cell_count": attempted_cell_count,
            "eligible_cell_count": eligible_cell_count,
            "exact_member_cell_count": exact_member_cell_count,
            "attempted_percent": _percentage(
                attempted_cell_count,
                required_cell_count,
            ),
            "candidates": candidate_rows,
        }

    payload = {
        "schema_version": "model-skyline/local-frontier-coverage/v3",
        "membership_semantics": (
            "Membership already incorporates each frontier axis's configured epsilon; "
            "advisory near membership is the smallest relative epsilon at which an eligible "
            "point ceases to be dominated under the core point/robust comparison. Rejected "
            "offerings are ineligible and receive no proximity; absent offerings are unmeasured. "
            "Candidate-population attempt coverage counts an ordinary evaluation or explicit "
            "eligibility rejection, not a win. This report does not alter a frontier, rank routes, "
            "or add a synthetic score."
        ),
        "near_epsilon": _decimal(args.near_epsilon),
        "candidate_population": population_report,
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
