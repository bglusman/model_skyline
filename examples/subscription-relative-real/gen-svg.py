#!/usr/bin/env python3
"""Render each frontier in summary-v2.json as an SVG scatter + pareto polyline.

Data-driven: reads the artifact, normalizes axes (log-ish compression for cost,
linear for index/percent/speed), draws dominant points + step-line over dominated
ones. Output: data/real/subscription-relative/svg/*.svg + manifest for glance.
"""
import json
import math
from pathlib import Path

BASE = Path("/root/.openclaw/workspace/model-skyline-external/data/real/subscription-relative")
OUT = BASE / "svg"
OUT.mkdir(exist_ok=True)

art = json.loads((BASE / "summary-v2.json").read_text())

W, H = 640, 320
M = {"l": 56, "r": 16, "t": 30, "b": 40}
IW, IH = W - M["l"] - M["r"], H - M["t"] - M["b"]


def is_cost_axis(frontier_id, primary_label):
    return "usd" in primary_label


def xform_x(val, lo, hi, log_cost):
    if log_cost:
        a, b = math.log10(max(val, 1e-6)), math.log10(max(hi, 1e-5))
        return M["l"] + (a - math.log10(max(lo, 1e-6))) / (b - math.log10(max(lo, 1e-6))) * IW
    return M["l"] + (val - lo) / (hi - lo) * IW


def xform_y(val, lo, hi):
    return M["t"] + (1 - (val - lo) / (hi - lo)) * IH


def svg_for(f):
    rows = f["ranked"]
    if not rows:
        return None, None
    pl = f["primary_label"]
    cost = is_cost_axis(f["id"], pl)
    xs = [r["primary"] for r in rows if r["primary"] is not None]
    ys = [float(r["aa_index"] or r["secondary"] or 0) for r in rows if r["primary"] is not None]
    lo_x, hi_x = min(xs), max(xs)
    lo_y, hi_y = min(ys) - 2, max(ys) + 2
    span = hi_x - lo_x
    log_cost = cost and (span / max(lo_x, 1e-6) > 4)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}" font-family="sans-serif" font-size="11">']
    parts.append(f'<text x="{M["l"]}" y="16" font-size="13" font-weight="bold">{f["id"]}</text>')
    parts.append(f'<text x="{M["l"]}" y="26" font-size="9" opacity="0.7">{f["workload"]} · {f["axes"][0]}</text')

    # gridlines
    for i in range(5):
        y = M["t"] + i * IH / 4
        parts.append(f'<line x1="{M["l"]}" y1="{y:.0f}" x2="{W-M["r"]}" y2="{y:.0f}" stroke="#666" stroke-opacity="0.25"/>')

    # frontier step-line over members
    members = sorted([r for r in rows if r["on_frontier"]], key=lambda r: r["primary"] or 0)
    if len(members) >= 2:
        pts = []
        for r in members:
            x = xform_x(r["primary"], lo_x, hi_x, log_cost)
            y = xform_y(float(r["aa_index"] or r["secondary"] or 0), lo_y, hi_y)
            pts.append((x, y))
        path = " ".join(f"{x:.0f},{y:.0f}" for x, y in pts)
        parts.append(f'<polyline points="{path}" fill="none" stroke="#8f7" stroke-width="1.5" stroke-dasharray="4 3"/>')

    for r in rows:
        if r["primary"] is None:
            continue
        yv = float(r["aa_index"] or r["secondary"] or 0)
        x = xform_x(r["primary"], lo_x, hi_x, log_cost)
        y = xform_y(yv, lo_y, hi_y)
        color = "#6c6" if r["on_frontier"] else "#bbb"
        rad = 4 if r["on_frontier"] else 3
        op = "1.0" if r["on_frontier"] else "0.45"
        parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{rad}" fill="{color}" fill-opacity="{op}"/>')
        label = r["offering"].split("/")[-1]
        parts.append(f'<text x="{x+7:.0f}" y="{y+3:.0f}" font-size="8.5" opacity="{op}">{label[:22]}</text>')
        ax = ("$%.4g" % r["primary"]) if cost else ("%.0f" % r["primary"])
        parts.append(f'<text x="{x-2:.0f}" y="{y-6:.0f}" font-size="8" opacity="{op}" text-anchor="end">{ax}</text>')
        mark = "†" if r.get("cap_assumed") else ""
        if mark:
            parts.append(f'<text x="{x+7:.0f}" y="{y+12:.0f}" font-size="8.5" fill="#d84">{mark} assumed cap</text>')
    parts.append(f'<text x="{M["l"]}" y="{H-8}" font-size="9" opacity="0.7">x: {"log" if log_cost else "linear"} {"USD/turn" if cost else pl} · y: intelligence (aa/swe/gpqa) · ★ green = pareto</text>')
    parts.append("</svg>")
    return "\n".join(parts), len(rows)


manifest = []
for f in art["frontiers"]:
    svg, n = svg_for(f)
    if svg:
        fn = f"{f['id']}.svg"
        (OUT / fn).write_text(svg)
        manifest.append({"id": f["id"], "svg": f"/svg/{fn}", "workload": f["workload"], "points": n})
        print(f"  {fn}  ({n} points)")

# svg manifest into summary-v2 so glance/other consumers can find them
art["svg"] = manifest
(BASE / "summary-v2.json").write_text(json.dumps(artifact_src := art, indent=1))
print(f"{len(manifest)} SVGs in {OUT}")
