# Poolside Laguna XS 2.1 local intake

This intake pins a local-first coding and terminal-agent challenger for 64 GB
Apple Silicon. It is an experiment plan, not a measured ModelSkyline offering
or a frontier claim. Publisher benchmark results define relevance and test
priority; they are not quality observations for a quantized local artifact.

## Candidate hypothesis

Laguna XS 2.1 is a 33B-total, 3B-active mixture-of-experts model designed for
agentic coding and long-horizon local work. It has 40 layers, 256 routed experts
plus one shared expert, a 3:1 sliding/global-attention layout, interleaved
reasoning, and a declared 262,144-token context. Poolside specifically describes
36 GB Macs as a target and publishes both MLX and GGUF conversions.

Use these first-party controls:

| Profile | Pinned artifact |
| --- | --- |
| Primary MLX | `poolside/Laguna-XS-2.1-NVFP4-mlx` at `841778bda563a36104dd521e37d99218e46f4f25`; 21,568,905,520 repository bytes |
| Portable GGUF | `poolside/Laguna-XS-2.1-GGUF` at `1a37c0a5fb8c7a18e6106decb6be6327d1b63fa6`; `Laguna-XS-2.1-Q4_K_M.gguf`; 20,274,300,032 bytes; SHA-256 `1ac7079101fca5a6df8c5a7523a3c30ea7d1c0e4b1258090e7d6d4039287f6cb` |
| Optional DFlash draft | `poolside/Laguna-XS-2.1-DFlash` at `5c36361aab23c8ed3afbd079c10c426b677bc607`; 924,135,848-byte safetensor; SHA-256 `0b51e20d76200a80e636414f45fb51a5c0e13b0852d977ba2db788214c68f6b8` |

The MLX and GGUF results remain different offerings. The 20–22 GB repository
sizes suggest ample nominal headroom, but peak physical footprint, cache/KV
growth, memory pressure, and swap must be measured at each retained position.

## Why it is worth measuring

Poolside reports that the model was trained for repository-level code, terminal
work, tool-calling, and long agent trajectories. Its release table reports
70.9 SWE-bench Verified, 47.6 SWE-bench Pro, and 37.5 Terminal-Bench 2.0, using
Harbor with thinking enabled and a 256K context. Comparison rows mix separately
published scores, task images were patched, and this local quantized setup is
different, so those figures are screening evidence only.

Runtime support is concrete but still requires local admission:

- llama.cpp Laguna support merged in PR 25165 on 2026-07-22, and the Q4_K_M is
  first-party. The installed build must postdate that merge.
- Poolside documents a Metal f16 overflow that can yield empty output in a MoE
  down-projection. The proposed fix PR was closed without merge, so ordinary,
  long-output, and tool probes must reject any empty/NaN behavior rather than
  assuming current Metal safety.
- The model depends on preserved interleaved reasoning. Dropping prior thinking
  blocks between tool calls changes behavior and is an integration failure.
- The optional 5-layer DFlash draft proposes up to 15 tokens, but published
  acceptance is not evidence of activation or agent-task speedup on either Mac.

## Exact profiles and evaluation order

1. **Plain MLX NVFP4:** exact first-party artifact, preserved reasoning, prefix
   cache enabled, no speculative decoder. This is the local-primary control.
2. **Plain llama.cpp Q4_K_M:** exact official GGUF and a pinned post-merge
   llama.cpp build. It is the portable and cross-Mac control.
3. **DFlash:** attach the exact draft only after the corresponding plain target
   passes. Record activation and acceptance separately for prose, code, tool
   JSON, and repeated text; retain the plain target as its own offering.

Use the release sampler for quality: temperature 1.0, top-k 20, top-p 1.0,
thinking enabled. Deterministic operational probes remain separate positions.
Never combine cache-disabled, cache-miss, warm-hit, or DFlash samples.

## Evaluation sequence and promotion gates

Run admission, three exact structured calls, short throughput, warm-cache and
uncached-tool positions, the 2K/32K/65K/126K retrieval ladder, and the frozen
Harbor smoke/pilot in that order. Stop a profile on parser, empty-output,
retrieval, or memory-safety failure. After an admitted M5 GGUF cohort, run the
identical bytes/build/pp2048/tg512 command on the M1 Studio.

A profile enters a frontier only when tool arguments are exact, reasoning and
tool envelopes survive multi-turn reconstruction, every claimed retrieval
position passes, no unexpected swap grows, and optional DFlash activity has
direct telemetry. External benchmark scores and the publisher's 36 GB fit
statement remain annotations.

## Pinned sources

Sources were retrieved on 2026-09-14.

| Source | Pin | Use |
| --- | --- | --- |
| [Laguna XS 2.1 source model](https://huggingface.co/poolside/Laguna-XS-2.1/tree/c5f36269bbdbd3f27fddc9a9f9dbae0cf2cf57db) | `c5f36269bbdbd3f27fddc9a9f9dbae0cf2cf57db` | official architecture, intended workload, context, sampler, benchmarks, parser, and local-runtime notes |
| [First-party NVFP4 MLX](https://huggingface.co/poolside/Laguna-XS-2.1-NVFP4-mlx/tree/841778bda563a36104dd521e37d99218e46f4f25) | `841778bda563a36104dd521e37d99218e46f4f25` | exact Apple-native artifact files and sizes |
| [First-party Q4_K_M GGUF](https://huggingface.co/poolside/Laguna-XS-2.1-GGUF/tree/1a37c0a5fb8c7a18e6106decb6be6327d1b63fa6) | `1a37c0a5fb8c7a18e6106decb6be6327d1b63fa6` | exact portable artifact bytes and digest |
| [First-party DFlash draft](https://huggingface.co/poolside/Laguna-XS-2.1-DFlash/tree/5c36361aab23c8ed3afbd079c10c426b677bc607) | `5c36361aab23c8ed3afbd079c10c426b677bc607` | exact speculative draft bytes and configuration |
| [Laguna M.1/XS.2 technical report](https://poolside.ai/assets/laguna/laguna-m1-xs2-technical-report.pdf) | published 2026-05-25 | training and long-horizon coding/tool trajectory rationale |
| [llama.cpp Laguna support](https://github.com/ggml-org/llama.cpp/pull/25165) | merged head `54f214a09b8c4e709357ae661a77925edb154f13` | architecture and Metal support baseline |
| [Metal overflow report/fix attempt](https://github.com/ggml-org/llama.cpp/pull/25389) | closed 2026-07-08, unmerged | explicit empty-output risk to test |
