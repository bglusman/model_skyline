# ADR 0005: Stable quality portfolios and enriched workload catalogs

- Status: accepted
- Date: 2026-08-31
- Decision owners: ModelSkyline maintainers

## Context

Most operators want a quality signal built from two to four recognizable
benchmarks, but benchmark captures change much more often than the operator's
intent. A long-lived policy such as “require coding and tool-use evidence no
older than one day” should not need editing whenever a leaderboard is fetched,
a result changes, or a cost frontier is recalculated.

Benchmark labels also do not identify production routes. A row named after a
model family may represent a different provider, endpoint, reasoning setting,
quantization, agent harness, or composite system. Combining rows before exact
reconciliation can therefore give a score to the wrong routable offering.

Finally, a quality-only catalog is not directly useful for the main workflow.
Cost-versus-quality evaluation would require a second, error-prone merge with
the workload catalog that already contains cost and performance observations.

## Decision

### Stable policy, volatile lock

`PortfolioPolicy` contains only operator intent:

- two to four logical component and frontier IDs;
- each benchmark's versioned workload, selected quality axis, and formula-safe
  output signal;
- per-component freshness and correlation group;
- required components and minimum measured coverage;
- an output workload; and
- an explicit statement that statistical independence is not assumed.

It contains no frontier snapshot, config, catalog, raw-capture, retrieval, or
rights hash. Those volatile values belong in `PortfolioDerivationSnapshot`.
The compact snapshot binds the policy hash, exact frontier/config/catalog/axis
inventory IDs, full selected-source descriptors, base and enriched catalog
hashes, validity window, and a per-candidate map of failed components. It does
not copy the selected `AxisEstimate` graph already committed by the exact
frontier snapshot.

The lock is audit and replay metadata, not a routing authorization. Consumers
must replay it against the trusted policy, base catalog, exact component
frontiers. A self-consistent hash alone does not authenticate its distribution
channel.

### Enrich the real workload catalog

Portfolio construction accepts an exact `ObservationCatalog` whose workload
matches the policy's output workload. Its offerings define the candidate
universe. Construction preserves every base offering, signal, default source,
and metadata field, then adds configured quality signals to candidates that
satisfy coverage. An ineligible candidate remains in the catalog with its base
cost/performance evidence but receives no portfolio quality signal, so the
ordinary frontier engine rejects it when that signal is required.

Each component declares a unique identifier matching
`[A-Za-z_][A-Za-z0-9_]*`, such as `swe_bench_score`. This makes the enriched
catalog a direct `FrontierEngine` input and lets an ordinary formula reference
`signals.swe_bench_score`. Cost-versus-quality and latency-versus-quality
frontiers need no model-name join or second catalog merge.

Because quality observations may have redistribution restrictions, enrichment
sets top-level `publication_safe` to false while retaining the base value in
the nested `quality_portfolio` metadata. Publication requires a separate
rights-reviewed projection.

### Exact reconciliation and visible failures

Matching uses the canonical bytes of the complete `OfferingKey`. A frontier
row with the same `offering_id` but a different provider, endpoint, billing
mode, region, tier, quantization, reasoning effort, harness, or capability set
is an error, not missing evidence. Fuzzy labels, aliases, family matching, and
provider fallback are outside this layer and cannot affect portfolio output.

Leaderboard adapters and the reviewed reconciliation contracts from
[ADR 0004](0004-quality-evidence-and-benchmark-bundles.md) must establish the
exact route before a benchmark becomes a component frontier. This permits
sources such as SWE-bench Verified, ARC-AGI-2, Terminal-Bench, tau2-bench, or a
local harness without treating any benchmark's display name as route identity.

Failure maps retain why a component was unusable, including missing evidence,
incomplete provenance or rights, missing/future timestamps, and staleness.
There is no imputation. Invalid or ambiguous upstream rows must already have
been excluded by their import and reconciliation contracts.

### Freshness, provenance, and correlation

Freshness is candidate-specific and begins at
`AxisEstimate.oldest_observed_at`, not at the time a frontier was regenerated.
Refreshing a price or companion latency observation therefore cannot make an
old benchmark score fresh. The enriched catalog is valid only until the
earliest included measured component expires.

Usable quality evidence requires embedded sources with version, methodology,
raw digest, retrieval time, and a license or terms locator. Incomplete source
metadata makes that component unusable, while the lock still retains its
source descriptor for audit. Correlation groups and rationale are mandatory,
and the policy always states that statistical independence is not assumed.

### Keep evidence separate; compose with core formulas

The portfolio always emits heterogeneous benchmark values separately because
disagreement, direction, units, and missingness are part of the evidence. It
does not implement a second normalization or weighting language.

Operators that want one scalar quality axis define a normal `FormulaMetric` in
`ProjectConfig`, for example a weighted expression over
`signals.swe_bench_score` and `signals.arc_agi_2_score`. The shared formula
engine supplies exact Decimal arithmetic, bounded functions such as `clamp`
and `if`, explicit dependency extraction, and failure on a referenced missing
signal. Fixed reference ranges and direction inversions therefore remain
visible in ordinary frontier configuration. Candidate-relative normalization
is discouraged because adding a model would change every existing score.

## Change-scoped invalidation

Each component lock includes a selected-quality projection digest over the
stable component intent, exact candidate identities, selected estimates,
and failures. It excludes the frontier's companion axis. The portfolio
projection combines only those component digests.

Consequently:

- a price, latency, config, or companion-axis-only change rebinds the exact
  derivation snapshot but does not change the quality projection;
- a base price change changes the enriched catalog and dependent cost frontier,
  but does not invalidate the independent quality projection;
- a benchmark value, timestamp, source capture, rights descriptor, route
  identity, output-signal declaration, or failure change updates the relevant
  quality projection; and
- a policy semantic change updates the policy hash.

This gives downstream systems a general dependency boundary without pretending
that an exact audit lock and a metric-specific quality value have the same
invalidation scope.

## Consequences and limits

The portfolio layer does not scrape leaderboards, choose benchmark revisions,
or approve row-to-route mappings. Adapters remain responsible for acquisition
metadata and reviewed reconciliation. Benchmark scores are evidence for the
declared workload and harness, not universal properties of a model family.

The v1alpha1 lock deliberately favors replay over self-contained duplication:
verification requires the exact source frontiers and base catalog. Operators
that need independently distributable public artifacts must create a reviewed
projection with suitable source licenses and transport authentication.
