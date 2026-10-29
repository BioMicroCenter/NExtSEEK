"""Formula-injection guard for exports (SEC-H03).

A user-stored value such as ``=HYPERLINK(...)`` runs as a formula when a
colleague opens the download in Excel. Workbooks keep the value byte-equal and
store it as text; CSV gets a leading apostrophe. Plain numbers ("-80",
"+1.5e3") are left alone.

chat_nextseek cannot import this module (import-linter contract); its CSV
exporter carries a copy of ``csv_safe``.
"""
from __future__ import annotations

_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def csv_safe(value):
    """Prefix an apostrophe to a string that Excel would read as a formula."""
    if isinstance(value, str) and value.startswith(_TRIGGERS) and not _is_number(value):
        return "'" + value
    return value


def text_cell(cell):
    """Force a string openpyxl cell to text so it is never a formula."""
    if isinstance(cell.value, str):
        cell.data_type = "s"
    return cell


def protect_sheet(ws):
    """Text-pin every string cell of a sheet written by pandas ``to_excel``."""
    for row in ws.iter_rows():
        for cell in row:
            text_cell(cell)
    return ws
