"""What the Download Templates page knows about sample types.

Pure data: no Django request objects and no openpyxl. The writer in
`sample_workbook.py` and the views in `seek/views.py` both consume what this
returns, so neither has to reach into the database itself.

Grouping is done here rather than by reusing
`DBtable_sampleattribute.getSampleTypes()`, whose rule is only `A.` / `D.` /
else and so files `M.LMM` and `M.CNN` under "Experimental". The search pages
depend on that function's output, so it is left alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from seek.models import Sample_types

from nextseek_api.services.sample_workbook import load_sample_type_context

logger = logging.getLogger(__name__)

# Display order, and the prefix that selects each group. "" is the fallback, so
# it must be matched last -- see group_for.
GROUPS = (
    ("", "Experimental types"),
    ("D.", "Data types"),
    ("A.", "Analysis types"),
    ("M.", "Model types"),
)
_GROUP_ORDER = {key: index for index, (key, _) in enumerate(GROUPS)}
_PREFIXES = tuple(key for key, _ in GROUPS if key)

# Excel's own limits. Asserted against real codes in the tests rather than
# trusted: today the longest code is 7 characters and none carries an illegal
# character, but a future sample type could break either.
MAX_SHEET_NAME = 31
ILLEGAL_SHEET_CHARS = set("[]:*?/\\")


@dataclass
class SampleTypeEntry:
    code: str
    sample_type_id: int
    name: str = ""
    description: str = ""
    group: str = ""


def group_for(code: str) -> str:
    """Prefix group key for a sample type code. Unknown prefixes are Experimental."""
    for prefix in _PREFIXES:
        if code.startswith(prefix):
            return prefix
    return ""


def load_catalog() -> list[SampleTypeEntry]:
    """Every sample type, enriched with context, in display order.

    The type list is the one hard dependency: there is no page without it. The
    context lookup is soft, per this module's house rule -- a code with no
    context row still appears, with a blank name and description.
    """
    rows = list(Sample_types.objects.all().values("id", "title"))

    entries = []
    for row in rows:
        title = (row.get("title") or "").strip()
        if not title:
            continue
        entries.append(SampleTypeEntry(
            code=title,
            sample_type_id=int(row["id"]),
            group=group_for(title),
        ))

    try:
        context = load_sample_type_context([e.code for e in entries])
    except Exception:
        logger.exception("sample type context unavailable; names will be blank")
        context = {}

    for entry in entries:
        found = context.get(entry.code) or {}
        entry.name = found.get("name", "") or ""
        entry.description = found.get("description", "") or ""

    entries.sort(key=lambda e: (_GROUP_ORDER[e.group], e.code))
    return entries
