# NessieAI/ns/reingest/workbook_diff.py
"""Is a rebuilt workbook the reviewed one plus Assay rows for new assays? (spec 3.5 step 3)

The upload creates a missing study assay, rebuilds, and uploads the rebuilt
workbook in place of the reviewed one only when nothing else changed. Pure:
openpyxl only.
"""
from __future__ import annotations

from collections import Counter

ASSAY_SHEET = "Assay"
ASSAY_ID_COLUMN = 2  # SampleType, AssayType, Assay, Direction


def _rows(book, name) -> list[tuple]:
    return [tuple(row) for row in book[name].iter_rows(values_only=True)]


def assay_only_changes(reviewed_path: str, rebuilt_path: str, created_ids) -> list[str]:
    import openpyxl

    allowed = {int(i) for i in created_ids}
    reviewed = openpyxl.load_workbook(reviewed_path, read_only=True, data_only=True)
    rebuilt = openpyxl.load_workbook(rebuilt_path, read_only=True, data_only=True)
    try:
        reasons: list[str] = []
        if reviewed.sheetnames != rebuilt.sheetnames:
            return [f"the sheets changed: {reviewed.sheetnames} became {rebuilt.sheetnames}"]
        for name in reviewed.sheetnames:
            before, after = _rows(reviewed, name), _rows(rebuilt, name)
            if name != ASSAY_SHEET:
                if before != after:
                    reasons.append(f"{name}: differs from the reviewed workbook")
                continue
            missing = Counter(before) - Counter(after)
            added = Counter(after) - Counter(before)
            if missing:
                reasons.append(f"{ASSAY_SHEET}: a reviewed row is gone")
            stray = [row for row in added
                     if len(row) <= ASSAY_ID_COLUMN or row[ASSAY_ID_COLUMN] not in allowed]
            if stray:
                reasons.append(f"{ASSAY_SHEET}: rows for assays this upload did not create")
        return reasons
    finally:
        reviewed.close()
        rebuilt.close()
