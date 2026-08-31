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
