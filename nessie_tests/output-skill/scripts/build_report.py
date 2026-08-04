#!/usr/bin/env python3
"""Render a reviewable HTML report from a nessie_tests run + a hand-authored triage.

Inputs
------
  manifest.json  what the harness recorded          (from fetch_run.py)
  turns.json     routing + full engine calls        (from fetch_run.py)
  corpus.json    every case expectation             (nessie_tests/corpus.json)
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
import importlib.util
import json
import pathlib
import sys
from collections import Counter

TPL_DEFAULT = pathlib.Path(__file__).resolve().parent.parent / "templates" / "report.html.tpl"
# This script ships INSIDE nessie_tests, so limits.py is two levels up.
LIMITS_DEFAULT = pathlib.Path(__file__).resolve().parents[2] / "limits.py"

VERDICTS = {"pass", "real", "drift", "policy", "masked", "notrun"}

# What an UNTRIAGED case is called. Only `failed` defaults to `real`, and the
# report captions the `real` tally "real product defects" — so the previous
# `"pass" if status == "passed" else "real"` filed a provider outage, a stale
# known_fail expectation and a case that evaluated zero criteria as product bugs.
# Every mapping below is the verdict SKILL.md's status table already tells a human
# to reach for; the tool now writes the same answer by default.
#
#   error + outage   the fallback chain 503'd before the turn ran, so nothing was
#                    tested. `notrun`, per SKILL.md — not `real` (no product
#                    behaviour was exercised) and not `drift` (the assertion is fine).
#   error            a dead endpoint or five consecutive poll failures. The harness
#                    observed nothing, which is `notrun` too; it still fails the
#                    gate, which is `gate_failed`'s job and not the verdict's.
#   skipped          the harness never issued a request for this case at all.
#   xpass            a known_fail that passed. The expectation is stale: drift.
#   no_assertions    the case evaluated zero criteria. Proving nothing is drift.
#
# An unknown status still falls through to `real`, which is the fail-safe
# direction: a status this map has not heard of should be loud.
DEFAULT_VERDICT = {
    "passed": "pass",
    "failed": "real",
    "error": "notrun",
    "skipped": "notrun",
    "xpass": "drift",
    "no_assertions": "drift",
}


def default_verdict(entry) -> str:
    if entry.get("outage"):
        return "notrun"
    return DEFAULT_VERDICT.get(entry.get("status"), "real")


def load_graph_limit_sentinels(repo):
    """`GRAPH_LIMIT_SENTINELS` read from the tree being reported on.

    It used to be the literal `250` in the META block — the cap the corpus ran
    under in 2026-07, 20x below the current 5000 — so every report built since
    flagged the wrong row counts as LIMIT hits and missed every real one. The
    number now comes from `nessie_tests/limits.py`, which is dependency-free
    precisely so it can be read from anywhere, and it moves when the cap moves.

    `--repo` first, because the report describes THAT tree's corpus; the copy
    beside this script is the fallback for a skill checked out on its own.
    """
    for p in (pathlib.Path(repo) / "nessie_tests" / "limits.py", LIMITS_DEFAULT):
        if p.exists():
            spec = importlib.util.spec_from_file_location("_nessie_limits", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.GRAPH_LIMIT_SENTINELS
    sys.exit(f"cannot find nessie_tests/limits.py under {repo} or beside this script")

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


# `reply` is the chatter's final answer — the thing the user actually reads. It is
# carried because a criterion can only ever check what someone thought to assert,
# and stale criteria are the main reason a run needs reviewing at all. Seeing the
# answer next to the call that produced it is what lets a reviewer judge a case the
# corpus scored wrongly in either direction.
CARRY = ("route", "src", "why", "mode", "aplan", "ameta", "gplan", "gmeta",
         "rplan", "model", "cost", "status", "reply")


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
    ap.add_argument("--cases", action="append", default=[],
                    help="probe/--cases file whose INLINE variants are not in the corpus. "
                         "Repeatable. Without it a probe run renders with no turns, no "
                         "criteria and no engine call, because the corpus does not contain "
                         "its variants.")
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

    # ONE source since 2026-08-04. It used to read the vendored catalog and the
    # superseded overlay file and merge them here, so this script had to reproduce
    # `corpus.merged`'s override rule to describe the right case. corpus.json has
    # one definition per id, retired ones included -- and reading those matters,
    # because an OLD manifest can name a case retired since.
    corpus_raw = lj(repo / "nessie_tests" / "corpus.json")
    variants = flatten(corpus_raw)
    # A --cases run is driven by variants defined INLINE in the probe file, which
    # `corpus.select_cases` returns at run time but which never enter the corpus. The
    # report joins each manifest entry to its declared turns via this dict, so without
    # the probe file every inline case renders empty: no query, no criteria, no call.
    #
    # Applied AFTER the corpus load, not before: an inline definition is what the run
    # actually drove, so where a probe redefines a corpus id the probe wins.
    for cf in a.cases:
        variants.update(flatten(lj(pathlib.Path(cf))))
    cgroups = {g["id"]: g for g in corpus_raw.get("consistency_groups", [])}

    verdicts = triage.get("verdicts", {})
    cases, flat_turns = [], []
    for e in manifest["entries"]:
        tds = turn_defs(e, variants, cgroups)
        flat_turns.extend(tds)
        tri = verdicts.get(e["id"], {})
        verdict = tri.get("verdict") or default_verdict(e)
        if verdict not in VERDICTS:
            sys.exit(f"unknown verdict {verdict!r} for {e['id']}")
        cases.append({
            "id": e["id"], "family": e["family"], "status": e["status"],
            "route": e.get("route"), "engine": e.get("engine"), "elapsed": e.get("elapsed_s"),
            "failed": e.get("failed_criteria", []), "xfail": e.get("expected_fail", False),
            # Carried so the record is self-describing AND so rehydrate_report.py can
            # rebuild a manifest that still gates the same way. `outage` in
            # particular: without it a rebuild turns a gate-exempt provider outage
            # back into a gate-failing error.
            "outage": e.get("outage", False), "cost": e.get("cost"),
            "route_source": e.get("route_source"),
            "route_sources": e.get("route_sources", []),
            "reason": e.get("reason", ""),
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
        # Active definitions only: a retired case cannot be run, so counting it
        # in the denominator would report permanent under-coverage.
        corpus = Counter(v["family"] for v in variants.values()
                         if v.get("status", "active") == "active")
        coverage = [[f, corpus.get(f, 0) or ran.get(f, 0), ran.get(f, 0)]
                    for f in sorted(set(corpus) | set(ran))]

    status = Counter(c["status"] for c in cases)
    # An outage is an `error`, so counting `error` alone put it in the `errored`
    # tile toned `drift` — "the assertion is stale, the product is fine" — which is
    # precisely the mis-read the flag exists to prevent. It gets its own tile, toned
    # `mute` because it is not a product result at all, and `errored` now counts the
    # infrastructure faults that really do have to be answered for.
    #
    # `xpass` and `no_assertions` had no tile whatsoever: they sat inside "cases
    # run" and nowhere else, so a run with five vacuous cases just showed five fewer
    # passes and no explanation. Both count as REAL failures in
    # `runner._is_real_failure`, hence the `real` tone — the corpus is out of step
    # with what the harness can observe, and that is the run's problem to fix.
    outaged = sum(1 for c in cases if c.get("outage"))
    stats = triage.get("stats") or [
        {"n": len(cases), "label": "cases run"},
        {"n": status.get("passed", 0), "label": "passed", "tone": "pass"},
        {"n": status.get("failed", 0), "label": "failed", "tone": "real"},
        {"n": sum(1 for c in cases if c["status"] == "error" and not c.get("outage")),
         "label": "errored", "tone": "drift"},
        {"n": outaged, "label": "provider outages", "tone": "mute"},
        {"n": status.get("xpass", 0), "label": "xpass", "tone": "real"},
        {"n": status.get("no_assertions", 0), "label": "asserted nothing", "tone": "real"},
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
                                # The CURRENT cap, taken from limits.py rather than
                                # frozen here. A triage may still pin an older one to
                                # review a run that really was capped at 250.
                                "graph_limit": triage.get(
                                    "graph_limit", max(load_graph_limit_sentinels(repo))),
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
    unjudged = [c for c in cases if c["status"] != "passed" and c["id"] not in verdicts]
    if unjudged:
        # Names the default each one got. A blanket "(defaulted to 'real')" was
        # both wrong and reassuring in the wrong direction now that an outage
        # defaults to `notrun` and a vacuous case to `drift`.
        by_verdict = Counter(c["verdict"] for c in unjudged)
        print(f"  WARNING {len(unjudged)} non-passing cases have no triage entry "
              f"(defaults applied: {dict(by_verdict)}): {[c['id'] for c in unjudged][:6]}")


if __name__ == "__main__":
    main()
