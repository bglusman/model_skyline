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

The pilot materializes two useful local frontiers after the validity gates pass:

- measured pilot success versus p95 wall time of successful tasks; and
- measured pilot success versus peak physical footprint.

Both require at least 60% success on the exact task set. The threshold prevents a
fast but mostly useless route from becoming a recommended resident. The existing
128K, cache, session-endurance, and operational frontiers remain separate because
these short repository tasks do not test those capabilities.

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
