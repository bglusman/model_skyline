# Media catalog structured-decision shadow pilot — 2026-09-19

## Result

Do not put Jev, OpenJev, or a SemIf-style local readout in the write path for
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
| Jev 1.13, six repetitions | 79.17% | 63.19% | 17/144 | 0.396 s | $0.003517 |
| Jev with 0.80 confidence fallback | 78.47% | 36.81% | 0/144 | same calls | same cost |

The deterministic result is implementation agreement on a rule-derived oracle,
not evidence that the rules generalize to every library. It is still the right
control: Jev bought only 5.86 percentage points of extra nominal handling while
making unsafe decisions on 11.81% of observations. Raising the threshold until
the observed unsafe count reached zero reduced handled share below the hard
gate. The persistent errors were exactly the cases where catalog automation has
historically caused damage: missing metadata that still *looks* like a match,
conflicting work IDs, language conflict, and same-title collisions.

The prompt-free result summary is
[`media-sync-jev-r6-summary.json`](media-sync-jev-r6-summary.json). The source
screen is [`media-sync-safety-screen-v1.json`](media-sync-safety-screen-v1.json),
and [`run_media_sync_baseline.py`](run_media_sync_baseline.py) is the transparent
control.

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

- **SemIf-style direct option logits** remain the first practical local control.
  They run on ordinary llama.cpp models, but the probabilities are conditional
  over the displayed options and are not calibrated confidence. The attempted
  9B run did not produce a score because the inference host's embedding workload
  continuously owned the GPU; its exclusive-runner guard correctly refused to
  evict that workload.
- **OpenJev on DiffusionGemma** is not currently a 16 GB-card deployment. The
  [vLLM recipe](https://github.com/vllm-project/recipes/blob/main/models/Google/diffusiongemma-26B-A4B-it.yaml)
  lists 24 GB minimum for both NVFP4 variants, and
  [OpenJev](https://github.com/razorback16/openjev) documents the same minimum.
  The available host has an RTX 5060 Ti with 16 GB VRAM and 10 GB system RAM.
- The enabling [vLLM structured-readout pull request](https://github.com/vllm-project/vllm/pull/57250)
  was open, blocked, and had a failing pre-commit check when this result was
  captured. OpenJev therefore pins a fork and provisional request fields. Treat
  the issue's statement that the change was merged as incorrect until upstream
  state changes.
- **system-one-open** is useful research and includes a much smaller 270M tier,
  but its published Gemma 4 E2B result trails Jev on its common subset and its
  current workflow is Modal-first. It is a candidate for an offline quality
  control, not yet the home deployment default.

## Next experiment

Build a review-only holdout from already-adjudicated catalog incidents. Preserve
the observed class balance and include contrast pairs where exactly one fact
changes. Measure review-order precision and recall—not autonomous write rate—at
three budgets: top 5%, top 20%, and all flagged items. Run the same packets
through hosted Jev, a co-resident small local direct-logit model, and OpenJev on
a documented >=24 GB host. Promote a model only if it improves reviewer yield
over deterministic ranking without hiding any hard contradiction.
