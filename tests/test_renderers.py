from __future__ import annotations

import html
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from model_skyline.engine import FrontierEngine
from model_skyline.models import ObservationCatalog, ProjectConfig
from model_skyline.renderers import (
    _display,
    frontier_view,
    render_csv,
    render_rss,
    render_rss_history,
)

RSS_NAMESPACE = "urn:model-skyline:rss:1.0"


def test_semantic_view_treats_absent_and_null_billing_mode_as_equivalent(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=datetime(2026, 8, 29, 19, tzinfo=UTC),
    )
    assert all("billing_mode" not in item[0] for item in frontier_view(snapshot))

    catalog = example_catalog.model_copy(deep=True)
    catalog.offerings = [
        item.model_copy(
            update={"offering": item.offering.model_copy(update={"billing_mode": "managed"})}
        )
        for item in catalog.offerings
    ]
    changed = FrontierEngine().calculate(
        example_config,
        catalog,
        "coding-value",
        generated_at=datetime(2026, 8, 29, 20, tzinfo=UTC),
    )
    assert any(item[0].get("billing_mode") == "managed" for item in frontier_view(changed))


def test_rss_escapes_dynamic_values_inside_embedded_html(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    catalog = example_catalog.model_copy(deep=True)
    first = catalog.offerings[0]
    catalog.offerings[0] = first.model_copy(
        update={
            "offering": first.offering.model_copy(
                update={"offering_id": "<script>alert(1)</script>"}
            )
        }
    )
    snapshot = FrontierEngine().calculate(
        config=example_config,
        catalog=catalog,
        frontier_id="coding-value",
        generated_at=datetime(2026, 8, 29, 19, tzinfo=UTC),
    )

    root = ET.fromstring(render_rss(snapshot))
    description = root.findtext("./channel/item/description") or ""

    assert "<script>" not in description
    assert html.escape("<script>alert(1)</script>") in description


def test_csv_neutralizes_spreadsheet_formula_ids_by_default(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    catalog = example_catalog.model_copy(deep=True)
    first = catalog.offerings[0]
    catalog.offerings[0] = first.model_copy(
        update={
            "offering": first.offering.model_copy(
                update={"offering_id": '=IMPORTXML("https://example.test")'}
            )
        }
    )
    snapshot = FrontierEngine().calculate(
        config=example_config,
        catalog=catalog,
        frontier_id="coding-value",
        generated_at=datetime(2026, 8, 29, 19, tzinfo=UTC),
    )

    assert "'=IMPORTXML" in render_csv(snapshot)
    assert "'=IMPORTXML" not in render_csv(snapshot, spreadsheet_safe=False)


def test_csv_neutralizes_spreadsheet_formula_headers(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    config = example_config.model_copy(deep=True)
    config.frontiers["coding-value"].metadata_fields = ("=HYPERLINK",)
    catalog = example_catalog.model_copy(deep=True)
    catalog.offerings[0].metadata["=HYPERLINK"] = "safe"
    snapshot = FrontierEngine().calculate(
        config=config,
        catalog=catalog,
        frontier_id="coding-value",
        generated_at=datetime(2026, 8, 29, 19, tzinfo=UTC),
    )

    assert render_csv(snapshot).splitlines()[0].endswith("'=HYPERLINK")


def test_single_item_rss_resets_cleanly_when_axis_contract_changes(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    previous = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=datetime(2026, 8, 29, 19, tzinfo=UTC),
    )
    responsiveness = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-responsiveness",
        generated_at=datetime(2026, 8, 29, 20, tzinfo=UTC),
    )
    current = responsiveness.model_copy(update={"frontier_id": previous.frontier_id})

    root = ET.fromstring(render_rss(current, previous=previous))

    assert root.findtext(f"./channel/item/{{{RSS_NAMESPACE}}}baselineReset") == "true"


def test_retained_rss_detects_routable_identity_change_at_same_values(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    previous = FrontierEngine().calculate(
        example_config,
        example_catalog,
        "coding-value",
        generated_at=datetime(2026, 8, 29, 19, tzinfo=UTC),
    )
    catalog = example_catalog.model_copy(deep=True)
    first = catalog.offerings[0]
    catalog.offerings[0] = first.model_copy(
        update={"offering": first.offering.model_copy(update={"provider": "alternate-route"})}
    )
    current = FrontierEngine().calculate(
        example_config,
        catalog,
        "coding-value",
        generated_at=datetime(2026, 8, 29, 20, tzinfo=UTC),
    )

    root = ET.fromstring(render_rss_history([previous, current]))

    assert len(root.findall("./channel/item")) == 2
    assert root.findtext(f"./channel/item/{{{RSS_NAMESPACE}}}baselineReset") == "false"


def test_retained_rss_emits_one_policy_hash_migration_reset_then_stabilizes(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    initial = (
        FrontierEngine()
        .calculate(
            example_config,
            example_catalog,
            "coding-value",
            generated_at=datetime(2026, 8, 29, 19, tzinfo=UTC),
        )
        .model_copy(update={"config_hash": "a" * 64})
    )
    migrated = initial.model_copy(
        update={
            "snapshot_id": "b" * 64,
            "config_hash": "c" * 64,
            "generated_at": datetime(2026, 8, 29, 20, tzinfo=UTC),
        }
    )
    stable_refresh = migrated.model_copy(
        update={
            "snapshot_id": "d" * 64,
            "generated_at": datetime(2026, 8, 29, 21, tzinfo=UTC),
        }
    )

    root = ET.fromstring(render_rss_history([initial, migrated, stable_refresh]))
    items = root.findall("./channel/item")

    assert len(items) == 2
    assert items[0].findtext("guid") == "b" * 64
    assert items[0].findtext(f"{{{RSS_NAMESPACE}}}baselineReset") == "true"
