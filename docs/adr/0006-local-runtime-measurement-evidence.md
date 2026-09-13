# ADR 0006: Exact local-runtime measurement evidence

- Status: accepted
- Date: 2026-09-13
- Decision owners: ModelSkyline maintainers

## Context

A local model name is not a performance identity. The same checkpoint can use
different model bytes, quantization, KV precision, physical context limits,
prefix caches, speculative decoders, runtimes, power modes, hardware, and
agent harnesses. These choices can change time to first token, decode rate,
memory use, tool correctness, and long-context behavior independently.

Warm-prefix latency, cold loading, steady-state throughput, and post-idle
reloads are also different workload positions. Interleaving model IDs behind a
memory-exclusive router measures unload/load churn as well as inference. A
capacity smoke test proves allocation, not retrieval quality, and a publisher
benchmark for a base checkpoint does not automatically apply to a particular
local quantization or harness.

ModelSkyline already has a language-neutral `ObservationCatalog` boundary and
an exact quality reconciliation path. Local capture needs enough additional
structure to reach that boundary without weakening either invariant.

## Decision

Add a strict, versioned `LocalMeasurementRecord` contract and two CLI commands:

- `validate-local-measurement` validates one bounded artifact; and
- `build-local-catalog` projects comparable records into an ordinary catalog.

The record keeps four concerns separate:

1. **System identity:** named hardware/core topology, memory, power state, exact
   model artifact digest and size, runtime build/backend/configuration,
   physical context capacity, KV type, prefix-cache enablement, speculative
   method, harness, and capabilities.
2. **Workload position:** prompt and output sizes, concurrency, runner warmth,
   cache warmth, repetitions, and benchmark-specific position fields.
3. **Evidence:** repeated Decimal performance series and explicit retrieval,
   tool-call, or structured-output pass counts.
4. **Provenance:** raw capture path and digest, command digest, capture tool and
   version, UTC timestamps, and methodology.

The exact local `OfferingKey` hashes system identity but excludes mutable
source descriptions and free-form metadata. Workload and provenance changes do
not manufacture a different deployment identity. A catalog accepts only one
workload position and one record per exact offering, so callers cannot pool
cold and warm requests, prose and tool modes, or different prompt lengths.

Runtime cache enablement belongs to offering identity because it changes the
deployed system. Cache warmth belongs to the workload position because it
describes a request. Speculative acceptance, tool parsing, and throughput may
  coexist as evidence. Configured capabilities remain stable system identity
  across workloads; any integrity check must name a capability configured on
  that offering, while the check's pass rate—not configuration alone—is the
  evidence used by an operational frontier.

Quality is deliberately absent from the local contract. GGUF, MLX, speculative,
and harness variants remain separate offerings. Benchmark quality can be added
only through the reviewed exact reconciliation and portfolio path described in
ADRs 0004 and 0005.

## Frontier semantics

Local frontiers remain ordinary two-axis frontiers. Recommended views include:

- fixed-position prompt throughput versus decode throughput;
- streaming TTFT versus decode throughput for one prompt/output/mode/cache
  position;
- exact long-context retrieval success versus end-to-end latency; and
- largest fully passing retrieval position versus peak physical footprint.

Quantization-quality comparisons require a separately reviewed quality cohort.
A speed-versus-memory plot must not imply that two artifacts have equal task
quality merely because they share a checkpoint label.

## Consequences and limits

Raw captures remain the replay boundary; normalized records retain their
digests but do not copy every backend-specific field into top-level schema.
The contract can represent GGUF, MLX, DFlash, oMLX, Ollama, and other runtimes
without blessing any one benchmark harness.

The alpha contract does not collect measurements, coordinate model residency,
infer physical memory capacity, assign quality scores, or decide compaction
policy. Harness scripts must stabilize prompt/tool-schema prefixes, run one
model profile in contiguous batches, record cache and runner state honestly,
and retain correctness alongside speed. A serving mutex such as llama-swap is
an operational companion, not part of the Pareto engine.
