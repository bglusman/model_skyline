# Mac Studio llama-swap deployment

This is the exact deployment manifest for the 64 GB M1 Max comparison host at
`192.168.1.175`. It replaces two independently persistent heavyweight servers
with one `llama-swap` endpoint and one exclusive `local-memory` group.

The router listens on the LAN at port 8090. Child inference servers bind only
to loopback. Loading-state stream messages are disabled so agent clients cannot
append operational text to conversation history. The server commands also hold
the same inherited `lockf` used by direct benchmark captures.

The Qwen route preserves the prior 131K llama.cpp configuration but deliberately
points at `model-skyline-exact/qwen3.8/`: that artifact is copied byte-for-byte
from the M5 benchmark host. The older Studio-local Qwen GGUF has a different hash
and is not suitable for an architecture-only comparison. The DS4 route preserves
the prior 32K DeepSeek V4 Flash SSD-streaming configuration. Ornith is
the byte-identical Q4_K_M artifact used for the existing M1/M5 comparison
(`ca6ea26329c88b78ffd90a85163be2e746c2fafd1024f56db47e499f117f9a7f`).
Three already-downloaded Ollama candidates are also routed through lock-holder
processes. Their five-minute route TTL matches Ollama's keep-alive; unloading a
route explicitly stops that model's weights while leaving the desktop daemon.
The two llama.cpp routes use the Homebrew binary so the M1 and M5 captures can
pin the same bottled build and runtime hash in addition to identical model bytes.

The old `com.openclaw.ds4-flash` LaunchAgent must be booted out before enabling
this one, and the orphaned standalone Qwen server must be terminated. Retain a
renamed copy of the old plist for rollback. Installation copies these files to
`~/.local/bin`, `~/.config/llama-swap`, and `~/Library/LaunchAgents`; no model
bytes are moved by the manifest itself. The exact Qwen comparison artifact was
transferred separately and verified by SHA-256 before its route was enabled.
