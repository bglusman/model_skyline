# ShoeHorn applicability audit

Audit date: 2026-09-13. Source reviewed: ShoeHorn 0.3.0 at
`107e710ef34a75eeea3f6d74cc00d46030f4980a`; runtime checked against
llama.cpp build 10809 at `5266f24da`.

ShoeHorn is a worthwhile fitter for dense BF16/F16 GGUFs and conventional,
fully resident MoE checkpoints such as Ornith 1.5. It is not yet a safe
exact-fit path for Qwen3.8 Flash Next or other hybrid/recurrent/pageable-weight
architectures. The active experiment therefore uses Ornith, retains the exact
output bytes, and compares it with ordinary Q4_K_M/Q5_K_M artifacts rather than
assuming the solver's error objective predicts agent quality.

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
