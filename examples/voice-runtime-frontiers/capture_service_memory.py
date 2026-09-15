#!/usr/bin/env python3
"""Sample the physical memory charged to one local inference service.

Apple unified-memory captures sum the kernel-accounted physical footprint of
the selected process tree. Linux/CUDA captures sum process proportional set
size (PSS) and CUDA device memory for the same tree. The combined split-memory
number is an efficiency accounting measure; its host and device components
remain separate because either pool can be the actual fit constraint.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "model-skyline/experimental-service-memory/v1alpha1"
MAX_SAMPLES = 100_000


class CaptureError(ValueError):
    """The memory capture cannot be completed with comparable measurements."""


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    rss_bytes: int
    command: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _process_table() -> dict[int, ProcessInfo]:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss=,command="],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError("could not read the process table") from exc
    if completed.returncode != 0:
        raise CaptureError("process table command failed")
    result: dict[int, ProcessInfo] = {}
    for line in completed.stdout.splitlines():
        fields = line.strip().split(maxsplit=3)
        if len(fields) != 4:
            continue
        try:
            pid = int(fields[0])
            ppid = int(fields[1])
            rss_bytes = int(fields[2]) * 1024
        except ValueError:
            continue
        result[pid] = ProcessInfo(pid, ppid, rss_bytes, fields[3])
    return result


def _ancestor_pids(table: dict[int, ProcessInfo], pid: int) -> set[int]:
    ancestors: set[int] = set()
    current = pid
    while current in table:
        parent = table[current].ppid
        if parent <= 0 or parent == current or parent in ancestors:
            break
        ancestors.add(parent)
        current = parent
    return ancestors


def _selected_pids(
    table: dict[int, ProcessInfo],
    *,
    root_pids: set[int],
    process_matches: tuple[str, ...],
    sampler_pid: int,
) -> set[int]:
    excluded = _ancestor_pids(table, sampler_pid) | {sampler_pid}
    roots = {pid for pid in root_pids if pid in table and pid not in excluded}
    for pid, info in table.items():
        if pid in excluded or "capture_service_memory.py" in info.command:
            continue
        if any(literal in info.command for literal in process_matches):
            roots.add(pid)
    selected = set(roots)
    changed = True
    while changed:
        changed = False
        for pid, info in table.items():
            if pid not in selected and info.ppid in selected:
                selected.add(pid)
                changed = True
    return selected


def _darwin_physical_footprint_bytes(pid: int) -> int | None:
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pid_rusage = libproc.proc_pid_rusage
        proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        proc_pid_rusage.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(512)
        if proc_pid_rusage(pid, 4, buffer) != 0:
            return None
        # RUSAGE_INFO_V4. ri_phys_footprint is the eighth uint64 after the UUID.
        return int.from_bytes(buffer.raw[72:80], byteorder=sys.byteorder)
    except (AttributeError, OSError):
        return None


def _linux_pss_bytes(pid: int) -> int | None:
    try:
        lines = Path(f"/proc/{pid}/smaps_rollup").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    for line in lines:
        if not line.startswith("Pss:"):
            continue
        fields = line.split()
        if len(fields) != 3 or fields[2] != "kB":
            return None
        try:
            return int(fields[1]) * 1024
        except ValueError:
            return None
    return None


def _cuda_process_memory_bytes(selected_pids: set[int]) -> int:
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
    total_mib = 0
    for line in completed.stdout.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 2:
            continue
        try:
            pid = int(fields[0])
            used_mib = int(fields[1])
        except ValueError:
            continue
        if pid in selected_pids:
            total_mib += used_mib
    return total_mib * 1024 * 1024


def _sample(
    *,
    architecture: str,
    table: dict[int, ProcessInfo],
    selected_pids: set[int],
    elapsed_milliseconds: int,
) -> dict[str, int | None]:
    rss_bytes = sum(table[pid].rss_bytes for pid in selected_pids)
    if architecture == "apple_unified":
        values = [_darwin_physical_footprint_bytes(pid) for pid in selected_pids]
        host_physical_bytes = (
            sum(value for value in values if value is not None)
            if all(value is not None for value in values)
            else None
        )
        device_memory_bytes = None
        combined_capacity_bytes = host_physical_bytes
    elif architecture == "linux_split_cuda":
        values = [_linux_pss_bytes(pid) for pid in selected_pids]
        host_physical_bytes = (
            sum(value for value in values if value is not None)
            if all(value is not None for value in values)
            else None
        )
        device_memory_bytes = _cuda_process_memory_bytes(selected_pids)
        combined_capacity_bytes = (
            None if host_physical_bytes is None else host_physical_bytes + device_memory_bytes
        )
    else:  # pragma: no cover - argparse owns this boundary
        raise CaptureError(f"unknown memory architecture {architecture!r}")
    return {
        "elapsed_milliseconds": elapsed_milliseconds,
        "selected_process_count": len(selected_pids),
        "rss_bytes": rss_bytes,
        "host_physical_bytes": host_physical_bytes,
        "device_memory_bytes": device_memory_bytes,
        "combined_capacity_bytes": combined_capacity_bytes,
    }


def _maximum(samples: list[dict[str, int | None]], field: str) -> int | None:
    values = [sample[field] for sample in samples if sample[field] is not None]
    return max(values) if values else None


def _summarize(samples: list[dict[str, int | None]]) -> dict[str, int | None]:
    complete = [sample for sample in samples if sample["combined_capacity_bytes"] is not None]
    if not complete:
        raise CaptureError("capture has no complete physical-memory sample")
    peak_sample = max(complete, key=lambda sample: int(sample["combined_capacity_bytes"] or 0))
    return {
        "sample_count": len(samples),
        "complete_sample_count": len(complete),
        "peak_selected_process_count": _maximum(samples, "selected_process_count"),
        "peak_rss_bytes": _maximum(samples, "rss_bytes"),
        "peak_host_physical_bytes": _maximum(samples, "host_physical_bytes"),
        "peak_device_memory_bytes": _maximum(samples, "device_memory_bytes"),
        "peak_combined_capacity_bytes": peak_sample["combined_capacity_bytes"],
        "peak_combined_elapsed_milliseconds": peak_sample["elapsed_milliseconds"],
        "host_bytes_at_combined_peak": peak_sample["host_physical_bytes"],
        "device_bytes_at_combined_peak": peak_sample["device_memory_bytes"],
    }


def _workload_sources(paths: list[Path]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise CaptureError(f"missing regular workload capture: {path.name}")
        sources.append({"name": path.name, "sha256": _sha256(path)})
    return sources


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

    child: subprocess.Popen[bytes] | None = None
    if command:
        child = subprocess.Popen(command)
        root_pids.add(child.pid)
    started_at = datetime.now(UTC)
    started_ns = time.monotonic_ns()
    samples: list[dict[str, int | None]] = []
    seen_process = False
    interrupted = False

    def stop_capture(_signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True

    previous_sigint = signal.signal(signal.SIGINT, stop_capture)
    previous_sigterm = signal.signal(signal.SIGTERM, stop_capture)
    try:
        while True:
            elapsed_seconds = (time.monotonic_ns() - started_ns) / 1_000_000_000
            table = _process_table()
            selected = _selected_pids(
                table,
                root_pids=root_pids,
                process_matches=process_matches,
                sampler_pid=os.getpid(),
            )
            if selected:
                seen_process = True
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
            if interrupted or child_finished or (stop_file is not None and stop_file.exists()):
                break
            if not seen_process and elapsed_seconds > wait_for_process_seconds:
                raise CaptureError("timed out waiting for a matching service process")
            if elapsed_seconds > timeout_seconds:
                raise CaptureError("memory capture timed out")
            time.sleep(interval_seconds)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    child_returncode = None if child is None else child.wait()
    finished_at = datetime.now(UTC)
    summary = _summarize(samples)
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
            "workload_captures": _workload_sources(workload_captures),
            "child_returncode": child_returncode,
            "contains_prompts_or_model_messages": False,
            "summary": summary,
            "samples": samples,
        },
        child_returncode,
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
