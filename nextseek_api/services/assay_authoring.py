"""Creating an assay: the only writer of `assay_context`.

Two halves, kept apart so the grammar can be tested with no database. `validate`
and `serialise_groups` are pure; `create_assay` (Task 3) does the writing.

Codes are written back in exactly the grammar `context_catalog.parse_alternation`
reads -- groups joined by ", ", alternatives within a group by " or " -- so a
created assay round-trips through the same parser that reads curated ones. The
round trip is pinned by a test rather than left to inspection.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import connections, transaction

from seek.models import Internal_assays
from nextseek_api.services.context_catalog import load_assays, slugify_name

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

    return {
        "name": name,
        "description": description,
        "required_parents": serialise_groups(required_parents),
        "optional_parents": serialise_groups(optional_parents),
        "children": serialise_groups(children),
        "slug": slug,
    }


# Every column create_assay writes, keyed by its LOWERCASED name. The value is
# the key in validate()'s result, or None for a column filled in from the
# vocabulary write rather than from the form.
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
    """Write the assay_context row, returning its new id."""
    with connections[settings.NEXTSEEK_DATABASE].cursor() as cursor:
        columns = actual_columns(cursor)
        names, values = [], []
        for lowered, key in _WRITTEN.items():
            if lowered in columns:
                names.append(columns[lowered])
                values.append(row[key])
        if "internal_assay_id" in columns:
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
