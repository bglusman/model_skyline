# Compound-system workload research agenda — 2026-09-19

## Working conclusion

The current `decision_component / single_model_system /
compound_model_system` split is useful for accounting, but too coarse to
explain why a compound works. It records the number of model components and
their calls; it does not yet describe the control graph, non-model components,
data-placement boundary, execution authority, state freshness, or failure
recovery policy.

The next iteration should therefore keep `system.kind` as a topology summary
and add orthogonal coordinates. This is a synthesis for ModelSkyline, not a
claim that the research community has converged on one taxonomy.

## Coordinates that should not be collapsed

| Coordinate | Values worth recording | Why it changes the workload |
| --- | --- | --- |
| control topology | direct, sequential, conditional cascade, parallel ensemble, iterative loop | A router, a vote, and a replan loop have different failure and latency behavior even with the same models. |
| component role | perception/extraction, retrieval, router, specialist, planner, worker, critic, verifier, executor | Component accuracy is meaningful only against its actual responsibility. |
| component kind | model, deterministic rule, retriever/index, tool/runtime, human, environment verifier | A useful compound system is rarely composed only of models. |
| contract | class/choice, score, structured record, tool call, plan, generated text, GUI action | Exact choice scoring cannot stand in for argument, sequence, or side-effect correctness. |
| placement and trust | on-device, homelab, private cloud, public API; raw, redacted, or derived data crossing each edge | Local/remote is a policy boundary, not merely a cost label. |
| authority | observe, recommend, stage, mutate reversibly, commit irreversibly | The same prediction can be harmless advice or an unsafe action. |
| state semantics | stateless, snapshot-bound, version-bound, conversational, environment loop | Computer use and catalog writes fail when a correct decision is applied to stale state. |
| activation | always, route choice, confidence fallback, disagreement, policy trigger, failure, post-action | Activation determines demand, cost, and which errors a downstream model can see. |
| adaptation | zero-shot, prompted, calibrated, fine-tuned, distilled, online-learned | CUA-S1 and FunctionGemma show that task training is part of the offering identity. |
| stopping and recovery | accept, abstain, ask user, retry, reobserve, replan, roll back | Repeated trials and partial failures can be more important than first-call accuracy. |

The future system identity should describe typed components and typed edges,
not encode all of this into a growing `kind` enum. Each edge needs at least an
activation rule, concurrency mode, data-release policy, and state/version
binding. The current schema should not be expanded until a real workload needs
each field, but new results should retain these facts in prompt-free metadata.

## Compound patterns to test

| Pattern | Primary hypothesis | Necessary controls | Failure cases the workload must expose |
| --- | --- | --- | --- |
| deterministic gate → model | Code can remove obvious cases before inference. | model-only; deterministic-only; oracle gate | bad hard rule, evidence missing, rule/model disagreement |
| light router → heavy worker | A cheap component can preserve quality while reducing heavy demand. | always-light; always-heavy; random router; oracle router | unsafe under-routing, needless escalation, distribution shift |
| local specialist → general fallback | Task training beats scale in scope while a general model covers the tail. | each component alone; scope oracle | false in-scope detection, OOD confidence, context/language overflow |
| proposer → verifier/guard | Independent review rescues more primary errors than successes it harms. | proposer alone; verifier acting alone; same-model self-review | correlated errors, rubber-stamping, false veto, stale-state approval |
| planner → executor → outcome verifier | Role separation improves long-horizon execution. | monolithic agent; oracle plan; deterministic executor | invalid plan, wrong action order, environmental change, false completion |
| parallel specialists → aggregator | Diverse errors make aggregation worth its call and latency cost. | best single model; same-model repeated samples; oracle selector | correlated errors, aggregator degradation, unavailable member |
| retrieval → reasoner | External state/provenance improves factual outcomes. | parametric-only; oracle retrieval; distractor retrieval | missing evidence, stale index, contradictory sources, citation mismatch |
| local redactor/router → remote worker | Hybrid placement buys capability without leaking prohibited data. | local-only; remote raw-input upper bound | reconstruction leakage, over-redaction, provider outage, latency deadline |
| observe → act → reobserve/replan | Fresh state prevents correct plans from becoming wrong actions. | no-reobserve loop; oracle state | token/window staleness, partial mutation, retry duplication, rollback |
| draft → target verification | A small model can reduce decoding cost without changing the target distribution. | target alone | low acceptance, memory contention, quality-changing arbitration |

The last pattern is an execution optimization, not automatically a semantic
compound. It belongs on throughput/latency/energy frontiers unless arbitration
is allowed to change the target answer.

## Evidence from adjacent work

- The [Berkeley compound-AI framing](https://bair.berkeley.edu/blog/2024/02/18/compound-ai-systems/)
  includes model calls, retrievers, and tools. That broader boundary is more
  useful here than “two language models.”
- [RouteLLM](https://arxiv.org/abs/2406.18665) learns strong-versus-weak routing
  from preference data. Its target is the quality/cost tradeoff, which means an
  oracle router and always-strong baseline are mandatory controls.
- [FunctionGemma](https://ai.google.dev/gemma/docs/functiongemma/model_card)
  reports a task-specific mobile-action fine-tune improving its own published
  evaluation from 58% to 85%. Together with CUA-S1, it motivates explicit
  specialization-scope and training-distribution fields.
- [ReAct](https://arxiv.org/abs/2210.03629) interleaves reasoning, action, and
  new observations. [PlanBench](https://arxiv.org/abs/2206.10498) separates
  generation, verification, state prediction, and replanning. Those are
  distinct workload stages, not one generic tool score.
- [Mixture-of-Agents](https://arxiv.org/abs/2406.04692) is a parallel/layered
  aggregation pattern. It needs a same-call-budget comparison and error
  diversity measurement, not only a score against one model.
- [tau-bench](https://arxiv.org/abs/2406.12045) evaluates the final database
  state and repeated-trial reliability rather than accepting the agent's claim
  of completion. That is the right shape for future BookLore/Biblioaudio write
  simulations.
- [WebArena-Verified](https://github.com/ServiceNow/webarena-verified) uses
  versioned tasks and deterministic response/network-trace evaluation. The
  same principle should govern computer-use compounds; action narration is not
  outcome evidence.

## Workload portfolio

One benchmark cannot answer every compound-system question. Build four layers:

1. **Component screens.** Frozen typed choices or tool calls for fast
   calibration, OOD, and contrast testing. These diagnose a component but do
   not approve a complete system.
2. **Topology stress screens.** Synthetic minimal pairs that change one routing
   or policy constraint. The new
   [`compound-routing-stress-screen-v2.json`](compound-routing-stress-screen-v2.json)
   is the first example.
3. **Replayable end-to-end environments.** Seeded database, API, or GUI states
   with deterministic postcondition checks, fault injection, and rollback.
4. **Shadow workloads.** Redacted packets and later read-only replays sampled
   from real incidents, with delayed human adjudication and no autonomous
   writes.

Promotion requires success at the next layer. A component-screen win is not
evidence of end-to-end benefit.

## Test-case construction rules

Every serious suite should include:

- one-field contrast sets for authority, evidence, scope, privacy, placement,
  reversibility, verifier availability, and state version;
- matched in-scope, near-boundary, and out-of-scope cases;
- hard negatives and intentionally incomplete packets;
- component failures: timeout, malformed output, unavailable local model,
  remote outage, verifier disagreement, and stale observations;
- repeated trials with stable environment seeds;
- distinct training, calibration, development, and untouched test splits by
  source/template/incident lineage rather than random rows;
- a case-level unsafe-action policy, because the same wrong route may be merely
  expensive in one case and a privacy or mutation violation in another; and
- executable postconditions for any task with side effects.

## Measurements and ablations

Report the complete system and each relevant counterfactual:

- final verified success, safe success, unsafe success, and abstention;
- component error rescue and override harm;
- router precision/recall for escalation plus oracle-router regret;
- specialist in-scope accuracy, OOD recall, selective accuracy, and coverage;
- verifier false-accept and false-reject rates;
- privacy-policy violations and raw/derived bytes crossing trust boundaries;
- model calls by component, wall time, queue/model-swap time, cost, memory, and
  energy where available;
- repeated-run `pass^k` or an equivalent reliability curve; and
- failure correlation between components, not only their marginal accuracy.

For a proposer/verifier pair, promote only when rescued primary errors exceed
newly harmed primary successes at an acceptable latency/cost and with no
increase in unsafe outcomes. For a router, compare against always-heavy and an
oracle route at the same workload distribution. For a specialist, compare
against deterministic rules and general models on the identical holdout.

## Near-term experiments

1. Run the 36-case contrast screen through hosted Jev, the best local direct
   readout, and one trained local decision model. Do not tune the cases after
   seeing those outputs; create a new suite version for changes.
2. Build a media `v2` holdout from adjudicated incidents with paired changes in
   work ID, edition, language, abridgement, coverage, and metadata corruption.
   Measure deterministic-only, model-only, deterministic→model, and
   proposer→verifier policies.
3. Create a disposable BookLore/Biblioaudio state fixture. Score database/API
   postconditions, idempotency, and rollback under injected stale-state and
   partial-failure cases before any live write path.
4. Use a small audited WebArena-Verified or OSWorld subset to compare one
   monolithic computer-use model with planner→actor and
   planner→actor→verifier variants. Pin the environment image and evaluator.
5. Add local/remote placement cases only after the harness can record what data
   crossed the boundary. A declared privacy policy without an egress trace is
   not measurement evidence.

This sequence grounds the taxonomy in outcomes while keeping experiments small
enough to run repeatedly on the available Macs and homelab.
