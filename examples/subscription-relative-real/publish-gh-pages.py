#!/usr/bin/env python3
"""Publish live frontier snapshots to model_skyline gh-pages.

Single self-contained page: inlines every SVG chart (no external asset
requests -> no broken links), plus raw JSON download links using RELATIVE
paths. Idempotent (commits only on diff); hard-resets to origin/gh-pages first.
"""
import json
import subprocess
import sys
from pathlib import Path
import shutil

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
for svg in (SRC / "svg").glob("*.svg"):
    shutil.copy(svg, DEST / svg.name)  # flat: svg files sit next to index for relative links

art = json.loads((SRC / "summary-v2.json").read_text())

sections = []
for m in art.get("svg", []):
    f = next(x for x in art["frontiers"] if x["id"] == m["id"])
    mem = ", ".join(f"★ {r['offering']}" for r in f.get("members", []))
    svg_text = (DEST / m["svg"].split("/")[-1]).read_text()
    body = svg_text.replace(
        "<svg ", '<svg preserveAspectRatio="xMidYMid meet" ', 1
    ).replace(
        "<svg ", '<svg style="max-width:100%;height:auto" ', 1
    )
    sections.append(f"""
<section>
<h2>{m['id']}</h2>
<p class="eyebrow">{m['workload']} · {f['axes'][0]}</p>
{body}
<p><small>Frontier members: {mem}</small></p>
</section>""")

note = art.get("note", "")
html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ModelSkyline frontier snapshots</title>
<style>
  :root {{ color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }}
  body {{ margin: 0 auto; max-width: 56rem; padding: 1rem; line-height: 1.5; }}
  section {{ margin: 1.2rem 0; }}
  h2 {{ margin: .2rem 0; }}
  .eyebrow {{ color: #526170; font-size: .85rem; }}
  svg {{ max-width: 100%; height: auto; }}
  small {{ opacity: .75; }}
</style></head><body>
<h1>Frontier snapshots</h1>
<p>{note[:300]}{'…' if len(note) > 300 else ''} · generated {art.get('generated_at','n/a')} ·
<a href="summary-v2.json">raw data (JSON)</a></p>
{''.join(sections)}
<p><small>ClinePass cap assumed $35; $20-tier sub caps modeled 8× price — †-flagged in charts.
Sources: AA Intelligence Index v4.1.1 (current scale), vals.ai SWE-bench Verified (independent
same-harness), AA-run GPQA Diamond, OpenRouter catalog, published subscription tables.</small></p>
</body></html>"""
(DEST / "index.html").write_text(html)

sh("git", "add", "research/snapshots")
st = subprocess.run(["git", "status", "--porcelain", "research/snapshots"], cwd=REPO, capture_output=True, text=True)
if st.stdout.strip():
    sh("git", "-c", "user.email=hermes@enjyn.com", "-c", "user.name=Hermes Frontier Publisher",
       "commit", "-m", "frontier snapshots: inline-SVG single page (self-contained, relative links)")
    sh("git", "push", "-q", "origin", "gh-pages")
else:
    print("no changes to publish; gh-pages already current")
print(f"published: {len(sections)} inline charts -> bglusman.github.io/model_skyline/research/snapshots/")
