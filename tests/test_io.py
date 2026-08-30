from __future__ import annotations

from decimal import Decimal

from model_skyline.io import load_catalog, load_config
from model_skyline.models import OfferingKey, OfferingObservation

EXACT_DECIMAL = "0.12345678901234567890123456789012345678"


def test_json_catalog_loader_preserves_decimal_literals_exactly(tmp_path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(
        f"""
        {{
          "schema_version": "model-skyline/v1alpha1",
          "workload": {{"id": "w", "version": "1", "unit": "task"}},
          "offerings": [{{
            "offering": {{
              "offering_id": "provider/model@tier",
              "model_id": "model",
              "provider": "provider"
            }},
            "signals": {{
              "quality": {{"value": {EXACT_DECIMAL}, "unit": "ratio"}}
            }},
            "metadata": {{"exact_ratio": {EXACT_DECIMAL}}}
          }}]
        }}
        """,
        encoding="utf-8",
    )

    catalog = load_catalog(path)
    offering = catalog.offerings[0]

    assert offering.signals["quality"].value == Decimal(EXACT_DECIMAL)
    assert offering.metadata["exact_ratio"] == EXACT_DECIMAL


def test_yaml_config_loader_preserves_decimal_literals_exactly(tmp_path) -> None:
    path = tmp_path / "frontier.yaml"
    path.write_text(
        f"""
        schema_version: model-skyline/v1alpha1
        workloads:
          w:
            unit: task
            version: "1"
            harness: harness@1
            cohort: test
            variables:
              exact_ratio: {EXACT_DECIMAL}
            assumptions:
              exact_ratio: {EXACT_DECIMAL}
        metrics:
          cost:
            kind: signal
            signal: cost
            unit: USD
          quality:
            kind: signal
            signal: quality
            unit: ratio
        frontiers:
          f:
            workload: w
            axes:
              - metric: cost
                goal: minimize
                epsilon_absolute: {EXACT_DECIMAL}
              - metric: quality
                goal: maximize
            order_by: cost
        """,
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.workloads["w"].variables["exact_ratio"] == Decimal(EXACT_DECIMAL)
    assert config.workloads["w"].assumptions["exact_ratio"] == EXACT_DECIMAL
    assert config.frontiers["f"].axes[0].epsilon_absolute == Decimal(EXACT_DECIMAL)


def test_programmatic_fractional_metadata_is_canonicalized() -> None:
    offering = OfferingObservation(
        offering=OfferingKey(
            offering_id="provider/model@tier",
            model_id="model",
            provider="provider",
        ),
        signals={},
        metadata={"ratio": 0.1, "nested": [Decimal("1.2300")]},
    )

    assert offering.metadata == {"ratio": "0.1", "nested": ["1.23"]}
