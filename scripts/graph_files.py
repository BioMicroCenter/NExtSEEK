#!/usr/bin/env python3
"""Collapse graphify's function-level graph into the file-level graph under docs/graph/.

    /graphify . --update            # refresh graphify-out/graph.json (code only, AST only)
    python3 scripts/graph_files.py  # from the repo root; stdlib only

Reads graphify-out/graph.json (local, gitignored) and writes docs/graph/graph-files.json,
docs/graph/graph.html and docs/graph/architecture.svg. docs/graph/README.md says what each is.

What it keeps: EXTRACTED import, call and inherit edges between two different tracked files,
for pairs joined by at least one import edge. graphify resolves a call by name, so a call to
`Path(...)` can land on any file that defines a `_Path`; a pair with calls but no import is dropped.
What it drops: an edge into a module whose name shadows the standard library or a driver
(graphify resolves a bare `import csv` to chat_nextseek's reports/exporters/csv.py, and
`from neo4j import ...` to helpers/tools/neo4j.py) unless the source file really imports
that module by its package path or a relative import.
Communities: Louvain (deterministic, resolution 0.6) on the weighted file graph. Inside each
Louvain community, an area (AREAS below) with at least SPLIT_MIN files keeps its own plain name
and the remaining files take the community's majority area; equal names then merge across
communities. A file with no kept edge takes its own area's name.
"""
from __future__ import annotations

import html
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

SOURCE = Path("graphify-out/graph.json")
OUT = Path("docs/graph")
IMPORTS = {"imports", "imports_from", "re_exports", "dynamic_import"}
RELATIONS = IMPORTS | {"calls", "inherits"}
SHADOWED = set(sys.stdlib_module_names) | {"neo4j"}
RESOLUTION = 0.6
# Inside one Louvain community, an area holding at least this many of its files keeps its own name.
SPLIT_MIN = 8

# Longest matching prefix names the area. Plain words; the order of rows does not matter.
AREAS = {
    "NessieAI/chat_nextseek/src/chat_nextseek/pipeline/": "Pipeline launch",
    "NessieAI/chat_nextseek/src/chat_nextseek/seqera/": "Pipeline launch",
    "NessieAI/chat_nextseek/src/chat_nextseek/luria/": "Pipeline launch",
    "NessieAI/chat_nextseek/src/chat_nextseek/evaluator/": "Retry evaluator",
    "NessieAI/chat_nextseek/": "NS engine",
    "NessieAI/tests/chat_nextseek/": "NS engine",
    "NessieAI/tests/api/": "NS engine",
    "NessieAI/cc/": "Container-CC engine",
    "NessieAI/tests/cc/": "Container-CC engine",
    "NessieAI/cc/op_registry/": "Ops and op registry",
    "NessieAI/ns/": "Ops and op registry",
    "NessieAI/tests/ns/": "Ops and op registry",
    "NessieAI/build_tools/": "Ops and op registry",
    "NessieAI/tests/build_tools/": "Ops and op registry",
    "NessieAI/docker/": "AI images",
    "NessieAI/router/": "Router and HiBayes",
    "NessieAI/dmac_assistant/": "Router and HiBayes",
    "NessieAI/hibayes/": "Router and HiBayes",
    "NessieAI/tests/router/": "Router and HiBayes",
    "NessieAI/tests/hibayes/": "Router and HiBayes",
    "NessieAI/tests/nessie_tests/": "Nessie test harness",
    "NessieAI/tests/e2e/": "Nessie test harness",
    "NessieAI/chat_frontend/": "Chat frontend",
    "NessieAI/schema_rag/": "Schema RAG",
    "NessieAI/tests/schema_rag/": "Schema RAG",
    "NessieAI/": "NS engine",
    "nextseek_api/batch_upload/": "Batch upload",
    "nextseek_api/attributes/": "Attribute API",
    "nextseek_api/assay_registration/": "Assay registration",
    "nextseek_api/graph_search/": "Graph search and sync",
    "nextseek_api/graph_sync/": "Graph search and sync",
    "nextseek_api/studies/": "Graph search and sync",
    "nextseek_api/": "nextseek_api core",
    "seek/": "SEEK pages and table layer",
    "dmac/": "SEEK pages and table layer",
    "startup/": "startup CLI",
    "ci/": "CI",
    "scripts/": "Scripts",
    "static/": "Site JavaScript",
    "themes/": "Site JavaScript",
    "docker/": "startup CLI",
}

# Where each named community sits in architecture.svg: (column, row). nextseek_api core is the
# hub, so its heaviest neighbours ring it; a name missing here goes to the first free cell.
SLOTS = {
    "Site JavaScript": (0, 0), "Chat frontend": (1, 0), "Scripts": (2, 0), "CI": (3, 0),
    "startup CLI": (4, 0),
    "Attribute API": (0, 1), "Graph search and sync": (1, 1), "Batch upload": (2, 1),
    "Assay registration": (3, 1),
    "SEEK pages and table layer": (1, 2), "nextseek_api core": (2, 2), "Schema RAG": (3, 2),
    "Router and HiBayes": (1, 3), "Container-CC engine": (2, 3), "Ops and op registry": (3, 3),
    "Nessie test harness": (0, 4), "Pipeline launch": (1, 4), "NS engine": (2, 4),
    "Retry evaluator": (3, 4), "AI images": (4, 4),
}
MIN_LINK = 6  # architecture.svg draws a cross-community link only from this many file links up
PALETTE = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1",
           "#ff9da7", "#9c755f", "#bab0ac", "#86bcb6", "#d37295", "#a0cbe8", "#ffbe7d",
           "#8cd17d", "#b6992d", "#499894", "#f1ce63", "#79706e", "#d4a6c8", "#9d9d9d"]


def area(path: str) -> str:
    best = max((p for p in AREAS if path.startswith(p)), key=len, default=None)
    return AREAS[best] if best else "Other"


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return "/tests/" in path or name.startswith("test_") or name == "conftest.py" or "/e2e/" in path


def real_import(source_text: str, target: str) -> bool:
    """True when a source file imports a shadowing module by its package path or relatively."""
    parts = Path(target).with_suffix("").parts
    stem, parent = parts[-1], parts[-2] if len(parts) > 1 else ""
    pats = [rf"\b{parent}\.{stem}\b", rf"\b{parent}\s+import\s+[^\n]*\b{stem}\b",
            rf"from\s+\.+{stem}\b", rf"from\s+\.+\s+import\s+[^\n]*\b{stem}\b"]
    return any(re.search(p, source_text) for p in pats)


def louvain(adj: dict[str, dict[str, float]], resolution: float) -> dict[str, str]:
    """Plain Louvain, deterministic (sorted visiting order). Returns node -> community key."""
    member = {n: n for n in adj}  # original node -> current super node
    graph = {u: dict(vs) for u, vs in adj.items()}
    while True:
        deg = {u: sum(vs.values()) for u, vs in graph.items()}
        m2 = sum(deg.values()) or 1.0
        comm = {u: u for u in graph}
        tot = dict(deg)
        moved_any, improved = True, False
        while moved_any:
            moved_any = False
            for u in sorted(graph):
                cu, ku = comm[u], deg[u]
                links = defaultdict(float)
                for v, w in graph[u].items():
                    if v != u:
                        links[comm[v]] += w
                tot[cu] -= ku
                best, gain = cu, links.get(cu, 0.0) - resolution * tot[cu] * ku / m2
                for c in sorted(links):
                    g = links[c] - resolution * tot[c] * ku / m2
                    if g > gain + 1e-12:
                        best, gain = c, g
                tot[best] += ku
                if best != cu:
                    comm[u], moved_any, improved = best, True, True
        if not improved:
            break
        new = defaultdict(lambda: defaultdict(float))
        for u, vs in graph.items():
            for v, w in vs.items():
                new[comm[u]][comm[v]] += w
        graph = {u: dict(vs) for u, vs in new.items()}
        member = {n: comm[s] for n, s in member.items()}
    return member


def main() -> int:
    if not SOURCE.exists():
        print(f"{SOURCE} not found: run graphify on the repo root first (code only)", file=sys.stderr)
        return 1
    tracked = set(subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split("\n"))
    tracked.discard("")
    commit = subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True).stdout.strip()
    raw = json.loads(SOURCE.read_text(encoding="utf-8"))
    file_of = {n["id"]: n.get("source_file") for n in raw["nodes"]}
    files = sorted({f for f in file_of.values() if f in tracked})

    texts: dict[str, str] = {}
    weight: Counter = Counter()
    imported: set = set()
    dropped = 0
    for e in raw.get("links", raw.get("edges", [])):
        if e.get("relation") not in RELATIONS or e.get("confidence") != "EXTRACTED":
            continue
        a, b = file_of.get(e["source"]), file_of.get(e["target"])
        if a == b or a not in tracked or b not in tracked:
            continue
        for src, dst in ((a, b), (b, a)):
            if dst.endswith(".py") and Path(dst).stem in SHADOWED:
                if src not in texts:
                    texts[src] = Path(src).read_text(encoding="utf-8", errors="replace")
                if not real_import(texts[src], dst):
                    break
        else:
            weight[tuple(sorted((a, b)))] += 1
            if e["relation"] in IMPORTS:
                imported.add(tuple(sorted((a, b))))
            continue
        dropped += 1
    name_only = [pair for pair in weight if pair not in imported]
    for pair in name_only:
        del weight[pair]
    dropped += len(name_only)

    adj: dict[str, dict[str, float]] = defaultdict(dict)
    for (a, b), w in weight.items():
        adj[a][b] = adj[b][a] = float(w)
    member = louvain(dict(adj), RESOLUTION)

    groups = defaultdict(list)
    for f, c in member.items():
        groups[c].append(f)
    name_of = {}
    for c, fs in groups.items():
        by_area = Counter(area(f) for f in fs)
        majority = by_area.most_common(1)[0][0]
        for f in fs:
            name_of[f] = area(f) if by_area[area(f)] >= SPLIT_MIN else majority
    for f in files:
        name_of.setdefault(f, area(f))

    names = sorted(set(name_of.values()), key=lambda n: (-sum(1 for v in name_of.values() if v == n), n))
    index = {f: i for i, f in enumerate(files)}
    degree = Counter()
    for (a, b) in weight:
        degree[a] += 1
        degree[b] += 1
    communities = []
    for n in names:
        fs = [f for f in files if name_of[f] == n]
        hubs = sorted(fs, key=lambda f: (-degree[f], f))[:5]
        communities.append({"name": n, "files": len(fs), "hubs": hubs})

    OUT.mkdir(parents=True, exist_ok=True)
    doc = {
        "generated_by": "scripts/graph_files.py",
        "commit": commit,
        "edge_rule": "EXTRACTED import, call and inherit edges between two tracked files; weight = edge count",
        "dropped_false_resolutions": dropped,
        "communities": communities,
        "files": [[f, names.index(name_of[f]), degree[f]] for f in files],
        "edges": [[index[a], index[b], w] for (a, b), w in sorted(weight.items())],
    }
    (OUT / "graph-files.json").write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    (OUT / "graph.html").write_text(render_html(doc), encoding="utf-8")
    (OUT / "architecture.svg").write_text(render_svg(doc, name_of, weight), encoding="utf-8")
    print(f"{len(files)} files, {len(weight)} file pairs, {len(names)} communities, {dropped} dropped -> {OUT}/")
    return 0


def render_svg(doc: dict, name_of: dict, weight: Counter) -> str:
    """Communities as boxes on a fixed grid; lines for the heaviest non-test cross-community links."""
    counts = {c["name"]: c["files"] for c in doc["communities"] if c["name"] != "Other"}
    cross: Counter = Counter()
    for (a, b), w in weight.items():
        if is_test(a) or is_test(b):
            continue
        na, nb = name_of[a], name_of[b]
        if na != nb:
            cross[tuple(sorted((na, nb)))] += w
    W, H, BW, BH, GAPX, GAPY, LEFT, TOP = 1000, 580, 185, 60, 10, 42, 15, 48
    free = [(c, r) for r in range(5) for c in range(5) if (c, r) not in SLOTS.values()]
    pos = {}
    for n in counts:
        col, row = SLOTS.get(n) or (free.pop(0) if free else (4, 3))
        pos[n] = (LEFT + col * (BW + GAPX), TOP + row * (BH + GAPY))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           'font-family="Helvetica, Arial, sans-serif">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           f'<text x="{W/2}" y="24" text-anchor="middle" font-size="16" font-weight="bold" fill="#222">'
           f'NExtSEEK code communities (file-level graph, commit {html.escape(doc["commit"])})</text>']
    top_links = [(pair, w) for pair, w in cross.most_common() if w >= MIN_LINK]
    maxw = max((w for _, w in top_links), default=1)
    for (a, b), w in top_links:
        if a not in pos or b not in pos:
            continue
        (xa, ya), (xb, yb) = pos[a], pos[b]
        sw = 1 + 5 * math.log1p(w) / math.log1p(maxw)
        out.append(f'<line x1="{xa+BW/2:.0f}" y1="{ya+BH/2:.0f}" x2="{xb+BW/2:.0f}" y2="{yb+BH/2:.0f}" '
                   f'stroke="#8a8f98" stroke-opacity="0.55" stroke-width="{sw:.1f}"><title>{html.escape(a)} - '
                   f'{html.escape(b)}: {w}</title></line>')
    for i, c in enumerate(doc["communities"]):
        n = c["name"]
        if n not in pos:
            continue
        x, y = pos[n]
        out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{BW}" height="{BH}" rx="8" fill="#ffffff"/>')
        color = PALETTE[i % len(PALETTE)]
        out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{BW}" height="{BH}" rx="8" fill="{color}" '
                   f'fill-opacity="0.22" stroke="{color}" stroke-width="2"/>')
        size = min(14.0, BW * 0.92 / (0.6 * len(n)))
        out.append(f'<text x="{x+BW/2:.0f}" y="{y+27:.0f}" text-anchor="middle" font-size="{size:.1f}" '
                   f'font-weight="bold" fill="#1a1a1a">{html.escape(n)}</text>')
        out.append(f'<text x="{x+BW/2:.0f}" y="{y+46:.0f}" text-anchor="middle" font-size="12" '
                   f'fill="#444">{c["files"]} files</text>')
    out.append(f'<text x="{W/2}" y="{H-10}" text-anchor="middle" font-size="11" fill="#555">Line width: '
               f'import, call and inherit links between non-test files ({MIN_LINK} or more). '
               'Regenerate: python3 scripts/graph_files.py</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def render_html(doc: dict) -> str:
    data = json.dumps({"c": [c["name"] for c in doc["communities"]], "f": doc["files"], "e": doc["edges"],
                       "p": PALETTE}, separators=(",", ":"))
    return HTML.replace("__COMMIT__", html.escape(doc["commit"])).replace("__DATA__", data)


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NExtSEEK file graph</title>
<script src="https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js"
        integrity="sha384-Ux6phic9PEHJ38YtrijhkzyJ8yQlH8i/+buBR8s3mAZOJrP1gwyvAcIYl3GWtpX1"
        crossorigin="anonymous"></script>
<style>
  body { margin: 0; display: flex; height: 100vh; font-family: Helvetica, Arial, sans-serif; background: #fff; color: #222; }
  #graph { flex: 1; }
  #side { width: 320px; overflow: auto; padding: 12px; border-left: 1px solid #ddd; font-size: 13px; }
  #side h1 { font-size: 15px; margin: 0 0 8px; }
  #q { width: 100%; box-sizing: border-box; padding: 6px; margin-bottom: 8px; }
  .lg { display: flex; align-items: center; gap: 6px; cursor: pointer; margin: 2px 0; }
  .sw { width: 12px; height: 12px; border-radius: 3px; flex: none; }
  .off { opacity: 0.35; }
  #info { margin-top: 12px; word-break: break-all; }
  #info li { margin: 2px 0; }
</style>
</head>
<body>
<div id="graph"></div>
<div id="side">
  <h1>NExtSEEK file graph (__COMMIT__)</h1>
  <input id="q" placeholder="Find a file (path substring)">
  <div id="legend"></div>
  <div id="info">Click a file to see its links. Click a community to hide or show it.</div>
</div>
<script>
const D = __DATA__;
const hidden = new Set();
const nodes = new vis.DataSet(D.f.map((f, i) => ({id: i, label: f[0].split("/").pop(), title: f[0],
  color: D.p[f[1] % D.p.length], value: 1 + f[2]})));
const edges = new vis.DataSet(D.e.map((e, i) => ({id: i, from: e[0], to: e[1], value: e[2]})));
const net = new vis.Network(document.getElementById("graph"), {nodes, edges}, {
  nodes: {shape: "dot", scaling: {min: 4, max: 28}, font: {size: 10}},
  edges: {color: {color: "#c8ccd2", highlight: "#333"}, scaling: {min: 0.5, max: 4}, smooth: false},
  physics: {solver: "forceAtlas2Based", stabilization: {iterations: 250}},
  interaction: {hover: true, tooltipDelay: 120}});
net.once("stabilizationIterationsDone", () => net.setOptions({physics: false}));
const legend = document.getElementById("legend");
D.c.forEach((name, ci) => {
  const n = D.f.filter(f => f[1] === ci).length;
  const row = document.createElement("div");
  row.className = "lg";
  row.innerHTML = '<span class="sw" style="background:' + D.p[ci % D.p.length] + '"></span>' + name + " (" + n + ")";
  row.onclick = () => {
    hidden.has(ci) ? hidden.delete(ci) : hidden.add(ci);
    row.classList.toggle("off");
    nodes.update(D.f.map((f, i) => ({id: i, hidden: hidden.has(f[1])})));
  };
  legend.appendChild(row);
});
function show(i) {
  const f = D.f[i];
  const nb = net.getConnectedNodes(i).map(j => D.f[j][0]).sort();
  document.getElementById("info").innerHTML = "<b>" + f[0] + "</b><br>" + D.c[f[1]] + ", " + nb.length +
    " linked files<ul>" + nb.map(p => "<li>" + p + "</li>").join("") + "</ul>";
}
net.on("click", p => { if (p.nodes.length) show(p.nodes[0]); });
document.getElementById("q").addEventListener("change", ev => {
  const s = ev.target.value.trim();
  const i = D.f.findIndex(f => f[0].includes(s));
  if (s && i >= 0) { net.selectNodes([i]); net.focus(i, {scale: 1.2}); show(i); }
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
