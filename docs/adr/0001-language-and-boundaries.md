# ADR 0001: Python control plane, protocol-defined runtimes

- Status: accepted for `v1alpha1`, with an integration checkpoint
- Date: 2026-08-29

## Context

The project needs both data-heavy evaluation/control-plane work and a small
runtime default/fallback resolver. Python, Go, Rust, and TypeScript were
considered. The first prototype used only the Python standard library, which
did not provide a compelling reason to accept Python's operational tradeoffs.

## Decision

Use Python for the control/data plane and deliberately reuse its ecosystem:
Pydantic/JSON Schema, DuckDB/Parquet, existing evaluation harnesses, model
catalog adapters, and telemetry instrumentors. Keep formulas in a restricted
grammar and oracles behind an explicit versioned registry/process boundary.

Use immutable JSON artifacts plus JSON Schema and HTTP/ETag semantics as the
runtime boundary. Write thin native agent clients rather than requiring agent
processes in every language to embed Python.

## Alternatives

Go is the preferred alternative if the product becomes primarily a single
deployable service. It offers a strong operational model and CEL-Go for typed,
safe policies, but would still require Python collectors for much of the eval
ecosystem.

Rust is the preferred extraction target if the full policy kernel must be
embedded in Python, Node, browser/edge WASM, or offline agents. Its correctness
and binding story are strong, but PyPI/npm/native release matrices are not yet
justified for an engine evaluating hundreds of offerings periodically.

TypeScript is a priority runtime-client language, especially for Vercel AI SDK
and LangChain.js. It was not chosen for the control plane because benchmark,
trace-analysis, and columnar-data interoperability remain stronger in Python.

## Consequences

- The project must actually reuse the Python data/eval ecosystem; a return to a
  generic JSON-only stdlib core would reopen this decision.
- Public schema and artifact compatibility are more important than internal
  Python API stability.
- Runtime clients validate hashes, refresh and pin snapshots, but do not
  reimplement frontier semantics.
- A future native kernel can be extracted behind the same contracts without
  invalidating published artifacts.
- The bundled alpha currently proves Pydantic, DuckDB, Lark, and RFC 8785
  integration, but its data are synthetic. The next milestone must ship a real
  catalog/price adapter plus a real trace/benchmark adapter (initially
  models.dev/LiteLLM and TraceLab/Aider). If it does not, reopen this ADR rather
  than carrying Python for hypothetical ecosystem value.
