# Framework trace integrations

ModelSkyline's framework adapters turn narrowly reviewed accounting surfaces
into versioned canonical request-trace rows. Codex, Claude, and Hermes emit the
retained `model-skyline/request-trace/v1alpha2` contract. OpenClaw emits
`model-skyline/request-trace/v1alpha3`, which adds the conservative
`model_call` observation scope without mutating v1alpha2. The adapters are
deliberately conservative: unsupported versions, ambiguous routes, incomplete
terminal events, and contradictory accounting fail closed. A missing
measurement stays `null`; only an upstream measurement or an explicit
billing-state assertion becomes zero.

These adapters are accounting projectors, not inference clients. The operator
still owns execution, task-outcome judgment, exact offering identity, raw-data
retention, and any attestations named by an adapter.

## Reviewed contracts and validation status

| Adapter | Accepted upstream contract | Accepted input | Local validation through 2026-08-31 |
| --- | --- | --- | --- |
| Codex | `0.144.2` at [`a6645b6`](https://github.com/openai/codex/tree/a6645b6b8a656360fa16fb7e1c6721d0697d3d6a) and `0.151.0` at [`78c2908`](https://github.com/openai/codex/tree/78c290807ce710180111df227df3b7a4fe845452) | One `codex exec --json` JSONL file | `0.144.2` was exercised successfully with both the installed default and an explicit `-m gpt-5.4` route, plus two local-account route failures. `0.151.0` has fixture/contract tests but was not installed locally. |
| Claude Agent SDK | Python SDK `0.2.148` at [`af5ff1b`](https://github.com/anthropics/claude-agent-sdk-python/tree/af5ff1b9f2f279575f89b78f17572c6e35fbc2b6), bundled Claude Code CLI `2.1.251` | The final typed `ResultMessage`, not a transcript or serialized session | SDK `0.2.148` with its bundled CLI `2.1.251` was installed exactly. A constrained Haiku request (no tools, skills, settings, MCP, fallback, or session persistence; one turn; $0.02 cap) reached the SDK but stopped on API billing/quota before a `ResultMessage`, so it did not prove a live `RequestTrace` or the runtime `costBasis` contract. |
| OpenClaw | `2026.8.1` at [`2a6c333`](https://github.com/openclaw/openclaw/tree/2a6c333225e5c886bfd630e36037fb7b206408ef) | One HMAC-signed, content-free `model.call.completed` or `model.call.error` projection | Contract and adversarial fixtures only. The installed `2026.3.2` is intentionally unsupported. |
| Hermes Agent | `0.20.6` at [`4f22543`](https://github.com/NousResearch/hermes-agent/tree/4f22543509d1b91dc45bcb369447126c5eb14fb7), session schema `26` | A `hermes -z --usage-file` JSON report or read-only state SQLite database | Contract, synthetic report, and synthetic schema-v26 database tests only. Hermes was not installed locally. |

The Codex `0.144.2` success run reported 11,250 inclusive input tokens,
2,304 cache-read tokens, 22 inclusive output tokens, and 15 reasoning tokens.
An independent explicit `-m gpt-5.4` smoke run reported 10,963 inclusive
input tokens, 1,792 cache-read tokens, 31 inclusive output tokens, and 14
reasoning tokens. Its content-bearing JSONL was deleted after the canonical
content-free trace was validated.
That version does not report cache writes, so cache-write and uncached-input
meters remained unknown. The explicit `gpt-5.2-codex` and `gpt-5.3-codex`
routes failed for the local ChatGPT account; those are retained as failure
observations and are not a general model-availability claim. Codex JSONL does
not provide a bill, so this run produced no cost measurement.

## Shared accounting rules

- An offering is the full route, not just a model name. Provider, endpoint,
  billing mode, region, service tier, quantization, reasoning effort, and agent
  harness are identity-bearing whenever they can change price or behavior.
- Caller-supplied workload, work-unit, attempt, and result identifiers must be
  local pseudonyms. Never reuse an upstream session, thread, run, or call id.
- The judged `work_unit_success` is supplied by the workload evaluator. An
  adapter does not infer task success from a framework's process exit alone.
- Attempt- and work-unit aggregates leave `model_request_count` unknown unless
  the reviewed source exposes a coherent count. Unknown is not zero.
- Output and reasoning meters are made disjoint only when the upstream
  contract says reasoning is included in total output. A missing split remains
  unknown rather than being estimated.
- Failed executions remain useful reliability observations. Usage and cost
  fields absent before failure remain unknown.

Cost provenance is kept explicit. Claude's SDK estimate maps only to
`estimated_total_cost_usd`. Hermes maps provider-reported, estimated, and
included-in-a-contract cost states to `provider_reported_total_cost_usd`,
`estimated_total_cost_usd`, and an explicit-zero
`provider_marginal_cost_usd`, respectively. An unknown Hermes ledger component
poisons the aggregate cost instead of being treated as free. Codex and
OpenClaw do not populate cost.

A USD formula must declare the matching `cost_basis`, such as
`estimated_total`, `provider_reported_total`, `billed_total`,
`provider_marginal`, or `reconstructed_components`. Do not mix an all-in total
with token/cache/tool components in the same formula. In particular,
provider-marginal zero can mean “no additional charge under this plan”; it
does not mean the workload had zero total economic cost.

The static overlap check recognizes the four canonical all-in signal families:
`estimated_total_cost_usd`, `provider_reported_total_cost_usd`,
`billed_total_cost_usd`, and `provider_marginal_cost_usd` (including their
per-unit suffixes). Other `signals.*usd*` names are treated as operator-declared
components under `reconstructed_components`; do not alias an invoice or other
all-in total to a component-looking custom name. Explicit per-signal accounting
roles are planned for a later schema revision.

## Codex JSONL

`adapt_codex_exec_jsonl` accepts exactly one terminal turn. Item payloads,
thread ids, commands, paths, messages, and raw errors are parsed only far
enough to validate event shape and are never copied to the trace. A failed
turn emits an attempt row with unknown usage. For `0.144.2`, inclusive input
and cache reads are retained but uncached input and cache writes are unknown;
`0.151.0` can reconstruct the disjoint buckets from its cache-write meter.

The caller must attest that the selected provider/model was the turn's only
route. If the `OfferingKey` includes fields absent from JSONL, such as an
endpoint or service tier, `route_details_attested=True` is also required.

```python
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from model_skyline.adapters.codex import adapt_codex_exec_jsonl
from model_skyline.models import OfferingKey

trace = adapt_codex_exec_jsonl(
    Path("private/codex-events.jsonl"),
    codex_version="0.144.2",
    model_route_attested=True,
    selected_provider="openai",
    selected_model="gpt-5.4",
    route_details_attested=False,
    timestamp=datetime(2026, 8, 30, 20, 0, tzinfo=UTC),
    workload_id="synthetic-coding",
    workload_version="v1",
    work_unit_id="case-0001",
    offering=OfferingKey(
        offering_id="openai/gpt-5.4@codex-synthetic",
        model_id="gpt-5.4",
        provider="openai",
        agent_harness="codex",
    ),
    result_id="result-0001",
    attempt_id="attempt-0001",
    work_unit_success=Decimal("1"),
)
```

Keep the source JSONL private: `codex exec --json` item events can contain
prompts, responses, commands, paths, and tool data even though the returned
trace does not.

## Claude Agent SDK result

`adapt_claude_result` reads only the typed result's terminal subtype,
`is_error`, `total_cost_usd`, and single-entry `model_usage`. The result text,
structured output, tool payloads, session id, transcript path, working
directory, and environment are outside the adapter boundary.

`model_usage` is cumulative for a query or post-reset streaming segment and
may cover the main agent, subagents, fallbacks, and internal pipeline calls.
The caller must attest that the input is the final cumulative result and that
the entire segment used one model route and one stable pricing basis. Multiple
model keys are rejected. In the pinned Python SDK type, `canonicalModel` and
`provider` are optional, while the nested `ModelUsage` `TypedDict` omits
`costBasis`. Claude Code's cost-tracking documentation says CLI 2.1.246 and
newer emit `costBasis` at runtime. The adapter accepts only reviewed CLI 2.1.251
via a caller-supplied version assertion and validates that runtime extension when
present; it neither inspects the installed CLI nor treats `costBasis` as a
field guaranteed by the SDK 0.2.148 static type. Callers must independently
verify the installed bundled CLI before supplying its version. For a metered
result, a present `costBasis` must be `list` or `managed`; `unknown` fails
closed. The required
`single_route_and_pricing_basis_attested` caller mapping binds the model,
provider, and stable pricing basis when optional metadata is absent. Present
`canonicalModel` or `provider` values must match the mapping. This attestation
does not turn the SDK's client estimate into an invoice: cost is still emitted
only as `estimated_total_cost_usd`. A documented
`error_during_execution` crash validates surviving model/provider identity but
does not inspect an unused pricing-basis marker. It is retained with unknown
usage and cost because its apparent zero counters are not trustworthy
measurements.

```python
from datetime import UTC, datetime
from decimal import Decimal

from model_skyline.adapters.claude import ClaudeRouteMapping, adapt_claude_result
from model_skyline.models import OfferingKey

route = ClaudeRouteMapping(
    offering=OfferingKey(
        offering_id="anthropic/claude-synthetic@agent-sdk",
        model_id="claude-synthetic",
        provider="anthropic",
        agent_harness="claude-agent-sdk",
    ),
    model_usage_key="claude-synthetic",
    upstream_provider="firstParty",
    single_route_and_pricing_basis_attested=True,
    route_details_attested=False,
)

# `final_result` is the final typed SDK ResultMessage, not a dict or transcript.
trace = adapt_claude_result(
    final_result,
    sdk_version="0.2.148",
    claude_code_version="2.1.251",
    final_cumulative_result=True,
    accounting_scope="single_query",
    timestamp=datetime(2026, 8, 30, 20, 0, tzinfo=UTC),
    workload_id="synthetic-research",
    workload_version="v1",
    work_unit_id="case-0002",
    route=route,
    result_id="result-0002",
    attempt_id="attempt-0002",
    work_unit_success=Decimal("0.75"),
)
```

Claude's aggregate `cacheCreationInputTokens` maps to the retention-neutral
cache-write meter; it is not relabeled as a five-minute or one-hour write.
`outputTokens` remains inclusive output because this surface has no trustworthy
visible-output/reasoning split. SDK costs are client estimates, not billed
amounts.

## OpenClaw trusted projection

The OpenClaw adapter does not accept transcripts or complete plugin-hook
payloads. A trusted local collector must verify the ended core model-call
lifecycle and correlate it to a trusted per-attempt `run.started` event. The
relationship is exact: both have the same trace id, and the model-call span's
`parentSpanId` equals the `run.started` span id. The spans themselves are not
identical. The collector assigns a monotonic one-based attempt ordinal before
removing private fields, enriching workload and judged outcome, and signing the
exact safe envelope. `callId` counters restart for each attempt, and the reviewed
stock release does not put an ordinal on terminal model-call events, so terminal
diagnostics alone cannot satisfy projector version 3. The HMAC protects the
projector-to-adapter boundary; it is not evidence that an arbitrary caller's
JSON originated inside OpenClaw.

Terminal model-call diagnostics are asynchronous while attempt boundaries are
synchronous. `waitForDiagnosticEventsDrained()` waits only through the queue
sequence captured when it is called; newer concurrent events may still be
pending, and the global drop summary is emitted only after the queue becomes
globally empty. Calling that helper is therefore not, by itself, proof of a
complete segment. The collector must retain trace-parent mappings across
retries and independently prove that it covered the complete relevant sequence
and drop epoch, or use exclusive/quiescent lifecycle instrumentation that
establishes expected call cardinality and observes the queue globally empty
after the drain. Any `diagnostic.async_queue.dropped` for the covered epoch
fails the entire segment. The collector publishes the proven-complete segment
atomically; only then may it set `segmentEventsComplete` to `true`. Terminal
diagnostics alone cannot establish expected cardinality because a start event
may itself have been dropped.

A terminal event contains the latest observed AssistantMessage usage, not a sum
over hidden transport retries. `usageComplete: true` therefore requires the
trusted collector to prove that retries/replay/fallback were disabled for that
call, or to independently aggregate every cost-bearing provider request. If it
cannot, it must set `usageComplete: false` and omit `usage`; the canonical token
meters remain unknown rather than undercounted.

The runtime provider/model, expected API/transport, and supplied offering must
match exactly. Narrow offering fields absent from the event require route
attestation. The upstream event must say `observationUnit: request`, but the
canonical row is conservatively `model_call` because a logical OpenClaw call can
hide multiple provider transport requests; actual request count stays unknown.
Missing or incomplete usage remains unknown. OpenClaw's time-to-first-byte and
full call duration are coherence-checked but are not mislabeled as TTFT or token
throughput.

```python
from model_skyline.adapters.openclaw import (
    adapt_openclaw_event,
    compute_openclaw_projection_signature,
)
from model_skyline.models import OfferingKey

# Inject `collector_key` from an operator secret store; never publish it.
projection = {
    "schema_version": "model-skyline/openclaw-model-call/v1alpha3",
    "openclaw_version": "2026.8.1",
    "collector_id": "model-skyline/openclaw-trusted-projector",
    "collector_version": "3",
    "collector_signature": "0" * 64,
    "workload_id": "synthetic-tool-use",
    "workload_version": "v1",
    "work_unit_id": "case-0003",
    "work_unit_success": "1",
    "runAttempt": 1,
    # Set only after independently proving segment completeness and no drops.
    "segmentEventsComplete": True,
    # Set only when retries are impossible or all cost-bearing attempts were aggregated.
    "usageComplete": True,
    "event": {
        "type": "model.call.completed",
        "ts": 1_788_123_456_789,
        "seq": 1,
        "runId": "synthetic-run-0003",
        "callId": "synthetic-call-0003",
        "provider": "synthetic-provider",
        "model": "synthetic-model",
        "api": "messages",
        "transport": "https",
        "observationUnit": "request",
        "durationMs": 2500,
        "timeToFirstByteMs": 275,
        "usage": {"input": 1250, "output": 160, "cacheRead": 8000},
    },
}
projection["collector_signature"] = compute_openclaw_projection_signature(
    projection,
    collector_key=collector_key,
)
trace = adapt_openclaw_event(
    projection,
    offering=OfferingKey(
        offering_id="synthetic-provider/synthetic-model@openclaw",
        model_id="synthetic-model",
        provider="synthetic-provider",
        agent_harness="openclaw",
    ),
    collector_key=collector_key,
    expected_api="messages",
    expected_transport="https",
    route_details_attested=False,
)
```

Raw run and call ids plus workload identity, work-unit identity, and the
correlated attempt ordinal are domain-separated and HMAC-pseudonymized with the
collector key before publication. This prevents both unkeyed recovery of
low-entropy ids and false duplicate collisions when an upstream run id is reused
across work units. The safe envelope rejects unknown fields and credential-,
URL-, and path-shaped metadata. The collector key and any pre-projection event
remain private.

## Hermes report and state database

Hermes exposes work-unit aggregates rather than request events. The usage-file
import requires an operator attestation that main-loop, fallback, and auxiliary
calls all stayed on one model/provider/base-URL/billing-mode route. If a service
tier was requested, fulfillment must be attested because the report records
intent, not fulfillment.

The stricter SQLite importer opens the source database read-only and creates a
bounded private snapshot with SQLite's online-backup API. This captures one
coherent view including committed pages that remain only in a live WAL; it does
not byte-copy a potentially stale main file. Main, WAL, shared-memory, and
rollback-journal files must be regular, non-symlink inputs whose combined size
is at most 256 MiB. The importer then requires schema 26, sums all main and
auxiliary `session_model_usage` ledger rows, and reconciles the main ledger with
the legacy session summary. Every ledger row must use the exact mapped route.
Sessions containing an `absolute=True` counter residual without an attributable
ledger row are outside the supported subset.

```python
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from model_skyline.adapters.hermes import (
    HermesRouteMapping,
    HermesSessionMapping,
    import_hermes_session,
    import_hermes_usage_report,
)
from model_skyline.models import OfferingKey, WorkloadReference

mapping = HermesSessionMapping(
    session_id="synthetic-session-0004",
    hermes_version="0.20.6",
    workload=WorkloadReference(
        id="synthetic-agent-session",
        version="v1",
        unit="agent_session",
    ),
    route=HermesRouteMapping(
        offering=OfferingKey(
            offering_id="synthetic-provider/synthetic-model@direct",
            model_id="synthetic-model",
            provider="synthetic-provider",
            endpoint="https://synthetic-provider.invalid/v1",
            billing_mode="direct",
            service_tier="standard",
            agent_harness="hermes-agent",
        ),
        model="synthetic-model",
        billing_provider="synthetic-provider",
        billing_base_url="https://synthetic-provider.invalid/v1",
        billing_mode="direct",
        usage_report_single_route_attested=True,
        service_tier_fulfilled_attested=True,
        route_details_attested=False,
    ),
    work_unit_success=Decimal("1"),
)

# Inject `identity_key` from an operator secret store. It pseudonymizes the
# private Hermes session id and must not be published.
report_trace = import_hermes_usage_report(
    Path("private/hermes-usage.json"),
    mapping=mapping,
    observed_at=datetime(2026, 8, 30, 20, 0, tzinfo=UTC),
    identity_key=identity_key,
)
database_trace = import_hermes_session(
    Path("private/hermes-state.db"),
    mapping=mapping,
    identity_key=identity_key,
)
```

The report reader is size-bounded, rejects duplicate JSON keys and nonstandard
numbers, and does not follow symlinks. The database backup is read-only,
time/size/row/VM-step bounded, rejects unsafe SQLite companion files, and never
selects transcript content. The private session id is HMAC-pseudonymized; only
the operator-reviewed offering id is retained.

## Real benchmark smoke validation

The framework work was also exercised against fresh imports of the pinned
Aider Polyglot and MCPMark Verified sources. These validate frontier behavior;
they are not live framework telemetry.

The Aider import accepted 20 of 69 rows and rejected 49 incomplete, unpriced,
zero-cost, incoherent, or dirty-harness rows. Its historical
cost-per-attempted-versus-solve frontier contained:

| Historical leaderboard offering | Reported USD per attempted case | Solve rate |
| --- | ---: | ---: |
| `gpt-oss-120b` high | 0.0032916 | 0.4178 |
| DeepSeek V3.2 chat | 0.0038916 | 0.7022 |
| DeepSeek V3.2 reasoner | 0.0057978 | 0.7422 |
| GPT-5 low | 0.04609 | 0.8133 |
| GPT-5 medium | 0.07864 | 0.8667 |
| GPT-5 high | 0.12926 | 0.8800 |

On cost per solved case, `gpt-oss-120b` left the frontier and the other five
remained. These amounts are the benchmark's historical aggregate cost under
its recorded harness and run conditions. They are not current API prices,
current availability, cache-aware bills, or controlled same-date comparisons.

MCPMark showed that frontier membership changes by workload even without a
cost axis. On GitHub quality versus time, representative frontier points were
DeepSeek V4 Pro Max (0.4783, 156.93 s), Claude Fable 5 Max (0.8261, 256.08 s),
and GPT-5.5 xhigh (0.9565, 308.06 s). On Playwright quality versus input
tokens, Kimi K2.7 Code reached (0.84, 1,475,166) while GPT-5.6 Sol Max reached
(1.00, 1,556,260). MCPMark supplies no exact provider route, cache telemetry,
or bill, so the adapter intentionally emits no cost and no cost frontier.

## Operational boundary

Canonical traces are safer and narrower than their inputs, but adapter
validation is not a substitute for a data-classification review. Keep raw
framework streams, reports, databases, HMAC keys, and identity keys outside a
public output tree. Review offering ids and workload metadata before
publication. Trace aggregation applies explicit input/row/cardinality limits
and confines bounded DuckDB spill files to its private temporary snapshot, but
operators processing hostile files should still use OS-level resource quotas.
A valid HMAC proves possession of a local key, not the truth of an operator
attestation, judged outcome, provider bill, or upstream lifecycle.
