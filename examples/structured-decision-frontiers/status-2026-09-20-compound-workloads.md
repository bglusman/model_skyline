# Compound workload stress result — 2026-09-20

## Result

The richer workload changed the conclusion. Jev handles the obvious route
categories, but it does not reliably honor the boundary fact that should stop,
relocate, or independently verify work.

The new screen contains 36 packets, exactly six for each of these routes:
`deterministic`, `local_specialist`, `local_general`, `remote_model`,
`model_plus_verifier`, and `human_review`. They form 18 contrast sets. Within
each pair, one state field changes and the correct route changes with it.

On three repetitions through hosted Jev 1.13:

| Measure | Result |
| --- | ---: |
| Exact route accuracy | 79/108 (73.15%) |
| Unsafe routes | 29/108 (26.85%) |
| Correct `human_review` routes | 3/18 (16.67%) |
| Correct local-general routes | 9/18 (50.00%) |
| Correct model-plus-verifier routes | 13/18 (72.22%) |
| Correct deterministic / local-specialist / remote routes | 54/54 (100%) |
| Contrast pair-repetitions with both sides correct | 25/54 (46.30%) |
| p95 latency | 0.399 s |
| Provider-reported cost | $0.003416364 |

The deterministic policy control matched the authored oracle on 36/36 cases.
That proves the screen and control agree; it does not prove the policy is a
general solution.

## What the aggregate hid

Jev was perfect when the category was stable and explicit. It failed when one
constraint invalidated that category:

- an out-of-context or unsupported-language specialist case remained routed
  to the specialist, at roughly 0.95–0.98 confidence;
- private catalog analysis still went to the remote model, around 0.80;
- missing evidence, authority, or checksum capability still went to the
  deterministic route, around 0.85–0.91;
- an irreversible catalog change still went to model-plus-verifier, around
  0.90; and
- an unauthorized BookLore mutation still went to model-plus-verifier.

The choice confusion matrix was therefore more informative than a scalar
accuracy. Of 18 expected `human_review` decisions, Jev selected deterministic
9 times, model-plus-verifier 6 times, and human review only 3 times. Of 18
expected local-general decisions, it selected the specialist 6 times and the
remote model 3 times.

A confidence fallback is not an adequate repair. The first measured projection
with zero unsafe routes required a 0.99 threshold and handled only 8.33% of
observations. Several unsafe boundary failures were confidently wrong.

The prompt-free result-of-record is
[`compound-routing-jev-r3-summary.json`](compound-routing-jev-r3-summary.json).

## Harness improvements forced by the experiment

The workload exposed two limitations that simpler three-choice screens did
not:

1. Safety cannot be defined only by the expected class. A remote choice can be
   safe-but-expensive for public data and unsafe for private data. The runner
   now accepts case-level `unsafe_predictions` and binds them into case
   identity.
2. `abstain` is a semantic role, not necessarily a literal option label. The
   suite now declares `human_review` as its abstention choice, so autonomous
   coverage is not overstated.

The result schema also retains prompt-free selected choice IDs for confusion
matrices. Earlier artifacts remain valid because these fields are additive and
optional.

## Implication for the compound hypothesis

The hypothesis should not be “two models beat one.” It should be:

> A typed control graph earns deployment only when its components have
> complementary errors and the graph improves verified outcomes under explicit
> cost, latency, privacy, authority, and safety constraints.

That requires separate representation of topology, component role and kind,
contract, placement, authority, state binding, activation, adaptation, and
recovery. The proposed classification and experiment backlog are in
[`compound-workload-research-agenda-2026-09-19.md`](compound-workload-research-agenda-2026-09-19.md).

## Next measurements

1. Run the unchanged screen through the best local trained decision model and
   a direct-logit control. The useful comparison is not Jev versus one model;
   it is which errors are complementary.
2. Build the media v2 holdout from adjudicated incidents, preserving the same
   one-fact contrast design for work ID, edition, language, abridgement,
   coverage, metadata corruption, and authority.
3. Add a disposable BookLore/Biblioaudio state fixture with deterministic
   postconditions, stale-version injection, idempotency checks, and rollback.
4. Only then compare model-only, deterministic→model,
   local-specialist→general-fallback, and proposer→verifier graphs on verified
   task outcomes.

No result here authorizes a catalog, synchronization, communication, or device
write.
