# ModelSkyline

ModelSkyline gives a numerical answer to a practical question: **which few
models are best for this particular meaning of “best”?**

Each frontier compares two things that matter, such as intelligence versus
cost, intelligence versus speed, or speed versus cost. A model remains on the
frontier when no tested alternative is better on both. The result is a small
set of useful tradeoffs—not one universal leaderboard winner.

The first-read result can be model-focused: “Qwen is the quality-first choice;
Muse uses less memory; Ornith is faster.” The underlying evidence remains more
specific because model performance and price depend on provider, hardware,
quantization, runtime, and settings. ModelSkyline keeps those details available
for audit without requiring every reader to understand them first.

> **Status:** working alpha. The catalog → frontier → selection path is
> implemented and tested, and an external v0.6 consumer used its CLI and JSON
> artifacts with real workload data. Benchmark ingestion, telemetry,
> publication, and RSS are first-party integrations around that path. There is
> no hosted service or package-registry release yet, and alpha schemas may
> change.

The [public research publications](https://bglusman.github.io/model_skyline/)
provide browsable tables, machine-readable manifests, RSS feeds, and retained
evidence. They are research outputs, not current routing instructions; the
landing page states the applicable freshness, cost-scope, and candidate-set
limits.

A [live frontier snapshot page](https://bglusman.github.io/model_skyline/research/snapshots/)
publishes regularly-refreshed Pareto frontiers as a worked *example*: one real
household's agent and coding workloads across subscription and metered purchase
paths, with charts, a cross-frontier model map, and full provenance (assumed
values are flagged, exclusions carry reasons). These are example data for our
arbitrary frontiers, not recommendations — real use cases lend themselves to
better-defined, custom frontiers, and the framework exists to support exactly
that: declare your own workload shape, offerings, and axes;
`modelskyline evaluate` and `modelskyline select` do the rest.

The current [plain-language local-model
frontiers](examples/local-runtime-frontiers/current-model-frontiers.md) show
this model-first view for the 64 GB Apple Silicon experiments and the 16 GB
RTX 5060 Ti. On the 5060, Qwen3.5 9B Q6 is the first tested route to retrieve
an exact value from 126,002 input tokens while fitting in about 11.72 GB of
combined host/GPU service capacity. Laguna remains the faster-generation and
shorter-context tradeoff. These are runtime screens, not coding-quality scores;
their exact measurements and limitations remain linked from the same page.

The [local intelligence-efficiency protocol](docs/intelligence-efficiency.md)
adds quality × average-power and quality × energy-per-item frontiers. It treats
adapter wattage, OS power mode, and GPU power limits as explicit service tiers,
so the same machine can be compared at several controllable operating points
without confusing a power ceiling with measured draw.

The [structured-decision and compound-system
experiment](examples/structured-decision-frontiers/README.md) adds separate
frontiers for typed routing decisions and complete tool-using systems. It can
compare Jev with general LLMs on the same decision contract after TypeSafe
waitlist access, then compare heavy-only, light-only, and router-plus-worker
policies end to end. The first local screen keeps both Qwen3.8 and faster
GPT-OSS on the accuracy/latency frontier, while Qwen alone survives the
accuracy/calibration comparison. A measured GPT-OSS→Qwen cascade shows why
calibration and residency matter: the overconfident router rarely called its
worker, and exclusive model swapping made the pair slower without improving
accuracy. Compound offerings identify every component call; primitive routing
accuracy is never relabeled as completed-agent quality.

The [voice runtime experiment](examples/voice-runtime-frontiers/README.md)
extends the same rules to local speech and complete voice-agent pipelines. Its
matched M5/M1/RTX 5060 screen combines a common intelligibility measure with
audible latency and throughput. An experimental consumer-GPU patch for Nari's
Qwen3-TTS engine now reaches 47 ms median and 57 ms p95 first audible audio on
the 5060 through the shared llama-swap endpoint. A matched three-seed,
90-utterance panel leaves that Nari path and vLLM-Omni as the two exact
quality/resource tradeoffs on all four TTS frontiers; both reduce to Qwen3-TTS
in the simpler model view. The fourth frontier compares intelligibility with a
whole-service memory measure spanning cold load and the matched workload: Nari
uses 9.73 GB of combined host/GPU capacity accounting versus vLLM-Omni's 18.84
GB. MLX and LoudKit measurements remain visible but fail a strict
zero-loop/zero-token-cap gate. A separate preliminary long-form screen records
pacing and diarizer-detected speaker changes; naturalness and speaker identity
still need matched human calibration controls. A paired replay of the two
winners finds a clear 513–517 ms Nari latency advantage, while the measured
0.71-point WER difference remains unresolved.

Its [local speech-recognition frontiers](examples/voice-runtime-frontiers/ASR.md)
now compare four model families on the same 24 public utterances across M1,
M5, and RTX 5060 Ti runtimes. Parakeet TDT 0.6B v3 is the combined
quality/speed point winner; Qwen3-ASR 0.6B 8-bit joins it on the Mac memory
frontier. On the 5060, a compiled Parakeet encoder turns the two-model baseline
frontier into one point-estimate resident. A separate quality-versus-restart
frontier shows the opposite deployment tradeoff: compiled Qwen takes about 43
seconds to return its first transcript from a fresh process even with seeded
compiler artifacts, while uncompiled 5060 offerings take 3.4–4.6 seconds and
M5 MLX offerings take 0.8–1.0 seconds. A second quality-versus-ready-runner
frontier times the narrower model-cold step after framework/device
initialization; it is not relabeled as a measured router swap. The file also
records why this small-panel result is provisional, why compile warmup changes
router policy, why M1-to-M5 speed has no universal multiplier, and why unlike
memory statistics are not mixed.

## What a frontier means

1. Name the job, such as coding-agent tasks or long-context retrieval.
2. Choose exactly two quantities to compare and whether higher or lower is
   better.
3. Reject candidates that fail must-haves such as correct tool calls or a
   required context length.
4. Keep the models that are not beaten on both quantities.

For example, an intelligence-versus-speed frontier can retain a slower model
that solves more tasks and a faster model that solves fewer. A model that is
both slower *and* solves fewer tasks is not on that frontier.

The model-focused publication can show two views of the same frontier:

- **Best available:** a model appears when one real implementation is on the
  exact frontier. This answers “what is the best this model can currently do?”
  and the drill-down also identifies the provider, runtime, or hardware needed
  to obtain it.
- **Balanced average:** both quantities are aggregated across the same declared
  provider or hardware panel for every model. This is a useful model-level
  heuristic, especially when direct evidence for a reader's environment is
  missing.

For a reader locked to one provider or machine, ModelSkyline can filter to that
environment first and calculate the same two-axis frontier. That is preferable
to an average when enough direct measurements exist.

Every view names its reduction rule. The best-available view keeps a real
tested point; it never constructs a fictional point by borrowing quality from
one implementation and speed or price from another. The average view requires
a balanced comparison and publishes its aggregation rule. Otherwise “average”
would reward whichever model happened to receive easier or newer test
conditions.

## The core path

```text
versioned workload + offering observations
                    |
                    v
       two declared metrics and goals
                    |
                    v
          auditable Pareto frontier
                    |
                    v
         default + ordered fallbacks
```

An *offering* is the exact implementation behind a model result: provider,
endpoint, hardware, quantization, runtime, service tier, reasoning settings,
and agent harness where applicable. Readers can start with the model name;
these details explain and reproduce its measured point. A workload and its
unit are also explicit. Missing, stale, non-finite, or unit-incompatible
evidence is rejected with a reason instead of being converted to zero.

Frontier axes may use direct observations, restricted Decimal formulas, or a
host-registered oracle. Typical pairs include total cost per successful coding
issue versus solve rate, or time to first token versus research quality.
Additional measured requirements can remain non-axis eligibility gates. Declare
their metric thresholds with `minimum_gate_values` or `maximum_gate_values`;
they affect admission and rejection reasons without entering Pareto dominance.
Under robust uncertainty, minimum gates use the lower bound and maximum gates
use the upper bound; both bounds are required. A missing or ineligible gate
observation rejects that offering.

## Quickstart

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are recommended.

```console
uv sync --extra dev
uv run modelskyline validate \
  examples/real-agent-value/frontier.yaml \
  examples/real-agent-value/observations.json
```

Evaluate the example cost/quality frontier:

```console
uv run modelskyline evaluate \
  examples/real-agent-value/frontier.yaml \
  examples/real-agent-value/observations.json \
  agent-value \
  --as-of 2026-09-01T02:00:00Z
```

Build the agent selection declared in the same configuration:

```console
uv run modelskyline select \
  examples/real-agent-value/frontier.yaml \
  examples/real-agent-value/observations.json \
  agent-defaults \
  --as-of 2026-09-01T02:00:00Z \
  --output selection.json
```

The fixed time deterministically replays a dated, payload-free real workload
aggregate and committed public price snapshot; it does not re-fetch mutable
upstream data. The two-offering candidate set and synthetic quality ordinals are
regression evidence, not a current market recommendation; see its
[data card](examples/real-agent-value/DATA_CARD.md).
Production inputs normally use current observation timestamps and omit
`--as-of`. The selection is an immutable control-plane artifact; an agent or
gateway must pin one resolved selection for the complete work unit rather than
re-resolving on every turn.

Run `uv run modelskyline --help` to see commands for the core workflow,
telemetry, data sources, source monitoring, quality evidence, publication, and
contracts.

### Discovery admission policies

Discovery does not rank offerings and does not alter the `evaluate` or `select`
JSON contracts. To record different admission rules for different frontiers,
pass a strict JSON policy file:

```console
cat > frontier-policies.json <<'JSON'
{"frontiers": {"agent-value": "require_quality", "experimental": "allow_catalog_only"}}
JSON
uv run modelskyline discover --frontier-policy-file frontier-policies.json \
  --output discovery.json
```

The supported policies are `require_quality` (exclude offerings without
evaluation quality evidence), `allow_catalog_only` (admit catalog-verified
offerings only), `allow_vendor_reported` (also admit vendor-reported
offerings), and `mark_unverified` (admit all discovered offerings as
unverified). Every decision is retained under `frontier_admissions`, including
an explicit exclusion reason. Weaker-evidence admissions carry
`uncertainty_marker: true` and an admission value ending in `*` in that
frontier's decision; the same offering can therefore be admitted by one
frontier and excluded by another. Catalog identity is never treated as an
evaluation result. The file is data-only JSON: arbitrary
code, plugins, and executable policy are not supported.

### Day-one provisional evidence catalog

A discovery run can also emit a separate, explicitly non-mature artifact:

```console
uv run modelskyline discover --provisional-catalog-output provisional.json \
  --output discovery.json
```

`provisional.json` is
`model-skyline/provisional-evidence-catalog/v1alpha1`. It retains
separate offering identities (including batch and contributor variants) and
copies launch-day catalog signals such as exact OpenRouter input/output/cache
prices, context length, and any explicitly supplied aggregator telemetry. Each
signal has an evidence label: `catalog_verified`, `vendor_evaluated`,
`independent_non_comparable`, `independent_comparable`, or
`aggregator_telemetry`. No absent quality is converted to zero. Named published
benchmark results may be supplied as a strict JSON array with
`--provisional-benchmarks`; every row must include offering id, benchmark,
methodology, score, source URL, and its evidence label.

This is a separate discovery inventory, not an `ObservationCatalog`, Pareto
frontier, or selection input. Its records carry
`mature_evaluation_eligible: false` and
`selection_eligible: false`; `evaluate` and `select` never read it. Vendor and
independent benchmark evidence can therefore be useful with an uncertainty
marker and provenance without weakening `require_quality` or promoting vendor
claims to an independent score.

## Small root API

The package root intentionally exposes only the common calculation path:

```python
from model_skyline import (
    FrontierEngine,
    FrontierSnapshot,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    ProjectConfig,
    SelectionSnapshot,
    select_models,
)
```

`model_skyline.__version__` is also public. Advanced APIs remain available from
explicit modules such as `model_skyline.quality_evidence`,
`model_skyline.quality_portfolio`, `model_skyline.traces`,
`model_skyline.local_measurements`, `model_skyline.catalog_composition`,
`model_skyline.model_views`, `model_skyline.publisher`, and
`model_skyline.resolver`.

## Current boundaries

- `real-agent-value` uses an authorized aggregate but a deliberately narrow,
  historical candidate fixture and synthetic quality axis; `coding-session`
  remains fully synthetic.
- Upstream adapters accept only their documented source versions and evidence
  shapes; validation status varies by adapter.
- Benchmark display names are never fuzzy-matched to routable offerings.
- A content hash detects mutation but does not authenticate who chose a policy.
- Public publication requires explicit source authorization and a separate
  privacy and rights review.
- The convenience resolver is for a trusted local file or trusted HTTPS
  origin. Version 0.9 does not ship signed remote distribution or durable
  anti-rollback state.
- Quality portfolios gate coverage and enrich an ordinary observation catalog;
  they do not prove statistical independence or create a universal quality
  score. Any scalar composite remains explicit operator policy in a core
  `FormulaMetric`.

## Where to go next

- [Architecture and data semantics](docs/architecture.md)
- [Agent-framework telemetry adapters](docs/framework-integrations.md)
- [Pricing and cache-aware formulas](docs/models-dev-pricing.md)
- [Benchmark evidence and quality portfolios](docs/quality-portfolios.md)
- [Small, realistic evaluation candidates](docs/small-realistic-evaluations.md)
- [Same- and cross-workload catalog composition](docs/catalog-enrichment.md)
- [Best-available, average, and environment-specific model views](docs/model-level-frontiers.md)
- [Efficient subset estimation and quantization quality](docs/efficient-quality-estimation.md)
- [Local quality, power, and energy frontiers](docs/intelligence-efficiency.md)
- [Local runtime measurement and frontier example](examples/local-runtime-frontiers/README.md)
- [Voice runtime and agent frontier experiment](examples/voice-runtime-frontiers/README.md)
- [Runtime and gateway integration options](docs/gateway-integrations.md)
- [Research, sources, and prior art](docs/research.md)
- [Security policy](SECURITY.md)

## Development

```console
uv sync --extra dev
uv run ruff check src tests
uv run mypy src
uv run pytest
```

The package has not been published to a registry. To install the CLI from a
source checkout, run `uv tool install .`.

## License

MIT. Upstream datasets and APIs retain their own licenses and terms; generated
artifacts must preserve source provenance.
