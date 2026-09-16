"""Creating an assay: the only writer of `assay_context`.

Two halves, kept apart so the grammar can be tested with no database. `validate`
and `serialise_groups` are pure; `create_assay` (Task 3) does the writing.

Codes are written back in exactly the grammar `context_catalog.parse_alternation`
reads -- groups joined by ", ", alternatives within a group by " or " -- so a
created assay round-trips through the same parser that reads curated ones. The
round trip is pinned by a test rather than left to inspection, and `validate`
also performs it on the proposal in hand: a sample type whose own NAME carries
one of that parser's separators cannot be stored unambiguously, so it is
refused at the form rather than written and silently dropped on read.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import connections, transaction

from seek.models import Internal_assays
from nextseek_api.services.context_catalog import (load_assays,
                                                   parse_alternation,
                                                   slugify_name)

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """A proposal the user must fix. Carries prose meant for the page."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _clean_groups(groups):
    """Strip blank codes and dedupe within each group, dropping emptied groups.

    The single place that cleans alternation groups, so `serialise_groups` and
    `validate`'s "did this role produce anything" check agree on what counts as
    a real code. `[[""]]` and `[["  "]]` clean to `[]`; `[["TIS", ""]]` cleans
    to `[["TIS"]]`; `[["TIS", "TIS"]]` cleans to `[["TIS"]]`, first-seen order
    kept.
    """
    cleaned = []
    for group in groups or []:
        seen = []
        for code in group or []:
            code = (code or "").strip()
            if code and code not in seen:
                seen.append(code)
        if seen:
            cleaned.append(seen)
    return cleaned


def serialise_groups(groups) -> str:
    """Alternation groups as the curator grammar.

        [["TIS", "CEX"], ["AB"]]  ->  "TIS or CEX, AB"
    """
    parts = [" or ".join(codes) for codes in _clean_groups(groups)]
    return ", ".join(parts)


# Roles in the order the page shows them, and the prose each is called by in an
# error message. Iterated by `validate`'s round-trip check, so a new role gets
# checked by adding one line here.
_ROLE_LABELS = (
    ("required parent", "required_parents"),
    ("optional parent", "optional_parents"),
    ("produced", "children"),
)


def _unstorable_code(groups) -> str:
    """The first code in `groups` that cannot be written and read back, or "".

    The write side allowlists codes against
    `template_catalog.downloadable_codes()` -- SEEK's own `Sample_types.title`
    values, where only `[]:*?/\\` and a length over 31 are illegal. The read
    side re-parses the stored string with `context_catalog.parse_alternation`,
    whose separators are `,` `;` a period followed by whitespace, ` and ` and
    ` or `. A title carrying one of those is offered in the create dropdown,
    serialises verbatim, and is then shredded on read into tokens that match
    nothing -- leaving an assay page with no components, no download, and no UI
    that can repair it.

    So the serialisation is read straight back here, against exactly the codes
    it was built from: if the structure does not survive, the write must not
    happen. `parse_alternation` is pure, so this stays a pure check -- no query,
    no request, nothing this function has to be told by a caller.
    """
    cleaned = _clean_groups(groups)
    if not cleaned:
        return ""
    codes = {code for group in cleaned for code in group}
    if parse_alternation(serialise_groups(cleaned), codes) == cleaned:
        return ""
    # Name the culprit rather than the whole role: a code that does not even
    # survive alone is the one the user has to remove.
    for group in cleaned:
        for code in group:
            if parse_alternation(code, {code}) != [[code]]:
                return code
    return cleaned[0][0]


def validate(name, description, required_parents, optional_parents, children,
             existing_slugs) -> dict:
    """The row `create_assay` will write, or raise ValidationError.

    `existing_slugs` is passed in rather than loaded here so this function stays
    pure and the caller controls how stale that set may be.
    """
    name = (name or "").strip()
    description = (description or "").strip()

    if not name:
        raise ValidationError("Give the assay a name.")
    if not description:
        raise ValidationError("Give the assay a short definition.")
    if not _clean_groups(children):
        raise ValidationError(
            "Name at least one sample type the assay produces. "
            "An assay that produces nothing has no templates to offer."
        )

    slug = slugify_name(name)
    if not slug:
        raise ValidationError("That name has no letters or digits in it.")
    if slug in (existing_slugs or set()):
        raise ValidationError(
            f"An assay with this name already exists ({slug}). "
            "Open it rather than creating a second one."
        )

    roles = {"required_parents": required_parents,
             "optional_parents": optional_parents,
             "children": children}
    for label, field in _ROLE_LABELS:
        offender = _unstorable_code(roles[field])
        if offender:
            raise ValidationError(
                f"The {label} sample type \u201c{offender}\u201d cannot be "
                "stored unambiguously: its name contains a comma, a semicolon, "
                "a period, \u201c and \u201d or \u201c or \u201d, and those "
                "are what separate sample types when the assay is read back. "
                "Leave it out, or have the sample type renamed."
            )

    return {
        "name": name,
        "description": description,
        "required_parents": serialise_groups(required_parents),
        "optional_parents": serialise_groups(optional_parents),
        "children": serialise_groups(children),
        "slug": slug,
    }


# Every column create_assay writes that comes from the FORM, keyed by its
# LOWERCASED name; the value is the key in validate()'s result.
# `internal_assay_id` is deliberately absent: it is filled in from the
# vocabulary write rather than from the form, so _insert_context appends it
# itself.
_WRITTEN = {
    "assay_name": "name",
    "description": "description",
    "required_parent_sample_types": "required_parents",
    "optional_parent_sample_types": "optional_parents",
    "children_sample_types": "children",
}


def actual_columns(cursor) -> dict:
    """Lowercased column name to the spelling this stack actually uses.

    `assay_context` has no Django model precisely because production and the
    seeded stacks disagree on case, and a model must commit to one spelling.
    Reads cope by lowercasing every column name in `_rows_from_cursor`; an
    INSERT cannot, because it has to name real columns. So it asks.
    """
    cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'assay_context'"
    )
    return {str(row[0]).lower(): str(row[0]) for row in cursor.fetchall()}


def _existing_slugs() -> set:
    """Slugs already in assay_context. Empty on failure, never raises."""
    try:
        return {entry.slug for entry in load_assays()}
    except Exception:
        logger.exception("assay catalog unavailable; duplicate check is skipped")
        return set()


def _insert_context(row: dict, internal_assay_id: int) -> int:
    """Write the assay_context row, returning its new id.

    `assay_name` and `internal_assay_id` are required in the introspected
    columns: every column here is NULLable, so silently skipping either would
    write a row with no name or no link to the vocabulary term -- the exact
    orphaned shape the module docstring says new rows must not join. Fail
    loudly instead; the other columns stay best-effort.
    """
    with connections[settings.NEXTSEEK_DATABASE].cursor() as cursor:
        columns = actual_columns(cursor)
        for required in ("assay_name", "internal_assay_id"):
            if required not in columns:
                raise RuntimeError(
                    f"assay_context has no '{required}' column on this stack; "
                    "refusing to write a row with no name or no vocabulary link"
                )
        names, values = [], []
        for lowered, key in _WRITTEN.items():
            if lowered in columns:
                names.append(columns[lowered])
                values.append(row[key])
        # Unconditional: the guard above has already raised if this column is
        # absent, so testing for it again could only ever be true.
        names.append(columns["internal_assay_id"])
        values.append(internal_assay_id)

        placeholders = ", ".join(["%s"] * len(values))
        joined = ", ".join(f"`{name}`" for name in names)
        cursor.execute(
            f"INSERT INTO assay_context ({joined}) VALUES ({placeholders})", values
        )
        return cursor.lastrowid


def create_assay(name, description, required_parents, optional_parents,
                 children) -> int:
    """Create an assay, returning its new `assay_context.id`.

    Two writes in one transaction on the NExtSEEK connection: the recipe row,
    and the `internal_assays` vocabulary term it links to. The term is what makes
    the assay registrable against real samples through the existing
    assay-hygiene workflow; 80 existing rows carry sample types with no such
    link, and new ones should not join them.

    Every failure leaves the caller a ValidationError carrying prose for the
    page, so the view has exactly one error path.
    """
    row = validate(name, description, required_parents, optional_parents,
                   children, _existing_slugs())

    try:
        with transaction.atomic(using=settings.NEXTSEEK_DATABASE):
            term = Internal_assays.objects.create(internal_assay_title=row["name"])
            return _insert_context(row, internal_assay_id=term.id)
    except Exception as exc:
        logger.exception("could not create assay %r", row["name"])
        raise ValidationError(
            "The assay could not be saved. Nothing was written; try again, and "
            "tell an administrator if it keeps happening."
        ) from exc
