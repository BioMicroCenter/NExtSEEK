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

from nextseek_api.services.context_catalog import slugify_name

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """A proposal the user must fix. Carries prose meant for the page."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def serialise_groups(groups) -> str:
    """Alternation groups as the curator grammar.

        [["TIS", "CEX"], ["AB"]]  ->  "TIS or CEX, AB"
    """
    parts = [" or ".join(codes) for codes in (groups or []) if codes]
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
    if not [codes for codes in (children or []) if codes]:
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
