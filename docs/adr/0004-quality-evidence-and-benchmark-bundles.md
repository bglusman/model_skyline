# ADR 0004: Quality evidence and benchmark portfolios

- Status: accepted for evidence/reconciliation and portfolio v1alpha1;
  supersedes this ADR's earlier bundle/oracle/gated-selection design
- Date: 2026-08-31
- Decision owners: ModelSkyline maintainers

## Context

An operator should be able to use relevant benchmarks as quality evidence, but
“quality” is not a universal scalar attached to a model name. A score belongs
to an exact cohort, harness, agent or prompting configuration, budget, scorer,
subject, and point in time. Public leaderboards often identify a mutable model
alias or a compound agent system rather than a currently routable provider
offering.

The first implementation accumulated several parallel artifacts: quality
bundles, a scalar quality oracle, quality-gated selections, and cross-frontier
overlap/proximity. They preserved useful provenance but duplicated coverage,
ranking, replay, CLI, schema, and resolver logic. A real v0.6 consumer instead
used the simple `ObservationCatalog -> FrontierSnapshot -> SelectionSnapshot`
boundary and did not need those extensions.

## Decision

### Keep three metric roles distinct

- A published leaderboard row or local benchmark run becomes a workload-bound
  observation and is consumed by a `SignalMetric`.
- An `OracleMetric` invokes trusted code registered by the embedding host. A
  public policy cannot dynamically import or execute an oracle, and the stock
  CLI/publisher do not provide a remote oracle transport.
- A weighted or compound quality score is an ordinary `FormulaMetric` for a new
  versioned composite workload. Its units, normalization anchors, missing-data
  behavior, weights, and rationale are explicit operator policy.

Axis direction remains visible policy (`minimize` or `maximize`), not an
adapter-side assumption.

### Retain exact, dependency-scoped evidence identity

Collectors keep exact captured bytes in private audit storage when rights and
local policy allow, while normalized artifacts retain bounded metadata and
digests. A single undifferentiated leaderboard hash is insufficient: an
unrelated row, new result, source methodology change, or rights review should
not invalidate the same things.

Evidence therefore separates at least these domains:

| Domain | Typical fields | What a change invalidates |
| --- | --- | --- |
| Raw audit | Captured-byte digest, locator, retrieval time, capture method, parser version | The acquisition/audit record |
| Source semantics | Benchmark release, cohort/task digest, harness, scorer, prompts/templates, budget, retry policy, work-unit definition | Every result under that methodology |
| Subject | Exact row locator and model/system/agent/route claims | That row's reviewed mapping |
| Result | Score, counts, bounds, observation time, typed result measures | The quality observation and dependent artifacts |
| Rights | License/terms assertion, review evidence/time, redistribution permission | Publication eligibility, not the numeric result |

Pricing changes do not invalidate independent quality evidence. A benchmark
result change does not require re-reviewing a stable subject mapping. Source or
subject drift and ambiguous/unmapped rows quarantine only the affected evidence.

### Require reviewed exact result-to-offering mapping

Leaderboard names are untrusted labels, never routing instructions. A mapping
pins the upstream row locator, adapter/projection versions, expected source and
subject identities, review evidence/time, and complete target `OfferingKey`.
Matching is exact. Case folding, family/prefix matching, provider fallback, and
source-side “latest” aliases do not establish identity.

Reconciliation distinguishes:

- `exact_subject_route`, where source evidence establishes the complete
  measured route; and
- `reviewed_quality_projection`, where an operator explicitly decides selected
  quality measures apply to a complete production offering even though the
  source did not disclose that route.

A quality-only projection carries only typed quality measurements and counts.
Source-reported price, latency, tokens, cache data, and result metadata remain
route-free. A multi-model, router, or undisclosed system cannot be relabeled as
a bare component-model score.

Exact key equality is syntactic, not proof that a provider alias is immutable.
The runtime operator still binds the complete key to a reviewed local target
revision and enforces any separate validity period.

### Compose with one portfolio enrichment layer

Version 0.9 replaces quality bundle/oracle/gated-selection and multi-frontier
runtime types with `PortfolioPolicy`, `build_portfolio`, and
`PortfolioDerivationSnapshot` in `model_skyline.quality_portfolio`.

A portfolio contains two to four components. Each declares an exact frontier,
workload, selected quality axis, output signal, maximum age, and correlation
group. The policy declares required component IDs, minimum measured coverage,
the output workload, and correlation rationale, and explicitly states that
statistical independence is not assumed.

Building a portfolio:

1. takes the candidate universe from one base catalog for the output workload;
2. verifies each exact component frontier and its per-axis evidence inventory;
3. matches complete offering identities and reads only the declared quality
   axis, even when the companion axis rejected the route;
4. validates source completeness, future skew, and evidence freshness;
5. hard-excludes candidates missing required/minimum coverage;
6. emits each component as a separate named signal in an ordinary enriched
   `ObservationCatalog`; and
7. emits a compact derivation record with policy, input, projection, catalog,
   freshness, and candidate-failure bindings for deterministic replay.

The enriched catalog enters the existing engine. Operators can define a
cost/quality frontier using one component signal or an explicit `FormulaMetric`
over normalized component signals, then produce the ordinary
`SelectionSnapshot`. There is no portfolio-specific selection or resolver.

The portfolio does not assign weights, normalize values, aggregate bounds or
sample counts, prove statistical independence, or infer benchmark-to-route
transfer. Fixed external normalization anchors are preferable to
candidate-relative min/max. Missing components are never imputed, silently
clamped, or handled by renormalizing the remaining weights.

The previous overlap/proximity idea remains historical in superseded
[ADR 0002](0002-multi-frontier-overlap-and-proximity.md), not an alternative
v0.9 execution path.

### Start with a small, operator-selected benchmark set

A practical general-agent portfolio uses two to four genuinely distinct
signals, for example:

| Component | Intended evidence | Required scoping |
| --- | --- | --- |
| SWE-bench Verified, fixed harness | Repository-issue resolution | Exact experiments revision, task cohort digest, harness/version/configuration, agent system, reasoning effort, attempts, budget, and per-task result digest |
| Terminal-Bench through Harbor | Multi-step computer/tool work | Exact board/dataset UUID, schema, row UUID, release-date contract, complete agent/model metadata, and result digest |
| tau2-bench or BFCL | Conversational policy/tool use or function calling | Exact release/commit, domain/split digest, simulator/tool configuration, submission identity, and verification flags |
| ARC-AGI-2 public evaluation | Abstract reasoning | Exact dataset revision and cohort; historical harness, attempt policy, task bytes, and production route remain unattested without a reviewed sidecar |

Two components can serve a narrow coding/tooling workload; three are a sensible
general-agent starting point; a fourth should add a material dimension. More
benchmarks are not inherently better, and correlated tool benchmarks should not
be silently double- or triple-weighted.

Collectors use pinned repository, JSON, dataset, or local-harness interfaces;
the sources do not expose a common RSS contract. ModelSkyline RSS begins only
after reviewed mapping and frontier evaluation.

### Keep freshness, uncertainty, and publication explicit

Keep three clocks distinct: evidence observation time, collector-health time,
and route-mapping validity. Retrieval time is acquisition provenance, not a
substitute for all three. An immutable historical benchmark can remain useful
while a provider route or price expires separately.

`sample_count` must identify its actual denominator: tasks, scored tasks,
repetitions, or judge votes. Bounds must state whether they are confidence
intervals, repeated-run ranges, rounding bounds, or something else. The core
formula engine does not propagate heterogeneous component intervals. A policy
requiring joint uncertainty should materialize a separately sourced composite
observation with an explicit methodology.

Portfolio-enriched catalogs are marked `publication_safe: false`. A source
license or terms locator is audit metadata, not automatic redistribution
authority. Public output requires a separate rights/privacy-reviewed
projection; publisher allowlists cannot waive the categorical block.

## Consequences

- Operators can use diverse quality evidence without blessing one global
  benchmark or judge.
- Price, cache rates, latency, quality results, mappings, source methodology,
  and rights state can invalidate their own dependents independently.
- Exact identities and mapping review add work but prevent model-family labels
  from becoming false production-route claims.
- One catalog/frontier/selection path replaces several duplicative policy and
  resolver surfaces, making ordinary consumers usable without understanding a
  quality-specific artifact graph.
- Scalar quality remains possible but visibly operator-authored and replayable
  through the core formula engine.
- The portfolio's two-to-four component limit is deliberate. Richer utility,
  subgroup constraints, learned routing, or nonlinear aggregation require a
  new demonstrated use case rather than gradual schema accretion.

See [Benchmark evidence and quality portfolios](../quality-portfolios.md) for
the operational guide and [ADR 0005](0005-quality-portfolios.md) for the exact
stable-policy/volatile-derivation design.
