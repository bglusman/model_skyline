# RTX 5060 Ti llama-swap deployment

This is the deployment manifest for `inference-vm-rtx5060ti16`. It exposes one
LAN endpoint at `192.168.1.125:8090`, keeps child runtimes on loopback, and puts
every registered heavyweight model in one exclusive `local-memory` group.
Loading progress is disabled so it cannot become assistant content.

The initial manifest registers the two Ollama models already present on the
host. The Laguna ShoeHorn routes will be added only after their exact artifacts
exist; a model list should not advertise a path that cannot start. Each future
llama.cpp launcher must use `local-model-exclusive`, `-ngl all -fit off`, an
explicit context/KV profile, and a loopback `${PORT}` supplied by llama-swap.

The mutex is cooperative. A client that calls Ollama directly on port 11434 can
still reload a model behind llama-swap's back. Consumers should use port 8090.
Before a benchmark, unload every router model, stop both known Ollama models,
confirm `/running` is empty, and confirm `nvidia-smi` shows the expected free
VRAM. Retain that free-memory value with the capture.

The lock wrapper acquires `flock` on file descriptor 9 and then `exec`s the
runner. This detail matters: `flock FILE command` forks on this host, so killing
the PID tracked by llama-swap can otherwise orphan the child and release the
lock at the wrong time. The Ollama stop hook also waits until `ollama ps` no
longer lists the model before releasing its holder.

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
