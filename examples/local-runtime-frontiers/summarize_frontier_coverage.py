#!/usr/bin/env python3
"""Summarize exact-route and model-family membership across local frontiers."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from model_skyline.io import load_frontier_snapshot


def _assignment(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if not separator or not label or not path:
        raise argparse.ArgumentTypeError("frontiers must use LABEL=PATH")
    return label, Path(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--frontier",
        action="append",
        type=_assignment,
        required=True,
        help="repeat LABEL=PATH for every position-specific frontier snapshot",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    labels = [label for label, _ in args.frontier]
    if len(labels) != len(set(labels)):
        parser.error("frontier labels must be unique")

    exact: dict[str, dict[str, object]] = {}
    families: dict[str, set[str]] = defaultdict(set)
    frontiers: list[dict[str, object]] = []
    for label, path in args.frontier:
        snapshot = load_frontier_snapshot(path)
        members: list[dict[str, str]] = []
        for member in snapshot.members:
            offering = member.offering
            exact_entry = exact.setdefault(
                offering.offering_id,
                {
                    "offering_id": offering.offering_id,
                    "model_id": offering.model_id,
                    "provider": offering.provider,
                    "quantization": offering.quantization,
                    "frontiers": [],
                },
            )
            exact_frontiers = exact_entry["frontiers"]
            assert isinstance(exact_frontiers, list)
            exact_frontiers.append(label)
            families[offering.model_id].add(label)
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
                "rejected_count": len(snapshot.rejected),
            }
        )

    exact_offerings = sorted(
        exact.values(),
        key=lambda item: (-len(item["frontiers"]), item["offering_id"]),
    )
    model_families = sorted(
        (
            {
                "model_id": model_id,
                "frontiers": sorted(member_frontiers),
                "frontier_count": len(member_frontiers),
            }
            for model_id, member_frontiers in families.items()
        ),
        key=lambda item: (-item["frontier_count"], item["model_id"]),
    )
    for item in exact_offerings:
        item["frontiers"] = sorted(item["frontiers"])
        item["frontier_count"] = len(item["frontiers"])

    payload = {
        "schema_version": "model-skyline/local-frontier-coverage/v1",
        "membership_semantics": (
            "Membership already incorporates each frontier axis's configured epsilon; "
            "this report does not add a second synthetic score."
        ),
        "frontiers": frontiers,
        "exact_offerings": exact_offerings,
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
