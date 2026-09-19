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
| [kev](https://github.com/jaredpalmer/kev) | Apple MPS; 4B/8B serve on a 32 GB Mac | Trained pointer head, exact sibling-question isolation, TypeSafe-compatible API, frozen in/out-of-domain suites, temperature calibration tooling | 4B/8B are research previews and fail the author's policy-reasoning release gate; calibration is workload-specific | **First** |
| [Bespoke Nimble](https://github.com/bespokelabsai/nimble) | Apple MLX; unquantized 9B weights are about 18 GB, suited to either 64 GB Mac | Qwen3.5-9B LoRA trained directly on typed decisions; 90.12% versus Jev's 93.21% on the same narrow 324-item synthetic screen | 2,048-token limit, 26 choices, narrow 2,676-example training set, and explicitly uncalibrated probabilities | **Second** |
| [jev-on-a-laptop](https://github.com/rorshopping/jev-on-a-laptop) | MLX on a 16 GB M5; tests 1.5B–8B models | Reproducible Mac benchmark and a full public TypeSafe-question comparison | Stock Qwen 7B was 73.8% versus Jev's 86.6%; confidence often stayed above 0.90 when wrong | Control/reference |
| [daseinlabs/open-jev](https://github.com/daseinlabs/open-jev) | Gemma 3 4B via MLX on Apple Silicon | Ready local API and full-option continuation scoring, including multi-token options | The project's own Jev quick-start comparison exposes overconfidence and two serious judgment disagreements | Control/reference |
| [system-one-open](https://github.com/mithalouni/system-one-open) | Modal-first Gemma 4 E2B/270M; weights were pending upload when reviewed | Trained decision model with demos and common-subset comparison | Not yet the cleanest reproducible home deployment | Later |
| [razorback16/openjev](https://github.com/razorback16/openjev) | Current server path requires NVIDIA >=24 GB; DiffusionGemma GGUF generation fits the 64 GB Macs | Closest open implementation of the diffusion-canvas theory | No current Apple server/readout contract; patched vLLM dependency | Research track |
| [CUA-S1-FORMS](https://huggingface.co/cua-ai/cua-s1-forms) | 706k parameters; reproduced on the M5 Max CPU and MPS | A trained, single-pass specialist for `fill/check/click/skip`; 196/196 on its published real-demo rows | Publisher-authored demo is small and skip-heavy; no media transfer claim; GitHub component docs contradict the current separate artifact release | Specialist pattern |

## Revised experiment order

The existing 24-case media screen should be run without changing its labels or
prompt contract in this order:

1. `kev-4b`, then `kev-8b` only if 4B misses the quality gate. It is the best
   combination of Mac support, decision-specific training, API compatibility,
   isolation, and honest evaluation artifacts.
2. Bespoke Nimble 9B through its MLX `ParallelScorer`. It is the strongest new
   Mac-native trained candidate, but its short context and narrow training need
   explicit failure accounting.
3. SemIf Qwen3.5 4B and `daseinlabs/open-jev` Gemma 3 4B as zero-shot controls.
   They test whether training adds value beyond direct option logits.
4. Keep hosted Jev as the closed reference and the deterministic hard gate as
   the deployment control.
5. Run the DiffusionGemma GGUF generation smoke separately. Do not block the
   Mac-native decision-model comparison on a llama.cpp structured-readout port.
6. Treat CUA-S1 as the precedent for a future media-specific scorer, not as a
   zero-shot media candidate. If enough adjudicated incidents exist, train one
   narrow action contract and compare it to every general candidate on the
   same source-disjoint holdout.

No candidate enters a write path from these screens. The useful first outcome
is higher review yield at zero observed unsafe decisions on a larger,
already-adjudicated holdout.
