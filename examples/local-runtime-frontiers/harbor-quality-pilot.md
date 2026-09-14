# Local Terminal-Bench quality pilot

This pilot adds real, verifier-scored agent work without pretending that five
chosen tasks estimate all 89 Terminal-Bench 2.1 tasks. The immutable protocol is
[`harbor-quality-pilot.yaml`](harbor-quality-pilot.yaml). It pins the benchmark
and Harbor revisions, Terminus-2 settings, exact task digests, local routes and
system profiles, validity gates, repetitions, and promotion rules.

## What the pilot can establish

The five-task set spans Git recovery, build/dependency repair, schema-aware ETL,
security patching, and concurrency debugging. A complete run is `measured`
evidence for the named five-task workload. It is not a Terminal-Bench 2.1 score,
not an `estimated` full score, and not a calibrated coreset. The smoke task is
only `proxy` evidence that the model, agent parser, terminal, and verifier form a
working loop.

The pilot materializes three useful local frontiers after the validity gates pass:

- measured pilot success versus p95 wall time across every valid task,
  including quality-attributable timeouts; and
- measured pilot success versus peak physical footprint; and
- measured pilot success versus total uncached input tokens across all five
  tasks, a cache-aware agent-compute measure that also penalizes excess turns.

Both require at least 60% success on the exact task set. The threshold prevents a
fast but mostly useless route from becoming a recommended resident. The existing
128K, cache, session-endurance, and operational frontiers remain separate because
these short repository tasks do not test those capabilities.

Cache reuse percentage, total output tokens, and successful-task-only p95 are
retained as diagnostics, but they are not recommendation axes. Cache reuse can
be gamed by taking more turns, and successful-only latency hides the 900-second
cost of a timed-out task. The decision latency therefore includes every valid
task, including quality-attributable failures. Context is controlled as an
eligibility/cohort property here: all routes receive the same 114,688-token
input and 16,384-token output envelope with the same compaction policy. Validated
maximum context remains a separate retrieval frontier rather than a configured
capacity claim.

## Harness-validity lesson from the first smoke

The first Qwen3.8 baseline smoke appeared to solve `fix-git` in its trajectory,
but Harbor reported reward zero. That reward is excluded: the verifier's own
dependency bootstrap failed TLS verification behind the Docker Desktop corporate
proxy, `uvx` never ran, and no CTRF artifact was produced. This is an
infrastructure-invalid trial, not a model failure.

The local rerun uses a private Docker Compose overlay that mounts the machine's
corporate trust root and a checksum-verified Linux amd64 `uv` 0.9.5 binary. The
overlay changes only verifier bootstrap connectivity; it does not alter the task,
solution, tests, or reward. The certificate and machine-specific paths are kept
outside this repository. A valid Terminal-Bench 2.1 trial must produce a
parseable `verifier/ctrf.json`; all 89 verifier scripts request that artifact.

## Execution order

Runs are serial and model-batched through llama-swap's exclusive `local-memory`
group. This avoids measuring repeated unload/load churn and prevents two heavy
runners from competing for unified memory. The common agent budget advertises
114,688 input tokens plus a 16,384-token output ceiling, exactly fitting the
131,072-token DS4 and Muse routes. Summarization starts only when fewer
than 8,192 tokens remain.

Qwen3.8 baseline, Ornith baseline, DS4 Flash Next, and Muse target-only enter the
smoke. DFlash variants are paired implementation-equivalence checks: they do not
inherit baseline quality merely because speculative verification should be exact.
The Muse DFlash cold/warm semantic divergence makes that rule especially
important.

After every route has a valid smoke, run the five-task set once. Repeat it five
times only for candidates whose uncertainty can alter a frontier or the default
and fallback order. A full 89-task, five-attempt run is reserved for promoted
candidates. A subset-to-full estimate remains prohibited until held-out local
quantization validation supplies an explicit error bound through ModelSkyline's
paired-estimate contract.

The future full phase is resolved by
[`terminal-bench-2.1-task-manifest.json`](terminal-bench-2.1-task-manifest.json):
all 89 task names and Harbor 0.23.0 content digests computed from the pinned
benchmark revision. The protocol pins the manifest's own SHA-256, and protocol
mode checks its revision, count, uniqueness, and task locks before accepting a
full-run summary.

## Auditable smoke evidence

The protocol validator now accepts all four verifier-valid `fix-git` summaries
without retaining prompts or model messages:

| Route | Protocol status | Reward | Agent execution | Input / cache / output tokens | Parser feedback |
| --- | --- | ---: | ---: | ---: | ---: |
| Ornith 1.5 baseline/F16 KV | v1 common budget | 1 | 66.475 s | 33,447 / 18,432 / 5,382 | 0 |
| Muse Glimmer target-only | v1 common budget | 1 | 203.477 s | 20,707 / 17,808 / 4,605 | 0 |
| Qwen3.8 Flash Next DS4 | v1 common budget | 1 | 260.620 s | 83,266 / 75,827 / 9,783 | 0 |
| Qwen3.8 baseline/F16 KV | v1 common budget | 1 | 407.209 s | 62,543 / 43,008 / 10,839 | 1 warning |

These are harness-validation observations, not a quality ranking. An older Qwen
infrastructure rerun advertised a 180,000-token input budget before the common
114,688-input/16,384-output envelope was frozen. The protocol validator rejects
that run, and it is not published. Its replacement and the other three routes
use the frozen budget; all produced a 2/2 pytest CTRF result and reward 1.

[`summarize_harbor_local_job.py`](summarize_harbor_local_job.py) fails closed on
missing or inconsistent verifier evidence, validates the durable task digest in
Harbor's trial lock, records the sanitized agent settings and hashes the job,
trial, trajectory, CTRF, stdout, and reward artifacts. It intentionally omits
prompts, terminal content, and model messages. The current summaries are
[`raw/harbor-smoke-ornith15-baseline-f16kv-fix-git-summary.json`](raw/harbor-smoke-ornith15-baseline-f16kv-fix-git-summary.json),
[`raw/harbor-smoke-muse-glimmer-target-fix-git-summary.json`](raw/harbor-smoke-muse-glimmer-target-fix-git-summary.json),
[`raw/harbor-smoke-qwen38-flash-next-ds4-fix-git-summary.json`](raw/harbor-smoke-qwen38-flash-next-ds4-fix-git-summary.json),
and
[`raw/harbor-smoke-qwen38-baseline-f16kv-fix-git-summary.json`](raw/harbor-smoke-qwen38-baseline-f16kv-fix-git-summary.json).

Render Harbor's machine-local `JobConfig` directly from the pinned protocol so
candidate route, task order, context/output budget, parser, sampling, compaction,
and concurrency do not drift between runs. Absolute task/job/overlay paths remain
in the private rendered config and are not publication artifacts.

```console
python examples/local-runtime-frontiers/render_harbor_pilot_config.py \
  --protocol examples/local-runtime-frontiers/harbor-quality-pilot.yaml \
  --candidate qwen38_flash_ds4 \
  --task-set pilot_5 \
  --tasks-directory /path/to/terminal-bench-2-1/tasks \
  --jobs-directory /path/to/harbor/jobs/local-quality-pilot \
  --job-name pilot5-v1-qwen38-flash-next-ds4 \
  --api-base http://127.0.0.1:8090/v1 \
  --extra-docker-compose /path/to/private-docker-overlay.yaml \
  --output /path/to/private-harbor-job-config.json

harbor run --config /path/to/private-harbor-job-config.json --yes
```

```console
python examples/local-runtime-frontiers/summarize_harbor_local_job.py \
  --job-directory /path/to/harbor/job \
  --protocol examples/local-runtime-frontiers/harbor-quality-pilot.yaml \
  --candidate ornith15_baseline \
  --task-set smoke \
  --output /path/to/prompt-free-summary.json
```

The legacy `result.json` task checksum and the durable `lock.json` task digest
are different Harbor hash schemes. The manifest pins and validates the latter;
the summary retains both rather than treating them as interchangeable.
Protocol mode also requires the pinned Harbor revision, Terminus identity,
parser and sampling settings, context/output budget, summarization settings,
concurrency, route, task set, and system-profile digest.

## Agent-task memory capture

The quality/memory frontier uses macOS's kernel-accounted physical footprint,
sampled during the agent task—not model-file size, RSS alone, or a peak borrowed
from a different runtime profile. Start
[`capture_harbor_runner_memory.py`](capture_harbor_runner_memory.py) before the
matching Harbor job. It waits for the job lock, verifies a serial single-model
batch, samples only the literal-matched runner process, and binds its output to
the job-lock hash. The prompt-free output records per-task peaks and whether
sampling began before each task's agent-execution interval.

```console
python examples/local-runtime-frontiers/capture_harbor_runner_memory.py \
  --job-directory /path/to/jobs/pilot5-v1-ornith15-baseline \
  --expected-model ornith-1.5-35b-a3b-oq4e-mtp:baseline-f16kv \
  --process-match omlx-server \
  --job-timezone America/New_York \
  --output /path/to/jobs/pilot5-v1-ornith15-baseline/runner-memory.json
```

Only tasks with `capture_started_before_agent_execution: true` can contribute to
the memory axis. This keeps a late-attached diagnostic capture useful without
silently treating its partial first-task series as a measured peak.
The timezone is required because Harbor 0.23 serializes local job timestamps
without a UTC offset; the sampler records the IANA zone used to interpret them.

## Timeout and exception policy

An agent that consumes the task's fixed 900-second budget without finishing is
a measured failure of that exact model/runtime/harness route, not an
infrastructure exclusion. The protocol therefore permits only
`AgentTimeoutError` and `AgentSafetyRefusalError` as quality-attributable trial
exceptions, and only when the verifier still emits consistent CTRF and reward
artifacts. Their exception type is retained without its path-bearing traceback.
Verifier timeouts, missing rewards, authentication/model lookup failures, and
all other exceptions remain infrastructure-invalid and fail closed. The job's
errored-trial count must exactly match the accepted attributable exceptions.
For such an exception, the trajectory may retain exactly one final episode whose
in-flight API request never produced a duration; the summary records that as one
`incomplete_api_requests`. A normal trial still requires one completed API timing
per episode.

## Normalize and evaluate

[`normalize_harbor_pilot.py`](normalize_harbor_pilot.py) accepts one prompt-free
summary per exact candidate and optional job-matched memory captures. It emits
an ordinary `ObservationCatalog`, with the Terminus/Harbor configuration hashed
into `OfferingKey.agent_harness` so these results cannot be silently joined to
the same inference server measured under the lightweight OpenAI matrix harness.

```console
python examples/local-runtime-frontiers/normalize_harbor_pilot.py \
  --protocol examples/local-runtime-frontiers/harbor-quality-pilot.yaml \
  --hardware examples/local-runtime-frontiers/hardware/macbook-m5max-64.json \
  --task-set pilot_5 \
  --summary /path/to/ornith-summary.json \
  --summary /path/to/ds4-summary.json \
  --memory-capture /path/to/ornith-runner-memory.json \
  --memory-capture /path/to/ds4-runner-memory.json \
  --output examples/local-runtime-frontiers/generated/harbor-pilot5-quality-catalog.json

modelskyline evaluate examples/local-runtime-frontiers/frontiers.yaml \
  examples/local-runtime-frontiers/generated/harbor-pilot5-quality-catalog.json \
  local-agent-quality-latency --format json \
  --output examples/local-runtime-frontiers/generated/harbor-pilot5-quality-latency-frontier.json \
  --as-of 2026-09-14T02:00:00Z
```

The latency axis uses a deterministic Hyndman–Fan type-7 p95 over the five
complete task wall times. The memory signal is omitted unless capture began
before every task's agent execution and every task has a positive
kernel-accounted physical-footprint peak. An incomplete capture therefore
remains auditable metadata but is rejected from the memory frontier.

## First two five-task results

Ornith 1.5 oQ4e/F16-KV and Qwen3.8 Flash Next on DS4 each scored 3/5 (60%),
passing `fix-git`, `multi-source-data-merger`, and
`fix-code-vulnerability`. Both failed `build-cython-ext`; DS4 also timed out on
`cancel-async-tasks`, while Ornith completed that trial with reward zero.

| Exact route | Success | All-task p95 wall | Uncached input | Cache reuse | Output | Peak process footprint |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Ornith 1.5 oQ4e, oMLX F16 KV | 3/5 | 857.665 s | 161,473 | 86.284% | 160,409 | ineligible capture |
| Qwen3.8 Flash Next, DS4 Q2/PLE-Q4_1 | 3/5 | 923.973 s | 45,888 | 93.448% | 49,492 | 5,461,911,280 B |

At equal measured success, Ornith is the latency resident and DS4 is the
uncached-input resident. DS4 is also the first eligible process-footprint
resident. The footprint is macOS's kernel-accounted active process working set;
it does not replace the separately retained 76.8 GB composite artifact size and
does not count file-backed demand-paged storage as if it were anonymous memory.
The dense Qwen3.8 and Muse Glimmer runs remain necessary before selecting a
default from this pilot.

The first Ornith memory capture began after the job and used an earlier sampler
without per-task coverage flags. Its observed peaks remain a private diagnostic,
but the normalizer intentionally emits no memory axis from it. A clean capture
must accompany a rerun before Ornith can enter the quality/memory frontier.
