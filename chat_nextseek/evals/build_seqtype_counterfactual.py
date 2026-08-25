#!/usr/bin/env python3
"""Write a COUNTERFACTUAL copy of prod_digests.json in which the confirmed
`SequencingType` mislabelling is corrected, so the defect's effect on pipeline
selection can be measured instead of assumed.

## The defect

survey_seqtype_labels.py established, against production, that the
fibroblast-subtypes cohort's 108 records labelled `Single Cell TCR` are in
fact 54 gene-expression libraries and 54 TCR libraries — a textbook 10x 5'
1:1 pairing, with the GEX half carrying the wrong label:

    SequencingType            GEX    TCR   unlabelled
    Single Cell RNAseq          0      0            6
    Single Cell TCR            54     54            0     <- 54 disagreements

So the cohort holds 60 gene-expression libraries (54 GEX-named + 6 already
labelled `Single Cell RNAseq`), not 6. build_input_inventory reports 6,
because that is what the metadata says.

## Why a counterfactual rather than a fix

The metadata is wrong in NExtSEEK, and that is where it should be corrected —
not in the digest, and not by teaching the selection payload to second-guess a
field. But waiting for a curation fix leaves an open question this can answer
now: is the mislabelling what stops the model naming scrnaseq, or is the
refusal independent of it?

So this writes a SEPARATE file. prod_digests.json is never modified, and the
output name says what it is. Nothing downstream should read the counterfactual
except the A/B run it exists for.

## What it changes, exactly

Only `input_data_inventory.raw_reads.by_sequencing_type`, recomputed from the
survey's cross-tab by relabelling each cell from the library kind the record's
own `Name`/`File_PrimaryData` declares. Deliberately NOT added: any note saying
the digest was corrected. A note is a payload difference of its own, and the
measurement is meant to isolate the counts.

    cd chat_nextseek && uv run python evals/build_seqtype_counterfactual.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
DIGESTS = EVALS_DIR / "prod_digests.json"
SURVEY = EVALS_DIR / "demo-output-team-inv" / "seqtype_label_survey.json"
OUT = EVALS_DIR / "prod_digests_seqtype_counterfactual.json"

#: The SequencingType a record would carry if its label matched the library its
#: own name declares. Kinds absent here (unlabelled, ADT, HTO ...) keep whatever
#: label they already have — this corrects a known confusion, it does not
#: re-curate the field wholesale.
KIND_TO_LABEL = {
    "GEX": "Single Cell RNAseq",
    "TCR": "Single Cell TCR",
    "BCR": "Single Cell BCR",
}


def corrected_counts(cross_tab: dict[str, dict[str, int]]) -> dict[str, int]:
    """Fold a {label: {library_kind: n}} cross-tab into corrected label counts."""
    out: dict[str, int] = {}
    for label, by_kind in cross_tab.items():
        for kind, n in by_kind.items():
            out[KIND_TO_LABEL.get(kind, label)] = out.get(KIND_TO_LABEL.get(kind, label), 0) + n
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--question", default="fibroblast-subtypes")
    args = parser.parse_args()

    digests = json.loads(DIGESTS.read_text())
    survey = json.loads(SURVEY.read_text())

    # The full-cohort pass is the authority; the 12-UID pass is a subset of it.
    entry = survey[-1]
    cross_tab = entry["cross_tab"]
    if not cross_tab:
        print("survey has an empty cross-tab; nothing to correct")
        return 1

    target = next((d for d in digests if d["id"] == args.question), None)
    if target is None:
        parser.error(f"no such question id in prod_digests.json: {args.question}")

    raw = target["digest"]["input_data_inventory"]["raw_reads"]
    before = dict(raw["by_sequencing_type"])
    after = corrected_counts(cross_tab)

    if sum(after.values()) != sum(before.values()):
        # A mismatch means the survey and the digest counted different record
        # sets, and the counterfactual would be comparing two different cohorts.
        print(f"REFUSING: survey covers {sum(after.values())} records but the digest's "
              f"raw_reads holds {sum(before.values())}. These must match.")
        return 1

    raw["by_sequencing_type"] = after
    OUT.write_text(json.dumps(digests, indent=1) + "\n")

    print(f"question:      {args.question}")
    print(f"  before:      {before}")
    print(f"  after:       {after}")
    print(f"  disagreeing: {entry['n_disagreements']} record(s) relabelled from their library name")
    print(f"\nwritten: {OUT}")
    print(f"unchanged: {DIGESTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
