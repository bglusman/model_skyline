# Current local-model frontiers

**Last updated: 2026-09-14.** These are provisional measurements on the 64 GB
M5 Max test machine, with matched M1 Max measurements used only for the
separate hardware comparison below. They are not an average across the two
machines.

## The short answer

There is no single best local model. There are currently four useful headline
choices:

- **Qwen3.8 27B** is the strongest measured coding-agent choice and appears
  across the widest variety of frontiers.
- **Qwen3.8 Flash Next** is the best proven long-context choice. The tested DS4
  implementation retrieved correctly at 125,964 input tokens.
- **Muse Glimmer** is the small-memory coding-agent tradeoff.
- **Ornith 1.5** is the raw-speed choice and shares the fast warm-tool-call
  frontier.

**Qwen3.8 Flash Coder** wins one narrow uncached tool-call test, but it failed
broader coding-agent and retrieval checks and is not a general recommendation.

## Best-available model frontiers

These are the **best available** versions: a model appears when one real tested
implementation is on the exact frontier. The drill-down identifies the M5
runtime and settings that produced it. “Coding success” here means the fraction
of one fixed five-task Harbor pilot solved correctly; it is not a claim about
universal intelligence. Times include the complete measured work, including
attributable failures.

| Meaning of “best” | Two quantities compared | Current model(s) on the frontier | Plain-English reading |
| --- | --- | --- | --- |
| Capable and fast coding agent | coding success ↑ × task time ↓ | **Qwen3.8 27B**† | Best measured quality-first balance. Its low-reasoning/4K profile solved 17/25 tasks across five runs versus 13/25 for default reasoning. |
| Capable coding agent under memory pressure | coding success ↑ × process memory ↓ | **Qwen3.8 27B** and **Muse Glimmer**† | Qwen solved more; Muse used much less memory, so neither beats the other on both. |
| Capable coding agent with less repeated input work | coding success ↑ × uncached input tokens ↓ | **Muse Glimmer**† | Muse is the only current member with complete accounting for this small pilot. |
| Fast prompt processing and generation | prompt speed ↑ × generation speed ↑ | **Ornith 1.5** | Ornith led both speed measurements in the comparable M5 llama.cpp test. |
| Correct, fast warm tool use | exact tool success ↑ × turnaround time ↓ | **Ornith 1.5** and **Qwen3.8 27B** | Both produced 3/3 exact calls; their 0.835 s and 0.862 s times are treated as practically equivalent. |
| Correct, fast uncached tool use | exact tool success ↑ × turnaround time ↓ | **Qwen3.8 Flash Coder** | It won this narrow cold-prefix test only. |
| More usable context in less memory | repeatedly validated input length ↑ × physical footprint ↓ | **Qwen3.8 Flash Next** | The DS4 implementation passed 3/3 retrieval attempts at 125,964 tokens. A strict 128,000-token claim is not yet proven. |
| Reuse more prompt work with less delay | measured prefix reuse ↑ × turnaround time ↓ | **Qwen3.8 27B** | Its DFlash setup reused 99.87% at 0.862 s; Ornith is close but outside the configured equivalence rule. |

Some frontiers also apply must-pass requirements without turning them into a
third axis. For example, the operationally gated coding frontiers still compare
coding success with time or memory, but require exact tools, no measured swap
growth, and at least 125,000 validated input tokens. **Qwen3.8 Flash Next** is
currently their only eligible member. The same view at a strict 128,000 tokens
has no eligible model.

† These coding-quality results use a small five-task pilot. The completed
five-run comparison strengthens the low-reasoning/4K choice *within Qwen3.8*;
it does not replace the broader one-run, cross-model comparison. Several
promising models have not yet been run five times.

## The second view: balanced averages

Each frontier should also publish a **balanced-average** model view when the
evidence supports one. For each model, both quantities are aggregated across
the same declared machines or providers, with equal coverage and a published
mean/median rule. The frontier is then recalculated from those model-level
points.

Only the raw-speed frontier currently has even a small balanced panel: the same
Ornith and Qwen GGUF implementations were run on both the M1 Max and M5 Max.
Ornith remains the prompt-speed × generation-speed resident when each model's
two machine results are given equal weight and their arithmetic means are
compared. The checked-in [policy](cross-mac-short-throughput-model-view-policy.json)
and [generated view](generated/cross-mac-two-model-short-throughput-model-view.json)
make this table reproducible:

| Two-Mac balanced average | Prompt tokens/s | Generation tokens/s | Frontier member? |
| --- | ---: | ---: | --- |
| Ornith 1.5 | 1,900.506 | 85.240 | Yes |
| Qwen3.8 27B | 404.421 | 19.099 | No; Ornith is higher on both |

The panel contains only two models, so it is a worked comparison rather than a
broad recommendation.

The other balanced-average local frontiers are **not yet publishable**. The M1
does not have matched coding-agent, memory, cache, tool, and long-context runs
for every candidate. Those views will be shown beside the best-available rows
as the matrix fills in; missing environments will not be silently omitted from
a model's average.

If a reader is locked to one machine, the most useful result is a
machine-filtered frontier computed from that machine's measurements. The
balanced average remains a heuristic when that direct evidence is unavailable.

## How implementation details are removed from the headline

Measurements begin with an exact implementation: model file, quantization,
runtime, settings, machine, and workload. The simple table above collapses
those details with this rule:

1. Compute the two-dimensional frontier over eligible, actually tested
   implementations.
2. Show a model family when at least one of its implementations is a frontier
   member.
3. If the same model has several qualifying implementations, show it once and
   link to one real representative selected by the stated priority.

This is a **best-observed-implementation view**. It cannot combine one setup's
quality with another setup's speed to make an impossible “best of both” point.
The balanced-average view separately requires every model to cover the same
environment panel.

The exact implementation-level frontiers, numbers, gates, and reproducibility
links are in the [local runtime README](README.md). The [overnight
handoff](overnight-handoff-2026-09-14.md) records the broader experimental and
runtime state.

## What the two Macs tell us

The M5 Max and M1 Max both have 64 GB of unified memory, so ordinary fully
resident quantizations often have similar *fit* eligibility. They do not have
similar speed. With byte-identical artifacts and commands, the measured M5:M1
speed ratios were:

| Exact matched test | Prompt processing | Generation |
| --- | ---: | ---: |
| Ornith 1.5 Q4, Metal | 3.885× | 2.005× |
| Qwen3.8 27B Q4, Metal | 5.302× | 2.303× |
| Qwen3.8 27B, CPU-only control | 1.119× | 1.435× |

This is not enough evidence for a universal “M5 multiplier,” and it does not
yet define a complete M1 model frontier. It does show that equal memory is a
reasonable first approximation for what fits, while chip generation and
runtime paths can materially change which speed tradeoff is attractive.

## Important coverage gap

Only 33 of 74 nominated model/frontier cells have been attempted. Laguna XS
2.1, North Mini Code, Nemotron 3.5 Lightning, and Agents-A1 are pinned
challengers but have no local frontier result yet. Untested means unknown, not
dominated.
