from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from model_skyline import cli as cli_module
from model_skyline.adapters import arc_agi
from model_skyline.adapters.arc_agi import (
    ARC_AGI_ADAPTER_ID,
    ARC_AGI_ADAPTER_VERSION,
    ARC_AGI_EXPECTED_RESULT_PATHS,
    ARC_AGI_HARNESS_COMMIT,
    ARC_AGI_HF_RESOLVE_PREFIX,
    ARC_AGI_HF_REVISION,
    ARC_AGI_TASK_COMMIT,
    EVIDENCE_FILENAME,
    INVENTORY_FILENAME,
    MANIFEST_FILENAME,
    ArcAgiAdapterError,
    ArcAgiCapture,
    normalize_arc_agi_public_eval_bytes,
    write_arc_agi_public_eval_capture,
)
from model_skyline.cli import app
from model_skyline.models import OfferingKey
from model_skyline.quality_evidence import (
    QualityImportOutcome,
    QualityMappingRelationship,
    QualityMeasurementRole,
    QualityReconciliation,
    QualityReconciliationEntry,
    reconcile_quality_evidence,
)

RETRIEVED_AT = datetime(2026, 8, 31, 22, tzinfo=UTC)
SYNTHETIC_TASK_IDS = tuple(f"{index:08x}" for index in range(120))
SYNTHETIC_TASK_SET_SHA256 = hashlib.sha256(
    "".join(f"{task_id}\n" for task_id in SYNTHETIC_TASK_IDS).encode("ascii")
).hexdigest()


def _revision_metadata() -> bytes:
    return json.dumps(
        {
            "id": "arcprize/arc_agi_v2_public_eval",
            "author": "arcprize",
            "sha": ARC_AGI_HF_REVISION,
            "lastModified": "2026-06-04T16:45:03Z",
            "private": False,
            "gated": False,
            "disabled": False,
            "tags": ["license:mit"],
            "cardData": {"license": "mit"},
            "siblings": [{"rfilename": path} for path in ARC_AGI_EXPECTED_RESULT_PATHS],
        },
        separators=(",", ":"),
    ).encode()


def _result_document(*, solved: int = 60) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    for index, task_id in enumerate(SYNTHETIC_TASK_IDS):
        tasks[task_id] = {
            "score": 1 if index < solved else 0,
            "cost": 1,
            "attempts": 2,
            "output_tokens": 10,
            "total_tokens": 20,
            "duration": 3,
            "num_attempts_with_empty_list": 0,
        }
    return {
        "score": solved,
        "total_tasks": 120,
        "total_cost": 120,
        "total_attempts": 240,
        "avg_cost_per_task": 1,
        "avg_cost_per_attempt": 0.5,
        "avg_output_tokens_per_task": 10,
        "avg_total_tokens_per_task": 20,
        "avg_duration_per_task": 3,
        "num_attempts_with_empty_list": 0,
        "task_results": tasks,
    }


def _encoded(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode()


def _result_files(
    *,
    base: Mapping[str, Any] | None = None,
    overrides: Mapping[str, bytes] | None = None,
) -> dict[str, bytes]:
    raw = _encoded(base or _result_document())
    files = {path: raw for path in ARC_AGI_EXPECTED_RESULT_PATHS}
    if overrides is not None:
        files.update(overrides)
    return files


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    base: Mapping[str, Any] | None = None,
    overrides: Mapping[str, bytes] | None = None,
) -> ArcAgiCapture:
    # Synthetic IDs exercise the exact digest algorithm without copying the
    # private task-key list into a public test artifact.
    monkeypatch.setattr(arc_agi, "ARC_AGI_TASK_SET_SHA256", SYNTHETIC_TASK_SET_SHA256)
    return normalize_arc_agi_public_eval_bytes(
        _revision_metadata(),
        _result_files(base=base, overrides=overrides),
        retrieved_at=RETRIEVED_AT,
    )


def _row(capture: ArcAgiCapture, path: str) -> Any:
    folder = path.removesuffix("/results.json")
    return next(row for row in capture.evidence.rows if row.subject.system_label == folder)


def _reviewed_mapping(capture: ArcAgiCapture, selected: Any) -> QualityReconciliation:
    return QualityReconciliation(
        schema_version="model-skyline/quality-reconciliation/v1alpha1",
        entries=(
            QualityReconciliationEntry(
                row_id=selected.row_id,
                adapter_id=ARC_AGI_ADAPTER_ID,
                projection_version=ARC_AGI_ADAPTER_VERSION,
                expected_source_identity_sha256=capture.evidence.source_identity_sha256,
                expected_subject_identity_sha256=selected.subject_identity_sha256,
                relationship=QualityMappingRelationship.REVIEWED_QUALITY_PROJECTION,
                offering=OfferingKey(
                    offering_id="reviewed-provider/reviewed-model@arc-agi",
                    model_id="reviewed-model",
                    provider="reviewed-provider",
                    endpoint=None,
                    billing_mode=None,
                    region=None,
                    service_tier=None,
                    quantization=None,
                    reasoning_effort=None,
                    agent_harness=None,
                    capabilities=("reasoning",),
                ).model_dump(mode="json"),
                review_evidence="Synthetic exact human review; no label inference.",
                reviewed_at=RETRIEVED_AT,
            ),
        ),
    )


def test_reviewed_source_pins_and_path_cohort_are_exact() -> None:
    assert arc_agi.ARC_AGI_HF_DATASET_ID == "arcprize/arc_agi_v2_public_eval"
    assert ARC_AGI_HF_REVISION == "026789c1c12a4c34580a32e84dcaf5630d7e8f31"
    assert ARC_AGI_TASK_COMMIT == "f3283f727488ad98fe575ea6a5ac981e4a188e49"
    assert ARC_AGI_HARNESS_COMMIT == "28e67d54b05df5be10281892243c509a42a874f1"
    assert arc_agi.ARC_AGI_TASK_EVALUATION_TREE == "8d04288aac3146b7c47d0b799c18bc9c0217d838"
    assert (
        arc_agi.ARC_AGI_TASK_SET_SHA256
        == "54ca25cdc4444e5669e272e25cbe301bbfe3aa81da8f555126095153aff69425"
    )
    assert len(ARC_AGI_EXPECTED_RESULT_PATHS) == 32
    assert tuple(sorted(ARC_AGI_EXPECTED_RESULT_PATHS)) == ARC_AGI_EXPECTED_RESULT_PATHS
    assert ARC_AGI_HF_REVISION in arc_agi.ARC_AGI_HF_REVISION_API
    assert ARC_AGI_HF_REVISION in ARC_AGI_HF_RESOLVE_PREFIX


def test_normalizes_exact_cohort_without_retaining_task_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _capture(monkeypatch)

    assert capture.rows_seen == 32
    assert capture.valid_rows == 32
    assert capture.invalid_rows == 0
    assert capture.evidence.raw_audit.upstream_revision == ARC_AGI_HF_REVISION
    source = capture.evidence.source_identity
    assert source.source_version.startswith(
        f"hf-revision/{ARC_AGI_HF_REVISION}/operator-raw-sha256:"
    )
    assert source.dataset.version == "unattested"
    assert source.evaluator_harness.version == "unattested"
    assert source.protocol.version == "unattested-attempt-policy"
    assert source.scope["hf_revision"] == ARC_AGI_HF_REVISION
    assert source.scope["capture_trust"] == "operator_raw_bound"
    assert capture.evidence.raw_audit.capture_method == (
        "operator-supplied-fixed-revision-multifile"
    )
    assert capture.evidence.raw_audit.source_locator.startswith(
        "operator-supplied-arc-agi-capture:sha256:"
    )
    assert capture.evidence.rights.license_expression == "NOASSERTION"
    manifest = capture.manifest()
    assert manifest["references"]["task_repository"]["commit"] == ARC_AGI_TASK_COMMIT
    assert manifest["references"]["evaluator_harness"]["commit"] == ARC_AGI_HARNESS_COMMIT

    row = capture.evidence.rows[0]
    assert row.subject.route_disclosure.value == "unknown"
    assert row.subject.kind.value == "single_model_system"
    assert len(row.subject.model_claims) == 1
    assert row.subject.model_claims[0].model_id == row.subject.system_label
    assert row.subject.model_claims[0].provider is None
    assert row.subject.model_claims[0].claims["dataset_path_only"] is True
    assert row.subject.model_claims[0].claims["identity_reviewed"] is False
    assert row.subject.benchmark_agent is None
    assert row.result is not None
    score = next(
        measurement
        for measurement in row.result.measurements
        if measurement.id == "arc_agi_2_public_eval_score_percent"
    )
    assert score.value == Decimal(50)

    serialized = json.dumps(
        {
            "evidence": capture.evidence.model_dump(mode="json"),
            "inventory": capture.inventory(),
            "manifest": capture.manifest(),
        }
    )
    assert SYNTHETIC_TASK_IDS[0] not in serialized
    assert SYNTHETIC_TASK_IDS[-1] not in serialized
    assert "task_results" not in serialized
    assert "attempt_1" not in serialized
    assert "attempt_2" not in serialized


def test_fractional_scores_are_recomputed_as_decimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _result_document(solved=0)
    document["task_results"][SYNTHETIC_TASK_IDS[0]]["score"] = 0.1
    document["task_results"][SYNTHETIC_TASK_IDS[1]]["score"] = 0.2
    document["score"] = 0.3

    capture = _capture(monkeypatch, base=document)

    result = capture.evidence.rows[0].result
    assert result is not None
    score = next(
        measurement
        for measurement in result.measurements
        if measurement.id == "arc_agi_2_public_eval_score_percent"
    )
    assert score.value == Decimal("0.25")


def test_exact_reviewed_quality_projection_maps_but_unreviewed_claim_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _capture(monkeypatch)
    selected = capture.evidence.rows[0]
    empty = QualityReconciliation(
        schema_version="model-skyline/quality-reconciliation/v1alpha1",
        entries=(),
    )

    unreviewed = reconcile_quality_evidence(capture.evidence, empty)
    unreviewed_record = next(
        record for record in unreviewed.records if record.row_id == selected.row_id
    )
    assert unreviewed_record.outcome is QualityImportOutcome.UNKNOWN_ROUTE

    reviewed = _reviewed_mapping(capture, selected)

    report = reconcile_quality_evidence(capture.evidence, reviewed)
    record = next(record for record in report.records if record.row_id == selected.row_id)
    assert record.outcome is QualityImportOutcome.MAPPED
    assert len(report.mapped_rows) == 1
    mapped = report.mapped_rows[0]
    assert mapped.row_id == selected.row_id
    assert {measurement.id for measurement in mapped.result.measurements} == {
        "arc_agi_2_public_eval_score_percent"
    }
    assert all(
        measurement.role is QualityMeasurementRole.QUALITY
        for measurement in mapped.result.measurements
    )


def test_only_high_level_fixed_capture_gets_official_provenance_and_rights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arc_agi, "ARC_AGI_TASK_SET_SHA256", SYNTHETIC_TASK_SET_SHA256)
    loaded = arc_agi.LoadedArcAgiSource(
        revision_metadata=_revision_metadata(),
        result_files=tuple(_result_files().items()),
        retrieved_at=RETRIEVED_AT,
    )

    def fake_load(
        *,
        retrieved_at: datetime | None = None,
        timeout_seconds: float = arc_agi.DEFAULT_TIMEOUT_SECONDS,
    ) -> arc_agi.LoadedArcAgiSource:
        del retrieved_at, timeout_seconds
        return loaded

    monkeypatch.setattr(arc_agi, "load_arc_agi_public_eval_source", fake_load)
    official = arc_agi.capture_arc_agi_public_eval()
    operator = arc_agi.normalize_arc_agi_public_eval_source(loaded)

    assert official.evidence.raw_audit.capture_method == ("https-get-fixed-hf-revision-multifile")
    assert official.evidence.raw_audit.source_locator == arc_agi.ARC_AGI_HF_REVISION_API
    assert official.evidence.source_identity.source_version == (
        f"hf-revision/{ARC_AGI_HF_REVISION}"
    )
    assert official.evidence.source_identity.scope["capture_trust"] == ("official_fixed_network")
    assert official.evidence.rights.license_expression == "MIT AND Apache-2.0"

    assert operator.evidence.raw_audit.capture_method == (
        "operator-supplied-fixed-revision-multifile"
    )
    assert operator.evidence.source_identity_sha256 != official.evidence.source_identity_sha256
    assert operator.evidence.rights.license_expression == "NOASSERTION"

    selected = official.evidence.rows[0]
    empty = QualityReconciliation(
        schema_version="model-skyline/quality-reconciliation/v1alpha1",
        entries=(),
    )
    unreviewed = reconcile_quality_evidence(official.evidence, empty)
    assert (
        next(record.outcome for record in unreviewed.records if record.row_id == selected.row_id)
        is QualityImportOutcome.UNKNOWN_ROUTE
    )
    reviewed = reconcile_quality_evidence(
        official.evidence,
        _reviewed_mapping(official, selected),
    )
    assert reviewed.mapped_rows[0].row_id == selected.row_id


def test_capture_requires_exact_result_file_and_metadata_path_cohorts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arc_agi, "ARC_AGI_TASK_SET_SHA256", SYNTHETIC_TASK_SET_SHA256)
    missing_file = _result_files()
    missing_file.pop(ARC_AGI_EXPECTED_RESULT_PATHS[-1])
    with pytest.raises(ArcAgiAdapterError, match="exactly match"):
        normalize_arc_agi_public_eval_bytes(
            _revision_metadata(),
            missing_file,
            retrieved_at=RETRIEVED_AT,
        )

    drifted_metadata = json.loads(_revision_metadata())
    drifted_metadata["siblings"].pop()
    with pytest.raises(ArcAgiAdapterError, match="path cohort drifted"):
        normalize_arc_agi_public_eval_bytes(
            _encoded(drifted_metadata),
            _result_files(),
            retrieved_at=RETRIEVED_AT,
        )


def test_quarantines_incomplete_mismatched_aggregate_and_schema_invalid_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete = _result_document()
    incomplete["task_results"].pop(SYNTHETIC_TASK_IDS[-1])
    incomplete["total_tasks"] = 119

    mismatched = _result_document()
    mismatched["task_results"]["deadbeef"] = mismatched["task_results"].pop(SYNTHETIC_TASK_IDS[-1])

    aggregate_mismatch = _result_document()
    aggregate_mismatch["score"] = 61

    schema_invalid = _result_document()
    schema_invalid["future_semantic_field"] = True

    selected = ARC_AGI_EXPECTED_RESULT_PATHS[:4]
    capture = _capture(
        monkeypatch,
        overrides={
            selected[0]: _encoded(incomplete),
            selected[1]: _encoded(mismatched),
            selected[2]: _encoded(aggregate_mismatch),
            selected[3]: _encoded(schema_invalid),
        },
    )

    assert capture.valid_rows == 28
    assert capture.invalid_rows == 4
    assert {_row(capture, path).invalid_result.code for path in selected} == {
        "incomplete_task_cohort",
        "task_set_mismatch",
        "aggregate_score_mismatch",
        "schema_invalid",
    }


def test_duplicate_keys_and_bounded_json_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = ARC_AGI_EXPECTED_RESULT_PATHS[:2]
    deeply_nested = b"[" * 21 + b"0" + b"]" * 21
    capture = _capture(
        monkeypatch,
        overrides={
            selected[0]: b'{"score":0,"score":0}',
            selected[1]: deeply_nested,
        },
    )

    assert capture.valid_rows == 30
    for path in selected:
        invalid = _row(capture, path).invalid_result
        assert invalid is not None
        assert invalid.code == "schema_invalid"

    duplicated_metadata = _revision_metadata().replace(
        b'{"id":',
        b'{"id":"duplicate","id":',
        1,
    )
    with pytest.raises(ArcAgiAdapterError, match="duplicate-key-free"):
        normalize_arc_agi_public_eval_bytes(
            duplicated_metadata,
            _result_files(),
            retrieved_at=RETRIEVED_AT,
        )


def test_missing_invalid_and_zero_accounting_preserve_quality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = _result_document()
    missing = {
        "score": missing["score"],
        "total_tasks": missing["total_tasks"],
        "task_results": {
            task_id: {"score": task["score"]} for task_id, task in missing["task_results"].items()
        },
    }

    invalid = _result_document()
    invalid["total_cost"] = "not-a-number"

    zero = _result_document()
    zero["total_cost"] = 0
    zero["avg_cost_per_task"] = 0
    zero["avg_cost_per_attempt"] = 0
    for task in zero["task_results"].values():
        task["cost"] = 0

    no_attempts = _result_document()
    no_attempts["total_attempts"] = 0
    no_attempts["avg_cost_per_attempt"] = 0
    for task in no_attempts["task_results"].values():
        task["attempts"] = 0

    selected = ARC_AGI_EXPECTED_RESULT_PATHS[:4]
    capture = _capture(
        monkeypatch,
        overrides={
            selected[0]: _encoded(missing),
            selected[1]: _encoded(invalid),
            selected[2]: _encoded(zero),
            selected[3]: _encoded(no_attempts),
        },
    )

    for path in selected:
        result = _row(capture, path).result
        assert result is not None
        assert result.primary_metric == "arc_agi_2_public_eval_score_percent"
        assert any(item.role is QualityMeasurementRole.QUALITY for item in result.measurements)
        assert not any(item.role is QualityMeasurementRole.COST for item in result.measurements)
        assert result.metadata["accounting_coherent"]["cost"] is False


def test_operator_supplied_result_change_is_raw_and_source_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _capture(monkeypatch, base=_result_document(solved=10))
    second = _capture(monkeypatch, base=_result_document(solved=11))

    assert first.evidence.raw_audit_sha256 != second.evidence.raw_audit_sha256
    assert first.evidence.source_identity_sha256 != second.evidence.source_identity_sha256
    assert [row.subject_identity_sha256 for row in first.evidence.rows] == [
        row.subject_identity_sha256 for row in second.evidence.rows
    ]
    assert [row.result_sha256 for row in first.evidence.rows] != [
        row.result_sha256 for row in second.evidence.rows
    ]
    original_row = first.evidence.rows[0]
    changed_report = reconcile_quality_evidence(
        second.evidence,
        _reviewed_mapping(first, original_row),
    )
    changed_record = next(
        record for record in changed_report.records if record.row_id == original_row.row_id
    )
    assert changed_record.outcome is QualityImportOutcome.IDENTITY_DRIFT


def test_result_identity_is_independent_of_ambient_decimal_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _capture(monkeypatch)
    with localcontext(Context(prec=2)):
        constrained = _capture(monkeypatch)

    assert constrained.evidence == baseline.evidence


def test_fixed_result_url_percent_encodes_the_reviewed_path() -> None:
    path = ARC_AGI_EXPECTED_RESULT_PATHS[0]
    url = arc_agi._fixed_result_url(path)

    assert url.startswith(ARC_AGI_HF_RESOLVE_PREFIX)
    assert url.removeprefix(ARC_AGI_HF_RESOLVE_PREFIX).endswith("%2Fresults.json")
    assert "/" not in url.removeprefix(ARC_AGI_HF_RESOLVE_PREFIX)
    with pytest.raises(ArcAgiAdapterError, match="unreviewed"):
        arc_agi._fixed_result_url("../unreviewed/results.json")


def test_fetch_requires_exact_repo_commit_header() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain", "x-repo-commit": "wrong"},
            content=b"{}",
        )

    with (
        httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        ) as client,
        pytest.raises(ArcAgiAdapterError, match="pinned repo commit"),
    ):
        arc_agi._fetch(
            client,
            arc_agi._fixed_result_url(ARC_AGI_EXPECTED_RESULT_PATHS[0]),
            maximum=1_000,
            media_types=frozenset({"text/plain"}),
            require_repo_commit=True,
        )


def test_fetch_rejects_expired_total_capture_deadline() -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200))) as client,
        pytest.raises(ArcAgiAdapterError, match="total time limit"),
    ):
        arc_agi._fetch(
            client,
            arc_agi._fixed_result_url(ARC_AGI_EXPECTED_RESULT_PATHS[0]),
            maximum=1_000,
            media_types=frozenset({"text/plain"}),
            require_repo_commit=True,
            deadline=arc_agi.time_module.monotonic() - 1,
        )


@pytest.mark.skipif(os.name != "posix", reason="explicit private modes are POSIX-specific")
def test_writes_private_atomic_bundle_without_raw_or_task_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    capture = _capture(monkeypatch)
    output = tmp_path / "arc-agi-capture"
    paths = write_arc_agi_public_eval_capture(capture, output)

    assert {path.name for path in paths} == {
        EVIDENCE_FILENAME,
        INVENTORY_FILENAME,
        MANIFEST_FILENAME,
    }
    assert output.stat().st_mode & 0o777 == 0o700
    assert {path.stat().st_mode & 0o777 for path in paths} == {0o600}
    published = "".join(path.read_text() for path in paths)
    assert SYNTHETIC_TASK_IDS[0] not in published
    assert SYNTHETIC_TASK_IDS[-1] not in published
    assert "task_results" not in published
    assert "attempt_1" not in published

    manifest = json.loads((output / MANIFEST_FILENAME).read_text())
    for filename, expected_digest in manifest["outputs"]["sha256"].items():
        assert hashlib.sha256((output / filename).read_bytes()).hexdigest() == expected_digest

    with pytest.raises(ArcAgiAdapterError, match="overwrite"):
        write_arc_agi_public_eval_capture(capture, output)


def test_capture_does_not_mutate_input_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    document = _result_document()
    baseline = copy.deepcopy(document)

    _capture(monkeypatch, base=document)

    assert document == baseline


def test_cli_captures_private_fixed_network_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    capture = _capture(monkeypatch)
    monkeypatch.setattr(cli_module, "capture_arc_agi_public_eval", lambda **_kwargs: capture)
    output = tmp_path / "arc-agi-cli-capture"

    result = CliRunner().invoke(
        app,
        ["capture-arc-agi-2-public-eval", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert "captured 32 ARC-AGI-2 public-eval rows" in result.output
    assert {path.name for path in output.iterdir()} == {
        EVIDENCE_FILENAME,
        INVENTORY_FILENAME,
        MANIFEST_FILENAME,
    }
