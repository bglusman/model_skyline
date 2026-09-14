# Qwen3.8 Flash Coder 160-expert subnet screen

This is an additive candidate screen, not a change to the frozen five-task
quality pilot. The exact artifact is
[`Jab1718/qwen3.8-flash-coder-26gb-gguf`](https://huggingface.co/Jab1718/qwen3.8-flash-coder-26gb-gguf/tree/4d62a522f3c70b6e9579015602129a15fe161804),
revision `4d62a522f3c70b6e9579015602129a15fe161804`, file size
28,394,087,776 bytes, and SHA-256
`11f6a81dba702bf9603d1a1249b01752e84e3e049d15ae366c2f80032670d121`.
Its publisher describes it as a 160-expert coding subnet selected from
Qwen3.8 Flash Next. That construction is candidate provenance, not evidence
that the slice retains the base model's quality.

The measured host used llama.cpp 0.4.0 build 10809, commit `5266f24da`, Metal
4, all layers offloaded, Q8_0 K/V, one 131,072-token slot, automatic fitting
disabled, and llama-swap's exclusive local-memory group. The default screened
route used low reasoning with a 4,096-token thinking ceiling; a second Harbor
control used xhigh reasoning with an 8,192-token ceiling. Exact system profiles
are in [`system-profiles/`](system-profiles/).

## What passed

The cache-disabled 30-tool automatic-selection control made the exact expected
call with valid arguments in 3/3 repetitions. It used 5,260 actual input tokens
and had 4.817–4.848 seconds TTFT, 5.524–5.553 seconds end-to-end latency, and
57.80–58.14 decoded token/s. Its 5.527-second median makes it the sole current
resident of the exact uncached 2K-prefix/256-output tool frontier, ahead of
DS4's earlier 6.433-second point.

With the identical prefix cached, a separate 1,024-output-ceiling control also
made the exact call 3/3 times. Median end-to-end latency was 0.987 seconds and
median TTFT was 0.287 seconds. It is nevertheless dominated at that exact warm
position by Ornith (0.835 seconds) and dense Qwen3.8 DFlash (0.862 seconds).

These are real operational measurements of one deterministic tool call. They
do not establish coding-agent quality.

## What failed

Allocated context and validated context diverged sharply:

| Actual input tokens | Needle position | Exact passes | TTFT | End-to-end |
| ---: | ---: | ---: | ---: | ---: |
| 2,011 | 90% | 0/3 | 1.692 s miss; 0.066–0.070 s warm | 2.320 s miss; 0.731 s warm |
| 32,734 | 90% | 0/1 | 41.511 s | 42.166 s |
| 65,501 | 90% | 0/1 | 125.590 s | 126.331 s |
| 125,963 | 90% | 0/1 | 399.604 s | 400.303 s |

At 2K the answer contained the correct passkey but violated the explicit
answer-only contract by adding prose and a code fence. At 32K and above the
returned content did not equal the passkey. The route therefore has a measured
131K allocation ceiling but no validated long-context capacity from this
screen, and the system profiles intentionally omit the `long-context`
capability.

A matched midpoint rerun then disabled the llama.cpp prompt cache at the
server, used the same deterministic prompt bytes as the capacity cohort, and
confirmed zero cached tokens and zero swap growth at every position:

| Actual input tokens | Needle position | Exact passes | TTFT | End-to-end | Physical footprint |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2,012 | 50% | 0/1 | 1.875 s | 2.029 s | 3,531,562,240 B |
| 32,732 | 50% | 0/1 | 42.158 s | 42.956 s | 4,110,147,248 B |
| 65,500 | 50% | 0/1 | 124.816 s | 125.555 s | 5,249,196,512 B |
| 125,964 | 50% | 0/1 | 388.905 s | 390.111 s | 7,050,454,744 B |

This one-repetition ladder is negative screening evidence, not a positive
capacity validation. All four positions normalize as failed records. The
capacity catalog retains the exact route and raw hashes for audit but emits no
validated-context or paired-footprint signal, so the standard frontier lists
it as rejected and keeps DS4 as the sole resident.

The verifier-valid Terminal-Bench `fix-git` smoke also failed under both
reasoning profiles:

| Profile | Reward | Episodes | Parser errors / warnings | Agent execution | Verifier |
| --- | ---: | ---: | ---: | ---: | --- |
| low / 4K thinking | 0 | 40 | 28 / 11 | 57.185 s | 0/2 tests |
| xhigh / 8K thinking | 0 | 40 | 23 / 15 | 137.498 s | 0/2 tests |

Both trials had complete API accounting and no infrastructure exception. The
xhigh control spent more than twice as long and reduced errors slightly, but
still exhausted the 40-turn agent limit and failed both verifier tests. The
candidate consequently did not advance to the five-task quality pilot.

## Decision

Keep this artifact as an optional llama.cpp specialist and regression target,
not an OpenCode/OMP default. It earns one narrow uncached-tool frontier because
that is what the measurements show. Its failed agent smoke and failed retrieval
ladder prevent that point from being generalized into coding quality or usable
128K context. A future promotion requires a clean Terminus JSON-action smoke
and a repeated multi-position retrieval ladder after any chat-template,
sampling, or expert-slice revision.

Prompt-free raw evidence is retained in [`raw/`](raw/), including the
cache-disabled and warm tool matrices, the original four retrieval captures,
the matched cache-disabled midpoint ladder, and both Harbor summaries.
[`harbor-quality-screen-qwen38-flash-coder.yaml`](harbor-quality-screen-qwen38-flash-coder.yaml)
is separate from `harbor-quality-pilot.yaml` so adding this candidate does not
invalidate the digest-bound results already published from the frozen pilot.
