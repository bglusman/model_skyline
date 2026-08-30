# Changelog

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
