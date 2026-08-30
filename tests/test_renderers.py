from __future__ import annotations

import html
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from model_skyline.engine import FrontierEngine
from model_skyline.models import ObservationCatalog, ProjectConfig
from model_skyline.renderers import render_csv, render_rss


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
