from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from model_skyline.adapters.structured_decisions import (
    StructuredDecisionRun,
    StructuredDecisionWorkloadIdentity,
    structured_decision_case_set_sha256,
    structured_decision_workload_version,
)
from model_skyline.canonical import content_hash
from model_skyline.io import load_catalog, load_config, load_frontier_snapshot
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
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bfcl_runner_module() -> ModuleType:
    path = EXAMPLE / "run_bfcl_single_turn_panel.py"
    spec = importlib.util.spec_from_file_location("bfcl_single_turn_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _media_baseline_module() -> ModuleType:
    path = EXAMPLE / "run_media_sync_baseline.py"
    spec = importlib.util.spec_from_file_location("media_sync_baseline", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _compound_routing_baseline_module() -> ModuleType:
    path = EXAMPLE / "run_compound_routing_baseline.py"
    spec = importlib.util.spec_from_file_location("compound_routing_baseline", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
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


def test_media_sync_screen_has_a_safe_deterministic_control() -> None:
    path = EXAMPLE / "media-sync-safety-screen-v1.json"
    suite = _object(path)
    case_ids = [case["case_id"] for case in suite["cases"]]

    assert suite["workload_id"] == "media-sync-safety-screen-v1"
    assert suite["workload_unit"] == "proposed_pair"
    assert suite["unsafe_predictions_by_expected"] == {
        "link": [],
        "separate": ["link"],
        "abstain": ["link", "separate"],
    }
    assert len(case_ids) == len(set(case_ids)) == 24
    assert {case["expected"] for case in suite["cases"]} == {
        "link",
        "separate",
        "abstain",
    }

    result = _media_baseline_module().run(path)
    assert result["correct_count"] == result["case_count"] == 24
    assert result["handled_count"] == 14
    assert result["unsafe_count"] == 0

    summary = _object(EXAMPLE / "media-sync-jev-r6-summary.json")
    assert summary["suite"]["case_manifest_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert summary["jev"]["observations"] == 144
    assert summary["jev"]["unsafe_count"] == 18

    laya_summary = _object(EXAMPLE / "media-sync-laya-r6-summary.json")
    assert (
        laya_summary["suite"]["case_manifest_sha256"]
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert laya_summary["laya"]["observations"] == 144
    assert laya_summary["laya"]["unsafe_count"] == 72
    assert laya_summary["laya"]["deterministic_across_repetitions"] is True


def test_compound_routing_stress_screen_is_balanced_and_contrastive() -> None:
    path = EXAMPLE / "compound-routing-stress-screen-v2.json"
    suite = _object(path)
    cases = suite["cases"]
    labels = set(suite["question"]["criteria"])

    assert suite["workload_id"] == "compound-routing-stress-screen-v2"
    assert suite["workload_unit"] == "routing_packet"
    assert len(cases) == 36
    assert len({case["case_id"] for case in cases}) == 36

    by_route: dict[str, list[dict[str, Any]]] = {}
    by_contrast: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        by_route.setdefault(case["expected"], []).append(case)
        by_contrast.setdefault(case["contrast_set"], []).append(case)
        assert case["expected"] in labels
        assert set(case["unsafe_predictions"]).issubset(labels - {case["expected"]})

    assert set(by_route) == labels
    assert {route: len(rows) for route, rows in by_route.items()} == {
        route: 6 for route in labels
    }
    assert len(by_contrast) == 18
    for pair in by_contrast.values():
        assert len(pair) == 2
        assert pair[0]["expected"] != pair[1]["expected"]
        changed_fields = {
            field
            for field in pair[0]["state"]
            if pair[0]["state"][field] != pair[1]["state"][field]
        }
        assert len(changed_fields) == 1
        assert pair[0]["state"].keys() == pair[1]["state"].keys()

    result = _compound_routing_baseline_module().run(path)
    assert result["case_count"] == result["correct_count"] == 36
    assert result["contrast_set_count"] == 18
    assert result["unsafe_count"] == 0


def test_case_specific_unsafe_policy_overrides_suite_default() -> None:
    runner = _runner_module()
    suite = {"unsafe_predictions_by_expected": {"human_review": []}}
    case = {"unsafe_predictions": ["remote_model"]}

    assert runner._unsafe_action(
        suite,
        expected="human_review",
        predicted="remote_model",
        case=case,
    )
    assert not runner._unsafe_action(
        suite,
        expected="human_review",
        predicted="local_general",
        case=case,
    )


def test_single_runner_accepts_compound_routing_stress_screen() -> None:
    runner = _runner_module()
    labels = [
        "deterministic",
        "local_specialist",
        "local_general",
        "remote_model",
        "model_plus_verifier",
        "human_review",
    ]

    class FakeContext:
        def __enter__(self):
            class FakeClient:
                def system_one(self, state: Any, questions: dict[str, Any]):
                    del state
                    question_name = next(iter(questions))
                    return SimpleNamespace(
                        choices={
                            question_name: SimpleNamespace(
                                choice="human_review",
                                probabilities={label: 1 / len(labels) for label in labels},
                            )
                        },
                        usage=SimpleNamespace(input_tokens=10, output_tokens=0),
                        debug={},
                    )

            return FakeClient()

        def __exit__(self, *args: Any) -> None:
            del args

    runner._client = lambda _candidate: FakeContext()
    result = runner.run(
        EXAMPLE / "compound-routing-stress-screen-v2.json",
        EXAMPLE / "qwen35-9b-5060ti-media-direct-candidate.json",
        repetitions=1,
    )
    validated = StructuredDecisionRun.model_validate(result)

    assert result["workload_id"] == "compound-routing-stress-screen-v2"
    assert result["workload_unit"] == "routing_packet"
    assert validated.workload.case_count == 36
    assert len(validated.results) == 36
    assert not any(item.unsafe_action for item in validated.results)
    assert {item.decision_choice for item in validated.results} == {"human_review"}


def test_single_runner_retains_probability_diagnostics_and_suite_workload() -> None:
    runner = _runner_module()
    suite = _object(EXAMPLE / "media-sync-safety-screen-v1.json")

    class FakeContext:
        def __enter__(self):
            class FakeClient:
                def system_one(self, state: Any, questions: dict[str, Any]):
                    del state
                    question_name = next(iter(questions))
                    return SimpleNamespace(
                        choices={
                            question_name: SimpleNamespace(
                                choice="abstain",
                                probabilities={"link": 0.2, "separate": 0.3, "abstain": 0.5},
                            )
                        },
                        usage=SimpleNamespace(input_tokens=10, output_tokens=0),
                        debug={},
                    )

            return FakeClient()

        def __exit__(self, *args: Any) -> None:
            del args

    runner._client = lambda _candidate: FakeContext()
    result = runner.run(
        EXAMPLE / "media-sync-safety-screen-v1.json",
        EXAMPLE / "qwen35-9b-5060ti-media-direct-candidate.json",
        repetitions=1,
    )
    validated = StructuredDecisionRun.model_validate(result)

    assert result["workload_id"] == "media-sync-safety-screen-v1"
    assert result["workload_unit"] == "proposed_pair"
    assert all(item.decision_max_probability == Decimal("0.5") for item in validated.results)
    assert all(item.expected_probability is not None for item in validated.results)
    assert {item.decision_choice for item in validated.results} == {"abstain"}
    serialized = json.dumps(result)
    assert '"state"' not in serialized
    assert '"expected"' not in serialized

    assert runner._unsafe_action(
        {"unsafe_predictions_by_expected": suite["unsafe_predictions_by_expected"]},
        expected="separate",
        predicted="link",
    )
    assert not runner._unsafe_action(
        {"unsafe_predictions_by_expected": suite["unsafe_predictions_by_expected"]},
        expected="separate",
        predicted="abstain",
    )


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
    jev_openrouter = _object(EXAMPLE / "jev-openrouter-candidate.json")
    qwen = _object(EXAMPLE / "qwen38-local-candidate.json")
    qwen_direct = _object(EXAMPLE / "qwen38-direct-logits-candidate.json")
    qwen35_direct = _object(EXAMPLE / "qwen35-4b-direct-logits-candidate.json")
    gpt_oss = _object(EXAMPLE / "gpt-oss-20b-local-candidate.json")
    laya = _object(EXAMPLE / "laya-typed-media-cpu-candidate.json")
    jev_offering = OfferingKey.model_validate(jev["offering"])
    jev_openrouter_offering = OfferingKey.model_validate(jev_openrouter["offering"])
    qwen_offering = OfferingKey.model_validate(qwen["offering"])
    qwen_direct_offering = OfferingKey.model_validate(qwen_direct["offering"])
    qwen35_direct_offering = OfferingKey.model_validate(qwen35_direct["offering"])
    gpt_oss_offering = OfferingKey.model_validate(gpt_oss["offering"])
    laya_offering = OfferingKey.model_validate(laya["offering"])

    assert (
        len(
            {
                jev_offering.offering_id,
                jev_openrouter_offering.offering_id,
                qwen_offering.offering_id,
                qwen_direct_offering.offering_id,
                qwen35_direct_offering.offering_id,
                gpt_oss_offering.offering_id,
                laya_offering.offering_id,
            }
        )
        == 7
    )
    assert (
        jev_offering.capabilities
        == jev_openrouter_offering.capabilities
        == qwen_offering.capabilities
        == qwen_direct_offering.capabilities
        == qwen35_direct_offering.capabilities
        == ("structured-decisions",)
    )
    assert gpt_oss_offering.capabilities == ("structured-decisions",)
    assert laya_offering.capabilities == ("structured-decisions",)
    assert laya["backend"]["revision"] == "f9ab0b228f0fc0f14d873dbc99038f135c2da1b2"
    assert gpt_oss["resource_class"] == "light"
    assert gpt_oss["backend"]["reasoning_effort"] == "low"
    assert gpt_oss["backend"]["structured_outputs"] is False
    assert qwen["backend"]["max_tokens"] == 512
    assert qwen["backend"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert qwen["measurement_conditions"]["model_residency"] == "warm"
    assert jev_openrouter["backend"]["kind"] == "openrouter-decisions"
    assert jev_openrouter["cost_basis"] == "provider_reported"
    assert qwen_direct["backend"]["prompt_version"] == "semif/direct-options-v1"
    assert len(qwen_direct["backend"]["source_revision"]) == 40
    assert qwen_direct["offering"]["quantization"] == "gguf-ud-q4-k-m"
    assert len(qwen_direct["measurement_conditions"]["model_artifact_sha256"]) == 64
    assert qwen35_direct["resource_class"] == "light"
    assert len(qwen35_direct["measurement_conditions"]["model_artifact_sha256"]) == 64

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

    granite_bfcl = _object(EXAMPLE / "granite4-3b-8bit-omlx-bfcl-candidate.json")
    qwen_bfcl = _object(EXAMPLE / "qwen38-oq4e-bfcl-candidate.json")
    veto = _object(EXAMPLE / "granite4-qwen38-no-call-veto-cascade.json")
    replacement = _object(EXAMPLE / "granite4-qwen38-single-tool-review-cascade.json")
    assert OfferingKey.model_validate(granite_bfcl["offering"]).capabilities == ("tools",)
    assert OfferingKey.model_validate(qwen_bfcl["offering"]).capabilities == ("tools",)
    assert OfferingKey.model_validate(veto["offering"]).capabilities == (
        "compound-system",
        "tools",
    )
    assert veto["primary_component_id"] == granite_bfcl["component_id"]
    assert veto["fallback_component_id"] == qwen_bfcl["component_id"]
    assert veto["routing_policy"]["resolution"] == "fallback-vetoes-primary-tool-call"
    assert replacement["routing_policy"]["resolution"] == "fallback-replaces-primary"
    assert veto["measurement_conditions"]["co_resident"] is True

    direct_compound = _object(EXAMPLE / "qwen35-qwen38-direct-light-gate-cascade.json")
    direct_compound_offering = OfferingKey.model_validate(direct_compound["offering"])
    assert direct_compound_offering.capabilities == (
        "compound-system",
        "structured-decisions",
    )
    assert direct_compound["routing_policy"]["invoke_worker_on_choices"] == [
        "heavy",
        "abstain",
    ]
    assert direct_compound["routing_policy"]["preserve_choices_without_review"] == ["light"]
    assert direct_compound["measurement_conditions"]["co_resident"] is True

    needle = _object(EXAMPLE / "needle3-local-candidate.json")
    needle_guard = _object(EXAMPLE / "needle3-jev-exact-call-guard.json")
    assert OfferingKey.model_validate(needle["offering"]).capabilities == (
        "home-automation",
        "media-control",
        "tools",
    )
    assert OfferingKey.model_validate(needle_guard["offering"]).capabilities == (
        "compound-system",
        "home-automation",
        "media-control",
        "tools",
    )
    assert needle_guard["worker_component_id"] == needle["component_id"]
    assert needle_guard["routing_policy"]["repair_behavior"].startswith("none")


def test_needle_manifest_pins_open_runtime_and_exact_environment_sources() -> None:
    manifest = _object(EXAMPLE / "needle-environments-v3.0.1-manifest.json")

    assert manifest["license"] == "Apache-2.0"
    assert manifest["suite_version"] == "cactus-needle==3.0.1"
    assert manifest["source"]["repository_revision"] == ("366b43cd8593fb520695c1bb89b9c3080762961b")
    assert sum(environment["case_count"] for environment in manifest["environments"].values()) == 64
    assert manifest["installed_artifacts"]["weights_bytes"] == 35_335_380
    assert len(manifest["installed_artifacts"]["weights_sha256"]) == 64


def test_frontier_recipes_keep_primitive_and_complete_system_claims_separate() -> None:
    config = load_config(EXAMPLE / "frontiers.yaml")

    assert len(config.frontiers) == 15
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
    assert config.frontiers[
        "decision-quality-vs-heavy-demand"
    ].eligibility.required_capabilities == ("structured-decisions",)
    assert config.frontiers[
        "single-turn-tool-outcome-vs-heavy-demand"
    ].eligibility.required_capabilities == ("tools",)
    assert config.frontiers[
        "voice-command-outcome-vs-latency"
    ].eligibility.required_capabilities == ("tools",)
    assert config.frontiers["voice-command-policy-vs-latency"].axes[0].metric == "tool_policy"


def test_bfcl_single_turn_catalog_and_frontiers_publish_the_matched_tradeoff() -> None:
    generated = EXAMPLE / "generated"
    catalog = load_catalog(generated / "bfcl-single-turn-r3-composed-catalog.json")
    assert catalog.workload.id == "bfcl-v4-offline-single-turn-40"
    assert catalog.workload.unit == "tool-case"
    assert len(catalog.offerings) == 3

    expected = {
        "compound/granite4-3b-primary+qwen3.8-27b-no-call-veto@m5-64gb-omlx-coresident-v1",
        "local/granite4-micro-3b-8bit@m5-64gb-omlx-bfcl-prompt-warm",
    }
    for filename in (
        "bfcl-single-turn-r3-outcome-latency-frontier.json",
        "bfcl-single-turn-r3-outcome-heavy-demand-frontier.json",
    ):
        snapshot = load_frontier_snapshot(generated / filename)
        assert {member.offering.offering_id for member in snapshot.members} == expected
        assert len(snapshot.evaluated) == 3


def test_semif_and_jev_routing_catalog_publishes_distinct_frontiers() -> None:
    generated = EXAMPLE / "generated"
    catalog = load_catalog(generated / "routing-r3-composed-catalog.json")
    assert catalog.workload.id == "structured-routing-screen-v1"
    assert catalog.workload.unit == "decision"
    assert len(catalog.offerings) == 5

    expected_by_frontier = {
        "routing-r3-decision-quality-vs-latency-frontier.json": (
            {
                "local/qwen3.5-4b-q4km@m5-64gb-llamacpp-semif-direct-coresident-v1",
                "local/qwen3.8-27b-ud-q4km@m5-64gb-llamacpp-semif-direct-v1",
            },
            5,
        ),
        "routing-r3-decision-quality-vs-calibration-frontier.json": (
            {
                "compound/qwen3.5-4b-semif-light-gate+qwen3.8-27b-semif-worker@"
                "m5-64gb-coresident-v1",
                "openrouter/typesafe-jev-1.13@decisions-default",
            },
            5,
        ),
        "routing-r3-decision-quality-vs-cost-frontier.json": (
            {"openrouter/typesafe-jev-1.13@decisions-default"},
            1,
        ),
        "routing-r3-decision-quality-vs-heavy-demand-frontier.json": (
            {
                "compound/qwen3.5-4b-semif-light-gate+qwen3.8-27b-semif-worker@"
                "m5-64gb-coresident-v1",
                "openrouter/typesafe-jev-1.13@decisions-default",
            },
            5,
        ),
        "routing-r3-compound-routing-quality-vs-heavy-demand-frontier.json": (
            {
                "compound/qwen3.5-4b-semif-light-gate+qwen3.8-27b-semif-worker@"
                "m5-64gb-coresident-v1",
            },
            1,
        ),
    }
    for filename, (expected, evaluated_count) in expected_by_frontier.items():
        snapshot = load_frontier_snapshot(generated / filename)
        assert {member.offering.offering_id for member in snapshot.members} == expected
        assert len(snapshot.evaluated) == evaluated_count


def test_needle_and_jev_publish_a_real_speed_quality_tradeoff() -> None:
    generated = EXAMPLE / "generated"
    catalog = load_catalog(generated / "needle-r3-composed-catalog.json")
    assert catalog.workload.id == "cactus-needle-v3-home-media-64"
    assert catalog.workload.unit == "voice-style-tool-case"
    assert len(catalog.offerings) == 2

    expected = {
        "cactus/needle3-3.0.1@m5-64gb-native-macos",
        "compound/needle3-3.0.1+jev-1.13-exact-call-guard@m5-openrouter-v1",
    }
    for filename in (
        "needle-r3-outcome-latency-frontier.json",
        "needle-r3-policy-latency-frontier.json",
    ):
        snapshot = load_frontier_snapshot(generated / filename)
        assert {member.offering.offering_id for member in snapshot.members} == expected
        assert len(snapshot.evaluated) == 2


def test_direct_option_logits_backend_reads_declared_label_probabilities(monkeypatch) -> None:
    runner = _runner_module()
    captured: dict[str, Any] = {}

    def fake_post_json(url, payload, *, headers=None, timeout_seconds):
        captured.update(
            url=url,
            payload=payload,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "A"},
                    "logprobs": {
                        "content": [
                            {
                                "top_logprobs": [
                                    {"token": "A", "bytes": [65], "logprob": -1.0},
                                    {"token": "B", "bytes": [66], "logprob": -2.0},
                                    {"token": "C", "bytes": [67], "logprob": -3.0},
                                ]
                            }
                        ]
                    },
                }
            ],
            "usage": {"prompt_tokens": 42, "completion_tokens": 1},
        }

    monkeypatch.setattr(runner, "_post_json", fake_post_json)
    client = runner._DirectOptionLogitsClient(
        {
            "model": "test-model",
            "base_url": "http://127.0.0.1:8080/v1",
            "prompt_version": "semif/direct-options-v1",
        }
    )
    response = client.system_one(
        {"request": "route me"},
        {
            "route": {
                "type": "choice",
                "instructions": "Choose a route.",
                "criteria": {"light": "Small", "heavy": "Large", "abstain": "Stop"},
            }
        },
    )

    assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    payload = captured["payload"]
    assert payload["max_tokens"] == 1
    assert payload["temperature"] == 1
    assert payload["logprobs"] is True
    assert payload["grammar"] == 'root ::= "A" | "B" | "C"'
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    evidence = json.loads(payload["messages"][1]["content"])
    assert evidence["options"] == [
        {"letter": "A", "description": "Small"},
        {"letter": "B", "description": "Large"},
        {"letter": "C", "description": "Stop"},
    ]
    answer = response.choices["route"]
    assert answer.choice == "light"
    assert set(answer.probabilities) == {"light", "heavy", "abstain"}
    assert sum(answer.probabilities.values()) == pytest.approx(1.0)
    assert response.usage.input_tokens == 42
    assert response.usage.output_tokens == 1


def test_openrouter_decisions_backend_preserves_reported_cost(monkeypatch) -> None:
    runner = _runner_module()

    def fake_post_json(url, payload, *, headers=None, timeout_seconds):
        del url, payload, headers, timeout_seconds
        return {
            "model": "typesafe/jev-1.13-20260917",
            "provider": "TypeSafe",
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "heavy",
                    "probabilities": {"light": 0.1, "heavy": 0.8, "abstain": 0.1},
                    "confidence": 0.7,
                }
            },
            "usage": {"input_tokens": 300, "output_tokens": 40, "cost": 0.0000126},
        }

    monkeypatch.setattr(runner, "_post_json", fake_post_json)
    client = runner._OpenRouterDecisionsClient(
        {
            "model": "typesafe/jev-1.13",
            "endpoint": "https://openrouter.ai/api/alpha/decisions",
        },
        api_key="test-key",
    )
    response = client.system_one(
        "state",
        {
            "route": {
                "type": "choice",
                "instructions": "Choose.",
                "criteria": {"light": None, "heavy": None, "abstain": None},
            }
        },
    )

    assert response.choices["route"].choice == "heavy"
    assert response.usage.cost == Decimal("0.0000126")
    assert response.debug == {
        "provider": "TypeSafe",
        "resolved_model": "typesafe/jev-1.13-20260917",
    }
    assert runner._response_cost(
        response,
        cost_basis="provider_reported",
        fixed_cost=None,
        calls=1,
    ) == Decimal("0.0000126")


def test_bfcl_contract_validation_rejects_unknown_or_incomplete_calls() -> None:
    runner = _bfcl_runner_module()
    functions = [
        {
            "name": "lookup",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
    ]

    assert runner._contract_valid([{"lookup": {"city": "Boston"}}], functions)
    assert not runner._contract_valid([{"lookup": {}}], functions)
    assert not runner._contract_valid([{"delete": {"city": "Boston"}}], functions)


def test_bfcl_no_call_veto_can_only_remove_a_valid_primary_call() -> None:
    runner = _bfcl_runner_module()
    primary = [{"lookup": {"city": "Boston"}}]
    competing_call = [{"lookup": {"city": "Cambridge"}}]

    assert runner._resolve_fallback(
        primary,
        primary_schema_valid=True,
        primary_contract_valid=True,
        fallback_decoded=[],
        fallback_schema_valid=True,
        resolution="fallback-vetoes-primary-tool-call",
    ) == ([], True)
    assert runner._resolve_fallback(
        primary,
        primary_schema_valid=True,
        primary_contract_valid=True,
        fallback_decoded=competing_call,
        fallback_schema_valid=True,
        resolution="fallback-vetoes-primary-tool-call",
    ) == (primary, True)
    assert runner._resolve_fallback(
        primary,
        primary_schema_valid=True,
        primary_contract_valid=False,
        fallback_decoded=competing_call,
        fallback_schema_valid=True,
        resolution="fallback-vetoes-primary-tool-call",
    ) == (competing_call, True)


def test_normalized_multiclass_brier_rejects_invalid_probabilities() -> None:
    runner = _runner_module()

    assert runner._brier(
        {"light": 1.0, "heavy": 0.0, "abstain": 0.0},
        "light",
        {"light", "heavy", "abstain"},
    ) == Decimal(0)

    rounded = runner._normalized_probabilities(
        {"light": 0.8, "heavy": 0.1, "abstain": 0.09},
        {"light", "heavy", "abstain"},
    )
    assert sum(rounded.values()) == Decimal(1)

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
