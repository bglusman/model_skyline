#!/usr/bin/env python3
"""Run the public routing screen through Jev or an OpenAI-compatible model.

Install the optional integration first:

    uv sync --extra structured-decisions

The output is a prompt-free ``structured-decision-run`` artifact. The source
suite and candidate configuration remain separate, auditable inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, cast

from model_skyline.adapters.structured_decisions import (
    structured_decision_case_set_sha256,
)
from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, canonical_bytes


class ChoiceAnswer(Protocol):
    choice: str
    probabilities: dict[str, float]


class Response(Protocol):
    choices: dict[str, ChoiceAnswer]
    usage: Any
    debug: dict[str, Any]


class Client(Protocol):
    def system_one(
        self,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, Any],
    ) -> Response: ...


class ClientContext(AbstractContextManager[Client]):
    """Type-only common context for the two third-party client classes."""

    def __enter__(self) -> Client:
        raise NotImplementedError

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        raise NotImplementedError


def _load_object(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value, raw


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _decimal(value: Decimal, *, decimal_places: int) -> str:
    with localcontext(POLICY_DECIMAL_CONTEXT):
        quantized = value.quantize(Decimal(1).scaleb(-decimal_places))
    return format(quantized, "f")


def _brier(probabilities: dict[str, float], expected: str, labels: set[str]) -> Decimal:
    if set(probabilities) != labels:
        raise ValueError("response probabilities do not cover the exact choice labels")
    with localcontext(POLICY_DECIMAL_CONTEXT):
        decimal_probabilities = {label: Decimal(str(probabilities[label])) for label in labels}
        if any(value < 0 or value > 1 for value in decimal_probabilities.values()):
            raise ValueError("response probabilities must be between zero and one")
        if abs(sum(decimal_probabilities.values(), Decimal(0)) - Decimal(1)) > Decimal("0.000001"):
            raise ValueError("response probabilities must sum to one")
        squared_error = sum(
            (decimal_probabilities[label] - (Decimal(1) if label == expected else Decimal(0))) ** 2
            for label in sorted(labels)
        )
        return squared_error / Decimal(len(labels))


def _api_key(backend: dict[str, Any]) -> str | None:
    environment_name = backend.get("api_key_env")
    if environment_name is None:
        return None
    if not isinstance(environment_name, str) or not environment_name:
        raise ValueError("backend.api_key_env must be null or a nonempty environment name")
    value = os.environ.get(environment_name)
    if not value:
        raise ValueError(f"required API key environment variable {environment_name!r} is unset")
    return value


def _client(candidate: dict[str, Any]) -> ClientContext:
    backend = cast(dict[str, Any], candidate["backend"])
    kind = backend["kind"]
    model = cast(str, backend["model"])
    if kind == "typesafe":
        from typesafe_sdk import TypeSafeClient

        return cast(
            ClientContext,
            TypeSafeClient(api_key=_api_key(backend), model=model),
        )
    if kind == "openai-compatible":
        import openai
        from system_one_adapter import SystemOneAdapterClient
        from system_one_adapter.providers import Message, ProviderResult
        from system_one_adapter.providers.base import render_messages, translating
        from system_one_adapter.providers.openai import OpenAIProvider

        class CappedOpenAICompatibleProvider:
            """Use the adapter's provider seam while pinning local generation bounds."""

            def __init__(self) -> None:
                self.model_name = model
                self._client = openai.OpenAI(
                    base_url=cast(str, backend["base_url"]),
                    api_key=_api_key(backend) or "local-no-secret",
                    max_retries=0,
                )

            @staticmethod
            def translate_error(error: Exception) -> Any:
                return OpenAIProvider.translate_error(error)

            def request(
                self,
                messages: list[Message],
                *,
                schema: dict[str, Any],
                structured: bool,
            ) -> ProviderResult:
                response_format: dict[str, Any] | None = None
                if structured:
                    response_format = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "evaluation",
                            "schema": schema,
                            "strict": True,
                        },
                    }
                with translating(self.translate_error):
                    response = self._client.chat.completions.create(
                        model=self.model_name,
                        messages=cast(Any, render_messages(messages)),
                        response_format=cast(Any, response_format),
                        max_tokens=int(backend["max_tokens"]),
                        temperature=float(backend.get("temperature", 0)),
                        extra_body={
                            "chat_template_kwargs": backend.get("chat_template_kwargs", {})
                        },
                    )
                usage = response.usage
                if usage is None:
                    raise ValueError("OpenAI-compatible response omitted token usage")
                return ProviderResult(
                    text=response.choices[0].message.content or "",
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens,
                )

        provider = CappedOpenAICompatibleProvider()
        return cast(
            ClientContext,
            SystemOneAdapterClient(
                structured_outputs=bool(backend.get("structured_outputs", False)),
                llm_answer_mode="probabilities",
                normalize_probabilities=False,
                n_retry_malformed_structure=0,
                model=provider,
            ),
        )
    raise ValueError("backend.kind must be typesafe or openai-compatible")


def _call_count(response: Response, *, backend_kind: str) -> int:
    if backend_kind == "typesafe":
        return 1
    attempts = response.debug.get("llm_attempts")
    if not isinstance(attempts, list) or not attempts:
        raise ValueError("adapter response did not expose nonempty llm_attempts")
    return len(attempts)


def run(suite_path: Path, candidate_path: Path, *, repetitions: int) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    suite, suite_raw = _load_object(suite_path, label="suite")
    candidate, candidate_raw = _load_object(candidate_path, label="candidate")
    question = cast(dict[str, Any], suite["question"])
    question_name = cast(str, question["name"])
    criteria = cast(dict[str, Any], question["criteria"])
    labels = set(criteria)
    backend = cast(dict[str, Any], candidate["backend"])
    component_id = cast(str, candidate["component_id"])
    resource_class = cast(str, candidate["resource_class"])
    cost_basis = cast(str, candidate["cost_basis"])
    fixed_cost_value = candidate.get("fixed_cost_usd_per_model_call")
    fixed_cost = Decimal(str(fixed_cost_value)) if fixed_cost_value is not None else None
    if (cost_basis == "unavailable") != (fixed_cost is None):
        raise ValueError(
            "cost_basis must be unavailable exactly when fixed_cost_usd_per_model_call is null"
        )

    results: list[dict[str, Any]] = []
    observed_at = datetime.now(UTC)
    questions = {
        question_name: {
            "type": "choice",
            "instructions": (f"{candidate['guidance']}\n\n{question['instructions']}"),
            "criteria": criteria,
        }
    }
    cases = cast(list[dict[str, Any]], suite["cases"])
    case_sha256_by_id: dict[str, str] = {}
    for case in cases:
        case_id = cast(str, case["case_id"])
        if case_id in case_sha256_by_id:
            raise ValueError(f"suite repeats case_id {case_id!r}")
        case_sha256_by_id[case_id] = _digest(
            {
                "case_id": case_id,
                "state": case["state"],
                "expected": case["expected"],
                "stratum": case["stratum"],
                "question": question,
            }
        )
    with _client(candidate) as client:
        for case in cases:
            expected = cast(str, case["expected"])
            if expected not in labels:
                raise ValueError(f"case {case['case_id']!r} has an unknown expected label")
            case_sha256 = case_sha256_by_id[case["case_id"]]
            for repetition in range(1, repetitions + 1):
                started = time.perf_counter()
                response = client.system_one(case["state"], questions)
                latency = Decimal(str(time.perf_counter() - started))
                answer = response.choices[question_name]
                calls = _call_count(response, backend_kind=cast(str, backend["kind"]))
                cost = fixed_cost * calls if fixed_cost is not None else None
                results.append(
                    {
                        "case_id": case["case_id"],
                        "case_sha256": case_sha256,
                        "repetition": repetition,
                        "decision_correct": answer.choice == expected,
                        "final_success": answer.choice == expected,
                        "schema_valid": True,
                        "abstained": answer.choice == "abstain",
                        "unsafe_action": (expected == "abstain" and answer.choice != "abstain"),
                        "latency_seconds": _decimal(latency, decimal_places=9),
                        "total_cost_usd": (
                            _decimal(cost, decimal_places=12) if cost is not None else None
                        ),
                        "model_calls": calls,
                        "heavy_model_calls": calls if resource_class == "heavy" else 0,
                        "component_usage": [
                            {
                                "component_id": component_id,
                                "calls": calls,
                                "cost_usd": (
                                    _decimal(cost, decimal_places=12) if cost is not None else None
                                ),
                            }
                        ],
                        "brier_score": _decimal(
                            _brier(answer.probabilities, expected, labels),
                            decimal_places=12,
                        ),
                        "tool_selection_correct": None,
                        "tool_arguments_correct": None,
                        "tool_sequence_correct": None,
                        "tool_policy_compliant": None,
                        "tool_side_effects_correct": None,
                    }
                )

    offering = cast(dict[str, Any], candidate["offering"])
    return {
        "schema_version": "model-skyline/structured-decision-run/v1alpha1",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "workload_id": "structured-routing-screen-v1",
        "workload_unit": "decision",
        "workload": {
            "suite_id": suite["suite_id"],
            "suite_version": suite["suite_version"],
            "case_manifest_sha256": hashlib.sha256(suite_raw).hexdigest(),
            "case_set_sha256": structured_decision_case_set_sha256(case_sha256_by_id.items()),
            "case_count": len(case_sha256_by_id),
            "harness_id": "model-skyline/system-one-screen",
            "harness_version": "1",
            "scorer_version": "normalized-multiclass-brier-v1",
            "oracle_kind": "deterministic",
            "repetitions_per_case": repetitions,
            "concurrency": 1,
        },
        "benchmark_source": {
            "id": suite["suite_id"],
            "version": suite["suite_version"],
            "url": "https://github.com/bglusman/model_skyline",
            "license": suite["license"],
            "methodology": suite["methodology"],
            "raw_sha256": hashlib.sha256(suite_raw).hexdigest(),
            "retrieved_at": observed_at.isoformat().replace("+00:00", "Z"),
        },
        "offering": offering,
        "system": {
            "kind": "decision_component",
            "routing_policy_sha256": _digest(candidate["routing_policy"]),
            "components": [
                {
                    "component_id": component_id,
                    "role": "decision",
                    "resource_class": resource_class,
                    "activation": "always",
                    "offering": offering,
                    "guidance_sha256": _digest(candidate["guidance"]),
                }
            ],
        },
        "cost_basis": cost_basis,
        "results": results,
        "metadata": {
            "suite_license": suite["license"],
            "suite_methodology": suite["methodology"],
            "backend_kind": backend["kind"],
            "candidate_configuration_sha256": hashlib.sha256(candidate_raw).hexdigest(),
            "measurement_conditions": candidate.get("measurement_conditions", {}),
            "input_tokens_total": None,
            "output_tokens_total": None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.suite, args.candidate, repetitions=args.repetitions)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
