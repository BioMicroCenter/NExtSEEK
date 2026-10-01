"""One definition of what a projects_context row is: a project, an investigation, or neither.

Two shapes of row are live. Production's table, and every box restored from it, types most
PROJECT rows 'investigation' with no parent_project and one sub-project row 'study'. The rows
the 2026-09-18 spec writes (the gated 6.16 write) are 'project' rows and 'investigation' rows
that name their parent_project. So a row is an investigation only when it is typed
'investigation' AND names a parent_project; a row typed 'project', or untyped, or a legacy
'investigation' with no parent_project is a project; anything else ('study') is neither.

The config's maps, the labs injection and the system agent read the Python predicates; the
SEEK project page filters in SQL. Both come from `chat_nextseek.context_rows`, and the SQL is
evaluated here against the predicates on every shape, so the two cannot drift apart.
Every name is invented.
"""
from __future__ import annotations

import itertools
import sqlite3

import pytest

from chat_nextseek import context_rows


def _row(entity_type, parent_project):
    return {"name": "Alder", "entity_type": entity_type, "parent_project": parent_project, "project_id": 4}


# --- the shapes that exist -----------------------------------------------------------

@pytest.mark.parametrize("row", [
    _row("project", None),
    _row("project", "Alder"),            # a project row never needs one, and is not an investigation for it
    _row(None, None),                    # the installer's table allows a NULL type: it predates investigations
    _row("", None),
    _row("investigation", None),         # production's legacy project row
    _row("investigation", ""),
    _row("investigation", "   "),
    _row("Investigation", None),
    {"name": "Alder", "project_id": 4},  # no entity_type and no parent_project keys at all
])
def test_project_rows(row):
    assert context_rows.is_project_row(row)
    assert not context_rows.is_investigation_row(row)


@pytest.mark.parametrize("row", [
    _row("investigation", "Alder"),
    _row(" Investigation ", "Alder"),
    _row("INVESTIGATION", "Alder Project"),
])
def test_investigation_rows(row):
    assert context_rows.is_investigation_row(row)
    assert not context_rows.is_project_row(row)


@pytest.mark.parametrize("row", [
    _row("study", "Alder"),              # production's one sub-project row
    _row("study", None),
    _row("assay", None),
    None,
    "Alder",
    ["Alder"],
])
def test_neither(row):
    assert not context_rows.is_project_row(row)
    assert not context_rows.is_investigation_row(row)


def test_a_parent_project_that_is_not_text_is_no_parent():
    assert context_rows.is_project_row(_row("investigation", 4))
    assert not context_rows.is_investigation_row(_row("investigation", 4))


# --- the SQL condition is the same rule -----------------------------------------------

_TYPES = (None, "", " ", "project", "Project", " project ", "investigation", "INVESTIGATION",
          " investigation ", "study", "assay")
_PARENTS = (None, "", "   ", "Alder", " Alder ")


def test_the_sql_condition_selects_exactly_the_project_rows():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE projects_context (id INTEGER, name TEXT, entity_type TEXT, "
                 "project_id INTEGER, parent_project TEXT)")
    shapes = list(itertools.product(_TYPES, _PARENTS))
    conn.executemany("INSERT INTO projects_context VALUES (?, 'Alder', ?, 4, ?)",
                     [(i, kind, parent) for i, (kind, parent) in enumerate(shapes)])

    selected = {i for (i,) in conn.execute(
        "SELECT id FROM projects_context WHERE " + context_rows.PROJECT_ROW_SQL)}

    expected = {i for i, (kind, parent) in enumerate(shapes)
                if context_rows.is_project_row(_row(kind, parent))}
    assert selected == expected
    assert expected, "the matrix holds project rows"
    assert len(expected) < len(shapes), "and rows that are not"


def test_the_sql_condition_is_one_parenthesised_condition():
    """It is ANDed into other WHERE clauses, so it must not leak an OR."""
    sql = context_rows.PROJECT_ROW_SQL
    assert sql.startswith("(") and sql.endswith(")")
    depth = 0
    for index, char in enumerate(sql):
        depth += {"(": 1, ")": -1}.get(char, 0)
        assert depth > 0 or index == len(sql) - 1
    assert "%" not in sql, "it is spliced into a query that carries %s parameters"
