"""Fold the labelling page's exported JSON into a labelled draft (JevLevROUTING, unit U4). No network, no database.

Usage: python -m scripts.laya.label_page.ingest DRAFT.jsonl heldout-labels.json OUT.jsonl
Labelled rows get route = the human's pick and truth_kind "human"; the rest keep their family default.
Then freeze_heldout.py freezes OUT.jsonl (operator's go).
"""
from __future__ import annotations

import json
import sys

from scripts.laya.draft_heldout import assert_outside_git

ROUTE = {"NS": "nextseek_query", "CC": "container_cc", "either": "either", "unrelated": "unrelated"}


def ingest(draft, export, out) -> int:
    assert_outside_git(out)
    rows = [json.loads(line) for line in open(draft) if line.strip()]
    labels = json.load(open(export))["labels"]
    unknown = set(labels) - {r["hash"] for r in rows}
    if unknown:
        raise ValueError(f"{len(unknown)} labelled hashes are not in the draft")
    bad = {v for v in labels.values() if v not in ROUTE}
    if bad:
        raise ValueError(f"unknown labels {sorted(bad)}")
    for r in rows:
        if r["hash"] in labels:
            r["route"], r["truth_kind"] = ROUTE[labels[r["hash"]]], "human"
    open(out, "w").write("".join(json.dumps(r) + "\n" for r in rows))
    return len(labels)


if __name__ == "__main__":
    print(ingest(*sys.argv[1:4]), "labels folded")
