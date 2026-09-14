# InternScience Agents-A1 local intake

This intake pins a complementary long-horizon search, research, and tool-use
candidate for 64 GB Apple Silicon. It is an experiment plan, not a measured
ModelSkyline offering or a frontier claim. Publisher and conversion-author
numbers are screening annotations only and never become local quality values.

## Candidate hypothesis

Agents-A1 is a 35B-total, roughly 3B-active Qwen3.5-MoE vision-language model.
Its publisher explicitly targets long-horizon search, engineering, scientific
research, instruction following, and heterogeneous tool use. That differs from
the current Terminal-Bench pilot: the model should compete there as an
engineering control, but its distinctive hypothesis belongs on the separate
`long-horizon-research-value` recipe.

Use these pinned controls:

| Profile | Pinned artifact |
| --- | --- |
| Primary MLX text/tool control | `mlx-community/Agents-A1-4bit` at `dafc52a5e3d7b2a9fba6d0bd1370bcbffb127624`; 20,422,448,883 repository bytes; uniform 4-bit affine, group size 64 |
| Portable GGUF text control | `InternScience/Agents-A1-Q4_K_M-GGUF` at `b1109cc937623d0679fdde708cf4885edeb0ae82`; `Agents-A1-Q4_K_M.gguf`; 21,166,757,632 bytes; SHA-256 `31aefa25b7e1edbde436e643e2b5e3f6e57820a4811d97b131130e48ff0772c2` |
| Optional vision projection | same official GGUF revision; `Agents-A1-mmproj.gguf`; 899,282,976 bytes; SHA-256 `781c47706c2a2002401358f2acbeeccf545ee4cf9bd7314d4470f4c17f083b1c` |

The first cohort is text-and-tools only, so the GGUF vision projection is not
loaded. Vision is a later, separately identified offering and workload. Both
primary controls declare a 262,144-token context, but only locally passing
retrieval positions count. The roughly 20–21 GB weight footprint makes a 128K
position plausible on a 64 GB host; it does not prove fit, cache headroom, or
no-swap operation.

The MLX conversion author reports that a data-driven oQ attempt produced an
expert layout its own loader could not read, while the uniform 4-bit conversion
loads through MLX-VLM and oMLX. This makes the uniform artifact the baseline.
The newer OptiQ and expert-pruned REAP variants are quantization experiments,
not substitutes for a stable control; admit them only through the paired
quantization-screening protocol.

The installed-runtime audit now clears the static MLX architecture gate. oMLX
0.6.4 embeds MLX-LM 0.31.3, whose `qwen3_5_moe` module imports successfully,
and contains the matching VLM/text dispatch and MoE sanitization paths. The
pinned artifact's exact config declares the same outer `qwen3_5_moe` type and
`qwen3_5_moe_text` language component. This is stronger than generic
Transformers recognition, but it is still not a weight-load or output-quality
result. See
[`runtime-support-audit-2026-09-14.md`](runtime-support-audit-2026-09-14.md).

## Why it is worth measuring

The publisher reports results across BrowseComp, GAIA, HLE-with-tools,
FrontierScience, SciCode, instruction-following, and general agent benchmarks,
and publishes an evaluation framework for tool use and multi-step reasoning.
Those results establish the intended workload and candidate priority, but do
not transfer to these quantized artifacts, our tools, or our hardware.

This exposes a useful frontier-design correction. A terminal/coding portfolio
cannot validate research claims. The packaged `long-horizon-research-value`
frontier therefore compares measured outcome quality with p95 full-task wall
time in one pinned research/search cohort. It gates exact tool calls, evidence
grounding, validated 128K context, and swap growth. Frozen-corpus and live-web
cohorts must remain distinct because search results and source availability can
change independently of the model.

## Exact profiles and evaluation order

Run one profile at a time through the exclusive local-memory group.

1. **Uniform MLX 4-bit, text-only:** MLX-VLM/oMLX, prefix cache enabled, no
   speculative decoder. Verify that the route actually skips vision state when
   no image is supplied.
2. **Official llama.cpp Q4_K_M, text-only:** same tools and sampler, no mmproj.
   This is the portable cross-Mac control.
3. **Vision control:** add the exact mmproj only after text/tool admission and
   use a separately versioned multimodal task set.
4. **Experimental quantizations:** OptiQ or REAP only after a paired coreset
   measures degradation against the admitted uniform MLX anchor.

Use the publisher sampler for quality experiments: temperature 0.85, top-p
0.95, top-k 20, min-p 0, and presence penalty 1.1. A deterministic operational
probe is a separate position. Preserve reasoning blocks across tool turns and
record the exact reasoning/tool parser.

## Evaluation sequence and promotion gates

1. Load and ordinary-completion smoke, with time-to-ready and physical-memory,
   memory-pressure, and swap capture.
2. Three exact structured tool calls, including malformed/omitted optional
   arguments and a multi-turn call with preserved reasoning.
3. Existing pp2048 throughput, warm-cache, uncached-tool, and 2K/32K/65K/126K
   retrieval positions.
4. Frozen five-task Terminal-Bench smoke/pilot as an engineering comparison.
5. A minimum 20-task research/search portfolio spanning multi-source search,
   scientific/tool reasoning, and multi-constraint synthesis. Freeze source
   documents or search results, tool schemas, expected evidence, and scoring;
   verify cited evidence rather than relying on a style judge alone.
6. Copy the exact GGUF to the M1 Studio for the same pp2048/tg512 command only
   after the current serial quality queue is idle.

A profile is eligible only when exact tool correctness and evidence grounding
are 100%, the retained retrieval position passes, no unexpected swap grows,
every request has complete usage/cache accounting, and quality evidence binds
the exact artifact, runtime, parser, sampler, cohort, and hardware. Publisher
scores remain annotations.

## Pinned sources

Sources were retrieved on 2026-09-14.

| Source | Pin | Use |
| --- | --- | --- |
| [Agents-A1 source model](https://huggingface.co/InternScience/Agents-A1/tree/addff08f1653ee72765c5cf458fe84556bb34f8e) | `addff08f1653ee72765c5cf458fe84556bb34f8e` | official architecture, intended domains, context, sampler, evaluation framework, and tool-parser guidance |
| [Official Q4_K_M GGUF](https://huggingface.co/InternScience/Agents-A1-Q4_K_M-GGUF/tree/b1109cc937623d0679fdde708cf4885edeb0ae82) | `b1109cc937623d0679fdde708cf4885edeb0ae82` | exact portable model/mmproj bytes and digests |
| [Uniform MLX 4-bit](https://huggingface.co/mlx-community/Agents-A1-4bit/tree/dafc52a5e3d7b2a9fba6d0bd1370bcbffb127624) | `dafc52a5e3d7b2a9fba6d0bd1370bcbffb127624` | exact MLX files, quantization, loader notes, and contextual performance screen |
| [Agents-A1 evaluation code](https://github.com/InternScience/Agents-A1/tree/ff57c5f2d2819c918a20281a3da76990cd82b554/evaluation) | `ff57c5f2d2819c918a20281a3da76990cd82b554` | publisher task/harness reference; create a separately pinned local cohort before reuse |
