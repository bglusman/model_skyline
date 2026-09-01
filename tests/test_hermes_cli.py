from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model_skyline.cli import app
from model_skyline.models import (
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    WorkloadReference,
)

FIXTURES = Path(__file__).parent / "fixtures"
IDENTITY_KEY_ENV = "MODEL_SKYLINE_HERMES_IDENTITY_KEY_HEX"
IDENTITY_KEY_HEX = "42" * 32
SESSION_ID = "synthetic-hermes-session-01"
PRIVATE_SENTINELS = (
    SESSION_ID,
    "PRIVATE_SYSTEM_PROMPT_SENTINEL",
    "PRIVATE_MESSAGE_PAYLOAD_SENTINEL",
    "PRIVATE_TOOL_PAYLOAD_SENTINEL",
    "PRIVATE_WORKSPACE_PATH_SENTINEL",
)
runner = CliRunner()


def _state_db(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    script = (FIXTURES / "hermes_state_v26_synthetic.sql").read_text(encoding="utf-8")
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()
    return path


def _mapping_payload(hermes_version: str = "0.21.0") -> dict[str, object]:
    return {
        "session_id": SESSION_ID,
        "hermes_version": hermes_version,
        "workload": {
            "id": "agentic-coding-session",
            "version": "1.0.0",
            "unit": "coding_session",
        },
        "route": {
            "offering": {
                "offering_id": "synthetic-provider/synthetic-model@synthetic-tier",
                "model_id": "synthetic-model",
                "provider": "synthetic-provider",
                "endpoint": "https://synthetic-provider.invalid/v1",
                "billing_mode": "synthetic-direct",
                "service_tier": "synthetic-tier",
                "agent_harness": "hermes-agent",
            },
            "model": "synthetic-model",
            "billing_provider": "synthetic-provider",
            "billing_base_url": "https://synthetic-provider.invalid/v1",
            "billing_mode": "synthetic-direct",
            "usage_report_single_route_attested": True,
            "service_tier_fulfilled_attested": True,
            "route_details_attested": False,
        },
        "work_unit_success": "1",
    }


def _mapping_file(tmp_path: Path, hermes_version: str = "0.21.0") -> Path:
    path = tmp_path / f"mapping-{hermes_version}.json"
    path.write_text(json.dumps(_mapping_payload(hermes_version)), encoding="utf-8")
    return path


def _arguments(state: Path, mapping: Path, output: Path | None = None) -> list[str]:
    result = ["import-hermes-session", str(state), str(mapping)]
    if output is not None:
        result.extend(("--output", str(output)))
    return result


def _catalog_file(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.json"
    catalog = ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=WorkloadReference(
            id="agentic-coding-session",
            version="1.0.0",
            unit="coding_session",
        ),
        offerings=[
            OfferingObservation(
                offering=OfferingKey(
                    offering_id="synthetic-provider/synthetic-model@synthetic-tier",
                    model_id="synthetic-model",
                    provider="synthetic-provider",
                    endpoint="https://synthetic-provider.invalid/v1",
                    billing_mode="synthetic-direct",
                    service_tier="synthetic-tier",
                    agent_harness="hermes-agent",
                ),
                signals={},
            )
        ],
    )
    path.write_text(catalog.model_dump_json(), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("hermes_version", "hermes_commit"),
    [
        ("0.20.6", "4f22543509d1b91dc45bcb369447126c5eb14fb7"),
        ("0.21.0", "29112bef099274229cadff79cdff7bf7b99c4b77"),
    ],
)
def test_cli_imports_reviewed_hermes_session_to_private_content_free_trace(
    tmp_path: Path,
    hermes_version: str,
    hermes_commit: str,
) -> None:
    state = _state_db(tmp_path)
    state_digest = state.read_bytes()
    mapping = _mapping_file(tmp_path, hermes_version)
    output = tmp_path / "private" / "trace.jsonl"

    result = runner.invoke(
        app,
        _arguments(state, mapping, output),
        env={IDENTITY_KEY_ENV: IDENTITY_KEY_HEX},
    )

    assert result.exit_code == 0, result.output
    assert result.output == ""
    assert state.read_bytes() == state_digest
    assert not state.with_name(f"{state.name}-journal").exists()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    serialized = output.read_text(encoding="utf-8")
    trace = json.loads(serialized)
    assert serialized.count("\n") == 1
    assert trace["adapter_id"] == "model-skyline/hermes-agent-aggregate"
    assert trace["upstream_version"] == hermes_version
    assert trace["upstream_commit"] == hermes_commit
    assert trace["model_request_count"] == 3
    assert trace["input_uncached_tokens"] == "1200"
    assert trace["input_cache_read_tokens"] == "800"
    assert trace["input_cache_write_tokens"] == "200"
    assert trace["output_total_tokens"] == "350"
    assert trace["reasoning_tokens"] == "50"
    assert trace["tool_calls"] == "7"
    assert trace["provider_reported_total_cost_usd"] == "0.0105"
    for sentinel in PRIVATE_SENTINELS:
        assert sentinel not in serialized
    assert IDENTITY_KEY_HEX not in serialized
    assert str(state) not in serialized
    assert str(mapping) not in serialized


def test_cli_hermes_import_refuses_overwrite(tmp_path: Path) -> None:
    state = _state_db(tmp_path)
    mapping = _mapping_file(tmp_path)
    output = tmp_path / "trace.jsonl"
    output.write_text("existing-private-output", encoding="utf-8")

    result = runner.invoke(
        app,
        _arguments(state, mapping, output),
        env={IDENTITY_KEY_ENV: IDENTITY_KEY_HEX},
    )

    assert result.exit_code == 2
    assert output.read_text(encoding="utf-8") == "existing-private-output"
    for sentinel in PRIVATE_SENTINELS:
        assert sentinel not in result.output


def test_cli_hermes_trace_aggregates_through_the_public_command(tmp_path: Path) -> None:
    state = _state_db(tmp_path)
    mapping = _mapping_file(tmp_path)
    trace = tmp_path / "trace.jsonl"
    catalog = _catalog_file(tmp_path)
    enriched = tmp_path / "enriched.json"

    imported = runner.invoke(
        app,
        _arguments(state, mapping, trace),
        env={IDENTITY_KEY_ENV: IDENTITY_KEY_HEX},
    )
    aggregated = runner.invoke(
        app,
        ["aggregate-traces", str(catalog), str(trace), "--output", str(enriched)],
    )

    assert imported.exit_code == 0, imported.output
    assert aggregated.exit_code == 0, aggregated.output
    payload = json.loads(enriched.read_text(encoding="utf-8"))
    signals = payload["offerings"][0]["signals"]
    assert signals["work_unit_count"]["value"] == "1"
    assert signals["request_count_per_work_unit"]["value"] == "3"
    assert signals["input_cache_read_tokens_per_work_unit"]["value"] == "800"
    assert signals["input_cache_write_tokens_per_work_unit"]["value"] == "200"
    assert signals["provider_reported_total_cost_usd_per_work_unit"]["value"] == "0.0105"


@pytest.mark.parametrize(
    "invalid_key",
    (
        "41" * 31 + "4",
        "41" * 32 + "4",
        "41" * 31 + "zz",
        " " + "41" * 32,
        "not-a-key",
    ),
)
def test_cli_hermes_import_requires_fixed_well_formed_environment_key(
    tmp_path: Path,
    invalid_key: str,
) -> None:
    state = _state_db(tmp_path)
    mapping = _mapping_file(tmp_path)

    result = runner.invoke(
        app,
        _arguments(state, mapping),
        env={IDENTITY_KEY_ENV: invalid_key},
    )

    assert result.exit_code == 2
    assert IDENTITY_KEY_ENV in result.output
    assert invalid_key not in result.output
    for sentinel in PRIVATE_SENTINELS:
        assert sentinel not in result.output


def test_cli_hermes_import_requires_environment_key_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state_db(tmp_path)
    mapping = _mapping_file(tmp_path)
    monkeypatch.delenv(IDENTITY_KEY_ENV, raising=False)

    result = runner.invoke(app, _arguments(state, mapping))

    assert result.exit_code == 2
    assert IDENTITY_KEY_ENV in result.output
    assert SESSION_ID not in result.output


def test_cli_hermes_identity_key_rotation_changes_only_opaque_ids(tmp_path: Path) -> None:
    state = _state_db(tmp_path)
    mapping = _mapping_file(tmp_path)
    first_output = tmp_path / "first.jsonl"
    second_output = tmp_path / "second.jsonl"

    first = runner.invoke(
        app,
        _arguments(state, mapping, first_output),
        env={IDENTITY_KEY_ENV: "41" * 32},
    )
    second = runner.invoke(
        app,
        _arguments(state, mapping, second_output),
        env={IDENTITY_KEY_ENV: "42" * 32},
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_trace = json.loads(first_output.read_text(encoding="utf-8"))
    second_trace = json.loads(second_output.read_text(encoding="utf-8"))
    for identifier in ("work_unit_id", "attempt_id", "request_id"):
        assert first_trace.pop(identifier) != second_trace.pop(identifier)
    assert first_trace == second_trace


def test_cli_hermes_stdout_contains_only_content_free_trace(tmp_path: Path) -> None:
    state = _state_db(tmp_path)
    mapping = _mapping_file(tmp_path)

    result = runner.invoke(
        app,
        _arguments(state, mapping),
        env={IDENTITY_KEY_ENV: IDENTITY_KEY_HEX},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["upstream_version"] == "0.21.0"
    for sentinel in PRIVATE_SENTINELS:
        assert sentinel not in result.output
    assert IDENTITY_KEY_HEX not in result.output


def test_cli_hermes_import_rejects_private_mapping_values_without_echo(tmp_path: Path) -> None:
    state = _state_db(tmp_path)
    mapping = tmp_path / "invalid-mapping.json"
    payload = _mapping_payload()
    private_value = "sk-" + "proj-private-session-mapping-value"
    payload["session_id"] = private_value
    payload["hermes_version"] = "0.21.1"
    mapping.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(
        app,
        _arguments(state, mapping),
        env={IDENTITY_KEY_ENV: IDENTITY_KEY_HEX},
    )

    assert result.exit_code == 2
    assert "invalid Hermes session mapping" in result.output
    assert private_value not in result.output
    assert IDENTITY_KEY_HEX not in result.output


def test_cli_hermes_import_rejects_symlinked_mapping(tmp_path: Path) -> None:
    state = _state_db(tmp_path)
    mapping = _mapping_file(tmp_path)
    alias = tmp_path / "mapping-alias.json"
    alias.symlink_to(mapping)

    result = runner.invoke(
        app,
        _arguments(state, alias),
        env={IDENTITY_KEY_ENV: IDENTITY_KEY_HEX},
    )

    assert result.exit_code == 2
    assert "cannot read the Hermes session mapping" in result.output
    assert SESSION_ID not in result.output


def test_cli_hermes_import_refuses_symlinked_output_parent(tmp_path: Path) -> None:
    state = _state_db(tmp_path)
    mapping = _mapping_file(tmp_path)
    real_parent = tmp_path / "real-output"
    real_parent.mkdir()
    alias_parent = tmp_path / "alias-output"
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    output = alias_parent / "trace.jsonl"

    result = runner.invoke(
        app,
        _arguments(state, mapping, output),
        env={IDENTITY_KEY_ENV: IDENTITY_KEY_HEX},
    )

    assert result.exit_code == 2
    assert not (real_parent / "trace.jsonl").exists()
    assert SESSION_ID not in result.output


def test_cli_hermes_help_exposes_no_identity_key_argument() -> None:
    result = runner.invoke(app, ["import-hermes-session", "--help"])

    assert result.exit_code == 0, result.output
    assert "MODEL_SKYLINE_HERMES_IDENTITY_KEY_HEX" in result.output
    assert "--identity-key" not in result.output
