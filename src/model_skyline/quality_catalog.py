"""Project exactly reconciled benchmark evidence into quality-only catalogs.

Adapters normalize upstream bytes into :class:`QualityEvidenceSet` values.
Operators then review source row identities against complete ``OfferingKey``
values.  This module is the shared final step: it replays that reconciliation
and emits ordinary observations without ever matching a model display name.

For ``reviewed_quality_projection`` relationships, the reconciliation layer
has already removed benchmark cost, latency, token usage, counts, and free-form
result metadata.  This projector preserves that boundary and carries only
content digests—not source labels or row locators—into the routable catalog.
"""

from __future__ import annotations

from model_skyline.models import (
    Observation,
    ObservationCatalog,
    OfferingObservation,
    SourceReference,
    WorkloadReference,
)
from model_skyline.quality_evidence import (
    QualityEvidenceSet,
    QualityImportReport,
    QualityMappingRelationship,
    QualityPublicationScope,
    QualityReconciliation,
    reconcile_quality_evidence,
)


class QualityCatalogError(ValueError):
    """Reviewed evidence cannot be projected without weakening its bindings."""


def quality_source_reference(
    evidence: QualityEvidenceSet,
) -> SourceReference:
    """Build the canonical source descriptor for one normalized capture.

    The semantic source identity is stable across result-only refreshes, while
    ``raw_sha256`` and ``retrieved_at`` identify the exact capture used for this
    catalog.  Neither identity is a claim that an upstream label names a
    production route.
    """

    validated = QualityEvidenceSet.model_validate(evidence.model_dump(mode="json"))
    identity = validated.source_identity
    source_url = _public_locator(validated.raw_audit.source_locator)
    terms_url = _public_locator(validated.rights.terms_locator)
    return SourceReference(
        id=identity.source_id,
        version=f"source-identity-sha256:{validated.source_identity_sha256}",
        url=source_url,
        terms_url=terms_url,
        # Preserve the descriptive upstream license for rights audit.  The
        # independent publication_safe/public_release_blocked boundary, not
        # this expression, controls redistribution of the route-bearing result.
        license=validated.rights.license_expression,
        methodology=(
            f"Normalized {identity.benchmark.id} evidence; dataset {identity.dataset.id}/"
            f"{identity.dataset.version}; split {identity.split}; evaluator "
            f"{identity.evaluator_harness.id}/{identity.evaluator_harness.version}; scorer "
            f"{identity.scorer.id}/{identity.scorer.version}; projection "
            f"{identity.projection.id}/{identity.projection.version}; semantic source identity "
            f"sha256:{validated.source_identity_sha256}. Exact raw capture bytes are "
            "content-addressed but not retained in the catalog. Model labels are not routes; "
            "only a separate exact reviewed reconciliation can create an offering observation."
        ),
        raw_sha256=validated.raw_audit.raw_sha256,
        retrieved_at=validated.raw_audit.retrieved_at,
    )


def _public_locator(locator: str | None) -> str | None:
    """Retain an evidence locator only when it satisfies the public URL contract."""

    if locator is None:
        return None
    try:
        probe = SourceReference(id="quality-locator-validation", url=locator)
    except ValueError:
        return None
    return str(probe.url) if probe.url is not None else None


def quality_workload_reference(
    evidence: QualityEvidenceSet,
    *,
    workload_id: str,
    unit: str,
) -> WorkloadReference:
    """Bind a workload reference to the evidence's semantic benchmark identity."""

    validated = QualityEvidenceSet.model_validate(evidence.model_dump(mode="json"))
    return WorkloadReference(
        id=workload_id,
        version=f"source-identity-sha256:{validated.source_identity_sha256}",
        unit=unit,
    )


def _validated_projection_inputs(
    evidence: QualityEvidenceSet,
    reconciliation: QualityReconciliation,
    report: QualityImportReport,
    workload: WorkloadReference,
    source: SourceReference,
) -> tuple[
    QualityEvidenceSet,
    QualityReconciliation,
    QualityImportReport,
    WorkloadReference,
    SourceReference,
]:
    validated_evidence = QualityEvidenceSet.model_validate(evidence.model_dump(mode="json"))
    validated_reconciliation = QualityReconciliation.model_validate(
        reconciliation.model_dump(mode="json")
    )
    validated_report = QualityImportReport.model_validate(report.model_dump(mode="json"))
    validated_workload = WorkloadReference.model_validate(workload.model_dump(mode="json"))
    validated_source = SourceReference.model_validate(source.model_dump(mode="json"))

    if validated_report.raw_audit_sha256 != validated_evidence.raw_audit_sha256:
        raise QualityCatalogError("quality import report raw-audit identity mismatch")
    if validated_report.source_identity_sha256 != validated_evidence.source_identity_sha256:
        raise QualityCatalogError("quality import report source identity mismatch")
    if validated_report.rights_sha256 != validated_evidence.rights_sha256:
        raise QualityCatalogError("quality import report rights identity mismatch")
    if validated_report.reconciliation_sha256 != validated_reconciliation.content_sha256:
        raise QualityCatalogError("quality import report reconciliation identity mismatch")
    replayed_report = reconcile_quality_evidence(
        validated_evidence,
        validated_reconciliation,
        publication_scope=validated_report.publication_scope,
    )
    if replayed_report != validated_report:
        raise QualityCatalogError(
            "quality import report does not match a deterministic reconciliation replay"
        )

    expected_version = f"source-identity-sha256:{validated_evidence.source_identity_sha256}"
    if validated_workload.version != expected_version:
        raise QualityCatalogError("quality workload version must bind the source identity")
    if validated_source != quality_source_reference(validated_evidence):
        raise QualityCatalogError(
            "quality source must exactly match evidence-derived provenance and rights"
        )

    return (
        validated_evidence,
        validated_reconciliation,
        validated_report,
        validated_workload,
        validated_source,
    )


def project_quality_import_report(
    evidence: QualityEvidenceSet,
    reconciliation: QualityReconciliation,
    report: QualityImportReport,
    *,
    workload: WorkloadReference,
    source: SourceReference,
) -> ObservationCatalog:
    """Project mapped results without retaining labels or silently widening scope."""

    evidence, _, report, workload, source = _validated_projection_inputs(
        evidence,
        reconciliation,
        report,
        workload,
        source,
    )
    evidence_rows = {row.row_id: row for row in evidence.rows}
    offerings: list[OfferingObservation] = []
    for mapped in report.mapped_rows:
        evidence_row = evidence_rows.get(mapped.row_id)
        if evidence_row is None:
            raise QualityCatalogError("mapped quality row is absent from normalized evidence")
        if evidence_row.subject_identity_sha256 != mapped.subject_identity_sha256:
            raise QualityCatalogError("mapped quality row subject identity mismatch")
        if evidence_row.result_sha256 != mapped.evidence_result_sha256:
            raise QualityCatalogError("mapped quality row result identity mismatch")
        if evidence_row.result is None:
            raise QualityCatalogError("mapped quality row points to quarantined evidence")
        expected_result = (
            evidence_row.result
            if mapped.relationship is QualityMappingRelationship.EXACT_SUBJECT_ROUTE
            else evidence_row.result.quality_projection()
        )
        if mapped.result != expected_result:
            raise QualityCatalogError("mapped quality result does not match normalized evidence")

        signals = {
            measurement.id: Observation(
                value=measurement.value,
                unit=measurement.unit,
                lower=measurement.lower,
                upper=measurement.upper,
                sample_count=measurement.sample_count,
                observed_at=mapped.result.observed_at,
                source=source,
            )
            for measurement in mapped.result.measurements
        }
        offerings.append(
            OfferingObservation(
                offering=mapped.offering,
                signals=signals,
                metadata={
                    "quality_source_identity_sha256": mapped.source_identity_sha256,
                    "quality_subject_identity_sha256": mapped.subject_identity_sha256,
                    "quality_result_sha256": mapped.result_sha256,
                    "quality_evidence_result_sha256": mapped.evidence_result_sha256,
                    "quality_rights_sha256": mapped.rights_sha256,
                    "quality_reconciliation_sha256": report.reconciliation_sha256,
                    "quality_mapping_relationship": mapped.relationship.value,
                    "quality_evidence_license_expression": (evidence.rights.license_expression),
                    "quality_publication_permission": (
                        evidence.rights.publication_permission.value
                    ),
                    "quality_only_projection": (
                        mapped.relationship
                        is QualityMappingRelationship.REVIEWED_QUALITY_PROJECTION
                    ),
                    "quality_counts": {count.id: count.value for count in mapped.result.counts},
                    "publication_safe": False,
                },
                default_source=source,
            )
        )
    if not offerings:
        raise QualityCatalogError("quality reconciliation produced no mapped offerings")
    offerings.sort(key=lambda item: item.offering.offering_id)
    return ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=workload,
        offerings=offerings,
    )


def reconcile_quality_catalog(
    evidence: QualityEvidenceSet,
    reconciliation: QualityReconciliation,
    *,
    workload: WorkloadReference,
    source: SourceReference,
    publication_scope: QualityPublicationScope = QualityPublicationScope.INTERNAL,
) -> tuple[ObservationCatalog, QualityImportReport]:
    """Reconcile and project one adapter-neutral quality catalog in one call."""

    validated_evidence = QualityEvidenceSet.model_validate(evidence.model_dump(mode="json"))
    validated_reconciliation = QualityReconciliation.model_validate(
        reconciliation.model_dump(mode="json")
    )
    report = reconcile_quality_evidence(
        validated_evidence,
        validated_reconciliation,
        publication_scope=publication_scope,
    )
    return (
        project_quality_import_report(
            validated_evidence,
            validated_reconciliation,
            report,
            workload=workload,
            source=source,
        ),
        report,
    )
