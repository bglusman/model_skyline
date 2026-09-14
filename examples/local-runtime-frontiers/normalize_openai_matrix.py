#!/usr/bin/env python3
"""Normalize one OpenAI-compatible matrix capture into exact local records."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from model_skyline.canonical import content_hash
from model_skyline.io import dump_json
from model_skyline.local_measurements import (
    LocalArtifactIdentity,
    LocalHardwareIdentity,
    LocalMeasurementRecord,
    LocalRuntimeIdentity,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise ValueError(f"{field} must be a JSON number")
    return Decimal(value)


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a JSON integer")
    return value


def _cached_tokens(row: dict[str, Any], *, cache_enabled: bool) -> int:
    usage = row.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("each result must contain an OpenAI usage object")
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict) or "cached_tokens" not in details:
        if cache_enabled:
            raise ValueError("cache-enabled captures must report cached_tokens")
        return 0
    cached = _integer(details["cached_tokens"], field="cached_tokens")
    if cached < 0:
        raise ValueError("cached_tokens cannot be negative")
    if not cache_enabled and cached:
        raise ValueError("cache-disabled capture reported cached prompt tokens")
    return cached


def _cache_state(row: dict[str, Any], *, cache_enabled: bool) -> str:
    if not cache_enabled:
        return "disabled"
    return "warm" if _cached_tokens(row, cache_enabled=True) else "miss"


def _usage_int(row: dict[str, Any], *names: str) -> int:
    usage = row["usage"]
    for name in names:
        if name in usage:
            return _integer(usage[name], field=name)
    raise ValueError(f"usage is missing {' or '.join(names)}")


def _metric(
    rows: list[dict[str, Any]],
    *,
    row_field: str | None = None,
    usage_field: str | None = None,
) -> list[Decimal] | None:
    values: list[Decimal] = []
    for row in rows:
        source = row if row_field is not None else row["usage"]
        field = row_field if row_field is not None else usage_field
        assert field is not None
        value = source.get(field)
        if value is None:
            return None
        values.append(_decimal(value, field=field))
    return values


def _timing_metrics(
    rows: list[dict[str, Any]], *, mode: str
) -> tuple[dict[str, dict[str, Any]], str, str]:
    metrics: dict[str, dict[str, Any]] = {
        "time_to_first_semantic_event_seconds": {
            "unit": "s",
            "values": _metric(rows, row_field="ttft_seconds"),
        },
        "end_to_end_seconds": {
            "unit": "s",
            "values": _metric(rows, row_field="end_to_end_seconds"),
        },
    }
    server_ttft = _metric(rows, usage_field="time_to_first_token")
    ttft_source = "unavailable"
    if server_ttft is not None:
        metrics["time_to_first_token_seconds"] = {
            "unit": "s",
            "values": server_ttft,
        }
        ttft_source = "server-reported"
    elif mode != "tool":
        client_ttft = _metric(rows, row_field="ttft_seconds")
        if client_ttft is not None:
            metrics["time_to_first_token_seconds"] = {
                "unit": "s",
                "values": client_ttft,
            }
            ttft_source = "client-first-streamed-text-event"

    decode_rate = _metric(rows, usage_field="generation_tokens_per_second")
    decode_rate_source = "server-reported" if decode_rate is not None else "unavailable"
    if decode_rate is None and mode != "tool":
        decode_rate = _metric(rows, row_field="decode_tokens_per_second")
        if decode_rate is not None:
            decode_rate_source = "client-post-first-token-inter-token-rate"
    if decode_rate is not None:
        metrics["decode_tokens_per_second"] = {
            "unit": "token/s",
            "values": decode_rate,
        }
    prompt_rate = _metric(rows, usage_field="prompt_tokens_per_second")
    if prompt_rate is not None:
        metrics["prompt_tokens_per_second"] = {
            "unit": "token/s",
            "values": prompt_rate,
        }
    return metrics, ttft_source, decode_rate_source


def _tool_integrity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    parse_passes = 0
    correct_passes = 0
    for row in rows:
        calls = row.get("tool_calls")
        parsed = (
            isinstance(calls, list)
            and len(calls) == 1
            and calls[0].get("parse_error") is None
            and isinstance(calls[0].get("arguments"), dict)
        )
        parse_passes += int(parsed)
        correct_passes += int(row.get("tool_correct") is True)
    total = len(rows)
    return {
        "retrieval": None,
        "tool_calls": {"passed": correct_passes, "total": total},
        "tool_argument_parsing": {"passed": parse_passes, "total": total},
        "structured_output": None,
    }


def _retrieval_integrity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exact_passes = 0
    value_passes = 0
    for row in rows:
        exact = row.get("expected_content_exact")
        present = row.get("expected_content_present", exact)
        digest = row.get("expected_content_sha256")
        if not isinstance(exact, bool):
            raise ValueError("retrieval rows must report exact expected-content equality")
        if not isinstance(present, bool):
            raise ValueError("retrieval rows must report expected-content presence")
        if exact and not present:
            raise ValueError("an exact retrieval answer must contain the expected content")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("retrieval rows must report the expected-content SHA-256")
        exact_passes += int(exact)
        value_passes += int(present)
    return {
        "retrieval": {"passed": exact_passes, "total": len(rows)},
        "retrieval_value": {"passed": value_passes, "total": len(rows)},
        "tool_calls": None,
        "tool_argument_parsing": None,
        "structured_output": None,
    }


def _swap_deltas(rows: list[dict[str, Any]]) -> list[int] | None:
    values: list[int] = []
    for row in rows:
        before = row.get("host_before")
        after = row.get("host_after")
        if not isinstance(before, dict) or not isinstance(after, dict):
            return None
        before_swap = before.get("swap_used_bytes")
        after_swap = after.get("swap_used_bytes")
        if not isinstance(before_swap, int) or not isinstance(after_swap, int):
            return None
        values.append(after_swap - before_swap)
    return values


def _find_speculation(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        speculation = value.get("speculation")
        if isinstance(speculation, dict) and isinstance(speculation.get("last"), dict):
            return speculation
        for child in value.values():
            found = _find_speculation(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_speculation(child)
            if found is not None:
                return found
    return None


def _speculative_acceptance(rows: list[dict[str, Any]]) -> list[Decimal] | None:
    values: list[Decimal] = []
    for row in rows:
        before = _find_speculation(row.get("runtime_stats_before"))
        after = _find_speculation(row.get("runtime_stats_after"))
        if after is None:
            return None
        last = after["last"]
        totals = after.get("totals")
        if not isinstance(totals, dict):
            raise ValueError("DFlash stats are missing cumulative request counters")
        after_requests = _integer(totals.get("requests"), field="DFlash requests after")
        before_requests = 0
        if before is not None:
            before_totals = before.get("totals")
            if not isinstance(before_totals, dict):
                raise ValueError("DFlash stats are missing pre-request counters")
            before_requests = _integer(
                before_totals.get("requests"), field="DFlash requests before"
            )
        if after_requests != before_requests + 1:
            raise ValueError(
                "DFlash request counter did not advance exactly once during the measured request"
            )
        if "acceptance_ratio" not in last:
            raise ValueError("DFlash stats are missing the per-request acceptance ratio")
        ratio = _decimal(last["acceptance_ratio"], field="acceptance_ratio")
        if ratio < 0 or ratio > 1:
            raise ValueError("acceptance_ratio must be between zero and one")
        values.append(ratio * 100)
    return values


def _load_profile(path: Path) -> tuple[str, LocalArtifactIdentity, LocalRuntimeIdentity, list[str]]:
    value = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "served_model",
        "artifact",
        "runtime",
        "capabilities",
    }:
        raise ValueError("system profile has an unsupported shape")
    if value["schema_version"] != "model-skyline/local-system-profile/v1":
        raise ValueError("system profile has an unsupported schema_version")
    if not isinstance(value["served_model"], str):
        raise ValueError("served_model must be a string")
    if not isinstance(value["capabilities"], list):
        raise ValueError("capabilities must be an array")
    return (
        value["served_model"],
        LocalArtifactIdentity.model_validate(value["artifact"]),
        LocalRuntimeIdentity.model_validate(value["runtime"]),
        value["capabilities"],
    )


def _configured_max_output(runtime: LocalRuntimeIdentity) -> int | None:
    value = runtime.configuration.get("max_output_tokens")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("runtime configuration max_output_tokens must be a positive integer")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--hardware", type=Path, required=True)
    parser.add_argument("--system-profile", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--raw-artifact-path", required=True)
    parser.add_argument("--measurement-prefix", required=True)
    parser.add_argument(
        "--kind",
        choices=("agent_integration", "prefix_cache", "long_context_retrieval"),
        default="agent_integration",
    )
    args = parser.parse_args()

    capture = json.loads(args.capture.read_text(encoding="utf-8"), parse_float=Decimal)
    hardware = LocalHardwareIdentity.model_validate_json(args.hardware.read_text(encoding="utf-8"))
    served_model, artifact, runtime, capabilities = _load_profile(args.system_profile)
    if capture.get("schema_version") != "model-skyline/raw-openai-matrix/v1":
        parser.error("unsupported capture schema_version")
    if capture.get("model") != served_model:
        parser.error("capture model does not match the system profile")
    mode = capture.get("mode")
    if mode not in {"prose", "code", "tool", "retrieval"}:
        parser.error("capture mode is invalid")
    if mode == "retrieval" and args.kind != "long_context_retrieval":
        parser.error("retrieval captures require --kind long_context_retrieval")
    rows = capture.get("results")
    if not isinstance(rows, list) or not rows:
        parser.error("capture results must be a non-empty array")
    if any(row.get("loading_state_events") != 0 for row in rows):
        parser.error("capture contains llama-swap loading content and is not valid evidence")

    grouped: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    try:
        configured_maximum = _configured_max_output(runtime)
        for row in rows:
            approximate = _integer(
                row.get("approximate_prefix_tokens"), field="approximate_prefix_tokens"
            )
            maximum = _integer(row.get("max_output_tokens"), field="max_output_tokens")
            if configured_maximum is not None and maximum > configured_maximum:
                raise ValueError(
                    f"requested max_output_tokens {maximum} exceeds runtime ceiling "
                    f"{configured_maximum}"
                )
            state = _cache_state(row, cache_enabled=runtime.prefix_cache_enabled)
            grouped[(approximate, maximum, state)].append(row)
    except ValueError as exc:
        parser.error(str(exc))

    invocation_identity: dict[str, Any] = {
        "tool": "model-skyline/openai-matrix@v1",
        "base_url": capture.get("base_url"),
        "model": served_model,
        "mode": mode,
        "system_prompt_sha256": capture.get("system_prompt_sha256"),
        "tool_schema_sha256": capture.get("tool_schema_sha256"),
        "tool_count": capture.get("tool_count", 0),
        "tool_choice": capture.get("tool_choice"),
        "thinking_mode": capture.get("thinking_mode", "runtime_default"),
        "sampling": capture.get("sampling", {"temperature": 0, "seed": 90421}),
        "repetitions": capture.get("repetitions"),
        "warmup": capture.get("warmup"),
        "positions": sorted([list(key) for key in grouped]),
    }
    if mode == "retrieval":
        invocation_identity["retrieval_position"] = capture.get("retrieval_position")
    invocation_sha256 = content_hash(invocation_identity)
    written: list[Path] = []
    for (approximate, maximum, state), group in sorted(grouped.items()):
        try:
            input_counts = {_usage_int(row, "input_tokens", "prompt_tokens") for row in group}
            if len(input_counts) != 1:
                raise ValueError("a normalized position has inconsistent input token counts")
            construction_counts = {row.get("construction_input_tokens") for row in group}
            if len(construction_counts) != 1:
                raise ValueError("a normalized position has inconsistent construction token counts")
            prompt_hashes = {row.get("prompt_sha256") for row in group}
            input_hashes = {
                row.get("input_definition_sha256", row.get("prompt_sha256")) for row in group
            }
            prompt_bytes = {row.get("prompt_bytes") for row in group}
            if len(prompt_hashes) != 1 or len(input_hashes) != 1 or len(prompt_bytes) != 1:
                raise ValueError("a normalized position has inconsistent prompt identity")
            output_counts = [_usage_int(row, "output_tokens", "completion_tokens") for row in group]
            metrics, ttft_source, decode_rate_source = _timing_metrics(group, mode=mode)
            if capture.get("runner_state") in {"cold_model_load", "post_idle_expiry"}:
                ready = _metric(group, row_field="runner_ready_seconds")
                if ready is not None:
                    metrics["cold_load_seconds"] = {"unit": "s", "values": ready}
            peak_rss = [row.get("peak_process_rss_bytes") for row in group]
            if all(isinstance(value, int) for value in peak_rss):
                metrics["peak_process_rss_bytes"] = {
                    "unit": "byte",
                    "values": peak_rss,
                }
            peak_footprint = [row.get("peak_process_physical_footprint_bytes") for row in group]
            if all(isinstance(value, int) for value in peak_footprint):
                metrics["peak_process_physical_footprint_bytes"] = {
                    "unit": "byte",
                    "values": peak_footprint,
                }
            peak_runtime_memory = [row.get("peak_runtime_memory_bytes") for row in group]
            if all(isinstance(value, int) for value in peak_runtime_memory):
                metrics["peak_metal_active_bytes"] = {
                    "unit": "byte",
                    "values": peak_runtime_memory,
                }
            swap_deltas = _swap_deltas(group)
            if swap_deltas is not None:
                metrics["swap_delta_bytes"] = {
                    "unit": "byte",
                    "values": swap_deltas,
                }
            acceptance = _speculative_acceptance(group)
            if acceptance is not None:
                metrics["speculative_acceptance_percent"] = {
                    "unit": "percent",
                    "values": acceptance,
                }
            if runtime.prefix_cache_enabled:
                metrics["prefix_cache_hit_tokens"] = {
                    "unit": "token",
                    "values": [_cached_tokens(row, cache_enabled=True) for row in group],
                }
            if mode == "tool":
                integrity = _tool_integrity(group)
            elif mode == "retrieval":
                integrity = _retrieval_integrity(group)
            else:
                integrity = None
            retrieval_position = capture.get("retrieval_position")
            reference_suffix = (
                f"-needle-{str(retrieval_position).replace('.', 'p')}"
                if mode == "retrieval"
                else ""
            )
            identifier = (
                f"{args.measurement_prefix}-p{approximate}-o{maximum}-{state}{reference_suffix}"
            )
            position = {
                "mode": mode,
                "prompt_bytes": next(iter(prompt_bytes)),
                "user_prompt_sha256": next(iter(prompt_hashes)),
                "system_prompt_sha256": capture.get("system_prompt_sha256"),
                "tool_schema_sha256": capture.get("tool_schema_sha256"),
                "tool_count": capture.get("tool_count", 0),
                "tool_choice": capture.get("tool_choice"),
                "thinking_mode": capture.get("thinking_mode", "runtime_default"),
                "sampling": capture.get("sampling", {"temperature": 0, "seed": 90421}),
                "time_to_first_token_source": ttft_source,
                "decode_rate_source": decode_rate_source,
                "token_count_url": capture.get("token_count_url"),
                "construction_input_tokens": next(iter(construction_counts)),
                "finish_reasons": dict(
                    sorted(
                        Counter(
                            reason
                            for row in group
                            for reason in row.get("finish_reasons", [])
                            if isinstance(reason, str)
                        ).items()
                    )
                ),
                "api": "openai-chat-completions-streaming",
            }
            if mode == "retrieval":
                position["retrieval_character_fraction"] = retrieval_position
            record = LocalMeasurementRecord.model_validate(
                {
                    "schema_version": "model-skyline/local-measurement/v1alpha1",
                    "measurement_id": identifier,
                    "status": "provisional",
                    "started_at": capture["started_at"],
                    "completed_at": capture["captured_at"],
                    "hardware": hardware.model_dump(mode="json"),
                    "artifact": artifact.model_dump(mode="json"),
                    "runtime": runtime.model_dump(mode="json"),
                    "workload": {
                        "reference": {
                            "id": (
                                f"openai-{mode}-p{approximate}-o{maximum}-{state}{reference_suffix}"
                            ),
                            "version": "model-skyline/openai-matrix@v1",
                            "unit": "request",
                        },
                        "kind": args.kind,
                        "input_definition_sha256": next(iter(input_hashes)),
                        "requested_input_tokens": approximate,
                        "max_output_tokens": maximum,
                        "repetitions": len(group),
                        "warmup_repetitions": int(capture.get("warmup") is True),
                        "concurrency": 1,
                        "runner_state": capture.get("runner_state", "warm"),
                        "prefix_cache_state": state,
                        "position": position,
                    },
                    "performance": {
                        "actual_input_tokens": next(iter(input_counts)),
                        "output_token_counts": output_counts,
                        "metrics": metrics,
                    },
                    "integrity": integrity,
                    "capabilities": capabilities,
                    "provenance": {
                        "tool": "model-skyline/openai-matrix",
                        "tool_version": "v1",
                        "command_sha256": invocation_sha256,
                        "raw_artifact_path": args.raw_artifact_path,
                        "raw_sha256": _sha256(args.capture),
                        "captured_at": capture["captured_at"],
                        "methodology": (
                            "Serial streaming OpenAI-compatible requests at one exact "
                            "prompt/output/mode/cache position; client TTFT, completion "
                            "latency, usage, output digest, and tool parsing retained."
                        ),
                        "source_url": None,
                        "license": "CC0-1.0",
                    },
                    "notes": "Provisional operational runtime evidence; no model-quality claim.",
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            parser.error(f"cannot normalize position {approximate}/{maximum}/{state}: {exc}")
        target = args.output_directory / f"{identifier}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dump_json(record), encoding="utf-8")
        written.append(target)

    for target in written:
        print(target)


if __name__ == "__main__":
    main()
