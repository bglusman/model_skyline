# Migrating to 0.9

Version 0.9 narrows ModelSkyline to its demonstrated catalog → frontier →
ordinary-selection path. It also removes two experimental subsystems: the
signed gateway/store/protocol and the duplicate quality
bundle/oracle/gated-selection/overlap runtime.

The `evaluate` and `select` CLI names and the ordinary
`ObservationCatalog`, `FrontierSnapshot`, and `SelectionSnapshot` JSON shapes
remain the primary compatibility boundary. There are no deprecated Python
aliases for removed names.

## Root imports that remain supported

These are the complete package-root imports in 0.9:

```python
from model_skyline import (
    FrontierEngine,
    FrontierSnapshot,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    ProjectConfig,
    SelectionSnapshot,
    __version__,
    select_models,
)
```

Advanced features that remain supported are imported from their defining
modules.

```python
from model_skyline.models import (
    AxisEvidenceCandidate,
    AxisEvidenceInventory,
    FrontierDefinition,
    PublicationManifest,
    WorkloadProfile,
    axis_evidence_inventory_hash,
    build_axis_evidence_inventory,
)
from model_skyline.oracles import OracleContext, OracleRegistry
from model_skyline.publisher import PublicationResult, publish_project
from model_skyline.quality_catalog import (
    project_quality_import_report,
    quality_source_reference,
    quality_workload_reference,
    reconcile_quality_catalog,
)
from model_skyline.quality_evidence import (
    QualityEvidenceSet,
    QualityImportReport,
    QualityReconciliation,
    reconcile_quality_evidence,
)
from model_skyline.resolver import DynamicResolver
```

Those imports account for every 0.8 root export that merely moved to an
explicit retained module.

## Quality composition migration

The following 0.8 root exports and their modules are removed:

```text
QualityBundlePolicy
QualityBundleSnapshot
build_quality_bundle_snapshot
eligible_quality_bundle_candidates
verify_quality_bundle_snapshot

FixedMinMaxNormalization
QualityOracleComponent
QualityOracleComponentCapture
QualityOraclePolicy
QualityOracleSnapshot
QualityOracleSourceSemantic
build_fixed_min_max_normalization
build_quality_oracle_snapshot
enrich_catalog_with_quality_oracle
fixed_min_max_normalization_hash
quality_oracle_axis
quality_oracle_catalog
quality_oracle_selected_quality_component_projection_hashes
quality_oracle_selected_quality_projection_hash
quality_oracle_source_capture_identity
quality_oracle_source_raw_identity
quality_oracle_source_retrieval_identity
quality_oracle_source_rights_identity
quality_oracle_source_semantic_identity
quality_oracle_source_semantics
verify_quality_oracle_snapshot

QualityGatedSelectionSnapshot
build_quality_gated_selection_snapshot
quality_gated_selection_hash
verify_quality_gated_selection_snapshot

CrossFrontierSelectionPolicy
FrontierPriorityGroup
FrontierProximitySnapshot
MultiFrontierSelectionSnapshot
SecondaryFrontierInput
SecondaryFrontierReference
build_frontier_proximity_snapshot
multi_frontier_policy_hash
select_models_across_frontiers
verify_multi_frontier_selection_snapshot
```

Use the new enrichment path:

```python
from model_skyline.quality_portfolio import (
    PortfolioDerivationSnapshot,
    PortfolioPolicy,
    build_portfolio,
    verify_portfolio,
)

portfolio = build_portfolio(
    policy,
    component_frontiers,
    base_catalog,
    generated_at=as_of,
)
frontier = FrontierEngine().calculate(
    config,
    portfolio.catalog,
    "cost-quality",
    generated_at=as_of,
)
selection = select_models(config, frontier, "defaults")
```

This is a semantic migration, not a rename:

- `PortfolioPolicy` gates two-to-four component coverage and emits separate
  signals; it does not normalize or weight them.
- Put an operator-reviewed scalar composite in an ordinary `FormulaMetric`, or
  use one component signal directly.
- Use the ordinary `SelectionSnapshot` and `DynamicResolver`; there is no
  quality-specific selection wrapper or overlap re-ranking in 0.9.
- The enriched catalog is private (`publication_safe: false`) until a separate
  rights-reviewed projection is built.

Removed commands include `build-frontier-proximity`,
`select-quality-gated`, `verify-quality-gated-selection`,
`build-quality-bundle`, `build-quality-oracle`,
`verify-quality-oracle`, and `enrich-catalog-with-quality-oracle`. Update scripts
to the portfolio build/verification surface shown by `modelskyline --help`, then
run the ordinary `evaluate` and `select` commands.

The corresponding bundle, oracle, gated-selection, proximity, and
multi-frontier schemas are no longer exported. Regenerate clients from the
committed 0.9 schema directory rather than retaining those types.

## Gateway migration

The explicit modules `model_skyline.gateway`,
`model_skyline.gateway_resolver`, and `model_skyline.gateway_store`, the
`gateway` optional dependency, gateway schemas, signed-pointer conformance
corpus, and `verify-gateway-bundle` command are removed.

For a trusted local file or trusted HTTPS origin, consume the ordinary
selection with:

```python
from model_skyline.resolver import DynamicResolver
```

`DynamicResolver` validates ordinary selection content, semantics, time bounds,
and process-local generation monotonicity. It does not provide signatures,
publisher authentication, durable restart-safe rollback/equivocation state, or
local target mapping. An agent/gateway integration must bind each complete
`OfferingKey` to an operator-created local target and pin the resolved tuple for
the whole work unit.

If your deployment relied on the removed signed protocol, remain on 0.8 while
you design an authenticated distribution boundary appropriate to the actual
consumer. Superseded [ADR 0003](adr/0003-signed-gateway-selection-protocol.md)
preserves the old design as historical input, not as a 0.9 interoperability
claim.

## Historical implementation

The removed code remains recoverable from Git history before the 0.9
simplification (for example commit `5f6c6ab`). It is not supported by 0.9 and
should not be copied forward merely to preserve an unused contract. See
[Quality portfolios](quality-portfolios.md) and
[Runtime integrations](gateway-integrations.md) for the smaller replacement
boundaries.
