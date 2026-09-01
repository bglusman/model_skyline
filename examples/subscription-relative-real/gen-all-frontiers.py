#!/usr/bin/env python3
"""Generate all summary frontiers for the glance artifact.

Workloads: agent-chat (real 30-day trace shape), coding-session (OpenCode Go
published per-model request shapes). Axes: economics (cap-share or metered $)
and responsiveness (AA output speed). Quality: AA Intelligence Index.

Output: summary-v2.json with {"frontiers": [{id, workload, axes, default,
fallbacks, ranked[]}]} — consumed by the glance custom-api widget.
"""
import json
import subprocess
from decimal import Decimal
from pathlib import Path

REPO = Path("/root/.openclaw/workspace/model-skyline-external")
BASE = REPO / "data/real/subscription-relative"
NOW = "2026-09-01T00:30:00Z"

# ---- shared inputs ----
chat = json.loads((BASE / "observations.json").read_text())
sel = json.loads((BASE / "selection.json").read_text())

# Real agent-chat token shape (per successful turn, from 30-day traces)
CHAT_SHAPE = {"in": 14592, "cached": 166621, "out": 592}
# Coding-session request shapes (OpenCode Go published, tokens/request)
CODING_SHAPES = {
    "glm-5.3": (700, 52000, 150), "glm-5.2": (700, 52000, 150),
    "deepseek-v4-flash-0731": (410, 71300, 310),
    "qwen3.8-2.4t-a95b": (420, 66000, 200),
    "qwen3.8-max": (420, 66000, 200),
}
# AA quality + speed (Intelligence Index, median output t/s, TTFT s)
AA = {
    "opus-5": {"aa": 63, "tps": None, "ttft": None},
    "claude-fable-5.1": {"aa": 66, "tps": 66, "ttft": 285.33},
    "qwen3.8-flash-next": {"aa": 56, "tps": 89, "ttft": 2.6},
    "gpt-5.6-luna": {"aa": 52, "tps": 132, "ttft": 163.88},
    "gpt-5.6-terra": {"aa": 57, "tps": 119, "ttft": 138.05},
    "muse-spark-1.2": {"aa": 57, "tps": None, "ttft": None},
    "muse-spark-1.2-contributor": {"aa": 57, "tps": None, "ttft": None},
    "longcat-2.0": {"aa": 34, "tps": None, "ttft": None},
    "claude-fable-5": {"aa": 62, "tps": 67, "ttft": 100.13},
    "gpt-5.6-sol": {"aa": 61, "tps": 81, "ttft": 52.39},
    "glm-5.3": {"aa": 60, "tps": 77, "ttft": 1.61},
    "gpt-oss-120b": {"aa": 24, "tps": 168, "ttft": 0.83},
    "gpt-oss-20b": {"aa": 15, "tps": 114, "ttft": 1.11},
    "qwen3-30b-a3b-2507": {"aa": 9, "tps": 143, "ttft": None},
    "qwen3-coder-30b": {"aa": 14, "tps": 93, "ttft": 2.57},
    "glm-5.3-flash": {"aa": 57, "tps": 45, "ttft": 1.54},
    "deepseek-v4-flash-0731": {"aa": 52, "tps": 119, "ttft": 1.12},
    "qwen3.8-2.4t-a95b": {"aa": 52, "tps": None, "ttft": None},
    "qwen3.8-max": {"aa": 52, "tps": None, "ttft": None},
}
# Go API-equivalent prices ($/Mtok: uncached, cached, out) and monthly cap
GO_PRICES = {
    "glm-5.3": (1.40, 0.26, 4.40, "15"), "glm-5.2": (1.40, 0.26, 4.40, "60"),
    "deepseek-v4-flash-0731": (0.44, 0.014, 1.32, "30"),
    "qwen3.8-max": (2.00, 0.25, 6.00, "15"),
}
CP_PRICES = {
    "glm-5.3": (1.40, 0.26, 4.40, "35"),
    "deepseek-v4-flash-0731": (0.44, 0.014, 1.32, "35"),
}
# OpenRouter metered (live catalog 2026-08-31)
PREMIUM_PRICES = {  # first-party metered equivalents for $20-tier subs
    "gpt-5.6-sol": (5.00, 0.50, 30.00),   # Sol $5/$30; cached rate estimated ~10%
    "gpt-5.6-luna": (0.20, 0.02, 1.20),   # Luna in Plus (250-2000 msgs/5h)
    "opus-5": (5.00, 0.50, 25.00),        # Opus 5 $5/$25; cache hits 10%
}
PREMIUM_VENDOR = {"gpt-5.6-sol": "chatgpt-plus", "gpt-5.6-luna": "chatgpt-plus", "opus-5": "claude-pro"}
SUB_CAP_MODEL = "160"  # modeled 8x purchase price, community-reported "many times"; UNVERIFIED
OR_PRICES = {
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "muse-spark-1.2": (1.25, 0.15, 4.25),
    "muse-spark-1.2-contributor": (0.10, 0.002, 0.20),  # trains on prompts; region-limited
    "longcat-2.0": (0.30, 0.006, 1.20),
    "claude-fable-5": (10.00, 1.00, 50.00),
    "claude-fable-5.1": (10.00, 0.25, 50.00),   # cache-read cut $1.00->$0.25 = the "finally economical" change
    "qwen3.8-flash-next": (0.15, 0.012, 0.47),  # OR metered; 89% cache discount
    "gpt-5.6-sol": (2.00, 0.20, 10.00),
    "qwen3.8-max": (2.00, 0.25, 6.00),  # = OR catalog qwen3.8-2.4t-a95b
    "glm-5.3": (1.40, 0.26, 4.40), "glm-5.3-flash": (0.075, 0.015, 0.25),
    "deepseek-v4-flash-0731": (0.065, 0.016, 0.18),
    "qwen3.8-2.4t-a95b": (2.00, 0.25, 6.00), "qwen3.8-27b": (0.425, 0.085, 2.55),
    "gpt-oss-120b": (0.037, 0.0, 0.17), "gpt-oss-20b": (0.030, 0.0, 0.13),
    "qwen3-30b-a3b-2507": (0.048, 0.0, 0.193), "qwen3-coder-30b": (0.070, 0.0, 0.28),
}
GPQA = {  # GPQA Diamond %; AA-run unless noted
    "gpt-5.6-sol": (94.1, "AA-run"),
    "gpt-5.6-terra": (92.5, "AA-run"),
    "gpt-5.6-luna": (91.1, "AA-run"),
    "claude-fable-5": (92.6, "vendor-reported"),
    "deepseek-v4-flash-0731": (90.8, "AA-run"),
    "qwen3.8-flash-next": (91.7, "vendor-reported"),
    "gpt-oss-120b": (80.1, "aggregator-reported"),
}
SWE = {  # SWE-bench Verified %; vals.ai = independent same-harness (Mini-SWE-agent), aggregator = openlm
    "glm-5.3": (95.4, "vals.ai-independent"),
    "glm-5.3-flash": (92.0, "vals.ai-independent"),
    "deepseek-v4-flash-0731": (88.8, "vals.ai-independent"),
    "qwen3.8-max": (85.6, "aggregator-reported"),
}
SRC = {"id": "brian-multi-frontier-v2", "version": "2", "license": "MIT (derived); upstream terms preserved",
       "methodology": ("Axes from AA Intelligence Index / median output t-s / TTFT (artificialanalysis.ai, 2026-08-31); "
                       "prices from OpenCode Go + ClinePass published tables and live OpenRouter catalog; shapes: agent-chat "
                       "from real 30-day traces, coding-session from OpenCode Go published request patterns. "
                       "ClinePass cap assumed $35; $20-tier sub caps modeled at 8x purchase price ($160) per community reporting, all UNVERIFIED and flagged. Added 2026-09-01: GPT-5.6 Luna/Terra, Muse Spark 1.2, Claude Fable 5.1 (aa 66, cache-read cut to $0.25/M = 1.6x cheaper than Fable 5 at our shape), Qwen3.8-Flash-Next (OR + local-slotstream zero-dollar offering, paid in time: ~49s decode/turn at 12 tok/s) (+contributor tier: Meta trains on prompts, region-limited — priced accordingly), LongCat 2.0; muse-glimmer excluded (no verified AA). Math-smarts axis: GLM-5.3/Flash and muse-spark lack verifiable GPQA numbers and are excluded-with-reason there — notable because GLM-5.3 leads the general index. Excluded where no verified AA data.")}


def cost_per_turn(prices, shape):
    unc, cch, out = prices
    return (Decimal(str(shape["in"])) * Decimal(str(unc))
            + Decimal(str(shape["cached"])) * Decimal(str(cch))
            + Decimal(str(shape["out"])) * Decimal(str(out))) / Decimal(1000000)


def build_offerings(workload, include_resellers, fid_hint=None):
    """Return ObservationCatalog-style offerings for a workload."""
    shapes = CHAT_SHAPE if workload == "agent-chat" else None
    obs = []
    models = set(GO_PRICES) | set(CP_PRICES if include_resellers else set())
    if include_resellers and fid_hint != "chat-metered":
        models |= set(PREMIUM_PRICES)
    # verified-AA only; everything else excluded-with-reason (see SRC)
    models &= {m for m in AA}
    if workload == "coding-session":
        # premium subs lack published coding request shapes -> excluded-with-reason there
        models = {m for m in models if (m in CODING_SHAPES or m in PREMIUM_PRICES) and AA[m]["aa"] is not None}
    else:
        models = {m for m in models if AA[m]["aa"] is not None}
    if fid_hint == "chat-metered":
        models |= {m for m in OR_PRICES if m in AA and AA[m]["aa"] is not None}
    for m in sorted(models):
        aa = AA[m]
        if workload == "coding-session":
            if m in CODING_SHAPES:
                wshape = {"in": CODING_SHAPES[m][0], "cached": CODING_SHAPES[m][1], "out": CODING_SHAPES[m][2]}
            else:
                # fall back to a generic coding request shape (Go's GLM-5.3 baseline)
                wshape = {"in": 700, "cached": 52000, "out": 150}
        else:
            wshape = shapes
        if fid_hint == "chat-metered":
            # metered frontier = OpenRouter catalog rates, subscription-free labeling
            if m not in OR_PRICES:
                continue  # excluded-with-reason: no verified OR price (e.g. glm-5.2)
            tiers = [("openrouter", OR_PRICES[m], "0")]
            # NOTE: local options (e.g. slotstream SSD-streaming) deliberately
            # excluded: their dominant cost is TIME (slow decode + prefill), which
            # neither axis captures — a $0 point on the cost axis would corrupt
            # the frontier. Local belongs here only once a time-cost metric exists.
        elif m in PREMIUM_PRICES:
            tiers = [(PREMIUM_VENDOR[m], PREMIUM_PRICES[m], SUB_CAP_MODEL)]
        elif m in GO_PRICES:
            tiers = [("opencode-go", GO_PRICES[m], GO_PRICES[m][3])]
        else:
            continue  # no verified price source
        if include_resellers and m in CP_PRICES:
            tiers.append(("clinepass", CP_PRICES[m], CP_PRICES[m][3]))
        for prov, prices, cap in tiers:
            cp = cost_per_turn(prices[:3], wshape)
            obs.append({
                "offering": {"offering_id": f"{prov}/{m}", "model_id": m, "provider": prov,
                             "endpoint": "openai-compatible", "region": "us",
                             "service_tier": "standard", "agent_harness": "brian-harness@1",
                             "capabilities": ["text", "tools", "structured_output"]},
                "metadata": {"aa_intelligence_index": aa["aa"], "monthly_cap_usd": cap,
                             "workload": workload, "cap_assumed": prov in ("clinepass", "chatgpt-plus", "claude-pro")},
                "default_source": SRC,
                "signals": {
                    "aa_intelligence_index": {"value": str(aa["aa"]), "unit": "index", "sample_count": 1, "observed_at": NOW},
                    "metered_usd_per_turn": {"value": str(cp), "unit": "USD", "observed_at": NOW},
                    "monthly_cap_usd": {"value": cap, "unit": "USD/month", "observed_at": NOW},
                },
            })
            # speed signals omitted when unverified -> responsiveness frontier
            # excludes these offerings with reason (never silently imputed)
            if aa.get("tps") is not None:
                obs[-1]["signals"]["aa_output_tokens_per_second"] = {"value": str(aa["tps"]), "unit": "tokens/second", "observed_at": NOW}
            if m in GPQA:
                obs[-1]["signals"]["gpqa_diamond"] = {"value": str(GPQA[m][0]), "unit": "percent", "observed_at": NOW}
                prov = "GPQA Diamond AA-run (independent)" if GPQA[m][1] == "AA-run" else f"GPQA Diamond {GPQA[m][1]}"
                obs[-1]["default_source"]["methodology"] += " | " + prov
            if m in SWE:
                obs[-1]["signals"]["swe_bench_verified"] = {"value": str(SWE[m][0]), "unit": "percent", "observed_at": NOW}
                prov = ("SWE-bench Verified via vals.ai (independent, Mini-SWE-agent harness)"
                        if SWE[m][1] == "vals.ai-independent" else
                        "SWE-bench Verified via openlm.ai aggregator (vendor-reported)")
                obs[-1]["default_source"]["methodology"] = obs[-1]["default_source"]["methodology"] + " | " + prov
            if aa.get("ttft") is not None:
                obs[-1]["signals"]["aa_ttft_seconds"] = {"value": str(aa["ttft"]), "unit": "seconds", "observed_at": NOW}
    return obs


FRONTIER = """# Multi-frontier config (generated): workloads x axes
schema_version: model-skyline/v1alpha1

workloads:
  {wl}:
    unit: successful_turn
    version: "2.0.0"
    harness: brian-harness@1
    cohort: {cohort}
    variables: {{}}
    assumptions:
      cache_mode: observed
      includes_failed_attempts: true
      pricing_mode: {pmode}
      fixture_data: real
      quality_source: artificial-analysis-index-v4.1.1

metrics:
  aa_intelligence_index:
    kind: signal
    signal: aa_intelligence_index
    unit: index
    requirements:
      max_age_hours: 8760
  aa_output_tokens_per_second:
    kind: signal
    signal: aa_output_tokens_per_second
    unit: tokens/second
    requirements:
      max_age_hours: 8760
  metered_usd_per_turn:
    kind: signal
    signal: metered_usd_per_turn
    unit: USD
    description: "{cost_desc}"
    requirements:
      max_age_hours: 8760
  swe_bench_verified:
    kind: signal
    signal: swe_bench_verified
    unit: percent
    description: "SWE-bench Verified resolution rate (vals.ai independent runs where available)"
    requirements:
      max_age_hours: 8760
  gpqa_diamond:
    kind: signal
    signal: gpqa_diamond
    unit: percent
    description: "GPQA Diamond score (AA-run independent where available)"
    requirements:
      max_age_hours: 8760

frontiers:
  {fid}-economics:
    workload: {wl}
    axes:
      - metric: metered_usd_per_turn
        goal: minimize
        epsilon_relative: 0.02
      - metric: aa_intelligence_index
        goal: maximize
        epsilon_absolute: 0.5
    order_by: metered_usd_per_turn
    uncertainty: point
    eligibility:
      required_capabilities: [tools, structured_output]
      allow_unknown_age: false
    metadata_fields: [aa_intelligence_index, monthly_cap_usd, workload, cap_assumed]

  {fid}-responsiveness:
    workload: {wl}
    axes:
      - metric: aa_output_tokens_per_second
        goal: maximize
      - metric: aa_intelligence_index
        goal: maximize
        epsilon_absolute: 0.5
    order_by: aa_output_tokens_per_second
    uncertainty: point
    eligibility:
      required_capabilities: [tools, structured_output]
      allow_unknown_age: false
    metadata_fields: [aa_intelligence_index, monthly_cap_usd, workload, cap_assumed]

{coding_frontier}
selections:
{coding_sel}  {fid}-econ-sel:
  {fid}-econ-sel:
    frontier: {fid}-economics
    strategy: lexicographic
    count: 2
    order_by: aa_intelligence_index
    max_per_provider: 1
    snapshot_ttl_seconds: 3600
    on_insufficient: return_available
  {fid}-econ-bulk:
    frontier: {fid}-economics
    strategy: lexicographic
    count: 2
    order_by: metered_usd_per_turn
    max_per_provider: 1
    snapshot_ttl_seconds: 3600
    on_insufficient: return_available
  {fid}-resp-sel:
    frontier: {fid}-responsiveness
    strategy: lexicographic
    count: 2
    order_by: aa_intelligence_index
    max_per_provider: 1
    snapshot_ttl_seconds: 3600
    on_insufficient: return_available
"""


def run(cmd):
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=90)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError((r.stderr or r.stdout or "empty output")[:400])
    return json.loads(r.stdout)


def pick(data, key):
    return data.get("data", data).get(key, {})


def oid(x):
    return (x.get("offering") or {}).get("offering_id", "?")


def rows_from_evaluated(evaluated, member_ids):
    def key(x):
        return (0 if oid(x) in member_ids else 1,
                float(x["axes"][list(x["axes"])[0]]["value"]))
    out = []
    for x in sorted(evaluated, key=key):
        axes = x["axes"]
        name = list(axes)[0]
        v = float(Decimal(axes[name]["value"]))
        second = list(axes)[1] if len(axes) > 1 else None
        out.append({"offering": oid(x), "on_frontier": oid(x) in member_ids,
                    "cap_assumed": bool((x.get("metadata") or {}).get("cap_assumed", False)),
                    "primary": round(v, 6) if v < 1 else round(v, 1),
                    "secondary_label": second,
                    "secondary": (round(float(Decimal(axes[second]["value"])), 1) if second else None),
                    "aa_index": (axes["aa_intelligence_index"]["value"] if "aa_intelligence_index" in axes else None),
                    "dominated_by": sorted({d.split("/", 1)[-1] for d in (x.get("dominated_by") or [])})})
    return out


def frontier_block(wl, fid, pmode, cohort, include_resellers):
    obs = build_offerings(wl, include_resellers, fid_hint=fid)
    d = REPO / f"data/real/subscription-relative/gen-{wl}-{fid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "observations.json").write_text(json.dumps({
        "schema_version": "model-skyline/v1alpha1",
        "workload": {"id": wl, "version": "2.0.0", "unit": "successful_turn"},
        "offerings": obs}, indent=1))
    coding_frontier = ""
    coding_sel = ""
    if wl == "coding-session":
        coding_frontier = """
  coding-smarts:
    workload: coding-session
    axes:
      - metric: metered_usd_per_turn
        goal: minimize
        epsilon_relative: 0.02
      - metric: swe_bench_verified
        goal: maximize
        epsilon_absolute: 0.5
    order_by: metered_usd_per_turn
    uncertainty: point
    eligibility:
      required_capabilities: [tools, structured_output]
      allow_unknown_age: false
    metadata_fields: [aa_intelligence_index, monthly_cap_usd, workload, cap_assumed]"""
        coding_sel = """  coding-smarts-sel:
    frontier: coding-smarts
    strategy: lexicographic
    count: 2
    order_by: swe_bench_verified
    max_per_provider: 1
    snapshot_ttl_seconds: 3600
    on_insufficient: return_available
"""
    else:
        coding_frontier = ""
        coding_sel = ""
    if wl in ("agent-chat", "coding-session"):
        wlid = "chat" if wl == "agent-chat" else "coding"
        math_name = f"math-smarts-{fid}"
        math_sel_name = f"math-smarts-{fid}-sel"
        coding_frontier = f"""
  {math_name}:
    workload: {wl}
    axes:
      - metric: metered_usd_per_turn
        goal: minimize
        epsilon_relative: 0.02
      - metric: gpqa_diamond
        goal: maximize
        epsilon_absolute: 0.5
    order_by: metered_usd_per_turn
    uncertainty: point
    eligibility:
      required_capabilities: [tools, structured_output]
      allow_unknown_age: false
    metadata_fields: [aa_intelligence_index, workload, cap_assumed]"""
        coding_sel = f"""
  {math_sel_name}:
    frontier: {math_name}
    strategy: lexicographic
    count: 2
    order_by: gpqa_diamond
    max_per_provider: 2
    snapshot_ttl_seconds: 3600
    on_insufficient: return_available
"""
    (d / "frontier.yaml").write_text(FRONTIER.format(
        wl=wl, fid=fid, pmode=pmode, cohort=cohort, coding_frontier=coding_frontier, coding_sel=coding_sel,
        cost_desc=("Metered-equivalent USD per successful turn. For subscription offerings "
                   "this is the cap-share denominator: divide by the offering's monthly cap "
                   "to get fraction-of-cap burned (lower = more turns per subscription).")))
    blocks = []
    for suffix, sel_id in (("economics", f"{fid}-econ-sel"), ("responsiveness", f"{fid}-resp-sel")):
        ev = run([str(REPO / ".venv/bin/modelskyline"), "evaluate",
                  f"data/real/subscription-relative/gen-{wl}-{fid}/frontier.yaml",
                  f"data/real/subscription-relative/gen-{wl}-{fid}/observations.json",
                  f"{fid}-{suffix}", "--format", "json"])
        try:
            selpath = REPO / f"data/real/subscription-relative/gen-{wl}-{fid}/selection-{suffix}.json"
            sr = subprocess.run([str(REPO / ".venv/bin/modelskyline"), "select",
                                 f"data/real/subscription-relative/gen-{wl}-{fid}/frontier.yaml",
                                 f"data/real/subscription-relative/gen-{wl}-{fid}/observations.json",
                                 sel_id, "--output", str(selpath)],
                                cwd=REPO, capture_output=True, text=True, timeout=90)
            if sr.returncode != 0 or not selpath.exists():
                raise RuntimeError((sr.stderr or "select failed")[:200])
            sdata = json.loads(selpath.read_text())
            sdata = sdata.get("data", sdata)
            default, fallbacks = oid(sdata.get("default", {})), [oid(f) for f in sdata.get("fallbacks", [])]
        except RuntimeError as e:
            default, fallbacks = None, []
            sel_note = f"selection error: {str(e)[:120]}"
        else:
            sel_note = None
        bulk_default = None
        if suffix == "economics":
            try:
                bpath = REPO / f"data/real/subscription-relative/gen-{wl}-{fid}/selection-econ-bulk.json"
                subprocess.run([str(REPO / ".venv/bin/modelskyline"), "select",
                                f"data/real/subscription-relative/gen-{wl}-{fid}/frontier.yaml",
                                f"data/real/subscription-relative/gen-{wl}-{fid}/observations.json",
                                f"{fid}-econ-bulk", "--output", str(bpath)],
                               cwd=REPO, capture_output=True, text=True, timeout=90)
                if bpath.exists():
                    bdata = json.loads(bpath.read_text())
                    bdata = bdata.get("data", bdata)
                    bulk_default = oid(bdata.get("default", {}))
            except Exception:
                bulk_default = None
        edata = ev.get("data", ev)
        member_ids = {oid(m) for m in edata.get("members", [])}
        wlid = "chat" if wl == "agent-chat" else "coding"
        math_name = f"math-smarts-{fid}"
        math_sel_name = f"math-smarts-{fid}-sel"
        if wl == "agent-chat" and suffix == "responsiveness":
            try:
                ms = run([str(REPO / ".venv/bin/modelskyline"), "evaluate",
                          f"data/real/subscription-relative/gen-{wl}-{fid}/frontier.yaml",
                          f"data/real/subscription-relative/gen-{wl}-{fid}/observations.json",
                          math_name, "--format", "json"])
                mdata = ms.get("data", ms)
                mmembers = {oid(m) for m in mdata.get("members", [])}
                mrows = rows_from_evaluated(mdata.get("evaluated", []), mmembers)
                msel = REPO / f"data/real/subscription-relative/gen-{wl}-{fid}/selection-math-smarts.json"
                subprocess.run([str(REPO / ".venv/bin/modelskyline"), "select",
                                f"data/real/subscription-relative/gen-{wl}-{fid}/frontier.yaml",
                                f"data/real/subscription-relative/gen-{wl}-{fid}/observations.json",
                                math_sel_name, "--output", str(msel)],
                               cwd=REPO, capture_output=True, text=True, timeout=90)
                msd = json.loads(msel.read_text()); msd = msd.get("data", msd)
                blocks.append({
                    "id": f"math-smarts-{fid}", "workload": wl,
                    "axes": ["usd/turn × GPQA-Diamond"],
                    "primary_label": "usd/turn",
                    "default": oid(msd.get("default", {})), "bulk_default": None,
                    "fallbacks": [oid(f) for f in msd.get("fallbacks", [])],
                    "selection_note": None,
                    "ranked": mrows,
                    "members": [r for r in mrows if r["on_frontier"]],
                })
            except Exception as e:
                print("math-smarts failed:", str(e)[:150])
        if wl == "coding-session" and suffix == "responsiveness":
            try:
                cs = run([str(REPO / ".venv/bin/modelskyline"), "evaluate",
                          f"data/real/subscription-relative/gen-{wl}-{fid}/frontier.yaml",
                          f"data/real/subscription-relative/gen-{wl}-{fid}/observations.json",
                          math_name, "--format", "json"])
                cdata = cs.get("data", cs)
                cmembers = {oid(m) for m in cdata.get("members", [])}
                crows = rows_from_evaluated(cdata.get("evaluated", []), cmembers)
                csel = REPO / f"data/real/subscription-relative/gen-{wl}-{fid}/selection-{math_name}.json"
                subprocess.run([str(REPO / ".venv/bin/modelskyline"), "select",
                                f"data/real/subscription-relative/gen-{wl}-{fid}/frontier.yaml",
                                f"data/real/subscription-relative/gen-{wl}-{fid}/observations.json",
                                math_sel_name, "--output", str(csel)],
                               cwd=REPO, capture_output=True, text=True, timeout=90)
                csd = json.loads(csel.read_text()); csd = csd.get("data", csd)
                blocks.append({
                    "id": f"math-smarts-{fid}", "workload": wl,
                    "axes": ["usd/turn × GPQA-Diamond"],
                    "primary_label": "usd/turn",
                    "default": oid(csd.get("default", {})), "bulk_default": None,
                    "fallbacks": [oid(f) for f in csd.get("fallbacks", [])],
                    "selection_note": None,
                    "ranked": crows,
                    "members": [r for r in crows if r["on_frontier"]],
                })
            except Exception as e:
                print("coding-smarts failed:", str(e)[:150])
        blocks.append({
            "id": f"{fid}-{suffix}",
            "workload": wl,
            "axes": ["usd/turn × intelligence" if suffix == "economics" else "tok/s × intelligence"],
            "primary_label": "USD/turn" if suffix == "economics" else "out tok/s",
            "default": default,
            "bulk_default": bulk_default,
            "fallbacks": fallbacks,
            "selection_note": sel_note,
            "ranked": rows_from_evaluated(edata.get("evaluated", []), member_ids),
        })
        blocks[-1]["members"] = [r for r in blocks[-1]["ranked"] if r["on_frontier"]]
    return blocks


frontiers = []
frontiers += frontier_block("agent-chat", "chat-subscription", "subscription_relative_cap_share",
                            "openclaw-30day-shape", include_resellers=True)
frontiers += frontier_block("agent-chat", "chat-metered", "current_catalog_price_counterfactual",
                            "openclaw-30day-shape", include_resellers=False)
frontiers += frontier_block("coding-session", "coding-subscription", "subscription_relative_cap_share",
                            "opencode-go-published-shapes", include_resellers=True)

artifact = {
    "generated_at": NOW,
    "schema": "brian-multi-frontier-v2",
    "note": SRC["methodology"],
    "frontiers": frontiers,
}
# crossover: turns/month where metered spend equals a $10 subscription
crossover = {}
for f in frontiers:
    if f["id"] == "chat-metered-economics":
        for r in f["ranked"]:
            if r["primary"] and r["primary"] > 0:
                crossover[r["offering"]] = {
                    "usd_per_turn": r["primary"],
                    "turns_per_10usd": int(10 / r["primary"]),
                    "aa_index": r["aa_index"],
                }
artifact["crossover_vs_10usd_sub"] = crossover
# flagship $20-tier subs: tasks/month where the sub beats metered
# (AA cost-per-task: Sol $1.04 at max effort; Fable derived ~3.1x Sol per AA's "1/3 the cost")
artifact["flagship_subs_20usd"] = {
    "chatgpt-plus/gpt-5.6-sol": {"modeled_cap_api_usd": 160, "aa_index": 61,
        "note": "Plus includes Sol (10-100 msgs/5h + unpublished weekly caps). Cap modeled 8x price (community: 'many times purchase price'); UNVERIFIED. Sub beats metered Sol above ~2,300 usd/turn-normalized turns/mo at modeled cap."},
    "claude-pro/opus-5": {"modeled_cap_api_usd": 160, "aa_index": 63,
        "note": "Pro flagship is Opus 5 (aa 63, current quality ceiling). Fable 5 NOT in Pro since 2026-07-20 (metered credits $10/$50). Cap modeled 8x price; UNVERIFIED."},
}
(BASE / "summary-v2.json").write_text(json.dumps(artifact, indent=1))
print(f"frontiers: {len(frontiers)}")
for f in frontiers:
    print(f"  {f['id']:28s} default={str(f['default']):34s} ranked={len(f['ranked'])}")
