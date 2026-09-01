#!/usr/bin/env python3
"""v4 publisher: model-centric landing page + interactive Plotly charts.

Design (responding to Brian's UX critique):
- PRIMARY interface: one model table. Rows = models (deduped across purchase
  paths), columns = per-frontier status chips (★ on / ≈near +% off / ·),
  linked dots<->rows via shared hover highlighting (JS).
- Charts: Plotly scatter+step, full interactive (hover tooltips, zoom, pan),
  mobile-responsive (config responsive: true, autosize). Replaces static SVG.
- No external CDN dependency risk beyond Plotly itself (pinned, fetched once).
"""
import json
import subprocess
import sys
import shutil
from pathlib import Path

REPO = "/tmp/msk-publish"
SRC = Path("/root/.openclaw/workspace/model-skyline-external/data/real/subscription-relative")
DEST = Path(REPO) / "research" / "snapshots"


def sh(*cmd, cwd=REPO):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(cmd[:4])}\n{(r.stderr or r.stdout)[:400]}")


sh("git", "fetch", "origin")
sh("git", "checkout", "gh-pages")
sh("git", "reset", "--hard", "origin/gh-pages")

DEST.mkdir(parents=True, exist_ok=True)
shutil.copy(SRC / "summary-v2.json", DEST / "summary-v2.json")
art = json.loads((SRC / "summary-v2.json").read_text())

# ---------- model table ----------
FR_ORDER = [f["id"] for f in art["frontiers"]]
FR_SHORT = {
    "chat-subscription-economics": "sub·cost", "chat-metered-economics": "metered·cost",
    "chat-subscription-responsiveness": "sub·speed", "chat-metered-responsiveness": "metered·speed",
    "coding-subscription-economics": "sub·code-cost", "coding-subscription-responsiveness": "sub·code-speed",
    "coding-smarts": "SWE smarts", "math-smarts-chat-subscription": "sub·math",
    "math-smarts-chat-metered": "metered·math", "math-smarts-coding-subscription": "sub·code-math",
    "math-smarts-coding": "metered·code-math",
}
rows_html = []
model_names = sorted(art.get("model_map", {}).items(),
                     key=lambda kv: -(len(kv[1].get("on", [])) * 2 + len(kv[1].get("near", []))))
for name, mm in model_names:
    on = set(mm.get("on", []))
    near = {n.split(" (+")[0] for n in mm.get("near", [])}
    chips = []
    for fid in FR_ORDER:
        cls, mark = "", "·"
        if fid in on:
            cls, mark = 'style="color:#2e7d32;font-weight:600"', "★"
        elif fid in near:
            cls, mark = 'style="color:#c9a227"', "≈"
        chips.append(f'<td class="chip" {cls} title="{FR_SHORT[fid]}">{mark}</td>')
    best = mm.get("best") or ""
    price = mm.get("price")
    price_s = f"${price:.4f}" if price and price < 1 else (f"${price:,.2f}" if price else "")
    rows_html.append(
        f'<tr class="mrow" data-model="{name}"><td class="mname">{name}</td>'
        f'<td class="mval">{best}</td><td class="mval">{price_s}</td>{"".join(chips)}</tr>')

head = "".join(f"<th>{FR_SHORT[fid]}</th>" for fid in FR_ORDER)
table = f"""
<section id="model-table">
<h2>Models across all frontiers</h2>
<p class="hint">★ = on frontier · ≈ = within 10% of frontier · hover a row to
highlight that model in every chart; hover a chart point to highlight its row.</p>
<div style="overflow-x:auto"><table id="mtable">
<thead><tr><th>model</th><th>AA</th><th>best $/turn</th>{head}</tr></thead>
<tbody>{''.join(rows_html)}</tbody></table></div>
</section>"""

# ---------- charts data ----------
charts_js = []
for m in art.get("svg", []):
    f = next(x for x in art["frontiers"] if x["id"] == m["id"])
    pts = []
    for r in f["ranked"]:
        if r.get("primary") is None:
            continue
        pts.append({
            "model": r["offering"], "x": r["primary"],
            "y": float(r["aa_index"] or r["secondary"] or 0),
            "on": r["on_frontier"], "cap_assumed": bool(r.get("cap_assumed")),
            "vendor": r["offering"].split("/")[0],
            "label": f"{r['offering']}<br>{m['id']}",
        })
    charts_js.append({
        "id": m["id"], "title": m.get("title") or m["id"],
        "xlabel": f["axes"][0].split("×")[0].strip(),
        "ylabel": f["axes"][0].split("×")[-1].strip(),
        "logx": "economics" in m["id"] or "smarts" in m["id"],
        "points": pts,
    })

html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ModelSkyline frontier snapshots</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0 auto; max-width: 60rem; padding: 1rem; line-height: 1.45;
         font-family: ui-sans-serif, system-ui, sans-serif; }}
  section {{ margin: 1.4rem 0; }}
  .chart {{ width: 100%; height: 380px; }}
  table#mtable {{ border-collapse: collapse; font-size: .85rem; min-width: 640px; }}
  #mtable th, #mtable td {{ padding: .25rem .45rem; text-align: center; }}
  #mtable th:first-child, #mtable td.mname {{ text-align: left; }}
  #mtable thead th {{ border-bottom: 2px solid #999; }}
  .mrow:hover, .mrow.hl {{ background: #e8f2e8; }}
  .chip {{ width: 2.2rem; }}
  .hint {{ color: #666; font-size: .8rem; }}
  .chartdiv {{ height: 380px; }}
</style></head><body>
<h1>Frontier snapshots</h1>
<p>Generated {art.get('generated_at','n/a')} · <a href="summary-v2.json">raw data</a></p>
{table}
<section id="charts"><h2>Charts</h2>
<p class="hint">Interactive: hover for exact values, zoom/pan by drag. † = assumed cap.</p>
</section>
<p><small>ClinePass cap assumed $35; $20-tier sub caps modeled 8× price — †-flagged.
Sources: AA Intelligence Index v4.1.1 (current scale), vals.ai SWE-bench Verified (independent
same-harness), AA-run GPQA Diamond, OpenRouter catalog, published subscription tables.</small></p>
<script>
const CHARTS = {json.dumps(charts_js, separators=(',', ':'))};
const layoutBase = {{
  margin: {{l: 64, r: 24, t: 36, b: 52}}, hovermode: 'closest',
  font: {{size: 11}}, showlegend: false, autosize: true,
}};
function xType(c) {{ return c.logx ? 'log' : 'linear'; }}
function renderAll() {{
  const wrap = document.getElementById('charts');
  CHARTS.forEach((c, i) => {{
    const div = document.createElement('div');
    div.className = 'chartdiv'; div.id = 'chart-' + i;
    wrap.appendChild(div);
    const frontier = c.points.filter(p => p.on);
    frontier.sort((a, b) => a.x - b.x);
    const dom = c.points.filter(p => !p.on);
    const traces = [];
    traces.push({{x: frontier.map(p=>p.x), y: frontier.map(p=>p.y), mode: 'lines+markers',
      line: {{shape: 'linear', dash: 'dash', color: '#2e7d32'}},
      marker: {{size: 9, color: '#2e7d32'}},
      text: frontier.map(p=>p.label), name: 'frontier', customdata: frontier.map(p=>p.model),
    }});
    if (dom.length) traces.push({{x: dom.map(p=>p.x), y: dom.map(p=>p.y), mode: 'markers',
      marker: {{size: 7, color: '#b9b9b9'}}, text: dom.map(p=>p.label), name: 'dominated',
      customdata: dom.map(p=>p.model),}});
    const near = dom.filter(p => p.cap_assumed);
    const layout = JSON.parse(JSON.stringify(layoutBase));
    layout.title = c.title; layout.xaxis = {{title: c.xlabel, type: xType(c)}};
    layout.yaxis = {{title: c.ylabel}};
    Plotly.newPlot(div.id, traces, layout, {{responsive: true}}).then(gd => {{
      gd.on('plotly_hover', ev => {{
        const m = (ev.points[0].customdata || '').split('/').pop();
        document.querySelectorAll('.mrow').forEach(r =>
          r.classList.toggle('hl', r.dataset.model === m));
      }});
      gd.on('plotly_unhover', () => document.querySelectorAll('.mrow.hl').forEach(r => r.classList.remove('hl')));
    }});
  }});
}}
// table row hover -> highlight matching points in all charts
document.querySelectorAll('.mrow').forEach(r => {{
  r.addEventListener('mouseenter', () => {{
    const m = r.dataset.model;
    CHARTS.forEach((c, i) => {{
      const idx = c.points.map(p => p.model.endsWith('/' + m) ? 1 : 0);
      Plotly.restyle('chart-' + i, {{'marker.opacity': [idx.map(v => v ? 1 : 0.15),
        'marker.size': [idx.map(v => v ? 12 : 7)]}});
    }});
  }});
  r.addEventListener('mouseleave', () => CHARTS.forEach((c, i) =>
    Plotly.restyle('chart-' + i, {{'marker.opacity': 1, 'marker.size': 7}})));
}});
renderAll();
</script>
</body></html>"""
(DEST / "index.html").write_text(html)

sh("git", "add", "research/snapshots")
st = subprocess.run(["git", "status", "--porcelain", "research/snapshots"], cwd=REPO, capture_output=True, text=True)
if st.stdout.strip():
    sh("git", "-c", "user.email=hermes@enjyn.com", "-c", "user.name=Hermes Frontier Publisher",
       "commit", "-m", "snapshots v4: model-centric landing table + interactive Plotly charts (linked hover)")
    sh("git", "push", "-q", "origin", "gh-pages")
else:
    print("no changes to publish; gh-pages already current")
print(f"published v4: {len(charts_js)} interactive charts + {len(rows_html)}-row model table")
