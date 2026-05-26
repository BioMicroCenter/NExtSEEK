"""Project ID and date-range parsing helpers. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import calendar
import re
from datetime import datetime
from typing import Iterable

from ..config import ChatConfig


def _normalize_project_id(config: ChatConfig, project: int | str | None) -> int | None:
    """
    Normalize a project identifier from int or string to canonical integer ID.
    Accepts numeric strings or known project names via PROJECT_NAME_TO_ID, raising on unknown names.
    """
    if project is None:
        return None
    if isinstance(project, int):
        return project
    key = project.strip().upper()
    if not key:
        return None
    if key.isdigit():
        return int(key)
    if key not in config.PROJECT_NAME_TO_ID:
        # Fuzzy fallback: accept if any canonical key is contained in the input or vice versa.
        fuzzy_match = next(
            (
                (canonical, pid)
                for canonical, pid in config.PROJECT_NAME_TO_ID.items()
                if canonical in key or key in canonical
            ),
            None,
        )
        if fuzzy_match:
            print(f"[WARN][PROJECT] '{project}' fuzzy-matched to '{fuzzy_match[0]}' (id={fuzzy_match[1]})")
            return fuzzy_match[1]
        raise ValueError(
            f"Unknown project '{project}'. Expected one of: {sorted(config.PROJECT_NAME_TO_ID.keys())} "
            f"or a numeric project_id."
        )
    return config.PROJECT_NAME_TO_ID[key]

def _normalize_years(years: Iterable[int | str]) -> list[str]:
    """
    Convert an iterable of years into deduped two-digit strings (e.g., '24', '25').
    Accepts 2- or 4-digit inputs as ints or strings and raises on malformed values to keep SQL filters safe.
    """
    out: list[str] = []
    for y in years:
        s = str(y).strip()
        if re.fullmatch(r"\d{4}", s):
            out.append(s[2:])
        elif re.fullmatch(r"\d{2}", s):
            out.append(s)
        else:
            raise ValueError(f"Invalid year value: {y!r}. Use 2024 or 24 (or strings).")
    seen = set()
    deduped = []
    for yy in out:
        if yy not in seen:
            seen.add(yy)
            deduped.append(yy)
    return deduped

def _parse_month(s: str) -> tuple[int, int]:
    """
    Parse a month string in formats like 'YYYY-MM', 'YYYY/MM', or 'YYYYMM' (also 'YY-MM' -> assumes 20YY).
    Returns (year, month) and raises ValueError on invalid inputs so callers can surface clear errors.
    """
    t = s.strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{2})", t) or re.fullmatch(r"(\d{4})(\d{2})", t)
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
    else:
        m2 = re.fullmatch(r"(\d{2})[-/](\d{2})", t) or re.fullmatch(r"(\d{2})(\d{2})", t)
        if not m2:
            raise ValueError(f"Invalid month format: {s!r}. Use '2024-01' (preferred).")
        year = 2000 + int(m2.group(1))
        month = int(m2.group(2))

    if not (1 <= month <= 12):
        raise ValueError(f"Invalid month in {s!r}.")
    return year, month

def _month_range_to_yymmdd_bounds(month_range: tuple[str, str]) -> tuple[str, str]:
    """
    Convert a (start_month, end_month) tuple into inclusive YYMMDD bounds (e.g., '2024-01' -> '240101').
    Swaps bounds when reversed to keep downstream SQL BETWEEN clauses robust.
    """
    y1, m1 = _parse_month(month_range[0])
    y2, m2 = _parse_month(month_range[1])

    start = f"{y1 % 100:02d}{m1:02d}01"
    last_day = calendar.monthrange(y2, m2)[1]
    end = f"{y2 % 100:02d}{m2:02d}{last_day:02d}"

    # If user accidentally swaps, we still handle it
    if start > end:
        start, end = end, start
    return start, end

def _parse_day(s: str) -> tuple[int, int, int]:
    """
    Accepts 'YYYY-MM-DD' or 'YYYY/MM/DD' or 'YYYYMMDD' (also 'YYMMDD' -> assumes 20YY).
    Returns (year, month, day).
    """
    t = s.strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{2})[-/](\d{2})", t) or re.fullmatch(r"(\d{4})(\d{2})(\d{2})", t)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m2 = re.fullmatch(r"(\d{2})(\d{2})(\d{2})", t)
        if not m2:
            raise ValueError(f"Invalid day format: {s!r}. Use '2024-04-22' (preferred).")
        year, month, day = 2000 + int(m2.group(1)), int(m2.group(2)), int(m2.group(3))

    # Basic validity check
    datetime(year, month, day)  # will raise if invalid
    return year, month, day

def _day_range_to_yymmdd_bounds(day_range: tuple[str, str]) -> tuple[str, str]:
    """
    Convert a (start_day, end_day) tuple into inclusive YYMMDD bounds for SQL filtering.
    Handles reversed inputs by swapping so BETWEEN logic stays correct.
    """
    (y1, m1, d1) = _parse_day(day_range[0])
    (y2, m2, d2) = _parse_day(day_range[1])
    start = f"{y1 % 100:02d}{m1:02d}{d1:02d}"
    end = f"{y2 % 100:02d}{m2:02d}{d2:02d}"
    if start > end:
        start, end = end, start
    return start, end
