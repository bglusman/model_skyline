#!/usr/bin/env python3
"""Generate balanced-hardware model-view policies for ASR frontier snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

FRONTIERS = (
    "asr-responsive-intelligibility",
    "asr-typical-intelligibility",
    "asr-batch-intelligibility",
    "asr-small-resident",
)
ENVIRONMENTS = {
    "Apple M1 Max 64 GB": {
        "environment_id": "m1-max-64gb",
        "provider": "local:m1-max-64gb",
    },
    "Apple M5 Max 64 GB": {
        "environment_id": "m5-max-64gb",
        "provider": "local:m5-max-64gb",
    },
    "NVIDIA GeForce RTX 5060 Ti 16 GB": {
        "environment_id": "rtx5060ti-16gb",
        "provider": "local:rtx5060ti-16gb",
    },
}
MAC_ENVIRONMENTS = {"Apple M1 Max 64 GB", "Apple M5 Max 64 GB"}


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _render(snapshot_path: Path) -> str:
    snapshot = _load(snapshot_path)
    if snapshot.get("kind") != "frontier":
        raise ValueError(f"{snapshot_path} is not a frontier snapshot")
    frontier_id = snapshot["frontier_id"]
    included_hardware = (
        MAC_ENVIRONMENTS if frontier_id == "asr-small-resident" else set(ENVIRONMENTS)
    )
    by_model: dict[str, dict[str, str]] = {}
    for item in snapshot["evaluated"]:
        model = item["offering"]["model_id"]
        hardware = item["metadata"]["hardware"]
        if hardware not in included_hardware:
            continue
        environment = ENVIRONMENTS[hardware]["environment_id"]
        by_model.setdefault(model, {})[environment] = item["offering"]["offering_id"]
    expected_environments = {
        ENVIRONMENTS[hardware]["environment_id"] for hardware in included_hardware
    }
    for model, offerings in by_model.items():
        if set(offerings) != expected_environments:
            raise ValueError(f"{model} does not have the declared balanced hardware panel")

    policy_scope = "two-mac" if frontier_id == "asr-small-resident" else "three-machine"
    policy = {
        "schema_version": "model-skyline/model-frontier-view-policy/v1alpha1",
        "policy_id": f"local-{policy_scope}-{frontier_id}",
        "source_snapshot_id": snapshot["snapshot_id"],
        "aggregation": "arithmetic_mean",
        "environments": [
            {
                "environment_id": environment["environment_id"],
                "provider": environment["provider"],
            }
            for hardware, environment in ENVIRONMENTS.items()
            if hardware in included_hardware
        ],
        "models": [
            {
                "model_id": model,
                "offerings": [
                    {
                        "environment_id": environment,
                        "offering_id": offerings[environment],
                    }
                    for environment in sorted(expected_environments)
                ],
            }
            for model, offerings in sorted(by_model.items())
        ],
    }
    return json.dumps(policy, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-dir", type=Path, required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if any policy is not byte-identical to its generated form.",
    )
    args = parser.parse_args()
    for frontier_id in FRONTIERS:
        snapshot = args.generated_dir / f"{frontier_id}.json"
        output = args.generated_dir / f"{frontier_id}-model-view-policy.json"
        rendered = _render(snapshot)
        if args.check:
            if not output.exists() or output.read_text(encoding="utf-8") != rendered:
                raise SystemExit(f"stale generated policy: {output}")
        else:
            output.write_text(rendered, encoding="utf-8")
            print(f"wrote {output}")
    if args.check:
        print(f"valid generated policies: {args.generated_dir}")


if __name__ == "__main__":
    main()
