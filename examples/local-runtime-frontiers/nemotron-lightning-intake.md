# NVIDIA Nemotron 3.5 Lightning local intake

This intake pins the next local candidate after the Qwen3.8 repeated-quality
cohort. It is an experiment plan, not a measured ModelSkyline offering or a
frontier claim. External benchmark results below are screening evidence only;
they must not enter a local quality axis or be attached to the exact artifact.

## Candidate hypothesis

NVIDIA Nemotron 3.5 Lightning is a 30-billion-parameter hybrid Mamba-2,
mixture-of-experts, and attention model with about 3 billion active parameters.
That makes the selected 4-bit MLX conversion a plausible short-decode and
throughput-frontier candidate on a 64 GB Apple Silicon host. Its tool
correctness, cache behavior, long-context retrieval, and real agent quality are
unknown until measured locally.

The exact candidate is:

| Field | Pinned value |
| --- | --- |
| Repository | `txgsync/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-oQ4e-mtp` |
| Revision | `96f1af4e0210e3d7369cafcc6aa8dc4b8632841f` |
| Reported repository bytes | `21,809,301,968` |
| Format | MLX safetensors, mixed oQ4e |
| Declared architecture | `nemotron_h` |
| Declared context | `262,144` tokens |
| Layers / hidden size | 52 / 2,688 |
| Routed experts | top 6; 4-bit routed experts |
| Higher-precision components | 8-bit shared experts and LM head; sensitivity-selected 6/8-bit Mamba projections |
| Draft weights | one full-precision next-token-prediction layer |
| License | OpenMDW License Agreement, version 1.1 |

The repository is roughly 21.8 GB, so it appears to leave substantial room for
runtime state and KV/cache allocations on a 64 GB unified-memory host. That is
only a fit hypothesis: admission, peak physical footprint, memory pressure,
swap growth, and the largest passing retrieval position must all be measured.

The community conversion says all 5,888 routed experts were calibrated over
64 sequences of 256 tokens. That describes the conversion procedure, not local
quality. The presence of draft weights also does not prove that a runtime
activated them.

## Why it is worth measuring

The official checkpoint was released on August 11, 2026 and supports
configurable reasoning and multi-token prediction. NVIDIA describes a broader
up-to-1-million-token family capability, while the selected conversion's own
configuration declares 262,144 positions. ModelSkyline therefore treats 262K
as an unvalidated artifact claim and treats every locally passing retrieval
position separately.

NVIDIA's same-harness BF16 release table reports 51.56 on SWE-bench Verified
and 24.58 on Terminal-Bench 2.1, below its Qwen3.6 35B A3B control at 70.12 and
44.38. Those scores are not transferable to this quantized artifact, but they
are a useful warning: this is primarily an efficiency-frontier hypothesis, not
a presumed replacement for the best local coding-quality route.

Two recent community projects make it a useful screen:

- `m4-local-llm-benchmark` reports 130.5/135 on its 27-prompt, non-blinded
  rubric for a 4-bit Nemotron route, versus 132.5 for Muse Glimmer and 118.5 for
  Qwen3.8. That rubric does not exercise tools or sessions and is not exact
  artifact-mapped quality evidence.
- `mlx-dspark` reports about 91.4 tokens/second for a 4-bit M4 Pro baseline and
  about 100.9 tokens/second with its current speculative decoder, a 1.10x
  geometric-mean speedup over its prompt set. This is hardware- and
  content-specific throughput evidence, not a prediction for either local Mac.

An oMLX issue from an M5 Max 40-core-GPU user reports that synthetic DFlash and
multi-token-prediction gains can shrink or reverse on real agent tasks, and
that DFlash can sacrifice ordinary prompt-cache behavior. That reinforces a
plain-first, cache-aware, tool-heavy protocol.

## Exact profiles and order

Test one resident profile at a time under the shared exclusive-runner lock. Do
not interleave profiles within a batch; model/profile swaps would contaminate
latency and memory observations.

1. **Plain oMLX:** selected artifact, no embedded MTP decode and no external
   drafter. This is the reference profile.
2. **Embedded MTP:** the same artifact with oMLX native MTP requested. Record
   three distinct facts: draft weights are present, the runtime is capable of
   attaching the `nemotron_h` MTP patch, and telemetry/log evidence proves it
   was actually active for the measured requests.
3. **External DSpark:** the same target artifact plus the exact pinned external
   drafter/runtime configuration. This is a distinct offering because its
   artifact set and runtime behavior differ from both plain and embedded-MTP
   profiles.

The current oMLX `nemotron_h` patch attaches the MTP module only when MTP is
active and keeps a per-instance decode-enabled marker. Capture the server
version/commit, launch arguments, relevant log evidence, and draft acceptance
or use telemetry for every speculative profile. Never infer activation merely
from the model card or weight files.

## Evaluation sequence

Stop a profile at the first correctness or memory-safety gate it cannot pass.
Use the existing local measurement schema and compact runner-memory evidence so
the resulting catalogs remain reproducible without committing raw traces.

1. **Admission and smoke**
   - Load under the exclusive lock and capture time to ready.
   - Record process/system physical memory and swap before load, at ready, and
     through the requests.
   - Require one ordinary completion and three deterministic structured tool
     calls with exact argument validation.
2. **Short throughput envelope**
   - Run the existing pp2048 position with 64-, 256-, and 1,024-token output
     caps where supported.
   - Retain TTFT, prefill/input throughput, decode throughput, end-to-end time,
     finish reason, repetitions, and response usage.
3. **Prefix-cache operation**
   - Send one byte-stable approximately 20K-token prefix twice, then change a
     suffix while preserving the prefix.
   - Compare cold/miss and warm/hit TTFT, uncached input tokens, reused tokens,
     full-prefix validation, and cache telemetry. Run cache-disabled only as an
     explicitly separate profile.
4. **Tool-agent positions**
   - Use the frozen 30-tool schema, 2K-prefix position at 256- and 1,024-token
     output caps.
   - Measure uncached/miss and warm/hit cohorts separately; score exact tool
     name and arguments as well as parse success.
5. **Retrieval and capacity ladder**
   - Test 2K, 32K, 65K, and 126K exact retrieval positions.
   - Only after all pass, probe a position near the artifact's 262K declared
     ceiling. A successful allocation without retrieval is capacity smoke, not
     long-context correctness.
6. **Real-agent quality**
   - Run the same frozen Harbor smoke used to screen the current candidates.
   - Promote a passing profile to the five-task pilot, with complete verifier,
     wall-time, usage, and runner-memory capture.
   - Repeat only after the smoke is valid; do not spend the repeated-quality
     budget on a broken route.

For plain, MTP, and DSpark comparisons, pin the same target revision, prompt
bytes, sampler, reasoning policy, output cap, cache state, and run conditions.
Embedded MTP and DSpark may change token paths despite a common target, so tool
correctness and verifier quality remain mandatory alongside speed.

## Promotion gates

A profile becomes eligible for the corresponding packaged frontier only when:

- all three structured tool calls are exactly correct;
- retrieval passes at every claimed context position and retained position;
- no unexpected swap growth or memory-pressure failure occurs during the
  measured batch;
- every speculative optimization has direct activation evidence;
- the Harbor smoke produces a valid verifier result and passes before the
  five-task pilot is scheduled;
- raw evidence is normalized against the exact artifact and runtime identity;
  and
- external rubric scores remain annotations, never imported quality values.

Each frontier still applies its own position and eligibility rules. In
particular, a fast but tool-invalid route cannot enter the tool frontier, an
allocation-only 262K run cannot enter the retrieval frontier, and an external
quality score cannot populate a ModelSkyline local quality frontier.

## Pinned sources

Sources were retrieved on 2026-09-14. A commit or artifact revision is pinned
where the source supplies one; prose claims remain dated observations.

| Source | Pin | Use |
| --- | --- | --- |
| [NVIDIA BF16 model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16) | model release page, retrieved 2026-09-14 | official architecture, active-parameter, reasoning, MTP, and family-context claims |
| [NVIDIA Nemotron repository](https://github.com/NVIDIA-NeMo/Nemotron) | repository observed 2026-09-14 | official implementation/documentation root |
| [NVIDIA four-over-six quantization guide](https://github.com/NVIDIA-NeMo/Nemotron/blob/main/docs/nemotron/lightning35/quantization.md) | repository document observed 2026-09-14 | official PTQ/QAD quantization tradeoffs |
| [Selected MLX artifact](https://huggingface.co/txgsync/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-oQ4e-mtp/tree/96f1af4e0210e3d7369cafcc6aa8dc4b8632841f) | `96f1af4e0210e3d7369cafcc6aa8dc4b8632841f` | exact files, bytes, configuration, calibration description, and license |
| [oMLX `nemotron_h` MTP patch](https://github.com/jundot/omlx/blob/ddc4718412c93c3ef15478b2a1f20d187a655dc8/omlx/patches/mlx_lm_mtp/nemotron_h_model.py) | `ddc4718412c93c3ef15478b2a1f20d187a655dc8` | native MTP activation implementation |
| [MLX DSpark](https://github.com/ARahim3/mlx-dspark/tree/d9ad4f38dc7c17faf8be1176b9c23bc765893656) | `d9ad4f38dc7c17faf8be1176b9c23bc765893656` | external-drafter implementation and current benchmark table |
| [M4 local LLM benchmark](https://github.com/andyast/m4-local-llm-benchmark/tree/4d13a460e46b7683de63fd66a024b381a2a19475) | `4d13a460e46b7683de63fd66a024b381a2a19475` | non-blinded quality-screen proxy |
| [oMLX M5 Max real-workload report](https://github.com/jundot/omlx/issues/1551) | issue observed 2026-09-14 | caution about synthetic speculative gains and prompt-cache tradeoffs |
