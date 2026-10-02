#!/usr/bin/env python3
"""Measure how much of a run's replies is answer and how much is machinery.

READ-ONLY, offline, free. It reads the `turns.json` a `fetch_run.py` pull already
wrote and counts markers in the reply text. Nothing here asks a model anything, so
a prompt change can be scored against a stored run before a paid re-run, and the
same numbers can be taken again afterwards.

Why it exists: the 2026-09-21 re-run scored 27 of 38 by the harness, and the real
complaint was not the failures. Across its 43 NExtSEEK-routed replies, 34 carried
the phrase "graph query over the sample network", 16 recited what the query was
constrained by, and 12 opened with the machinery instead of the answer. The worst
was 8 words of answer to 33 of machinery. None of that is visible in a pass rate.

    python reply_style.py <turns.json | a run-review directory> [--json] [--all]
    python reply_style.py run-review --worst 5     # the replies with the least answer

What a marker is: a regex over the reply, with the debug block cut off first. The
markers are deliberately literal -- they name the observed phrasings rather than
judging prose -- so a drop is evidence the phrasing went away, and a marker that
never fires is not evidence the reply was good. Read the worst offenders by hand.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: The reply the user sees ends where the debug panel begins.
_DEBUG_MARKER = "**Debug info**"

#: Each marker: a name, what it catches, and why it is a defect.
MARKERS: dict[str, tuple[str, str]] = {
    "search_machinery": (
        r"(?:graph|keyword|text|database)\s+(?:quer(?:y|ies)|search(?:es)?)\s+over\b"
        r"|was\s+determined\s+by\s+a\b"
        r"|(?:this|the)\s+(?:count|number|result|figure)\s+(?:was|comes|came)\b",
        "names the kind of search that ran, which the user cannot act on",
    ),
    "constrained_by": (
        r"\bconstrained\s+by\b|\bfiltered\s+(?:by|on)\s+the\s+(?:sample\s+type|project|keyword)",
        "recites the query's filters back at the user",
    ),
    "code_and_name": (
        r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+\((?:[A-Z][A-Z0-9._]{1,9})\)"
        r"|\b[A-Z][A-Z0-9._]{1,9}\s+\([A-Z][a-z][A-Za-z ]+\)",
        "writes a type both ways, '140 RNA samples (RNA Sample)'",
    ),
    "hedge": (
        r"\bbased on\b|\baccording to\b|\bit (?:appears|seems) that\b",
        "hedges an answer the data settles",
    ),
    "offers_to_rerun": (
        r"\b(?:feel free to|you (?:might|could|may) (?:want to )?try)\b|\blet me know if you\b",
        "offers work instead of doing it",
    ),
}

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
#: Machinery is sometimes a trailing clause of an otherwise clean sentence ("..., based on
#: a graph query over the sample network"), so the word count works on clauses: a whole
#: sentence counts only when its FIRST clause is machinery, which is the sentence that
#: exists to describe the query. Counting every sentence that mentioned the query called
#: a 19-word reply 100% machinery.
_CLAUSE = re.compile(r"(?<=[.!?,;:])\s+")


def reply_body(reply: str | None) -> str:
    """The user-facing reply: everything before the debug panel."""
    text = reply or ""
    at = text.find(_DEBUG_MARKER)
    return (text[:at] if at != -1 else text).strip()


def sentences(body: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(body) if s.strip()]


def _is_machinery(text: str) -> bool:
    """Does this clause describe the search rather than answer the question?"""
    return any(re.search(MARKERS[name][0], text, re.I) for name in ("search_machinery", "constrained_by"))


def score_reply(reply: str | None) -> dict:
    """Which markers fire, and how much of the reply is machinery by word count."""
    body = reply_body(reply)
    hits = {name: bool(re.search(pattern, body, re.I)) for name, (pattern, _) in MARKERS.items()}
    parts = sentences(body)
    machinery_words = 0
    for part in parts:
        clauses = [c for c in _CLAUSE.split(part) if c.strip()] or [part]
        if _is_machinery(clauses[0]):
            # The sentence exists to describe the query, so all of it counts, including
            # the clauses that only continue the recital ("the project Impact,").
            machinery_words += len(part.split())
        else:
            machinery_words += sum(len(c.split()) for c in clauses[1:] if _is_machinery(c))
    words = len(body.split())
    opens_with_machinery = bool(parts) and any(
        re.search(MARKERS[name][0], parts[0], re.I) for name in ("search_machinery", "constrained_by")
    )
    return {
        **hits,
        "opens_with_machinery": opens_with_machinery,
        "words": words,
        "machinery_words": machinery_words,
        "machinery_share": (machinery_words / words) if words else 0.0,
    }


def load_turns(target: Path) -> list[dict]:
    path = target / "turns.json" if target.is_dir() else target
    if not path.exists():
        sys.exit(f"no turns.json at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        sys.exit(f"{path} is not a list of turns")
    return data


def score_run(turns: list[dict], *, every_route: bool = False) -> list[dict]:
    rows = []
    for turn in turns:
        if not every_route and turn.get("route") != "nextseek_query":
            continue
        if not reply_body(turn.get("reply")):
            continue
        rows.append({"id": turn.get("id"), "q": turn.get("q"), "mode": turn.get("mode"),
                     **score_reply(turn.get("reply"))})
    return rows


def report(rows: list[dict], *, worst: int = 0) -> str:
    if not rows:
        return "no replies scored"
    n = len(rows)
    lines = [f"{n} replies scored", ""]
    names = list(MARKERS) + ["opens_with_machinery"]
    width = max(len(name) for name in names)
    for name in names:
        hit = sum(1 for row in rows if row[name])
        lines.append(f"  {name:<{width}}  {hit:>3} of {n}  {hit / n:>5.0%}")
    share = sum(r["machinery_words"] for r in rows) / max(1, sum(r["words"] for r in rows))
    median_words = sorted(r["words"] for r in rows)[n // 2]
    lines += ["", f"  machinery share of all words: {share:.0%}", f"  median reply length: {median_words} words"]
    if worst:
        lines += ["", f"  the {worst} replies with the largest machinery share:"]
        for row in sorted(rows, key=lambda r: -r["machinery_share"])[:worst]:
            lines.append(f"    {row['machinery_share']:>4.0%}  {row['machinery_words']:>3}/{row['words']:<4} "
                         f"words  {(row['q'] or '')[:60]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=Path, help="a turns.json, or the run-review directory holding one")
    ap.add_argument("--all", action="store_true", dest="every_route",
                    help="score every route, not only the NExtSEEK-routed replies")
    ap.add_argument("--json", action="store_true", help="per-reply rows instead of the summary")
    ap.add_argument("--worst", type=int, default=0, help="also list the N replies with the most machinery")
    args = ap.parse_args(argv)

    rows = score_run(load_turns(args.target), every_route=args.every_route)
    print(json.dumps(rows, indent=1) if args.json else report(rows, worst=args.worst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
