#!/usr/bin/env python3
"""Run the pinned BFCL single-turn panel through one model or a contract fallback.

The BFCL repository checkout is an explicit input. Its pinned model handler and
AST scorer are reused directly; prompts and raw model responses are never
written to the result artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import types
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from model_skyline.adapters.structured_decisions import (
    structured_decision_case_set_sha256,
)
from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, canonical_bytes

SINGLE_TURN_CATEGORIES = {
    "simple_python",
    "multiple",
    "parallel",
    "parallel_multiple",
    "irrelevance",
}


class _ApproximateTokenizer:
    """Only supplies BFCL's pre-request context-cap calculation.

    The endpoint reports authoritative token usage after each request. Splitting
    on whitespace is conservative enough for this small panel's 128K+ models;
    it is not used as measured token evidence.
    """

    @staticmethod
    def tokenize(value: str) -> list[str]:
        return value.split()


def _load_object(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value, raw


def _load_jsonl(path: Path) -> dict[str, tuple[dict[str, Any], bytes]]:
    result: dict[str, tuple[dict[str, Any], bytes]] = {}
    with path.open("rb") as handle:
        for line in handle:
            raw = line.rstrip(b"\r\n")
            if not raw:
                continue
            value = json.loads(raw)
            case_id = value.get("id")
            if not isinstance(case_id, str) or not case_id:
                raise ValueError(f"{path} contains a row without a valid id")
            if case_id in result:
                raise ValueError(f"{path} repeats id {case_id!r}")
            result[case_id] = (value, raw)
    return result


def _git_blob_sha1(raw: bytes) -> str:
    prefix = f"blob {len(raw)}\0".encode()
    return hashlib.sha1(prefix + raw).hexdigest()  # noqa: S324 - Git object identity


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _decimal(value: Decimal, *, decimal_places: int) -> str:
    with localcontext(POLICY_DECIMAL_CONTEXT):
        quantized = value.quantize(Decimal(1).scaleb(-decimal_places))
    return format(quantized, "f")


def _install_bfcl_imports(bfcl_root: Path) -> tuple[Any, Any, Any, Any]:
    package_root = bfcl_root / "berkeley-function-call-leaderboard"
    if not package_root.is_dir():
        raise ValueError(
            "--bfcl-root must be the Gorilla checkout containing "
            "berkeley-function-call-leaderboard"
        )
    sys.path.insert(0, str(package_root))

    # The AST scorer imports the complete leaderboard model registry merely to
    # inspect underscore-to-dot behavior. Avoid importing unrelated proprietary
    # SDKs by supplying the one property this pinned local panel needs.
    model_config = types.ModuleType("bfcl_eval.constants.model_config")
    cast(Any, model_config).MODEL_CONFIG_MAPPING = {
        "model-skyline-local": SimpleNamespace(underscore_to_dot=False)
    }
    sys.modules["bfcl_eval.constants.model_config"] = model_config

    from bfcl_eval.constants.enums import Language  # type: ignore[import-not-found]
    from bfcl_eval.eval_checker.ast_eval.ast_checker import (  # type: ignore[import-not-found]
        ast_checker,
    )
    from bfcl_eval.model_handler.local_inference.granite_4 import (  # type: ignore[import-not-found]
        Granite4FCHandler,
    )
    from bfcl_eval.model_handler.local_inference.qwen_fc import (  # type: ignore[import-not-found]
        QwenFCHandler,
    )

    return Language, ast_checker, Granite4FCHandler, QwenFCHandler


def _handler(candidate: dict[str, Any], imports: tuple[Any, Any, Any, Any]) -> Any:
    from openai import OpenAI

    _, _, granite_handler, qwen_handler = imports
    backend = cast(dict[str, Any], candidate["backend"])
    handler_kind = backend["bfcl_handler"]
    handler_class = {
        "granite4": granite_handler,
        "qwen_fc": qwen_handler,
    }.get(handler_kind)
    if handler_class is None:
        raise ValueError("backend.bfcl_handler must be granite4 or qwen_fc")
    model = cast(str, backend["model"])
    instance = handler_class(
        model,
        float(backend.get("temperature", 0.001)),
        "model-skyline-local-FC",
        True,
    )
    instance.model_path_or_id = model
    instance.max_context_length = int(backend["max_context_tokens"])
    instance.tokenizer = _ApproximateTokenizer()
    instance.base_url = cast(str, backend["base_url"])
    instance.client = OpenAI(base_url=instance.base_url, api_key="local-no-secret")
    return instance


def _basic_type_matches(value: Any, declared: str) -> bool:
    normalized = declared.lower()
    if normalized in {"string", "str"}:
        return isinstance(value, str)
    if normalized in {"integer", "int"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized in {"number", "float", "double"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if normalized in {"boolean", "bool"}:
        return isinstance(value, bool)
    if normalized in {"array", "list", "tuple"}:
        return isinstance(value, list)
    if normalized in {"object", "dict"}:
        return isinstance(value, dict)
    return True


def _contract_valid(decoded: list[dict[str, Any]], functions: list[dict[str, Any]]) -> bool:
    declarations = {function["name"]: function for function in functions}
    for call in decoded:
        if len(call) != 1:
            return False
        name, arguments = next(iter(call.items()))
        declaration = declarations.get(name)
        if declaration is None or not isinstance(arguments, dict):
            return False
        parameters = declaration.get("parameters", {})
        properties = parameters.get("properties", {})
        required = set(parameters.get("required", []))
        if not required.issubset(arguments) or not set(arguments).issubset(properties):
            return False
        for key, value in arguments.items():
            property_schema = properties[key]
            declared_type = property_schema.get("type")
            if isinstance(declared_type, str) and not _basic_type_matches(value, declared_type):
                return False
            enum = property_schema.get("enum")
            if isinstance(enum, list) and value not in enum:
                return False
    return True


def _decode(handler: Any, raw_response: str, language: Any) -> tuple[list[dict[str, Any]], bool]:
    try:
        decoded = handler.decode_ast(raw_response, language.PYTHON, True)
    except Exception:
        return [], False
    if not isinstance(decoded, list) or any(not isinstance(item, dict) for item in decoded):
        return [], False
    malformed_tool_envelope = "<tool_call>" in raw_response and not decoded
    return decoded, not malformed_tool_envelope


def _resolve_fallback(
    primary_decoded: list[dict[str, Any]],
    *,
    primary_schema_valid: bool,
    primary_contract_valid: bool,
    fallback_decoded: list[dict[str, Any]],
    fallback_schema_valid: bool,
    resolution: str,
) -> tuple[list[dict[str, Any]], bool]:
    if not primary_contract_valid or resolution == "fallback-replaces-primary":
        return fallback_decoded, fallback_schema_valid
    if resolution != "fallback-vetoes-primary-tool-call":
        raise ValueError("compound resolution is not supported")
    if fallback_schema_valid and not fallback_decoded:
        return [], True
    return primary_decoded, primary_schema_valid


def _selection_correct(
    decoded: list[dict[str, Any]], answer: dict[str, Any] | None
) -> bool:
    actual = Counter(next(iter(call)) for call in decoded if len(call) == 1)
    if answer is None:
        return not actual
    expected = Counter(next(iter(call)) for call in answer["ground_truth"])
    return actual == expected


def _score_result(
    decoded: list[dict[str, Any]],
    *,
    schema_valid: bool,
    question: dict[str, Any],
    answer: dict[str, Any] | None,
    category: str,
    language: Any,
    ast_checker: Any,
) -> tuple[bool, bool, bool | None, bool | None]:
    selection_correct = schema_valid and _selection_correct(decoded, answer)
    if answer is None:
        return selection_correct, selection_correct, None, selection_correct
    if not schema_valid:
        return False, selection_correct, None, None
    score = ast_checker(
        question["function"],
        decoded,
        answer["ground_truth"],
        language.PYTHON,
        category,
        "model-skyline-local",
    )
    official_valid = bool(score["valid"])
    argument_correct = official_valid if selection_correct else None
    return official_valid, selection_correct, argument_correct, None


def _load_cases(
    manifest: dict[str, Any], bfcl_root: Path
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    cases: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    for category in manifest["categories"]:
        if category["id"] not in SINGLE_TURN_CATEGORIES:
            continue
        question_path = bfcl_root / category["question_path"]
        question_raw = question_path.read_bytes()
        if _git_blob_sha1(question_raw) != category["question_blob_sha1"]:
            raise ValueError(f"BFCL question blob drifted: {category['id']}")
        questions = _load_jsonl(question_path)
        answers: dict[str, tuple[dict[str, Any], bytes]] = {}
        if category["answer_path"] is not None:
            answer_path = bfcl_root / category["answer_path"]
            answer_raw = answer_path.read_bytes()
            if _git_blob_sha1(answer_raw) != category["answer_blob_sha1"]:
                raise ValueError(f"BFCL answer blob drifted: {category['id']}")
            answers = _load_jsonl(answer_path)
        for case_id in category["case_ids"]:
            question, _ = questions[case_id]
            case = {
                "case_id": case_id,
                "category": category["id"],
                "question": question,
                "answer": answers.get(case_id, (None, b""))[0],
            }
            cases.append(case)
            hashes[case_id] = _digest(question)
    return cases, hashes


def run(
    manifest_path: Path,
    candidate_path: Path,
    bfcl_root: Path,
    *,
    repetitions: int,
    fallback_candidate_path: Path | None,
    compound_candidate_path: Path | None,
) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    manifest, manifest_raw = _load_object(manifest_path, label="manifest")
    candidate, candidate_raw = _load_object(candidate_path, label="candidate")
    fallback: dict[str, Any] | None = None
    fallback_raw: bytes | None = None
    compound: dict[str, Any] | None = None
    compound_raw: bytes | None = None
    if (fallback_candidate_path is None) != (compound_candidate_path is None):
        raise ValueError(
            "fallback_candidate_path and compound_candidate_path must be supplied together"
        )
    if fallback_candidate_path is not None:
        fallback, fallback_raw = _load_object(
            fallback_candidate_path, label="fallback candidate"
        )
        assert compound_candidate_path is not None
        compound, compound_raw = _load_object(
            compound_candidate_path, label="compound candidate"
        )
        if candidate["resource_class"] != "light" or fallback["resource_class"] != "heavy":
            raise ValueError("compound run requires light primary and heavy fallback")
        if compound["primary_component_id"] != candidate["component_id"]:
            raise ValueError("compound primary_component_id does not match primary candidate")
        if compound["fallback_component_id"] != fallback["component_id"]:
            raise ValueError("compound fallback_component_id does not match fallback candidate")
        triggers = compound["routing_policy"]["invoke_fallback_on"]
        expected_triggers = [
            "malformed-or-contract-invalid",
            "single-available-tool-with-primary-call",
        ]
        if triggers != expected_triggers:
            raise ValueError(f"compound invoke_fallback_on must equal {expected_triggers!r}")
        resolution = compound["routing_policy"].get("resolution")
        if resolution not in {
            "fallback-replaces-primary",
            "fallback-vetoes-primary-tool-call",
        }:
            raise ValueError("compound resolution is not supported")

    imports = _install_bfcl_imports(bfcl_root)
    language, ast_checker, _, _ = imports
    primary_handler = _handler(candidate, imports)
    fallback_handler = _handler(fallback, imports) if fallback is not None else None
    cases, case_sha256_by_id = _load_cases(manifest, bfcl_root)

    primary_id = cast(str, candidate["component_id"])
    fallback_id = cast(str, fallback["component_id"]) if fallback is not None else None
    results: list[dict[str, Any]] = []
    component_input_tokens = {primary_id: 0}
    component_output_tokens = {primary_id: 0}
    if fallback_id is not None:
        component_input_tokens[fallback_id] = 0
        component_output_tokens[fallback_id] = 0
    observed_at = datetime.now(UTC)

    for case in cases:
        question = cast(dict[str, Any], case["question"])
        answer = cast(dict[str, Any] | None, case["answer"])
        for repetition in range(1, repetitions + 1):
            started = time.perf_counter()
            primary_response, primary_usage = primary_handler.inference(
                deepcopy(question), False, True
            )
            primary_decoded, primary_schema_valid = _decode(
                primary_handler, primary_response, language
            )
            primary_contract_valid = primary_schema_valid and _contract_valid(
                primary_decoded, question["function"]
            )
            primary_valid, _, _, _ = _score_result(
                primary_decoded,
                schema_valid=primary_schema_valid,
                question=question,
                answer=answer,
                category=case["category"],
                language=language,
                ast_checker=ast_checker,
            )
            component_input_tokens[primary_id] += int(primary_usage["input_token_count"])
            component_output_tokens[primary_id] += int(primary_usage["output_token_count"])

            final_decoded = primary_decoded
            final_schema_valid = primary_schema_valid
            fallback_calls = 0
            fallback_input = 0
            fallback_output = 0
            invoke_fallback = not primary_contract_valid
            if (
                fallback_handler is not None
                and compound is not None
                and len(question["function"]) == 1
                and bool(primary_decoded)
            ):
                invoke_fallback = True
            if fallback_handler is not None and invoke_fallback:
                fallback_response, fallback_usage = fallback_handler.inference(
                    deepcopy(question), False, True
                )
                fallback_decoded, fallback_schema_valid = _decode(
                    fallback_handler, fallback_response, language
                )
                assert compound is not None
                final_decoded, final_schema_valid = _resolve_fallback(
                    primary_decoded,
                    primary_schema_valid=primary_schema_valid,
                    primary_contract_valid=primary_contract_valid,
                    fallback_decoded=fallback_decoded,
                    fallback_schema_valid=fallback_schema_valid,
                    resolution=compound["routing_policy"]["resolution"],
                )
                fallback_calls = 1
                fallback_input = int(fallback_usage["input_token_count"])
                fallback_output = int(fallback_usage["output_token_count"])
                assert fallback_id is not None
                component_input_tokens[fallback_id] += fallback_input
                component_output_tokens[fallback_id] += fallback_output

            official_valid, selection_correct, argument_correct, policy_compliant = (
                _score_result(
                    final_decoded,
                    schema_valid=final_schema_valid,
                    question=question,
                    answer=answer,
                    category=case["category"],
                    language=language,
                    ast_checker=ast_checker,
                )
            )

            latency = Decimal(str(time.perf_counter() - started))
            component_usage = [
                {"component_id": primary_id, "calls": 1, "cost_usd": None}
            ]
            primary_heavy_calls = (
                1
                if fallback is None and candidate["resource_class"] == "heavy"
                else 0
            )
            if fallback_id is not None:
                component_usage.append(
                    {
                        "component_id": fallback_id,
                        "calls": fallback_calls,
                        "cost_usd": None,
                    }
                )
            results.append(
                {
                    "case_id": case["case_id"],
                    "case_sha256": case_sha256_by_id[case["case_id"]],
                    "repetition": repetition,
                    "decision_correct": official_valid,
                    "final_success": official_valid,
                    "schema_valid": final_schema_valid,
                    "abstained": not final_decoded,
                    "unsafe_action": False,
                    "latency_seconds": _decimal(latency, decimal_places=9),
                    "total_cost_usd": None,
                    "model_calls": 1 + fallback_calls,
                    "heavy_model_calls": primary_heavy_calls + fallback_calls,
                    "component_usage": component_usage,
                    "brier_score": None,
                    "primary_success": (
                        primary_valid if fallback_handler is not None else None
                    ),
                    "tool_selection_correct": selection_correct,
                    "tool_arguments_correct": argument_correct,
                    "tool_sequence_correct": None,
                    "tool_policy_compliant": policy_compliant,
                    "tool_side_effects_correct": None,
                }
            )

    source = manifest["source"]
    primary_offering = cast(dict[str, Any], candidate["offering"])
    if fallback is None:
        offering = primary_offering
        system_kind = "single_model_system"
        policy = candidate["routing_policy"]
        components = [
            {
                "component_id": primary_id,
                "role": "worker",
                "resource_class": candidate["resource_class"],
                "activation": "always",
                "offering": primary_offering,
                "guidance_sha256": _digest(candidate["guidance"]),
            }
        ]
    else:
        assert fallback_id is not None and fallback_raw is not None
        assert compound is not None and compound_raw is not None
        fallback_offering = cast(dict[str, Any], fallback["offering"])
        policy = compound["routing_policy"]
        offering = compound["offering"]
        system_kind = "compound_model_system"
        components = [
            {
                "component_id": primary_id,
                "role": "decision",
                "resource_class": "light",
                "activation": "always",
                "offering": primary_offering,
                "guidance_sha256": _digest(candidate["guidance"]),
            },
            {
                "component_id": fallback_id,
                "role": "fallback",
                "resource_class": "heavy",
                "activation": "policy_trigger",
                "offering": fallback_offering,
                "guidance_sha256": _digest(fallback["guidance"]),
            },
        ]

    case_set_sha256 = structured_decision_case_set_sha256(case_sha256_by_id.items())
    return {
        "schema_version": "model-skyline/structured-decision-run/v1alpha1",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "workload_id": "bfcl-v4-offline-single-turn-40",
        "workload_unit": "tool-case",
        "workload": {
            "suite_id": "bfcl-v4-offline-single-turn-40",
            "suite_version": source["revision"],
            "case_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "case_set_sha256": case_set_sha256,
            "case_count": len(case_sha256_by_id),
            "harness_id": "model-skyline/bfcl-single-turn-panel",
            "harness_version": "1",
            "scorer_version": f"bfcl-ast-checker@{source['revision']}",
            "oracle_kind": "deterministic",
            "repetitions_per_case": repetitions,
            "concurrency": 1,
        },
        "benchmark_source": {
            "id": manifest["panel_id"],
            "version": source["revision"],
            "url": source["methodology_url"],
            "license": source["license"],
            "methodology": (
                "The five single-turn categories and pinned IDs from the ModelSkyline "
                "BFCL v4 calibration manifest, scored with the pinned upstream AST checker."
            ),
            "raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "retrieved_at": source["retrieved_at"],
        },
        "offering": offering,
        "system": {
            "kind": system_kind,
            "routing_policy_sha256": _digest(policy),
            "components": components,
        },
        "cost_basis": "unavailable",
        "results": results,
        "metadata": {
            "suite_license": source["license"],
            "candidate_configuration_sha256": hashlib.sha256(candidate_raw).hexdigest(),
            "fallback_candidate_configuration_sha256": (
                hashlib.sha256(fallback_raw).hexdigest() if fallback_raw is not None else None
            ),
            "compound_candidate_configuration_sha256": (
                hashlib.sha256(compound_raw).hexdigest() if compound_raw is not None else None
            ),
            "measurement_conditions": {
                "request_order": "category-then-case-major",
                "concurrency": 1,
                "deployment": (
                    compound["measurement_conditions"]["model_residency"]
                    if compound is not None
                    else candidate["measurement_conditions"]["model_residency"]
                ),
            },
            "input_tokens_total": sum(component_input_tokens.values()),
            "output_tokens_total": sum(component_output_tokens.values()),
            "component_input_tokens": component_input_tokens,
            "component_output_tokens": component_output_tokens,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--bfcl-root", type=Path, required=True)
    parser.add_argument("--fallback-candidate", type=Path)
    parser.add_argument("--compound-candidate", type=Path)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.manifest,
        args.candidate,
        args.bfcl_root,
        repetitions=args.repetitions,
        fallback_candidate_path=args.fallback_candidate,
        compound_candidate_path=args.compound_candidate,
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
