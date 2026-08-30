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

The current repository proves Pydantic, DuckDB, Lark, and protocol boundaries,
but all bundled observations are synthetic. Keeping Python is conditional on
the next milestone shipping at least one real catalog/price adapter and one
real trace/benchmark adapter. Otherwise ADR 0001 must be reopened.

## Non-negotiable invariants

- A frontier has exactly two distinct axes; each explicitly says `minimize` or
  `maximize`.
- A candidate is an offering, not only a model: model version, provider
  endpoint, region, tier, quantization, reasoning configuration, and agent
  harness can change its position.
- A metric is scoped to a versioned workload and has a unit, source, timestamp,
  sample count, and optional confidence bounds.
- An observation catalog is bound to exactly one workload id, version, and
  work-unit definition; the engine rejects cross-workload reuse. Offering
  harness identity must match the workload harness.
- Missing, stale, unit-mismatched, or unbounded values are excluded with a
  reason when required. They are never silently zero-filled or imputed.
- Formula configuration cannot execute Python. Oracle code is registered by
  the host and addressed by name plus version.
- Published snapshots are immutable and content-hashed. Agents pin one
  selection snapshot for an entire work unit so a trajectory cannot switch
  models halfway through.
- A stale selection is usable only as a bounded last-known-good value after a
  refresh error. A newly fetched expired artifact is rejected.
- Artifact hashes provide content identity, not publisher authentication.
  Runtime fetches require HTTPS by default and validate the expected selection,
  frontier, and workload identities.

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
  numeric workload variables, and numeric metadata. An oracle is resolved from
  an explicit `(name, version)` registry.

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
sample count, and interval requirements.

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
published snapshot. Reusing an id with a different version, URL, methodology,
hash, or retrieval time is rejected rather than conflating watermarks. Source
URLs are public citations: user information, query strings, and fragments are
forbidden because snapshots must never publish signed URLs or credentials.

Formula output units are declared but dimensional analysis is not yet
implemented. Robust interval propagation through formulas is also deferred;
today a robust frontier rejects formula axes rather than manufacturing unsafe
bounds.

Oracle metrics are an interface, not a configuration escape hatch. Production
oracle clients should use an HTTP or JSONL-subprocess protocol and return:

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

## Work-unit cost

Token price alone is not the economic objective. Canonical request traces keep
mutually exclusive billing meters:

- uncached input tokens;
- cache-read tokens;
- cache-write tokens by retention tier;
- cache storage token-hours;
- output and reasoning tokens;
- request, tool, web-search, media, and other provider charges;
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

Canonical input billing meters are mutually exclusive. In particular, an
adapter must not report reasoning tokens separately if the provider's output
counter already includes them. The observed cache-hit rate is cache reads
divided by uncached input plus cache reads plus cache writes across retention
tiers.

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
must exceed epsilon to count as meaningfully better.

For `robust` uncertainty, A dominates B only when A's pessimistic interval
bound is no worse than B's optimistic bound on both axes and meaningfully
better on one. Missing intervals exclude an offering. Probability-of-dominance
from bootstrap samples is a later mode.

The current all-pairs implementation is deterministic and simple for catalogs
of hundreds or thousands of offerings. A two-dimensional sort-and-scan can
replace it if measurements show the need.

## Selection and publication

The alpha selection strategy is explicitly `lexicographic` on one declared
frontier axis, with the other axis as a stable tie-breaker and optional maximum
offerings per provider.
Planned policies include threshold-then-optimize, target proximity, normalized
knee point, minimum residence time, and admission from later Pareto layers.

Fallback diversity is a list-level property and should grow beyond provider to
model family, region, and shared infrastructure failure domains. Availability,
rate limits, and health are short-lived runtime eligibility overlays; they do
not mutate benchmark snapshots.

Recommended publication layout:

```text
frontiers/<id>/<snapshot-id>.json
frontiers/<id>/latest.json
frontiers/<id>/table.csv
frontiers/<id>/table.txt
selections/<id>/<snapshot-id>.json
selections/<id>/latest.json
feeds/<frontier-id>.xml
```

JSON is the contract. CSV/table are human views. RSS is a change feed: one item
summarizes entrants, removals, and the current ordered list, linking to the
full immutable snapshot. A publisher should retain items from prior snapshots.
CSV neutralizes spreadsheet-formula prefixes in textual cells by default;
`--raw-csv` is available for systems that require exact raw text.

## Canonical artifacts and resolver trust

Decimals serialize as finite fixed-point JSON strings. Hash inputs use
[RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) canonical JSON followed by
lowercase hexadecimal SHA-256; the algorithm identifier is
`sha256-rfc8785-v1`. `snapshot_id` excludes only its own field. Frontier
`config_hash` covers the selected frontier, workload, and its two metric
definitions; `catalog_hash` covers the complete canonical observation catalog.
Selection `policy_hash` covers its id and definition.

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

The resolver verifies content hash, semantic identity, expiry, future skew,
and monotonic generation time. It returns defensive copies so callers cannot
mutate its in-memory last-known-good value. Hashes do not stop a publisher or
network attacker from replacing and re-hashing content; use HTTPS and a trusted
origin. The built-in loader accepts HTTPS by default, can enforce an exact host
allowlist, and caps artifacts at 10 MiB. Plain HTTP and local files require
separate explicit opt-ins; file URLs with remote hosts are rejected. A custom
loader is a trusted integration boundary and must enforce equivalent transport
and size controls. Signed manifests are a future option for untrusted
distribution.

There is not yet a scheduled/persistent publisher. The CLI emits one snapshot
or RSS item, and the resolver cache is process-local. A production “always
current” deployment still needs scheduling, atomic `latest` updates, retained
history/feed items, monitoring, and persistent last-known-good storage.

## Near-term roadmap

1. One real vertical slice: models.dev/LiteLLM pricing plus Aider Polyglot, and
   a TraceLab coding-trace adapter.
2. Effective-dated `PriceCard` schema joined to request event time and official
   provider pricing/cache policies.
3. Trace adapters for OpenTelemetry GenAI, OpenInference, and common agent
   framework usage events; then broader coding and tool-use benchmarks.
4. HTTP/subprocess oracle protocol with content-addressed result cache.
5. Formula dimensional analysis and safe interval propagation.
6. Publisher/service with ETags, atomic `latest` pointers, snapshot history,
   RSS retention, and health overlays.
7. Thin TypeScript and framework-specific clients (PydanticAI first, then
   LangChain/LangGraph, Vercel AI SDK, and generic OpenAI-compatible agents).
8. Selection hysteresis, failure-domain diversity, capability thresholds, and
   recomputation after dynamic filtering.
