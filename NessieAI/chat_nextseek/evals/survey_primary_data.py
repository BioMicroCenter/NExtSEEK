#!/usr/bin/env python3
"""Survey File_PrimaryData across every ground-truth cohort's sampled D.SEQ
records, so 'primary data is an accession, not a runnable file' is a
data-derived label rather than a hand-pick of 220720BRY.

Reports, per cohort, the distinct File_PrimaryData values on the D.SEQ
samples the eval actually shows the model (the first SAMPLE_CAP UIDs sorted
lexicographically), plus LibrarySource/LibraryStrategy/SequencingType.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from chat_nextseek.config import ChatConfig
from chat_nextseek.reports.metadata import fetch_reporter_metadata

SAMPLE_CAP = 10
GT = Path("/app/chat_nextseek/evals/groundtruth_cohorts.json")

# A primary-data value that no nf-core pipeline can consume: a bare archive
# accession (GEO sample/series, SRA run/experiment) rather than a file.
ACCESSION_RE = re.compile(r"^(GSM\d+|GSE\d+|SRR\d+|SRX\d+|SRS\d+|ERR\d+|PRJ[A-Z]+\d+)$")
FASTQ_RE = re.compile(r"\.(fastq|fq)(\.gz)?$", re.I)


def classify(value: str | None) -> str:
    if not value:
        return "empty"
    v = value.strip()
    if ACCESSION_RE.match(v):
        return "accession"
    if FASTQ_RE.search(v):
        return "fastq"
    return "other"


def main() -> int:
    cohorts = json.loads(GT.read_text())
    cfg = ChatConfig()
    rows = []
    for c in cohorts:
        uids = sorted(c["uids"])[:SAMPLE_CAP]
        m = fetch_reporter_metadata(cfg, uids)
        wanted = set(uids)
        vals, kinds, srcs, strats, seqs = [], Counter(), set(), set(), set()
        for blk in m["data"]["data"]:
            if blk["sample_type"] != "D.SEQ":
                continue
            for s in blk["samples"]:
                md = s["metadata"]
                if md.get("UID") not in wanted:
                    continue
                fp = md.get("File_PrimaryData")
                vals.append(fp)
                kinds[classify(fp)] += 1
                srcs.add(md.get("LibrarySource"))
                strats.add(md.get("LibraryStrategy"))
                seqs.add(md.get("SequencingType"))
        rows.append({
            "study": c["study"],
            "analysis": c["analysis"],
            "n_sampled": len(uids),
            "n_matched": sum(kinds.values()),
            "kinds": dict(kinds),
            "examples": vals[:3],
            "LibrarySource": sorted(x for x in srcs if x),
            "LibraryStrategy": sorted(x for x in strats if x),
            "SequencingType": sorted(x for x in seqs if x),
        })
        print(
            f"{c['study']:<12} {c['analysis']:<16} matched={sum(kinds.values()):>2}/{len(uids):<2} "
            f"kinds={dict(kinds)} src={sorted(x for x in srcs if x)} "
            f"strat={sorted(x for x in strats if x)} ex={vals[:2]}",
            file=sys.stderr, flush=True,
        )
    out = Path("/app/chat_nextseek/evals/demo-output-datafit/primary_data_survey.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2) + "\n")
    print(f"\nwritten: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
