# Local speech-recognition frontiers

## The short answer

Across the three tested machines, **Parakeet TDT 0.6B v3 is the current local
ASR default**. Its MLX offering on the M5 is the sole exact resident on the
combined quality/speed frontiers, and the simpler model-level views agree.
Its compiled encoder offering is also the sole point-estimate quality/speed
resident when the RTX 5060 Ti is considered alone.

If minimizing memory matters more than obtaining the best point WER,
**Qwen3-ASR 0.6B 8-bit is the other useful Mac tradeoff**. It used about 1.15 GB of
process RSS versus about 2.52 GB for Parakeet. Qwen3-ASR 1.7B and Whisper
large-v3-turbo are dominated on both Macs. The plain 5060 baseline initially
kept Qwen3-ASR 1.7B as a quality tradeoff; a validated Parakeet encoder-compile
profile is both faster and slightly better on this panel, so Qwen leaves the
point-estimate frontier once optimized offerings are admitted.

That is a point-estimate decision, not proof that Parakeet is universally more
accurate. The WER confidence intervals overlap because this first frozen panel
contains only 24 utterances and 610 reference words. Parakeet's latency lead is
large and statistically clear; its quality lead needs a larger panel.

## Exactly what “best” means here

Each frontier has two dimensions. Requirements such as a nonempty transcript
are pass/fail gates, not hidden scoring dimensions.

| Frontier | First dimension | Second dimension | M1 residents | M5 residents | 5060 residents |
|---|---|---|---|---|---|
| Responsive intelligibility | WER, lower is better | p95 final-transcript latency, lower is better | Parakeet | Parakeet | compiled Parakeet |
| Typical intelligibility | WER, lower is better | median final-transcript latency, lower is better | Parakeet | Parakeet | compiled Parakeet |
| Batch intelligibility | WER, lower is better | audio seconds processed per wall second, higher is better | Parakeet | Parakeet | compiled Parakeet |
| Small resident | WER, lower is better | peak process RSS, lower is better | Qwen 0.6B; Parakeet | Qwen 0.6B; Parakeet | not ranked: no comparable memory signal |

The exact executable definitions are in
[`asr-frontier.yaml`](asr-frontier.yaml). The all-hardware catalog is
[`asr-observations.json`](asr-observations.json); the M1-only, M5-only, and
5060-only catalogs make the same comparison for a reader who already owns one
machine.
Generated exact snapshots and simpler model views are in [`generated/`](generated/).

“Final-transcript latency” starts after the complete audio file is available
and ends when the resident model returns its final transcript. It is not a full
voice-agent latency measurement. A live system also pays for audio capture,
end-of-turn detection, and any streaming/finalization policy.

## Measured points

Every row uses batch size one, declared unscored warmup calls, the same exact
24 audio files in the same order, and the pinned model revision recorded in the catalog.
The Macs use MLX 0.32.2 and MLX-Audio 0.5.4. The 5060 uses Transformers 5.17,
PyTorch 2.13, CUDA 13, and BF16 weights. No audio was played.

### M5 Max 64 GB

| Exact artifact | WER | p50 final | p95 final | Corpus RTF | Peak RSS |
|---|---:|---:|---:|---:|---:|
| Parakeet TDT 0.6B v3 FP16 | **8.20%** | **38.7 ms** | **59.2 ms** | **242.1x** | 2,524 MB |
| Qwen3-ASR 0.6B 8-bit | 8.85% | 108.0 ms | 138.3 ms | 87.2x | **1,152 MB** |
| Qwen3-ASR 1.7B 8-bit | 8.69% | 202.8 ms | 263.8 ms | 47.7x | 2,544 MB |
| Whisper large-v3-turbo FP16 | 10.33% | 135.0 ms | 155.4 ms | 71.3x | 1,702 MB |

### M1 Max 64 GB

| Exact artifact | WER | p50 final | p95 final | Corpus RTF | Peak RSS |
|---|---:|---:|---:|---:|---:|
| Parakeet TDT 0.6B v3 FP16 | **8.20%** | **80.0 ms** | **109.1 ms** | **115.4x** | 2,520 MB |
| Qwen3-ASR 0.6B 8-bit | 9.02% | 212.6 ms | 280.1 ms | 43.3x | **1,154 MB** |
| Qwen3-ASR 1.7B 8-bit | 8.52% | 385.9 ms | 529.9 ms | 23.9x | 2,543 MB |
| Whisper large-v3-turbo FP16 | 10.33% | 413.5 ms | 450.3 ms | 23.1x | 1,696 MB |

### RTX 5060 Ti 16 GB

| Exact artifact | WER | p50 final | p95 final | Corpus RTF | Peak CUDA allocation |
|---|---:|---:|---:|---:|---:|
| Parakeet BF16, static 16 s, compiled encoder | **8.36%** | **60.6 ms** | **74.1 ms** | **157.8x** | **1.33 GB** |
| Parakeet TDT 0.6B v3 BF16 baseline | 9.02% | 68.1 ms | 81.5 ms | 140.7x | 1.34 GB |
| Qwen3-ASR 0.6B BF16, dynamic compile | 8.85% | 189.6 ms | 273.2 ms | 47.9x | 1.77 GB |
| Qwen3-ASR 0.6B BF16 baseline | 9.18% | 345.0 ms | 448.3 ms | 29.2x | 1.72 GB |
| Qwen3-ASR 1.7B BF16, dynamic compile | 8.52% | 357.4 ms | 534.4 ms | 25.8x | 4.28 GB |
| Qwen3-ASR 1.7B BF16 baseline | 8.52% | 410.4 ms | 583.0 ms | 23.7x | 4.23 GB |
| Whisper large-v3-turbo BF16 | 10.49% | 122.8 ms | 140.2 ms | 78.7x | 1.70 GB |

The compiled Parakeet point dominates every other 5060 offering on all three
quality/speed definitions. This remains a point-estimate result: its 5.04–12.09%
WER interval overlaps Qwen 1.7B's 4.84–12.77% interval. This is not an exact
hardware-only comparison with the Macs: the CUDA artifacts, precision, and
runtime differ from the MLX offerings.
The raw CUDA captures retain both process RSS and CUDA allocator peaks, but
neither is published as the cross-platform memory axis. Doing so would make an
allocator statistic look comparable to macOS process RSS when it is not.

The model-focused result is therefore unusually simple: Parakeet occupies all
three combined quality/speed frontiers, while Parakeet and Qwen 0.6B occupy the
Mac quality/memory frontier. The exact view adds an important deployment clue:
on the 5060, tuning collapses a two-model baseline frontier to one resident.

Both model-level reductions agree. The **best-available** view asks whether a
model's single best tested implementation survives the combined exact
frontier. The **balanced-average** view averages each quality/speed dimension
across M1, M5, and 5060 so every machine counts equally. For the memory
frontier it averages only the two Macs, where the measurement is comparable.
Neither reduction changes the model residents above. That agreement is useful
evidence that the recommendation is not just an artifact of selecting the M5.

## What compilation changed

The optimization profiles are exact offerings, not edits to the baseline rows.
[Qwen's model card](https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf#torch-compile)
documents forward compilation and reports a batch-four A100 gain. For this
variable-length batch-one workload, default compilation repeatedly specialized
new input shapes and reduced total throughput to 7.6x realtime. Starting with
dynamic shapes avoided that failure: Qwen 0.6B improved by 1.84x at p50, 1.65x
at p95, and 1.64x in corpus throughput. Qwen 1.7B improved by only 1.15x, 1.09x,
and 1.09x respectively.

Compilation is not free. The first measured Qwen dynamic-compile warmup took
about 94 seconds; a second process with compiler artifacts cached still took
about 40 seconds. It therefore makes sense for a long-lived resident service,
not a router that unloads the model frequently.

[Transformers' Parakeet guide](https://huggingface.co/docs/transformers/model_doc/parakeet#making-the-model-go-brrr)
shows full-generation compilation with static padding for a CTC model. The TDT
generation wrapper in Transformers 5.17 is not full-graph traceable, so this
profile pads to 16 seconds and compiles only the FastConformer encoder. Static
padding alone improved p50 to 62.7 ms without changing WER. Encoder compilation
reached 60.6 ms and changed two of 24 transcripts, for four fewer net word
errors. A complete repeat reproduced the same transcripts, 8.36% WER, 60.6 ms
p50, and 74.2 ms p95. Ten warmups were required; three left two compilation
outliers in the scored tail.

Those numerical changes are part of the exact runtime result, not evidence that
compilation generally improves recognition. A larger panel is needed before
treating the four-word change as anything but provisional.

## What the two Macs teach us

The M5 was about 1.9–2.1 times faster for Qwen and Parakeet, but about 3.1 times
faster for Whisper. Peak process RSS differed by less than 0.4% for every exact
pair. Equal 64 GB capacity therefore produced the same fit choices and the same
frontier residents, but not the same latency.

| Exact artifact | M5 p50 speedup | M5 p95 speedup | M5 throughput speedup |
|---|---:|---:|---:|
| Qwen3-ASR 0.6B 8-bit | 1.97x | 2.02x | 2.01x |
| Qwen3-ASR 1.7B 8-bit | 1.90x | 2.01x | 1.99x |
| Parakeet TDT 0.6B v3 FP16 | 2.07x | 1.84x | 2.10x |
| Whisper large-v3-turbo FP16 | 3.06x | 2.90x | 3.09x |

This is a whole-machine control, not a CPU-only benchmark. M1 Max versus M5
Max changes CPU, GPU, Metal kernels, memory subsystem, and OS together. The
model-specific multipliers reinforce the broader local-model result: there is
no honest universal “M5 is N times faster than M1” conversion.

Qwen and Parakeet each differed by one normalized word across the two chips;
Whisper was identical. Repeating the M5 run produced the same Qwen and
Parakeet transcripts. These tiny differences are consistent with
non-bitwise-identical accelerator execution and are far smaller than the
panel's sampling uncertainty. The machine comparison is generated by
[`compare_asr_macs.py`](compare_asr_macs.py) and retained in
[`raw/m1-m5-local-asr-pilot-v1-comparison.json`](raw/m1-m5-local-asr-pilot-v1-comparison.json).

## Quality limits and domain clues

The paired, domain-stratified bootstrap resamples utterances—not individual
words—10,000 times. On the M5, the 95% WER intervals are 4.94–11.93% for
Parakeet, 4.54–13.82% for Qwen 0.6B, 4.90–13.03% for Qwen 1.7B, and
5.51–16.77% for Whisper. Every pairwise WER difference includes zero.

Aggregate WER also hides workload differences. Parakeet beat Qwen 0.6B on the
financial-call and audiobook slices, while Qwen 0.6B had fewer errors on the
meeting and political-speech slices. Each slice has only six clips, so these
are leads for the next panel—not separate leaderboard claims.

The frozen panel draws six 5–15 second English clips from each of four public
Open ASR Leaderboard dataset configurations:

- corrected AMI meetings (CC-BY-4.0);
- Earnings22 calls (CC-BY-SA-4.0);
- LibriSpeech `test.other` (CC-BY-4.0); and
- corrected VoxPopuli speech (CC0-1.0).

The exact dataset revision, row IDs, source hashes, references, and audio
normalization are in
[`prompts/local-asr-pilot-v1.json`](prompts/local-asr-pilot-v1.json). Common
Voice is deliberately excluded because the aggregate package asks the
downloader to accept separate terms; this experiment did not silently accept
them.

## Reproducing and extending the pilot

[`prepare_asr_panel.py`](prepare_asr_panel.py) deterministically selects and
normalizes the local audio. [`bench_mlx_asr.py`](bench_mlx_asr.py) and
[`bench_transformers_asr.py`](bench_transformers_asr.py) apply the same clock
on MLX and CUDA, verify all hashes, and retain per-utterance transcripts,
errors, latency, and runtime-specific memory statistics.
[`analyze_asr_pilot.py`](analyze_asr_pilot.py) produces the paired bootstrap.
[`build_asr_observations.py`](build_asr_observations.py) refuses stale
capture/bootstrap bindings and generates the four catalogs.

The next measurements should be:

1. a cold-start/swap-ready frontier with a controlled compiler-cache state;
2. a live-streaming panel that measures partial text, finalization, and
   endpointer delay;
3. a larger English panel with noise, accents, long-form speech, and local
   microphone conditions; and
4. separate multilingual workloads rather than averaging languages into one
   opaque score.

Those additions may introduce new frontier residents. They should not be
projected from this pilot with a generic hardware multiplier.
