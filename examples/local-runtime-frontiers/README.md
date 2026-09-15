# Local runtime frontiers

The [current model-first summary](current-model-frontiers.md) gives the simple
answer first: which models remain when “best” is defined by two measured
priorities such as coding success and speed, or coding success and memory. It
separates the best available implementation from a balanced average across
machines; read that page first if you are choosing a model.

The generic [`model-frontier-view` contract](../../docs/model-level-frontiers.md)
now generates both views from one exact frontier. The checked-in two-Mac speed
example proves that the public model table can be replayed rather than being a
hand-maintained interpretation.

This longer page is the evidence drill-down. It keeps hardware, exact artifact
bytes, runtime build and configuration, physical context ceiling,
KV/cache/speculation profile, harness, workload position, run conditions, and
raw-result digest so each headline result remains reproducible.

The current artifacts are provisional. They now include cross-model
throughput, warm and uncached tool operation, repeated 126K retrieval, and a
strict plus value-found capacity/physical-footprint roll-up. They remain
workload-specific, not a universal model ranking.

The newest 16 GB RTX 5060 Ti screens add Qwen3.5 9B Q6_K and Muse Glimmer
AD-IQ3_XXS as practical 128K-class routes. Both returned the exact hidden value
near the beginning, middle, and end of 126K-class prompts. Qwen's middle
position took 72.103 s uncached, about 0.693 s after reusing 125,998 prompt
tokens, and peaked at 11.72 GB of combined host/GPU service capacity. Muse's
fresh-server middle position took 181.46 s including an 11.34-second load; its
sampled whole-service memory capture and coding-quality pilot remain open.
Qwen alone therefore remains on the validated-context versus service-memory
frontier. Neither screen measures vision, and neither yet supplies local coding
quality. The [2026-09-15 status](overnight-status-2026-09-15.md) is the shortest
handoff for the new Muse/ShoeHorn work.

The [small, realistic evaluation survey](../../docs/small-realistic-evaluations.md)
compares several text, tool, vision, and SVG candidates before choosing a
default. [Little Dorrit](dorrit-benchmark-intake.md) remains one intake in that
survey; it is not yet a selected benchmark and is not mixed into the current
text/coding score.

Speech and complete voice-agent workloads use different latency clocks and
quality gates. Their definitions, first three-host measurements, and retained
captures live in the separate [voice runtime frontier
experiment](../voice-runtime-frontiers/README.md); voice results are not mixed
into the coding frontiers on this page.

## Where custom fits belong

A hardware-targeted quantization is a real candidate, not an approximation
that must be hidden from the frontier. In this repository a
[ShoeHorn](https://github.com/notactuallytreyanastasio/shoehorn) output is one
exact **offering** of its parent model: source revision, importance-matrix
digest, ShoeHorn commit and solved plan, output digest, context/KV budget, and
runtime settings identify the bytes that were actually tested. The headline
model view can therefore select a ShoeHorn fit as the best observed way to run
that model on a particular machine.

It does not inherit quality from either the source checkpoint or another
quantization. The fitted bytes must pass the same tool, context, quality,
latency, and memory measurements as a stock artifact. Two fits aimed at 32K
Q8 KV and 128K Q4 KV are also different workload/configuration choices: the
larger context spends more accelerator memory on KV and may force lower-fidelity
weights. The exact offering frontier keeps that detail; the model-first table
collapses it only after the comparison.

This matters especially on discrete GPUs. The current RTX 5060 Ti host has
16,311 MiB of reported VRAM and only 9.64 GiB of VM RAM. A stock artifact that
fits a 64 GB unified-memory Mac may not be fully GPU-resident there, while a
ShoeHorn fit can legitimately become the 5060 recommendation if its exact
bytes win after the desired context and quality gates. The checked-in
[`hardware profile`](hardware/inference-vm-rtx5060ti16.json) records that target;
the exact 32K/Q8-KV Laguna fit is built and measured. It is faster than the
stock Q2_K_L control, but it fails the matched 30K retrieval test and produces
non-finite perplexity. It is therefore useful quantization evidence, not a
route to deploy. A 128K/Q8-KV resident fit is arithmetically impossible at this
budget, while the 128K/Q4-KV screen leaves only a severe 2.222-bpw weight plan.
Neither 32K result is a substitute for the requested 128K agent route.

The separate Qwen3.5 9B Q6_K control now supplies that 128K-class route without
a custom fit. The exact artifact is 7,359,230,048 bytes, uses a 131,072-token
runtime allocation and Q8 KV cache, and is identified by SHA-256 in its
[`system profile`](system-profiles/qwen35-9b-q6k-llamacpp-cuda-ctx128k-q8kv.json).
It validated 126,002 actual input tokens and passed a 30-tool selection probe.
The official checkpoint's larger native context is retained as metadata, not
reported as locally proven capacity. An exploratory Q4 KV run used less memory
but was materially slower at the long position, so the checked-in route keeps
Q8 KV. No exploratory Q4 raw result or altered artifact is promoted.

The dated [`overnight handoff`](overnight-handoff-2026-09-14.md) summarizes the
published frontier residents, cross-Mac conclusions, runtime state, and pending
benchmark queue in one durable document.

## Evidence levels

1. **Capacity smoke:** the runtime allocates and answers. This is not retrieval
   quality evidence.
2. **Runtime microbenchmark:** fixed prompt/decode work with retained repetitions.
3. **Operational probe:** streaming TTFT/end-to-end time plus retrieval,
   structured-output, or tool correctness at the same workload position.
4. **Reviewed quality portfolio:** benchmark quality mapped to the exact local
   `OfferingKey` through the existing reviewed reconciliation path.

Publisher quality numbers never enter `LocalMeasurementRecord`. A GGUF result
does not inherit a score from its base checkpoint, and an MLX, DFlash, or agent
harness result remains a distinct offering. Quality can be added only through
the existing exact reconciliation and portfolio machinery.

Small benchmark subsets can reduce that cost, but only through the explicit
`proxy` → `estimated` → `measured` protocol in
[`docs/efficient-quality-estimation.md`](../../docs/efficient-quality-estimation.md).
Use the paired, benchmark-specific degradation from an exact high-fidelity
anchor; do not apply one quantization multiplier across tasks. The copyable
local, cache, context, quant-screening, and remote recipes are in
[`recommended-frontier-recipes.yaml`](recommended-frontier-recipes.yaml).

Those recipes also include named lexicographic selectors for quality-first,
latency-first, memory-first, cache-demand, fixed-128K, scientific-workspace
research, live-web browsing, general-assistant tasks, session-endurance, warm
cache, and quantization-screening priorities. A selector ranks only eligible
members of one Pareto frontier: its correctness, context, evidence, freshness,
and no-swap gates have already been applied. `return_available` deliberately
returns fewer than three choices when the evidence cannot support three; it
does not backfill an ineligible model. These are reusable policy templates, not
a claim that the provisional example population is ready for unattended
automatic routing. Cross-frontier coverage remains an advisory portfolio view,
not a hidden weighted score or a selector that merges incompatible workloads.

The first verifier-scored local-agent population is specified in
[`harbor-quality-pilot.yaml`](harbor-quality-pilot.yaml) and explained in
[`harbor-quality-pilot.md`](harbor-quality-pilot.md). Its five-task score is a
complete measurement of that named pilot only; it is not a calibrated estimate
of the full 89-task Terminal-Bench 2.1 release. Published compact runner-memory
summaries replay task peaks from the full samples and retain the source capture
digest, so the quality/memory snapshots can be regenerated without committing
large per-second traces.

The additive
[`Qwen3.8 Flash Coder 160-expert screen`](qwen38-flash-coder-screen.md)
evaluates one custom MoE slice without changing the frozen pilot digest. It
fails the real-agent smoke and retrieval ladder; Laguna now also dominates it
on the narrow uncached-tool frontier it previously occupied.

Four additional candidates were pinned without treating external scores as
local measurements. The
[`North Mini Code local intake`](north-mini-code-intake.md) is the agent-quality
challenger: it targets terminal work directly and has both oMLX and portable
GGUF controls. The
[`Nemotron 3.5 Lightning local intake`](nemotron-lightning-intake.md) is the
architecture-efficiency challenger and separates plain oMLX, embedded-MTP, and
external-DSpark profiles. The
[`Agents-A1 local intake`](agents-a1-intake.md) adds a distinct long-horizon
search/research hypothesis and motivates a separate evidence-grounded research
frontier rather than importing research scores into Terminal-Bench. The
[`Laguna XS 2.1 local intake`](laguna-xs-2.1-intake.md) adds a first-party
local-focused coding challenger with MLX and GGUF controls. Laguna has now run
seven intended positions and occupies the synthetic tool/cache and fast
value-retrieval frontiers, but failed its real-agent smoke and strict retrieval
gate. North, Nemotron, and Agents-A1 remain pinned rather than measured
residents.

The
[`installed runtime-support audit`](runtime-support-audit-2026-09-14.md)
records a narrower admission fact: oMLX 0.6.4 has importable, artifact-matching
Laguna and Qwen3.5-MoE paths, and installed llama.cpp includes the merged Laguna
and Qwen3.8 Flash Next architecture support. Exact weights still have to pass
load, output, parser, memory, context, and workload gates.

## Runtime choices on Apple Silicon

- **MLX-LM** is the simplest native MLX baseline: direct safetensors models,
  a small Python server, and explicit prompt-cache APIs. It is valuable as the
  least layered comparison and for checking whether an oMLX optimization caused
  a regression. It does not by itself provide this host's shared model pool,
  SSD-backed prefix lifecycle, memory guard, or profile aliases. [Apple's MLX-LM
  overview](https://developer.apple.com/videos/play/wwdc2025/298/)
- **oMLX** remains the practical agent default when its profile passes
  correctness checks. It adds a model pool, continuous batching, paged/SSD
  prefix caching, memory enforcement and telemetry, TurboQuant KV, native MTP,
  and DFlash profiles. Those features create more configuration identity and
  more ways to benchmark the wrong combination. In particular, the current
  DFlash engine bypasses the ordinary paged prefix cache but has its own bounded
  in-memory L1; a miss performs full prefill, while long-context fallback
  regains the batched engine's cache. [oMLX DFlash
  integration](https://github.com/jundot/omlx/blob/main/docs/experimental/dflash_mlx_integration.md)
- **llama.cpp/GGUF** is the portability and controlled-comparison path. The
  exact same artifact and build can run on both Macs, and Apple Silicon is a
  first-class Metal backend. It has a mature quantization/tooling ecosystem and
  fewer Python layers, but cannot consume MLX safetensors or inherit oMLX-only
  accelerators; newly supported hybrid architectures still require versioned
  validation. [llama.cpp](https://github.com/ggml-org/llama.cpp)
- **BaseRT** is a promising M5-specific follow-up, not part of the current
  winner set. Its July M5 paper reports dedicated Metal 4 tensor kernels and
  especially large prefill gains, and its server exposes tool calls, paged KV,
  and prefix caching. However, the current 0.2 changelog names Qwen3.5/3.6
  hybrid support—not Qwen3.8—and the execution engine is a separately licensed
  proprietary binary even though the CLI/format are Apache-2.0. Test it only
  after exact Qwen3.8 or Ornith compatibility is demonstrated; publisher
  maxima are not a substitute for this harness. [BaseRT M5
  paper](https://arxiv.org/abs/2607.19438) · [BaseRT
  repository](https://github.com/basecompute/baseRT)

MLX is the tensor framework beneath MLX-LM and oMLX; llama.cpp does not “support
MLX” as a model format. Comparing them therefore means comparing separate MLX
safetensors and GGUF artifacts, not flipping an MLX flag on one set of weights.

## Exact frontier definitions

Every row below is still a two-dimensional tradeoff. Correctness, minimum
context, no-swap, and evidence requirements are filters: they decide which
models may compete, but they do not become hidden extra axes. The simpler
[model-first table](current-model-frontiers.md#best-available-model-frontiers)
collapses qualifying implementation details after these exact frontiers are
calculated.

Every active frontier has exactly two decision axes:

- `short-throughput-envelope`: prompt throughput vs decode throughput at one
  fixed `llama-bench` position. Useful for hardware/runtime mechanics, not agent
  latency or quality.
- `interactive-agent-latency`: streaming TTFT (minimize) vs decode throughput
  (maximize), at an exact prompt/output/mode/cache position.
- `tool-agent-operational`: exact tool-call success (maximize) vs end-to-end
  latency (minimize). A 100% success threshold rejects fast broken routes.
- `tool-agent-operational-screen`: the same two axes with one sample permitted.
  It is for early candidate screening and must not be presented as the
  three-sample operational result.
- `warm-cache-operational`: observed input reuse (reported cache-hit tokens
  divided by actual input tokens per repetition, maximize) vs end-to-end latency
  (minimize), with exact tool correctness and zero swap-growth gates. Reuse
  differences within one percentage point are equivalent, preventing a
  negligible cache-ratio change from preserving a much slower route. This is a
  fixed-prefix operational ratio, not a general cache-hit probability.
- `long-context-operational`: exact retrieval success (maximize) vs end-to-end
  latency (minimize), at a fixed long-context position.
- `long-context-value-operational`: requested-value presence (maximize) vs
  end-to-end latency (minimize) at the same fixed position. This isolates
  information access from exact output-format obedience.
- `validated-capacity-memory`: largest fully exact-answer retrieval position
  (maximize) vs peak physical footprint (minimize).
- `validated-capacity-service-memory-screen`: largest fully exact-answer input
  position (maximize) vs a whole-service capacity peak (minimize). On Apple
  unified memory the peak is the kernel-accounted service footprint. On split
  CUDA memory it is host proportional-set memory plus GPU allocation sampled
  at the same instant.
- `validated-capacity-latency-screen`: largest fully exact-answer input
  position (maximize) vs uncached end-to-end time (minimize). It keeps the
  long-and-slow versus short-and-fast choice visible instead of collapsing it
  into a score.
- `retrieved-value-capacity-memory`: largest position where every response
  contains the requested value (maximize) vs peak physical footprint
  (minimize).
- `local-agent-quality-latency`: exact five-task verifier success (maximize) vs
  p95 full task wall time, including attributable failures (minimize).
- `local-agent-quality-memory`: the same exact success score (maximize) vs a
  same-job, fully covered physical-footprint peak (minimize).
- `local-agent-quality-cache-efficiency`: the same exact success score
  (maximize) vs mean uncached input tokens per complete task-set repetition
  (minimize), only when every API request has complete usage accounting.
  Recorded tokens from a timed-out or truncated final request are a lower
  bound, not an eligible axis.

The reusable recipe catalog keeps three non-coding agent workloads separate:

- `long-horizon-research-value`: scientific-workspace research quality versus
  p95 task wall time, with pinned workspaces, rubric, and judge plus exact tool,
  evidence-grounding, 128K, and no-swap gates.
- `web-browsing-value`: hard-to-find live-web answer accuracy versus p95 task
  wall time. The encrypted task revision, answer grader, search/browser
  providers, tool budget, and execution window are cohort identity because the
  web changes.
- `general-assistant-value`: heterogeneous exact-answer quality versus p95 task
  wall time for file, media, browsing, reasoning, and tool tasks. Gated task
  contents remain private while IDs and digests bind the cohort.

A coding/terminal score cannot stand in for any of these, and they cannot stand
in for one another. Their upstream benchmark mapping and publication rules are
documented in
[`research frontier benchmark map`](research-frontier-benchmark-map.md).
The
[`prompt-free real-world summary contract`](real-world-agent-summary-contract.md)
makes those recipes executable: it normalizes task digests, scores, timings,
tool checks, and research grounding through the existing reviewed quality
reconciliation path while rejecting task contents and overbroad BrowseComp or
GAIA publication claims. Context retrieval and swap remain separate exact-route
gates rather than being inferred from a benchmark run.

[`harbor-repeated-frontiers.yaml`](harbor-repeated-frontiers.yaml) packages
robust versions of the three quality frontiers. They require the protocol's
five equal-count repetitions per candidate and compare per-run repeatability
bounds; the initial one-run snapshots remain in `frontiers.yaml` as point
evidence.

A repeated frontier is scoped to the exact cohort supplied to it. The current
five-repeat experiment compares two dense-Qwen reasoning profiles, so its
snapshots answer a configuration-stability question rather than a cross-model
winner question. They must not replace the broader one-run quality population
or add extra Qwen family coverage to `cross-frontier-coverage.json`. Promote a
repeated quality frontier into that general coverage report only after the
relevant cross-model candidates have equal repetition counts under the same
pinned protocol.

Do not pool prompt lengths, cache-warmth states, prose/code/tool modes, or cold
and warm runner states. Build a catalog per position. A separate same-model
efficiency view may compare decode throughput with peak memory, but must restrict
the candidate universe to one checkpoint/quality cohort so quantization quality
is not silently assumed equal.

For a deliberately uncached comparison, `build-local-catalog
--cache-cohort uncached` may combine cache-disabled records with cache-miss
records only when every miss reports zero reused tokens. Runtime cache
enablement remains part of exact offering identity. Position metadata derived
from the observed response (finish reason and timing-source availability) does
not change request identity.

The current epsilon-aware coverage result is:

| Position-specific frontier | Member(s) |
| --- | --- |
| Cross-model pp2048/tg512 throughput | Ornith 1.5 Q4 GGUF |
| Warm 30-tool, 2K-prefix/1K-output operation | Laguna XS 2.1 NVFP4 oMLX |
| Warm 30-tool prefix reuse vs latency | Laguna XS 2.1 NVFP4 oMLX; Qwen3.8 27B DFlash |
| Uncached 30-tool, 2K-prefix/256-output operation | Laguna XS 2.1 NVFP4 oMLX |
| Uncached 126K exact retrieval | Qwen3.8 Flash Next on DS4 |
| Uncached 126K value-found retrieval | Laguna XS 2.1 NVFP4 oMLX |
| Strict exact-answer capacity vs physical footprint | Qwen3.8 Flash Next on DS4 |
| Value-found capacity vs physical footprint | Qwen3.8 Flash Next on DS4 |
| Five-task local-agent quality vs latency | Qwen3.8 27B oMLX, low reasoning/4K thinking |
| Five-task local-agent quality vs exact uncached input | Muse Glimmer |
| Five-task local-agent quality vs process footprint | Qwen3.8 27B oMLX, low reasoning/4K thinking; Muse Glimmer |

The quality rows above deliberately report the Harbor axes before operational
evidence from other workloads is applied. The reviewed
[`Harbor operational-gate policy`](harbor-pilot5-operational-gates-policy.json)
now pins the base catalog plus the exact long-context and uncached-tool source
catalog hashes, and maps complete source and target `OfferingKey` values. Its
[`gated configuration`](harbor-operational-gated-frontiers.yaml) produces three
additional audit views without counting the same quality axes as new portfolio
roles:

| Operationally gated view | Result |
| --- | --- |
| Quality vs latency, validated input ≥125K | Qwen3.8 Flash Next on DS4 |
| Quality vs process footprint, validated input ≥125K | Qwen3.8 Flash Next on DS4 |
| Quality vs latency, strict validated input ≥128,000 | No eligible resident |

All three require at least 60% pilot success, 3/3 exact synthetic tool-call
emission, and nonpositive swap growth at the repeated long-context probe. DS4's
measured 125,964-token position satisfies the first two but not the strict 128K
view. The default Qwen route receives its separately reviewed probe signals but
fails the 60% pilot-quality floor. The tuned Qwen route remains unmapped because
its reasoning policy differs; Muse and Ornith remain unmapped because matching
gate evidence is absent or failed. Missing evidence is therefore a visible
rejection, not inferred success. The generated
[`enriched catalog`](generated/harbor-pilot5-operational-gated-catalog.json) and
all three snapshots retain the policy hash and applied mapping notes.

The three five-task rows above remain the broad one-attempt, cross-model point
frontiers. A separate completed five-run comparison tests only the two dense
Qwen reasoning profiles. Low reasoning with a 4K thinking budget solved 17/25
tasks (68%, per-run range 60–80%) versus 13/25 (52%, range 40–60%) for default
reasoning. The robust quality × time and quality × memory frontiers retain only
the bounded-reasoning profile; default reasoning is below their 60% quality
floor. The quality × uncached-input frontier has no member because timed-out
requests leave both profiles' token totals incomplete.

This supports a Qwen configuration choice, not a cross-model winner. Muse,
DS4, Ornith, and future challengers still need five matched runs before the
repeated result can replace the wider point frontier or enter the family
coverage count. The exact evidence is retained in the
[`paired five-run catalog`](generated/harbor-pilot5-qwen38-paired-five-repeat-catalog.json)
and its [quality × time](generated/harbor-pilot5-qwen38-paired-five-repeat-quality-latency-frontier.json),
[quality × memory](generated/harbor-pilot5-qwen38-paired-five-repeat-quality-memory-frontier.json),
and [quality × uncached input](generated/harbor-pilot5-qwen38-paired-five-repeat-quality-cache-efficiency-frontier.json)
snapshots.

The cross-frontier summary is in
[`generated/cross-frontier-coverage.json`](generated/cross-frontier-coverage.json).
At model-family identity, Laguna covers four exact frontiers—three related
tool/cache views plus fast value retrieval—and is near the value-capacity
frontier. Dense Qwen3.8 and DS4 Flash Next cover three exact frontiers each,
Muse Glimmer covers two, and Ornith covers raw throughput. These are roles, not
points in a hidden combined score. Laguna is not promoted to a general winner
because it failed the matched real-agent smoke and every strict retrieval
position. Flash Coder no longer covers an exact frontier. The
coverage artifact keeps distinct harness, reasoning, and runtime offerings
separate rather than manufacturing a synthetic score.
Muse's matched warm tool probe remains rejected by the 100% correctness
threshold even though its verifier-scored route remains valuable on the
cache-demand and memory frontiers.

That resident count is not a population-completeness claim. The versioned
[`candidate population`](candidate-population.yaml) nominates nine model
families only for the positions where they are intended to compete. Coverage
v3 reports each required model-family/frontier cell as eligible-evaluated,
explicitly gate-rejected, or unattempted. The current evidence attempts 47 of
92 nominated cells (51.09%): dense Qwen and DS4 have complete coverage for their
declared roles, Muse is missing five positions, Ornith two, Flash Coder one,
Laguna four, and North, Nemotron, and Agents-A1 remain 0/11. A rejected cell
counts as an honest attempt but never as exact or near membership. This keeps a
winner among measured offerings from being presented as a settled winner over
promising models that have not run yet.
The cell count measures protocol coverage, not independent experiments; the
warm-tool and warm-cache frontiers deliberately project different decisions
from the same matched captures.

Coverage v3 also reports advisory nearness for every *eligible evaluated*
point. Distance is the smallest relative epsilon at which that point ceases to
be dominated, calculated with the frontier's absolute tolerances and the core
point/robust bound semantics. The report retains the exact half-open dominance
intervals and witnesses; `--near-epsilon` merely labels distances at or below a
chosen threshold. It does not alter membership or selection, and an absent or
eligibility-rejected route never receives a distance. No current dominated
candidate is inside the configured 5% advisory band except Laguna on
value-capacity. Laguna's 126,000-token calibrated position is only 36 tokens
longer than DS4's, so it would be a frontier member with zero context tolerance;
the declared 2% equivalence makes DS4's much lower physical footprint decisive.
On warm cache, Laguna's
0.692-second latency and Qwen DFlash's 99.87% reuse preserve both as exact
members; Ornith now needs about 22.68% relative tolerance to escape dominance.

A separate hardware-only slice compares the same Qwen3.8 27B UD-Q4_K_M bytes,
llama.cpp/ggml binary, and command position on M1 Max and M5 Max. It is retained
in [`generated/qwen38-exact-cross-mac-short-throughput-frontier.json`](generated/qwen38-exact-cross-mac-short-throughput-frontier.json)
but omitted from model-family coverage so a duplicate hardware offering cannot
inflate Qwen's cross-workload count.

The first five-candidate quality population is retained in
[`generated/harbor-pilot5-quality-catalog.json`](generated/harbor-pilot5-quality-catalog.json).
The low-reasoning/4K-thinking Qwen3.8 profile scored 4/5 and has the lowest
all-task p95 at 804.267 seconds, making it the sole quality/latency resident.
The otherwise identical default-reasoning route scored only 2/5. This is strong
one-run configuration-specific evidence, not a stable 40-point difference: the
matched two-run bundles narrow the pooled gap to 70% versus 50%, with touching
quality ranges and overlapping latency ranges.
The recorded uncached-input totals (45,888 for DS4 and
161,473 for Ornith) are lower bounds because their timeout paths contain
incomplete API requests. Muse's terminal-wait timeout had no in-flight model
request, making its 51,424-token uncached total exact and the first cache-demand
resident. Tuned Qwen and Muse form the quality/process-footprint frontier:
80% at 23,782,290,440 bytes versus 60% at 3,484,714,192 bytes. These footprints
are active process working sets, not artifact sizes or a claim that file-backed
demand-paged weights occupy no system cache. The tuned route's one in-flight
timeout makes its 98,321 uncached-input subtotal ineligible for exact cache
comparison. Ornith's initial memory capture remains correctly rejected.

## Reproduce an exact cross-Mac comparison

The original Ornith comparison used the same model bytes and llama.cpp commit:

- artifact SHA-256:
  `ca6ea26329c88b78ffd90a85163be2e746c2fafd1024f56db47e499f117f9a7f`
- artifact size: `21,713,462,848` bytes
- llama.cpp: build 10809, commit `5266f24da`
- position: pp2048/tg512, five measured repetitions, built-in warmup,
  six CPU threads, all Metal layers, Q8_0 K/V, flash attention on,
  batch 2048, micro-batch 512, concurrency one

Capture on each host:

```console
python examples/local-runtime-frontiers/capture_llama_bench.py \
  --binary /path/to/llama-bench \
  --model /path/to/Ornith-1.5-35B-Q4_K_M.gguf \
  --host-id macbook-m5max-64 \
  --exclusive-lock ~/.local/state/model-skyline/local-model-runner.lock \
  --output examples/local-runtime-frontiers/raw/result.json
```

The capture hashes the binary and model, retains every native sample, hashes the
portable command, and replaces only absolute binary/model paths. Normalization
requires an explicit `--quantization` label; custom mixed artifacts must never
inherit the Q4_K_M control's label. Workload identity and methodology are
derived from the capture's actual prompt, generation, runtime commit, and
repetition count. Then build an ordinary catalog:

```console
modelskyline validate-local-measurement measurements/result.json
modelskyline build-local-catalog measurements/m5.json measurements/m1.json \
  --output generated/short-throughput-catalog.json
modelskyline evaluate frontiers.yaml generated/short-throughput-catalog.json \
  short-throughput-envelope --format json \
  --output generated/short-throughput-frontier.json \
  --as-of 2026-09-13T03:15:00Z
```

Once one exact snapshot contains the provider or host panel, generate its two
model-focused views with an explicit balanced matrix:

```console
modelskyline model-frontier-view \
  generated/cross-mac-two-model-short-throughput-frontier.json \
  --balanced-policy cross-mac-short-throughput-model-view-policy.json \
  --format json \
  --output generated/cross-mac-two-model-short-throughput-model-view.json
```

This keeps the fastest real implementation as the best-available view and
separately averages the matched M1/M5 values. It fails if either model is
missing a declared machine.

OpenAI-compatible captures retain a position-specific record reference. To
evaluate several exact offerings under one declared workload, provide the
configured catalog reference explicitly. `uncached` accepts only disabled or
reported-zero-hit misses:

```console
modelskyline build-local-catalog measurements/qwen.json measurements/ds4.json \
  --workload-id long-context-retrieval-v1 \
  --workload-version 1 \
  --workload-unit retrieval_probe \
  --cache-cohort uncached \
  --output generated/long-context-catalog.json
```

Capacity is a roll-up rather than a single prompt position. It chooses each
exact offering's largest fully passing uncached retrieval record and requires a
sampled physical-footprint series at that position. Attempted offerings that
never pass are retained with their ladder and configured context in audit
metadata, but receive no validated-context or paired-memory signal. The
frontier therefore rejects them visibly instead of treating allocation as
usable context or dropping them from the candidate universe:

```console
modelskyline build-local-capacity-catalog measurements/*retrieval*.json \
  --output generated/validated-capacity-catalog.json
```

The default above is strict. The value-found roll-up uses only records with an
explicit `retrieval_value` result and changes both the workload and signal name,
so downstream users cannot mistake it for exact-answer capacity:

```console
modelskyline build-local-capacity-catalog measurements/*retrieval*.json \
  --integrity-check retrieval_value \
  --workload-id retrieved-value-capacity-v1 \
  --output generated/retrieved-value-capacity-catalog.json
```

Quantization integrity uses a separate pinned-corpus capture. It hashes the
runtime, model, and corpus bytes, disables llama.cpp's automatic fit behavior,
holds the same host-wide runner lock, and retains the full native output plus
the parsed final estimate and standard error:

```console
python examples/local-runtime-frontiers/capture_llama_perplexity.py \
  --binary /path/to/llama-perplexity \
  --model /path/to/model.gguf \
  --corpus /path/to/wikitext-2-raw-v1-test-head512.txt \
  --host-id macbook-m5max-64 \
  --exclusive-lock ~/.local/state/model-skyline/local-model-runner.lock \
  --output examples/local-runtime-frontiers/raw/model-ppl.json
```

PPL is compared only across captures with identical corpus bytes, tokenizer
path, llama.cpp build, context/chunk settings, and KV/runtime configuration. It
is a quantization-sensitive regression signal, not a general coding-quality
score and not an axis of the throughput frontier. A missing, malformed, or
non-finite final estimate is still written as an `invalid` raw capture, with
the process status and portable native output retained; it is never silently
dropped or converted into a numeric score.

## Ornith exact-artifact result

Medians from the retained repetitions:

| Hardware | Prompt tok/s | Decode tok/s |
| --- | ---: | ---: |
| MacBook Pro, M5 Max 40-core GPU, 64 GB | 3,022.92 | 113.7480 |
| Mac Studio, M1 Max 32-core GPU, 64 GB | 778.092 | 56.7327 |

On this Q4_K_M MoE artifact and fixed build/config, the M5 Max is 3.885x faster
for prompt processing and 2.005x faster for decode. The very different ratios
are useful evidence about where newer GPU/tensor capabilities matter; they do
not justify projecting the same multipliers onto long-context attention,
dense models, MLX, tool-heavy speculative decoding, or end-to-end agent work.

The regenerated diagnostic frontier now evaluates five exact offerings: Q4 on
both Macs, the ShoeHorn mixed artifact on both Macs, and Q5 on the M5. The M5
Q4 artifact is the sole prompt/decode frontier member. M5 Q5 is dominated by
M5 Q4 on both axes, and the M5 ShoeHorn artifact is dominated by both ordinary
quants. This is a speed-frontier result, not a claim that their quality is
equal; the separate perplexity evidence in the ShoeHorn audit merely failed to
show a compensating fidelity gain.

## Qwen3.8 exact-artifact result

The later dense-model control hashes both the 16,464,440,224-byte UD-Q4_K_M
artifact and the runtime. Both hosts used artifact SHA-256
`322e194ff79741c7baa497c240f677f54b201b0efab44ca8e50f122b39123482`
and llama-bench SHA-256
`30723a650e9e4a3d12bbe558c44e378d34707d14ebfd5d5e8535a8827eadceb9`.
The pp2048/tg512 position and every runtime option match the Ornith protocol.

| Hardware | Prompt tok/s | Decode tok/s |
| --- | ---: | ---: |
| MacBook Pro, M5 Max 40-core GPU, 64 GB | 680.485 | 26.6322 |
| Mac Studio, M1 Max 32-core GPU, 64 GB | 128.356 | 11.5657 |

The M5 advantage is 5.302x for prompt processing and 2.303x for decode. Along
with Ornith's 3.885x/2.005x, this shows that a single hardware multiplier is
not portable even across two exact llama.cpp positions. Because all model
layers are on Metal, these runs compare the full systems rather than isolating
CPU performance; a separate CPU-only position is needed for that claim.

The separate CPU-only Qwen control uses pp512/tg64, three repetitions, six
threads, and `--n-gpu-layers 0`. M5 medians are 16.870 prompt and 6.788 decode
token/s; M1 medians are 15.077 and 4.730, respectively. The 1.119x/1.435x gaps
are much smaller than the all-Metal gaps. They still measure CPU plus DRAM and
the compiled ggml backend rather than pure CPU core IPC, and therefore remain a
hardware diagnostic outside the production frontier.

The M5 capture used AC power through a directly connected Apple 140W adapter;
the charger reports a negotiated 140W and `pmset` reports mode `2` (High Power
configured). On this macOS/M5 combination, `system_profiler` nevertheless
reports High Power “No” and Low Power “Yes,” matching a recent reported status
disagreement. The evidence therefore identifies the mode as
`high-power-configured-pmset-2`; it does not claim an independently verified
power governor. No thermal or performance warning was present on the later
recheck.

During a later sustained ShoeHorn exact-error pass using roughly 17 CPU cores,
the raw Apple battery telemetry reported `SystemPowerIn=96993` and
`BatteryPower=0` while the same USB-C adapter remained negotiated at 140 W.
Those undocumented telemetry units plausibly mean about 97 W into the system,
but this is not a wall-meter measurement. The defensible conclusion is that
the cable sustained the workload without battery supplementation—not that the
machine continuously drew the adapter's full 140 W rating.

## Cache and switch semantics

On the measured host, llama-swap owns one OpenAI-compatible endpoint at
`127.0.0.1:8090`. Every llama.cpp, MLX-LM, oMLX, DS4, Muse, Ornith, and Ollama
model is a member of one exclusive `local-memory` group, so selecting a model
ID unloads the previous managed runner before starting the next one. The oMLX
process is shared by aliases for each exposed profile; switching between an
oMLX profile and another heavyweight runtime still follows the same mutex.
`/running` is the authoritative check before a direct benchmark bypasses the
router. Every managed launcher also holds the same host-wide advisory lock for
its full lifetime. Direct llama-bench and llama-perplexity captures request that
lock with a zero-second timeout, closing the race in which an agent request
could start a runner after the idle check; a busy lock fails the capture instead
of contaminating it. The capture wrappers use the host's native tool: BSD
`lockf` on macOS and Linux `flock`. The lock path must match the launcher on that
host; the path itself is not shared between machines.

Loading-state streaming is disabled because some agent clients preserve those
operational messages as assistant content. The managed TTL is 900 seconds, and
cold-load/post-expiry latency is measured separately. The oMLX launcher enables
the paged SSD prefix cache by default with a zero-byte RAM hot cache; controlled
cache-free measurements set `QWEN38_OMLX_CACHE=0`. OpenCode and OMP both expose
the non-speculative baseline/F16-KV and baseline/TQ4-KV controls, MTP/F16-KV,
MTP/TQ4-KV, and DFlash/TQ4 aliases through the same router. The paired baseline
profiles isolate KV compression from speculative decoding. OMP has to decide
when to shorten a long conversation into a summary. It is set
to do that after the conversation uses 75% of whichever model's declared
context window is selected. This leaves the final 25% for the next response,
tool results, and the summarization step itself.

| Selected model's declared window | OMP starts compaction at | Remaining headroom |
| ---: | ---: | ---: |
| 262,144 tokens | 196,608 tokens | 65,536 tokens |
| 131,072 tokens | 98,304 tokens | 32,768 tokens |

There is no fixed global token limit. We initially discussed using 180,000
tokens for every model, but that number is larger than the entire window of the
131,072-token DS4 and Muse routes. The percentage setting therefore adapts to
both model classes. These numbers describe when the OMP client summarizes a
conversation; they do not prove that a model can use its advertised context
well. Retrieval and memory tests supply that separate evidence.

The Ollama-backed `gpt-oss:20b` route is the one deliberate TTL exception:
its per-model llama-swap TTL is 300 seconds, matching Ollama's documented
five-minute default keep-alive. Without that alignment the router could report
the holder as warm for ten minutes after Ollama had silently released the
weights, hiding a reload inside an allegedly steady-state request.

In OMP, do not name a custom llama-swap-backed provider exactly `ollama`.
That reserved provider enables Ollama discovery semantics; when its base URL
was set to llama-swap, every router model was duplicated as an Ollama model and
fuzzy selection preferred false 128K/8K/no-reasoning metadata. The local config
uses `ollama-local` for the routed `gpt-oss` entry and keeps explicit
`llama.cpp`, `mlx-local`, `omlx-local`, and `ds4-local` model identities. Verify
changes with `omp --list-models <pattern>` before trusting a fuzzy selection.

`prefix_cache_enabled` belongs to runtime identity. Cache warmth (`disabled`,
`miss`, `warm`, or `mixed`) belongs to the workload position. Cold load, warm
runner, and post-idle-expiry are also separate positions. A matrix should run
one model/profile in a contiguous batch so llama-swap unload/reload time is not
mistaken for steady-state request latency. Deliberate cold-start measurements
should retain their own load/TTFT series.

The included oMLX cache A/B uses the same 18,099-token prompt and 64-token
completion twice. With cache disabled, TTFT was 4.674 s then 4.749 s. With the
SSD prefix cache enabled, the miss took 4.652 s and the warm request took
0.892 s with 16,384 cached tokens. End-to-end latency fell from 5.098 s on the
miss to 1.339 s warm while decode stayed near 143 token/s. These are separate
miss and warm positions, not two samples of one distribution.

`openai_matrix.py` captures first semantic stream event, server TTFT when
reported, end-to-end time, usage, output
digests, loading-state contamination, and tool-call JSON/correctness. It sends
one model in a contiguous serial batch and offers fixed prefix and output
ladders:

```console
python examples/local-runtime-frontiers/openai_matrix.py \
  --base-url http://127.0.0.1:8090/v1 \
  --model exact-served-model-id \
  --mode tool \
  --tool-count 30 \
  --tool-choice auto \
  --thinking-mode disabled \
  --prefix-tokens 512,8192,32768 \
  --max-outputs 64,256 \
  --repetitions 3 \
  --warmup \
  --process-match 'omlx serve' \
  --runtime-stats-url http://127.0.0.1:8184/admin/api/stats \
  --runtime-stats-cookie-env OMLX_ADMIN_SESSION \
  --output examples/local-runtime-frontiers/raw/tool-matrix.json
```

The harness opens a fresh loopback TCP connection for every request. Some local
servers close the connection after a very large streamed response while still
remaining healthy; reusing that stale connection made the following cached
request look like a model failure. This transport choice avoids that false
failure and does not alter the measured model workload.

Current oMLX protects its admin telemetry with a signed session cookie even
when the inference API itself has no key. `--runtime-stats-cookie-env` sends an
already-created session value only to the loopback stats URL and records the
environment-variable name, never the credential. It may be omitted for an
unprotected telemetry endpoint; a 401 is retained as a capture error and
cannot yield speculative-acceptance evidence.
While a request is active, the same authenticated sampler polls oMLX's reported
MLX/Metal active-memory pressure once per second. Its maximum is published as
`local_peak_metal_active_bytes`; process RSS remains a separate metric because
it does not include all unified-memory allocations visible to MLX.

Tool mode can expose one to 30 deterministic schemas. `--tool-choice forced`
is the backward-compatible argument-generation control; `auto` also tests
selection among realistic distractors, and `required` requires some tool
without naming it. Tool count, complete schema, and choice policy are all part
of the hashed semantic input and retained workload metadata. A 30-tool result
must therefore remain distinct from the one-tool microbenchmark.

Thinking is also an explicit workload dimension. The harness defaults to
`--thinking-mode disabled`, sends that choice through chat-template arguments,
includes it in the semantic request hash, and retains it in position metadata.
Use `enabled` for a reasoning workload or `runtime_default` only when the
runtime's implicit behavior is itself the subject of the measurement. A short
output ceiling can otherwise truncate reasoning before any final answer and
turn a retrieval check into a harness artifact.
For models such as Muse that also interpret a text instruction for reasoning
strength, `--system-prompt` sends an exact caller-supplied value. The harness
hashes that value but does not retain its plaintext. System-prompt variants are
therefore different workload positions, while private prompts remain absent
from publishable raw evidence.

Normalize with `normalize_openai_matrix.py` plus an exact hardware profile and
system profile. The normalizer rejects captures containing llama-swap loading
content, rejects a requested output limit above the runtime profile's server
ceiling, and automatically splits cache misses from warm hits. The semantic
input hash covers the system message, user message, tool schema, and tool
choice—not just the user text—and the tool count/schema hash and finish reasons
remain in the position metadata. Real agent A/B tests should likewise keep
system prompts and tool schemas byte-stable; reducing the tool set is a new
workload and must not be mixed into the same position. For tool mode the
normalizer publishes both exact-call success and argument-JSON parse success,
so a faster speculative profile cannot hide broken tool syntax behind
aggregate TPS.
Each request retains host swap before and after on macOS and Linux. macOS also
retains memory-pressure, thermal-warning, and power snapshots; `--process-match`
adds sampled peak RSS and macOS
kernel-accounted physical footprint for a literal command substring. Physical
footprint captures Metal allocations that ordinary RSS misses. Clean mapped
files, artifact byte size, and physical footprint remain distinct quantities.
Cold-load and post-idle captures use `--runner-state` in a separate
one-repetition run with no warmup.
When a request goes through llama-swap, add
`--runner-status-url http://127.0.0.1:8090/running` and, for an alias, its real
`--runner-model-id`. The harness polls the router's `starting` to `ready`
transition during the request and normalizes health-ready latency as
`local_cold_load_seconds`; TTFT remains the separate user-visible
load-plus-prefill measure. Before a deliberate cold run, unload managed models
with `POST /api/models/unload` and verify `/running` is empty. These lifecycle
endpoints are part of [llama-swap's documented API](https://github.com/mostlygeek/llama-swap#api-endpoints).
For oMLX DFlash profiles, `--runtime-stats-url` captures the engine's exact
per-request acceptance summary; normalization emits it as
`local_speculative_acceptance_percent` rather than inferring acceptance from
TPS. Acceptance is measured separately for prose, code, and tool requests;
there is no assumed constant acceptance rate. TurboQuant KV is likewise a
separate runtime identity. Four-bit KV can greatly reduce the KV component, but
it does not promise to double total context headroom when weights, recurrent
state, compute buffers, or only a subset of hybrid layers dominate memory.
Long-position retrieval and tool-call checks are required before promotion.
DFlash's private in-memory prefix cache is also independent of oMLX's ordinary
paged cache. If enabled in the model profile, the runtime identity must say so
and captures must split its zero-hit miss from warm hits. Tool APIs may buffer
a structured call into one stream delta; in that case the harness retains
first-complete-semantic-event latency separately and prefers the server's
reported generation timing over a meaningless near-zero stream interval.
Every normalized position records `time_to_first_token_source` and
`decode_rate_source`. A server-reported generation rate and the client's
post-first-token inter-token rate are different estimands and must not share a
frontier axis without an explicit conversion policy. If a tool call is buffered
and the server reports neither value, the normalizer deliberately omits TTFT and
decode rate while retaining semantic-event and end-to-end latency.

For a whole-service memory capture, bind its digest to the exact OpenAI matrix
capture during normalization:

```console
python examples/local-runtime-frontiers/normalize_openai_matrix.py \
  --capture examples/local-runtime-frontiers/raw/request-matrix.json \
  --hardware examples/local-runtime-frontiers/hardware/inference-vm-rtx5060ti16.json \
  --system-profile examples/local-runtime-frontiers/system-profiles/exact-profile.json \
  --raw-artifact-path raw/request-matrix.json \
  --service-memory-capture examples/local-runtime-frontiers/raw/service-memory.json \
  --service-memory-raw-artifact-path raw/service-memory.json \
  --measurement-prefix exact-profile-request \
  --kind long_context_retrieval \
  --output-directory examples/local-runtime-frontiers/measurements
```

Both service-memory arguments are required together. The normalizer verifies
the hardware ID, clean child exit, privacy flag, exact workload filename and
SHA-256 binding, then attaches the memory evidence only to an uncached or
cache-disabled position. The compact measurement retains the peak and source
digest rather than duplicating every sampler row.

Retrieval mode builds deterministic unique distractor records, inserts one
passkey at a fixed character fraction, and asks for that passkey alone. It
records two deliberately different checks: `retrieval` passes only when the
complete stripped answer is the passkey, while `retrieval_value` records whether
the passkey appeared anywhere in the answer. Only the strict first check grants
`local_validated_context_tokens` and admission to the validated-capacity
frontier. The second emits a separate retrieved-value capacity signal and can
admit the model to the explicitly named value-found frontiers. It explains the
common case where a model found the value but ignored the exact-output
instruction; it never turns allocation or verbose substring-bearing prose into
a strict pass. The requested sizes are prompt
construction targets; the response API's usage count is the actual token count
published in evidence. Run early, middle, and late needles as separate captures:

```console
python examples/local-runtime-frontiers/openai_matrix.py \
  --base-url http://127.0.0.1:8090/v1 \
  --model exact-served-model-id \
  --mode retrieval \
  --thinking-mode disabled \
  --retrieval-position 0.9 \
  --prefix-tokens 2048,32768,65536,126000 \
  --max-outputs 64 \
  --repetitions 3 \
  --warmup \
  --token-count-url http://127.0.0.1:8184/v1/messages/count_tokens \
  --output examples/local-runtime-frontiers/raw/retrieval-late.json
```

When available, `--token-count-url` binary-searches prompt construction against
the loaded model's tokenizer before inference. The requested ladder value,
construction count, and response API's actual input count are all retained.
This prevents high-entropy distractors from silently turning a nominal 32K
prompt into a much larger tokenizer workload.
For a runtime without token counting, replay the resulting byte-identical
prompts with `--retrieval-characters` and one comma-separated character target
per ladder position. This is preferable to assuming that four characters equal
one token; the response's actual usage count remains authoritative.

## Provisional Qwen3.8 agent controls

The first realistic tool-selection control exposes 30 deterministic tool
schemas, leaves selection on automatic, disables thinking explicitly, and asks
for one exact call. The complete request contains 5,380 actual input tokens.
Server-reported medians are:

| oMLX profile / cache position | TTFT (s) | End-to-end (s) | Decode tok/s | Prompt tok/s | Exact calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| DFlash2 + TQ4 KV, miss | 6.76 | 7.430 | 63.92 | 795.56 | 1/1 |
| DFlash2 + TQ4 KV, warm L1 | 0.18 | 0.812 | 67.50 | 30,181 | 3/3 |
| Baseline + F16 KV, disabled | 6.34 | 7.536 | 33.68 | 849.22 | 3/3 |
| Baseline + TQ4 KV, disabled | 6.80 | 8.095 | 32.23 | 790.72 | 3/3 |

DFlash acceptance was 80.95% in every measured tool request, and every profile
selected `lookup_fixture`, produced parseable JSON, and supplied the exact
arguments. No request increased swap usage. These results contradict both a
blanket claim that DFlash collapses on tool calls and a blanket claim that
four-bit KV is automatically faster: DFlash doubled decode here, while TQ4
without speculation was slower than F16 KV at this short position.

The paired baseline and DFlash one-repetition 126K capacity probes reuse
byte-identical prompts, contain 125,964 actual input tokens, disable thinking,
and all return the middle-position passkey exactly:

| oMLX profile | TTFT (s) | Decode tok/s | Prompt tok/s | Peak active bytes | Post-request active bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| DFlash2 + TQ4 KV | 486.20 | 35.92 | 259.08 | 39,785,067,960 | 25,957,774,848 |
| Baseline + F16 KV | 281.49 | 20.81 | 447.49 | 47,971,333,248 | 25,651,737,728 |
| Baseline + TQ4 KV | 273.47 | 17.47 | 460.61 | 47,957,374,032 | 22,298,621,032 |

No request increased swap usage. TQ4 retained 3.35 GB less active memory after
the request than F16, but its peak was effectively identical because this oMLX
path prefills in F16 before converting the eligible cache layers. It also
decoded 16% slower at this position. DFlash reduced peak active memory by about
8.17 GB and decoded 73% faster than F16, but prefilling the same bytes took 73%
longer. Its per-request speculative acceptance was 63.64%. These are provisional
capacity observations, not stable latency distributions: each 126K profile has
one measured repetition. The full DFlash ladder separately passed the same
exact retrieval check at 2,012, 32,732, 65,500, and 125,964 actual input tokens.

## Provisional DS4 Qwen3.8 Flash Next experiment

Here, DS4 means [DwarfStar](https://github.com/antirez/ds4), not DeepSeek 4.
The tested [Qwen3.8 port](https://github.com/antirez/ds4/pull/26) serves
Qwen3.8 Flash Next weights through a DS4-specific Metal engine. The exact
composite artifact is a 44,806,612,192-byte IQ2 main GGUF plus a
32,000,157,440-byte Q4_1 PLE sidecar. The runtime maps the main artifact and
demand-pages the sidecar; its launch plan is 47.00 GiB resident, including
4.17 GiB of KV and 1.11 GiB of buffers. This is therefore an aggressive 64 GB
experiment, not evidence that the full 76.81 GB composite is resident at once.

The target-only engine returned the exact middle-position passkey at every
point in this one-repetition ladder, with thinking explicitly disabled:

| Actual input tokens | Client first text (s) | End-to-end (s) | Client post-first-token rate (token/s) | Exact retrieval |
| ---: | ---: | ---: | ---: | ---: |
| 2,012 | 3.932 | 4.267 | 53.62 | 1/1 |
| 32,732 | 50.583 | 50.970 | 49.18 | 1/1 |
| 65,500 | 100.887 | 101.275 | 48.99 | 1/1 |
| 125,964 | 189.257 | 189.662 | 49.44 | 1/1 |

A separate warmed-OS-cache, three-repetition 2K control makes target-only and
native MTP easier to compare. The target-only medians were 2.399 s to first
text, 2.731 s end-to-end, and 54.15 token/s post-first-token. MTP medians were
2.531 s, 2.780 s, and 72.39 token/s. At 125,964 input tokens, the one-shot MTP
request finished in 185.603 s versus 189.662 s target-only. MTP improved token
cadence, but did not improve short-response end-to-end latency in the repeated
2K control.

Both profiles also selected the exact tool from 30 automatic choices and
produced parseable, exact arguments in 3/3 trials. Median end-to-end latency was
6.433 s target-only and 6.591 s with MTP. DS4 buffers the structured tool call
and does not expose server timing in its response usage, so those tool records
intentionally contain no TTFT or decode-rate metric. All published DS4 requests
reported zero prefix-cache hit tokens and zero per-request swap growth. Native
MTP remains benchmark-only for now; target-only is the safer default for agent
work until longer coding-output controls show an end-to-end benefit.

These measurements establish capacity and narrow integrity checks, not quality
equivalence with the oMLX 27B profile or any cloud model. Early/late needles,
longer generated code, and repeated 126K positions are still required before
DS4 enters a long-context utility frontier.

## Provisional Muse Glimmer experiment

Muse Glimmer is a dense 30B-class checkpoint; “DFlash2” in the local route is a
separate speculative draft model and does not make Muse an MoE. The official
19,653,960,832-byte Dynamic Q4_K_XL is the current quantization winner. At the
same llama.cpp pp2048/tg512 position it reached median 717.593 prompt token/s
and 24.9124 decode token/s. The 19,672,967,680-byte ShoeHorn fit reached
695.556 and 24.5243 respectively, while its pinned-corpus PPL was 5.4027 versus
5.0167 for the official artifact. The custom fit is therefore slightly larger,
slower on both axes, and worse on two small PPL controls; it does not enter the
recommended set.

The 16 GB RTX 5060 Ti requires a much smaller offering. AtomicChat's calibrated
12,224,327,968-byte AD-IQ3_XXS is the retained route. It reaches median
pp2048/tg512 rates of 1,022.59 and 31.3664 token/s, passes 3/3 automatic
30-tool calls, and returns the exact hidden value with the needle near the
beginning, middle, and end of roughly 126K input tokens. Its pinned-corpus PPL
is 5.2003 ± 0.11916.

A general tensor-override implementation proposed in ShoeHorn PR #6 made a
13,096,573,440-byte, 3.758-bpw alternative possible at the same 128K/Q4-KV
service position. It also passes the checked tools and all three retrieval
positions, and its prompt-processing median is 1,109.02 token/s. It is not the
default: decode falls to 30.6355 token/s, PPL worsens 10.2% to 5.7316, and some
answers consume far more reasoning tokens. This is a valid custom fit and a
failed promotion, not a broken artifact. The exact plan and matched evidence
are in the [Muse audit](muse-glimmer-audit.md).

The llama-swap routes expose both the official target-only artifact and the
same target plus the official DFlash2 Q4_K_M draft. Both use a 131,072-token
allocation and explicit Q8_0 target/draft KV where applicable. On a 5,390-token
30-tool prompt with a 1,024-token output allowance, target-only selected the
exact tool cold (1/1) and warm (3/3). DFlash selected it warm in two independent
3/3 batches, cutting one batch's median end-to-end time from 15.038 s to
8.359 s, but failed after two independent cold loads with the same non-tool
answer byte-for-byte. Its ready transition was only 2.29–2.42 s; the failure is
semantic, not loading text or a health-check timeout. With a 256-token ceiling,
DFlash also failed all three requests by exhausting the allowance before a tool
call.
Disabling prompt reuse preserved the same wrong answer on consecutive requests;
the correct tool call appeared only after a 5,389-token prefix-cache hit. This
isolates the divergence to DFlash plus llama.cpp's prompt-reuse path.

Consequently OpenCode and OMP label DFlash experimental and expose target-only
as the recommended Muse route. Every capture reported zero loading-state
events and zero per-request swap growth. See the [Muse Glimmer audit](muse-glimmer-audit.md)
for exact artifacts, the ShoeHorn plan, full cache-state evidence, and suggested
upstream follow-ups.

See [ShoeHorn applicability audit](shoehorn-audit.md) for the checked boundary
between a meaningful Ornith exact-fit experiment and the architectural work
still required before applying the same method to Qwen3.8 Flash Next.
