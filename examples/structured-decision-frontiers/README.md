# Structured decisions, tool use, and compound model systems

## Plain-language result

A model pair is useful only when it earns a place beside the individual models.
For a two-axis frontier, that means no tested alternative is both better on the
first axis and better on the second.

The current 64 GB M5 single-turn tool calibration has two residents:

| Choice | Correct tool outcomes | p95 wall time | Qwen calls per case | Why it remains |
| --- | ---: | ---: | ---: | --- |
| Granite 4 Micro 3B 8-bit | 95.00% | 1.42 s | 0 | speed-first choice |
| Granite primary + Qwen3.8 no-call veto | 96.67% | 11.94 s | 0.45 | quality-first choice |

Qwen3.8 alone scored 94.17% at 21.65 seconds p95, so Granite dominates it for
this narrow job. This does **not** say Granite is generally smarter than Qwen.
It says a small tool specialist is the better implementation for these 40 BFCL
single-turn cases.

The compound policy is intentionally limited. Granite constructs the call.
When exactly one tool is available and Granite proposes using it, Qwen may veto
the call by independently deciding that no tool is appropriate. Qwen cannot
rewrite a valid Granite call. Across three repetitions, the veto rescued 2 of
6 primary errors and harmed 0 of 114 primary successes.

This is positive but provisional evidence: 120 observations came from only 40
distinct cases, and both rescues were repetitions of one no-call case. The
result justifies keeping compound candidates in the framework; it does not yet
justify a default production cascade.

### Direct decisions: SemIf, real Jev, and a local compound

[SemIf](https://github.com/TheoLeeCJ/SemIf) is not Jev and does not contain an
open Jev model. It wraps an ordinary open model with a useful readout: ask for
one option letter, read that next token's logits for every allowed letter, and
normalize only those scores. This avoids generating and parsing a probability
object. ModelSkyline records the model, quantization, prompt/readout version,
runtime, and source revision as part of the offering.

On the same 18 routing cases repeated three times on the 64 GB M5:

| Candidate | Correct routes | p95 | Mean Brier error | Unsafe routes | Heavy calls/case |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen3.5 4B Q4 direct logits | 55.56% | 0.127 s | 0.1962 | 0 | 0 |
| Qwen3.8 27B UD-Q4_K_M generated probabilities | 83.33% | 2.271 s | 0.0994 | 5.56% | 1.00 |
| Real Jev 1.13 through OpenRouter | 83.33% | 0.623 s | **0.0659** | 0 | 0 |
| Qwen3.8 27B UD-Q4_K_M direct logits | **88.89%** | 0.541 s | 0.0830 | 0 | 1.00 |
| Qwen3.5 light gate → Qwen3.8 direct worker | **88.89%** | 0.675 s | 0.0757 | 0 | **0.78** |

The controlled Qwen3.8 comparison uses the same GGUF, llama.cpp runtime, cases,
and repetition order. Direct readout is both more accurate here and about 4.2×
faster at p95 than generated probabilities. That is evidence for this readout
on this screen, not evidence that direct logits always improve a model.

The 4B model is too inaccurate to act alone. It is nevertheless useful as a
conservative gate: it handles only cases it calls `light`; both `heavy` and
`abstain` go to the 27B worker. The pair matches the 27B worker's final accuracy
with 22.22% fewer heavy calls. A more aggressive prompt raised the router's
top-1 accuracy to 77.78% but produced 9 unsafe routes in 54 observations, so it
was rejected rather than published as the candidate. See
[`status-2026-09-18.md`](status-2026-09-18.md) for exact limits and frontier
membership.

### Voice, media, and home automation: Needle + Jev

[Cactus Needle 3](https://github.com/cactus-compute/needle) is an open, tiny
specialist that maps short requests to declared tool calls; it is not a chat
model. On the 64 smart-home and media-player cases shipped with version 3.0.1,
repeated three times on the 64 GB M5, it creates a clear speed/quality tradeoff:

| Complete system | Exact success | Tool-policy compliance | p95 |
| --- | ---: | ---: | ---: |
| Needle 3 alone | 85.9375% | 87.500% | **0.0812 s** |
| Needle 3 + Jev exact-call guard | **95.3125%** | **96.875%** | 0.4802 s |

Both systems are on the two corresponding frontiers: local Needle is the speed
choice, while the guarded pair is the quality choice. The guard reviews only a
nonempty Needle proposal and can approve it unchanged or reject it. It does not
repair arguments. The guarded system still made 6 unsafe calls in 192
observations, so this is evidence for the architecture, not approval for direct
control of real devices.

See
[`status-2026-09-18-needle-home-automation.md`](status-2026-09-18-needle-home-automation.md)
for the deployment design, cost, limitations, and next Home Assistant holdout.

## Other pairs worth measuring

The useful pattern is complementary roles, not merely “two models.” The next
high-value candidates are:

- a general worker plus [Granite Guardian](https://www.ibm.com/granite/docs/models/guardian)
  or [Qwen3Guard](https://huggingface.co/Qwen/Qwen3Guard-Gen-0.6B), measured on
  safety caught versus false-positive blocks and added latency;
- a general worker plus a tiny function-calling specialist such as
  [xLAM-2 1B](https://huggingface.co/Salesforce/xLAM-2-1b-fc-r), measured on a
  heterogeneous workflow where text/code generation and tool dispatch are both
  scored; and
- a target model plus a speculative draft model, measured on equivalent output
  quality versus latency and memory. The existing Qwen+DFlash work belongs to
  this execution-optimization class, not the semantic-cascade class above.

[FunctionGemma 270M](https://huggingface.co/google/functiongemma-270m-it) is a
later candidate after task-specific tuning; its own model card says it is meant
to be fine-tuned for a particular function-calling task, so a zero-shot test
would not fairly answer whether the architecture is useful.

This experiment answers two related questions without pretending they are the
same question:

1. **Which component makes a small structured decision most accurately, quickly,
   and cheaply?** Jev belongs here, alongside general LLMs asked the identical
   typed question.
2. **Which complete system finishes a tool-using task most accurately, quickly,
   and cheaply?** A single model and a light-router-plus-heavy-worker pair both
   belong here, provided every call is counted.

Jev is a hosted, non-generative structured-decision model, not a drop-in general
LLM. TypeSafe has not published weights or a self-hosting commitment. Jev 1.13
is now available through the TypeSafe API, OpenRouter Decisions, and Vercel AI
Gateway; the committed result uses OpenRouter and retains its provider-reported
cost. The compound-system measurements test router usefulness instead of
transferring a router score to its worker.

## System topology and specialization

| Candidate type | What is measured | Fair comparisons |
| --- | --- | --- |
| `decision_component` | One typed decision such as `light / heavy / abstain` | Jev, a small local LLM, or a large LLM answering the same question |
| `single_model_system` | One complete routable agent/tool system | Small-only, heavy-only, local, or remote complete systems |
| `compound_model_system` | A versioned policy connecting two or more named components | Jev+worker, small-local+worker, router+worker+guardrail, or other model pairs |

These three types describe system topology. Specialization is a separate axis,
not a fourth type. A narrow scorer such as
[CUA-S1-FORMS](https://huggingface.co/cua-ai/cua-s1-forms) is still a
`decision_component`; its specialization scope, training provenance,
out-of-scope policy, and exact artifact revision belong in run metadata and its
workload identity. It may share a frontier with a general decision model only
when both answer the same action contract on the same pinned cases.

The local
[`CUA-S1-FORMS` intake](cua-s1-specialist-intake-2026-09-19.md) reproduces the
published 196-row demo result, explains why that is not media-domain evidence,
and defines the selective-accuracy, safety, outcome, and fallback-demand axes
needed before a media-specific specialist can enter an active frontier.

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

[`frontiers.yaml`](frontiers.yaml) contains fifteen ordinary two-axis frontiers:

| Frontier | First axis | Second axis | What it tells us |
| --- | --- | --- | --- |
| decision quality vs latency | correct route | p95 response time | fastest reliable decision component |
| decision quality vs cost | correct route | mean cost | cheapest reliable decision component |
| decision quality vs calibration | correct route | normalized Brier error | whether confidence is useful, not merely whether top-1 wins |
| safe automation vs cost | non-abstained share | mean cost | most work handled under 95% autonomous accuracy and zero unsafe actions |
| safe automation vs latency | non-abstained share | p95 response time | local/self-hosted version when a defensible dollar cost is unavailable |
| decision quality vs heavy demand | final route accuracy | heavy calls per case | whether a pair saves heavy calls when competing directly with single components |
| compound routing quality vs heavy demand | final route accuracy | heavy calls per case | whether a routing cascade improves decisions without simply calling the heavy model every time |
| tool outcome vs latency | complete case success | p95 wall time | fastest complete tool system |
| tool outcome vs cost | complete case success | cost per success | cheapest complete tool system |
| tool arguments vs latency | correct arguments | p95 wall time | argument quality after selection and policy gates |
| compound outcome vs heavy demand | complete case success | heavy calls per case | whether routing actually avoids expensive work without losing outcomes |
| single-turn tool outcome vs latency | correct BFCL case | p95 wall time | whether a specialist or pair earns the speed/quality tradeoff |
| single-turn tool outcome vs heavy demand | correct BFCL case | heavy calls per case | whether added heavy-model work buys an outcome improvement |
| voice command outcome vs latency | exact final tool-call result | p95 wall time | whether a guard's quality gain earns its voice-loop delay |
| voice command policy vs latency | calls that obey the declared tool contract | p95 wall time | whether safer automation earns its voice-loop delay |

Tool selection, argument correctness, sequence correctness, policy compliance,
side-effect correctness, schema validity, and unsafe-action rate remain separate
signals. A single average would hide whether a model chose the wrong function,
corrupted its arguments, called tools in the wrong order, or acted when it
should have stopped.

The “tool outcome” frontiers admit both single and compound systems. That is the
direct answer to whether a pair beats either component alone. A compound-only
frontier can explain how pairs differ, but it cannot establish that any pair is
worth using; only the shared frontier can do that.

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
- [`jev-candidate.json`](jev-candidate.json) configures the direct TypeSafe API
  route; [`jev-openrouter-candidate.json`](jev-openrouter-candidate.json)
  configures the measured OpenRouter Decisions route.
- [`qwen38-local-candidate.json`](qwen38-local-candidate.json) configures the
  current llama-swap OpenAI-compatible route.
- [`qwen38-direct-logits-candidate.json`](qwen38-direct-logits-candidate.json)
  and [`qwen38-gguf-generated-candidate.json`](qwen38-gguf-generated-candidate.json)
  isolate direct readout versus generated probabilities on one exact GGUF.
- [`qwen35-4b-direct-logits-coresident-candidate.json`](qwen35-4b-direct-logits-coresident-candidate.json)
  and [`qwen35-qwen38-direct-light-gate-cascade.json`](qwen35-qwen38-direct-light-gate-cascade.json)
  pin the local light gate and compound policy.
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
- [`run_bfcl_single_turn_panel.py`](run_bfcl_single_turn_panel.py) reuses the
  pinned upstream Granite/Qwen prompt handlers and AST scorer for the 40
  single-turn cases. The runner writes scored, prompt-free evidence.
- [`granite4-3b-8bit-omlx-bfcl-candidate.json`](granite4-3b-8bit-omlx-bfcl-candidate.json)
  and [`qwen38-oq4e-bfcl-candidate.json`](qwen38-oq4e-bfcl-candidate.json) pin the
  two single-model controls.
- [`granite4-qwen38-no-call-veto-cascade.json`](granite4-qwen38-no-call-veto-cascade.json)
  defines the winning selective-veto policy. The naive replacement policy is
  retained separately as a negative control.
- [`generated/bfcl-single-turn-r3-composed-catalog.json`](generated/bfcl-single-turn-r3-composed-catalog.json)
  contains the three normalized offerings. The two adjacent frontier snapshots
  are the result-of-record views.
- [`generated/routing-r3-composed-catalog.json`](generated/routing-r3-composed-catalog.json)
  contains the five matched direct-decision offerings. The adjacent
  `routing-r3-*-frontier.json` files are the result-of-record views.
- [`needle-environments-v3.0.1-manifest.json`](needle-environments-v3.0.1-manifest.json),
  [`needle3-local-candidate.json`](needle3-local-candidate.json), and
  [`needle3-jev-exact-call-guard.json`](needle3-jev-exact-call-guard.json) pin the
  voice-style home/media comparison. The `generated/needle-r3-*` artifacts are
  its normalized catalogs and two frontier snapshots.

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

Run Jev directly after placing a TypeSafe key in the environment:

```console
TYPESAFE_API_KEY=... uv run python \
  examples/structured-decision-frontiers/run_system_one_screen.py \
  examples/structured-decision-frontiers/routing-screen-v1.json \
  examples/structured-decision-frontiers/jev-candidate.json \
  --repetitions 3 --output jev-run.json
```

Or run the measured OpenRouter Decisions offering:

```console
OPENROUTER_API_KEY=... uv run python \
  examples/structured-decision-frontiers/run_system_one_screen.py \
  examples/structured-decision-frontiers/routing-screen-v1.json \
  examples/structured-decision-frontiers/jev-openrouter-candidate.json \
  --repetitions 3 --output jev-openrouter-run.json
```

Run SemIf-style direct logits through a llama.cpp-compatible endpoint that
supports first-token `top_logprobs`:

```console
uv run python \
  examples/structured-decision-frontiers/run_system_one_screen.py \
  examples/structured-decision-frontiers/routing-screen-v1.json \
  examples/structured-decision-frontiers/qwen38-direct-logits-candidate.json \
  --repetitions 3 --output qwen38-direct-run.json
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

The generated-probability runner uses TypeSafe's official
[`system-one-adapter`](https://github.com/typesafe-ai/system-one-adapter-python)
so a general LLM receives the same `Choice` contract. Direct Jev uses its typed
API; SemIf-style local runs use the same state, question, and declared options
but read option logits rather than generated JSON. Every path emits no prompt,
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

## Run the BFCL single-turn calibration

Install the small offline-runner dependency set, then provide a Gorilla checkout
at revision `6ea57973c7a6097fd7c5915698c54c17c5b1b6c8`:

```console
uv sync --extra bfcl-pilot
uv run python examples/structured-decision-frontiers/run_bfcl_single_turn_panel.py \
  examples/structured-decision-frontiers/bfcl-v4-offline-64-manifest.json \
  examples/structured-decision-frontiers/granite4-3b-8bit-omlx-bfcl-candidate.json \
  --bfcl-root /path/to/gorilla \
  --fallback-candidate \
    examples/structured-decision-frontiers/qwen38-oq4e-bfcl-candidate.json \
  --compound-candidate \
    examples/structured-decision-frontiers/granite4-qwen38-no-call-veto-cascade.json \
  --repetitions 3 --output compound-run.json
```

Run each single-model candidate without the two compound flags. Normalize each
run, compose the three catalogs, and evaluate the two `single-turn-*`
frontiers. The committed generated artifacts demonstrate that complete path.

## Run the Needle home/media calibration

Install the pinned Needle package and run the local control:

```console
uv sync --extra needle-pilot
NEEDLE_TELEMETRY=0 DO_NOT_TRACK=1 uv run python \
  examples/structured-decision-frontiers/run_needle_home_media_panel.py \
  examples/structured-decision-frontiers/needle-environments-v3.0.1-manifest.json \
  examples/structured-decision-frontiers/needle3-local-candidate.json \
  --repetitions 3 --output needle-run.json
```

Add the measured Jev exact-call guard:

```console
OPENROUTER_API_KEY=... NEEDLE_TELEMETRY=0 DO_NOT_TRACK=1 uv run python \
  examples/structured-decision-frontiers/run_needle_home_media_panel.py \
  examples/structured-decision-frontiers/needle-environments-v3.0.1-manifest.json \
  examples/structured-decision-frontiers/needle3-local-candidate.json \
  --compound-candidate \
    examples/structured-decision-frontiers/needle3-jev-exact-call-guard.json \
  --repetitions 3 --output needle-jev-run.json
```

## Current status

The schema, normalizer, exact routing screen, BFCL manifest, BFCL runner,
cascade runner, and frontier policies are implemented. See
[`status-2026-09-18.md`](status-2026-09-18.md) for the matched SemIf-style,
Jev, and local compound result, and [`status-2026-09-17.md`](status-2026-09-17.md)
for the Granite/Qwen tool result.

The next result-of-record step is a holdout no-call/tool-policy panel and a
small state-verifying workload. Granite Guardian or Qwen3Guard are sensible
guardrail-pair candidates after that harness exists. Real Jev is measured on
the routing screen above and remains queued for that later state-verifying
workload beside the local candidates.
