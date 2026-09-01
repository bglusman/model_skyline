"""ModelSkyline: auditable, workload-specific model frontiers."""

from model_skyline.engine import FrontierEngine
from model_skyline.models import (
    FrontierSnapshot,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    ProjectConfig,
    SelectionSnapshot,
)
from model_skyline.selection import select_models
from model_skyline.version import VERSION as __version__

__all__ = [
    "__version__",
    "FrontierEngine",
    "FrontierSnapshot",
    "Observation",
    "ObservationCatalog",
    "OfferingKey",
    "OfferingObservation",
    "ProjectConfig",
    "SelectionSnapshot",
    "select_models",
]
