# ModelSkyline

ModelSkyline calculates workload-specific, two-objective Pareto frontiers over
model offerings and turns a frontier into an ordered default-and-fallback
selection for agents.

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

An offering is narrower than a model name: provider, endpoint, region, service
tier, quantization, reasoning configuration, and agent harness can all affect
price or performance. A workload and its unit are also explicit. Missing,
stale, non-finite, or unit-incompatible evidence is rejected with a reason
instead of being converted to zero.

Frontier axes may use direct observations, restricted Decimal formulas, or a
host-registered oracle. Typical pairs include total cost per successful coding
issue versus solve rate, or time to first token versus research quality.

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

### Day-one/provisional frontier view

A discovery run can also emit a separate, explicitly non-mature artifact:

```console
uv run modelskyline discover --provisional-output provisional.json \
  --output discovery.json
```

`provisional.json` is `model-skyline/provisional-frontier/v1alpha1`. It retains
separate offering identities (including batch and contributor variants) and
copies launch-day catalog signals such as exact OpenRouter input/output/cache
prices, context length, and any explicitly supplied aggregator telemetry. Each
signal has an evidence label: `catalog_verified`, `vendor_evaluated`,
`independent_non_comparable`, `independent_comparable`, or
`aggregator_telemetry`. No absent quality is converted to zero. Named published
benchmark results may be supplied as a strict JSON array with
`--provisional-benchmarks`; every row must include offering id, benchmark,
methodology, score, source URL, and its evidence label.

This is a separate discovery view, not an `ObservationCatalog`, mature frontier,
or selection input. Its records carry `mature_evaluation_eligible: false` and
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
`model_skyline.publisher`, and `model_skyline.resolver`.

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
