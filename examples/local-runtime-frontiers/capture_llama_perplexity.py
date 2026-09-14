#!/usr/bin/env python3
"""Capture one reproducible llama-perplexity run as machine-readable evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_FINAL_ESTIMATE = re.compile(
    r"Final estimate:\s*PPL\s*=\s*"
    r"(?P<perplexity>[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\s*"
    r"\+/-\s*"
    r"(?P<standard_error>[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"
)
_BUILD_PATTERNS = (
    re.compile(r"build\s*[:=]\s*(?P<number>[0-9]+)\s*\((?P<commit>[0-9a-f]+)\)"),
    re.compile(r"\(?build\s+(?P<number>[0-9]+),\s*commit\s+(?P<commit>[0-9a-f]+)\)?"),
)
_CHUNK_COUNT = re.compile(r"calculating perplexity over (?P<count>[0-9]+) chunks")
_UNUSED_TENSOR = re.compile(
    r"model has unused tensor (?P<name>\S+) \(size = (?P<size>[0-9]+) bytes\) -- ignoring"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _portable(value: str, replacements: dict[str, str]) -> str:
    for source, replacement in replacements.items():
        value = value.replace(source, replacement)
    return value


def _parse_result(stdout: str, stderr: str) -> dict[str, Any]:
    combined = f"{stdout}\n{stderr}"
    matches = tuple(_FINAL_ESTIMATE.finditer(combined))
    if len(matches) != 1:
        raise ValueError(f"expected one final perplexity estimate, found {len(matches)}")
    match = matches[0]
    try:
        perplexity = Decimal(match.group("perplexity"))
        standard_error = Decimal(match.group("standard_error"))
    except InvalidOperation as exc:  # pragma: no cover - guarded by the regex
        raise ValueError("invalid numeric final perplexity estimate") from exc
    if not perplexity.is_finite() or perplexity <= 0:
        raise ValueError("perplexity must be finite and positive")
    if not standard_error.is_finite() or standard_error < 0:
        raise ValueError("perplexity standard error must be finite and nonnegative")

    builds = {
        (item.group("number"), item.group("commit"))
        for pattern in _BUILD_PATTERNS
        for item in pattern.finditer(combined)
    }
    if len(builds) > 1:
        raise ValueError("runtime output contains conflicting build identities")
    build = next(iter(builds), None)
    chunk_counts = {int(item.group("count")) for item in _CHUNK_COUNT.finditer(combined)}
    if len(chunk_counts) != 1:
        raise ValueError(f"expected one actual perplexity chunk count, found {len(chunk_counts)}")
    unused_tensors: list[dict[str, Any]] = [
        {"name": item.group("name"), "size_bytes": int(item.group("size"))}
        for item in _UNUSED_TENSOR.finditer(combined)
    ]
    return {
        "perplexity": str(perplexity),
        "standard_error": str(standard_error),
        "chunks_evaluated": next(iter(chunk_counts)),
        "runtime_build_number": None if build is None else int(build[0]),
        "runtime_commit": None if build is None else build[1],
        "unused_tensors": unused_tensors,
        "unused_tensor_bytes": sum(int(item["size_bytes"]) for item in unused_tensors),
    }


def _runtime_version_output(binary: Path) -> str:
    completed = subprocess.run(
        [str(binary), "--version"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    value = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0 or not value:
        raise ValueError("runtime did not provide usable --version output")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host-id", required=True)
    parser.add_argument(
        "--exclusive-lock",
        type=Path,
        help="optional BSD lock file shared with local model launchers",
    )
    parser.add_argument(
        "--lock-timeout",
        type=int,
        default=0,
        help="seconds to wait for --exclusive-lock; zero fails immediately",
    )
    parser.add_argument("--context", type=int, default=4096)
    parser.add_argument("--chunks", type=int, default=6)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--batch", type=int, default=2048)
    parser.add_argument("--ubatch", type=int, default=512)
    parser.add_argument(
        "--gpu-layers",
        type=int,
        default=-1,
        help="number of layers to offload; -1 requests every layer",
    )
    parser.add_argument("--kv", default="q8_0")
    parser.add_argument("--flash-attention", choices=("on", "off", "auto"), default="on")
    args = parser.parse_args()

    binary_argument = args.binary.expanduser()
    model_argument = args.model.expanduser()
    corpus_argument = args.corpus.expanduser()
    binary = binary_argument.resolve(strict=True)
    model = model_argument.resolve(strict=True)
    corpus = corpus_argument.resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error("--binary must be an executable regular file")
    if not model.is_file():
        parser.error("--model must be a regular file")
    if not corpus.is_file():
        parser.error("--corpus must be a regular file")
    for option, value in {
        "--context": args.context,
        "--chunks": args.chunks,
        "--threads": args.threads,
        "--batch": args.batch,
        "--ubatch": args.ubatch,
    }.items():
        if value <= 0:
            parser.error(f"{option} must be positive")
    if args.gpu_layers < -1:
        parser.error("--gpu-layers must be -1 or nonnegative")
    if args.lock_timeout < 0:
        parser.error("--lock-timeout must be nonnegative")

    command = [
        str(binary),
        "-m",
        str(model),
        "-f",
        str(corpus),
        "-c",
        str(args.context),
        "--chunks",
        str(args.chunks),
        "-ngl",
        str(args.gpu_layers),
        "-fit",
        "off",
        "-ctk",
        args.kv,
        "-ctv",
        args.kv,
        "-fa",
        args.flash_attention,
        "-t",
        str(args.threads),
        "-tb",
        str(args.threads),
        "-b",
        str(args.batch),
        "-ub",
        str(args.ubatch),
    ]
    replacements = {
        str(binary): "${LLAMA_PERPLEXITY}",
        str(model): "${MODEL_FILE}",
        str(corpus): "${CORPUS_FILE}",
    }
    command_template = [_portable(item, replacements) for item in command]
    command_sha256 = hashlib.sha256(
        json.dumps(command_template, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    run_command = command
    coordination: dict[str, Any] | None = None
    if args.exclusive_lock is not None:
        lock = args.exclusive_lock.expanduser().resolve()
        if not lock.parent.is_dir():
            parser.error("--exclusive-lock parent directory must exist")
        lockf = Path("/usr/bin/lockf")
        if not lockf.is_file():
            parser.error("--exclusive-lock requires /usr/bin/lockf")
        run_command = [str(lockf), "-k", "-t", str(args.lock_timeout), str(lock), *command]
        coordination = {
            "method": "bsd-flock",
            "lock_file": "${LOCAL_MODEL_RUNNER_LOCK}",
            "timeout_seconds": args.lock_timeout,
        }

    try:
        runtime_version_output = _runtime_version_output(binary)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        parser.error(str(exc))

    started_at = _timestamp()
    start = time.monotonic_ns()
    completed = subprocess.run(run_command, text=True, capture_output=True, check=False)
    elapsed_ns = time.monotonic_ns() - start
    captured_at = _timestamp()
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)
    try:
        result = _parse_result(
            completed.stdout,
            f"{completed.stderr}\n{runtime_version_output}",
        )
    except ValueError as exc:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise SystemExit(str(exc)) from exc

    artifact = {
        "filename": model_argument.name,
        "size_bytes": model.stat().st_size,
        "sha256": _sha256(model),
    }
    corpus_identity = {
        "filename": corpus_argument.name,
        "size_bytes": corpus.stat().st_size,
        "sha256": _sha256(corpus),
    }
    payload = {
        "schema_version": "model-skyline/raw-llama-perplexity/v1",
        "captured_at": captured_at,
        "started_at": started_at,
        "elapsed_ns": elapsed_ns,
        "host": {
            "host_id": args.host_id,
            "machine": platform.machine(),
            "platform": platform.platform(),
        },
        "artifact": artifact,
        "corpus": corpus_identity,
        "runtime": {
            "filename": binary_argument.name,
            "sha256": _sha256(binary),
            "version_output": runtime_version_output,
        },
        "configuration": {
            "context_tokens": args.context,
            "chunks_requested": args.chunks,
            "threads": args.threads,
            "batch": args.batch,
            "micro_batch": args.ubatch,
            "gpu_layers": args.gpu_layers,
            "kv_cache": f"{args.kv}/{args.kv}",
            "flash_attention": args.flash_attention,
            "fit": "off",
        },
        "invocation": {"command": command_template, "command_sha256": command_sha256},
        "coordination": coordination,
        "normalization": (
            "Absolute binary/model/corpus paths replaced with portable placeholders; "
            "numeric estimates serialized as decimal strings."
        ),
        "result": result,
        "stdout": _portable(completed.stdout, replacements),
        "stderr": _portable(completed.stderr, replacements),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
