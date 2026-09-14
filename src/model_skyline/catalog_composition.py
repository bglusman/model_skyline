"""Fail-closed composition of observation catalogs for one exact workload."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from model_skyline.models import (
    MAX_SELECTION_CANDIDATES,
    Observation,
    ObservationCatalog,
    OfferingObservation,
)

MAX_COMPOSED_CATALOGS = 1_024


class CatalogCompositionError(ValueError):
    """Catalogs cannot be composed without weakening identity or provenance."""


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
