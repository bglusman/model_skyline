# ADR 0004: Quality evidence and benchmark bundles

- Status: accepted; evidence, reconciliation, and bundle v1alpha1 contracts implemented;
  remote oracle transport deferred
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
  In v0.7 this is a library-embedding feature only: the stock CLI and publisher
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

### Exact, dependency-scoped evidence identity

The collector/operator should retain exact captured bytes in private audit
storage when source terms and local policy permit; the adapter always retains a
raw digest but need not copy raw bytes into its output bundle. A mapping must
not bind one undifferentiated hash of an entire mutable
leaderboard. Raw captures can contain contact PII, prompts, provider responses,
copyrighted web content, or secrets; public artifacts expose only allowlisted
normalized fields and hashes under an explicit rights review. A single evidence
set has one rights assertion, so adapters must split rows governed by different
licenses or terms. An undifferentiated leaderboard hash would require human
review when an unrelated row is added or when a correctly identified subject
merely receives a new score. Normalized quality evidence therefore separates at
least these hash domains:

| Digest | Identity-bearing fields | What a change invalidates |
|---|---|---|
| raw audit | Exact retrieved bytes/digest plus acquisition provenance | Audit acquisition only |
| source identity | Origin, board/dataset revision and split, task cohort, evaluator harness, scorer, protocol, and adapter projection version | Component workload and all mappings under that identity |
| subject identity | Exact row locator plus the model/system, agent, route, reasoning, and attempt claims used for reconciliation | That row's reviewed offering mapping |
| result | Score, counts, interval, observation time, and reported cost/time/token measures | Quality observation, catalog, and dependent frontiers |
| rights | License/terms assertion and redistribution review | Publication eligibility, not the numeric result or route mapping |

The source-identity domain binds task IDs or a task-set digest, cohort weights,
excluded or unscored tasks, prompts/templates, judge model and prompt when
applicable, tools/environment, generation parameters, seeds/epochs, retry
policy, concurrency, resource budget, work-unit definition, and sample-count
meaning. The raw-audit domain separately binds the raw digest, retrieval time,
source locator, capture method, parser implementation, and any asserted upstream
revision; those are audit metadata, not fields presumed to exist in the captured
bytes. Methodology relevant to semantic comparability belongs in source identity.
License and terms locators, review evidence/time, and redistribution permission
belong in the independent rights domain.

A semantic change to the cohort, evaluator harness, scorer, judge, parser
projection, configuration, or budget produces a new source identity. A change
to the system/model claims used for reconciliation produces a new subject
identity. A result-only change revises quality without forcing route review;
adding an unrelated row changes audit/quarantine coverage without resetting an
already mapped row. Merely re-fetching identical semantic bytes does not revise
the score. Pricing changes do not invalidate independent quality evidence.

### Reviewed result-to-offering mapping

Leaderboard names are untrusted labels, not routing instructions. A mapping
entry must contain the exact upstream row locator, adapter and projection
version, expected source-identity and subject-identity digests, the complete
target `OfferingKey`, review evidence, and review time. It may additionally pin
one complete evidence artifact for a frozen audit, but an always-current mapping
does not bind result-only or unrelated raw-source changes.

Mappings use exact equality only. Case folding, prefix/family matching,
provider fallback, source-side “latest” alias resolution, and fuzzy names are
forbidden. A mutable alias disclosed by the benchmark source is mechanically
quarantined. A source-identity or subject-identity change likewise quarantines
the affected mapping pending review. A new unmapped row is reported but cannot
affect selection. If provider, endpoint, service tier, reasoning effort,
quantization, or another material route field is unknown, the observation may
remain useful for research but cannot silently become a routable selection
candidate.

Exact `OfferingKey` equality is syntactic; it does not by itself prove that a
provider-scoped target name is immutable. Pin a target revision in the route
registry where the provider exposes one. Otherwise the host or gateway must
bind the offering to a separately reviewed target revision and enforce that
attestation's validity interval. The signed gateway profile provides this local
target-revision boundary; the generic reconciliation artifact does not.

Reconciliation distinguishes two relationships. `exact_subject_route` asserts
that the benchmark subject identifies the mapped production route; its complete
result may be retained. `reviewed_quality_projection` is an explicit human
assertion that selected quality evidence applies to a complete production
`OfferingKey` even though the source did not disclose that execution route. A
reviewed projection retains only measurements and counts typed as quality and
removes result metadata. Source-reported cost, latency, token use, cache fields,
and other telemetry remain in the route-free evidence set. Mutable aliases are
quarantined under both relationships until a future typed external identity pin
can prove what the alias meant at evaluation time.

Evaluator identity and route identity are distinct. The benchmark harness and
submitted agent belong to the workload/evidence subject. `OfferingKey.agent_harness`
describes only a harness that is part of the production routing target; it is
often null in the generic contract. The Harbor Terminal-Bench adapter requires
it to be explicit because every imported row is a compound agent system. This
distinction lets the same exact routable offering overlap
across SWE-bench, reasoning, and tool-use frontiers without erasing the
benchmark-specific evaluator provenance. A multi-model, router, or undisclosed
submission remains a composite/research subject and cannot be relabeled as a
bare component-model score.

### Small benchmark bundles

A benchmark bundle is a logical, content-addressed operator policy over two to
four components. The v1alpha1 policy and snapshot contracts bind exact component
frontier, catalog, configuration, workload, axis, and full-offering identities.
They expose measured, missing, and quarantined coverage independently and hard
exclude candidates that miss a required component or the minimum measured count.
Component, frontier, and snapshot IDs must be unique, but those syntactic checks
cannot prove that two differently packaged frontiers contain statistically
independent evidence. The operator must declare genuinely distinct intended
benchmark components and must not count one benchmark or task cohort more than
once without an explicit composite-workload rationale. A future contract can
bind typed benchmark-family/source-identity digests and correlation groups.

The recommended target general-agent bundle has three required components:

| Component | Intended evidence | Required scoping |
|---|---|---|
| [SWE-bench Verified, bash-only](https://github.com/SWE-bench/experiments/tree/main/evaluation/bash-only) | Repository-issue resolution | Exact experiments commit, submission directory, 500-instance cohort digest, harness generation/version/configuration, agent system, reasoning effort, attempts, resource budget, and per-instance result digest. Harness generations are separate components, not one time series. |
| [Terminal-Bench through Harbor](https://www.harborframework.com/docs/hosted-harbor/cli-leaderboards) | Multi-step computer/tool work | Exact board and dataset UUIDs, complete embedded schemas, rank and release-date column contract, row UUID, full agent/model source metadata, reasoning claim, and result digest. Current public rows support reviewed quality projection, not production-route cost attribution. |
| [tau2-bench](https://github.com/sierra-research/tau2-bench/tree/main/web/leaderboard/public/submissions) | Conversational agent policy/tool use | Exact repository commit, manifest class, submission directory, benchmark version, domain, task/split digest, user simulator, retrieval configuration, modality, reasoning effort, and verification flags. Keep airline, retail, telecom, and banking distinct unless a versioned macro workload defines full coverage. |

A reasoning-augmented bundle may add operator-supplied
[ARC-AGI-2](https://github.com/arcprize/arc-agi-benchmarking) results as a fourth
required component. Bind the exact task-data and harness commits, split, task-set
digest, canonical provider/model configuration, attempts, retry/budget policy, and
per-task results. ARC Prize's website is not a supported scheduled collection
interface; accept a local official-harness result or manually supplied snapshot.
[BFCL](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard)
is a useful tool-focused substitute for Harbor or tau2-bench, but normally should
not be added to both and silently triple-weight tool calling.

Operators should substitute domain-specific components rather than expand a
bundle indefinitely. Two components are sufficient for a narrow coding/tooling
policy; three are the default general-agent policy; four may cover a material
reasoning, research, or customer-service workload. More heterogeneous evidence
should normally remain in separate bundles. These sources do not provide a
common native RSS evidence contract: collectors poll supported JSON/Git/local
harness interfaces, and ModelSkyline emits RSS only after a reviewed mapped
frontier changes.

Each component keeps its own workload and score unit. The default selection
method is separate cost/performance frontiers followed by exact multi-frontier
overlap/proximity under [ADR 0002](0002-multi-frontier-overlap-and-proximity.md).
This preserves disagreements and missing measurements. It is preferable to a
single “quality” average. A production bundle policy must additionally declare
its required component IDs and minimum measured-component coverage; visible
missing evidence alone must not make an insufficiently measured candidate
eligible.

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
while its route-availability or price observation expires independently. Keep
three clocks separate: observation time for metric/source eligibility,
collector health for monitoring whether upstream is still being polled, and
mapping validity for mutable route-identity attestations. Retrieval time is
acquisition provenance and must not substitute for all three. An operator can
still set metric or source-specific maximum ages when deployment drift makes
old evidence unsuitable.

The bundle policy applies a per-component snapshot age and may bind an earlier
`evidence_valid_until` deadline supplied by its producer. A frontier snapshot
does not contain enough information to reconstruct every original observation
expiry, so omitting that deadline is an explicit producer assertion that the
snapshot-age limit is sufficient. The v1alpha1 reconciliation contract blocks
source-disclosed mutable aliases and does not infer mapping expiry from review
time. It cannot detect every mutable provider target encoded as an otherwise
exact `OfferingKey`; hosts must enforce that route-attestation validity clock
separately.

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
        -> normalized evidence + dependency-scoped hashes
        -> reviewed full-OfferingKey reconciliation
        -> typed mapped/quarantined/drift report
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
license/terms metadata. Collector policy should additionally declare
`supported_api`, `git_api`, or `manual_only`, its terms URL and review date,
maximum cadence, conditional-request behavior, and redistribution assertion.

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
- Result-only changes can refresh quality without remapping a stable subject;
  source/subject drift and newly unmapped rows fail closed or quarantine with a
  typed audit outcome.
- Multi-frontier overlap keeps heterogeneous evidence visible and avoids
  arbitrary scalar weights by default.
- Future generic Inspect, lm-eval, and leaderboard adapters could cover many
  benchmarks; they are not implemented in v0.7, and task-specific adapters
  remain necessary when result identity or telemetry is richer than those
  formats preserve.
- v0.7 ships normalized evidence/reconciliation/report contracts, a strict
  Harbor importer, and content-addressed two-to-four-component bundle policy and
  snapshot contracts. Library and CLI paths build proximity sidecars, hard-gate
  exact route identities, source-replay positive coverage against a separately
  supplied expected policy, recompute every participating feasible frontier,
  emit a bundle-bound default/fallback artifact, and fully verify it from exact
  sources. The convenience resolver requires the stable bundle-ID pin, offers
  exact version/policy-hash pins, and enforces the wrapper's hard deadline;
  its anti-rollback floor remains process-local. `publish-project`, signed
  gateway-pointer support, native framework consumers, a generic adapter
  registry, executable oracle transport, structured interval descriptors, and
  cross-component uncertainty propagation remain future work.
