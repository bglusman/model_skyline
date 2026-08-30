from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from model_skyline.adapters.aider import (
    AiderAdapterError,
    import_aider_polyglot,
    load_aider_source,
    normalize_aider_polyglot,
    wilson_interval_95,
)
from model_skyline.cli import app
from model_skyline.engine import FrontierEngine
from model_skyline.io import load_catalog, load_config

FIXTURE = Path(__file__).parent / "fixtures" / "aider_polyglot_tiny.yml"
RETRIEVED_AT = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
runner = CliRunner()


def _fixture_sha256() -> str:
    return hashlib.sha256(FIXTURE.read_bytes()).hexdigest()


def test_normalizes_strict_rows_with_exact_cost_and_quality_metrics() -> None:
    result = import_aider_polyglot(
        FIXTURE,
        expected_sha256=_fixture_sha256(),
        retrieved_at=RETRIEVED_AT,
        source_version="tiny-fixture-v1",
        source_license="CC0-1.0",
        terms_url=None,
    )

    assert result.rows_seen == 6
    assert len(result.catalog.offerings) == 2
    assert len(result.rejections) == 4
    reasons = {rejection.row_id: rejection.reason for rejection in result.rejections}
    assert "dirty" in reasons["2025-01-03--dirty-architect"]
    assert "positive" in reasons["2025-01-04--zero-cost"]
    assert "test_cases=total_tests=225" in reasons["2025-01-05--incomplete"]
    assert "incoherent" in reasons["2025-01-06--incoherent"]

    alpha = next(
        offering
        for offering in result.catalog.offerings
        if offering.offering.model_id == "alpha-model"
    )
    assert alpha.metadata["commit_hash"] == "abc1234, 012abcd"
    assert alpha.offering.provider == "unknown"
    assert alpha.offering.endpoint is None
    assert alpha.signals["solve_rate_2"].value == Decimal("0.8")
    assert alpha.signals["total_cost_usd"].value == Decimal("18")
    assert alpha.signals["total_cost_usd"].lower == Decimal("17.995")
    assert alpha.signals["total_cost_usd"].upper == Decimal("18.005")
    assert alpha.signals["usd_per_attempted_workunit"].value == Decimal("0.08")
    assert alpha.signals["usd_per_solved_workunit"].value == Decimal("0.1")
    assert alpha.signals["agent_edit_seconds_per_case"].value == Decimal("10.25")
    solve_rate = alpha.signals["solve_rate_2"]
    assert solve_rate.lower is not None and solve_rate.lower < solve_rate.value
    assert solve_rate.upper is not None and solve_rate.upper > solve_rate.value
    assert solve_rate.sample_count == 225
    assert alpha.default_source is not None
    assert alpha.default_source.raw_sha256 == _fixture_sha256()
    assert alpha.default_source.retrieved_at == RETRIEVED_AT
    assert alpha.default_source.url is None
    assert "command" not in alpha.metadata
    assert len(str(alpha.metadata["command_sha256"])) == 64

    assert set(result.config.frontiers) == {
        "cost-per-attempted-vs-solve-rate",
        "total-cost-vs-solve-rate",
        "cost-per-solved-vs-solve-rate",
        "agent-edit-seconds-vs-solve-rate",
    }
    assert (
        "model" not in result.config.frontiers["cost-per-attempted-vs-solve-rate"].metadata_fields
    )


def test_include_dirty_accepts_multi_commit_run_if_any_component_is_dirty() -> None:
    result = import_aider_polyglot(
        FIXTURE,
        retrieved_at=RETRIEVED_AT,
        include_dirty=True,
    )

    assert len(result.catalog.offerings) == 3
    dirty = next(
        offering
        for offering in result.catalog.offerings
        if offering.offering.model_id.startswith("gamma")
    )
    assert dirty.metadata["commit_dirty"] is True
    assert dirty.metadata["commit_hash"] == "def5678, abc0000-dirty"
    assert result.source.license is None
    assert result.source.terms_url is None


def test_generated_frontiers_evaluate_without_special_runtime_support() -> None:
    result = import_aider_polyglot(FIXTURE, retrieved_at=RETRIEVED_AT)

    for frontier_id in result.config.frontiers:
        snapshot = FrontierEngine().calculate(
            result.config,
            result.catalog,
            frontier_id,
            generated_at=datetime(2026, 8, 30, 13, 0, tzinfo=UTC),
        )
        assert {member.offering.model_id for member in snapshot.members} == {
            "alpha-model",
            "beta-model",
        }
        assert not snapshot.rejected


def test_wilson_interval_handles_boundary_counts_with_decimals() -> None:
    lower_zero, upper_zero = wilson_interval_95(0, 225)
    lower_all, upper_all = wilson_interval_95(225, 225)

    assert lower_zero == 0
    assert Decimal(0) < upper_zero < Decimal(1)
    assert Decimal(0) < lower_all < Decimal(1)
    assert upper_all == 1
    with pytest.raises(ValueError, match="0 <= passed <= attempted"):
        wilson_interval_95(2, 1)


def test_source_hash_size_scheme_and_redirect_policies(tmp_path: Path) -> None:
    with pytest.raises(AiderAdapterError, match="SHA-256 mismatch"):
        load_aider_source(FIXTURE, expected_sha256="0" * 64)

    oversized = tmp_path / "oversized.yml"
    oversized.write_bytes(b"x" * 11)
    with pytest.raises(AiderAdapterError, match="10-byte limit"):
        load_aider_source(oversized, max_bytes=10)

    with pytest.raises(AiderAdapterError, match="HTTPS"):
        load_aider_source("http://example.test/leaderboard.yml")

    with pytest.raises(AiderAdapterError, match="query strings"):
        load_aider_source("https://example.test/leaderboard.yml?token=private")

    def oversized_response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": "100"}, content=b"[]")

    with pytest.raises(AiderAdapterError, match="5-byte limit"):
        load_aider_source(
            "https://example.test/leaderboard.yml",
            max_bytes=5,
            allowed_hosts=("example.test",),
            transport=httpx.MockTransport(oversized_response),
        )

    def downgrade_redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://example.test/plain.yml"})

    with pytest.raises(AiderAdapterError, match="redirects are not followed"):
        load_aider_source(
            "https://example.test/leaderboard.yml",
            allowed_hosts=("example.test",),
            transport=httpx.MockTransport(downgrade_redirect),
        )

    with pytest.raises(AiderAdapterError, match="not allowed"):
        load_aider_source(
            "https://example.test/leaderboard.yml",
            transport=httpx.MockTransport(oversized_response),
        )

    with pytest.raises(AiderAdapterError, match="reserved IP hosts"):
        load_aider_source(
            "https://127.0.0.1/leaderboard.yml",
            allowed_hosts=("127.0.0.1",),
            transport=httpx.MockTransport(oversized_response),
        )

    with pytest.raises(AiderAdapterError, match="retrieved_at cannot be supplied"):
        load_aider_source(
            "https://example.test/leaderboard.yml",
            retrieved_at=RETRIEVED_AT,
            allowed_hosts=("example.test",),
            transport=httpx.MockTransport(oversized_response),
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"- dirname: first\n  dirname: second\n", "duplicate key"),
        (b"- &row\n  dirname: first\n- *row\n", "aliases are not allowed"),
        (b"!!python/object/apply:os.system ['never-run']\n", "tag"),
    ],
)
def test_yaml_parser_rejects_ambiguous_or_executable_features(
    tmp_path: Path, payload: bytes, message: str
) -> None:
    source = tmp_path / "unsafe.yml"
    source.write_bytes(payload)
    loaded = load_aider_source(source, retrieved_at=RETRIEVED_AT)

    with pytest.raises(AiderAdapterError, match=message):
        normalize_aider_polyglot(loaded)


def test_cli_writes_a_self_contained_offline_project(tmp_path: Path) -> None:
    output = tmp_path / "aider-project"
    result = runner.invoke(
        app,
        [
            "import-aider-polyglot",
            str(output),
            "--source",
            str(FIXTURE),
            "--expected-sha256",
            _fixture_sha256(),
            "--source-version",
            "tiny-fixture-v1",
            "--retrieved-at",
            "2026-08-30T12:00:00Z",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "imported 2 of 6" in result.output
    config = load_config(output / "frontier.yaml")
    catalog = load_catalog(output / "observations.json")
    manifest = json.loads((output / "import.json").read_text(encoding="utf-8"))
    assert len(config.frontiers) == 4
    assert len(catalog.offerings) == 2
    assert manifest["rows"] == {"seen": 6, "imported": 2, "rejected": 4}
    assert str(FIXTURE) not in (output / "import.json").read_text(encoding="utf-8")

    evaluated = runner.invoke(
        app,
        [
            "evaluate",
            str(output / "frontier.yaml"),
            str(output / "observations.json"),
            "cost-per-attempted-vs-solve-rate",
            "--as-of",
            "2026-08-30T13:00:00Z",
        ],
    )
    assert evaluated.exit_code == 0, evaluated.output
    assert "alpha-model" in evaluated.output
    assert "beta-model" in evaluated.output

    refused = runner.invoke(
        app,
        ["import-aider-polyglot", str(output), "--source", str(FIXTURE)],
    )
    assert refused.exit_code == 2
    assert "refusing to overwrite" in refused.output
