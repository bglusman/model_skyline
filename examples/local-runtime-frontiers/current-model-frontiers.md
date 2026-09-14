# Current local-model frontiers

**Last updated: 2026-09-14.** These are provisional measurements on the 64 GB
M5 Max test machine, with matched M1 Max measurements used only for the
separate hardware comparison below. They are not an average across the two
machines.

## The short answer

There is no single best local model. There are currently four useful headline
choices:

- **Qwen3.8 27B** is the strongest measured general coding-agent choice.
- **Qwen3.8 Flash Next** is the best proven long-context choice. The tested DS4
  implementation retrieved correctly at 125,964 input tokens.
- **Muse Glimmer** is the small-memory coding-agent tradeoff.
- **Ornith 1.5** is the raw-speed choice. Laguna has now displaced it on the
  matched warm-tool position.

**Laguna XS 2.1** is the new synthetic-tool and fast information-retrieval
specialist: 3/3 uncached and 4/4 warm exact calls, with 1.815 s and 0.692 s
median turnaround. It also recovered the hidden 126K value in 3/3 runs with an
87.455 s median, but did not obey the exact-answer format. It is not a general
coding-agent recommendation yet: its Harbor smoke solved 0/2 verifier checks
and produced 25 parser errors. **Qwen3.8 Flash
Coder** previously won the uncached tool test; Laguna now dominates it there,
and Flash Coder still fails the broader agent and retrieval checks.

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
| Correct, fast warm tool use | exact tool success ↑ × turnaround time ↓ | **Laguna XS 2.1** | It produced 4/4 exact calls at a 0.692 s median, faster than the matched Ornith and Qwen routes. |
| Correct, fast uncached tool use | exact tool success ↑ × turnaround time ↓ | **Laguna XS 2.1** | It produced 3/3 exact calls at 1.815 s. This is a synthetic tool test, not an agent-quality score. |
| Find information in a long prompt quickly | requested value found ↑ × turnaround time ↓ | **Laguna XS 2.1** | It found the value in 3/3 byte-identical 126K-class prompts at an 87.455 s median. This does not claim exact instruction following. |
| More strict exact-answer context in less memory | largest repeatedly exact input length ↑ × physical footprint ↓ | **Qwen3.8 Flash Next** | The DS4 implementation passed 3/3 retrieval attempts at 125,964 tokens. A strict 128,000-token claim is not yet proven. |
| More fact-retrieval context in less memory | largest position with the value found every time ↑ × physical footprint ↓ | **Qwen3.8 Flash Next** | DS4 and Laguna both reached about 126K, but DS4 used about 5.46 GB versus Laguna's 33.81 GB. Laguna is an advisory near-member because its calibrated prompt was 36 tokens longer. |
| Reuse more prompt work with less delay | measured prefix reuse ↑ × turnaround time ↓ | **Laguna XS 2.1** and **Qwen3.8 27B** | Laguna is faster at 0.692 s; Qwen reuses more of the fixed prefix (99.87% versus 96.71%), so neither wins both quantities. |

The machine-readable model views are generated, not hand-ranked:
[speed](generated/cross-model-short-throughput-model-view.json),
[warm tools](generated/tool-agent-warm-p2048-o1024-model-view.json),
[uncached tools](generated/tool-agent-uncached-p2048-o256-model-view.json),
[warm cache](generated/warm-cache-operational-p2048-o1024-model-view.json),
[126K retrieval](generated/long-context-uncached-p126k-model-view.json),
[126K value retrieval](generated/long-context-value-uncached-p126k-model-view.json),
[strict capacity/memory](generated/validated-capacity-model-view.json),
[value capacity/memory](generated/retrieved-value-capacity-model-view.json), and the three
coding-quality views for [latency](generated/harbor-pilot5-quality-latency-model-view.json),
[memory](generated/harbor-pilot5-quality-memory-model-view.json), and
[uncached input](generated/harbor-pilot5-quality-cache-efficiency-model-view.json).

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

### Custom fits are allowed to win

A [ShoeHorn](https://github.com/notactuallytreyanastasio/shoehorn) fit remains
the same model family but becomes a separate, exactly reproducible
implementation. If that fitted artifact is the fastest or smallest version
that clears the same correctness and context requirements, it can put its
model on a best-available or machine-filtered frontier. Its score is always
measured from the fitted bytes; it never borrows the unquantized model's score.

This is how the 16 GB RTX 5060 Ti will be compared fairly with the two 64 GB
Macs. A 32K fit and a 128K fit answer different questions because KV memory
grows with context and leaves a different budget for weight quality. The
[ShoeHorn audit](shoehorn-audit.md) retains the exact plans and the limitations
of the current memory estimator.

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

Only 47 of 92 nominated model/frontier cells have been attempted. Laguna XS
2.1 now has seven of its eleven intended positions: it wins the three
tool/cache views and fast value-retrieval view, is near the value-capacity
frontier, and is explicitly rejected by both strict retrieval views. Its
raw-speed and three full quality positions remain open. North Mini Code,
Nemotron 3.5 Lightning, and Agents-A1 remain unmeasured. Untested means
unknown, not dominated.
