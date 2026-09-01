from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_skyline.models import SelectionSnapshot

from model_skyline_litellm.cli import run
from model_skyline_litellm.models import IntegrationConfig
from model_skyline_litellm.reconcile import IndeterminateActivationError


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_plan_prints_only_content_free_projection(
    tmp_path: Path,
    selection: SelectionSnapshot,
    config: IntegrationConfig,
    capsys,
) -> None:
    selection_path = tmp_path / "selection.json"
    config_path = tmp_path / "config.json"
    _write(selection_path, selection.model_dump(mode="json"))
    _write(config_path, config.model_dump(mode="json"))

    result = run(
        [
            "plan",
            str(selection_path),
            str(config_path),
            "--at",
            selection.generated_at.isoformat(),
        ]
    )
    output = capsys.readouterr()

    assert result == 0
    assert selection.snapshot_id in output.out
    for forbidden in ("openai/fake-a", "fake-a-v1", "private_projection_input"):
        assert forbidden not in output.out
    assert output.err == ""


def test_invalid_config_does_not_echo_rejected_secret(
    tmp_path: Path,
    selection: SelectionSnapshot,
    capsys,
) -> None:
    selection_path = tmp_path / "selection.json"
    config_path = tmp_path / "config.json"
    _write(selection_path, selection.model_dump(mode="json"))
    _write(
        config_path,
        {
            "schema_version": "model-skyline-litellm/v1alpha1",
            "stable_alias": "skyline/coding",
            "expected_selection_id": "coding-defaults",
            "expected_frontier_id": "coding-value",
            "expected_workload": {
                "id": "coding-agent",
                "version": "v1",
                "unit": "issue",
            },
            "targets": {
                "target": {
                    "model": "openai/fake",
                    "credential_name": "sk-" + "ant-private-secret-value",
                    "revision": "a" * 64,
                }
            },
            "bindings": [],
        },
    )

    result = run(["plan", str(selection_path), str(config_path)])
    output = capsys.readouterr()

    assert result == 2
    assert "integration configuration is invalid" in output.err
    assert "private-secret" not in output.err


def test_config_rejects_duplicate_members_and_nonstandard_numbers(
    tmp_path: Path,
    selection: SelectionSnapshot,
    capsys,
) -> None:
    selection_path = tmp_path / "selection.json"
    config_path = tmp_path / "config.json"
    _write(selection_path, selection.model_dump(mode="json"))

    for raw in (
        '{"stable_alias":"first","stable_alias":"second"}',
        '{"max_candidates":NaN}',
    ):
        config_path.write_text(raw, encoding="utf-8")
        result = run(["plan", str(selection_path), str(config_path)])
        output = capsys.readouterr()
        assert result == 2
        assert output.err == "error: integration configuration is invalid\n"


def test_indeterminate_activation_has_a_distinct_machine_exit(
    tmp_path: Path,
    selection: SelectionSnapshot,
    config: IntegrationConfig,
    capsys,
    monkeypatch,
) -> None:
    selection_path = tmp_path / "selection.json"
    config_path = tmp_path / "config.json"
    _write(selection_path, selection.model_dump(mode="json"))
    _write(config_path, config.model_dump(mode="json"))

    class Client:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise IndeterminateActivationError("outcome unavailable")

    monkeypatch.setattr("model_skyline_litellm.cli.LiteLLMAdminClient", Client)
    monkeypatch.setattr("model_skyline_litellm.cli.activate", fail)
    result = run(
        [
            "activate",
            str(selection_path),
            str(config_path),
            "--at",
            selection.generated_at.isoformat(),
            "--base-url",
            "https://gateway.example",
        ]
    )
    output = capsys.readouterr()

    assert result == 3
    assert output.err == "indeterminate: outcome unavailable\n"
