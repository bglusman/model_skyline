# Structured decisions, tool use, and compound model systems

This experiment answers two related questions without pretending they are the
same question:

1. **Which component makes a small structured decision most accurately, quickly,
   and cheaply?** Jev belongs here, alongside general LLMs asked the identical
   typed question.
2. **Which complete system finishes a tool-using task most accurately, quickly,
   and cheaply?** A single model and a light-router-plus-heavy-worker pair both
   belong here, provided every call is counted.

Jev is a hosted, non-generative structured-decision model, not a drop-in general
LLM. TypeSafe has not published weights or a self-hosting commitment, and access
is currently waitlisted. It may still improve a general agent as a router,
guardrail, or verifier after access becomes available. The compound-system
measurements test that claim instead of transferring a router score to its
worker.

## The three candidate types

| Candidate type | What is measured | Fair comparisons |
| --- | --- | --- |
| `decision_component` | One typed decision such as `light / heavy / abstain` | Jev, a small local LLM, or a large LLM answering the same question |
| `single_model_system` | One complete routable agent/tool system | Small-only, heavy-only, local, or remote complete systems |
| `compound_model_system` | A versioned policy connecting two or more named components | Jev+worker, small-local+worker, router+worker+guardrail, or other model pairs |

A compound offering is not merely named “Jev + Qwen.” Its identity includes:

- each exact component offering, including provider or local runtime details;
- each component's role and light/heavy resource class;
- a hash of the instructions given to each component;
- a hash of the routing policy;
- the agent harness; and
- per-case calls and attributed costs for every declared component.

The importer rejects a run if component calls do not sum to total calls, heavy
component calls do not sum to the heavy-call total, or attributed component
costs do not sum to total cost.

## Frontier definitions

[`frontiers.yaml`](frontiers.yaml) contains ten ordinary two-axis frontiers:

| Frontier | First axis | Second axis | What it tells us |
| --- | --- | --- | --- |
| decision quality vs latency | correct route | p95 response time | fastest reliable decision component |
| decision quality vs cost | correct route | mean cost | cheapest reliable decision component |
| decision quality vs calibration | correct route | normalized Brier error | whether confidence is useful, not merely whether top-1 wins |
| safe automation vs cost | non-abstained share | mean cost | most work handled under 95% autonomous accuracy and zero unsafe actions |
| safe automation vs latency | non-abstained share | p95 response time | local/self-hosted version when a defensible dollar cost is unavailable |
| compound routing quality vs heavy demand | final route accuracy | heavy calls per case | whether a routing cascade improves decisions without simply calling the heavy model every time |
| tool outcome vs latency | complete case success | p95 wall time | fastest complete tool system |
| tool outcome vs cost | complete case success | cost per success | cheapest complete tool system |
| tool arguments vs latency | correct arguments | p95 wall time | argument quality after selection and policy gates |
| compound outcome vs heavy demand | complete case success | heavy calls per case | whether routing actually avoids expensive work without losing outcomes |

Tool selection, argument correctness, sequence correctness, policy compliance,
side-effect correctness, schema validity, and unsafe-action rate remain separate
signals. A single average would hide whether a model chose the wrong function,
corrupted its arguments, called tools in the wrong order, or acted when it
should have stopped.

The “tool outcome” frontiers admit both single and compound systems. That is the
direct answer to whether a pair beats heavy-only or light-only. The final
compound-only frontier then explains *how* a winning pair saves work.

## First evaluation matrix

Run these controls on the same exact cases and agent harness:

| Policy | Decision component | Worker behavior |
| --- | --- | --- |
| heavy-only | none | always use the heavy worker |
| light-only | none | always use the light worker |
| Jev router | Jev | use light, heavy, or abstain exactly as routed |
| local router | small local model | same policy and route labels as Jev |
| confidence fallback | Jev or small local model | use heavy when confidence is below a pinned threshold |

Useful worker panels include local Qwen3.8, another smaller local model, and a
remote model when privacy policy permits. The category is deliberately broader
than Jev: any light/heavy pairing is valid when its whole policy is frozen and
its end-to-end outcomes are measured.

## Reproducible inputs

- [`routing-screen-v1.json`](routing-screen-v1.json) is an 18-case public
  calibration screen for `light / heavy / abstain`. It is small on purpose and
  must not be reported as general intelligence.
- [`jev-candidate.json`](jev-candidate.json) configures the TypeSafe API route.
- [`qwen38-local-candidate.json`](qwen38-local-candidate.json) configures the
  current llama-swap OpenAI-compatible route.
- [`gpt-oss-20b-local-candidate.json`](gpt-oss-20b-local-candidate.json)
  configures the lighter local GPT-OSS control.
- [`gpt-oss-qwen38-review-cascade.json`](gpt-oss-qwen38-review-cascade.json)
  defines an exact GPT-OSS router plus Qwen reviewer policy. It explicitly says
  that the present deployment swaps models rather than keeping them co-resident.
- [`bfcl-v4-offline-64-manifest.json`](bfcl-v4-offline-64-manifest.json) pins 64
  offline BFCL cases, source blobs, answers, scorers, source revision, license,
  retrieval time, and the mapping to ModelSkyline's tool subdimensions. It is a
  ModelSkyline calibration panel, not the full upstream BFCL score and not the
  BFCL V4 Agentic aggregate.

The BFCL panel covers single, competing, parallel, parallel+competing,
intentional no-call, multi-turn, missing-function, and missing-parameter cases.
It does not prove final application state. Leave side-effect correctness null
unless a pinned environment verifier, such as a later AutomationBench run,
actually checks that state.

## Run Jev or a local model

Install the optional official TypeSafe comparison adapter:

```console
uv sync --extra structured-decisions
```

Run Jev after placing its key in the environment:

```console
TYPESAFE_API_KEY=... uv run python \
  examples/structured-decision-frontiers/run_system_one_screen.py \
  examples/structured-decision-frontiers/routing-screen-v1.json \
  examples/structured-decision-frontiers/jev-candidate.json \
  --repetitions 3 --output jev-run.json
```

Run the same typed questions through the local OpenAI-compatible endpoint:

```console
uv run python \
  examples/structured-decision-frontiers/run_system_one_screen.py \
  examples/structured-decision-frontiers/routing-screen-v1.json \
  examples/structured-decision-frontiers/qwen38-local-candidate.json \
  --repetitions 3 --output qwen38-run.json
```

Run the pinned local review cascade:

```console
uv run python \
  examples/structured-decision-frontiers/run_system_one_screen.py \
  examples/structured-decision-frontiers/routing-screen-v1.json \
  examples/structured-decision-frontiers/gpt-oss-20b-local-candidate.json \
  --worker-candidate \
    examples/structured-decision-frontiers/qwen38-local-candidate.json \
  --compound-candidate \
    examples/structured-decision-frontiers/gpt-oss-qwen38-review-cascade.json \
  --repetitions 3 --output cascade-run.json
```

The runner uses TypeSafe's official
[`system-one-adapter`](https://github.com/typesafe-ai/system-one-adapter-python)
so Jev and a general LLM receive the same `Choice` contract. It emits no prompt,
state, expected answer, or model response in the result artifact.

Normalize a completed run into a normal ModelSkyline observation catalog:

```console
uv run modelskyline normalize-structured-decision-run jev-run.json \
  --retrieved-at 2026-09-16T15:00:00Z --output jev-catalog.json
```

The runner covers decision components and a two-component routing-review
cascade. It records the router's accuracy, abstention, maximum probability and
Brier score separately from the final system result. A BFCL or environment
harness emits the same run schema for complete tool systems. That harness must
count technical failures, router calls, retries, fallbacks, and worker calls
rather than dropping failed cases.

## Current status

The schema, normalizer, exact routing screen, BFCL manifest, cascade runner, and
frontier policy are implemented. Qwen3.8, GPT-OSS, and the first swap-backed
compound policy have been measured on the M5. See
[`status-2026-09-16.md`](status-2026-09-16.md) for the results and their limits.

No Jev result exists because the account is on TypeSafe's early-access
waitlist. That is an access prerequisite, not evidence for or against Jev.

The next result-of-record step is a matched matrix of heavy-only, light-only,
Jev+worker, and small-local+worker on the BFCL panel, followed by a smaller
state-verifying workload. Promotion requires multiple candidates, nontrivial
score spread, stable repetitions, and no unexplained parser or policy failures.
