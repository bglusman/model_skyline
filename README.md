# ModelSkyline

ModelSkyline is an **evidence-to-selection compiler** for model and AI-system
deployments. Give it a versioned workload, exact deployment candidates, and
measured observations. It produces:

1. an auditable two-axis Pareto frontier; and
2. an immutable default-plus-fallback selection artifact.

It is not a model host, inference proxy, universal leaderboard, or automatic
benchmark service. It does not decide what “best” means for you.

> **Status:** working alpha. The `validate` → `evaluate` → `select` path and its
> JSON contracts are the supported kernel. One external consumer has used that
> path with real workload data. Collection adapters, publication, local-runtime
> harnesses, and research studies are useful but have narrower validation and
> should not be mistaken for the product contract. There is no hosted service
> or package-registry release, and alpha schemas may change.

## The whole product in one diagram

```text
your workload + exact offerings + observations + policy
                           |
                           v
                    validate inputs
                           |
                           v
              evaluate one two-axis frontier
                           |
                           v
               select default + fallbacks
                           |
                           v
        your gateway, agent, report, or deployment system
```

An *offering* is a deployable implementation, not just a model name. Provider,
endpoint, hardware, quantization, runtime, service tier, reasoning settings,
and harness can all change cost or behavior. ModelSkyline preserves that exact
identity and excludes missing, stale, incompatible, or invalid evidence with a
reason instead of silently filling gaps.

For the scope of each part of the repository, start with the
[project map](docs/project-map.md). For a candid assessment of what has become
too broad, see the [simplification review](docs/simplification-review-2026-09-21.md).

## Quickstart

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are recommended.

```console
uv sync --extra dev
uv run modelskyline validate \
  examples/real-agent-value/frontier.yaml \
  examples/real-agent-value/observations.json

uv run modelskyline evaluate \
  examples/real-agent-value/frontier.yaml \
  examples/real-agent-value/observations.json \
  agent-value \
  --as-of 2026-09-01T02:00:00Z

uv run modelskyline select \
  examples/real-agent-value/frontier.yaml \
  examples/real-agent-value/observations.json \
  agent-defaults \
  --as-of 2026-09-01T02:00:00Z \
  --output selection.json
```

The fixed time replays the dated example deterministically. The example uses a
real, payload-free workload aggregate but a deliberately small historical
candidate set and synthetic quality ordinals. It is regression evidence, not a
current recommendation; see its
[data card](examples/real-agent-value/DATA_CARD.md).

In production, use current observation timestamps and normally omit `--as-of`.
A consumer should pin one resolved selection for the whole work unit rather
than re-resolving in the middle of a multi-turn task.

## What a frontier answers

A frontier answers: **which candidates are not beaten on both of these declared
quantities, after the required gates are applied?**

1. Define one versioned job, such as a coding session or transcription run.
2. Choose exactly two metrics and whether each is minimized or maximized.
3. Declare eligibility gates such as minimum correctness or context capacity.
4. Compare only candidates with compatible, sufficiently fresh evidence.
5. Retain candidates for which no other candidate is at least as good on both
   axes and better on one.

This produces a defensible tradeoff set, not a universal ranking. `select`
adds the operator's ordering policy to choose a default and ordered fallbacks.

## Choose your entry point

| If you want to… | Start here | Maturity |
|---|---|---|
| Validate, compare, and select from observations you already have | This quickstart and [architecture](docs/architecture.md) | Supported alpha kernel |
| Understand the files, commands, and package boundaries | [Project map](docs/project-map.md) | Maintained orientation |
| Learn from a small complete project | [`real-agent-value`](examples/real-agent-value/) | Tutorial/regression fixture |
| Operate the existing household frontier consumer | [`subscription-relative-real`](examples/subscription-relative-real/) | Operational case study, not a generic default |
| Gather or reconcile benchmark/telemetry evidence | [Evidence and integration docs](docs/project-map.md#evidence-tooling) | Optional integrations; validation varies |
| Inspect local model, voice, or structured-decision experiments | [Examples and studies catalog](examples/README.md) | Research; conclusions are workload-specific |
| Publish static snapshots and RSS | [Architecture: publication](docs/architecture.md#publication-and-rss) | First-party extension |
| Route live inference | Consume a selection using [gateway integration guidance](docs/gateway-integrations.md) | ModelSkyline itself is not the runtime router |

Run `uv run modelskyline --help` for the complete command inventory. Most of
those commands ingest or normalize evidence; they are not steps in the core
workflow.

## Stable conceptual boundary

The kernel is intentionally small:

```text
ProjectConfig + ObservationCatalog
              -> FrontierSnapshot
              -> SelectionSnapshot
```

The package root mirrors that boundary:

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

Advanced APIs remain in explicit modules. The committed JSON Schemas are the
language-neutral integration contracts. Core command names and artifact shapes
have a real external consumer and should not be changed without migration and
deprecation support.

## Important limits

- ModelSkyline can show tradeoffs only among the candidates and evidence it was
  given. An omitted candidate cannot win.
- A two-axis frontier is deliberately legible, but it is not a general
  multi-objective optimizer. Put non-negotiable requirements in gates.
- A content hash detects mutation; it does not authenticate the policy author.
- Benchmark names never become route identities through fuzzy matching.
- Public artifacts still require privacy, licensing, and redistribution review.
- Research results in `examples/` are dated measurements, not live routing
  advice or evidence that a result generalizes to another workload.
- A compound pipeline should be treated as one offering only when it has been
  measured end to end. Component scores cannot be assembled into a fictional
  system score.

## More documentation

- [Project map and maturity boundaries](docs/project-map.md)
- [Examples and research studies](examples/README.md)
- [Architecture and data semantics](docs/architecture.md)
- [Model-level views](docs/model-level-frontiers.md)
- [Benchmark evidence and quality portfolios](docs/quality-portfolios.md)
- [Catalog composition and reviewed cross-workload enrichment](docs/catalog-enrichment.md)
- [Runtime and gateway integrations](docs/gateway-integrations.md)
- [Discovery and provisional evidence](docs/discovery.md)
- [Research sources and prior art](docs/research.md)
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
