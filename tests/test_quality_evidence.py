from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from model_skyline.canonical import canonical_bytes
from model_skyline.quality_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    RECONCILIATION_SCHEMA_VERSION,
    MappedQualityRow,
    QualityComponentIdentity,
    QualityDigestDomain,
    QualityEvidenceRow,
    QualityEvidenceSet,
    QualityImportOutcome,
    QualityInvalidResult,
    QualityMappingRelationship,
    QualityMeasurement,
    QualityMeasurementRole,
    QualityModelClaim,
    QualityPublicationPermission,
    QualityPublicationScope,
    QualityRawAudit,
    QualityReconciliation,
    QualityResult,
    QualityRights,
    QualityRouteDisclosure,
    QualitySourceIdentity,
    QualitySubjectIdentity,
    QualitySubjectKind,
    quality_content_id,
    quality_content_sha256,
    quality_raw_sha256,
    reconcile_quality_evidence,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _component(name: str, version: str = "1") -> QualityComponentIdentity:
    return QualityComponentIdentity(id=name, version=version, configuration={})


def _source(*, version: str = "board-1") -> QualitySourceIdentity:
    return QualitySourceIdentity(
        source_id="example-leaderboard",
        source_version=version,
        benchmark=_component("example-benchmark", "2"),
        dataset=_component("example-dataset", "verified-3"),
        split="verified",
        evaluator_harness=_component("evaluation-only-harness", "7"),
        scorer=_component("exact-match-scorer", "1"),
        protocol=_component("benchmark-protocol", "4"),
        projection=_component("fixture-adapter", "projection-1"),
        scope={
            "task_set_sha256": "a" * 64,
            "sample_count_meaning": "scored tasks",
            "retry_limit": 0,
        },
    )


def _raw(*, marker: str = "first", retrieved_at: datetime = NOW) -> QualityRawAudit:
    raw = f'{{"capture":"{marker}"}}'.encode()
    return QualityRawAudit(
        source_locator="https://bench.example/results.json",
        raw_sha256=quality_raw_sha256(raw),
        retrieved_at=retrieved_at,
        upstream_revision="commit-123",
        capture_method="operator-supplied-local-snapshot",
        parser_implementation=_component("fixture-parser-build", "sha256:abc"),
        metadata={"marker": marker},
    )


def _rights(
    permission: QualityPublicationPermission = QualityPublicationPermission.UNRESTRICTED,
    *,
    evidence: str = "Reviewed source license and result terms.",
) -> QualityRights:
    return QualityRights(
        license_expression="Apache-2.0",
        terms_locator="https://bench.example/terms",
        publication_permission=permission,
        reviewed_at=NOW,
        review_evidence=evidence,
    )


def _claim(model_id: str = "model-revision-2026-08-01") -> QualityModelClaim:
    return QualityModelClaim(
        model_id=model_id,
        display_name="Upstream display label",
        provider="provider-a",
        endpoint="responses",
        revision="2026-08-01",
        reasoning_effort="high",
    )


def _subject(
    row_id: str = "row-1",
    *,
    kind: QualitySubjectKind = QualitySubjectKind.SINGLE_MODEL_SYSTEM,
    route: QualityRouteDisclosure = QualityRouteDisclosure.EXACT,
    reasoning: str = "high",
) -> QualitySubjectIdentity:
    claims: tuple[QualityModelClaim, ...]
    if kind is QualitySubjectKind.SINGLE_MODEL_SYSTEM:
        claims = (_claim(),)
    elif kind is QualitySubjectKind.COMPOSITE_SYSTEM:
        claims = (_claim("model-a"), _claim("model-b"))
    elif kind is QualitySubjectKind.UNDISCLOSED_SYSTEM:
        claims = ()
    else:
        claims = (_claim("router-component"),)
    return QualitySubjectIdentity(
        row_id=row_id,
        kind=kind,
        system_label=f"system for {row_id}",
        model_claims=claims,
        benchmark_agent=_component("submitted-benchmark-agent", "3"),
        route_disclosure=route,
        reasoning_claims={"effort": reasoning},
        attempt_claims={"attempts_per_task": 1},
    )


def _result(value: str = "0.75") -> QualityResult:
    return QualityResult(
        primary_metric="solve_rate",
        measurements=(
            QualityMeasurement(
                id="reported_cost_usd",
                role=QualityMeasurementRole.COST,
                value="12.50",
                unit="usd/run",
            ),
            QualityMeasurement(
                id="solve_rate",
                role=QualityMeasurementRole.QUALITY,
                value=value,
                unit="ratio",
                lower="0.70",
                upper="0.80",
                sample_count=100,
            ),
        ),
        counts=(),
        observed_at=NOW,
        metadata={"telemetry_method": "upstream benchmark report"},
    )


def _row(
    row_id: str = "row-1",
    *,
    kind: QualitySubjectKind = QualitySubjectKind.SINGLE_MODEL_SYSTEM,
    route: QualityRouteDisclosure = QualityRouteDisclosure.EXACT,
    result: QualityResult | None = None,
    invalid: bool = False,
) -> QualityEvidenceRow:
    subject = _subject(row_id, kind=kind, route=route)
    if invalid:
        return QualityEvidenceRow(
            subject=subject,
            invalid_result=QualityInvalidResult(
                code="incoherent-counts",
                detail="Reported successes exceeded the declared denominator.",
            ),
        )
    return QualityEvidenceRow(subject=subject, result=result or _result())


def _evidence(
    rows: tuple[QualityEvidenceRow, ...] = (_row(),),
    *,
    source: QualitySourceIdentity | None = None,
    raw: QualityRawAudit | None = None,
    rights: QualityRights | None = None,
) -> QualityEvidenceSet:
    return QualityEvidenceSet(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        raw_audit=raw or _raw(),
        source_identity=source or _source(),
        rights=rights or _rights(),
        rows=rows,
    )


def _offering(
    *, offering_id: str = "provider-a/model-1/high", model_id: str = "model-1"
) -> dict[str, Any]:
    # Every field, including every null and the empty capability list, is review input.
    return {
        "offering_id": offering_id,
        "model_id": model_id,
        "provider": "provider-a",
        "endpoint": "responses",
        "billing_mode": None,
        "region": None,
        "service_tier": None,
        "quantization": None,
        "reasoning_effort": "high",
        "agent_harness": None,
        "capabilities": ["tools"],
    }


def _entry(
    evidence: QualityEvidenceSet,
    row_id: str = "row-1",
    *,
    offering: dict[str, Any] | None = None,
    expected_source: str | None = None,
    expected_subject: str | None = None,
    expected_raw_audit: str | None = None,
    relationship: QualityMappingRelationship = QualityMappingRelationship.EXACT_SUBJECT_ROUTE,
) -> dict[str, Any]:
    row = next(item for item in evidence.rows if item.row_id == row_id)
    return {
        "row_id": row_id,
        "adapter_id": evidence.source_identity.projection.id,
        "projection_version": evidence.source_identity.projection.version,
        "expected_source_identity_sha256": expected_source or evidence.source_identity_sha256,
        "expected_subject_identity_sha256": expected_subject or row.subject_identity_sha256,
        "expected_raw_audit_sha256": expected_raw_audit,
        "relationship": relationship,
        "offering": offering or _offering(offering_id=f"offering/{row_id}"),
        "review_evidence": "Compared immutable submission metadata with provider route records.",
        "reviewed_at": NOW.isoformat(),
    }


def _reconciliation(*entries: dict[str, Any]) -> QualityReconciliation:
    return QualityReconciliation.model_validate(
        {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "entries": list(entries),
        }
    )


def _outcomes(report: Any) -> dict[str, QualityImportOutcome]:
    return {item.row_id: item.outcome for item in report.records}


def test_content_hashes_are_canonical_domain_separated_and_exact_raw_bytes() -> None:
    component = _component("same", "1")
    source_digest = quality_content_sha256(
        QualityDigestDomain.SOURCE_IDENTITY,
        component,
    )
    subject_digest = quality_content_sha256(
        QualityDigestDomain.SUBJECT_IDENTITY,
        component,
    )

    assert len(source_digest) == 64
    assert source_digest != subject_digest
    assert quality_content_id(QualityDigestDomain.SOURCE_IDENTITY, component) == (
        f"model-skyline:quality:source-identity:sha256:{source_digest}"
    )
    assert quality_raw_sha256(b'{"x":1}') == hashlib.sha256(b'{"x":1}').hexdigest()
    assert quality_raw_sha256(b'{ "x": 1 }') != quality_raw_sha256(b'{"x":1}')
    with pytest.raises(TypeError, match="must be bytes"):
        quality_raw_sha256("not bytes")  # type: ignore[arg-type]


def test_normalized_models_are_bounded_canonical_and_timezone_stable() -> None:
    result = QualityResult(
        primary_metric="z-score",
        measurements=(
            QualityMeasurement(
                id="z-score",
                role=QualityMeasurementRole.QUALITY,
                value=Decimal("0.500"),
                unit="ratio",
            ),
            QualityMeasurement(
                id="a-cost",
                role=QualityMeasurementRole.COST,
                value=Decimal("1.20"),
                unit="usd/run",
            ),
        ),
        observed_at=NOW.astimezone(timezone(timedelta(hours=-4))),
    )
    assert [item.id for item in result.measurements] == ["a-cost", "z-score"]
    assert result.observed_at == NOW
    assert result.model_dump(mode="json")["measurements"][0]["value"] == "1.2"

    with pytest.raises(ValidationError, match="timezone"):
        _raw(retrieved_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError, match="finite"):
        QualityMeasurement(
            id="score",
            role=QualityMeasurementRole.QUALITY,
            value=Decimal("NaN"),
            unit="ratio",
        )

    nested: dict[str, Any] = {}
    cursor = nested
    for index in range(40):
        child: dict[str, Any] = {}
        cursor[str(index)] = child
        cursor = child
    with pytest.raises(ValidationError, match="depth"):
        QualityComponentIdentity(id="too-deep", version="1", configuration=nested)


def test_evidence_and_reconciliation_enforce_one_row_but_report_duplicate_targets() -> None:
    with pytest.raises(ValidationError, match="duplicate quality evidence row"):
        _evidence((_row(), _row()))

    evidence = _evidence((_row("row-1"), _row("row-2")))
    with pytest.raises(ValidationError, match="duplicate quality reconciliation row"):
        _reconciliation(_entry(evidence, "row-1"), _entry(evidence, "row-1"))

    shared_target = _offering(offering_id="one-target")
    reconciliation = _reconciliation(
        _entry(evidence, "row-1", offering=shared_target),
        _entry(evidence, "row-2", offering=shared_target),
    )
    report = reconcile_quality_evidence(evidence, reconciliation)
    assert _outcomes(report) == {
        "row-1": QualityImportOutcome.DUPLICATE_TARGET,
        "row-2": QualityImportOutcome.DUPLICATE_TARGET,
    }
    assert report.mapped_rows == ()


def test_reconciliation_requires_every_offering_key_field_explicitly() -> None:
    evidence = _evidence()
    missing_null = _entry(evidence)
    missing_null["offering"] = _offering()
    del missing_null["offering"]["agent_harness"]

    with pytest.raises(ValidationError, match="agent_harness"):
        _reconciliation(missing_null)
    with pytest.raises(ValidationError, match="every OfferingKey field"):
        _reconciliation({**_entry(evidence), "offering": "model-name"})


def test_exact_mapping_keeps_evaluator_and_production_harness_separate() -> None:
    evidence = _evidence()
    reconciliation = _reconciliation(_entry(evidence))
    report = reconcile_quality_evidence(evidence, reconciliation)

    assert _outcomes(report) == {"row-1": QualityImportOutcome.MAPPED}
    mapped = report.mapped_rows[0]
    assert mapped.source_identity.evaluator_harness.id == "evaluation-only-harness"
    assert mapped.subject.benchmark_agent is not None
    assert mapped.subject.benchmark_agent.id == "submitted-benchmark-agent"
    assert mapped.offering.agent_harness is None
    assert mapped.source_identity_sha256 == evidence.source_identity_sha256
    assert mapped.subject_identity_sha256 == evidence.rows[0].subject_identity_sha256
    assert mapped.evidence_result_sha256 == evidence.rows[0].result_sha256
    assert mapped.result_sha256 == mapped.result.content_sha256
    assert [item.id for item in mapped.result.measurements] == [
        "reported_cost_usd",
        "solve_rate",
    ]
    assert mapped.result.metadata == {"telemetry_method": "upstream benchmark report"}
    assert mapped.result_sha256 == mapped.evidence_result_sha256
    assert mapped.relationship is QualityMappingRelationship.EXACT_SUBJECT_ROUTE
    assert report.records[0].evidence_result_sha256 == evidence.rows[0].result_sha256

    inconsistent = mapped.model_dump(mode="json")
    inconsistent["evidence_result_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="preserve the complete evidence result"):
        MappedQualityRow.model_validate(inconsistent)


def test_reviewed_quality_projection_maps_unknown_route_quality_only() -> None:
    evidence = _evidence((_row(route=QualityRouteDisclosure.UNKNOWN),))
    reconciliation = _reconciliation(
        _entry(
            evidence,
            relationship=QualityMappingRelationship.REVIEWED_QUALITY_PROJECTION,
        )
    )

    report = reconcile_quality_evidence(evidence, reconciliation)
    assert _outcomes(report) == {"row-1": QualityImportOutcome.MAPPED}
    mapped = report.mapped_rows[0]
    assert mapped.relationship is QualityMappingRelationship.REVIEWED_QUALITY_PROJECTION
    assert [item.id for item in mapped.result.measurements] == ["solve_rate"]
    assert all(item.role is QualityMeasurementRole.QUALITY for item in mapped.result.measurements)
    assert mapped.result.metadata == {}
    assert mapped.result_sha256 != mapped.evidence_result_sha256
    assert report.records[0].relationship is QualityMappingRelationship.REVIEWED_QUALITY_PROJECTION
    assert report.publication_safe is False


def test_result_rights_raw_and_unrelated_rows_do_not_invalidate_mapping() -> None:
    original = _evidence()
    reconciliation = _reconciliation(_entry(original))
    original_result_digest = original.rows[0].result_sha256

    changed_row = _row(result=_result("0.76"))
    changed = _evidence(
        (changed_row, _row("new-row")),
        raw=_raw(marker="second", retrieved_at=NOW + timedelta(hours=1)),
        rights=_rights(evidence="A later rights review reached the same permission."),
    )
    report = reconcile_quality_evidence(changed, reconciliation)

    assert changed.source_identity_sha256 == original.source_identity_sha256
    changed_existing = next(item for item in changed.rows if item.row_id == "row-1")
    assert changed_existing.subject_identity_sha256 == original.rows[0].subject_identity_sha256
    assert changed_existing.result_sha256 != original_result_digest
    assert changed.raw_audit_sha256 != original.raw_audit_sha256
    assert changed.rights_sha256 != original.rights_sha256
    assert _outcomes(report) == {
        "new-row": QualityImportOutcome.UNMAPPED,
        "row-1": QualityImportOutcome.MAPPED,
    }
    assert [item.row_id for item in report.mapped_rows] == ["row-1"]


@pytest.mark.parametrize("drift", ["source", "subject", "projection", "pinned-raw"])
def test_mapping_dependencies_fail_closed_as_identity_drift(drift: str) -> None:
    original = _evidence()
    entry = _entry(original)
    evidence = original
    if drift == "source":
        evidence = _evidence(source=_source(version="board-2"))
    elif drift == "subject":
        changed_subject = _subject(reasoning="medium")
        evidence = _evidence((QualityEvidenceRow(subject=changed_subject, result=_result()),))
    elif drift == "projection":
        entry["projection_version"] = "projection-older"
    else:
        entry["expected_raw_audit_sha256"] = original.raw_audit_sha256
        evidence = _evidence(raw=_raw(marker="recaptured"))

    report = reconcile_quality_evidence(evidence, _reconciliation(entry))
    assert _outcomes(report) == {"row-1": QualityImportOutcome.IDENTITY_DRIFT}
    assert report.mapped_rows == ()


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            _row(kind=QualitySubjectKind.COMPOSITE_SYSTEM),
            QualityImportOutcome.RESEARCH_ONLY_COMPOSITE,
        ),
        (_row(kind=QualitySubjectKind.ROUTER_SYSTEM), QualityImportOutcome.RESEARCH_ONLY_COMPOSITE),
        (
            _row(kind=QualitySubjectKind.UNDISCLOSED_SYSTEM),
            QualityImportOutcome.RESEARCH_ONLY_COMPOSITE,
        ),
        (_row(route=QualityRouteDisclosure.MUTABLE_ALIAS), QualityImportOutcome.MUTABLE_ALIAS),
        (_row(route=QualityRouteDisclosure.UNKNOWN), QualityImportOutcome.UNKNOWN_ROUTE),
        (_row(invalid=True), QualityImportOutcome.INVALID_RESULT),
    ],
)
def test_non_routable_evidence_is_typed_and_quarantined(
    row: QualityEvidenceRow,
    expected: QualityImportOutcome,
) -> None:
    evidence = _evidence((row,))
    report = reconcile_quality_evidence(evidence, _reconciliation(_entry(evidence)))
    assert _outcomes(report) == {"row-1": expected}
    assert report.mapped_rows == ()


def test_unmapped_and_missing_required_rows_are_both_visible() -> None:
    evidence_with_old_row = _evidence((_row("removed-row"),))
    required_entry = _entry(evidence_with_old_row, "removed-row")
    current = _evidence((_row("new-row"),))

    report = reconcile_quality_evidence(current, _reconciliation(required_entry))
    assert _outcomes(report) == {
        "new-row": QualityImportOutcome.UNMAPPED,
        "removed-row": QualityImportOutcome.MISSING_REQUIRED_ROW,
    }


def test_rights_only_gate_requested_publication_scope() -> None:
    derived_only = _evidence(rights=_rights(QualityPublicationPermission.DERIVED_ONLY))
    reconciliation = _reconciliation(_entry(derived_only))

    internal = reconcile_quality_evidence(derived_only, reconciliation)
    derived = reconcile_quality_evidence(
        derived_only,
        reconciliation,
        publication_scope=QualityPublicationScope.DERIVED,
    )
    full = reconcile_quality_evidence(
        derived_only,
        reconciliation,
        publication_scope=QualityPublicationScope.FULL,
    )
    assert _outcomes(internal)["row-1"] is QualityImportOutcome.MAPPED
    assert _outcomes(derived)["row-1"] is QualityImportOutcome.MAPPED
    assert _outcomes(full)["row-1"] is QualityImportOutcome.LICENSE_BLOCKED

    prohibited = _evidence(rights=_rights(QualityPublicationPermission.PROHIBITED))
    prohibited_reconciliation = _reconciliation(_entry(prohibited))
    blocked = reconcile_quality_evidence(
        prohibited,
        prohibited_reconciliation,
        publication_scope=QualityPublicationScope.DERIVED,
    )
    assert _outcomes(blocked)["row-1"] is QualityImportOutcome.LICENSE_BLOCKED


def test_report_is_deterministic_for_input_order_and_self_consistent() -> None:
    forward = _evidence((_row("a"), _row("b")))
    reverse = _evidence((_row("b"), _row("a")))
    forward_mapping = _reconciliation(
        _entry(forward, "a"),
        _entry(forward, "b"),
    )
    reverse_mapping = _reconciliation(
        _entry(reverse, "b"),
        _entry(reverse, "a"),
    )

    first = reconcile_quality_evidence(forward, forward_mapping)
    second = reconcile_quality_evidence(reverse, reverse_mapping)
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first.content_sha256 == second.content_sha256
    assert [item.row_id for item in first.records] == ["a", "b"]
    assert [item.row_id for item in first.mapped_rows] == ["a", "b"]


def test_subject_kind_and_result_invariants_reject_ambiguous_evidence() -> None:
    with pytest.raises(ValidationError, match="exactly one model claim"):
        QualitySubjectIdentity(
            row_id="bad-single",
            kind=QualitySubjectKind.SINGLE_MODEL_SYSTEM,
            system_label="bad",
            model_claims=(),
            route_disclosure=QualityRouteDisclosure.EXACT,
        )
    with pytest.raises(ValidationError, match="exactly one of result or invalid_result"):
        QualityEvidenceRow(subject=_subject(), result=None, invalid_result=None)
    with pytest.raises(ValidationError, match="primary_metric"):
        QualityResult(
            primary_metric="missing",
            measurements=(
                QualityMeasurement(
                    id="score",
                    role=QualityMeasurementRole.QUALITY,
                    value="1",
                    unit="ratio",
                ),
            ),
            observed_at=NOW,
        )
