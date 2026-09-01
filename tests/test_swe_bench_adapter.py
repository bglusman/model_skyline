from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Context, localcontext
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from model_skyline.adapters.swe_bench import (
    EVIDENCE_FILENAME,
    INVENTORY_FILENAME,
    MANIFEST_FILENAME,
    SWE_BENCH_ADAPTER_ID,
    SWE_BENCH_ADAPTER_VERSION,
    SweBenchAdapterError,
    SweBenchCapture,
    SweBenchSourceIdentityMode,
    load_swe_bench_source,
    normalize_swe_bench_bytes,
    write_swe_bench_capture,
)
from model_skyline.cli import app
from model_skyline.io import dump_json
from model_skyline.models import OfferingKey
from model_skyline.quality_catalog import (
    QualityCatalogError,
    project_quality_import_report,
    quality_source_reference,
    quality_workload_reference,
    reconcile_quality_catalog,
)
from model_skyline.quality_evidence import (
    QualityImportOutcome,
    QualityMappingRelationship,
    QualityPublicationPermission,
    QualityReconciliation,
    QualityReconciliationEntry,
    reconcile_quality_evidence,
)

RETRIEVED_AT = datetime(2026, 8, 31, 22, tzinfo=UTC)


def _details(*, resolved_count: int, changed_task_set: bool = False) -> dict[str, Any]:
    values = {
        f"task-{index:03d}": {
            "api_calls": 2,
            "cost": 0.1,
            "resolved": index < resolved_count,
        }
        for index in range(500)
    }
    if changed_task_set:
        values["different-task"] = values.pop("task-499")
    return values


def _row(
    suffix: str,
    *,
    resolved_count: int = 400,
    model_id: str | None = None,
    details: dict[str, Any] | None | object = ...,  # noqa: ANN401
) -> dict[str, Any]:
    selected_details = _details(resolved_count=resolved_count) if details is ... else details
    model = model_id or f"synthetic-model-{suffix}"
    return {
        "agent": "mini-SWE-agent",
        "agent_org": "Synthetic SWE-agent",
        "checked": True,
        "cost": 50,
        "date": "2026-08-30",
        "folder": f"20260830_mini-v2.0.0_{suffix}",
        "instance_calls": 2,
        "instance_cost": 0.1,
        "logo": [],
        "logs": None,
        "mini-swe-agent_version": "2.0.0",
        "model_display": f"Synthetic Model {suffix}",
        "model_org": "Synthetic Org",
        "model_release_date": None,
        "name": f"Synthetic Model {suffix} (high)",
        "os_model": False,
        "os_system": True,
        "per_instance_details": selected_details,
        "reasoning_effort": "high",
        "resolved": resolved_count / 5,
        "site": "https://example.invalid",
        "tags": [
            f"Model: {model}",
            "Org: Synthetic Org",
            "System: Attempts - 1",
            "Mini: 2.0.0",
        ],
        "trajs": None,
        "trajs_docent": False,
        "warning": None,
    }


def _document(*rows: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "leaderboards": [
                {"name": "Verified", "results": [{"unrelated": "ignored"}]},
                {"name": "bash-only", "results": list(rows)},
            ]
        },
        separators=(",", ":"),
    ).encode()


def _capture(raw: bytes) -> SweBenchCapture:
    return normalize_swe_bench_bytes(
        raw,
        retrieved_at=RETRIEVED_AT,
        source_locator="operator-local-capture:synthetic",
        upstream_revision="synthetic-revision-1",
        source_identity_mode=SweBenchSourceIdentityMode.OFFICIAL_SEMANTIC,
    )


def _mapping(capture: SweBenchCapture, row_index: int = 0) -> QualityReconciliation:
    row = capture.evidence.rows[row_index]
    return QualityReconciliation(
        schema_version="model-skyline/quality-reconciliation/v1alpha1",
        entries=(
            QualityReconciliationEntry(
                row_id=row.row_id,
                adapter_id=SWE_BENCH_ADAPTER_ID,
                projection_version=SWE_BENCH_ADAPTER_VERSION,
                expected_source_identity_sha256=capture.evidence.source_identity_sha256,
                expected_subject_identity_sha256=row.subject_identity_sha256,
                relationship=QualityMappingRelationship.REVIEWED_QUALITY_PROJECTION,
                offering=OfferingKey(
                    offering_id="synthetic-provider/synthetic-model@mini-swe-agent",
                    model_id="synthetic-model",
                    provider="synthetic-provider",
                    endpoint=None,
                    billing_mode=None,
                    region=None,
                    service_tier=None,
                    quantization=None,
                    reasoning_effort="high",
                    agent_harness="mini-swe-agent/2.0.0",
                    capabilities=("coding",),
                ).model_dump(mode="json"),
                review_evidence="Synthetic exact quality-projection review fixture.",
                reviewed_at=RETRIEVED_AT,
            ),
        ),
    )


def test_normalizes_exact_cohort_without_retaining_task_ids() -> None:
    capture = _capture(_document(_row("alpha"), _row("beta", resolved_count=350)))

    assert capture.rows_seen == 2
    assert capture.valid_rows == 2
    assert capture.invalid_rows == 0
    assert capture.evidence.source_identity.source_version == "mini-swe-agent/2.0.0"
    assert capture.evidence.source_identity.scope["expected_instances"] == 500
    task_set_sha256 = capture.evidence.source_identity.scope["task_set_sha256"]
    assert isinstance(task_set_sha256, str)
    assert len(task_set_sha256) == 64
    row = capture.evidence.rows[0]
    assert row.subject.row_id.startswith("bash-only/")
    assert row.subject.route_disclosure.value == "unknown"
    assert row.result is not None
    assert row.result.measurements[0].id == "swe_bench_reported_api_calls_per_issue"
    assert {measurement.id for measurement in row.result.measurements} == {
        "swe_bench_resolved_percent",
        "swe_bench_reported_api_calls_per_issue",
        "swe_bench_reported_cost_per_issue_usd",
        "swe_bench_reported_total_cost_usd",
    }
    assert "task-000" not in capture.evidence.model_dump_json()
    assert "task-000" not in json.dumps(capture.inventory())


def test_reviewed_evidence_projects_to_exact_quality_only_catalog(tmp_path: Path) -> None:
    capture = _capture(_document(_row("alpha")))
    reconciliation = _mapping(capture)
    source = quality_source_reference(capture.evidence)
    assert source.url is None
    assert source.terms_url is None
    assert source.license == "NOASSERTION"
    workload = quality_workload_reference(
        capture.evidence,
        workload_id="swe-bench/bash-only/mini-swe-agent-v2",
        unit="issue",
    )

    catalog, report = reconcile_quality_catalog(
        capture.evidence,
        reconciliation,
        workload=workload,
        source=source,
    )

    assert catalog.workload == workload
    assert len(catalog.offerings) == 1
    offering = catalog.offerings[0]
    assert offering.offering == reconciliation.entries[0].offering
    assert set(offering.signals) == {"swe_bench_resolved_percent"}
    assert offering.signals["swe_bench_resolved_percent"].source == source
    assert offering.metadata["quality_only_projection"] is True
    assert offering.metadata["publication_safe"] is False
    assert offering.metadata["quality_publication_permission"] == "unknown"
    serialized = catalog.model_dump_json()
    assert "Synthetic Model alpha" not in serialized
    assert "task-000" not in serialized
    assert "swe_bench_reported_total_cost_usd" not in serialized

    prohibited_rights = capture.evidence.rights.model_copy(
        update={
            "license_expression": "MIT",
            "publication_permission": QualityPublicationPermission.PROHIBITED,
        }
    )
    prohibited_evidence = capture.evidence.model_copy(update={"rights": prohibited_rights})
    prohibited_source = quality_source_reference(prohibited_evidence)
    assert prohibited_source.license == "MIT"

    wrong_rights = source.model_copy(update={"license": "different-license"})
    with pytest.raises(QualityCatalogError, match="provenance and rights"):
        project_quality_import_report(
            capture.evidence,
            reconciliation,
            report,
            workload=workload,
            source=wrong_rights,
        )

    entry = reconciliation.entries[0]
    changed_target = entry.model_copy(
        update={"offering": entry.offering.model_copy(update={"model_id": "different-model"})}
    )
    changed_reconciliation = reconciliation.model_copy(update={"entries": (changed_target,)})
    with pytest.raises(QualityCatalogError, match="reconciliation identity"):
        project_quality_import_report(
            capture.evidence,
            changed_reconciliation,
            report,
            workload=workload,
            source=source,
        )

    evidence_path = tmp_path / "evidence.json"
    reconciliation_path = tmp_path / "reconciliation.json"
    report_path = tmp_path / "report.json"
    output_path = tmp_path / "catalog.json"
    evidence_path.write_text(dump_json(capture.evidence), encoding="utf-8")
    reconciliation_path.write_text(dump_json(reconciliation), encoding="utf-8")
    report_path.write_text(dump_json(report), encoding="utf-8")
    projected = CliRunner().invoke(
        app,
        [
            "project-quality-catalog",
            str(evidence_path),
            str(reconciliation_path),
            str(report_path),
            "--workload-id",
            workload.id,
            "--workload-unit",
            workload.unit,
            "--output",
            str(output_path),
        ],
    )
    assert projected.exit_code == 0, projected.output
    assert json.loads(output_path.read_text(encoding="utf-8"))["workload"] == (
        workload.model_dump(mode="json")
    )
    if os.name == "posix":
        assert output_path.stat().st_mode & 0o777 == 0o600

    refused_overwrite = CliRunner().invoke(
        app,
        [
            "project-quality-catalog",
            str(evidence_path),
            str(reconciliation_path),
            str(report_path),
            "--workload-id",
            workload.id,
            "--workload-unit",
            workload.unit,
            "--output",
            str(output_path),
        ],
    )
    assert refused_overwrite.exit_code == 2
    assert "overwrite" in refused_overwrite.output


def test_quarantines_missing_details_and_score_mismatch() -> None:
    mismatch = _row("mismatch", resolved_count=400)
    mismatch["resolved"] = 79.8
    capture = _capture(_document(mismatch, _row("missing", details=None)))

    assert capture.valid_rows == 0
    assert capture.invalid_rows == 2
    assert sorted(
        row.invalid_result.code for row in capture.evidence.rows if row.invalid_result is not None
    ) == ["aggregate_detail_mismatch", "missing_per_instance_details"]


def test_accounting_aggregate_mismatch_keeps_quality_but_drops_cost() -> None:
    row = _row("accounting")
    row["cost"] = 999
    capture = _capture(_document(row))

    result = capture.evidence.rows[0].result
    assert result is not None
    assert [measurement.id for measurement in result.measurements] == ["swe_bench_resolved_percent"]
    assert result.metadata["accounting_aggregate_coherent"] is False


def test_malformed_accounting_keeps_recomputed_quality() -> None:
    row = _row("accounting-unavailable")
    row["cost"] = None
    row["per_instance_details"]["task-000"]["cost"] = "not-a-number"
    capture = _capture(_document(row))

    result = capture.evidence.rows[0].result
    assert result is not None
    assert [measurement.id for measurement in result.measurements] == ["swe_bench_resolved_percent"]
    assert result.metadata["accounting_aggregate_coherent"] is False


def test_result_identity_is_independent_of_ambient_decimal_context() -> None:
    raw = _document(_row("alpha"))
    baseline = _capture(raw)
    with localcontext(Context(prec=2)):
        constrained = _capture(raw)

    assert constrained.evidence == baseline.evidence


def test_reviewed_projection_maps_exact_identity_and_strips_route_telemetry() -> None:
    original = _capture(_document(_row("alpha")))
    mapping = _mapping(original)

    report = reconcile_quality_evidence(original.evidence, mapping)
    assert report.records[0].outcome is QualityImportOutcome.MAPPED
    assert [item.id for item in report.mapped_rows[0].result.measurements] == [
        "swe_bench_resolved_percent"
    ]
    assert {item.id for item in report.mapped_rows[0].result.counts} == {
        "attempted_issues",
        "resolved_issues",
    }

    refreshed = _capture(_document(_row("alpha", resolved_count=399)))
    assert refreshed.evidence.source_identity_sha256 == original.evidence.source_identity_sha256
    assert (
        refreshed.evidence.rows[0].subject_identity_sha256
        == original.evidence.rows[0].subject_identity_sha256
    )
    assert refreshed.evidence.rows[0].result_sha256 != original.evidence.rows[0].result_sha256
    refreshed_report = reconcile_quality_evidence(refreshed.evidence, mapping)
    assert refreshed_report.records[0].outcome is QualityImportOutcome.MAPPED

    drift_row = _row("alpha")
    drift_row["model_display"] = "Different Model Identity"
    drifted = _capture(_document(drift_row))
    assert drifted.evidence.source_identity_sha256 == original.evidence.source_identity_sha256
    drift_report = reconcile_quality_evidence(drifted.evidence, mapping)
    assert drift_report.records[0].outcome is QualityImportOutcome.IDENTITY_DRIFT


def test_unrelated_board_changes_only_raw_identity() -> None:
    first_raw = _document(_row("alpha"))
    first = _capture(first_raw)
    document = json.loads(first_raw)
    document["leaderboards"][0]["results"] = [{"new": "unrelated data"}]
    second = _capture(json.dumps(document).encode())

    assert first.evidence.raw_audit_sha256 != second.evidence.raw_audit_sha256
    assert first.evidence.source_identity_sha256 == second.evidence.source_identity_sha256
    assert (
        first.evidence.rows[0].subject_identity_sha256
        == second.evidence.rows[0].subject_identity_sha256
    )


def test_material_subject_claim_changes_invalidate_mapping_identity() -> None:
    first = _capture(_document(_row("alpha")))
    changed = _row("alpha")
    changed["warning"] = "Upstream identity warning changed."
    second = _capture(_document(changed))

    assert first.evidence.source_identity_sha256 == second.evidence.source_identity_sha256
    assert (
        first.evidence.rows[0].subject_identity_sha256
        != second.evidence.rows[0].subject_identity_sha256
    )


def test_quarantines_future_dates_and_out_of_domain_numbers() -> None:
    future = _row("future")
    future["date"] = "2026-09-01"
    oversized = _row("oversized")
    oversized["resolved"] = 1e100
    capture = _capture(_document(future, oversized))

    assert {
        row.invalid_result.code for row in capture.evidence.rows if row.invalid_result is not None
    } == {"future_observation_date", "invalid_aggregate_score"}


def test_fails_closed_on_schema_and_task_set_drift() -> None:
    extra = _row("extra")
    extra["new_semantic_field"] = True
    with pytest.raises(SweBenchAdapterError, match="unreviewed field"):
        _capture(_document(extra))

    changed = _row(
        "changed",
        details=_details(resolved_count=400, changed_task_set=True),
    )
    with pytest.raises(SweBenchAdapterError, match="task-set drift"):
        _capture(_document(_row("alpha"), changed))

    with pytest.raises(SweBenchAdapterError, match="bounded JSON"):
        _capture(b'{"leaderboards":[],"leaderboards":[]}')

    with pytest.raises(SweBenchAdapterError, match="nesting limit"):
        _capture(b"[" * 17 + b"0" + b"]" * 17)


def test_local_loader_rejects_symlink_and_requires_revision(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(_document(_row("alpha")))
    link = tmp_path / "source-link.json"
    link.symlink_to(source)

    with pytest.raises(SweBenchAdapterError, match="cannot read|regular file"):
        load_swe_bench_source(
            link,
            expected_sha256=sha256(source.read_bytes()).hexdigest(),
            source_revision="synthetic",
            retrieved_at=RETRIEVED_AT,
        )
    with pytest.raises(SweBenchAdapterError, match="source_revision"):
        load_swe_bench_source(
            source,
            expected_sha256=sha256(source.read_bytes()).hexdigest(),
            retrieved_at=RETRIEVED_AT,
        )
    with pytest.raises(SweBenchAdapterError, match="expected_sha256"):
        load_swe_bench_source(
            source,
            source_revision="synthetic",
            retrieved_at=RETRIEVED_AT,
        )


def test_remote_loader_accepts_only_exact_official_repository_paths() -> None:
    with pytest.raises(SweBenchAdapterError, match="official repository revision"):
        load_swe_bench_source(
            "https://raw.githubusercontent.com/example/other/"
            + "1" * 40
            + "/data/leaderboards.json",
            expected_sha256="2" * 64,
            source_revision="1" * 40,
        )
    with pytest.raises(SweBenchAdapterError, match="official raw-content host"):
        load_swe_bench_source(
            "https://2130706433/SWE-bench/swe-bench.github.io/"
            + "1" * 40
            + "/data/leaderboards.json",
            expected_sha256="2" * 64,
            source_revision="1" * 40,
        )


@pytest.mark.skipif(os.name != "posix", reason="explicit private modes are POSIX-specific")
def test_writes_private_bundle_and_cli_can_capture_local_source(tmp_path: Path) -> None:
    raw = _document(_row("alpha"))
    capture = _capture(raw)
    output = tmp_path / "library-output"
    paths = write_swe_bench_capture(capture, output)

    assert {path.name for path in paths} == {
        EVIDENCE_FILENAME,
        INVENTORY_FILENAME,
        MANIFEST_FILENAME,
    }
    assert stat_mode(output) == 0o700
    assert {stat_mode(path) for path in paths} == {0o600}

    source = tmp_path / "source.json"
    source.write_bytes(raw)
    cli_output = tmp_path / "cli-output"
    result = CliRunner().invoke(
        app,
        [
            "capture-swe-bench-bash-only",
            str(cli_output),
            "--source",
            str(source),
            "--source-revision",
            "synthetic-revision-1",
            "--expected-sha256",
            sha256(raw).hexdigest(),
            "--retrieved-at",
            RETRIEVED_AT.isoformat(),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "1 valid, 0 quarantined" in result.output
    assert stat_mode(cli_output) == 0o700
    assert {stat_mode(path) for path in cli_output.iterdir()} == {0o600}
    evidence = json.loads((cli_output / EVIDENCE_FILENAME).read_text())
    assert evidence["rights"]["license_expression"] == "NOASSERTION"


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777
