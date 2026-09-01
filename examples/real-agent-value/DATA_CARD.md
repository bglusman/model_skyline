# Payload-free agent-value golden example

This fixture is a minimized reproduction of the successful v0.6 user study in
PR #14. It demonstrates one useful path: apply historical per-component API
prices to an observed agent workload shape, place cost against an
synthetic regression-quality signal, then choose an ordered default and fallback.

## Published aggregate

The data owner authorized publication of these aggregate statistics from a
private 30-day agentic tool-use workload:

- 1,336 turns and a reported, rounded 93% success rate
- 14,592 uncached input tokens per successful turn
- 166,621 cache-read input tokens per successful turn
- 592 output tokens per successful turn

No prompts, responses, trace rows, account identifiers, paths, or direct user
identity are included. This is pseudonymized, not anonymous: its lineage to the
public PR #14 contribution can associate the aggregate with that contributor.
The three token-shape values are workload variables, so they are declared once
rather than copied into every offering.

## Provenance boundaries

The private aggregate, exact public price snapshots, and synthetic regression
scores have distinct `SourceReference` identifiers. Changing any value changes
the relevant config or catalog digest; the cost axis records both the aggregate
and offering-specific pricing source IDs, while the quality axis records only
its quality source ID.

The two public OpenRouter single-model endpoints were retrieved at
2026-09-01T01:37:14Z. Their complete response digests are retained in offering
metadata. Price-source versions hash only the exact offering id and three
selected price fields, so a description or benchmark field changing in the same
API response does not falsely invalidate price semantics. Per-token prices were
multiplied by 1,000,000 for the USD/Mtok inputs. This is an immutable snapshot,
not a live-price promise. The quality values are maintainer-authored synthetic
ordinals used only to exercise frontier and selection behavior. They are not
benchmark evidence, model-quality claims, or a current feed.

The cost axis is deliberately named `metered_token_cost_per_success`: it includes
uncached input, cache-read input, and output token meters. The source records did
not establish cache-write/storage, request, tool/search, agent-compute,
subscription, or credit-purchase charges. Unknown or out-of-scope components are
listed in workload assumptions and are not silently treated as a measured zero.

Workload variables currently have source provenance but no per-variable
observation timestamp. The engine therefore records their source and hashes their
values, but does not enforce metric or source age against them. Operators must
version or replace this aggregate deliberately when workload shape changes.

## Scope and reproduction

This is a two-offering regression fixture, not a market-wide frontier. Structured
scope and exclusions are recorded in `workloads.*.assumptions.candidate_scope`.
No generated selection artifact is committed; the deterministic test builds it
at a fixed time and checks exact Decimal values:

```console
uv run pytest tests/test_real_agent_value_example.py
```
