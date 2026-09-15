#!/usr/bin/env python3
"""Capture service memory and reject samples contaminated by other CUDA jobs.

This is the strict successor to ``capture_service_memory.py``.  The original
sampler remains unchanged because published v1 results bind its exact digest.
Version 2 reuses its platform measurement primitives, but queries every CUDA
compute allocation at every sample and fails if any allocation belongs to a
process outside the selected service tree.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

SCHEMA = "model-skyline/experimental-service-memory/v2alpha1"
V1_PATH = Path(__file__).with_name("capture_service_memory.py")


def _load_v1() -> ModuleType:
    spec = importlib.util.spec_from_file_location("model_skyline_service_memory_v1", V1_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - importlib contract
        raise RuntimeError(f"could not load {V1_PATH.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v1 = _load_v1()
CaptureError = v1.CaptureError
MAX_SAMPLES = v1.MAX_SAMPLES


def _parse_cuda_process_rows(output: str) -> list[tuple[int, int]]:
    rows: list[tuple[int, int]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 2:
            raise CaptureError("nvidia-smi returned an unparseable compute-process row")
        try:
            rows.append((int(fields[0]), int(fields[1]) * 1024 * 1024))
        except ValueError as exc:
            raise CaptureError("nvidia-smi returned a non-numeric compute-process row") from exc
    return rows


def _query_cuda_process_rows() -> list[tuple[int, int]]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError("nvidia-smi is unavailable for split_cuda capture") from exc
    if completed.returncode != 0:
        raise CaptureError("nvidia-smi process-memory query failed")
    return _parse_cuda_process_rows(completed.stdout)


def _cuda_process_memory_bytes(selected_pids: set[int]) -> int:
    selected_bytes = 0
    unselected: dict[int, int] = {}
    for pid, used_bytes in _query_cuda_process_rows():
        if pid in selected_pids:
            selected_bytes += used_bytes
        else:
            unselected[pid] = unselected.get(pid, 0) + used_bytes
    if unselected:
        total = sum(unselected.values())
        raise CaptureError(
            "CUDA isolation check found "
            f"{len(unselected)} unselected compute process(es) using {total} bytes"
        )
    return selected_bytes


def _sample(
    *,
    architecture: str,
    table: dict[int, Any],
    selected_pids: set[int],
    elapsed_milliseconds: int,
) -> dict[str, int | None]:
    rss_bytes = sum(table[pid].rss_bytes for pid in selected_pids)
    if architecture == "apple_unified":
        values = [v1._darwin_physical_footprint_bytes(pid) for pid in selected_pids]
        host_physical_bytes = (
            sum(value for value in values if value is not None)
            if all(value is not None for value in values)
            else None
        )
        device_memory_bytes = None
        combined_capacity_bytes = host_physical_bytes
        unselected_cuda_process_count = None
        unselected_cuda_memory_bytes = None
    elif architecture == "linux_split_cuda":
        values = [v1._linux_pss_bytes(pid) for pid in selected_pids]
        host_physical_bytes = (
            sum(value for value in values if value is not None)
            if all(value is not None for value in values)
            else None
        )
        device_memory_bytes = _cuda_process_memory_bytes(selected_pids)
        combined_capacity_bytes = (
            None if host_physical_bytes is None else host_physical_bytes + device_memory_bytes
        )
        unselected_cuda_process_count = 0
        unselected_cuda_memory_bytes = 0
    else:  # pragma: no cover - argparse owns this boundary
        raise CaptureError(f"unknown memory architecture {architecture!r}")
    return {
        "elapsed_milliseconds": elapsed_milliseconds,
        "selected_process_count": len(selected_pids),
        "rss_bytes": rss_bytes,
        "host_physical_bytes": host_physical_bytes,
        "device_memory_bytes": device_memory_bytes,
        "combined_capacity_bytes": combined_capacity_bytes,
        "unselected_cuda_process_count": unselected_cuda_process_count,
        "unselected_cuda_memory_bytes": unselected_cuda_memory_bytes,
    }


def _terminate_child(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    child.wait(timeout=5)


def capture(
    *,
    architecture: str,
    hardware_label: str,
    offering_id: str,
    process_label: str,
    process_matches: tuple[str, ...],
    root_pids: set[int],
    interval_seconds: float,
    wait_for_process_seconds: float,
    timeout_seconds: float,
    stop_file: Path | None,
    workload_captures: list[Path],
    command: list[str],
) -> tuple[dict[str, Any], int | None]:
    if architecture == "apple_unified" and sys.platform != "darwin":
        raise CaptureError("apple_unified capture requires macOS")
    if architecture == "linux_split_cuda" and not sys.platform.startswith("linux"):
        raise CaptureError("linux_split_cuda capture requires Linux")
    if not command and not process_matches and not root_pids:
        raise CaptureError("capture needs a command, process match, or root PID")
    if stop_file is not None and stop_file.exists():
        raise CaptureError("stop file must not exist when capture starts")

    # Reject a pre-existing CUDA owner before starting another command. Existing
    # selected services are allowed when attaching by PID or process match.
    if architecture == "linux_split_cuda":
        initial_table = v1._process_table()
        initial_selected = v1._selected_pids(
            initial_table,
            root_pids=root_pids,
            process_matches=process_matches,
            sampler_pid=os.getpid(),
        )
        _cuda_process_memory_bytes(initial_selected)

    child: subprocess.Popen[bytes] | None = None
    if command:
        child = subprocess.Popen(command, start_new_session=True)
        root_pids.add(child.pid)
    started_at = datetime.now(UTC)
    started_ns = time.monotonic_ns()
    samples: list[dict[str, int | None]] = []
    seen_process = False
    interrupted_signal: int | None = None
    stop_reason = "selected process exited"

    def stop_capture(signum: int, _frame: object) -> None:
        nonlocal interrupted_signal
        interrupted_signal = signum

    previous_sigint = signal.signal(signal.SIGINT, stop_capture)
    previous_sigterm = signal.signal(signal.SIGTERM, stop_capture)
    try:
        while True:
            elapsed_seconds = (time.monotonic_ns() - started_ns) / 1_000_000_000
            table = v1._process_table()
            selected = v1._selected_pids(
                table,
                root_pids=root_pids,
                process_matches=process_matches,
                sampler_pid=os.getpid(),
            )
            if selected:
                seen_process = True
            elif seen_process:
                stop_reason = (
                    "launched command exited"
                    if child is not None and child.poll() is not None
                    else "selected process exited"
                )
                break
            if seen_process:
                samples.append(
                    _sample(
                        architecture=architecture,
                        table=table,
                        selected_pids=selected,
                        elapsed_milliseconds=int(elapsed_seconds * 1000),
                    )
                )
                if len(samples) >= MAX_SAMPLES:
                    raise CaptureError(f"capture exceeds {MAX_SAMPLES} samples")

            child_finished = child is not None and child.poll() is not None
            controller_stopped = stop_file is not None and stop_file.exists()
            if interrupted_signal is not None:
                stop_reason = f"signal {interrupted_signal}"
                raise CaptureError(f"memory capture interrupted by signal {interrupted_signal}")
            if child_finished:
                stop_reason = "launched command exited"
                break
            if controller_stopped:
                stop_reason = "external stop file"
                break
            if not seen_process and elapsed_seconds > wait_for_process_seconds:
                raise CaptureError("timed out waiting for a matching service process")
            if elapsed_seconds > timeout_seconds:
                raise CaptureError("memory capture timed out")
            time.sleep(interval_seconds)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if child is not None and child.poll() is None:
            _terminate_child(child)

    child_returncode = None if child is None else child.returncode
    command_exit_status = child_returncode if stop_reason == "launched command exited" else None
    finished_at = datetime.now(UTC)
    summary = v1._summarize(samples)
    return (
        {
            "schema": SCHEMA,
            "offering_id": offering_id,
            "hardware": hardware_label,
            "process_label": process_label,
            "captured_at": started_at.isoformat().replace("+00:00", "Z"),
            "finished_at": finished_at.isoformat().replace("+00:00", "Z"),
            "host": {
                "system": platform.system(),
                "machine": platform.machine(),
                "release": platform.release(),
            },
            "instrument": {
                "memory_architecture": architecture,
                "sample_interval_seconds": str(interval_seconds),
                "process_selector_sha256": [
                    hashlib.sha256(value.encode("utf-8")).hexdigest() for value in process_matches
                ],
                "explicit_root_pid_count": len(root_pids) - (1 if child is not None else 0),
                "launched_command_sha256": (
                    None
                    if not command
                    else hashlib.sha256("\0".join(command).encode("utf-8")).hexdigest()
                ),
                "host_measure": (
                    "sum of macOS RUSAGE_INFO_V4 physical footprint for the process tree"
                    if architecture == "apple_unified"
                    else "sum of Linux smaps_rollup PSS for the process tree"
                ),
                "device_measure": (
                    None
                    if architecture == "apple_unified"
                    else "sum of nvidia-smi used_memory for process-tree compute contexts"
                ),
                "cuda_process_isolation": (
                    None
                    if architecture == "apple_unified"
                    else "every sample rejects compute allocations outside the selected tree"
                ),
                "combined_measure": (
                    "unified physical footprint"
                    if architecture == "apple_unified"
                    else "host PSS plus CUDA device memory in the same sample"
                ),
                "warning": (
                    "For split memory, the combined value is an efficiency accounting metric, "
                    "not a standalone fit guarantee; host and device peaks remain separate."
                ),
            },
            "stop_reason": stop_reason,
            "workload_captures": v1._workload_sources(workload_captures),
            "child_returncode": child_returncode,
            "contains_prompts_or_model_messages": False,
            "summary": summary,
            "samples": samples,
        },
        command_exit_status,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--architecture", choices=("apple_unified", "linux_split_cuda"), required=True
    )
    parser.add_argument("--hardware-label", required=True)
    parser.add_argument("--offering-id", required=True)
    parser.add_argument("--process-label", required=True)
    parser.add_argument("--process-match", action="append", default=[])
    parser.add_argument("--root-pid", action="append", type=int, default=[])
    parser.add_argument("--interval-seconds", type=float, default=0.1)
    parser.add_argument("--wait-for-process-seconds", type=float, default=600.0)
    parser.add_argument("--timeout-seconds", type=float, default=3600.0)
    parser.add_argument(
        "--stop-file",
        type=Path,
        help="stop cleanly after this external sentinel appears",
    )
    parser.add_argument("--workload-capture", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 0.05 <= args.interval_seconds <= 60:
        parser.error("--interval-seconds must be between 0.05 and 60")
    if args.wait_for_process_seconds < 0 or args.timeout_seconds <= 0:
        parser.error("wait and timeout values must be non-negative")
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        payload, returncode = capture(
            architecture=args.architecture,
            hardware_label=args.hardware_label,
            offering_id=args.offering_id,
            process_label=args.process_label,
            process_matches=tuple(args.process_match),
            root_pids=set(args.root_pid),
            interval_seconds=args.interval_seconds,
            wait_for_process_seconds=args.wait_for_process_seconds,
            timeout_seconds=args.timeout_seconds,
            stop_file=args.stop_file,
            workload_captures=args.workload_capture,
            command=command,
        )
    except CaptureError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if returncode not in (None, 0):
        raise SystemExit(returncode)


if __name__ == "__main__":
    main()
