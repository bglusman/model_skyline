from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from model_skyline.models import Observation, OfferingObservation, WorkloadProfile


class OracleError(ValueError):
    """A versioned oracle could not evaluate a candidate."""


@dataclass(frozen=True, slots=True)
class OracleContext:
    name: str
    version: str
    offering: OfferingObservation
    workload_id: str
    workload: WorkloadProfile
    options: Mapping[str, Any]


class Oracle(Protocol):
    def evaluate(self, context: OracleContext) -> Observation: ...


class OracleRegistry:
    """Explicit registry for oracle clients.

    Applications register HTTP or subprocess clients at startup. Configuration
    can name a registered, versioned oracle but cannot import arbitrary Python.
    """

    def __init__(self) -> None:
        self._oracles: dict[tuple[str, str], Oracle] = {}

    def register(self, name: str, version: str, oracle: Oracle) -> None:
        key = (name, version)
        if not name or not version:
            raise ValueError("oracle name and version must be non-empty")
        if key in self._oracles:
            raise ValueError(f"oracle {name!r} version {version!r} is already registered")
        self._oracles[key] = oracle

    def evaluate(
        self,
        *,
        name: str,
        version: str,
        offering: OfferingObservation,
        workload_id: str,
        workload: WorkloadProfile,
        options: Mapping[str, Any],
    ) -> Observation:
        key = (name, version)
        try:
            oracle = self._oracles[key]
        except KeyError as exc:
            raise OracleError(f"oracle {name!r} version {version!r} is not registered") from exc
        context = OracleContext(
            name=name,
            version=version,
            offering=offering,
            workload_id=workload_id,
            workload=workload,
            options=options,
        )
        try:
            return oracle.evaluate(context)
        except OracleError:
            raise
        except Exception as exc:
            raise OracleError(f"oracle {name!r} version {version!r} failed: {exc}") from exc
