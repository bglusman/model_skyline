from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from model_skyline.cli import app
from model_skyline.engine import FrontierEngine
from model_skyline.models import (
    EvidenceTier,
    ObservationCatalog,
    ObservationRequirements,
    OfferingKey,
    ProjectConfig,
)
from model_skyline.quality_estimation import (
    PairedItemScore,
    PairedQualityEstimate,
    QualityEstimatorValidation,
    apply_paired_quality_estimate,
    build_paired_quality_estimate,
    paired_item_set_sha256,
    paired_quality_estimate_hash,
    paired_quality_observation,
)
from model_skyline.quality_evidence import (
    QualityComponentIdentity,
    QualityPublicationPermission,
    QualityRawAudit,
    QualityRights,
    QualitySourceIdentity,
    quality_raw_sha256,
)

NOW = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)


def _component(identifier: str, version: str = "1") -> QualityComponentIdentity:
    return QualityComponentIdentity(id=identifier, version=version, configuration={})


def _offering(identifier: str, quantization: str) -> OfferingKey:
    return OfferingKey(
        offering_id=f"local/m5/{identifier}",
        model_id="example/model@revision",
        provider="local:m5",
        endpoint="llama-swap",
        billing_mode="owned_hardware",
        region="local",
        service_tier="high-power",
        quantization=quantization,
        reasoning_effort=None,
        agent_harness="omp@pinned",
        capabilities=("structured-output", "tools"),
    )


def _estimate() -> PairedQualityEstimate:
    raw = b'{"paired":"capture"}'
    items = (
        PairedItemScore(
            item_id="task-b",
            task_group="code",
            anchor_score=Decimal("0.6"),
            candidate_score=Decimal("0.4"),
            weight=Decimal("3"),
        ),
        PairedItemScore(
            item_id="task-a",
            task_group="code",
            anchor_score=Decimal("0.8"),
            candidate_score=Decimal("0.7"),
            weight=Decimal("1"),
        ),
    )
    return build_paired_quality_estimate(
        anchor_offering=_offering("anchor", "bf16"),
        candidate_offering=_offering("candidate", "q4_k_m"),
        raw_audit=QualityRawAudit(
            source_locator="https://bench.example/paired.json",
            raw_sha256=quality_raw_sha256(raw),
            retrieved_at=NOW,
            upstream_revision="tasks-2026-09",
            capture_method="pinned-local-harness",
            parser_implementation=_component("paired-result-parser", "sha256:123"),
        ),
        source_identity=QualitySourceIdentity(
            source_id="example-code-subset",
            source_version="2026-09",
            benchmark=_component("example-code-benchmark", "2"),
            dataset=_component("example-code-tasks", "2026-09"),
            split="test",
            evaluator_harness=_component("omp-benchmark-harness", "sha256:456"),
            scorer=_component("task-pass-scorer", "1"),
            protocol=_component("paired-seed-protocol", "1"),
            projection=_component("paired-delta-projector", "1"),
            scope={"full_task_set_sha256": "a" * 64, "attempts_per_task": 1},
        ),
        rights=QualityRights(
            license_expression="Apache-2.0",
            terms_locator="https://bench.example/terms",
            publication_permission=QualityPublicationPermission.DERIVED_ONLY,
            reviewed_at=NOW,
            review_evidence="Reviewed benchmark task and result publication terms.",
        ),
        metric_id="estimated_code_success",
        unit="ratio",
        metric_minimum=Decimal("0"),
        metric_maximum=Decimal("1"),
        full_anchor_score=Decimal("0.9"),
        full_anchor_result_sha256="b" * 64,
        subset_selection=_component("versioned-code-coreset", "sha256:789"),
        estimator=_component("paired-additive-delta", "1"),
        items=items,
        paired_delta_lower=Decimal("-0.20"),
        paired_delta_upper=Decimal("-0.15"),
        validation=QualityEstimatorValidation(
            protocol=_component("leave-one-family-out", "1"),
            training_population_sha256="c" * 64,
            held_out_offering_count=24,
            absolute_error_bound=Decimal("0.03"),
            coverage_probability=Decimal("0.95"),
            validated_domain={
                "model_families": ["example", "other"],
                "quantization_types": ["gguf-q4", "mlx-4bit"],
            },
        ),
        observed_at=NOW,
    )


def test_paired_estimate_replays_delta_bounds_and_identity() -> None:
    estimate = _estimate()

    assert [item.item_id for item in estimate.items] == ["task-a", "task-b"]
    assert estimate.paired_delta == Decimal("-0.175")
    assert estimate.estimated_value == Decimal("0.725")
    assert estimate.estimated_lower == Decimal("0.67")
    assert estimate.estimated_upper == Decimal("0.78")
    assert estimate.selected_items_sha256 == paired_item_set_sha256(estimate.items)
    assert estimate.estimate_id == paired_quality_estimate_hash(estimate)

    observation = paired_quality_observation(estimate)
    assert observation.evidence_tier is EvidenceTier.ESTIMATED
    assert observation.value == Decimal("0.67")
    assert observation.lower == Decimal("0.67")
    assert observation.upper == Decimal("0.78")
    assert observation.sample_count == 2


def test_paired_estimate_rejects_tampered_derived_values_and_item_digest() -> None:
    payload = _estimate().model_dump(mode="json")
    payload["paired_delta"] = "-0.1"
    with pytest.raises(ValidationError, match="paired_delta does not match"):
        PairedQualityEstimate.model_validate(payload)

    payload = _estimate().model_dump(mode="json")
    payload["selected_items_sha256"] = "d" * 64
    with pytest.raises(ValidationError, match="selected_items_sha256"):
        PairedQualityEstimate.model_validate(payload)


def test_apply_estimate_requires_complete_offering_identity_and_never_overwrites() -> None:
    estimate = _estimate()
    catalog = ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload={"id": "code", "version": "1", "unit": "task"},
        offerings=[
            {
                "offering": estimate.candidate_offering,
                "signals": {
                    "decode_tps": {"value": "40", "unit": "token/s"},
                },
            }
        ],
    )

    enriched = apply_paired_quality_estimate(catalog, estimate)
    candidate = enriched.offerings[0]
    assert candidate.signals[estimate.metric_id].evidence_tier is EvidenceTier.ESTIMATED
    assert candidate.metadata["publication_safe"] is False
    assert (
        candidate.metadata["paired_quality_estimates"][estimate.metric_id]["estimate_id"]
        == estimate.estimate_id
    )

    with pytest.raises(ValueError, match="already contains signal"):
        apply_paired_quality_estimate(enriched, estimate)

    wrong_key = estimate.candidate_offering.model_copy(update={"quantization": "other"})
    mismatched = catalog.model_copy(
        update={"offerings": [catalog.offerings[0].model_copy(update={"offering": wrong_key})]}
    )
    with pytest.raises(ValueError, match="complete estimated OfferingKey"):
        apply_paired_quality_estimate(mismatched, estimate)


def test_estimated_observations_require_explicit_metric_opt_in(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    first = example_catalog.offerings[0]
    signals = dict(first.signals)
    signals["success_rate"] = signals["success_rate"].model_copy(
        update={
            "evidence_tier": EvidenceTier.ESTIMATED,
            "lower": Decimal("0.60"),
            "upper": Decimal("0.64"),
        }
    )
    catalog = example_catalog.model_copy(
        update={
            "offerings": [
                first.model_copy(update={"signals": signals}),
                *example_catalog.offerings[1:],
            ]
        }
    )

    default_snapshot = FrontierEngine().calculate(
        example_config,
        catalog,
        "coding-value",
        generated_at=datetime(2026, 8, 29, 19, tzinfo=UTC),
    )
    rejection = next(
        item for item in default_snapshot.rejected if item.offering_id == first.offering.offering_id
    )
    assert any(
        "evidence tier 'estimated' is not accepted" in reason for reason in rejection.reasons
    )

    quality_metric = example_config.metrics["coding_session_success"]
    quality_metric = quality_metric.model_copy(
        update={
            "requirements": ObservationRequirements(
                max_age_hours=Decimal("8760"),
                minimum_samples=20,
                require_bounds=True,
                accepted_evidence_tiers=(EvidenceTier.MEASURED, EvidenceTier.ESTIMATED),
            )
        }
    )
    opted_in = example_config.model_copy(
        update={
            "metrics": {
                **example_config.metrics,
                "coding_session_success": quality_metric,
            }
        }
    )
    accepted_snapshot = FrontierEngine().calculate(
        opted_in,
        catalog,
        "coding-value",
        generated_at=datetime(2026, 8, 29, 19, tzinfo=UTC),
    )
    evaluated = next(
        item
        for item in accepted_snapshot.evaluated
        if item.offering.offering_id == first.offering.offering_id
    )
    assert evaluated.axes["coding_session_success"].evidence_tiers == (EvidenceTier.ESTIMATED,)


def test_formula_metric_cannot_discard_estimated_input_bounds(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    first = example_catalog.offerings[0]
    signals = dict(first.signals)
    input_signal = signals["input_uncached_tokens_per_success"]
    signals["input_uncached_tokens_per_success"] = input_signal.model_copy(
        update={
            "evidence_tier": EvidenceTier.ESTIMATED,
            "lower": input_signal.value - Decimal("1"),
            "upper": input_signal.value + Decimal("1"),
        }
    )
    catalog = example_catalog.model_copy(
        update={
            "offerings": [
                first.model_copy(update={"signals": signals}),
                *example_catalog.offerings[1:],
            ]
        }
    )
    cost_metric = example_config.metrics["total_cost_per_success"]
    cost_metric = cost_metric.model_copy(
        update={
            "requirements": ObservationRequirements(
                max_age_hours=Decimal("8760"),
                accepted_evidence_tiers=(EvidenceTier.MEASURED, EvidenceTier.ESTIMATED),
            )
        }
    )
    config = example_config.model_copy(
        update={"metrics": {**example_config.metrics, "total_cost_per_success": cost_metric}}
    )

    snapshot = FrontierEngine().calculate(
        config,
        catalog,
        "coding-value",
        generated_at=datetime(2026, 8, 29, 19, tzinfo=UTC),
    )
    rejection = next(
        item for item in snapshot.rejected if item.offering_id == first.offering.offering_id
    )
    assert any(
        "cannot consume estimated evidence without interval propagation" in reason
        for reason in rejection.reasons
    )


def test_cli_validates_and_applies_paired_estimate(tmp_path: Path) -> None:
    estimate = _estimate()
    catalog = ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload={"id": "code", "version": "1", "unit": "task"},
        offerings=[{"offering": estimate.candidate_offering, "signals": {}}],
    )
    estimate_path = tmp_path / "estimate.json"
    catalog_path = tmp_path / "catalog.json"
    estimate_path.write_text(estimate.model_dump_json(indent=2) + "\n", encoding="utf-8")
    catalog_path.write_text(catalog.model_dump_json(indent=2) + "\n", encoding="utf-8")

    validated = CliRunner().invoke(
        app,
        ["validate-paired-quality-estimate", str(estimate_path)],
    )
    assert validated.exit_code == 0, validated.output
    assert estimate.estimate_id in validated.output
    assert "estimated_code_success=0.725 [0.67, 0.78]" in validated.output

    applied = CliRunner().invoke(
        app,
        ["apply-paired-quality-estimate", str(catalog_path), str(estimate_path)],
    )
    assert applied.exit_code == 0, applied.output
    payload = json.loads(applied.output)
    projected = payload["offerings"][0]["signals"]["estimated_code_success"]
    assert projected["evidence_tier"] == "estimated"
    assert projected["lower"] == "0.67"
