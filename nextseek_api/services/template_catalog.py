"""What the Download Templates page knows about sample types.

Pure data: no Django request objects and no openpyxl. The writer in
`sample_workbook.py` and the views in `seek/views.py` both consume what this
returns, so neither has to reach into the database itself.

Grouping is done here rather than by reusing
`DBtable_sampletype.getSampleTypes()`, whose rule is only `A.` / `D.` /
else and so files `M.LMM` and `M.CNN` under "Experimental". The search pages
depend on that function's output, so it is left alone.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from seek.models import Sample_type_requirements, Sample_types, Sample_types_context

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

# Excel's own limits, enforced in load_catalog() below: today the longest code
# is 7 characters and none carries an illegal character, but a future sample
# type could break either, and an unusable code must cost that one type rather
# than crash the whole download.
MAX_SHEET_NAME = 31
ILLEGAL_SHEET_CHARS = set("[]:*?/\\")

# Sheet names write_template_workbook always creates itself (README_SHEET,
# MANIFEST_SHEET, CV_SHEET in sample_workbook.py). Duplicated here as literals
# rather than imported: sample_workbook.py already imports
# load_sample_type_context from this module at module level, so importing
# back would be circular.
RESERVED_SHEET_NAMES = ("README", "_NEXTSEEK", "Controlled Vocabularies")


def _is_legal_sheet_name(code: str) -> bool:
    """Whether `code` can safely become its own Excel sheet.

    Three ways a code can break `book.create_sheet`: an illegal character
    (`ValueError`), a name over 31 characters (`ValueError`), or -- not an
    openpyxl error, but just as unusable -- a name matching one of the sheets
    the writer always creates itself, which would either collide outright or
    be silently renamed to something the part-2 converter cannot find.
    """
    if len(code) > MAX_SHEET_NAME:
        return False
    if set(code) & ILLEGAL_SHEET_CHARS:
        return False
    if code in RESERVED_SHEET_NAMES:
        return False
    return True


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

    A code that cannot become a legal, unambiguous sheet name is dropped here,
    the same rule this module already applies to relationship parsing: a
    malformed input must never surface to the user or raise. It costs that one
    type rather than every type in the download.
    """
    rows = list(Sample_types.objects.all().values("id", "title", "description"))

    entries = []
    for row in rows:
        title = (row.get("title") or "").strip()
        if not title:
            continue
        if is_deprecated(row.get("description")):
            # Retired in SEEK. Offering it produces a nameless row nobody can
            # use, because nothing curates a context entry for a dead type.
            continue
        if not _is_legal_sheet_name(title):
            logger.warning(
                "sample type %r cannot be used as a sheet name; dropped from the catalog",
                title,
            )
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


# Relationship columns are curated free text, not a delimited list. Real values:
#   TIS.child_sampletypes  = "DNA or RNA or BAC or D.TITR or D.AD**, or CEL"
#   MUS.parent_sampletypes = "AB, BAC. CHM"
# Comma, semicolon, a standalone "or", and a stray period all separate; a
# wildcard like D.AD** is not a code. Splitting on a bare "." would break every
# D./A./M. code, so the period is only a separator when it is followed by
# whitespace.
_RELATED_SPLIT = re.compile(r",|;|\.\s+|\s+or\s+", flags=re.IGNORECASE)

MAX_SUGGESTIONS = 12

# Retired sample types are still rows in SEEK; the only marker is a word in the
# free-text description, and the repository does not spell it consistently --
# six say "Depreciated" (itself a misspelling of deprecated) and A.SEQ says
# "Depcreciated". Matching the exact string would leak that one type through,
# which is one of the four a user reported as showing no name at all. So match
# the stem both spellings share, case-insensitively.
_DEPRECATED = re.compile(r"depr?e?c", re.IGNORECASE)


def is_deprecated(description) -> bool:
    """True when SEEK's own description marks the type as retired.

    All seven types with no `sample_types_context` row are retired ones, which
    is why they render nameless in the picker. A blank name is the symptom;
    being offered a retired type at all is the defect.
    """
    return bool(description) and bool(_DEPRECATED.search(str(description)))



def parse_related(raw, known) -> list[str]:
    """Codes named in a relationship column, in order, deduped.

    Anything that is not an exact match for a known sample type code is dropped
    silently: a suggestion is a convenience, and a malformed token must never
    surface to the user or raise.
    """
    if not raw:
        return []

    out = []
    for token in _RELATED_SPLIT.split(str(raw)):
        code = token.strip().strip(".").strip()
        if code in known and code not in out:
            out.append(code)
    return out


def load_relationships(codes, known) -> dict[str, dict[str, list[str]]]:
    """{code: {"parents": [...], "children": [...]}} for the codes given.

    A code whose two sides are both empty is omitted rather than mapped to empty
    lists, so a caller can test membership to decide whether to render anything.
    """
    wanted = sorted({c for c in codes if c})
    if not wanted:
        return {}

    try:
        rows = list(
            Sample_types_context.objects.filter(sample_type__in=wanted).values(
                "sample_type", "parent_sampletypes", "child_sampletypes"
            )
        )
    except Exception:
        # Soft, like every other enrichment here: no relationships costs the
        # suggestion strip and a README line, never the download.
        logger.exception("sample_types_context unavailable; no relationships")
        return {}

    out = {}
    for row in rows:
        code = row.get("sample_type")
        if not code:
            continue
        parents = parse_related(row.get("parent_sampletypes"), known)
        children = parse_related(row.get("child_sampletypes"), known)
        if parents or children:
            out[code] = {"parents": parents, "children": children}
    return out


def suggest(selected, relationships) -> list[str]:
    """Types commonly used with the current selection.

    Children only, one hop. Parents are excluded because suggesting DNA to
    someone who just picked D.SEQ names something they almost certainly already
    have. One hop because PAT's transitive closure is most of the catalog.

    Ordered by how many selected types name each candidate, then by code, and
    capped -- the UI's "add all" adds exactly what is shown, never a hidden tail.

    This has no production call site of its own: `templatesList.html`'s inline
    `renderSuggestions()` reimplements the same rule in JavaScript, because the
    strip is re-derived client-side as boxes are ticked, with no round trip.
    Keep the two in lockstep by hand if you change either.
    """
    chosen = list(selected or [])
    if not chosen:
        return []

    counts = {}
    for code in chosen:
        for child in (relationships.get(code) or {}).get("children", []):
            if child in chosen:
                continue
            counts[child] = counts.get(child, 0) + 1

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [code for code, _ in ranked[:MAX_SUGGESTIONS]]


def load_type_links(codes) -> dict[str, dict[str, dict]]:
    """Everything the picker adds for you, in one read.

    Returns {"requires": {trigger: {...}}, "companions": {trigger: {...}}},
    each inner value {"add": [...], "assays": [...]}.

    A row is dropped unless every code it names is one this instance still has
    -- a chip the user cannot satisfy is worse than no chip. Same rule
    parse_related() applies to the curator columns.

    `coverage` and `support` are deliberately not read. They are what the rules
    were derived from, auditable in the table itself; the page shows a link or
    it does not, and shipping the numbers to the browser only widened the
    surface a malformed row could break.

    Soft, like every other enrichment here: an absent or unreadable table costs
    the links and the picker behaves as it did before the feature existed.
    """
    empty = {"requires": {}, "companions": {}}
    known = {c for c in (codes or []) if c}
    if not known:
        return empty

    try:
        rows = list(
            Sample_type_requirements.objects.filter(
                trigger_code__in=sorted(known)
            ).values("kind", "trigger_code", "add_codes", "assay_titles")
        )
    except Exception:
        logger.exception("sample_type_requirements unavailable; no links shown")
        return empty

    out = {"requires": {}, "companions": {}}
    for row in rows:
        # Everything that touches the row's own data belongs inside the try:
        # `source='curator'` is reserved for hand-written rows, and the whole
        # point of the failure contract is that the first bad one costs a link
        # rather than the page. set() on a non-list add_codes ('5' is valid
        # JSON) raises TypeError just as loudly as a bad parse.
        try:
            add = json.loads(row["add_codes"])
            assays = json.loads(row["assay_titles"]) if row["assay_titles"] else []
            if not add or not set(add) <= known:
                continue
            if not isinstance(assays, list):
                assays = []
        except (TypeError, ValueError):
            # A malformed row is one bad link, not a broken page.
            logger.warning("unparseable link row for %s", row.get("trigger_code"))
            continue

        bucket = ("companions"
                  if row["kind"] == Sample_type_requirements.KIND_COMPANION
                  else "requires")
        out[bucket][row["trigger_code"]] = {"add": add, "assays": assays}
    return out


def build_catalog() -> dict:
    """Everything a template picker needs, in one read.

    Both call paths read this: `seek.views.assets._templates_context` renders it,
    and `nextseek_api.services.templates.TemplatesViewSet.catalog` serialises it.
    `suggest()` above already records that `templatesList.html` reimplements its
    rule in JavaScript and that the two must be kept in lockstep by hand; a third
    copy of the same rule inside the API is that documented failure mode, so
    neither caller assembles this itself.

    `groups` holds `SampleTypeEntry` objects rather than dicts on purpose. The
    Django template reads `entry.code` / `entry.name`, and
    `write_template_workbook` reads `.code` and `.sample_type_id` off the same
    objects; handing out dicts would work in the template and break the writer.
    """
    entries = load_catalog()
    by_code = {e.code: e for e in entries}
    relationships = load_relationships(list(by_code), set(by_code))
    links = load_type_links(set(by_code))

    groups = [
        {"key": key, "label": label,
         "entries": [e for e in entries if e.group == key]}
        for key, label in GROUPS
    ]
    return {
        "groups": [g for g in groups if g["entries"]],
        # Children only, matching the one-hop rule in suggest(): the page
        # re-derives its suggestion strip in the browser from this map.
        "children": {
            code: rel.get("children", []) for code, rel in relationships.items()
        },
        # `requires` runs child -> parent (what an upload cannot omit);
        # `companions` runs parent -> child (what it will almost certainly also
        # record). Keyed on the whole catalog, not on any selection.
        "requires": links["requires"],
        "companions": links["companions"],
        "max_suggestions": MAX_SUGGESTIONS,
    }
