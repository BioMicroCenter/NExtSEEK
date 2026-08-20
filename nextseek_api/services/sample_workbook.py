"""The single writer for sample-download workbooks.

Every sample download in NExtSEEK ends here, so the README sheet cannot drift
between the legacy `seek` views and the `nextseek_api` endpoint. See
docs/sample-download-workflow.md for how the call paths converge on this module.
"""

from __future__ import annotations

import logging
from typing import Iterable, Mapping

import pandas as pd
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

from seek.models import Sample_fields_context, Sample_types_context

logger = logging.getLogger(__name__)

CONTEXTDB_URL = (
    "https://github.com/BioMicroCenter/NExtSEEK/blob/main/"
    "chat_nextseek/src/chat_nextseek/context/sampletypes_db.json"
)

README_SHEET = "README"
README_LINK_TEXT = "Sample type definitions: sampletypes_db.json (GitHub)"

# Sample UIDs lead with the sample-type code: "MUS-230101ABC-1", "D.SEQ-240910LAU-3".
# The dotted alternative must come first or "D.SEQ" truncates to "D".
SAMPLE_TYPE_RE = r"([A-Z]+\.[A-Z]+|[A-Z]+)"


COLUMN_TABLE_HEADER = ["Column", "Meaning"]

# Excel's hard per-cell limit. openpyxl does not enforce it: a longer value is
# written happily and Excel then reports the file as needing repair. `meaning`
# is a TEXT column, so 65,535 characters is reachable from the definitions table.
EXCEL_MAX_CELL_CHARS = 32767


def build_readme_blocks(
    sheets: Iterable[tuple[str, Iterable[str]]],
    context_by_code: Mapping[str, Mapping[str, str]],
    meaning_by_pair: Mapping[tuple[str, str], str],
) -> list[dict]:
    """One block per sheet, in the order the sheets will be written.

    `sheets` is (sample_type code, its columns in sheet order). Columns are kept
    in that order rather than sorted so the README can be read beside the tab.
    An undocumented sample type still gets a block, and a column with no
    definition is still listed, so the README always indexes the whole workbook.
    """
    blocks = []
    for code, columns in sheets:
        entry = context_by_code.get(code) or {}
        blocks.append({
            "code": code,
            "name": entry.get("name", "") or "",
            "description": entry.get("description", "") or "",
            "columns": [
                (column, meaning_by_pair.get((code, column), "") or "")
                for column in columns
            ],
        })
    return blocks


def load_sample_type_context(codes: Iterable[str]) -> dict[str, dict[str, str]]:
    """Look up code -> {name, description} in dmac.sample_types_context.

    Joins on the `sample_type` code string, not `sampletype_id`: the id column
    does not agree with `sample_types.id` across instances.
    """
    wanted = sorted({c for c in codes if c})
    if not wanted:
        return {}
    try:
        rows = Sample_types_context.objects.filter(sample_type__in=wanted).values(
            "sample_type", "name", "description"
        )
        return {
            r["sample_type"]: {
                "name": r.get("name") or "",
                "description": r.get("description") or "",
            }
            for r in rows
        }
    except Exception:
        # A missing or unreachable context table must not cost the user their
        # download; the README then lists codes with blank name/description.
        logger.exception("sample_types_context lookup failed; README will be unpopulated")
        return {}


def load_sample_field_context(
    pairs: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    """Resolve (sample_type, field_name) -> meaning against sample_fields_context.

    Precedence per pair: a row scoped to that sample type, else the global row
    (`sample_type == ''`), else blank. Resolving here means no caller has to
    reimplement the fallback.
    """
    wanted = [(st or "", fn) for st, fn in pairs if fn]
    if not wanted:
        return {}
    try:
        rows = list(
            Sample_fields_context.objects.filter(
                field_name__in=sorted({fn for _, fn in wanted})
            ).values("field_name", "sample_type", "meaning")
        )
    except Exception:
        # A missing or unreachable table must not cost the user their download;
        # every meaning then renders blank.
        logger.exception("sample_fields_context lookup failed; meanings will be blank")
        return {}

    global_by_field: dict[str, str] = {}
    scoped: dict[tuple[str, str], str] = {}
    for row in rows:
        meaning = row.get("meaning") or ""
        code = row.get("sample_type") or ""
        name = row.get("field_name")
        if code:
            scoped[(code, name)] = meaning
        else:
            global_by_field[name] = meaning

    return {
        (code, name): scoped.get((code, name), global_by_field.get(name, ""))
        for code, name in wanted
    }


def _safe_cell_value(value: str) -> str:
    """Make a string safe to hand to openpyxl.

    Two failure modes, both fatal to the whole download and neither caught by
    the try/except around the *query*:
    - a control character (e.g. \\x0b, what a pasted Word/PDF line break becomes)
      makes `ws.cell(...)` raise IllegalCharacterError
    - a value over Excel's cell limit writes a file Excel calls corrupt

    Definitions are reviewer-authored prose, so both are reachable. Losing a
    stray character or a tail of an absurdly long definition is always better
    than losing the workbook.
    """
    return ILLEGAL_CHARACTERS_RE.sub("", value or "")[:EXCEL_MAX_CELL_CHARS]


def _write_cell(ws, row: int, column: int, value: str, bold: bool = False):
    """The single place the README writes text, so sanitizing cannot be skipped."""
    cell = ws.cell(row=row, column=column, value=_safe_cell_value(value))
    if bold:
        cell.font = Font(bold=True)
    return cell


def _write_readme(book, blocks: list[dict]) -> None:
    ws = book.create_sheet(README_SHEET, 0)
    _write_cell(ws, 1, 1, README_LINK_TEXT)
    ws["A1"].hyperlink = CONTEXTDB_URL
    ws["A1"].style = "Hyperlink"
    # Row 2 is left blank to separate the link from the first section.
    row = 3
    for block in blocks:
        heading = f"{block['code']} — {block['name']}" if block["name"] else block["code"]
        _write_cell(ws, row, 1, heading, bold=True)
        row += 1
        _write_cell(ws, row, 1, block["description"])
        row += 2  # description, then a blank line before the column table
        # A block whose columns all dropped out gets no table header: a bare
        # Column/Meaning row with nothing under it reads as a rendering bug.
        if block["columns"]:
            for column, label in enumerate(COLUMN_TABLE_HEADER, start=2):
                _write_cell(ws, row, column, label, bold=True)
            row += 1
            for name, meaning in block["columns"]:
                _write_cell(ws, row, 2, name)
                _write_cell(ws, row, 3, meaning)
                row += 1
        row += 1  # blank line between sections
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 34
    ws.column_dimensions["C"].width = 100


def write_samples_workbook(parsed_df, output_path, context_by_code=None) -> None:
    """Write README as sheet 1, then one sheet per sample type.

    `parsed_df` must carry a `uuid` column; `sample_type` is derived here so the
    extraction regex lives in exactly one place. Sheets are prepared before the
    README is built, because the README must describe the columns that survive
    the all-empty drop rather than everything in the frame.
    """
    df = parsed_df.copy()
    df["sample_type"] = df["uuid"].astype(str).str.extract(SAMPLE_TYPE_RE, expand=False)

    codes = df["sample_type"].dropna().unique().tolist()
    if context_by_code is None:
        context_by_code = load_sample_type_context(codes)

    prepared = []
    for sample_type, sample_type_df in df.groupby("sample_type"):
        frame = sample_type_df.drop(columns=["uuid", "sample_type"])
        frame = frame.replace("", pd.NA)
        frame = frame.dropna(axis=1, how="all")
        prepared.append((sample_type, frame))

    sheets = [(code, list(frame.columns)) for code, frame in prepared]
    meaning_by_pair = load_sample_field_context(
        [(code, column) for code, columns in sheets for column in columns]
    )
    blocks = build_readme_blocks(sheets, context_by_code, meaning_by_pair)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        book = writer.book
        # pandas removes openpyxl's default sheet, but guard in case that changes.
        if "Sheet" in book.sheetnames:
            del book["Sheet"]
        _write_readme(book, blocks)

        for code, frame in prepared:
            frame.to_excel(writer, sheet_name=code, index=False)
