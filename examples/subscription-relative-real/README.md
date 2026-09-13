# Real subscription-relative frontier (live deployment example)

Frontier over two real coding-model subscriptions (OpenCode Go, ClinePass),
where the cost axis is **share of the offering's monthly subscription cap
burned per successful agent turn** — computed from 30 days of real agent
traces (1,336 turns, 93% success) at each subscription's API-equivalent
rates. Quality axis: Artificial Analysis Intelligence Index v4.1.1.

Peak/off-peak DeepSeek tiers are modeled as separate offerings; the provider
passes DeepSeek's own pricing windows (01:00-04:00, 06:00-10:00 UTC) through
unchanged. `summary.json` is the published artifact consumed by a dashboard
widget (pareto ranking + domination info).

ClinePass cap is assumed at $35/month (advertised "2-5x usage on $9.99"),
not dollar-published — see provenance in observations.json.

## Local runtime measurements

Treat each hardware, artifact format, quantization, runtime version, and tuned
configuration as a distinct offering. In particular, an MLX conversion and a
GGUF quant of the same upstream model are comparable candidates, not the same
offering: their kernels, quantizers, prompt caches, and serving behavior differ.

`measure-ingest.py` accepts current `llama-bench -o json`, MLX-LM benchmark
text, Ollama benchmark CSV, and DS4 benchmark CSV output. DS4 ladders represent
different context workloads rather than repeated samples; use
`--context-tokens` to select the frontier being ingested (the largest is the
default). Supply a runtime-specific `--offering-id`; add `--capability tools`
or `--capability images` only after an end-to-end server test. Quality metadata
is intentionally omitted unless both `--aa-index` and its `--aa-source` are
supplied.

For publishable comparisons, run one engine at a time with the same prompt and
generation lengths, repeat count, power mode, and background-load policy.
Record cold-load latency separately, and do not publish a run taken while OS
indexing, synchronization, or another local model process is active.
