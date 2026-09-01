# Codex CLI smoke trace

This is one real, content-free accounting trace retained to exercise the
`import-codex-exec` → `aggregate-traces` path. It is integration evidence, not
a benchmark result, availability promise, cost estimate, or routing
recommendation.

## Capture

- Captured on 2026-09-01 with the installed `codex-cli 0.144.2`, whose reviewed
  upstream commit is `a6645b6b8a656360fa16fb7e1c6721d0697d3d6a`.
- Ran in an empty temporary directory with `--ephemeral`,
  `--ignore-user-config`, `--ignore-rules`, `--skip-git-repo-check`,
  `--sandbox read-only`, and explicit `-m gpt-5.4`.
- The deliberately non-sensitive work unit asked for exactly `OK` and no tool
  use. Before projection, a bounded check established exactly one matching
  agent-message item, no other completed item, and exactly one terminal
  `turn.completed`; the judged outcome is therefore `1`.
- The scrubbed process inherited no provider API key. Provider `openai`, model
  `gpt-5.4`, billing mode `chatgpt_subscription`, and the single-route
  condition are operator attestations; Codex JSONL does not independently
  expose them. The billing mode is retained in the paired catalog, while the
  attempt trace binds it through the complete offering id.

The private 317-byte JSONL stream contained the benign prompt, response, and
an upstream thread identifier. It was never added to the repository and was
deleted after the canonical trace and aggregate were validated. The retained
trace contains only local pseudonyms and canonical accounting fields. Its
SHA-256 is
`2b3f774357f49c10e30f4315553f3bac5081da833ced75e44ab3e2f5648e9720`.

The trace was projected with the new command after those checks:

```console
modelskyline import-codex-exec private/codex-events.jsonl \
  --codex-version 0.144.2 \
  --provider openai \
  --model gpt-5.4 \
  --offering-id openai/gpt-5.4@codex-chatgpt-subscription-smoke \
  --timestamp 2026-09-01T10:33:42Z \
  --workload-id codex-cli-smoke \
  --workload-version v1 \
  --work-unit-id case-0001 \
  --result-id result-0001 \
  --attempt-id attempt-0001 \
  --work-unit-success 1 \
  --model-route-attested \
  --billing-mode chatgpt_subscription \
  --route-details-attested \
  --output private/codex-trace.jsonl
```

## Accounting result

The turn reported 10,962 inclusive input tokens, 1,792 cache-read tokens, and
22 inclusive output tokens, of which 15 were reasoning tokens. Codex 0.144.2
does not report cache writes, so cache-write and uncached-input meters remain
unknown. The stream is attempt-scoped and does not establish provider request
count, price, bill, TTFT, or throughput; those fields remain unknown.

Run the retained trace through the ordinary aggregation boundary:

```console
modelskyline aggregate-traces \
  examples/framework-traces/codex-cli-smoke/catalog.json \
  examples/framework-traces/codex-cli-smoke/trace.jsonl \
  --source-id live-codex-cli-smoke
```

The result is one successful work unit and one attempt, with the same known
token meters projected per work unit and per success. It is too small and too
synthetic to support a quality, reliability, latency, or cost frontier.
