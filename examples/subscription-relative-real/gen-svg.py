#!/usr/bin/env python3
"""Tufte-style frontier render, v3: model-centric tables + readable small multiples.

Principles applied:
- data-ink: no chart frames, minimal grid; every pixel carries information
- label EVERYTHING with its value directly (no legend-hunting); no "0" garbage
- model names as full stable identifiers (vendor/model), not fragments
- the ranked TABLE is the primary interface (sorted, value-per-row, frontier
  status + near-frontier margin + cross-frontier presence per model)
- plots are secondary small multiples with real axes and human-readable ticks
"""
import json
import math
from pathlib import Path

BASE = Path("/root/.openclaw/workspace/model-skyline-external/data/real/subscription-relative")
OUT = BASE / "svg"
OUT.mkdir(exist_ok=True)

art = json.loads((BASE / "summary-v2.json").read_text())
model_map = art.get("model_map", {})

W, H = 760, 300
M = {"l": 74, "r": 170, "t": 34, "b": 34}
IW, IH = W - M["l"] - M["r"], H - M["t"] - M["b"]

AXIS_LABEL = {
    "chat-subscription-economics": ("cost: % of monthly sub-cap per turn", "intelligence: AA index (0-100)"),
    "chat-metered-economics": ("cost: metered USD per turn (log)", "intelligence: AA index (0-100)"),
    "chat-subscription-responsiveness": ("speed: output tok/s", "intelligence: AA index (0-100)"),
    "chat-metered-responsiveness": ("speed: output tok/s", "intelligence: AA index (0-100)"),
    "coding-subscription-economics": ("cost: % of monthly sub-cap per coding turn", "intelligence: AA index (0-100)"),
    "coding-subscription-responsiveness": ("speed: output tok/s (coding shape)", "intelligence: AA index (0-100)"),
    "math-smarts-chat-subscription": ("cost: % of monthly sub-cap per turn", "math: GPQA Diamond %"),
    "math-smarts-chat-metered": ("cost: metered USD per turn (log)", "math: GPQA Diamond %"),
    "math-smarts-coding-subscription": ("cost: % of monthly sub-cap per coding turn", "math: GPQA Diamond %"),
    "math-smarts-coding": ("cost: metered USD per turn (log)", "math: GPQA Diamond %"),
}
SHORT = {
    "chat-subscription-economics": "Subscriptions · cost↔smarts",
    "chat-metered-economics": "Metered · cost↔smarts",
    "chat-subscription-responsiveness": "Subscriptions · speed↔smarts",
    "chat-metered-responsiveness": "Metered · speed↔smarts",
    "coding-subscription-economics": "Subscriptions · coding cost↔smarts",
    "coding-subscription-responsiveness": "Subscriptions · coding speed↔smarts",
    "math-smarts-chat-subscription": "Subscriptions · math smarts↔cost",
    "math-smarts-chat-metered": "Metered · math smarts↔cost",
    "math-smarts-coding-subscription": "Subscriptions · coding math↔cost",
    "math-smarts-coding": "Metered · coding math↔cost",
}


def nice_ticks(lo, hi, n=4):
    step = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(step)) if step > 0 else 1
    for mult in (1, 2, 2.5, 5, 10):
        if mult * mag >= step:
            step = mult * mag
            break
    t0 = math.ceil(lo / step) * step
    return [t0 + i * step for i in range(n + 1) if t0 + i * step <= hi + step * 0.01]


def fmt_cost(v):
    if v is None:
        return "—"
    if v < 0.001:
        return f"${v*100:.3f}¢" if v < 0.01 else f"${v:.4f}"
    if v < 1:
        return f"${v:.4f}"
    return f"${v:,.2f}"


def fmt_prim(fid, v):
    if v is None:
        return "—"
    if "economics" in fid:
        return fmt_cost(v)
    if "responsiveness" in fid:
        return f"{v:.0f} tok/s"
    return fmt_cost(v)


def model_display(offering_id):
    vendor, _, name = offering_id.partition("/")
    pretty = {"opencode-go": "Go", "clinepass": "Cline†", "openrouter": "OR-metered",
              "chatgpt-plus": "GPT+†", "claude-pro": "ClaudePro†"}.get(vendor, vendor)
    return f"{pretty}/{name}"


def chart(f):
    rows = sorted([r for r in f["ranked"] if r.get("primary") is not None], key=lambda r: r["primary"])
    if not rows:
        return None
    fid = f["id"]
    cost = "economics" in fid or "math-smarts" in fid
    xs = [r["primary"] for r in rows]
    ys = [float(r["aa_index"] or r["secondary"] or 0) for r in rows]
    lo_x, hi_x = min(xs), max(xs)
    lo_y, hi_y = min(min(ys) - 3, 0), max(ys) + 2
    log_x = cost and (hi_x / max(lo_x, 1e-9) > 50)

    def X(v):
        if log_x:
            a, b = math.log10(max(v, 1e-9)), math.log10(hi_x)
            la = math.log10(max(lo_x, 1e-9))
            return M["l"] + (a - la) / (b - la) * IW if b > la else M["l"]
        return M["l"] + (v - lo_x) / max(hi_x - lo_x, 1e-9) * IW

    def Y(v):
        return M["t"] + (1 - (v - lo_y) / (hi_y - lo_y)) * IH

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'role="img" aria-label="{SHORT.get(fid, fid)}" font-family="system-ui, sans-serif">']
    p.append(f'<text x="{M["l"]}" y="14" font-size="12.5" font-weight="600">{SHORT.get(fid, fid)}</text>')
    p.append(f'<text x="{M["l"]}" y="26" font-size="9.5" fill="#777">{AXIS_LABEL.get(fid, ("", ""))[0]}  ↔  {AXIS_LABEL.get(fid, ("", ""))[1]}</text>')

    # y ticks (Tufte: tiny ticks, no frame)
    for t in nice_ticks(lo_y, hi_y):
        y = Y(t)
        p.append(f'<line x1="{M["l"]}" y1="{y:.0f}" x2="{W-M["r"]}" y2="{y:.0f}" stroke="#888" stroke-opacity="0.14"/>')
        p.append(f'<text x="{M["l"]-6}" y="{y+3:.0f}" font-size="8.5" fill="#666" text-anchor="end">{t:.0f}</text>')

    # x ticks
    if log_x:
        decades = [10**i for i in range(math.floor(math.log10(max(lo_x, 1e-9))) - 1, math.ceil(math.log10(hi_x)) + 1)]
        for t in decades:
            if t < lo_x / 3 or t > hi_x * 3:
                continue
            x = X(t)
            p.append(f'<line x1="{x:.0f}" y1="{M["t"]+IH}" x2="{x:.0f}" y2="{M["t"]+IH+4}" stroke="#888" stroke-opacity="0.5"/>')
            p.append(f'<text x="{x:.0f}" y="{M["t"]+IH+14}" font-size="8.5" fill="#666" text-anchor="middle">'
                     + (f"${t:g}" if t >= 0.01 else f"{t*100:.1f}¢") + "</text>")
    else:
        for t in nice_ticks(lo_x, hi_x):
            x = X(t)
            p.append(f'<line x1="{x:.0f}" y1="{M["t"]+IH}" x2="{x:.0f}" y2="{M["t"]+IH+4}" stroke="#888" stroke-opacity="0.5"/>')
            p.append(f'<text x="{x:.0f}" y="{M["t"]+IH+14}" font-size="8.5" fill="#666" text-anchor="middle">'
                     + (f"{t*100:.2f}%cap" if "economics" in fid and fid.startswith(("chat-sub", "coding-sub")) else f"{t:g}") + "</text>")

    # pareto step line
    members = [r for r in rows if r["on_frontier"]]
    if len(members) >= 2:
        pts = [(X(r["primary"]), Y(float(r["aa_index"] or r["secondary"] or 0))) for r in members]
        path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        p.append(f'<path d="{path}" fill="none" stroke="#3a8f3a" stroke-width="1.4" stroke-dasharray="5 3"/>')

    for r in rows:
        x, y = X(r["primary"]), Y(float(r["aa_index"] or r["secondary"] or 0))
        on = r["on_frontier"]
        near = r.get("dominated_by") and not on
        col = "#2e7d32" if on else ("#c9a227" if near else "#b9b9b9")
        p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{5 if on else 3.5}" fill="{col}" '
                 f'fill-opacity="{1.0 if on else (0.75 if near else 0.35)}"/>')
        # right-side label column: every model, full name + value, colored by status
        ly = M["t"] + 12 + rows.index(r) * 13.5
        p.append(f'<circle cx="{W-M["r"]+4}" cy="{ly-3}" r="3" fill="{col}" fill-opacity="{1.0 if on else (0.75 if near else 0.35)}"/>')
        p.append(f'<text x="{W-M["r"]+12}" y="{ly}" font-size="9.5" fill="{col if on else "#555"}" '
                 f'font-weight="{600 if on else 400}">{model_display(r["offering"])}</text>')
        val = fmt_prim(fid, r["primary"])
        p.append(f'<text x="{W-8}" y="{ly}" font-size="9.5" text-anchor="end" fill="{col if on else "#555"}">{val}</text>')
        # on-point annotation: value + quality at the dot
        p.append(f'<text x="{x-7:.0f}" y="{y-7:.0f}" font-size="8.5" text-anchor="end" '
                 f'fill="{col}">{val}</text>' if on else "")
    p.append(f'<text x="{M["l"]}" y="{H-6}" font-size="8.5" fill="#777">'
             f'● frontier · ● amber = within 10% of frontier · ● gray = dominated · † = assumed cap</text>')
    p.append("</svg>")
    return "\n".join(p)


out, manifest = [], []
for f in art["frontiers"]:
    s = chart(f)
    if s:
        fn = f"{f['id']}.svg"
        (OUT / fn).write_text(s)
        manifest.append({"id": f["id"], "svg": f"/{fn}", "workload": f["workload"],
                         "title": SHORT.get(f["id"], f["id"]), "points": len(f["ranked"])})

art["svg"] = manifest
(BASE / "summary-v2.json").write_text(json.dumps(art, indent=1))
print(f"{len(manifest)} v3 charts -> {OUT}")
for m in manifest:
    print(f"  {m['id']}")
