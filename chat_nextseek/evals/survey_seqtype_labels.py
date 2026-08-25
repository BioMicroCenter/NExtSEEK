#!/usr/bin/env python3
"""Cross-tab `SequencingType` against the library name for the fibroblast
cohort's raw-read records, to settle whether a suspicious label split is real.

## Why this exists

`build_input_inventory` (pipeline/sample_digest.py) reports what a cohort's
records point at. For fibroblast-subtypes it reports 114 raw-read records
split `Single Cell TCR: 108` / `Single Cell RNAseq: 6`, i.e. only 5% of the
raw reads are gene expression. Three things contradict that:

  - 10x 5' experiments pair GEX and TCR libraries roughly 1:1.
  - The cohort's own `Name` values alternate: BTC-GBM-001-001-GEX,
    BTC-GBM-001-001-TCR, BTC-GBM-001-002-GEX.
  - Every example filename the inventory picked up is a `-GEX` FASTQ.

If `SequencingType` is mis-entered on ~100 production records then the
inventory is faithfully reporting a curation defect — and, because it states
that split prominently, arguing the model out of naming scrnaseq. That is
worth knowing before either the metadata or the selection payload is touched.

This script does not guess. It reads both `Name` and `File_PrimaryData` and
prints the cross-tab, so a disagreement between the label and the library
identity is visible as a cell in a table rather than an inference.

## What it prints

For the 12 UIDs the digest was actually built from (reproducing exactly the
record set the model saw) and then for the full 453-UID cohort. Shape of the
output, with INVENTED numbers — this is a layout example, not a result:

    SequencingType             GEX       TCR     total
    Single Cell RNAseq           6         0         6
    Single Cell TCR             51        57       108      <- 51 disagreements

A clean diagonal means the split is real and the cohort genuinely is
TCR-heavy. Off-diagonal counts are records whose label disagrees with their
own library name, and the `--examples` flag prints them by UID so a curator
can fix them at source.

Runs on the HOST, like survey_prod_protocols.py and build_prod_digests.py:
production rejects the container's `demo` credentials, and the password stays
here rather than crossing into the container.

    cd chat_nextseek && uv run python evals/survey_seqtype_labels.py
"""
from __future__ import annotations
import os

import argparse
import base64
import collections
import functools
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

import certifi

EVALS_DIR = Path(__file__).resolve().parent
BASE = "https://nextseek.mit.edu"
ENV_PATH = Path(os.environ.get("NEXTSEEK_EVAL_ENV_FILE", ".env"))
QUESTIONS = EVALS_DIR / "team_questions.json"
DIGESTS = EVALS_DIR / "prod_digests.json"
OUT = EVALS_DIR / "demo-output-team-inv" / "seqtype_label_survey.json"
BATCH = 50
CTX = ssl.create_default_context(cafile=certifi.where())

#: Library-identity suffixes carried in `Name` (BTC-GBM-001-001-GEX). These are
#: the 10x library kinds this cohort was built from; anything else is bucketed
#: as "other" and shown rather than silently folded into one of them.
NAME_SUFFIXES = ("GEX", "TCR", "BCR", "ADT", "HTO")


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


@functools.cache
def auth_header() -> str:
    """Read the credential on first use, not at import.

    survey_prod_protocols.py builds this at module level, which means importing
    it at all requires the .env to be present and readable. Deferring it keeps
    the pure helpers below importable and testable on a machine that has no
    production credentials at all.
    """
    env = load_env()
    return "Basic " + base64.b64encode(
        f"{env['NEXTSEEK_USERNAME']}:{env['NEXTSEEK_PASSWORD']}".encode()
    ).decode()


def retrieve(uids: list[str]) -> dict | None:
    url = f"{BASE}/nextseek_api/admin/samples/retrieve/?page_size=1000"
    req = urllib.request.Request(
        url, data=json.dumps({"identifiers": uids}).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": auth_header()},
    )
    try:
        with urllib.request.urlopen(req, timeout=180, context=CTX) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code}: {e.read()[:160].decode('utf-8', 'replace')}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001 - live network, report and continue
        print(f"    {type(e).__name__}: {e}", file=sys.stderr)
        return None


def library_kind(*values: object) -> str:
    """The library kind a record's own strings say it is.

    `Name` is checked first, then the primary-data filenames — a record whose
    name is blank still names its libraries in `BTC-GBM-001-001-GEX_S1_...`.
    Returns "unlabelled" when nothing says.
    """
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        upper = value.upper()
        hits = {s for s in NAME_SUFFIXES if f"-{s}" in upper or f"_{s}_" in upper}
        if len(hits) == 1:
            return hits.pop()
        if len(hits) > 1:
            return "mixed:" + "+".join(sorted(hits))
    return "unlabelled"


def collect(uids: list[str]) -> tuple[dict, list[dict]]:
    """Return (cross-tab, per-record rows) for the D.SEQ records in `uids`'
    lineage. Keyed by UID, so a record reached from two queried samples is
    counted once — the same basis build_input_inventory counts on."""
    seen: dict[str, dict] = {}
    for i in range(0, len(uids), BATCH):
        body = retrieve(uids[i:i + BATCH])
        if not body:
            continue
        for blk in body.get("data") or []:
            if (blk.get("sample_type") or "") != "D.SEQ":
                continue
            for s in blk.get("samples") or []:
                md = s.get("metadata") or {}
                uid = md.get("UID") or s.get("uuid")
                if uid:
                    seen[uid] = md

    table: dict[str, collections.Counter] = {}
    rows: list[dict] = []
    for uid, md in sorted(seen.items()):
        seq_type = (md.get("SequencingType") or "").strip() or "(not recorded)"
        kind = library_kind(md.get("Name"), md.get("File_PrimaryData"))
        table.setdefault(seq_type, collections.Counter())[kind] += 1
        rows.append({"uid": uid, "sequencing_type": seq_type, "library_kind": kind,
                     "name": md.get("Name"),
                     "file": (md.get("File_PrimaryData") or "")[:60]})
    return {k: dict(v) for k, v in sorted(table.items())}, rows


def agrees(seq_type: str, kind: str) -> bool:
    """Does the label agree with the library the record names?

    Only a label that itself names a library kind can disagree with one. Other
    cohorts spell SequencingType "ShortRead" or "Short Read Sequencing", which
    describe the platform and say nothing about GEX versus TCR — counting those
    as disagreements would manufacture a defect out of a vocabulary difference.
    """
    upper = seq_type.upper()
    squashed = upper.replace(" ", "").replace("-", "")
    names_expression = "RNASEQ" in squashed
    names_receptor = {k for k in ("TCR", "BCR") if k in upper}
    if not names_expression and not names_receptor:
        return True  # uninformative label, nothing to contradict

    if kind == "GEX":
        return names_expression and not names_receptor
    if kind in ("TCR", "BCR"):
        return kind in names_receptor
    return True  # unlabelled library, nothing to compare against


def report(label: str, uids: list[str]) -> dict:
    print(f"\n=== {label}: {len(uids)} UID(s) queried ===")
    table, rows = collect(uids)
    kinds = sorted({k for counts in table.values() for k in counts})
    if not table:
        print("  no D.SEQ records returned")
        return {"label": label, "n_uids": len(uids), "cross_tab": {}, "n_disagreements": 0}

    header = f"  {'SequencingType':<24}" + "".join(f"{k:>10}" for k in kinds) + f"{'total':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for seq_type, counts in table.items():
        line = f"  {seq_type:<24}" + "".join(f"{counts.get(k, 0):>10}" for k in kinds)
        print(line + f"{sum(counts.values()):>10}")

    disagreements = [r for r in rows if not agrees(r["sequencing_type"], r["library_kind"])]
    print(f"\n  {len(disagreements)} of {len(rows)} record(s) carry a SequencingType that "
          f"disagrees with their own library name.")
    return {"label": label, "n_uids": len(uids), "n_records": len(rows),
            "cross_tab": table, "n_disagreements": len(disagreements),
            "disagreements": disagreements}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--question", default="fibroblast-subtypes")
    parser.add_argument("--examples", type=int, default=5,
                        help="How many disagreeing records to print by UID (0 to suppress)")
    args = parser.parse_args()

    question = next(
        (q for q in json.loads(QUESTIONS.read_text()) if q["id"] == args.question), None
    )
    if question is None:
        parser.error(f"no such question id: {args.question}")

    digest_entry = next(
        (e for e in json.loads(DIGESTS.read_text()) if e["id"] == args.question), {}
    )
    digest_uids = digest_entry.get("uids_used") or []

    out: list[dict] = []
    if digest_uids:
        out.append(report("the 12 UIDs the digest was built from", digest_uids))
    out.append(report("the full cohort", question["uids"]))

    if args.examples:
        for entry in out:
            for row in (entry.get("disagreements") or [])[: args.examples]:
                print(f"    {row['uid']:<22} SequencingType={row['sequencing_type']!r} "
                      f"but Name={row['name']!r}")
            break

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwritten: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
