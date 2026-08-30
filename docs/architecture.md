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
`sha256-rfc8785-v1`. `snapshot_id` excludes only its own field. Frontier
`config_hash` covers the selected frontier, workload, and its two metric
definitions, with only workload-source `retrieved_at` acquisition timestamps
normalized away; `catalog_hash` covers the complete canonical observation
catalog. Selection `policy_hash` covers its id and definition.

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

The static publisher provides persistent history, coherent project commit
markers, retained feeds, and atomic single-file aliases. The repository's
scheduled GitHub Actions workflow imports only the pinned Apache-2.0 Aider
bundle, restores durable history from `gh-pages`, validates a public
publication in a read-only build job, and uses a separate write-authorized job
to advance that branch without force-pushing. The repository's Pages setting
still must be activated, and the workflow does not provide monitoring,
publisher authentication/signatures, a general hosted service, or persistent
resolver cache. Production “always current” operation still needs a trusted
static origin plus alerting and an explicit retention policy. The resolver
cache remains process-local.

## Near-term roadmap

1. Effective-dated `PriceCard` schema seeded from models.dev/LiteLLM and joined
   to request event time and official
   provider pricing/cache policies.
2. A TraceLab release adapter plus operator-trace profiles for observed
   cache-aware coding cost; then OpenTelemetry GenAI and OpenInference inputs.
3. Current provider/catalog joins for the implemented Aider and MCPMark
   benchmark adapters, with explicit historical versus counterfactual labels.
4. Activate and monitor Pages for the scheduled Aider publication, then add
   ETag-aware serving and persistent resolver state.
5. OpenClaw and Hermes telemetry/selection adapters, then Claude and Codex
   consumers; the researched seams are listed in `research.md` and none of
   these native integrations is implemented yet.
6. Broader licensed research, customer-service, and terminal-task adapters.
7. HTTP/subprocess oracle protocol with content-addressed result cache.
8. Formula dimensional analysis and safe interval propagation.
9. Selection hysteresis, failure-domain diversity, capability thresholds, and
   recomputation after dynamic filtering.
10. Manifest authentication and checkpointed or sharded publication history.
