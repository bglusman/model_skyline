from __future__ import annotations

import stat
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model_skyline.catalog_composition import CatalogCompositionError, compose_catalogs
from model_skyline.cli import app
from model_skyline.io import dump_json, load_catalog
from model_skyline.models import (
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    SourceReference,
    WorkloadReference,
)

WORKLOAD = WorkloadReference(id="research-agent-v1", version="source-sha256:abc", unit="task")
OBSERVED_AT = datetime(2026, 9, 14, tzinfo=UTC)


def _source(source_id: str, digest: str) -> SourceReference:
    return SourceReference(
        id=source_id,
        version="v1",
        license="CC0-1.0",
        raw_sha256=digest * 64,
        retrieved_at=OBSERVED_AT,
    )


def _offering(
    offering_id: str,
    *,
    model_id: str | None = None,
    signal_id: str = "quality",
    value: str = "80",
    source: SourceReference | None = None,
    default_source: SourceReference | None = None,
    metadata: dict[str, object] | None = None,
) -> OfferingObservation:
    return OfferingObservation(
        offering=OfferingKey(
            offering_id=offering_id,
            model_id=model_id or offering_id,
            provider="local:test-host",
        ),
        signals={
            signal_id: Observation(
                value=Decimal(value),
                unit="percent" if signal_id == "quality" else "token",
                observed_at=OBSERVED_AT,
                source=source,
            )
        },
        metadata=metadata or {},
        default_source=default_source,
    )


def _catalog(
    *offerings: OfferingObservation,
    workload: WorkloadReference = WORKLOAD,
) -> ObservationCatalog:
    return ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=workload,
        offerings=list(offerings),
    )


def test_composes_distinct_candidates_in_deterministic_order() -> None:
    composed = compose_catalogs(
        (
            _catalog(_offering("local/zeta")),
            _catalog(_offering("local/alpha")),
        )
    )

    assert [row.offering.offering_id for row in composed.offerings] == [
        "local/alpha",
        "local/zeta",
    ]
    assert composed.workload == WORKLOAD


def test_joins_disjoint_signals_and_materializes_fallback_sources() -> None:
    quality_source = _source("quality", "a")
    capacity_source = _source("capacity", "b")
    composed = compose_catalogs(
        (
            _catalog(
                _offering(
                    "local/qwen",
                    default_source=quality_source,
                    metadata={"quality": {"manifest": "one"}, "shared": "same"},
                )
            ),
            _catalog(
                _offering(
                    "local/qwen",
                    signal_id="validated_context",
                    value="128000",
                    default_source=capacity_source,
                    metadata={"capacity": {"probe": "late-needle"}, "shared": "same"},
                )
            ),
        )
    )

    row = composed.offerings[0]
    assert row.default_source is None
    assert row.signals["quality"].source == quality_source
    assert row.signals["validated_context"].source == capacity_source
    assert row.metadata == {
        "capacity": {"probe": "late-needle"},
        "quality": {"manifest": "one"},
        "shared": "same",
    }


def test_exact_duplicate_row_is_idempotent() -> None:
    row = _offering("local/qwen", default_source=_source("quality", "a"))

    assert compose_catalogs((_catalog(row), _catalog(row))).offerings == [row]


def test_rejects_workload_widening() -> None:
    other = WorkloadReference(id=WORKLOAD.id, version="different", unit=WORKLOAD.unit)

    with pytest.raises(CatalogCompositionError, match="does not exactly match"):
        compose_catalogs((_catalog(_offering("a")), _catalog(_offering("b"), workload=other)))


def test_rejects_conflicting_offering_identity() -> None:
    with pytest.raises(CatalogCompositionError, match="multiple OfferingKey"):
        compose_catalogs(
            (
                _catalog(_offering("same", model_id="model-a")),
                _catalog(_offering("same", model_id="model-b")),
            )
        )


def test_rejects_conflicting_signal() -> None:
    with pytest.raises(CatalogCompositionError, match="conflicting signal"):
        compose_catalogs(
            (
                _catalog(_offering("same", value="80")),
                _catalog(_offering("same", value="81")),
            )
        )


def test_rejects_conflicting_metadata_leaf() -> None:
    with pytest.raises(CatalogCompositionError, match="quality.score_kind"):
        compose_catalogs(
            (
                _catalog(_offering("same", metadata={"quality": {"score_kind": "exact"}})),
                _catalog(_offering("same", metadata={"quality": {"score_kind": "estimated"}})),
            )
        )


def test_rejects_source_id_collision_across_candidates() -> None:
    with pytest.raises(CatalogCompositionError, match="inconsistent source provenance"):
        compose_catalogs(
            (
                _catalog(_offering("one", source=_source("shared", "a"))),
                _catalog(_offering("two", source=_source("shared", "b"))),
            )
        )


def test_requires_at_least_two_catalogs() -> None:
    with pytest.raises(CatalogCompositionError, match="at least two"):
        compose_catalogs((_catalog(_offering("one")),))


def test_cli_writes_private_composed_catalog(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    output = tmp_path / "private" / "composed.json"
    first.write_text(dump_json(_catalog(_offering("zeta"))), encoding="utf-8")
    second.write_text(dump_json(_catalog(_offering("alpha"))), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["compose-catalogs", str(first), str(second), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert [row.offering.offering_id for row in load_catalog(output).offerings] == [
        "alpha",
        "zeta",
    ]
