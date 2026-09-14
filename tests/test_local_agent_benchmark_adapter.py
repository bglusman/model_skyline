from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from model_skyline.adapters.local_agent_benchmark import (
    LOCAL_AGENT_SUMMARY_SCHEMA,
    LocalAgentBenchmarkAdapterError,
    normalize_local_agent_benchmark_bytes,
)
from model_skyline.cli import app
from model_skyline.io import load_quality_evidence
from model_skyline.quality_evidence import QualityMeasurementRole


def _component(seed: str) -> dict[str, str]:
    return {"id": f"component-{seed}", "version": "1", "configuration_sha256": seed * 64}


def _cohort(kind: str) -> dict[str, Any]:
    if kind == "researchclawbench":
        return {
            "workspace_manifest_sha256": "1" * 64,
            "source_snapshot_sha256": "2" * 64,
            "judge_model": "judge/model",
            "judge_revision": "judge-revision",
            "network_policy": "frozen",
        }
    if kind == "browsecomp":
        return {
            "encrypted_dataset_sha256": "3" * 64,
            "grader_model": "grader/model",
            "grader_revision": "grader-revision",
            "search_provider": "search-provider",
            "browser_provider": "browser-provider",
            "execution_window_started_at": "2026-09-13T00:00:00Z",
            "execution_window_finished_at": "2026-09-13T01:00:00Z",
            "tool_budget": 100,
        }
    return {
        "attachment_manifest_sha256": "4" * 64,
        "attachment_policy": "pinned-private",
        "web_policy": "live-web",
        "dataset_access": "gated",
    }


def _summary(kind: str = "researchclawbench") -> dict[str, Any]:
    tasks: list[dict[str, Any]] = [
        {
            "task_sha256": "5" * 64,
            "score_percent": "60" if kind == "researchclawbench" else "100",
            "wall_seconds": "10",
            "tool_checks": {"passed": 1, "total": 1},
        },
        {
            "task_sha256": "6" * 64,
            "score_percent": "80" if kind == "researchclawbench" else "0",
            "wall_seconds": "20",
            "tool_checks": {"passed": 2, "total": 2},
        },
    ]
    if kind == "researchclawbench":
        tasks[0]["grounding_checks"] = {"passed": 2, "total": 2}
        tasks[1]["grounding_checks"] = {"passed": 3, "total": 3}
    task_manifest_sha256 = hashlib.sha256(
        "\n".join(sorted(task["task_sha256"] for task in tasks)).encode("ascii")
    ).hexdigest()
    return {
        "schema_version": LOCAL_AGENT_SUMMARY_SCHEMA,
        "contains_prompts_or_model_messages": False,
        "benchmark": {
            "kind": kind,
            "dataset_id": f"dataset-{kind}",
            "dataset_revision": "revision-1",
            "split": "validation",
            "task_manifest_sha256": task_manifest_sha256,
            "task_count": 2,
            "task_set_kind": "screen",
            "harness": _component("a"),
            "scorer": _component("b"),
            "protocol": _component("c"),
            "cohort": _cohort(kind),
        },
        "subject": {
            "row_id": "local-model-row",
            "system_label": "Local model exact artifact",
            "model_id": "publisher/model",
            "model_revision": "model-revision",
            "artifact_sha256": "8" * 64,
            "quantization": "mlx-4bit",
            "runtime_id": "omlx",
            "runtime_version": "0.6.4",
            "runtime_configuration_sha256": "9" * 64,
            "benchmark_agent": _component("d"),
            "reasoning_claims": {
                "mode": "enabled",
                "budget": 4096,
                "thinking_preserved": True,
            },
            "attempt_claims": {"concurrency": 1, "attempts_per_task": 1},
        },
        "rights": {
            "license_expression": "upstream terms; derived task statistics only",
            "terms_locator": "https://example.test/terms",
            "publication_permission": "derived_only",
            "reviewed_at": "2026-09-12T00:00:00Z",
            "review_evidence": "Task contents are omitted; derived aggregate publication reviewed.",
        },
        "observed_at": "2026-09-13T01:00:00Z",
        "tasks": tasks,
    }


def _normalize(value: dict[str, Any]):
    return normalize_local_agent_benchmark_bytes(
        json.dumps(value).encode(),
        retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
        source_locator="private/local-summary.json",
    )


@pytest.mark.parametrize(
    ("kind", "quality_signal"),
    (
        ("researchclawbench", "measured_research_task_quality_percent"),
        ("browsecomp", "measured_browsing_answer_accuracy_percent"),
        ("gaia", "measured_general_assistant_exact_match_percent"),
    ),
)
def test_normalizes_real_world_benchmark_statistics(kind: str, quality_signal: str) -> None:
    evidence = _normalize(_summary(kind))
    row = evidence.rows[0]
    assert row.result is not None
    measurements = {item.id: item for item in row.result.measurements}

    assert measurements[quality_signal].value == (70 if kind == "researchclawbench" else 50)
    assert measurements[quality_signal].sample_count == 2
    assert measurements["p95_agent_task_wall_seconds"].value == Decimal("19.5")
    tool = measurements["local_tool_call_success_percent"]
    assert (tool.value, tool.lower, tool.upper, tool.sample_count) == (100, 100, 100, 3)
    assert evidence.source_identity.scope["task_set_kind"] == "screen"
    assert evidence.raw_audit.metadata["contains_prompts_or_model_messages"] is False
    if kind == "researchclawbench":
        grounding = measurements["research_evidence_grounding_success_percent"]
        assert (grounding.value, grounding.sample_count) == (100, 5)
    else:
        assert "research_evidence_grounding_success_percent" not in measurements


def test_reviewed_quality_projection_drops_route_specific_latency_and_gates() -> None:
    result = _normalize(_summary()).rows[0].result
    assert result is not None

    projected = result.quality_projection()

    assert {item.id for item in projected.measurements} == {
        "measured_research_task_quality_percent"
    }
    assert all(item.role is QualityMeasurementRole.QUALITY for item in projected.measurements)


def test_rejects_task_contents_and_unknown_fields() -> None:
    value = _summary()
    value["tasks"][0]["prompt"] = "private question"

    with pytest.raises(LocalAgentBenchmarkAdapterError, match="reviewed field contract"):
        _normalize(value)


def test_rejects_nonbinary_browsecomp_or_gaia_score() -> None:
    value = _summary("browsecomp")
    value["tasks"][0]["score_percent"] = "50"

    with pytest.raises(LocalAgentBenchmarkAdapterError, match="must be binary"):
        _normalize(value)


def test_rejects_duplicate_tasks_or_mismatched_full_count() -> None:
    duplicate = _summary()
    duplicate["tasks"][1]["task_sha256"] = duplicate["tasks"][0]["task_sha256"]
    with pytest.raises(LocalAgentBenchmarkAdapterError, match="must be unique"):
        _normalize(duplicate)

    wrong_full = _summary("gaia")
    wrong_full["benchmark"]["task_set_kind"] = "full"
    with pytest.raises(LocalAgentBenchmarkAdapterError, match="full benchmark task_count"):
        _normalize(wrong_full)


def test_rejects_task_digest_set_mismatch_or_aggregated_attempt_claim() -> None:
    wrong_manifest = _summary()
    wrong_manifest["benchmark"]["task_manifest_sha256"] = "7" * 64
    with pytest.raises(LocalAgentBenchmarkAdapterError, match="canonical task digest set"):
        _normalize(wrong_manifest)

    repeated = _summary()
    repeated["subject"]["attempt_claims"]["attempts_per_task"] = 2
    with pytest.raises(LocalAgentBenchmarkAdapterError, match="one recorded attempt"):
        _normalize(repeated)


def test_rejects_unrestricted_browsecomp_or_gaia_publication_claim() -> None:
    value = _summary("gaia")
    value["rights"]["publication_permission"] = "unrestricted"

    with pytest.raises(LocalAgentBenchmarkAdapterError, match="cannot claim unrestricted"):
        _normalize(value)


def test_rejects_future_browsecomp_execution_window() -> None:
    value = _summary("browsecomp")
    value["benchmark"]["cohort"]["execution_window_finished_at"] = "2026-09-15T00:00:00Z"

    with pytest.raises(LocalAgentBenchmarkAdapterError, match="execution window"):
        _normalize(value)


def test_cli_writes_private_normalized_evidence(tmp_path: Path) -> None:
    summary = tmp_path / "summary.json"
    output = tmp_path / "evidence.json"
    summary.write_text(json.dumps(_summary()), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "normalize-local-agent-benchmark",
            str(summary),
            "--retrieved-at",
            "2026-09-14T00:00:00Z",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    evidence = load_quality_evidence(output)
    assert evidence.rows[0].row_id == "local-model-row"


def test_source_identity_is_independent_of_subject_and_result() -> None:
    first = _normalize(_summary())
    changed = deepcopy(_summary())
    changed["subject"]["model_id"] = "publisher/other"
    changed["tasks"][0]["score_percent"] = "10"
    second = _normalize(changed)

    assert first.source_identity_sha256 == second.source_identity_sha256
    assert first.rows[0].subject_identity_sha256 != second.rows[0].subject_identity_sha256
    assert first.rows[0].result_sha256 != second.rows[0].result_sha256
