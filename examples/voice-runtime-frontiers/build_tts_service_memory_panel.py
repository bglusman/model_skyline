#!/usr/bin/env python3
"""Validate, stage, and summarize the matched whole-service TTS memory panel."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_DEFINITION = HERE / "tts-service-memory-panel.json"
DEFAULT_OUTPUT = HERE / "raw" / "tts-service-memory-panel-v1-results.json"
MEMORY_SCHEMA = "model-skyline/experimental-service-memory/v1alpha1"
RESULT_SCHEMA = "model-skyline/experimental-service-memory-panel/v1alpha1"
PRIVATE_MARKERS = ("192.168.", "/Users/", "/root/", "model-skyline-tts-memory")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _without_seed(offering: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in offering.items() if key != "seed"}


def _safe_public_capture(path: Path) -> None:
    serialized = path.read_text(encoding="utf-8")
    marker = next((value for value in PRIVATE_MARKERS if value in serialized), None)
    if marker is not None:
        raise ValueError(f"{path.name} contains private marker {marker!r}")


def _stage(definition: dict[str, Any], source_dir: Path) -> None:
    if not source_dir.is_dir():
        raise ValueError(f"ingest source is not a directory: {source_dir}")
    for spec in definition["offerings"]:
        pairs = [(spec["source_filename"], spec["memory_capture"])]
        pairs.extend((run["source_filename"], run["path"]) for run in spec["workloads"])
        for source_name, destination_name in pairs:
            source = source_dir / source_name
            destination = HERE / destination_name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"missing regular ingest source: {source}")
            _safe_public_capture(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())


def _validate_summary(capture: dict[str, Any], path: Path, architecture: str) -> None:
    samples = capture.get("samples")
    summary = capture.get("summary")
    if not isinstance(samples, list) or not samples or not isinstance(summary, dict):
        raise ValueError(f"{path.name} needs samples and a summary")
    complete = [
        sample
        for sample in samples
        if isinstance(sample, dict) and sample.get("combined_capacity_bytes") is not None
    ]
    if not complete:
        raise ValueError(f"{path.name} has no complete sample")
    peak = max(complete, key=lambda sample: int(sample["combined_capacity_bytes"]))
    checks = {
        "sample_count": len(samples),
        "complete_sample_count": len(complete),
        "peak_rss_bytes": max(int(sample["rss_bytes"]) for sample in samples),
        "peak_host_physical_bytes": max(
            int(sample["host_physical_bytes"])
            for sample in samples
            if sample.get("host_physical_bytes") is not None
        ),
        "peak_combined_capacity_bytes": peak["combined_capacity_bytes"],
        "peak_combined_elapsed_milliseconds": peak["elapsed_milliseconds"],
        "host_bytes_at_combined_peak": peak["host_physical_bytes"],
        "device_bytes_at_combined_peak": peak["device_memory_bytes"],
    }
    device_values = [
        int(sample["device_memory_bytes"])
        for sample in samples
        if sample.get("device_memory_bytes") is not None
    ]
    checks["peak_device_memory_bytes"] = max(device_values) if device_values else None
    for field, expected in checks.items():
        if summary.get(field) != expected:
            raise ValueError(f"{path.name} has an inconsistent {field}")
    if architecture == "apple_unified":
        if device_values or peak["combined_capacity_bytes"] != peak["host_physical_bytes"]:
            raise ValueError(f"{path.name} does not contain a unified-memory capture")
    else:
        if not device_values or peak["combined_capacity_bytes"] != (
            int(peak["host_physical_bytes"]) + int(peak["device_memory_bytes"])
        ):
            raise ValueError(f"{path.name} does not contain a split CUDA capture")


def _validate_workload(
    *,
    path: Path,
    expected_seed: int,
    expected_prompt_sha256: str,
) -> dict[str, Any]:
    payload = _load(path)
    _safe_public_capture(path)
    workload = payload.get("workload")
    offering = payload.get("offering")
    if not isinstance(workload, dict) or not isinstance(offering, dict):
        raise ValueError(f"{path.name} needs workload and offering objects")
    if workload.get("manifest_sha256") != expected_prompt_sha256:
        raise ValueError(f"{path.name} uses an unexpected prompt manifest")
    if workload.get("prompt_count") != 30:
        raise ValueError(f"{path.name} is not a 30-prompt run")
    if offering.get("seed") != expected_seed:
        raise ValueError(f"{path.name} does not declare seed {expected_seed}")
    measurements = payload.get("measurements")
    if not isinstance(measurements, list):
        raise ValueError(f"{path.name} needs measurement rows")
    scored = [row for row in measurements if row.get("included_in_summary") is True]
    if len(scored) != 30:
        raise ValueError(f"{path.name} does not contain 30 scored measurements")
    return offering


def _aggregate_offering(
    *,
    root: Path,
    spec: dict[str, Any],
    quality_offering: dict[str, Any],
    seeds: list[int],
    prompt_sha256: str,
) -> dict[str, Any]:
    if quality_offering.get("offering_id") != spec["offering_id"]:
        raise ValueError(f"quality-panel offering ID mismatch for {spec['slug']}")
    memory_path = root / spec["memory_capture"]
    capture = _load(memory_path)
    _safe_public_capture(memory_path)
    if capture.get("schema") != MEMORY_SCHEMA:
        raise ValueError(f"unexpected memory schema in {memory_path.name}")
    expected_fields = {
        "offering_id": spec["offering_id"],
        "hardware": spec["hardware"],
        "process_label": spec["process_label"],
    }
    for field, expected in expected_fields.items():
        if capture.get(field) != expected:
            raise ValueError(f"unexpected {field} in {memory_path.name}")
    architecture = spec["memory_architecture"]
    if capture.get("instrument", {}).get("memory_architecture") != architecture:
        raise ValueError(f"unexpected memory architecture in {memory_path.name}")
    if capture.get("contains_prompts_or_model_messages") is not False:
        raise ValueError(f"{memory_path.name} does not assert prompt-free memory samples")
    _validate_summary(capture, memory_path, architecture)

    workload_specs = spec.get("workloads")
    if not isinstance(workload_specs, list):
        raise ValueError(f"{spec['slug']} needs workload captures")
    by_seed = {int(item["seed"]): item for item in workload_specs}
    if sorted(by_seed) != seeds or len(by_seed) != len(workload_specs):
        raise ValueError(f"{spec['slug']} does not contain the expected seeds")
    memory_sources = {item["name"]: item["sha256"] for item in capture["workload_captures"]}
    workload_sources: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    for seed in seeds:
        workload_spec = by_seed[seed]
        workload_path = root / workload_spec["path"]
        offering = _validate_workload(
            path=workload_path,
            expected_seed=seed,
            expected_prompt_sha256=prompt_sha256,
        )
        digest = _sha256(workload_path)
        if memory_sources.get(workload_path.name) != digest:
            raise ValueError(f"{memory_path.name} does not bind {workload_path.name}")
        identities.append(_without_seed(offering))
        workload_sources.append({"seed": seed, "path": workload_spec["path"], "sha256": digest})
    if any(identity != identities[0] for identity in identities[1:]):
        raise ValueError(f"{spec['slug']} changed identity between memory runs")
    quality_identity = quality_offering["offering_identity_without_seed"]
    for field in spec["identity_fields"]:
        if identities[0].get(field) != quality_identity.get(field):
            raise ValueError(f"{spec['slug']} changed {field} from its quality panel")

    return {
        "slug": spec["slug"],
        "offering_id": spec["offering_id"],
        "memory_capture": {
            "path": spec["memory_capture"],
            "sha256": _sha256(memory_path),
            "captured_at": capture["captured_at"],
            "finished_at": capture["finished_at"],
        },
        "memory_architecture": architecture,
        "isolation": spec["isolation"],
        "instrument": capture["instrument"],
        "summary": capture["summary"],
        "workload_sources": workload_sources,
    }


def _render(definition_path: Path) -> str:
    definition = _load(definition_path)
    root = definition_path.parent
    harness = definition["capture_harness"]
    harness_path = root / harness["path"]
    if _sha256(harness_path) != harness["sha256"]:
        raise ValueError("capture harness does not match its frozen post-capture digest")
    quality_path = root / definition["quality_panel"]
    quality_panel = _load(quality_path)
    quality_offerings = {item["slug"]: item for item in quality_panel["offerings"]}
    specs = definition.get("offerings")
    if not isinstance(specs, list) or not specs:
        raise ValueError("memory panel needs offering specifications")
    if {spec["slug"] for spec in specs} != set(quality_offerings):
        raise ValueError("memory and quality panels must contain the same offerings")
    seeds = sorted(int(seed) for seed in definition["seeds"])
    payload = {
        "schema": RESULT_SCHEMA,
        "panel": {
            "id": definition["id"],
            "version": definition["version"],
            "seeds": seeds,
            "prompts_per_seed": 30,
            "quality_panel": definition["quality_panel"],
            "quality_panel_sha256": _sha256(quality_path),
            "prompt_manifest_sha256": definition["prompt_manifest_sha256"],
            "capture_harness": harness,
        },
        "methodology": {
            "capture_scope": (
                "cold model load plus three complete 30-prompt runs; generated audio was not played"
            ),
            "apple_unified": (
                "sum of macOS RUSAGE_INFO_V4 physical footprint for the selected process tree"
            ),
            "linux_split_cuda": (
                "host smaps_rollup PSS plus CUDA process memory for the same process tree "
                "and sample"
            ),
            "axis_warning": (
                "Combined split-memory capacity is an efficiency accounting value, not a fit "
                "guarantee; independent host and device peaks remain hard capacity checks."
            ),
            "capture_harness_binding": harness["binding"],
        },
        "offerings": [
            _aggregate_offering(
                root=root,
                spec=spec,
                quality_offering=quality_offerings[spec["slug"]],
                seeds=seeds,
                prompt_sha256=definition["prompt_manifest_sha256"],
            )
            for spec in specs
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, default=DEFAULT_DEFINITION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--ingest-from",
        type=Path,
        help="Stage the definition's exact source filenames before building.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail unless the output is byte-identical to the validated panel.",
    )
    args = parser.parse_args()
    definition = _load(args.definition)
    if args.ingest_from is not None:
        if args.check:
            parser.error("--ingest-from and --check cannot be combined")
        _stage(definition, args.ingest_from)
    rendered = _render(args.definition)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated memory panel: {args.output}")
        print(f"valid generated memory panel: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
