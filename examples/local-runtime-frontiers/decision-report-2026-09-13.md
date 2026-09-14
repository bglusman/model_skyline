# Local-model decision report — 2026-09-13

This is the handoff state after the first M5 Max/M1 Max local-frontier pass. All
rankings are provisional and scoped to exact artifacts, runtimes, hardware, and
workload positions. No publisher full-precision score has been assigned to a
local quant.

## Recommended set

| Candidate | Best current role | Evidence-backed advantage | Important limitation |
| --- | --- | --- | --- |
| Qwen3.8 Flash Next on DS4 target-only | Best uncached and 126K route | Sole member of three position-specific frontiers: exact tools 3/3 at 6.433 s; 126K retrieval 3/3 at 197.929 s; 5.461 GB sampled physical footprint | No matched M1 implementation; quality beyond these deterministic integrity probes is not established |
| Ornith 1.5 oMLX baseline/F16 KV | Warm iterative tool sessions | Exact tools 3/3 at 0.835 s warm, co-frontier with Qwen3.8 DFlash under the 5% latency epsilon | Fast 126K execution failed the exact retrieval check 0/3, so it is ineligible for the long-context frontier |
| Qwen3.8 27B oMLX DFlash2 | Warm iterative tool sessions | Exact tools 3/3 at 0.862 s warm, co-frontier with Ornith; earlier 256-token warm run was 0.812 s | The matched 126K baseline is much slower than DS4, and DFlash's earlier 126K prefill was slower still |
| Qwen3.8 27B oMLX baseline/F16 KV | Stable uncached control | Exact tools 3/3 and 126K retrieval 3/3 | 126K median was 299.638 s with 47.855 GB physical footprint, dominated by DS4 on both active axes |
| Ornith 1.5 Q4_K_M | Raw throughput / cross-Mac control | Sole cross-model pp2048/tg512 frontier member: 3,022.92 prompt and 113.748 decode token/s; byte-identical M1 evidence exists | The GGUF throughput result cannot inherit the oMLX route's tool evidence or any base-model quality score |
| Muse Glimmer official Dynamic Q4_K_XL, target-only | Experimental dense alternative | Official quant beats the custom ShoeHorn fit | Matched warm tool probes were 0/4 at 256 tokens and only 3/4 at 1,024; it fails the 100% correctness eligibility gate |

The practical default remains workload-dependent. DS4 Flash Next is the clear
uncached/long-context operational winner. Ornith and Qwen3.8 DFlash are both
near-optimal warm tool routes, with Ornith slightly faster in this sample.
Ornith GGUF is the throughput leader. Muse stays available for investigation,
but does not pass the current promotion gate.

Muse DFlash2 remains selectable but is labeled experimental in both OMP and
OpenCode. Two cold/cache-miss runs returned the same wrong non-tool answer;
cache-warm runs returned the exact tool call in 6/6 trials. With prompt reuse
explicitly disabled, two consecutive requests remained uncached and reproduced
the wrong answer byte-for-byte. Target-only passed both cold and warm. This is a
reproducible DFlash + llama.cpp prompt-reuse semantic divergence, not a router
load-state artifact.

## Local Pareto frontiers

Four complementary frontier definitions are now materialized as six
position-specific snapshots. They use Model Skyline's ordinary two-axis Pareto
engine and a strict local offering identity: hardware, exact artifact bytes,
quantization, runtime build/profile, KV/cache/speculation configuration,
physical context capacity, and harness remain distinct.

| Snapshot | Axes and eligibility | Members | Result |
| --- | --- | --- | --- |
| `short-throughput` | Max prompt and decode token/s, 3% epsilon | Ornith Q4 GGUF | Ornith 3022.92/113.748; Qwen3.8 680.485/26.632; Muse 717.593/24.912 |
| `exact-Qwen-cross-Mac` | Same axes; byte-identical Qwen artifact and runtime | M5 Max | M5 680.485/26.632; M1 128.356/11.566 |
| `warm-agent-tools` | Max exact-call success, min end-to-end, 100% gate, 5% latency epsilon | Ornith oMLX; Qwen3.8 DFlash | 0.835 s vs 0.862 s, both 3/3; Muse rejected at 3/4 and 30.65 s median among successful-cache-position rows |
| `uncached-agent-tools` | Same definition at a proven zero-hit position | DS4 Qwen3.8 Flash Next | DS4 6.433 s vs Qwen baseline 7.536 s, both 3/3 |
| `long-context-126k` | 100% exact retrieval gate, min end-to-end, 5% epsilon | DS4 Qwen3.8 Flash Next | DS4 197.929 s; Qwen baseline 299.638 s; Ornith rejected at 0/3 despite 88.474 s median |
| `validated-capacity` | Max repeatedly validated tokens, min sampled physical footprint | DS4 Qwen3.8 Flash Next | Both DS4 and Qwen validated 125,964 tokens 3/3; DS4 used 5.461 GB vs Qwen 47.855 GB |

The result now has the intended cross-frontier shape. At exact-route identity,
DS4 Flash Next is on three frontiers. At model-family identity, DS4 Flash Next
covers three and Ornith covers two (GGUF throughput plus oMLX warm tools).
Qwen3.8 27B covers one warm frontier. Muse covers none. Epsilon-aware membership
is used directly; there is no extra weighted score that could hide a weak axis.
The coverage summary intentionally omits the duplicate hardware-only Qwen slice;
that snapshot answers a machine-comparison question rather than adding a model
candidate.

The `interactive-agent-latency` TTFT/decode definition remains available for
text-streaming positions, but it is not used for the cross-runtime tool result:
DS4 and llama.cpp buffer tool calls and do not expose the same token-timing
estimands. `tool-agent-operational` therefore uses exact-call success and
end-to-end latency, which every backend provides.

The capacity roll-up now selects each exact offering's largest fully passing
uncached retrieval record and pairs it with macOS's sampled kernel-accounted
physical footprint. This replaces the invalid RSS comparison that reported
only a small wrapper/process view for Metal allocations. Clean demand-paged
file mappings remain distinct from charged footprint and artifact size.

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
- [`generated/cross-model-short-throughput-frontier.json`](generated/cross-model-short-throughput-frontier.json)
- [`generated/qwen38-exact-cross-mac-short-throughput-frontier.json`](generated/qwen38-exact-cross-mac-short-throughput-frontier.json)
- [`generated/tool-agent-warm-p2048-o1024-frontier.json`](generated/tool-agent-warm-p2048-o1024-frontier.json)
- [`generated/tool-agent-uncached-p2048-o256-frontier.json`](generated/tool-agent-uncached-p2048-o256-frontier.json)
- [`generated/long-context-uncached-p126k-frontier.json`](generated/long-context-uncached-p126k-frontier.json)
- [`generated/validated-capacity-frontier.json`](generated/validated-capacity-frontier.json)
- [`generated/cross-frontier-coverage.json`](generated/cross-frontier-coverage.json)
- [`README.md`](README.md)

## Hardware result

Two byte-identical artifact/runtime comparisons now show that the generation
gap is strongly workload/model dependent:

| Artifact | M5 prompt | M1 prompt | Ratio | M5 decode | M1 decode | Ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Ornith 1.5 Q4_K_M | 3,022.92 | 778.092 | 3.885x | 113.7480 | 56.7327 | 2.005x |
| Qwen3.8 27B UD-Q4_K_M | 680.485 | 128.356 | 5.302x | 26.6322 | 11.5657 | 2.303x |

For Qwen, both files hash to
`322e194ff79741c7baa497c240f677f54b201b0efab44ca8e50f122b39123482`
and both `llama-bench` binaries hash to
`30723a650e9e4a3d12bbe558c44e378d34707d14ebfd5d5e8535a8827eadceb9`.
The command position is also identical: pp2048/tg512, five repetitions, six
threads, all Metal layers, Q8_0 KV, flash attention on, batch 2048, and
micro-batch 512.

The asymmetric multipliers make a single “M5 versus M1” factor indefensible.
The much larger prefill gain plausibly reflects newer GPU/tensor execution and
memory-system improvements; decode remains more bandwidth/serial constrained.
CPU generation can matter for tokenization, orchestration, some recurrent
kernels, and host-side quantization, but all compared model layers were on
Metal. These exact runs isolate the complete M1-Max-versus-M5-Max system change,
not the CPU alone; a CPU-only position is required for a CPU-specific claim.

That CPU-only control is now captured separately at pp512/tg64, three
repetitions, six threads, and zero Metal layers. Qwen's median prompt/decode
rates were 16.870/6.788 token/s on M5 and 15.077/4.730 on M1, advantages of
1.119x and 1.435x. This measures the combined CPU, DRAM, and llama.cpp CPU
backend—not CPU core IPC in isolation—but it strongly suggests that the much
larger all-Metal prefill gaps above are accelerator-path effects. The CPU
control is intentionally not mixed into the production frontier.

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
  per type. All 25 tests pass. The branch is published on the `bglusman/shoehorn`
  fork and proposed upstream as
  [ShoeHorn PR #3](https://github.com/notactuallytreyanastasio/shoehorn/pull/3);
  it is not merged upstream yet.
- The main remaining ShoeHorn design opportunities are immutable HF revision
  handling, pagination, fail-fast unsupported GGML types, target/MTP residency
  classes, hybrid-state memory accounting, exact server calibration, and a
  task-level validation/rejection loop.

See [the ShoeHorn audit](shoehorn-audit.md) and
[the Muse audit](muse-glimmer-audit.md) for exact commands and provenance.

## Efficient quality-calibration plan

The current perplexity captures remain `proxy` evidence. They can reject a
damaged artifact, as with the sampled Muse ShoeHorn fit, but they cannot assign
HumanEval, BFCL, RULER, or agent-task quality. None of the current artifacts has
an exact, same-protocol full-precision benchmark anchor in this repository, so
no quantization-adjusted task score is claimed yet.

The next quality pass will use three explicit tiers: proxy screening, calibrated
subset estimates with confidence bounds, and full measurements for likely
frontier residents. Candidate and high-fidelity anchor will be evaluated on
identical task IDs and seeds. The default estimator transfers the
benchmark-specific paired score difference, not a global multiplicative Q4/Q5
factor. Long-context, coding, and tool-use deltas remain separate because
quantization damage is not stable across those workloads.

Estimated results will enter only a screening frontier using their conservative
lower confidence bound. A complete run is promoted when the estimate's interval
could change frontier membership, its epsilon-equivalent cluster, or the
default/fallback order. Final local recommendations continue to prefer fully
measured quality evidence.

The reusable protocol, estimator requirements, and research basis are in
[`../../docs/efficient-quality-estimation.md`](../../docs/efficient-quality-estimation.md).
Copyable quantization, 128K, session-endurance, cache, local-agent, and remote
frontier policies are in
[`recommended-frontier-recipes.yaml`](recommended-frontier-recipes.yaml).

## Runtime and client state

- llama-swap v0.2.55 owns `127.0.0.1:8090`, watches configuration changes, and
  places every managed heavyweight backend in one exclusive `local-memory`
  group. Managed launchers also share a BSD file lock with direct benchmarks.
- The M1 Studio now has the same llama-swap version on LAN port 8090 and the
  same one-runner exclusive policy. Qwen and Ornith use Homebrew llama.cpp build
  10809; Qwen's routed artifact is the byte-identical M5 file. Route selection
  and explicit unload smokes passed for Qwen and Ornith.
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
- The M1 Studio now runs the same llama-swap v0.2.55 pattern at
  `192.168.1.175:8090`: six configured routes in one exclusive group, child
  servers on loopback, and loading messages disabled. Qwen-to-Ornith switching
  and explicit unload were smoke-tested, ending with no resident route. The old
  always-on DS4 LaunchAgent is preserved as a dated disabled rollback file.
- The Studio's Ornith Q4 hash exactly matches the cross-Mac control. Its older
  Qwen Q4 hash did not match the MacBook's Unsloth artifact, so it was excluded.
  The exact MacBook Qwen artifact was copied into a separate evidence directory
  and produced the matched M1 captures reported above.

## Storage and repository state

The explicitly enumerated goal-related model/cache directories currently
occupy about 478 GiB by `du`; this includes BF16 sources retained for ShoeHorn,
custom outputs, ordinary controls, oMLX copies/cache, and the DS4 main/PLE pair.
APFS/Hugging Face sharing means this is directory-reported allocation, not a
claim about network bytes downloaded. The data volume has about 583 GiB free.
A deliberately untouched 817 MiB partial Muse DFlash BF16 file is included;
no destructive cleanup was performed.

The Model Skyline local-runtime work was published in
[PR #35](https://github.com/bglusman/model_skyline/pull/35) and squash-merged to
`main` as `d0d6bff`. ShoeHorn is committed through `ca565f8`, pushed to the
`bglusman/shoehorn` fork, and awaiting upstream review in PR #3. No system-wide
power, security, or wired-memory setting was changed.

## Exact next experiments

1. Recheck the paste-ready
   [Muse DFlash reproducer](muse-dflash-prompt-cache-reproducer.md) on a newer
   llama.cpp commit, then submit it upstream only with explicit approval.
2. Treat Muse's matched 0/4-at-256 and 3/4-at-1024 tool behavior as a failed
   promotion gate; revisit long-context work only after the reasoning/tool
   interaction is made deterministic.
3. Complete a byte-identical Muse copy to the M1 Studio and run the same
   pp2048/tg512 capture with the same llama.cpp commit. The exact Qwen transfer
   and matched capture are complete.
4. Build paired calibrated subsets for code, tool use, and long context across
   Qwen3.8 oMLX, DS4 Flash Next, Ornith, and Muse. Preserve item IDs, seeds,
   task-set hashes, estimator error, and confidence bounds; keep the existing
   deterministic probes as operational gates rather than task-quality scores.
5. Run complete benchmarks only for candidates whose estimated interval could
   change a frontier or selection, then reconcile those results to exact local
   offerings. Do not inherit base-model scores onto ShoeHorn/IQ2 artifacts.
6. Revisit Qwen3.6 only as a low-priority Skyline data point. Qwen3-Coder-Next
   remains intentionally undownloaded because no new evidence established a
   competitive frontier.
