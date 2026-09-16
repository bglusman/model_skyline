from __future__ import annotations

import hashlib
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from model_skyline.adapters.structured_decisions import (
    StructuredDecisionRun,
    StructuredDecisionWorkloadIdentity,
    structured_decision_case_set_sha256,
    structured_decision_workload_version,
)
from model_skyline.canonical import content_hash
from model_skyline.io import load_config
from model_skyline.models import OfferingKey

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "structured-decision-frontiers"


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _runner_module() -> ModuleType:
    path = EXAMPLE / "run_system_one_screen.py"
    spec = importlib.util.spec_from_file_location("structured_decision_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_routing_suite_identity_matches_frontier_workload() -> None:
    path = EXAMPLE / "routing-screen-v1.json"
    suite = _object(path)
    config = load_config(EXAMPLE / "frontiers.yaml")
    case_ids = [case["case_id"] for case in suite["cases"]]

    assert len(case_ids) == len(set(case_ids)) == 18
    assert {case["expected"] for case in suite["cases"]} == {
        "light",
        "heavy",
        "abstain",
    }
    manifest_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    case_set_sha256 = structured_decision_case_set_sha256(
        (
            case["case_id"],
            content_hash(
                {
                    "case_id": case["case_id"],
                    "state": case["state"],
                    "expected": case["expected"],
                    "stratum": case["stratum"],
                    "question": suite["question"],
                }
            ),
        )
        for case in suite["cases"]
    )
    workload = StructuredDecisionWorkloadIdentity(
        suite_id=suite["suite_id"],
        suite_version=suite["suite_version"],
        case_manifest_sha256=manifest_sha256,
        case_set_sha256=case_set_sha256,
        case_count=18,
        harness_id="model-skyline/system-one-screen",
        harness_version="1",
        scorer_version="normalized-multiclass-brier-v1",
        oracle_kind="deterministic",
        repetitions_per_case=3,
        concurrency=1,
    )

    configured = config.workloads["structured-routing-screen-v1"]
    assert configured.version == structured_decision_workload_version(workload)
    assert configured.assumptions["case_manifest_sha256"] == manifest_sha256
    assert configured.assumptions["case_set_sha256"] == case_set_sha256


def test_bfcl_manifest_is_exact_balanced_and_matches_workload() -> None:
    path = EXAMPLE / "bfcl-v4-offline-64-manifest.json"
    manifest = _object(path)
    config = load_config(EXAMPLE / "frontiers.yaml")
    categories = manifest["categories"]
    case_ids = [case_id for category in categories for case_id in category["case_ids"]]

    assert len(categories) == 8
    assert all(len(category["case_ids"]) == 8 for category in categories)
    assert len(case_ids) == len(set(case_ids)) == manifest["case_count"] == 64
    assert manifest["source"]["license"] == "Apache-2.0"
    assert manifest["source"]["retrieved_at"] == "2026-09-16T00:00:00Z"
    assert len(manifest["case_set_sha256"]) == 64
    assert all(len(category["question_blob_sha1"]) == 40 for category in categories)
    assert all(len(scorer["blob_sha1"]) == 40 for scorer in manifest["scorers"])

    manifest_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    workload = StructuredDecisionWorkloadIdentity(
        suite_id=manifest["panel_id"],
        suite_version=manifest["source"]["revision"],
        case_manifest_sha256=manifest_sha256,
        case_set_sha256=manifest["case_set_sha256"],
        case_count=manifest["case_count"],
        harness_id="gorilla/bfcl",
        harness_version=manifest["source"]["revision"],
        scorer_version=(
            "ast:160fd75cfc8b2b7ef9f83a244f81da0fe0244e39+"
            "multi-turn:34b36715cdfaa4464ba1be137af4d30e85da8efb"
        ),
        oracle_kind="environment_verifier",
        repetitions_per_case=1,
        concurrency=1,
    )

    configured = config.workloads["bfcl-v4-offline-cross-category-64"]
    assert configured.version == structured_decision_workload_version(workload)
    assert configured.assumptions["panel_manifest_sha256"] == manifest_sha256
    assert configured.assumptions["case_set_sha256"] == manifest["case_set_sha256"]


def test_candidate_configs_have_complete_distinct_offerings() -> None:
    jev = _object(EXAMPLE / "jev-candidate.json")
    qwen = _object(EXAMPLE / "qwen38-local-candidate.json")
    gpt_oss = _object(EXAMPLE / "gpt-oss-20b-local-candidate.json")
    jev_offering = OfferingKey.model_validate(jev["offering"])
    qwen_offering = OfferingKey.model_validate(qwen["offering"])
    gpt_oss_offering = OfferingKey.model_validate(gpt_oss["offering"])

    assert (
        len(
            {
                jev_offering.offering_id,
                qwen_offering.offering_id,
                gpt_oss_offering.offering_id,
            }
        )
        == 3
    )
    assert jev_offering.capabilities == qwen_offering.capabilities == ("structured-decisions",)
    assert gpt_oss_offering.capabilities == ("structured-decisions",)
    assert gpt_oss["resource_class"] == "light"
    assert gpt_oss["backend"]["reasoning_effort"] == "low"
    assert gpt_oss["backend"]["structured_outputs"] is False
    assert qwen["backend"]["max_tokens"] == 512
    assert qwen["backend"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert qwen["measurement_conditions"]["model_residency"] == "warm"

    compound = _object(EXAMPLE / "gpt-oss-qwen38-review-cascade.json")
    compound_offering = OfferingKey.model_validate(compound["offering"])
    assert compound_offering.capabilities == ("compound-system", "structured-decisions")
    assert compound["routing_policy"] == {
        "policy_id": "heavy-or-low-confidence-review-v1",
        "invoke_worker_on_choices": ["heavy"],
        "invoke_worker_below_max_probability": "0.800000",
        "preserve_choices_without_review": ["abstain"],
        "resolution": "worker_replaces_router",
        "maximum_worker_calls_per_case": 1,
    }
    assert compound["measurement_conditions"]["co_resident"] is False


def test_frontier_recipes_keep_primitive_and_complete_system_claims_separate() -> None:
    config = load_config(EXAMPLE / "frontiers.yaml")

    assert len(config.frontiers) == 10
    assert config.frontiers["decision-quality-vs-latency"].eligibility.required_capabilities == (
        "structured-decisions",
    )
    assert config.frontiers["tool-outcome-vs-latency"].eligibility.required_capabilities == (
        "tools",
    )
    assert config.frontiers[
        "compound-outcome-vs-heavy-demand"
    ].eligibility.required_capabilities == ("compound-system", "tools")
    assert config.frontiers[
        "compound-routing-quality-vs-heavy-demand"
    ].eligibility.required_capabilities == ("compound-system", "structured-decisions")


def test_normalized_multiclass_brier_rejects_invalid_probabilities() -> None:
    runner = _runner_module()

    assert runner._brier(
        {"light": 1.0, "heavy": 0.0, "abstain": 0.0},
        "light",
        {"light", "heavy", "abstain"},
    ) == Decimal(0)

    try:
        runner._brier(
            {"light": 0.8, "heavy": 0.8, "abstain": 0.0},
            "light",
            {"light", "heavy", "abstain"},
        )
    except ValueError as exc:
        assert "sum to one" in str(exc)
    else:  # pragma: no cover - assertion aid
        raise AssertionError("invalid probability distribution was accepted")


def test_compound_runner_counts_both_components_without_emitting_inputs() -> None:
    runner = _runner_module()

    class FakeContext:
        def __init__(self, choice: str) -> None:
            self.choice = choice

        def __enter__(self):
            choice = self.choice

            class FakeClient:
                def system_one(self, state: Any, questions: dict[str, Any]):
                    del state
                    question_name = next(iter(questions))
                    probabilities = {
                        "light": 1.0 if choice == "light" else 0.0,
                        "heavy": 1.0 if choice == "heavy" else 0.0,
                        "abstain": 1.0 if choice == "abstain" else 0.0,
                    }
                    return SimpleNamespace(
                        choices={
                            question_name: SimpleNamespace(
                                choice=choice,
                                probabilities=probabilities,
                            )
                        },
                        usage=SimpleNamespace(
                            input_tokens=10,
                            output_tokens=2,
                            input_tokens_total=10,
                            output_tokens_total=2,
                        ),
                        debug={"llm_attempts": [{}]},
                    )

            return FakeClient()

        def __exit__(self, *args: Any) -> None:
            del args

    def fake_client(candidate: dict[str, Any]) -> FakeContext:
        model_id = candidate["offering"]["model_id"]
        return FakeContext("heavy" if model_id == "openai/gpt-oss-20b" else "light")

    runner._client = fake_client
    result = runner.run_compound(
        EXAMPLE / "routing-screen-v1.json",
        EXAMPLE / "gpt-oss-20b-local-candidate.json",
        EXAMPLE / "qwen38-local-candidate.json",
        EXAMPLE / "gpt-oss-qwen38-review-cascade.json",
        repetitions=1,
    )
    validated = StructuredDecisionRun.model_validate(result)

    assert validated.system.kind == "compound_model_system"
    assert len(validated.results) == 18
    assert all(item.model_calls == 2 for item in validated.results)
    assert all(item.heavy_model_calls == 1 for item in validated.results)
    assert all(item.router_abstained is False for item in validated.results)
    serialized = json.dumps(result)
    assert '"state"' not in serialized
    assert '"expected"' not in serialized
