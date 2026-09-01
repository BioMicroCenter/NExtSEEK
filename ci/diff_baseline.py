#!/usr/bin/env python3
"""Compare a pytest run against ci/pytest-baseline.txt and render a job summary.

Usage:
    python ci/diff_baseline.py <run-output.txt> [--baseline ci/pytest-baseline.txt]
                              [--summary $GITHUB_STEP_SUMMARY]

Always exits 0. The pytest job is informational by decision: it surfaces NEW
failures prominently and never blocks a merge.

Two parsing rules are load-bearing, both learned the hard way:

1. Scope to the "short test summary info" block. An anchored grep for ^FAILED or
   ^ERROR over the whole output also matches Django log records whose level is
   literally ERROR, e.g. "ERROR    django.request:log.py:253 Internal Server
   Error:". That inflated one measurement by 13 entries.

2. Compare deduplicated NAMES, never totals. pytest's summary line and its
   summary block disagree by design: a single errored test contributes both a
   setup and a teardown line, so one run read "73 errors" against 134 ERROR
   lines. Names are stable; counts are not.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SUMMARY_START = re.compile(r"^=+ short test summary info =+$")
SECTION = re.compile(r"^=+ .* =+$")
# The id runs to the " - <reason>" separator, NOT to the first space: parametrized
# ids routinely contain spaces, e.g. test_recall[BAL fluid samples]. Splitting on
# whitespace truncates them, and a run diffed against its own baseline then reports
# phantom new failures.
RESULT = re.compile(r"^(FAILED|ERROR) (?P<id>.+?)(?: - .*)?$")
# A real test id is a path, optionally with ::. This is what rejects log lines.
LOOKS_LIKE_ID = re.compile(r"(\.py(::|$))|/")


def extract(run_output: str) -> set[str]:
    ids: set[str] = set()
    in_summary = False
    for line in run_output.splitlines():
        if SUMMARY_START.match(line):
            in_summary = True
            continue
        if in_summary and SECTION.match(line):
            break
        if not in_summary:
            continue
        m = RESULT.match(line)
        if not m:
            continue
        test_id = m.group("id").rstrip()
        if LOOKS_LIKE_ID.search(test_id):
            ids.add(test_id)
    return ids


def load_baseline(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {
        ln.strip()
        for ln in path.read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_output", type=Path)
    ap.add_argument("--baseline", type=Path, default=Path("ci/pytest-baseline.txt"))
    ap.add_argument("--summary", type=Path, default=None,
                    help="File to append the rendered summary to (GITHUB_STEP_SUMMARY).")
    ap.add_argument("--emit-baseline", action="store_true",
                    help="Print the run's failing ids in baseline format and exit. "
                         "Regenerate the baseline with THIS, never with an ad-hoc "
                         "grep: one parser for both sides makes drift impossible.")
    args = ap.parse_args()

    run = extract(args.run_output.read_text(errors="replace"))

    if args.emit_baseline:
        for t in sorted(run):
            print(t)
        return 0
    base = load_baseline(args.baseline)

    new = sorted(run - base)
    fixed = sorted(base - run)

    out: list[str] = ["## pytest (informational)", ""]
    if new:
        out += [f"### {len(new)} NEW failure(s)", ""]
        out += [f"- `{t}`" for t in new[:50]]
        if len(new) > 50:
            out.append(f"- ...and {len(new) - 50} more")
        out.append("")
    else:
        out += ["### No new failures", ""]

    if fixed:
        out += [
            f"<details><summary>{len(fixed)} baseline entr(ies) now passing "
            f"- shrink the baseline</summary>", "",
        ]
        out += [f"- `{t}`" for t in fixed[:50]]
        out += ["", "</details>", ""]

    out += [
        "<details><summary>Reference counts (not comparable between runs)</summary>",
        "",
        f"- failing ids this run: {len(run)}",
        f"- baseline entries: {len(base)}",
        "",
        "Totals from pytest's own summary line are deliberately not used: a single "
        "errored test contributes both a setup and a teardown line, so they "
        "disagree with the summary block. Names are compared, never counts.",
        "",
        "</details>",
    ]

    text = "\n".join(out)
    print(text)
    if args.summary:
        with args.summary.open("a") as fh:
            fh.write(text + "\n")

    # Always 0. Informational by decision.
    return 0


if __name__ == "__main__":
    sys.exit(main())
