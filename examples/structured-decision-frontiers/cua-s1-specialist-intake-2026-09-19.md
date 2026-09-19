# CUA-S1-FORMS specialist intake — 2026-09-19

## Decision

Track [CUA-S1-FORMS](https://huggingface.co/cua-ai/cua-s1-forms) as evidence for
a **task-specialist decision component**, not as another zero-shot Jev
replacement. The published checkpoint is real, tiny, and locally runnable. It
does not answer the media-catalog decision problem without task-specific
training and an independent media holdout.

This distinction extends the structured-decision work in one important way:
specialization is orthogonal to system topology. A specialist can still be a
`decision_component` or one component of a `compound_model_system`. Its result
is comparable to Jev or a general model only when all candidates run the same
exact workload and action contract.

## What was verified

The [Show HN submission](https://news.ycombinator.com/item?id=49767564) links
the Cua repository. The current artifact sources are:

- source: [`trycua/cua`](https://github.com/trycua/cua/tree/main/libs/cua-s1),
  revision `9bbfa7dd3e27ca7f1861ede70aaca390174493f9`;
- model: [`cua-ai/cua-s1-forms`](https://huggingface.co/cua-ai/cua-s1-forms),
  revision `f54adbf447f4ca6ec259f529ee3f2e3e09f8cc71`;
- dataset: [`cua-ai/cua-s1-forms`](https://huggingface.co/datasets/cua-ai/cua-s1-forms),
  revision `8273f34778b99ac2e12d9f6e7d57dad99ae20845`.

The safe checkpoint is 2,828,784 bytes and contains 706,048 trainable
parameters. The loader verified its signed tensor/config document. Its model
code says the option-attention head and byte collation were adapted from
`jevlike` commit `94f5fd1`; CUA-S1 vendors that code rather than taking an
unpinned runtime dependency.

On the published 196-row `demo.jsonl`, a pinned local reproduction on the
64 GB Apple M5 Max using Python 3.12.8, PyTorch 2.14.0, and safetensors 0.8.0
produced:

| Device | Top-1 | Median single-element inference | p95 | Mean confidence | 10-bin ECE |
| --- | ---: | ---: | ---: | ---: | ---: |
| CPU | 196/196 (100%) | 2.19 ms | 2.57 ms | 0.9987 | 0.00128 |
| MPS | 196/196 (100%) | 2.96 ms | 10.71 ms | 0.9987 | 0.00128 |

Latency covers a warmed single-element forward pass plus softmax at concurrency
one; it excludes model loading, collation, and host/device transfer. It is a
local implementation check, not an end-to-end form-completion comparison.

The measured file SHA-256 values were:

- safetensors: `05954c1caf51c2fb6c13ea4acbfc88a2e7653dea192252bb51dc89e76a356ddc`;
- JSON sidecar: `62d31e2f9a001a8e9b6f8534c5194d07ebdd3f9d62ef1ac281906622992650ca`;
- demo rows: `4f43b442e79ba2e2ce731e27e9b8e340c2b5dfcaffc92d8ff564c34f115ff1ca`.

This reproduces the model card's demo top-1 result and establishes that the
checkpoint runs locally. It is not independent evidence of broad form
reliability: the artifact publisher also authored the 196-row demo set, which
contains 150 `skip`, 36 `fill`, 4 `check`, and 6 `click` decisions across only
three forms and three documents. The reported CUA-S1-versus-Jev result remains
vendor-authored and was not rerun because the hosted Jev comparison inputs and
full result artifact are not present in the linked source tree.

## Release inconsistency

The repository root now links the Hugging Face weights and dataset, but the
component README and model card at the same source revision still describe a
source-only release with no distributed weights or checkpoint result. The
model card on Hugging Face instead reports the architecture, artifacts,
training recipe, and results. The Hugging Face artifact revisions above are
therefore the evidence of record for this intake; the contradictory GitHub
component text is a provenance warning.

## Frontier treatment

Do not add the form checkpoint to the existing media or general-routing
frontiers. Accuracy numbers from different task distributions are not a shared
quality axis. For a specialist workload, record at least:

- an exact task/action contract and specialization scope;
- training-data provenance and a split key that prevents template, source, or
  entity leakage;
- in-scope top-1 and calibration;
- out-of-scope detection, abstention, selective accuracy, and coverage;
- wrong-action, wrong-target, and unsafe-action rates;
- end-to-end verified outcome after execution, not merely the selected action;
- latency, memory, model calls, and any general-model fallback demand.

The useful two-axis views are then:

1. in-scope decision accuracy versus latency;
2. selective accuracy versus handled coverage, gated on zero observed unsafe
   actions;
3. verified task success versus latency; and
4. verified task success versus heavy-model calls.

These become active ModelSkyline frontiers only after at least two candidates
have run the same pinned workload. Until then, adding a one-point frontier
would create a technically valid but decision-useless chart.

## Media and homelab experiment

CUA-S1 itself cannot decide book identity, edition compatibility, or metadata
repairs. The transferable idea is its small trained option head. A useful
media specialist should start with one narrow contract, for example:

- duplicate disposition: `same_edition / same_work_other_edition / different / review`;
- metadata action: `keep / swap_author_title / repair / review`; or
- Biblioaudio compatibility: `pair / incompatible / review`.

Train on adjudicated historical decisions plus deliberate one-fact contrast
pairs. Split by catalog source, work family, or incident lineage rather than
random rows. Compare the specialist, a deterministic rules baseline, hosted
Jev, and the best local general decision model on the untouched same-case
holdout. Keep the result advisory until it improves reviewer yield without
hiding a hard identifier, language, abridgement, coverage, or edition
contradiction.

The form executor also supplies a good safety pattern for later automation:
dry-run plans, snapshot/version-bound targets, re-reading state after mutation,
separate execute and submit permissions, and outcome verification. Those
boundaries are more transferable to BookLore, Audiobookshelf, Bookshelf, and
Biblioaudio than the form checkpoint's learned labels.
