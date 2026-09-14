# Local-model overnight handoff — 2026-09-14

This is the durable handoff after the overnight M5 Max/M1 Max local-model
work. It distinguishes merged tooling and measured observations from the
experiments that are still running. The detailed first-pass decision report is
in [`decision-report-2026-09-13.md`](decision-report-2026-09-13.md).

## Executive summary

- The five-repeat Qwen data in PR #47 is now complete. Its paired result is
  ready for review alongside the work already merged to `main`.
- Local selection is no longer represented by one misleading leaderboard.
  Nine complementary frontier definitions are materialized as ten snapshots,
  including throughput, warm and uncached tools, cache reuse, validated
  context capacity, and three verifier-scored quality tradeoffs.
- The strongest measured default for the small real-agent pilot is presently
  Qwen3.8 27B oMLX with low reasoning and a 4K thinking budget. That is a
  provisional configuration-specific result, not a stable universal winner.
- Qwen3.8 Flash Next on DS4 is the validated 126K/context-footprint winner.
  Muse Glimmer is the compact exact-cache-demand resident. Ornith 1.5 is the
  raw-throughput and warm-tool specialist. The custom Flash Coder slice wins
  only the narrow uncached-tool position and failed broader agent/retrieval
  gates.
- Equal 64 GB memory gives the two Macs broadly similar *fit eligibility*, but
  not similar speed. Exact cross-Mac controls show much larger M5 gains in
  Metal prompt processing than in decode, and far smaller gains in CPU-only
  runs. A single M1-to-M5 multiplier is not defensible.
- Both Macs now use `llama-swap` with one exclusive heavyweight-memory group.
  OMP and OpenCode select model IDs through the router; direct benchmarks use
  the same host lock. Loading-state prompt pollution is disabled and oMLX
  prefix caching is enabled by default.
- ShoeHorn is useful for dense and conventional resident-MoE models, but its
  current output is not suitable for Qwen3.8 Flash Next's pageable PLE design.
  Three Hugging Face discovery fixes are proposed upstream; architectural
  memory accounting, output sharding, unsupported GGUF handling, and server
  calibration still need work.

## Current measured frontier residents

All rows use strict offering identity: exact model bytes, quantization,
runtime/build/profile, KV/cache/speculation settings, allocated context,
hardware, and harness. Cache warmth is a workload position rather than noise.

| Frontier position | Axes and important gates | Current resident(s) |
| --- | --- | --- |
| Cross-model short throughput | maximize prompt and decode token/s; 3% epsilon | Ornith 1.5 Q4 GGUF on M5: 3,022.92 prompt / 113.748 decode token/s |
| Exact-Qwen cross-Mac throughput | same two axes; byte-identical artifact, runtime, and command | M5 Max: 680.485 / 26.632 versus M1 Max: 128.356 / 11.566 |
| Warm agent tools | maximize exact tool success, minimize end-to-end time; 100% success gate | Ornith oMLX at 0.835 s and Qwen3.8 DFlash at 0.862 s, both 3/3 |
| Warm cache reuse | maximize exact prefix reuse, minimize latency; exact-tool and zero-swap gates | Qwen3.8 DFlash: 99.870% reuse at 0.862 s; Ornith is near-only under the configured epsilon |
| Uncached agent tools | maximize exact tool success, minimize end-to-end time at a proven zero-hit position | Qwen3.8 Flash Coder: 5.527 s, 3/3 |
| Uncached 126K retrieval | minimize end-to-end time; 100% exact-retrieval gate | Qwen3.8 Flash Next on DS4: 197.929 s |
| Validated capacity | maximize repeatedly validated tokens, minimize sampled physical footprint | Qwen3.8 Flash Next on DS4: 125,964 tokens, 3/3, 5.461 GB sampled footprint |
| Five-task quality / latency | maximize verifier success, minimize all-task p95 wall time; 60% quality gate | Qwen3.8 27B oMLX low-reasoning/4K profile: 4/5 and 804.267 s p95 in the initial run |
| Five-task quality / exact uncached input | maximize success, minimize exact uncached input; complete-request gate | Muse Glimmer: 3/5, 51,424 uncached input tokens, 95.738% reuse |
| Five-task quality / process footprint | maximize success, minimize fully covered process footprint; 60% gate | Tuned Qwen: 80% / 23.782 GB; Muse: 60% / 3.485 GB |

At model-family level, dense Qwen3.8 currently covers four complementary
frontiers; DS4 Flash Next, Muse, and Ornith each cover two; Flash Coder covers
one narrow frontier. This count is descriptive, not a weighted score.

After this snapshot was written, the reviewed cross-workload enrichment path
was used to materialize operationally gated versions of the two Harbor
quality frontiers. The gate policy requires at least 60% pilot success, 3/3
exact synthetic tool emission, nonpositive swap growth at the repeated 126K
probe, and validated input of at least 125,000 tokens. DS4 Flash Next is the
sole eligible resident on both quality/latency and quality/memory. A parallel
strict 128,000-token snapshot has no resident because the strongest exact proof
is 125,964 tokens. These overlays are not added to the family frontier count
above because their axes repeat existing quality roles with stronger gates.
See the
[`operational-gate policy`](harbor-pilot5-operational-gates-policy.json) and
[`gated configuration`](harbor-operational-gated-frontiers.yaml).

The population is not complete. Coverage v3 records 33 of 74 nominated
model-family/frontier cells as attempted (44.59%). North Mini Code, Nemotron,
Agents-A1, and Laguna are pinned challengers but have not yet produced local
frontier evidence. A gate failure remains visible as a rejection; an untested
candidate is never silently treated as dominated.

## What changed in Model Skyline

The overnight merged work added:

- verifier-scored local quality ingestion with task-scoped process-memory and
  request/cache accounting;
- auditable paired subset estimates and a documented quantization-calibration
  policy that transfers benchmark-specific paired deltas, never a universal
  quantization multiplier;
- repeat-aware point and robust frontiers, with quality ranges and p95-latency
  ranges kept separate;
- non-axis eligibility gates, so correctness, swap, completeness, and context
  requirements cannot be hidden inside a weighted score;
- advisory distance-to-frontier reporting for eligible evaluated losers;
- retained failed-capacity candidates and a population coverage audit;
- packaged selectors for common local priorities and a dedicated warm-cache
  reuse frontier;
- separate ResearchClawBench, BrowseComp, and GAIA recipes instead of pooling
  incompatible real-world agent tasks;
- a strict prompt-free `normalize-local-agent-benchmark` importer for those
  three benchmark families. It recomputes task aggregates and rejects prompts,
  answers, trajectories, attachment contents, invalid rights claims, incomplete
  manifests, and multiple-attempt summaries. Exact offering reconciliation is
  still required before route-specific latency/tool signals are trusted.

The last item is merged as [PR #57](https://github.com/bglusman/model_skyline/pull/57).
It makes real-world benchmark evidence ingestible; it does **not** claim that
ResearchClawBench, BrowseComp, or GAIA has already been run on the local
candidate population.

These local definitions differ from the existing provider-facing frontiers.
Remote frontiers typically trade intelligence/task success against API price,
subscription capacity, or p95 latency. The local set additionally treats
artifact quantization, runner/runtime, verified physical context, resident
memory, cache reuse, swap, and warm/cold position as first-class evidence.
Advertised context length is not accepted as usable capacity.

## Qwen five-repeat experiment

The completed robust Qwen comparison is in
[PR #47](https://github.com/bglusman/model_skyline/pull/47).

- The default-reasoning profile has completed all five runs: 13/25 tasks =
  52%, with per-run success ranging from 40% to 60% and all-task p95 wall time
  of 925.232 s.
- The low-reasoning/4K-thinking profile completed all five runs: 17/25 tasks =
  68%, with per-run results of 80%, 60%, 60%, 60%, and 80%.
- On the paired robust quality × time and quality × memory frontiers, bounded
  reasoning is the only eligible member; default reasoning fails the 60%
  pooled-quality floor.
- The robust quality × uncached-input frontier is empty because both profiles
  have incomplete token accounting on timed-out requests.
- This is a configuration result within Qwen3.8, not a cross-model robust
  winner. Other families need the same five-run protocol before comparison.

The queue runs one model/profile batch at a time so it does not measure
`llama-swap` churn. No competing model download or inference benchmark should
be started while it owns the runner lock.

## Equal-memory cross-Mac conclusions

Byte-identical llama.cpp controls produced:

| Model/artifact | M5:M1 prompt ratio | M5:M1 decode ratio |
| --- | ---: | ---: |
| Ornith 1.5 Q4_K_M, all Metal layers | 3.885x | 2.005x |
| Qwen3.8 27B UD-Q4_K_M, all Metal layers | 5.302x | 2.303x |
| Qwen3.8 CPU-only control | 1.119x | 1.435x |

The two machines have the same 64 GB unified-memory capacity and the same
reported recommended Metal working set in these captures, so the set of
ordinary fully resident quants that fit should often be similar. Newer GPU and
tensor paths materially change prefill speed, while generation remains more
bandwidth/serial constrained. CPU, DRAM, tokenization, orchestration, recurrent
kernels, and runtime implementation still matter. These results isolate the
whole machines—not CPU IPC alone—and show why Model Skyline stores hardware in
the exact offering identity.

The direct USB-C charger negotiated the adapter's full 140 W capability. Under
one sustained ShoeHorn CPU load, internal telemetry reported roughly 97 W into
the system with no battery supplementation. That verifies adequate delivery,
not continuous 140 W draw; adapter wattage is a ceiling rather than a target.

## Runtime and client state

- The M5 router is `llama-swap` v0.2.55 at `127.0.0.1:8090`.
- The M1 Studio at `192.168.1.175:8090` now uses the same version and exclusive
  one-runner policy. Qwen-to-Ornith switching and explicit unload passed.
- OMP v14.6.6 and OpenCode v1.3.17 list managed Qwen, DS4, Muse, Ornith, and
  Ollama routes. OMP summarizes a conversation at 75% of the selected model's
  declared window: 196,608 tokens on 262,144-token routes and 98,304 on
  131,072-token routes. The remaining 25% is working room for responses, tools,
  and summarization. A single 180,000-token setting was rejected because it is
  larger than the smaller routes' entire window. This client setting is
  separate from measured usable-context evidence.
- `sendLoadingState` is disabled because operational messages were entering
  agent history.
- oMLX paged prefix caching is on by default. Repeating an 18,099-token prefix
  reduced TTFT from 4.652 s to 0.892 s and end-to-end time from 5.098 s to
  1.339 s. Benchmark profiles can still disable caching explicitly.
- TQ4 KV is selectable, not universal: at 126K it saved 3.35 GB after the
  request but did not materially lower peak footprint and decoded 16% slower
  than F16 KV in the measured position.

## ShoeHorn result and feedback

Measured custom fits did not displace ordinary quants:

- Ornith's 48.748 GB exact-error ShoeHorn artifact decoded at 74.765 token/s,
  versus 113.748 for the 21.713 GB ordinary Q4, without a demonstrated quality
  gain.
- Muse's sampled 18.31 GiB fit was slightly larger and slower than the official
  Dynamic Q4 and about 7.7% worse on each of two small perplexity controls. An
  expensive exact-error rerun was correctly stopped.

The published ShoeHorn fork branch adds recursive, paginated, revision-safe
Hugging Face discovery. It is proposed upstream as
[ShoeHorn PR #3](https://github.com/notactuallytreyanastasio/shoehorn/pull/3).
The strongest remaining findings are:

- fail closed on GGUF tensor types ShoeHorn cannot decode;
- use `-fit off` and `-ngl all` with current llama.cpp;
- account for hybrid attention/recurrent state and server parallelism;
- preserve or construct shards by residency class;
- narrow the global “exact-fit” claim or add an exact/local-swap solve;
- add current-llama differential and real-load tests.

Qwen3.8 Flash Next is specifically a poor current ShoeHorn target: its 51B PLE
table depends on pageable/residency-aware sharding, while ShoeHorn currently
budgets all tensor bytes as resident and merges the output. ShoeHorn remains
promising for dense BF16/F16 GGUF and conventional fully resident MoE models.

## Next queue

1. Run the same five-repeat pilot for the most competitive non-Qwen residents
   before promoting the Qwen configuration result to a cross-model default.
2. Run plain Laguna NVFP4 under oMLX, then Agents-A1 uniform-4 oMLX, with GGUF
   controls where exact compatible artifacts exist. Try Laguna DFlash only
   after the target-only control.
3. Populate the prompt-free ResearchClawBench/BrowseComp/GAIA path with actual
   task-digest summaries and join separate validated-context/swap evidence at
   exact offering identity.
4. Add strict composition of multiple reconciled candidate catalogs so a
   real-world frontier can compare several models without weakening source or
   workload identity.
5. Continue filling the 41 unattempted nominated population cells, prioritizing
   models likely to change a frontier rather than exhaustively benchmarking
   clearly dominated configurations.

## Published locations

- Main local-runtime documentation and generated evidence:
  [`examples/local-runtime-frontiers/`](.)
- Frontier definitions: [`frontiers.yaml`](frontiers.yaml)
- Recommended reusable recipes:
  [`recommended-frontier-recipes.yaml`](recommended-frontier-recipes.yaml)
- Cross-frontier coverage:
  [`generated/cross-frontier-coverage.json`](generated/cross-frontier-coverage.json)
- Detailed first-pass report:
  [`decision-report-2026-09-13.md`](decision-report-2026-09-13.md)
- Runtime-support audit:
  [`runtime-support-audit-2026-09-14.md`](runtime-support-audit-2026-09-14.md)
- Real-world summary contract:
  [`real-world-agent-summary-contract.md`](real-world-agent-summary-contract.md)
