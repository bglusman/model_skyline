# Project map

ModelSkyline contains one small product kernel, several optional evidence and
publication extensions, and a large research workspace. Those layers share
data contracts, but they do not have the same maturity or audience.

## The organizing sentence

**ModelSkyline compiles workload-bound evidence and operator policy into an
auditable frontier and a pinned selection artifact.**

If a feature does not help create, verify, publish, or consume one of those
artifacts, it is probably research adjacent to ModelSkyline rather than part of
its product surface.

## Layer 1: supported kernel

The shortest useful path is:

```text
frontier.yaml + observations.json
              |
              +-- modelskyline validate
              +-- modelskyline evaluate <frontier>
              `-- modelskyline select <selection>
```

The main inputs and outputs are:

| Object | Purpose |
|---|---|
| `ProjectConfig` | Workloads, metrics, two-axis frontiers, gates, and selection policies |
| `ObservationCatalog` | Exact offerings and workload-bound measurements with provenance |
| `FrontierSnapshot` | Members, exclusions, dominance reasons, evidence, and policy/input bindings |
| `SelectionSnapshot` | One default plus ordered fallbacks bound to an exact frontier |

The kernel lives primarily in `models.py`, `io.py`, `formula.py`, `engine.py`,
and `selection.py`. The root Python API exports only this common path. JSON
Schemas in `schemas/` are the cross-language contracts.

This layer has unit/property tests and a real external CLI/JSON consumer. It is
still alpha, but it is the part for which compatibility matters most.

## Layer 2: first-party extensions

These modules use the kernel but are not required to calculate a frontier:

| Capability | Commands/modules | Role |
|---|---|---|
| Static publication | `publish-project`, `publisher.py`, RSS/renderers | Publish a coherent, immutable project snapshot |
| Trusted consumption | `resolver.py` | Resolve a pinned selection from a trusted file or HTTPS origin |
| Catalog composition | `compose-catalogs`, `enrich-catalog-across-workloads` | Join exact same-workload data or explicitly authorized cross-workload gates |
| Model-focused views | `model-frontier-view`, `model_views.py` | Reduce offering results without inventing a composite implementation |
| Discovery | `discover`, `discovery.py` | Produce a review inventory; it does not rank or select |

These are maintained first-party features. They have stronger contracts than a
one-off experiment but less consumer evidence than the kernel.

## Evidence tooling

The remaining top-level commands mostly acquire, normalize, reconcile, or
monitor evidence:

- local runtime/capacity/power measurements;
- Codex, Hermes, Claude, and OpenClaw trace projections;
- Aider, MCPMark, SWE-bench, ARC-AGI, Harbor, and models.dev adapters; and
- quality reconciliation, paired estimates, and benchmark portfolios.

This tooling is valuable when its exact source and harness match your need. It
is not a mandatory ModelSkyline workflow, a generic benchmark runner, or a
promise that all adapters have equal operational validation. Start at the
specific document for the adapter and preserve its pinned source, methodology,
time, rights, and identity constraints.

The current flat CLI makes this layer look larger and more central than it is.
For compatibility, command names remain unchanged in the current major
version. A future major version can group them under namespaces such as
`evidence`, `catalog`, and `publish` while retaining aliases through a
deprecation window.

## Layer 3: examples and research studies

`examples/` serves three distinct purposes:

1. small tutorial and regression fixtures;
2. an operational household case study; and
3. dated research programs with raw captures and bespoke runners.

Only the first category is an onboarding example. The latter two are evidence
and research records, not product defaults. See the
[examples catalog](../examples/README.md) before copying anything from them.

The public GitHub Pages output is likewise a research publication. It shows
what the machinery can preserve and render, but its candidate set, freshness,
hardware, prices, and household workloads do not make it universal advice.

## Where compound systems fit

A model, deterministic rule, retriever, verifier, tool executor, or human gate
can be a component of a larger control graph. ModelSkyline should not infer the
quality of that graph from component scores.

Use two separate levels of evidence:

- **component workload:** measure a typed decision, classification, retrieval,
  or tool-call component on a fixed case set; and
- **system workload:** measure the complete pipeline's verified outcome,
  latency, cost, safety failures, and resource use end to end.

The complete measured pipeline may then be represented as an offering. Its
identity must include the topology, component versions, routing policy,
thresholds, runtime placement, and harness. It earns a deployment claim only
when it improves an end-to-end outcome or constraint; a faster classifier in
isolation is not enough.

## What ModelSkyline intentionally does not own

- serving models or proxying inference;
- executing fallbacks or retries at request time;
- inventing a universal quality score;
- silently matching benchmark display names to deployments;
- continuously benchmarking every model;
- deciding privacy, licensing, or operational authority policy; or
- turning vendor claims into measured evidence.

Existing gateways, harnesses, provider catalogs, and benchmark frameworks
should be integrated at explicit artifact boundaries rather than reimplemented.

## Repository guide

| Path | Read it when… |
|---|---|
| `README.md` | You need the product definition and shortest runnable path |
| `docs/architecture.md` | You need exact semantics and invariants |
| `schemas/` | You are implementing a non-Python producer or consumer |
| `src/model_skyline/` | You are changing the kernel or an extension |
| `src/model_skyline/adapters/` | You are maintaining one pinned upstream projection |
| `examples/README.md` | You are choosing a tutorial, case study, or research program |
| `docs/research.md` | You need dated prior-art and data-source research |
| `docs/adr/` | You need historical decisions, including ideas later removed |
| `HANDOVER.md` | You are operating the existing household consumer deployment |

## Adding work without widening the product by accident

Before adding an adapter, harness, or model study, answer:

1. Which user decision will the result change?
2. Is this a kernel feature, reusable extension, or dated research artifact?
3. Why can the existing generic catalog/observation contract not express it?
4. What exact source, version, workload, harness, and rights metadata bind it?
5. What proves it works beyond its own synthetic fixtures?
6. What will be frozen, retired, or replaced if this becomes the preferred
   path?

If those answers are missing, keep the work in a dated research intake rather
than expanding the package API or top-level CLI.
