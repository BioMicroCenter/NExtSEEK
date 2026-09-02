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
# FIELD names, not db_columns. `tags` is the field; its db_column is capital-T
# Tags, and selecting the db_column raises FieldError -- which the loader's
# soft-dependency except then swallowed into an empty catalog on every request.
# Pinned by test_every_selected_column_is_a_real_model_field.
_SAMPLE_TYPE_COLUMNS = (
    "sample_type", "sampletype_id", "name", "description", "clade", "tags",
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
            tags=parse_list(row.get("tags")),
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


@dataclass
class AssayRow:
    row_id: int | None
    description: str = ""
    tags: list[str] = field(default_factory=list)
    alternative_names: list[str] = field(default_factory=list)
    required_parents: list[list[str]] = field(default_factory=list)
    optional_parents: list[list[str]] = field(default_factory=list)
    children: list[list[str]] = field(default_factory=list)
    parent_clade: str = ""
    child_clade: str = ""
    sheet_link: str = ""
    repository: str = ""
    critical_attributes: list[str] = field(default_factory=list)
    internal_assay_id: int | None = None


@dataclass
class AssayEntry:
    """One page. Usually one row; 24 slugs of 193 carry two.

    Two rows are NOT merged. 22 name pairs in assay_context are one curated row
    plus one auto-generated row from a different source, and choosing between
    their descriptions would be a data decision this page has no standing to
    make. Both are rendered, stacked, and the page says why.
    """
    slug: str
    name: str
    rows: list[AssayRow] = field(default_factory=list)


def _rows_from_cursor(cursor) -> list[dict]:
    """Rows as dicts keyed by LOWERCASED column name.

    The lowercasing is the whole point. Production spells these columns in mixed
    case (`Required_Parent_Sample_Types`), and chat_nextseek's own mapper hedges
    by trying both spellings for every field, which is the evidence that nothing
    in this repo actually knows which one a given stack has. Lowercasing once
    here means the rest of the module never has to, and it is why assay_context
    and projects_context get no Django model: a model must commit to one
    spelling and would silently read nothing on a stack that uses the other.
    """
    columns = [column[0].lower() for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _query(sql: str, params=None) -> list[dict]:
    """Run one read against the NExtSEEK database. Raises; callers soften it."""
    with connections[settings.NEXTSEEK_DATABASE].cursor() as cursor:
        cursor.execute(sql, params or [])
        return _rows_from_cursor(cursor)


def _assay_rows() -> list[dict]:
    """Raw assay_context rows. Its own function so tests replace the database."""
    return _query("SELECT * FROM assay_context ORDER BY id")


def _known_sample_type_codes() -> set[str]:
    """Codes parse_alternation validates against. Empty on failure, never raises."""
    try:
        return {e.code for e in load_sample_types()}
    except Exception:
        logger.exception("sample type codes unavailable; assay relationships will be bare")
        return set()


def load_assays() -> list[AssayEntry]:
    """Every curated assay, grouped by slug, in name order.

    Grouped by slug rather than by name so that two spellings of one assay land
    on one page: 217 rows carry 195 distinct names but only 193 distinct slugs,
    the two extra collapses being a hyphen apart.
    """
    try:
        rows = _assay_rows()
    except Exception:
        logger.exception("assay_context unavailable; assay catalog will be empty")
        return []

    known = _known_sample_type_codes()

    by_slug: dict[str, AssayEntry] = {}
    for row in rows:
        name = (row.get("assay_name") or row.get("name") or "").strip()
        if not name:
            continue
        slug = slugify_name(name)
        if not slug:
            continue
        entry = by_slug.get(slug)
        if entry is None:
            # First row wins the display name. _assay_rows orders by id, so this
            # is deterministic rather than whichever the database felt like.
            entry = by_slug[slug] = AssayEntry(slug=slug, name=name)
        entry.rows.append(AssayRow(
            row_id=row.get("id"),
            description=(row.get("description") or "").strip(),
            tags=parse_list(row.get("tags")),
            alternative_names=parse_list(row.get("alternative_assay_names")),
            required_parents=parse_alternation(row.get("required_parent_sample_types"), known),
            optional_parents=parse_alternation(row.get("optional_parent_sample_types"), known),
            children=parse_alternation(row.get("children_sample_types"), known),
            parent_clade=(row.get("parent_clade_type") or "").strip(),
            child_clade=(row.get("child_clade_type") or "").strip(),
            sheet_link=(row.get("assaysheet_link") or "").strip(),
            repository=(row.get("associatedrepository") or "").strip(),
            critical_attributes=parse_list(row.get("critical_attributes")),
            internal_assay_id=row.get("internal_assay_id"),
        ))

    return sorted(by_slug.values(), key=lambda e: e.name.lower())


def load_assay(slug: str) -> AssayEntry | None:
    """One entry by slug, or None."""
    for entry in load_assays():
        if entry.slug == slug:
            return entry
    return None


def assay_slug_for_name(name) -> str:
    """The catalog URL segment for an assay named in a curator column.

    A separate name from slugify_name so the cross-link contract is visible at
    the call site: sample type rows name their assays in prose, and this is the
    single place that turns such a name into a link target.
    """
    return slugify_name(name)


def _coerce_json_list(value) -> list[str]:
    """A JSON array column as a list, falling back to pipe-delimited text.

    Same two-step chat_nextseek's map_project already does, and for the same
    reason: the column is curated by hand and both forms are in it.
    """
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return [part.strip() for part in text.split("|") if part.strip()]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [text]


def _project_context_row(project_id: int) -> dict | None:
    """The projects_context row for a SEEK project id, or None."""
    rows = _query("SELECT * FROM projects_context WHERE project_id = %s LIMIT 1",
                  [project_id])
    return rows[0] if rows else None


def load_project_context(project_id: int) -> dict | None:
    """Curated context for one project, or None when there is none.

    None rather than an empty dict, so the template can test one thing to decide
    whether to render the enriched header at all. Every stack but production has
    an empty table today, so None is the common case and must be cheap.
    """
    try:
        row = _project_context_row(int(project_id))
    except Exception:
        logger.exception("projects_context unavailable for project_id=%s", project_id)
        return None
    if not row:
        return None
    return {
        "name": row.get("name") or "",
        "alternative_names": _coerce_json_list(row.get("alternative_names")),
        "key_data_types": _coerce_json_list(row.get("key_data_types")),
        "parent_project": row.get("parent_project") or "",
        "pi": row.get("pi") or "",
        "research_focus": row.get("research_focus") or "",
        "nih_reporter_link": row.get("nih_reporter_link") or "",
        "fairdomhub_published_link": row.get("fairdomhub_published_link") or "",
        "tags": parse_list(row.get("tags")),
    }
