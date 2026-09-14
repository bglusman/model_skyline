# Research, browsing, and general-assistant frontier benchmark map

This map turns three different real-world agent claims into separate reusable
frontier cohorts. It is a protocol map, not a result, benchmark redistribution,
or claim that any local artifact has passed. The task sources stay upstream;
public ModelSkyline evidence may retain only permitted task identifiers,
digests, aggregate scores, and prompt-free audit summaries.

## Why three frontiers

| Frontier | Reference workload | Quality axis | What changes independently of the model |
| --- | --- | --- | --- |
| `long-horizon-research-value` | ResearchClawBench | weighted research-report rubric score | workspace, target paper, rubric, multimodal judge, allowed tools |
| `web-browsing-value` | BrowseComp | answer accuracy under one pinned grader | live web, search/browser provider, tool budget, grader, execution date |
| `general-assistant-value` | GAIA | official normalized quasi-exact match | gated questions/attachments, open web, extraction tools, attachment policy |

Each quality axis is paired with p95 full-task wall time. Exact structured tool
correctness, locally validated 128K capacity, and zero swap growth are gates;
research also requires complete evidence grounding. Do not average the three
quality scores. Cross-frontier exact/near coverage is the portfolio view that
can reveal a model family useful in multiple roles without inventing a common
unit.

Only the scientific-workspace frontier has a quality eligibility floor: 50,
because ResearchClawBench defines that score as matching the source paper.
BrowseComp and GAIA publish no equivalent universal usefulness threshold, so
their recipes do not invent one; ordinary Pareto membership exposes the
observed quality/latency tradeoff after the shared correctness and safety gates.

## Scientific-workspace research

ResearchClawBench contains 40 real-science tasks across astronomy, chemistry,
earth science, energy, information science, life science, materials, math,
neuroscience, and physics. An agent receives curated data and reference
materials, writes analysis code, figures, and `report/report.md`, then a
multimodal LLM judge scores an expert-weighted checklist against a target paper.

Use repository revision `ed664513287a1fc60ae319e6bf7dbe7a62144135`.
Because the judge is part of the measurement, retain its exact route/version,
prompt digest, sampling, image rendering, retry policy, and per-item scores.
A judge failure is protocol-invalid, not a zero attributed to the local model.
An agent timeout after valid task execution begins is a quality-attributable
failure with the fixed timeout wall cost.

- A ten-task screen may use one predeclared task from each discipline, but is
  proxy evidence and cannot populate the measured frontier.
- The full 40-task cohort satisfies the recipe's 20-sample minimum and is the
  preferred measured population.
- Pin the entire task workspace, target-study material, rubric, harness config,
  sandbox image, and network policy. The repository commit alone does not prove
  that external/LFS material remained unchanged after acquisition.
- Report research score, p95 task wall, per-discipline coverage, report/figure
  artifact integrity, judge-invalid count, and task-level repeatability bounds.

## Live-web browsing

BrowseComp has 1,266 encrypted hard-to-find questions with short answers. Its
official evaluator decrypts tasks locally and uses a grader model to decide
whether the returned exact answer matches the reference; therefore this is not
a bytewise exact-match score. OpenAI explicitly requests that benchmark
examples not be republished. Never commit decrypted questions, answers, prompts,
or rendered trajectories.

Use `openai/simple-evals` revision
`652c89d0ca9df547706735883097e9537d40dc47`, including its
`browsecomp_eval.py`. At acquisition, also retain the encrypted CSV byte count
and SHA-256 because the evaluator downloads it from an unversioned blob URL.
Bind the grader model, grader prompt digest, search and browser provider
versions, maximum calls/pages/bytes, timeouts, retry behavior, and execution
window.

A deterministic, seeded subset may screen candidates, but paired candidates
must receive the identical encrypted task IDs and tool budgets. A full score or
a statistically declared estimate is required for measured publication. Web
unavailability and provider failures must be distinguished from model-quality
failures. A frozen-web replay is a different workload version, not another run
of the live-web cohort.

## Heterogeneous general-assistant work

GAIA has 466 real-world questions involving reasoning, web browsing, code and
tool use, and sometimes PDFs, spreadsheets, images, audio, video, or other
attachments. It uses a normalized quasi-exact-match scorer for short,
unambiguous answers. The official dataset is gated to reduce contamination and
forbids redistribution outside gated/private repositories.

Use `gaia-benchmark/GAIA` dataset revision
`682dd723ee1e1697e00360edccf2366dc8418dd9`. Public ModelSkyline artifacts may
retain split, level, opaque task ID, task/attachment digests, and aggregate
results, but not task contents or answers. Record whether media are supplied to
the model directly or transformed by tools; those are different capabilities
and offering cohorts.

Use the released validation answers for local development. Keep level 1, 2,
and 3 results visible even when an overall score is calculated, and bind the
browser/search providers and execution window for questions that touch the
live web. A text-only model using OCR or transcription tools is not equivalent
to a VLM receiving the original attachment.

## Common publication contract

For all three frontiers:

1. Bind exact model artifacts, runtime/parser builds, sampler/reasoning policy,
   context and output budgets, cache lifecycle, tools, harness, hardware, and
   power/concurrency conditions.
2. Batch one offering at a time under the exclusive runner lock; model swaps
   must not enter task latency or memory observations.
3. Retain complete request usage so uncached-input/cache-demand views remain
   eligible; incomplete timeout requests are lower bounds.
4. Publish per-task outcomes and uncertainty bounds without prompts, hidden
   answers, proprietary source material, or benchmark-prohibited examples.
5. Treat publisher scores only as candidate-screening annotations. Quantized
   MLX/GGUF/DFlash offerings earn quality only by running the declared cohort.

## Pinned primary sources

Sources were retrieved on 2026-09-14.

| Source | Pin | Protocol fact |
| --- | --- | --- |
| [ResearchClawBench](https://github.com/InternScience/ResearchClawBench/tree/ed664513287a1fc60ae319e6bf7dbe7a62144135) | `ed664513287a1fc60ae319e6bf7dbe7a62144135` | 40 tasks, 10 disciplines, curated workspaces, report/figure artifacts, weighted multimodal LLM-judge rubric |
| [BrowseComp announcement](https://openai.com/index/browsecomp/) | published 2025-04-10 | 1,266 hard-to-find live-web tasks and explicit non-redistribution request for examples |
| [BrowseComp evaluator](https://github.com/openai/simple-evals/blob/652c89d0ca9df547706735883097e9537d40dc47/browsecomp_eval.py) | `652c89d0ca9df547706735883097e9537d40dc47` | encrypted dataset acquisition, seeded subset behavior, response format, and grader-based correctness |
| [GAIA paper](https://arxiv.org/abs/2311.12983) | version observed 2026-09-14 | 466 questions, real-world tool/media/web coverage, and normalized quasi-exact-match scoring |
| [GAIA dataset](https://huggingface.co/datasets/gaia-benchmark/GAIA/tree/682dd723ee1e1697e00360edccf2366dc8418dd9) | `682dd723ee1e1697e00360edccf2366dc8418dd9` | gated split metadata and exact attachment files/digests |
