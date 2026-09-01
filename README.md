# ModelSkyline

ModelSkyline is a workload-aware model-selection control plane. It calculates
an auditable two-objective Pareto frontier over **model offerings** (model plus
provider, endpoint, region, tier, and configuration), publishes immutable
artifacts, and turns a frontier into a current agent default with ordered
fallbacks.

> **Status:** working alpha. Frontier evaluation, failure/cache-aware trace
> aggregation, selection artifacts, retained RSS publication, an in-process
> resolver, pinned benchmark imports, exact models.dev price projections, and
> strict Codex, Claude, OpenClaw, and Hermes telemetry adapters are implemented.
> A signed, durable, fail-closed
> gateway selection protocol is available as a `v1alpha1` contract with Python
> reference resolver and language-neutral conformance vectors. Exact normalized
> quality evidence/reconciliation, two-to-four benchmark coverage bundles, and a
> live-tested Harbor Terminal-Bench adapter, pinned SWE-bench bash-only
> collector, pinned ARC-AGI-2 public-evaluation collector, and semantic
> SWE-bench feed monitor are also implemented. Multi-frontier
> overlap/proximity now has hash-bound library, JSON Schema, and CLI paths; a
> bundle-bound artifact gates and recomputes every participating frontier before
> `DynamicResolver` exposes its default and fallbacks. `publish-project`, the
> signed gateway profile, and native framework-side consumers do not yet publish
> or authenticate that wrapper. Schemas may change during the alpha.
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

Published benchmark results normally enter as workload-bound signals; an
`OracleMetric` is reserved for a trusted host-run evaluator or judge and is
library-embedding-only; a remote oracle protocol is still deferred. The
v1alpha1 quality-evidence contract separates raw, source, subject, result, and
rights identities before an exact reviewed reconciliation to a complete
offering. A projection from an upstream model label may carry only explicitly
typed quality evidence; source cost, latency, and token fields cannot silently
become production-route measurements. A quality bundle gates candidates on two
to four operator-declared benchmark frontiers without forcing them into one
average. Distinct component IDs and snapshot hashes do not prove statistical
independence; operators must not duplicate one benchmark under several IDs.
The recommended target general-agent policy uses two to four distinct signals,
typically fixed-harness SWE-bench, Terminal-Bench/Harbor, and tau2-bench, with
ARC-AGI-2 as an optional abstract-reasoning component. Harbor, the pinned
SWE-bench bash-only mini-SWE-agent v2 cohort, and a pinned ARC-AGI-2
public-evaluation cohort are implemented and live-tested; tau2-bench remains an
operator-supplied input until its dedicated collector ships.
Leaderboard names are never fuzzy-matched to routable offerings. See
[ADR 0004](docs/adr/0004-quality-evidence-and-benchmark-bundles.md). The first
live implementation is the fail-closed
[Harbor Terminal-Bench adapter](docs/harbor-terminal-bench.md), followed by the
[SWE-bench bash-only collector](docs/swe-bench.md) and the conservative
[ARC-AGI-2 collector](docs/arc-agi.md).

For operators who need a single quality axis, the opt-in
[`QualityOraclePolicy`](docs/quality-oracle.md) combines exactly two to four
fully measured components using self-hashed fixed normalization references and
Decimal weights. Separate frontiers remain the default: the scalar is a new
versioned composite workload, rejects missing/out-of-range evidence, discloses
correlation groups without claiming independence, and must be replayed against
its trusted policy, exact bundle, and current time before use.

Prices and usage are not one indivisible "cost" field. Input, output,
cache-read, cache-write, request, tool, and other rates and quantities should
be separate timestamped, source-attributed observations. A formula records the
exact signal paths it evaluated, so a changed cache rate does not change a
cache-free formula, while a missing or stale rate that the formula actually
uses makes that offering ineligible. Frontier policy can impose both general
metric freshness and stricter per-source freshness. Artifact identity still
binds the complete catalog and frontier, but the v0.8 per-axis evidence
inventory prevents a missing, stale, or changed companion price from erasing a
still-valid benchmark measurement. Quality bundles require that inventory and
use exact complete offerings from it, including routes rejected only on the
companion axis. The complete input catalog remains bound for auditability; see the
[models.dev accounting guide](docs/models-dev-pricing.md#dependency-and-invalidation-model)
for the important distinction between value dependencies and provenance
changes.

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

For a quality-gated selection, always pin the stable bundle ID as well:

```python
resolver = DynamicResolver(
    "https://control.example/selections/coding-quality/latest.json",
    expected_selection_id="coding-agent-defaults",
    expected_frontier_id="coding-value",
    expected_workload_id="coding-session-v1",
    expected_workload_version="1.0.0",
    expected_quality_bundle_id="general-agent-quality",
)
selection = resolver.resolve()
```

That pin rejects a downgrade to an ordinary single-frontier selection. The
bundle ID authorizes versions of that operator policy to advance. Set
`expected_quality_bundle_version` and
`expected_quality_bundle_policy_hash` as additional exact pins when automatic
policy evolution is not desired. Within one process, the resolver rejects
selection and bundle-generation rollback or same-generation equivocation; that
floor is not durable across restarts. Replay the exact policy and sources with
`verify_quality_gated_selection_snapshot` whenever the consumer owns them.
Unlike ordinary convenience selections, a quality-gated artifact fails hard at
its earliest benchmark, primary, secondary, or nested-selection deadline;
`stale_if_error` cannot extend it. The example URL represents an operator-owned,
atomically updated trusted channel—`publish-project` does not create it yet.

For an untrusted distribution path or an invisible gateway-side logical model,
the signed gateway profile authenticates ordinary single-frontier selections.
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

Gateway-pointer v1alpha1 accepts only `kind: "selection"`; it does not yet
authenticate `kind: "quality-gated-selection"`. Until the signed profile and
publisher are extended, distribute quality-gated artifacts only through a
trusted channel, pin `expected_quality_bundle_id`, and do not infer durable
anti-rollback protection from their content hashes.

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

For selection across several exact frontier snapshots, the library and CLI can
build content-addressed proximity sidecars and apply ordered overlap/proximity
priority groups. Exact-membership count, near-only membership, and per-frontier
Decimal distance precede the original primary order. Complete `OfferingKey`
equality prevents an upstream model label from crossing provider, billing,
region, tier, or harness boundaries. See
[`ADR 0002`](docs/adr/0002-multi-frontier-overlap-and-proximity.md).

The quality-bound path makes the ordering operational: it first requires an
explicit measured/missing/quarantined record for every primary candidate,
removes hard-ineligible routes, recomputes Pareto membership and proximity on
the primary and every bound secondary frontier, and only then chooses the
default and fallbacks. Start with the runnable, network-free
[`examples/quality-gated`](examples/quality-gated/) three-benchmark example; it
constructs every exact binding in code, proves a missing benchmark excludes one
route, and fully replays the result. For operator-produced artifacts, a typical
command sequence is:

```console
modelskyline build-frontier-proximity swe.json -o swe-proximity.json
modelskyline build-frontier-proximity harbor.json -o harbor-proximity.json
modelskyline build-quality-bundle quality-policy.json \
  --component-frontier swe=swe.json \
  --component-frontier harbor=harbor.json \
  --candidate-frontier economic-primary.json \
  -o quality-bundle.json
modelskyline select-quality-gated frontier.yaml economic-primary.json \
  quality-policy.json quality-bundle.json overlap-policy.json \
  coding-agent-defaults \
  --secondary-frontier swe.json --proximity swe-proximity.json \
  --secondary-frontier harbor.json --proximity harbor-proximity.json \
  -o quality-selection.json
modelskyline verify-quality-gated-selection frontier.yaml \
  quality-policy.json quality-bundle.json economic-primary.json \
  quality-selection.json overlap-policy.json coding-agent-defaults \
  --component-frontier swe=swe.json \
  --component-frontier harbor=harbor.json \
  --secondary-frontier swe.json --proximity swe-proximity.json \
  --secondary-frontier harbor.json --proximity harbor-proximity.json
```

The policy files deliberately bind exact snapshot and sidecar hashes, so they
are authored after those inputs exist. Add a third tau2 component for the
recommended general-agent profile and the pinned or operator-produced
ARC-AGI-2 component only when its distinct workload is material. The CLI bundle
builder currently
emits measured or missing coverage; independently sourced quarantine records
and their provenance use the library API.

Quality bundle, scalar-oracle, and oracle-enriched catalog files are private by
default: file outputs are mode `0600` on POSIX and refuse replacement without
`--overwrite`. Full frontier snapshots also contain the exact per-axis evidence
inventory, including complete routes and successful partial values for rejected
candidates. A source catalog marked `publication_safe: false` produces a
durable `public_release_blocked` frontier; `publish-project --public` will not
override it with license or source flags. Public release requires a separate
reviewed publication projection.

Source-owning consumers should pin the expected selection and bundle policy,
then run `verify-quality-gated-selection` or call
`verify_quality_gated_selection_snapshot` with every exact source artifact and
a trusted current time. The selection builder itself source-replays every
positive measured-coverage claim against its bound component frontier before
routing; the full verifier additionally replays the independently supplied
candidate universe and quarantine records. Both reject expiry, excessive
future skew, omitted candidates or components, and different source inputs. A
matching content hash detects mutation but does not authenticate who chose the
policy. `publish-project` and the signed gateway profile do not yet emit or
authenticate this additive artifact.

## Run with pinned real benchmark data

Custom/local benchmark adapters can emit the language-neutral quality evidence
and reviewed reconciliation contracts, then use the generic fail-closed join:

```console
uv run modelskyline reconcile-quality-evidence \
  normalized-evidence.json reviewed-reconciliation.json \
  --publication-scope internal \
  --output import-report.json
```

The live collector integrations include a local, separately captured Harbor
Terminal-Bench response, an immutable official SWE-bench website snapshot, and
32 result summaries at one immutable ARC-AGI-2 Hugging Face revision. They
produce route-free evidence and require exact reviewed offering mappings;
neither public collector attributes historical benchmark cost fields to
production routes. See the
[Harbor workflow and its cost/cache caveats](docs/harbor-terminal-bench.md) and
the [SWE-bench](docs/swe-bench.md) and [ARC-AGI-2](docs/arc-agi.md) capture
guides.

```console
uv run modelskyline capture-swe-bench-bash-only ./swe-bench-capture
uv run modelskyline capture-arc-agi-2-public-eval ./arc-agi-2-capture
uv run modelskyline check-swe-bench-feed
uv run modelskyline check-arc-agi-2-feed
```

Both public feeds have scheduled drift monitors. SWE-bench classifies raw,
result, subject, row-set, and semantic-source changes; ARC-AGI-2 treats every
dataset-head change as a manual adapter-review event and never repins itself.

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

The Aider import is fail-closed: its pinned host is allowed by default,
redirects are refused, and a custom Aider remote requires an explicit repeated
`--allow-host`. Local files need no network allowlist. The models.dev adapter
below has a narrower remote policy: it accepts only the exact official API URL.

For a price-snapshot reconstruction, combine reviewed exact Aider routes with
a fresh or locally pinned models.dev snapshot:

```console
uv run modelskyline project-aider-models-dev \
  ./aider-gpt5-price-snapshot \
  examples/mappings/aider-gpt5-models-dev.json
uv run modelskyline evaluate \
  ./aider-gpt5-price-snapshot/frontier.yaml \
  ./aider-gpt5-price-snapshot/observations.json \
  price-snapshot-cost-per-attempted-vs-solve-rate
```

This separate, price-only project prices aggregate Aider prompt tokens as
ordinary uncached input and completion tokens as output. It is labeled
reconstructed token marginal cost, not a current provider bill, total
infrastructure cost, cache-aware estimate, or current-quality claim. Pricing
freshness can invalidate these projection frontiers without invalidating the
independent historical Aider project. Mappings are exact, reviewed, and
command-digest-bound; compound runs and context-tiered price cards fail closed.
The price observations cite a selected-price semantic source, while
`projection.json` and offering metadata separately preserve the complete
pricing-catalog digest. An unused cache rate or unselected catalog record can
therefore rotate the immutable catalog/snapshot identity without changing the
frontier configuration or ordered view, or adding an RSS item; a used
input/output rate or selected status/reasoning-compatibility change rotates the
semantic price source and projected workload. See
[`docs/models-dev-pricing.md`](docs/models-dev-pricing.md) for the accounting,
dependency, freshness, cache, provenance, and automation boundaries.

The daily/manual models.dev Pages workflow automatically advances these three
research frontiers and retains each exact five-file projection bundle under a
separate content-addressed evidence tree. Those static aliases do not enforce
the 48-hour source limit and the workflow emits no agent selection: verify
watermarks for research, and use the separately signed, hard-TTL gateway
protocol before routing.

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

The versioned trace contracts and pinned adapters cover Codex `exec --json`,
Claude Agent SDK final result messages, signed OpenClaw model-call projections,
and Hermes usage reports or ledger-complete v26 sessions. Codex, Claude, and
Hermes emit retained v1alpha2 rows; OpenClaw emits v1alpha3 because its logical
`model_call` scope cannot truthfully claim one provider request. The adapters
retain failures, preserve unknown rather than inventing zero, and require
explicit route/outcome attestations for fields the upstream framework does not
expose. Raw prompts, responses, tool payloads, paths, and credentials are
deliberately outside the adapter outputs. See
[`docs/framework-integrations.md`](docs/framework-integrations.md) for exact
supported versions, limitations, and examples.

See `docs/architecture.md` for semantics and `docs/research.md` for prior art,
data sources, workload evidence, licenses, and integration recommendations.

## License

MIT. Upstream datasets and APIs retain their own licenses and terms; generated
artifacts must preserve source provenance.
