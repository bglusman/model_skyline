#!/usr/bin/env python3
"""Run the public routing screen through one model or an explicit two-model cascade.

Install the optional integration first:

    uv sync --extra structured-decisions

The output is a prompt-free ``structured-decision-run`` artifact. The source
suite and candidate configurations remain separate, auditable inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import time
from collections.abc import Mapping
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, cast
from urllib.error import HTTPError
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from model_skyline.adapters.structured_decisions import (
    structured_decision_case_set_sha256,
)
from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, canonical_bytes


class ChoiceAnswer(Protocol):
    choice: str
    probabilities: dict[str, float]


class Response(Protocol):
    choices: Mapping[str, ChoiceAnswer]
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


@dataclass(frozen=True)
class _Choice:
    choice: str
    probabilities: dict[str, float]


@dataclass(frozen=True)
class _Usage:
    input_tokens: int
    output_tokens: int
    cost: Decimal | None = None


@dataclass(frozen=True)
class _Response:
    choices: dict[str, _Choice]
    usage: _Usage
    debug: dict[str, Any]


class _PlainClientContext(ClientContext):
    """Context adapter for stateless HTTP clients."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def __enter__(self) -> Client:
        return self._client

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: float,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = URLRequest(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            value = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{url} returned a non-object JSON response")
    return value


class _OpenRouterDecisionsClient:
    def __init__(self, backend: dict[str, Any], *, api_key: str) -> None:
        self._backend = backend
        self._api_key = api_key

    def system_one(
        self,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, Any],
    ) -> _Response:
        payload = {
            "model": self._backend["model"],
            "state": state,
            "questions": questions,
        }
        response = _post_json(
            cast(str, self._backend["endpoint"]),
            payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "HTTP-Referer": "https://github.com/bglusman/model_skyline",
                "X-OpenRouter-Title": "ModelSkyline structured-decision calibration",
            },
            timeout_seconds=float(self._backend.get("timeout_seconds", 30)),
        )
        raw_answers = response.get("answers")
        raw_usage = response.get("usage")
        if not isinstance(raw_answers, dict) or not isinstance(raw_usage, dict):
            raise ValueError("OpenRouter Decisions response omitted answers or usage")
        choices: dict[str, _Choice] = {}
        for name, raw_answer in raw_answers.items():
            if not isinstance(name, str) or not isinstance(raw_answer, dict):
                raise ValueError("OpenRouter Decisions returned a malformed answer")
            if raw_answer.get("type") != "choice":
                raise ValueError("this screen requires Choice answers")
            choice = raw_answer.get("choice")
            probabilities = raw_answer.get("probabilities")
            if not isinstance(choice, str) or not isinstance(probabilities, dict):
                raise ValueError("OpenRouter Choice answer omitted choice or probabilities")
            choices[name] = _Choice(
                choice=choice,
                probabilities={str(key): float(value) for key, value in probabilities.items()},
            )
        raw_cost = raw_usage.get("cost")
        return _Response(
            choices=choices,
            usage=_Usage(
                input_tokens=int(raw_usage["input_tokens"]),
                output_tokens=int(raw_usage["output_tokens"]),
                cost=Decimal(str(raw_cost)) if raw_cost is not None else None,
            ),
            debug={"provider": response.get("provider"), "resolved_model": response.get("model")},
        )


_OPTION_LETTERS = "ABCDEFGHIJKLMNOP"
_SEMIF_DIRECT_SYSTEM = (
    "Apply the supplied criterion to the supplied evidence. Choose exactly one listed option. "
    "Respond with only its uppercase letter, with no explanation or reasoning."
)


def _softmax(values: list[float]) -> list[float]:
    if len(values) < 2 or any(not math.isfinite(value) for value in values):
        raise ValueError("need at least two finite option log-probabilities")
    maximum = max(values)
    weights = [math.exp(value - maximum) for value in values]
    total = sum(weights)
    return [weight / total for weight in weights]


class _DirectOptionLogitsClient:
    """SemIf-style option-logit readout through a llama.cpp-compatible API."""

    def __init__(self, backend: dict[str, Any]) -> None:
        self._backend = backend

    def system_one(
        self,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, Any],
    ) -> _Response:
        if len(questions) != 1:
            raise ValueError("direct-option-logits currently accepts exactly one question")
        question_name, question = next(iter(questions.items()))
        if not isinstance(question, dict) or question.get("type") != "choice":
            raise ValueError("direct-option-logits requires one Choice question")
        criteria = question.get("criteria")
        instructions = question.get("instructions")
        if not isinstance(criteria, dict) or not isinstance(instructions, str):
            raise ValueError("Choice question omitted criteria or instructions")
        labels = _OPTION_LETTERS[: len(criteria)]
        if len(labels) != len(criteria) or len(labels) < 2:
            raise ValueError("direct-option-logits supports 2 to 16 options")
        option_ids = list(criteria)
        evidence = {
            "evidence": state,
            "criterion": instructions,
            "options": [
                {"letter": label, "description": criteria[option_id]}
                for label, option_id in zip(labels, option_ids, strict=True)
            ],
        }
        payload = {
            "model": self._backend["model"],
            "messages": [
                {"role": "system", "content": _SEMIF_DIRECT_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "max_tokens": 1,
            "temperature": 1,
            "top_k": 0,
            "top_p": 1,
            "logprobs": True,
            "top_logprobs": max(20, len(labels)),
            "grammar": "root ::= " + " | ".join(f'"{label}"' for label in labels),
            "chat_template_kwargs": {"enable_thinking": False},
        }
        base_url = cast(str, self._backend["base_url"]).rstrip("/")
        response = _post_json(
            f"{base_url}/chat/completions",
            payload,
            timeout_seconds=float(self._backend.get("timeout_seconds", 120)),
        )
        raw_choices = response.get("choices")
        raw_usage = response.get("usage")
        if not isinstance(raw_choices, list) or not raw_choices or not isinstance(raw_usage, dict):
            raise ValueError("direct-option-logits response omitted choices or usage")
        first_choice = raw_choices[0]
        if not isinstance(first_choice, dict):
            raise ValueError("direct-option-logits response returned a malformed choice")
        raw_logprobs = first_choice.get("logprobs")
        if not isinstance(raw_logprobs, dict):
            raise ValueError("backend did not return first-token log-probabilities")
        logprob_content = raw_logprobs.get("content", [])
        if not isinstance(logprob_content, list) or not logprob_content:
            raise ValueError("backend did not return first-token log-probabilities")
        raw_top = logprob_content[0].get("top_logprobs", {})
        if not isinstance(raw_top, list):
            raise ValueError("backend returned malformed top_logprobs")
        by_label: dict[str, float] = {}
        for item in raw_top:
            if not isinstance(item, dict):
                continue
            token = item.get("token")
            raw_bytes = item.get("bytes")
            for label in labels:
                if token == label or raw_bytes == [ord(label)]:
                    by_label[label] = float(item["logprob"])
        if set(by_label) != set(labels):
            missing = sorted(set(labels) - set(by_label))
            raise ValueError(f"backend omitted option log-probabilities for {missing}")
        option_probabilities = _softmax([by_label[label] for label in labels])
        probabilities = dict(zip(option_ids, option_probabilities, strict=True))
        choice = max(probabilities, key=probabilities.__getitem__)
        return _Response(
            choices={question_name: _Choice(choice=choice, probabilities=probabilities)},
            usage=_Usage(
                input_tokens=int(raw_usage["prompt_tokens"]),
                output_tokens=int(raw_usage["completion_tokens"]),
            ),
            debug={
                "llm_attempts": [{}],
                "prompt_version": self._backend.get("prompt_version"),
                "probability_status": (
                    "conditional option score; uncalibrated as decision confidence"
                ),
            },
        )


class _LayaClient:
    def __init__(self, backend: dict[str, Any]) -> None:
        laya = importlib.import_module("laya")
        huggingface_hub = importlib.import_module("huggingface_hub")

        model = cast(str, backend["model"])
        revision = cast(str, backend["revision"])
        model_dir = huggingface_hub.snapshot_download(repo_id=model, revision=revision)
        self._agent = laya.load(model_dir, device=cast(str, backend.get("device", "cpu")))
        self._model = model
        self._revision = revision

    def system_one(
        self,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, Any],
    ) -> _Response:
        response = self._agent.system_one(state, questions)
        raw_answers = response.get("answers")
        raw_usage = response.get("usage")
        if not isinstance(raw_answers, dict) or not isinstance(raw_usage, dict):
            raise ValueError("Laya response omitted answers or usage")
        choices: dict[str, _Choice] = {}
        for name, raw_answer in raw_answers.items():
            if not isinstance(name, str) or not isinstance(raw_answer, dict):
                raise ValueError("Laya returned a malformed answer")
            choice = raw_answer.get("choice")
            probabilities = raw_answer.get("probabilities")
            if not isinstance(choice, str) or not isinstance(probabilities, dict):
                raise ValueError("this screen requires Laya Choice answers")
            choices[name] = _Choice(
                choice=choice,
                probabilities={str(key): float(value) for key, value in probabilities.items()},
            )
        return _Response(
            choices=choices,
            usage=_Usage(
                input_tokens=int(raw_usage["input_tokens"]),
                output_tokens=int(raw_usage["output_tokens"]),
            ),
            debug={
                "resolved_model": self._model,
                "provider": "local-laya",
                "model_revision": self._revision,
            },
        )


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


def _normalized_probabilities(
    probabilities: Mapping[str, float | Decimal], labels: set[str]
) -> dict[str, Decimal]:
    if set(probabilities) != labels:
        raise ValueError("response probabilities do not cover the exact choice labels")
    with localcontext(POLICY_DECIMAL_CONTEXT):
        decimal_probabilities = {label: Decimal(str(probabilities[label])) for label in labels}
        if any(value < 0 or value > 1 for value in decimal_probabilities.values()):
            raise ValueError("response probabilities must be between zero and one")
        probability_sum = sum(decimal_probabilities.values(), Decimal(0))
        if probability_sum == 0 or abs(probability_sum - Decimal(1)) > Decimal("0.02"):
            raise ValueError(f"response probabilities must sum to one; got {probability_sum}")
        return {label: value / probability_sum for label, value in decimal_probabilities.items()}


def _brier(
    probabilities: Mapping[str, float | Decimal], expected: str, labels: set[str]
) -> Decimal:
    with localcontext(POLICY_DECIMAL_CONTEXT):
        decimal_probabilities = _normalized_probabilities(probabilities, labels)
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
    if kind == "openrouter-decisions":
        api_key = _api_key(backend)
        if api_key is None:
            raise ValueError("openrouter-decisions requires backend.api_key_env")
        return _PlainClientContext(
            cast(Client, _OpenRouterDecisionsClient(backend, api_key=api_key))
        )
    if kind == "direct-option-logits":
        return _PlainClientContext(cast(Client, _DirectOptionLogitsClient(backend)))
    if kind == "laya":
        return _PlainClientContext(cast(Client, _LayaClient(backend)))
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
                request: dict[str, Any] = {
                    "model": self.model_name,
                    "messages": render_messages(messages),
                    "response_format": response_format,
                    "max_tokens": int(backend["max_tokens"]),
                    "temperature": float(backend.get("temperature", 0)),
                    "extra_body": {"chat_template_kwargs": backend.get("chat_template_kwargs", {})},
                }
                reasoning_effort = backend.get("reasoning_effort")
                if reasoning_effort is not None:
                    request["reasoning_effort"] = reasoning_effort
                with translating(self.translate_error):
                    response = self._client.chat.completions.create(**cast(Any, request))
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
    raise ValueError(
        "backend.kind must be typesafe, openrouter-decisions, "
        "direct-option-logits, laya, or openai-compatible"
    )


def _call_count(response: Response, *, backend_kind: str) -> int:
    if backend_kind in {"typesafe", "openrouter-decisions", "direct-option-logits", "laya"}:
        return 1
    attempts = response.debug.get("llm_attempts")
    if not isinstance(attempts, list) or not attempts:
        raise ValueError("adapter response did not expose nonempty llm_attempts")
    return len(attempts)


def _token_totals(response: Response) -> tuple[int, int]:
    """Read direct TypeSafe usage or adapter totals without changing semantics."""

    usage = response.usage
    return (
        int(getattr(usage, "input_tokens_total", usage.input_tokens)),
        int(getattr(usage, "output_tokens_total", usage.output_tokens)),
    )


def _fixed_cost(candidate: dict[str, Any]) -> tuple[str, Decimal | None]:
    cost_basis = cast(str, candidate["cost_basis"])
    fixed_cost_value = candidate.get("fixed_cost_usd_per_model_call")
    fixed_cost = Decimal(str(fixed_cost_value)) if fixed_cost_value is not None else None
    permits_missing_fixed_cost = cost_basis in {"unavailable", "provider_reported"}
    if permits_missing_fixed_cost != (fixed_cost is None):
        raise ValueError(
            "fixed cost must be null exactly for unavailable or provider-reported cost"
        )
    return cost_basis, fixed_cost


def _response_cost(
    response: Response,
    *,
    cost_basis: str,
    fixed_cost: Decimal | None,
    calls: int,
) -> Decimal | None:
    if cost_basis == "unavailable":
        return None
    if cost_basis == "provider_reported":
        value = getattr(response.usage, "cost", None)
        if value is None:
            raise ValueError("provider-reported cost basis requires usage.cost")
        return Decimal(str(value))
    if fixed_cost is None:
        raise ValueError("fixed-cost basis requires fixed_cost_usd_per_model_call")
    return fixed_cost * calls


def _unsafe_action(
    suite: dict[str, Any],
    *,
    expected: str,
    predicted: str,
    case: dict[str, Any] | None = None,
) -> bool:
    raw_policy = case.get("unsafe_predictions") if case is not None else None
    if raw_policy is not None:
        if not isinstance(raw_policy, list) or not all(
            isinstance(value, str) for value in raw_policy
        ):
            raise ValueError("case unsafe_predictions must be a string array")
        return predicted in raw_policy

    raw_policy = suite.get("unsafe_predictions_by_expected")
    if raw_policy is None:
        return expected == "abstain" and predicted != "abstain"
    if not isinstance(raw_policy, dict):
        raise ValueError("unsafe_predictions_by_expected must be an object")
    raw_predictions = raw_policy.get(expected, [])
    if not isinstance(raw_predictions, list) or not all(
        isinstance(value, str) for value in raw_predictions
    ):
        raise ValueError("unsafe prediction sets must be string arrays")
    return predicted in raw_predictions


def _case_identity(case: dict[str, Any], question: dict[str, Any]) -> dict[str, Any]:
    """Bind optional scoring metadata without changing legacy case digests."""

    identity = {
        "case_id": case["case_id"],
        "state": case["state"],
        "expected": case["expected"],
        "stratum": case["stratum"],
        "question": question,
    }
    for field in ("contrast_set", "unsafe_predictions"):
        if field in case:
            identity[field] = case[field]
    return identity


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
    cost_basis, fixed_cost = _fixed_cost(candidate)

    results: list[dict[str, Any]] = []
    input_tokens_total = 0
    output_tokens_total = 0
    resolved_models: set[str] = set()
    providers: set[str] = set()
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
        case_sha256_by_id[case_id] = _digest(_case_identity(case, question))
    with _client(candidate) as client:
        for case in cases:
            expected = cast(str, case["expected"])
            if expected not in labels:
                raise ValueError(f"case {case['case_id']!r} has an unknown expected label")
            case_sha256 = case_sha256_by_id[case["case_id"]]
            for repetition in range(1, repetitions + 1):
                started = time.perf_counter()
                try:
                    response = client.system_one(case["state"], questions)
                except Exception as exc:
                    raise RuntimeError(
                        f"candidate failed on case {case['case_id']!r}, repetition {repetition}"
                    ) from exc
                latency = Decimal(str(time.perf_counter() - started))
                answer = response.choices[question_name]
                answer_probabilities = _normalized_probabilities(answer.probabilities, labels)
                calls = _call_count(response, backend_kind=cast(str, backend["kind"]))
                response_input_tokens, response_output_tokens = _token_totals(response)
                input_tokens_total += response_input_tokens
                output_tokens_total += response_output_tokens
                resolved_model = response.debug.get("resolved_model")
                provider = response.debug.get("provider")
                if isinstance(resolved_model, str) and resolved_model:
                    resolved_models.add(resolved_model)
                if isinstance(provider, str) and provider:
                    providers.add(provider)
                cost = _response_cost(
                    response,
                    cost_basis=cost_basis,
                    fixed_cost=fixed_cost,
                    calls=calls,
                )
                results.append(
                    {
                        "case_id": case["case_id"],
                        "case_sha256": case_sha256,
                        "repetition": repetition,
                        "decision_correct": answer.choice == expected,
                        "final_success": answer.choice == expected,
                        "schema_valid": True,
                        "abstained": answer.choice == "abstain",
                        "unsafe_action": _unsafe_action(
                            suite,
                            expected=expected,
                            predicted=answer.choice,
                            case=case,
                        ),
                        "decision_choice": answer.choice,
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
                            _brier(answer_probabilities, expected, labels),
                            decimal_places=12,
                        ),
                        "decision_max_probability": _decimal(
                            max(answer_probabilities.values()),
                            decimal_places=12,
                        ),
                        "expected_probability": _decimal(
                            answer_probabilities[expected],
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
        "workload_id": suite.get("workload_id", "structured-routing-screen-v1"),
        "workload_unit": suite.get("workload_unit", "decision"),
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
            "input_tokens_total": input_tokens_total,
            "output_tokens_total": output_tokens_total,
            **(
                {
                    "provider_observations": {
                        "resolved_models": sorted(resolved_models),
                        "providers": sorted(providers),
                    }
                }
                if resolved_models or providers
                else {}
            ),
        },
    }


def run_compound(
    suite_path: Path,
    router_candidate_path: Path,
    worker_candidate_path: Path,
    compound_candidate_path: Path,
    *,
    repetitions: int,
) -> dict[str, Any]:
    """Measure a light router whose selected heavy route receives worker review.

    This is a routing-decision cascade, not an end-to-end tool-task result. The
    worker answers the same pinned routing question and replaces the router's
    decision only on the choices named by the immutable policy.
    """

    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    suite, suite_raw = _load_object(suite_path, label="suite")
    router, router_raw = _load_object(router_candidate_path, label="router candidate")
    worker, worker_raw = _load_object(worker_candidate_path, label="worker candidate")
    compound, compound_raw = _load_object(compound_candidate_path, label="compound candidate")
    if router["resource_class"] != "light":
        raise ValueError("compound router candidate must have resource_class light")
    if worker["resource_class"] != "heavy":
        raise ValueError("compound worker candidate must have resource_class heavy")

    question = cast(dict[str, Any], suite["question"])
    question_name = cast(str, question["name"])
    criteria = cast(dict[str, Any], question["criteria"])
    labels = set(criteria)
    policy = cast(dict[str, Any], compound["routing_policy"])
    expected_policy_keys = {
        "policy_id",
        "invoke_worker_on_choices",
        "invoke_worker_below_max_probability",
        "preserve_choices_without_review",
        "resolution",
        "maximum_worker_calls_per_case",
    }
    if set(policy) != expected_policy_keys:
        raise ValueError("routing policy must contain the exact supported keys")
    if not isinstance(policy["policy_id"], str) or not policy["policy_id"]:
        raise ValueError("routing_policy.policy_id must be a nonempty string")
    invoke_worker_on_choices = policy.get("invoke_worker_on_choices")
    if not isinstance(invoke_worker_on_choices, list) or not invoke_worker_on_choices:
        raise ValueError("routing_policy.invoke_worker_on_choices must be a nonempty list")
    worker_choices = set(invoke_worker_on_choices)
    if not worker_choices.issubset(labels):
        raise ValueError("routing policy names a choice absent from the suite")
    preserve_choices_value = policy.get("preserve_choices_without_review", [])
    if not isinstance(preserve_choices_value, list):
        raise ValueError("routing_policy.preserve_choices_without_review must be a list")
    preserve_choices = set(preserve_choices_value)
    if not preserve_choices.issubset(labels):
        raise ValueError("routing policy preserves a choice absent from the suite")
    if worker_choices.intersection(preserve_choices):
        raise ValueError("a route cannot both invoke the worker and bypass review")
    threshold_value = policy.get("invoke_worker_below_max_probability")
    confidence_threshold = Decimal(str(threshold_value)) if threshold_value is not None else None
    if confidence_threshold is not None and not 0 <= confidence_threshold <= 1:
        raise ValueError("routing confidence threshold must be between zero and one")
    if policy.get("resolution") != "worker_replaces_router":
        raise ValueError("routing_policy.resolution must be worker_replaces_router")
    if policy.get("maximum_worker_calls_per_case") != 1:
        raise ValueError("routing_policy.maximum_worker_calls_per_case must equal one")

    router_component_id = cast(str, compound["router_component_id"])
    worker_component_id = cast(str, compound["worker_component_id"])
    if not router_component_id or not worker_component_id:
        raise ValueError("compound component IDs must be nonempty")
    if router_component_id == worker_component_id:
        raise ValueError("compound component IDs must be distinct")

    compound_cost_basis = cast(str, compound["cost_basis"])
    _, router_fixed_cost = _fixed_cost(router)
    _, worker_fixed_cost = _fixed_cost(worker)
    if compound_cost_basis == "unavailable":
        if router_fixed_cost is not None or worker_fixed_cost is not None:
            raise ValueError("unavailable compound cost requires unavailable component costs")
    elif router_fixed_cost is None or worker_fixed_cost is None:
        raise ValueError("a declared compound cost basis requires both component costs")

    router_questions = {
        question_name: {
            "type": "choice",
            "instructions": f"{router['guidance']}\n\n{question['instructions']}",
            "criteria": criteria,
        }
    }
    worker_questions = {
        question_name: {
            "type": "choice",
            "instructions": f"{worker['guidance']}\n\n{question['instructions']}",
            "criteria": criteria,
        }
    }
    cases = cast(list[dict[str, Any]], suite["cases"])
    case_sha256_by_id: dict[str, str] = {}
    for case in cases:
        case_id = cast(str, case["case_id"])
        if case_id in case_sha256_by_id:
            raise ValueError(f"suite repeats case_id {case_id!r}")
        case_sha256_by_id[case_id] = _digest(_case_identity(case, question))

    results: list[dict[str, Any]] = []
    input_tokens_total = 0
    output_tokens_total = 0
    component_input_tokens = {router_component_id: 0, worker_component_id: 0}
    component_output_tokens = {router_component_id: 0, worker_component_id: 0}
    observed_at = datetime.now(UTC)
    router_backend = cast(dict[str, Any], router["backend"])
    worker_backend = cast(dict[str, Any], worker["backend"])

    with ExitStack() as stack:
        router_client = stack.enter_context(_client(router))
        worker_client = stack.enter_context(_client(worker))
        for case in cases:
            expected = cast(str, case["expected"])
            if expected not in labels:
                raise ValueError(f"case {case['case_id']!r} has an unknown expected label")
            case_sha256 = case_sha256_by_id[case["case_id"]]
            for repetition in range(1, repetitions + 1):
                started = time.perf_counter()
                try:
                    router_response = router_client.system_one(case["state"], router_questions)
                except Exception as exc:
                    raise RuntimeError(
                        f"router failed on case {case['case_id']!r}, repetition {repetition}"
                    ) from exc
                router_answer = router_response.choices[question_name]
                router_probabilities = _normalized_probabilities(
                    router_answer.probabilities, labels
                )
                router_max_probability = max(router_probabilities.values())
                router_calls = _call_count(
                    router_response,
                    backend_kind=cast(str, router_backend["kind"]),
                )
                router_input, router_output = _token_totals(router_response)
                component_input_tokens[router_component_id] += router_input
                component_output_tokens[router_component_id] += router_output

                worker_calls = 0
                worker_input = 0
                worker_output = 0
                final_answer = router_answer
                confidence_fallback = (
                    confidence_threshold is not None
                    and router_answer.choice not in preserve_choices
                    and router_max_probability < confidence_threshold
                )
                if router_answer.choice in worker_choices or confidence_fallback:
                    try:
                        worker_response = worker_client.system_one(case["state"], worker_questions)
                    except Exception as exc:
                        raise RuntimeError(
                            f"worker failed on case {case['case_id']!r}, repetition {repetition}"
                        ) from exc
                    final_answer = worker_response.choices[question_name]
                    worker_calls = _call_count(
                        worker_response,
                        backend_kind=cast(str, worker_backend["kind"]),
                    )
                    worker_input, worker_output = _token_totals(worker_response)
                    component_input_tokens[worker_component_id] += worker_input
                    component_output_tokens[worker_component_id] += worker_output

                final_probabilities = _normalized_probabilities(final_answer.probabilities, labels)

                latency = Decimal(str(time.perf_counter() - started))
                input_tokens_total += router_input + worker_input
                output_tokens_total += router_output + worker_output
                router_cost = (
                    router_fixed_cost * router_calls if router_fixed_cost is not None else None
                )
                worker_cost = (
                    worker_fixed_cost * worker_calls if worker_fixed_cost is not None else None
                )
                total_cost = (
                    router_cost + worker_cost
                    if router_cost is not None and worker_cost is not None
                    else None
                )
                results.append(
                    {
                        "case_id": case["case_id"],
                        "case_sha256": case_sha256,
                        "repetition": repetition,
                        "decision_correct": final_answer.choice == expected,
                        "final_success": final_answer.choice == expected,
                        "schema_valid": True,
                        "abstained": final_answer.choice == "abstain",
                        "unsafe_action": _unsafe_action(
                            suite,
                            expected=expected,
                            predicted=final_answer.choice,
                            case=case,
                        ),
                        "decision_choice": final_answer.choice,
                        "latency_seconds": _decimal(latency, decimal_places=9),
                        "total_cost_usd": (
                            _decimal(total_cost, decimal_places=12)
                            if total_cost is not None
                            else None
                        ),
                        "model_calls": router_calls + worker_calls,
                        "heavy_model_calls": worker_calls,
                        "component_usage": [
                            {
                                "component_id": router_component_id,
                                "calls": router_calls,
                                "cost_usd": (
                                    _decimal(router_cost, decimal_places=12)
                                    if router_cost is not None
                                    else None
                                ),
                            },
                            {
                                "component_id": worker_component_id,
                                "calls": worker_calls,
                                "cost_usd": (
                                    _decimal(worker_cost, decimal_places=12)
                                    if worker_cost is not None
                                    else None
                                ),
                            },
                        ],
                        "brier_score": _decimal(
                            _brier(final_probabilities, expected, labels),
                            decimal_places=12,
                        ),
                        "router_decision_correct": router_answer.choice == expected,
                        "router_abstained": router_answer.choice == "abstain",
                        "router_choice": router_answer.choice,
                        "router_max_probability": _decimal(
                            router_max_probability,
                            decimal_places=12,
                        ),
                        "router_brier_score": _decimal(
                            _brier(router_probabilities, expected, labels),
                            decimal_places=12,
                        ),
                        "tool_selection_correct": None,
                        "tool_arguments_correct": None,
                        "tool_sequence_correct": None,
                        "tool_policy_compliant": None,
                        "tool_side_effects_correct": None,
                    }
                )

    compound_offering = cast(dict[str, Any], compound["offering"])
    router_offering = cast(dict[str, Any], router["offering"])
    worker_offering = cast(dict[str, Any], worker["offering"])
    return {
        "schema_version": "model-skyline/structured-decision-run/v1alpha1",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "workload_id": suite.get("workload_id", "structured-routing-screen-v1"),
        "workload_unit": suite.get("workload_unit", "decision"),
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
        "offering": compound_offering,
        "system": {
            "kind": "compound_model_system",
            "routing_policy_sha256": _digest(policy),
            "components": [
                {
                    "component_id": router_component_id,
                    "role": "router",
                    "resource_class": "light",
                    "activation": "always",
                    "offering": router_offering,
                    "guidance_sha256": _digest(router["guidance"]),
                },
                {
                    "component_id": worker_component_id,
                    "role": "worker",
                    "resource_class": "heavy",
                    "activation": "confidence_fallback",
                    "offering": worker_offering,
                    "guidance_sha256": _digest(worker["guidance"]),
                },
            ],
        },
        "cost_basis": compound_cost_basis,
        "results": results,
        "metadata": {
            "suite_license": suite["license"],
            "suite_methodology": suite["methodology"],
            "component_backend_kinds": {
                router_component_id: router_backend["kind"],
                worker_component_id: worker_backend["kind"],
            },
            "candidate_configuration_sha256": hashlib.sha256(compound_raw).hexdigest(),
            "router_candidate_configuration_sha256": hashlib.sha256(router_raw).hexdigest(),
            "worker_candidate_configuration_sha256": hashlib.sha256(worker_raw).hexdigest(),
            "measurement_conditions": compound.get("measurement_conditions", {}),
            "input_tokens_total": input_tokens_total,
            "output_tokens_total": output_tokens_total,
            "component_input_tokens": component_input_tokens,
            "component_output_tokens": component_output_tokens,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--worker-candidate", type=Path)
    parser.add_argument("--compound-candidate", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.worker_candidate is None) != (args.compound_candidate is None):
        parser.error("--worker-candidate and --compound-candidate must be supplied together")
    if args.worker_candidate is None:
        result = run(args.suite, args.candidate, repetitions=args.repetitions)
    else:
        result = run_compound(
            args.suite,
            args.candidate,
            args.worker_candidate,
            args.compound_candidate,
            repetitions=args.repetitions,
        )
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
