# Efficient quality estimation

ModelSkyline can use small, calibrated benchmark subsets to decide which model
offerings deserve an expensive full evaluation. It must not silently copy an
unquantized model's published score onto a quantized artifact or apply one
global "Q4 quality multiplier."

This protocol applies to local quantizations and to costly remote offerings.
The exact model revision, tokenizer and chat template, runtime, inference
settings, agent harness, benchmark version, scorer, and task-set digest remain
part of the evidence identity.

## Evidence tiers

Use three explicit tiers:

| Tier | Permitted use | Not permitted |
| --- | --- | --- |
| `proxy` | Reject an obviously damaged artifact; prioritize the next test | Claim a task-quality score or inherit the base model's score |
| `estimated` | Populate an uncertainty-aware screening frontier and select candidates for full evaluation | Publish the point estimate as a measured benchmark result |
| `measured` | Populate the decision frontier and default/fallback selection | Transfer the score to a different offering identity |

Perplexity, token-distribution divergence, quantizer reconstruction error, and
short deterministic syntax checks are proxy evidence. A score predicted by a
versioned coreset estimator is estimated evidence. Only the complete declared
benchmark is measured evidence.

The default recommendation path should prefer measured evidence. Estimated
evidence may drive an automatic default only when policy explicitly opts in,
uses the conservative confidence bound, and records the estimator's validated
domain. Proxy evidence is never a quality axis.

## Paired delta, not a universal multiplier

When an exact full-precision or high-fidelity anchor can be evaluated with the
same harness, score the anchor and quantized artifact on the same selected
items. For item score `s`, estimate a benchmark-specific degradation:

```text
delta_hat(q, b) = weighted_mean_i(s(q, i) - s(b, i))
score_hat(q)     = score_full(b) + delta_hat(q, b)
```

Clip only to the benchmark's declared numeric range. The item weights must come
from the subset-selection protocol; a curated subset is not automatically a
simple random sample.

A raw ratio such as `subset_quant / subset_base` is a poor default. Ratios are
unstable near zero, ignore chance floors, behave strangely near a ceiling, and
assume proportional damage across capabilities. If a descriptive retention
ratio is needed for one fixed benchmark with chance floor `c`, report it
separately:

```text
retention = (score_quant - c) / (score_base - c)
```

Do not transfer that retention value to another benchmark, model family,
context length, quantizer, or KV-cache format.

The full anchor must use the same benchmark release and scoring protocol as the
subset. A vendor score produced with another prompt template, sampling policy,
attempt budget, or harness is not an interchangeable anchor. If the high-
fidelity artifact will not fit locally, the paired subset may be run on another
machine, but the runtime difference then becomes an explicit limitation and
should be validated on artifacts that both systems can run.

## Subset construction and uncertainty

Prefer a published, versioned estimator when its source-model population covers
the candidate. `tinyBenchmarks` demonstrates that curated 100-item subsets and
item-response-theory estimators can reproduce several full benchmark scores
with small average error. More recent work frames the same problem as feature
selection and regression, and reports stronger ranking prediction from mRMR
selection plus kernel ridge regression in sufficiently data-rich settings.

Neither result proves that an existing coreset is maximally sensitive to the
small differences between neighboring quantizations. Validate the estimator on
held-out quantized artifacts before making it a packaged default. A useful
training matrix includes multiple model families, sizes, ordinary quants,
custom mixed quants, weight formats, and KV precisions. Use leave-one-family-out
validation so several quantizations of one checkpoint cannot make transfer look
better than it is.

For deterministic binary tasks, preserve paired item outcomes in
`{-1, 0, +1}` and bootstrap items or task clusters. For stochastic generation,
pair task and seed, retain every attempt, and bootstrap at the task level rather
than treating attempts as independent. Store a point estimate, lower and upper
bounds, sample count, estimator version, training-population digest, selected
item IDs/digest, full-anchor result digest, and validation error.

Use sequential budgets:

1. Run cheap proxy checks and a 20–30-item semantic smoke set.
2. Run approximately 100 calibrated items per decision-relevant capability.
3. Add items while the confidence interval can still change dominance or the
   selected default.
4. Run the complete benchmark only for predicted frontier members and offerings
   whose interval intersects the frontier's epsilon envelope.

The numbers are starting budgets, not universal sample-size guarantees. A
one-point quantization regression is intrinsically hard to estimate from a
small binary benchmark, while a catastrophic regression can be rejected very
quickly.

## Recommended benchmark portfolio

Do not collapse every capability into one quantization coefficient. Start with
small, independently reported components:

- general knowledge/reasoning: the published tinyBenchmarks subsets where their
  exact protocol applies;
- code generation: a versioned EvalPlus HumanEval+/MBPP+ subset, selected from
  a result matrix rather than merely taking the first tasks;
- tool use: a stratified BFCL subset spanning correct invocation, distractors,
  parallel/multiple calls, and should-not-call cases;
- long context: a RULER-style ladder across task categories and context lengths,
  plus a code/repository retrieval component;
- real agent work: a stable subset of repository tasks with the exact same
  harness, tool schema, attempt budget, and environment.

Long-context and tool components are mandatory for an agent recommendation.
Published evidence shows that four-bit degradation can grow sharply at long
context and varies materially by model and quantization method. A short-context
academic subset cannot certify a 128K local coding route.

## Frontier recipes

Estimated and measured quality should normally produce separate snapshots:

| Frontier | Axis 1 | Axis 2 | Required gates/cohort |
| --- | --- | --- | --- |
| Quant screening | estimated quality lower bound, maximize | peak physical footprint, minimize | same benchmark component; `estimated` only |
| Interactive local value | measured agent quality, maximize | p95 successful-turn latency, minimize | tool correctness; minimum usable context; no swap growth |
| Fixed-128K usefulness | measured long-context quality, maximize | p95 successful latency, minimize | 128K input plus output reserve; no OOM |
| Session endurance | effective pre-compaction tokens, maximize | p95 incremental-turn latency, minimize | correctness floor; byte-stable agent protocol |
| Warm-cache operation | eligible-token reuse, maximize | p95 successful-turn latency, minimize | correctness equal to uncached control; fixed cache lifecycle |
| Remote agent value | measured or conservative estimated quality, maximize | expected cost per successful work unit, minimize | exact route and price basis; same agent budget |

When the core engine cannot express a third metric as an eligibility gate, the
catalog builder must create a separately versioned, prefiltered cohort and
retain the gate observations. Do not hide a correctness or context constraint
in prose.

For estimated quality, use robust uncertainty or materialize the conservative
lower confidence bound as the explicit quality signal. Keep estimator error in
addition to sampling uncertainty. A candidate is promoted to full evaluation
when either bound could change frontier membership, an epsilon-equivalent
cluster, or the ordered default/fallback selection.

## Packaged-default requirements

A reusable ModelSkyline estimator or example should fail closed unless it has:

- exact full-anchor and candidate offering identities;
- identical benchmark, dataset, split, scorer, prompt, and attempt semantics;
- paired item IDs and immutable task-set hashes;
- a declared selection/weighting method and estimator version;
- point, lower, and upper estimates plus sample count;
- out-of-sample validation error and the model/quantization domain tested;
- an explicit evidence tier and no automatic base-score inheritance;
- source, retrieval/effective time, methodology, and rights metadata; and
- a promotion policy for complete measurements.

The same mechanism saves work for cloud models, but mutable provider aliases
make exact route identity and observation freshness especially important.

## Research basis

- [tinyBenchmarks: evaluating LLMs with fewer examples](https://arxiv.org/abs/2402.14992)
  and its [reference implementation](https://github.com/felipemaiapolo/tinyBenchmarks)
- [Efficient Benchmarking Is Just Feature Selection and Multiple Regression](https://arxiv.org/abs/2605.25773)
- [“Give Me BF16 or Give Me Death”? Accuracy-Performance Trade-Offs in LLM Quantization](https://aclanthology.org/2025.acl-long.1304/)
- [Does quantization affect models' performance on long-context tasks?](https://aclanthology.org/2025.emnlp-main.479/)
- [EvalPlus](https://github.com/evalplus/evalplus),
  [BFCL](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard),
  and [RULER](https://github.com/NVIDIA/RULER)
