# Recent community evidence for M5 Max 64 GB

Checked 2026-09-11. These are external comparison points, not measurements
produced by this repository. Preserve the linked runtime configuration and
quantization with every comparison; nominal model names alone are not enough.

## Exact M5 Max, 40 GPU cores, 64 GB

| Published | Model and runtime | Context ladder / result | Caveat |
| --- | --- | --- | --- |
| 2026-08-28 | Qwen3.8-27B oQ4e, oMLX 0.6.2, MTP + 4-bit TurboQuant KV | 64K: 524.5 prefill, 26.0 decode tok/s; 28.0 GB model peak | Thermal state reached heavy. [Run](https://omlx.ai/benchmarks/performance/d608stun) |
| 2026-08-27 | Qwen3.8-27B MLX 4-bit, oMLX, DFlash2 + 4-bit KV | 32K: 59.6 decode / 23.3 GB; 64K: 52.3 / 31.6 GB; 128K: 37.7 / 47.8 GB | Draft acceptance and quality remain prompt-dependent. [Run](https://omlx.ai/benchmarks/performance/b8xr77gp) |
| 2026-08-23 | Qwen3.8-27B oQ4e, oMLX, MTP + SpecPrefill | 4K: 48.3; 16K: 59.7; 64K: 55.4; 128K: 40.0 decode tok/s, 29.4 GB peak | Multiple accelerators; isolate them locally. [Run](https://omlx.ai/benchmarks/performance/ggxkzz2a) |
| 2026-08-20 | Qwen3.8-27B AWQ 5 bpw, SpecPrefill + TurboQuant KV + MTP | 32K: 51.9; 64K: 56.7; 128K: 46.9; 195K: 29.0 decode tok/s, 32.1 GB peak | Leaderboard quant label is incomplete; validate quality independently. [Run](https://omlx.ai/benchmarks/performance/t0fo080b) |
| 2026-08-23 | Qwen3.6-35B-A3B MLX 4-bit, no MTP | 16K: 110.7; 64K: 86.2; 128K: 69.2; 195K: 52.7 decode tok/s | Quality is below dense 3.8, but the 3B-active MoE remains a distinct speed frontier. [Run](https://omlx.ai/benchmarks/performance/be1ydkxp) |
| 2026-08-10 | gpt-oss-20b MXFP4-Q8, oMLX | 4K: 108.2 decode / 11.8 GB; 16K: 88.5 / 12.1 GB | Strong small/fast tier; lower GPU utilization than the Qwen runs. [Run](https://omlx.ai/benchmarks/performance/ib1u26jv) |
| 2026-07-10 | Gemma-4-26B-A4B-it MLX 4-bit, oMLX | 4K: 113.7; 64K: 63.0; 128K: 42.7; 195K: 30.4 decode tok/s, 27.6 GB peak | Exact hardware and a compelling fast multimodal/long-context tier; quality and tool behavior still need local validation. [Run](https://omlx.ai/benchmarks/performance/7cmzuj2r) |

The dense Qwen3.8 results establish that a 256K configured ceiling and a
128K practical working region are plausible on this machine. They do not show
that retrieval quality remains flat to the advertised limit. OMP therefore
compacts at 180K while the local backends retain a 262,144-token hard ceiling.

## Coding-model candidates after the first local sweep

Ornith 1.5 is the most compelling next tier that cleanly fits. Its official
35B-A3B release reports 67.8 on Terminal-Bench 2.1 with Terminus-2, 79.0 on
SWE-bench Verified, and 59.6 on SWE-bench Pro. Those are publisher results with
documented harnesses, not a directly comparable Artificial Analysis score.
The official Q5_K_M GGUF is 25.35 GB, and the local 123K retrieval pass confirms
that it retains substantial memory headroom on this host. [Model
card](https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF)

Qwen3-Coder-Next remains the strongest large follow-up specifically for coding
specialization: 80B total / 3B active, 262K native context, and an official
Q4_K_M GGUF around 48.4 GB. That should fit at 128K with compressed KV, but it
is close enough to the Metal working-set boundary that it is a capacity-risk
experiment rather than an automatic replacement for Ornith. [Model
card](https://huggingface.co/Qwen/Qwen3-Coder-Next-GGUF)

Agents-A1 is a lower-priority complementary agent/research candidate. Published
oMLX runs on this exact M5 Max class show a useful long-context speed ladder,
but its available evidence is less coding-specific and less current than
Ornith's. Download it only if tool-heavy research behavior proves meaningfully
different in a matched local eval; raw CanIRun ranking alone is insufficient.

CanIRun remains useful for discovering names and approximate weight sizes, but
its estimator adds a fixed overhead and scales MoE speed from active/total
parameter ratios without modeling KV growth, context position, runtime-specific
kernels, Metal working-set limits, or quantization quality. Treat its order as
a candidate queue, never as the local Pareto frontier.

## Qwen3.8-Flash-Next on 64 GB

| Published | Hardware / runtime | Layout and result | Caveat |
| --- | --- | --- | --- |
| 2026-08-27 | M5 Max 40-core/64 GB, patched llama.cpp | Atomic AD-3.84 M64: 45.8 GB resident + 39.1 GB SSD PLE; 517.9 prefill / 36.0 decode tok/s | Publisher self-report and short context; top-1 agreement only 82.68%. [Model card](https://huggingface.co/AtomicChat/Qwen3.8-Flash-Next-GGUF) |
| 2026-09-09 | M5 Pro/64 GB, DS4 Qwen branch | Mixed Q2 + PLE: 42.86 GiB planned; 31-32 tok/s plain and 41-45 with MTP at 8K | Different/lower-bandwidth chip; later PLE prefault fix matters. [Report](https://huggingface.co/ivanfioravanti/Qwen3.8-Flash-Next-DS4-IQ2/discussions/3) |
| 2026-09-07 | M4 Max 40-core/64 GB, DS4 Qwen branch | 32K: 301.5 prefill / 38.7 decode; 64K: 308.6 / 38.9; MTP 32K: 45.1 decode | Required a 57,344 MB wired limit in that report. [Commands and telemetry](https://huggingface.co/ivanfioravanti/Qwen3.8-Flash-Next-DS4-IQ2/discussions/2) |
| 2026-09-11 | M1 Max 32-core/64 GB, current `ds4-metal` Q2 | 64K: 242 prefill / 20.9 decode / 27.6 MTP; 128K: 200–217 / 20.2 / 26.8 MTP; 256K: 19.7 plain decode | Maintainer measurement in current branch docs; 128K became unstable as swap grew and 256K is explicitly not a daily setting. [Qwen guide](https://github.com/ivanfioravanti/ds4-metal/blob/qwen3.8-flash-next/docs/QWEN38_FLASH_NEXT.md) |
| 2026-08 | M2 Max/64 GB, Atomic AD-3.84 | 8K: 22.9; 32K: 20.2; 128K: 20.2 decode tok/s; 256K OOM | Independent report on older hardware. [Discussion](https://huggingface.co/AtomicChat/Qwen3.8-Flash-Next-GGUF/discussions/5) |

Flash-Next is approximately 125B language-model parameters with 6B active per
token, plus a very large n-gram embedding table and an MTP component. `Flash`
is a product/model variant name, not a synonym for MoE, although this model is
sparse. Its speed comes from the small active parameter count and hybrid
Gated-DeltaNet/sparse-attention architecture. The 64 GB implementations then
add a separate systems trick: only sparsely accessed PLE rows remain on SSD.

The two local candidates test different failure modes:

- Atomic AD-4.27 M64 prioritizes quantization fidelity (reported 89.49% top-1
  agreement and 1.026 perplexity ratio), but its reported 54.5 GB resident
  footprint leaves little room for context and macOS.
- DS4 mixed Q2 leaves much more memory headroom and uses architecture-specific
  Metal kernels, but its routed experts are quantized far more aggressively.
  Test coherent output, tools, code quality, and long-context retrieval before
  accepting a throughput result.

The DS4 Qwen implementation is tracked in [antirez/ds4 PR
#990](https://github.com/antirez/ds4/pull/990). The locally built descendant is
[ivanfioravanti/ds4-metal, `qwen3.8-flash-next`](https://github.com/ivanfioravanti/ds4-metal/tree/qwen3.8-flash-next),
which supplies resumable Q2 artifacts and a server while retaining the
Qwen-specific Metal kernels.

## Runtime implications

- Plain MLX-LM is the control: few moving parts, direct MLX weights, and useful
  reference behavior.
- oMLX uses the same MLX model family but adds a production server, continuous
  batching, RAM/SSD prefix caching, model profiles, memory guards, TurboQuant
  KV, DFlash2, SpecPrefill, MTP, and optional ANE/CPU prefill splitting. Each
  accelerator must be reported as part of the offering.
- llama.cpp remains the portable GGUF baseline and exposes low-level batch,
  micro-batch, thread, flash-attention, KV, and mmap controls.
- DS4 is intentionally architecture-specific. Its value is not generic model
  support but a specialized Qwen/DeepSeek execution and storage design.

oMLX 0.6.4's release notes document Qwen3.8 fixes and DFlash2 support:
[release](https://github.com/jundot/omlx/releases/tag/v0.6.4). Its optimized
results require separate target/draft checkpoints; wrapping the stock MLX
checkpoint in the oMLX server does not create MTP weights or DFlash behavior.
