# Benchmark evidence and quality portfolios

ModelSkyline treats “quality” as workload evidence, not a timeless property of
a model name. A SWE-bench result, an ARC-AGI-2 result, and a tool-use score have
different cohorts, harnesses, units, budgets, and failure modes. A useful
quality policy should keep those identities visible.

Version 0.9 provides one composition path:

```text
benchmark capture
  -> route-free QualityEvidenceSet
  -> reviewed reconciliation to complete OfferingKey values
  -> one frontier snapshot per benchmark component
  -> PortfolioPolicy + build_portfolio
  -> enriched ObservationCatalog
  -> ordinary FormulaMetric, FrontierEngine, and SelectionSnapshot
```

This replaces the earlier parallel bundle, scalar-oracle,
quality-gated-selection, and overlap/proximity runtime. The portfolio gates
coverage and materializes benchmark signals; the core engine remains the only
place that calculates frontiers and default/fallback selections.

## What a portfolio is

A `PortfolioPolicy` declares two to four components. Each component binds:

- a stable component id;
- one exact frontier id and `WorkloadReference`;
- the exact quality-axis descriptor to read;
- a formula-safe output signal name;
- a maximum evidence age; and
- a correlation group.

The policy also declares required components, minimum measured coverage, a
versioned output workload, and a correlation rationale. It always sets
`statistical_independence_assumed: false`. Distinct IDs or hashes do not prove
that benchmarks are independent or that differently packaged task sets are not
duplicates.

`build_portfolio` accepts the policy, a mapping from component id to exact
`FrontierSnapshot`, and a base `ObservationCatalog` for the output workload. It
returns:

- an enriched ordinary catalog containing the base cost/performance signals
  plus each admitted quality component as its own signal; and
- a content-addressed `PortfolioDerivationSnapshot` recording exact component
  bindings, source metadata, freshness deadline, projection hashes, catalog
  hashes, and per-candidate failures.

It does not emit a special selection type. Feed the returned catalog into the
same `FrontierEngine` and `select_models` path used by every other workload.

## Exact identity and coverage

Leaderboard display names are never routing instructions. Reconciliation must
bind each reviewed row to a complete `OfferingKey`, including provider,
endpoint, region, service tier, billing mode, quantization, reasoning setting,
and agent harness when applicable. Matching is exact. Case folding, aliases,
prefix/family matching, and provider fallback are rejected.

The base catalog defines the candidate universe. Every component frontier must
match its declared id, workload, axis, and valid content hash and must contain
the v0.8 per-axis evidence inventory. Portfolio construction reads the selected
axis from that inventory, so a valid quality measurement survives when stale or
missing price rejected the route from the component's two-axis frontier.

For every candidate, a component is usable only when:

- the exact complete offering exists in its axis inventory;
- the selected axis has an estimate and sources;
- source version, methodology, raw digest, rights metadata, and retrieval time
  are present;
- source and observation times are not impermissibly future-dated; and
- the observation remains inside the component's maximum age.

Missing or invalid evidence is recorded with reason codes. A candidate becomes
eligible only when every required component and the minimum measured count are
satisfied. Signals are added only for eligible candidates; a failure is not
converted to zero or an average over what happens to be available.

Reusing an `offering_id` with a different complete key fails closed. Component
IDs, frontier IDs, and output signal names must be unique. Reusing one workload
under conflicting correlation groups is also rejected.

## Library use

```python
from datetime import UTC, datetime

from model_skyline import FrontierEngine, select_models
from model_skyline.quality_portfolio import (
    PortfolioPolicy,
    build_portfolio,
    verify_portfolio,
)

as_of = datetime.now(UTC)
component_frontiers = {
    "coding": swe_bench_frontier,
    "tool_use": terminal_bench_frontier,
    "reasoning": arc_agi_frontier,
}
result = build_portfolio(
    policy,
    component_frontiers,
    base_cost_catalog,
    generated_at=as_of,
)

# result.catalog is a normal ObservationCatalog.
# result.snapshot is the replay/audit record.
frontier = FrontierEngine().calculate(
    project,
    result.catalog,
    "agent-value",
    generated_at=as_of,
)
selection = select_models(project, frontier, "agent-defaults")

verify_portfolio(
    policy,
    component_frontiers,
    base_cost_catalog,
    result.snapshot,
    now=as_of,
)
```

Verification checks the derivation hash and expiry, then deterministically
rebuilds it from the exact policy, base catalog, and component frontiers. A
snapshot SHA-256 detects mutation; it does not authenticate the origin. Inputs
still require a trusted channel or separately authenticated manifest.

## CLI build and replay

Assign every policy component to one exact frontier file. Component IDs must
match the policy exactly, and `--component-frontier` is repeatable:

```console
modelskyline build-quality-portfolio \
  portfolio-policy.json base-cost-catalog.json \
  --component-frontier coding=swe-bench-frontier.json \
  --component-frontier tool_use=terminal-bench-frontier.json \
  --component-frontier reasoning=arc-agi-frontier.json \
  --catalog-output enriched-catalog.json \
  --derivation-output portfolio-derivation.json \
  --as-of 2026-08-31T23:00:00Z
```

Both outputs are private files, mode `0600` on POSIX, and existing paths are
refused unless `--overwrite` is supplied. The compact derivation is written
before the catalog; each file is canonical and independently replayable, but
the two-file operation is not one atomic rename.

Before using the catalog, replay the derivation against the exact inputs:

```console
modelskyline verify-quality-portfolio \
  portfolio-policy.json base-cost-catalog.json portfolio-derivation.json \
  --component-frontier coding=swe-bench-frontier.json \
  --component-frontier tool_use=terminal-bench-frontier.json \
  --component-frontier reasoning=arc-agi-frontier.json \
  --at 2026-08-31T23:01:00Z
```

Then pass `enriched-catalog.json` to the ordinary `evaluate`, `select`, or
library workflow. The portfolio commands do not create a separate routing
artifact.

## Defining a scalar quality axis

The portfolio deliberately does not normalize or weight benchmark scores. If
an operator needs one scalar axis, define it as an ordinary `FormulaMetric` for
a new versioned composite workload. For example, after declaring fixed,
reviewed reference anchors:

```yaml
metrics:
  composite_agent_quality:
    kind: formula
    unit: ratio
    expression: >-
      0.50 * ((signals.swe_verified_pct - 0) / 100)
      + 0.30 * ((signals.terminal_bench_pct - 0) / 100)
      + 0.20 * ((signals.arc_agi2_pct - 0) / 100)
```

The actual formula, units, normalization anchors, weights, and rationales are
operator policy. Prefer fixed external anchors over candidate-relative min/max,
which changes every model's value when one candidate enters or leaves. Never
silently clamp out-of-range scores or renormalize weights around missing
components.

The core formula engine does not propagate heterogeneous benchmark intervals or
derive a meaningful joint sample count. If uncertainty or subgroup floors
matter, materialize a separately sourced composite observation whose
methodology defines those semantics. Keep components separate when a scalar
would hide a decision-relevant tradeoff.

## Choosing two to four components

A reasonable general-agent starting portfolio is:

| Component | What it measures | Identity caveat |
| --- | --- | --- |
| SWE-bench Verified with one fixed harness | Repository-issue resolution | Pin experiments revision, cohort, harness, agent configuration, attempt/budget policy, and per-task result digest. Harness generations are different evidence. |
| Terminal-Bench through Harbor | Multi-step computer/tool work | Pin board/dataset/schema/row identities and the full agent/model metadata. Public rows generally support reviewed quality projection, not historical production-route cost. |
| tau2-bench or BFCL | Conversational policy/tool use or function calling | Pin release, domains, task split, simulator/tool configuration, and submission identity; do not silently count overlapping tool benchmarks twice. |
| ARC-AGI-2 public evaluation | Abstract reasoning | Public summaries do not by themselves attest the historical harness, attempts, task bytes, or a currently routable provider offering. |

Two components may be enough for a narrow coding/tooling policy; three are a
practical general-agent default; a fourth should cover a genuinely material
dimension. More signals do not automatically improve validity. Substituting a
domain-specific benchmark is usually better than growing a generic portfolio.

These sources do not expose one common RSS interface. Collectors poll pinned
JSON, dataset, repository, or local-harness inputs. ModelSkyline publishes RSS
only after evidence has been reviewed, reconciled, evaluated, and admitted to a
frontier.

## Independent invalidation

Quality and price have separate identity domains. A cache-read rate, cache-write
rate, ordinary input rate, result score, route mapping, source methodology, and
rights review can each change independently. Component projection hashes cover
the selected quality evidence, while the base catalog separately binds cost and
performance inputs. A relevant quality change rebuilds the portfolio; a price
change rebuilds a downstream cost frontier without pretending the benchmark was
remeasured.

The immutable derivation still records exact source frontier, config, catalog,
axis-inventory, and projection hashes for replay. This provides auditability
without putting volatile capture hashes into stable operator intent.

## Rights, privacy, and publication

Raw benchmark captures can contain prompts, responses, contact information,
copyrighted content, or secrets. Keep them in private audit storage when terms
permit, and publish only reviewed normalized projections. A source license or
terms URL is provenance, not an automatic redistribution grant.

Portfolio-enriched catalogs set `publication_safe: false` and preserve the base
catalog's previous publication marker in metadata. That categorical block
cannot be waived by a publisher source/license allowlist. Public output requires
a distinct, rights-reviewed catalog projection. The portfolio implementation
does not perform legal, secret, or PII review.

## Honest limitations

- Exact key equality proves syntactic agreement, not that a provider alias is
  immutable. Operators still need a reviewed local target binding.
- Correlation groups are disclosure and validation aids, not statistical tests.
- The portfolio does not learn weights, infer transfer from a benchmark route
  to a production route, or establish causal quality.
- Freshness is a policy clock. An immutable historical benchmark may remain
  useful even when a production route or its price has expired separately.
- No bundled collector currently establishes a broad, always-current ranking
  of every provider offering. Unmapped rows remain research evidence only.
