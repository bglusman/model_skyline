# Voice runtime frontiers

This experiment asks the same simple question as the text-model work: **which
few voice systems are best for a specific two-way tradeoff?**

Across both latency frontiers, the first pilot has one clear model-level
resident: **Qwen3-TTS**. Its M5 MLX offering is the latency winner, while its
BF16 CUDA offering had a slightly lower point-estimate word error rate. That
quality difference is not statistically resolved, so it is evidence to
repeat—not a claim that CUDA is more accurate. LoudKit remains interesting for
portability, footprint, sustained throughput, and barge-in behavior. Its M5
offering reaches the batch frontier, but LoudKit is not on either
quality/latency frontier.

The speech-recognition side is now measured too. On both 64 GB Macs,
**Parakeet TDT 0.6B v3** is the sole point-estimate resident for WER versus
median latency, p95 latency, and throughput. The small-resident frontier keeps
both Parakeet and **Qwen3-ASR 0.6B 8-bit**. See the concise
[`local ASR report`](ASR.md) for definitions, exact points, uncertainty, and
the controlled M1/M5 comparison.

The runnable TTS definitions are [`frontier.yaml`](frontier.yaml), and the four
exact TTS offerings are in [`observations.json`](observations.json). The latter
is generated and cross-checked against the retained raw captures by
[`build_observations.py`](build_observations.py), so the table below is an
explanation of an executable frontier rather than a hand-ranked list.
Replayable exact snapshots and the simpler model-first projections are retained
in [`generated/`](generated/). ASR has its own
[`definitions`](asr-frontier.yaml) and [`catalog`](asr-observations.json) so
speech synthesis and recognition cannot be accidentally mixed.

## The frontiers in plain language

Every frontier has exactly two axes. Requirements such as language support or
a stable voice are pass/fail gates, not hidden extra dimensions.

| Frontier | First axis | Second axis | Status |
|---|---|---|---|
| TTS responsive | corpus word error rate, lower is better | p95 playback-aware time to first audible audio, lower is better | measured pilot |
| TTS typical response | corpus word error rate, lower is better | p50 playback-aware time to first audible audio, lower is better | measured pilot |
| TTS batch | corpus word error rate, lower is better | sustained real-time factor, higher is better | measured pilot |
| TTS small resident | corpus word error rate, lower is better | peak whole-service memory, lower is better | defined; awaiting comparable memory captures |
| STT responsive | corpus word error rate, lower is better | p95 resident complete-file-to-final latency, lower is better | measured 24-utterance pilot |
| STT typical response | corpus word error rate, lower is better | p50 resident complete-file-to-final latency, lower is better | measured 24-utterance pilot |
| STT batch | corpus word error rate, lower is better | corpus real-time factor, higher is better | measured 24-utterance pilot |
| STT small resident | corpus word error rate, lower is better | peak in-process RSS, lower is better | measured for comparable MLX offerings |
| Voice agent | deterministic task success, higher is better | p95 time from user stop to first audible response, lower is better | defined; not yet measured here |
| Voice agent cost | deterministic task success, higher is better | marginal cost per completed session, lower is better | defined; not yet measured here |

The primary latency statistic is p95: a voice interaction is especially
sensitive to occasional awkward pauses. The p50 frontier remains separate so a
system with good ordinary latency is not hidden by a tail problem. A candidate
must also pass declared gates for no truncation, no playback underrun, supported
language, intelligible output, and stable speaker identity. Long-form speaker
consistency is not folded into word error rate; it gets its own reproducible
gate.

The full voice-agent clock is also explicit:

```text
end-of-turn detection + final ASR + LLM first speakable text
                      + TTS first audible audio + playback buffering
```

LLM time to first token alone is therefore not a voice latency measurement.
The exact pipeline identity includes VAD, ASR, LLM, TTS, buffering, hardware,
runtime, and network path.

## First quality-linked local frontier

All rows used the same 30 public service-style prompts, one fixed synthesis
seed per offering, one unscored warmup, one resident model process, and no
audio playback. The generated audio was scored by one pinned local
Whisper-large-v3-turbo FP16 instrument using CoVAL normalization version 2 and
corpus-level WER. Lower WER and audible latency are better; higher real-time
factor (seconds of audio produced per wall second) is better.

| Exact offering | WER | p50 audible | p95 audible | Median RTF | Result |
|---|---:|---:|---:|---:|---|
| Qwen3-TTS 1.7B CustomVoice 6-bit, MLX-Audio 0.5.4, M5 Max 64 GB | 8.11% | **74 ms** | **237 ms** | 5.67x | frontier resident |
| Qwen3-TTS 1.7B CustomVoice BF16, vLLM-Omni 0.26, RTX 5060 Ti 16 GB | **7.34%** | 587 ms | 633 ms | 4.07x | frontier resident |
| LoudKit `loudr-1-turbo`, PyTorch/CUDA, RTX 5060 Ti 16 GB | 13.71% | 499 ms | 600 ms | **14.54x** | excluded: 1/30 cap/suspect case |
| LoudKit `loudr-1-turbo`, CPU generator/MPS renderer, M5 Max 64 GB | 13.13% | 1,093 ms | 1,639 ms | 6.30x | latency-frontier dominated; batch-frontier resident |

Both the typical-response and responsive p95 point-estimate frontiers contain
the two Qwen offerings: MLX is faster, while BF16 CUDA has the lower WER point
estimate. Their model-level projection contains only Qwen3-TTS. Across the 518
normalized reference words, CUDA made 38 errors and MLX made 42. A paired
utterance bootstrap puts the MLX-minus-CUDA difference at approximately -2.45
to +4.00 percentage points (95% interval), so the evidence does not establish a
real quality difference. With uncertainty handled conservatively, both exact
offerings remain instead of selecting a winner from noise.

This is still a pilot, not a finished voice leaderboard. WER is an
intelligibility proxy and cannot establish naturalness or speaker identity.
One synthesis seed is also too little for a stochastic model. The next frozen
version will use a prompt-by-seed panel and the speaker-consistency gate.

The batch frontier contains three eligible tradeoffs: M5 LoudKit at 6.30x RTF,
M5 MLX Qwen at 5.67x with lower WER, and CUDA Qwen at 4.07x with the lowest WER
point estimate. CUDA LoudKit supplies the highest raw throughput at 14.54x but
fails the no-cap gate. Peak-memory numbers are not mixed yet because MLX
accelerator peaks, process RSS, and multi-process CUDA allocations are
different measurements. The memory frontier will use one defined whole-service
measure for every row.

### Matched hardware-only controls

The earlier uncontrolled-seed screen remains useful for isolating the two Macs.
It is not mixed into the fixed-seed quality frontier.

| Exact same artifact/runtime | M5 Max 64 GB | M1 Max 64 GB | M5 advantage |
|---|---:|---:|---:|
| Qwen3-TTS 1.7B 6-bit MLX, p50 audible | 75 ms | 163 ms | 2.16x |
| Qwen3-TTS 1.7B 6-bit MLX, median RTF | 5.69x | 2.64x | 2.16x |
| LoudKit `loudr-1-turbo`, p50 audible | 1,047 ms | 1,924 ms | 1.84x |
| LoudKit `loudr-1-turbo`, median RTF | 6.37x | 3.29x | 1.94x |

The retained raw captures are in [`raw/`](raw/). They retain the model
identifier (and checkpoint hash where the runtime exposed one), runtime
version, hardware, settings, individual requests, and public-prompt digest.
The generated catalog also records the two Qwen repository revisions found in
the local caches after capture and labels that weaker provenance explicitly;
the capture helpers now accept immutable revisions so reruns bind them at
capture time.

### Pacing screen prompted by the HN discussion

The same saved audio can answer a narrower question without a model judge:
how quickly does it speak, and how much of each utterance is spent in internal
pauses? `score_tts_pacing.py` divides normalized reference words by the span
from first to last audible frame and counts continuous 150 ms below-threshold
runs inside that span.

| Exact offering | Corpus words/min | Utterance p50 | Utterance p95 | Median internal-pause share |
|---|---:|---:|---:|---:|
| Qwen3-TTS 1.7B 6-bit, MLX/M5 | 94.6 | 103.8 | 141.4 | 17.7% |
| Qwen3-TTS 1.7B BF16, vLLM-Omni/5060 | 119.3 | 127.6 | 172.4 | 15.3% |
| LoudKit turbo, M5 | 160.9 | 169.8 | 243.9 | 10.2% |
| LoudKit turbo, 5060 | 160.1 | 169.7 | 241.9 | 10.0% |

This does not turn “natural pacing” into a score. It shows that the two local
Qwen runtimes produced materially different durations despite the same prompt
panel, model family, voice, and requested seed, while LoudKit had a much faster
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
| Qwen3-TTS 1.7B 6-bit, MLX/M5 | 47.2–77.6 s | 1.32% | 0/3 | 1/3 | 101.6 |
| Qwen3-TTS 1.7B BF16, vLLM-Omni/5060 | 38.3–53.0 s | 1.32% | 0/3 | 0/3 | 128.0 |
| LoudKit turbo, M5 | 32.4–35.7 s | 1.66% | 0/3 | 0/3 | 180.1 |
| LoudKit turbo, 5060 | 31.1–36.6 s | 1.99% | 0/3 | 0/3 | 180.8 |

The pinned 117M-parameter MLX
[Streaming Sortformer](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1)
reported one speaker, no label changes, and no secondary-speaker activity in
all 12 generated passages. A local calibration smoke test also reported one
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
word loops. These are ASR-derived warnings, not proof of an acoustic defect,
and are not frontier gates yet.
The short-panel LoudKit/CUDA cap did not recur here, but three successful long
passages do not erase the retained 1/30 short-panel failure.

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

The capture therefore retains both numbers. The later fixed-seed pass used the
guarded bootstrap suppression: it did not report the silent first frame as
deliverable audio and measured 587 ms median without a hidden underrun.

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

[Nari's Qwen3-TTS engine](https://github.com/nari-labs/nari-qwen3-tts) is an
H100-specific serving implementation today: its published path uses CUDA 13,
FP8 kernels, FlashInfer, and an SM90/H100 requirement. The RTX 5060 Ti cannot
run it unchanged. One reusable idea did transfer cleanly: suppress Qwen's fixed
1,920-sample bootstrap frame only after verifying that frame is below the same
audibility threshold. That cut the M5 MLX Qwen median from roughly 147 ms to
the retained 75 ms without blindly deleting audible output.

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
winner. The single CUDA token-cap case must be understood before it can pass a
strict agent-voice gate.

## Practical deployment recommendation

For this homelab, the most promising first complete pipeline is:

```text
always-on M1: resident local ASR and TTS
              |
              +-- local or remote LLM selected per task

available M5: fastest local speech and larger local LLM experiments

RTX 5060 Ti:  CUDA audio experiments or a text model, but not both resident
              under the current near-full-VRAM vLLM-Omni configuration
```

This keeps turn-taking local and private while allowing the LLM to be remote
when its quality advantage matters. The 5060 is the fastest LoudKit host, but
the M5 is currently the fastest tested Qwen TTS host and the M1 can deliver a
163 ms median while remaining always available. A voice service should stay
outside the text-model `llama-swap` exclusivity group unless a combined memory
measurement shows that co-residency is unsafe.

## Reproducing the latency screen

The prompt file is an exact Apache-2.0 copy of CoVAL `tts-v1` 1.2.0 at commit
`c9786d181776ba393e85718e26e6b9f67fe19f7c`; its SHA-256 is
`e30909112f5fd886157008ca960c00d4f68f29de69b29d5913a5bf3383ad71ec`.
Each helper buffers output and does not play audio.

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
  --wer-capture result-long-wer.json \
  --output result-long-completion.json

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

1. Repeat stochastic TTS over a frozen prompt-by-seed panel and attach
   uncertainty intervals rather than treating four word errors as a model
   difference.
2. Add matched human same-speaker and different-speaker controls, then turn the
   preliminary long-form diarization screen into a calibrated admission gate.
3. Record matched human controls, align pauses to punctuation, and only then
   calibrate speaking-rate and pause gates.
4. Capture whole-service peak memory with one definition across MLX, PyTorch,
   and multi-process CUDA servers.
5. Measure Qwen3-ASR, Whisper, and Parakeet on a small conversational, noisy,
   technical-vocabulary, and accented panel, retaining
   both partial latency and final-transcript latency.
6. Measure the complete voice-agent clock and deterministic task success with
   local and remote LLMs. Only then publish full-pipeline frontier residents.
