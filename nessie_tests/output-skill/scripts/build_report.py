#!/usr/bin/env python3
"""Render a reviewable HTML report from a nessie_tests run + a hand-authored triage.

Inputs
------
  manifest.json  what the harness recorded          (from fetch_run.py)
  turns.json     routing + full engine calls        (from fetch_run.py)
  catalog.json   the imported corpus expectations   (chat_nextseek/e2e/catalog.json)
  overlay.json   nessie's own variants              (nessie_tests/overlay.json)
  triage.json    YOUR analysis: verdicts, findings, gaps, next steps

Everything a reviewer needs to judge a case ends up in that one case's record:
each turn's query, how it routed and why, the exact call the engine ran (cypher
with bound parameters, or the full REST request body), what came back, and how
that landed against the asserted criteria. The mechanical join is done here; the
judgement lives in triage.json. See examples/triage.json.

Output is ONE self-contained HTML file. No external assets, no network fetches.
By default it omits <!doctype>/<html>/<body> because the Artifact publisher
supplies them; pass --standalone for a complete document to open or send.

Usage
-----
    python build_report.py --run ./run-2026-07-24 \
        --repo /path/to/dev-v3-merge \
        --triage ./triage.json \
        --out ./report.html
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

TPL_DEFAULT = pathlib.Path(__file__).resolve().parent.parent / "templates" / "report.html.tpl"

VERDICTS = {"pass", "real", "drift", "policy", "masked", "notrun"}

STANDALONE = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
              '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
              "{head}\n</head>\n<body>\n{body}\n</body>\n</html>\n")


def lj(p):
    return json.loads(pathlib.Path(p).read_bytes().decode("utf-8", errors="replace"))


def flatten(cat):
    out = {}
    for fam in cat.get("families", {}).values():
        for v in fam.get("variants", []):
            out[v["id"]] = v
    return out


def norm(s):
    return (s or "").strip().replace("—", "-").replace("�", "-").lower()


def turn_defs(entry, variants, cgroups):
    """The declared turns of a case. Consistency groups keep their queries elsewhere."""
    v = variants.get(entry["id"], {})
    if v.get("turns"):
        return [{"label": t.get("label", ""), "query": t.get("query", ""),
                 "criteria": [{"f": c["field"], "op": c["op"], "v": c.get("value")}
                              for c in t.get("pass_criteria", [])]}
                for t in v["turns"]]
    g = cgroups.get(entry["id"])
    if g:
        return [{"label": f"q{i+1}", "query": q, "criteria": []}
                for i, q in enumerate(g.get("queries", []))]
    return []


CARRY = ("route", "src", "why", "mode", "aplan", "ameta", "gplan", "gmeta",
         "rplan", "model", "cost", "status")


def align(flat_turns, tasks):
    """Greedily match each declared turn to its task, forward-only.

    Forward-only matters: query text repeats across variants ("Find mice treated
    with NDMA" appears in several), so a global text lookup mis-assigns. Walking
    both sequences in execution order keeps each occurrence with its own case,
    and lets leading tasks from an earlier run be skipped.
    """
    i = matched = 0
    for t in flat_turns:
        want = norm(t["query"])[:60]
        j = i
        while j < len(tasks) and norm(tasks[j].get("q"))[:60] != want:
            j += 1
        if j < len(tasks):
            src = tasks[j]
            t["task"] = src.get("id")
            for k in CARRY:
                if src.get(k) is not None:
                    t[k] = src[k]
            i = j + 1
            matched += 1
    return matched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--triage", required=True)
    ap.add_argument("--template", default=str(TPL_DEFAULT))
    ap.add_argument("--out", required=True)
    ap.add_argument("--standalone", action="store_true",
                    help="emit a complete HTML document (for opening locally or sending); "
                         "omit when publishing via the Artifact tool, which supplies the skeleton")
    a = ap.parse_args()

    run, repo = pathlib.Path(a.run), pathlib.Path(a.repo)
    manifest = lj(run / "manifest.json")
    tasks = lj(run / "turns.json")
    triage = lj(a.triage)

    overlay_raw = lj(repo / "nessie_tests" / "overlay.json")
    base = flatten(lj(repo / "chat_nextseek" / "e2e" / "catalog.json"))
    overlay = flatten(overlay_raw)
    variants = {**base, **overlay}
    cgroups = {g["id"]: g for g in overlay_raw.get("consistency_groups", [])}

    verdicts = triage.get("verdicts", {})
    cases, flat_turns = [], []
    for e in manifest["entries"]:
        tds = turn_defs(e, variants, cgroups)
        flat_turns.extend(tds)
        tri = verdicts.get(e["id"], {})
        verdict = tri.get("verdict") or ("pass" if e["status"] == "passed" else "real")
        if verdict not in VERDICTS:
            sys.exit(f"unknown verdict {verdict!r} for {e['id']}")
        cases.append({
            "id": e["id"], "family": e["family"], "status": e["status"],
            "route": e.get("route"), "engine": e.get("engine"), "elapsed": e.get("elapsed_s"),
            "failed": e.get("failed_criteria", []), "xfail": e.get("expected_fail", False),
            "turns": tds, "verdict": verdict, "head": tri.get("head"),
            "observed": tri.get("observed"), "note": tri.get("note"),
        })

    matched = align(flat_turns, tasks)
    print(f"turn->task alignment: {matched}/{len(flat_turns)} declared turns matched a task")
    for t in flat_turns:
        if "task" not in t:
            print(f"  never ran: {t['query'][:60]!r}")

    for c in cases:
        run_turns = [t for t in c["turns"] if t.get("task")]
        c["task"] = run_turns[-1]["task"] if run_turns else None
        gm = next((t.get("gmeta") for t in reversed(c["turns"]) if t.get("gmeta")), None)
        c["gcount"] = (gm or {}).get("count")

    coverage = triage.get("coverage")
    if not coverage:
        ran = Counter(e["family"] for e in manifest["entries"])
        corpus = Counter(v["family"] for v in base.values())
        corpus.update(v["family"] for v in overlay.values())
        coverage = [[f, corpus.get(f, 0) or ran.get(f, 0), ran.get(f, 0)]
                    for f in sorted(set(corpus) | set(ran))]

    status = Counter(c["status"] for c in cases)
    stats = triage.get("stats") or [
        {"n": len(cases), "label": "cases run"},
        {"n": status.get("passed", 0), "label": "passed", "tone": "pass"},
        {"n": status.get("failed", 0), "label": "failed", "tone": "real"},
        {"n": status.get("error", 0), "label": "errored", "tone": "drift"},
        {"n": f"{100*sum(r[2] for r in coverage)/max(1,sum(r[1] for r in coverage)):.1f}%",
         "label": "of corpus", "tone": "mute"},
    ]

    html = pathlib.Path(a.template).read_text(encoding="utf-8")
    subs = {
        "__TITLE__": triage.get("title", "nessie_tests run review"),
        "__EYEBROW__": triage.get("eyebrow", ""),
        "__HEADLINE__": triage.get("headline", ""),
        "__SUBHEAD__": triage.get("subhead", ""),
        "__RUNROOT__": triage.get("runroot", "/app/outputs/<run>"),
        "__META__": json.dumps({"runline": triage.get("runline", []), "stats": stats,
                                "reframe": triage.get("reframe", ""),
                                "coverage_lede": triage.get("coverage_lede"),
                                "graph_limit": triage.get("graph_limit", 250),
                                "title": triage.get("title", "nessie_tests run review"),
                                # notes_id keys browser-local autosave; bump it per run
                                # so two reports never share a notes store
                                "notes_id": triage.get("notes_id", pathlib.Path(a.run).name),
                                "notes_file": triage.get("notes_file", "nessie-notes")},
                               separators=(",", ":")),
        "__CASES__": json.dumps(cases, separators=(",", ":")),
        "__TURNS__": json.dumps(tasks, separators=(",", ":")),
        "__FINDINGS__": json.dumps(triage.get("findings", []), separators=(",", ":")),
        "__COVERAGE__": json.dumps(coverage, separators=(",", ":")),
        "__GAPS__": json.dumps(triage.get("gaps", []), separators=(",", ":")),
        "__NEXT__": json.dumps(triage.get("next", []), separators=(",", ":")),
    }
    for k, v in subs.items():
        html = html.replace(k, v)
    left = [k for k in subs if k in html]
    if left:
        sys.exit(f"unsubstituted: {left}")

    if a.standalone:
        # <title>/<style> belong in <head>; everything from the first <div> is body.
        split = html.find("<div class=\"wrap\">")
        head, body = (html[:split], html[split:]) if split > 0 else ("", html)
        html = STANDALONE.format(head=head.strip(), body=body.strip())

    pathlib.Path(a.out).write_text(html, encoding="utf-8")
    print(f"wrote {a.out} ({len(html)} bytes){'  [standalone document]' if a.standalone else ''}")
    print(f"  cases {len(cases)}  verdicts {dict(Counter(c['verdict'] for c in cases))}")
    withcall = sum(1 for c in cases for t in c["turns"] if t.get("aplan") or t.get("gplan"))
    print(f"  turns carrying a graph/REST call: {withcall}")
    unjudged = [c["id"] for c in cases
                if c["status"] != "passed" and c["id"] not in verdicts]
    if unjudged:
        print(f"  WARNING {len(unjudged)} non-passing cases have no triage entry "
              f"(defaulted to 'real'): {unjudged[:6]}")


if __name__ == "__main__":
    main()
