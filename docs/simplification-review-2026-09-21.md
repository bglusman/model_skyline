# Adversarial simplification review

Date: 2026-09-21

This review treats accumulated functionality as a liability until it shows a
clear user, boundary, and validation story. It does not propose breaking the
existing external consumer or deleting retained research evidence.

## Verdict

The core idea is sound and narrower than the repository makes it appear:

> compile exact, workload-bound observations into a two-axis Pareto frontier
> and a pinned default-plus-fallback artifact.

The repository has become difficult to understand because it presents at
least four projects as one:

1. a frontier/selection library and CLI;
2. an evidence interchange and reconciliation system;
3. a collection of benchmark/runtime harnesses; and
4. a public research notebook for local models, voice, and compound systems.

All four can share contracts. They should not share equal billing, maturity,
or navigation.

At this snapshot, the CLI exposes 34 visible commands plus one hidden schema
command, `cli.py` is 1,965 lines, and `examples/` contains roughly 805 files and
16 MB. The supported user story still begins with only three commands. That
ratio is the clearest symptom: evidence-acquisition breadth is obscuring the
decision product.

## What is genuinely coherent

- Workload identity prevents unrelated measurements from being combined.
- Offering identity correctly stays narrower than model identity.
- Missing/stale/incompatible evidence is excluded with reasons.
- Two explicit axes keep the comparison explainable.
- Gates separate non-negotiable requirements from Pareto tradeoffs.
- Immutable frontier and selection snapshots create a clean runtime boundary.
- JSON Schema and canonical artifacts support non-Python consumers.
- The project retains provenance and negative results better than most model
  comparison tooling.

These are the differentiators. Protect them.

## Where the project has drifted

### 1. The front page became a changelog of experiments

Transient local-model, TTS, ASR, Jev, and compound-system conclusions appeared
before the quickstart. A new user had to understand the lab before discovering
the product. Results also age much faster than the contract they were meant to
illustrate.

Correction: the root README now defines the product, shortest runnable path,
and limits. Dated results belong in the examples/research catalog and public
research publication.

### 2. “Examples” means four incompatible things

Tiny fixtures, a live consumer, raw measurement archives, and bespoke research
harnesses share one directory level. File volume makes the large studies look
canonical even though the small fixture is the correct onboarding path.

Correction: `examples/README.md` labels tutorial fixtures, operational case
studies, and dated research programs separately.

### 3. The CLI exposes implementation history as product design

Every new evidence source gained a top-level verb. Help grouping improves
scanning but does not answer which commands a normal user needs. The flat
surface also implies equal support across adapters whose real validation differs.

Recommendation for a future major version:

```text
modelskyline validate|evaluate|select
modelskyline catalog discover|compose|enrich|view
modelskyline evidence import|normalize|reconcile|verify
modelskyline publish build
```

Do not rename commands in the current major version. The deployed consumer
depends on `evaluate` and `select`; existing names need aliases, warnings, and a
documented migration before removal.

### 4. The project description overstates runtime ownership

“Dynamic model selection for agents” sounds like a live router. ModelSkyline
actually emits a static, versioned control-plane artifact that another system
consumes. That is a useful and safer boundary, but it should be named plainly.

Correction: call the project an evidence-to-selection compiler. Describe
gateways and agents as consumers, not hidden parts of the product.

### 5. Collection work can outrun decision value

The repository is very good at preserving provenance, but each source adapter,
feed monitor, local harness, and special artifact creates permanent maintenance.
More data is not automatically a better decision. Several studies have richer
capture machinery than external validation.

Recommendation: freeze new reusable adapters unless an intake names the user
decision, an expected consumer, why generic observations are insufficient, and
an independent realistic validation plan. A one-off candidate can remain a
research script without becoming a package feature.

### 6. Research findings and product guarantees blur together

Measured, vendor-reported, synthetic, operational, and recommended are
different claims. They appear near each other, and “frontier resident” can be
misread as “deploy this.” This is especially risky for safety/authority routing
and compound systems.

Recommendation: every study summary should carry those evidence labels plus
workload, candidate set, harness, environment, date, and known applicability
limits. “Recommended” requires a separately stated deployment policy.

### 7. Compound systems stress the existing abstraction

It is valid to model a whole pipeline as an offering only after measuring the
whole pipeline. It is invalid to combine a router's standalone accuracy, a
worker's benchmark score, and a verifier's latency into an imagined end-to-end
point. Configuration identity also becomes much larger: topology, thresholds,
placement, authority, stopping, and recovery matter.

Recommendation: keep component and system workloads separate. Admit a compound
offering to a deployment frontier only from end-to-end evidence and require it
to beat its components or satisfy a system-level constraint.

### 8. The research survey is expensive to keep true

Claims such as “no surveyed project combines all of these” decay quickly. The
Jev-like ecosystem demonstrated how a landscape can change in days. A large
prior-art document invites false completeness.

Recommendation: date every landscape, distinguish inventory from evaluation,
and publish a small current comparison matrix generated from candidate intakes.
Archive older narrative rather than continuously expanding one universal survey.

## Keep, demote, freeze

| Decision | Scope |
|---|---|
| Keep as the product | Contracts, validation, formula/gate semantics, frontier evaluation, selection artifacts |
| Keep as supported extensions | Publication, trusted resolution, exact catalog composition, model views |
| Keep but label optional | Trace/benchmark adapters, quality portfolios, local measurement normalization |
| Keep as research | Local hardware, voice, structured-decision, compound-model, and candidate screens |
| Freeze pending consumer evidence | New top-level commands, new special artifact families, new benchmark-specific package modules |
| Avoid | Live inference proxy, universal leaderboard, fuzzy identity matching, inferred compound scores |

“Demote” means change navigation and claims, not delete evidence.

## A simpler operating model

Each new effort should produce one of four artifacts:

1. **intake:** why a source/candidate might matter, with claims clearly marked;
2. **measurement:** frozen raw/normalized evidence for an exact workload;
3. **decision:** frontier/selection or explicit no-promotion finding; or
4. **integration:** proof that a real consumer can use the decision artifact.

Work should stop after the earliest artifact that answers the question. A model
inventory does not require an adapter. A failed screen does not require a new
frontier. A frontier does not require a runtime integration unless someone
intends to use it.

## Near-term sequence

1. Merge navigation and claim-boundary changes without altering contracts.
2. Keep the current core command names stable.
3. Add a machine-readable study index only if it replaces hand-maintained root
   prose; do not create another parallel catalog.
4. Finish one realistic compound-system integration with postconditions,
   stale-state handling, idempotency, and rollback evidence before adding more
   orchestration taxonomies.
5. Compare Jev-like candidates on the exact same frozen workload and report
   deterministic, specialist, generative, and human-review baselines together.
6. Promote only the workflow that changes a real deployment decision; archive
   the rest as dated candidate evidence.

## Success criteria for the simplification

A new reader should be able to answer, in under five minutes:

- What does ModelSkyline produce?
- Which three commands form the supported path?
- What data must I supply?
- Is this repository routing my requests? (No.)
- Which example should I run first?
- Which results are research rather than recommendations?
- Where do I integrate the resulting selection?

If future additions make those answers harder, the addition belongs below the
product boundary or outside this repository.
