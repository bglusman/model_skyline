# ARC-AGI-2 public-evaluation evidence

ModelSkyline has a pinned, fail-closed library adapter for the official
[ARC-AGI-2 public-evaluation result dataset][hf-dataset]. It turns the reviewed
`results.json` summaries into route-free quality evidence. It does not infer a
verified provider, model identity, endpoint, or `OfferingKey` from a result
folder name; the label is retained only as an explicitly unreviewed claim.

This is a benchmark evidence source, not a model catalog. An operator must
separately review and sign an exact reconciliation before a score can influence
a production offering.

## Fixed source

The version-1 adapter fixes every mutable upstream input that it reads:

| Component | Reviewed value | Meaning |
| --- | --- | --- |
| Hugging Face dataset | `arcprize/arc_agi_v2_public_eval` | Result publication |
| Result revision | `026789c1c12a4c34580a32e84dcaf5630d7e8f31` | Exact HF repository revision |
| HF `lastModified` | `2026-06-04T16:45:03Z` | Upload/publication observation, not run time |
| Summary paths | 32 fixed `*/results.json` paths | Exact reviewed file cohort |
| Expected tasks | 120 | Required in every admitted row |
| Task-ID digest | `54ca25cdc4444e5669e272e25cbe301bbfe3aa81da8f555126095153aff69425` | SHA-256 of sorted task IDs, each followed by `\n` |
| Reference task repository | `arcprize/ARC-AGI-2@f3283f727488ad98fe575ea6a5ac981e4a188e49` | Expected current task source; not asserted as the historical run input |
| Reference evaluation tree | `8d04288aac3146b7c47d0b799c18bc9c0217d838` | Current `data/evaluation` Git tree; reference only |
| Reference harness | `arcprize/arc-agi-benchmarking@28e67d54b05df5be10281892243c509a42a874f1` | Schema/methodology reference; not asserted as the historical run harness |

The collector first reads the revision metadata API and requires the exact
dataset ID, revision, public/ungated state, MIT card tag, publication time, and
32-path result cohort. It then reads only those 32 summary files through fixed,
percent-encoded `api/resolve-cache` URLs and requires the exact
`X-Repo-Commit` response header. Redirects and content encoding are rejected.
The cache endpoint is a Hugging Face implementation endpoint rather than its
public `/resolve/` spelling; this dependency is deliberately pinned and tested.
The CLI timeout is one aggregate deadline for revision metadata and all 32
summaries, not a fresh timeout for every request.

The Hugging Face Dataset Viewer/Parquet API is not used. At the reviewed
revision it cannot unify the historical result schemas and returns a schema
cast error.

## Admission and quarantine

Source metadata drift is a capture-level failure. Each individual result row is
otherwise admitted or quarantined independently.

A valid quality row must:

- be bounded UTF-8 JSON with no duplicate keys, non-finite numbers, excessive
  nesting, excessive structural nodes, or oversized strings;
- contain only reviewed result and per-task fields;
- declare and contain exactly 120 task results;
- contain the exact pinned task-ID set;
- contain a finite decimal score from zero through one for every task; and
- have a top-level score equal to the independently recomputed sum of per-task
  scores.

The normalized primary metric is:

```text
arc_agi_2_public_eval_score_percent =
    100 * sum(per_task_score) / 120
```

The upstream aggregate is a sum, not a percentage. The adapter never trusts it
as the emitted value.

Incomplete cohorts, changed task IDs, aggregate mismatches, unknown schema
fields, and malformed quality values become bounded `QualityInvalidResult`
records. They do not disappear from the inventory and cannot become quality
measurements.

### Accounting is optional evidence

Historical cost, token, duration, attempt, and empty-output fields are checked
dimension by dimension against their per-task values. A coherent dimension is
retained as route-free telemetry. A missing, malformed, or inconsistent
dimension is omitted without discarding independently valid quality.

In particular, `total_cost = 0` is treated as unknown unless a separate source
proves that the run was actually free. Historical benchmark cost is never
production route pricing and does not become a cost frontier input through
model-name inference.

All numeric policy and accounting work uses `Decimal`; the adapter does not use
binary floating-point money arithmetic.

## Identity and mapping boundary

The adapter deliberately keeps these identities independent:

- **Raw capture:** exact revision metadata and 32 result-file byte digests.
- **Result publication:** the immutable Hugging Face repository revision.
- **Semantic source:** the observed task-ID cohort and projection contract,
  conservatively pinned to the immutable HF revision. Because task bytes are
  unattested, a future same-ID publication must receive a fresh source review
  rather than silently inheriting an old mapping.
- **Observed cohort:** the exact 120 task-ID set and score projection.
- **Task content:** `unattested`. The summaries contain IDs but no task-content
  revision. Those IDs remained stable across known task-content edits.
- **Evaluator harness:** `unattested`. The summaries contain no harness commit
  or run configuration. The pinned harness commit is reference material only.
- **Attempt protocol:** `unattested`. Reported attempt totals may be retained as
  telemetry, but the adapter does not claim a fixed attempts-per-input policy.
- **Subject:** a single-model-system claim whose only model claim is the opaque
  dataset folder label, explicitly marked `dataset_path_only` and unreviewed.
  Provider, endpoint, revision, and route remain unknown.
- **Normalized result:** recomputed quality plus only coherent optional
  accounting. For the registered network capture this remains a separate digest
  domain; operator-supplied captures additionally bind all raw file digests into
  source identity as a provenance safeguard.

This avoids a dangerous but tempting shortcut: parsing a label such as
`gpt-5-2-...` into a provider/model route. Folder labels are claims without the
endpoint, provider account, region, service tier, quantization, billing mode,
or run attestation needed for an exact offering identity.

To use a row in a catalog, create an operator-reviewed
`QualityReconciliationEntry` that pins the adapter version, source-identity
digest, subject-identity digest, relationship, and complete `OfferingKey`.
Mutable aliases and fuzzy/string-similarity matching remain ineligible.

## Upstream revision monitor

The metadata-only monitor checks whether the public Hugging Face dataset head
still equals the reviewed revision without fetching result summaries, attempts,
task IDs, model labels, prompts, or answers:

```console
uv run modelskyline check-arc-agi-2-feed
```

It exits `3` after rendering a bounded status document when the head differs.
Use `--report-only` when another process will interpret `review_required`.
Every head change requires manual review; the monitor never repins the adapter
or reuses its source semantics automatically. The scheduled repository workflow
uses the same command and publishes only the compact metadata status.

## Library use

Capture the registered fixed source from the CLI:

```console
uv run modelskyline capture-arc-agi-2-public-eval \
  ./private-captures/arc-agi-2-public-eval
```

The command has no “latest” or arbitrary-URL mode. Updating the immutable
revision is an adapter review and versioning operation, not an unattended data
refresh.

```python
from pathlib import Path

from model_skyline.adapters.arc_agi import (
    capture_arc_agi_public_eval,
    write_arc_agi_public_eval_capture,
)

capture = capture_arc_agi_public_eval()
write_arc_agi_public_eval_capture(
    capture,
    Path("private-captures/arc-agi-2-public-eval"),
)

print(capture.rows_seen, capture.valid_rows, capture.invalid_rows)
```

Only `capture_arc_agi_public_eval()` receives registered-source semantics. It
uses the allowlisted network loader in the same call path and records
`https-get-fixed-hf-revision-multifile` acquisition. The public byte and
`LoadedArcAgiSource` normalization functions are intentionally for
operator-supplied material: they record an operator locator/method, bind the
complete multifile capture digest into semantic source identity, and default to
`NOASSERTION` rights. Consequently, edited or fabricated bytes cannot inherit a
previous official reconciliation unless an operator explicitly reviews the new
raw-bound source identity. Supplying a custom rights assertion is an explicit
operator action, not an inferred adapter default.

The output directory is atomically published with mode `0700`; its three files
use mode `0600` on POSIX:

- `quality-evidence.json` contains normalized, route-free evidence;
- `inventory.json` contains row status and content identities; and
- `capture.json` contains source pins, file digests, counts, warnings, and
  output hashes.

The bundle does not contain source result JSON, per-attempt documents, prompts,
answers, raw task IDs, or per-task outcomes. It retains only aggregate
measurements and non-reversible digests needed for audit and change detection.
The network collector never requests the adjacent attempt files.

Publication permission always defaults to `UNKNOWN`. For the registered network
capture, the HF result card and reference harness declare MIT while ARC-AGI-2
task data declares Apache-2.0. Operator-supplied normalization defaults its
license expression to `NOASSERTION`. Operators must review the dataset card,
Hugging Face terms, attribution, provider-output rights, acquisition provenance,
and the intended projection before publishing derived or full output.

## Reviewed live result

At the pinned revision, a live capture on 2026-08-31 produced 32 rows: 22 valid
quality rows and 10 quarantined incomplete cohorts. The incomplete rows contain
90, 107, 109, 116, 117, or 119 tasks.

One otherwise complete row reports 328 attempts, while the other 21 complete
rows report 334. The current harness defaults and dataset presentation suggest
two attempts for each of the 167 public-evaluation test pairs, but the result
summaries do not attest that policy and upstream documentation is internally
inconsistent. Version 1 therefore preserves a coherent reported attempt total
as telemetry and leaves attempt policy unattested. A policy that requires an
exact two-attempt protocol must add reviewed run sidecars or quarantine rows
that do not prove 334 attempts.

The adapter is intentionally pinned rather than “latest.” A new upstream
revision requires a fresh review of result paths, schemas, cohort/task-content
identity, scoring behavior, licensing, and any available harness/run
attestations before constants or adapter version are changed. The head monitor
makes that review condition observable; it does not make the update safe by
itself.

[hf-dataset]: https://huggingface.co/datasets/arcprize/arc_agi_v2_public_eval
