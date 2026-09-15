#!/usr/bin/env python3
"""Import and initialize an ASR framework before starting a model-cold probe.

The parent launches this helper without starting its measurement clock. This
helper imports the selected existing ASR benchmark, initializes the accelerator
context without loading model weights, signals that the framework is ready,
and waits. The parent's timed activation command then lets the unchanged
benchmark run in startup-probe mode.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("mlx", "transformers"), required=True)
    parser.add_argument("--benchmark-script", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("benchmark_arguments", nargs=argparse.REMAINDER)
    return parser


def _load_benchmark(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("model_skyline_asr_benchmark", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import benchmark: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _initialize_accelerator(module: ModuleType, backend: str) -> None:
    if backend == "mlx":
        mx = module.mx
        probe = mx.zeros((1,))
        mx.eval(probe)
        mx.synchronize()
        del probe
        mx.clear_cache()
        return

    torch = module.torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    torch.cuda.init()
    probe = torch.empty(1, device="cuda")
    torch.cuda.synchronize()
    del probe
    torch.cuda.empty_cache()


def _descriptor(name: str) -> int:
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(f"missing parent communication descriptor: {name}")
    return int(value)


def _signal_ready_and_wait() -> None:
    ready_fd = _descriptor("MODEL_SKYLINE_FRAMEWORK_READY_FD")
    go_fd = _descriptor("MODEL_SKYLINE_FRAMEWORK_GO_FD")
    try:
        os.write(ready_fd, b"R")
    finally:
        os.close(ready_fd)
    try:
        command = os.read(go_fd, 1)
    finally:
        os.close(go_fd)
    if command != b"G":
        raise RuntimeError("parent did not send the activation command")


def main() -> None:
    args = _parser().parse_args()
    if args.benchmark_arguments and args.benchmark_arguments[0] == "--":
        args.benchmark_arguments = args.benchmark_arguments[1:]
    if not args.benchmark_arguments:
        raise SystemExit("pass benchmark arguments after --")
    if "--output" in args.benchmark_arguments or "--startup-probe" in args.benchmark_arguments:
        raise SystemExit("the framework-hot launcher owns --output and --startup-probe")

    benchmark = _load_benchmark(args.benchmark_script)
    _initialize_accelerator(benchmark, args.backend)
    _signal_ready_and_wait()

    sys.argv = [
        str(args.benchmark_script),
        *args.benchmark_arguments,
        "--startup-probe",
        "--output",
        str(args.output),
    ]
    benchmark.main()


if __name__ == "__main__":
    main()
