from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from model_skyline.adapters import mcpmark as mcpmark_adapter
from model_skyline.adapters.mcpmark import (
    MCPMARK_SECTIONS,
    MCPMARK_VERIFIED_COMMIT,
    MCPMARK_VERIFIED_SHA256,
    MCPMARK_VERIFIED_URL,
    MCPMarkAdapterError,
    build_mcpmark_project_config,
    catalogs_from_mcpmark_bytes,
    fetch_mcpmark_catalogs,
    load_mcpmark_catalogs,
    write_mcpmark_import,
)
from model_skyline.cli import app
from model_skyline.engine import FrontierEngine
from model_skyline.io import load_catalog, load_config

RETRIEVED_AT = datetime(2026, 8, 30, 12, tzinfo=UTC)
runner = CliRunner()


def _complete_row(
    *,
    score: float = 0.6667,
    total_tasks: int = 3,
    per_run_input_tokens: int | None = None,
    per_run_output_tokens: int | None = None,
) -> dict[str, Any]:
    total_input_tokens = 100 * total_tasks
    total_output_tokens = 10 * total_tasks
    return {
        "total_tasks": total_tasks,
        "total_agent_execution_time": 1.2345 * total_tasks,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "total_turns": 2 * total_tasks,
        "avg_agent_execution_time": 1.2345,
        "avg_input_tokens": 100.0,
        "avg_output_tokens": 10.0,
        "avg_total_tokens": 110.0,
        "avg_turns": 2.0,
        "per_run_input_tokens": (
            total_input_tokens if per_run_input_tokens is None else per_run_input_tokens
        ),
        "per_run_output_tokens": (
            total_output_tokens if per_run_output_tokens is None else per_run_output_tokens
        ),
        "per_run_cost": None,
        "actual_model_name": "gpt-5.5",
        "is_open_source_model": False,
        "is_reasoning_model": True,
        "pass@1": {"avg": score, "std": 0.0},
    }


def _scaled_row(row: dict[str, Any], factor: int) -> dict[str, Any]:
    result = {**row, "pass@1": dict(row["pass@1"])}
    for field in (
        "total_tasks",
        "total_agent_execution_time",
        "total_input_tokens",
        "total_output_tokens",
        "total_tokens",
        "total_turns",
        "per_run_input_tokens",
        "per_run_output_tokens",
    ):
        if result[field] is not None:
            result[field] *= factor
    return result


def _scores_only_row(total_tasks: int) -> dict[str, Any]:
    result = _complete_row(total_tasks=total_tasks)
    for field in (
        "total_agent_execution_time",
        "total_input_tokens",
        "total_output_tokens",
        "total_tokens",
        "total_turns",
        "avg_agent_execution_time",
        "avg_input_tokens",
        "avg_output_tokens",
        "avg_total_tokens",
        "avg_turns",
        "per_run_input_tokens",
        "per_run_output_tokens",
    ):
        result[field] = None
    result["actual_model_name"] = "scores-only-model"
    result["scores_only"] = True
    return result


def _summary_bytes(*, row: dict[str, Any] | None = None) -> bytes:
    valid = row or _complete_row()
    component_section = {
        "gpt-5-5-xhigh": valid,
        "scores-only": _scores_only_row(valid["total_tasks"]),
    }
    overall_section = {
        alias: _scaled_row(model_row, len(MCPMARK_SECTIONS) - 1)
        for alias, model_row in component_section.items()
    }
    value: dict[str, Any] = {
        "generated_at": "2026-07-20T16:05:27.844147",
        "k": 4,
        "experiment_name": "verified",
        "task_set": "standard",
        "task_version": "verified",
        "overall": overall_section,
    }
    value.update({name: component_section for name in MCPMARK_SECTIONS[1:]})
    return json.dumps(value, separators=(",", ":")).encode()


def _mutate_summary(mutator: Any) -> bytes:
    value = json.loads(_summary_bytes())
    mutator(value)
    return json.dumps(value, separators=(",", ":")).encode()


def _adapt(raw: bytes) -> dict[str, Any]:
    return catalogs_from_mcpmark_bytes(
        raw,
        source_url="https://example.test/verified/summary.json",
        source_version="fixture-v1",
        required_sha256=hashlib.sha256(raw).hexdigest(),
        retrieved_at=RETRIEVED_AT,
    )


def test_adapts_all_sections_with_exact_values_and_narrow_identity() -> None:
    raw = _summary_bytes()
    catalogs = _adapt(raw)

    assert tuple(catalogs) == MCPMARK_SECTIONS
    for section, catalog in catalogs.items():
        assert catalog.workload.id == f"mcpmark-summary-{section}"
        assert catalog.workload.unit == "task"
        assert len(catalog.offerings) == 1

    offering = catalogs["filesystem"].offerings[0]
    assert offering.signals["pass_at_1"].value == Decimal("0.6667")
    assert offering.signals["avg_agent_seconds"].value == Decimal("1.2345")
    assert offering.signals["avg_input_tokens"].value == Decimal("100.0")
    assert offering.signals["avg_output_tokens"].value == Decimal("10.0")
    assert offering.signals["avg_turns"].value == Decimal("2")
    assert offering.offering.offering_id == ("mcpmark/gpt-5-5-xhigh@mcpmark:standard@verified")
    assert offering.offering.model_id == "gpt-5.5"
    assert offering.offering.provider == "unknown"
    assert offering.offering.reasoning_effort == "xhigh"
    assert offering.offering.agent_harness == "mcpmark:standard@verified"


def test_provenance_is_explicit_and_does_not_claim_cost_or_provider_route() -> None:
    raw = _summary_bytes()
    offering = _adapt(raw)["overall"].offerings[0]
    source = offering.default_source

    assert source is not None
    assert source.license == "NOASSERTION"
    assert source.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert source.retrieved_at == RETRIEVED_AT
    assert source.url is not None and str(source.url) == (
        "https://example.test/verified/summary.json"
    )
    assert source.methodology is not None
    assert "Operator-supplied MCPMark-shaped" in source.methodology
    assert "does not assert its repository origin" in source.methodology
    assert "pinned revision" not in source.methodology
    assert "verified/README.md" not in source.methodology
    assert "cost are not reported" in source.methodology
    assert source.id.startswith("mcpmark-summary-")
    assert source.version == "fixture-v1"
    assert offering.signals.keys() == {
        "pass_at_1",
        "avg_agent_seconds",
        "avg_input_tokens",
        "avg_output_tokens",
        "avg_turns",
    }
    assert all(observation.observed_at is None for observation in offering.signals.values())
    assert offering.metadata["source_generated_at_timezone"] == "unspecified"


def test_only_exact_pinned_digest_receives_verified_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _summary_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(mcpmark_adapter, "MCPMARK_VERIFIED_SHA256", digest)

    catalogs = _adapt(raw)
    source = catalogs["overall"].offerings[0].default_source

    assert source is not None
    assert source.id.startswith("mcpmark-experiments-verified-")
    assert source.version == MCPMARK_VERIFIED_COMMIT
    assert source.methodology is not None
    assert "MCPMark pinned verified" in source.methodology
    assert "verified/README.md" in source.methodology
    assert "Operator-supplied" not in source.methodology
    assert catalogs["overall"].workload.id == "mcpmark-verified-overall"


def test_wilson_bounds_require_a_uniquely_recoverable_single_run_count() -> None:
    single_run = _adapt(_summary_bytes())["filesystem"].offerings[0]
    estimate = single_run.signals["pass_at_1"]
    assert estimate.value == Decimal("0.6667")
    assert estimate.lower is not None and estimate.lower < estimate.value
    assert estimate.upper is not None and estimate.upper > estimate.value
    assert single_run.metadata["recovered_pass_at_1_successes"] == 2
    assert single_run.metadata["pass_at_1_interval"] == "wilson-score-95-reference"
    assert "IID Bernoulli" in single_run.metadata["pass_at_1_interval_assumptions"]
    assert "not a guarantee" in single_run.metadata["pass_at_1_interval_assumptions"]

    multiple_runs = _complete_row(
        per_run_input_tokens=150,
        per_run_output_tokens=15,
    )
    multiple_run_estimate = (
        _adapt(_summary_bytes(row=multiple_runs))["filesystem"].offerings[0].signals["pass_at_1"]
    )
    assert multiple_run_estimate.lower is None
    assert multiple_run_estimate.upper is None

    ambiguous = _complete_row(score=0.5, total_tasks=50_000)
    ambiguous_estimate = (
        _adapt(_summary_bytes(row=ambiguous))["filesystem"].offerings[0].signals["pass_at_1"]
    )
    assert ambiguous_estimate.value == Decimal("0.5")
    assert ambiguous_estimate.lower is None
    assert ambiguous_estimate.upper is None


def test_wilson_bounds_are_rounded_outward() -> None:
    estimate = (
        _adapt(_summary_bytes(row=_complete_row(score=0.0, total_tasks=2)))["filesystem"]
        .offerings[0]
        .signals["pass_at_1"]
    )

    assert estimate.lower == Decimal("0E-12")
    assert estimate.upper == Decimal("0.657619772494")


def test_rejects_incomplete_non_scores_only_and_populated_scores_only_rows() -> None:
    incomplete = _mutate_summary(
        lambda value: value["github"]["gpt-5-5-xhigh"].__setitem__("avg_input_tokens", None)
    )
    with pytest.raises(MCPMarkAdapterError, match="non-scores-only.*incomplete telemetry"):
        _adapt(incomplete)

    populated_scores_only = _mutate_summary(
        lambda value: value["notion"]["scores-only"].__setitem__("avg_turns", 0)
    )
    with pytest.raises(MCPMarkAdapterError, match="scores-only.*unexpectedly contains telemetry"):
        _adapt(populated_scores_only)


def test_rejects_cross_section_roster_identity_and_task_cohort_drift() -> None:
    roster_drift = _mutate_summary(lambda value: value["github"].pop("scores-only"))
    with pytest.raises(MCPMarkAdapterError, match="model roster differs"):
        _adapt(roster_drift)

    identity_drift = _mutate_summary(
        lambda value: value["postgres"]["gpt-5-5-xhigh"].__setitem__(
            "actual_model_name", "different-model"
        )
    )
    with pytest.raises(MCPMarkAdapterError, match="model identity.*differs"):
        _adapt(identity_drift)

    def change_one_task_count(value: dict[str, Any]) -> None:
        value["filesystem"]["gpt-5-5-xhigh"] = _complete_row(total_tasks=4)

    task_drift = _mutate_summary(change_one_task_count)
    with pytest.raises(MCPMarkAdapterError, match="do not share one task count"):
        _adapt(task_drift)


def test_rejects_incoherent_totals_scores_and_excess_precision() -> None:
    bad_token_total = _mutate_summary(
        lambda value: value["filesystem"]["gpt-5-5-xhigh"].__setitem__("total_tokens", 331)
    )
    with pytest.raises(MCPMarkAdapterError, match="total_tokens does not equal"):
        _adapt(bad_token_total)

    aggregate_score_drift = _mutate_summary(
        lambda value: value["overall"]["gpt-5-5-xhigh"]["pass@1"].__setitem__("avg", 0.5)
    )
    with pytest.raises(MCPMarkAdapterError, match="overall pass@1.*incoherent"):
        _adapt(aggregate_score_drift)

    excess_precision = _mutate_summary(
        lambda value: value["playwright"]["gpt-5-5-xhigh"]["pass@1"].__setitem__("avg", 0.66667)
    )
    with pytest.raises(MCPMarkAdapterError, match="exceeds four-decimal"):
        _adapt(excess_precision)


def test_bytes_and_local_load_enforce_size_hash_and_timezone(tmp_path: Path) -> None:
    raw = _summary_bytes()
    path = tmp_path / "summary.json"
    path.write_bytes(raw)

    loaded = load_mcpmark_catalogs(
        path,
        required_sha256=hashlib.sha256(raw).hexdigest(),
        retrieved_at=RETRIEVED_AT,
    )
    assert len(loaded["postgres"].offerings) == 1

    with pytest.raises(MCPMarkAdapterError, match="SHA-256 mismatch"):
        catalogs_from_mcpmark_bytes(raw, required_sha256="0" * 64)
    with pytest.raises(MCPMarkAdapterError, match="byte limit"):
        catalogs_from_mcpmark_bytes(raw, max_bytes=len(raw) - 1)
    with pytest.raises(MCPMarkAdapterError, match="byte limit"):
        load_mcpmark_catalogs(path, max_bytes=len(raw) - 1)
    with pytest.raises(MCPMarkAdapterError, match="timezone"):
        catalogs_from_mcpmark_bytes(raw, retrieved_at=datetime(2026, 8, 30))
    with pytest.raises(MCPMarkAdapterError, match="absolute HTTPS"):
        catalogs_from_mcpmark_bytes(raw, source_url="http://example.test/summary.json")


def test_fetch_is_explicit_bounded_and_offline_testable() -> None:
    raw = _summary_bytes()
    digest = hashlib.sha256(raw).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://example.test/summary.json")
        return httpx.Response(200, content=raw, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        catalogs = fetch_mcpmark_catalogs(
            url="https://example.test/summary.json",
            source_version="fixture-v1",
            required_sha256=digest,
            allowed_hosts=("example.test",),
            client=client,
        )
    assert len(catalogs["github"].offerings) == 1

    def too_large(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 101, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(too_large)) as client,
        pytest.raises(MCPMarkAdapterError, match="byte limit"),
    ):
        fetch_mcpmark_catalogs(
            url="https://example.test/summary.json",
            required_sha256=None,
            max_bytes=100,
            allowed_hosts=("example.test",),
            client=client,
        )
    with pytest.raises(MCPMarkAdapterError, match="timeout_seconds"):
        fetch_mcpmark_catalogs(timeout_seconds=0)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(MCPMarkAdapterError, match="not allowed"),
    ):
        fetch_mcpmark_catalogs(
            url="https://example.test/summary.json",
            required_sha256=digest,
            client=client,
        )

    with pytest.raises(MCPMarkAdapterError, match="reserved IP hosts"):
        fetch_mcpmark_catalogs(
            url="https://127.0.0.1/summary.json",
            required_sha256=None,
            allowed_hosts=("127.0.0.1",),
        )


def test_builds_runnable_quality_time_and_quality_token_frontiers() -> None:
    catalogs = _adapt(_summary_bytes())
    config = build_mcpmark_project_config(catalogs)

    assert len(config.workloads) == 6
    assert len(config.frontiers) == 12
    assert "cost" not in config.metrics
    workload = config.workloads["mcpmark-summary-overall"]
    assert workload.assumptions["cost"] == "not reported"
    assert "IID Bernoulli" in workload.assumptions["pass_at_1_interval"]
    assert workload.assumptions["verification_status"] == (
        "self-reported; authenticity and verification not asserted"
    )
    assert "actual_model_name" not in config.frontiers["overall-quality-time"].metadata_fields

    generated_at = datetime(2026, 8, 30, 13, tzinfo=UTC)
    time_frontier = FrontierEngine().calculate(
        config,
        catalogs["filesystem"],
        "filesystem-quality-time",
        generated_at=generated_at,
    )
    token_frontier = FrontierEngine().calculate(
        config,
        catalogs["filesystem"],
        "filesystem-quality-input-tokens",
        generated_at=generated_at,
    )
    assert [item.offering.model_id for item in time_frontier.members] == ["gpt-5.5"]
    assert time_frontier.order_by == "avg_agent_seconds"
    assert token_frontier.order_by == "avg_input_tokens"


def test_default_remote_is_pinned_to_an_immutable_verified_document() -> None:
    assert MCPMARK_VERIFIED_COMMIT in MCPMARK_VERIFIED_URL
    assert len(MCPMARK_VERIFIED_SHA256) == 64


def test_cli_writes_and_evaluates_an_offline_mcpmark_project(tmp_path: Path) -> None:
    raw = _summary_bytes()
    source = tmp_path / "summary.json"
    source.write_bytes(raw)
    output = tmp_path / "mcpmark-project"

    imported = runner.invoke(
        app,
        [
            "import-mcpmark-verified",
            str(output),
            "--source",
            str(source),
            "--expected-sha256",
            hashlib.sha256(raw).hexdigest(),
            "--source-version",
            "fixture-v1",
            "--retrieved-at",
            "2026-08-30T12:00:00Z",
        ],
    )

    assert imported.exit_code == 0, imported.output
    assert "operator-supplied MCPMark summary" in imported.output
    assert "filesystem=1" in imported.output
    config = load_config(output / "frontier.yaml")
    catalog = load_catalog(output / "observations-filesystem.json")
    assert len(config.frontiers) == 12
    assert len(catalog.offerings) == 1

    evaluated = runner.invoke(
        app,
        [
            "evaluate",
            str(output / "frontier.yaml"),
            str(output / "observations-filesystem.json"),
            "filesystem-quality-time",
            "--as-of",
            "2026-08-30T13:00:00Z",
        ],
    )
    assert evaluated.exit_code == 0, evaluated.output
    assert "gpt-5.5" in evaluated.output


def test_writes_reviewable_import_without_silent_overwrite(tmp_path: Path) -> None:
    catalogs = _adapt(_summary_bytes())
    config = build_mcpmark_project_config(catalogs)
    output = tmp_path / "mcpmark-import"

    targets = write_mcpmark_import(catalogs, config, output)

    assert len(targets) == 8
    assert load_config(output / "frontier.yaml") == config
    for section in MCPMARK_SECTIONS:
        assert load_catalog(output / f"observations-{section}.json") == catalogs[section]
    manifest = json.loads((output / "import.json").read_text(encoding="utf-8"))
    assert manifest["license_status"] == "unknown"
    assert manifest["adapter"] == "mcpmark-summary"
    assert manifest["verification_status"] == (
        "self-reported; authenticity and verification not asserted"
    )
    assert manifest["sections"]["overall"]["offerings"] == 1
    assert any("cost are not reported" in warning for warning in manifest["warnings"])
    assert any("IID Bernoulli" in warning for warning in manifest["warnings"])
    assert any("does not match" in warning for warning in manifest["warnings"])

    original = (output / "frontier.yaml").read_text(encoding="utf-8")
    with pytest.raises(MCPMarkAdapterError, match="refusing to overwrite"):
        write_mcpmark_import(catalogs, config, output)
    assert (output / "frontier.yaml").read_text(encoding="utf-8") == original
