# Local runtime frontiers

This example makes local inference measurements ordinary ModelSkyline evidence
without pretending that a model name determines performance. Hardware, exact
artifact bytes, runtime build and full configuration, physical context ceiling,
KV/cache/speculation profile, harness, workload position, run conditions, and
raw-result digest are all retained.

The current artifacts are provisional. They now include cross-model
throughput, warm and uncached tool operation, repeated 126K retrieval, and a
validated-capacity/physical-footprint roll-up. They remain workload-specific,
not a universal model ranking.

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
earns the narrow uncached-tool frontier but fails the real-agent smoke and the
retrieval ladder, so it is not a general local-agent recommendation.

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

## Frontier definitions

Every active frontier has exactly two decision axes:

- `short-throughput-envelope`: prompt throughput vs decode throughput at one
  fixed `llama-bench` position. Useful for hardware/runtime mechanics, not agent
  latency or quality.
- `interactive-agent-latency`: streaming TTFT (minimize) vs decode throughput
  (maximize), at an exact prompt/output/mode/cache position.
- `tool-agent-operational`: exact tool-call success (maximize) vs end-to-end
  latency (minimize). A 100% success threshold rejects fast broken routes.
- `long-context-operational`: exact retrieval success (maximize) vs end-to-end
  latency (minimize), at a fixed long-context position.
- `validated-capacity-memory`: largest fully passing retrieval position
  (maximize) vs peak physical footprint (minimize).
- `local-agent-quality-latency`: exact five-task verifier success (maximize) vs
  p95 full task wall time, including attributable failures (minimize).
- `local-agent-quality-memory`: the same exact success score (maximize) vs a
  same-job, fully covered physical-footprint peak (minimize).
- `local-agent-quality-cache-efficiency`: the same exact success score
  (maximize) vs mean uncached input tokens per complete task-set repetition
  (minimize), only when every API request has complete usage accounting.
  Recorded tokens from a timed-out or truncated final request are a lower
  bound, not an eligible axis.

[`harbor-repeated-frontiers.yaml`](harbor-repeated-frontiers.yaml) packages
robust versions of the three quality frontiers. They require the protocol's
five equal-count repetitions per candidate and compare per-run repeatability
bounds; the initial one-run snapshots remain in `frontiers.yaml` as point
evidence.

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
| Warm 30-tool, 2K-prefix/1K-output operation | Ornith 1.5 oMLX; Qwen3.8 27B DFlash |
| Uncached 30-tool, 2K-prefix/256-output operation | Qwen3.8 Flash Coder 160-expert Q4_K_M |
| Uncached 126K exact retrieval | Qwen3.8 Flash Next on DS4 |
| Validated capacity vs physical footprint | Qwen3.8 Flash Next on DS4 |
| Five-task local-agent quality vs latency | Qwen3.8 27B oMLX, low reasoning/4K thinking |
| Five-task local-agent quality vs exact uncached input | Muse Glimmer |
| Five-task local-agent quality vs process footprint | Qwen3.8 27B oMLX, low reasoning/4K thinking; Muse Glimmer |

The three five-task rows above are the initial one-attempt point frontiers, not
stable defaults. Matched second Qwen jobs produced 5/10 pooled success for the
default profile (40–60% observed run range) and 7/10 for bounded reasoning
(60–80%). Their quality ranges touch at 60% and their latency ranges overlap,
so this is directional configuration evidence rather than a robust winner. The
packaged robust frontiers stay unpublished until every compared candidate has
the protocol's five repetitions. The exact paired evidence is retained in the
[`two-repeat catalog`](generated/harbor-pilot5-qwen38-paired-repeat2-catalog.json).

The cross-frontier summary is in
[`generated/cross-frontier-coverage.json`](generated/cross-frontier-coverage.json).
At model-family identity, dense Qwen3.8 covers three complementary frontiers;
DS4 Flash Next, Muse Glimmer, and Ornith each cover two. The custom Flash Coder
slice covers one narrow uncached-tool frontier. It is not promoted to a wider
winner because it failed the matched real-agent smoke and every strict
retrieval position. The coverage artifact keeps distinct harness, reasoning,
and runtime offerings separate rather than manufacturing a synthetic score.
Muse's matched warm tool probe remains rejected by the 100% correctness
threshold even though its verifier-scored route remains valuable on the
cache-demand and memory frontiers.

Coverage v2 also reports advisory nearness for every *eligible evaluated*
point. Distance is the smallest relative epsilon at which that point ceases to
be dominated, calculated with the frontier's absolute tolerances and the core
point/robust bound semantics. The report retains the exact half-open dominance
intervals and witnesses; `--near-epsilon` merely labels distances at or below a
chosen threshold. It does not alter membership or selection, and an absent or
eligibility-rejected route never receives a distance. At the packaged 5%
threshold there are currently no near-only residents. The closest dominated
points are DS4 on the uncached-tool frontier at 14.09% and Flash Coder on the
warm-tool frontier at 15.32%, so neither is honestly interchangeable with that
frontier's exact residents.

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
score and not an axis of the throughput frontier.

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
router. Every managed launcher also holds the same BSD file lock for its full
lifetime. Direct llama-bench captures request that lock with a zero-second
timeout, closing the race in which an agent request could start a runner after
the idle check; a busy lock fails the capture instead of contaminating it.

Loading-state streaming is disabled because some agent clients preserve those
operational messages as assistant content. The managed TTL is 900 seconds, and
cold-load/post-expiry latency is measured separately. The oMLX launcher enables
the paged SSD prefix cache by default with a zero-byte RAM hot cache; controlled
cache-free measurements set `QWEN38_OMLX_CACHE=0`. OpenCode and OMP both expose
the non-speculative baseline/F16-KV and baseline/TQ4-KV controls, MTP/F16-KV,
MTP/TQ4-KV, and DFlash/TQ4 aliases through the same router. The paired baseline
profiles isolate KV compression from speculative decoding. OMP keeps the
agreed 180,000-token global compaction trigger, while 262K-capable backends
advertise a 262,144-token hard ceiling.

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
On macOS, each request also retains swap, memory-pressure, thermal-warning, and
power snapshots; `--process-match` adds sampled peak RSS and macOS
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

Retrieval mode builds deterministic unique distractor records, inserts one
passkey at a fixed character fraction, asks for that passkey alone, and counts
only an exact stripped final answer as success. It never treats allocation or
substring-bearing prose as a retrieval pass. The requested sizes are prompt
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
