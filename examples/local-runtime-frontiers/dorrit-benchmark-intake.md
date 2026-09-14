# Little Dorrit benchmark intake

**Decision as of 2026-09-14:** retain Little Dorrit as an opt-in benchmark for
future local vision-language models. Do not blend it into the current text-only
coding-agent score.

## What it adds

The [Little Dorrit Editor benchmark](https://dorrit.pairsys.ai/) asks a model to
read scanned book pages with handwritten proofreading marks and return the
edits as structured JSON. This is a useful combination of document vision,
OCR, editorial judgment, spatial/line reasoning, and output-format compliance.
Those capabilities are not represented by the current Terminal-Bench pilot.

The upstream [code repository](https://github.com/PAIR-Systems-Inc/little-dorrit-editor)
is MIT-licensed and was inspected at commit
`6824f6c24f27533a80d180460ead9cfd20c3895e`. Its predictor accepts an
OpenAI-compatible base URL and sends images as `image_url` message parts, so a
local vision route should require only a pinned configuration adapter rather
than a new inference protocol.

## Why it is not a current frontier

- The current Laguna, Qwen3.8, DS4, Muse, and Ornith local artifacts are tested
  as text routes; several manifests deliberately omit their vision towers.
- The technical report describes only six evaluation pages and reports wide
  confidence intervals. This is a focused specialist probe, not a general
  intelligence ranking.
- Scoring uses heuristic matching followed by an LLM judge. The judge model,
  prompt, revision, decoding settings, and repeated judgments therefore belong
  in the evidence identity.
- The repository includes two public sample pages but excludes evaluation data
  from Git. Its README points to a full Hugging Face dataset, which was not
  anonymously accessible during this intake. Dataset access, licensing, and an
  immutable content digest must be resolved before publishing local scores.
- Image resizing and recompression can affect results. Every comparison must
  use the same image bytes or explicitly split into separate cohorts.

## Proposed two-dimensional frontiers

Once at least two exact local vision offerings are measured, publish these as
separate specialist frontiers:

| Meaning of “best” | First dimension | Second dimension |
| --- | --- | --- |
| Accurate interactive document editor | Dorrit F1, maximize | end-to-end seconds per page, minimize |
| Accurate document editor under memory pressure | Dorrit F1, maximize | peak physical footprint or VRAM, minimize |

JSON parse success and complete page/run coverage should be eligibility gates,
not a hidden third axis. Precision and recall should remain visible diagnostic
signals because equal F1 can hide very different editing behavior.

## Admission checklist

1. Pin the upstream commit, evaluation image/annotation manifest, dataset
   license, prompt, few-shot examples, judge, and number of runs per page.
2. Route a vision-capable local model through llama-swap so the same exclusive
   memory lock applies as the text-model tests.
3. Record the exact model artifact, including vision projector/tower bytes,
   image preprocessing, runtime build, context/KV settings, and machine.
4. Capture raw prediction latency, token usage, parse failures, peak memory,
   per-page scores, and bootstrap intervals without treating judge prose as a
   model-quality signal.
5. Run a small human audit of judge decisions and a judge-sensitivity check
   before promoting a result from provisional to reviewed.

This makes Dorrit useful without implying that a good handwritten-editing
model is also the best coding agent—or that current text-only models failed a
vision task they were never eligible to enter.
