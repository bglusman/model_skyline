"""Fail-closed composition of observation catalogs for one exact workload."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Annotated, Any, Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from model_skyline.canonical import canonical_bytes, content_hash
from model_skyline.engine import catalog_hash
from model_skyline.models import (
    MAX_SELECTION_CANDIDATES,
    FrozenModel,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    PortablePublicationId,
    Sha256Digest,
    StrictModel,
    WorkloadReference,
)

MAX_COMPOSED_CATALOGS = 1_024
MAX_ENRICHMENT_PROJECTIONS = 64
MAX_ENRICHMENT_POLICY_BYTES = 16_000_000
CATALOG_ENRICHMENT_POLICY_SCHEMA_VERSION = "model-skyline/catalog-enrichment-policy/v1alpha1"
CATALOG_ENRICHMENT_AUDIT_SCHEMA_VERSION = "model-skyline/catalog-enrichment-audit/v1alpha1"
SignalId = Annotated[str, Field(min_length=1, max_length=512)]


class CatalogCompositionError(ValueError):
    """Catalogs cannot be composed without weakening identity or provenance."""


class CatalogSignalMapping(FrozenModel):
    """Reviewed projection of named signals between two complete offerings."""

    source_offering: OfferingKey
    target_offering: OfferingKey
    signal_ids: tuple[SignalId, ...] = Field(min_length=1, max_length=512)
    review_note: str = Field(min_length=1, max_length=4_096)

    @field_validator("signal_ids", mode="before")
    @classmethod
    def canonical_signal_ids(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
            raise ValueError("signal_ids must be an array of strings")
        duplicates = sorted(item for item, count in Counter(value).items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate signal id {duplicates[0]!r}")
        return tuple(sorted(value))


class CatalogSignalProjection(FrozenModel):
    """One exact source catalog and its reviewed offering mappings."""

    projection_id: PortablePublicationId
    catalog_sha256: Sha256Digest
    workload: WorkloadReference
    mappings: tuple[CatalogSignalMapping, ...] = Field(
        min_length=1, max_length=MAX_SELECTION_CANDIDATES
    )

    @field_validator("mappings")
    @classmethod
    def canonical_mappings(
        cls, value: tuple[CatalogSignalMapping, ...]
    ) -> tuple[CatalogSignalMapping, ...]:
        identities = [canonical_bytes(item) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("projection mappings must be unique")
        return tuple(item for _, item in sorted(zip(identities, value, strict=True)))


class CatalogEnrichmentPolicy(StrictModel):
    """Data-only policy for reviewed signal projection across workloads."""

    schema_version: Literal["model-skyline/catalog-enrichment-policy/v1alpha1"] = (
        "model-skyline/catalog-enrichment-policy/v1alpha1"
    )
    kind: Literal["catalog-enrichment-policy"] = "catalog-enrichment-policy"
    algorithm_version: Literal["reviewed-exact-offering-signal-projection-v1"] = (
        "reviewed-exact-offering-signal-projection-v1"
    )
    policy_id: PortablePublicationId
    base_catalog_sha256: Sha256Digest
    base_workload: WorkloadReference
    output_workload_id: str = Field(min_length=1, max_length=512)
    output_workload_unit: str = Field(min_length=1, max_length=512)
    projections: tuple[CatalogSignalProjection, ...] = Field(
        min_length=1, max_length=MAX_ENRICHMENT_PROJECTIONS
    )

    @field_validator("projections")
    @classmethod
    def canonical_projections(
        cls, value: tuple[CatalogSignalProjection, ...]
    ) -> tuple[CatalogSignalProjection, ...]:
        ids = [item.projection_id for item in value]
        hashes = [item.catalog_sha256 for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("projection ids must be unique")
        if len(hashes) != len(set(hashes)):
            raise ValueError("projection catalog hashes must be unique")
        return tuple(sorted(value, key=lambda item: item.projection_id))

    @model_validator(mode="after")
    def policy_is_bounded_and_unambiguous(self) -> Self:
        mapping_count = sum(len(item.mappings) for item in self.projections)
        if mapping_count > MAX_SELECTION_CANDIDATES:
            raise ValueError(f"policy exceeds the mapping limit of {MAX_SELECTION_CANDIDATES}")
        target_signals = [
            (canonical_bytes(mapping.target_offering), signal_id)
            for projection in self.projections
            for mapping in projection.mappings
            for signal_id in mapping.signal_ids
        ]
        if len(target_signals) != len(set(target_signals)):
            raise ValueError("a target offering signal may be projected only once")
        if any(item.workload == self.base_workload for item in self.projections):
            raise ValueError(
                "cross-workload projections must differ from the base workload; "
                "use compose_catalogs for same-workload inputs"
            )
        if len(canonical_bytes(self.model_dump(mode="json"))) > MAX_ENRICHMENT_POLICY_BYTES:
            raise ValueError(f"policy exceeds {MAX_ENRICHMENT_POLICY_BYTES} canonical bytes")
        return self


def _workload_label(catalog: ObservationCatalog) -> str:
    workload = catalog.workload
    return f"{workload.id}@{workload.version}/{workload.unit}"


def _effective_signals(item: OfferingObservation) -> dict[str, Observation]:
    """Make fallback provenance explicit before combining offering rows."""

    return {
        signal_id: (
            observation
            if observation.source is not None or item.default_source is None
            else observation.model_copy(update={"source": item.default_source})
        )
        for signal_id, observation in item.signals.items()
    }


def _merge_metadata(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    offering_id: str,
    path: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Merge only identical or disjoint metadata leaves."""

    merged: dict[str, Any] = {}
    for key in sorted(set(left) | set(right)):
        if key not in left:
            merged[key] = deepcopy(right[key])
            continue
        if key not in right:
            merged[key] = deepcopy(left[key])
            continue
        left_value = left[key]
        right_value = right[key]
        if left_value == right_value:
            merged[key] = deepcopy(left_value)
            continue
        if isinstance(left_value, Mapping) and isinstance(right_value, Mapping):
            merged[key] = _merge_metadata(
                left_value,
                right_value,
                offering_id=offering_id,
                path=(*path, key),
            )
            continue
        field = ".".join((*path, key))
        raise CatalogCompositionError(
            f"offering {offering_id!r} has conflicting metadata at {field!r}"
        )
    return merged


def _merge_offering_rows(
    left: OfferingObservation,
    right: OfferingObservation,
) -> OfferingObservation:
    offering_id = left.offering.offering_id
    if left == right:
        return left
    if left.offering != right.offering:
        raise CatalogCompositionError(
            f"offering id {offering_id!r} maps to multiple OfferingKey values"
        )

    signals = _effective_signals(left)
    for signal_id, observation in _effective_signals(right).items():
        existing = signals.get(signal_id)
        if existing is not None and existing != observation:
            raise CatalogCompositionError(
                f"offering {offering_id!r} has conflicting signal {signal_id!r}"
            )
        signals[signal_id] = observation

    try:
        return OfferingObservation(
            offering=left.offering,
            signals={key: signals[key] for key in sorted(signals)},
            metadata=_merge_metadata(
                left.metadata,
                right.metadata,
                offering_id=offering_id,
            ),
            # Every fallback source was materialized above. A single default
            # cannot truthfully describe independently sourced signal groups.
            default_source=None,
        )
    except ValidationError as exc:
        raise CatalogCompositionError(
            f"offering {offering_id!r} has inconsistent source provenance"
        ) from exc


def compose_catalogs(catalogs: Iterable[ObservationCatalog]) -> ObservationCatalog:
    """Compose candidate rows for one exactly matching workload.

    Duplicate offering rows are joined only when their full ``OfferingKey``
    values match. Signal and metadata conflicts fail closed; fallback sources
    are copied onto their observations before a row can carry more than one
    source group.
    """

    values = tuple(catalogs)
    if len(values) < 2:
        raise CatalogCompositionError("catalog composition requires at least two catalogs")
    if len(values) > MAX_COMPOSED_CATALOGS:
        raise CatalogCompositionError(
            f"catalog composition accepts at most {MAX_COMPOSED_CATALOGS} catalogs"
        )

    workload = values[0].workload
    for index, catalog in enumerate(values[1:], start=2):
        if catalog.workload != workload:
            raise CatalogCompositionError(
                f"catalog {index} workload {_workload_label(catalog)!r} does not exactly "
                f"match {_workload_label(values[0])!r}"
            )

    offerings: dict[str, OfferingObservation] = {}
    for catalog in values:
        for item in catalog.offerings:
            offering_id = item.offering.offering_id
            existing = offerings.get(offering_id)
            offerings[offering_id] = (
                item if existing is None else _merge_offering_rows(existing, item)
            )
            if len(offerings) > MAX_SELECTION_CANDIDATES:
                raise CatalogCompositionError(
                    "composed catalog exceeds the supported offering limit of "
                    f"{MAX_SELECTION_CANDIDATES}"
                )

    try:
        return ObservationCatalog(
            schema_version="model-skyline/v1alpha1",
            workload=workload,
            offerings=[offerings[key] for key in sorted(offerings)],
        )
    except ValidationError as exc:
        raise CatalogCompositionError(
            "composed catalog has inconsistent source provenance"
        ) from exc


def catalog_enrichment_policy_hash(policy: CatalogEnrichmentPolicy) -> str:
    """Return the stable content identity of an enrichment policy."""

    return content_hash(policy)


def catalog_enrichment_workload(policy: CatalogEnrichmentPolicy) -> WorkloadReference:
    """Bind an enriched workload identity to the complete reviewed policy."""

    return WorkloadReference(
        id=policy.output_workload_id,
        version=f"catalog-enrichment-sha256:{catalog_enrichment_policy_hash(policy)}",
        unit=policy.output_workload_unit,
    )


def _catalogs_by_hash(
    catalogs: Iterable[ObservationCatalog],
) -> dict[str, ObservationCatalog]:
    values: dict[str, ObservationCatalog] = {}
    for catalog in catalogs:
        digest = catalog_hash(catalog)
        if digest in values:
            raise CatalogCompositionError("projection catalogs must have distinct hashes")
        values[digest] = catalog
        if len(values) > MAX_ENRICHMENT_PROJECTIONS:
            raise CatalogCompositionError(
                f"at most {MAX_ENRICHMENT_PROJECTIONS} projection catalogs are supported"
            )
    return values


def enrich_catalog_across_workloads(
    policy: CatalogEnrichmentPolicy,
    base_catalog: ObservationCatalog,
    projection_catalogs: Iterable[ObservationCatalog],
) -> ObservationCatalog:
    """Project allowlisted signals through explicit complete-offering mappings.

    The base catalog defines the candidate universe. Each source and target
    ``OfferingKey`` is supplied in the reviewed policy; no alias, model-name,
    or partial-identity matching occurs. Source catalog metadata is not copied.
    """

    policy = CatalogEnrichmentPolicy.model_validate(policy.model_dump(mode="json"))
    if catalog_hash(base_catalog) != policy.base_catalog_sha256:
        raise CatalogCompositionError("base catalog hash does not match the policy")
    if base_catalog.workload != policy.base_workload:
        raise CatalogCompositionError("base catalog workload does not match the policy")
    if not base_catalog.offerings:
        raise CatalogCompositionError("base catalog must contain at least one candidate")

    supplied = _catalogs_by_hash(projection_catalogs)
    expected_hashes = {item.catalog_sha256 for item in policy.projections}
    if set(supplied) != expected_hashes:
        missing = len(expected_hashes - set(supplied))
        unexpected = len(set(supplied) - expected_hashes)
        raise CatalogCompositionError(
            "projection catalog set does not match the policy "
            f"(missing={missing}, unexpected={unexpected})"
        )

    base_by_id = {item.offering.offering_id: item for item in base_catalog.offerings}
    signals_by_id = {key: dict(item.signals) for key, item in base_by_id.items()}
    audits_by_id: dict[str, list[dict[str, Any]]] = {key: [] for key in base_by_id}
    for item in base_catalog.offerings:
        if "cross_workload_enrichment" in item.metadata:
            raise CatalogCompositionError(
                f"base offering {item.offering.offering_id!r} already uses reserved "
                "cross_workload_enrichment metadata"
            )

    for projection in policy.projections:
        source_catalog = supplied[projection.catalog_sha256]
        if source_catalog.workload != projection.workload:
            raise CatalogCompositionError(
                f"projection {projection.projection_id!r} workload does not match the policy"
            )
        source_by_id = {item.offering.offering_id: item for item in source_catalog.offerings}
        for mapping in projection.mappings:
            target_id = mapping.target_offering.offering_id
            target = base_by_id.get(target_id)
            if target is None:
                raise CatalogCompositionError(
                    f"projection {projection.projection_id!r} target offering is absent "
                    "from the base catalog"
                )
            if target.offering != mapping.target_offering:
                raise CatalogCompositionError(
                    f"projection {projection.projection_id!r} target OfferingKey mismatch"
                )

            source = source_by_id.get(mapping.source_offering.offering_id)
            if source is None:
                raise CatalogCompositionError(
                    f"projection {projection.projection_id!r} source offering is absent "
                    "from its catalog"
                )
            if source.offering != mapping.source_offering:
                raise CatalogCompositionError(
                    f"projection {projection.projection_id!r} source OfferingKey mismatch"
                )

            effective = _effective_signals(source)
            target_signals = signals_by_id[target_id]
            for signal_id in mapping.signal_ids:
                observation = effective.get(signal_id)
                if observation is None:
                    raise CatalogCompositionError(
                        f"projection {projection.projection_id!r} source is missing "
                        f"signal {signal_id!r}"
                    )
                if observation.source is None:
                    raise CatalogCompositionError(
                        f"projected signal {signal_id!r} has no source provenance"
                    )
                if signal_id in target_signals:
                    raise CatalogCompositionError(
                        f"cross-workload projection would overwrite target signal {signal_id!r}"
                    )
                target_signals[signal_id] = observation

            audits_by_id[target_id].append(
                {
                    "projection_id": projection.projection_id,
                    "source_catalog_sha256": projection.catalog_sha256,
                    "source_workload": projection.workload.model_dump(mode="json"),
                    "source_offering_id": mapping.source_offering.offering_id,
                    "signal_ids": list(mapping.signal_ids),
                    "review_note": mapping.review_note,
                }
            )

    policy_sha256 = catalog_enrichment_policy_hash(policy)
    offerings: list[OfferingObservation] = []
    for offering_id in sorted(base_by_id):
        base = base_by_id[offering_id]
        metadata: dict[str, Any] = deepcopy(base.metadata)
        metadata["cross_workload_enrichment"] = {
            "schema_version": CATALOG_ENRICHMENT_AUDIT_SCHEMA_VERSION,
            "policy_id": policy.policy_id,
            "policy_sha256": policy_sha256,
            "base_catalog_sha256": policy.base_catalog_sha256,
            "base_workload": policy.base_workload.model_dump(mode="json"),
            "applied_mappings": audits_by_id[offering_id],
        }
        try:
            offerings.append(
                OfferingObservation(
                    offering=base.offering,
                    signals={
                        key: signals_by_id[offering_id][key]
                        for key in sorted(signals_by_id[offering_id])
                    },
                    metadata=metadata,
                    default_source=base.default_source,
                )
            )
        except ValidationError as exc:
            raise CatalogCompositionError(
                f"enriched offering {offering_id!r} has inconsistent source provenance"
            ) from exc

    try:
        return ObservationCatalog(
            schema_version="model-skyline/v1alpha1",
            workload=catalog_enrichment_workload(policy),
            offerings=offerings,
        )
    except ValidationError as exc:
        raise CatalogCompositionError(
            "enriched catalog has inconsistent source provenance"
        ) from exc
