# SWE-bench bash-only quality evidence

ModelSkyline's SWE-bench collector consumes one exact cohort from the official
SWE-bench website data file: the `bash-only` leaderboard rows produced by
mini-SWE-agent `2.0.0`. It does not ingest the broader Verified leaderboard,
compare different mini-SWE-agent generations, or turn a display name into a
routable model offering.

The default source is pinned to an exact commit and SHA-256. The collector
downloads only from the allowlisted HTTPS host, rejects redirects and compressed
responses, imposes byte and structural limits, and verifies the source digest.
Local captures require an explicit source revision and retrieval timestamp.
They also require the exact expected SHA-256; arbitrary remote mirrors are not
accepted. A non-default capture is conservatively raw-bound in semantic source
identity and receives `NOASSERTION` rights unless the embedding operator supplies
an explicit reviewed assertion. It cannot masquerade as a result-only refresh of
the registered official snapshot.

## Capture

Run the immutable live capture:

```console
uv run modelskyline capture-swe-bench-bash-only ./swe-bench-capture
```

Or normalize separately acquired bytes without network access:

```console
uv run modelskyline capture-swe-bench-bash-only ./swe-bench-capture \
  --source ./leaderboards.json \
  --source-revision ac7583972e21606e9dad4447a9c447685c03b57a \
  --expected-sha256 fa4b61d3167dfe99e1a834e007a38372c5bac07b7627f8e2c3904fb48cd4a006 \
  --retrieved-at 2026-08-31T22:00:00Z
```

The output directory is private (`0700`) and its three files are `0600` on
POSIX systems:

- `quality-evidence.json` is the generic route-free evidence contract;
- `inventory.json` is the bounded local mapping-review inventory;
- `capture.json` records source, rights, cohort, counts, warnings, and filenames.

Raw upstream bytes and individual task identifiers are not copied into the
bundle. The evidence retains a digest of the exact 500-task set and a separate
digest of each row's sorted task outcomes.

At the pinned source snapshot, the collector sees 13 mini-SWE-agent `2.0.0`
rows. Eleven have 500 coherent per-instance records. One is quarantined because
its aggregate score disagrees with its task outcomes, and one because it omits
per-instance details. These counts are asserted by the opt-in live-source test;
a future source revision must be reviewed and repinned rather than silently
changing the fixture.

## Validation and identity boundaries

For every selected row the collector:

1. requires the reviewed row field contract and a stable `bash-only/<folder>`
   row identifier;
2. requires exactly 500 task records with the same task-set digest across the
   cohort;
3. validates boolean outcomes, non-negative Decimal cost, and non-negative
   integer API-call counts;
4. recomputes resolved percentage from the task outcomes;
5. quarantines a score mismatch, while an accounting-only mismatch preserves
   the verified quality score but drops cost and API-call measurements.

Five identities change independently:

- raw-audit identity changes for any byte change;
- source identity changes when the selected harness, task set, scorer, or
  projection contract changes;
- subject identity changes when the row's model/system/harness claims change;
- result identity changes when outcomes or measurements change;
- rights identity changes when the reviewed license assertion changes.

An unrelated leaderboard edit therefore changes the raw audit without
invalidating reviewed mappings for an unchanged `bash-only` cohort. A result
refresh does not require remapping an unchanged subject. Task-set, harness,
projection, or subject drift fails the applicable identity pin.

## Exact reviewed reconciliation

The website provides model and organization labels, not a complete provider
route. Every row therefore has `route_disclosure: "unknown"`. Use the inventory
to review the row and author a generic reconciliation entry with:

- the exact row ID;
- adapter ID `model-skyline/swe-bench-website-bash-only` and projection version
  `1`;
- the inventory's exact source- and subject-identity SHA-256 values;
- `relationship: "reviewed_quality_projection"`;
- every field of one complete `OfferingKey`, including explicit nulls;
- human review evidence and a timezone-aware review timestamp.

Then reconcile without fuzzy matching:

```console
uv run modelskyline reconcile-quality-evidence \
  ./swe-bench-capture/quality-evidence.json \
  ./reviewed-reconciliation.json \
  --publication-scope internal \
  --output ./swe-bench-import-report.json
```

Project only the reviewed rows into the ordinary catalog boundary:

```console
uv run modelskyline project-quality-catalog \
  ./swe-bench-capture/quality-evidence.json \
  ./reviewed-reconciliation.json \
  ./swe-bench-import-report.json \
  --workload-id swe-bench/bash-only/mini-swe-agent-v2 \
  --workload-unit issue \
  --output ./swe-bench-quality-catalog.json
```

The projector replays the reviewed reconciliation and requires the supplied
import report to match it byte-for-byte at the model level; the report alone is
not routing authority. A file output is private (`0600` on POSIX) and is not
overwritten unless `--overwrite` is explicit. Source and terms URLs are derived
from the evidence's raw-audit and rights locators rather than caller input. The
derived route-bearing source retains the evidence's descriptive license
expression and publication permission as explicit metadata, but neither becomes
public-redistribution authority. The durable categorical marker remains the
publication boundary. The
catalog workload version is the semantic
source-identity SHA-256, so a
task-set, scorer, harness, or projection change cannot join an older workload.
The exact capture digest and retrieval time remain in its source descriptor.
The projected catalog contains complete reviewed `OfferingKey` values and
quality measurements, but no leaderboard labels, task IDs, raw bytes, or
benchmark accounting fields. It remains marked `publication_safe: false`;
the public publisher rejects that marker even when a license or source override
is supplied. Producing a catalog does not grant redistribution rights.

Case folding, family matching, prefixes, aliases, and inferred providers are not
join strategies. An ambiguous row, multi-model system, mutable alias, identity
drift, invalid result, duplicate target, or missing reviewed entry remains
unmapped or quarantined. A reviewed quality projection copies only quality-role
measurements and counts. The source's cost and API-call fields are not attributed
to the production offering.

## Using the score in a cost/performance policy

`swe_bench_resolved_percent` is one workload-bound quality signal, not a global
model score. Build its component frontier only after exact reconciliation and
an exact `OfferingKey` join to an independently sourced competing metric, such
as current cache-aware cost per repository issue or observed end-to-end issue
latency. Historical leaderboard cost is not a substitute for that current route
measurement.

For a typical general-agent policy, keep two to four benchmark components
separate—commonly SWE-bench for repository coding, Harbor/Terminal-Bench for
terminal work, tau2-bench for stateful tool/policy work, and optionally the
pinned ARC-AGI-2 public-evaluation evidence or a better-attested local run for
abstract reasoning. `PortfolioPolicy` requires explicit coverage and exact
complete offering equality, then emits each component as a separate catalog
signal. An optional scalar composite is an ordinary `FormulaMetric` with an
explicit versioned normalization and weighting policy.

## Rights and limitations

The official website repository declares CC-BY-NC-4.0 at the pinned revision.
The adapter records that assertion but defaults publication permission to
`unknown`, so derived/full publication fails until an operator completes a
rights review appropriate to the deployment. The raw website file is never
vendored.

This is an unversioned website presentation artifact. It does not establish the
historical SWE-bench dataset commit, evaluator commit, provider endpoint,
billing mode, region, service tier, or current availability for a row. The
collector preserves those gaps instead of filling them from names. Its source
pin is intentionally manual. The scheduled `check-swe-bench-feed` workflow polls
the supported GitHub revision API, normalizes the latest file entirely in
memory, and reports whether only raw bytes, results, subjects, the row set, or
the semantic source identity changed. It retains and publishes no raw data or
model labels. The public Actions summary contains only the change class, action,
and source attribution; detailed hashes, counts, and quarantine reasons remain
in an ephemeral runner-local status file. A semantic change fails the monitor
so a maintainer can capture, diff, validate, rights-review, and deliberately
repin it; it never commits or publishes an upstream change automatically.
