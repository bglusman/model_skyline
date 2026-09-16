from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from model_skyline.adapters.structured_decisions import (
    STRUCTURED_DECISION_RUN_SCHEMA_VERSION,
    StructuredDecisionAdapterError,
    normalize_structured_decision_bytes,
    normalize_structured_decision_file,
    structured_decision_case_set_sha256,
)
from model_skyline.cli import app
from model_skyline.io import load_catalog


def _offering(
    offering_id: str,
    model_id: str,
    *,
    capabilities: list[str],
    agent_harness: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "offering_id": offering_id,
        "model_id": model_id,
        "provider": "local-test",
        "endpoint": "http://127.0.0.1:8090/v1",
        "billing_mode": "self-hosted",
        "quantization": "test-4bit",
        "capabilities": capabilities,
    }
    if agent_harness is not None:
        value["agent_harness"] = agent_harness
    return value


def _result(
    case_id: str,
    repetition: int,
    *,
    correct: bool,
    latency: str,
    cost: str | None,
    abstained: bool = False,
    schema_valid: bool = True,
    model_calls: int = 1,
    heavy_model_calls: int = 0,
    component_id: str = "decision",
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "case_sha256": ("a" if case_id == "case-a" else "b") * 64,
        "repetition": repetition,
        "decision_correct": correct,
        "final_success": correct,
        "schema_valid": schema_valid,
        "abstained": abstained,
        "unsafe_action": False,
        "latency_seconds": latency,
        "total_cost_usd": cost,
        "model_calls": model_calls,
        "heavy_model_calls": heavy_model_calls,
        "component_usage": [
            {
                "component_id": component_id,
                "calls": model_calls,
                "cost_usd": cost,
            }
        ],
        "brier_score": "0.25",
        "tool_selection_correct": correct,
        "tool_arguments_correct": correct,
        "tool_sequence_correct": None,
        "tool_policy_compliant": True,
        "tool_side_effects_correct": None,
    }


def _decision_run() -> dict[str, Any]:
    offering = _offering(
        "typesafe/jev@api",
        "typesafe/jev",
        capabilities=["structured-decisions"],
    )
    results = [
        _result("case-a", 1, correct=True, latency="0.1", cost="0.001"),
        _result(
            "case-a",
            2,
            correct=False,
            latency="0.2",
            cost="0.002",
            abstained=True,
        ),
        _result("case-b", 1, correct=True, latency="0.3", cost="0.003"),
        _result(
            "case-b",
            2,
            correct=False,
            latency="0.4",
            cost="0.004",
            schema_valid=False,
        ),
    ]
    return {
        "schema_version": STRUCTURED_DECISION_RUN_SCHEMA_VERSION,
        "observed_at": "2026-09-16T12:00:00Z",
        "workload_id": "structured-routing-screen-v1",
        "workload_unit": "decision",
        "workload": {
            "suite_id": "model-skyline/structured-routing-screen",
            "suite_version": "1",
            "case_manifest_sha256": "c" * 64,
            "case_set_sha256": structured_decision_case_set_sha256(
                (("case-a", "a" * 64), ("case-b", "b" * 64))
            ),
            "case_count": 2,
            "harness_id": "system-one-comparison",
            "harness_version": "1",
            "scorer_version": "1",
            "oracle_kind": "deterministic",
            "repetitions_per_case": 2,
            "concurrency": 1,
        },
        "benchmark_source": {
            "id": "model-skyline/structured-routing-screen",
            "version": "1",
            "url": "https://github.com/bglusman/model_skyline",
            "license": "CC0-1.0",
            "methodology": "Public deterministic routing calibration screen.",
            "raw_sha256": "c" * 64,
            "retrieved_at": "2026-09-16T11:00:00Z",
        },
        "offering": offering,
        "system": {
            "kind": "decision_component",
            "routing_policy_sha256": "d" * 64,
            "components": [
                {
                    "component_id": "decision",
                    "role": "decision",
                    "resource_class": "light",
                    "activation": "always",
                    "offering": offering,
                    "guidance_sha256": "e" * 64,
                }
            ],
        },
        "cost_basis": "billed",
        "results": results,
        "metadata": {"candidate_family": "decision-component"},
    }


def _normalize(value: dict[str, Any]):
    return normalize_structured_decision_bytes(
        json.dumps(value).encode(),
        retrieved_at=datetime(2026, 9, 16, 13, tzinfo=UTC),
    )


def test_normalizes_decision_and_tool_subdimensions() -> None:
    catalog = _normalize(_decision_run())
    row = catalog.offerings[0]
    signals = row.signals

    assert signals["structured_decision_accuracy_percent"].value == 50
    assert signals["structured_final_success_percent"].value == 50
    assert signals["structured_schema_valid_percent"].value == 75
    assert signals["structured_autonomous_coverage_percent"].value == 75
    assert signals["structured_autonomous_accuracy_percent"].value == Decimal(
        "66.66666666666666666666666666666667"
    )
    assert signals["structured_p95_latency_seconds"].value == Decimal("0.385")
    assert signals["structured_mean_cost_usd_per_case"].value == Decimal("0.0025")
    assert signals["structured_cost_usd_per_success"].value == Decimal("0.005")
    assert signals["structured_mean_brier_score"].value == Decimal("0.25")
    assert signals["structured_tool_selection_accuracy_percent"].value == 50
    assert signals["structured_tool_argument_accuracy_percent"].value == 50
    assert signals["structured_tool_policy_compliance_percent"].value == 100
    assert "structured_tool_sequence_accuracy_percent" not in signals
    assert row.metadata["contains_prompt_or_response_text"] is False
    assert row.metadata["compound"] is False
    assert row.default_source is not None
    assert str(row.default_source.url) == "https://github.com/bglusman/model_skyline"
    assert row.default_source.license == "CC0-1.0"
    assert "Public deterministic routing" in (row.default_source.methodology or "")


def test_compound_system_gets_final_credit_but_retains_router_and_worker_demand() -> None:
    value = _decision_run()
    router = _offering(
        "local/small-router@mlx",
        "example/small-router",
        capabilities=["structured-decisions"],
    )
    worker = _offering(
        "local/heavy-worker@mlx",
        "example/heavy-worker",
        capabilities=["text", "tools"],
    )
    value["offering"] = _offering(
        "compound/small-router+heavy-worker@policy-v1",
        "compound/small-router+heavy-worker",
        capabilities=["compound-system", "tools"],
        agent_harness="model-skyline/router-worker-policy-v1",
    )
    value["system"] = {
        "kind": "compound_model_system",
        "routing_policy_sha256": "1" * 64,
        "components": [
            {
                "component_id": "router",
                "role": "router",
                "resource_class": "light",
                "activation": "always",
                "offering": router,
                "guidance_sha256": "2" * 64,
            },
            {
                "component_id": "worker",
                "role": "worker",
                "resource_class": "heavy",
                "activation": "router_selected",
                "offering": worker,
                "guidance_sha256": "3" * 64,
            },
        ],
    }
    value["cost_basis"] = "unavailable"
    for index, result in enumerate(value["results"]):
        result["total_cost_usd"] = None
        result["final_success"] = True
        result["schema_valid"] = True
        result["model_calls"] = 2 if index in {1, 3} else 1
        result["heavy_model_calls"] = 1 if index in {1, 3} else 0
        result["component_usage"] = [
            {"component_id": "router", "calls": 1, "cost_usd": None},
            {
                "component_id": "worker",
                "calls": 1 if index in {1, 3} else 0,
                "cost_usd": None,
            },
        ]
        result["tool_selection_correct"] = True
        result["tool_arguments_correct"] = True

    row = _normalize(value).offerings[0]

    assert row.signals["structured_decision_accuracy_percent"].value == 50
    assert row.signals["structured_final_success_percent"].value == 100
    assert row.signals["structured_mean_model_calls_per_case"].value == Decimal("1.5")
    assert row.signals["structured_mean_heavy_model_calls_per_case"].value == Decimal("0.5")
    assert "structured_mean_cost_usd_per_case" not in row.signals
    assert row.metadata["compound"] is True
    assert row.metadata["component_count"] == 2


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda value: value["results"].pop(), "exact configured repetitions"),
        (
            lambda value: value["results"][0].update(total_cost_usd=None),
            "requires cost for every result",
        ),
        (
            lambda value: value["results"][0].update(heavy_model_calls=2),
            "cannot exceed model_calls",
        ),
    ),
)
def test_rejects_incomplete_or_incoherent_runs(mutate: Any, message: str) -> None:
    value = _decision_run()
    mutate(value)

    with pytest.raises(StructuredDecisionAdapterError, match=message):
        _normalize(value)


def test_rejects_duplicate_json_keys_without_echoing_contents() -> None:
    with pytest.raises(StructuredDecisionAdapterError, match="duplicate JSON key"):
        normalize_structured_decision_bytes(
            b'{"schema_version":"first","schema_version":"second"}',
            retrieved_at=datetime(2026, 9, 16, tzinfo=UTC),
        )


def test_rejects_symlink_input(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    target.write_text(json.dumps(_decision_run()), encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(StructuredDecisionAdapterError, match="cannot open"):
        normalize_structured_decision_file(
            link,
            retrieved_at=datetime(2026, 9, 16, tzinfo=UTC),
        )


def test_cli_writes_observation_catalog(tmp_path: Path) -> None:
    run = tmp_path / "run.json"
    output = tmp_path / "catalog.json"
    run.write_text(json.dumps(_decision_run()), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "normalize-structured-decision-run",
            str(run),
            "--retrieved-at",
            "2026-09-16T13:00:00Z",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    catalog = load_catalog(output)
    assert catalog.offerings[0].offering.model_id == "typesafe/jev"


def test_decision_component_requires_decision_capability_and_identity_shape() -> None:
    missing_capability = _decision_run()
    missing_capability["offering"]["capabilities"] = []
    with pytest.raises(StructuredDecisionAdapterError, match="structured-decisions"):
        _normalize(missing_capability)

    wrong_shape = deepcopy(_decision_run())
    wrong_shape["system"]["components"][0]["role"] = "worker"
    with pytest.raises(StructuredDecisionAdapterError, match="exactly one decision"):
        _normalize(wrong_shape)


def test_rejects_prompt_or_response_text_bags_without_echoing_value() -> None:
    value = _decision_run()
    sensitive_marker = "do-not-echo-this-example-prompt"
    value["metadata"] = {"nested": [{"prompt": sensitive_marker}]}

    with pytest.raises(StructuredDecisionAdapterError) as captured:
        _normalize(value)

    assert "metadata cannot contain prompts" in str(captured.value)
    assert sensitive_marker not in str(captured.value)
