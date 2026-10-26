"""The supplementary zip → evidence items, and gaps for what could not be read.

Workbooks and csv/tsv give one item per non-empty cell; PDF and txt give paragraphs.
Anything else, anything over the size cap, and anything the paper lists but the zip
lacks becomes a gap (spec §10: a missing supplement never stops a run). An unreadable
archive, or a readable file with no text, is a gap too. Evidence IDs stay unique: a
repeated basename is keyed by its folder-qualified path instead.
"""
from __future__ import annotations

import csv
import io
import os
import zipfile

from NessieAI.ns.paper.models import EvidenceItem, Gap, SupplementRef
from NessieAI.ns.paper.pdftext import chunk_paragraphs, pdf_to_text

MAX_MEMBER_BYTES = 50 * 1024 * 1024
MAX_ITEMS_PER_FILE = 50_000
_DELIMITED = {".csv": ",", ".tsv": "\t"}


def supplement_items(zip_bytes: bytes, expected: list[SupplementRef]) -> tuple[list[EvidenceItem], list[Gap]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        return [], [Gap(kind="supplement_unparsed", detail=f"supplementary archive: {type(exc).__name__}")] + [
            Gap(kind="supplement_unavailable", detail=s.name) for s in expected]
    items: list[EvidenceItem] = []
    gaps: list[Gap] = []
    found: set[str] = set()
    seen: set[str] = set()
    with zf:
        for info in zf.infolist():
            base = info.filename.rsplit("/", 1)[-1]
            if info.is_dir() or not base or base.startswith("."):
                continue
            found.add(base)
            # The first member with a basename keeps it; a repeat is keyed by its path in the zip.
            name = info.filename if base in seen else base
            seen.add(base)
            if info.file_size > MAX_MEMBER_BYTES:
                gaps.append(Gap(kind="supplement_unparsed", detail=f"{name}: over {MAX_MEMBER_BYTES // 2**20} MB"))
                continue
            ext = os.path.splitext(base)[1].lower()
            try:
                data = zf.read(info)
                if ext in (".xlsx", ".xlsm"):
                    new = _workbook(name, data)
                elif ext in _DELIMITED:
                    new = _delimited(name, data, _DELIMITED[ext])
                elif ext == ".pdf":
                    new = _prose(name, pdf_to_text(data))
                elif ext == ".txt":
                    new = _prose(name, data.decode("utf-8", errors="replace"))
                else:
                    gaps.append(Gap(kind="supplement_unparsed", detail=f"{name}: {ext or 'no extension'} is not read"))
                    continue
            except Exception as exc:  # a corrupt member is a gap, never a failed run
                gaps.append(Gap(kind="supplement_unparsed", detail=f"{name}: {type(exc).__name__}"))
                continue
            if not new:
                gaps.append(Gap(kind="supplement_unparsed", detail=f"{name}: no readable text"))
                continue
            if len(new) > MAX_ITEMS_PER_FILE:
                gaps.append(Gap(kind="supplement_unparsed", detail=f"{name}: only the first {MAX_ITEMS_PER_FILE} cells read"))
                new = new[:MAX_ITEMS_PER_FILE]
            items += new
    gaps += [Gap(kind="supplement_unavailable", detail=s.name) for s in expected if s.name not in found]
    return items, gaps


def _workbook(name: str, data: bytes) -> list[EvidenceItem]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[EvidenceItem] = []
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is None or not str(cell.value).strip():
                        continue
                    out.append(EvidenceItem(id=f"E:si:{name}!{ws.title}!{cell.coordinate}", text=str(cell.value).strip(),
                                            source=f"SI {name}, sheet {ws.title}, cell {cell.coordinate}"))
                    if len(out) > MAX_ITEMS_PER_FILE:
                        return out
    finally:
        wb.close()
    return out


def _delimited(name: str, data: bytes, sep: str) -> list[EvidenceItem]:
    from openpyxl.utils import get_column_letter

    out: list[EvidenceItem] = []
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace")), delimiter=sep)
    for r, row in enumerate(reader, start=1):
        for c, value in enumerate(row, start=1):
            if value.strip():
                coord = f"{get_column_letter(c)}{r}"
                out.append(EvidenceItem(id=f"E:si:{name}!{coord}", text=value.strip(), source=f"SI {name}, cell {coord}"))
                if len(out) > MAX_ITEMS_PER_FILE:
                    return out
    return out


def _prose(name: str, text: str) -> list[EvidenceItem]:
    return [EvidenceItem(id=f"E:si:{name}!p{i}", text=chunk, source=f"SI {name}, paragraph {i}")
            for i, chunk in enumerate(chunk_paragraphs(text.splitlines()), start=1)]
