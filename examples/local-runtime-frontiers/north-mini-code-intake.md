# Cohere North Mini Code local intake

This intake pins an agentic-coding challenger for the 64 GB Apple Silicon
population. It is an experiment plan, not a measured ModelSkyline offering or
a frontier claim. Publisher and community benchmark results are candidate-
screening annotations only and must not become quality observations for either
quantized artifact.

## Candidate hypothesis

North Mini Code 1.0 is a 30-billion-parameter sparse mixture-of-experts model
with about 3 billion active parameters. Cohere trained it specifically for code
generation, terminal work, and agentic software engineering, including
multi-harness supervised and reinforcement-learning data. Its 128 experts,
eight active experts per token, and 3:1 sliding/global-attention pattern make it
a plausible quality/latency challenger rather than merely another small-model
speed control.

Use two exact artifacts:

| Profile | Pinned artifact |
| --- | --- |
| Primary MLX | `mlx-community/North-Mini-Code-1.0-4bit` at `dfbe084dfa26e241345af99ca32848f38fd865f9`; 18,514,771,285 repository bytes; four 4-bit affine safetensor shards |
| Portable GGUF control | `bartowski/North-Mini-Code-1.0-GGUF` at `6ff6563002170723a6f7a672bf4c99775be6c0dd`; `North-Mini-Code-1.0-Q4_K_M.gguf`; 18,744,024,640 bytes; SHA-256 `38a7f05a0bf703be5a9c474d205229d68ffa6c15f8e793923cd2d46c9b77a723` |

Both are public, ungated Apache-2.0 conversions. The MLX conversion contains
49 layers, hidden size 2,048, 128 experts with eight selected per token, 4,096-
token sliding windows, and full attention every fourth layer. Its config
declares 500,000 positions, while Cohere's model card promises a 256K total
context and 64K maximum output. Treat 256K as the lower unvalidated publisher
claim and every locally passing position as the only decision evidence; do not
advertise 500K from conversion metadata.

The files are roughly 18.5–18.7 GB, leaving apparent room for runtime state and
KV/cache allocations on a 64 GB host. That is only a fit hypothesis. Peak
physical footprint, memory pressure, swap growth, and validated retrieval must
be measured at each context position.

## Why it is worth measuring

Cohere reports 67.6 on SWE-bench Verified, 40.2 on SWE-bench Pro, and 36 on
Terminal-Bench 2.0 for the source checkpoint. Its model card says each benchmark
was run with three seeds, but competitor rows combine public and internal
results. These numbers establish agentic-coding intent and test priority; they
do not transfer across benchmark versions, harnesses, samplers, or
quantization.

The July 2026 `mlx-coding-bench` matrix is a useful independent Apple-Silicon
screen but does not contain North Mini Code. Its absence is a measurement gap,
not evidence for or against the model. CanIRun's approximate capacity listing
similarly says that a 4-bit copy fits, but does not replace process-memory,
context, tool, or Harbor measurements.

Runtime support is unusually concrete:

- llama.cpp merged `cohere2_moe` architecture support on June 13, 2026. The
  implementation supports conversion, quantization, chat parsing, built-in
  tools, and optional MTP, although the pull-request history records early chat
  template/parser failures. Exact structured-call tests remain mandatory.
- The installed oMLX 0.6.4 build recognizes `cohere2_moe`, loads it through its
  MLX VLM path, and includes a Cohere Melody output parser. Code presence proves
  runtime capability, not that this artifact loads or parses correctly.
- Cohere publishes an OpenCode configuration with interleaved reasoning. That
  makes a real OpenCode/OMP smoke especially relevant, but parser behavior must
  first pass the frozen direct API tool probe.

North and Nemotron answer different hypotheses. North is the higher-priority
agent-quality challenger because its post-training and public evaluation target
the exact use case. Nemotron remains the stronger architecture-efficiency and
speculative-decoding experiment. Neither displaces a measured resident before
local gates pass.

## Exact profiles and order

Test one resident profile at a time under the shared exclusive-runner lock and
batch each profile rather than interleaving model loads.

1. **Plain oMLX 4-bit:** exact MLX revision, prefix cache enabled, no speculative
   decoder. This is the primary Apple-native agent profile.
2. **Plain llama.cpp Q4_K_M:** exact GGUF bytes, current pinned build, no MTP.
   This is the portable runtime and cross-Mac control.
3. **Optimized profile only after both controls pass:** MTP, alternate KV
   precision, or other optimization becomes a distinct offering with direct
   activation evidence. The experimental MTP discussion in the original
   llama.cpp support pull request is not enough to promote it by default.

Pin prompt bytes, tools, sampler, reasoning policy, output cap, context
allocation, cache state, concurrency, and power/run conditions. The official
sampling recommendation is temperature 1.0 and top-p 0.95; use that for the
quality route, and retain any deterministic operational probe as a separately
identified sampler position.

## Evaluation sequence

1. **Admission and parser smoke**
   - Capture time to ready, process/system physical memory, memory pressure, and
     swap before load, at ready, and through all requests.
   - Require an ordinary completion plus three exact structured tool calls.
   - Verify separation of reasoning, final content, tool name, and tool
     arguments. Retain parser/version identity and redacted response digests.
2. **Short throughput envelope**
   - Run the existing pp2048 position with 64-, 256-, and 1,024-token output
     caps where supported.
   - Retain TTFT, input/prefill throughput, decode throughput, end-to-end time,
     finish reason, usage, and repetitions.
3. **Prefix-cache operation**
   - Repeat a byte-stable approximately 20K-token prefix, then preserve the
     prefix while changing only the suffix.
   - Compare cold/miss and warm/hit TTFT, uncached input, reused tokens, exact
     response validity, and cache telemetry. Cache-disabled is a separate
     profile, not a mixed sample.
4. **Tool-agent positions**
   - Run the frozen 30-tool, 2K-prefix workload at 256- and 1,024-token output
     caps for uncached/miss and warm/hit cohorts.
   - Score exact tool name and arguments, parse success, turn latency, and any
     reasoning-parser failure.
5. **Retrieval and capacity ladder**
   - Test exact retrieval at 2K, 32K, 65K, and 126K.
   - If all pass, test near the official 256K total-context claim with an output
     reserve. Allocation alone remains capacity smoke.
6. **Real-agent quality**
   - Run the frozen Harbor smoke with the same Terminus-2 harness used for the
     current local candidates.
   - Promote a valid passing profile to the five-task pilot with complete
     verifier, wall-time, request-usage, and compact runner-memory evidence.
   - Then run one short OpenCode or OMP task to validate the intended
     interleaved-reasoning integration without substituting anecdotal success
     for the Harbor score.
7. **Cross-Mac control**
   - Copy the exact GGUF bytes to the M1 Studio only after the M5 quality cohort
     is idle.
   - Reuse the identical llama.cpp build hash and pp2048/tg512 command position;
     report M5/M1 prompt and decode ratios for this architecture separately.

## Promotion gates

A profile becomes eligible for the corresponding packaged frontier only when:

- all three direct structured tool calls are exactly correct;
- reasoning and tool envelopes are parsed without leaked control tokens;
- retrieval passes at every claimed and retained context position;
- no unexpected swap growth or memory-pressure failure occurs;
- every optional optimization has direct runtime activation evidence;
- the Harbor smoke produces a valid passing verifier result before the
  five-task pilot is scheduled;
- evidence binds the exact artifact, runtime, parser, sampler, cache state,
  context allocation, and hardware; and
- publisher/community scores remain annotations rather than imported quality.

The MLX and GGUF results remain separate offerings even if their source model
is the same. A broken parser cannot enter the tool frontier, a 256K allocation
cannot enter the retrieval frontier, and a successful OpenCode anecdote cannot
replace the declared verifier-scored workload.

## Pinned sources

Sources were retrieved on 2026-09-14.

| Source | Pin | Use |
| --- | --- | --- |
| [Cohere source model card](https://huggingface.co/CohereLabs/North-Mini-Code-1.0) | model card observed 2026-09-14 | official architecture, context, sampling, OpenCode, and evaluation methodology |
| [Cohere/Hugging Face release article](https://huggingface.co/blog/CohereLabs/introducing-north-mini-code) | published 2026-06-09 | official training and agentic-coding rationale |
| [MLX 4-bit artifact](https://huggingface.co/mlx-community/North-Mini-Code-1.0-4bit/tree/dfbe084dfa26e241345af99ca32848f38fd865f9) | `dfbe084dfa26e241345af99ca32848f38fd865f9` | exact MLX files, sizes, and configuration |
| [GGUF Q4_K_M artifact](https://huggingface.co/bartowski/North-Mini-Code-1.0-GGUF/blob/6ff6563002170723a6f7a672bf4c99775be6c0dd/North-Mini-Code-1.0-Q4_K_M.gguf) | `6ff6563002170723a6f7a672bf4c99775be6c0dd` | exact portable artifact bytes and digest |
| [llama.cpp `cohere2_moe` support](https://github.com/ggml-org/llama.cpp/pull/24260) | merged 2026-06-13 | architecture, conversion, parser/tool, and experimental-MTP implementation history |
| [MLX coding benchmark](https://github.com/weklund/mlx-coding-bench) | repository observed 2026-09-14 | Apple-Silicon comparison methodology and explicit candidate-coverage gap |
