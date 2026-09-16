# Small, realistic evaluation candidates

**Status as of 2026-09-16: survey plus pinned BFCL calibration manifest.** No
candidate on this page is a default ModelSkyline quality signal yet. The first
BFCL source revision, 64 exact case IDs, file blobs, answers, scorers, license,
and signal mapping are now pinned in
[`bfcl-v4-offline-64-manifest.json`](../examples/structured-decision-frontiers/bfcl-v4-offline-64-manifest.json).
It has not yet passed multi-model calibration. The purpose remains choosing a
small set of useful heuristics, not accumulating benchmarks.

## The simple goal

We want tests that answer questions people actually have:

- Will the model follow all of my instructions?
- Can it select and use tools correctly over more than one step?
- Can it finish a realistic coding or office task?
- Can it understand a document or a crowded application screenshot?
- Can it produce or edit a vector graphic that really renders?

Each test should produce a score a program can reproduce. A model judge is not
the default. A fixed judge may eventually be retained as clearly labeled
advisory evidence for qualities such as aesthetics, but it must not silently
become ground truth.

## Best first experiments

These are proposed calibration panels, not new official subsets. Before using a
panel, publish its exact task IDs, source revision, source-file hashes, prompt
wrapper, output extraction rule, generation settings, and grader version. A
panel result must never be presented as the upstream full-benchmark score.

| Candidate | What it usefully predicts | Grading | Full size | Proposed first run | Current assessment |
| --- | --- | --- | ---: | ---: | --- |
| [IFBench](https://github.com/allenai/IFBench) | Whether a text model obeys precise length, format, copying, counting, and content constraints | 58 published Python verifiers; no judge | 300 prompts | all 300 | **Run soon.** Cheap, easy to replay, and more discriminating than saturated IFEval. |
| [BFCL repository v4 data](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard) | Whether a model emits the right function and arguments, including parallel and multi-turn cases | AST and executable checks; no judge for the offline core | large, split into categories | 64 now-pinned offline cases | **Ready for calibration runs.** The panel spans eight offline categories and is not the newer BFCL V4 Agentic aggregate. |
| [AutomationBench](https://github.com/zapier/AutomationBench) | Whether an agent leaves simulated CRM, inbox, calendar, support, finance, and HR systems in the correct state | Positive and negative final-state assertions; no judge | 600 scored public tasks | 30 pinned tasks, five per domain | **Highest-value realistic pilot.** More expensive than BFCL, but much closer to actual work. |
| [SVGEditBench](https://github.com/mti-lab/SVGEditBench) | Whether a text model can make a requested SVG edit without corrupting the image | Render target comparison; compression also examines code | 600 prompts: 100 images × 6 edits | 60 pinned prompts, ten per edit | **Run as a calibration.** Excellent metric shape, but mostly single-attribute edits may now be too easy, and upstream does not ship a turnkey scorer. |
| [OCRBench](https://github.com/qywh2023/OCRbench) | Whether a vision model can read scenes, documents, key fields, and handwritten formulas | Published answer matching by task type | 1,000 prompts in v1; 10,000 in v2 | 100 pinned v1 prompts, 20 per component | **Useful first VLM screen.** Small enough after stratification, but check current saturation before promotion. |
| [ScreenSpot-Pro](https://github.com/likaixin2000/ScreenSpot-Pro-GUI-Grounding) | Whether a vision model can locate a requested control in a dense professional application | Predicted point inside the annotated target box | 1,581 tasks | 92 pinned tasks: two text and two icon targets per application | **Useful VLM grounding screen.** Specialized GUI models have improved rapidly, so report general-purpose and specialized models separately. |

The proposed run order is IFBench, BFCL, SVGEditBench, AutomationBench, then
OCRBench and ScreenSpot-Pro once at least two viable local VLM offerings are
available. The first three test adapters and score dispersion cheaply.
AutomationBench then tests whether those micro-signals predict a longer task.

### Why AutomationBench is especially promising

Its public tasks use local simulated applications and inspect the final state.
The strict score passes only when every assertion passes, while partial credit
shows how close the agent came. The upstream CLI accepts a custom OpenAI-style
base URL, named tasks, and a concurrency limit. This is a good match for
llama-swap and for comparing the same exact offering locally and remotely.

The upstream repository currently reports only 50.3% strict success for its
best listed model on the 600 public scored tasks. That is evidence of useful
headroom, not proof that our proposed 30-task panel has the same difficulty.
We must calibrate and publish uncertainty for the fixed panel itself.

### Why IFBench is not just another trivia score

IFBench combines held-out real-user prompts with 58 mechanically verifiable
constraints. The paper introduced it because the older IFEval constraint set
had become saturated. It reported GPT-4.1 and Claude 3.7 Sonnet below 50% on
IFBench, while its strongest reported result, o3, reached 69.3%. Newer systems
have improved, so we should first confirm that our local and remote candidates
still spread out on its 300 public prompts.

## Larger second-stage experiments

These tests are realistic and objectively scored, but their environment cost
makes them finalist evaluations rather than a first filter.

| Candidate | Why it matters | Why it is second-stage |
| --- | --- | --- |
| [SWE-bench Verified Mini](https://hal.cs.princeton.edu/swebench_verified_mini) | 50 human-validated real GitHub issues with unit-test outcomes | Repository containers and long agent loops; it also overlaps the current Harbor coding signal. |
| [SWE-bench Multimodal v2](https://www.swebench.com/multimodal) | Real JavaScript/TypeScript issues whose screenshots, recordings, diagrams, or renderings matter to the fix | 480 tasks and repository environments; define a small fixed pilot only after validating the newly opened v2 artifacts. |
| [AndroidWorld](https://github.com/google-research/android_world) | 116 programmatically graded tasks across 20 real Android apps, dynamically parameterized to resist memorization | Requires an emulator and a computer-use agent, so it measures a complete harness as well as the model. |
| [OSWorld](https://github.com/xlang-ai/OSWorld) | Real desktop and cross-application tasks with execution-based graders | Powerful but operationally much heavier and more harness-sensitive than a one-image screen. |
| [CodeClash](https://github.com/CodeClash-ai/CodeClash) | Goal-directed code improvement judged by actual simulated competition | Relative to opponents and stochastic simulations, so it is better for harness tournaments than a stable model-quality axis. |

## Interesting, but not in the first queue

- [Little Dorrit](https://dorrit.pairsys.ai/) is a compelling document-editing
  task, but the reviewed implementation does not yet provide the complete,
  independently pinned data-and-judge package needed for a result of record.
- [CCTU](https://github.com/Junjie-Ye/CCTU) has 200 executable complex-tool
  cases and reports strict success below 20% for every evaluated model. It may
  be very discriminating, but it is new enough that we should audit the tasks
  and look for a floor effect on local models before adoption.
- [tau3-bench](https://github.com/sierra-research/tau2-bench) measures useful
  policy-following conversations and grades tool state, but an LLM simulates
  the user. That extra model can change the tested trajectory, so
  AutomationBench is the cleaner first stateful-agent experiment.
- [StructEval](https://arxiv.org/abs/2505.20139) and
  [SVG-Bench](https://github.com/therealtimex/star-vector-svg) cover attractive
  structured and visual-generation tasks. Their learned visual metrics are
  useful research signals but do not meet the no-oracle preference for a
  headline score.
- JSON-schema-only suites are useful for comparing constrained-decoding
  engines. They do not by themselves show that a model chose semantically
  correct field values, so they belong with runtime compatibility evidence.

## A scoreable successor to the pelican test

Simon Willison's [pelican-on-a-bicycle archive](https://github.com/simonw/pelican-bicycle)
is valuable precisely because a person can see failures that ordinary coding
benchmarks miss. It is not a reproducible scalar quality measure. Free-form
appearance and aesthetics do not become objective just because a judge emits a
number.

Use three evidence layers instead:

1. **Deterministic eligibility gates:** exactly one parseable SVG; renders in a
   pinned engine; no scripts, network requests, raster images, or embedded
   base64; non-blank output; and declared token, element, and render-time
   budgets.
2. **Mechanically specified graphics tasks:** requested edits, transforms,
   layout relationships, colors, cropping, and preservation regions with
   generated target renders or pixel masks. SVGEditBench is the starting point;
   compound multi-edit cases can extend it if the public tasks saturate.
3. **Separate subjective review:** retain pelican-like prompts and blind human
   pairwise comparisons for aesthetics and semantic coherence. Report those as
   human preference evidence, not as the deterministic SVG score. A frozen
   vision-embedding similarity can be recorded as advisory diagnostics, never
   as the sole quality axis.

This cannot fully quantify “beautiful pelican” without human or learned visual
judgment. It can quantify whether a model produces safe, valid, efficient SVG
and whether it performs difficult visual edits correctly. Keeping those claims
separate is more informative than a single opaque judge score.

## How a candidate becomes ModelSkyline evidence

For every adopted benchmark:

1. Pin source revision, task bytes and IDs, licenses, grader, prompt wrapper,
   model artifact, runtime, decoding settings, tool schema, and agent harness.
2. Run strong and weak calibration offerings first. Reject panels with ceiling,
   floor, unstable, or obviously gameable results.
3. Preserve per-task outcomes. For a small panel, publish a binomial interval
   and paired task differences; do not imply full-set precision.
4. Keep each quality component separate. Do not average instruction following,
   coding, OCR, GUI grounding, and SVG editing into “intelligence” by default.
5. Build ordinary two-axis frontiers from one exact offering, for example:
   strict task success versus end-to-end task time, strict task success versus
   peak memory, or strict task success versus remote cost.
6. Never borrow the benchmark score from one quantization, runtime, harness, or
   context setting for another offering.
