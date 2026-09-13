# MacBook Pro M5 Max 64 GB benchmark notes

These are provisional setup measurements, not publishable Model Skyline
offerings. The 2026-09-11 runs below were taken while first-day Spotlight,
Tanium, and metadata indexing remained active. Keep the raw figures as tuning
and integration evidence, then replace them with quiet-system repetitions
before generating offering JSON.

## Environment

- MacBook Pro Mac17,6: Apple M5 Max, 18 CPU cores, 40 GPU cores, 64 GB unified
  memory
- macOS 26.6.2 (25G83)
- Apple 140 W USB-C adapter connected through the display adapter but
  negotiating only 91 W; battery at 100%, `powermode 2` (High Power)
- llama.cpp 0.4.0, build 10809 (`5266f24da`), ggml 0.23.0, Metal
- MLX-LM 0.31.3, MLX 0.32.2
- oMLX 0.6.4 (baseline server profile: cache disabled, concurrency 1, dynamic
  balanced memory guard; Apple's 51.8 GB recommended Metal working-set cap was
  left unchanged)
- Model: Qwen3.8-27B
  - GGUF: Unsloth UD-Q4_K_M, 16,453,443,584 bytes
  - MLX: `mlx-community/Qwen3.8-27B-4bit`, peak benchmark memory 18.129 GB
  - oMLX accelerated target: `mlx-works/Qwen3.8-27B-oQ4e-mtp`
  - DFlash2 draft: `z-lab/Qwen3.8-27B-DFlash2`, quantized to 4-bit weights
    at load time

Observed competing load near the end of the run included Spotlight at 158.5%
CPU, TaniumCX at 93.2%, and `mds_stores` at 58.3%.

The installed oMLX bottle includes its normal M5/MLX runtime patches, but its
optional compiled custom-kernel bundle is unavailable. This machine currently
has Command Line Tools rather than full Xcode (`xcrun -f metal` fails), so an
oMLX HEAD/custom-kernel or private ANE-prefill comparison needs Xcode installed
first. Do not mix that future result with the current stable-bottle baseline.

## Runtime boundaries

MLX-LM and oMLX consume the same MLX safetensor family and both execute through
Apple MLX/Metal. MLX-LM is the direct Apple CLI/Python baseline: it has fewer
moving parts and is the better reference for conversion, fine-tuning, and
isolated single-model measurements. oMLX is a serving and orchestration layer
over the MLX stack. It adds OpenAI/Anthropic APIs, continuous batching,
multi-model lifecycle management, memory guards, tiered RAM/SSD prefix caches,
profiles, and integrations for MTP, SpecPrefill, DFlash2, TurboQuant KV, and
optional ANE/CPU prefill. Merely wrapping an ordinary MLX checkpoint in oMLX
does not create an acceleration; the large gains require a compatible
target/draft pair or another explicitly enabled optimization.

llama.cpp is a separate GGML/GGUF runtime and does not execute MLX checkpoints.
Keep its quantization and benchmark rows separate even when the underlying
model name is the same.

Three unrelated uses of “flash” also need separate labels in results:

- Qwen3.8-Flash-Next is the official sparse hybrid model variant.
- oMLX DFlash2 is a block-diffusion speculative drafter used here with the
  dense Qwen3.8-27B target.
- FlashAttention is a memory-efficient attention kernel.

## Context-allocation smoke tests

The dense GGUF loaded and served a small request with both 131,072- and
262,144-token physical slots. With llama.cpp's default F16 KV cache, process
RSS was approximately 25.4 GB at 128K and 34.1 GB at 256K. The extra 128K of
reserved context therefore cost about 8.6 GB on this architecture and quant.
This establishes capacity, not long-context retrieval quality or sustained
speed.

OpenCode and OMP now advertise a 262,144-token hard ceiling and a 16,384-token
output ceiling for the dense llama.cpp, MLX-LM, and oMLX paths. OMP's global
automatic compaction threshold is 180,000 tokens. This intentionally separates
the physical capacity ceiling from the earlier practical maintenance point.

## Matched microbenchmarks

Each block used 2,048 prompt tokens, 128 generated tokens, five measured
repetitions, and the runtime's warm-up. llama.cpp used six threads, all Metal
layers, batch 2,048, micro-batch 512, and automatic flash attention.

| Runtime | Block | Prefill tok/s | Decode tok/s | Decode stddev | Peak memory |
| --- | ---: | ---: | ---: | ---: | ---: |
| llama.cpp GGUF | 1 | 622.77 | 26.25 | 0.79 | — |
| llama.cpp GGUF | 2 | 548.65 | 24.59 | 0.56 | — |
| MLX-LM 4-bit | 1 | 699.43 | 31.93 | 0.10 | 18.129 GB |
| MLX-LM 4-bit | 2 | 780.40 | 31.82 | 0.09 | 18.129 GB |

MLX decode was stable across the two blocks (0.35% difference). llama.cpp's
blocks differed by 6.6%, consistent with the changing background workload.
The observed MLX decode advantage therefore spans 21.6% to 29.4%; do not reduce
that range to a single publishable point yet.

## Provisional oMLX context and accelerator sweep

These are single oMLX built-in benchmark passes with an exact-length Python
code corpus, 128 generated tokens, a quick warm-up, LM-only loading, no prefix
cache, and concurrency one. Hugging Face downloads remained active, so the
figures are diagnostic rather than publishable. Sampled footprint is the
largest process physical-footprint sample during the individual test; oMLX's
process-lifetime high-water value can carry across tests and is not used here.

| Checkpoint / profile | Prompt | Prefill tok/s | Decode tok/s | Sampled footprint |
| --- | ---: | ---: | ---: | ---: |
| stock MLX 4-bit | 1K | 867.2 | 31.8 | 16.38 GB |
| stock MLX 4-bit | 4K | 913.4 | 31.6 | 18.16 GB |
| stock MLX 4-bit | 16K | 779.0 | 29.6 | 21.02 GB |
| stock MLX 4-bit | 64K | 522.7 | 24.8 | 31.45 GB |
| stock MLX 4-bit | 128K | 398.8 | 20.5 | 45.68 GB |
| oQ4e-MTP, acceleration off | 4K | 934.3 | 30.6 | 18.88 GB |
| oQ4e-MTP, MTP + F16 KV | 4K | 925.6 | 40.8 | 19.40 GB |
| oQ4e-MTP, MTP + TurboQuant 4-bit KV | 4K | 868.4 | 35.3 | 19.30 GB |
| oQ4e-MTP, MTP + F16 KV | 64K | 574.6 | 37.6 | 33.03 GB |
| oQ4e-MTP, MTP + TurboQuant 4-bit KV | 64K | 524.7 | 18.5 | 33.13 GB |
| oQ4e-MTP, DFlash2 + TurboQuant 4-bit KV | 4K | 883.0 | 52.0 | 20.33 GB |
| oQ4e-MTP, DFlash2 + TurboQuant 4-bit KV | 64K | 539.9 | 41.3 | 34.08 GB |
| oQ4e-MTP, DFlash2 + TurboQuant 4-bit KV | 128K | 362.4 | 23.1 | 48.87 GB |

The 64K MTP result is workload-sensitive rather than a fixed multiplier. With
F16 KV, the benchmark completion accepted 92 of 103 proposed tokens (89.3%)
and emitted 3.37 tokens per speculative cycle. With TurboQuant, the adaptive
controller parked MTP after 52 generated tokens. TurboQuant reduces the
estimated steady-state KV block from 4.00 MB to 1.20 MB per 64 tokens, but its
quantize/dequantize overhead and altered draft acceptance can lose throughput;
judge it at long context and keep it a distinct approximate-cache offering.

DFlash2 was the stronger speculative path in these first passes. Its 4K and
64K decode rates were 27.5% and 9.8% above MTP with F16 KV, respectively. The
oMLX DFlash completion log reports whole-request throughput under a generation
label; the table uses the benchmark result's separated `gen_tps`, not that log
line. The 64K DFlash run reached 69.5% draft-token acceptance.

The exact 128K DFlash pass completed under Apple's unchanged 51.8 GB Metal
working-set cap. It spent the sustained run in thermal state 2, while two large
model downloads remained active, and reached 79.7% acceptance. Its 23.1 tok/s
is therefore a provisional lower-bound-like result rather than a quiet-system
comparison with the published 37.7 tok/s run.

The 128K boundary was operationally tight for MTP. F16 KV triggered a pooled
Metal reclaim and full prefill restart after 118,784 tokens. TurboQuant under
the balanced guard rejected at 108,544 tokens; the aggressive guard advanced
to 120,832 before a restart, and its built-in chunked-prefill run reached
129,024 before the 49.2 GB safety cap rejected the final chunk. A separate API
probe unintentionally tokenized above 148K and was rejected at 148,480. These
are capacity/guard observations, not evidence that 128K retrieval quality is
good. The exact DFlash success makes it the practical 128K client profile, but
does not establish usable retrieval beyond that point. OMP clamps the global
180K fixed threshold to each model's declared `contextWindow - 1`, so the DS4
profile triggers near its 131K ceiling rather than waiting for 180K. Its
overflow path can also compact and retry, although an explicit fixed threshold
does not retain OMP's usual 15% output reserve near that boundary.

## llama.cpp tuning sweeps

All decode values below are three-repetition means. Sequential sweep order and
the active indexers make sub-percent differences non-actionable.

| Setting | Values tested (decode tok/s) | Decision |
| --- | --- | --- |
| CPU threads | 1: 25.08; 3: 25.30; 6: 25.48; 9: 25.17; 12: 25.05; 18: 24.33 | Keep default 6 |
| Flash attention | on: 25.26; off: 25.47 | Keep `auto` |
| Batch size | 512/1,024/2,048 were effectively tied in forward and reverse sweeps | Keep default 2,048 |
| Micro-batch | 256: 25.74; 512: 25.73; 1,024: 25.79 | Keep default 512 |

The thread sweep shows that decode is predominantly GPU/memory-bandwidth
limited here. Extra CPU threads do not improve it and 18 threads regress.

## OpenCode integration turn

Both backends executed OpenCode's `read` tool against `pyproject.toml` and
returned `model-skyline|>=3.11`. llama.cpp was resident before timing; the MLX
timer overlapped roughly the final two seconds of a load started by an earlier
invalid-model-ID attempt. Times include OpenCode startup, its extra session/title
request, a roughly 26.5K-token agent prompt, model output, the tool round-trip,
and the follow-up turn.

| Runtime | Wall time | First input/output | Follow-up input/output |
| --- | ---: | ---: | ---: |
| llama.cpp | 81.24 s | 26,513 / 74 tokens | 1,089 / 30 tokens |
| MLX-LM | 75.92 s | 26,550 / 85 tokens | 1,221 / 30 tokens |

The raw MLX wall time was 6.6% lower on this single task, but the small startup
asymmetry prevents treating that delta as a clean runtime comparison. This is an
integration smoke benchmark, not a quality comparison or a substitute for
repeated traces.

oMLX was subsequently installed and exposed as a third OpenAI-compatible
backend. Direct alias resolution, an OMP no-tools turn, and an OpenCode build
turn all completed successfully. The stock MLX checkpoint does not contain the
MTP weights declared by its architecture, so this is currently an oMLX
serving/correctness baseline rather than an MTP benchmark. A separate oQ4e-MTP
target and DFlash2 drafter were therefore installed for the accelerated
comparison.

The explicit `qwen3.8-27b-oq4e-mtp:dflash2-tq4` profile is now also exposed to
both clients at the 262,144 hard ceiling and 16,384 output ceiling. An OMP
no-tools request returned the requested exact text, and an OpenCode run used
its `read` tool on `pyproject.toml` and returned the expected project metadata.
The already-cached 13 GB `gpt-oss:20b` Ollama model is OMP's existing fast
local tier and is now exposed to OpenCode at 131,072 context; its OpenCode
`read`-tool smoke test also passed.

## Automatic runtime switching

`llama-swap` v255 now runs as a login LaunchAgent and exposes every managed
local model on `127.0.0.1:8090`. OpenCode and OMP retain their descriptive
provider/model names but all point to this shared endpoint. One exclusive group
serializes transitions across llama.cpp, MLX-LM, oMLX, DS4, and Ollama; model
selection unloads the current heavyweight process, starts the requested
backend on a private port, waits for health, and then proxies the request. A
15-minute idle TTL frees memory when no client is using the active model.

Automatic switch smokes exercised all five managed command paths and covered
Ollama to DS4, DS4 to the oMLX DFlash2 alias, plain MLX-LM, and finally
llama.cpp. At each boundary `/running` and the process table showed only the
selected heavyweight runner. OMP returned an exact response after selecting
the managed Ollama model. OpenCode then cold-selected DS4 and returned the
requested exact text after its normal title request and 27,328-token
agent/tool-schema prompt; the complete client transition took about 82 seconds.

The oMLX stock and DFlash2 IDs are aliases for one managed oMLX server, so
switching between those checkpoints uses oMLX's own engine pool rather than
starting duplicate oMLX processes. The persistent Ollama desktop daemon remains
small; the switcher's stop command explicitly unloads its separate model-weight
process.

Direct `serve-qwen38-*` scripts remain available for controlled benchmarks
and intentionally bypass the supervisor. Unload all managed models through
`POST /api/models/unload` before using a direct launcher. Operational commands
and service details are in `~/.config/llama-swap/README.md`.

## Flash-Next experiment matrix

Two 64 GB-specific Qwen3.8-Flash-Next layouts are being retained as separate
offerings because they trade quality, memory, and runtime specialization very
differently:

- AtomicChat AD-4.27bpw Q4_K_M M64 under llama.cpp: approximately 54.5 GB
  resident plus a 38.4 GB mmap/pageable PLE shard. This is the quality-first
  but tight-memory baseline.
- `ds4-metal` mixed Q2: 41.73 GiB main/MTP GGUF plus a
  29.8 GiB SSD-backed Q4_1 PLE sidecar. It uses Qwen-specific Metal kernels and
  a substantially more aggressive routed-expert quantization.

The literal `antirez/ds4` PR #990 source was cloned at
`9803df46ac7c8da8161239c029be240adc9c67f8` and both `ds4` and `ds4-server`
built cleanly. That PR has no compatible ready-made artifact. The realistic
end-to-end path is its actively developed `ivanfioravanti/ds4-metal`
descendant, pinned here at `10830f4d6919a9820382f6934362683379f26d7b`;
its binaries built and the complete Qwen Metal kernel suite passed on this
M5 Max. The matching Q2 main model and PLE sidecar downloaded successfully.

### DS4 Q2 local results

The native target-only benchmark used the fixed `promessi_sposi.txt` corpus,
temperature-zero forced decode, 128 generated tokens, a 1,024-token prefill
chunk, the promoted compensated Metal 4 tensor path, and a demand-paged PLE.
The 8K-to-64K ladder's prefill column measures only the new interval at each
frontier; the separate 128K run prefills the entire 131,072-token prefix.

| Context | Prefill tok/s | Decode tok/s | First token | Planned memory |
| ---: | ---: | ---: | ---: | ---: |
| 8K | 465.12 | 47.99 | 32.4 ms | 42.86 GiB |
| 16K | 522.00 | 48.87 | 22.0 ms | — |
| 32K | 512.04 | 48.87 | 22.9 ms | — |
| 64K | 513.81 | 49.26 | 22.7 ms | 44.80 GiB |
| 128K | 506.53 | 49.51 | 25.1 ms | 47.00 GiB |

The exact 128K pass completed in 262 seconds while the host already had about
4.1 GiB of historical swap allocated; swap did not grow. This establishes a
working 128K capacity and throughput point on this host, not retrieval quality.
Raw CSVs are retained under `raw/` and can be selected explicitly with the new
`measure-ingest.py --format ds4-bench-csv --context-tokens ...` path.

A separate API retrieval probe placed exact codes at records near the start,
middle, and end of a 123,628-token prompt. Target-only DS4 returned all three
codes exactly and in the requested order. The completed request took 160.46
seconds and generated 171 tokens including hidden reasoning. This is evidence
that the nearly-full 128K context was useful for a simple multi-needle task; it
is not a substitute for a broader RULER-style retrieval and distractor suite.

Embedded MTP improved one warm 8K prompt from 50.04 to 55.46 decode tok/s with
62.3% draft acceptance. That microbenchmark did not transfer to OpenCode's
large first turn. With a roughly 27.4K-token coding-agent prompt, the first MTP
tool-call generation fell to 0.55 tok/s before the post-tool turn recovered to
52.99 tok/s. Bounding the PLE working set with
`DS4_QWEN4_PLE_EVICT_TOKENS=1024` improved the complete MTP OpenCode turn to
148.31 seconds; disabling MTP completed the matched tool turn in 62.91 seconds.
Both runs correctly read `pyproject.toml` and returned `model-skyline >=3.11`.
The optional `MTP_DRAFT_ROWS=151936` experiment also changed first-cycle draft
acceptance at 64K, causing the strict paired harness to reject the comparison.

The daily launcher therefore defaults to target-only decode at 131,072 context,
with bounded PLE residency; `QWEN38_DS4_MTP=1` remains available for explicit
warm-workload experiments. OpenCode and OMP both expose this DS4 offering at a
131,072-token context and 16,384-token output limit. A raw OpenAI tool-call and
tool-result round trip, an OMP exact-response smoke, and the OpenCode tool run
all passed.

Do not merge either result with dense Qwen3.8-27B measurements. Flash-Next is a
125B/6B-active hybrid sparse model with an additional large n-gram embedding
table; the M64/DS4 storage trick and sparse active compute are intrinsic parts
of those offerings.

### Atomic AD-4.27 placement correction

The complete 92.9 GB Atomic AD-4.27 file set loads at 131,072 context with Q8
KV when only 47 layers are offloaded, but a warmed correctness probe measured
just 18.25 prefill and 6.19 decode tok/s. At that rate a 123K retrieval run
would take nearly two hours. This is not the intended configuration and is
retained only as a diagnostic in `raw/atomic-qwen38-flash-ad427-partial-offload-smoke.json`.

Atomic's published 36 tok/s recipe instead uses `-ngl 99`, `--fit off`, mmap,
and `iogpu.wired_limit_mb=57344`. The local kernel setting remains unchanged
(`0`, meaning the macOS default policy), because raising that system-wide wired
allocation ceiling reduces safety headroom for the rest of a 64 GB system.
The AD-4.27 128K experiment is therefore gated on an explicit decision to make
that reversible system tuning change. The lighter AD-3.84 build is the other
route: its published 45.8 GB resident set fits below the default ceiling, at the
cost of materially lower reported quantization fidelity.

## Ornith 1.5 Q5_K_M

`ornith-ai/Ornith-1.5-35B-A3B-GGUF` is now the fast local coding/agent model.
It is a 35.5B-total, roughly 3B-active MoE; the selected official Q5_K_M file is
25,347,532,544 bytes. The managed llama.cpp profile uses full Metal offload,
Q8 KV, flash attention, one 262,144-token slot, and `--fit off`.

The matched llama-bench run used a 2,048-token prompt, 512 generated tokens,
and three repetitions:

| Prompt | Prefill tok/s | Decode tok/s | Decode stddev |
| ---: | ---: | ---: | ---: |
| 2,048 | 2,769.69 | 104.90 | 0.20 |

A separate API probe placed three exact checkpoint strings near 10%, 50%, and
90% of a 122,906-token server prompt. Ornith returned all three in the correct
order. Cumulative prefill was 532.08 tok/s, decode was 48.14 tok/s for 146
tokens, and end-to-end time was 234.14 seconds. GPU allocation after the run was
34.39 GB (29.02 GB in use); `pmset` recorded no thermal or performance warning.
The system had 7.53 GiB of historical/system-wide swap allocated, so that value
must not be attributed solely to the model.

### Ornith oMLX sweep

The text-only `mlx-works/Ornith-1.5-35B-A3B-oQ4e-mtp` target is 20.42 GB on
disk and 19.2--19.8 GB when loaded. oMLX's built-in-MTP support was evaluated
rather than presumed useful. The benchmark corpus tokenized slightly below or
above each requested boundary, so the actual prompt-token column is canonical.

| Profile | Actual prompt | Prefill tok/s | Decode tok/s |
| --- | ---: | ---: | ---: |
| plain oQ4e, F16 KV | 4,369 | 2,945.6 | 131.5 |
| MTP, F16 KV | 4,365 | 2,978.0 | 122.0 |
| MTP, TurboQuant 4-bit KV | 4,367 | 3,194.9 | 111.0 |
| plain oQ4e, F16 KV | 61,987 | 2,416.6 | 101.3 |
| MTP, F16 KV | 61,988 | 2,230.2 | 89.9 |
| MTP, TurboQuant 4-bit KV | 61,989 | 1,679.9 | 55.3 |
| plain oQ4e, F16 KV | 122,420 | 1,376.8 | 67.7 |

Native MTP lost 7.2% at 4K and 11.3% at 62K despite 52.3% and 67.1% draft
acceptance. Adding TurboQuant lost 15.6% and 45.4% respectively versus plain
oQ4e. The daily alias is therefore
`ornith-1.5-35b-a3b-oq4e-mtp:baseline-f16kv`; the losing profiles remain
available only for future regression checks.

The oQ4e profile also returned all three checkpoint values from the identical
122,906-token retrieval prompt. Its 95.81-second wall time was 59.1% lower than
the Q5 llama.cpp run. This is not a pure runtime comparison because Q5_K_M and
oQ4e are different quantizations, so both stay as distinct Model Skyline
offerings.

Both integration layers passed real Q5 tool turns. OMP and OpenCode selected
`ornith-1.5-35b-a3b-q5km` through llama-swap, invoked `read` on
`pyproject.toml`, and returned `model-skyline | >=3.11`; both clients repeated
that success through the recommended oMLX alias. A direct OpenAI request also
produced a correctly structured function call. The llama-swap exclusive group
ensures neither runner can coexist with the other heavyweight backends.

The raw short-context benchmarks, profile sweep, both 123K retrieval results,
and generated speed-only Model Skyline offerings are retained beside this
document. A quality axis is intentionally omitted until an independent
compatible score exists; the model publisher's strong Terminal-Bench and
SWE-bench results are useful candidate evidence, not a substitute for a matched
local quality eval.

## Clean rerun gate

Rerun the matched five-repetition blocks only after Spotlight, Tanium, cloud
synchronization, and other model processes are quiescent. Alternate runtime
order across at least three blocks, record cold-load latency separately, and
then use `measure-ingest.py` to create the runtime-specific offering JSON files.
