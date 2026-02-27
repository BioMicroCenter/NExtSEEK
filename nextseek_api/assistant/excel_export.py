"""
Excel export utilities for assistant artifacts.

Converts report_writer_output and search results into Excel workbooks.
Uses openpyxl (available via chat_nextseek dependency).
"""

from __future__ import annotations

import io
import logging
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)


def flatten_report_to_tables(report_writer_output: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a report_writer_output dict into a list of table artifact dicts.

    Each table has: key, label, columns, data.
    Handles known report structures (GEO samples/protocols/study) and
    falls back to generic auto-flattening for unknown types.
    """
    report = report_writer_output.get("report") or {}
    if not report:
        return []

    tables: list[dict[str, Any]] = []

    for key, value in report.items():
        if isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
            # List of dicts -> table rows
            columns = list(value[0].keys())
            tables.append({
                "key": key,
                "label": key.replace("_", " ").title(),
                "columns": columns,
                "data": value,
            })
        elif isinstance(value, dict) and not _is_deeply_nested(value):
            # Flat dict -> single-row table
            columns = list(value.keys())
            tables.append({
                "key": key,
                "label": key.replace("_", " ").title(),
                "columns": columns,
                "data": [value],
            })

    return tables


def _is_deeply_nested(d: dict) -> bool:
    """Check if a dict has nested dicts/lists as values (not suitable for flat table)."""
    for v in d.values():
        if isinstance(v, (dict, list)):
            return True
    return False


def extract_table_artifacts(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the artifacts list from a bundle dict.

    For reporter bundles: extracts table artifacts from report_writer_output
    and file artifacts from report_saved_files.
    For search bundles: returns empty (search xlsx is generated client-side).
    """
    mode = bundle.get("mode", "")
    if mode not in ("reporter", "report_generation", "sql_report"):
        return []

    artifacts: list[dict[str, Any]] = []

    # Table artifacts from report_writer_output
    rwo = bundle.get("report_writer_output")
    if isinstance(rwo, dict):
        tables = flatten_report_to_tables(rwo)
        for t in tables:
            artifacts.append({
                "artifact_type": "table",
                "key": t["key"],
                "label": t["label"],
                "columns": t["columns"],
                "data": t["data"],
            })

    # File artifacts from saved files
    saved = bundle.get("report_saved_files") or {}
    if "geo_seq_workbooks" in saved:
        workbooks = saved["geo_seq_workbooks"]
        if isinstance(workbooks, list) and len(workbooks) > 0:
            artifacts.append({
                "artifact_type": "file",
                "key": "geo_seq_workbooks",
                "label": "GEO Submission Workbook",
                "file_format": "xlsx",
            })

    return artifacts


def generate_table_xlsx(tables: list[dict[str, Any]]) -> bytes:
    """Generate an Excel workbook with one sheet per table.

    Args:
        tables: list of dicts with keys: label, columns, data

    Returns:
        Excel file bytes.
    """
    wb = openpyxl.Workbook()

    if not tables:
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    # Remove default sheet, we'll create named ones
    default_sheet = wb.active
    wb.remove(default_sheet)

    for table in tables:
        label = table.get("label", "Sheet")[:31]  # Excel sheet name max 31 chars
        ws = wb.create_sheet(title=label)
        columns = table.get("columns", [])
        data = table.get("data", [])

        # Header row
        for col_idx, col_name in enumerate(columns, 1):
            cell = ws.cell(row=1, column=col_idx, value=col_name)
            cell.font = openpyxl.styles.Font(bold=True)

        # Data rows
        for row_idx, row in enumerate(data, 2):
            for col_idx, col_name in enumerate(columns, 1):
                ws.cell(row=row_idx, column=col_idx, value=row.get(col_name))

        # Auto-width columns
        for col_idx, col_name in enumerate(columns, 1):
            max_len = len(str(col_name))
            for row in data:
                val = str(row.get(col_name, ""))
                max_len = max(max_len, len(val))
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 50)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate_search_xlsx(bundle: dict[str, Any]) -> bytes:
    """Generate Excel from a search result bundle.

    Flattens JSON:API response data into a tabular format.
    """
    api_result = bundle.get("api_result_full") or {}
    data_list = api_result.get("data") or []

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Search Results"

    if not data_list:
        ws.cell(row=1, column=1, value="No results")
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    # Flatten JSON:API: merge id, type, and attributes
    rows: list[dict[str, Any]] = []
    for item in data_list:
        row: dict[str, Any] = {}
        if "id" in item:
            row["id"] = item["id"]
        if "type" in item:
            row["type"] = item["type"]
        attrs = item.get("attributes") or {}
        for k, v in attrs.items():
            # Skip deeply nested values
            if not isinstance(v, (dict, list)):
                row[k] = v
        rows.append(row)

    if not rows:
        ws.cell(row=1, column=1, value="No results")
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    columns = list(rows[0].keys())

    # Header
    for col_idx, col_name in enumerate(columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = openpyxl.styles.Font(bold=True)

    # Data
    for row_idx, row in enumerate(rows, 2):
        for col_idx, col_name in enumerate(columns, 1):
            ws.cell(row=row_idx, column=col_idx, value=row.get(col_name))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
