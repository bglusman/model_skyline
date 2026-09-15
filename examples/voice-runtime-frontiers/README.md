# Voice runtime frontiers

This experiment asks the same simple question as the text-model work: **which
few voice systems are best for a specific two-way tradeoff?**

The matched three-seed panel leaves two exact TTS winners, both serving
**Qwen3-TTS** on the RTX 5060 Ti. The patched Nari consumer is the interactive
choice: 57 ms p95 to audible speech and 4.97x real-time generation. vLLM-Omni
is the measured-intelligibility tradeoff: 9.33% WER versus Nari's 10.04%, but
roughly half a second slower to speak. Their WER intervals overlap, so this is
not evidence that one runtime is truly more accurate. These two offerings sit
on all four measured TTS frontiers. On the new memory frontier, Nari uses 9.73
GB of combined capacity accounting versus vLLM-Omni's 18.84 GB.

The M5 MLX path is fast and had slightly lower pooled WER than Nari, but one of
90 utterances contained a conspicuous repeated-word loop and fails the strict
zero-warning gate. LoudKit remains interesting for portability, footprint,
sustained throughput, and barge-in behavior, but token-cap cases at multiple
seeds keep both LoudKit offerings outside the current strict frontiers.

The speech-recognition side is now measured on both 64 GB Macs and the RTX
5060 Ti. **Parakeet TDT 0.6B v3** is the sole combined point-estimate resident
for WER versus median latency, p95 latency, and throughput. On the 5060 alone,
an encoder-compiled Parakeet profile dominates every baseline and optimized
candidate on those point estimates. The Mac small-resident frontier keeps
Parakeet and **Qwen3-ASR 0.6B 8-bit**. See the concise
[`local ASR report`](ASR.md) for definitions, exact points, uncertainty, and
the controlled M1/M5 comparison. The additional restart frontier measures a
fresh process through its first complete transcript: it keeps four tradeoffs
on the 5060 because compilation helps resident requests but hurts activation.
A ready-runner frontier removes Python/framework and accelerator setup from
that clock while keeping the model cold; it has the same residents and is
explicitly an approximation rather than a measured router swap. ASR memory
remains narrower than TTS memory because the current ASR experiment does not
yet have the same cross-platform whole-service instrument.

The runnable TTS definitions are [`frontier.yaml`](frontier.yaml), and the five
exact TTS offerings are in [`observations.json`](observations.json). The latter
is generated from the audited three-seed result in
[`tts-seed-panel-v1-results.json`](raw/tts-seed-panel-v1-results.json). The
[`aggregate helper`](aggregate_tts_seed_panel.py) verifies every raw hash and
cross-link before pooling utterances; [`build_observations.py`](build_observations.py)
then creates the ordinary Skyline catalog. The table below therefore explains
an executable frontier rather than a hand-ranked list.
Replayable exact snapshots and the simpler model-first projections are retained
in [`generated/`](generated/). ASR has its own
[`definitions`](asr-frontier.yaml) and [`catalog`](asr-observations.json) so
speech synthesis and recognition cannot be accidentally mixed.

## The frontiers in plain language

Every frontier has exactly two axes. Requirements such as language support or
a stable voice are pass/fail gates, not hidden extra dimensions.

| Frontier | First axis | Second axis | Status |
|---|---|---|---|
| TTS responsive | corpus word error rate, lower is better | p95 playback-aware time to first audible audio, lower is better | measured 90-utterance seed panel |
| TTS typical response | corpus word error rate, lower is better | p50 playback-aware time to first audible audio, lower is better | measured 90-utterance seed panel |
| TTS batch | corpus word error rate, lower is better | sustained real-time factor, higher is better | measured 90-utterance seed panel |
| TTS small resident | corpus word error rate, lower is better | peak whole-service capacity accounting, lower is better | measured across cold load plus the 90-utterance panel |
| STT responsive | corpus word error rate, lower is better | p95 resident complete-file-to-final latency, lower is better | measured 24-utterance pilot |
| STT typical response | corpus word error rate, lower is better | p50 resident complete-file-to-final latency, lower is better | measured 24-utterance pilot |
| STT batch | corpus word error rate, lower is better | corpus real-time factor, higher is better | measured 24-utterance pilot |
| STT small resident | corpus word error rate, lower is better | peak in-process RSS, lower is better | measured for comparable MLX offerings |
| STT restart | corpus word error rate, lower is better | median fresh-process launch to first complete transcript, lower is better | measured over ten restarts per offering |
| STT ready runner | corpus word error rate, lower is better | median model-cold activation after framework/device readiness, lower is better | measured over ten isolated activations per offering |
| Voice agent | deterministic task success, higher is better | p95 time from user stop to first audible response, lower is better | defined; not yet measured here |
| Voice agent cost | deterministic task success, higher is better | marginal cost per completed session, lower is better | defined; not yet measured here |

The primary latency statistic is p95: a voice interaction is especially
sensitive to occasional awkward pauses. The p50 frontier remains separate so a
system with good ordinary latency is not hidden by a tail problem. The current
executable gate rejects any offering with a captured endpoint/synthesis
failure, empty output, explicit token cap, invalid/suspect latency flag, or
deterministic completion/repetition warning. Supported language is part of the
offering identity. Long-form speaker consistency remains a separate diagnostic
until it has human-calibrated controls, so it is not silently folded into word
error rate or treated as a frontier gate yet.

The full voice-agent clock is also explicit:

```text
end-of-turn detection + final ASR + LLM first speakable text
                      + TTS first audible audio + playback buffering
```

LLM time to first token alone is therefore not a voice latency measurement.
The exact pipeline identity includes VAD, ASR, LLM, TTS, buffering, hardware,
runtime, and network path.

## First quality-linked local frontier

All rows used the same 30 public service-style prompts at seeds 7, 1234, and
2026: 90 scored utterances per offering, with one unscored warmup per run and
no audio playback. The generated audio was scored by one pinned local
Whisper-large-v3-turbo FP16 instrument using CoVAL normalization version 2 and
corpus-level WER. Lower WER and audible latency are better; higher real-time
factor (seconds of audio produced per wall second) is better.

| Exact offering | WER (descriptive 95% interval) | p50 audible | p95 audible | Median RTF | Automatic failures | Result |
|---|---:|---:|---:|---:|---:|---|
| Qwen3-TTS 1.7B BF16, patched Nari consumer through llama-swap, RTX 5060 Ti | 10.04% (6.66–14.06) | **47 ms** | **57 ms** | 4.97x | 0/90 | resident; fastest first audio |
| Qwen3-TTS 1.7B BF16, vLLM-Omni 0.26, RTX 5060 Ti | **9.33%** (5.66–13.96) | 560 ms | 574 ms | 4.13x | 0/90 | resident; lowest WER point estimate |
| Qwen3-TTS 1.7B 6-bit, MLX-Audio 0.5.4, M5 Max | 9.65% (6.49–13.53) | 79 ms | 102 ms | 5.43x | 1/90 | excluded: repeated-word loop |
| LoudKit `loudr-1-turbo`, PyTorch/CUDA, RTX 5060 Ti | 11.52% (6.40–18.62) | 501 ms | 595 ms | **14.40x** | 3/90 | excluded: token caps |
| LoudKit `loudr-1-turbo`, CPU generator/MPS renderer, M5 Max | 11.33% (6.53–17.67) | 1,082 ms | 1,422 ms | 6.24x | 2/90 | excluded: token caps |

All four exact frontiers now contain the same two offerings. Nari is much
faster on both latency axes and has higher sustained throughput; vLLM-Omni has
the lower WER point estimate. Neither dominates the other, so both remain for
responsive, typical-response, batch, and memory-efficiency use. Across 1,554
normalized reference words, Nari made 156 errors and vLLM-Omni made 145. Their
descriptive intervals overlap substantially, so the evidence does not establish
a real accuracy ordering.

### Whole-service memory frontier

The memory frontier compares the same corpus WER with peak capacity charged to
the complete selected serving process tree. Each capture starts before model
load and spans the same three 30-prompt seed runs. It therefore includes
transient load costs as well as resident inference; it is not just model-file
size or one idle RSS reading.

| Exact offering | Peak combined accounting | Independent host peak | Independent GPU peak | Strict result |
|---|---:|---:|---:|---|
| LoudKit, M5 Max | **3.37 GB unified** | 3.37 GB | — | excluded: 2/90 token caps |
| Qwen3-TTS 6-bit MLX, M5 Max | 3.95 GB unified | 3.95 GB | — | excluded: 1/90 repetition warning |
| LoudKit, RTX 5060 Ti | 4.22 GB | 2.50 GB | 1.72 GB | excluded: 3/90 token caps |
| Qwen3-TTS BF16, patched Nari, RTX 5060 Ti | **9.73 GB** | 5.51 GB | 6.91 GB | resident; smaller eligible point |
| Qwen3-TTS BF16, vLLM-Omni, RTX 5060 Ti | 18.84 GB | 7.31 GB | 12.38 GB | resident; lower WER point estimate |

For Apple, “combined” is the kernel-accounted unified physical footprint. For
CUDA, it is host PSS plus GPU allocation measured in the same sample. That
makes the second axis a useful resource-efficiency comparison, but not a claim
that Apple and CUDA bytes are interchangeable. A split-memory system fits only
when both its independent host peak and independent GPU peak fit their own
pools. The combined CUDA number can be lower than the sum of those two peaks
because the component maxima need not occur at the same instant.

The strict frontier still retains Nari and vLLM-Omni: Nari is smaller, while
vLLM-Omni has the lower WER point estimate. Among eligible rows, Nari reduces
combined accounting by about 48% and the independent GPU peak by about 44%.
The three smaller rows remain visible, but their deterministic failures are
admission gates rather than a hidden third axis. The model-first view again
reduces both eligible offerings to Qwen3-TTS; there is still no balanced
cross-model environment panel from which to compute an honest average.

One failed capture also exposed an operational boundary. The exclusive file
lock serializes runners that agree to use it, but it cannot stop a client from
calling Ollama directly. A direct client loaded another model during the first
vLLM attempt, so that attempt was discarded. The retained vLLM and CUDA
LoudKit captures temporarily stopped both llama-swap and Ollama, verified an
empty GPU, and then held the exclusive lock. The deployed follow-up now keeps
Ollama on loopback and makes the shared launcher refuse a second CUDA owner.
The mutex is still cooperative, not a machine-wide memory scheduler: a local
process can ignore both the router and its lock.

Future CUDA memory runs should use
[`capture_service_memory_v2.py`](capture_service_memory_v2.py). It checks all
CUDA compute allocations before launch and on every sample. If even one belongs
to a process outside the selected service tree, the capture stops instead of
quietly publishing an inflated result. It also records system-wide host swap
before, during, and after the run. A zero-growth claim therefore comes from the
capture itself, provided the host is otherwise isolated; swap is host-wide and
cannot be attributed to the selected process tree on a busy machine. The original
[`capture_service_memory.py`](capture_service_memory.py) remains unchanged
because the published v1 panel records its exact file hash. In other words, v1
reproduces the existing evidence; v2 is the safer default for new evidence.

### What the matched winner comparison adds

Because Nari and vLLM-Omni synthesized the same prompts at the same requested
seed settings, their difference can be measured cell by cell instead of by
comparing two independent summary intervals. The retained
[`paired comparison`](raw/tts-seed-panel-v1-paired-comparisons.json) reports
Nari minus vLLM-Omni:

| Difference | Measured delta | Descriptive paired 95% interval | Plain meaning |
|---|---:|---:|---|
| Corpus WER | +0.71 percentage points | −2.53 to +3.84 | no resolved accuracy ordering |
| p50 audible latency | −513 ms | −516 to −511 ms | Nari is materially faster |
| p95 audible latency | −517 ms | −629 to −470 ms | Nari is materially faster in the tail |
| Median real-time factor | +0.85x | +0.82x to +0.86x | Nari generates more audio per wall second |

Nari reached first audible audio sooner in all 90 matched cells. It made fewer
word errors in 10 cells, the same number in 60, and more in 20. Only 25.9% of
the descriptive bootstrap resamples favored Nari on WER, but the interval
crosses zero widely. That fraction is not a posterior probability or a
significance test; with three seed labels, the honest conclusion remains
“clear measured speed difference, unresolved accuracy difference.” Matching a
seed label also does not imply that different runtimes consumed identical
random draws.

The model-first best-available view contains only Qwen3-TTS because both exact
winners implement that model family. A balanced-average model view is not
published here: after the strict failure gate, the catalog does not contain a
complete common environment panel for two different model families. Inventing
an average over unmatched runtimes or hardware would imply evidence we do not
have.

This remains a provisional panel, not a finished voice leaderboard. WER is an
intelligibility proxy and cannot establish naturalness or speaker identity.
Three seeds expose meaningful run-to-run variation, but are still too few for
a stable population estimate. The interval therefore resamples seeds first
and prompts second and is explicitly descriptive.

The batch result changed materially from the one-seed screen. MLX's 5.43x RTF
would otherwise be competitive, while CUDA LoudKit is the raw throughput
leader at 14.40x; both fail the zero-warning gate. Nari and vLLM-Omni remain a
quality/throughput tradeoff rather than collapsing to one winner.
The separate memory frontier avoids mixing ordinary RSS, model-file sizes, and
runtime-specific allocator counters by using one process-tree definition and
retaining the unified-versus-split accounting distinction.

### Matched hardware-only controls

The earlier uncontrolled-seed screen remains useful for isolating the two Macs.
It is not mixed into the matched-seed quality frontier.

| Exact same artifact/runtime | M5 Max 64 GB | M1 Max 64 GB | M5 advantage |
|---|---:|---:|---:|
| Qwen3-TTS 1.7B 6-bit MLX, p50 audible | 75 ms | 163 ms | 2.16x |
| Qwen3-TTS 1.7B 6-bit MLX, median RTF | 5.69x | 2.64x | 2.16x |
| LoudKit `loudr-1-turbo`, p50 audible | 1,047 ms | 1,924 ms | 1.84x |
| LoudKit `loudr-1-turbo`, median RTF | 6.37x | 3.29x | 1.94x |

The retained raw captures are in [`raw/`](raw/). They retain the model
identifier (and checkpoint hash where the runtime exposed one), runtime
version, hardware, settings, individual requests, and public-prompt digest.
All three Qwen paths now declare their immutable repository revision at capture
time. The seed-panel result also retains the SHA-256 of every latency, WER,
pacing, and completion capture, so changing any input makes the replay fail.

### Pacing screen prompted by the HN discussion

The same saved audio can answer a narrower question without a model judge:
how quickly does it speak, and how much of each utterance is spent in internal
pauses? `score_tts_pacing.py` divides normalized reference words by the span
from first to last audible frame and counts continuous 150 ms below-threshold
runs inside that span.

| Exact offering | Corpus words/min | Utterance p50 | Utterance p95 | Median internal-pause share |
|---|---:|---:|---:|---:|
| Qwen3-TTS 1.7B BF16, Nari/5060 | 113.8 | 119.6 | 165.2 | 15.8% |
| Qwen3-TTS 1.7B 6-bit, MLX/M5 | 100.9 | 110.3 | 153.7 | 16.5% |
| Qwen3-TTS 1.7B BF16, vLLM-Omni/5060 | 113.4 | 118.6 | 171.8 | 13.6% |
| LoudKit turbo, M5 | 161.0 | 171.1 | 261.8 | 10.8% |
| LoudKit turbo, 5060 | 161.0 | 171.0 | 261.8 | 10.8% |

This does not turn “natural pacing” into a score. It shows that the three local
Qwen runtimes produced materially different durations despite the same prompt
panel, model family, voice, and requested seeds, while LoudKit had a much faster
tail. A matched set of human recordings is needed before declaring an
acceptable range or adding a gate. The energy-based pause proxy also is not yet
aligned to the prompt's punctuation.

### Preliminary long-form consistency screen

The HN report of a voice changing during a 33-second clip is testable, but not
with short customer-service prompts. The separate
[`speaker-consistency-v1.json`](prompts/speaker-consistency-v1.json) workload
contains three authored 94–106 word passages. It is intentionally kept out of
the CoVAL-derived frontier catalog: a different workload must not silently
contribute observations to the existing frontier.

| Exact offering | Audio range | WER | Unexpected-speaker cases | Repetition warnings | Words/min |
|---|---:|---:|---:|---:|---:|
| Qwen3-TTS 1.7B BF16, Nari/5060 | 42.2–65.4 s | 1.32% | 0/3 | 0/3 | 116.2 |
| Qwen3-TTS 1.7B 6-bit, MLX/M5 | 47.2–77.6 s | 1.32% | 0/3 | 1/3 | 101.6 |
| Qwen3-TTS 1.7B BF16, vLLM-Omni/5060 | 38.3–53.0 s | 1.32% | 0/3 | 0/3 | 128.0 |
| LoudKit turbo, M5 | 32.4–35.7 s | 1.66% | 0/3 | 0/3 | 180.1 |
| LoudKit turbo, 5060 | 31.1–36.6 s | 1.99% | 0/3 | 0/3 | 180.8 |

The pinned 117M-parameter MLX
[Streaming Sortformer](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1)
reported one speaker, no label changes, and no secondary-speaker activity in
all 15 generated passages. A local calibration smoke test also reported one
speaker for same-voice concatenations and two for one deliberately mixed
Qwen-Vivian/LoudKit-Joe control. The HN failure was therefore **not reproduced
in this small panel**; that is not evidence that it cannot occur.

These results are a quality screen, not a fourth frontier. There are only
three passages and one seed per offering, the diarizer can make mistakes, and
the controls are synthetic rather than matched human recordings. One Qwen/MLX
passage lasted 77.6 seconds and its ASR hypothesis ended with repeated
ellipsis-like output. Normalized WER charged only one insertion, illustrating
why completion, repetition, pacing, and speaker stability need separate gates.
The deterministic [`score_tts_completion.py`](score_tts_completion.py) replay
flags that case because its ASR hypothesis ends in eight identical punctuation
tokens while the reference has one. It also looks for adjacent one-to-eight
word loops. These are ASR-derived warnings, not proof of an acoustic defect.
The short matched panel's deterministic warnings are a conservative frontier
gate; the separate long-form result remains diagnostic and does not contribute
an observation to that gate.
The short panel hit a LoudKit token cap at all three CUDA seeds and at two of
three M5 seeds. Its absence from three long passages does not erase those
retained failures.

## What the matched Macs tell us

The two Macs have the same 64 GB capacity, but capacity is not speed. On the
exact same Qwen artifact and MLX configuration, the M5 was about 2.16x faster
than the M1 in both median audible latency and sustained generation. On the
exact same LoudKit checkpoint and split placement, the M5 was about 1.84x
faster to first audio and 1.94x faster in sustained generation.

That suggests a useful first approximation for other Apple Silicon users:

- equal unified memory makes roughly the same model/context combinations
  *possible*;
- chip generation and memory bandwidth can still move interactive speed by
  roughly 2x for these two runtimes;
- the multiplier is runtime- and workload-specific, so it should not be used
  as a universal model score;
- a CPU-heavy split such as LoudKit's Apple default makes CPU generation an
  explicit part of the comparison, while MLX exercises a different mix of GPU,
  unified-memory, and CPU work.

This is evidence from two machines, not a fitted scaling law. More prompt
lengths and at least one same-artifact GGUF control are needed before packaging
a hardware predictor.

## Why “first chunk” was not enough

The first vLLM-Omni endpoint run exposed a measurement trap. Its first small Qwen audio
frame arrived quickly, but meaningful later bytes often arrived after the
playback buffer would have emptied. The usual formula—first deliverable chunk
arrival plus the leading-silence duration—reported a 140 ms median. Mapping
each sample to the time it could actually play reported 506 ms, and 28 of 30
prompts had a pre-audible stall.

The capture therefore retains both numbers. The later matched-seed passes used
the guarded bootstrap suppression: it did not report the silent first frame as
deliverable audio. The seed-1234 pass measured 587 ms median without a hidden
underrun; the pooled three-seed result is 560 ms.

- **CoVAL-compatible TTFA** preserves comparison with the published formula;
- **playback-aware TTFA** pauses the audio clock on underrun and is the axis
  used for the local responsiveness frontier.

This is an inference from the observed HTTP chunk timeline. It does not imply
that the public hosted Nari service has the same buffering behavior. The
upstream [CoVAL methodology](https://github.com/coval-ai/benchmarks/blob/c9786d181776ba393e85718e26e6b9f67fe19f7c/docs/methodology.md)
defines the published first-arrival-plus-leading-silence metric; the local
extension covers a client behavior that formula does not model.

## What the HN discussion changed

The [Show HN discussion](https://news.ycombinator.com/item?id=49699267) is a
small, self-selected discussion, not validation data. It still supplied three
good test hypotheses and one candidate source:

1. [One listener](https://news.ycombinator.com/item?id=49701701) reported a
   voice change partway through a 33-second clip. The
   long-form screen above tests that regime directly rather than generalizing
   from the report; it did not reproduce the switch in this small panel.
2. Multiple commenters asked for independent quality evaluation and demos.
   This reinforces the decision not to publish latency-only rows as frontier
   winners.
3. [Another listener](https://news.ycombinator.com/item?id=49704759) thought
   all the samples spoke too quickly. The deterministic screen above now
   records normalized words per minute and an energy-based pause proxy.
   Speaking-rate limits belong in a calibrated use-case gate; WER cannot detect
   unnatural pacing.
4. The thread surfaced [LoudKit](https://github.com/loudreader/loudkit), which
   is now measured on all three local hosts.

The thread also linked the [Open ASR
Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard) and
[Qwen demo spaces](https://huggingface.co/Qwen/spaces). Those are useful for
candidate discovery, but a community link does not show that Nari's optimized
ASR serving engine is open or that leaderboard ranks transfer to this
hardware. The local STT panel should therefore test Qwen3-ASR directly against
Whisper and Parakeet rather than importing a rank.

The preliminary consistency screen synthesizes three fixed passages and uses a
pinned streaming diarizer to count unexpected speaker labels, label changes,
and secondary-speaker activity. Its settings distinguished the deliberately
mixed synthetic control, but thresholds still need real same-speaker and
different-speaker controls before the screen can admit or reject models. A
short listening audit remains diagnostic, not a numerical frontier axis.

## Nari engine and LoudKit tradeoffs

[Nari's Qwen3-TTS engine](https://github.com/nari-labs/nari-qwen3-tts) publishes
an H100-oriented path using CUDA 13, FP8 kernels, FlashInfer, and an SM90/H100
requirement. It does not run unchanged on the RTX 5060 Ti. We tested a small,
explicit compatibility patch instead: keep the talker projection in BF16 when
Hopper FP8 is unavailable, capture only batch one, and reduce the KV pages and
FlashInfer workspace. The exact patch and batch-one profile are retained with
the [5060 router manifest](../local-runtime-frontiers/inference-vm-router/README.md).
This is an experimental local patch, not an upstream Nari release.

That path worked unusually well for an interactive voice agent. It used 6,586
MiB of resident CUDA allocation after startup, produced byte-identical audio
through direct and llama-swap endpoints, and delivered the pooled panel at
47 ms median and 57 ms p95 first audible audio. A text-model → Nari → unload
switch test left one runner and one lock holder at each step. The 6,586 MiB
number is a resident CUDA observation. The cross-runtime capture above is
broader: it includes host PSS, model load, and all three seed runs, reaching a
6.91 GB independent device peak and 9.73 GB same-sample combined peak.

Nari suppresses Qwen's fixed bootstrap audio in the server. The independent
MLX path uses a guarded client-side version: it drops the 1,920-sample frame
only after verifying that the frame is below the same audibility threshold.
That cut the M5 MLX Qwen median from roughly 147 ms to the retained 75 ms
without blindly deleting audible output.

The official [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) implementation,
[MLX-Audio](https://github.com/Blaizzy/mlx-audio), and
[vLLM-Omni](https://github.com/vllm-project/vllm-omni) provide the portable
comparison paths used here. Nari's hosted [CoVAL result
write-up](https://narilabs.com/blog/nari-labs-leads-coval-voice-ai-benchmarks/)
is useful provider evidence, but it is not imported as a score for local
weights or runtimes.

LoudKit is a different tradeoff. Its checkpoint is small, its API supports
multiple local backends, continuation, cancellation, and voice cloning, and it
is fast in sustained CUDA generation. Its current Python streaming surface
yields complete synthesis windows, so short-prompt first audio arrives much
later than Qwen's codec-frame stream. On the present evidence it is a strong
footprint, portability, batch, and barge-in candidate—not the local TTFA
winner. The CUDA path hit one token cap in every seed run, and that recurring
failure must be understood before it can pass a strict agent-voice gate.

## Practical deployment recommendation

For this homelab, the most promising first complete pipeline is:

```text
always-on M1: resident local ASR and TTS
              |
              +-- local or remote LLM selected per task

available M5: fastest local speech and larger local LLM experiments

RTX 5060 Ti:  Nari TTS or a text model selected through one llama-swap endpoint;
              the exclusive group unloads one before starting the other
```

This keeps turn-taking local and private while allowing the LLM to be remote
when its quality advantage matters. Patched Nari on the 5060 is now the fastest
tested Qwen TTS path; the M5 MLX path avoids an experimental CUDA patch, and the
M1 can deliver a 163 ms median while remaining always available. On the 5060,
TTS belongs in the same exclusive `llama-swap` memory group as text models:
automatic switching has been tested, while safe co-residency has not.

## Reproducing the latency screen

The prompt file is an exact Apache-2.0 copy of CoVAL `tts-v1` 1.2.0 at commit
`c9786d181776ba393e85718e26e6b9f67fe19f7c`; its SHA-256 is
`e30909112f5fd886157008ca960c00d4f68f29de69b29d5913a5bf3383ad71ec`.
Each helper buffers output and does not play audio.
Run each synthesis command once with each of the matched seeds `7`, `1234`,
and `2026`, using a different output file and WAV directory for every seed.
Keep every other offering setting byte-for-byte stable and run only one model
runner per accelerator at a time.

MLX/Qwen:

```console
python examples/voice-runtime-frontiers/bench_mlx_tts.py \
  --model mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit \
  --model-revision 1c6c0ff58c43afa8df571facde2efa077efd85e2 \
  --speaker Vivian --language English --streaming-interval 0.32 --seed 1234 \
  --suppress-fixed-bootstrap \
  --prompt-manifest examples/voice-runtime-frontiers/prompts/coval-tts-v1.json \
  --save-wav-dir generated/qwen-mlx-audio \
  --output result.json
```

LoudKit:

```console
python examples/voice-runtime-frontiers/bench_loudkit_tts.py \
  --model loudreader/loudr-1-turbo --voice joe --language en --seed 7 \
  --prompt-manifest examples/voice-runtime-frontiers/prompts/coval-tts-v1.json \
  --save-wav-dir generated/loudkit-audio \
  --output result.json
```

OpenAI-compatible PCM endpoint:

```console
python examples/voice-runtime-frontiers/bench_openai_tts.py \
  --base-url http://host:port \
  --model Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice \
  --model-revision 0c0e3051f131929182e2c023b9537f8b1c68adfe \
  --runtime-label "runtime and version" --server-hardware "exact host" \
  --voice vivian --language English --seed 1234 --suppress-fixed-bootstrap \
  --prompt-manifest examples/voice-runtime-frontiers/prompts/coval-tts-v1.json \
  --save-wav-dir generated/qwen-http-audio \
  --output result.json
```

For vLLM-Omni 0.26, start the Qwen server in Omni mode; without `--omni` the
CLI selects base vLLM's incompatible single-model path:

```console
VLLM_USE_FLASHINFER_SAMPLER=0 vllm-omni serve \
  Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice --omni \
  --revision 0c0e3051f131929182e2c023b9537f8b1c68adfe \
  --max-model-len 2048 --port 8091
```

Strict Nari servers omit `stream_format` and explicitly request streaming
generation with `--stream-format omit --no-non-streaming-mode`. When the real
router address is private, `--endpoint-label` records a public-safe label while
the network clock still uses `--base-url`. The capture hashes its own benchmark
script so later catalog builds can reject a mismatched harness.

Score the audio with the fixed local instrument, then rebuild and evaluate the
frontiers:

```console
python examples/voice-runtime-frontiers/score_tts_wer.py \
  --audio-dir generated/qwen-mlx-audio \
  --prompt-manifest examples/voice-runtime-frontiers/prompts/coval-tts-v1.json \
  --asr-revision 624c19c9af5603fa73b83bce14d4aeea96156d18 \
  --latency-capture result.json \
  --output result-wer.json

python examples/voice-runtime-frontiers/score_tts_pacing.py \
  --audio-dir generated/qwen-mlx-audio \
  --prompt-manifest examples/voice-runtime-frontiers/prompts/coval-tts-v1.json \
  --output result-pacing.json

python examples/voice-runtime-frontiers/score_tts_speaker_drift.py \
  --audio-dir generated/qwen-mlx-long-audio \
  --prompt-manifest \
    examples/voice-runtime-frontiers/prompts/speaker-consistency-v1.json \
  --latency-capture result-long.json \
  --output result-long-diarization.json

python examples/voice-runtime-frontiers/score_tts_completion.py \
  --wer-capture result-wer.json \
  --output result-completion.json

python examples/voice-runtime-frontiers/aggregate_tts_seed_panel.py \
  --panel examples/voice-runtime-frontiers/tts-seed-panel.json \
  --output examples/voice-runtime-frontiers/raw/tts-seed-panel-v1-results.json
python examples/voice-runtime-frontiers/compare_tts_seed_panel.py \
  --panel examples/voice-runtime-frontiers/tts-seed-panel.json \
  --aggregate examples/voice-runtime-frontiers/raw/tts-seed-panel-v1-results.json \
  --output \
    examples/voice-runtime-frontiers/raw/tts-seed-panel-v1-paired-comparisons.json
python examples/voice-runtime-frontiers/build_tts_service_memory_panel.py \
  --check
python examples/voice-runtime-frontiers/build_observations.py
modelskyline validate \
  examples/voice-runtime-frontiers/frontier.yaml \
  examples/voice-runtime-frontiers/observations.json
modelskyline evaluate \
  examples/voice-runtime-frontiers/frontier.yaml \
  examples/voice-runtime-frontiers/observations.json \
  tts-responsive-intelligibility
```

The generated WAV files are not committed. The raw JSON retains content hashes,
measurements, and transcriptions without adding unexpected audio playback.

## Next evidence required

1. Expand the frozen panel beyond three synthesis seeds; the aggregate and
   paired bootstrap intervals are still provisional.
2. Add matched human same-speaker and different-speaker controls, then turn the
   preliminary long-form diarization screen into a calibrated admission gate.
3. Record matched human controls, align pauses to punctuation, and only then
   calibrate speaking-rate and pause gates.
4. Repeat whole-service memory captures to quantify run-to-run peak variation,
   then add comparable ASR and complete voice-pipeline memory panels.
5. Measure Qwen3-ASR, Whisper, and Parakeet on a small conversational, noisy,
   technical-vocabulary, and accented panel, retaining
   both partial latency and final-transcript latency.
6. Measure the complete voice-agent clock and deterministic task success with
   local and remote LLMs. Only then publish full-pipeline frontier residents.
