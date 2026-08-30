# Prior art, data sources, and workload research

Research snapshot: **2026-08-29**. Model catalogs, benchmark results, API terms,
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

- [Aider Polyglot leaderboard data](https://github.com/Aider-AI/aider/blob/main/aider/website/_data/polyglot_leaderboard.yml)
  already combines pass rate, total cost, and time/case with command and Git
  hashes, making it an unusually convenient coding Pareto adapter. The live
  YAML also contains zero and explicitly marked incorrect cost values, so an
  adapter must validate records and pin the source commit rather than ingesting
  it blindly.
- [BFCL](https://gorilla.cs.berkeley.edu/leaderboard) covers function/tool
  selection with subscores, raw responses, cost, and latency.
- [MCPMark](https://github.com/eval-sys/mcpmark) and
  [MCP-Universe](https://github.com/SalesforceAIResearch/MCP-Universe) exercise
  real multi-turn computer/MCP environments with programmatic verification.
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
