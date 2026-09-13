#!/usr/bin/env python3
"""Capture one reproducible llama-bench run as machine-readable evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _portable_result(value: Any, *, binary: Path, model: Path) -> Any:
    """Remove host-specific paths without changing benchmark values."""

    if isinstance(value, str):
        return value.replace(str(binary), "${LLAMA_BENCH}").replace(str(model), "${MODEL_FILE}")
    if isinstance(value, list):
        return [_portable_result(child, binary=binary, model=model) for child in value]
    if isinstance(value, dict):
        return {
            key: _portable_result(child, binary=binary, model=model) for key, child in value.items()
        }
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host-id", required=True)
    parser.add_argument("--prompt", type=int, default=2048)
    parser.add_argument("--generate", type=int, default=512)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--batch", type=int, default=2048)
    parser.add_argument("--ubatch", type=int, default=512)
    parser.add_argument("--kv", default="q8_0")
    parser.add_argument("--flash-attention", choices=("on", "off", "auto"), default="on")
    args = parser.parse_args()

    binary = args.binary.expanduser().resolve(strict=True)
    model = args.model.expanduser().resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error("--binary must be an executable regular file")
    if not model.is_file():
        parser.error("--model must be a regular file")
    positive = {
        "--prompt": args.prompt,
        "--generate": args.generate,
        "--repetitions": args.repetitions,
        "--threads": args.threads,
        "--batch": args.batch,
        "--ubatch": args.ubatch,
    }
    for option, value in positive.items():
        if value <= 0:
            parser.error(f"{option} must be positive")

    command = [
        str(binary),
        "-m",
        str(model),
        "-p",
        str(args.prompt),
        "-n",
        str(args.generate),
        "-r",
        str(args.repetitions),
        "-ngl",
        "99",
        "-fa",
        args.flash_attention,
        "-ctk",
        args.kv,
        "-ctv",
        args.kv,
        "-b",
        str(args.batch),
        "-ub",
        str(args.ubatch),
        "-t",
        str(args.threads),
        "-o",
        "json",
    ]
    command_template = [
        "${LLAMA_BENCH}" if item == str(binary) else "${MODEL_FILE}" if item == str(model) else item
        for item in command
    ]
    command_sha256 = hashlib.sha256(
        json.dumps(command_template, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    started_at = _timestamp()
    start = time.monotonic_ns()
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    elapsed_ns = time.monotonic_ns() - start
    completed_at = _timestamp()
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)
    try:
        results = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        sys.stderr.write(completed.stdout)
        raise SystemExit(f"llama-bench stdout was not JSON: {exc}") from exc

    payload = {
        "schema_version": "model-skyline/raw-llama-bench/v1",
        "captured_at": completed_at,
        "started_at": started_at,
        "elapsed_ns": elapsed_ns,
        "host": {
            "host_id": args.host_id,
            "machine": platform.machine(),
            "platform": platform.platform(),
        },
        "artifact": {
            "filename": model.name,
            "size_bytes": model.stat().st_size,
            "sha256": _sha256(model),
        },
        "runtime": {
            "filename": binary.name,
            "sha256": _sha256(binary),
        },
        "invocation": {
            "command": command_template,
            "command_sha256": command_sha256,
        },
        "normalization": "Absolute binary/model paths replaced with ${LLAMA_BENCH}/${MODEL_FILE}.",
        "results": _portable_result(results, binary=binary, model=model),
        "stderr": _portable_result(completed.stderr, binary=binary, model=model),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
