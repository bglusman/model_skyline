# Draft upstream report: DFlash prompt reuse changes Muse output

Proposed title: `Muse Glimmer DFlash produces different greedy tool result after identical-prefix reuse`

This is a local draft, not a submitted issue.

## Environment

- llama.cpp build 10809, commit `5266f24da`
- Apple M5 Max, macOS 26.6.2, Metal, one server slot
- target: Muse Glimmer Dynamic Q4_K_XL, 19,653,960,832 bytes, SHA-256
  `ac7023d6a4c704eb9af54ab53e476a66b7f5b6c0ef2fc4a8dde5253c291a6c38`
- draft: Muse Glimmer DFlash2 Q4_K_M, 1,645,657,280 bytes, SHA-256
  `93dbfb6f88e4645dec1347cf93f9d6fc80b90d413038722385b2a8e53565c949`

## Server

```console
llama-server \
  --model Muse-Glimmer-30B-KQuant-Dynamic-Q4_K_XL.gguf \
  --model-draft Muse-Glimmer-30B-DFlash2-Q4_K_M.gguf \
  --alias muse-dflash-repro --host 127.0.0.1 --port 8085 \
  --ctx-size 131072 --parallel 1 \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --cache-type-k-draft q8_0 --cache-type-v-draft q8_0 \
  --n-gpu-layers all --n-gpu-layers-draft all --fit off \
  --flash-attn on --jinja --spec-type draft-dflash --spec-draft-n-max 3
```

## Request

From a Model Skyline checkout, run the same seeded 30-tool request twice:

```console
python examples/local-runtime-frontiers/openai_matrix.py \
  --base-url http://127.0.0.1:8085/v1 \
  --model muse-dflash-repro \
  --mode tool --tool-count 30 --tool-choice auto \
  --thinking-mode runtime_default \
  --system-prompt \
    'You are a deterministic local runtime measurement probe. Reasoning strength: low.' \
  --prefix-tokens 2048 --max-outputs 1024 --repetitions 2 \
  --output dflash-cache-repro.json
```

The harness sends temperature 0 and seed 90421. The complete semantic input
definition SHA-256 is identical for both repetitions. The API reports 5,390
input tokens.

Observed with prompt reuse enabled:

1. cache miss: 463 completion tokens, `finish_reason=stop`, no tool call,
   269 content bytes, SHA-256
   `ca3b6cdcba52b8648197959acaeb68af671a3c068a0de37b22515dad278d4876`;
2. 5,389 cached prefix tokens: 335 completion tokens,
   `finish_reason=tool_calls`, exact `lookup_fixture` call with
   `{"key":"skyline-cache-probe","limit":7}`.

This miss/hit split reproduced across fresh server loads. Two independent cold
loads produced the same miss output digest. Two separate warm batches produced
the exact tool call in all six requests.

## No-reuse control

Add `--cache-ram 0 --no-cache-prompt` to the server command and repeat the same
two requests. Both report zero cached tokens, both produce 463 completion
tokens, and both return the same 269-byte non-tool output digest. Setting only
`--cache-ram 0` is insufficient: same-slot longest-common-prefix reuse remains
active and the second request still reports 5,389 cached tokens.

A target-only server using the same main artifact produces the exact tool call
on both cache miss and hit, although its reasoning-token count also changes.

## Expected behavior

Reusing an identical prefix should preserve the greedy target result. At
minimum, the cached and uncached paths should not change whether the model emits
the requested tool call. The controls indicate an interaction between
`draft-dflash` and prompt reuse rather than a cold model-load or router issue.

Machine-readable captures are in `examples/local-runtime-frontiers/raw/` with
prefix `muse-glimmer-dynamic-q4xl-dflash2`.
