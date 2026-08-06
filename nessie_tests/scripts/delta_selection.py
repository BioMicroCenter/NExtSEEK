"""Narrow `is_bayesian` to the variants a prior run has NOT already graded.

WHY THIS EXISTS. `--bayesian` drives every variant flagged `is_bayesian` and
`active`; there is no case-list flag, deliberately (`cli.py`: "`is_bayesian` IS
the selection. Accepting a second selection source would make 'what ran' depend
on two things at once"). So the only way to run a DELTA is to narrow the flag.

Running the full 152 would repay for the 127 already on disk -- about $30 of
container_cc spend to re-answer questions a human has already graded.

USE
    # deselect everything the prior run graded, leaving only the new variants
    python nessie_tests/scripts/delta_selection.py --graded nessie_bayes_full/grades.json

    # ... run the delta into its OWN directory (option B: no paid manifest touched)
    python -m nessie_tests --bayesian --base-url http://localhost:8000 \
        --user demo --password demopassword \
        --out ./nessie_bayes_delta --max-usd 12

    # then put the corpus back. The delta state is a RUN-TIME narrowing, not a
    # thing to commit: the committed corpus is the whole 152-variant study.
    git checkout nessie_tests/corpus.json

The narrowing is written to corpus.json in place because that is the only path
`--bayesian` reads. `git checkout` is the undo, which is why this refuses to run
against a dirty corpus.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus.json"


def _corpus_is_clean() -> bool:
    out = subprocess.run(["git", "status", "--porcelain", "--", str(CORPUS)],
                         capture_output=True, text=True, cwd=CORPUS.parents[1])
    return out.returncode == 0 and not out.stdout.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graded", type=pathlib.Path, required=True,
                    help="grades.json from the prior run; its keys are <variant_id>::<arm>")
    ap.add_argument("--force", action="store_true",
                    help="narrow even if corpus.json already has uncommitted changes")
    a = ap.parse_args(argv)

    if not a.force and not _corpus_is_clean():
        sys.exit(f"{CORPUS} has uncommitted changes. `git checkout` is the only undo "
                 f"for this narrowing, so refusing to bury an edit under it. Commit or "
                 f"discard first, or pass --force if you know what is there.")

    graded = {k.rsplit("::", 1)[0] for k in json.loads(a.graded.read_text())}
    corpus = json.loads(CORPUS.read_text())

    before, deselected, kept = 0, [], []
    for body in corpus["families"].values():
        for v in body.get("variants", []):
            if not (v.get("is_bayesian") and v.get("status") == "active"):
                continue
            before += 1
            if v["id"] in graded:
                v["is_bayesian"] = False
                deselected.append(v["id"])
            else:
                kept.append(v["id"])

    if not kept:
        sys.exit("nothing left to run: every selected variant is already graded.")

    unmatched = graded - set(deselected)
    CORPUS.write_text(json.dumps(corpus, indent=2) + "\n")

    print(f"selection {before} -> {len(kept)}")
    print(f"  deselected (already graded): {len(deselected)}")
    print(f"  left to run:                 {len(kept)}  = {len(kept) * 2} arms")
    if unmatched:
        # Not an error: a prior run can hold grades for variants since retired or
        # renamed. Loud because a LARGE number means the grades and this corpus
        # are not describing the same study.
        print(f"  NOTE: {len(unmatched)} graded ids are not in the current selection "
              f"(retired, renamed, or already deselected)")
    print()
    print("restore with:  git checkout nessie_tests/corpus.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
