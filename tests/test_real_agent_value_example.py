from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from model_skyline.engine import FrontierEngine
from model_skyline.io import load_catalog, load_config, public_schemas
from model_skyline.renderers import render_table
from model_skyline.selection import select_models

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "real-agent-value"
AS_OF = datetime(2026, 9, 1, 2, tzinfo=UTC)


def test_real_agent_value_example_validates_evaluates_and_selects() -> None:
    config = load_config(EXAMPLE / "frontier.yaml")
    catalog = load_catalog(EXAMPLE / "observations.json")
    schemas = public_schemas()

    Draft202012Validator(schemas["project-config.schema.json"]).validate(
        config.model_dump(mode="json")
    )
    Draft202012Validator(schemas["observation-catalog.schema.json"]).validate(
        catalog.model_dump(mode="json")
    )

    frontier = FrontierEngine().calculate(
        config,
        catalog,
        "agent-value",
        generated_at=AS_OF,
    )
    selection = select_models(config, frontier, "agent-defaults")

    Draft202012Validator(schemas["frontier-snapshot.schema.json"]).validate(
        frontier.model_dump(mode="json")
    )
    Draft202012Validator(schemas["selection-snapshot.schema.json"]).validate(
        selection.model_dump(mode="json")
    )

    evaluated = {item.offering.offering_id: item for item in frontier.evaluated}
    flash = evaluated["openrouter/z-ai/glm-5.3-flash"]
    full = evaluated["openrouter/z-ai/glm-5.3"]

    assert flash.axes["metered_token_cost_per_success"].value == Decimal("0.003741715")
    assert full.axes["metered_token_cost_per_success"].value == Decimal("0.06635506")
    assert flash.axes["regression_quality_score"].value == Decimal("2")
    assert full.axes["regression_quality_score"].value == Decimal("3")
    assert {item.offering.offering_id for item in frontier.members} == set(evaluated)

    assert selection.default.offering.offering_id == "openrouter/z-ai/glm-5.3"
    assert [item.offering.offering_id for item in selection.fallbacks] == [
        "openrouter/z-ai/glm-5.3-flash"
    ]
    assert selection.default.axes["metered_token_cost_per_success"].value == Decimal("0.06635506")
    table = render_table(frontier)
    assert " | 3 " in table

    cost = full.axes["metered_token_cost_per_success"]
    quality = full.axes["regression_quality_score"]
    assert cost.dependencies == (
        "signals.input_cache_read_usd_per_million",
        "signals.input_uncached_usd_per_million",
        "signals.output_usd_per_million",
        "workload.input_cache_read_tokens_per_success",
        "workload.input_uncached_tokens_per_success",
        "workload.output_tokens_per_success",
    )
    assert cost.source_ids == (
        "openrouter-model-z-ai-glm-5.3-2026-09-01",
        "private-agent-trace-aggregate-v1",
    )
    assert quality.source_ids == ("model-skyline-synthetic-regression-quality-v1",)
    assert {source.id for source in frontier.sources} == {
        "openrouter-model-z-ai-glm-5.3-2026-09-01",
        "openrouter-model-z-ai-glm-5.3-flash-2026-09-01",
        "model-skyline-synthetic-regression-quality-v1",
        "private-agent-trace-aggregate-v1",
    }
