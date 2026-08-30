from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from model_skyline.adapters.aider import (
    DEFAULT_SOURCE_COMMIT,
    DEFAULT_SOURCE_SHA256,
    DEFAULT_SOURCE_URL,
    import_aider_polyglot,
)
from model_skyline.adapters.mcpmark import (
    MCPMARK_SECTIONS,
    MCPMARK_VERIFIED_COMMIT,
    MCPMARK_VERIFIED_SHA256,
    MCPMARK_VERIFIED_URL,
    build_mcpmark_project_config,
    fetch_mcpmark_catalogs,
)
from model_skyline.engine import FrontierEngine

pytestmark = pytest.mark.skipif(
    os.environ.get("MODELSKYLINE_TEST_LIVE_SOURCES") != "1",
    reason="set MODELSKYLINE_TEST_LIVE_SOURCES=1 to exercise immutable upstream snapshots",
)

GENERATED_AT = datetime(2026, 8, 30, 13, tzinfo=UTC)


def test_pinned_aider_polyglot_snapshot_and_cost_quality_frontier() -> None:
    result = import_aider_polyglot()

    assert DEFAULT_SOURCE_COMMIT in DEFAULT_SOURCE_URL
    assert result.source.url is not None
    assert str(result.source.url) == DEFAULT_SOURCE_URL
    assert result.source.version == DEFAULT_SOURCE_COMMIT
    assert result.source.raw_sha256 == DEFAULT_SOURCE_SHA256
    assert result.rows_seen == 69
    assert len(result.catalog.offerings) == 20
    assert len(result.rejections) == 49
    assert {
        offering.signals["solve_rate_2"].sample_count for offering in result.catalog.offerings
    } == {225}

    snapshot = FrontierEngine().calculate(
        result.config,
        result.catalog,
        "cost-per-attempted-vs-solve-rate",
        generated_at=GENERATED_AT,
    )

    assert [member.offering.model_id for member in snapshot.members] == [
        "gpt-oss-120b (high)",
        "DeepSeek-V3.2-Exp (Chat)",
        "DeepSeek-V3.2-Exp (Reasoner)",
        "gpt-5 (low)",
        "gpt-5 (medium)",
        "gpt-5 (high)",
    ]
    assert not snapshot.rejected


def test_pinned_mcpmark_snapshot_catalogs_tasks_and_quality_time_frontier() -> None:
    catalogs = fetch_mcpmark_catalogs()

    assert MCPMARK_VERIFIED_COMMIT in MCPMARK_VERIFIED_URL
    assert tuple(catalogs) == MCPMARK_SECTIONS
    assert len(catalogs) == 6
    expected_tasks = {
        "overall": 127,
        "filesystem": 30,
        "github": 23,
        "notion": 28,
        "playwright": 25,
        "postgres": 21,
    }
    for section, expected_task_count in expected_tasks.items():
        catalog = catalogs[section]
        assert len(catalog.offerings) == 6
        assert {offering.signals["pass_at_1"].sample_count for offering in catalog.offerings} == {
            expected_task_count
        }
        assert {
            offering.default_source.raw_sha256
            for offering in catalog.offerings
            if offering.default_source is not None
        } == {MCPMARK_VERIFIED_SHA256}

    config = build_mcpmark_project_config(catalogs)
    snapshot = FrontierEngine().calculate(
        config,
        catalogs["github"],
        "github-quality-time",
        generated_at=GENERATED_AT,
    )

    assert [member.offering.model_id for member in snapshot.members] == [
        "deepseek-v4-pro",
        "claude-fable-5",
        "gpt-5.5",
    ]
    assert not snapshot.rejected
