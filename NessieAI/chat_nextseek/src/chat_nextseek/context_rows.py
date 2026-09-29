"""What a ``projects_context`` row is: a project, an investigation, or neither. One definition.

Every runtime reader of ``dmac.projects_context`` and of its export ``projects_db.json`` that
tells the two kinds apart reads it here: ``ChatConfig``'s project and investigation maps, the
``PROJECT_NAME_TO_ID`` merge and the labs injection, the system agent's ENTITY_DETAILS, and the
SEEK project page's header query (``nextseek_api.services.context_catalog``), which filters in
SQL through ``PROJECT_ROW_SQL``. Standard library only, because that page imports it.

Two shapes of row are live, and the code must be right on both:

* **The legacy shape**, which production holds and so does every box restored from it: most
  PROJECT rows are typed ``investigation`` with no ``parent_project``, one sub-project row is
  typed ``study``, and a few rows ``project``.
* **The shape the 2026-09-18 spec writes** (the gated 6.16 write): ``project`` rows, and
  ``investigation`` rows whose ``parent_project`` names the owning project.
  ``scripts/context_gen.py`` refuses an investigation row without one.

So:

* an **investigation** is typed ``investigation`` AND names a non-empty ``parent_project``;
* a **project** is typed ``project``, empty or missing, or is a legacy ``investigation`` row
  with no ``parent_project``;
* anything else (``study``) is **neither**: it reaches the entity agent with every other row,
  and no project or investigation map, name-to-id merge or labs list.

``entity_type`` compares case-insensitively, ignoring surrounding space. ``parent_project`` is
empty when it is missing, not text, or blank.
"""
from __future__ import annotations

PROJECT = "project"
INVESTIGATION = "investigation"


def entity_type(row) -> str:
    """The row's ``entity_type``, stripped and lower case; ``""`` when missing or not text."""
    value = row.get("entity_type") if isinstance(row, dict) else None
    return value.strip().lower() if isinstance(value, str) else ""


def names_parent_project(row) -> bool:
    """Whether the row's ``parent_project`` is non-empty text."""
    value = row.get("parent_project") if isinstance(row, dict) else None
    return isinstance(value, str) and bool(value.strip())


def is_investigation_row(row) -> bool:
    """Typed ``investigation`` AND naming its ``parent_project``."""
    return isinstance(row, dict) and entity_type(row) == INVESTIGATION and names_parent_project(row)


def is_project_row(row) -> bool:
    """Typed ``project``, untyped, or a legacy ``investigation`` row with no ``parent_project``."""
    if not isinstance(row, dict):
        return False
    kind = entity_type(row)
    return kind in ("", PROJECT) or (kind == INVESTIGATION and not names_parent_project(row))


# ``is_project_row`` as one parenthesised SQL condition over the table's own columns, for a
# reader that filters in the database. It holds in MySQL and SQLite alike, and
# NessieAI/tests/chat_nextseek/test_context_rows.py evaluates it against ``is_project_row`` on
# every shape. It carries no ``%``, so it can be spliced into a query with ``%s`` parameters.
PROJECT_ROW_SQL = (
    "(COALESCE(LOWER(TRIM(entity_type)), '') IN ('', 'project')"
    " OR (LOWER(TRIM(entity_type)) = 'investigation'"
    " AND COALESCE(TRIM(parent_project), '') = ''))"
)
