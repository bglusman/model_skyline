# ModelSkyline

ModelSkyline is a workload-aware model-selection control plane. It calculates
an auditable two-objective Pareto frontier over **model offerings** (model plus
provider, endpoint, region, tier, and configuration), publishes immutable
artifacts, and turns a frontier into a current agent default with ordered
fallbacks.

> **Status:** working alpha. Frontier evaluation, failure/cache-aware trace
> aggregation, selection artifacts, retained RSS publication, an in-process
> resolver, pinned benchmark imports, and strict Codex, Claude, OpenClaw, and
> Hermes telemetry adapters are implemented. A signed, durable, fail-closed
> gateway selection protocol is available as a `v1alpha1` contract with Python
> reference resolver and language-neutral conformance vectors. Multi-frontier
> overlap/proximity selection is available as a hash-bound library contract;
> publisher wiring and native framework-side consumers remain. Schemas may
> change during the alpha.
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

Or publish every matching frontier and selection as one coherent static site:

```console
uv run modelskyline publish-project \
  examples/coding-session/frontier.yaml \
  ./site \
  --project-id coding-demo \
  --catalog examples/coding-session/observations.json \
  --as-of 2026-08-29T19:00:00Z \
  --base-url https://control.example/model-skyline
```

The site contains immutable JSON, CSV, text, history, feed, selection, and
publication-manifest artifacts alongside convenient mutable `latest` and table
aliases. Readers that need a coherent multi-file view start at the root
`latest.json` manifest and follow only its digest-checked immutable references.
RSS retains meaningful changes rather than emitting a duplicate item for an
unchanged ordered view.

Public redistribution is an explicit, fail-closed mode. It requires an HTTPS
base URL and authorization for every source retained in history, either by an
allowed license or a separately documented exact source-id override:

```console
uv run modelskyline publish-project \
  examples/coding-session/frontier.yaml \
  ./public-site \
  --project-id coding-demo \
  --catalog examples/coding-session/observations.json \
  --as-of 2026-08-29T19:00:00Z \
  --base-url https://control.example/model-skyline \
  --public \
  --allow-license CC0-1.0
```

`--public` is a redistribution guard, not a privacy scanner or legal opinion.
Operators must separately remove prompts, secrets, personal data, private
endpoints, and other sensitive metadata. The output directory is a dedicated
publisher-owned namespace; see `docs/architecture.md` and `SECURITY.md` before
using it on a shared or adversarial filesystem.

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

For an untrusted distribution path or an invisible gateway-side logical model,
use the signed gateway profile instead of the unsigned convenience resolver.
It authenticates a small DSSE pointer, binds the exact publication and
selection bytes, prevents rollback with a durable SQLite checkpoint, maps only
complete offerings pre-registered in local policy, and pins one route for the
whole work unit:

```console
uv sync --extra gateway
```

```python
from pathlib import Path

from model_skyline.gateway import parse_gateway_trust_policy
from model_skyline.gateway_resolver import SignedGatewayResolver
from model_skyline.gateway_store import SqliteGatewayInstallationStore

policy = parse_gateway_trust_policy(Path("gateway-policy.json").read_bytes())
state_directory = Path("private-gateway-state")
state_directory.mkdir(mode=0o700, exist_ok=True)
with SqliteGatewayInstallationStore(state_directory / "gateway-state.sqlite3") as store:
    resolver = SignedGatewayResolver(
        "https://control.example/model-skyline/channels/coding-defaults.dsse.json",
        policy=policy,
        store=store,
    )
    route = resolver.resolve()  # retain this route for the complete trajectory
```

Verify the bundled cross-language example and print its pinned three-target
route without installing state:

```console
uv run --extra gateway modelskyline verify-gateway-bundle \
  conformance/gateway-pointer/v1alpha1/valid/envelope.dsse.json \
  conformance/gateway-pointer/v1alpha1/artifacts/publication.json \
  conformance/gateway-pointer/v1alpha1/artifacts/selection.json \
  conformance/gateway-pointer/v1alpha1/valid/trust-policy.json \
  --at 2026-08-29T19:00:00Z
```

This CLI performs static verification only. Production admission uses the
resolver plus durable store so every update is checked against an anti-rollback
floor.

The HTTP origin must serve the pointer envelope as
`application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+dsse` and
both referenced artifacts as `application/json`; filename suffixes alone are
not enough. The reference fetcher requests identity encoding and rejects
redirects, partial responses, compression, or a different media type.

Start with [ADR 0003](docs/adr/0003-signed-gateway-selection-protocol.md), the
[portable accept/reject vectors](conformance/gateway-pointer/v1alpha1/), and
the [gateway integration guide](docs/gateway-integrations.md). Signing-key
custody and publication are deliberately operator concerns; the first CLI will
be verification-only rather than accepting private key material casually.

For selection across several independently published frontiers, the library
can build a content-addressed proximity sidecar and re-rank only the exact
members of a primary frontier. Ordered priority groups compare exact overlap,
near-only overlap, and per-frontier Decimal distance before the original
primary order; complete `OfferingKey` equality prevents model aliases from
crossing provider, billing, region, tier, or harness boundaries. See
[`ADR 0002`](docs/adr/0002-multi-frontier-overlap-and-proximity.md). The current
API is the resolved exact-snapshot layer; it is not yet emitted by
`publish-project` or accepted by `DynamicResolver`.

Agent consumers must pin the expected selection ID and overlap policy from
trusted configuration, then call `verify_multi_frontier_selection_snapshot`
with the primary and secondary source artifacts and a timezone-aware trusted
current time before routing. The verifier rejects expired and implausibly
future-dated snapshots. A matching content hash detects mutation but does not
authenticate who chose the policy; see the ADR's trust-boundary section.

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

## Agent-framework telemetry

The v1alpha2 trace contract and pinned adapters cover Codex `exec --json`,
Claude Agent SDK final result messages, signed OpenClaw model-call projections,
and Hermes usage reports or ledger-complete v26 sessions. They retain failures,
preserve unknown rather than inventing zero, and require explicit route/outcome
attestations for fields the upstream framework does not expose. Raw prompts,
responses, tool payloads, paths, and credentials are deliberately outside the
adapter outputs. See [`docs/framework-integrations.md`](docs/framework-integrations.md)
for exact supported versions, limitations, and examples.

See `docs/architecture.md` for semantics and `docs/research.md` for prior art,
data sources, workload evidence, licenses, and integration recommendations.

## License

MIT. Upstream datasets and APIs retain their own licenses and terms; generated
artifacts must preserve source provenance.
