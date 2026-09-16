# Local intelligence efficiency

The short version: ModelSkyline treats energy efficiency as two ordinary
two-dimensional frontiers, not as one magic score.

1. **Quality × average power** asks which choices deliver the most correct work
   while drawing the fewest watts during that work. This is useful when heat,
   fan noise, a power cap, or battery drain rate matters.
2. **Quality × energy per completed item** asks which choices deliver the most
   correct work while using the fewest joules per benchmark item. This is the
   better default for electricity use and time-to-solution: a high-power machine
   can finish quickly enough to use less total energy.

`accuracy / watt` (IPW) and `accuracy / joule` (IPJ) remain useful labels. They
are derived from those raw axes and are not the frontier definition. A ratio can
hide whether a result comes from better quality or lower resource use, and its
ranking depends on the chosen units. Keeping both axes visible preserves the
actual choice.

## What counts as one candidate

An efficiency resident is not just a model name. It is:

> model + exact weights/quantization + runtime/configuration + hardware + power
> policy + fixed workload

Power policy is part of the offering identity. On the same Mac, a 60 W USB-C
adapter run and a 140 W adapter run are separate candidates. So are Automatic,
Low Power, and High Power modes. On NVIDIA hardware, every configured GPU power
limit is a separate candidate. This makes power caps a useful experimental
dimension instead of hidden noise.

The physical machine can therefore appear several times on a frontier:

| Physical hardware | Exact service tier | Question it answers |
|---|---|---|
| M5 Max MacBook Pro | 60 W adapter, observed OS mode | What can the constrained office setup sustain? |
| M5 Max MacBook Pro | 140 W adapter, High Power | What does the same machine do without that input constraint? |
| RTX 5060 Ti | default limit | What is the stock discrete-GPU point? |
| RTX 5060 Ti | lower explicit power cap | How much quality/speed is retained per watt saved? |

Adapter wattage is a ceiling, not the measured workload draw. It belongs in
identity; the measured mean watts and joules belong on the axes.

## What is comparable

Every candidate on one frontier must use the same:

- task IDs and task bytes;
- attempt count and sampling settings;
- agent, tools, output budget, context and compaction policy;
- concurrency and cache state;
- quality scorer; and
- energy boundary.

The last item matters. The official Intelligence Per Watt harness currently
reports whole-SoC energy (`soc`: CPU + GPU + ANE) on Apple Silicon and GPU energy
(`gpu`) on NVIDIA. ModelSkyline refuses mixed bases in one imported run. It also
keeps Apple-SoC and NVIDIA-GPU frontiers separate by default: those readings do
not cover the same components. A cross-platform chart can be shown as
provisional, clearly labeled evidence, but an exact device comparison needs a
common wall-power meter or another common measurement boundary.

The first useful comparisons are therefore:

- exact model and quantization across the M1 and M5 Macs, using `soc` on both;
- multiple adapter/power-mode tiers on the M5, using the same model and tasks;
- multiple GPU power limits on the RTX 5060 Ti, using `gpu` throughout; and
- models within one exact hardware/power tier.

One smaller GGUF model that fits all three machines is useful as a mechanical
control. Larger machine-specific winners still belong in separate per-hardware
frontiers.

## Why hardware and power mode can change the winner

Efficiency does not vary by one uniform multiplier. Dense and sparse/MoE
models stress compute, memory bandwidth, CPU scheduling, and KV-cache traffic
differently. Runtime features such as prefix caching, speculative decoding,
batching, and CPU offload change those proportions again.

Two broad expectations are worth testing rather than assuming:

- **Watts may move less than joules.** A faster chip can draw more power but use
  less energy because it finishes sooner.
- **Power caps can improve efficiency before they hurt usefulness.** A cap may
  remove the least efficient top part of a frequency curve. Past a point,
  latency or quality failures erase that gain.

The Intelligence Per Watt paper reports that changing inference frameworks can
shift absolute efficiency by several percent while usually preserving much of
the ranking. Its hardware results also show larger changes in per-joule than
per-watt comparisons. Those are useful priors, not substitutes for measuring
the exact local runtime.

## Measurement protocol

ModelSkyline's initial protocol uses the official
[HazyResearch/intelligence-per-watt](https://github.com/HazyResearch/intelligence-per-watt)
harness pinned to reviewed commit
`645f68a65f5bb9b0296bc2840474ef86744af3c2`. The harness samples power at 50 ms
and supports Apple `powermetrics`, NVIDIA NVML, AMD ROCm SMI, and Intel RAPL.

Before a publishable run:

1. Record the exact hardware profile, adapter negotiation or GPU power cap, OS
   power mode, battery/charging state, runtime, artifact digest, context/KV
   configuration, cache state, and concurrency.
2. Keep the task cohort fixed and hash its manifest. A small screen must say
   `screen`; it must not be presented as a full benchmark.
3. Warm the model using the same policy for every candidate. Exclude model load
   from steady-state measurements, or define a separate cold-start workload.
4. Run one model as a batch. Do not interleave models and accidentally measure
   llama-swap unload/load churn.
5. Reject runs with missing scored items, zero telemetry, imputed energy, mixed
   energy bases, or truncated telemetry windows.
6. Repeat the complete run at least three times for screening and preferably
   ten times for publication-grade comparisons. Record ambient/thermal state
   and randomize tier order when practical.
7. Treat differences inside 15% as equivalent until repeatability or an
   external meter establishes a narrower error band. The software telemetry
   methodology itself is commonly estimated to be inaccurate by roughly
   10–15%.

For long agent sessions, report both a cold-prefix and warm-prefix cohort.
Prefix caching can reduce prefill work dramatically, so a single blended number
is not a stable description of agent use.

## Safe import into ModelSkyline

The upstream `analysis/accuracy.json` can contain prompts, reference answers,
and model responses. Do not commit it. Create a reviewed binding conforming to
[`intelligence-per-watt-import.schema.json`](../schemas/intelligence-per-watt-import.schema.json),
then project only its aggregates:

```bash
modelskyline import-intelligence-per-watt \
  /private/run/analysis/accuracy.json \
  /private/run/model-skyline-binding.json \
  --retrieved-at 2026-09-16T15:00:00Z \
  --output observations/ipw-catalog.json
```

The binding supplies the exact offering, workload label, dataset revision, task
manifest hash, attempt count, concurrency, and configuration hash. The output
workload version is derived from all comparability-critical run fields. The
adapter then verifies the reported accuracy and IPW/IPJ arithmetic, checks full
telemetry coverage, and emits only:

- `ipw_accuracy_percent`;
- `ipw_average_power_watts`;
- `ipw_average_energy_joules_per_item`;
- `ipw_intelligence_per_watt`; and
- `ipw_intelligence_per_joule`.

The first three are the primary frontier evidence. The two ratios are retained
for reporting and comparison with the paper.

## Frontier recipe

After copying the exact workload ID, version, and unit from the imported
catalog into a project config, use these definitions:

```yaml
metrics:
  ipw_quality:
    kind: signal
    signal: ipw_accuracy_percent
    unit: percent
    requirements:
      minimum_samples: 20
      require_source: true

  average_power:
    kind: signal
    signal: ipw_average_power_watts
    unit: W
    requirements:
      minimum_samples: 20
      require_source: true

  energy_per_item:
    kind: signal
    signal: ipw_average_energy_joules_per_item
    unit: J/item
    requirements:
      minimum_samples: 20
      require_source: true

frontiers:
  local-quality-power:
    workload: your-exact-imported-workload-id
    axes:
      - {metric: ipw_quality, goal: maximize}
      - {metric: average_power, goal: minimize, epsilon_relative: 0.15}
    order_by: ipw_quality
    uncertainty: point

  local-quality-energy:
    workload: your-exact-imported-workload-id
    axes:
      - {metric: ipw_quality, goal: maximize}
      - {metric: energy_per_item, goal: minimize, epsilon_relative: 0.15}
    order_by: ipw_quality
    uncertainty: point
```

The 15% epsilon says that telemetry differences inside the known measurement
noise do not preserve an otherwise inferior candidate. Per-item minima and
maxima are retained as audit metadata, not mislabeled as confidence intervals.
Once repeated runs produce a justified interval, publish that interval and use
the robust frontier mode.

## Current local rollout

The current M5 profile is intentionally recorded as a separate 60 W tier in
[`macbook-m5max-64-adapter60.json`](../examples/local-runtime-frontiers/hardware/macbook-m5max-64-adapter60.json).
Its AC `pmset` setting is 2, while `system_profiler` simultaneously reports Low
Power enabled and High Power disabled; the conflict is preserved rather than
renamed as verified High Power.

The official Apple collector requires privileged access to `/usr/bin/powermetrics`.
Until that access is explicitly authorized, a run that falls back to zero
telemetry is rejected rather than published. Once enabled, the first matrix is:

1. one exact small GGUF control on M5/60 W, M5/140 W, M1 Studio, and RTX 5060 Ti;
2. the current M5 winner candidates on 60 W and 140 W;
3. two or three explicit RTX 5060 Ti power limits; and
4. a cold-prefix/warm-prefix pair for the best agent route on each machine.

That matrix will reveal whether a machine changes only absolute efficiency or
actually changes frontier membership.
