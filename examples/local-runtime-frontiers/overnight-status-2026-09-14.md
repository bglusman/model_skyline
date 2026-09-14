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
- PR [#49](https://github.com/bglusman/model_skyline/pull/49) pinned the exact
  Nemotron 3.5 Lightning MLX candidate and its plain/MTP/DSpark evaluation
  contract without importing external scores.
- PR [#50](https://github.com/bglusman/model_skyline/pull/50) made the reusable
  local recipes executable: ten fail-closed selectors now cover quality,
  latency, memory, cache demand, fixed 128K, session endurance, warm cache, and
  quant-screening priorities.
- PR [#51](https://github.com/bglusman/model_skyline/pull/51) pinned North Mini
  Code MLX and GGUF controls as the next agent-quality challenger, with explicit
  parser, tool, context, Harbor, and cross-Mac gates.
- PR [#52](https://github.com/bglusman/model_skyline/pull/52) added a versioned
  seven-family candidate population and coverage-v3 attempt audit. It makes
  unattempted frontier cells visible without treating them as dominated points.
- Main is clean and synchronized at `8e254e9`. The implementation progress is
  also tracked on issue
  [#32](https://github.com/bglusman/model_skyline/issues/32#issuecomment-5662742682).

Each merged PR passed two CI matrices on Python 3.11–3.14 plus both package
jobs. Local validation after PR #52 was 828 passed and 6 skipped; its touched
Python files also pass Ruff format and lint.

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

Those residents cover 29 of the 50 model-family/frontier cells nominated for
their intended roles (58%). Dense Qwen and DS4 have complete attempt coverage
for their declared positions; Muse is missing three, Ornith one, Flash Coder
one, and the newly pinned North and Nemotron candidates remain 0/8. An explicit
eligibility rejection counts as an attempted cell but never as exact or near
membership.

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
| Default reasoning, F16 KV | 4/5 | 11/20, 55% | 40–60% |
| Low reasoning / 4K thinking, F16 KV | 2/5 | 7/10, 70% | 60–80% |

Default repeats 3 and 4 both scored 3/5, passing
`fix-code-vulnerability`, `fix-git`, and `multi-source-data-merger`; both
`build-cython-ext` and `cancel-async-tasks` timed out. Repeat 4 completed in
54m46s. Its prompt-free result and compact memory summary replay cleanly through
the normalizer. Default repeat 5 is running.

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

The next controlled experiments after the Qwen queue have different purposes:

1. **Agent-quality challenger:** screen North Mini Code 30B-A3B using the pinned
   18.5 GB MLX 4-bit artifact, then its exact 18.744 GB Q4_K_M portable control.
   It targets terminal/agentic coding directly, while parser correctness and
   local quality remain unmeasured.
2. **Architecture-efficiency challenger:** evaluate Nemotron 3.5 Lightning
   30B-A3B. Its selected oQ4e/MTP artifact is about 21.8 GB; test plain oMLX,
   verified embedded MTP, and external DSpark as separate profiles.
3. **Hardware isolation:** copy the exact 19,653,960,832-byte Muse Glimmer
   Dynamic Q4_K_XL artifact to the M1 Studio, verify SHA-256
   `ac7023d6a4c704eb9af54ab53e476a66b7f5b6c0ef2fc4a8dde5253c291a6c38`,
   and run the same llama.cpp pp2048/tg512 control on both Macs.
4. Expand equal-repeat real-agent evidence to other model families before any
   robust Qwen profile result is promoted as a cross-model default.

No additional model transfer or benchmark is running concurrently with the
Qwen cohort; this protects the latency and memory measurements from swap, disk,
and unified-memory interference.
