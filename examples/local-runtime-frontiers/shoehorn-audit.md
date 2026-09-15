# ShoeHorn applicability audit

Audit date: 2026-09-15. Source reviewed: ShoeHorn 0.3.0 at
`107e710ef34a75eeea3f6d74cc00d46030f4980a` plus proposed fixes through
`7a4b093`; runtime checked against llama.cpp build 10809 at `5266f24da`.
Those fixes are published in upstream PR
[#3](https://github.com/notactuallytreyanastasio/shoehorn/pull/3), which remains
open and mergeable.

[ShoeHorn](https://github.com/notactuallytreyanastasio/shoehorn) is a worthwhile
fitter for dense BF16/F16 GGUFs and fully resident checkpoints whose runtime
memory model has been validated. Its result is a valid exact local offering:
it may enter a hardware-filtered frontier when the source revision, plan,
output bytes, context/KV settings, and measurements are all retained. Ornith
1.5 is a hybrid attention/Gated-DeltaNet MoE, not a conventional all-attention
MoE, so its current plan is a deliberately conservative experiment rather than
an independently proven memory decomposition. ShoeHorn is not yet a safe fit
path for pageable-weight architectures. The active experiments retain exact
output bytes and compare them with ordinary artifacts rather than assuming the
solver's error objective predicts agent quality.

## Confirmed implementation findings

1. Hugging Face discovery queried only the top-level tree, so repositories with
   BF16 shards or imatrices in subdirectories failed before planning. PR #3 now
   resolves `main` once to an immutable revision, traverses the recursive tree
   with validated pagination, preserves safe nested cache paths under a
   revision-specific directory, and uses the same revision for downloads.
2. Common GGML types outside ShoeHorn's encoding ladder become `Other` with a
   zero-byte block layout and can later panic in decoding. A Q2_K model can
   contain Q3_K tensors and reproduce this path. Unsupported input tensor types
   should fail once, before parallel scoring, with tensor/type names. Current
   GGML has substantially more types than ShoeHorn recognizes; see the
   [authoritative enum](https://github.com/ggml-org/llama.cpp/blob/master/ggml/include/ggml.h).
3. The solver respects its supplied tensor-byte ceiling, but “exact” does not
   mean a globally optimal multiple-choice knapsack solution. Its Lagrangian
   threshold plus one-way greedy upgrades can leave slack or miss a better
   exchange. The default also samples 128 rows per tensor unless
   `--exact-errors` is used. The defensible guarantee is “does not exceed the
   modeled tensor budget”; stronger optimality or “every spare megabyte” claims
   need a swap/local-search phase or a bounded exact frontier solve.
4. ShoeHorn invokes current llama.cpp with `-ngl 99` and leaves its independent
   auto-fit enabled. Because ShoeHorn already owns the fit decision, generated
   calibration/run/eval commands should use `-ngl all -fit off`; otherwise a
   second fitter can change the tested placement. Models over 99 layers also do
   not literally mean “all” under the current argument.
5. `--calibrate` parses classic KV and compute-buffer lines from a one-sequence
   `llama-cli` run. It does not fully account for recurrent state, repacking or
   model buffers, lazy tensor residency, server slot/checkpoint scaling, or a
   child that printed recognizable allocation lines and then failed. Until
   those are modeled, server calibration should be explicit about parallelism
   and context checkpoints and fail closed on nonzero child status.
6. Mixed plans previously derived GGUF `file_type` from the largest individual
   solved tensor. Muse therefore displayed as F16 because its single output
   tensor is 2.50 GiB, even though Q6_K accounts for the largest aggregate byte
   assignment. Commit `ca565f8` aggregates bytes by quantization type and adds a
   regression test. The PR's release test suite passes. Existing artifacts created before
   the fix retain a stale display label but unchanged tensor encodings.

## Ornith plan observed on this host

The source is Bartowski's two-shard BF16 checkpoint at immutable repository
revision `64b0493d34a5ca4c1b4ad67bb99b41d74b4f07d6`: 71.1 GB decimal (66.19
GiB), 753 tensors, plus a 192,223,936-byte importance matrix. The two source
LFS SHA-256 object IDs are `b50ba22a501bb2cb03c48bcc4b84d72f5d82c949afa13047369931d62e5522e9`
and `626fc796b98a4fd64da2d48d3b0bc3ee4ccc1acc45072de5531a1dc5722330ec`;
the downloaded imatrix hashes to
`8d5b16938373b678b68c3081ef3f6985b789df9b25c18af130909658a47c0228`.
With
`--ctx 131072 --budget 51.84GiB --kv q8_0 --exact-errors`, ShoeHorn reserves a
modeled 5.45 GiB for KV, 517 MiB for compute, and 512 MiB for runtime overhead,
leaving 45.39 GiB for weights. The earlier sampled-error dry plan assigned 307
tensors to F16 and left 204,124 bytes of modeled weight slack. The completed
exact-row-error solve took about 50 minutes on the M5 Max and instead assigned
317 tensors to F16, 91 to Q8_0, 38 to Q6_K, 13 to Q5_K, 2 to Q4_K, 17 to
IQ4_XS, 2 to IQ3_S, and 4 to IQ3_XXS. It retained the same 10.981 overall bpw
but used the budget to within 48,476 bytes.

```console
shoehorn fit bartowski/Ornith-1.5-35B-A3B-GGUF \
  --ctx 131072 --budget 51.84GiB --kv q8_0 --exact-errors \
  -o Ornith-1.5-35B-A3B-shoehorn-64gb-ctx131k-q8kv.gguf
```

Ornith exposes 40 hybrid layers while oMLX reports only 10 full attention KV
caches. That does **not** prove llama.cpp allocates only one quarter as much:
the current standard llama.cpp KV path creates per-layer K/V tensors using one
common `kv_size`, including sliding-window layers. ShoeHorn's all-layer charge
may therefore match current llama.cpp allocation even though it overstates the
model's semantic attention window. Recurrent state and the actual runtime
allocation still need direct measurement before claiming either overcount or
reclaimable space. See the current
[`llama-kv-cache.cpp`](https://github.com/ggml-org/llama.cpp/blob/master/src/llama-kv-cache.cpp).
Calibration was deliberately disabled because the current one-sequence
llama.cpp parser neither disables llama.cpp auto-fit nor accounts for this
recurrent layout. The real fit uses
exact row errors and produced a 48,747,873,632-byte GGUF whose file SHA-256 is
`138e4fad79b6c11b9e8cd206ba7bd442482efe599550f11a629b6af8f181b150`.
Its retained file manifest is
[`artifacts/ornith-1.5-35b-a3b-shoehorn-ctx131k-q8kv-manifest.json`](artifacts/ornith-1.5-35b-a3b-shoehorn-ctx131k-q8kv-manifest.json).

The first runtime gate passed with llama.cpp build 10809: all layers on Metal,
131,072-token context, Q8_0 K/V, flash attention on, and auto-fit off. A
single-turn deterministic generation returned `SHOEHORN_SMOKE_OK` exactly. The
first uncached process reached 48,914,366,464 bytes of RSS, reported no process
swaps, and exited cleanly. RSS includes mapped file pages and is not an estimate
of active Metal allocation; the result confirms operational fit, not the
accuracy of ShoeHorn's modeled 51.84 GiB decomposition.

The first fixed-position M5 microbenchmark is also complete. At the same
llama.cpp build/configuration and pp2048/tg512 position, the custom artifact's
medians are 2,664.58 prompt token/s and 74.7645 decode token/s. The official
Q4_K_M control reaches 3,022.92 and 113.748 respectively. The custom artifact
therefore retains 88.1% of Q4 prompt throughput but only 65.7% of its decode
throughput while occupying 2.245 times the file bytes. That is an efficiency
regression unless a separate fidelity test demonstrates a compensating gain.
The Q5_K_M control reaches 2,775.23 prompt token/s and 105.721 decode token/s;
the custom artifact uses 1.923 times its bytes while retaining 96.0% and 70.7%
of those rates. The captures preserve the literal `-ngl` choice; Q4 used 99
and Q5/custom used -1, but all three report every 40-layer target block placed
on Metal and no CPU MoE placement.

The pinned six-chunk WikiText-2 slice did not demonstrate that gain:

| Artifact | Bytes | PPL | Reported standard error | Ignored MTP bytes |
| --- | ---: | ---: | ---: | ---: |
| Official Q4_K_M | 21,713,462,848 | 10.0669 | 0.28042 | 546,703,360 |
| Official Q5_K_M | 25,347,532,544 | 10.0506 | 0.28062 | 618,399,744 |
| ShoeHorn exact-error mixed | 48,747,873,632 | 10.1321 | 0.28437 | 1,689,307,136 |

All captures use identical corpus bytes, tokenizer/runtime build, 4,096-token
context, Q8_0 K/V, and six chunks. The point estimates differ by much less than
their reported uncertainty, so they do not establish a statistically reliable
PPL ordering. They do establish that this nearly doubled weight budget produced
no obvious held-out-language-modeling improvement. The raw captures also expose
a concrete planning inefficiency: ordinary target-only llama.cpp ignores the 20
`blk.40` MTP tensors, but the mixed plan spends 1.689 GB on them, including three
512 MiB expert tensors retained at F16. A target-only fit should allow those
tensors to be omitted or forced to a floor quant, while a speculative-runtime
fit must account for them as a separate draft-head cohort. Today ShoeHorn does
neither. This result makes the current artifact provisionally dominated on the
M5; coding/tool and long-context integrity remain useful only as diagnostics of
whether a task-specific exception exists.

## Why Qwen3.8 Flash is different

Flash Next has hybrid attention/recurrent state and a very large PLE table.
ShoeHorn currently charges ordinary KV dimensions for every layer and omits
some recurrent/server state; the net accuracy of that estimate depends on the
exact llama.cpp cache implementation and must be measured. More importantly,
it budgets every weight byte as resident, merges all input shards into one
output, and removes `split.*` metadata. That destroys the model's useful
separation between ordinarily resident weights and the pageable PLE shard. A
plausible fit therefore requires recurrent-state accounting, residency classes,
and split-preserving output—not merely a different quantization target.

## RTX 5060 Ti 16 GB target

The new discrete-GPU target is
[`inference-vm-rtx5060ti16`](hardware/inference-vm-rtx5060ti16.json): NVIDIA
reports 16,311 MiB of VRAM, while the VM has 9.64 GiB of ordinary RAM. Existing
Ollama processes must be unloaded before measuring usable VRAM; the inventory
snapshot found only 4,080 MiB free because two unrelated models were resident.
The planning budget therefore uses the unloaded capacity minus an explicit
reserve, not that contaminated free-memory snapshot.

The first candidate is Laguna XS 2.1 from a pinned BF16 GGUF plus pinned
importance matrix. Three distinct targets are intentionally kept separate:

| Target | Why test it | Frontier interpretation |
| --- | --- | --- |
| 32K context, Q8 KV | Leaves the largest weight budget and should maximize quantized quality | Useful small-context 5060 offering; does not satisfy the 128K gate |
| 128K context, Q4 KV | Exchanges KV fidelity for enough weight space to attempt the desired context | Valid 128K candidate only if exact retrieval and quality pass |
| 128K context, Q8 KV | Higher-fidelity cache control | May be infeasible fully resident; a failed plan/load is still useful admission evidence |

Each successful output will retain the ShoeHorn source revision, importance
matrix digest, commit, solved tensor assignments, output SHA-256, llama.cpp
build, actual placement, and measured peak VRAM. A plan that fits only by
spilling heavily into the VM's limited RAM is not equivalent to a resident-GPU
offering and must remain a separate placement profile.

The first exact 32K output is now complete. ShoeHorn used exact row errors for
all 476 tensors covered by the importance matrix and filled its modeled weight
budget to within 512 bytes. The result is 12,481,779,328 bytes at 2.985 average
bits per weight. Its plan assigns most routed-expert tensors between IQ2_XXS
and IQ4_XS, while retaining two 512 MiB tensors at F16. The pinned plan and
output hashes are in
[`laguna-xs21-shoehorn-5060-ctx32k-q8kv-exact-plan.json`](artifacts/laguna-xs21-shoehorn-5060-ctx32k-q8kv-exact-plan.json)
and the one-file
[`artifact manifest`](artifacts/laguna-xs21-shoehorn-5060-ctx32k-q8kv-exact-manifest.json).

The 128K plans make the context tradeoff concrete. With Q8 KV, 128K is not a
fully resident option: KV leaves only 3.65 GiB for weights, below ShoeHorn's
8.13 GiB minimum. Q4 KV is arithmetically feasible, but leaves only 8.65 GiB
for weights and drives the sampled plan down to 2.222 average bits per weight.
That is a severe enough compression step that it should not consume an exact
solve until the 32K fit demonstrates usable quality.

The fair stock control is Bartowski's pinned 12,358,537,600-byte Q2_K_L, only
123,241,728 bytes smaller than the custom output. On the 5060 it passes 3/3
exact automatic calls from a 30-tool schema and 3/3 exact retrieval attempts at
30,000 input tokens. Its pp2048/tg512 medians are 2,070.5 prompt token/s and
139.458 decode token/s; the pinned six-chunk WikiText slice reports
12.8376 +/- 0.38260 perplexity.

The stock control also exposed a host-side configuration trap. Default
memory-mapped loading briefly charged 9.51 GB of PSS to a VM with only 9.64 GiB
of RAM and grew host swap by as much as 130 MB. llama.cpp `--load-mode none`
reduced the full tool/retrieval run's host peak below 0.94 GB with no loss of
correctness or material latency change. All six measured requests then had
zero swap delta; whole-run swap ended 3.4 MB below its starting value, although
the host-wide sampler saw a transient 0.5 MB rise during load. The direct-load
route is therefore the eligible operational control. The mmap capture remains
diagnostic evidence rather than a recommended route.

The matched custom result is a useful negative result rather than a new model
recommendation:

| Same 5060, 32K Q8 KV | Stock Q2_K_L | ShoeHorn 2.985 bpw | Winner |
| --- | ---: | ---: | --- |
| Prompt processing, pp2048 median | 2,070.5 token/s | 2,738.48 token/s | ShoeHorn |
| Generation, tg512 median | 139.458 token/s | 153.091 token/s | ShoeHorn |
| Exact automatic tool call | 3/3 | 3/3 | ShoeHorn on lower latency |
| Exact 30K retrieval | 3/3 | 0/3 | Stock |
| Six-chunk pinned-corpus perplexity | 12.8376 +/- 0.38260 | invalid: all chunks `nan` | Stock |

The custom fit is 32.3% faster for prompt processing and 9.8% faster for
generation, despite being about 1% larger. Its 2.307-second median synthetic
tool call also beats stock's 2.732 seconds. Those wins do not override the
retrieval and numerical failures. A second perplexity diagnostic with flash
attention disabled and F16 KV still returned `nan` for all six chunks, so the
failure is not specific to the tested flash-attention/Q8-KV combination.
Cross-backend testing would be needed to distinguish a CUDA quantization-kernel
problem from a generally damaged artifact.

The machine-readable results intentionally preserve all three views. The
[throughput frontier](generated/laguna-xs21-5060-quant-short-throughput-frontier.json)
and [synthetic-tool frontier](generated/laguna-xs21-5060-quant-tool30-p2048-o256-frontier.json)
select the custom fit for their narrow two-axis definitions. The
[retrieval frontier](generated/laguna-xs21-5060-quant-retrieval-p30000-frontier.json)
selects stock and explicitly rejects the custom fit. Perplexity is a promotion
gate, not a hidden third frontier axis. Consequently the custom artifact is not
registered in llama-swap and the 128K exact solve is stopped.

This suggests two concrete ShoeHorn improvements: run a small numerical and
retrieval canary before publishing or spending more time on a fit, and expose
enough per-tensor diagnostics to help separate solver/format defects from
backend-kernel defects. It also demonstrates why a successful load or tool
call alone is too weak a promotion gate for an aggressively mixed artifact.

Recent upstream reports also show why runtime version and server configuration
must remain evidence identity for hybrid models: recurrent checkpoint handling
has active edge cases in [llama.cpp issue 27211](https://github.com/ggml-org/llama.cpp/issues/27211),
while Qwen3.8 support itself is recent ([PR 27742](https://github.com/ggml-org/llama.cpp/pull/27742)).
These are runtime caveats rather than ShoeHorn defects, but they make a smoke
load insufficient quality or long-context evidence.

## Experiment acceptance criteria

For the Ornith output to become a serious candidate rather than a novelty:

- record the immutable BF16 source revision, imatrix digest, ShoeHorn commit,
  complete plan, output digest, and actual llama.cpp placement;
- verify load and generation with auto-fit disabled;
- benchmark the exact bytes on both 64 GB machines with the same llama.cpp
  build and fixed pp2048/tg512 position;
- compare held-out perplexity against ordinary Q4_K_M and Q5_K_M controls;
- run tool-call parsing/correctness and a long-context retrieval position; and
- keep it only if task or fidelity gains compensate for added bytes, memory,
  load time, and likely lower decode throughput.
