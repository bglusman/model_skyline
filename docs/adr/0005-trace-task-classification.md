# ADR 0005: Provenance-bound task classification on request traces

- Status: accepted; request-trace v1alpha4 contract implemented, per-class materializer deferred
- Date: 2026-09-01
- Decision owners: ModelSkyline maintainers

## Context

Cost, latency, and quality depend on the work being attempted. A mixed agent
history cannot become a coding, research, or tool-use workload merely because a
model name, command, prompt, or tool payload suggests one. We need a narrow way
for a harness, operator, registered deterministic classifier, or registered
oracle to attach a reviewable task class before separate workload profiles are
materialized.

The trace wire contract is already released in three forms. The unversioned
`request-trace.schema.json` is v1alpha1 compatibility input; v1alpha2 added
unknown-preserving accounting and producer provenance; v1alpha3 added the
logical `model_call` scope. Adding a field to either released version would make
the same schema identifier mean two different things.

## Decision

Request-trace v1alpha4 adds one optional `trace_classification` object. When
present, it is complete:

- `class_id` follows the portable, extensible convention `<namespace>/<class>`
  with up to four further hierarchy segments. It is lowercase ASCII, bounded to
  128 characters, and is not a closed global enum. Examples are
  `openclaw/coding` and `openclaw/coding/repo-change`.
- `source.method` is one of `harness_tag`, `operator`,
  `registered_classifier`, or `oracle`.
- Every source has a bounded `id` and `version`. Registered classifiers and
  oracles additionally require a lowercase SHA-256 implementation/configuration
  digest. The object names registered behavior; it never contains import paths,
  code, prompts, or executable expressions.
- `confidence` is the existing canonical Decimal type in the closed interval
  `[0, 1]`, with at most nine fractional digits. It is not binary floating-point.

JSONL rows are validated through Pydantic before DuckDB sees them. Parquet rows
bypass Pydantic, so the DuckDB path independently enforces the exact nested
STRUCT shape, class and source patterns, method vocabulary, conditional digest,
Decimal precision, and bounds. All rows for one work unit must be either
unclassified or carry the same complete classification. Mixed classified and
unclassified rows, or conflicting class/source/confidence values, fail closed.

Classification stays on trace input. It is not copied onto every
`OfferingObservation`: a catalog is already bound to one exact
`WorkloadReference`. A future per-class materializer must bind at least the
parent workload, class ID, classification source ID/version/digest, confidence
policy, materializer algorithm version, and exact input digest into the derived
workload version and sources. It must report unclassified and below-threshold
coverage rather than silently dropping it.

The current `aggregate_traces` API validates and preserves v1alpha4 input in its
typed DuckDB relation but still aggregates the declared parent workload. It
does not yet filter or manufacture per-class workload catalogs. Operators may
use a separately reviewed preprocessing step, but must not label that output as
the forthcoming built-in materializer. This limitation is intentional: stable
class IDs, hierarchy roll-up, confidence thresholds, multi-label policy, and
coverage accounting require a separate contract.

## Compatibility

Classification is legal only in v1alpha4. A v1alpha2 or v1alpha3 JSONL/Parquet
artifact carrying the new column is rejected. Schema generation projects the
current model back to the old shapes and tests exact released bytes:

- v1alpha1: `8326ff220e961b44eab01cc76a07352fabe75a804488b50c0a17f46af8aeea8c`
- v1alpha2: `405a150c126da7bd7b788f3fe9e2839f6e3e9327573e68248d47defcb3fc5b5b`
- v1alpha3: `ef5f1a29962e1f05d78a0b11dda392506a4094c429412e18d5e32868711ba1d4`

The committed v1alpha4 JSON Schema carries the registered-classifier/oracle
digest conditional that Pydantic model validators cannot generate by
themselves. Runtime and JSON Schema negative tests cover both validation paths.

## Consequences and trust boundary

- A class is evidence about a work unit, not a universal property of a model.
- Names are never inferred from leaderboard labels, model IDs, prompts, paths,
  or tool payloads. Those inputs may contain secrets or adversarial text.
- A source digest proves content identity, not that an operator authorized the
  taxonomy or that a probabilistic classification is correct.
- Registered code/oracles remain host-controlled. Public configuration cannot
  dynamically import a classifier or nominate an arbitrary remote evaluator.
- Benchmark quality evidence remains under ADR 0004's separate source/subject
  reconciliation boundary. Task classification chooses a workload slice; it
  does not reconcile a benchmark's display name to an `OfferingKey`.

## Alternatives rejected

- Three independently optional flat fields allow partial, incoherent records.
- A closed coding/research/tool-use enum prevents operator-specific taxonomies
  and forces wire revisions for ordinary semantic additions.
- Inferring class from model or tool names is unreliable and injection-prone.
- Adding the field to v1alpha3 would mutate a released language-neutral
  contract.
- Immediately grouping by class would hide unresolved roll-up, confidence, and
  unclassified-coverage policy behind an accidental API.
