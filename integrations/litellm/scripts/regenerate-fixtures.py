#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from model_skyline.canonical import content_hash
from model_skyline.engine import FrontierEngine
from model_skyline.io import dump_json, load_catalog, load_config
from model_skyline.selection import select_models

from model_skyline_litellm.models import (
    IntegrationConfig,
    OfferingBinding,
    TargetTemplate,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "integrations" / "litellm" / "tests" / "fixtures"
CONFIG_SOURCE = ROOT / "examples" / "coding-session" / "frontier.yaml"
CATALOG_SOURCE = ROOT / "examples" / "coding-session" / "observations.json"


def _artifacts() -> dict[Path, str]:
    config = load_config(CONFIG_SOURCE)
    config.selections["coding-agent-defaults"].count = 2
    config.selections["coding-agent-defaults"].max_per_provider = 1
    catalog_a = load_catalog(CATALOG_SOURCE)
    frontier_a = FrontierEngine().calculate(
        config,
        catalog_a,
        "coding-value",
        generated_at=datetime(2026, 8, 29, 19, tzinfo=UTC),
    )
    selection_a = select_models(config, frontier_a, "coding-agent-defaults")

    catalog_b = catalog_a.model_copy(deep=True)
    quality = next(
        item
        for item in catalog_b.offerings
        if item.offering.offering_id == "qualityworks/large-reasoner@us-priority"
    )
    quality.signals["success_rate"] = quality.signals["success_rate"].model_copy(
        update={"value": Decimal("0.70")}
    )
    cheap_prices = {
        "input_uncached_usd_per_million": "0.05",
        "input_cache_read_usd_per_million": "0.005",
        "input_cache_write_5m_usd_per_million": "0.006",
        "input_cache_write_1h_usd_per_million": "0.008",
        "output_usd_per_million": "0.10",
        "reasoning_usd_per_million": "0.10",
        "tool_call_usd": "0",
        "web_search_usd": "0",
        "sandbox_usd_per_second": "0",
        "request_usd": "0",
        "cache_storage_usd_per_million_token_hour": "0",
        "other_cost_usd_per_success": "0",
    }
    for signal, value in cheap_prices.items():
        quality.signals[signal] = quality.signals[signal].model_copy(
            update={"value": Decimal(value)}
        )
    frontier_b = FrontierEngine().calculate(
        config,
        catalog_b,
        "coding-value",
        generated_at=datetime(2026, 8, 29, 19, 10, tzinfo=UTC),
    )
    selection_b = select_models(config, frontier_b, "coding-agent-defaults")

    assert [item.offering.provider for item in selection_a.choices] == [
        "qualityworks",
        "balancedai",
    ]
    assert [item.offering.provider for item in selection_b.choices] == [
        "balancedai",
        "qualityworks",
    ]
    offerings = {
        choice.offering.offering_id: choice.offering
        for selection in (selection_a, selection_b)
        for choice in selection.choices
    }
    integration = IntegrationConfig(
        stable_alias="skyline/coding",
        expected_selection_id="coding-agent-defaults",
        expected_frontier_id="coding-value",
        expected_workload=selection_a.workload,
        max_candidates=2,
        targets={
            "fake-a": TargetTemplate(
                model="openai/fake-a",
                credential_name="modelskyline/fake-a",
                revision=content_hash({"test_target": "fake-a-v1"}),
            ),
            "fake-b": TargetTemplate(
                model="openai/fake-b",
                credential_name="modelskyline/fake-b",
                revision=content_hash({"test_target": "fake-b-v1"}),
            ),
        },
        bindings=(
            OfferingBinding(
                offering=offerings["qualityworks/large-reasoner@us-priority"],
                target_id="fake-a",
            ),
            OfferingBinding(
                offering=offerings["balancedai/mid-agent@us-standard"],
                target_id="fake-b",
            ),
        ),
    )
    return {
        FIXTURES / "selection-a.json": dump_json(selection_a),
        FIXTURES / "selection-b.json": dump_json(selection_b),
        FIXTURES / "bindings.json": dump_json(integration),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    artifacts = _artifacts()
    if arguments.write:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        for path, content in artifacts.items():
            path.write_text(content, encoding="utf-8")
        return 0
    mismatches = [
        path
        for path, content in artifacts.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    if mismatches:
        raise SystemExit("generated LiteLLM fixtures are stale; run with --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
