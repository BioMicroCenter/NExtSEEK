"""The single writer for sample-download workbooks.

Every sample download in NExtSEEK ends here, so the README sheet cannot drift
between the legacy `seek` views and the `nextseek_api` endpoint. See
docs/sample-download-workflow.md for how the call paths converge on this module.
"""

from __future__ import annotations

import logging
from typing import Iterable, Mapping

import pandas as pd

from seek.models import Sample_fields_context, Sample_types_context

logger = logging.getLogger(__name__)

CONTEXTDB_URL = (
    "https://github.com/BioMicroCenter/NExtSEEK/blob/main/"
    "chat_nextseek/src/chat_nextseek/context/sampletypes_db.json"
)

README_SHEET = "README"
README_HEADER = ["Sample Type", "Name", "Description"]
README_LINK_TEXT = "Sample type definitions: sampletypes_db.json (GitHub)"

# Sample UIDs lead with the sample-type code: "MUS-230101ABC-1", "D.SEQ-240910LAU-3".
# The dotted alternative must come first or "D.SEQ" truncates to "D".
SAMPLE_TYPE_RE = r"([A-Z]+\.[A-Z]+|[A-Z]+)"


def build_readme_rows(
    codes: Iterable[str],
    context_by_code: Mapping[str, Mapping[str, str]],
) -> list[list[str]]:
    """Header row plus one row per distinct code, sorted, blanks when undocumented.

    Undocumented codes are listed rather than omitted so the README always
    indexes every sheet in the workbook, and a gap in the context table is
    visible instead of silent.
    """
    rows = [list(README_HEADER)]
    for code in sorted({c for c in codes if c}):
        entry = context_by_code.get(code) or {}
        rows.append([code, entry.get("name", "") or "", entry.get("description", "") or ""])
    return rows


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


def _write_readme(book, rows: list[list[str]]) -> None:
    ws = book.create_sheet(README_SHEET, 0)
    ws["A1"] = README_LINK_TEXT
    ws["A1"].hyperlink = CONTEXTDB_URL
    ws["A1"].style = "Hyperlink"
    # Row 2 is left blank to separate the link from the table.
    for r, row in enumerate(rows, start=3):
        for c, value in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=value)
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 34
    ws.column_dimensions["C"].width = 100


def write_samples_workbook(parsed_df, output_path, context_by_code=None) -> None:
    """Write README as sheet 1, then one sheet per sample type.

    `parsed_df` must carry a `uuid` column; `sample_type` is derived here so the
    extraction regex lives in exactly one place.
    """
    df = parsed_df.copy()
    df["sample_type"] = df["uuid"].astype(str).str.extract(SAMPLE_TYPE_RE, expand=False)

    codes = df["sample_type"].dropna().unique().tolist()
    if context_by_code is None:
        context_by_code = load_sample_type_context(codes)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        book = writer.book
        # pandas removes openpyxl's default sheet, but guard in case that changes.
        if "Sheet" in book.sheetnames:
            del book["Sheet"]
        _write_readme(book, build_readme_rows(codes, context_by_code))

        for sample_type, sample_type_df in df.groupby("sample_type"):
            sample_type_df = sample_type_df.drop(columns=["uuid", "sample_type"])
            sample_type_df = sample_type_df.replace("", pd.NA)
            sample_type_df = sample_type_df.dropna(axis=1, how="all")
            sample_type_df.to_excel(writer, sheet_name=sample_type, index=False)
