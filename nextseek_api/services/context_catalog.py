"""What the sample type and assay catalog pages know.

Pure data: no Django request objects, no HTTP, no templates. The views in
`seek/views/catalog.py` and the reworked `seek/views/projects.py` both consume
what this returns, so neither reaches into the database itself. Same division
`template_catalog.py` already draws for the Download Templates page.

House rule, inherited from `template_catalog.load_catalog` and pinned by tests:
a missing table or a missing row costs that one field or entry. Nothing here
raises to a caller. A stack without `assay_context` renders an empty assays
page; it does not 500 and it does not take the project page down with it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from django.conf import settings
from django.db import connections

from seek.models import Sample_types_context
from nextseek_api.services.template_catalog import is_deprecated

logger = logging.getLogger(__name__)

# Clade display order. Anything unmapped sorts last rather than being dropped,
# so an incomplete clade column degrades visibly. Mirrors CLADE_ORDER in
# sampletype_connections.py, which uses the same four names for the same reason.
CLADE_ORDER = ["Source", "Raw", "Processed", "Analyzed"]
_CLADE_RANK = {name: index for index, name in enumerate(CLADE_ORDER)}
UNASSIGNED_CLADE = "Unassigned"

# A comma or a semicolon separates GROUPS; a standalone `or` separates
# ALTERNATIVES within a group. Deliberately NOT template_catalog._RELATED_SPLIT,
# which treats `or` as a group separator too: that is right for a suggestion
# strip and wrong here, because it turns "MUS or PAV" into two independent
# parents and loses the fact that an upload needs one of them, not both.
#
# Three group separators, all measured against the live tables rather than
# guessed:
#   `,` and `;`   the ordinary case
#   `. ` a period FOLLOWED BY WHITESPACE. MUS.parent_sampletypes is literally
#        "AB, BAC. CHM". The whitespace is required, or this would split every
#        D./A./M. code in half.
#   ` and `  means BOTH, which is what a comma already means here. 9 rows of
#        assay_context and 5 of sample_types_context write "TIS and AB", and
#        "AB or ABP and CEL or TIS" mixes it with `or` in one value. Treating
#        `and` as a group separator gets that last one exactly right:
#        [["AB","ABP"], ["CEL","TIS"]], one of each group.
_GROUP_SPLIT = re.compile(r"[,;]|\.\s+|\s+and\s+", flags=re.IGNORECASE)
_ALT_SPLIT = re.compile(r"\s+or\s+", flags=re.IGNORECASE)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def parse_list(raw) -> list[str]:
    """A plain comma-separated column as a list, in order, blanks dropped.

    Used for the metadata field columns and Tags, which are free text naming
    attributes rather than sample type codes, so nothing is validated against a
    known set: an unrecognised field name is still a field name.
    """
    if not raw:
        return []
    return [token.strip() for token in str(raw).split(",") if token.strip()]


def parse_alternation(raw, known) -> list[list[str]]:
    """Groups of alternative sample type codes, structure preserved.

        "MUS or PAV"                   -> [["MUS", "PAV"]]
        "TIS or CEX or CEL, AB or ABP" -> [["TIS","CEX","CEL"], ["AB","ABP"]]

    Codes not in `known` are dropped, and a group emptied by that is dropped
    with them, so a malformed token costs itself and never surfaces or raises.
    That is the rule `template_catalog.parse_related` already applies to the
    same curator columns.
    """
    if not raw:
        return []
    groups = []
    for chunk in _GROUP_SPLIT.split(str(raw)):
        alternatives = []
        for token in _ALT_SPLIT.split(chunk):
            code = token.strip().strip(".").strip()
            if code in known and code not in alternatives:
                alternatives.append(code)
        if alternatives:
            groups.append(alternatives)
    return groups


def slugify_name(name) -> str:
    """A URL segment for an assay name. Not guaranteed unique; see load_assays."""
    return _SLUG_STRIP.sub("-", str(name or "").lower()).strip("-")


def _clade_sort_key(clade, code):
    return (_CLADE_RANK.get(clade, len(CLADE_ORDER)), clade or "", code)


@dataclass
class SampleTypeContextEntry:
    code: str
    sample_type_id: int | None
    name: str = ""
    description: str = ""
    clade: str = UNASSIGNED_CLADE
    tags: list[str] = field(default_factory=list)
    required_metadata: list[str] = field(default_factory=list)
    standard_metadata: list[str] = field(default_factory=list)
    possible_metadata_fields: list[str] = field(default_factory=list)
    parent_types: list[str] = field(default_factory=list)
    child_types: list[str] = field(default_factory=list)
    assay_parents: list[str] = field(default_factory=list)
    assay_children: list[str] = field(default_factory=list)


# Every column the page renders. sampletype_file_link is deliberately absent:
# it is NULL on all 101 rows, so selecting it would only invite rendering it.
_SAMPLE_TYPE_COLUMNS = (
    "sample_type", "sampletype_id", "name", "description", "clade", "Tags",
    "required_metadata", "standard_metadata", "possible_metadata_fields",
    "parent_sampletypes", "child_sampletypes",
    "associated_assay_parents", "associated_assay_children",
)


def _sample_type_rows() -> list[dict]:
    """Raw rows. Its own function so tests can replace the database, not the parse."""
    return list(Sample_types_context.objects.all().values(*_SAMPLE_TYPE_COLUMNS))


def load_sample_types() -> list[SampleTypeContextEntry]:
    """Every curated sample type, parsed, in clade then code order.

    Retired types are omitted, reusing `template_catalog.is_deprecated`: SEEK
    spells the marker six different ways in its own descriptions and that
    function already matches the stem they share.
    """
    try:
        rows = _sample_type_rows()
    except Exception:
        logger.exception("sample_types_context unavailable; catalog will be empty")
        return []

    known = {row.get("sample_type") for row in rows if row.get("sample_type")}

    entries = []
    for row in rows:
        code = (row.get("sample_type") or "").strip()
        if not code:
            continue
        if is_deprecated(row.get("description")):
            continue
        entries.append(SampleTypeContextEntry(
            code=code,
            sample_type_id=row.get("sampletype_id"),
            name=(row.get("name") or "").strip(),
            description=(row.get("description") or "").strip(),
            clade=(row.get("clade") or UNASSIGNED_CLADE).strip() or UNASSIGNED_CLADE,
            tags=parse_list(row.get("Tags")),
            required_metadata=parse_list(row.get("required_metadata")),
            standard_metadata=parse_list(row.get("standard_metadata")),
            possible_metadata_fields=parse_list(row.get("possible_metadata_fields")),
            parent_types=[c for group in parse_alternation(row.get("parent_sampletypes"), known)
                          for c in group],
            child_types=[c for group in parse_alternation(row.get("child_sampletypes"), known)
                         for c in group],
            assay_parents=parse_list(row.get("associated_assay_parents")),
            assay_children=parse_list(row.get("associated_assay_children")),
        ))

    entries.sort(key=lambda e: _clade_sort_key(e.clade, e.code))
    return entries


def load_sample_type(code: str) -> SampleTypeContextEntry | None:
    """One entry by code, or None. Loads the catalog: 101 rows, one query."""
    for entry in load_sample_types():
        if entry.code == code:
            return entry
    return None
