#!/usr/bin/env python3
"""Write upload workbooks for the local-only Nessie test cohorts.

  make_test_cohorts.py <out-dir> [<cohort> ...]

One folder per cohort, one workbook per level, numbered in upload order. Upload them in
that order on the LOCAL site's upload page, into the local project "Nessie test cohorts"
(a child names its parent by Name, which the upload resolves against the earlier
workbook's samples). Never upload these to dev or prod. Run inside the nextseek
container (it imports NessieAI.ns.upload_workbook, which needs openpyxl).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from NessieAI.ns.upload_workbook import render_upload_workbook

COHORTS = Path(__file__).resolve().parent / "cohorts"


def cohort_rows(definition: dict) -> dict[str, list[dict]]:
    return {level["sample_type"]: [{"json_metadata": dict(row), "assay_ids": []} for row in level["rows"]]
            for level in definition["levels"]}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    out = Path(argv[0])
    names = argv[1:] or sorted(p.stem for p in COHORTS.glob("*.json"))
    for name in names:
        definition = json.loads((COHORTS / f"{name}.json").read_text())
        folder = out / name
        folder.mkdir(parents=True, exist_ok=True)
        for n, (sample_type, rows) in enumerate(cohort_rows(definition).items(), 1):
            path = folder / f"{n}-{sample_type.replace('.', '_')}.xlsx"
            render_upload_workbook(sample_type, rows, str(path))
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
