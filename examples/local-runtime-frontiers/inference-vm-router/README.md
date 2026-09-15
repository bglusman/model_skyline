# RTX 5060 Ti llama-swap deployment

This is the deployment manifest for the RTX 5060 Ti inference VM. It exposes one
LAN llama-swap endpoint, keeps child runtimes on loopback, and puts
every registered heavyweight model in one exclusive `local-memory` group.
Loading progress is disabled so it cannot become assistant content.

The manifest registers the two Ollama models already present on the host and an
experimental Qwen3-TTS route served by Nari's consumer-GPU profile. The tested
Laguna ShoeHorn artifact is deliberately not registered: although it is fast,
it fails the matched retrieval and perplexity gates. A model list should not
advertise a path that cannot start or is known to be unusable. Each future
llama.cpp launcher must use `local-model-exclusive`, `-ngl all -fit off`, an
explicit context/KV profile, and a loopback `${PORT}` supplied by llama-swap.

The checked-in `ollama-model-skyline.conf` binds Ollama to loopback, limits it
to one loaded model, and aligns its five-minute keep-alive with llama-swap's
300-second TTL. This makes port 8090 the only LAN entry point instead of merely
asking clients to cooperate. Before a benchmark, unload every router model,
stop both known Ollama models, confirm `/running` is empty, and confirm
`nvidia-smi` shows the expected free VRAM. Retain that free-memory value with
the capture.

The official [Ollama FAQ](https://docs.ollama.com/faq) documents loopback as
the default and `OLLAMA_HOST` as the network-exposure control. Its local API has
no authentication, so exposing port 11434 would bypass both routing and model
ownership here.

The lock wrapper acquires `flock` on file descriptor 9 and then `exec`s the
runner. This detail matters: `flock FILE command` forks on this host, so killing
the PID tracked by llama-swap can otherwise orphan the child and release the
lock at the wrong time. The Ollama stop hook also waits until `ollama ps` no
longer lists the model before releasing its holder.

The mutex works only when every heavyweight launcher participates. A direct
vLLM, TTS, benchmark, or ad-hoc Python process can still allocate the GPU behind
llama-swap's back. Such launchers must acquire the same `local-model-exclusive`
lock for their whole lifetime. Benchmark harnesses should also fail closed when
the GPU is already occupied; the ASR activation probes do this before every
child rather than assuming an empty llama-swap `/running` response proves that
VRAM is free.

The checked-in lock wrapper now performs that fail-closed CUDA check itself.
After obtaining the cooperative flock, it refuses to launch if `nvidia-smi`
reports any existing compute PID. This catches deployment mistakes and stale
direct clients before a second participating runner starts. It cannot prevent a
new local process from bypassing both the router and lock later, so the Ollama
loopback binding remains essential.

## Apply and verify the deployment

Install the checked-in overrides and wrappers, reload systemd, then restart the
two services. Restarting Ollama unloads its current model and disconnects any
client that still bypasses llama-swap, so do this at a planned handoff point.

```console
install -m 0644 ollama-model-skyline.conf \
  /etc/systemd/system/ollama.service.d/zz-model-skyline.conf
install -m 0755 local-model-exclusive verify-ollama-loopback \
  /usr/local/libexec/model-skyline/
systemctl daemon-reload
systemctl restart ollama.service llama-swap.service
/usr/local/libexec/model-skyline/verify-ollama-loopback
```

Do not infer success from the override file alone. `systemctl cat` can show a
correct checked-in fragment while another deployed copy still sets
`OLLAMA_HOST=0.0.0.0:11434`. The verifier checks the effective systemd
environment, the actual listening socket, active client peers, and the local
API. LAN consumers must use llama-swap instead of port 11434.

The Nari launcher is deliberately a single foreground process: llama-swap's
tracked PID, the lock owner, and the CUDA server remain the same process after
the shell wrappers call `exec`. It prepends the project virtual environment to
`PATH` because Nari's runtime compilation invokes the installed `ninja`
executable by name. It also forces offline model resolution, one active request,
and loopback binding. This VM's default repository location is
`/root/nari-qwen3-tts`; set `NARI_QWEN3_TTS_DIR` in the llama-swap service when a
deployment uses another path.

The exact experimental Nari tree used for the measurements starts at upstream
commit `e8c5b2bf6d65a965037f161d713bfa3e8856feb3` and applies
[`nari-qwen3-tts-consumer.patch`](nari-qwen3-tts-consumer.patch). The patch adds
an explicit non-H100 opt-in and a batch-one profile; it does not weaken the
existing H100 profiles. Install the repository's locked `codec`, `cuda`, and
`serving` dependency groups before using the route. This is a measured local
compatibility patch, not an upstream Nari release.

llama-swap v255 successfully dispatches `/v1/audio/speech` for the exact Qwen
model ID, but its `/v1/models` response omits this audio-only route. TTS clients
must therefore configure `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` explicitly;
absence from that discovery response does not mean the route is unavailable.

The pinned router binary is llama-swap v255, release commit `7761aa1`. The
official Linux AMD64 archive SHA-256 is
`84aa0df0cf3e302a8591e39de347f64c0c7dce1c3a948df68723a82e1fb4f1d4`.
See [llama-swap](https://github.com/mostlygeek/llama-swap) for the upstream
router and API documentation.

## Deployed validation

On 2026-09-14 the manifest was installed as an enabled systemd service. A
Qwen-embedding → Qwen3.5 chat → Qwen-embedding → Qwen3.5 switch sequence left
exactly one Ollama runner and one tracked lock-holder after every completed
transition. An explicit router unload left no Ollama runner, no holder, and
15,779 of 16,311 MiB VRAM free (`nvidia-smi` reports 70 MiB in use for the
display stack). The earlier `flock FILE command` wrapper and Ollama CLI stop
hook both failed this validation; the checked-in descriptor-lock and HTTP
unload forms are the tested versions.

A later audit found simultaneous 9B chat and 0.6B embedding runners loaded by
direct LAN calls to Ollama even while `/running` was empty. This was the
remaining bypass that motivated the loopback binding and
`OLLAMA_MAX_LOADED_MODELS=1` defense above. Clients formerly using port 11434
must select the same model IDs through port 8090.

On 2026-09-15 a whole-service TTS capture caught the same class of drift again:
the repository override said loopback, but the installed
`zz-model-skyline.conf` still contained `OLLAMA_HOST=0.0.0.0:11434`, and live
LAN clients loaded Qwen while a manual vLLM process held the cooperative lock.
That capture was discarded. The retained capture stopped both services first;
the deployment verifier exists so future work can detect the effective-state
mismatch before benchmarking.

The override was corrected on the same day. Validation confirmed a real
loopback-only listener, rejection of a deliberately direct CUDA resident with
exit 75, successful model activation through llama-swap after cleanup, and an
explicit unload leaving both the GPU and flock free.
