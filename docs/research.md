# Prior art, data sources, and workload research

Research snapshot: **2026-08-30**. Model catalogs, benchmark results, API terms,
and licenses change; adapters must pin source versions and re-check terms.

## Finding and project position

There are strong gateways, learned routers, cascades, catalogs, and benchmark
aggregators. There is also already a hosted product named
[OpenRouter Pareto Router](https://openrouter.ai/docs/guides/routing/routers/pareto-router)
and an unrelated young open-source project named
[`pareto-router`](https://github.com/Keyan-sm/pareto-router). The working name
**ModelSkyline** avoids that collision.

No surveyed open-source project combines all of these:

- an arbitrary operator-defined pair of signal, formula, or oracle metrics;
- versioned workload profiles and offering-level identity;
- observed or simulated cache-aware total cost per successful work unit;
- provenance, uncertainty, freshness, and dominance explanations;
- immutable frontier and default/fallback manifests consumable by multiple
  agent frameworks;
- machine JSON plus a human table and change feed.

The defensible position is therefore a selection control plane that feeds
existing gateways, not another inference proxy.

## Routing and optimization prior art

| Project | License/access | Reuse or comparison | Gap relative to ModelSkyline |
|---|---|---|---|
| [OpenRouter Pareto Router](https://openrouter.ai/docs/guides/routing/routers/pareto-router) | Hosted service | Strong product prior art: coding percentile tiers, cheapest/fastest choice, two fallbacks, conversation stickiness | Coding-only, one fixed quality signal, no arbitrary axes, and no direct cost/latency cap |
| [vLLM Semantic Router](https://github.com/vllm-project/semantic-router) | Apache-2.0 | Execution/routing backend with programmable request signals and heterogeneous infrastructure | Not a versioned workload-frontier publication control plane |
| [LiteLLM](https://github.com/BerriAI/litellm) | MIT for the open repository; separate enterprise code exists | Provider abstraction, fallback execution, spend tracking, and normalized price registry | Routing/gateway focus; workload-quality frontier remains external |
| [TensorZero](https://github.com/tensorzero/tensorzero) | Apache-2.0 | Gateway, traces, feedback, evaluations, and experimentation | No arbitrary published two-axis frontier |
| [BitRouter](https://github.com/bitrouter/bitrouter) | Apache-2.0 | Agent-step-aware routing, policy manifests, evaluator interface, and OTLP telemetry | Useful execution/policy peer rather than a complete frontier data plane |
| [RouteLLM](https://github.com/lm-sys/RouteLLM) | Apache-2.0 | Learned strong-versus-weak router baseline and OpenAI-compatible serving | Primarily two-model per-query cost/quality routing |
| [FrugalGPT](https://github.com/stanford-futuredata/FrugalGPT) | Apache-2.0 | Budgeted cascade baseline | Simplified cost accounting and older evaluation pool |
| [ParetoBandit](https://github.com/ParetoBandit/ParetoBandit) | Apache-2.0 | Online adaptation, budget pacing, and nonstationarity ideas | Current formulation centers on cost versus quality |
| [LLMRouter](https://github.com/ulab-uiuc/LLMRouter) | MIT | Common research evaluation surface for one-shot, multi-turn, agentic, and personalized routers | Research framework, not a production artifact/control protocol |
| [Pareto set](https://github.com/tommyod/paretoset) / [pymoo](https://github.com/anyoptimization/pymoo) | MIT / Apache-2.0 | Validation reference and later multi-objective policy optimization | A two-dimensional observed skyline is small enough to implement directly |

Additional research baselines to evaluate include
[CARROT](https://arxiv.org/abs/2502.03261),
[AutoMix](https://arxiv.org/abs/2310.12963),
[OmniRouter](https://github.com/dongyuanjushi/OmniRouter), and
[Unified Routing and Cascading](https://arxiv.org/abs/2410.10347). Do not copy
code from repositories without a clear license.

## Agent-framework integration seams and priority

This is an implementation plan based on the official surfaces reviewed on the
research date, not a claim that any native integration is present in
ModelSkyline today. The integration boundary should remain the published
selection/frontier schemas plus canonical request traces. Each adapter must pin
or record its upstream API version, map a route to the complete offering
identity, preserve the framework/harness version, and pin one selection for a
whole work unit rather than re-resolving midway through an agent trajectory.

### 1. OpenClaw: first bidirectional reference integration

OpenClaw has the best combination of a dynamic selection seam and useful
telemetry. Its official plugin documentation and types expose a
`before_model_resolve` hook that can override provider/model choice before a
run, while its model configuration already owns ordered fallback execution.
The same typed hook surface includes model-call/output events, and its
OpenTelemetry documentation covers model usage and timing. That supports one
small plugin that resolves a ModelSkyline selection at work-unit start, maps
the selected offering to OpenClaw's provider/model identity, leaves retries to
OpenClaw, and emits cache-, token-, latency-, outcome-, and fallback-aware
canonical traces afterward.

The reviewed official sources are pinned to OpenClaw commit `ad00ba8`:

- [plugin hook lifecycle](https://github.com/openclaw/openclaw/blob/ad00ba847d891a95792de8d5ec5de696756c910d/docs/plugins/hooks.md);
- [typed hook contracts](https://github.com/openclaw/openclaw/blob/ad00ba847d891a95792de8d5ec5de696756c910d/src/plugins/hook-types.ts);
- [model selection and fallbacks](https://github.com/openclaw/openclaw/blob/ad00ba847d891a95792de8d5ec5de696756c910d/docs/concepts/models.md);
- [gateway OpenTelemetry](https://github.com/openclaw/openclaw/blob/ad00ba847d891a95792de8d5ec5de696756c910d/docs/gateway/opentelemetry.md).

The adapter must not collapse OpenClaw's provider, model, profile, or gateway
route into a bare model id. Hook contracts can change, so the first plugin
should pin a tested OpenClaw range and fail closed on unknown event shapes.

### 2. Hermes Agent: telemetry bridge, then selection mapping

Hermes is the quickest second path to real local usage and session evidence.
Its official CLI exposes usage data, its observer supports structured
observability, session storage retains the trajectory boundary needed for a
work-unit aggregate, and its provider configuration already describes fallback
behavior. Start with a read-only importer that maps usage/session records to
canonical traces without prompts or tool payloads. Only then add a consumer
that translates published choices into the provider/model configuration Hermes
actually supports; do not imply that a ModelSkyline list has native semantics
until failure ordering and stickiness have been verified end to end.

The reviewed official sources are pinned to Hermes Agent commit `4f22543`:

- [CLI commands and usage](https://github.com/NousResearch/hermes-agent/blob/4f22543509d1b91dc45bcb369447126c5eb14fb7/website/docs/reference/cli-commands.md);
- [observability observer](https://github.com/NousResearch/hermes-agent/blob/4f22543509d1b91dc45bcb369447126c5eb14fb7/docs/observability/README.md);
- [fallback-provider configuration](https://github.com/NousResearch/hermes-agent/blob/4f22543509d1b91dc45bcb369447126c5eb14fb7/website/docs/user-guide/features/fallback-providers.md);
- [session storage](https://github.com/NousResearch/hermes-agent/blob/4f22543509d1b91dc45bcb369447126c5eb14fb7/website/docs/developer-guide/session-storage.md).

### 3. Claude: typed cost/cache ingestion with a bounded native chain

Claude Agent SDK result types provide a strong typed ingestion surface for
reported total cost and usage, including cache creation/read counters. Claude
Code also documents fallback-model chains with a maximum of three models. A
first adapter can therefore ingest result messages into canonical traces and
generate a bounded chain from a selection when every offering maps to an
explicit supported Claude model/configuration.

Relevant official references are the
[pinned Python SDK types at `af5ff1b`](https://github.com/anthropics/claude-agent-sdk-python/blob/af5ff1b9f2f279575f89b78f17572c6e35fbc2b6/src/claude_agent_sdk/types.py),
[Agent SDK cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking),
[fallback model chains](https://code.claude.com/docs/en/model-config#fallback-model-chains),
and [Agent SDK observability](https://code.claude.com/docs/en/agent-sdk/observability).
The hosted runtime is commercial, and OpenTelemetry/event configuration can
carry sensitive prompt or tool context. Collection must be opt-in and redacted
before any trace or derived metadata reaches a public publication.

### 4. Codex: trace consumer and per-run choice before fallback orchestration

Codex provides two useful official machine interfaces. `codex exec --json`
streams JSONL events whose completed-turn record includes token usage, while
the App Server protocol has an explicit model on thread start and typed thread
token-usage notifications. Its advanced configuration also exposes
OpenTelemetry. These are credible telemetry and per-run model-selection seams.
No official ordered model-fallback contract was found in the reviewed Codex
surfaces, so the initial integration should ingest Codex events and set one
selected default per work unit; an external controller must own fallback
orchestration unless Codex later publishes such a contract.

Official references are
[`codex exec` non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
the [App Server guide](https://learn.chatgpt.com/docs/app-server), the pinned
[`ThreadStartParams` schema](https://github.com/openai/codex/blob/94cbbddafc1776d5e377bca1b05932c697e82238/codex-rs/app-server-protocol/schema/json/v2/ThreadStartParams.json),
the pinned
[`ThreadTokenUsageUpdatedNotification` schema](https://github.com/openai/codex/blob/94cbbddafc1776d5e377bca1b05932c697e82238/codex-rs/app-server-protocol/schema/json/v2/ThreadTokenUsageUpdatedNotification.json),
and [observability configuration](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry).

Across all four integrations, raw framework events should stay local by
default. The reusable output is a workload-bound, content-hashed aggregate or
redacted canonical trace with explicit cache-counter semantics—not a dump of
prompts, responses, tool arguments, repository paths, or environment values.

## Catalog, price, and performance sources

| Source | Useful fields | Access/licensing and caveats | Recommendation |
|---|---|---|---|
| [OpenRouter Models API](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties) and [endpoint API](https://openrouter.ai/docs/api/api-reference/endpoints/list-all-endpoints-for-a-model) | Model/provider metadata, capabilities, context, prompt/output/request/search/cache prices, endpoint latency/throughput percentiles, uptime, region, quantization; current API can sort by price, throughput, latency, and AA indices and emit RSS | Hosted API and terms; performance reflects routed traffic and rolling windows | High-value live adapter; keep model and endpoint observations distinct and do not assume redistribution rights |
| [Artificial Analysis Data API](https://artificialanalysis.ai/data-api/docs) | Versioned intelligence/coding/agentic indices, benchmark details, price and cached price, TTFT, output speed, end-to-end latency, endpoint percentiles/time series by tier | Free endpoint exposes headline/median fields; richer tiers require paid access. Attribution is required and redistribution needs appropriate terms | Optional licensed source, never vendored wholesale |
| [models.dev](https://github.com/anomalyco/models.dev) | Keyless provider/model APIs with capabilities, context limits, input/output/cache pricing, dates, licenses, and source links | MIT, community-maintained, no SLA; provider overrides demonstrate why model and offering are separate | Best open seed catalog; verify material prices against official sources |
| [LiteLLM price registry](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) and [schema](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.schema.json) | Cache creation/read by retention, long-context thresholds, tiers, batch/flex/priority, request/search/image/audio/page billing, and source URLs | Community normalization can lag or change schema | Richest reusable normalization input; ingest with source/effective dates and verify high-spend routes |
| [Portkey Models](https://github.com/Portkey-AI/models) | Large provider/model catalog including non-token units | MIT; community data | Secondary catalog and cross-check |
| [Hugging Face official leaderboard API](https://huggingface.co/docs/hub/leaderboard-data-guide) | Programmatic official benchmark discovery, ranked scores, verification flag, submission source, and pre-aggregated Parquet | Each benchmark/dataset has its own license and method | Preferred machine benchmark entry point; preserve benchmark/config/version and `verified` |
| [Epoch AI Benchmarking Hub](https://epoch.ai/benchmarks/use-this-data) | Downloadable normalized benchmark snapshots and client | Epoch aggregation is CC BY; underlying question licenses and protocols vary | Useful cross-check and history, not a license umbrella |
| [MLPerf Client/Endpoints](https://mlcommons.org/benchmarks/endpoints/) | P95 TTFT, per-user output rate, throughput and concurrency under declared load | Controlled endpoint harness; not the operator's traffic mix | Design precedent: latency and throughput are load curves, not model constants |
| [GuideLLM](https://github.com/vllm-project/guidellm) | Operator-run TTFT, inter-token latency, throughput, and end-to-end distributions against OpenAI-compatible endpoints | Apache-2.0; results depend on region, load, prompt distribution, and serving config | Preferred way to measure the actual deployment |

### Cache semantics require first-party policy

Provider cache mechanisms differ too much to infer from one discounted-token
price. Store effective-dated rules from the official documentation:

- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Gemini context caching](https://ai.google.dev/gemini-api/docs/caching)
- [AWS Bedrock prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
- [DeepSeek context caching and pricing](https://api-docs.deepseek.com/quick_start/pricing)

The normalized policy needs minimum prefixes, cache breakpoints/granularity,
implicit versus explicit creation, TTL choices and refresh behavior, read/write
multipliers, storage fees, usage-counter names, rate-limit treatment,
provider/region affinity, and privacy/ZDR constraints.

## Evidence for workload profiles

### Coding agents

[TraceLab](https://github.com/uw-syfi/TraceLab) is the strongest open empirical
starting point found. It publishes sanitized Claude and Codex invocation traces
with prompt-cache splits, timings, tools, and a reproducible DuckDB pipeline.
The repository's pinned v0.0.1 release describes 357,161 LLM rounds from 43
developers; the live site already reports a larger v2 pool, demonstrating why
an adapter must pin release, checksum, and analysis version. Code is
Apache-2.0 and the public dataset is CC BY 4.0. Its population is still a
specific developer cohort, and prompt/tool contents are intentionally stripped.

TraceLab supports a `coding_session` profile based on distributions rather
than a single mean: long repeated prefixes, small incremental appends, short
outputs, many LLM/tool rounds, compaction, tool latency, and error/retry paths.
Operator traces should be mixed in before treating it as representative.

### Search and research

[Search Arena 24K](https://huggingface.co/datasets/lmarena-ai/search-arena-24k)
contains 24,069 in-the-wild multilingual search conversations, search traces,
intent/language annotations, and 12,652 preference votes. User prompts are CC
BY 4.0; model outputs remain governed by provider terms. Arena selection,
response length, and citation presentation can bias preference.

[DeepResearch Bench](https://github.com/Ayanami0730/deep_research_bench)
provides report and citation metrics over 100 English/Chinese research tasks.
It is useful for a `deep_research_report` profile, but judge model/prompt/version
must be part of the observation identity.

### Proposed initial taxonomy

| Profile | Work unit | Empirical/benchmark seeds | Primary outcome examples |
|---|---|---|---|
| `interactive_chat` | completed user turn or conversation | operator traces, Arena datasets | preference, task completion, p95 latency |
| `coding_session` | completed interactive agent session | TraceLab plus operator Claude/Codex/OpenHands traces | user goal completion, tests, cost/session |
| `repo_issue_resolution` | issue resolved under a fixed harness/budget | [SWE-bench](https://github.com/SWE-bench/SWE-bench), [SWE-bench Live](https://github.com/microsoft/SWE-bench-Live), [SWE-rebench](https://swe-rebench.com/about) | verified tests, cost/success, wall time |
| `terminal_task` | verified terminal environment task | [Terminal-Bench/Harbor](https://github.com/harbor-framework/terminal-bench) | verifier score, tokens, cost, time |
| `search_qa` | answered search question with evidence | Search Arena, [BrowseComp](https://github.com/openai/simple-evals) | answer accuracy, citation quality, latency |
| `deep_research_report` | complete sourced report | DeepResearch Bench and operator research runs | report score, factual/citation score, cost |
| `customer_service_tool_task` | policy-compliant resolved stateful case | [tau2-bench](https://github.com/sierra-research/tau2-bench) | database end state, policy compliance, pass^k |
| `mcp_computer_work` | verified task across MCP applications | [MCPMark](https://github.com/eval-sys/mcpmark), [MCP-Universe](https://github.com/SalesforceAIResearch/MCP-Universe) | programmatic success, turns, tools, cost/time |
| `workplace_composite` | weighted organization-specific task mix | operator traces plus the above adapters | weighted success, SLOs, total cost |

Every profile should define task/cohort weights, calls and tool calls,
input/prefix/append/output distributions, reuse gaps and TTLs, concurrency,
retry policy, reasoning effort, harness version, success oracle, and latency
SLO. Model-dependent output length and retry/tool behavior must remain
model-dependent; using the same token count for every model is only a declared
counterfactual.

## Ready-made task-specific sources

### Implemented: Aider Polyglot

The Aider adapter pins
[`cb6a152`](https://github.com/Aider-AI/aider/blob/cb6a152e5ee27fbc77ac499d5e628ccd74a5fa2a/aider/website/_data/polyglot_leaderboard.yml)
and raw SHA-256
`85a50b25953512d18ba4bb0c23c0b8e626fcf9a5b52d287644b8a0b44b9535de`.
The repository and leaderboard are Apache-2.0. The file has 69 historical runs;
the strict import admits 20 after requiring all 225 cases, positive cost and
time, coherent exact pass counts, and a clean benchmark checkout. This is a
mixed historical leaderboard cohort, not a controlled experiment: admitted
rows still differ in Aider version, date, edit format, optional editor model,
and provider conditions. The generated harness and workload labels say so.

`pass_num_2` means cumulative tasks solved after Aider's optional repair edit,
not independent pass@2 sampling. `total_cost` is Aider's aggregate recorded
model-call cost across first and repair calls. It can reflect caching only to
the extent that the historical Aider/provider accounting did; there are no
cache read/write token splits, and local test execution or other infrastructure
is excluded. The adapter therefore publishes historical cost per attempted and
solved case but never relabels it as a current-price estimate.

The upstream `seconds_per_case` timer covers the Aider agent edit/generation
loop and stops before subsequent unit-test execution, so ModelSkyline publishes
it as `agent_edit_seconds_per_case`, not end-to-end task latency. Upstream cost
is displayed at finite decimal precision; derived cost observations carry
bounds that propagate half of the least displayed `total_cost` unit rather than
implying exact recurring decimal precision.

Solve-rate bounds are descriptive Wilson 95% binomial reference intervals under
an IID task-sampling assumption. They do not capture repeated-run, serving,
provider, or temporal variance, and the included frontiers intentionally use
point dominance.

On that strict cohort, cost per attempted case versus two-edit solve rate has
six frontier members: gpt-oss-120b high, DeepSeek V3.2 Experimental Chat and
Reasoner, and GPT-5 low, medium, and high. Changing the cost axis to cost per
solved case removes gpt-oss-120b because the denominator now embeds quality.
That difference is a useful demonstration of why frontier formulas must be
operator-visible.

### Implemented experimentally: MCPMark Verified

The MCPMark adapter pins the
[`b8a62a9` verified summary](https://github.com/eval-sys/mcpmark-experiments/blob/b8a62a98cc3b596c9d2e8a7879478df37a582c46/verified/summary.json)
with SHA-256
`1854f62b24dac18370dcfb61f87c6f2ef0dbdfce31ffa20cb29170c2a01753d3`.
It produces separate catalogs for 127 tasks across filesystem, GitHub, Notion,
Playwright, and Postgres, plus the overall mixture. Six models have complete
single-run pass rate, agent time, turn, and input/output token telemetry; two
score-only rows are excluded.

The code benchmark repository is Apache-2.0, but the separate experiments
repository had no license at the pinned revision. ModelSkyline therefore does
not vendor those results and labels their license `NOASSERTION`. The verified
summary also has no provider endpoint, cache split, tool charge, or cost. A
models.dev price join is technically possible for the six aliases, but it would
be a route-assumed current-price counterfactual, not observed cost; the initial
adapter deliberately limits itself to quality/time and quality/input-token
frontiers.

These workload-specific results differ materially. For example, the
quality/time frontier has four members for filesystem and Notion, three for
GitHub and Postgres, and only DeepSeek V4 Pro plus GPT-5.6 SOL for Playwright.
The quality/input-token frontier changes membership again. An overall aggregate
is consequently not a safe substitute for an operator's workload mix.

MCPMark solve-rate bounds have the same limited interpretation: descriptive
single-suite binomial reference intervals, not run-to-run confidence or evidence
that fixed tasks are genuinely IID. Generated manifests and workload
assumptions carry that caveat.

### Additional candidates

- [BFCL](https://gorilla.cs.berkeley.edu/leaderboard) covers function/tool
  selection with subscores, raw responses, cost, and latency.
- [tau2-bench](https://github.com/sierra-research/tau2-bench) is MIT-licensed
  and publishes agent trajectory cost and pass rates for airline, retail, and
  telecom, but excludes simulator, judge, retrieval, and tool infrastructure.
- [MCP-Universe](https://github.com/SalesforceAIResearch/MCP-Universe) expands
  programmatically verified multi-turn computer/MCP environments.
- [GAIA](https://huggingface.co/datasets/gaia-benchmark/GAIA) is useful general
  agent evaluation but gated; do not redistribute it.
- [TwinRouterBench](https://github.com/CommonstackAI/TwinRouterBench) is
  especially relevant router evaluation because it covers agent steps and
  realized spend/cache behavior rather than only one-shot prompts.
- [RouterArena](https://github.com/RouteWorks/RouterArena),
  [RouterBench](https://github.com/withmartian/routerbench), and
  [LLMRouterBench](https://github.com/ynulihao/LLMRouterBench) are router
  comparison surfaces. RouterArena's dataset has evaluation-only restrictions;
  LLMRouterBench did not expose a clear repository license during this review,
  so neither should be vendored without a specific terms check.

Benchmark scores belong to `(model offering, agent/harness, config, budget,
benchmark version)`, not to a bare model. Contamination, harness differences,
judge drift, and unequal tool budgets are material.

## Telemetry standards and components to reuse

- [OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai)
  for token usage, duration, TTFT, output chunks, model/provider identity, and
  standardized event/span transport.
- [OpenInference semantic conventions](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md)
  for agent/tool/evaluator spans and cache-read/write, reasoning, and cost
  attributes.
- [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) as the preferred
  Python evaluation orchestrator for tool-using agents where an existing
  benchmark harness is not authoritative.
- DuckDB/Parquet as the reproducible local analysis substrate. Keep raw traces
  immutable and materialize derived observations with query/config hashes.

Pin semantic-convention versions. Both OpenTelemetry GenAI and OpenInference
are evolving, and a field with the same name can change interpretation.

## Cost metrics to keep distinct

- mean request/attempt cost;
- mean work-unit cost, including retries and failures;
- cost per completed work unit;
- `cost_per_success = total spend / successful work units`;
- historical cost at event-time prices;
- counterfactual cost at current prices;
- counterfactual expected cost under a simulated cache/routing policy.

The primary economic frontier for agents should usually use cost per success or
total work-unit cost, not cost per token. Cost/token remains a useful optional
axis and catalog sanity check.

## Principal risks

- provider billing meters or cache semantics are normalized incorrectly;
- latency mixes regions, tiers, concurrency, queueing, prompt shapes, or
  streaming definitions;
- mutable provider aliases silently change weights or configurations;
- workload mixture hides subgroup regressions (including Simpson's paradox);
- judge self-preference, nondeterminism, prompt drift, or judge-model changes;
- small metric movements cause selection oscillation;
- health or reliability becomes an undeclared third objective rather than an
  explicit eligibility/diversity rule or separate frontier;
- source licenses permit analysis but not redistribution;
- trace publication leaks prompts, tool data, secrets, or identifying context.

Mitigations are effective dates, exact identity grain, minimum samples,
confidence intervals, subgroup snapshots, provenance hashes, hysteresis,
bounded stale behavior, privacy review, and never bundling upstream data unless
its license clearly permits it.
