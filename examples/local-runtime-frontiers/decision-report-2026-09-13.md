# Local-model decision report — 2026-09-13

This is the handoff state after the first M5 Max/M1 Max local-frontier pass. All
rankings are provisional and scoped to exact artifacts, runtimes, hardware, and
workload positions. No publisher full-precision score has been assigned to a
local quant.

## Recommended set

| Candidate | Best current role | Evidence-backed advantage | Important limitation |
| --- | --- | --- | --- |
| Qwen3.8 27B oMLX DFlash2 | Warm iterative agent sessions | 30-tool warm-prefix run: 0.812 s median end-to-end, 67.50 decode token/s, exact 3/3; 126K exact retrieval with 39.8 GB peak active memory | 126K prefill was 486 s versus 281 s for baseline F16 KV; DFlash is not uniformly faster |
| Qwen3.8 27B oMLX baseline/F16 KV | Cold or long-prefill control | Exact 30-tool calls and 126K retrieval; fastest measured 126K prefill among the three oMLX profiles | Roughly half DFlash's short-position decode rate; higher retained memory than TQ4 |
| Qwen3.8 Flash Next on DS4 target-only | Largest-model 64 GB experiment | Exact retrieval through 125,964 input tokens, about 49 token/s decode there, exact 30-tool calls 3/3 | Aggressive 47.00 GiB resident plan plus demand-paged 32.0 GB PLE sidecar; no matched M1 run yet |
| Ornith 1.5 Q4_K_M | Raw throughput / cross-Mac control | 3,022.92 prompt and 113.748 decode token/s on M5; same bytes measured on M1 | Tool/coding and long-context integrity portfolio is incomplete |
| Muse Glimmer official Dynamic Q4_K_XL, target-only | Dense agent/tool alternative | Exact tool selection cold and warm; official quant beats the custom ShoeHorn fit | Only 24.9124 token/s in llama.cpp microbenchmark; long-context retrieval not yet measured |

The practical default remains workload-dependent. oMLX Qwen3.8 is the best
measured warm agent loop; its baseline/F16 profile is the safer long-prefill
control. DS4 target-only is the most ambitious model that fits this 64 GB host.
Ornith is the throughput leader. Muse is worth retaining for task-level agent
quality comparisons, but not for speed.

Muse DFlash2 remains selectable but is labeled experimental in both OMP and
OpenCode. Two cold/cache-miss runs returned the same wrong non-tool answer;
cache-warm runs returned the exact tool call in 6/6 trials. With prompt reuse
explicitly disabled, two consecutive requests remained uncached and reproduced
the wrong answer byte-for-byte. Target-only passed both cold and warm. This is a
reproducible DFlash + llama.cpp prompt-reuse semantic divergence, not a router
load-state artifact.

## Local Pareto frontiers

Four complementary local frontier views were created. They use Model Skyline's
ordinary two-axis Pareto engine, but introduce a stricter local offering
identity: hardware, exact artifact bytes, checkpoint, quantization, runtime
commit, Metal/acceleration profile, KV type, cache enablement, speculative
decoder, physical context capacity, and harness configuration remain distinct.

| Frontier | Axes | Question answered | Current state |
| --- | --- | --- | --- |
| `short-throughput-envelope` | Maximize prompt and decode token/s | Which exact hardware/artifact/runtime combination is mechanically fastest at fixed pp2048/tg512? | Evaluated |
| `interactive-agent-latency` | Minimize TTFT and maximize decode token/s | Which configuration is most responsive for one fixed agent prompt, output allowance, mode, and cache state? | Defined; normalized evidence captured |
| `long-context-operational` | Maximize exact retrieval success and minimize end-to-end latency | Which configuration actually uses a fixed long-context position correctly and quickly? | Defined; 2K–126K evidence captured |
| `validated-capacity-memory` | Maximize validated context and minimize peak physical memory | Which configuration delivers the largest repeatedly proven context without swap or retrieval failure? | Defined; repeated roll-up still needed |

The short-throughput frontier has two generated same-checkpoint slices:

- **Ornith:** Q4 and the ShoeHorn artifact were measured on both Macs, with Q5
  additionally measured on the M5. M5 Q4 is the sole frontier member at
  3,022.92 prompt and 113.748 decode token/s. M5 Q5, both ShoeHorn runs, and
  both M1 offerings are dominated on these two speed axes.
- **Muse:** official Dynamic Q4_K_XL is the sole frontier member at 717.593
  prompt and 24.9124 decode token/s. The ShoeHorn fit is dominated on both
  throughput axes. Its larger size and worse small-corpus PPL are supporting
  diagnostics rather than frontier axes.

The other three definitions intentionally remain separate and provisional.
Agent latency cannot pool cold and warm runners, cache misses and hits,
prose/code/tool prompts, different output ceilings, or incompatible timing
estimands. Long-context membership requires an exact retrieval pass rather than
a successfully allocated context window. Validated capacity additionally needs
the largest repeatedly passing position and one consistent physical-memory
metric; process RSS, MLX active memory, and demand-paged PLE residency cannot be
silently mixed.

These differ from Model Skyline's existing provider-facing frontiers, which
typically compare intelligence or task success against API cost, subscription
cap consumption, or p95 latency. The local frontiers focus on deployment
mechanics—throughput, TTFT, retrieval, context, and memory—and deliberately omit
quality until a benchmark is reconciled to the exact local artifact. A local
IQ2, Dynamic Q4, MLX, or ShoeHorn artifact never inherits its base model's
publisher score automatically. Cache and runner warmth are workload positions,
not noise, and no automatic local selection policy exists yet: these are
diagnostic frontiers rather than a one-number “best local model” leaderboard.

Definitions and generated artifacts:

- [`frontiers.yaml`](frontiers.yaml)
- [`generated/short-throughput-frontier.json`](generated/short-throughput-frontier.json)
- [`generated/muse-short-throughput-frontier.json`](generated/muse-short-throughput-frontier.json)
- [`README.md`](README.md)

## Hardware result

The identical Ornith Q4_K_M artifact and llama.cpp build were 3.885x faster in
M5 prompt processing and 2.005x faster in M5 decode than on the M1 Max:

| Hardware | Prompt token/s | Decode token/s |
| --- | ---: | ---: |
| M5 Max, 40 GPU cores | 3,022.92 | 113.7480 |
| M1 Max, 32 GPU cores | 778.092 | 56.7327 |

The asymmetric multipliers make a single “M5 versus M1” factor indefensible.
The much larger prefill gain plausibly reflects newer GPU/tensor execution and
memory-system improvements; decode remains more bandwidth/serial constrained.
CPU generation can matter for tokenization, orchestration, some recurrent
kernels, and host-side quantization, but all compared model layers were on
Metal, so these numbers do not isolate CPU contribution.

The M5 direct USB-C connection negotiated the adapter's full 140 W capability;
that is not the same as continuous 140 W draw. During a sustained CPU-heavy
ShoeHorn pass, undocumented battery telemetry reported about 97 W into the
system and zero battery supplementation. `pmset` recorded High Power mode 2,
while `system_profiler` reported a contradictory state. Evidence is therefore
labeled “140 W negotiated / High Power configured,” not “140 W continuously
drawn.”

## Quantization and ShoeHorn decisions

- Ornith ShoeHorn exact-error output is 48,747,873,632 bytes and fits, but is
  dominated in local efficiency: 74.7645 decode token/s versus 113.748 for the
  21,713,462,848-byte Q4, with no clear PPL improvement.
- Muse's sampled 18.31 GiB ShoeHorn fit is 19 MB larger than the official
  Dynamic Q4, 3.17% slower in prefill, 1.58% slower in decode, and about 7.7%
  worse on each of two small PPL controls. The stopping rule correctly rejects
  a costly `--exact-errors` rerun until the objective or candidate constraints
  change.
- ShoeHorn commit `43908a1` fixes recursive Hugging Face tree discovery.
  Commit `ca565f8` fixes mixed-quant `file_type` reporting by aggregating bytes
  per type. All 25 tests pass. Both commits remain local and unpushed.
- The main remaining ShoeHorn design opportunities are immutable HF revision
  handling, pagination, fail-fast unsupported GGML types, target/MTP residency
  classes, hybrid-state memory accounting, exact server calibration, and a
  task-level validation/rejection loop.

See [the ShoeHorn audit](shoehorn-audit.md) and
[the Muse audit](muse-glimmer-audit.md) for exact commands and provenance.

## Runtime and client state

- llama-swap v0.2.55 owns `127.0.0.1:8090`, watches configuration changes, and
  places every managed heavyweight backend in one exclusive `local-memory`
  group. Managed launchers also share a BSD file lock with direct benchmarks.
- Loading-state streaming is disabled. Ollama's route TTL is 300 seconds to
  match its keep-alive; other routes use 900 seconds.
- oMLX prefix caching is enabled by default. A controlled 18,099-token repeat
  reduced TTFT from 4.652 s to 0.892 s and end-to-end time from 5.098 s to
  1.339 s. Cache-disabled benchmark profiles remain available.
- TQ4 KV is retained as a selectable profile, not a blanket default. At 126K it
  reduced post-request active memory by 3.35 GB but left peak memory nearly
  unchanged and decoded 16% slower than F16 KV.
- OMP v14.6.6 and OpenCode v1.3.17 both list the managed Qwen, DS4, Muse,
  Ornith, and Ollama routes. OMP's global compaction trigger is 180,000 tokens.
- The final llama-swap `/running` state is empty; no goal-owned heavyweight
  runner is active. The Studio's pre-existing processes were not killed or
  reconfigured.

## Storage and repository state

The explicitly enumerated goal-related model/cache directories currently
occupy about 478 GiB by `du`; this includes BF16 sources retained for ShoeHorn,
custom outputs, ordinary controls, oMLX copies/cache, and the DS4 main/PLE pair.
APFS/Hugging Face sharing means this is directory-reported allocation, not a
claim about network bytes downloaded. The data volume has about 583 GiB free.
A deliberately untouched 817 MiB partial Muse DFlash BF16 file is included;
no destructive cleanup was performed.

Model Skyline work is committed locally on `feat/m5-local-benchmarks`, including
Muse checkpoint `c5bd8a2`. ShoeHorn is committed locally through `ca565f8`.
Nothing was pushed, no PR or public issue was created, and no system-wide power,
security, or wired memory setting was changed.

## Exact next experiments

1. Recheck the paste-ready
   [Muse DFlash reproducer](muse-dflash-prompt-cache-reproducer.md) on a newer
   llama.cpp commit, then submit it upstream only with explicit approval.
2. Run target-only Muse retrieval at 2K/32K/64K/126K with early/middle/late
   needles, then repeat only the useful positions with DFlash after the cache
   divergence is fixed.
3. Run the official Muse Dynamic Q4 bytes on the M1 Max when its pre-existing
   workload is safely idle; do not substitute a different quant.
4. Expand agent-quality comparisons across Qwen3.8 oMLX, DS4 Flash Next,
   Ornith, and Muse using byte-stable coding/tool tasks and explicit output
   budgets. Keep task correctness as an eligibility gate, not a throughput
   axis.
5. Add reviewed benchmark portfolios only when they reconcile to exact local
   offerings. Do not inherit base-model scores onto ShoeHorn/IQ2 artifacts.
6. Revisit Qwen3.6 only as a low-priority Skyline data point. Qwen3-Coder-Next
   remains intentionally undownloaded because no new evidence established a
   competitive frontier.
