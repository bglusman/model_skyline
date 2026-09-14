# Prompt-free real-world local-agent summary contract

The `normalize-local-agent-benchmark` command turns task-level local results
for ResearchClawBench, BrowseComp, or GAIA into ModelSkyline's route-free
quality-evidence contract. It exists to make the three real-world frontier
recipes executable without publishing questions, answers, attachments,
trajectories, or model messages.

Normalization does **not** make a result routeable. The ordinary reviewed
quality-reconciliation workflow must still map the subject to every field of an
exact `OfferingKey`. An `exact_subject_route` mapping preserves measured
quality, latency, and tool/grounding checks. A
`reviewed_quality_projection` mapping deliberately retains only quality, so
runtime-specific latency and gates cannot leak onto a different quantization or
serving configuration.

## Accepted summary

The JSON schema version is
`model-skyline/local-agent-benchmark-summary/v1`. Unknown fields fail closed.
All decimal values are strings, task identity is a lowercase SHA-256 digest,
and the top-level `contains_prompts_or_model_messages` value must be `false`.
`task_manifest_sha256` is the SHA-256 of the sorted unique task digests joined
with newline bytes, so the adapter can recompute the cohort without task text.

```json
{
  "schema_version": "model-skyline/local-agent-benchmark-summary/v1",
  "contains_prompts_or_model_messages": false,
  "benchmark": {
    "kind": "researchclawbench",
    "dataset_id": "InternScience/ResearchClawBench",
    "dataset_revision": "ed664513287a1fc60ae319e6bf7dbe7a62144135",
    "split": "screen-20",
    "task_manifest_sha256": "<64 lowercase hex>",
    "task_count": 20,
    "task_set_kind": "screen",
    "harness": {
      "id": "researchclawbench",
      "version": "<pinned revision>",
      "configuration_sha256": "<64 lowercase hex>"
    },
    "scorer": {
      "id": "weighted-research-rubric",
      "version": "<pinned revision>",
      "configuration_sha256": "<64 lowercase hex>"
    },
    "protocol": {
      "id": "model-skyline/local-research-screen",
      "version": "1",
      "configuration_sha256": "<64 lowercase hex>"
    },
    "cohort": {
      "workspace_manifest_sha256": "<64 lowercase hex>",
      "source_snapshot_sha256": "<64 lowercase hex>",
      "judge_model": "<exact judge route>",
      "judge_revision": "<exact revision>",
      "network_policy": "frozen"
    }
  },
  "subject": {
    "row_id": "agents-a1-mlx4-omlx064",
    "system_label": "Agents-A1 exact local profile",
    "model_id": "InternScience/Agents-A1",
    "model_revision": "<immutable revision>",
    "artifact_sha256": "<64 lowercase hex>",
    "quantization": "mlx-affine-4bit-g64",
    "runtime_id": "omlx",
    "runtime_version": "0.6.4",
    "runtime_configuration_sha256": "<64 lowercase hex>",
    "benchmark_agent": {
      "id": "<agent harness>",
      "version": "<version>",
      "configuration_sha256": "<64 lowercase hex>"
    },
    "reasoning_claims": {
      "mode": "enabled",
      "budget": 4096,
      "thinking_preserved": true
    },
    "attempt_claims": {
      "concurrency": 1,
      "attempts_per_task": 1
    }
  },
  "rights": {
    "license_expression": "<reviewed expression>",
    "terms_locator": "<terms URL or private review locator>",
    "publication_permission": "derived_only",
    "reviewed_at": "2026-09-14T00:00:00Z",
    "review_evidence": "<bounded review note>"
  },
  "observed_at": "2026-09-14T01:00:00Z",
  "tasks": [
    {
      "task_sha256": "<64 lowercase hex>",
      "score_percent": "72.5",
      "wall_seconds": "834.2",
      "tool_checks": {"passed": 4, "total": 4},
      "grounding_checks": {"passed": 8, "total": 8}
    }
  ]
}
```

ResearchClawBench accepts continuous task rubric percentages and requires
grounding counts. BrowseComp and GAIA require binary task scores (`0` or `100`)
and omit `grounding_checks`. Their kind-specific cohort fields are:

| Benchmark | Required cohort identity |
| --- | --- |
| ResearchClawBench | workspace and frozen-source manifest digests, judge model/revision, network policy |
| BrowseComp | encrypted dataset digest, grader model/revision, search and browser providers, bounded execution window, tool budget |
| GAIA | attachment manifest digest, attachment policy, web policy, literal `dataset_access: gated` |

For `task_set_kind: full`, the adapter requires the reviewed task count: 40
ResearchClawBench tasks, 1,266 BrowseComp tasks, or 466 GAIA tasks. Smaller
`screen` inputs normalize honestly, but the frontier recipes' minimum-sample
rules can still reject them. A screen is never relabeled as a full benchmark.

## Derived measurements

The adapter recomputes, rather than trusts, all aggregates:

- arithmetic mean task quality;
- all-task p95 wall time using deterministic Hyndman–Fan type 7;
- exact tool-check success; and
- exact grounding-check success for ResearchClawBench.

Version 1 records exactly one attempt per task. Multiple-attempt input is
rejected instead of treating a pre-aggregated task score as raw repetition
evidence; a future repeated contract must retain individual attempts and
repeatability bounds.

The emitted signal names match the packaged recipes. Exact check percentages
carry degenerate empirical bounds so a 100% gate means every recorded check
passed; those are not statistical confidence intervals.

Validated 128K retrieval and zero swap are intentionally absent. They must be
joined from separately captured evidence for the same exact offering. A long
context allocation or a low-memory benchmark task cannot impersonate either
gate.

## Rights and privacy

BrowseComp asks users not to redistribute decrypted examples, and GAIA is a
gated dataset. The adapter therefore rejects an `unrestricted` publication
claim for either benchmark. Rights still require a dated human assertion and
remain enforced by the normal reconciliation/publication scope.

Task records contain only digests and derived statistics. Any `prompt`,
`answer`, attachment path, trajectory, or other unexpected field rejects the
entire input rather than being copied into evidence metadata.

## Workflow

```console
modelskyline normalize-local-agent-benchmark summary.json \
  --retrieved-at 2026-09-14T12:00:00Z \
  --output quality-evidence.json

modelskyline reconcile-quality-evidence \
  quality-evidence.json reconciliation.json \
  --publication-scope internal \
  --output import-report.json

modelskyline project-quality-catalog \
  quality-evidence.json reconciliation.json import-report.json \
  --workload-id measured-local-research-agent-v1 \
  --workload-unit benchmark_task \
  --output research-quality-catalog.json

modelskyline compose-catalogs \
  qwen-research-quality-catalog.json \
  laguna-research-quality-catalog.json \
  agents-a1-research-quality-catalog.json \
  --output research-quality-candidates.json
```

The first command writes private evidence by default. Use the derived
publication path only after the benchmark-specific rights review and after
confirming that no gated content appears in downstream artifacts.

Composition requires the complete `WorkloadReference` to match exactly. It
sorts distinct candidate rows deterministically and may join rows for the same
offering only when their complete `OfferingKey` values match. Conflicting
signals, metadata leaves, or source descriptors fail closed. When joined rows
used different fallback sources, the command materializes each source on its
own signal instead of assigning one source to the combined row. Output remains
private; composition does not change publication rights.

This command combines candidates measured under the same benchmark cohort.
Validated context, swap, and session-endurance gates require the separate
[`CatalogEnrichmentPolicy`](../../docs/catalog-enrichment.md). That policy pins
every input catalog/workload, complete source and target `OfferingKey`, selected
signal, and review note. Its output workload version binds the complete policy;
no route mapping is inferred from a model name or partial identity.
