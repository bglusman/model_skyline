# ADR 0002: Task-classification contract on request traces

- Status: Accepted-for-PR (pending maintainer review in issue #1)
- Date: 2026-08-30
- Coordination: issue #1 ruling 2026-08-30T18:00Z/18:20Z; preservation requirement 19:16Z

## Context

Request traces record what happened when a harness asked a model to do work, but
they do not say what *kind* of work it was. Downstream consumers need task-class
profiles — cost-per-solved on coding vs long-context research are different
frontiers — and the OpenClaw real-data track is blocked without them. The
classifier itself is out of scope; this ADR fixes only the contract a
classification decision must satisfy when it lands on a trace.

## Decision

`RequestTrace` gains one optional nested object, `trace_classification`,
present-or-absent as a unit; inside it, every member is required so no
incoherent partial state can be expressed.

- `class_id` (`TraceClassId`): namespaced convention `<namespace>/<class>` with
  up to four further `/` segments (`openclaw/coding/repo-change`), conservative
  lowercase portable pattern, max length 128. No closed global enum — taxonomies
  are operator namespaces, not core vocabulary.
- `source` (`TraceClassificationSource`): how the class was decided. Methods:
  `harness_tag`, `operator`, `registered_classifier`, `oracle`.
  `registered_classifier` and `oracle` require `id` and `version` (plus optional
  `sha256`, pattern `^[0-9a-f]{64}$`): a deterministic classifier is *registered
  code* referenced by identity, never inlined. A model-based classifier belongs
  behind the existing versioned oracle boundary (ADR 0001) and is referenced,
  not embedded. Harness/operator declarations are valid and may assert
  confidence 1.
- `confidence` (`CanonicalDecimal`, ge=0, le=1, DECIMAL(18,9)): canonical
  decimal arithmetic only; no binary float. Canonicalization and [0,1]
  semantics validated at model level.
- Classification is trace input used to materialize workload profiles. It is
  NOT copied onto normalized observations; `ObservationCatalog` is untouched.
- Aggregation SQL is unchanged in this PR (no grouping by class yet); rows with
  and without classification aggregate together, NULLs tolerated.

## Representation (DuckDB)

The ingest path validates columns against an explicit type map and rejects
unknown columns, so the classification object must have a declared
representation or it would be silently dropped — which would violate the
maintainer's preservation requirement (issue #1, 19:16Z: classification must
survive JSONL and Parquet round-trips with classifier version/digest binding).

We carry it as a nested STRUCT:

```
STRUCT(class_id VARCHAR,
       "source" STRUCT("method" VARCHAR, id VARCHAR, "version" VARCHAR, sha256 VARCHAR),
       confidence DECIMAL(18,9))
```

Nested STRUCT over flattened columns: keeps the JSONL, Pydantic, and Parquet
representations isomorphic (verified against DuckDB 1.5.5: `read_json(columns=)`
maps nested JSON to STRUCT natively, absent keys → NULL, and Parquet round-trip
type strings match exactly), with zero mapping code to drift. The Parquet
validator accepts an exact member match with confidence scale ≤ 9 and rejects
member mismatches, wider scales, and non-STRUCT columns. The SQL row-guard
mirrors the model invariants ([0,1] bounds, required-together members) because
Parquet rows bypass Pydantic validation.

## Consequences

- Third-party harnesses can attach task classes without touching core
  vocabulary; namespaces evolve independently.
- Classifier provenance (id/version/digest) travels with every classified
  trace, making downstream frontier claims auditable to the exact classifier.
- A future PR may group aggregation by class (per-class workload profiles) and
  may extract an adapter Protocol after the third concrete adapter (issue #1
  ruling); both are now unblocked without further contract change.
- JSON Schema for the trace contract is regenerated and committed; the
  embedded schema remains byte-identical to the generated one.
