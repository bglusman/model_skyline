# Local Terminal-Bench quality pilot

This pilot adds real, verifier-scored agent work without pretending that five
chosen tasks estimate all 89 Terminal-Bench 2.1 tasks. The immutable protocol is
[`harbor-quality-pilot.yaml`](harbor-quality-pilot.yaml). It pins the benchmark
and Harbor revisions, Terminus-2 settings, exact task digests, local routes and
system profiles, validity gates, repetitions, and promotion rules.

New candidates must not be appended to that digest-bound file after results
are published. The Qwen3.8 Flash Coder experiment therefore uses the separate
additive [`candidate screen`](harbor-quality-screen-qwen38-flash-coder.yaml);
its failed smoke does not invalidate or join this five-task population.

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
- measured pilot success versus mean uncached input tokens per complete
  five-task repetition, a cache-aware agent-compute measure that also penalizes
  excess turns without penalizing additional measurement runs, provided every
  API request has complete usage accounting.

All three require at least 60% success on the exact task set. The cache-demand
frontier additionally requires zero incomplete API requests: a timeout can hide
the final request's usage, and treating the recorded subtotal as exact would
reward failure. The quality threshold prevents a fast but mostly useless route
from becoming a recommended resident. The existing
128K, cache, session-endurance, and operational frontiers remain separate because
these short repository tasks do not test those capabilities.

Exact cache reuse percentage, total output tokens, and successful-task-only p95
are retained as diagnostics, but they are not recommendation axes. When a run
has an incomplete API request, only explicitly labeled recorded-token lower
bounds remain in metadata. Cache reuse can be gamed by taking more turns, and
successful-only latency hides the 900-second cost of a timed-out task. The
decision latency therefore includes every valid task, including
quality-attributable failures. Context is controlled as an
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

[`normalize_harbor_pilot.py`](normalize_harbor_pilot.py) accepts one or more
prompt-free summaries per exact candidate and optional job-matched memory
captures. Every candidate in one catalog must have the same repetition count.
It emits an ordinary `ObservationCatalog`, with the Terminus/Harbor
configuration hashed into `OfferingKey.agent_harness` so these results cannot
be silently joined to the same inference server measured under the lightweight
OpenAI matrix harness.

The historical [`frontiers.yaml`](frontiers.yaml) snapshots retain the initial
one-attempt cohort. Once each candidate supplied to a catalog has the
protocol's five jobs, evaluate it with
[`harbor-repeated-frontiers.yaml`](harbor-repeated-frontiers.yaml). Those three
frontiers require bounds and use robust dominance, so one route dominates
another only when their observed per-run ranges do not overlap adversely on
either axis. These ranges measure repeatability of the exact pilot; they are not
confidence intervals for the full 89-task benchmark.

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

The latency axis uses a deterministic Hyndman–Fan type-7 p95 over all complete
task wall times. Repeated quality is the pooled verifier success rate. Quality,
token demand, and per-run peak-memory ranges are retained as repeatability
bounds; p95 latency bounds also enclose the pooled p95 when type-7 interpolation
places it just outside the individual-run p95 range. Token-demand axes use the
mean per complete task-set repetition rather than growing with the number of
runs. The memory signal is the maximum across repetitions and is omitted unless
every job's capture began before every task's agent execution and every task
has a positive kernel-accounted physical-footprint peak. An incomplete capture
therefore remains auditable metadata but is rejected from the memory frontier.

## First five five-task results

Ornith 1.5 oQ4e/F16-KV and Qwen3.8 Flash Next on DS4 each scored 3/5 (60%),
passing `fix-git`, `multi-source-data-merger`, and
`fix-code-vulnerability`. Both failed `build-cython-ext`; DS4 also timed out on
`cancel-async-tasks`, while Ornith completed that trial with reward zero.

| Exact route | Success | All-task p95 wall | Recorded uncached input lower bound | Recorded cache reuse | Recorded output lower bound | Peak process footprint |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Ornith 1.5 oQ4e, oMLX F16 KV | 3/5 | 857.665 s | 161,473 | 86.284% | 160,409 | ineligible capture |
| Qwen3.8 Flash Next, DS4 Q2/PLE-Q4_1 | 3/5 | 923.973 s | 45,888 | 93.448% | 49,492 | 5,461,911,280 B |
| Qwen3.8 27B oQ4e, oMLX F16 KV | 2/5 | 923.979 s | 87,051 | 69.748% | 58,939 | 23,198,069,456 B |
| Qwen3.8 27B oQ4e, oMLX F16 KV, low reasoning/4K thinking | 4/5 | 804.267 s | 98,321 | 78.375% | 31,942 | 23,782,290,440 B |
| Muse Glimmer 30B Dynamic Q4_K_XL | 3/5 | 836.343 s | 51,424 exact | 95.738% | 40,157 exact | 3,484,714,192 B |

The bounded-reasoning Qwen route passed every task except `build-cython-ext`,
improving the identical artifact/runtime family from 2/5 to 4/5. It is the sole
quality/latency resident at 80% and 804.267 seconds p95. Its 23.782 GB process
footprint and higher quality form one end of the quality/memory frontier, while
Muse's 3.485 GB footprint and 60% quality form the other. The tuned route's one
timeout ended during an API request, so its 98,321 uncached-input and 31,942
output-token subtotals remain lower bounds and are not cache-frontier evidence.

The footprint is macOS's kernel-accounted active process working set;
it does not replace the separately retained 76.8 GB composite artifact size and
does not count file-backed demand-paged storage as if it were anonymous memory.
Muse passed `cancel-async-tasks`, `fix-git`, and `multi-source-data-merger`.
Its one timeout occurred while the agent was waiting on the terminal rather
than an API request, so all request token totals are complete. It is the first
and only cache-demand resident.
Dense Qwen3.8 passed `fix-git` and `multi-source-data-merger`, but its three
timeouts leave it below the 60% quality gate and make its token subtotals lower
bounds as well. Its full-run memory capture is valid, but the quality gate keeps
it off the memory frontier. The paired profile result shows that reasoning and
agent configuration are decision-relevant offering identity, not harmless
metadata; it does not establish a transferable gain outside this five-task,
single-attempt pilot.

The first Ornith memory capture began after the job and used an earlier sampler
without per-task coverage flags. Its observed peaks remain a private diagnostic,
but the normalizer intentionally emits no memory axis from it. A clean capture
must accompany a rerun before Ornith can enter the quality/memory frontier.

## First matched replications

Both dense-Qwen profiles now have two identical five-task jobs in the
[`paired catalog`](generated/harbor-pilot5-qwen38-paired-repeat2-catalog.json):

| Exact profile | Pooled success | Per-run range | Pooled all-task p95 | Retained p95 range | Peak process footprint |
| --- | ---: | ---: | ---: | ---: | ---: |
| Default reasoning | 5/10 (50%) | 40–60% | 924.873 s | 923.099–924.873 s | 23,533,990,632 B |
| Low reasoning / 4K thinking | 7/10 (70%) | 60–80% | 916.955 s | 804.267–916.955 s | 23,782,290,440 B |

The bounded-reasoning route's second job scored 3/5 versus 4/5 initially. Both
runs passed `fix-git`, `multi-source-data-merger`, and
`fix-code-vulnerability`; `cancel-async-tasks` changed from pass to fail, while
`build-cython-ext` failed both times. The default route improved from 2/5 to
3/5 because `fix-code-vulnerability` changed from fail to pass. Its other four
task outcomes were unchanged, although two passing tasks reached the timeout
boundary in the second job and retained the attributable timeout metadata.

This confirms that the initial 80% versus 40% point difference was not stable
enough to promote as a default. The pooled result favors bounded reasoning by
20 points and about eight seconds of p95 latency, but the quality ranges touch
at 60% and the latency ranges overlap. The published one-run frontiers remain
historical point evidence; the separate robust frontiers require the protocol's
five attempts for every candidate in the compared cohort.

Both repeat bundles remain ineligible for the cache-demand axis: the default
jobs contain six incomplete API requests in total, and the bounded-reasoning
initial job contains one. Prompt-free second-run summaries are retained for the
[`default`](raw/harbor-pilot5-qwen38-baseline-f16kv-repeat2-summary.json) and
[`bounded-reasoning`](raw/harbor-pilot5-qwen38-low-think4k-repeat2-summary.json)
profiles.
