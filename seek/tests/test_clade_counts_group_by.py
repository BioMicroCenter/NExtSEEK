"""Regression tests: the projects page clade tables (#14).

``DBtable_sample_types_clades.getAllCounts`` selected ``c.title``, ``c.color``
and ``st.id`` without aggregation under a bare ``GROUP BY st.description``.
MySQL's ``ONLY_FULL_GROUP_BY`` -- on by default in the ``mysql:8.0`` image and
not overridden anywhere in this tree -- rejects that with error 1055:

    Expression #1 of SELECT list is not in GROUP BY clause and contains
    nonaggregated column 'dmac.c.title' which is not functionally dependent
    on columns in GROUP BY clause

``seek.views.projects`` wraps the call in try/except and falls back to an empty
``clade_data``, so the page rendered fine but permanently *without* its clade
tables. The guard stays; the SQL is what had to change.

These tests are hermetic: they capture the SQL the method emits and check it
against MySQL's ONLY_FULL_GROUP_BY rule rather than needing a live server.
``__init__`` is bypassed because ``DBconn_django.__init__`` opens a cursor;
``getAllCounts`` only needs ``self.dbname`` and the private query sender.
"""

import re

import pytest

from dmac.dbtable_sampletypesclades import DBtable_sample_types_clades

# Primary key of each table alias used by getAllCounts. Grouping by a table's
# PK makes every other column of that table functionally dependent on the
# grouping key, which ONLY_FULL_GROUP_BY accepts.
ALIAS_PRIMARY_KEY = {
    "c": "c.id",  # dmac.clades
    "st": "st.id",  # seek_production.sample_types
    "stc": "stc.id",  # dmac.sample_types_clades
    "s": "s.id",  # seek_production.samples
}

AGGREGATE_FUNCTIONS = ("count", "any_value", "sum", "min", "max", "avg", "group_concat")

COLUMN_REF = re.compile(r"\b([a-z_]+)\.([a-z_]+)\b", re.IGNORECASE)


@pytest.fixture
def emitted_sql():
    """The SQL getAllCounts actually sends, with the private sender stubbed."""
    table = DBtable_sample_types_clades.__new__(DBtable_sample_types_clades)
    table.dbname = "dmac"
    captured = []

    def _capture(query):
        captured.append(query)
        return []

    # __sendQuery is name-mangled on the class that defines it.
    table._DBtable_sample_types_clades__sendQuery = _capture
    table.getAllCounts()

    assert len(captured) == 1, "getAllCounts should emit exactly one query"
    return " ".join(captured[0].split())


def _clause(sql, keyword, terminators):
    stop = "|".join(terminators) if terminators else "$"
    match = re.search(
        rf"\b{keyword}\b\s+(.*?)(?=\s+\b(?:{stop})\b|$)", sql, re.IGNORECASE
    )
    return match.group(1) if match else ""


def _split_top_level(clause):
    """Split a comma-separated clause, ignoring commas inside parentheses."""
    items, depth, current = [], 0, []
    for char in clause:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if "".join(current).strip():
        items.append("".join(current).strip())
    return items


def _is_aggregated(expression):
    lowered = expression.lower()
    return any(f"{fn}(" in lowered for fn in AGGREGATE_FUNCTIONS)


def _uncovered_columns(expressions, grouped):
    """Column refs that ONLY_FULL_GROUP_BY would reject."""
    offenders = []
    for expression in expressions:
        if _is_aggregated(expression):
            continue
        for alias, column in COLUMN_REF.findall(expression):
            reference = f"{alias}.{column}".lower()
            if reference in grouped:
                continue
            if ALIAS_PRIMARY_KEY.get(alias.lower(), "").lower() in grouped:
                continue
            offenders.append(reference)
    return offenders


class TestGetAllCountsIsOnlyFullGroupBySafe:
    def test_every_selected_column_is_grouped_or_aggregated(self, emitted_sql):
        select_clause = _clause(emitted_sql, "SELECT", ["FROM"])
        group_clause = _clause(emitted_sql, "GROUP BY", ["ORDER BY", "HAVING", "LIMIT"])
        grouped = {item.strip().lower() for item in _split_top_level(group_clause)}

        assert grouped, "getAllCounts must still aggregate"

        offenders = _uncovered_columns(_split_top_level(select_clause), grouped)
        assert not offenders, (
            "SELECT columns not covered by GROUP BY (MySQL error 1055 under "
            f"ONLY_FULL_GROUP_BY): {sorted(set(offenders))}"
        )

    def test_order_by_columns_are_grouped_or_aggregated(self, emitted_sql):
        # ONLY_FULL_GROUP_BY applies to ORDER BY as well as the SELECT list --
        # `ORDER BY c.order` is only legal because c.id is in the GROUP BY.
        group_clause = _clause(emitted_sql, "GROUP BY", ["ORDER BY", "HAVING", "LIMIT"])
        order_clause = _clause(emitted_sql, "ORDER BY", ["LIMIT"])
        grouped = {item.strip().lower() for item in _split_top_level(group_clause)}

        offenders = _uncovered_columns(_split_top_level(order_clause), grouped)
        assert not offenders, (
            "ORDER BY columns not covered by GROUP BY (MySQL error 1055 under "
            f"ONLY_FULL_GROUP_BY): {sorted(set(offenders))}"
        )

    def test_row_shape_the_projects_template_consumes_is_unchanged(self, emitted_sql):
        # projectsList.html reads .st_group / .count / .color off each row and
        # seek.views.projects groups on .title, so the aliases are load-bearing.
        for alias in ("AS title", "AS color", "AS st_id", "AS st_group", "AS count"):
            assert alias in emitted_sql, f"missing column alias: {alias}"

    def test_still_grouped_one_row_per_sample_type_description(self, emitted_sql):
        # The template renders one row per sample-type description within a
        # clade; grouping must not collapse or fan those out.
        group_clause = _clause(emitted_sql, "GROUP BY", ["ORDER BY", "HAVING", "LIMIT"])
        grouped = {item.strip().lower() for item in _split_top_level(group_clause)}
        assert "st.description" in grouped
