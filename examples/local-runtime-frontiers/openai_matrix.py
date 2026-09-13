#!/usr/bin/env python3
"""Measure streaming TTFT, decode rate, cache warmth, and tool correctness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

SYSTEM_PROMPT = "You are a deterministic local runtime measurement probe."


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _positive_csv(value: str) -> list[int]:
    try:
        values = [int(item) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("values must be positive")
    return values


def _fraction(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except ArithmeticError as exc:
        raise argparse.ArgumentTypeError("expected a decimal fraction") from exc
    if not parsed.is_finite() or parsed <= 0 or parsed >= 1:
        raise argparse.ArgumentTypeError("fraction must be greater than zero and less than one")
    return parsed


def _prefix(mode: str, approximate_tokens: int) -> str:
    if mode == "code":
        atom = "def stable_prefix(value: int) -> int:\n    return value * 17 + 3\n\n"
    else:
        atom = "Stable prefix datum: amber cedar delta seven; retain exactly.\n"
    target_characters = approximate_tokens * 4
    repeated = (atom * ((target_characters // len(atom)) + 1))[:target_characters]
    if mode == "tool":
        instruction = (
            "\nCall lookup_fixture exactly once with key='skyline-cache-probe' and limit=7. "
            "Do not answer in prose."
        )
    elif mode == "code":
        instruction = "\nReturn one short Python function named final_probe."
    else:
        instruction = "\nReply with a concise two-sentence summary."
    return repeated + instruction


def _retrieval_prompt(approximate_tokens: int, position: Decimal) -> tuple[str, str]:
    position_label = str(position).replace(".", "p")
    expected = f"SKYLINE-PASSKEY-{approximate_tokens}-{position_label}-A7C9"
    header = (
        "A single archive record below has anchor ORION. At the final question, "
        "return only that record's exact passkey. Ignore every decoy_code.\n\n"
    )
    needle = f"\nTarget archive record: anchor=ORION; passkey={expected}.\n"
    footer = "\nQuestion: What is the exact passkey in the record whose anchor is ORION?\n"
    target_characters = approximate_tokens * 4
    filler_target = max(target_characters - len(header) - len(needle) - len(footer), 0)
    lines: list[str] = []
    characters = 0
    index = 0
    while characters < filler_target:
        digest = hashlib.sha256(f"skyline-distractor-{index}".encode()).hexdigest()
        line = (
            f"Archive {index:06d}: anchor={digest[:12]}; decoy_code={digest[12:36]}; "
            f"sequence={index * 104729 + 17}.\n"
        )
        lines.append(line)
        characters += len(line)
        index += 1
    filler = "".join(lines)[:filler_target]
    split = int(Decimal(len(filler)) * position)
    return header + filler[:split] + needle + filler[split:] + footer, expected


def _tool_definition() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "lookup_fixture",
                "description": "Return one deterministic local fixture.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["key", "limit"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return _sha256(encoded)


def _input_definition(prompt: str, mode: str) -> dict[str, Any]:
    """Return the complete semantic request prefix, excluding output controls."""
    value: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
    }
    if mode == "tool":
        value["tools"] = _tool_definition()
        value["tool_choice"] = {
            "type": "function",
            "function": {"name": "lookup_fixture"},
        }
    return value


def _command_output(arguments: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (completed.stdout + completed.stderr).strip()
    return output[:4096] if output else None


def _swap_used_bytes() -> int | None:
    output = _command_output(["sysctl", "-n", "vm.swapusage"])
    if output is None:
        return None
    match = re.search(r"\bused\s*=\s*([0-9.]+)([KMG])\b", output)
    if match is None:
        return None
    scale = {"K": 1024, "M": 1024**2, "G": 1024**3}[match.group(2)]
    return int(Decimal(match.group(1)) * scale)


def _host_state() -> dict[str, Any] | None:
    if sys.platform != "darwin":
        return None
    return {
        "swap_used_bytes": _swap_used_bytes(),
        "memory_pressure": _command_output(["memory_pressure", "-Q"]),
        "thermal_state": _command_output(["pmset", "-g", "therm"]),
        "power_source": _command_output(["pmset", "-g", "ps"]),
    }


def _runtime_stats(client: httpx.Client, url: str | None) -> dict[str, Any] | None:
    if url is None:
        return None
    try:
        response = client.get(url, timeout=10)
        response.raise_for_status()
        value = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        return {"capture_error": type(exc).__name__}
    if not isinstance(value, dict):
        return {"capture_error": "non_object_response"}
    return value


def _matching_process_rss_bytes(literal: str) -> int:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,rss=,command="],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    output = completed.stdout
    total_kib = 0
    for line in output.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3 or literal not in fields[2]:
            continue
        try:
            pid, rss_kib = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        if pid != os.getpid() and "openai_matrix.py" not in fields[2]:
            total_kib += rss_kib
    return total_kib * 1024


class _RssSampler:
    def __init__(self, process_match: str | None) -> None:
        self.process_match = process_match
        self.peak_bytes: int | None = None
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.process_match is None:
            return
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()

    def stop(self) -> int | None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=6)
        return self.peak_bytes

    def _sample(self) -> None:
        assert self.process_match is not None
        while not self._stopped.is_set():
            value = _matching_process_rss_bytes(self.process_match)
            self.peak_bytes = value if self.peak_bytes is None else max(self.peak_bytes, value)
            self._stopped.wait(0.25)
        value = _matching_process_rss_bytes(self.process_match)
        self.peak_bytes = value if self.peak_bytes is None else max(self.peak_bytes, value)


class _RunnerStateSampler:
    def __init__(self, url: str | None, model_id: str | None) -> None:
        self.url = url
        self.model_id = model_id
        self.events: list[dict[str, Any]] = []
        self.ready_seconds: float | None = None
        self._started_ns = 0
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, started_ns: int) -> None:
        if self.url is None:
            return
        self._started_ns = started_ns
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()

    def stop(self) -> tuple[list[dict[str, Any]], float | None]:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=6)
        return self.events, self.ready_seconds

    def _sample(self) -> None:
        assert self.url is not None
        previous: tuple[tuple[str, str], ...] | None = None
        with httpx.Client(timeout=2) as client:
            while not self._stopped.is_set():
                sampled_ns = time.monotonic_ns()
                try:
                    response = client.get(self.url)
                    response.raise_for_status()
                    payload = response.json()
                    running = payload.get("running") if isinstance(payload, dict) else None
                    if not isinstance(running, list):
                        raise ValueError("running response is not an array")
                    snapshot = tuple(
                        sorted(
                            (item["model"], item["state"])
                            for item in running
                            if isinstance(item, dict)
                            and isinstance(item.get("model"), str)
                            and isinstance(item.get("state"), str)
                            and (self.model_id is None or item["model"] == self.model_id)
                        )
                    )
                except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError):
                    snapshot = (("capture", "error"),)
                elapsed = (sampled_ns - self._started_ns) / 1_000_000_000
                if snapshot != previous:
                    self.events.append(
                        {
                            "elapsed_seconds": elapsed,
                            "models": [
                                {"model": model, "state": state} for model, state in snapshot
                            ],
                        }
                    )
                    previous = snapshot
                if self.ready_seconds is None and any(state == "ready" for _, state in snapshot):
                    self.ready_seconds = elapsed
                self._stopped.wait(0.05)


def _semantic_delta(delta: dict[str, Any]) -> str:
    for key in ("content", "reasoning_content", "reasoning"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            return value
    calls = delta.get("tool_calls")
    return "tool_call" if isinstance(calls, list) and calls else ""


def _stream_request(
    client: httpx.Client,
    *,
    endpoint: str,
    model: str,
    prompt: str,
    mode: str,
    max_tokens: int,
    process_match: str | None,
    runtime_stats_url: str | None,
    expected_content: str | None,
    runner_status_url: str | None,
    runner_model_id: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        **_input_definition(prompt, mode),
        "max_tokens": max_tokens,
        "temperature": 0,
        "seed": 90421,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if mode == "tool":
        payload["tools"] = _tool_definition()
        payload["tool_choice"] = {"type": "function", "function": {"name": "lookup_fixture"}}

    runtime_stats_before = _runtime_stats(client, runtime_stats_url)
    host_before = _host_state()
    rss_sampler = _RssSampler(process_match)
    rss_sampler.start()
    started_ns = time.monotonic_ns()
    runner_sampler = _RunnerStateSampler(runner_status_url, runner_model_id)
    runner_sampler.start(started_ns)
    first_semantic_ns: int | None = None
    content_parts: list[str] = []
    tool_names: dict[int, str] = {}
    tool_arguments: dict[int, str] = {}
    usage: dict[str, Any] = {}
    finish_reasons: list[str] = []
    loading_state_events = 0
    try:
        with client.stream("POST", endpoint, json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                event = json.loads(data)
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                for choice in event.get("choices") or ():
                    delta = choice.get("delta") or {}
                    semantic = _semantic_delta(delta)
                    if semantic and first_semantic_ns is None:
                        first_semantic_ns = time.monotonic_ns()
                    content = delta.get("content")
                    if isinstance(content, str):
                        if "llama-swap loading model:" in content:
                            loading_state_events += 1
                        else:
                            content_parts.append(content)
                    for call in delta.get("tool_calls") or ():
                        index = int(call.get("index", 0))
                        function = call.get("function") or {}
                        if function.get("name"):
                            tool_names[index] = tool_names.get(index, "") + function["name"]
                        if function.get("arguments"):
                            tool_arguments[index] = (
                                tool_arguments.get(index, "") + function["arguments"]
                            )
                    if choice.get("finish_reason"):
                        finish_reasons.append(choice["finish_reason"])
        completed_ns = time.monotonic_ns()
    finally:
        runner_state_events, runner_ready_seconds = runner_sampler.stop()
        peak_process_rss_bytes = rss_sampler.stop()
        host_after = _host_state()
        runtime_stats_after = _runtime_stats(client, runtime_stats_url)
    if first_semantic_ns is None:
        raise RuntimeError("stream completed without a semantic content or tool-call delta")

    parsed_calls: list[dict[str, Any]] = []
    for index in sorted(set(tool_names) | set(tool_arguments)):
        raw_arguments = tool_arguments.get(index, "")
        try:
            arguments = json.loads(raw_arguments)
            parse_error = None
        except json.JSONDecodeError as exc:
            arguments = None
            parse_error = str(exc)
        parsed_calls.append(
            {
                "name": tool_names.get(index),
                "arguments": arguments,
                "arguments_sha256": _sha256(raw_arguments.encode()),
                "parse_error": parse_error,
            }
        )
    tool_correct = None
    if mode == "tool":
        tool_correct = parsed_calls == [
            {
                "name": "lookup_fixture",
                "arguments": {"key": "skyline-cache-probe", "limit": 7},
                "arguments_sha256": parsed_calls[0]["arguments_sha256"] if parsed_calls else "",
                "parse_error": None,
            }
        ]

    ttft_seconds = (first_semantic_ns - started_ns) / 1_000_000_000
    end_to_end_seconds = (completed_ns - started_ns) / 1_000_000_000
    completion_tokens = usage.get("completion_tokens")
    decode_seconds = end_to_end_seconds - ttft_seconds
    decode_tokens_per_second = (
        completion_tokens / decode_seconds
        if isinstance(completion_tokens, int) and completion_tokens > 0 and decode_seconds > 0
        else None
    )
    content = "".join(content_parts)
    return {
        "ttft_seconds": ttft_seconds,
        "end_to_end_seconds": end_to_end_seconds,
        "decode_tokens_per_second": decode_tokens_per_second,
        "usage": usage,
        "finish_reasons": finish_reasons,
        "content_sha256": _sha256(content.encode()),
        "content_bytes": len(content.encode()),
        "expected_content_exact": content.strip() == expected_content if expected_content else None,
        "tool_calls": parsed_calls,
        "tool_correct": tool_correct,
        "loading_state_events": loading_state_events,
        "peak_process_rss_bytes": peak_process_rss_bytes,
        "host_before": host_before,
        "host_after": host_after,
        "runtime_stats_before": runtime_stats_before,
        "runtime_stats_after": runtime_stats_after,
        "runner_state_events": runner_state_events,
        "runner_ready_seconds": runner_ready_seconds,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8090/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--mode",
        choices=("prose", "code", "tool", "retrieval"),
        default="prose",
    )
    parser.add_argument(
        "--retrieval-position",
        type=_fraction,
        default=Decimal("0.5"),
        help="fractional character position for the retrieval needle",
    )
    parser.add_argument("--prefix-tokens", type=_positive_csv, default=[512, 2048, 8192, 32768])
    parser.add_argument("--max-outputs", type=_positive_csv, default=[64, 256, 1024])
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument(
        "--runner-state",
        choices=("warm", "cold_model_load", "post_idle_expiry"),
        default="warm",
    )
    parser.add_argument(
        "--process-match",
        help="literal process-command substring for optional peak RSS sampling",
    )
    parser.add_argument(
        "--runtime-stats-url",
        help="optional loopback JSON endpoint sampled before and after every request",
    )
    parser.add_argument(
        "--runner-status-url",
        help="optional loopback llama-swap /running endpoint sampled during requests",
    )
    parser.add_argument(
        "--runner-model-id",
        help="optional real llama-swap model ID used to filter /running transitions",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("--base-url must be an HTTP loopback endpoint")
    if args.runtime_stats_url is not None:
        stats_url = urlparse(args.runtime_stats_url)
        if stats_url.scheme != "http" or stats_url.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            parser.error("--runtime-stats-url must be an HTTP loopback endpoint")
    if args.runner_status_url is not None:
        status_url = urlparse(args.runner_status_url)
        if status_url.scheme != "http" or status_url.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            parser.error("--runner-status-url must be an HTTP loopback endpoint")
    if args.repetitions <= 0:
        parser.error("--repetitions must be positive")
    if args.runner_state != "warm" and (args.warmup or args.repetitions != 1):
        parser.error("cold/post-idle captures require no warmup and exactly one repetition")

    endpoint = args.base_url.rstrip("/") + "/chat/completions"
    results: list[dict[str, Any]] = []
    started_at = _timestamp()
    with httpx.Client(timeout=httpx.Timeout(900, connect=10)) as client:
        if args.warmup:
            _stream_request(
                client,
                endpoint=endpoint,
                model=args.model,
                prompt="Reply with the word warm.",
                mode="prose",
                max_tokens=8,
                process_match=args.process_match,
                runtime_stats_url=args.runtime_stats_url,
                expected_content=None,
                runner_status_url=args.runner_status_url,
                runner_model_id=args.runner_model_id,
            )
        for approximate_tokens in args.prefix_tokens:
            if args.mode == "retrieval":
                prompt, expected_content = _retrieval_prompt(
                    approximate_tokens, args.retrieval_position
                )
            else:
                prompt = _prefix(args.mode, approximate_tokens)
                expected_content = None
            prompt_sha256 = _sha256(prompt.encode())
            input_definition_sha256 = _canonical_sha256(_input_definition(prompt, args.mode))
            for max_tokens in args.max_outputs:
                for repetition in range(1, args.repetitions + 1):
                    result = _stream_request(
                        client,
                        endpoint=endpoint,
                        model=args.model,
                        prompt=prompt,
                        mode=args.mode,
                        max_tokens=max_tokens,
                        process_match=args.process_match,
                        runtime_stats_url=args.runtime_stats_url,
                        expected_content=expected_content,
                        runner_status_url=args.runner_status_url,
                        runner_model_id=args.runner_model_id,
                    )
                    results.append(
                        {
                            "approximate_prefix_tokens": approximate_tokens,
                            "prompt_bytes": len(prompt.encode()),
                            "prompt_sha256": prompt_sha256,
                            "input_definition_sha256": input_definition_sha256,
                            "expected_content_sha256": (
                                _sha256(expected_content.encode()) if expected_content else None
                            ),
                            "max_output_tokens": max_tokens,
                            "repetition": repetition,
                            **result,
                        }
                    )
    payload = {
        "schema_version": "model-skyline/raw-openai-matrix/v1",
        "started_at": started_at,
        "captured_at": _timestamp(),
        "base_url": args.base_url,
        "model": args.model,
        "mode": args.mode,
        "retrieval_position": (str(args.retrieval_position) if args.mode == "retrieval" else None),
        "repetitions": args.repetitions,
        "warmup": args.warmup,
        "runner_state": args.runner_state,
        "process_match": args.process_match,
        "runtime_stats_url": args.runtime_stats_url,
        "runner_status_url": args.runner_status_url,
        "runner_model_id": args.runner_model_id,
        "system_prompt_sha256": _sha256(SYSTEM_PROMPT.encode()),
        "tool_schema_sha256": (
            _canonical_sha256(_tool_definition()) if args.mode == "tool" else None
        ),
        "tool_count": len(_tool_definition()) if args.mode == "tool" else 0,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
