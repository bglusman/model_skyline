#!/usr/bin/env python3
"""Measure repeat model activation from process launch to first transcript.

This parent intentionally imports only the Python standard library. Each
sample starts the selected ASR benchmark in a new child process, where model
loading and the first fixed transcription happen without a warmup request.
Model weights must already be present locally. An optional seed child warms
ordinary file/runtime caches; compiled CUDA profiles can additionally reuse an
isolated TorchInductor cache across child processes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import select
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCHEMA = "model-skyline/experimental-asr-activation-capture/v1alpha1"
CHILD_SCHEMA = "model-skyline/experimental-local-asr-capture/v1alpha1"
WRAPPER_VERSION = "1"


def _read_exact(fd: int, size: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise TimeoutError("startup child timed out before returning its transcript")
        readable, _, _ = select.select([fd], [], [], timeout)
        if not readable:
            raise TimeoutError("startup child timed out before returning its transcript")
        chunk = os.read(fd, remaining)
        if not chunk:
            raise RuntimeError("startup child closed its pipe before returning a transcript")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _command_output(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _mac_power_state() -> dict[str, Any]:
    state: dict[str, Any] = {}
    battery = _command_output(["pmset", "-g", "batt"])
    if battery is not None:
        source = re.search(r"Now drawing from '([^']+)'", battery)
        if source:
            state["source"] = source.group(1)
    profile = _command_output(["pmset", "-g", "custom"])
    if profile is not None:
        section: str | None = None
        for line in profile.splitlines():
            stripped = line.strip()
            if stripped == "Battery Power:":
                section = "battery_power"
            elif stripped == "AC Power:":
                section = "ac_power"
            elif section is not None:
                match = re.fullmatch(r"powermode\s+(\d+)", stripped)
                if match:
                    state[f"{section}_powermode"] = int(match.group(1))
    return state


def _host_state(backend: str) -> dict[str, Any]:
    state: dict[str, Any] = {"platform": platform.platform()}
    if backend == "mlx":
        state["power"] = _mac_power_state()
        state["thermal"] = _command_output(["pmset", "-g", "therm"])
    else:
        state["gpu"] = _command_output(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,power.draw,pstate,clocks.sm,clocks.mem,memory.used",
                "--format=csv,noheader,nounits",
            ]
        )
    return state


def _public_failure(error: Exception) -> str:
    message = str(error).lower()
    if "timeout" in message:
        return "activation_timeout"
    if (
        "foreign nvidia compute process" in message
        or "verify that the nvidia gpu is idle" in message
    ):
        return "gpu_idle_check_failed"
    return "child_process_failed"


def _public_benchmark_arguments(arguments: list[str]) -> list[str]:
    path_options = {"--audio-dir": "<audio-dir>", "--prompt-manifest": "<prompt-manifest>"}
    public: list[str] = []
    redact_next: str | None = None
    for argument in arguments:
        if redact_next is not None:
            public.append(redact_next)
            redact_next = None
            continue
        matched = False
        for option, placeholder in path_options.items():
            if argument == option:
                public.append(option)
                redact_next = placeholder
                matched = True
                break
            if argument.startswith(f"{option}="):
                public.append(f"{option}={placeholder}")
                matched = True
                break
        if not matched:
            public.append(argument)
    return public


def _python_version(python: Path) -> str:
    try:
        result = subprocess.run(
            [str(python), "--version"], check=False, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    output = (result.stdout or result.stderr).strip()
    return output or "unknown"


def _assert_idle_nvidia() -> None:
    command = [
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("could not verify that the NVIDIA GPU is idle") from error
    if result.returncode != 0:
        raise RuntimeError(f"could not verify that the NVIDIA GPU is idle: {result.stderr}")
    processes = result.stdout.strip()
    if processes:
        raise RuntimeError(f"foreign NVIDIA compute process detected: {processes}")


def _child_command(args: argparse.Namespace, output: Path) -> list[str]:
    command = [
        str(args.python),
        str(args.benchmark_script),
        *args.benchmark_arguments,
        "--startup-probe",
        "--output",
        str(output),
    ]
    return command


def _run_child(
    args: argparse.Namespace,
    output: Path,
    compiler_cache: Path | None,
) -> tuple[dict[str, Any], float]:
    if args.require_idle_nvidia:
        _assert_idle_nvidia()
    environment = os.environ.copy()
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    if compiler_cache is not None:
        environment["TORCHINDUCTOR_CACHE_DIR"] = str(compiler_cache / "inductor")
        environment["TRITON_CACHE_DIR"] = str(compiler_cache / "triton")
        environment["CUDA_CACHE_PATH"] = str(compiler_cache / "cuda")
    read_fd, write_fd = os.pipe()
    environment["MODEL_SKYLINE_STARTUP_READY_FD"] = str(write_fd)
    with (
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout,
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr,
    ):
        started = time.monotonic()
        process = subprocess.Popen(
            _child_command(args, output),
            stdout=stdout,
            stderr=stderr,
            text=True,
            env=environment,
            pass_fds=(write_fd,),
        )
        os.close(write_fd)
        try:
            deadline = started + args.timeout_seconds
            payload_size = struct.unpack("!Q", _read_exact(read_fd, 8, deadline))[0]
            transcript = _read_exact(read_fd, payload_size, deadline).decode("utf-8")
            elapsed = time.monotonic() - started
            remaining_timeout = max(1.0, deadline - time.monotonic())
            returncode = process.wait(timeout=remaining_timeout)
        except (TimeoutError, subprocess.TimeoutExpired) as error:
            if process.poll() is None:
                process.kill()
            process.wait()
            stderr.seek(0)
            detail = stderr.read().strip()
            message = f"startup child exceeded the {args.timeout_seconds:g}-second timeout"
            if detail:
                message += f":\n{detail}"
            raise RuntimeError(message) from error
        except Exception as error:
            if process.poll() is None:
                process.kill()
            process.wait()
            stderr.seek(0)
            detail = stderr.read().strip()
            message = str(error)
            if detail:
                message += f":\n{detail}"
            raise RuntimeError(message) from error
        finally:
            os.close(read_fd)
        stderr.seek(0)
        error_output = stderr.read().strip()
    if returncode != 0:
        raise RuntimeError(f"startup child failed with exit {returncode}:\n{error_output}")
    payload = _load(output)
    if payload.get("schema") != CHILD_SCHEMA:
        raise ValueError("startup child produced an unsupported capture")
    if payload.get("offering", {}).get("startup_probe") is not True:
        raise ValueError("startup child did not identify itself as a startup probe")
    if payload.get("summary", {}).get("sample_count") != 1:
        raise ValueError("startup child must score exactly one fixed clip")
    if payload["measurements"][0]["hypothesis"] != transcript:
        raise ValueError("startup pipe transcript does not match the retained child capture")
    return payload, elapsed


def _identity(payload: dict[str, Any]) -> dict[str, Any]:
    offering = payload["offering"]
    workload = payload["workload"]
    exact_offering = {key: value for key, value in offering.items() if key != "startup_probe"}
    return {
        **exact_offering,
        "manifest_id": workload["manifest_id"],
        "manifest_version": workload["manifest_version"],
        "manifest_sha256": workload["manifest_sha256"],
        "audio_set_sha256": workload["audio_set_sha256"],
        "panel_sample_count": workload["panel_sample_count"],
    }


def _measurement(index: int, child: dict[str, Any], elapsed: float) -> dict[str, Any]:
    transcript = child["measurements"][0]
    inference_seconds = float(transcript["final_latency_ms"]) / 1000.0
    model_load_seconds = float(child["model_load_seconds"])
    return {
        "run": index,
        "launch_to_first_transcript_seconds": round(elapsed, 6),
        "child_model_load_seconds": round(model_load_seconds, 6),
        "child_first_inference_seconds": round(inference_seconds, 6),
        "other_process_seconds": round(
            max(0.0, elapsed - model_load_seconds - inference_seconds), 6
        ),
        "testcase_id": transcript["testcase_id"],
        "audio_sha256": transcript["audio_sha256"],
        "hypothesis": transcript["hypothesis"],
        "normalized_hypothesis": transcript["normalized_hypothesis"],
        "errors": transcript["errors"],
        "reference_words": transcript["reference_words"],
    }


def _summary(measurements: list[dict[str, Any]], attempt_count: int) -> dict[str, Any]:
    if not measurements:
        raise ValueError("activation probe produced no successful measurements")
    launch = [float(item["launch_to_first_transcript_seconds"]) for item in measurements]
    loads = [float(item["child_model_load_seconds"]) for item in measurements]
    inference = [float(item["child_first_inference_seconds"]) for item in measurements]
    return {
        "sample_count": len(measurements),
        "attempt_count": attempt_count,
        "failure_count": attempt_count - len(measurements),
        "failure_percent": round((attempt_count - len(measurements)) / attempt_count * 100.0, 6),
        "launch_to_first_transcript_p50_seconds": round(statistics.median(launch), 6),
        "model_load_p50_seconds": round(statistics.median(loads), 6),
        "first_inference_p50_seconds": round(statistics.median(inference), 6),
        "transcript_variants": len({str(item["normalized_hypothesis"]) for item in measurements}),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("mlx", "transformers"), required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--benchmark-script", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--seed-runs", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--require-idle-nvidia", action="store_true")
    parser.add_argument(
        "--compiler-cache",
        choices=("system-default", "seeded-isolated", "cold-per-run-isolated"),
        default="system-default",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("benchmark_arguments", nargs=argparse.REMAINDER)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.runs < 3:
        raise SystemExit("--runs must be at least 3")
    if args.seed_runs < 0:
        raise SystemExit("--seed-runs cannot be negative")
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    if args.backend == "mlx" and args.compiler_cache != "system-default":
        raise SystemExit("MLX probes must use --compiler-cache system-default")
    if args.backend == "mlx" and args.require_idle_nvidia:
        raise SystemExit("--require-idle-nvidia only applies to the Transformers backend")
    if "--output" in args.benchmark_arguments or "--startup-probe" in args.benchmark_arguments:
        raise SystemExit("the wrapper owns --output and --startup-probe")
    if args.benchmark_arguments and args.benchmark_arguments[0] == "--":
        args.benchmark_arguments = args.benchmark_arguments[1:]
    if not args.benchmark_arguments:
        raise SystemExit("pass benchmark arguments after --")

    measurements: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    seed_seconds: list[float] = []
    exact_identity: dict[str, Any] | None = None
    host_state_before = _host_state(args.backend)
    with tempfile.TemporaryDirectory(prefix="model-skyline-asr-activation-") as temp:
        temp_path = Path(temp)
        shared_cache = (
            temp_path / "compiler-cache" if args.compiler_cache == "seeded-isolated" else None
        )
        if shared_cache is not None:
            shared_cache.mkdir()

        for seed in range(args.seed_runs):
            cache = shared_cache
            if args.compiler_cache == "cold-per-run-isolated":
                cache = temp_path / f"seed-cache-{seed + 1}"
                cache.mkdir()
            child, elapsed = _run_child(
                args,
                temp_path / f"seed-{seed + 1}.json",
                cache,
            )
            exact_identity = _identity(child)
            seed_seconds.append(round(elapsed, 6))

        for index in range(1, args.runs + 1):
            cache = shared_cache
            if args.compiler_cache == "cold-per-run-isolated":
                cache = temp_path / f"run-cache-{index}"
                cache.mkdir()
            try:
                child, elapsed = _run_child(
                    args,
                    temp_path / f"run-{index}.json",
                    cache,
                )
            except (OSError, RuntimeError, ValueError) as error:
                failures.append(
                    {
                        "run": index,
                        "error_type": type(error).__name__,
                        "public_reason": _public_failure(error),
                    }
                )
                continue
            identity = _identity(child)
            if exact_identity is None:
                exact_identity = identity
            elif identity != exact_identity:
                raise ValueError("startup child offering identity changed between runs")
            measurements.append(_measurement(index, child, elapsed))

    if exact_identity is None:
        raise AssertionError("no startup process was run")
    payload = {
        "schema": SCHEMA,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "offering": exact_identity,
        "methodology": {
            "clock": (
                "parent monotonic wall time immediately before child-process launch "
                "through receipt of every byte of the first fixed UTF-8 transcript "
                "over a dedicated pipe after backend synchronization"
            ),
            "clock_includes": (
                "Python/runtime imports, manifest parsing, fixed-audio verification, "
                "local model resolution, model loading, and first inference"
            ),
            "clock_excludes": "child WER post-processing, JSON writing, and process teardown",
            "process_isolation": "every seed and scored sample uses a new child process",
            "weights": "already downloaded; Hugging Face and Transformers offline modes enabled",
            "os_file_cache": "normal host state; not cleared",
            "compiler_cache": args.compiler_cache,
            "compiler_cache_detail": (
                "system-managed Metal and MLX caches; not controlled by this harness"
                if args.compiler_cache == "system-default"
                else (
                    "one benchmark-scoped TorchInductor, Triton, and CUDA JIT cache "
                    "seeded before measured fresh-process launches"
                    if args.compiler_cache == "seeded-isolated"
                    else (
                        "new empty benchmark-scoped TorchInductor, Triton, and CUDA "
                        "JIT cache for every fresh-process launch"
                    )
                )
            ),
            "seed_runs": args.seed_runs,
            "timeout_seconds": args.timeout_seconds,
            "require_idle_nvidia": args.require_idle_nvidia,
            "audio_playback": False,
            "warning": (
                "This measures repeat activation from locally cached artifacts. It is not "
                "first installation, machine reboot, or resident-request latency."
            ),
        },
        "harness": {
            "wrapper": Path(__file__).name,
            "wrapper_version": WRAPPER_VERSION,
            "wrapper_sha256": _sha256(Path(__file__).resolve()),
            "child_script": args.benchmark_script.name,
            "child_script_sha256": _sha256(args.benchmark_script),
            "python": args.python.name,
            "python_version": _python_version(args.python),
            "backend": args.backend,
            "benchmark_arguments": _public_benchmark_arguments(args.benchmark_arguments),
        },
        "host_state_before": host_state_before,
        "host_state_after": _host_state(args.backend),
        "seed_launch_to_first_transcript_seconds": seed_seconds,
        "measurements": measurements,
        "failures": failures,
        "summary": _summary(measurements, args.runs),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
