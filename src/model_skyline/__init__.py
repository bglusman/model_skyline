"""ModelSkyline: auditable, workload-specific model frontiers."""

from model_skyline.engine import FrontierEngine
from model_skyline.models import (
    FrontierDefinition,
    FrontierSnapshot,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    ProjectConfig,
    PublicationManifest,
    SelectionSnapshot,
    WorkloadProfile,
)
from model_skyline.oracles import OracleContext, OracleRegistry
from model_skyline.publisher import PublicationResult, publish_project
from model_skyline.resolver import DynamicResolver
from model_skyline.selection import select_models
from model_skyline.selection_overlap import (
    CrossFrontierSelectionPolicy,
    FrontierPriorityGroup,
    FrontierProximitySnapshot,
    MultiFrontierSelectionSnapshot,
    SecondaryFrontierInput,
    SecondaryFrontierReference,
    build_frontier_proximity_snapshot,
    multi_frontier_policy_hash,
    select_models_across_frontiers,
    verify_multi_frontier_selection_snapshot,
)
from model_skyline.version import VERSION as __version__

__all__ = [
    "__version__",
    "CrossFrontierSelectionPolicy",
    "DynamicResolver",
    "FrontierDefinition",
    "FrontierEngine",
    "FrontierPriorityGroup",
    "FrontierProximitySnapshot",
    "FrontierSnapshot",
    "MultiFrontierSelectionSnapshot",
    "Observation",
    "ObservationCatalog",
    "OfferingKey",
    "OfferingObservation",
    "OracleContext",
    "OracleRegistry",
    "ProjectConfig",
    "PublicationManifest",
    "PublicationResult",
    "SelectionSnapshot",
    "SecondaryFrontierInput",
    "SecondaryFrontierReference",
    "WorkloadProfile",
    "build_frontier_proximity_snapshot",
    "multi_frontier_policy_hash",
    "publish_project",
    "select_models",
    "select_models_across_frontiers",
    "verify_multi_frontier_selection_snapshot",
]
