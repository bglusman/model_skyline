from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model_skyline.arc_feed_monitor import ArcAgiFeedState, ArcAgiFeedStatus
from model_skyline.cli import _safe_error_message, app

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "coding-session"
GATEWAY_CONFORMANCE = ROOT / "conformance" / "gateway-pointer" / "v1alpha1"
runner = CliRunner()


def test_cli_help_groups_commands_without_renaming_them() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    for panel in (
        "Core workflow",
        "Frontier composition",
        "Telemetry",
        "Gateway",
        "Data sources",
        "Source monitoring",
        "Quality evidence",
        "Contracts",
    ):
        assert panel in result.output
    for command in ("evaluate", "aggregate-traces", "verify-gateway-bundle", "export-schemas"):
        assert command in result.output


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
    assert (output / "gateway-selection-pointer.schema.json").is_file()
    assert (output / "gateway-selection-envelope.schema.json").is_file()
    assert (output / "gateway-trust-policy.schema.json").is_file()
    assert (output / "cross-frontier-selection-policy.schema.json").is_file()
    assert (output / "frontier-proximity.schema.json").is_file()
    assert (output / "quality-gated-selection-snapshot.schema.json").is_file()
    assert (output / "quality-oracle-policy.schema.json").is_file()
    assert (output / "quality-oracle-snapshot.schema.json").is_file()
    assert (output / "project-config.schema.json").read_bytes() == (
        ROOT / "schemas" / "project-config.schema.json"
    ).read_bytes()


def test_cli_reports_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.8.0"


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


def test_cli_verifies_language_neutral_gateway_bundle(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "verify-gateway-bundle",
            str(GATEWAY_CONFORMANCE / "valid" / "envelope.dsse.json"),
            str(GATEWAY_CONFORMANCE / "artifacts" / "publication.json"),
            str(GATEWAY_CONFORMANCE / "artifacts" / "selection.json"),
            str(GATEWAY_CONFORMANCE / "valid" / "trust-policy.json"),
            "--at",
            "2026-08-29T19:00:00Z",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    route = payload["route"]
    assert route["sequence"] == 7
    assert route["selection_id"] == "coding-agent-defaults"
    assert [target["target_id"] for target in route["targets"]] == [
        "target-0",
        "target-1",
        "target-2",
    ]
    assert payload["checkpoint"]["sequence"] == 7
    assert len(payload["verified_key_ids"]) == 1

    checkpoint_path = tmp_path / "verification.json"
    checkpoint_path.write_text(result.output, encoding="utf-8")
    checkpoint = runner.invoke(
        app,
        [
            "verify-gateway-bundle",
            str(GATEWAY_CONFORMANCE / "valid" / "envelope.dsse.json"),
            str(GATEWAY_CONFORMANCE / "artifacts" / "publication.json"),
            str(GATEWAY_CONFORMANCE / "artifacts" / "selection.json"),
            str(GATEWAY_CONFORMANCE / "valid" / "trust-policy.json"),
            "--checkpoint",
            str(checkpoint_path),
            "--at",
            "2026-08-29T19:00:00Z",
        ],
    )
    assert checkpoint.exit_code == 0, checkpoint.output
