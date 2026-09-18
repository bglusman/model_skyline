#!/usr/bin/env python3
"""Measure Needle alone or behind a selective Jev exact-call guard.

Install the pinned local runtime first::

    uv sync --extra needle-pilot

The output is a prompt-free ``structured-decision-run`` artifact. The vendor
environment modules remain the inspectable source of utterances and reference
calls; this runner verifies their hashes against the committed manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from model_skyline.adapters.structured_decisions import structured_decision_case_set_sha256
from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, canonical_bytes


def _load(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value, raw


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decimal(value: Decimal | float, *, places: int) -> str:
    with localcontext(POLICY_DECIMAL_CONTEXT):
        quantized = Decimal(str(value)).quantize(Decimal(1).scaleb(-places))
    return format(quantized, "f")


def _equal_calls(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    def encode(call: dict[str, Any]) -> str:
        return json.dumps(call, sort_keys=True, separators=(",", ":"))

    return sorted(map(encode, left)) == sorted(map(encode, right))


def _selection_correct(got: list[dict[str, Any]], want: list[dict[str, Any]]) -> bool:
    got_names = [str(call.get("name")) for call in got]
    want_names = [str(call.get("name")) for call in want]
    return sorted(got_names) == sorted(want_names)


def _post_json(
    url: str, payload: dict[str, Any], *, api_key: str, timeout_seconds: float
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/bglusman/model_skyline",
            "X-OpenRouter-Title": "ModelSkyline Needle guard calibration",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            value = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:2000]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    if not isinstance(value, dict):
        raise ValueError("OpenRouter Decisions returned a non-object response")
    return value


def _review_with_jev(
    *,
    backend: dict[str, Any],
    guidance: str,
    query: str,
    tool_schemas: list[dict[str, Any]],
    proposed_calls: list[dict[str, Any]],
    confidence: float,
    validation: dict[str, Any],
) -> tuple[str, dict[str, float], Decimal, int, int, str | None, str | None]:
    key_name = cast(str, backend["api_key_env"])
    api_key = os.environ.get(key_name)
    if not api_key:
        raise ValueError(f"required API key environment variable {key_name!r} is unset")
    payload = {
        "model": backend["model"],
        "state": {
            "user_request": query,
            "available_tools": tool_schemas,
            "proposed_calls": proposed_calls,
            "proposal_confidence": confidence,
            "proposal_validation": validation,
        },
        "questions": {
            "verdict": {
                "type": "choice",
                "instructions": guidance,
                "criteria": {
                    "approve": "Execute the proposed calls exactly as written.",
                    "reject": (
                        "Execute no proposed call; repair or clarification is outside this guard."
                    ),
                },
            }
        },
    }
    response = _post_json(
        cast(str, backend["endpoint"]),
        payload,
        api_key=api_key,
        timeout_seconds=float(backend.get("timeout_seconds", 30)),
    )
    answers = response.get("answers")
    usage = response.get("usage")
    if not isinstance(answers, dict) or not isinstance(usage, dict):
        raise ValueError("OpenRouter Decisions response omitted answers or usage")
    answer = answers.get("verdict")
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ValueError("OpenRouter Decisions response omitted the verdict Choice")
    choice = answer.get("choice")
    probabilities = answer.get("probabilities")
    if choice not in {"approve", "reject"} or not isinstance(probabilities, dict):
        raise ValueError("OpenRouter Decisions returned a malformed verdict")
    normalized = {str(label): float(probability) for label, probability in probabilities.items()}
    if set(normalized) != {"approve", "reject"}:
        raise ValueError("OpenRouter Decisions verdict probabilities are incomplete")
    raw_cost = usage.get("cost")
    if raw_cost is None:
        raise ValueError("OpenRouter Decisions response omitted provider-reported cost")
    return (
        cast(str, choice),
        normalized,
        Decimal(str(raw_cost)),
        int(usage["input_tokens"]),
        int(usage["output_tokens"]),
        cast(str | None, response.get("model")),
        cast(str | None, response.get("provider")),
    )


def _brier(probabilities: dict[str, float], expected: str) -> str:
    with localcontext(POLICY_DECIMAL_CONTEXT):
        score = sum(
            (Decimal(str(probabilities[label])) - Decimal(label == expected)) ** 2
            for label in ("approve", "reject")
        ) / Decimal(2)
    return _decimal(score, places=12)


def run(
    manifest_path: Path,
    needle_candidate_path: Path,
    *,
    compound_candidate_path: Path | None,
    repetitions: int,
) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    os.environ.setdefault("NEEDLE_TELEMETRY", "0")
    os.environ.setdefault("DO_NOT_TRACK", "1")

    import needle  # type: ignore[import-untyped]
    from needle.agent import fetch  # type: ignore[import-untyped]
    from needle.environments import media_player, smart_home  # type: ignore[import-untyped]

    manifest, manifest_raw = _load(manifest_path)
    needle_candidate, needle_raw = _load(needle_candidate_path)
    compound: dict[str, Any] | None = None
    compound_raw: bytes | None = None
    if compound_candidate_path is not None:
        compound, compound_raw = _load(compound_candidate_path)

    package_version = importlib.metadata.version("cactus-needle")
    if package_version != "3.0.1" or manifest["suite_version"] != "cactus-needle==3.0.1":
        raise ValueError("this result-of-record runner requires cactus-needle==3.0.1")
    installed_artifacts = cast(dict[str, Any], manifest["installed_artifacts"])
    weights_path = Path(fetch.fetch_weights(3))
    runtime_path = Path(needle._library_path(3))
    for artifact_path, digest_key, bytes_key in (
        (weights_path, "weights_sha256", "weights_bytes"),
        (runtime_path, "macos_runtime_sha256", "macos_runtime_bytes"),
    ):
        if _file_sha256(artifact_path) != installed_artifacts[digest_key]:
            raise ValueError(f"installed {artifact_path.name} does not match the manifest")
        if artifact_path.stat().st_size != installed_artifacts[bytes_key]:
            raise ValueError(f"installed {artifact_path.name} size does not match the manifest")
    modules = {"media_player": media_player, "smart_home": smart_home}
    for name, module in modules.items():
        installed_hash = _file_sha256(Path(cast(str, module.__file__)))
        expected_hash = manifest["environments"][name]["module_sha256"]
        if installed_hash != expected_hash:
            raise ValueError(f"installed {name} module does not match the pinned manifest")
        if len(module.TEST_CASES) != manifest["environments"][name]["case_count"]:
            raise ValueError(f"installed {name} case count does not match the manifest")

    cases: list[tuple[str, Any, dict[str, Any]]] = []
    case_sha256_by_id: dict[str, str] = {}
    for environment, module in modules.items():
        tool_schemas = [cast(dict[str, Any], tool._needle_tool) for tool in module.TOOLS]
        source = {
            "environment": environment,
            "system": module.SYSTEM,
            "tools": tool_schemas,
        }
        for index, case in enumerate(module.TEST_CASES, start=1):
            case_id = f"{environment}/{index:02d}/{case['category']}"
            case_payload = {**source, "case": case}
            case_sha256_by_id[case_id] = _digest(case_payload)
            cases.append((case_id, module, case))

    observed_at = datetime.now(UTC)
    results: list[dict[str, Any]] = []
    resolved_models: set[str] = set()
    providers: set[str] = set()
    guard_input_tokens = 0
    guard_output_tokens = 0
    guard_decisions = {"approve": 0, "reject": 0}
    guard_correct = 0
    guard_calls = 0
    guard_brier_total = Decimal(0)
    needle_component_id = cast(str, needle_candidate["component_id"])
    guard_component_id = cast(str, compound["guard_component_id"]) if compound else None
    if compound is not None and compound["worker_component_id"] != needle_component_id:
        raise ValueError("compound worker_component_id must match the Needle candidate")

    agents: dict[str, Any] = {}
    for case_id, module, case in cases:
        environment = case_id.split("/", 1)[0]
        if environment not in agents:
            agents[environment] = needle.Needle(
                tools=module.TOOLS,
                system=module.SYSTEM,
                auto_date=False,
            )
        agent = agents[environment]
        tool_schemas = [cast(dict[str, Any], tool._needle_tool) for tool in module.TOOLS]
        want = cast(list[dict[str, Any]], case["calls"])
        for repetition in range(1, repetitions + 1):
            agent.reset()
            started = time.perf_counter()
            response = cast(dict[str, Any], agent.complete(case["query"]))
            proposed = cast(list[dict[str, Any]], response.get("function_calls") or [])
            validation = cast(dict[str, Any], response.get("validation") or {})
            if proposed and (validation.get("ungrounded") or validation.get("negation")):
                proposed = []
            primary_success = _equal_calls(proposed, want)
            final_calls = proposed
            guard_cost = Decimal(0)
            review_brier: str | None = None
            this_guard_calls = 0
            review_correct: bool | None = None
            if compound is not None and proposed:
                (
                    verdict,
                    probabilities,
                    guard_cost,
                    input_tokens,
                    output_tokens,
                    resolved_model,
                    provider,
                ) = _review_with_jev(
                    backend=cast(dict[str, Any], compound["guard_backend"]),
                    guidance=cast(str, compound["guard_guidance"]),
                    query=cast(str, case["query"]),
                    tool_schemas=tool_schemas,
                    proposed_calls=proposed,
                    confidence=float(response.get("confidence", 0)),
                    validation=validation,
                )
                expected_verdict = "approve" if primary_success else "reject"
                review_correct = verdict == expected_verdict
                review_brier = _brier(probabilities, expected_verdict)
                guard_brier_total += Decimal(review_brier)
                final_calls = proposed if verdict == "approve" else []
                this_guard_calls = 1
                guard_calls += 1
                guard_correct += int(review_correct)
                guard_decisions[verdict] += 1
                guard_input_tokens += input_tokens
                guard_output_tokens += output_tokens
                if resolved_model:
                    resolved_models.add(resolved_model)
                if provider:
                    providers.add(provider)
            latency = time.perf_counter() - started
            success = _equal_calls(final_calls, want)
            unsafe = not want and bool(final_calls)
            total_cost = guard_cost if compound is not None else None
            usages: list[dict[str, Any]] = [
                {
                    "component_id": needle_component_id,
                    "calls": 1,
                    "cost_usd": "0.000000000000" if compound is not None else None,
                }
            ]
            if compound is not None and guard_component_id is not None:
                usages.append(
                    {
                        "component_id": guard_component_id,
                        "calls": this_guard_calls,
                        "cost_usd": _decimal(guard_cost, places=12),
                    }
                )
            results.append(
                {
                    "case_id": case_id,
                    "case_sha256": case_sha256_by_id[case_id],
                    "repetition": repetition,
                    "decision_correct": success,
                    "final_success": success,
                    "schema_valid": True,
                    "abstained": not final_calls,
                    "unsafe_action": unsafe,
                    "latency_seconds": _decimal(latency, places=9),
                    "total_cost_usd": (
                        _decimal(total_cost, places=12) if total_cost is not None else None
                    ),
                    "model_calls": 1 + this_guard_calls,
                    "heavy_model_calls": 0,
                    "component_usage": usages,
                    "brier_score": None,
                    "primary_success": primary_success if compound is not None else None,
                    "tool_selection_correct": _selection_correct(final_calls, want),
                    "tool_arguments_correct": success,
                    "tool_sequence_correct": None,
                    "tool_policy_compliant": not unsafe,
                    "tool_side_effects_correct": None,
                }
            )

    workload = {
        "suite_id": manifest["suite_id"],
        "suite_version": manifest["suite_version"],
        "case_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "case_set_sha256": structured_decision_case_set_sha256(case_sha256_by_id.items()),
        "case_count": len(case_sha256_by_id),
        "harness_id": "model-skyline/needle-home-media-panel",
        "harness_version": "1",
        "scorer_version": "exact-unordered-tool-calls-after-vendor-validation-filter-v1",
        "oracle_kind": "deterministic",
        "repetitions_per_case": repetitions,
        "concurrency": 1,
    }
    needle_offering = cast(dict[str, Any], needle_candidate["offering"])
    if compound is None:
        offering = needle_offering
        system = {
            "kind": "single_model_system",
            "routing_policy_sha256": _digest(needle_candidate["guidance"]),
            "components": [
                {
                    "component_id": needle_component_id,
                    "role": "worker",
                    "resource_class": "light",
                    "activation": "always",
                    "offering": needle_offering,
                    "guidance_sha256": _digest(needle_candidate["guidance"]),
                }
            ],
        }
        cost_basis = "unavailable"
    else:
        offering = cast(dict[str, Any], compound["offering"])
        system = {
            "kind": "compound_model_system",
            "routing_policy_sha256": _digest(compound["routing_policy"]),
            "components": [
                {
                    "component_id": needle_component_id,
                    "role": "worker",
                    "resource_class": "light",
                    "activation": "always",
                    "offering": needle_offering,
                    "guidance_sha256": _digest(needle_candidate["guidance"]),
                },
                {
                    "component_id": guard_component_id,
                    "role": "guardrail",
                    "resource_class": "light",
                    "activation": "policy_trigger",
                    "offering": compound["guard_offering"],
                    "guidance_sha256": _digest(compound["guard_guidance"]),
                },
            ],
        }
        cost_basis = compound["cost_basis"]

    source = manifest["source"]
    metadata: dict[str, Any] = {
        "suite_license": manifest["license"],
        "suite_methodology": manifest["methodology"],
        "needle_candidate_configuration_sha256": hashlib.sha256(needle_raw).hexdigest(),
        "measurement_conditions": (
            compound["measurement_conditions"]
            if compound is not None
            else needle_candidate["measurement_conditions"]
        ),
        "source_revision": source["repository_revision"],
        "weights_sha256": manifest["installed_artifacts"]["weights_sha256"],
        "runner_sha256": _file_sha256(Path(__file__)),
    }
    if compound is not None and compound_raw is not None:
        metadata.update(
            {
                "compound_candidate_configuration_sha256": hashlib.sha256(compound_raw).hexdigest(),
                "guard_calls": guard_calls,
                "guard_correct_calls": guard_correct,
                "guard_mean_brier_score": _decimal(
                    guard_brier_total / guard_calls if guard_calls else Decimal(0),
                    places=12,
                ),
                "guard_decisions": guard_decisions,
                "guard_input_tokens": guard_input_tokens,
                "guard_output_tokens": guard_output_tokens,
                "provider_observations": {
                    "resolved_models": sorted(resolved_models),
                    "providers": sorted(providers),
                },
                "cost_scope": (
                    "OpenRouter guard charges only; local electricity and capital excluded"
                ),
            }
        )
    return {
        "schema_version": "model-skyline/structured-decision-run/v1alpha1",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "workload_id": "cactus-needle-v3-home-media-64",
        "workload_unit": "voice-style-tool-case",
        "workload": workload,
        "benchmark_source": {
            "id": manifest["suite_id"],
            "version": manifest["suite_version"],
            "url": source["url"],
            "license": manifest["license"],
            "methodology": manifest["methodology"],
            "raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "retrieved_at": source["retrieved_at"],
        },
        "offering": offering,
        "system": system,
        "cost_basis": cost_basis,
        "results": results,
        "metadata": metadata,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("needle_candidate", type=Path)
    parser.add_argument("--compound-candidate", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.manifest,
        args.needle_candidate,
        compound_candidate_path=args.compound_candidate,
        repetitions=args.repetitions,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
