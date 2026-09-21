# Media catalog structured-decision shadow pilot — 2026-09-19

## Result

Do not put Jev, Laya, OpenJev, or a SemIf-style local readout in the write path for
BookLore, Audiobookshelf, Bookshelf, or Biblioaudio yet. The useful architecture
is deterministic evidence first, model-assisted review second.

The redacted 24-case screen covers failure classes observed in the live media
work: conflicting work identifiers, neighboring series volumes, title
collisions, language and abridgement conflicts, omnibus/single-work coverage,
duplicate formats, title/author transposition, author-as-title pollution,
missing titles or language, and one row spanning multiple works. It contains no
private catalog records and makes no writes.

| Policy | Accuracy | Handled without review | Unsafe non-abstentions | p95 | Cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| Deterministic hard gate | 100% | 58.33% | 0/24 | local code | unavailable |
| Jev 1.13, six repetitions | 79.17% | 61.11% | 18/144 | 0.433 s | $0.003517 |
| Jev with 0.80 confidence fallback | 77.08% | 35.42% | 0/144 | same calls | same cost |
| Laya typed-decisions 0.3.3, six repetitions | 37.50% | 95.83% | 72/144 | 0.256 s | self-hosted |
| Laya with 0.45 confidence fallback | 41.67% | 4.17% | 0/144 | same calls | self-hosted |

The deterministic result is implementation agreement on a rule-derived oracle,
not evidence that the rules generalize to every library. It is still the right
control: Jev bought only 2.78 percentage points of extra nominal handling while
making unsafe decisions on 12.50% of observations. Raising the threshold until
the observed unsafe count reached zero reduced handled share below the hard
gate. The persistent errors were exactly the cases where catalog automation has
historically caused damage: missing metadata that still *looks* like a match,
conflicting work IDs, language conflict, and same-title collisions.

The prompt-free result summaries are
[`media-sync-jev-r6-summary.json`](media-sync-jev-r6-summary.json) for Jev and
[`media-sync-laya-r6-summary.json`](media-sync-laya-r6-summary.json) for local
Laya. The source screen is
[`media-sync-safety-screen-v1.json`](media-sync-safety-screen-v1.json),
and [`run_media_sync_baseline.py`](run_media_sync_baseline.py) is the transparent
control. A later link-by-link review found several additional, unrelated
“OpenJev” projects; the source census, identity map, and revised experiment
order are in
[`jev-landscape-audit-2026-09-19.md`](jev-landscape-audit-2026-09-19.md).
A later specialist-model intake and local checkpoint reproduction are in
[`cua-s1-specialist-intake-2026-09-19.md`](cua-s1-specialist-intake-2026-09-19.md).

## Correct deployment shape

Use ordinary code to resolve facts that are already encoded in identifiers or
catalog invariants:

1. Exact file hashes, authoritative work and edition IDs, ISBN/ASIN, media
   language, abridgement, coverage, series number, and row shape are hard
   evidence. Contradictions stop the flow.
2. A model may score only the residual ambiguity. Its output is an annotation
   on a review item, never a delete, merge, metadata update, Biblioaudio pair
   activation, or progress write.
3. Decompose the model request instead of asking for one action:
   `work_identity`, `edition_relation`, `content_compatibility`,
   `metadata_anomaly`, and `evidence_sufficiency`. Code combines those typed
   answers with the hard evidence.
4. Keep an explicit `unknown`/human-review option for every question. Confidence
   is a ranking feature until calibration on a real holdout proves a threshold.
5. Record evidence references and read back any later human-approved write from
   the owning service. HTTP success alone is not acceptance evidence.

This is useful in Biblioaudio before alignment work: rank possible ebook/audio
pairs, flag likely language/abridgement/coverage mismatches, and prioritize
manual review. It should not choose exact locators, certify an alignment map, or
move listening progress. For BookLore and Bookshelf, it can rank duplicate and
metadata-repair candidates after exact-ID and file-shape checks have run.

## Local alternatives

- **Laya is hostable but not a zero-shot media advisor.** The pinned 421M
  typed-decisions checkpoint ran entirely on an Apple M5 Max CPU at 0.256 s
  p95, but scored 37.50% with 72/144 unsafe decisions. All six repetitions were
  identical. A confidence fallback reached zero observed unsafe decisions only
  by handling one case out of 24. Its own benchmark report says the base models
  are near chance on typed decisions and the stronger checkpoint is fine-tuned
  on four synthetic workflows; a media-specific fine-tune is research, not a
  deployable default.
- **SemIf-style direct option logits** remain the first practical local control.
  They run on ordinary llama.cpp models, but the probabilities are conditional
  over the displayed options and are not calibrated confidence. The attempted
  9B run did not produce a score because the inference host's embedding workload
  continuously owned the GPU; its exclusive-runner guard correctly refused to
  evict that workload.
- **kev and Bespoke Nimble are now the first local trained-model candidates.**
  Both appeared after the initial pass, run natively on Apple Silicon, and fit
  either 64 GB Mac. `kev` provides a TypeSafe-compatible endpoint, an isolated
  trained readout head, and unusually candid in/out-of-domain evidence. Nimble
  provides a Qwen3.5-9B typed-decision LoRA and an MLX parallel scorer. Neither
  has earned a media safety claim: kev's larger checkpoints are research
  previews, and Nimble explicitly says its option probabilities are not
  calibrated correctness.
- **OpenJev as distributed** is not currently a 16 GB-card deployment. The
  [vLLM recipe](https://github.com/vllm-project/recipes/blob/main/models/Google/diffusiongemma-26B-A4B-it.yaml)
  lists 24 GB minimum for both NVFP4 variants, and
  [OpenJev](https://github.com/razorback16/openjev) documents the same minimum.
  That rules out the CUDA image on the RTX 5060 Ti, but it does **not** rule out
  the model locally. Unsloth publishes DiffusionGemma GGUFs from roughly 16 GB
  (Q4_K_M) through 25 GB (Q8_0), and the llama.cpp DiffusionGemma pull request
  documents running them with the dedicated `llama-diffusion-cli`. Both the M5
  Max MacBook and M1 Max Mac Studio have 64 GB unified memory, so Q4 through Q8
  have ample weight-fit headroom. A real load test is still required to measure
  context/KV overhead and useful throughput on each machine.
- **Apple capacity is not Apple readout support.** The currently installed
  MLX-LM 0.31.3 rejects the `diffusion_gemma` model type, vLLM-Metal does not
  list DiffusionGemma among its supported families, and the GGUF route currently
  depends on the draft
  [llama.cpp DiffusionGemma pull request](https://github.com/ggml-org/llama.cpp/pull/24423)
  plus its dedicated CLI. The normal `llama-cli`/`llama-server` path cannot yet
  generate from these GGUFs, and `llama-diffusion-cli` does not expose OpenJev's
  seeded-canvas, fixed-slot, read-only-step probability contract. The Macs are
  therefore credible local generation and port/prototype hosts, not drop-in
  OpenJev hosts today.
- The enabling [vLLM structured-readout pull request](https://github.com/vllm-project/vllm/pull/57250)
  was open, blocked, and had a failing pre-commit check when this result was
  captured. OpenJev therefore pins a fork and provisional request fields. Treat
  the issue's statement that the change was merged as incorrect until upstream
  state changes.
- **system-one-open** is useful research and includes a much smaller 270M tier,
  but its published Gemma 4 E2B result trails Jev on its common subset and its
  current workflow is Modal-first. It is a candidate for an offline quality
  control, not yet the home deployment default.
- **CUA-S1-FORMS validates the specialist pattern, not media transfer.** Its
  published 706k-parameter checkpoint loaded successfully and reproduced
  196/196 top-1 decisions on the publisher's real-demo rows, with 2.57 ms CPU
  p95 on the M5 Max. That set has only 196 decisions, 150 of them `skip`, and
  was published by the model author. The useful next move is a comparably tiny
  media-specific option scorer trained on adjudicated incidents, with a
  source- or work-family-disjoint holdout—not sending catalog cases to this
  form model.

## Next experiment

Build a review-only holdout from already-adjudicated catalog incidents. Preserve
the observed class balance and include contrast pairs where exactly one fact
changes. Measure review-order precision and recall—not autonomous write rate—at
three budgets: top 5%, top 20%, and all flagged items. Run the same packets
through hosted Jev, kev-4B, Bespoke Nimble 9B, and SemIf/Gemma direct-logit
controls. Keep the completed Laya result as a negative baseline. Separately,
smoke-test the 16 GB DiffusionGemma Q4_K_M GGUF with `llama-diffusion-cli` on the
M5 Max; do not block the trained Mac-model comparison on implementing its
missing structured-readout contract. Promote a model only if it improves
reviewer yield over deterministic ranking without hiding any hard contradiction.

If the adjudicated incident set becomes large enough for training, add one
specialist track with a frozen action contract such as duplicate disposition
or Biblioaudio compatibility. Split it by catalog source, work family, or
incident lineage rather than random row; report out-of-scope abstention and
verified reviewer outcomes separately from in-scope top-1 accuracy.
