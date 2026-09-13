# ShoeHorn applicability audit

Audit date: 2026-09-13. Source reviewed: ShoeHorn 0.3.0 at
`107e710ef34a75eeea3f6d74cc00d46030f4980a` plus the local discovery fix at
`43908a1`; runtime checked against llama.cpp build 10809 at `5266f24da`.

ShoeHorn is a worthwhile fitter for dense BF16/F16 GGUFs and fully resident
checkpoints whose memory model has been validated. Ornith 1.5 is a hybrid
attention/Gated-DeltaNet MoE, not a conventional all-attention MoE, so its
current plan is a deliberately conservative experiment rather than an exact
memory-fit claim. ShoeHorn is not yet a safe exact-fit path for Qwen3.8 Flash
Next or other pageable-weight architectures. The active experiment retains the
exact output bytes and compares them with ordinary Q4_K_M/Q5_K_M artifacts
rather than assuming the solver's error objective predicts agent quality.

## Confirmed implementation findings

1. Hugging Face discovery queried only the top-level tree, so repositories with
   BF16 shards or imatrices in subdirectories failed before planning. Local
   commit `43908a1` requests a recursive tree, uses an explicit 1,000-entry
   limit, safely flattens selected cache filenames, and adds regression tests.
   All 24 tests pass. The commit is intentionally local and unpushed.
2. Pagination beyond 1,000 entries is still unimplemented. Revisions are also
   resolved through mutable `main`, so a future fetch contract should record
   and use the immutable repository revision in both API and download URLs.
3. Common GGML types outside ShoeHorn's encoding ladder become `Other` with a
   zero-byte block layout and can later panic in decoding. A Q2_K model can
   contain Q3_K tensors and reproduce this path. Unsupported input tensor types
   should fail once, before parallel scoring, with tensor/type names. Current
   GGML has substantially more types than ShoeHorn recognizes; see the
   [authoritative enum](https://github.com/ggml-org/llama.cpp/blob/master/ggml/include/ggml.h).
4. The solver respects its supplied tensor-byte ceiling, but “exact” does not
   mean a globally optimal multiple-choice knapsack solution. Its Lagrangian
   threshold plus one-way greedy upgrades can leave slack or miss a better
   exchange. The default also samples 128 rows per tensor unless
   `--exact-errors` is used. The defensible guarantee is “does not exceed the
   modeled tensor budget”; stronger optimality or “every spare megabyte” claims
   need a swap/local-search phase or a bounded exact frontier solve.
5. ShoeHorn invokes current llama.cpp with `-ngl 99` and leaves its independent
   auto-fit enabled. Because ShoeHorn already owns the fit decision, generated
   calibration/run/eval commands should use `-ngl all -fit off`; otherwise a
   second fitter can change the tested placement. Models over 99 layers also do
   not literally mean “all” under the current argument.
6. `--calibrate` parses classic KV and compute-buffer lines from a one-sequence
   `llama-cli` run. It does not fully account for recurrent state, repacking or
   model buffers, lazy tensor residency, server slot/checkpoint scaling, or a
   child that printed recognizable allocation lines and then failed. Until
   those are modeled, server calibration should be explicit about parallelism
   and context checkpoints and fail closed on nonzero child status.

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
leaving 45.39 GiB for weights. The solver assigned 307 tensors to F16, 99 to
Q8_0, 41 to Q6_K, 11 to Q5_K, and the remaining selected tensors across Q4_K,
IQ4_XS, IQ3_S, and IQ3_XXS. The modeled result is 10.981 bpw with 204,124 bytes
of budget slack.

```console
shoehorn fit bartowski/Ornith-1.5-35B-A3B-GGUF \
  --ctx 131072 --budget 51.84GiB --kv q8_0 --exact-errors \
  -o Ornith-1.5-35B-A3B-shoehorn-64gb-ctx131k-q8kv.gguf
```

Ornith exposes 40 hybrid layers but oMLX reports only 10 attention KV caches.
ShoeHorn currently charges classic KV for all 40 layers, so its 5.45 GiB Q8 KV
allowance is roughly four times the classic attention-only component; recurrent
state still needs direct measurement. That conservatism should make the output
safe to try, but may leave about 4 GiB that a future architecture-aware plan
could exchange for weight fidelity or context. Calibration was deliberately
disabled because the current one-sequence llama.cpp parser neither disables
llama.cpp auto-fit nor accounts for this recurrent layout. The real fit uses
exact row errors and will be accepted only after an auto-fit-disabled load and
runtime memory measurements.

## Why Qwen3.8 Flash is different

Flash Next has hybrid attention/recurrent state and a very large PLE table.
ShoeHorn currently multiplies KV dimensions by every layer, which overstates
128K attention KV by roughly four times for this architecture, while omitting
some recurrent/server state. It then budgets every weight byte as resident,
merges all input shards into one output, and removes `split.*` metadata. That
destroys the model's useful separation between ordinarily resident weights and
the pageable PLE shard. A plausible fit therefore requires architecture-aware
attention masks, recurrent-state accounting, residency classes, and
split-preserving output—not merely a different quantization target.

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
