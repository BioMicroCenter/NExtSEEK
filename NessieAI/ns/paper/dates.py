"""Lenient dates: ISO (with or without time) and GEO's 'Jan 05 2024'."""
from __future__ import annotations

from datetime import date, datetime


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
