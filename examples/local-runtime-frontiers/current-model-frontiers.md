# Current local-model frontiers

**Last updated: 2026-09-15.** The main cross-model results are provisional
measurements on the 64 GB M5 Max test machine, with matched M1 Max measurements
used only for the separate hardware comparison below. Clearly separated RTX
5060 Ti sections now compare two Laguna quantizations and the stock Laguna
control with Qwen3.5 9B. Results are not averaged across machines.

## The short answer

There is no single best local model. On the 64 GB M5 Max, there are currently
four useful headline choices:

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

On the 16 GB RTX 5060 Ti, **Qwen3.5 9B Q6** is the first measured practical
128K-class route. It returned the exact hidden value with the needle near the
beginning, middle, and end of three separate 126,002-token prompts. Its middle
run peaked at 11.72 GB of combined host/GPU service capacity. It has not run
the Mac cohort's coding-quality pilot, but it has now solved 3/5 tasks in a
matched 5060 pilot, exactly clearing that protocol's 60% usefulness floor.
**Muse Glimmer 30B AD-IQ3_XXS** is now a second measured 128K-class route on
that GPU. It also passes the three retrieval positions and 30-tool screen, but
it solved only 2/5 coding tasks and is excluded from the 5060 quality frontiers.

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

## First RTX 5060 same-model frontiers

The first 5060 comparison asks a smaller question than the headline table:
which of two Laguna XS 2.1 files is better on the same GPU, runtime, 32K context,
and Q8 KV settings? It compares Bartowski's stock Q2_K_L with an exact ShoeHorn
2.985-bpw mixed fit. Because both are Laguna, this is a quantization/offering
frontier, not yet a broad 5060 model-family frontier.

| Meaning of “best” | Two quantities compared | Resident | What it means |
| --- | --- | --- | --- |
| Raw short speed | prompt speed ↑ × generation speed ↑ | **ShoeHorn fit** | It leads both: 2,738.48 prompt and 153.091 generation token/s. |
| Correct, fast synthetic tool use | exact tool success ↑ × turnaround time ↓ | **ShoeHorn fit** | Both pass 3/3; ShoeHorn's median is 2.307 s versus stock's 2.732 s. |
| Correct, fast 30K retrieval | exact retrieval success ↑ × turnaround time ↓ | **Stock Q2_K_L** | Stock passes 3/3. ShoeHorn fails 0/3 and is rejected before latency can help it. |

The simple recommendation is **use stock if a 32K Laguna test is wanted; do
not deploy the custom fit**. The custom artifact also yields `nan` for every
chunk in two perplexity configurations. Perplexity is a must-pass quality gate,
not a third axis, so this does not change the two-dimensional definitions above;
it confirms the deployment rejection. Neither artifact satisfies a 128K route.

The exact generated views are [speed](generated/laguna-xs21-5060-quant-short-throughput-model-view.json),
[tools](generated/laguna-xs21-5060-quant-tool30-p2048-o256-model-view.json), and
[retrieval](generated/laguna-xs21-5060-quant-retrieval-p30000-model-view.json).
Each model-first view still displays Laguna only once while retaining the exact
winning file underneath.

## RTX 5060 model frontiers: Qwen3.5 9B and Laguna

This is the first cross-model 5060 comparison. It uses the Qwen3.5 9B Q6_K
GGUF with a 131,072-token allocation and Q8 KV cache, alongside the stock
Laguna XS 2.1 Q2_K_L control. Each row still compares exactly two quantities.
The candidate set has only two models, and the operational Qwen results are
single-run screens unless stated otherwise.

| Meaning of “best” | Two quantities compared | Frontier resident(s) | Plain-English reading |
| --- | --- | --- | --- |
| Fast short processing | prompt speed ↑ × generation speed ↑ | **Qwen3.5 9B** and **Laguna XS 2.1** | Qwen processes the prompt faster (2,664.44 vs 2,070.5 token/s); Laguna generates faster (139.458 vs 57.925 token/s). Neither wins both. Each result has five benchmark samples. |
| Correct, fast uncached tool use | exact tool success ↑ × turnaround time ↓ | **Qwen3.5 9B** and **Laguna XS 2.1** | Both pass exactly. Laguna takes 2.732 s and Qwen 2.846 s; the configured 5% latency tolerance treats that difference as equivalent. Qwen has only one independent uncached attempt, so this is a screen. |
| More proven context in less service memory | exact-answer input length ↑ × whole-service capacity ↓ | **Qwen3.5 9B** | Qwen validates 126,002 tokens at 11.72 GB. Laguna validates 30,000 at 14.27 GB, so Qwen wins both quantities. |
| More proven context without waiting | exact-answer input length ↑ × uncached turnaround time ↓ | **Qwen3.5 9B** and **Laguna XS 2.1** | Qwen handles much more context but takes 72.103 s. Laguna handles less and takes 18.530 s. Neither wins both. |

“Whole-service capacity” makes the discrete-GPU and unified-memory results
comparable without pretending they expose the same operating-system counter.
For this CUDA host it is host proportional-set memory plus GPU allocation from
the same sampled instant. Qwen's device allocation itself peaked at 9.75 GB,
which also directly demonstrates that this profile fits the 16 GB GPU. The
combined number is capacity accounting, not a claim that the GPU alone used
11.72 GB.

The middle-position Qwen run included one uncached request and two repeats. The
uncached request took 72.103 s; both repeats reused 125,998 of 126,002 input
tokens and took about 0.693 s. Its 30-tool run similarly fell from 2.846 s on
the cache miss to a 0.815 s median on two warm repeats. Fresh-server early and
late probes each reported zero cached tokens and returned the exact value, so
the position checks did not accidentally rely on the middle prompt's cache.

The [official checkpoint](https://huggingface.co/Qwen/Qwen3.5-9B) advertises a
262,144-token native context, but this runtime allocated 131,072 and the
experiment proves 126,002—not 262K. This
route is text-only because the tested GGUF has no vision projector. A Q4 KV
exploration used less capacity but slowed long-context generation to about
24.7 token/s, versus about 36.3 token/s with Q8 KV, so Q8 is the retained
profile. The separate 5060 coding result below does not displace Qwen3.8 27B
on the Mac coding frontiers above; hardware-specific populations are not
silently mixed.

The generated model views are [short speed](generated/qwen35-laguna-5060-short-throughput-model-view.json),
[uncached tools](generated/qwen35-laguna-5060-tool-miss-screen-model-view.json),
[validated context versus service memory](generated/qwen35-laguna-5060-validated-capacity-service-memory-screen-model-view.json),
and [validated context versus latency](generated/qwen35-laguna-5060-validated-capacity-latency-screen-model-view.json).

## RTX 5060 coding-quality frontiers: Qwen3.5 9B and Muse

Both exact 128K routes ran the same five pinned Terminal-Bench tasks through
Harbor/Terminus-2. This is a complete result for that small workload, not a
Terminal-Bench 2.1 score or a general intelligence ranking.

| Exact route | Tasks solved | p95 task wall time | Uncached input tokens | Frontier result |
| --- | ---: | ---: | ---: | --- |
| Qwen3.5 9B Q6_K, Q8 KV | 3/5 (60%) | 786.460 s | 118,599 | Resident on quality × latency and quality × uncached-input frontiers |
| Muse Glimmer AD-IQ3_XXS, Q4 KV | 2/5 (40%) | 805.445 s | 48,206 | Ineligible: below the declared 60% usefulness floor |

Qwen passed `fix-git`, `multi-source-data-merger`, and
`cancel-async-tasks`. Muse passed the first two but failed
`cancel-async-tasks`; it also timed out on `build-cython-ext`. Qwen emitted many
recoverable JSON-wrapper warnings. Muse was usually cleaner but still produced
four parser feedback events across two tasks. Parser cleanliness therefore did
not predict verifier success in this sample.

Muse used fewer uncached input tokens, but it is not a frontier resident because
it failed the usefulness gate. Among useful candidates, Qwen is currently the
only resident; this is a one-model eligible set, not evidence that its token
demand is broadly optimal.

The machine-readable evidence is the
[catalog](generated/harbor-pilot5-5060-qwen35-muse-catalog.json),
[quality × latency frontier](generated/harbor-pilot5-5060-quality-latency-frontier.json),
and [quality × uncached-input frontier](generated/harbor-pilot5-5060-quality-cache-efficiency-frontier.json).
The simpler model-first views are [latency](generated/harbor-pilot5-5060-quality-latency-model-view.json)
and [uncached input](generated/harbor-pilot5-5060-quality-cache-efficiency-model-view.json).
The [protocol](harbor-quality-pilot-5060-qwen35-muse.yaml) is immutable and
separate from the original Mac cohort.

## RTX 5060 Muse quantization frontier

This same-checkpoint comparison keeps the base model, GPU, llama.cpp build,
131,072-token allocation, Q4 KV, and benchmark positions fixed. AtomicChat's
calibrated AD-IQ3_XXS is compared with a ShoeHorn 3.758-bpw fit that forces the
embedding and output tensors to IQ4_XS.

| Meaning of “best” | Two quantities compared | Frontier resident(s) | Plain-English reading |
| --- | --- | --- | --- |
| Raw short speed | prompt speed ↑ × generation speed ↑ | **ShoeHorn fit** | ShoeHorn leads prompt processing (1,109.02 vs 1,022.59 tok/s). The control's 2.4% generation lead (31.3664 vs 30.6355 tok/s) is inside this frontier's 3% practical-equivalence tolerance. |
| Correct, fast warm tool use | exact tool success ↑ × turnaround time ↓ | **AD-IQ3_XXS** | Both pass 3/3, but the control's median is 4.929 s versus 8.799 s. |
| Lower held-out loss in fewer bytes | perplexity ↓ × artifact bytes ↓ | **AD-IQ3_XXS** | The control is smaller and scores 5.2003 versus 5.7316. This is a quantization gate, not a general coding-quality frontier. |

Both artifacts also return the exact hidden value with the needle near the
beginning, middle, and end of roughly 126K input tokens. That prevents a false
rejection of the custom file, but does not erase its worse held-out loss,
slower decode, or much longer reasoning on some prompts. The deployed and
recommended Muse route is therefore **AD-IQ3_XXS**. The ShoeHorn result remains
publishable evidence that a custom fit can preserve checked semantics while
still losing the overall deployment decision.

The machine-readable narrow speed result is the
[offering frontier](generated/muse-glimmer-5060-quant-short-throughput-frontier.json)
with its [model view](generated/muse-glimmer-5060-quant-short-throughput-model-view.json).
It deliberately does not hide the separate promotion gate: “frontier member
for raw speed” and “recommended deployment” are different statements.
The matched warm-tool [offering frontier](generated/muse-glimmer-5060-quant-tool-warm-p2048-o1024-frontier.json)
and [model view](generated/muse-glimmer-5060-quant-tool-warm-p2048-o1024-model-view.json)
select the calibrated control after both candidates clear the exact-call gate.

In the broader 5060 raw-speed set, both Muse offerings are dominated by Qwen3.5
9B Q6: Qwen processes and generates faster. The matched coding pilot now also
rejects Muse from the quality frontiers because its 40% success is below the
60% usefulness gate. The expanded [offering frontier](generated/qwen35-laguna-muse-5060-short-throughput-frontier.json)
and [model view](generated/qwen35-laguna-muse-5060-short-throughput-model-view.json)
retain Qwen and Laguna and exclude Muse on this exact raw-speed definition.

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
