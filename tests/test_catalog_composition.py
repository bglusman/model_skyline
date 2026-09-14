from __future__ import annotations

import stat
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model_skyline.catalog_composition import (
    CATALOG_ENRICHMENT_AUDIT_SCHEMA_VERSION,
    CatalogCompositionError,
    CatalogEnrichmentPolicy,
    CatalogSignalMapping,
    CatalogSignalProjection,
    catalog_enrichment_policy_hash,
    catalog_enrichment_workload,
    compose_catalogs,
    enrich_catalog_across_workloads,
)
from model_skyline.cli import app
from model_skyline.engine import catalog_hash
from model_skyline.io import dump_json, load_catalog, load_catalog_enrichment_policy
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
    agent_harness: str | None = None,
) -> OfferingObservation:
    return OfferingObservation(
        offering=OfferingKey(
            offering_id=offering_id,
            model_id=model_id or offering_id,
            provider="local:test-host",
            agent_harness=agent_harness,
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


def _enrichment_policy(
    base: ObservationCatalog,
    source: ObservationCatalog,
    *,
    source_offering: OfferingKey,
    target_offering: OfferingKey,
    signal_ids: tuple[str, ...] = ("validated_context",),
) -> CatalogEnrichmentPolicy:
    return CatalogEnrichmentPolicy(
        policy_id="research-agent-operational-gates",
        base_catalog_sha256=catalog_hash(base),
        base_workload=base.workload,
        output_workload_id="research-agent-with-operational-gates",
        output_workload_unit="benchmark_task",
        projections=(
            CatalogSignalProjection(
                projection_id="validated-capacity",
                catalog_sha256=catalog_hash(source),
                workload=source.workload,
                mappings=(
                    CatalogSignalMapping(
                        source_offering=source_offering,
                        target_offering=target_offering,
                        signal_ids=signal_ids,
                        review_note=(
                            "The source uses the byte-identical runtime route; only the "
                            "measurement harness differs."
                        ),
                    ),
                ),
            ),
        ),
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


def test_reviewed_cross_workload_projection_preserves_candidate_and_sources() -> None:
    quality_source = _source("research-quality", "a")
    capacity_source = _source("capacity-probe", "b")
    target = _offering(
        "local/qwen-harbor",
        model_id="Qwen/Qwen3.8-27B",
        default_source=quality_source,
        agent_harness="harbor/terminus-2",
        metadata={"publication_safe": False},
    )
    source = _offering(
        "local/qwen-capacity-probe",
        model_id="Qwen/Qwen3.8-27B",
        signal_id="validated_context",
        value="128000",
        default_source=capacity_source,
        agent_harness="late-needle/v1",
    )
    unrelated = _offering(
        "local/unrelated-capacity-probe",
        signal_id="validated_context",
        value="64000",
        source=capacity_source,
    )
    base = _catalog(target)
    capacity = _catalog(
        source,
        unrelated,
        workload=WorkloadReference(id="late-needle", version="1", unit="context_position"),
    )
    policy = _enrichment_policy(
        base,
        capacity,
        source_offering=source.offering,
        target_offering=target.offering,
    )

    enriched = enrich_catalog_across_workloads(policy, base, (capacity,))

    assert enriched.workload == catalog_enrichment_workload(policy)
    assert [item.offering for item in enriched.offerings] == [target.offering]
    row = enriched.offerings[0]
    assert row.default_source == quality_source
    assert row.signals["quality"].source is None
    assert row.signals["validated_context"].source == capacity_source
    assert row.metadata["publication_safe"] is False
    audit = row.metadata["cross_workload_enrichment"]
    assert isinstance(audit, dict)
    assert audit["schema_version"] == CATALOG_ENRICHMENT_AUDIT_SCHEMA_VERSION
    assert audit["policy_sha256"] == catalog_enrichment_policy_hash(policy)
    assert audit["applied_mappings"] == [
        {
            "projection_id": "validated-capacity",
            "source_catalog_sha256": catalog_hash(capacity),
            "source_workload": capacity.workload.model_dump(mode="json"),
            "source_offering_id": "local/qwen-capacity-probe",
            "signal_ids": ["validated_context"],
            "review_note": (
                "The source uses the byte-identical runtime route; only the "
                "measurement harness differs."
            ),
        }
    ]


def test_policy_hash_and_mapping_order_are_canonical() -> None:
    target = _offering("target", agent_harness="agent")
    source = _offering(
        "source",
        signal_id="validated_context",
        value="128000",
        source=_source("capacity", "a"),
        agent_harness="probe",
    )
    base = _catalog(target)
    capacity = _catalog(
        source,
        workload=WorkloadReference(id="capacity", version="1", unit="position"),
    )
    first = _enrichment_policy(
        base,
        capacity,
        source_offering=source.offering,
        target_offering=target.offering,
        signal_ids=("zeta", "alpha"),
    )
    second_payload = first.model_dump(mode="json")
    second_payload["projections"][0]["mappings"][0]["signal_ids"] = ["alpha", "zeta"]
    second = CatalogEnrichmentPolicy.model_validate(second_payload)

    assert first == second
    assert catalog_enrichment_policy_hash(first) == catalog_enrichment_policy_hash(second)


def test_policy_rejects_same_workload_projection() -> None:
    target = _offering("target")
    source = _offering("source", signal_id="capacity")
    base = _catalog(target)
    same_workload_source = _catalog(source)

    with pytest.raises(ValueError, match="use compose_catalogs"):
        _enrichment_policy(
            base,
            same_workload_source,
            source_offering=source.offering,
            target_offering=target.offering,
            signal_ids=("capacity",),
        )


def test_cross_workload_projection_requires_pinned_catalog_set() -> None:
    target = _offering("target")
    source = _offering(
        "source",
        signal_id="capacity",
        source=_source("capacity", "a"),
    )
    base = _catalog(target)
    capacity = _catalog(
        source,
        workload=WorkloadReference(id="capacity", version="1", unit="position"),
    )
    policy = _enrichment_policy(
        base,
        capacity,
        source_offering=source.offering,
        target_offering=target.offering,
        signal_ids=("capacity",),
    )

    with pytest.raises(CatalogCompositionError, match="catalog set"):
        enrich_catalog_across_workloads(policy, base, ())


@pytest.mark.parametrize("identity_side", ("source", "target"))
def test_cross_workload_projection_requires_complete_reviewed_identity(
    identity_side: str,
) -> None:
    target = _offering("target", model_id="model-a")
    source = _offering(
        "source",
        model_id="model-a",
        signal_id="capacity",
        source=_source("capacity", "a"),
    )
    base = _catalog(target)
    capacity = _catalog(
        source,
        workload=WorkloadReference(id="capacity", version="1", unit="position"),
    )
    reviewed_source = (
        source.offering.model_copy(update={"model_id": "model-b"})
        if identity_side == "source"
        else source.offering
    )
    reviewed_target = (
        target.offering.model_copy(update={"model_id": "model-b"})
        if identity_side == "target"
        else target.offering
    )
    policy = _enrichment_policy(
        base,
        capacity,
        source_offering=reviewed_source,
        target_offering=reviewed_target,
        signal_ids=("capacity",),
    )

    with pytest.raises(CatalogCompositionError, match=f"{identity_side} OfferingKey mismatch"):
        enrich_catalog_across_workloads(policy, base, (capacity,))


def test_cross_workload_projection_rejects_missing_or_unsourced_signal() -> None:
    target = _offering("target")
    source = _offering("source", signal_id="other", value="1")
    base = _catalog(target)
    capacity = _catalog(
        source,
        workload=WorkloadReference(id="capacity", version="1", unit="position"),
    )
    missing_policy = _enrichment_policy(
        base,
        capacity,
        source_offering=source.offering,
        target_offering=target.offering,
        signal_ids=("capacity",),
    )
    unsourced_policy = _enrichment_policy(
        base,
        capacity,
        source_offering=source.offering,
        target_offering=target.offering,
        signal_ids=("other",),
    )

    with pytest.raises(CatalogCompositionError, match="missing signal"):
        enrich_catalog_across_workloads(missing_policy, base, (capacity,))
    with pytest.raises(CatalogCompositionError, match="no source provenance"):
        enrich_catalog_across_workloads(unsourced_policy, base, (capacity,))


def test_cross_workload_projection_never_overwrites_base_signal() -> None:
    target = _offering("target", signal_id="capacity", value="64000")
    source = _offering(
        "source",
        signal_id="capacity",
        value="128000",
        source=_source("capacity", "a"),
    )
    base = _catalog(target)
    capacity = _catalog(
        source,
        workload=WorkloadReference(id="capacity", version="1", unit="position"),
    )
    policy = _enrichment_policy(
        base,
        capacity,
        source_offering=source.offering,
        target_offering=target.offering,
        signal_ids=("capacity",),
    )

    with pytest.raises(CatalogCompositionError, match="would overwrite"):
        enrich_catalog_across_workloads(policy, base, (capacity,))


def test_cli_replays_cross_workload_enrichment_privately(tmp_path: Path) -> None:
    target = _offering("target", default_source=_source("quality", "a"))
    source = _offering(
        "source",
        signal_id="capacity",
        value="128000",
        source=_source("capacity", "b"),
    )
    base = _catalog(target)
    capacity = _catalog(
        source,
        workload=WorkloadReference(id="capacity", version="1", unit="position"),
    )
    policy = _enrichment_policy(
        base,
        capacity,
        source_offering=source.offering,
        target_offering=target.offering,
        signal_ids=("capacity",),
    )
    policy_path = tmp_path / "policy.json"
    base_path = tmp_path / "base.json"
    capacity_path = tmp_path / "capacity.json"
    output_path = tmp_path / "private" / "enriched.json"
    policy_path.write_text(dump_json(policy), encoding="utf-8")
    base_path.write_text(dump_json(base), encoding="utf-8")
    capacity_path.write_text(dump_json(capacity), encoding="utf-8")

    validation = CliRunner().invoke(
        app,
        ["validate-catalog-enrichment-policy", str(policy_path)],
    )
    assert validation.exit_code == 0, validation.output
    assert catalog_enrichment_policy_hash(policy) in validation.output
    assert catalog_enrichment_workload(policy).version in validation.output

    result = CliRunner().invoke(
        app,
        [
            "enrich-catalog-across-workloads",
            str(policy_path),
            str(base_path),
            str(capacity_path),
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert load_catalog(output_path).workload == catalog_enrichment_workload(
        load_catalog_enrichment_policy(policy_path)
    )
