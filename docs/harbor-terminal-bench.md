# Harbor Terminal-Bench quality evidence

ModelSkyline's Harbor adapter consumes a **captured local JSON response** from
`harbor hub leaderboard show ... --json`. It never executes a command supplied
by project configuration and it never turns a leaderboard model label into a
provider route by name matching.

The supported first projection is the public
`terminal-bench/terminal-bench/4-0-0` shape exposed by Harbor 0.22.0. The parser
version is `harbor-terminal-bench-leaderboard-json@2`. It is allowlisted and
bounded; it rejects duplicate JSON members, excessive input, unexpected
board/scoring/release-date contracts, incoherent trial accuracy, unsafe URLs,
duplicate row UUIDs, impossible timestamps, and incomplete/unknown result
states.

## Collection and review

Pin the collector independently of ModelSkyline and record the actual retrieval
time:

```console
uvx --from harbor==0.22.0 harbor hub leaderboard show \
  terminal-bench/terminal-bench/4-0-0 --json > terminal-bench-4.0.json

# Start with reviewed acquisition/rights metadata and no route assertions.
# The reconciliation entries array in bootstrap-import-config.json is empty.

modelskyline inspect-harbor-terminal-bench terminal-bench-4.0.json \
  bootstrap-import-config.json \
  --retrieved-at 2026-08-31T18:07:29Z \
  --output terminal-bench-4.0-inventory.json
```

The bootstrap configuration has this shape. URLs, rights status, review evidence,
and timestamps are operator assertions; replace the examples with assertions you
actually reviewed.

```json
{
  "schema_version": "model-skyline/harbor-terminal-bench-import-config/v1alpha1",
  "source_url": "https://www.harborframework.com/docs/hosted-harbor/cli-leaderboards",
  "methodology_url": "https://www.tbench.ai/docs",
  "capture_tool": "harbor",
  "capture_tool_version": "0.22.0",
  "publication_scope": "internal",
  "rights": {
    "license_expression": "NOASSERTION",
    "terms_locator": "https://hub.harborframework.com/terms",
    "publication_permission": "unknown",
    "reviewed_at": "2026-08-31T18:07:29Z",
    "review_evidence": "Upstream redistribution rights have not been established.",
    "metadata": {}
  },
  "reconciliation": {
    "schema_version": "model-skyline/quality-reconciliation/v1alpha1",
    "entries": []
  }
}
```

The inventory contains stable source-identity and subject-identity digests for
review. A mapping must name an exact row UUID, both expected identities, the
installed adapter/projection version, a complete `OfferingKey`, review evidence,
and a timezone-aware review time. Every `OfferingKey` field must be explicit,
including `null` fields. Case-folded, family, prefix, provider-fallback, and
"latest" matching do not exist.

The inventory also repeats bounded upstream display labels and public claim URLs
so a reviewer can identify the subject. Treat it as a local review artifact, not
as a redistribution-safe feed. Its `schema_version` labels a bounded operational
view; unlike the generic evidence, reconciliation, and import-report files, it is
not currently a public contract with a committed JSON Schema and semantic loader.

Private single-file output and the private import bundle currently require POSIX
no-follow filesystem primitives. The bundle's explicit-mode path checks that
capability before touching the filesystem and creates each staged payload with
exclusive, no-follow opens; the generic public-bundle defaults remain portable.
Private output fails closed on platforms that cannot provide those primitives;
Windows operators should run collection/import in a POSIX environment rather
than weakening link and permission checks.

The multi-file directory publisher still assumes its existing parent path is
controlled by the invoking identity for the duration of publication. Its
preflight rejects static symbolic links and unexpected entries, but it does not
pin every ancestor with directory file descriptors across the final rename.
Do not target a directory whose ancestors another user can rename or replace.

After review, add generic `QualityReconciliation` entries to a copy of that
configuration. Each entry must use `adapter_id: "harbor-terminal-bench"`, the
inventory's exact projection version and source/subject digests, and normally
`relationship: "reviewed_quality_projection"`. Then import it:

```console
modelskyline import-harbor-terminal-bench ./terminal-bench-project \
  terminal-bench-4.0.json reviewed-import-config.json \
  --retrieved-at 2026-08-31T18:07:29Z
```

Import requires at least one reviewed entry and fails if any reviewed row does
not map. `--allow-partial` is an explicit audit-only escape hatch: it writes the
typed failures and any successful rows, but a zero- or partially mapped bundle
must not be treated as a usable quality catalog.

The output is an atomic bundle containing normalized route-free evidence, the
generic reconciliation/import report, mapped observations, policy, the exact
review configuration, and a warning manifest. Raw upstream bytes are not copied
into the output bundle. On POSIX, the directory is mode `0700` and every file is
`0600`; existing output is preserved unless `--overwrite` is explicit. The local
manifest binds the SHA-256 of every other bundle file. The inventory and manifest
are operational local views, not additional public wire contracts.

This is a local audit/import bundle, not an automatically publication-safe feed.
The exact review configuration and import report may contain internal review
evidence, and the normalized evidence remains governed by its rights assertion.
Create a separately reviewed derived/full publication projection before serving
any of those artifacts publicly.

## What is safe to project

Current Terminal-Bench 4.0 rows identify an agent product, model display label
and documentation/news URL, model and agent organizations, and a reasoning-effort
label. They do **not** disclose the provider endpoint actually used, an API model
ID assertion, region, service tier, billing mode, agent version/commit, or a
cache-accounting methodology binding.

Harbor's required `metadata.date` is rendered by the board's pinned column
contract as **Release Date**. The adapter therefore binds it as the model claim's
revision. It also binds a digest of every source metadata field into subject
identity, including display and optional release-date fields. A changed date,
organization, label, link, reasoning claim, or newly populated optional metadata
field cannot reuse an old review.

Ordinary Harbor rows therefore use a `reviewed_quality_projection` relationship:
the reviewed production offering may receive measured accuracy, its source
interval, and quality-role pass-at-k values, visibly tagged as a projection from
the exact benchmark subject.
Harbor-reported cost, time, token, and cache fields remain research evidence for
that benchmark subject. They are not copied into production-route observations
or used for route cost/performance frontiers. Combine projected quality with
independently sourced current route pricing instead.

The generated Harbor policy consequently has no frontier by itself: one primary
quality dimension is not a Pareto frontier. A cost/quality frontier becomes
meaningful only after the projected quality observation is joined to independent,
current cost evidence for the same complete `OfferingKey`.

ModelSkyline does not invent the second
axis. An operator must materialize that paired frontier from independently
sourced cost/performance observations, preserving byte-for-byte `OfferingKey`
identity, before using Harbor as a portfolio component. `build_portfolio` can
then add the reconciled Harbor quality signal to an ordinary base catalog; it
does not claim that a production route join occurred or transfer the
benchmark's reported cost.

Only a future source row that establishes an `exact_subject_route` may transfer
route-specific cost/time/token measurements. A model documentation link alone is
not that evidence.

`OfferingKey.agent_harness` always describes the production target. The Harbor
benchmark agent and evaluator harness live in the quality subject and workload;
the adapter never copies `Claude Code`, `Codex`, or another leaderboard agent
label into the route key.

## Independent invalidation domains

| Change | Digest that changes | Mapping review required? |
|---|---|---|
| Retrieval time or raw formatting only | raw audit | No |
| Board/dataset UUID, complete embedded schemas, scoring/rank/release-date column contract, parser projection | source identity | Yes |
| Row UUID or any agent/model metadata, including release date, organizations, links, labels, display metadata, and reasoning claim | subject identity | Yes |
| Accuracy, interval, trials, status, rank, or any metric metadata including cost/time/token telemetry | result | No; a new result is emitted or quarantined |
| License/terms review | rights | No route remap; publication eligibility may change |

Mappings normally bind source and subject identity, not raw bytes or result
values. An operator may additionally pin a raw-audit digest for a one-off review.
Hidden or otherwise non-complete rows produce quarantine/import-report records;
they never become quality observations.

## Cost and cache caveat

Harbor's token bucket meaning is board-version-specific. In the audited 4.0
response, `total_tokens` equaled uncached input plus output even when a large
`cached_input_tokens` value was present, so cached input overlapped another
reported input quantity. Earlier Terminal-Bench boards used different
relationships. ModelSkyline preserves the fields in normalized research evidence
and preserves Harbor's reported total dollars; it does not add token buckets or
reconstruct spend from them.

Reported dollars may also omit infrastructure, tools, retries, judge cost, or
other charges outside the upstream submission method. They are evidence about
the submitted benchmark system, not a current provider price card.

## Live interoperability check

On 2026-08-31, a live unauthenticated Harbor 0.22.0 capture of Terminal-Bench 4.0
returned board UUID `9f966760-00f1-424e-90f5-c964fb6f6091`, dataset-version UUID
`1922072f-a433-429a-8929-350d5e1bcf02`, and 10 displayed rows. Exact response
SHA-256 at `2026-08-31T18:07:29Z` was
`aa75ce1babdb328a404546f3012d4751399076f21dc6015e268b05dc92750fb3`.
These identifiers and counts are a point-in-time interoperability record, not
vendored result data or an availability promise.

A second capture at `2026-08-31T19:20:59Z` had raw SHA-256
`d553cd0ae8d6ed572f39aa6b4373d632d29d20ae1c5796ce2dcb21794194d9d7`.
The hardened `@2` parser produced source-identity digest
`150ed103206e6ddcb58041de5a4ca502f4e6da845234df728c740e2541f43ce7`.
One ephemeral reviewed projection for one exact row mapped; the other nine rows
remained `unknown_route`. The mapped catalog contained accuracy and
the source's pass-at-k signals, but no frontier. Its cost, duration, total-token,
uncached, cached, and output-token measurements remained solely in normalized
evidence. The generated private bundle was mode-checked and its five payload
digests were verified against the manifest. The ephemeral mapping and raw
response were not written to the repository.

Harbor/Terminal-Bench result redistribution rights were not established during
this check. We retained only minimal factual interoperability metadata here
(interface/version, UUIDs, digests, row/outcome counts, and signal classes), not
leaderboard labels or values. The repository contains no raw capture. Use an
explicit rights assertion and the generic publication-scope gate before
publishing derived or full evidence.
