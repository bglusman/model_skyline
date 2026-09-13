# Local runtime frontiers

This example makes local inference measurements ordinary ModelSkyline evidence
without pretending that a model name determines performance. Hardware, exact
artifact bytes, runtime build and full configuration, physical context ceiling,
KV/cache/speculation profile, harness, workload position, run conditions, and
raw-result digest are all retained.

The current artifacts are provisional. They demonstrate the contract and one
controlled cross-generation comparison; they are not yet a broad model ranking.

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

## Frontier definitions

Every active frontier has exactly two decision axes:

- `short-throughput-envelope`: prompt throughput vs decode throughput at one
  fixed `llama-bench` position. Useful for hardware/runtime mechanics, not agent
  latency or quality.
- `interactive-agent-latency`: streaming TTFT (minimize) vs decode throughput
  (maximize), at an exact prompt/output/mode/cache position.
- `long-context-operational`: exact retrieval success (maximize) vs end-to-end
  latency (minimize), at a fixed long-context position.
- `validated-capacity-memory`: largest fully passing retrieval position
  (maximize) vs peak physical footprint (minimize).

Do not pool prompt lengths, cache-warmth states, prose/code/tool modes, or cold
and warm runner states. Build a catalog per position. A separate same-model
efficiency view may compare decode throughput with peak memory, but must restrict
the candidate universe to one checkpoint/quality cohort so quantization quality
is not silently assumed equal.

## Reproduce the current comparison

Both machines used the same model bytes and llama.cpp commit:

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

## Initial exact-artifact result

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

The M5 capture used AC power through a directly connected Apple 140W adapter;
the charger reports a negotiated 140W and `pmset` reports mode `2` (High Power
configured). On this macOS/M5 combination, `system_profiler` nevertheless
reports High Power “No” and Low Power “Yes,” matching a recent reported status
disagreement. The evidence therefore identifies the mode as
`high-power-configured-pmset-2`; it does not claim an independently verified
power governor. No thermal or performance warning was present on the later
recheck.

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
the baseline, MTP/F16-KV, MTP/TQ4-KV, and DFlash/TQ4 aliases through the same
router. OMP keeps the agreed 180,000-token global compaction trigger, while
262K-capable backends advertise a 262,144-token hard ceiling.

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

`openai_matrix.py` captures streaming TTFT, end-to-end time, usage, output
digests, loading-state contamination, and tool-call JSON/correctness. It sends
one model in a contiguous serial batch and offers fixed prefix and output
ladders:

```console
python examples/local-runtime-frontiers/openai_matrix.py \
  --base-url http://127.0.0.1:8090/v1 \
  --model exact-served-model-id \
  --mode tool \
  --prefix-tokens 512,8192,32768 \
  --max-outputs 64,256 \
  --repetitions 3 \
  --warmup \
  --process-match 'omlx serve' \
  --runtime-stats-url http://127.0.0.1:8184/admin/api/stats \
  --output examples/local-runtime-frontiers/raw/tool-matrix.json
```

Normalize with `normalize_openai_matrix.py` plus an exact hardware profile and
system profile. The normalizer rejects captures containing llama-swap loading
content and automatically splits cache misses from warm hits. For tool mode it
publishes both exact-call success and argument-JSON parse success, so a faster
speculative profile cannot hide broken tool syntax behind aggregate TPS.
On macOS, each request also retains swap, memory-pressure, thermal-warning, and
power snapshots; `--process-match` adds sampled peak RSS for a literal command
substring. RSS is labeled as process RSS and must not be presented as Metal
active memory. Cold-load and post-idle captures use `--runner-state` in a
separate one-repetition run with no warmup.
For oMLX DFlash profiles, `--runtime-stats-url` captures the engine's exact
per-request acceptance summary; normalization emits it as
`local_speculative_acceptance_percent` rather than inferring acceptance from
TPS.

See [ShoeHorn applicability audit](shoehorn-audit.md) for the checked boundary
between a meaningful Ornith exact-fit experiment and the architectural work
still required before applying the same method to Qwen3.8 Flash Next.
