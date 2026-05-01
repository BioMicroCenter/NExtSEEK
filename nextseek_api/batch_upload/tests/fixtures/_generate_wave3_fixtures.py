"""Generate the two Wave 3 integration-test xlsx fixtures from InputRowModel.

Every row is constructed as a dict, validated through InputRowModel before
write, so the fixture is guaranteed to match the actual pipeline contract
rather than a hand-authored shape.

Contract points enforced:
- json_metadata is written with "UID" as the first key, matching the UID column.
  When both are empty, UID is serialized as null.
- assay_ids is always [401] (matching the seeded assay in the integration fixture).
- Column set matches orchestrator expectations: UID, SampleType, json_metadata,
  assay_ids, study_id, study_title.

Run:
    /opt/NExtSEEK/.venv/bin/python nextseek_api/batch_upload/tests/fixtures/_generate_wave3_fixtures.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import OrderedDict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")

import django
django.setup()

import openpyxl

from nextseek_api.batch_upload.models import InputRowModel


STUDY_ID = 701
STUDY_TITLE = "Wave 3 Integration Study"
ASSAY_IDS = [401]
COLUMNS = ("UID", "SampleType", "json_metadata", "assay_ids", "study_id", "study_title")


def _oversized_fastq_identity(n_files: int = 20) -> str:
    parts = [
        f"GBM1-DFCI4-S1to6-CSFCells-1_VDJ_IGO_16516_B_{i}_S{i+12}_L001_I1_001.fastq.gz"
        for i in range(1, n_files + 1)
    ]
    return "; ".join(parts)


OVERSIZED_IDENTITY = _oversized_fastq_identity()


def _meta(uid: str | None, extras: dict | None = None) -> str:
    """Build a json_metadata string with UID always the first key."""
    out: "OrderedDict[str, object]" = OrderedDict()
    out["UID"] = uid  # first key; None when empty
    if extras:
        for k, v in extras.items():
            if k == "UID":
                continue
            out[k] = v
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def _row(uid: str | None, sample_type: str, meta_extras: dict | None) -> dict:
    return {
        "UID": uid,
        "SampleType": sample_type,
        "json_metadata": _meta(uid, meta_extras),
        "assay_ids": ",".join(str(x) for x in ASSAY_IDS),
        "study_id": STUDY_ID,
        "study_title": STUDY_TITLE,
    }


def _validate(rows: list[dict]) -> None:
    """Validate each row against InputRowModel. Raises on any shape violation."""
    for i, r in enumerate(rows):
        try:
            InputRowModel(**r)
        except Exception as exc:
            raise AssertionError(f"Row {i} fails InputRowModel validation: {r!r}\n{exc}") from exc


def _write_xlsx(path: Path, rows: list[dict]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(list(COLUMNS))
    for r in rows:
        ws.append([r[c] for c in COLUMNS])
    wb.save(path)


def build_default_mode() -> list[dict]:
    """Default mode: new uploads that collide / reference seeded historical rows."""
    return [
        # 1. drift-a (UID column empty, json Name=drift-a → should skip "name_already_exists" and map to seeded WTNAM-240301BMC-1)
        _row(None, "WTNAM_blood", {"Name": "drift-a"}),
        # 2. drift-b (seeded with title='Undefined', json Name=drift-b)
        _row(None, "WTNAM_blood", {"Name": "drift-b"}),
        # 3. drift-c (seeded with clean title+Name)
        _row(None, "WTNAM_blood", {"Name": "drift-c"}),
        # 4. drift-w (seeded with title=UID from prior pre-fix upsert, json Name=drift-w)
        _row(None, "WTNAM_blood", {"Name": "drift-w"}),
        # 5. drift-d.fastq (file-based seeded row)
        _row(None, "D.WTFIL_files", {"File_PrimaryData": "drift-d.fastq"}),
        # 6. UID-only row (UID column set, json_metadata only has UID — matches seeded WTNAM-240301BMC-5)
        _row("WTNAM-240301BMC-5", "WTNAM_blood", None),
        # 7. child-ambiguous — Parent="dup" matches two seeded rows (WTNAM-240301BMC-6 and -7) → ambiguity failure
        _row(None, "WTNAM_blood", {"Name": "child-ambiguous", "Parent": "dup"}),
        # 8. orphan-child — Parent="missing-parent" references nothing seeded → orphan preservation
        _row(None, "WTNAM_blood", {"Name": "orphan-child", "Parent": "missing-parent"}),
        # 9. child-by-name — Parent="drift-a" resolves to seeded WTNAM-240301BMC-1 (DD-12 ambiguity-free)
        _row(None, "WTNAM_blood", {"Name": "child-by-name", "Parent": "drift-a"}),
        # 10. child-by-file — Parent="drift-d.fastq" resolves to seeded D.WTFIL-240301BMC-1 (file-based identity)
        _row(None, "WTNAM_blood", {"Name": "child-by-file", "Parent": "drift-d.fastq"}),
        # 11. child-of-oversized resolves through the full long File_PrimaryData token
        _row(None, "WTNAM_blood", {"Name": "child-of-oversized", "Parent": OVERSIZED_IDENTITY}),
        # 12. future-parent — used by orphan_resolution test (the pre-seeded orphan references this name)
        _row(None, "WTNAM_blood", {"Name": "future-parent"}),
    ]


def build_update_mode() -> list[dict]:
    """Update_existing mode: re-upload existing rows with canonicalized keys / new scalar fields.

    Update rows populate both the UID column and json_metadata["UID"] with the target
    sample's UID — this is the primary contract for updates. Rows intended as NEW
    inserts (ambiguity-failure; future-parent for orphan resolution) use UID=None.
    """
    return [
        # 1. drift-a update — explicit UID targets seeded WTNAM-240301BMC-1
        _row("WTNAM-240301BMC-1", "WTNAM_blood", {"Name": "drift-a", "Scientist": "Updated Scientist"}),
        # 2. drift-d.fastq update — explicit UID targets seeded D.WTFIL-240301BMC-1; uses TYPO spelling to test canonicalization
        _row("D.WTFIL-240301BMC-1", "D.WTFIL_files", {"File_PrimartyData": "drift-d.fastq", "Scientist": "Updated File Scientist"}),
        # 3. UID-only update — explicit UID; no Name/File_PrimaryData key
        _row("WTNAM-240301BMC-5", "WTNAM_blood", {"Scientist": "UID Only Update"}),
        # 4. child-ambiguous-update — NEW row intentionally failing ambiguity via Parent="dup"; UID=None is correct
        _row(None, "WTNAM_blood", {"Name": "child-ambiguous-update", "Parent": "dup"}),
        # 5. child-of-oversized resolves through the full long File_PrimaryData token
        _row(None, "WTNAM_blood", {"Name": "child-of-oversized", "Parent": OVERSIZED_IDENTITY}),
        # 6. future-parent — NEW row that satisfies the pre-seeded orphan; UID=None is correct
        _row(None, "WTNAM_blood", {"Name": "future-parent"}),
    ]


def main() -> None:
    here = Path(__file__).resolve().parent
    default_rows = build_default_mode()
    update_rows = build_update_mode()

    _validate(default_rows)
    _validate(update_rows)

    _write_xlsx(here / "wave3_default_mode.xlsx", default_rows)
    _write_xlsx(here / "wave3_update_mode.xlsx", update_rows)

    print(f"Wrote default_mode: {len(default_rows)} rows")
    print(f"Wrote update_mode: {len(update_rows)} rows")


if __name__ == "__main__":
    main()
