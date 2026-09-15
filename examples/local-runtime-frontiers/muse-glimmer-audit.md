# Muse Glimmer local audit

Audit dates: 2026-09-13 through 2026-09-15. The first results use the Apple M5
Max 40-core-GPU, 64 GB hardware profile; the later section is an explicitly
separate RTX 5060 Ti 16 GB comparison. Muse Glimmer is a dense 30B-class model;
DFlash2 is a companion speculative decoder, not a mixture-of-experts
designation. The
[official model card](https://huggingface.co/meta-models/Muse-Glimmer-30B)
reports a 131,072-token context and strong agent/tool benchmarks. Those
publisher scores motivate testing but are not assigned to either local quant in
Model Skyline.

## Exact artifacts

The current recommended target is Meta's official Dynamic Q4_K_XL at immutable
GGUF revision `70bf1b61ac09f91b24d39038091b41c582bc5d7a`:

- 19,653,960,832 bytes;
- SHA-256 `ac7023d6a4c704eb9af54ab53e476a66b7f5b6c0ef2fc4a8dde5253c291a6c38`.

The experimental DFlash route adds z-lab's official Q4_K_M draft at revision
`880882627431093d99d3b2368efb4a6fcf12d4cb`:

- 1,645,657,280 bytes;
- SHA-256 `93dbfb6f88e4645dec1347cf93f9d6fc80b90d413038722385b2a8e53565c949`.

The composite manifest is
[`artifacts/muse-glimmer-30b-dynamic-q4xl-dflash2-manifest.json`](artifacts/muse-glimmer-30b-dynamic-q4xl-dflash2-manifest.json).
Both managed llama.cpp routes allocate 131,072 tokens, use Q8_0 K/V, place all
layers on Metal, disable auto-fit, enable flash attention/Jinja, and retain the
default 8 GiB prompt cache. The DFlash route applies the same Q8_0 types to its
draft KV and drafts at most three tokens.

## ShoeHorn fit

The custom artifact was created from AtomicChat's two-shard BF16 GGUF and its
calibrated importance matrix at immutable revision
`b26f8ce4a435571e24656d27efef978073809356`. The exact source and imatrix file
sizes and hashes, request flags, solver counts, and output digest are retained
in
[`artifacts/muse-glimmer-shoehorn-18p31gib-plan.json`](artifacts/muse-glimmer-shoehorn-18p31gib-plan.json).
The request was:

```console
shoehorn quantize \
  --model Muse-Glimmer-30B-BF16-00001-of-00002.gguf \
  --imatrix muse-glimmer.imatrix.gguf \
  --ctx 131072 --kv q8_0 --budget 26.25GiB --reserve 4GiB \
  --output Muse-Glimmer-30B-ShoeHorn-18.31GiB.gguf
```

This was the default sampled-error solve—128 sampled rows per tensor—not
`--exact-errors`. It solved 418 of 731 tensors, found imatrix data for 417,
reported 18.31 GiB of weights, 5.646 overall bpw, and 290,816 bytes of modeled
slack. The output is 19,672,967,680 bytes with SHA-256
`4037b09f40a0f4a212ed749ec1b53621925dcf4903f45f9d94d6e4fe3abb601e`.

ShoeHorn previously derived GGUF `file_type` from the single largest solved
tensor. Muse's 2.50 GiB F16 output tensor therefore mislabeled this heavily
mixed file as F16 even though Q6_K is the largest aggregate assignment at
6.16 GiB. Local ShoeHorn commit
`ca565f8df394ece7d83b692b31f2d6afc19088cd` aggregates bytes per type and adds
a regression test. The existing output predates that fix: its embedded label is
stale, but the tensor encodings are valid and llama.cpp loads them correctly.

## Same-checkpoint quantization result

Both artifacts used llama.cpp build 10809 at `5266f24da`, all layers on Metal,
auto-fit off, Q8_0 K/V, pp2048/tg512, six CPU threads, one built-in warmup, and
five measured serial repetitions.

| Artifact | Bytes | Median prompt tok/s | Median decode tok/s | PPL ± reported SE |
| --- | ---: | ---: | ---: | ---: |
| Official Dynamic Q4_K_XL | 19,653,960,832 | 717.593 | 24.9124 | 5.0167 ± 0.11407 |
| ShoeHorn sampled mixed fit | 19,672,967,680 | 695.556 | 24.5243 | 5.4027 ± 0.12476 |

The official artifact is 19,006,848 bytes smaller, 3.17% faster in prompt
processing, and 1.58% faster in decode. The ShoeHorn PPL point estimate is
7.69% higher on the pinned five-chunk, 101,113-byte WikiText-2 slice. An earlier
small source-code control independently measured 2.3073 versus 2.1406, a 7.79%
increase. Tiny-corpus PPL is not a coding or agent-quality score, but the
direction replicated and there is no compensating local signal. Within this
same-checkpoint diagnostic, the official quant strictly dominates the custom
fit on bytes and both throughput axes and has the better held-out loss signal.

This result does not show that ShoeHorn is generally ineffective. It shows that
the current sampled per-tensor reconstruction objective did not beat an expert
dynamic quant on this checkpoint. Useful upstream improvements would include:

- automatically comparing a candidate with ordinary/dynamic quant baselines and
  rejecting a fit that is dominated on its requested deployment target;
- testing `--exact-errors` and reporting whether the additional compute changes
  the selected layout or held-out loss;
- adding an optional task/corpus-level validation objective instead of assuming
  local tensor error predicts sequence loss;
- embedding the source revision, imatrix digest, complete plan, and solver mode
  into output metadata; and
- retaining the new aggregate-byte `file_type` logic for mixed artifacts.

## RTX 5060 Ti 16 GB result

The 5060 comparison asks a different question: what is the best fully resident
Muse offering when a 131,072-token service allocation and Q4 K/V cache must fit
in 16 GB of VRAM? The control is AtomicChat's calibrated AD-IQ3_XXS file from
revision `b26f8ce4a435571e24656d27efef978073809356`: 12,224,327,968 bytes,
SHA-256 `392ee6a723541aaf79374c046ddc41464981698e807f5d600f41e294d4b7cec6`.
The publisher reports 3.51 effective bpw and separate neutral/agentic KLD
measurements; Model Skyline retains those as provenance rather than treating
them as a local task score.

ShoeHorn's default sampled solve spent 2.66 GiB on `token_embd.weight` and
`output.weight`, even though AtomicChat's published calibration identified
output as a poor low-bit investment relative to several attention/FFN groups.
The general tensor-override implementation in upstream
[ShoeHorn PR #6](https://github.com/notactuallytreyanastasio/shoehorn/pull/6)
made the comparison testable without a Muse-specific code path. Forcing those
two tensors to IQ4_XS reduced their combined charge to 1.34 GiB and let the
solver redistribute the remaining budget. The complete request, assignments,
source hashes, log hashes, and output digest are in the
[5060 plan](artifacts/muse-glimmer-shoehorn-5060-ctx131k-q4kv-iq4embdout-plan.json).

Both files used llama.cpp build 2515 at `5266f24d`, all layers on CUDA,
auto-fit and mmap-style loading disabled, Q4 K/V, pp2048/tg512, six CPU
threads, one built-in warmup, and five measured repetitions.

| 5060 artifact | Bytes | Median prompt tok/s | Median decode tok/s | PPL ± reported SE |
| --- | ---: | ---: | ---: | ---: |
| AtomicChat AD-IQ3_XXS | 12,224,327,968 | 1,022.59 | 31.3664 | 5.2003 ± 0.11916 |
| ShoeHorn sampled mixed 3.758 bpw | 13,096,573,440 | 1,109.02 | 30.6355 | 5.7316 ± 0.13457 |

The custom file is 7.1% larger and 8.5% faster for short prompt processing,
but 2.3% slower for decode and 10.2% worse on the identical pinned-corpus
perplexity control. Its loaded service also occupied about 12.84 GiB of VRAM
versus about 12.06 GiB for the control in one post-load snapshot. These VRAM
snapshots are deployment observations, not sampled memory frontier records.

Both artifacts passed the small semantic gates: 3/3 exact automatic calls from
30 tool schemas and exact 126K retrieval with the needle near the beginning,
middle, and end. However, the custom quant used 248 completion tokens for each
warm tool call versus 142–143 for the control. Its long-context completions
ranged from 285 to 941 tokens; the control's successful 1,024-token-allowance
runs used 192–329. The late custom request therefore took 198.0 seconds despite
faster prefill, versus 174.0 seconds for the control. A 256-token allowance is
not sufficient for Muse at this workload: the control found no final answer
before the length stop, then returned the exact answer with 1,024 allowed.
The retained middle-position control was also repeated from a fully unloaded
server: all 126,001 prompt tokens were uncached, health-ready took 11.34
seconds, the first semantic event arrived at 167.54 seconds including load, and
the exact answer completed at 181.46 seconds.

The result is a useful success for *fit* and a rejection for *promotion*.
ShoeHorn produced a loadable 128K-class offering that retained the checked
tool/retrieval behavior, unlike the earlier Laguna fit, but the larger file and
faster prefill do not compensate for worse held-out loss, slower decode, and
less predictable reasoning length. The deployed 5060 route is therefore the
AD-IQ3_XXS control; the custom route is retained only as reproducible evidence.

## Agent-facing target versus DFlash

The operational probe exposes 30 deterministic tools, leaves selection on
automatic, uses a byte-stable 5,390-token request, and asks for exact arguments.
Muse receives `Reasoning strength: low.` in the hashed system prompt and the
runtime's default chat-template behavior. Tool APIs buffer the structured call,
so llama.cpp supplies no server TTFT/decode timing; Model Skyline correctly
publishes first semantic-event and end-to-end time instead.

| Route / state | Output allowance | Exact calls | First semantic event | Median end-to-end | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Target-only, cold load | 1,024 | 1/1 | 10.545 s | 28.685 s | 21.010 GB |
| Target-only, warm prefix | 1,024 | 3/3 | 0.218 s | 15.038 s | 21.011 GB |
| DFlash2, cold load batch 1 | 1,024 | 0/1 | 10.758 s | 22.411 s | 23.492 GB |
| DFlash2, cold load batch 2 | 1,024 | 0/1 | 12.579 s | 25.975 s | 23.492 GB |
| DFlash2, warm prefix batch 1 | 1,024 | 3/3 | 0.183 s | 8.359 s | 23.534 GB |
| DFlash2, warm prefix batch 2 | 1,024 | 3/3 | 0.200 s | 10.665 s | 23.5 GB |
| DFlash2, miss/warm | 256 | 0/3 | 0.184–8.652 s | 6.630–14.991 s | 23.500 GB |

Both cold DFlash trials ended after 463 completion tokens with the same 269-byte
non-tool answer and identical content SHA-256. The following warm-prefix trials
ended after 335 tokens with the exact call and arguments. Target-only also
changed reasoning length between cold and warm positions, but remained
semantically correct in every trial. No request contained a llama-swap loading
event or increased swap usage.

A follow-up control distinguishes persistent prompt reuse from the server's
separate 8 GiB saved-prompt cache. Setting only `--cache-ram 0` did not disable
same-slot longest-common-prefix reuse: the second request still reported 5,389
cached tokens and changed from the wrong answer to the exact tool call. With
both `--cache-ram 0` and `--no-cache-prompt`, two consecutive requests reported
zero cached tokens and reproduced the identical 463-token wrong answer. The raw
captures retain both controls.

This isolates a reproducible semantic divergence to DFlash plus llama.cpp's
prompt-reuse path: the same hashed request, seed, target/draft bytes, and runtime
configuration yields a different answer when its 5,389-token prefix is reused.
The evidence does not yet identify whether the incorrect state is the uncached
or reused computation, but target-only supplies the expected tool call in both
states. Target-only is therefore the recommended agent route. DFlash remains
exposed as experimental because its warm speedup is valuable enough to
investigate, but a warm-only benchmark must not hide the divergence.
