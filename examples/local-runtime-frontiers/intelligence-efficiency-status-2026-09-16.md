# Intelligence-efficiency status — 2026-09-16

## Outcome so far

The frontier is feasible and useful, but no energy resident has been published
yet. ModelSkyline now has a reviewed aggregate importer, an exact 60 W M5
hardware tier, and a documented measurement protocol. The first official-
harness smoke correctly failed the publication gates instead of turning missing
telemetry into a misleading efficiency result.

The two new frontier definitions are:

- task accuracy (maximize) × average active power in watts (minimize); and
- task accuracy (maximize) × average energy per task in joules (minimize).

IPW (`accuracy / watts`) and IPJ (`accuracy / joules`) are reported as derived
labels. They are not substitutes for the two explicit axes.

## Current M5 tier

The machine was observed on AC power with:

- a negotiated 60 W USB-C adapter (20 V × 3 A);
- battery at 100%, not charging;
- `pmset` AC `powermode` 2; and
- `system_profiler` simultaneously reporting High Power off and Low Power on.

That state is retained as
[`macbook-m5max-64-adapter60`](hardware/macbook-m5max-64-adapter60.json).
It is intentionally different from the historical 140 W profile. The adapter
rating is an identity/control field, not the measured watt axis.

## Official-harness smoke

Harness:
[HazyResearch/intelligence-per-watt](https://github.com/HazyResearch/intelligence-per-watt)
at reviewed commit `645f68a65f5bb9b0296bc2840474ef86744af3c2`.

Route:
`qwen3.8-27b-oq4e-mtp:baseline-f16kv-low-think4k` through the local llama-swap
OpenAI-compatible endpoint.

Workload:
one MMLU-Pro test item after one warmup item. This was plumbing validation, not
a quality estimate.

Observed performance:

- measured query wall time: 29.62 s;
- time to first streamed chunk: 27.00 s;
- median inter-chunk gap: 137 ms; and
- warmup item: about 21 s.

The harness reported zero GPU, CPU, ANE, and SoC energy/power because its Apple
collector could not run privileged `powermetrics`. ModelSkyline's importer
rejects this artifact: it has zero telemetry, no covered scored items, and no
usable efficiency evidence.

## Two upstream issues exposed by the smoke

1. The Apple energy monitor launches `sudo powermetrics`. Without an authorized
   sudo path it can appear to start and then produce zeros. A successful
   collector self-test with positive readings is now a mandatory preflight.
2. The current MMLU-Pro docs describe exact-match scoring, but the reviewed
   implementation invokes an evaluator model to extract the answer letter. The
   default cloud evaluator failed because the configured OpenAI account had no
   remaining API credit. Two local evaluator attempts also remained
   unevaluated because the extractor hard-codes a five-token output budget;
   reasoning models spend that budget before returning a letter.

The larger screen should not run until scoring is deterministic or the exact
evaluator route and sufficient output budget are pinned. A public MMLU prompt
can safely use a small remote extractor if desired, but its identity must be
part of the workload configuration.

## What landed in ModelSkyline

- `modelskyline import-intelligence-per-watt` projects private upstream
  `analysis/accuracy.json` into a prompt-free observation catalog.
- The import binding has a committed language-neutral JSON Schema.
- The adapter accepts only the reviewed upstream revision and exact local
  offerings with a quantization and power service tier.
- It recomputes accuracy, IPW, IPJ, energy totals, and coverage; it rejects
  failed/unevaluated items, imputation, zero readings, mixed bases, truncated
  windows, incomplete record sets, and inconsistent power provenance.
- The output workload version is a content hash of dataset revision, task
  manifest, attempt count, concurrency, and configuration digest.
- Raw prompts, references, and model answers are never copied into the public
  catalog.

See the full [quality/power/energy protocol](../../docs/intelligence-efficiency.md).

## Next measurement matrix

After positive telemetry preflight:

1. M5 Max at the current 60 W tier, followed later by the 140 W tier.
2. The same exact GGUF control on the 64 GB M1 Studio.
3. A smaller exact GGUF control that also fits the RTX 5060 Ti, followed by
   several explicit NVIDIA power limits.
4. Current M5 candidates (dense Qwen3.8, Ornith, and the best practical sparse
   route) within one fixed power tier.
5. Cold-prefix and warm-prefix cohorts for the best agent route.

Apple results use whole-SoC (`soc`) energy. NVIDIA's current collector uses GPU
(`gpu`) energy. Those become separate exact frontiers; a cross-device view is
provisional until a common wall-power boundary is measured.

## Authorization still needed for M5 power evidence

The official macOS collector needs privileged access to
`/usr/bin/powermetrics`. A narrow passwordless sudoers rule for that executable
is the upstream-recommended unattended setup. It has **not** been installed.
Without it, M5 performance tests remain possible, but ModelSkyline will not
publish watts, joules, IPW, or IPJ from the zero-reading fallback.
