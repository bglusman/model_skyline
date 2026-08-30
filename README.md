# ModelSkyline

ModelSkyline is a workload-aware model-selection control plane. It calculates
an auditable two-objective Pareto frontier over **model offerings** (model plus
provider, endpoint, region, tier, and configuration), publishes immutable
artifacts, and turns a frontier into a current agent default with ordered
fallbacks.

> **Status:** working alpha. Frontier evaluation, trace aggregation, selection
> artifacts, an in-process resolver, and pinned Aider and MCPMark benchmark
> adapters are implemented. A scheduled publisher, effective-dated price cards,
> and native runtime clients are next. Schemas may change during the alpha.
> “ModelSkyline” is a working name chosen to avoid collision with several
> existing projects called Pareto Router.

## Why another model selector?

Most catalogs compare token price or a global benchmark score. Real agent work
is shaped by repeated requests, cache reads and writes, cache retention,
reasoning/output length, tool charges, retries, failures, provider routing, and
the agent harness itself. ModelSkyline makes the workload and both competing
objectives explicit and versioned.

A frontier may compare any two declared metrics, for example:

- total USD per successful coding issue versus issue-resolution rate;
- p95 time to first token versus judged research quality;
- total session cost versus tool-task success;
- output tokens per second versus cache-adjusted cost per work unit.

Metrics can be direct observations, restricted formulas, or results from a
versioned oracle. Missing, stale, unit-incompatible, or non-finite values are
excluded with reasons rather than silently imputed.

## Architecture

```text
benchmarks + price cards + traces + provider telemetry
                         |
                         v
              canonical observations
                         |
                         v
       metrics -> eligibility -> 2-D frontier snapshot
                         |
              +----------+-----------+
              |                      |
       JSON / table / RSS      selection snapshot
                                      |
                         agent default + fallbacks
```

The Python package is the ecosystem-facing control/data plane. JSON Schema and
immutable JSON snapshots are the interoperability boundary; agent runtimes do
not need to embed Python.

Python is a conditional choice, not an assumption: Pydantic, DuckDB/Parquet,
and the evaluation ecosystem justify it for collectors and analysis. The real
benchmark adapters exercise that ecosystem-facing role; JSON Schema and
immutable artifacts remain the boundary. Runtime clients should remain native
to their agent frameworks, beginning with TypeScript.

## Install from a source checkout

The project has not been published to a package registry yet.

```console
uv tool install .
modelskyline --version
```

## Development quickstart

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are recommended.

```console
uv sync --extra dev
uv run pytest
uv run modelskyline --help
```

An executable example is in `examples/coding-session/`.

```console
uv run modelskyline evaluate \
  examples/coding-session/frontier.yaml \
  examples/coding-session/observations.json \
  coding-value \
  --as-of 2026-08-29T19:00:00Z
```

The fixed `--as-of` makes the dated synthetic fixture reproducible. Production
runs normally omit it. Add `--format json`, `csv`, or `rss`; JSON is the public
contract.

Publish a default and ordered fallbacks from the same frontier:

```console
uv run modelskyline select \
  examples/coding-session/frontier.yaml \
  examples/coding-session/observations.json \
  coding-agent-defaults \
  --as-of 2026-08-29T19:00:00Z \
  --output selection.json
```

An agent resolves once at the beginning of a work unit and retains that
snapshot throughout the trajectory:

```python
from model_skyline import DynamicResolver

resolver = DynamicResolver(
    "https://control.example/selections/coding-agent-defaults/latest.json",
    expected_selection_id="coding-agent-defaults",
    expected_frontier_id="coding-value",
    expected_workload_id="coding-session-v1",
    expected_workload_version="1.0.0",
)
selection = resolver.resolve()  # pin this object for the whole work unit
default = selection.default.offering
fallbacks = [choice.offering for choice in selection.fallbacks]
```

## Run with pinned real benchmark data

The Aider adapter downloads an immutable Apache-2.0 Polyglot leaderboard file,
verifies its SHA-256, rejects incomplete, unpriced, zero-cost, incoherent, and
dirty-harness rows, and writes a normal ModelSkyline project:

```console
uv run modelskyline import-aider-polyglot ./aider-real
uv run modelskyline evaluate \
  ./aider-real/frontier.yaml \
  ./aider-real/observations.json \
  cost-per-attempted-vs-solve-rate
```

This is a mixed historical leaderboard comparison, not a controlled same-date
provider experiment, current price, or availability claim. Aider versions,
edit formats, and run conditions vary. Its aggregate cost does not expose cache
meter splits or non-model infrastructure charges, and its reported
`seconds_per_case` excludes unit-test execution. The adapter therefore labels
that metric as agent edit/generation time and preserves all caveats in the
generated project and manifest.

Remote imports are fail-closed: the pinned host is allowed by default, redirects
are refused, and a custom remote requires an explicit repeated `--allow-host`.
Local files need no network allowlist.

MCPMark Verified demonstrates workload-dependent agent behavior across
filesystem, GitHub, Notion, Playwright, and Postgres tasks:

```console
uv run modelskyline import-mcpmark-verified ./mcpmark-real
uv run modelskyline evaluate \
  ./mcpmark-real/frontier.yaml \
  ./mcpmark-real/observations-github.json \
  github-quality-time
```

Its experiment-results repository had no declared license at the pinned
revision, so no results are vendored and generated outputs should be treated as
locally analyzed data, not redistributed. It deliberately emits no cost:
the source lacks provider route and cache telemetry, and inventing those would
turn a useful benchmark into a misleading bill estimate.

Both adapters use point estimates for frontier membership. Their Wilson bounds
are descriptive binomial reference intervals under an IID task-sampling
assumption; they are not measurements of run-to-run or serving variance.

See `docs/architecture.md` for semantics and `docs/research.md` for prior art,
data sources, workload evidence, licenses, and integration recommendations.

## License

MIT. Upstream datasets and APIs retain their own licenses and terms; generated
artifacts must preserve source provenance.
