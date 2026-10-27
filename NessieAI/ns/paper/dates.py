"""Lenient dates: ISO (with or without time) and GEO's 'Jan 05 2024'.

parse_date gives a full date or None. parse_partial_date keeps a date at the precision its
source gives, as reduced-precision ISO 8601 ('2024', '2024-02' or '2024-02-03'), so a year or
a month is never padded into a day the source did not name.
"""
from __future__ import annotations

import re
from datetime import date, datetime

_REDUCED = re.compile(r"\d{4}(?:-(\d{2}))?")
_MONTH_YEAR = re.compile(r"([A-Za-z]{3,9})\.? (\d{4})|(\d{4}) ([A-Za-z]{3,9})\.?")


def parse_date(value) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%b %d %Y").date()
    except ValueError:
        return None


def parse_partial_date(value) -> str | None:
    """'YYYY', 'YYYY-MM' or 'YYYY-MM-DD': as much of the date as the value gives, or None."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    full = parse_date(value)
    if full is not None:
        return full.isoformat()
    text = str(value or "").strip()
    reduced = _REDUCED.fullmatch(text)
    if reduced:
        month = reduced.group(1)
        return text if month is None or 1 <= int(month) <= 12 else None
    named = _MONTH_YEAR.fullmatch(text)
    if named:
        name, year = (named.group(1), named.group(2)) if named.group(1) else (named.group(4), named.group(3))
        for fmt in ("%b", "%B"):
            try:
                return f"{year}-{datetime.strptime(name, fmt).month:02d}"
            except ValueError:
                pass
    return None
