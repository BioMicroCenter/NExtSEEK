#!/usr/bin/env python3
"""Render a reviewable HTML report from a nessie_tests run + a hand-authored triage.

Inputs
------
  manifest.json  what the harness recorded          (from fetch_run.py)
  turns.json     router decisions + cypher + REST   (from fetch_run.py)
  catalog.json   the imported corpus expectations   (chat_nextseek/e2e/catalog.json)
  overlay.json   nessie's own variants              (nessie_tests/overlay.json)
  triage.json    YOUR analysis: verdicts, findings, gaps, next steps

The mechanical join (case -> query -> asserted criteria -> observed route/engine)
is done here. The judgement (real vs drift vs policy, and why) lives in
triage.json and is written by whoever runs the triage. See examples/triage.json.

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


def load_json(p: pathlib.Path):
    # Corpus text can carry non-UTF8 bytes; never let that kill the build.
    return json.loads(p.read_bytes().decode("utf-8", errors="replace"))


def flatten(catalog: dict) -> dict:
    out = {}
    for fam in catalog.get("families", {}).values():
        for v in fam.get("variants", []):
            out[v["id"]] = v
    return out


def build_cases(manifest, variants, triage, turns):
    """Join manifest entries to their queries, criteria, and authored verdict."""
    verdicts = triage.get("verdicts", {})
    by_query = {}
    for t in turns:
        by_query.setdefault((t.get("q") or "").strip(), t)

    cases = []
    for e in manifest["entries"]:
        v = variants.get(e["id"], {})
        turn_defs = [
            {"label": t.get("label", ""), "query": t.get("query", ""),
             "criteria": [{"f": c["field"], "op": c["op"], "v": c.get("value")}
                          for c in t.get("pass_criteria", [])]}
            for t in v.get("turns", [])
        ]
        # Recover the task id by matching the (last) turn's query text.
        task = None
        gcount = None
        for td in reversed(turn_defs):
            hit = by_query.get((td["query"] or "").strip())
            if hit:
                task, gcount = hit.get("id"), hit.get("cnt")
                break

        tri = verdicts.get(e["id"], {})
        verdict = tri.get("verdict")
        if verdict is None:
            verdict = "pass" if e["status"] == "passed" else "real"
        if verdict not in VERDICTS:
            sys.exit(f"triage.json: unknown verdict {verdict!r} for {e['id']}")

        cases.append({
            "id": e["id"], "family": e["family"], "status": e["status"],
            "route": e.get("route"), "engine": e.get("engine"),
            "elapsed": e.get("elapsed_s"), "failed": e.get("failed_criteria", []),
            "reason": e.get("reason", ""), "xfail": e.get("expected_fail", False),
            "turns": turn_defs, "task": tri.get("task", task), "gcount": gcount,
            "verdict": verdict, "head": tri.get("head"),
            "observed": tri.get("observed"), "note": tri.get("note"),
        })
    return cases


def build_coverage(manifest, base_variants, overlay_variants):
    ran = Counter(e["family"] for e in manifest["entries"])
    corpus = Counter(v["family"] for v in base_variants.values())
    corpus.update(v["family"] for v in overlay_variants.values())
    rows = []
    for fam in sorted(set(corpus) | set(ran)):
        rows.append([fam, corpus.get(fam, 0) or ran.get(fam, 0), ran.get(fam, 0)])
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="dir holding manifest.json + turns.json")
    ap.add_argument("--repo", required=True, help="dev-v3-merge checkout root")
    ap.add_argument("--triage", required=True)
    ap.add_argument("--template", default=str(TPL_DEFAULT))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    run = pathlib.Path(args.run)
    repo = pathlib.Path(args.repo)

    manifest = load_json(run / "manifest.json")
    turns = load_json(run / "turns.json")
    triage = load_json(pathlib.Path(args.triage))

    base = flatten(load_json(repo / "chat_nextseek" / "e2e" / "catalog.json"))
    overlay = flatten(load_json(repo / "nessie_tests" / "overlay.json"))
    variants = {**base, **overlay}

    cases = build_cases(manifest, variants, triage, turns)
    coverage = triage.get("coverage") or build_coverage(manifest, base, overlay)

    tally = Counter(c["verdict"] for c in cases)
    status = Counter(c["status"] for c in cases)
    stats = triage.get("stats") or [
        {"n": len(cases), "label": "cases run"},
        {"n": status.get("passed", 0), "label": "passed", "tone": "pass"},
        {"n": status.get("failed", 0), "label": "failed", "tone": "real"},
        {"n": status.get("error", 0), "label": "errored", "tone": "drift"},
        {"n": f"{100*sum(r[2] for r in coverage)/max(1,sum(r[1] for r in coverage)):.1f}%",
         "label": "of corpus", "tone": "mute"},
    ]

    html = pathlib.Path(args.template).read_text(encoding="utf-8")
    subs = {
        "__TITLE__":    triage.get("title", "nessie_tests run review"),
        "__EYEBROW__":  triage.get("eyebrow", "Test run review"),
        "__HEADLINE__": triage.get("headline", "nessie_tests run review"),
        "__SUBHEAD__":  triage.get("subhead", ""),
        "__RUNROOT__":  triage.get("runroot", "/app/outputs/<run>"),
        "__META__":     json.dumps({
                            "runline": triage.get("runline", []),
                            "stats": stats,
                            "reframe": triage.get("reframe", ""),
                            "coverage_lede": triage.get("coverage_lede"),
                            "graph_limit": triage.get("graph_limit", 250),
                        }, separators=(",", ":")),
        "__CASES__":    json.dumps(cases, separators=(",", ":")),
        "__TURNS__":    json.dumps(turns, separators=(",", ":")),
        "__FINDINGS__": json.dumps(triage.get("findings", []), separators=(",", ":")),
        "__COVERAGE__": json.dumps(coverage, separators=(",", ":")),
        "__GAPS__":     json.dumps(triage.get("gaps", []), separators=(",", ":")),
        "__NEXT__":     json.dumps(triage.get("next", []), separators=(",", ":")),
    }
    for k, v in subs.items():
        html = html.replace(k, v)

    leftover = [k for k in subs if k in html]
    if leftover:
        sys.exit(f"template placeholders not substituted: {leftover}")

    pathlib.Path(args.out).write_text(html, encoding="utf-8")
    print(f"wrote {args.out}  ({len(html)} bytes)")
    print(f"  cases {len(cases)}  verdicts {dict(tally)}")
    unjudged = [c["id"] for c in cases
                if c["status"] != "passed" and c["id"] not in triage.get("verdicts", {})]
    if unjudged:
        print(f"  WARNING {len(unjudged)} non-passing cases have no triage entry "
              f"(defaulted to 'real'): {unjudged[:6]}")


if __name__ == "__main__":
    main()
