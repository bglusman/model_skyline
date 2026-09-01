# ModelSkyline

ModelSkyline calculates workload-specific, two-objective Pareto frontiers over
model offerings and turns a frontier into an ordered default-and-fallback
selection for agents.

> **Status:** working alpha. The core catalog → frontier → selection path is
> implemented and tested. The repository also contains advanced benchmark,
> telemetry, quality-composition, publication, and signed-gateway components,
> but several are version-pinned or contract-only integrations. There is no
> hosted service or package-registry release yet, and alpha schemas may change.

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

The fixed time makes a dated, de-identified real workload aggregate and exact
public price snapshot reproducible. The two-offering candidate set and
operator-entered quality values are regression evidence, not a current market
recommendation; see its [data card](examples/real-agent-value/DATA_CARD.md).
Production inputs normally use current observation timestamps and omit
`--as-of`. The selection is an immutable control-plane artifact; an agent or
gateway must pin one resolved selection for the complete work unit rather than
re-resolving on every turn.

Run `uv run modelskyline --help` to see the unchanged commands grouped as core
workflow, frontier composition, telemetry, gateway, data sources, source
monitoring, quality evidence, and contracts.

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
`model_skyline.traces`, `model_skyline.publisher`, and
`model_skyline.gateway`; pruning eager root imports does not remove them.

## Current boundaries

- `real-agent-value` uses an authorized aggregate but a deliberately narrow,
  historical candidate/quality fixture; `coding-session` remains synthetic.
- Upstream adapters accept only their documented source versions and evidence
  shapes; validation status varies by adapter.
- Benchmark display names are never fuzzy-matched to routable offerings.
- A content hash detects mutation but does not authenticate who chose a policy.
- Public publication requires explicit source authorization and a separate
  privacy and rights review.
- The signed gateway profile authenticates ordinary selections, but native
  framework consumers and broader quality-gated gateway support remain work in
  progress.

## Where to go next

- [Architecture and data semantics](docs/architecture.md)
- [Agent-framework telemetry adapters](docs/framework-integrations.md)
- [Pricing and cache-aware formulas](docs/models-dev-pricing.md)
- [Benchmark evidence and composite quality](docs/quality-oracle.md)
- [Gateway protocol and integration options](docs/gateway-integrations.md)
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
