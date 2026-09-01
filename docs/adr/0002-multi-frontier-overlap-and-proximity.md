# ADR 0002: Multi-frontier overlap and proximity selection

- Status: superseded in v0.9; design retained as historical research
- Date: 2026-08-30
- Decision owners: ModelSkyline maintainers and Hermes integration track
- Design discussion: [issue #1](https://github.com/bglusman/model_skyline/issues/1#issuecomment-5471065874)

Version 0.9 removed the `selection_overlap.py` runtime, its special selection
artifact, CLI commands, and schemas. No demonstrated consumer required this
parallel selection system; the external v0.6 consumer used the ordinary
single-frontier `SelectionSnapshot`. Quality composition now uses
`PortfolioPolicy` to enrich an ordinary catalog, followed by the core
`FormulaMetric`, frontier, and selection path. This ADR preserves the reasoning
and can inform a future consumer-driven design, but it is not a shipped v0.9
contract.

## Context

A model offering can be an attractive default because it is non-dominated on
several independently useful frontiers, not only because it is first on one
primary axis. Operators also need to distinguish an offering that is narrowly
dominated from one that is far from a secondary frontier.

This must not change a published frontier after the fact. Selection may reorder
only the exact members admitted by its primary frontier snapshot. Every
secondary input must remain independently reproducible, including when it was
calculated for a different workload.

## Decision

ModelSkyline adds two content-addressed artifacts in
`selection_overlap.py`:

1. `FrontierProximitySnapshot` is a descriptive sidecar over every evaluated
   offering in one exact frontier snapshot.
2. `MultiFrontierSelectionSnapshot` records a re-ranking of the primary
   frontier members, the exact secondary inputs and policy, the complete
   pre-diversity rank evidence, and the chosen default and fallbacks.

The existing frontier artifact and dominance calculation are unchanged. The
primary frontier is implicit in overlap policy and cannot be counted again as a
secondary frontier.

### Exact offering and input identity

Cross-frontier matches compare the complete canonical `OfferingKey`, including
`offering_id`, model, provider, endpoint, billing mode, region, service tier,
quantization, reasoning effort, agent harness, and capabilities. Matching only
`offering_id` is forbidden. An offering absent under this exact identity is
unmeasured; it never receives an implicit zero distance.

Each secondary reference binds all of:

- frontier ID;
- exact frontier snapshot ID and independently repeated content hash;
- exact proximity-sidecar snapshot ID;
- its own maximum age;
- an optional, per-frontier `near_epsilon` in the closed interval `[0, 2]`, on
  a fixed grid of at most 34 decimal places.

The maximum of 2 is not an arbitrary percentage cap. For finite values the
normalized difference between two scalars cannot exceed twice their maximum
absolute magnitude. Decimal input length and precision remain bounded by the
normal canonical-decimal contract.

At selection time the implementation verifies both artifact hashes, rebuilds
the sidecar under its declared algorithm version, checks candidate and member
universe hashes, and checks axes, goals, units, absolute tolerances, uncertainty
mode, workload reference, and generation time. Extra or missing secondary
inputs are rejected. Cross-workload inputs are allowed intentionally.

### Proximity definition

Distance uses Decimal policy arithmetic and the same pessimistic/optimistic
bounds as normal point or robust dominance. For candidate dominator `A`, target
`B`, and axis `i`, define an oriented advantage `d_i`:

```text
minimize: d_i = B_i - A_i
maximize: d_i = A_i - B_i
scale:    s_i = max(abs(A_i), abs(B_i))
```

For robust uncertainty, `A_i` is A's pessimistic bound and `B_i` is B's
optimistic bound. The policy comparison first rounds both operands symmetrically
to the engine's 34-significant-digit, round-half-even Decimal context; the
artifact still retains the original observation precision. This prevents a high-precision operand
from being compared asymmetrically with a rounded tolerance expression and
preserves the finite normalized-gap bound. Holding configured absolute
tolerance `a_i` fixed, A
dominates B at relative epsilon `e` exactly when:

```text
for every i: d_i >= -(a_i + e * s_i)
for some i:  d_i >    a_i + e * s_i
```

For each A/B pair this produces either no dominance or one half-open interval
`[entry_epsilon, exit_epsilon)`. Endpoints lie on the fixed decimal grid
`k / 10^34` for integer `k` in `[0, 2 * 10^34]`. A rounded algebraic ratio is
only a search hint: exponential bracketing and integer bisection call the same
core axis predicate at every decision until they find the first grid value
where the relation changes. This avoids both an unbounded Decimal-ULP walk and
claiming an open boundary at which the engine's strict comparison still reports
dominance. The algorithm sorts every pair interval and merges the connected
component containing epsilon zero. The first grid epsilon not covered by that
component is B's `minimal_relative_epsilon`.

This interval treatment matters because symmetric tolerance is not generally
monotone: a candidate that is too poor on one axis to dominate at epsilon zero
can begin dominating when tolerance grows, then stop when its advantage on the
other axis is also within tolerance. Considering only the epsilon-zero
dominators would therefore understate distance.

The sidecar emits every blocking interval and, for each axis, the largest
normalized exit slack among those blockers plus its exact OfferingKey
witnesses. The scalar distance is the maximum of those two axis slacks. This
makes the derivation auditable without mixing heterogeneous axis units.

Distance zero means non-dominated at relative epsilon zero under the fixed
absolute tolerances. Exact membership remains a separate flag copied from the
source snapshot. This distinction is necessary when the source frontier itself
uses a nonzero configured relative tolerance.

Policy near-membership is the monotone predicate:

```text
minimal_relative_epsilon <= near_epsilon
```

It is a threshold over the first-entry scalar, not a claim that recomputed
epsilon-dominance remains monotone at every larger epsilon.

### Priority groups resolve priority versus overlap count

Raw overlap count and ordered frontier priority are different policies. This
ADR chooses explicit ordered priority groups. A policy has the shape:

```yaml
strategy: priority-group-overlap
priority_groups:
  - name: critical
    frontiers:
      - frontier_id: coding-cost
        frontier_snapshot_id: <sha256>
        frontier_snapshot_hash: <same-sha256>
        proximity_snapshot_id: <sha256>
        near_epsilon: "0.05"
        max_age_seconds: 3600
  - name: supporting
    frontiers:
      - frontier_id: research-quality
        frontier_snapshot_id: <sha256>
        frontier_snapshot_hash: <same-sha256>
        proximity_snapshot_id: <sha256>
        max_age_seconds: 21600
```

For each group in declaration order, candidates are compared
lexicographically by:

1. descending exact-membership count;
2. descending near-only-membership count (exact members are not counted twice);
3. ascending per-frontier proximity vector in declaration order, with measured
   values ahead of missing values.

Only after the complete key for one group ties does comparison proceed to the
next group. Consequently one exact membership in `critical` beats any number
of memberships found only in `supporting`. This is deliberate and is covered
by a conflict test. After all groups, the existing primary-axis order and
`offering_id` tie-break apply.

Provider diversity is a hard list constraint applied after this re-ranking.
The snapshot retains all primary candidates in pre-diversity order, making
that precedence visible. Each ranked record also carries the source axes and
metadata used to construct its corresponding `ModelChoice`. Artifact validation
requires contiguous ranks, a permutation of primary ranks, evidence-derived
ordering, the exact policy threshold for every near flag, and the deterministic
greedy diversity prefix. Existing insufficient-candidate behavior remains in
force.

### Trust boundary and source-backed verification

Content addressing proves integrity, not operator authorization or publisher
authenticity. Before an agent routes work from a downloaded artifact, it must
pin the intended selection ID and overlap policy from trusted configuration,
authenticate the publication channel or signed manifest, and call
`verify_multi_frontier_selection_snapshot` with a timezone-aware trusted current
time. That verifier rejects expired or implausibly future-dated artifacts,
checks the artifact hash and trusted policy hash, verifies all bound source
hashes, regenerates the proximity sidecars and selection, and requires exact
equality—including rank evidence, axes, metadata, default, and fallbacks. As in
`DynamicResolver`, `valid_until` itself is the final valid instant; any later
time is expired.

Pydantic validation and JSON Schema establish portable structure and useful
self-consistency only. JSON Schema cannot express the global flattened policy
limit, total evidence limit, canonical OfferingKey byte/order/uniqueness rules,
cryptographic hashes, freshness against a reference clock, or truth against the
bound source artifacts. Those are mandatory semantic checks for every
non-Python implementation. The generated schema does expose local
measured/unmeasured evidence conditionals and canonical epsilon strings, but
schema acceptance alone is never authorization to consume a selection.

### Freshness and hashes

The selection generation time is the primary snapshot generation time.
Future-dated secondary inputs and inputs at or beyond their individual maximum
age are rejected. Selection validity ends at the earliest of its configured
TTL and every secondary input's freshness deadline.

Policy hash includes the existing selection definition, ordered priority
groups, exact input hashes, freshness limits, and canonical per-frontier near
epsilon. Artifact hashes use SHA-256 over RFC 8785 canonical JSON with
`snapshot_id` excluded. Repeated calculation over identical inputs is therefore
byte- and hash-stable.

## Consequences

- Operators can favor evidence breadth without weighted floating-point scores.
- Existing frontier consumers remain compatible because proximity is a
  sidecar, not a field added to the frontier snapshot.
- Missing measurements are conservative and visible.
- Sidecar calculation and verification are `O(n^2)` per secondary candidate
  universe. The alpha contract therefore caps a sidecar at 128 candidates,
  32,768 repeated evidence references, and 2,048 canonical bytes per offering
  identity. A policy can bind at most 128 total secondary frontiers across all
  groups, and a selection artifact can contain at most 32,768 candidate/frontier
  evidence entries. Larger catalogs need digest-based witness references or a
  verified two-dimensional optimization before these limits can grow safely.
- A multi-frontier selection may contain at most 10,000 primary members; the
  candidate/frontier evidence limit normally constrains it further. Primary
  OfferingKeys use the same 2,048-byte canonical identity bound, and fallback
  arrays are structurally bounded before semantic validation.
- The additive multi-frontier artifact needs publisher, CLI, and dynamic
  resolver wiring before it can replace the existing single-frontier selection
  feed end to end.
