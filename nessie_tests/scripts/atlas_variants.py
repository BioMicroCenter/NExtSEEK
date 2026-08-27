#!/usr/bin/env python3
"""Turn the atlas's expressible capability assertions into corpus variants.

Reads FAMILIES.json and emits one variant per capability whose `expressible` is
"yes" -- meaning an agent wrote it while holding the closed corpus vocabulary and
produced a structured `criterion` plus an `example_query`. The other four states
are deliberately skipped and belong in the gap document, not the corpus:

  partial (49)         the observable is only half reachable
  no (84)              nothing in the vocabulary reaches it
  needs_criterion (29) prose names a real field but no criterion was written
  unit_lane (93)       reads as a pytest assertion over internal state
                       (`len(session['chat_log']) == 50`), not an HTTP criterion

WHY is_bayesian IS FALSE ON ALL OF THEM. These questions are machine-authored and
unreviewed. The 2026-07-30 review retired 100 variants, most for being bad
questions or near-duplicates, so putting 111 fresh ones straight into a paid
paired run would repeat exactly that mistake at cost. They run in the free route
tier and in a full tier immediately; they enter `--bayesian` only when someone
has read them.

`origin: "atlas"` becomes a tag through `corpus._to_variants`, so every one of
these is greppable and reversible as a set.

--dry-run prints the plan. --check reports near-duplicate queries against the
existing corpus and exits without writing.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import pathlib
import re
import sys

NT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = NT / "corpus.json"
FAMILIES = NT / "FAMILIES.json"

# Existing id prefixes, so a new variant sorts next to its neighbours rather than
# announcing itself. Families with no incumbent get a new prefix.
PREFIX = {
    "sample_search": "advanced", "sample_retrieve": "retrieve", "catalog_browse": "sys",
    "graph_traversal": "graph", "lineage_tree": "tree", "vocabulary_resolution": "entity",
    "system_capability_question": "sys", "followup_over_results": "refrec",
    "search_refinement": "refrec", "retrieval_path_selection": "path",
    "project_summary_report": "report", "submission_package": "report",
    "artifact_delivery": "artifact", "pipeline_launch": "pipeline",
    "pipeline_output_reingest": "reingest", "batch_upload_preparation": "batch",
    "harmonization": "harmon", "entity_write": "write", "writes_unsupported": "write",
    "engine_routing": "route", "route_overrides": "override",
    "cross_session_memory": "memory", "session_lifecycle": "session",
    "turn_limits_and_failure": "limits", "turn_delivery_and_trace": "delivery",
    "cc_sandbox_contract": "sandbox", "unsupported": "unsup",
    "turn_evaluation_and_retry": "eval",
}

SLUG_LEN = 32


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:SLUG_LEN].rstrip("_")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true", help="report near-duplicates and exit")
    a = ap.parse_args()

    payload = json.loads(CORPUS.read_text())
    fams = json.loads(FAMILIES.read_text())

    existing_ids = {v["id"] for b in payload["families"].values() for v in b["variants"]}
    existing_q = {v["turns"][0]["query"]: v["id"]
                  for b in payload["families"].values() for v in b["variants"] if v["turns"]}

    ready = [(f["family"], c) for f in fams["families"] for g in f["groups"]
             for c in g["capabilities"] if c.get("expressible") == "yes"]

    # Near-duplicate report. The corpus retired 100 variants on 2026-07-30, most of
    # them near-duplicates, so an atlas question that merely restates an existing
    # one is a regression rather than coverage.
    dupes = []
    for fam, c in ready:
        q = (c.get("example_query") or "").strip()
        if not q:
            continue
        for eq, eid in existing_q.items():
            r = difflib.SequenceMatcher(None, q.lower(), eq.lower()).ratio()
            if r >= 0.80:
                dupes.append((round(r, 2), fam, q, eid, eq))
    dupes.sort(reverse=True)
    if dupes:
        print(f"NEAR-DUPLICATE queries (>=0.80 similar to an existing variant): {len(dupes)}")
        for r, fam, q, eid, eq in dupes[:20]:
            print(f"  {r}  [{fam}] {q[:64]}\n        vs {eid}: {eq[:64]}")
        print()
    if a.check:
        return 0

    dupe_q = {d[2] for d in dupes}
    made, skipped, buckets = [], 0, collections.defaultdict(list)
    for fam, c in ready:
        q = (c.get("example_query") or "").strip()
        crit = c.get("criterion")
        if not q or not crit or not crit.get("field"):
            skipped += 1
            continue
        if q in dupe_q:
            skipped += 1
            continue
        vid = f"{PREFIX.get(fam, 'atlas')}.{slug(q)}"
        n = 2
        while vid in existing_ids:
            vid = f"{PREFIX.get(fam, 'atlas')}.{slug(q)}_{n}"
            n += 1
        existing_ids.add(vid)
        pc = {"field": crit["field"], "op": crit["op"], "value": crit.get("value")}

        # Per-variant HiBayes overrides, mirroring the convention the hand-authored
        # deposits already follow. `submission_package` defaults to Report-GEO, so an
        # SRA- or PRIDE-phrased variant left on the default is mislabelled -- exactly
        # what `test_every_reporting_deposit_variant_overrides_its_family_default`
        # exists to catch. And a question whose criterion demands a clarifying reply
        # must say so, or it scores as a failure for doing the right thing.
        ov = {}
        ql = q.upper()
        if fam == "submission_package":
            for token, (st, kind) in {
                "GEO": ("Report-GEO", "GEO_XLSX"),
                "SRA": ("Report-SRA", "SRA_PACKAGE"),
                "PRIDE": ("Report-PRIDE", "PRIDE_PACKAGE"),
            }.items():
                if token in ql:
                    ov = {"hibayes_subtype": st, "expected_behavior": "GenerateArtifact",
                          "artifact_expected": True, "artifact_kind": kind}
                    break
        asks = re.search(r"(?i)\b(which|what) .*(should i|do you (mean|want))|ambiguous|clarif",
                         str(crit.get("value") or "") + " " + c["testable_assertion"])
        if asks and not ov:
            ov = {"expected_behavior": "ClarifyIfAmbiguous",
                  "artifact_expected": False, "artifact_kind": "NONE_EXPECTED"}
        buckets[fam].append({
            "id": vid,
            "family": fam,
            "name": c["name"],
            "tags": ["nessie", "full", "atlas"],
            "requires_env": [],
            "turns": [{"label": "main", "query": q, "pass_criteria": [pc]}],
            "status": "active",
            "origin": "atlas",
            "is_bayesian": False,
            "hibayes_subtype": ov.get("hibayes_subtype"),
            "expected_behavior": ov.get("expected_behavior"),
            "artifact_expected": ov.get("artifact_expected"),
            "artifact_kind": ov.get("artifact_kind"),
            "retirement": None,
            # ONE annotation key, not two. `test_the_hand_written_annotations_survived_adoption`
            # counts `_`-prefixed keys to notice if the 37 hand-written `_why` notes ever
            # vanish; 79 machine keys spread over two names would drown that signal.
            "_atlas": {"capability": c["name"], "assertion": c["testable_assertion"]},
        })
        made.append(vid)

    print(f"{'family':30} {'new':>4} {'existing':>9}")
    for fam in payload["families"]:
        n = len(buckets.get(fam, []))
        if n:
            print(f"{fam:30} {n:4} {len(payload['families'][fam]['variants']):9}")
    print(f"\nnew variants: {len(made)}   skipped (no query/criterion or duplicate): {skipped}")

    if a.dry_run:
        print("\nDRY RUN, nothing written")
        return 0

    for fam, vs in buckets.items():
        payload["families"][fam]["variants"].extend(vs)
    CORPUS.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"\nwrote {CORPUS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
