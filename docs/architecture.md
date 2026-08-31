# Architecture and semantics

This document describes the `v1alpha1` contracts and the decisions that must
remain stable while the implementation evolves.

## Product boundary

ModelSkyline is a model-selection **control plane**, not an inference gateway.
It turns versioned observations and operator policy into two immutable
artifacts:

1. A frontier snapshot containing every evaluated offering, the non-dominated
   members, exclusions, dominance explanations, provenance watermarks, and the
   exact effective-policy and input-catalog hashes.
2. A selection snapshot containing one default and ordered fallbacks, tied to
   a particular frontier snapshot and expiration time.

LiteLLM, TensorZero, vLLM Semantic Router, OpenRouter, or provider SDKs can
execute the selected route. This project should integrate with those systems,
not duplicate their gateway and retry machinery.

## Language decision

The control/data plane is Python because the work that differentiates this
project is evaluation and data interoperability: Inspect AI, lm-eval,
SWE-bench-style harnesses, Hugging Face datasets, LiteLLM catalogs,
OpenInference instrumentation, and DuckDB/Parquet are Python-first. The
frontier algorithm itself is small and did not drive the choice.

Python is not the runtime interoperability boundary. Pydantic exports JSON
Schema 2020-12 contracts, snapshots are canonical JSON, and HTTP clients use
ETags. Thin runtime clients should be native to their agent ecosystem. A Rust
kernel becomes worthwhile if offline embedding, WASM, or sharing identical
policy execution between Python and Node becomes a proven requirement. A Go
service becomes worthwhile if a standalone operator daemon becomes the
primary distribution model.

The repository now uses that ecosystem for pinned, real Aider and MCPMark
benchmark adapters in addition to Pydantic, DuckDB, Lark, and protocol
boundaries. Bundled fixtures remain synthetic; upstream data is fetched and
verified rather than silently vendored. The next language checkpoint is a real
effective-price adapter and an empirical trace adapter. If those do not benefit
materially from Python, ADR 0001 should be reopened.

## Non-negotiable invariants

- A frontier has exactly two distinct axes; each explicitly says `minimize` or
  `maximize`.
- A candidate is an offering, not only a model: model version, provider
  endpoint, region, tier, quantization, reasoning configuration, and agent
  harness can change its position.
- A metric is scoped to a versioned workload and has a unit, source, timestamp,
  sample count, and optional uncertainty/reference bounds whose statistical
  interpretation must be declared by the workload.
- An observation catalog is bound to exactly one workload id, version, and
  work-unit definition; the engine rejects cross-workload reuse. The workload
  harness identifies the evaluator that produced the evidence. An offering's
  optional `agent_harness` instead identifies a production routing target, so
  the two are independently versioned and are never implicitly equated.
- Missing, stale, unit-mismatched, or unbounded values are excluded with a
  reason when required. They are never silently zero-filled or imputed.
- Formula configuration cannot execute Python. Oracle code is registered by
  the host and addressed by name plus version.
- Published snapshots are immutable and content-hashed. Agents pin one
  selection snapshot for an entire work unit so a trajectory cannot switch
  models halfway through.
- A stale selection is usable only as a bounded last-known-good value after a
  refresh error. A newly fetched expired artifact is rejected.
- Artifact hashes provide content identity, not publisher authentication. The
  convenience resolver therefore requires a trusted HTTPS origin. Untrusted
  distribution uses the signed gateway profile in
  [ADR 0003](adr/0003-signed-gateway-selection-protocol.md), which additionally
  authenticates audience/channel, checkpoints sequence, binds exact artifact
  bytes, and maps complete offerings only to local targets.

## Domain model

`OfferingKey`
: Stable routable identity. The `offering_id` should encode deployment choices
  that materially affect price, behavior, or latency. `model_id` identifies the
  underlying versioned model.

`Observation`
: Decimal point estimate plus unit, optional interval, sample count, timestamp,
  and `SourceReference`.

`ObservationCatalog`
: A set of offering observations bound to one exact `WorkloadReference`.
  Workload-independent facts such as prices may be copied into a workload
  catalog for the alpha contract; a separate effective-dated `PriceCard` will
  remove that duplication.

`WorkloadProfile`
: Immutable versioned definition of a work unit. Examples are a successful
  coding session, resolved repository issue, completed research report, or
  stateful customer-service trajectory. Harness and cohort are explicit;
  benchmark, budget, and assumptions cover the evaluator, retry policy, task
  mixture, and constraints rather than only prompt text.

`MetricDefinition`
: One of `signal`, `formula`, or `oracle`. Formula inputs are offering signals,
  numeric workload variables, and numeric metadata. A published benchmark
  result is normally imported as a workload-bound signal. An oracle is a
  host-run evaluator or judge resolved from an explicit `(name, version)`
  registry; it is not a synonym for every quality benchmark.

`FrontierDefinition`
: Workload reference, two metric/goal pairs, eligibility, freshness and sample
  requirements, uncertainty mode, epsilon tolerances, output order, and
  projected metadata.

`FrontierSnapshot`
: All evaluated offerings are retained, including dominated candidates. This
  matters because a runtime eligibility change requires recomputing the
  frontier: merely filtering existing frontier members can omit an offering
  that was dominated only by the now-ineligible model.

`SelectionDefinition`
: An explicit ordering axis, desired count, provider diversity constraint,
  expiry, and behavior when too few candidates are available. A frontier is a
  set, so it has no inherent “best” member without this extra policy.

`SelectionSnapshot`
: The chosen default and fallbacks plus strategy, requested count, provider
  cap, insufficient-candidate behavior, policy hash, frontier snapshot id, and
  validity window. Semantic validators reject duplicate or policy-incoherent
  choices before a resolver accepts the artifact.

## Metric evaluation

Signal metrics copy a canonical observation after checking unit, freshness,
sample count, and interval requirements. Benchmark adapters should materialize
published or locally generated results as signals after binding the exact
benchmark release, task cohort, harness, configuration, budget, and offering or
agent-system identity. A score is not a timeless property of a bare model.

Formula metrics use a small non-Turing-complete grammar with decimal
arithmetic. Available roots are `signals`, `workload`, and `metadata`.
Supported functions include `min`, `max`, `mean`, `clamp`, `coalesce`, and a
lazy `if`. General attribute access, imports, comprehensions, and arbitrary
function calls do not exist. Formula dependency paths are recorded so input
freshness can be checked. Expression length, syntax-tree depth, literal size,
power/round/exp operations, finite values, and canonical output size are
bounded.

Every published axis estimate retains its evaluated dependency paths, source
ids and full source references, and its oldest timestamp and minimum sample
count when those aggregates are complete. The frontier also binds the complete
input catalog by SHA-256. For a formula axis, signal paths retain the
observation source (or the offering's default source), `workload.*` paths
retain every source on the workload profile, and `metadata.*` paths retain the
offering's default source. `oldest_observed_at` and `minimum_sample_count` are
populated only when every evaluated signal observation supplies that field;
`null` means the aggregate is incomplete or unknown, never a minimum computed
from only the known subset.

A source id maps to exactly one full descriptor within a catalog, project, or
published snapshot, and the engine also requires matching descriptors across
the selected workload and catalog. Reusing an id with a different version,
URL, methodology, hash, or retrieval time is rejected rather than conflating
watermarks. Every `max_source_age_hours` key must resolve to a source declared
by that workload or catalog; an unknown or misspelled id is an evaluation error,
while a known source limit applies only to axis observations that actually use
that source. These limits compare `Observation.observed_at` with evaluation
time, not `SourceReference.retrieved_at`. Source
URLs are public citations: user information, query strings, and fragments are
forbidden because snapshots must never publish signed URLs or credentials.
Remote benchmark retrieval is an operator-controlled action: adapters use a
fail-closed hostname allowlist, refuse redirects, bound bytes and time, and pin
release sources by digest. A custom hostname must be explicitly allowed; this
option must not be exposed directly to untrusted application input.

`SourceReference.retrieved_at` records an acquisition event, not an evaluation
policy choice, so it is the one workload-source field excluded from a frontier's
`config_hash`. Source id, version, raw digest, URLs, license, and methodology
remain policy-bound. Every workload source is also embedded in the frontier
snapshot, and catalog sources remain bound by `catalog_hash`, so retrieval time
is still covered by immutable provenance identities. An identical refresh can
therefore advance snapshot history without appearing as a new semantic RSS
event. Oracles may receive the full workload for provenance, but should treat
retrieval time as non-semantic when caching; a value-affecting recency rule
belongs in versioned workload variables, oracle options, or observation
timestamps, and semantic oracle changes require a new `oracle_version`.

Formula output units are declared but dimensional analysis is not yet
implemented. Robust interval propagation through formulas is also deferred;
today a robust frontier rejects formula axes rather than manufacturing unsafe
bounds.

Weighted quality composites can be formulas when all inputs belong to one
declared composite workload and missing-value and normalization policies are
explicit. If uncertainty matters, an adapter should materialize the composite
as a new observation with its own source and bounds instead of implying that
the formula engine propagated component intervals. Separate workload frontiers
plus the overlap policy in [ADR 0002](adr/0002-multi-frontier-overlap-and-proximity.md)
are preferable when a scalar blend would hide tradeoffs.

Oracle metrics are an interface, not a configuration escape hatch. In v0.6,
they are usable only by a Python application that registers implementations in
an `OracleRegistry` and passes it to `FrontierEngine`. The stock `evaluate`,
`select`, and project publisher paths construct an empty registry; an oracle
metric therefore rejects its candidates there as unregistered. No HTTP or
JSONL subprocess transport is implemented yet. A future explicit transport may
return an observation shaped like:

```json
{
  "value": "0.82",
  "unit": "ratio",
  "lower": "0.77",
  "upper": "0.86",
  "sample_count": 200,
  "observed_at": "2026-08-29T18:00:00Z",
  "source": {
    "id": "tests-pass-oracle@2",
    "methodology": "oracle prompt/model/hash or executable digest"
  }
}
```

Results should be cached by a content hash covering workload, offering,
inputs, oracle implementation, prompt, and judge model.
Arbitrary oracle exception text is never copied into a public rejection
artifact; adapters should send detailed diagnostics to a private, redacted log.

[ADR 0004](adr/0004-quality-evidence-and-benchmark-bundles.md) defines the
quality-evidence boundary, reviewed benchmark-to-offering mappings, a small
benchmark-bundle design, and the adapter/discovery path. The bundle is a
logical composition of existing workload catalogs and frontiers in v0.6, not a
new implemented wire artifact.

## Work-unit cost

Token price alone is not the economic objective. Canonical usage traces keep
explicit billing meters:

- uncached input tokens;
- cache-read tokens;
- cache-write tokens by retention tier, or an explicit unknown-retention bucket;
- cache storage token-hours;
- an optional inclusive input-token total;
- non-reasoning output and reasoning tokens, plus an optional inclusive total;
- request, tool, web-search, media, and other provider charges;
- optional estimated and authoritative billed all-in costs as alternative cost bases;
- sandbox/compute duration;
- attempts, retries, and failed work units.

An observed request cost is a dot product of meter quantities and the
effective-dated price card. The useful population quantities are:

```text
work_unit_cost = sum(all request + tool + compute costs in every attempt)
cost_per_success = sum(cost of successful and failed work units)
                   / sum(success weight)
```

This differs from mean attempt cost and prevents a cheap but failure-prone
offering from looking artificially attractive. `aggregate-traces` computes
both per-work-unit and per-success quantities in DuckDB and deliberately keeps
failures in the numerator. DuckDB returns raw Decimal totals; all division is
performed under the engine's fixed 34-digit Decimal context. Duplicate request
rows and workload mismatches are rejected, and latency statistics carry their
own request-level sample counts and watermarks. JSONL rows are validated against
the strict `RequestTrace` contract before ingestion. Parquet inputs must expose
the same closed schema, timezone-aware timestamps, and exact integer or Decimal
meters; binary floating-point columns are rejected rather than rounded. Each
aggregate summary is bound to its workload and records the raw input file's
SHA-256, so it cannot be applied to a different workload catalog or silently
lose source identity.

Trace ingestion is intentionally finite: JSONL is capped at 256 MiB, Parquet
at 1 GiB, and either representation at one million rows, 10,000 distinct
offerings, and 500,000 work-unit groups. Before reading a DuckDB relation, the
aggregator sets two worker threads, a 256 MiB DuckDB memory limit, and a 512 MiB
spill limit. Spill files live under the private input-snapshot temporary
directory rather than the working tree and are removed with that snapshot.
Aggregate results are fetched in bounded batches. These controls bound the
engine's configured resources and output cardinality; they are not an OS-level
sandbox or a substitute for process/container quotas when ingesting hostile
files.

The compatibility contract `request-trace.schema.json` remains the published
v1alpha1 format, including its legacy zero defaults. The retained
`model-skyline/request-trace/v1alpha2` contract validates against
`request-trace-v1alpha2.schema.json` and remains byte-for-byte unchanged.
`model-skyline/request-trace/v1alpha3` validates against
`request-trace-v1alpha3.schema.json` and adds only the `model_call` observation
scope needed by logical-call telemetry such as OpenClaw's. Both Draft 2020-12
schemas enforce row-local scope/count rules, request-only timing, cache-write
representation, and complete producer/collector provenance. A single input
must use exactly one supported schema version. JSON Schema cannot compare
arbitrary exact Decimal fields, so it is not the complete trust boundary:
consumers MUST also run the `RequestTrace` semantic validator for input/output
total arithmetic and the trace aggregator for cross-row identity, scope,
outcome, offering, timestamp, and provenance coherence.

`RequestTrace` can declare `observation_unit` as `request`, `attempt`, or
`work_unit` under v1alpha2, with v1alpha3 additionally allowing `model_call`. A
request row contributes one actual provider request. A `model_call` row
represents one logical model invocation, which may span an unknown number of
provider requests because of retries or transport behavior. Aggregate rows contribute only an explicit
`model_request_count`; when the framework does not expose that count,
request-count signals are omitted rather than fabricated as one. Request,
model-call, and attempt rows derive attempts from distinct `attempt_id` values;
a work-unit aggregate must provide `attempt_count` or leave the attempt signal
unknown. Multiple model-call rows may share an attempt, but one work unit cannot
mix observation granularities, and attempt/work-unit aggregates must be unique
for their declared scope. `request_id` is the unique trace-record id for
aggregate rows and should be a local pseudonym rather than a raw framework
session id.

Unsupported meters are `null`, not measured zero. A quantity is published only
when every contributing row reports it, so partial telemetry cannot silently
undercount a work unit. Input billing buckets are mutually exclusive. Generic
`input_cache_write_tokens` represents the complete write total when retention
is not exposed and is mutually exclusive with the 5m/1h representation. It
cannot be priced with a retention-specific rate without an explicit operator
rule. Either complete representation can support cache-hit calculation without
inventing zero-valued retention buckets. `input_total_tokens` preserves an
upstream inclusive counter when a disjoint cache split is unavailable and is
validated against the split whenever all buckets are known. `output_tokens` excludes reasoning;
when an upstream total includes
reasoning, an adapter with a reliable split subtracts it and also records
`output_total_tokens`. If no split exists, only the inclusive total is reported.
Likewise, `estimated_total_cost_usd`, `provider_reported_total_cost_usd`, and
`billed_total_cost_usd` overlap the component meters and are alternative cost
bases, never extra charges to add to a reconstructed bill. Runtime/client price
calculations belong in the estimated meter; only provider billing
reconciliation belongs in the billed meter. `provider_marginal_cost_usd` can
record the provider charge, including an explicit zero for an included
subscription call, but is intentionally labeled as marginal rather than total
economic cost. Every USD formula declares exactly one basis, and static formula
analysis rejects overlap among the four canonical all-in signal families before
evaluation. Other `signals.*usd*` names are treated as operator-declared
reconstructed components; an operator must not disguise an invoice or total
under a custom component name. Explicit per-signal accounting roles remain a
future schema improvement.
The observed cache-hit rate is emitted only when every input/cache bucket is
known, and is cache reads divided by uncached input plus cache reads plus all
cache writes.

Two cache modes are planned:

- `observed`: use the provider usage meters in actual traces. This is the
  implemented path.
- `simulated`: replay prefix identities, reuse gaps, TTLs, concurrency,
  eligibility thresholds, region/provider affinity, and retention charges.
  Counterfactual values must always carry their assumptions and must never be
  labeled as historical actual cost.

Historical actual cost uses the price card effective at event time. “Same
workload at current prices” is a separate counterfactual metric.

The bundled example is explicitly the latter: it multiplies workload totals by
one declared synthetic price observation, including request and cache-storage
meters. It demonstrates configurable total-cost formulas but is not an
event-time historical-cost implementation. Effective-dated `PriceCard`
selection remains the first roadmap item.

## Pareto semantics

For point estimates, offering A dominates B when A is no worse on both axes
and strictly better on at least one. Identical points remain co-members.

An axis tolerance is:

```text
epsilon = epsilon_absolute
          + epsilon_relative * max(abs(value_a), abs(value_b))
```

Within epsilon, values are treated as practically equivalent. A difference
must exceed epsilon to count as meaningfully better. Policy comparisons round
both axis operands symmetrically to the fixed 34-significant-digit,
round-half-even Decimal context before tolerance arithmetic; snapshots retain the original observation
precision. This avoids comparing one exact high-precision operand with a
rounded add/subtract result.

For `robust` uncertainty, A dominates B only when A's pessimistic interval
bound is no worse than B's optimistic bound on both axes and meaningfully
better on one. Missing intervals exclude an offering. Probability-of-dominance
from bootstrap samples is a later mode.

The current all-pairs implementation is deterministic and simple for catalogs
of hundreds or thousands of offerings. A two-dimensional sort-and-scan can
replace it if measurements show the need.

## Selection and publication

The published single-frontier selection strategy is explicitly `lexicographic`
on one declared frontier axis, with the other axis as a stable tie-breaker and
optional maximum offerings per provider.

The additive multi-frontier library contract implements the overlap/proximity
policy in [ADR 0002](adr/0002-multi-frontier-overlap-and-proximity.md). It builds
a content-addressed descriptive sidecar over one exact frontier snapshot, then
re-ranks only the members of a primary frontier using ordered priority groups.
Within each group, exact-membership count, near-only count, and an ordered
per-frontier distance vector are compared before moving to the next group and
the primary ordering. Missing exact offering routes are explicit and rank after
measured evidence. Provider diversity is applied to the fully re-ranked stream.
The policy binds exact frontier and sidecar hashes plus individual freshness
limits; cross-workload evidence is intentional. Its schemas are
`frontier-proximity.schema.json` and
`multi-frontier-selection-snapshot.schema.json`.

The multi-frontier JSON Schema is structural, not an authenticity boundary.
Before routing, a consumer must pin the authorized selection ID and policy,
authenticate its publication channel or manifest, and run the source-backed
`verify_multi_frontier_selection_snapshot` replay against every bound artifact.

This release exposes the resolved exact-snapshot layer in Python and JSON
Schema. Static logical references in `ProjectConfig`, CLI materialization,
publisher layout, and `DynamicResolver` support are not implemented yet and
must not be inferred from the existing single-frontier feed. Other planned
policies include threshold-then-optimize, normalized knee point, minimum
residence time, and admission from later Pareto layers.

Fallback diversity is a list-level property and should grow beyond provider to
model family, region, and shared infrastructure failure domains. Availability,
rate limits, and health are short-lived runtime eligibility overlays; they do
not mutate benchmark snapshots.

`publish-project` evaluates all selected frontiers and their selections at one
UTC timestamp, then commits one internally coherent publication. Repeating
`--catalog` supplies at most one catalog per workload. Omitting `--frontier`
selects every configured frontier whose workload has a supplied catalog;
omitting `--selection` selects every configured selection backed by those
frontiers.

The implemented publication layout is:

```text
latest.json                                      # mutable project commit marker
publications/<publication-id>.json               # immutable project manifest
frontiers/<id>/<snapshot-id>.json
frontiers/<id>/<snapshot-id>.csv
frontiers/<id>/<snapshot-id>.txt
frontiers/<id>/history-<history-sha256>.json
frontiers/<id>/latest.json
frontiers/<id>/history.json
frontiers/<id>/table.csv
frontiers/<id>/table.txt
selections/<id>/<snapshot-id>.json
selections/<id>/latest.json
feeds/<frontier-id>/<feed-sha256>.xml
feeds/<frontier-id>.xml
```

JSON and its published schemas are the contract. CSV/table are human views.
RSS retains semantic changes across committed snapshots: items summarize
entrants, removals, rank or value changes, and the current ordered view while
linking to the full immutable snapshot when a base URL is configured. A new
snapshot whose ordered view is unchanged extends history without adding a
duplicate feed item. A workload, axis, policy, or offering-identity change is a
baseline reset rather than a misleading point-by-point diff. CSV neutralizes
spreadsheet-formula prefixes in textual cells.

Snapshot ids and artifact SHA-256 values name immutable files. A publication
manifest names only immutable files and links to its immutable predecessor;
the conventional `latest.json`, `history.json`, tables, selection aliases, and
flat feed paths are mutable discovery conveniences. A coherent reader begins
with root `latest.json`, verifies its `publication_id`, and follows the
manifest's immutable, digest-bearing references. It must not assemble a
project view by independently reading several mutable aliases.

Publication is ordered so primary immutable snapshots and derived artifacts
become durable first, the immutable publication manifest is written after all
files it names, mutable aliases are atomically replaced one at a time, and root
`latest.json` is replaced last. The process takes an advisory writer lock in
the output directory's parent and fsyncs files and directories where supported.
If a process stops before the last step, root `latest.json` still commits the
previous project view even if some aliases already show complete newer files.
Temporary files use unguessable names in the output root's parent, outside the
managed and served namespace, and only the creating process removes them. A
hard crash can leave a harmless sibling for operator cleanup; the publisher
does not infer ownership of pre-existing files from their names. Consequently,
the root and its parent must share a filesystem. The next identical run
validates the last committed chain and repairs aliases. Immutable collisions,
missing files, digest mismatches, timestamp rollback, unmanaged paths, and
symlinks fail closed.

An existing root is intentionally **full-refresh and additive**. Every
previously published frontier and selection must be included on every run;
new ones may be added, but omission is rejected instead of being interpreted
as retirement. There is no retirement operation in this alpha. Publish a new
project root (and, when appropriate, a new project id) when a set must shrink.
All frontier snapshots in a publication share one timestamp. Time is monotonic,
and one timestamp cannot identify two different snapshots for a frontier.

Public mode adds an HTTPS-base-URL requirement and checks the sources of both
the candidate and the entire retained frontier history. Every source must have
a license named by `--allow-license` or an exact id named by
`--authorize-source`, representing separately documented authority. It also
rejects aliases or immutable files not reachable from the committed chain or
the exact candidate, so an interrupted candidate can be retried but arbitrary
orphan content cannot silently become part of a public tree. These checks make
redistribution intent explicit; they do not determine that a license applies,
provide legal advice, or inspect content for secrets, prompts, personal data,
or private endpoints. Privacy review and source-rights verification remain
operator responsibilities.

The filesystem trust boundary is also explicit: the output root and every
parent directory must be exclusively writable by the publisher identity.
Path-component and symlink checks reduce accidents, but the implementation has
check-then-use windows and does not defend against a hostile local actor that
can concurrently alter those directories. Use a dedicated directory on a
trusted filesystem and serve a copy or read-only view to less-trusted users.

History reconstruction is alpha-scale. Each run reconstructs a retained
frontier history in O(n) entries and also validates the complete publication
manifest chain; practical work and I/O therefore grow with retained history.
Current hard limits include 10,000 history entries/manifests, 100,000 existing
files, 64 MiB per artifact, and 1,000 RSS items. Sharded/checkpointed history is
required before using one root for very long-running high-frequency
publication.

## Canonical artifacts and resolver trust

Decimals serialize as finite fixed-point JSON strings. Hash inputs use
[RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) canonical JSON followed by
lowercase hexadecimal SHA-256; the algorithm identifier is
`sha256-rfc8785-v1`. Snapshot identity hashes exclude `snapshot_id` itself;
apart from the null normalization described below, all remaining fields are
included. Frontier `config_hash` covers the selected frontier, workload, and its two metric
definitions, with only workload-source `retrieved_at` acquisition timestamps
normalized away; `catalog_hash` covers the complete canonical observation
catalog. Selection `policy_hash` covers its id and definition. The sole field
normalization exception is described below: `FrontierSnapshot`,
`SelectionSnapshot`, frontier semantic-view, and `ObservationCatalog` hash
inputs omit `OfferingKey.billing_mode` when its value is null.

File loaders parse JSON and YAML decimal literals directly as `Decimal`; they
never pass policy, price, observation, or epsilon values through binary
floating point. Fractional values inside free-form metadata, assumptions, and
oracle-option bags normalize to fixed-point strings as well. Formula paths can
consume those numeric strings. Integers remain JSON integers within the I-JSON
safe range. Programmatic callers should likewise prefer `Decimal` or strings;
float inputs are normalized from their shortest decimal representation before
publication.

The committed Draft 2020-12 schemas have stable URN `$id` values and ship in
the wheel. `export-schemas` copies those release contracts byte-for-byte;
maintainers use the hidden `regenerate-schemas` command under the locked
toolchain and review the diff.

The current contracts add optional `OfferingKey.billing_mode` to catalogs and
frontier/selection snapshots, and `FormulaMetric.cost_basis` to project
configuration. Catalog payloads that omit `billing_mode` remain valid, but an
older closed-schema validator will reject a newly serialized `billing_mode`
field even when it is `null`; publishers and consumers should therefore roll
these artifacts together. Likewise, existing USD formula configurations must
choose a `cost_basis` before loading under the new semantic validator, which is
an intentional ambiguity-closing compatibility break.

`FrontierSnapshot`, `SelectionSnapshot`, `ObservationCatalog`, and frontier
semantic-view hashes normalize a missing `billing_mode` and an explicit null
to the same state. This preserves immutable v0.3 frontier, history, RSS, and
selection identities after Pydantic loads the new optional field. Verification
also recognizes the explicit-null frontier, selection, and view hashes emitted
briefly by v0.4.0, but new artifacts use the stable normalized encoding.

The unsigned convenience resolver verifies content hash, semantic identity, expiry, future skew,
and monotonic generation time. It returns defensive copies so callers cannot
mutate its in-memory last-known-good value. Hashes do not stop a publisher or
network attacker from replacing and re-hashing content; use HTTPS and a trusted
origin. The built-in loader accepts HTTPS by default, can enforce an exact host
allowlist, and caps artifacts at 10 MiB. Plain HTTP and local files require
separate explicit opt-ins; file URLs with remote hosts are rejected. A custom
loader is a trusted integration boundary and must enforce equivalent transport
and size controls.

The signed gateway resolver is the untrusted-distribution and gateway-control
profile. Its DSSE pointer, exact-byte artifact bindings, local threshold policy,
durable SQLite anti-rollback state, exact target mapping, strict hard expiry,
and per-work-unit `PinnedGatewayRoute` are described in ADR 0003. Cross-language
fixtures ship in the repository and wheel. The signed resolver is additive: it
does not make ordinary frontier/publication hashes signatures, and it does not
place provider credentials or endpoints in remote artifacts.

The static publisher provides persistent history, coherent project commit
markers, retained feeds, and atomic single-file aliases. One scheduled workflow
imports the pinned Apache-2.0 Aider bundle. A second daily/manual workflow joins
three reviewed Aider GPT-5 routes to the exact models.dev URL and retains every
five-file projection under a content-addressed evidence tree. Both restore
durable `gh-pages` state, validate in read-only build jobs, serialize updates,
and use small write-authorized jobs without force-pushing. Valid prices advance
the models.dev research feed automatically; unsupported pricing shapes and
mapping drift fail closed.

Pages aliases remain static after source evidence expires. Neither workflow
provides failure monitoring, a spend-change approval gate, or a signed routing
selection. Production “always current” operation therefore needs alerts,
watermark checks, an explicit retention policy, and the separately reviewed
signed gateway channel with hard TTL. `DynamicResolver` state remains
process-local; `SignedGatewayResolver` uses a durable checkpoint and exact
last-known-good bundle.

## Near-term roadmap

1. Effective-dated `PriceCard` schema seeded from models.dev/LiteLLM and joined
   to request event time and official
   provider pricing/cache policies.
2. A TraceLab release adapter plus operator-trace profiles for observed
   cache-aware coding cost; then OpenTelemetry GenAI and OpenInference inputs.
3. Current provider/catalog joins for the implemented Aider and MCPMark
   benchmark adapters, with explicit historical versus counterfactual labels.
4. Activate and monitor both scheduled Pages research publications, then add a
   reviewed signed gateway channel and signing-key operational profile.
5. Build Wardwright as the first native signed-protocol consumer, followed by
   gateway and framework adapters described in `gateway-integrations.md`.
6. A Harbor leaderboard JSON/Terminal-Bench adapter, fixed-harness SWE-bench
   results, then Inspect/lm-eval adapters and broader licensed research,
   customer-service, and reasoning bundles under ADR 0004.
7. HTTP/subprocess oracle protocol, declared option schemas, exact result
   bindings, and a content-addressed result cache.
8. Formula dimensional analysis and safe interval propagation.
9. Selection hysteresis, failure-domain diversity, capability thresholds, and
   recomputation after dynamic filtering.
10. Autonomous trust-root rotation (likely TUF), checkpoint sharding, and
    signed multi-frontier selection references.
