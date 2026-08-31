# Changelog

## 0.7.0 - 2026-08-31

- Add language-neutral normalized quality-evidence, reviewed reconciliation,
  and deterministic import-report contracts. Source, subject, result, raw,
  rights, and complete production-offering identities are independently
  content-addressed; mutable aliases, composites, identity drift, invalid rows,
  and ambiguous targets fail closed instead of being fuzzy-matched.
- Distinguish an exact evaluated route from a reviewed quality-only projection.
  A projection may transfer only explicitly typed quality measurements and
  counts; benchmark-reported cost, latency, tokens, cache fields, and result
  metadata remain route-free evidence.
- Add content-addressed quality-bundle policy and snapshot contracts for two to
  four operator-declared benchmark frontiers. Required components, minimum
  measured coverage, per-component freshness/evidence deadlines, exact complete
  offering identity, and typed missing/quarantine states are hard gates; scores
  are not forced into an average. Unique artifacts do not by themselves prove
  statistical independence, so duplicate benchmark evidence remains an
  operator policy error.
- Add a bundle-bound quality selection artifact, source-backed replay verifier,
  runnable synthetic three-benchmark example, proximity/policy schemas, and CLI
  build plus full-verification paths. The builder requires the separately
  expected policy and source-replays every positive measured-coverage claim.
  Hard-ineligible routes are removed before dominance and proximity are
  recomputed across every participating frontier, preventing an excluded route
  from suppressing an eligible default. `DynamicResolver` requires an explicit
  bundle-ID pin for this artifact, supports exact version/policy-hash pins,
  rejects process-local selection/bundle rollback and equivocation, and never
  extends the wrapper's evidence deadline through stale-on-error behavior.
  `publish-project` and the signed gateway-pointer profile do not yet support
  the new wrapper.
- Add a fail-closed Harbor 0.22.0 Terminal-Bench adapter with inspect/import CLI
  workflows, private atomic audit bundles, exact complete board/schema/row and
  release-date/subject-metadata identities, quality-only current-row projection,
  strict reviewed-row admission, source cost/cache caveats, and a public
  import-config schema. A repeated live 4.0-board test normalized 10 rows, mapped
  one exact reviewed projection, and left nine unreviewed routes excluded.
- Add a generic `reconcile-quality-evidence` CLI and public quality/Harbor JSON
  Schemas, bounded regular-file loaders, duplicate-key detection, deep immutable
  evidence bags, whole-artifact limits, and adversarial tests for Pydantic
  validation bypass, symlinks, FIFOs, file races, excessive depth, and output
  amplification.
- Recommend a target quality bundle of fixed-harness SWE-bench,
  Harbor/Terminal-Bench, and tau2-bench, with operator-supplied ARC-AGI-2 as an
  optional fourth reasoning signal and BFCL as a tool-focused substitute. Each
  collector retains exact harness/cohort identity and independent rights,
  automation, freshness, and PII constraints.

## 0.6.0 - 2026-08-31

- Add a fail-closed models.dev adapter that combines reviewed exact Aider route
  bindings with fresh input/output price snapshots, exact Decimal accounting,
  copied mapping evidence, operator-versus-official provenance, and three
  cache-disabled cost/quality frontiers. Tiered/context-dependent and separate
  reasoning meters remain unsupported rather than guessed.
- Track formula dependencies and freshness per source, so pricing evidence can
  expire independently of historical benchmark evidence. Selected price fields
  have a semantic digest separate from the complete acquisition digest: an
  unused cache/card change advances immutable catalog history without changing
  the cache-disabled policy or semantic frontier, while a relevant rate change
  invalidates it. A default-empty per-source age policy preserves the pre-0.6
  effective-policy hash, while a configured source-age limit changes it. Fail
  closed on unknown source-age keys, conflicting source descriptors across
  workload/catalog inputs, and official/operator price catalog substitution.
- Add a real loopback-TLS signed gateway integration test covering ETag
  rotation, durable anti-rollback state, offline last-known-good admission, and
  transport degradation, plus current gateway integration profiles and
  conformance guidance.
- Define benchmark-bundle quality evidence and document the current hosted
  oracle boundary, exact benchmark-result identity requirements, central
  adapter candidates, normalization and missing-data policies, and the option
  to use multi-frontier overlap instead of collapsing quality to one score.
  Recommend Harbor JSON/Terminal-Bench as the first live generic quality
  adapter and fixed-harness SWE-bench next; ARC-AGI-2 remains local-only until
  automated retrieval is authorized by its terms.
- Add a daily/manual models.dev research publication that pins the exact route
  mapping, retains each five-file projection in a content-addressed evidence
  tree, and independently race-checks publication and evidence pointers. Static
  Pages aliases are explicitly not freshness enforcement or routing authority.

## 0.5.1 - 2026-08-31

- Require the trusted OpenClaw projector to correlate each model-call child span
  to its per-attempt `run.started` parent, assign a one-based ordinal,
  independently prove asynchronous segment completeness, reject dropped
  events, and attest whether usage covers hidden transport retries. Logical
  calls now use the canonical `model_call` scope with unknown provider-request
  count, incomplete usage is omitted, and workload/work-unit identity scopes
  pseudonymous ids. Request-trace v1alpha3 adds that scope while the released
  v1alpha2 schema remains byte-for-byte unchanged. The corrected producer is
  adapter `1alpha3` / collector `3`; ambiguous older projector rows fail closed.
- Bounded-retry concurrent WAL activation, then serialize SQLite schema
  initialization through one immediate transaction with an idempotent metadata
  insert and post-insert version check, removing first-start races without
  changing the stored schema.
- Preserve bounded expiry diagnostics and block subsequent admissions when a
  fresh post-synchronization clock crosses a signed gateway hard expiry.
- Align the Claude Agent SDK adapter with pinned `0.2.148`: its Python
  `ModelUsage` type omits runtime `costBasis`, while the bundled CLI can pass it
  through. Metered results accept present `list`/`managed` values and fail
  closed on `unknown`; optional model/provider identity is cross-checked, and
  caller route/pricing attestation remains mandatory. Adapter `2` validates
  crash identity while ignoring unused crash pricing metadata and leaving every
  crash meter unknown; historical adapter `1` remains accepted.

## 0.5.0 - 2026-08-30

- Add a gateway-neutral signed selection profile: a threshold Ed25519 DSSE
  envelope authenticates an RFC 8785 pointer that binds audience, channel,
  sequence, hard expiry, and the exact publication and selection bytes.
- Add strict local complete-offering target bindings, per-work-unit pinned
  routes, fail-closed HTTPS refresh, and crash-consistent SQLite anti-rollback
  plus last-known-good state.
- Export pointer, envelope, and trust-policy schemas; ship deterministic
  language-neutral accept/reject vectors with intermediate hash/signature bytes
  and an independently validated Elixir-compatible wire contract.
- Document the gateway/control-plane boundary, threat model, retry and receipt
  constraints, ecosystem integration points, and a concrete Wardwright native
  consumer sequence.

## 0.4.1 - 2026-08-30

- Restore retained v0.3 publication validation after the optional
  `OfferingKey.billing_mode` addition by treating absent and null values as the
  same hash/view state, while continuing to verify v0.4.0's short-lived
  explicit-null hashes.
- Revalidate the pinned Aider retained-history and RSS automation against the
  live `gh-pages` chain; history advances without a spurious semantic feed
  event.

## 0.4.0 - 2026-08-30

- Add content-addressed frontier-proximity sidecars and multi-frontier selection
  snapshots based on the Hermes-track overlap proposal, with exact offering
  identity, per-frontier freshness/tolerance, ordered priority groups, explicit
  missing evidence, and diversity applied after re-ranking.
- Add a v1alpha2 canonical trace contract for request-, attempt-, and work-unit
  aggregates with unknown-preserving meters, inclusive token totals, generic or
  retention-tier cache writes, timing scope, and reviewed producer provenance;
  retain the v1alpha1 schema byte-for-byte for compatibility.
- Separate reconstructed, estimated, provider-reported, billed, and provider-
  marginal cost bases so formula policy cannot silently add overlapping bills.
- Add fail-closed, content-free adapters for pinned Codex, Claude Agent SDK,
  OpenClaw, and Hermes Agent telemetry, including failure rows and exact route
  attestations where upstream events do not expose identity.
- Validate Codex 0.144.2 against live successful and failed `codex exec --json`
  streams, and re-run pinned Aider and MCPMark imports to demonstrate that
  frontier membership changes by workload and cost denominator.

## 0.3.1 - 2026-08-30

- Treat workload-source retrieval timestamps as volatile acquisition provenance
  rather than frontier policy, while retaining source identity, version, digest,
  licensing, URLs, and methodology in `config_hash`.
- Keep refreshed source provenance bound into immutable snapshot history without
  emitting a duplicate RSS policy-reset event when the frontier view is unchanged.
- Preserve retained 0.3.0 history as an auditable one-time policy-hash baseline;
  later identical 0.3.1 refreshes do not add further semantic feed events.

## 0.3.0 - 2026-08-30

- Add `publish-project` for coherent multi-workload frontier, retained-history,
  RSS, and default/fallback selection publication.
- Add content-addressed frontier JSON/CSV/text, history, feed, selection, and
  publication-manifest artifacts with convenient mutable discovery aliases.
- Make root `latest.json` the last-written cross-artifact commit marker, with
  advisory single-writer locking, atomic file replacement, immutable collision
  checks, out-of-root temporary staging, idempotent reruns, and fail-closed
  chain and digest validation.
- Preserve every previously published frontier and selection on refresh while
  allowing additive expansion; reject implicit retirement, timestamp rollback,
  and conflicting snapshots at one timestamp.
- Add a public redistribution mode requiring HTTPS links and explicit license
  or exact source-id authority across the complete retained history, while
  rejecting unmanaged and unreachable publication content.
- Default new publication files to owner-only permissions and fail closed on
  unreadable tree entries; license checks cover every validated ancestor
  manifest even if a current history view is damaged or pruned.
- Add a least-privilege scheduled workflow that rebuilds the redistributable
  Aider-only publication and advances durable `gh-pages` history without force
  pushes; repository Pages activation remains an operator setting.
- Publish `PublicationManifest` and `FrontierHistory` JSON Schema contracts and
  emit RSS only for meaningful view changes or policy/axis baseline resets.

## 0.2.0 - 2026-08-30

- Add pinned, hash-verified Aider Polyglot import and four historical
  cost/quality/time frontier definitions.
- Add an experimental non-vendoring MCPMark Verified import with separate
  filesystem, GitHub, Notion, Playwright, Postgres, and overall workloads.
- Preserve source license uncertainty and refuse to manufacture MCPMark cost
  from missing provider and cache telemetry.
- Add offline adapter fixtures, import manifests, CLI workflows, and real-source
  documentation.
- Label Aider timing and its mixed historical cohort precisely, propagate source
  cost rounding bounds, and record the limited interpretation of Wilson
  reference intervals.
- Fail closed for remote adapter hosts and redirects, reserve remote retrieval
  timestamps for the importer, validate MCPMark cohort integrity, and publish
  staged adapter bundles with a manifest commit marker and in-process rollback.

## 0.1.0 - 2026-08-30

- Implement the `v1alpha1` observation, policy, frontier, trace, and selection
  contracts.
- Add deterministic two-axis Pareto evaluation, safe formulas, versioned
  oracles, JSON/CSV/table/RSS rendering, and dynamic default/fallback resolution.
- Add cache- and failure-aware trace aggregation, public JSON Schemas, CI, and
  pre-publication security hardening.
