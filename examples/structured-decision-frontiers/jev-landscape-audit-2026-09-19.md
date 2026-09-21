# Jev and OpenJev landscape audit — 2026-09-19

## Scope and correction

The first media-catalog evaluation did **not** review every project linked from
the Hacker News discussions. It followed hosted Jev, Laya, SemIf's readout
pattern, the DiffusionGemma vLLM change, and `razorback16/openjev`. That was not
enough: “OpenJev” currently names several unrelated projects with different
models, runtimes, and confidence semantics.

This audit enumerated every external URL in both relevant Hacker News trees via
their Algolia item JSON:

- [Introducing System One Models and Jev](https://news.ycombinator.com/item?id=49717558):
  55 unique comment links across 28 hosts;
- [OpenJev](https://news.ycombinator.com/item?id=49752041): 34 unique comment
  links across 17 hosts;
- 86 unique URLs after deduplication across both discussions.

Every linked implementation, model, paper, benchmark, or runtime that could
change the local-alternative decision received a source/README review. Vendor
documentation, applications, generic background, and off-topic links were
classified but were not treated as independent model evidence.

A later [CUA-S1 Show HN](https://news.ycombinator.com/item?id=49767564)
introduced a different category: a 706k-parameter form-action specialist built
from the `jevlike` option-attention design. Its repository, model, dataset, and
launch comments were reviewed separately; the pinned artifact reproduction is
in
[`cua-s1-specialist-intake-2026-09-19.md`](cua-s1-specialist-intake-2026-09-19.md).

The 2026-09-21
[Kev discussion](https://news.ycombinator.com/item?id=49783999) added another
17 unique external links. The materially new candidate or evaluation links were
Kev, Bespoke Nimble, OpenDecision, Jeff, JevBench, two pinned `openjev-sglang`
reports, an independent Banking77 comparison, and an embedding-plus-logistic
classifier argument. Kev and OpenDecision were subsequently measured on the
two already-frozen ModelSkyline screens; the results are below. The remaining
links were TypeSafe documentation/legal pages, applications, background, or
projects already covered here.

## The name collision

| Name | Actual project | Mechanism | Deployment implication |
| --- | --- | --- | --- |
| OpenJev HN submission | [SemIf](https://github.com/TheoLeeCJ/SemIf), formerly `TheoLeeCJ/openjev` | Direct option-token logits from ordinary open models; shared-prefix MLX path | Runs on Apple Silicon; conditional option probabilities, not calibrated correctness |
| `razorback16/openjev` | [DiffusionGemma server](https://github.com/razorback16/openjev) | Seeded diffusion canvas and read-only denoise logits through a patched vLLM | Jev-compatible API, but the published container needs NVIDIA >=24 GB and an unmerged vLLM change |
| `daseinlabs/open-jev` | [Gemma 3 4B scorer](https://github.com/daseinlabs/open-jev) | Scores complete option continuations in a prefix-shared MLX batch | Native Mac server at about 90 ms/request; its own quick-start comparison shows severe overconfidence and judgment errors |
| `ekzhang/openjev-sglang` | [Qwen3.6 35B-A3B server](https://github.com/ekzhang/openjev-sglang) | Shared prefill plus one-token label-logit reads per question | Stronger base model and compatible API, but its documented deployment is SGLang on a B200/Modal; probabilities explicitly uncalibrated |
| `xingwudao/OpenJev` | [API and SDK scaffold](https://github.com/xingwudao/OpenJev) | Mock answers today | Not a model candidate; real inference is only planned |

These projects share an interface idea, not weights, training, evaluation, or a
single implementation lineage. A result from one must not be attributed to
another.

## Implementations linked in the HN discussions

| Project | What it contributes | Evidence boundary | Relevance here |
| --- | --- | --- | --- |
| [SemIf](https://github.com/TheoLeeCJ/SemIf) | Reproducible direct-logit, prefix-reuse, parallel-suffix, MLX, and WebGPU baselines from 0.6B to 4B | The probabilities are normalized only over supplied options; the project does not claim Jev calibration | Useful zero-shot Mac control and implementation reference |
| [Laya](https://github.com/NandhaKishorM/laya) | A trained 421M ModernBERT decision head with `choice`, `score`, and `noul` | Short context and task-specific training; our media screen measured 37.5% accuracy | Already measured; unsuitable as the zero-shot media default |
| [DiffusionGemma vLLM PR 57250](https://github.com/vllm-project/vllm/pull/57250) | Seeded canvases, read-only steps, and fixed-slot logprobs | Draft upstream change, not a stable runtime contract | Important design, but not the shortest path on the existing Macs |
| [openjev-sglang](https://github.com/ekzhang/openjev-sglang) | Jev-compatible prefill-only API on Qwen3.6-35B-A3B | B200/Modal recipe; entropy concentration is explicitly not calibrated correctness | Hosted quality control, not a home-Mac deployment |
| [Eider](https://github.com/rdaum/eider) | Shared-state fork, batched question suffixes, and a compact 64-row output head for Gemma 4/Qwen models | Custom DGX Spark/CUDA 13 runtime and `/v1/decisions`, not the TypeSafe endpoint | Strong engineering reference for a future llama.cpp/MLX implementation |
| [jevlike](https://github.com/vinnylarouge/jevlike) | A small trainable option-attention architecture and game demonstrations | Starter model trained from scratch; no evidence of broad zero-shot knowledge or calibration | Interesting domain-specific research, not a ready catalog reviewer |
| [Qwen parallel constrained-decoding engine](https://huggingface.co/harshatheg/Qwen-2.5-1B-RLCD) | MLX KV-cache broadcasting and candidate-logit slicing | Despite the name, raw candidate softmax is not proof of RLCD-style calibration | Reusable Mac implementation technique |
| Earlier [sales-conversion](https://arxiv.org/abs/2503.23303) and [confidence-routing](https://arxiv.org/abs/2510.01237) work | Papers, a domain model, and a sales-conversation dataset predating Jev | Narrow sales and pre-generation routing objectives, not a general System One model | Background for Laya's lineage, not a media candidate |

The launch thread also links GLiNER2, GLiClass, DeBERTa zero-shot, Guidance,
DSPy adapters, and several Jev applications. They are useful adjacent baselines
or consumers, but none supplies a Jev-compatible, calibrated, local
general-decision model.

## Important projects that appeared during or after the threads

| Project | Local fit | What is materially new | Caveat | Priority |
| --- | --- | --- | --- | --- |
| [kev](https://github.com/jaredpalmer/kev) | Apple MPS; the measured Qwen3.5 4B path used the 64 GB M5 Max | Trained pointer head, exact sibling-question isolation, TypeSafe-compatible API, frozen in/out-of-domain suites, temperature calibration tooling | The measured 4B checkpoint did not transfer to either local policy screen and never abstained | Measured; reject for these workloads |
| [Bespoke Nimble](https://github.com/bespokelabsai/nimble) | Apple MLX; unquantized 9B weights are about 18 GB, suited to either 64 GB Mac | Qwen3.5-9B LoRA trained directly on typed decisions; 90.12% versus Jev's 93.21% on the same narrow 324-item synthetic screen | 2,048-token limit, 26 choices, narrow 2,676-example training set, and explicitly uncalibrated probabilities | **Second** |
| [OpenDecision](https://github.com/deepanwadhwa/OpenDecision) | ModernBERT-large on MPS; about 60–100 ms warm per request here | TypeSafe-compatible API over a conventional zero-shot/NLI classifier plus document retrieval and relation modes | Developer preview; the project calls its scores uncalibrated, and the measured generic classifier did not transfer | Measured; reject for these workloads |
| [Jeff](https://github.com/logan-markewich/jeff) | GLiFormer with Torch/ONNX CPU, MPS, or CUDA paths | A careful TypeSafe-compatible server, batching, limits, calibration temperature, and an eight-dataset comparison | The author's 1,600-item result is substantially behind Jev on AG News and is not evidence for the media/authority boundaries here | Useful server/reference; defer model run |
| Embeddings + logistic classifier | Tiny CPU classifier after a fixed embedding/feature extractor | Learns the operator's labels from tens or hundreds of adjudicated examples; cheap, inspectable, and easy to recalibrate | Requires representative labels and source-disjoint evaluation; it is not zero-shot | **Required domain-specific baseline** |
| [jev-on-a-laptop](https://github.com/rorshopping/jev-on-a-laptop) | MLX on a 16 GB M5; tests 1.5B–8B models | Reproducible Mac benchmark and a full public TypeSafe-question comparison | Stock Qwen 7B was 73.8% versus Jev's 86.6%; confidence often stayed above 0.90 when wrong | Control/reference |
| [daseinlabs/open-jev](https://github.com/daseinlabs/open-jev) | Gemma 3 4B via MLX on Apple Silicon | Ready local API and full-option continuation scoring, including multi-token options | The project's own Jev quick-start comparison exposes overconfidence and two serious judgment disagreements | Control/reference |
| [system-one-open](https://github.com/mithalouni/system-one-open) | Modal-first Gemma 4 E2B/270M; weights were pending upload when reviewed | Trained decision model with demos and common-subset comparison | Not yet the cleanest reproducible home deployment | Later |
| [razorback16/openjev](https://github.com/razorback16/openjev) | Current server path requires NVIDIA >=24 GB; DiffusionGemma GGUF generation fits the 64 GB Macs | Closest open implementation of the diffusion-canvas theory | No current Apple server/readout contract; patched vLLM dependency | Research track |
| [CUA-S1-FORMS](https://huggingface.co/cua-ai/cua-s1-forms) | 706k parameters; reproduced on the M5 Max CPU and MPS | A trained, single-pass specialist for `fill/check/click/skip`; 196/196 on its published real-demo rows | Publisher-authored demo is small and skip-heavy; no media transfer claim; GitHub component docs contradict the current separate artifact release | Specialist pattern |

## Frozen local follow-up — 2026-09-21

The Kev-thread candidates were not given a new prompt or an easier suite. Both
used the existing TypeSafe `Choice` contract and the same case order as hosted
Jev. The artifacts pin the candidate configuration, source/model revisions,
hardware, runtime, case hashes, and prompt-free per-case outcomes.

### Compound routing stress screen

| Candidate | Exact route | Unsafe | Abstained | p95 | Mean Brier |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hosted Jev 1.13 | **73.15%** | 26.85% | 2.78% | 0.399 s | **0.0700** |
| Kev 4B Qwen3.5, MPS | 55.56% | 33.33% | 0% | 0.620 s | 0.0987 |
| OpenDecision ModernBERT-large, MPS | 30.56% | 30.56% | 47.22% | **0.115 s** | 0.1467 |

Kev was deterministic across all three repetitions of every case but never
selected human review. A confidence fallback reached zero unsafe routes only at
0.90, where it handled no cases. OpenDecision was also deterministic; at 0.50
it handled only 5.56% and still made 3 unsafe decisions. At 0.90 it handled
none. Neither improves the transparent deterministic control on this authored
policy workload.

### Media-catalog safety screen

| Candidate | Exact route | Unsafe | Abstained | p95 | Mean Brier |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hosted Jev 1.13 | **79.17%** | **12.50%** | 38.89% | 0.433 s | **0.0940** |
| Laya typed decisions, CPU | 37.50% | 50.00% | 4.17% | 0.256 s | 0.2182 |
| Kev 4B Qwen3.5, MPS | 41.67% | 58.33% | 0% | 0.516 s | 0.2577 |
| OpenDecision ModernBERT-large, MPS | 16.67% | 58.33% | 4.17% | **0.066 s** | 0.3302 |

Kev's 0.95 confidence fallback removed the observed unsafe routes, but handled
only 12.5% of observations and reached 54.17% overall accuracy. The transparent
hard gate already handles 58.33% with zero unsafe decisions and exact agreement
with the authored oracle. OpenDecision reached zero unsafe routes only by
handling nothing at a threshold of 0.80. These results reject both candidates
for media-catalog advice without claiming they are generally poor classifiers.

The first request in each local server process was colder than the steady
state. The raw per-case artifacts retain it; the aggregate p95 values above do
not make a cold-start claim. Self-hosted dollar cost and energy were not
measured, so these points do not enter an active cost frontier.

The result-of-record files are:

- [`compound-routing-kev4b-qwen35-r3-result.json`](compound-routing-kev4b-qwen35-r3-result.json)
  and [`media-sync-kev4b-qwen35-r6-result.json`](media-sync-kev4b-qwen35-r6-result.json);
- [`compound-routing-opendecision-r3-result.json`](compound-routing-opendecision-r3-result.json)
  and [`media-sync-opendecision-r6-result.json`](media-sync-opendecision-r6-result.json); and
- the exact candidate configurations
  [`kev-4b-qwen35-mps-candidate.json`](kev-4b-qwen35-mps-candidate.json) and
  [`opendecision-modernbert-large-mps-candidate.json`](opendecision-modernbert-large-mps-candidate.json).

### What the new thread changes

It does not justify treating every Jev-shaped server as another member of one
model family. The mechanisms now span trained pointer heads, LoRA decision
heads, zero-shot NLI/encoder classifiers, direct token-logit reads, diffusion
readouts, hosted Jev, ordinary fast LLMs, and domain-trained linear
classifiers. Interface compatibility is not behavioral comparability.

[JevBench](https://github.com/fstandhartinger/jevbench) is useful discovery
evidence, but its 314-item composite mixes capability, measured latency, and
sometimes estimated hosted cost; its ranking also changed when the default
weights and cost scale changed. ModelSkyline should ingest its pinned raw
dimensions only after source/method review, not copy its composite rank.

The practical next baseline is therefore not another launch-week clone. It is
an embedding-plus-logistic or similarly small supervised classifier trained on
real adjudicated media cases and evaluated on a source-disjoint holdout. Nimble
9B remains a reasonable zero-shot research comparison only if that same holdout
exists; otherwise a third synthetic transfer run would add volume without
reducing deployment uncertainty.

## Revised experiment order

The existing 24-case media screen was run without changing its labels or prompt
contract. The remaining order is now:

1. Build the real, adjudicated, source-disjoint media holdout. Do not tune on
   the current 24 authored cases.
2. Add an embedding-plus-logistic or similarly tiny supervised classifier as
   the domain-specific baseline.
3. Run Bespoke Nimble 9B only on that unchanged holdout. Its short context and
   narrow training still need explicit failure accounting.
4. Run SemIf Qwen3.5 4B and `daseinlabs/open-jev` Gemma 3 4B as zero-shot controls.
   They test whether training adds value beyond direct option logits.
5. Keep hosted Jev as the closed reference and the deterministic hard gate as
   the deployment control.
6. Run the DiffusionGemma GGUF generation smoke separately. Do not block the
   Mac-native decision-model comparison on a llama.cpp structured-readout port.
7. Treat CUA-S1 as the precedent for a future media-specific scorer, not as a
   zero-shot media candidate. If enough adjudicated incidents exist, train one
   narrow action contract and compare it to every general candidate on the
   same source-disjoint holdout.

No candidate enters a write path from these screens. The useful first outcome
is higher review yield at zero observed unsafe decisions on a larger,
already-adjudicated holdout.
