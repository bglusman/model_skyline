# Local candidate runtime-support audit — 2026-09-14

This audit separates static/runtime capability from an exact artifact load.
Importing an architecture module proves that the installed runtime has a code
path for it; it does not prove that a particular conversion loads, generates
valid text, parses tools, survives long context, or belongs on a frontier.

## Installed stack

| Component | Exact installed identity |
| --- | --- |
| oMLX | 0.6.4 |
| MLX-LM inside oMLX | 0.31.3 |
| MLX | 0.32.0 |
| Transformers | 5.12.1 |
| DFlash-MLX | `0.1.10+omlx.7` |
| llama.cpp | 0.4.0, build 10809, commit `5266f24da75dc449bd56cbed7addb9c8e4a6a73e` |

The audit used the Python environment embedded in the Homebrew oMLX formula,
not an unrelated system Python or the separately installed `mlx-lm` tool.

## Laguna XS 2.1

The pinned first-party NVFP4 artifact declares `model_type: laguna`,
`LagunaForCausalLM`, NVFP4 4-bit/group-16 quantization, and 262,144 positions.
Against that exact configuration shape, installed oMLX provides all of the
following:

- a vendored `mlx_lm.models.laguna` implementation from MLX-LM PR 1223 at head
  `0857ee1cf1f4ba7c43e73b836309d9c884529ca8`;
- a Laguna-specific tool parser;
- compressed-quantization normalization for the first-party NVFP4 metadata;
- tokenizer/template handling for Laguna's XML-like tool calls and preserved
  reasoning; and
- a Laguna DFlash target/draft adapter.

The patch registration test changed from not registered to registered and both
the model and parser modules then imported successfully. This clears the
**static MLX runtime gate**. The exact 21.57 GB checkpoint has not yet been
downloaded or loaded, so load, ordinary generation, empty/NaN safety, parser
correctness, cache behavior, and context remain unmeasured.

Installed llama.cpp contains the merged Laguna support commit
`1f66c3ce1c26c95db3fadb734086c7d9fba23bb9`: GitHub's ancestry comparison
places build commit `5266f24da` 722 commits ahead and zero behind. This clears
the version gate for the first-party Q4_K_M control, but not its exact load or
Metal correctness gates.

## InternScience Agents-A1

The pinned MLX artifact declares outer `model_type: qwen3_5_moe`, architecture
`Qwen3_5MoeForConditionalGeneration`, text model type `qwen3_5_moe_text`, and
262,144 positions. Installed oMLX's embedded MLX-LM successfully imports
`mlx_lm.models.qwen3_5_moe`; oMLX also contains the VLM/text dispatch and MoE
sanitization paths used by this family. This clears the **static architecture
gate**, not an exact model-load gate.

Because this is a composite vision-language checkpoint, the first admission
must verify that text-only requests take the intended language path without
loading or retaining unnecessary vision state. Tool parsing and multi-turn
reasoning preservation remain explicit gates. The official GGUF and optional
mmproj remain separate text-only and multimodal offerings.

## Qwen3.8 Flash Next control

Installed llama.cpp also contains the merged Qwen3.8 Flash Next support commit
`6c84c7d5d8833c6e0df69628f75a0f599797934e`: build commit `5266f24da` is 149
commits ahead and zero behind. Existing DS4 results therefore compare against a
runtime that already has native architecture support; this does not make the
DS4 expert-streamed artifact equivalent to an ordinary GGUF load.

## Consequence for the next queue

After the serial Qwen repeated-quality cohort is idle:

1. Run plain Laguna NVFP4 oMLX admission first: it has first-party weights plus
   explicit installed model, quantization, tokenizer, and parser support.
2. Run plain Agents-A1 uniform-4-bit oMLX admission next. It is the most useful
   challenger for the newly separated scientific-research workload, but its
   composite VLM dispatch adds an extra admission risk.
3. Admit the corresponding plain GGUF controls with the pinned llama.cpp build
   before any cross-Mac comparison.
4. Add Laguna DFlash only after the plain target passes and retain acceptance
   separately for code, tools, prose, and repetition.
5. Do not schedule full Harbor or research portfolios for a route that fails
   load, ordinary output, exact tool parsing, memory/swap, or 128K retrieval.

This order is based on installed support and experimental value. It is not a
quality ranking and does not turn either candidate into a resident.

## Reproducibility commands

The checks intentionally avoid loading weights:

```console
omlx --version
llama-cli --version

# Run with the Python embedded in the installed oMLX package.
python -c 'import mlx_lm.models.qwen3_5_moe'
python -c 'from omlx.patches.laguna import apply_laguna_patch; assert apply_laguna_patch()'
python -c 'import mlx_lm.models.laguna, mlx_lm.tool_parsers.laguna'
```

The pinned artifact configurations and upstream merge identities are linked in
the corresponding Agents-A1 and Laguna intake documents.
