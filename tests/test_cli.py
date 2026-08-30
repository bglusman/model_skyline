from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from model_skyline.cli import app

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "coding-session"
runner = CliRunner()


def test_cli_validates_example_contracts() -> None:
    result = runner.invoke(
        app,
        ["validate", str(EXAMPLE / "frontier.yaml"), str(EXAMPLE / "observations.json")],
    )

    assert result.exit_code == 0, result.output
    assert "4 offerings" in result.output


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


def test_cli_exports_contract_schemas(tmp_path) -> None:
    output = tmp_path / "schemas"
    result = runner.invoke(app, ["export-schemas", str(output)])

    assert result.exit_code == 0, result.output
    assert (output / "project-config.schema.json").is_file()
    assert (output / "selection-snapshot.schema.json").is_file()
    assert (output / "request-trace.schema.json").is_file()
    assert (output / "project-config.schema.json").read_bytes() == (
        ROOT / "schemas" / "project-config.schema.json"
    ).read_bytes()


def test_cli_reports_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"
