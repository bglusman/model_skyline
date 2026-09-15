# Local-model status — 2026-09-15

This is the plain-language handoff for the RTX 5060 Ti work completed after the
Qwen3.5 9B result. The M5 Mac GPU was kept idle during this phase.

## What changed

The 5060 now has two recommended 128K-class text routes behind the same
llama-swap mutex:

- **Qwen3.5 9B Q6_K, Q8 KV** — the faster and smaller long-context utility
  route already published in PR #84.
- **Muse Glimmer 30B AD-IQ3_XXS, Q4 KV** — a slower, larger-model candidate
  added in this branch for coding-quality evaluation.

Selecting either route unloads the other. The published config does not expose
the rejected ShoeHorn experiment. At handoff, llama-swap reports no running
model, no CUDA compute process is present, and the temporary benchmark firewall
and loopback proxy have been removed.

## Muse result in simple terms

AtomicChat's calibrated AD-IQ3_XXS is the retained Muse file. It is 12.22 GB,
fits a 131,072-token service allocation on the 16 GB GPU, passes 3/3 automatic
30-tool calls, and returns the exact passkey with the needle near the beginning,
middle, and end of roughly 126K input tokens. A fresh-server middle run reported
zero cached prompt tokens and completed exactly.

The custom ShoeHorn file also fits and passes those semantic checks. It is not
the default because it is 7.1% larger, 2.3% slower in short decode, 10.2% worse
on the same pinned-corpus perplexity check, and sometimes spends far more tokens
reasoning before the same answer. Its one clear win is prompt processing.

| Matched 5060 check | AD-IQ3_XXS | ShoeHorn 3.758 bpw |
| --- | ---: | ---: |
| File bytes | 12,224,327,968 | 13,096,573,440 |
| pp2048 median | 1,022.59 tok/s | 1,109.02 tok/s |
| tg512 median | 31.3664 tok/s | 30.6355 tok/s |
| Pinned-corpus PPL | 5.2003 ± 0.11916 | 5.7316 ± 0.13457 |
| Warm 30-tool median | 4.929 s, 3/3 exact | 8.799 s, 3/3 exact |
| Early/middle/late 126K retrieval | 3/3 exact | 3/3 exact |

This is a successful custom *fit* and a failed *promotion*. It is much more
useful evidence than a load failure: it shows why fit, throughput, held-out
loss, semantic checks, and answer length all need to be considered together.

## Frontier residents

Every row still compares exactly two quantities:

- **Muse raw prompt speed × generation speed:** ShoeHorn is the resident under
  the existing 3% practical-equivalence rule. Its prompt lead is material; the
  control's 2.4% decode lead is inside the tolerance.
- **Muse exact tool success × warm turnaround:** AD-IQ3_XXS is the resident.
  Both are 100% correct, and the control is faster end to end.
- **Muse held-out loss × artifact bytes:** AD-IQ3_XXS is smaller and has lower
  perplexity. This is a quantization diagnostic, not a coding-intelligence
  claim.
- **Broader 5060 prompt speed × generation speed:** Qwen3.5 9B and Laguna XS
  2.1 remain the model residents. Both Muse offerings are dominated on this
  narrow raw-speed definition.

Muse may still earn a quality × speed or quality × memory frontier position;
that requires running the same local coding-quality pilot. Publisher benchmark
scores are not copied onto the local quant.

## ShoeHorn work sent upstream

- [PR #3](https://github.com/notactuallytreyanastasio/shoehorn/pull/3): model
  discovery, input validation, mixed-file metadata, and runtime fixes.
- [PR #5](https://github.com/notactuallytreyanastasio/shoehorn/pull/5): build
  Linux releases on Ubuntu 22.04 so the binary does not require GLIBC 2.39.
- [PR #6](https://github.com/notactuallytreyanastasio/shoehorn/pull/6): repeatable
  exact tensor-type overrides for fit, plan, and quantize. This enabled the
  Muse experiment without adding a Muse-specific heuristic.

The inference VM disk was expanded from 300 to 400 GiB so the two 27.8 GB BF16
source shards, imatrix, controls, and outputs could coexist. The source and
output hashes and the complete constrained plan are retained in
[`muse-glimmer-shoehorn-5060-ctx131k-q4kv-iq4embdout-plan.json`](artifacts/muse-glimmer-shoehorn-5060-ctx131k-q4kv-iq4embdout-plan.json).

## Next useful work

1. Run the same small Harbor coding-quality pilot on 5060 Qwen3.5 and Muse.
   That creates the missing quality × latency and quality × memory comparison.
2. Add a sampled whole-service memory capture for Muse. The current VRAM values
   are deployment snapshots, not eligible memory-frontier evidence.
3. Decide whether ShoeHorn should grow a baseline-comparison command that
   automatically rejects a larger/worse-loss plan before publication.
4. Keep Mac GPU work paused until local noise is acceptable; none of this 5060
   work depends on it.
