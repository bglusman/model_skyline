# Local-model overnight status — 2026-09-14

This is the durable handoff for the M5 Max 64 GB local-model and Model Skyline
work. It distinguishes published default behavior from experiments that are
still running.

## Published to Model Skyline main

- PR [#45](https://github.com/bglusman/model_skyline/pull/45) added generic
  non-axis eligibility gates. Context capacity, exact tool correctness, and
  swap growth can now gate a two-axis frontier without becoming hidden
  objectives.
- PR [#46](https://github.com/bglusman/model_skyline/pull/46) added report-only
  exact/near cross-frontier coverage. It uses the core Decimal dominance rules,
  retains witnesses and per-axis slack, and never changes membership or
  routing.
- PR [#48](https://github.com/bglusman/model_skyline/pull/48) made the real
  Harbor quality/memory evidence reproducible from GitHub. Six compact public
  summaries replay their task peaks from full one-second traces while retaining
  the source SHA-256 and byte count. Existing catalogs and snapshots rebuild
  byte-for-byte; all decision values, members, and rejections are unchanged.
- Main is clean and synchronized at `95c8d67`. The implementation progress is
  also tracked on issue
  [#32](https://github.com/bglusman/model_skyline/issues/32#issuecomment-5662742682).

PR #48 passed two CI matrices on Python 3.11–3.14 plus both package jobs. Local
validation after the final hardening was 827 passed and 6 skipped, with Ruff
format/lint and mypy green.

## Current broad local frontier residents

These are exact residents of the published position-specific frontiers, not a
universal ranking:

| Frontier position | Current resident(s) |
| --- | --- |
| Cross-model pp2048/tg512 throughput | Ornith 1.5 Q4 GGUF |
| Warm 30-tool, 2K-prefix/1K-output operation | Ornith 1.5 oMLX; dense Qwen3.8 DFlash |
| Uncached 30-tool, 2K-prefix/256-output operation | Qwen3.8 Flash Coder Q4_K_M |
| Uncached 126K exact retrieval | Qwen3.8 Flash Next on DS4 |
| Validated context capacity vs physical footprint | Qwen3.8 Flash Next on DS4 |
| Five-task quality vs latency, one-run historical cohort | Dense Qwen3.8, low reasoning/4K thinking |
| Five-task quality vs exact uncached-input demand, one-run historical cohort | Muse Glimmer |
| Five-task quality vs process footprint, one-run historical cohort | Dense Qwen3.8 low-thinking; Muse Glimmer |

At model-family identity, dense Qwen3.8 covers three complementary published
frontiers; DS4 Flash Next, Muse Glimmer, and Ornith cover two each; Flash Coder
covers one narrow frontier. At the packaged 5% near threshold there are no
near-only residents. The closest dominated candidates are DS4 on the uncached
tool frontier at 14.09% and Flash Coder on the warm-tool frontier at 15.32%.

The three one-run quality rows remain historical point evidence. They are not
stable defaults, and the paired Qwen repeat experiment must not inflate the
general cross-model coverage count.

## Five-repeat Qwen configuration cohort

Draft PR [#47](https://github.com/bglusman/model_skyline/pull/47) is the active
serial cohort. It is clean, mergeable, and fully green in CI, but remains draft
until both exact profiles have five jobs and the derived robust frontiers are
complete.

Completed evidence:

| Exact profile | Completed jobs | Pooled result | Per-run success range |
| --- | ---: | ---: | ---: |
| Default reasoning, F16 KV | 3/5 | 8/15, 53.33% | 40–60% |
| Low reasoning / 4K thinking, F16 KV | 2/5 | 7/10, 70% | 60–80% |

Default repeat 3 scored 3/5, passing `fix-code-vulnerability`, `fix-git`, and
`multi-source-data-merger`; `build-cython-ext` and `cancel-async-tasks` timed
out. Its prompt-free result and compact memory summary are pushed. Default
repeat 4 is currently running: task 1 completed as an attributable timeout and
task 2 is active.

The final catalog will contain 25 verifier-scored trials per profile. The three
robust frontiers compare quality with p95 all-task wall time, fully covered peak
physical footprint, and exact mean uncached-input demand. Cache-demand remains
ineligible for any profile with even one incomplete timed-out API request.

## Same-artifact M1 Max versus M5 Max controls

The exact same Qwen3.8 UD-Q4_K_M bytes, llama.cpp build, and pp2048/tg512
position produced:

| Hardware/backend | Prompt tok/s | Decode tok/s |
| --- | ---: | ---: |
| M5 Max 64 GB, Metal | 680.485 | 26.632 |
| M1 Max 64 GB, Metal | 128.356 | 11.566 |
| M5 Max 64 GB, CPU-only | 16.870 | 6.788 |
| M1 Max 64 GB, CPU-only | 15.077 | 4.730 |

For exact Ornith Q4 GGUF, the M5/M1 results were 3022.92/113.748 versus
778.092/56.733 prompt/decode tok/s. These controls show why Model Skyline does
not apply one M5/M1 multiplier: prefill, decode, Metal, CPU-only execution, and
architecture interact differently.

The M1 Max Studio now has llama-swap running on its LAN endpoint with no model
resident, and 2.6 TiB free. The M5 has 504 GiB free. Both routers report healthy.

## Local runner and agent configuration

- llama-swap uses one exclusive local-memory group, so selecting another local
  route unloads the current runner before starting the next.
- Loading-state text is disabled and cannot leak into agent history.
- oMLX prefix caching is on by default; explicit no-cache profiles remain
  separate benchmark offerings.
- OMP compaction is now `thresholdPercent: 75` with no fixed token threshold.
  That yields 196,608 tokens on 262K Qwen routes and 98,304 on 131K DS4/Muse
  routes, avoiding the old fixed-180K clamp that left no usable margin on 131K
  models.
- The private Harbor queue has been updated so future runs automatically emit
  the compact public memory summary after each job.

## ShoeHorn and next model candidates

The ShoeHorn recursive Hugging Face discovery fixes are pushed in upstream PR
[#3](https://github.com/notactuallytreyanastasio/shoehorn/pull/3). The branch is
clean and its 27 tests pass. It includes revision-safe recursive discovery,
pagination, cache-collision prevention, file/type filtering, imatrix matching,
and dominant mixed-quantization reporting. It is mergeable but awaits upstream
maintainer action; this account cannot merge that repository.

The next controlled experiments after the Qwen queue are:

1. Copy the exact 19,653,960,832-byte Muse Glimmer Dynamic Q4_K_XL artifact to
   the M1 Studio, verify SHA-256
   `ac7023d6a4c704eb9af54ab53e476a66b7f5b6c0ef2fc4a8dde5253c291a6c38`,
   and run the same llama.cpp pp2048/tg512 control on both Macs.
2. Evaluate Nemotron 3.5 Lightning 30B-A3B as a likely speed/quality Pareto
   candidate. The selected oQ4e/MTP artifact is about 21.8 GB and fits the M5;
   oMLX 0.6.4 has native Nemotron-H MTP support. Runtime activation, tools,
   retrieval, memory, and verifier quality still need measurement.
3. Expand equal-repeat real-agent evidence to other model families before any
   robust Qwen profile result is promoted as a cross-model default.

No additional model transfer or benchmark is running concurrently with the
Qwen cohort; this protects the latency and memory measurements from swap, disk,
and unified-memory interference.
