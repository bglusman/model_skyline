# ADR 0004: Quality evidence and benchmark bundles

- Status: accepted architecture; bundle and oracle transports are not yet wire contracts
- Date: 2026-08-31
- Decision owners: ModelSkyline maintainers

## Context

An operator should be able to use any relevant benchmark as quality evidence,
but “quality” is not a universal scalar attached to a model name. A benchmark
score belongs to a particular task cohort, harness, agent or prompting
configuration, budget, scorer, model offering, and point in time. Public
leaderboards frequently identify only a model alias or a complete submitted
agent system, neither of which is automatically a currently routable provider
offering.

ModelSkyline also needs a practical way to cast a wider evidentiary net without
turning project configuration into a remote-code loader, silently blending
incomparable scores, or inheriting upstream redistribution rights that do not
exist.

## Decision

### Three distinct metric roles

ModelSkyline uses these terms narrowly:

- A published leaderboard row or a completed local benchmark run is imported
  into an `ObservationCatalog` and exposed through a `SignalMetric`.
- An `OracleMetric` calls a trusted evaluator or judge registered by the host.
  In v0.6 this is a library-embedding feature only: the stock CLI and publisher
  create empty oracle registries. HTTP and JSONL subprocess clients remain a
  future protocol, not a current configuration feature.
- A weighted or otherwise compound quality score is a `FormulaMetric` when its
  inputs share one declared composite workload. When interval propagation,
  subgroup policy, or a nontrivial reducer matters, the composite is instead
  materialized as a new sourced observation.

An axis still declares `minimize` or `maximize`; direction is not an intrinsic
property hidden inside an adapter. An adapter may emit a visible recommended
default and validate an upstream declaration such as lm-evaluation-harness
`higher_is_better`, but the resulting frontier goal remains reviewable operator
policy.

### Exact evidence identity

Every benchmark component binds, directly or through a content hash:

- source URL, exact retrieved bytes and SHA-256, retrieval time, upstream
  release or commit, parser/normalizer version, methodology, and source terms;
- dataset revision, split, task IDs or task-set digest, cohort weights, and
  excluded or unscored tasks;
- harness and agent implementation, scorer or verifier, prompts/templates,
  judge model and prompt when applicable, tools/environment, generation
  parameters, seeds/epochs, retry policy, concurrency, and resource budget;
- work-unit definition, sample-count meaning, observation time, and interval
  method and coverage or an explicit statement that bounds are unavailable;
- exact result row or system identity and the complete `OfferingKey` to which
  it is applied.

A semantic change to the cohort, harness, scorer, judge, parser, configuration,
budget, or offering produces a new workload/source/result identity. Merely
refreshing identical bytes does not revise the score. Pricing changes do not
invalidate independent quality evidence; a mutable model alias, reviewed route
mapping, or quality-producing configuration change does.

### Reviewed result-to-offering mapping

Leaderboard names are untrusted labels, not routing instructions. A mapping
entry must contain the exact upstream row locator, source/system label, a
canonical selected-row SHA-256, expected source digest and parser version, the
complete target `OfferingKey`, review evidence, and review time. An agent-system
submission must remain identified as that agent system; it cannot be relabeled
as a bare underlying model score.

Mappings use exact equality only. Case folding, prefix/family matching, provider
fallback, “latest” aliases, and fuzzy names are forbidden. A row or source hash
change invalidates the mapping pending review. If provider, endpoint, harness,
or another material route field is unknown, the observation may remain useful
for research but cannot silently become a routable selection candidate.

### Small benchmark bundles

A benchmark bundle is a logical, content-addressed operator policy over two to
four components. It is not a new implemented v0.6 wire artifact. In v0.6 it is
represented by the component workload catalogs, frontier snapshots, checked-in
mapping/policy, and their hashes.

The recommended first general-purpose bundle has three components:

| Component | Intended evidence | Required scoping |
|---|---|---|
| [SWE-bench Verified](https://github.com/SWE-bench/SWE-bench) | Repository-issue resolution | Exact dataset/harness revision, agent system, inference configuration, resource budget, prediction/result hashes, and resolved fraction. The score is an agent-system result, not inherently a model-only result. |
| [ARC-AGI-2](https://github.com/arcprize/ARC-AGI-2) | Abstract reasoning and efficiency | Exact public, semi-private, or private split; task/release digest; attempts; scoring and compute/cost limits. Public and privately verified results are distinct evidence classes. |
| One agent/tool benchmark: [BFCL](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard), [MCPMark](https://github.com/eval-sys/mcpmark), or [Terminal-Bench/Harbor](https://github.com/harbor-framework/terminal-bench-1) | Function calling or multi-step tool use | Exact benchmark/version/category, agent harness, tool/environment versions, model route, retry/concurrency policy, and verifier. The selected benchmark is part of bundle identity; these alternatives are not interchangeable. |

Operators should substitute domain-specific components rather than expand a
bundle indefinitely. Two components are sufficient for a narrow coding/tooling
policy; a fourth may cover a material workload such as research or customer
service. More heterogeneous evidence should normally remain in separate
bundles.

Each component keeps its own workload and score unit. The default selection
method is separate cost/performance frontiers followed by exact multi-frontier
overlap/proximity under [ADR 0002](0002-multi-frontier-overlap-and-proximity.md).
This preserves disagreements and missing measurements. It is preferable to a
single “quality” average.

An optional scalar composite requires a new versioned composite workload with:

- an explicit normalization formula and reference population for every input;
- non-negative Decimal weights and their rationale;
- macro versus micro aggregation and task/cohort weighting;
- a fail-closed missing policy, normally requiring every component rather than
  treating missing as zero;
- subgroup floors or eligibility rules where an average could conceal a severe
  regression;
- a declared uncertainty method. Formula axes cannot currently propagate
  component intervals and therefore cannot be used for robust dominance.

### Freshness and uncertainty

Quality evidence usually ages by semantic identity rather than a short wall
clock. A fixed, immutable benchmark run may remain valid historical evidence
while its route-availability or price observation expires independently. An
operator can still set metric or source-specific maximum ages when deployment
drift makes old evidence unsuitable.

`sample_count` must be described as tasks, scored tasks, repetitions, judge
votes, or another exact denominator. Model failures, harness failures, and
unscored cases must remain distinguishable. `lower` and `upper` are not
automatically confidence intervals: the workload methodology must state their
type, coverage, calculation, and assumptions. Reference Wilson intervals,
rounding bounds, repeated-run intervals, and judge disagreement are different
objects. Robust frontier mode is appropriate only when both axes carry
commensurable conservative bounds.

## Adapter and discovery architecture

Centralization is for discovery and immutable normalized evidence, not for a
global model score:

```text
official leaderboard or local eval log
        -> allowlisted, versioned adapter
        -> immutable source + selected-row hashes
        -> reviewed full-OfferingKey mapping
        -> workload-bound ObservationCatalog
        -> independent frontier(s)
        -> optional overlap/proximity selection
```

Adapters are installed and registered by the trusted host. Public project
configuration may name an installed adapter/version but may not dynamically
import a package, execute an expression, install a repository, or follow an
unreviewed URL. A future adapter descriptor should declare accepted format
versions, implementation digest, output units, compatible workload/harness
patterns, option schema, deterministic/stochastic behavior, batch support, and
license/terms metadata.

The first recommended public leaderboard adapter is the
[Harbor leaderboard CLI JSON interface](https://www.harborframework.com/docs/hosted-harbor/cli-leaderboards).
It exposes board and dataset-version UUIDs, row UUIDs, embedded schemas,
quality intervals, pass@k, cost, time, and token fields for public boards such
as Terminal-Bench. A trusted collector should pin Harbor, capture JSON, and
feed a non-executing parser; public configuration must never supply a command.
Token/cache relationships must be validated per board version rather than
assuming field names imply disjoint buckets.

The next coding adapter should use the fixed-harness
[SWE-bench `evaluation/bash-only` results](https://github.com/SWE-bench/experiments/tree/main/evaluation/bash-only),
where mini-SWE-agent version, reasoning effort, metadata, and per-instance
results can be bound to a repository commit. Multi-model submissions remain
composite systems and cannot become bare-model routes.

Other high-leverage generic inputs are:

- [Inspect AI evaluation logs](https://inspect.aisi.org.uk/eval-logs.html),
  which retain run status, task/model plan, aggregate results, usage, samples,
  and reductions. Import headers or redacted summaries locally because full
  logs can contain prompts, targets, outputs, tools, and API data. The
  [Inspect Evals register](https://github.com/UKGovernmentBEIS/inspect_evals/blob/main/EVAL_REGISTER.md)
  is discovery only; since May 2026, new evals live in independently versioned
  upstream repositories.
- [lm-evaluation-harness results](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/python-api.md),
  whose result contract includes task configs/versions, score standard errors,
  n-shot settings, direction, sample counts, and optional samples.
- The [Hugging Face official leaderboard API](https://huggingface.co/docs/hub/leaderboard-data-guide),
  which exposes benchmark discovery, score, verification status, source result
  file, and submission PR. `verified` is useful provenance, not proof of route
  identity or cross-benchmark comparability.

ARC-AGI-2 has a clean public leaderboard JSON document, but the current
[ARC Prize terms](https://arcprize.org/terms) prohibit automated/scripted and
systematic retrieval. ModelSkyline therefore must not schedule-fetch it without
written permission; an operator-supplied local snapshot can still be imported
under its applicable terms. None of these benchmark sources currently provides
an RSS contract suitable for evidence ingestion. Collectors poll their
supported JSON/CLI or commit-addressed files; ModelSkyline emits RSS only after
an exact mapped semantic observation changes.

Framework and repository licenses do not automatically cover every bundled
dataset, task, leaderboard result, model output, or judge output. Every source
retains its own license/terms and redistribution authorization. Restricted or
unknown-license bytes stay outside public bundles; ModelSkyline may publish a
derived observation only when the operator has documented authority to do so.

## Consequences

- Operators can choose relevant quality evidence without blessing one global
  benchmark or judge.
- Independent price, latency, and quality observations can refresh or expire
  without erasing one another.
- Exact mappings and workload versions add review work, but prevent a model
  family name from becoming a false provider-route claim.
- Multi-frontier overlap keeps heterogeneous evidence visible and avoids
  arbitrary scalar weights by default.
- Generic Inspect, lm-eval, and leaderboard adapters cover many benchmarks;
  task-specific adapters remain necessary when result identity or telemetry is
  richer than those formats preserve.
- v0.6 can import normalized benchmark signals, but it does not yet ship a
  bundle manifest, generic adapter registry, executable oracle transport,
  structured interval descriptor, or cross-component uncertainty propagation.
