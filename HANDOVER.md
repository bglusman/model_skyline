# HANDOVER: consumer-deployment state & project context (2026-08-31)

Audience: whoever takes over coding on this repo (Claude Code / opencode-go
GLM-5.3 / DeepSeek V4 in a coding harness on Brian's MacBook). Written by
Hermes (the household assistant agent) which runs the LIVE consumer
deployment and stays in the GitHub lane.

## What is actually deployed and running (do not break)

- **Live frontier**: `examples/subscription-relative-real/` (via PR #14 —
  mergeable, CI green at time of writing). Running since 2026-08-31.
- **Consumer stack**: 3-day cron re-checks prices/quality, re-runs
  `evaluate` + `select`, publishes `summary.json` to a dashboard widget.
- **Hard consumer dependencies**: `evaluate`/`select` CLI names and flags;
  ObservationCatalog schema (strict — extra fields on the offering object
  are REJECTED; `purchase_model` had to live in metadata); JSON output
  shapes of both commands. If the simplification pass renames or reshapes
  any of these, the consumer deployment needs a deprecation note in the
  release body.
- **Consumer reports available on request**: issue #1 carries the first one
  (what ran, what was never needed: signed gateway, overlap/proximity).

## Open threads worth reading before coding

- #1 — coordination + architecture checkpoint ("promotion requires one real
  end-to-end producer or consumer" — the deployment above is that evidence).
- #8 — pricing-window metadata proposal + IMPORTANT correction sub-thread:
  windows are ground-truth per-OFFERING (resellers can re-meter or define
  their own); model-level attachment is only an optional canonical reference
  where inheritance is proven (DeepSeek → Go/ClinePass is verified).
- #14 — real-data examples PR, awaiting merge; merge it before refactoring
  examples/ paths.
- bglusman/biblioaudio#51 — integration wishlist for the other repo (shadow
  mode first, one-command runs, progress diffs, health endpoint). Same
  overbuild-risk pattern applies there; the simplification instinct is right.

## Known gotchas (learned the hard way, 2026-08-31)

- Reasoning models (qwen3.8-max class) return EMPTY content if max_tokens is
  too small — they burn the budget thinking. Headroom ≥ 200 for smoke tests.
- ClinePass `/api/v1/models` does NOT list `cline-pass/*` IDs; the docs
  roster is authoritative. Chat-completions accepts them regardless.
- DeepSeek pricing windows (01:00-04:00, 06:00-10:00 UTC) apply at both
  subscription resellers identically — inherited from upstream, verified.
- Decimal rendering in the table output shows `5E+1` — cosmetic fix candidate.

## Backlog ideas with consumer demand (not yet built)

1. Coding-workload frontier: same machinery, different token shape. The
   existing frontier uses an agent-chat shape (~15k uncached / ~167k
   cache-read / ~600 output per success, 30-day traces). A coding-harness
   shape is materially different (Go's docs publish per-model request
   shapes, e.g. ~1.1k in / ~71k cached / ~220 out) — a second frontier
   config + observations over the same catalog would give coding-session
   economics. Natural second consumer for `evaluate`/`select`.
2. Release-artifact publishing: once a tag exists, attach dated
   summary.json/snapshot artifacts to a rolling `frontier-snapshots` release
   (consumer cron already produces them).
3. Resolve-time pricing-window selection (see #8).

## Working agreements

- Provenance rules in AGENTS.md are load-bearing — every signal needs
  source id/methodology/date; exclude-with-reason, never silent drops.
- `Decimal` for money; no binary float.
- Brian's doctrine: verify vs sources, no overclaims, terse responses,
  stop-not-delete (snapshot first), reversible changes only.
- House rule: check `git status --short` before committing in ANY shared
  clone (two agents work from the same checkout on the deployment host).
