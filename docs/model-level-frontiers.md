# Model-level frontier views

ModelSkyline measures exact offerings, but most readers want model names first.
The `model-frontier-view` command turns one exact two-axis frontier into two
model-level answers without hiding how either answer was made.

## Best available

This view asks: **what is the best this model can do in the tested market or
hardware pool?**

A model is shown when at least one of its real offerings is on the exact
frontier. The output keeps the winning offering, so the result also recommends
the provider, runtime, or machine that achieved it. If two implementations of
one model occupy different useful tradeoffs, both real points are retained
under the same model name.

## Balanced average

This view asks: **how does the model perform across the same environments?**

A data-only policy names the provider or local-host panel and selects one real
offering for every model in every environment. Both frontier measurements are
then averaged with equal weight, and the two-axis frontier is recalculated over
those model averages.

Generation fails if a model is missing an environment, if a selected offering
was rejected by the exact frontier, or if its provider does not match the
declared environment. This prevents a model tested only under favorable
conditions from being compared with a model tested everywhere.

The first contract supports an arithmetic mean over providers. Later policies
can add other published aggregation rules without changing the exact frontier.

## If the reader is locked to one environment

Filter the exact catalog or frontier to that provider or machine first, then
calculate the ordinary two-axis frontier. Direct evidence for the reader's
environment is more useful than an average. The balanced average is a heuristic
when direct coverage is missing; best available shows what is possible and how
to obtain it.

## No “best of both” invention

Neither view may take quality from one offering and speed or price from another
to create a point that no real setup achieved:

- best available retains complete real frontier points;
- balanced average uses the same explicitly selected offering for both
  measurements in each environment;
- the output retains every contributing offering and every per-environment
  value.

## Generate a view

First calculate an exact frontier as usual. Then provide the balanced-panel
policy and that snapshot:

```console
modelskyline model-frontier-view \
  model-view-policy.json \
  exact-frontier.json
```

The default output is a short model-first table. Add `--format json` to create
the replayable artifact:

```console
modelskyline model-frontier-view \
  model-view-policy.json \
  exact-frontier.json \
  --format json \
  --output model-frontier-view.json
```

The policy is bound to the exact source snapshot's SHA-256 identity. The output
is also self-hashed and records the normalized policy hash, the original two
axes, best-available offerings, balanced per-environment values, averages,
members, and dominance explanations.

The current local worked example uses
[`cross-mac-short-throughput-model-view-policy.json`](../examples/local-runtime-frontiers/cross-mac-short-throughput-model-view-policy.json).
It compares the same Qwen and Ornith GGUF quantizations on the 64 GB M1 Max and
64 GB M5 Max with equal machine weight.
