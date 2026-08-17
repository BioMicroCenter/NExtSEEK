"""Regression tests: ``seek.views.get_children_uids`` inlined its SQL ``IN`` lists (#78).

This is the *second* copy of the defect fixed by ``feaa816``. That fix was scoped
to ``seek/dbtable_sample.py``; this function is a duplicate of it living in the
view layer on a different URL, and it was outside that scope.

``get_children_uids`` built both ``IN`` lists as quoted string literals --
``', '.join(f"'{uid}'" for uid in uids)`` -- and called ``cursor.execute(query)``
with no parameter argument, so a single quote in either list breaks out of the
literal.

Reachable at ``POST ^seek/admin/retrieve/`` (``seek/urls.py:21`` ->
``adminRetrieveSamples``), behind ``seekdb.getSeekLogin`` -- authenticated, but
not necessarily privileged. Second-order: ``request.POST['retrieval_uids']`` is
bound correctly into the Cypher via ``$sample_uids``; the values that reach the
SQL literal are ``r[0]['uuids']`` read back out of Neo4j, so a hostile uuid has
to be written into the graph first. ``user_project_ids`` comes from SEEK's
``getCurrentUser()`` and is the other half of the same pair of lines.

Hermetic, like the rest of ``seek/tests`` -- there is no conftest here and no
database. ``seek.views`` is imported inside the helper so collection does not
depend on the Django app registry being ready at module import.

The twin guards are ``nextseek_api/tests/test_views.py`` ::
``TestGetChildrenUIDsSQLInjection`` (the ``dbtable_sample`` copy, #78) and
``TestAdminSampleRetrieveSQLInjection`` (the ``nextseek_api`` copy, 7698848).
"""

from unittest.mock import MagicMock, Mock, patch

import pytest


@pytest.fixture(autouse=True)
def _import_seek_views(settings):
    """Import seek.views with the settings its module scope requires.

    seek/views.py:89-104 reads several values from the gitignored
    local_settings.py (bind-mounted at runtime, absent from the image and from a
    fresh checkout) at import time. Injecting the local_settings.example.py
    surface keeps this hermetic -- verified: without it all four tests fail on
    django.conf AttributeError before reaching an assertion.

    Same surface as the ``seek_views`` fixture in test_seek_public_links.py:89.
    Autouse rather than a named fixture on purpose: mock.patch appends its mocks
    as trailing positional args, and pytest strips exactly that many names off
    the end of the signature when resolving fixtures, so a named fixture mixed
    with @patch decorators binds to the wrong parameter.
    """
    settings.ASSISTANT_PARTICIPATING_PROJECTS = {"1"}
    settings.TEST_CASES = {}
    settings.SAMPLE_TEMPLATES_FOLDER = "/templates"
    settings.SAMPLE_TEMPLATES_FOLDER_PROJECT = "1"
    settings.PUBLISH_URL = "https://fairdomhub.org"
    settings.PUBLISH_STATS_FILE = "/tmp/published_stats.xlsx"
    settings.SMART_SEARCH_URL = "iframe url"
    settings.SEEK_PUBLIC_URL = "https://seek.example.org"
    import seek.views  # noqa: F401


# Bound at import time in seek/views.py:89 from the *real* settings, so it stays
# a plain string even while seek.views.settings is patched.
SEEK_DB_ALIAS = "seek"

MOCK_DB = {
    SEEK_DB_ALIAS: {
        "HOST": "localhost",
        "USER": "u",
        "PASSWORD": "p",
        "NAME": "testdb",
    },
    "default": {"NAME": "dmac"},
}

NEO4J_DB = {
    "URI": "bolt://localhost:7687",
    "AUTH": ("neo4j", "password"),
    "NAME": "neo4j",
}

_VIEWS = "seek.views"


def _setup_settings(mock_settings):
    mock_settings.DATABASES = MOCK_DB
    mock_settings.NEO4J_DATABASE = NEO4J_DB


def _driver(graph_result):
    """A neo4j driver usable as a context manager, per seek/views.py:1180."""
    d = MagicMock()
    d.__enter__ = Mock(return_value=d)
    d.__exit__ = Mock(return_value=False)
    d.execute_query.return_value = graph_result
    return d


def _mysql_cursor(fetchall_val, description):
    cur = MagicMock()
    cur.fetchall.return_value = fetchall_val
    cur.description = description
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


class TestSeekViewsGetChildrenUIDsSQLInjection:

    _PAYLOAD = (
        "NHP-1' UNION SELECT id, sample_type_id, uuid, json_metadata "
        "FROM testdb.samples WHERE '1'='1"
    )

    _DESCRIPTION = [("id",), ("sample_type_id",), ("uuid",), ("json_metadata",)]

    @staticmethod
    def _executed(cur):
        """Return (sql, params) for the SELECT the view issued."""
        args, kwargs = cur.execute.call_args
        sql = args[0]
        params = args[1] if len(args) > 1 else kwargs.get("args")
        return sql, params

    def _run(self, mock_graph, mock_mysql, graph_uids, project_ids, admin, rows=None):
        from seek.views import get_children_uids

        # The graph query is what supplies the uuids that reach the SQL.
        mock_graph.driver.return_value = _driver(
            ([{"uuids": list(graph_uids)}], None, None)
        )
        conn, cur = _mysql_cursor(
            fetchall_val=[(1, 12, "NHP-1", "{}")] if rows is None else rows,
            description=self._DESCRIPTION,
        )
        mock_mysql.connect.return_value = conn

        # user_project_ids arrives as a single-pass map() from views.py:1247.
        df = get_children_uids(["NHP-1"], iter(project_ids), admin)
        return df, cur

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.settings")
    def test_uids_from_the_graph_are_bound_not_interpolated(
        self, mock_s, mock_graph, mock_mysql
    ):
        _setup_settings(mock_s)
        uids = [self._PAYLOAD, "TIS-2"]

        df, cur = self._run(mock_graph, mock_mysql, uids, [1], True)

        sql, params = self._executed(cur)
        assert params is not None, (
            "graph uuids were interpolated into the statement, not bound"
        )
        assert list(params) == uids
        assert self._PAYLOAD not in sql
        assert "UNION" not in sql.upper()
        assert "uuid IN (%s, %s)" in sql
        assert not df.empty

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.settings")
    def test_quote_in_uid_does_not_change_the_statement(
        self, mock_s, mock_graph, mock_mysql
    ):
        """Two runs over same-length uuid lists must produce identical SQL."""
        _setup_settings(mock_s)
        seen = []
        for value in ("NHP-1", "O'Brien'; DROP TABLE samples; --"):
            _, cur = self._run(mock_graph, mock_mysql, [value, "TIS-2"], [1], True)
            seen.append(self._executed(cur)[0])
        assert seen[0] == seen[1]

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.settings")
    def test_project_scoped_branch_binds_uids_and_project_ids(
        self, mock_s, mock_graph, mock_mysql
    ):
        """The non-superuser branch interpolated the project ids too."""
        _setup_settings(mock_s)
        uids = [self._PAYLOAD, "TIS-2"]

        df, cur = self._run(mock_graph, mock_mysql, uids, ["7", "8"], False)

        sql, params = self._executed(cur)
        assert params is not None, "uuids/project ids were interpolated, not bound"
        assert list(params) == [self._PAYLOAD, "TIS-2", "7", "8"]
        assert self._PAYLOAD not in sql
        assert "'7'" not in sql and "'8'" not in sql
        assert "s.uuid IN (%s, %s)" in sql
        assert "ps.project_id IN (%s, %s)" in sql

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.settings")
    def test_no_project_ids_still_matches_nothing(self, mock_s, mock_graph, mock_mysql):
        """A caller with no mapped projects produced ``ps.project_id IN ()``, a
        MySQL syntax error out of an unwrapped ``cursor.execute`` -> 500. Bind a
        sentinel instead, exactly as nextseek_api/views.py:841 does: valid SQL
        that matches nothing.

        This does not stop the request 500ing -- ``adminRetrieveSamples`` has no
        empty-frame guard, so the empty result now fails later in
        ``write_samples_workbook``. The statement is what had to become valid.
        """
        _setup_settings(mock_s)

        df, cur = self._run(mock_graph, mock_mysql, ["NHP-1"], [], False, rows=[])

        sql, params = self._executed(cur)
        assert params is not None
        assert list(params) == ["NHP-1", ""]
        assert "IN ()" not in sql
        assert "ps.project_id IN (%s)" in sql
        assert df.empty
