# Examples and research studies

The directories here are not all interchangeable examples. Check the category
and evidence limits before using one as a template or recommendation.

## Start here

| Directory | Category | What it demonstrates |
|---|---|---|
| [`real-agent-value`](real-agent-value/) | Tutorial/regression fixture | The complete `validate` → `evaluate` → `select` path with a small catalog |
| [`coding-session`](coding-session/) | Synthetic fixture | A minimal alternate workload shape; not real comparative evidence |
| [`framework-traces`](framework-traces/) | Adapter fixtures | Redacted framework trace shapes for importer development |
| [`mappings`](mappings/) | Adapter configuration | Reviewed identity mapping examples |

Use `real-agent-value` to learn the product. Do not begin with the local-runtime
or voice directories unless you are reproducing those specific studies.

## Operational case studies

| Directory | Status | Scope |
|---|---|---|
| [`subscription-relative-real`](subscription-relative-real/) | Used by a household consumer | Subscription and metered agent/coding economics for one declared workload and operator |
| [`openrouter-real-workload`](openrouter-real-workload/) | Narrow real-workload snapshot | OpenRouter measurements under its recorded candidate set and dates |

These show real integration, not portable defaults. Reuse the workflow and
contracts; replace the workload assumptions, offerings, observations, and
policy.

## Dated research programs

| Directory | Research question | Main caution |
|---|---|---|
| [`local-runtime-frontiers`](local-runtime-frontiers/) | Which local model/runtime/hardware configurations trade quality, speed, memory, capacity, and energy? | Large collection of machine-specific captures; unlike harnesses or resource measures must not be mixed |
| [`structured-decision-frontiers`](structured-decision-frontiers/) | When do typed decision components or compound systems improve cost, latency, calibration, safety, or end-to-end success? | Narrow case sets and rapidly changing candidates; component wins do not imply system wins |
| [`voice-runtime-frontiers`](voice-runtime-frontiers/) | Which local ASR, TTS, and voice pipeline configurations trade quality, latency, throughput, and memory? | Perceptual quality, runtime patches, hardware, and warm/cold state materially affect conclusions |

These directories intentionally retain negative results, raw captures,
candidate metadata, and dated status notes. Treat each result as a frozen
experiment whose applicability ends at its stated workload, candidate set,
hardware, harness, and date.

## Evidence labels

When reading an example, distinguish:

- **measured:** produced by the recorded harness and environment;
- **replayed/derived:** deterministically calculated from recorded inputs;
- **vendor-reported:** copied from a first-party claim but not reproduced;
- **synthetic:** authored to test a contract or hypothesis;
- **operational:** consumed by a real workflow; and
- **recommended:** supported for the exact declared use, which is a much
  stronger statement than “on a frontier.”

An artifact can have more than one label. For example, a synthetic screen can
be measured locally, and an operational publication can still use provisional
quality evidence.

## Promotion rule

Research should enter the reusable product surface only after it has:

1. a named user decision and workload;
2. a reproducible, versioned producer;
3. at least one realistic evaluation or consumer outside its own unit tests;
4. explicit failure, safety, and applicability boundaries; and
5. a reason the existing generic observation contract is insufficient.

Otherwise keep it here as a dated study. That is not a lesser result; it is a
more honest boundary.
