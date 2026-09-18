# Needle + Jev for voice and home automation — 2026-09-18

## Short answer

Yes: [Cactus Needle 3](https://github.com/cactus-compute/needle) is unusually
well matched to the narrow step between speech recognition and Home Assistant:
turn a short request into one or more typed calls. Jev can review that proposal
cheaply before anything happens. Neither component is a conversational voice
agent, and the measured pair is not safe enough to control a real home without
additional deterministic policy and confirmation.

“OpenNeedle” here means the open Cactus Needle repository and weights; no
separate OpenNeedle implementation was used.

## What was measured

The result uses the 32 `smart_home` and 32 `media_player` acceptance cases
shipped in `cactus-needle==3.0.1`, each repeated three times on the 64 GB M5.
Calls marked `ungrounded` or `negation` by the shipped validator are discarded,
then the remaining unordered call set must exactly match the reference.

The Jev policy is deliberately simple:

1. Needle proposes calls locally.
2. If the proposal is empty, execute nothing and do not call Jev.
3. Otherwise Jev chooses `approve` or `reject` from the request, declared tool
   schemas, proposed calls, Needle confidence, and validation flags.
4. `approve` executes the proposal unchanged; `reject` executes nothing.

Jev does not generate a corrected call. A rejected valid request therefore
needs clarification or a separate tool-capable repair worker such as Qwen.

| Complete system | Exact success | Tool-policy compliance | Unsafe actions | p95 wall time | Guard cost/case |
| --- | ---: | ---: | ---: | ---: | ---: |
| Needle 3 alone | 85.9375% | 87.500% | 12.500% | 0.0812 s | n/a |
| Needle 3 + Jev exact-call guard | **95.3125%** | **96.875%** | **3.125%** | 0.4802 s | $0.00003595 |

Both are residents of both committed two-axis frontiers. Needle is the speed
choice; the pair is the quality and policy-compliance choice. Neither dominates
the other because the guard adds about 0.40 seconds at p95.

Inside the compound run, its matched Needle primary succeeded on 165/192
observations. The guard rescued 18 of the 27 primary failures and harmed 0 of
the 165 primary successes. Jev made 144 reviews, agreed with the exact-call
oracle on 138, and cost $0.006901524 total. Six unsafe media calls remained.
On the smart-home half alone, the guarded pair succeeded on 93/96 observations
(96.875%) with zero observed unsafe actions; all six false approvals were in
the media half. That small split is encouraging, not a zero-risk guarantee.

These numbers are a calibration result, not a product safety claim:

- the cases were written by the model vendor and may resemble training data;
- the tool sets are small, and no Home Assistant state transition was checked;
- the changing date fact that Needle normally injects into its system context
  was disabled and recorded so a later rerun sees the same instructions;
- “unsafe” here narrowly means executing a call when the frozen tool contract
  expected none, not a real-world severity assessment; and
- the reported cost excludes the Mac, electricity, network, ASR, TTS, Home
  Assistant, and any repair worker.

## Why the media failures matter

The remaining false approvals were requests for a podcast or radio station.
Those are reasonable user requests, but the frozen `media_player` contract
supports music, playback state, and volume—not podcasts or radio. In a real
deployment there are two valid fixes: add explicit podcast/radio tools, or keep
rejecting those requests. Silently mapping them to `play_music` violates the
declared contract even if the result sometimes sounds acceptable.

## Recommended voice/home pipeline

```text
microphone
  -> local voice-activity detection + ASR
  -> identity, room, and permission context
  -> per-user allow-listed tool schemas
  -> Needle proposal
  -> deterministic schema/range/negation/device-state checks
  -> Jev review for every proposed action during the pilot
       approve -> risk policy -> Home Assistant service call
       reject  -> clarify, refuse, or ask a tool-capable model to repair
  -> verify resulting device state
  -> local or hosted TTS
```

Important deployment rules:

- Never give Needle's `agent.run()` direct access to real actuators during the
  pilot. Use `complete()`, intercept calls, validate them, then execute through
  a small Home Assistant adapter.
- Keep deterministic policy outside both models: user/device permissions,
  numeric ranges, alarm/lock/garage/purchase rules, quiet hours, and maximum
  action counts should be ordinary code.
- Require spoken or UI confirmation for locks, alarms, doors, purchases,
  security cameras, remote access, and unusually large HVAC changes even when
  Jev approves.
- A repair model must not bypass the same validation and review path.
- Send Jev the smallest typed state possible. The guard is remote, so the
  transcript fragment, schemas, and proposal leave the home. Sensitive actions
  may need a local guard instead.
- Keep Needle resident as a tiny sidecar. The downloaded 3.0.1 weight artifact
  is 35,335,380 bytes and the macOS runtime is 788,224 bytes, so there is no
  memory reason to swap it with the large llama-swap models.

## Practical next evaluation

The next useful test is not another vendor-authored utterance list. Build a
small Home Assistant simulator with the actual local entity and service schemas,
feed paraphrased/noisy ASR transcripts, and verify final state. Include:

- missing rooms and ambiguous device names;
- negation, corrections, and multi-action requests;
- stale/unavailable devices and already-satisfied states;
- permission boundaries and high-impact actions;
- podcast, radio, queue, volume, and whole-home media behaviors; and
- a Qwen repair path after Jev rejects a valid request.

That holdout can define a production-oriented frontier: verified task success
versus voice-to-action latency, with zero high-impact policy violations as an
eligibility gate.

## Reproducible artifacts

- [`needle-environments-v3.0.1-manifest.json`](needle-environments-v3.0.1-manifest.json)
  pins the package, source revision, modules, runtime, and weights.
- [`run_needle_home_media_panel.py`](run_needle_home_media_panel.py) runs Needle
  alone or the Jev guard and emits prompt-free evidence.
- [`generated/needle-r3-composed-catalog.json`](generated/needle-r3-composed-catalog.json)
  contains the two complete systems.
- [`generated/needle-r3-outcome-latency-frontier.json`](generated/needle-r3-outcome-latency-frontier.json)
  and [`generated/needle-r3-policy-latency-frontier.json`](generated/needle-r3-policy-latency-frontier.json)
  are the result-of-record frontier snapshots.
