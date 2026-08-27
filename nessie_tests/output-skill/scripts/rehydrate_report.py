#!/usr/bin/env python3
"""Rebuild an ALREADY-BUILT report so it picks up fields the builder has since gained.

Why this exists: reports are the durable artefact of a run, but the run directory
(manifest.json) gets overwritten by the next run, and the triage.json that produced
the report is usually a scratch file. So once `build_report.py` learns to render a
new field — the chatter's reply, say — older reports cannot simply be rebuilt.

They can, though, because a built report embeds everything needed to reconstruct its
own inputs: `CASES` carries the manifest entries plus the triage verdicts, and
`META`/`FINDINGS`/`GAPS`/`NEXT` carry the rest of the triage. This extracts both back
out, so `fetch_run.py` + `build_report.py` can run again against the live task rows
and produce the same report with the new fields filled in.

    python rehydrate_report.py --html /path/old-report.html --out ./rebuilt-run

writes `<out>/manifest.json` and `<out>/triage.json`. Then the normal flow:

    python fetch_run.py --out <out> --manifest <ignored> --id-min A --id-max B
    python build_report.py --run <out> --repo <repo> --triage <out>/triage.json --out new.html

fetch_run.py only writes manifest.json when the remote file actually exists, so point
--manifest at a path that is gone (which it will be, since the next run overwrote it)
and the reconstruction here survives. It prints a WARNING; that is expected.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

# (manifest field, the key build_report.py writes it under in CASES, the value to
# use when the report predates the field).
#
# LIVE, not decoration: `main` rebuilds every entry from this table. The version
# before it was a comment-only tuple that named `cost` and `reason` while the code
# beside it silently dropped both — and dropped `outage` with them, which is the
# one that mattered. `runner._is_real_failure` EXEMPTS a provider outage from the
# gate; an entry rebuilt without the flag is an ordinary `error`, so rebuilding a
# report converted every exempt outage back into a gate-failing red. Ten of the
# eighteen reds in the 2026-08-03 seed-6 run were one Bedrock outage, so that is
# not a rounding error on a rebuild — it is most of the failure signal.
#
# `route_source`/`route_sources` are here for the same reason at lower stakes:
# without them a rebuilt manifest cannot say whether a turn was routed by a
# decision or fell through to the keyword regex, and `classify_entries` silently
# stops reporting heuristic routing.
_ENTRY_FIELDS = (
    ("family", "family", ""),
    ("status", "status", "failed"),
    ("route", "route", None),
    ("engine", "engine", None),
    ("route_source", "route_source", None),
    ("route_sources", "route_sources", []),
    ("cost", "cost", None),
    ("elapsed_s", "elapsed", 0.0),
    ("failed_criteria", "failed", []),
    ("expected_fail", "xfail", False),
    ("outage", "outage", False),
    ("reason", "reason", ""),
)
# Keys it reads off the triage, top level.
_TRIAGE_KEYS = ("title", "eyebrow", "headline", "subhead", "runroot", "runline",
                "reframe", "coverage_lede", "graph_limit", "stats", "coverage",
                "notes_id", "notes_file")


def _literal(html: str, name: str):
    """Pull `const NAME = <json>;` back out of a built report."""
    m = re.search(rf"const {name}\s*=\s*(\[.*?\]|\{{.*?\}});", html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        sys.exit(f"could not parse embedded {name}: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True, help="an already-built report")
    ap.add_argument("--out", required=True, help="directory to write manifest.json + triage.json")
    a = ap.parse_args()

    html = pathlib.Path(a.html).read_text(encoding="utf-8")
    cases = _literal(html, "CASES")
    if not cases:
        sys.exit("no CASES literal found — is this a report built by build_report.py?")
    meta = _literal(html, "META") or {}

    entries = []
    verdicts = {}
    for c in cases:
        # `observations` stays empty: a built report carries the per-turn evidence
        # under TURNS, not the per-criterion rows, so there is nothing to recover.
        entry = {"id": c["id"], "tier": "full", "observations": []}
        for field, key, default in _ENTRY_FIELDS:
            value = c.get(key)
            entry[field] = default if value is None else value
        entries.append(entry)
        # The triage half of each case record.
        if any(c.get(k) for k in ("verdict", "head", "observed", "note")):
            verdicts[c["id"]] = {k: c[k] for k in ("verdict", "task", "head", "observed", "note")
                                 if c.get(k) is not None}

    manifest = {"started_at": meta.get("started_at", ""), "ended_at": meta.get("ended_at", ""),
                "tier": "full", "scope": "all", "entries": entries}

    triage = {k: meta[k] for k in _TRIAGE_KEYS if meta.get(k) is not None}
    triage.setdefault("title", "nessie_tests run review")
    for key, name in (("findings", "FINDINGS"), ("gaps", "GAPS"), ("next", "NEXT")):
        v = _literal(html, name)
        if v:
            triage[key] = v
    triage["verdicts"] = verdicts

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "triage.json").write_text(json.dumps(triage, indent=2), encoding="utf-8")
    print(f"{a.html}")
    print(f"  manifest.json  {len(entries)} entries")
    print(f"  triage.json    {len(verdicts)} verdicts, "
          f"{len(triage.get('findings', []))} findings, {len(triage.get('gaps', []))} gaps")


if __name__ == "__main__":
    main()
