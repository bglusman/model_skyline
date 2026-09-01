# models.dev price-snapshot reconstruction

ModelSkyline can combine historical Aider Polyglot quality and token totals
with an acquired snapshot of the public [models.dev API](https://models.dev/api.json).
The result is a separate, normal ModelSkyline project with tables, JSON, CSV,
RSS, and publication history. It is a reconstructed token marginal-cost
comparison. It is not an observed provider bill, a total-cost estimate, or a
claim that historical quality, latency, availability, or model behavior is
current.

> **Legacy cohort:** OpenAI's current model pages resolve
> [`gpt-5`](https://developers.openai.com/api/docs/models/gpt-5),
> [`o3`](https://developers.openai.com/api/docs/models/o3), and
> [`o3-pro`](https://developers.openai.com/api/docs/models/o3-pro) to the dated
> snapshots in this projection. OpenAI's
> [deprecation schedule](https://developers.openai.com/api/docs/deprecations#2026-06-11-gpt-5-and-o3-model-deprecations)
> says those snapshots are deprecated and will be removed from the API on
> December 11, 2026. At the September 1 review, models.dev still reported their
> status as unspecified. The mapping therefore opts in to deprecated routes
> explicitly, the scheduled workflow refuses new acquisitions on or after the
> shutdown date, and the publication remains historical research—not current
> model guidance or routing input.

## Run the reviewed multi-model projection

The included mapping covers six pinned Aider runs: GPT-5 low, medium, and high;
o3 with default and high reasoning; and o3-pro high. The GPT-5 commands name
the exact `openai/gpt-5` route. The o3 commands use bare model names. Their
source-row Aider commits pin LiteLLM 1.73.1; the reviewed package catalog binds
those names to provider `openai` ([PyPI release metadata](https://pypi.org/pypi/litellm/1.73.1/json),
sdist SHA-256 `33ad55ff051bf925419619ec37f32949decdc52a6109c8c0700cfb1209696590`).
Every entry binds its command digest, expected reasoning configuration,
provider/model assertion, deprecation opt-in, and review evidence. The adapter
does no fuzzy name matching.

```console
uv run modelskyline project-aider-models-dev \
  ./aider-models-dev-price-snapshot \
  examples/mappings/aider-models-dev.json

uv run modelskyline evaluate \
  ./aider-models-dev-price-snapshot/frontier.yaml \
  ./aider-models-dev-price-snapshot/observations.json \
  price-snapshot-cost-per-attempted-vs-solve-rate
```

The reviewed September 1 acquisition evaluated all six configurations with no
rejections. GPT-5 low, medium, and high were frontier members; both o3 rows and
o3-pro high were dominated. Generated JSON retains all evaluated candidates
and their dominators, while the tables show frontier members. A later valid
price acquisition can change that membership and is recomputed rather than
forced to preserve this result.

The first command fetches the hash-pinned Aider source and the exact official
models.dev API URL. It writes `observations.json`, `frontier.yaml`, an exact
copy of the reviewed `mapping.json`, canonical `selected-prices.json`, and
`projection.json`. The manifest
records the Aider and pricing source descriptors, the full pricing-body digest,
the selected-price digest, and the mapping digest. In particular,
`sources.pricing_catalog` describes the complete acquired document and
`sources.selected_prices` describes only the reviewed fields that can affect
this projection.

To reproduce one official pricing acquisition, archive the API bytes and
supply its digest and acquisition time. The official-source assertion is
required before a local file receives models.dev/MIT provenance:

```console
uv run modelskyline project-aider-models-dev \
  ./aider-models-dev-pinned \
  examples/mappings/aider-models-dev.json \
  --pricing-source ./api.json \
  --pricing-expected-sha256 <64-hex-digest> \
  --pricing-retrieved-at 2026-08-31T15:00:00Z \
  --assert-official-pricing-source
```

Use `--assert-official-pricing-source` only when the archived bytes actually
came from the official endpoint. Without it, a local compatible document is
correctly labeled `operator-models-dev-compatible` with license
`NOASSERTION`. Custom remote pricing hosts are not accepted: acquire and
review a mirror separately, then pass it as a bounded local file.

Remote retrieval accepts only `https://models.dev/api.json`, refuses
redirects, proxies, compression, partial responses, and unexpected media
types, and bounds time and bytes. Parsing rejects duplicate JSON keys,
non-finite numbers, excessive nesting or structure, and oversized strings.
Rates are parsed directly as `Decimal`. The endpoint does not expose its
deployed Git commit, so its body SHA-256—not a guessed repository revision—is
the acquisition version.

## Accounting contract

Version `v1alpha1` supports one deliberately narrow scenario:

```text
cache-disabled reconstructed token marginal cost =
  (Aider prompt tokens × models.dev ordinary input rate
   + Aider completion tokens × models.dev output rate) / 1,000,000
```

All Aider prompt tokens are assigned to ordinary uncached input. This is a
scenario assumption, not evidence that a provider cache was disabled during
the historical run. Completion tokens are charged at the ordinary output
rate. `thinking_tokens` is not used because an Aider row may use it for a
configured reasoning budget rather than measured billed usage. The
cost-per-solved metric retains successful and failed cases in the numerator.

The formula preserves distinct sources: Aider supplies historical quality and
token quantities, while models.dev supplies price-snapshot rates. Every
published axis cites the sources of the observations it actually evaluates.
The estimate excludes cache economics, tool/search/request fees, taxes,
credits, batch or priority discounts, local compute, and all other unreported
infrastructure cost.

Required ordinary input or output rates must be present for a selected route.
Missing optional models.dev meters remain unknown, not zero. Explicit zero
base rates remain zero. Context-tiered cards and the legacy
`context_over_200k` view are rejected because aggregate benchmark totals
cannot establish each request's context band. Separate reasoning prices are
also rejected because this Aider source does not provide a disjoint measured
billed-reasoning quantity. Experimental pricing modes are not selected by the
default-mode mapping.

## Dependency and invalidation model

ModelSkyline treats each rate, quantity, and outcome as an independent
observation with its own unit, timestamp, and source. A formula is evaluated
from named signal paths, and its published `dependencies` contain the exact
paths used, including lazy-branch behavior. This separates numeric dependency
from artifact provenance.

The adapter projection uses these fields:

| Observation | Source | Role |
| --- | --- | --- |
| prompt and completion token totals | pinned historical Aider run | cost numerator quantities |
| attempted and solved work units | pinned historical Aider run | per-work-unit denominators |
| solve rate | pinned historical Aider run | historical quality axis |
| ordinary input and output rates | semantic `selected_prices` source | cost numerator rates |
| cache, request, audio, and other optional rates | complete-catalog metadata only | no v0.6 formula dependency |

Consequently, an input-price change can change all three price metrics; an
attempt-count change affects only the attempted-work-unit metric; and a cache
rate change cannot change a v0.6 numeric result because no formula references
it. A future cache-aware formula must expose cache-read and cache-write rates,
matching token quantities, retention/tier policy, and any hit-rate assumption
as separate signals. Missing any required component must remain unknown and
exclude the offering rather than silently becoming zero.

Freshness is evaluated on the observations a metric actually used. A
frontier's per-source age limit is combined with any general metric age limit;
the stricter limit wins. The included mapping defaults the models.dev source
to `pricing_max_age_hours: 48`, so stale input/output rates make a projected
offering ineligible. It deliberately sets no maximum age for Aider quality or
token observations: those values remain historical and must never be
presented as current measurements.

Pricing reconstruction is emitted as its own price-only project. Refreshing or
expiring models.dev rates therefore does not invalidate the independently
generated Aider historical-cost, quality, or timing frontiers. Within the
projection project, operators must recompute each frontier after any input or
policy change; filtering an old member list is not equivalent to a new Pareto
evaluation.

There are three related but different content identities:

- `sources.pricing_catalog.raw_sha256` binds every byte of the acquired API
  document for audit and tamper detection;
- `selected_prices_sha256` and `sources.selected_prices` bind only each selected
  provider/model/mode record's input/output rates and
  status/reasoning-compatibility fields; and
- `mapping_sha256` binds the operator's route assertions and review evidence.

For an official acquisition, the semantic source id is
`models-dev-selected-cache-disabled-prices`; for a compatible operator source
it is `operator-selected-cache-disabled-prices`. Its version contains the
selected-price digest; `raw_sha256` is null so it cannot be confused with the
complete document at the upstream URL. The digest is independently verifiable
against the exact canonical `selected-prices.json` bytes. The input/output
price observations and per-source freshness rule cite this semantic source.
The full catalog source remains in `projection.json` and in each offering's
audit metadata rather than becoming a formula dependency.

The projected workload version is derived from the historical workload,
selected-price, mapping, scenario, and stable semantic price-source descriptor
(all source fields except volatile `retrieved_at`). Official and operator
sources with identical selected values therefore remain different workloads,
and their catalogs/configurations cannot be cross-substituted. An unrelated
models.dev entry can still leave `selected_prices_sha256` and every computed
value unchanged. The frontier snapshot binds the complete generated
observation catalog, whose offering metadata retains the complete API digest.
An unused cache-rate or unselected-record change therefore creates a different
`catalog_hash` and immutable snapshot for audit, but keeps the projected
workload version, `config_hash`, computed values, and ordered frontier view
stable. This is an explicit semantic projection for this adapter, not a
generic field-level Merkle invalidation facility for arbitrary documents.

RSS suppresses a duplicate item when both the ordered semantic frontier view
and its baseline-defining configuration remain unchanged. For example, a new
retrieval timestamp for identical source bytes can extend immutable history
without a new RSS event. So can a changed unused cache rate or unselected
catalog record: its full provenance changes, but its selected-price semantics
do not. A selected input/output rate, status, or supported reasoning-effort
change rotates `sources.selected_prices` and the projected workload, producing
a new baseline event. A selected display field included in frontier metadata
can also change the ordered semantic view even when the numeric axes do not.
Consumers should compare both source identities, the evaluated axis
dependencies, and the immutable artifact hashes; RSS is not a substitute for
artifact verification.

## Mapping review contract

Every mapping entry contains:

- the exact Aider offering ID, expected model label, reasoning effort, and
  SHA-256 of its sanitized command;
- the exact opaque models.dev provider and provider-scoped model IDs;
- an operator assertion that the historical row and price card represent the
  same provider/model route;
- human review evidence and a timezone-aware review timestamp; and
- the default pricing mode and an explicit deprecated-route opt-in for this
  legacy cohort.

The assertion is necessary but not magical proof that behavior or billing is
portable across time. Provider/model IDs are never normalized, split for
region or quantization, or fanned out to other providers. Their structured
route tuple is hashed into a stable offering suffix while raw values remain in
`OfferingKey` and metadata. Compound Aider architect/editor rows are rejected
because one price card cannot reconstruct their combined spend.

Public provider request bodies, headers, endpoints, and environment-variable
names from models.dev are never executed or copied into a gateway route. A
published pricing offering still requires a separately trusted local target
binding before an agent or gateway can execute it from an ordinary selection.

Mapping evidence is copied into public offering metadata and the exact mapping
is copied into the generated project. Treat the mapping as publishable input:
never put credentials, personal data, private paths, customer names, or
confidential review notes in it.

The `v1alpha1` mapping and projection-manifest labels are adapter-local Python
contracts introduced in v0.6 and validated by the installed Pydantic
implementation. They do not have language-neutral files in `schemas/`;
non-Python automation should regard the copied artifacts as audit evidence,
not a stable independent wire contract.

## Automation boundary

The repository's `publish-models-dev-pages.yml` workflow runs daily and can be
started manually. It pins the reviewed mapping digest and Aider source digest,
fetches only the exact models.dev API URL, evaluates all three projected
frontiers, and publishes a public research feed. Every run also retains the
exact five generated inputs under:

```text
aider-models-dev-evidence/snapshots/<pricing-catalog-sha>/<bundle-sha>/
```

The evidence pointer binds every file hash, selected-price and mapping hashes,
the corresponding publication, and its preceding pointer. The build job has
read-only repository access; the smaller write job revalidates the artifact and
updates the two models.dev Pages subtrees plus the repository's static landing
page without force-pushing.
`model-skyline/models-dev-pages-evidence/v1alpha1` is a repository-workflow
audit format introduced in v0.6, not an exported language-neutral schema or
signed trust root. The earlier `models-dev-gpt5` and
`models-dev-gpt5-evidence` paths remain frozen historical publications; the
expanded cohort starts a new project and evidence chain rather than silently
changing that three-configuration project's scope.

The unattended research loop is:

```text
scheduled exact-URL fetch
  -> strict parse + raw SHA-256
  -> exact mapping resolution
  -> three frontier evaluations
  -> content-addressed evidence archive + static Pages/RSS publication
```

Route removals, a models.dev `deprecated` status without opt-in, schema drift,
tiers, context schedules, distinct reasoning meters, and mapping changes fail
closed. A provider lifecycle notice missing from models.dev cannot trigger
that source check; this cohort handles the known gap with reviewed mapping
evidence, explicit opt-in, public warnings, and a fixed December 11 shutdown
gate. A valid ordinary input/output price change before then advances this
research feed automatically. There is no spend threshold, human approval gate,
or selection artifact in that workflow.

The workflow does not ingest first-party alias-to-snapshot or lifecycle pages.
An alias retargeting or earlier provider shutdown before the fixed gate would
therefore require manual mapping review; it cannot be inferred from a
models.dev record that still reports no status. Adding that source is separate
adapter work, not a property this publication claims today.

Pages `latest.json`, tables, and RSS remain retrievable indefinitely and do not
expire themselves. A successful run yesterday does not prove that today's run
completed. Research consumers must verify immutable hashes and compare the
selected source watermark with current time. Agent routing must instead apply
operator thresholds and capability rules, generate a separately reviewed and
short-lived ordinary selection, deliver it over a trusted channel, and fail
closed or use an explicitly bounded last-known-good policy when refresh
monitoring reports an outage. Version 0.9 does not authenticate that channel or
provide durable anti-rollback state. Never route directly from this static
Pages feed.

A generalized price book will need independent context schedules and cache,
reasoning, audio, request, and tool meters joined to equally explicit
per-request usage. It must not infer cache retention, discount, or refresh
semantics that the source does not publish.

The upstream schema and USD-per-million convention used for this adapter were
reviewed at commit
[`4a3a072`](https://github.com/anomalyco/models.dev/blob/4a3a072b45d6d79611b6d1ccddf23f22a7b4cfc2/packages/core/src/schema.ts).
models.dev is MIT licensed and community maintained; material spend decisions
should still be checked against first-party provider prices and terms.
