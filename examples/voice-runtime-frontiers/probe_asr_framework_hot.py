#!/usr/bin/env python3
"""Measure model activation after the framework and device context are ready.

Every seed and scored sample gets a fresh process to prevent model state from
leaking between attempts. The clock starts only after that process has imported
the inference framework and initialized its accelerator context without loading
model weights. This isolates a framework-hot/model-cold activation cost; it is
an approximation of a persistent runner, not an in-process unload/reload test.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import probe_asr_activation as activation

SCHEMA = "model-skyline/experimental-asr-framework-hot-capture/v1alpha1"
WRAPPER_VERSION = "1"
LAUNCHER_VERSION = "1"


def _child_command(args: argparse.Namespace, output: Path) -> list[str]:
    return [
        str(args.python),
        str(args.launcher),
        "--backend",
        args.backend,
        "--benchmark-script",
        str(args.benchmark_script),
        "--output",
        str(output),
        "--",
        *args.benchmark_arguments,
    ]


def _run_child(
    args: argparse.Namespace,
    output: Path,
    compiler_cache: Path | None,
) -> tuple[dict[str, Any], float, float]:
    if args.require_idle_nvidia:
        activation._assert_idle_nvidia()
    environment = os.environ.copy()
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    if compiler_cache is not None:
        environment["TORCHINDUCTOR_CACHE_DIR"] = str(compiler_cache / "inductor")
        environment["TRITON_CACHE_DIR"] = str(compiler_cache / "triton")
        environment["CUDA_CACHE_PATH"] = str(compiler_cache / "cuda")

    ready_read_fd, ready_write_fd = os.pipe()
    go_read_fd, go_write_fd = os.pipe()
    transcript_read_fd, transcript_write_fd = os.pipe()
    environment["MODEL_SKYLINE_FRAMEWORK_READY_FD"] = str(ready_write_fd)
    environment["MODEL_SKYLINE_FRAMEWORK_GO_FD"] = str(go_read_fd)
    environment["MODEL_SKYLINE_STARTUP_READY_FD"] = str(transcript_write_fd)
    with (
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout,
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr,
    ):
        launched = time.monotonic()
        process = subprocess.Popen(
            _child_command(args, output),
            stdout=stdout,
            stderr=stderr,
            text=True,
            env=environment,
            pass_fds=(ready_write_fd, go_read_fd, transcript_write_fd),
        )
        os.close(ready_write_fd)
        os.close(go_read_fd)
        os.close(transcript_write_fd)
        try:
            deadline = launched + args.timeout_seconds
            if activation._read_exact(ready_read_fd, 1, deadline) != b"R":
                raise RuntimeError("framework child returned an invalid ready signal")
            framework_prepare_seconds = time.monotonic() - launched
            started = time.monotonic()
            os.write(go_write_fd, b"G")
            os.close(go_write_fd)
            go_write_fd = -1
            payload_size = struct.unpack(
                "!Q", activation._read_exact(transcript_read_fd, 8, deadline)
            )[0]
            transcript = activation._read_exact(transcript_read_fd, payload_size, deadline).decode(
                "utf-8"
            )
            elapsed = time.monotonic() - started
            remaining_timeout = max(1.0, deadline - time.monotonic())
            returncode = process.wait(timeout=remaining_timeout)
        except (TimeoutError, subprocess.TimeoutExpired) as error:
            if process.poll() is None:
                process.kill()
            process.wait()
            stderr.seek(0)
            detail = stderr.read().strip()
            message = f"framework-hot child exceeded the {args.timeout_seconds:g}-second timeout"
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
            os.close(ready_read_fd)
            os.close(transcript_read_fd)
            if go_write_fd >= 0:
                os.close(go_write_fd)
        stderr.seek(0)
        error_output = stderr.read().strip()
    if returncode != 0:
        raise RuntimeError(f"framework-hot child failed with exit {returncode}:\n{error_output}")
    payload = activation._load(output)
    if payload.get("schema") != activation.CHILD_SCHEMA:
        raise ValueError("framework-hot child produced an unsupported capture")
    if payload.get("offering", {}).get("startup_probe") is not True:
        raise ValueError("framework-hot child did not use startup-probe mode")
    if payload.get("summary", {}).get("sample_count") != 1:
        raise ValueError("framework-hot child must score exactly one fixed clip")
    if payload["measurements"][0]["hypothesis"] != transcript:
        raise ValueError("framework-hot pipe transcript does not match the child capture")
    return payload, elapsed, framework_prepare_seconds


def _measurement(
    index: int,
    child: dict[str, Any],
    elapsed: float,
    framework_prepare_seconds: float,
) -> dict[str, Any]:
    result = activation._measurement(index, child, elapsed)
    result["activation_command_to_first_transcript_seconds"] = result.pop(
        "launch_to_first_transcript_seconds"
    )
    result["framework_prepare_seconds"] = round(framework_prepare_seconds, 6)
    return result


def _summary(measurements: list[dict[str, Any]], attempt_count: int) -> dict[str, Any]:
    if not measurements:
        raise ValueError("framework-hot probe produced no successful measurements")
    activation_seconds = [
        float(item["activation_command_to_first_transcript_seconds"]) for item in measurements
    ]
    loads = [float(item["child_model_load_seconds"]) for item in measurements]
    inference = [float(item["child_first_inference_seconds"]) for item in measurements]
    prepare = [float(item["framework_prepare_seconds"]) for item in measurements]
    failure_count = attempt_count - len(measurements)
    return {
        "sample_count": len(measurements),
        "attempt_count": attempt_count,
        "failure_count": failure_count,
        "failure_percent": round(failure_count / attempt_count * 100.0, 6),
        "framework_hot_activation_p50_seconds": round(statistics.median(activation_seconds), 6),
        "model_load_p50_seconds": round(statistics.median(loads), 6),
        "first_inference_p50_seconds": round(statistics.median(inference), 6),
        "framework_prepare_p50_seconds": round(statistics.median(prepare), 6),
        "transcript_variants": len({str(item["normalized_hypothesis"]) for item in measurements}),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("mlx", "transformers"), required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--launcher", type=Path, required=True)
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
    seed_prepare_seconds: list[float] = []
    exact_identity: dict[str, Any] | None = None
    host_state_before = activation._host_state(args.backend)
    with tempfile.TemporaryDirectory(prefix="model-skyline-asr-framework-hot-") as temp:
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
            child, elapsed, prepare_seconds = _run_child(
                args, temp_path / f"seed-{seed + 1}.json", cache
            )
            exact_identity = activation._identity(child)
            seed_seconds.append(round(elapsed, 6))
            seed_prepare_seconds.append(round(prepare_seconds, 6))

        for index in range(1, args.runs + 1):
            cache = shared_cache
            if args.compiler_cache == "cold-per-run-isolated":
                cache = temp_path / f"run-cache-{index}"
                cache.mkdir()
            try:
                child, elapsed, prepare_seconds = _run_child(
                    args, temp_path / f"run-{index}.json", cache
                )
            except (OSError, RuntimeError, ValueError) as error:
                failures.append(
                    {
                        "run": index,
                        "error_type": type(error).__name__,
                        "public_reason": activation._public_failure(error),
                    }
                )
                continue
            identity = activation._identity(child)
            if exact_identity is None:
                exact_identity = identity
            elif identity != exact_identity:
                raise ValueError("framework-hot child offering identity changed between runs")
            measurements.append(_measurement(index, child, elapsed, prepare_seconds))

    if exact_identity is None:
        raise AssertionError("no framework-hot process was run")
    payload = {
        "schema": SCHEMA,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "offering": exact_identity,
        "methodology": {
            "clock": (
                "parent monotonic wall time immediately before sending a one-byte activation "
                "command to a model-cold child whose framework and accelerator context are "
                "ready, through receipt of every byte of the first fixed UTF-8 transcript"
            ),
            "clock_includes": (
                "activation-command delivery, manifest parsing, fixed-audio verification, "
                "local model resolution, model loading, and synchronized first inference"
            ),
            "clock_excludes": (
                "process launch, Python/framework imports, accelerator-context initialization, "
                "child WER post-processing, JSON writing, and process teardown"
            ),
            "process_isolation": (
                "every seed and scored sample uses a new process; the clock starts only after "
                "the framework and accelerator context report ready"
            ),
            "runner_interpretation": (
                "framework-hot/model-cold approximation; not an observed in-process model "
                "unload/reload cycle and not a complete serving-router measurement"
            ),
            "weights": "already downloaded; Hugging Face and Transformers offline modes enabled",
            "os_file_cache": "normal host state; not cleared",
            "compiler_cache": args.compiler_cache,
            "compiler_cache_detail": (
                "system-managed Metal and MLX caches; not controlled by this harness"
                if args.compiler_cache == "system-default"
                else (
                    "one benchmark-scoped TorchInductor, Triton, and CUDA JIT cache seeded "
                    "before measured framework-hot/model-cold activations"
                    if args.compiler_cache == "seeded-isolated"
                    else (
                        "new empty benchmark-scoped TorchInductor, Triton, and CUDA JIT cache "
                        "for every framework-hot/model-cold activation"
                    )
                )
            ),
            "seed_runs": args.seed_runs,
            "timeout_seconds": args.timeout_seconds,
            "require_idle_nvidia": args.require_idle_nvidia,
            "audio_playback": False,
        },
        "harness": {
            "wrapper": Path(__file__).name,
            "wrapper_version": WRAPPER_VERSION,
            "wrapper_sha256": activation._sha256(Path(__file__).resolve()),
            "activation_base": Path(activation.__file__).name,
            "activation_base_sha256": activation._sha256(Path(activation.__file__).resolve()),
            "launcher": args.launcher.name,
            "launcher_version": LAUNCHER_VERSION,
            "launcher_sha256": activation._sha256(args.launcher),
            "child_script": args.benchmark_script.name,
            "child_script_sha256": activation._sha256(args.benchmark_script),
            "python": args.python.name,
            "python_version": activation._python_version(args.python),
            "backend": args.backend,
            "benchmark_arguments": activation._public_benchmark_arguments(args.benchmark_arguments),
        },
        "host_state_before": host_state_before,
        "host_state_after": activation._host_state(args.backend),
        "seed_activation_seconds": seed_seconds,
        "seed_framework_prepare_seconds": seed_prepare_seconds,
        "measurements": measurements,
        "failures": failures,
        "summary": _summary(measurements, args.runs),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
