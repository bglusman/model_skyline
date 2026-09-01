from __future__ import annotations

import hashlib
import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model_skyline.arc_feed_monitor import ArcAgiFeedState, ArcAgiFeedStatus
from model_skyline.cli import _safe_error_message, app

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "coding-session"
CODEX_LIVE_EXAMPLE = ROOT / "examples" / "framework-traces" / "codex-cli-smoke"
runner = CliRunner()
CODEX_PRIVATE_SENTINEL = "PRIVATE_CODEX_CLI_PAYLOAD_MUST_NOT_PERSIST"


def _write_codex_stream(path: Path) -> None:
    events = [
        {"type": "thread.started", "thread_id": "raw-codex-thread-id"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "id": "raw-item-id",
                "type": "agent_message",
                "text": CODEX_PRIVATE_SENTINEL,
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1000,
                "cached_input_tokens": 700,
                "output_tokens": 120,
                "reasoning_output_tokens": 20,
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )


def _codex_import_arguments(source: Path) -> list[str]:
    return [
        "import-codex-exec",
        str(source),
        "--codex-version",
        "0.144.2",
        "--provider",
        "openai",
        "--model",
        "gpt-5.4",
        "--offering-id",
        "openai/gpt-5.4@codex",
        "--timestamp",
        "2026-09-01T10:00:00Z",
        "--workload-id",
        "coding-agent",
        "--workload-version",
        "v1",
        "--work-unit-id",
        "case-0001",
        "--result-id",
        "result-0001",
        "--attempt-id",
        "attempt-0001",
        "--work-unit-success",
        "1",
    ]


def test_cli_help_groups_commands_without_renaming_them() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    for panel in (
        "Core and publication",
        "Telemetry",
        "Data sources",
        "Source monitoring",
        "Quality evidence",
        "Contracts",
    ):
        assert panel in result.output
    for command in (
        "evaluate",
        "aggregate-traces",
        "import-codex-exec",
        "import-hermes-session",
        "build-quality-portfolio",
        "export-schemas",
    ):
        assert command in result.output


def test_cli_imports_codex_jsonl_to_private_content_free_trace(tmp_path: Path) -> None:
    source = tmp_path / "codex-private.jsonl"
    output = tmp_path / "canonical" / "trace.jsonl"
    _write_codex_stream(source)

    result = runner.invoke(
        app,
        [
            *_codex_import_arguments(source),
            "--model-route-attested",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output == ""
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    serialized = output.read_text(encoding="utf-8")
    payload = json.loads(serialized)
    assert serialized.count("\n") == 1
    assert payload["schema_version"] == "model-skyline/request-trace/v1alpha2"
    assert payload["adapter_id"] == "model-skyline/codex-exec-jsonl"
    assert payload["upstream_version"] == "0.144.2"
    assert payload["input_total_tokens"] == "1000"
    assert payload["input_cache_read_tokens"] == "700"
    assert payload["input_uncached_tokens"] is None
    assert payload["input_cache_write_tokens"] is None
    assert payload["output_tokens"] == "100"
    assert payload["reasoning_tokens"] == "20"
    assert payload["output_total_tokens"] == "120"
    assert CODEX_PRIVATE_SENTINEL not in serialized
    assert "raw-codex-thread-id" not in serialized
    assert "raw-item-id" not in serialized
    assert str(source) not in serialized


def test_cli_codex_import_requires_explicit_route_attestation(tmp_path: Path) -> None:
    source = tmp_path / "codex-private.jsonl"
    _write_codex_stream(source)

    result = runner.invoke(app, _codex_import_arguments(source))

    assert result.exit_code == 2
    assert "model_route_attested must explicitly be true" in result.output
    assert CODEX_PRIVATE_SENTINEL not in result.output


def test_cli_codex_import_requires_attestation_for_optional_route_identity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "codex-private.jsonl"
    _write_codex_stream(source)

    result = runner.invoke(
        app,
        [
            *_codex_import_arguments(source),
            "--model-route-attested",
            "--billing-mode",
            "chatgpt_subscription",
        ],
    )

    assert result.exit_code == 2
    assert "route_details_attested is required" in result.output
    assert CODEX_PRIVATE_SENTINEL not in result.output


def test_cli_codex_import_refuses_source_alias_through_symlinked_parent(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    source = real_parent / "codex-private.jsonl"
    _write_codex_stream(source)
    alias_parent = tmp_path / "alias"
    try:
        alias_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    result = runner.invoke(
        app,
        [
            *_codex_import_arguments(alias_parent / source.name),
            "--model-route-attested",
            "--output",
            str(source),
        ],
    )

    assert result.exit_code == 2
    assert "refusing to overwrite existing private output" in result.output
    assert CODEX_PRIVATE_SENTINEL in source.read_text(encoding="utf-8")


def test_cli_aggregates_committed_real_codex_trace() -> None:
    assert hashlib.sha256((CODEX_LIVE_EXAMPLE / "trace.jsonl").read_bytes()).hexdigest() == (
        "2b3f774357f49c10e30f4315553f3bac5081da833ced75e44ab3e2f5648e9720"
    )
    result = runner.invoke(
        app,
        [
            "aggregate-traces",
            str(CODEX_LIVE_EXAMPLE / "catalog.json"),
            str(CODEX_LIVE_EXAMPLE / "trace.jsonl"),
            "--source-id",
            "live-codex-cli-smoke",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    signals = payload["offerings"][0]["signals"]
    assert signals["work_unit_count"]["value"] == "1"
    assert signals["successful_work_units"]["value"] == "1"
    assert signals["attempt_count_per_work_unit"]["value"] == "1"
    assert signals["input_total_tokens_per_work_unit"]["value"] == "10962"
    assert signals["input_cache_read_tokens_per_work_unit"]["value"] == "1792"
    assert signals["output_tokens_per_work_unit"]["value"] == "7"
    assert signals["reasoning_tokens_per_work_unit"]["value"] == "15"
    assert signals["output_total_tokens_per_work_unit"]["value"] == "22"
    assert "input_uncached_tokens_per_work_unit" not in signals
    assert "input_cache_write_tokens_per_work_unit" not in signals
    assert "model_requests_per_work_unit" not in signals
    assert not any("cost" in name for name in signals)


def test_cli_validates_example_contracts() -> None:
    result = runner.invoke(
        app,
        ["validate", str(EXAMPLE / "frontier.yaml"), str(EXAMPLE / "observations.json")],
    )

    assert result.exit_code == 0, result.output
    assert "4 offerings" in result.output


def test_cli_validate_rejects_mismatched_cost_basis(tmp_path: Path) -> None:
    config = tmp_path / "frontier.yaml"
    config.write_text(
        (EXAMPLE / "frontier.yaml")
        .read_text(encoding="utf-8")
        .replace("cost_basis: reconstructed_components", "cost_basis: billed_total"),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["validate", str(config), str(EXAMPLE / "observations.json")],
    )

    assert result.exit_code == 2
    assert "does not reference that cost basis" in result.output


def test_cli_evaluate_and_select() -> None:
    evaluate = runner.invoke(
        app,
        [
            "evaluate",
            str(EXAMPLE / "frontier.yaml"),
            str(EXAMPLE / "observations.json"),
            "coding-value",
            "--as-of",
            "2026-08-29T19:00:00Z",
        ],
    )
    assert evaluate.exit_code == 0, evaluate.output
    assert "fastcloud/quick-small@us-standard" in evaluate.output
    assert "legacy-mid" not in evaluate.output

    selection = runner.invoke(
        app,
        [
            "select",
            str(EXAMPLE / "frontier.yaml"),
            str(EXAMPLE / "observations.json"),
            "coding-agent-defaults",
            "--as-of",
            "2026-08-29T19:00:00Z",
        ],
    )
    assert selection.exit_code == 0, selection.output
    payload = json.loads(selection.output)
    assert payload["default"]["offering"]["provider"] == "qualityworks"


def test_cli_publishes_complete_project(tmp_path: Path) -> None:
    output = tmp_path.resolve() / "site"
    result = runner.invoke(
        app,
        [
            "publish-project",
            str(EXAMPLE / "frontier.yaml"),
            str(output),
            "--project-id",
            "coding-demo",
            "--catalog",
            str(EXAMPLE / "observations.json"),
            "--as-of",
            "2026-08-29T19:00:00Z",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "2 frontiers, 1 selections" in result.output
    assert (output / "latest.json").is_file()
    assert (output / "feeds" / "coding-value.xml").is_file()


def test_cli_exports_contract_schemas(tmp_path) -> None:
    output = tmp_path / "schemas"
    result = runner.invoke(app, ["export-schemas", str(output)])

    assert result.exit_code == 0, result.output
    assert (output / "project-config.schema.json").is_file()
    assert (output / "selection-snapshot.schema.json").is_file()
    assert (output / "publication-manifest.schema.json").is_file()
    assert (output / "frontier-history.schema.json").is_file()
    assert (output / "request-trace.schema.json").is_file()
    assert (output / "request-trace-v1alpha2.schema.json").is_file()
    assert (output / "request-trace-v1alpha3.schema.json").is_file()
    assert (output / "quality-evidence.schema.json").is_file()
    assert (output / "quality-reconciliation.schema.json").is_file()
    assert (output / "quality-import-report.schema.json").is_file()
    assert (output / "quality-portfolio-policy.schema.json").is_file()
    assert (output / "quality-portfolio-derivation.schema.json").is_file()
    assert (output / "project-config.schema.json").read_bytes() == (
        ROOT / "schemas" / "project-config.schema.json"
    ).read_bytes()


def test_cli_reports_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.9.0"


def test_cli_arc_feed_monitor_fails_after_rendering_changed_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = ArcAgiFeedStatus(
        observed_revision="1" * 40,
        pinned_revision="0" * 40,
        observed_last_modified=datetime(2026, 8, 31, 22, tzinfo=UTC),
        retrieved_at=datetime(2026, 8, 31, 23, tzinfo=UTC),
        state=ArcAgiFeedState.REVIEW_REQUIRED,
    )
    monkeypatch.setattr("model_skyline.cli.inspect_arc_agi_feed", lambda: status)

    failed = runner.invoke(app, ["check-arc-agi-2-feed"])
    reported = runner.invoke(app, ["check-arc-agi-2-feed", "--report-only"])

    assert failed.exit_code == 3
    assert json.loads(failed.output)["action"] == "manual_adapter_review"
    assert reported.exit_code == 0
    assert json.loads(reported.output)["different_head_policy"] == ("no_automatic_semantic_reuse")


def test_cli_arc_feed_monitor_accepts_reviewed_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = ArcAgiFeedStatus(
        observed_revision="0" * 40,
        pinned_revision="0" * 40,
        observed_last_modified=datetime(2026, 8, 31, 22, tzinfo=UTC),
        retrieved_at=datetime(2026, 8, 31, 23, tzinfo=UTC),
        state=ArcAgiFeedState.PINNED,
    )
    monkeypatch.setattr("model_skyline.cli.inspect_arc_agi_feed", lambda: status)

    result = runner.invoke(app, ["check-arc-agi-2-feed"])

    assert result.exit_code == 0
    assert json.loads(result.output)["review_required"] is False


def test_cli_error_messages_escape_terminal_controls_and_are_bounded() -> None:
    message = _safe_error_message(ValueError("before\x1b[31m\u202eafter\n" + ("x" * 10_000)))

    assert "\x1b" not in message
    assert "\u202e" not in message
    assert "\n" not in message
    assert "\\u001b" in message
    assert "\\u202e" in message
    assert "\\u000a" in message
    assert message.endswith("…[truncated]")
    assert len(message) <= 4_110
