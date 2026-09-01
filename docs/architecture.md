# Architecture and semantics

ModelSkyline is a workload-specific model-selection control plane. It is not an
inference proxy. It turns observations and operator policy into immutable,
language-neutral artifacts that an agent, gateway, or SDK can consume.

## Supported product path

The supported kernel is intentionally small:

```text
ProjectConfig + ObservationCatalog
              -> FrontierSnapshot
              -> SelectionSnapshot
              -> JSON, table, CSV, or RSS
```

The frontier contains every evaluated offering, exclusions and dominance
reasons, provenance, and exact policy/catalog bindings. The selection names one
default plus ordered fallbacks and binds them to that exact frontier. A
consumer pins one selection for a complete work unit so a multi-turn trajectory
cannot change models midway through execution.

Formula evaluation, exact offering identity, two-axis Pareto calculation, and
ordered selection are the kernel. Publication/RSS, the trusted-channel
resolver, trace aggregation, benchmark reconciliation, source adapters, and
quality portfolios are first-party integrations around it. They remain
explicit modules rather than expanding the package-root API.

An external consumer used the v0.6 `evaluate` and `select` commands and the
`ObservationCatalog -> FrontierSnapshot -> SelectionSnapshot` JSON boundary
with real workload data. That is the current end-to-end evidence. The v0.9
simplification preserves those command names and core artifact shapes.

Version 0.9 removes two experimental branches that had no demonstrated runtime
consumer:

- the duplicate quality-bundle, scalar-oracle, quality-gated-selection, and
  multi-frontier overlap/proximity runtime; and
- the signed pointer/store/gateway protocol and conformance implementation.

Their design history remains in [ADR 0002](adr/0002-multi-frontier-overlap-and-proximity.md)
and [ADR 0003](adr/0003-signed-gateway-selection-protocol.md). They are not
shipped contracts in v0.9. Quality composition now enriches an ordinary catalog
through one portfolio abstraction, and runtime integration consumes an ordinary
`SelectionSnapshot` over a trusted channel.

## Why Python, and where interoperability lives

The control plane is Python because the differentiating work is evidence and
data interoperability: benchmark harnesses, Hugging Face datasets, agent
telemetry, DuckDB/Parquet, and provider catalogs are Python-heavy. The Pareto
algorithm itself is small and did not dictate the language.

Python is not the runtime boundary. Pydantic exports JSON Schema 2020-12,
snapshots use canonical JSON, and HTTP consumers can use ETags. A native agent
or gateway should validate and consume the JSON in its own runtime. Revisit the
language choice only when a measured requirement such as offline embedding,
WASM, or identical cross-runtime policy execution outweighs the data-tooling
advantage.

## Non-negotiable invariants

- A frontier has exactly two distinct axes, each declaring `minimize` or
  `maximize`.
- A candidate is an offering, not merely a model name. Provider, endpoint,
  region, service tier, billing mode, quantization, reasoning configuration,
  and agent harness may alter cost or behavior.
- A catalog is bound to one exact workload id, version, and work-unit
  definition. Cross-workload observations are not silently reused.
- Evidence has a Decimal value, unit, source, and relevant time/sample
  metadata. Missing, stale, non-finite, unit-mismatched, or insufficient
  evidence is excluded with a reason; it is never filled with zero.
- Configured formulas cannot execute Python. A host-registered oracle is code,
  but public configuration can only name an implementation already registered
  by the host.
- Benchmark display names never become routing identities through fuzzy,
  family, prefix, or case-insensitive matching.
- Immutable hashes identify bytes and semantic inputs; they do not authenticate
  the policy author or publication origin.
- A new expired selection is rejected. A cached selection may be used after a
  refresh error only within the resolver's explicit bounded stale window.

## Domain model

`OfferingKey`
: Complete routable identity. `model_id` identifies the underlying model
  version; `offering_id` and the remaining fields identify the deployment and
  commercial choices that can change measurements.

`WorkloadProfile`
: Versioned definition of a work unit, evaluator, cohort, budget, assumptions,
  and constraints. Examples include a resolved repository issue, completed
  research report, or stateful support trajectory.

`Observation`
: Decimal estimate with a unit, optional bounds, sample count, observation
  time, and `SourceReference`.

`ObservationCatalog`
: Offering observations for exactly one `WorkloadReference`. An alpha catalog
  may copy provider prices into the workload catalog; effective-dated price
  cards remain a planned normalization.

`MetricDefinition`
: A direct signal, restricted Decimal formula, or host-registered oracle.
  Published benchmark results normally enter as workload-bound signals. A
  scalar benchmark composite is normally an explicit formula over portfolio
  signals rather than a separate selection system.

`FrontierDefinition`
: Workload, two metric/goal pairs, eligibility, freshness, minimum samples,
  uncertainty mode, epsilon tolerances, output order, and metadata projection.

`FrontierSnapshot`
: All evaluated offerings, Pareto membership, exclusions, source watermarks,
  hashes, and the per-axis evidence inventory. The inventory retains a valid
  quality estimate even when missing or stale price excludes the same offering
  from a cost/quality frontier.

`SelectionDefinition`
: Ordering axis, desired count, provider-diversity cap, expiration, and behavior
  when too few candidates qualify.

`SelectionSnapshot`
: The chosen default and fallbacks plus policy, frontier, generation, and
  validity bindings. A frontier is a set; the selection policy provides the
  additional ordering decision.

`QualityEvidenceSet` and `QualityReconciliation`
: Route-free benchmark evidence and an operator-reviewed exact mapping to a
  complete `OfferingKey`. Mutable aliases, composites, identity drift,
  ambiguous targets, and invalid rows remain quarantined.

`PortfolioPolicy` and `PortfolioDerivationSnapshot`
: A two-to-four-benchmark coverage policy and replay record. `build_portfolio`
  reads one declared quality axis from each exact component frontier, matches
  the complete `OfferingKey`, enforces freshness and required coverage, and
  returns a normal enriched `ObservationCatalog`. Each component becomes a
  separate named signal. The core engine and `FormulaMetric` then define any
  scalar quality axis and cost/quality frontier.

## Metric evaluation and invalidation

Signal metrics copy an observation after unit, freshness, sample-count, and
interval checks. Formula metrics use a small, non-Turing-complete grammar over
`signals`, `workload`, and `metadata` with Decimal arithmetic. General imports,
attribute traversal, comprehensions, and arbitrary calls do not exist.
Expression length, tree depth, literal size, expensive operations, finite
values, and output size are bounded.

Every evaluated axis records its exact dependency paths and sources. This is
the invalidation mechanism: a change matters to a frontier only when it changes
the catalog or a dependency used by that frontier. Independent price meters,
cache prices, workload cache-hit assumptions, and benchmark scores may all
change separately. Re-evaluation produces new immutable artifacts, while an
unrelated field need not manufacture a semantic change in an axis that did not
depend on it.

The catalog hash still binds the complete input for audit. The per-axis evidence
inventory and dependency list explain which part affected an estimate. Source
ids must map to one complete descriptor; reusing an id for a different URL,
version, methodology, digest, license, or retrieval record is rejected.

Formula output units are declared rather than dimensionally inferred. Robust
interval propagation through arbitrary formulas is not implemented; robust
mode rejects a formula axis. Operators needing propagated uncertainty should
materialize a separately sourced observation whose methodology defines it.

### Work-unit cost

`total_workunit_cost` should account for the meters actually observed or
explicitly modeled, for example:

```text
uncached_input_tokens * uncached_input_price
+ cache_read_tokens * cache_read_price
+ cache_write_tokens_by_retention * matching_write_prices
+ output_tokens * output_price
+ request_or_tool_charges
+ cache_storage_or_retention_charges
```

Use Decimal values and declare the accounting basis. Provider-reported billed
cost, reconstructed components, estimated cost, and provider-marginal cost are
not interchangeable and must not be added twice. Unknown meters stay unknown.
Cost per successful work unit is distinct from cost per token and normally more
useful for agent workloads.

Historical actual cost uses rates effective at event time. Repricing the same
trace with today's rates is a counterfactual and must be labeled separately.
The current example demonstrates configurable formulas; it is not a complete
effective-dated billing engine.

## Quality portfolio

A benchmark score belongs to a cohort, harness, scorer, configuration, budget,
agent system, and time. `quality_portfolio.py` keeps those benchmark components
separate while making them usable by the ordinary engine:

```text
benchmark captures
  -> route-free evidence
  -> reviewed exact reconciliation
  -> component frontier snapshots
  -> build_portfolio(policy, component_frontiers, base_catalog)
  -> enriched ObservationCatalog
  -> core FormulaMetric + FrontierEngine + select_models
```

The policy declares two to four components, distinct output signal names,
required component IDs, a minimum measured count, maximum ages, correlation
groups, and rationale. It always declares that statistical independence is not
assumed. Building fails when inputs do not exactly match the declared
frontiers/workloads/axes or when reused `offering_id` values hide a different
complete key.

The enriched catalog keeps base cost/performance observations, adds only
eligible benchmark signals, records failures for every candidate, and sets
`publication_safe: false`. A separate rights-reviewed projection is required
before public redistribution. `verify_portfolio` deterministically rebuilds the
derivation against the source frontiers and rejects expiry or mismatch.

The portfolio is a coverage and enrichment layer, not a universal oracle. It
does not normalize scores, assign weights, combine intervals, or infer
independence. An operator who wants one quality number defines normalization
and weights in an ordinary `FormulaMetric`; keeping component signals separate
is preferable when a scalar would conceal important tradeoffs. See
[Quality portfolios](quality-portfolios.md).

## Pareto and selection semantics

For point estimates, A dominates B when A is no worse on both axes and strictly
better on at least one. Identical points remain co-members. An axis tolerance
is:

```text
epsilon = epsilon_absolute
          + epsilon_relative * max(abs(value_a), abs(value_b))
```

Differences within epsilon are treated as equivalent. Arithmetic uses a fixed
34-significant-digit, round-half-even Decimal context for symmetric comparison;
snapshots retain original observation precision.

In robust mode, A dominates B only when A's pessimistic interval bound is no
worse than B's optimistic bound on both axes and meaningfully better on one.
Missing bounds exclude the offering. The current all-pairs implementation is
deterministic and suitable for catalogs of hundreds or thousands of offerings.

Selection is lexicographic on one declared frontier axis, then the other axis,
with a stable offering-identity tie-breaker and an optional maximum per
provider. Additional policies such as knee points or hysteresis should be added
only when a real consumer demonstrates the need.

## Publication and RSS

`publish-project` evaluates selected frontiers and selections at one UTC time
and commits one coherent static publication. It writes immutable frontier,
selection, manifest, history, and RSS artifacts plus mutable discovery aliases.
A coherent reader starts at root `latest.json`, verifies the immutable
manifest and digests, and never assembles a project state from independently
read aliases.

Writes are ordered so immutable data becomes durable first and root
`latest.json` commits last. Existing roots are additive/full-refresh: omission
of a previously published frontier or selection is rejected rather than
treated as retirement. Immutable collisions, missing files, digest mismatch,
timestamp rollback, unmanaged paths, and symlinks fail closed.

RSS records semantic frontier changes, not every acquisition refresh. Tables
and CSV are human views; JSON and committed schemas are the contract. Public
mode requires an HTTPS base URL, explicit source rights authorization, and
rejects any catalog or retained evaluated item marked
`publication_safe: false`. These checks do not replace a privacy, PII, secret,
or legal review.

Publisher directories must be exclusively writable by the publisher identity.
The local filesystem checks reduce mistakes but cannot defeat a hostile process
that can concurrently modify the same directories.

## Canonical artifacts and resolver trust

Decimals serialize as finite fixed-point JSON strings. Hash inputs use RFC 8785
canonical JSON and lowercase SHA-256 (`sha256-rfc8785-v1`). Snapshot identity
excludes the identity field itself. Loaders parse numeric JSON/YAML values as
Decimal rather than passing money through binary floating point.

The committed Draft 2020-12 schemas have stable URN `$id` values and ship in the
wheel. `export-schemas` copies release contracts byte-for-byte; maintainers use
`regenerate-schemas` after model changes and review the diff.

`DynamicResolver` consumes only an ordinary `SelectionSnapshot`. It verifies
its content/semantic hash, expiry, future skew, and process-local monotonic
generation, and returns defensive copies. The built-in loader accepts HTTPS by
default with optional exact-host allowlisting and a 10 MiB bound; local files
and plain HTTP require explicit opt-in. Local paths must be regular files and
are still a trusted deployment boundary.

Content hashes do not authenticate a publisher. Version 0.9 therefore supports
trusted local-file or trusted HTTPS delivery only. It does not promise signed
untrusted distribution, durable restart-safe anti-rollback, remote credential
construction, provider-error fallback execution, or a hosted gateway. ADR 0003
preserves the removed signed-protocol design for a future, consumer-driven
implementation.

## Near-term roadmap

1. Keep the payload-free real workload example executable from validation
   through selection and preserve the v0.6 CLI/JSON consumer boundary.
2. Exercise `PortfolioPolicy -> build_portfolio -> FormulaMetric -> selection`
   with two to four real benchmark feeds and reviewed exact mappings.
3. Validate an ordinary `SelectionSnapshot` in Wardwright through trusted local
   file delivery, exact local offering bindings, expiry, and per-work-unit
   pinning.
4. Automate one fresh price/quality publication and measure operational value
   before expanding protocol or source breadth.

Effective-dated price cards, more benchmark collectors, dimensional analysis,
signed distribution, durable resolver state, selection hysteresis, and gateway
profiles are possible follow-ons. Each should be pulled by a demonstrated
producer-to-consumer path.
