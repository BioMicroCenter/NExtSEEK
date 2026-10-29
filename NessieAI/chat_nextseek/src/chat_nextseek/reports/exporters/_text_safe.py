"""Formula-injection guards for the report exporters (SEC-H03).

Copies of nextseek_api.services.excel_safe: import-linter bars chat_nextseek from importing nextseek_api.
"""
from __future__ import annotations


def csv_safe(value: str) -> str:
    """Prefix an apostrophe to a string Excel would read as a formula; plain numbers stay."""
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        try:
            float(value)
        except ValueError:
            return "'" + value
    return value


def text_cell(cell):
    """Store a string cell as text so it is never a formula (value stays byte-equal)."""
    if isinstance(cell.value, str):
        cell.data_type = "s"
    return cell


def set_text(ws, row: int, col: int, value):
    """Write a user value into a worksheet cell as text."""
    cell = ws.cell(row=row, column=col)
    cell.value = value
    return text_cell(cell)
