"""#93 — the advanced sample-search statement must not carry client values.

``seek/search.py``'s WHERE-fragment builders emit ``%s`` and hand back a params
list.  ``seek/dbtable_sample.py`` is the other half: it has to carry those params
from the builder (``__sqlQuery_select_records_filters_advanced``) through
``__sqlQuery_select_records`` and ``__retrieveRecords_advanced`` down to a
``cursor.execute`` that accepts them.  These tests pin that thread end to end —
the emitted statement text is a constant modulo the *shape* of the request (how
many rules), and every value the client picked travels in the parameter list.

Modelled on ``nextseek_api/tests/test_views.py::TestGetChildrenUIDsSQLInjection``:
the instance is built with ``DBtable_sample.__new__`` so ``__init__`` never
touches Django models, and the private methods are reached through name mangling
(``_DBtable_sample__sqlQuery_select_records``).

**Why the helpers below tolerate two return shapes.**  ``_select_records`` and
``test_the_payload_never_reaches_the_executed_statement`` accept BOTH the pre-#93
shape (a bare statement string / an execute through ``self.db.queryToListDics``)
and the post-#93 shape (a ``(sql, params)`` tuple / an execute through
``__retrieveCustomSQL``).  That is deliberate.  Normalising the shape here means
the injection assertions fail against pre-fix code *because the payload is in the
statement text*, not because a tuple unpack raised — which is what makes them
evidence of the vulnerability rather than a signature check on the new API.
"""

from unittest.mock import MagicMock, patch


# A quote-breakout that also changes the shape of the statement, so it is visible
# in the SQL text if it is spliced and invisible if it is bound.
PAYLOAD = "12' UNION SELECT id, title, sample_type_id FROM samples WHERE '1'='1"


def _instance():
    """Build the instance without __init__, which touches Django models."""
    from seek.dbtable_sample import DBtable_sample

    return DBtable_sample.__new__(DBtable_sample)


def _filters(sampletype_id, project_id=0, search_type="FILTERING", search_text=None):
    """The dict ``__initSearchFilters``/``__parseSearchFilters`` hands downstream.

    Only the keys ``__sqlQuery_select_records`` actually reads are set.  The
    client-controlled value on the FILTERING path is ``filterRules[0]['value']``
    — ``sampletype_id`` straight off ``request.GET`` (SPEC.md, ``seek/search.py``
    line 519 in the ``3d71a23`` table).  ``tableField``/``categoryField`` are
    server-set at ``seek/dbtable_sample.py:1937-1938``.
    """
    filtersdic = {
        "project_id": project_id,
        "orderby": " ",
        "startNo": 0,
        "endNo": 100,
        "searchType": search_type,
        "tableField": "json_metadata",
        "categoryField": "sample_type_id",
        "filterRules": [
            {"field": "sample_type_id", "op": "equal", "value": sampletype_id}
        ],
    }
    if search_text is not None:
        filtersdic["searchText"] = search_text
    return filtersdic


def _select_records(inst, filtersdic, with_limit=True):
    """Call ``__sqlQuery_select_records`` and normalise pre-/post-#93 shapes.

    Pre-#93 it returns a bare statement string; post-#93 a ``(sql, params)``
    tuple.  Returning ``params=None`` for the old shape lets the assertions below
    fail on the payload being in the SQL rather than on an unpack error.
    """
    out = inst._DBtable_sample__sqlQuery_select_records(filtersdic, with_limit)
    if isinstance(out, str):
        return out, None
    sql, params = out
    return sql, params


class TestStatementTextIsInvariantToClientValues:
    """The whole point of #93: the value changes the params, never the SQL."""

    def test_hostile_sampletype_id_never_reaches_the_statement_text(self):
        sql, params = _select_records(_instance(), _filters(PAYLOAD))

        assert PAYLOAD not in sql, "the request value was spliced into the statement"
        assert "UNION" not in sql.upper()
        assert params is not None, "the builder produced no params to bind"
        assert PAYLOAD in params, "the payload must survive verbatim as a bound value"

    def test_statement_is_character_identical_for_benign_and_hostile_values(self):
        inst = _instance()
        benign_sql, benign_params = _select_records(inst, _filters("12"))
        hostile_sql, hostile_params = _select_records(inst, _filters(PAYLOAD))

        assert hostile_sql == benign_sql
        assert benign_params == ["12"]
        assert hostile_params == [PAYLOAD]

    def test_placeholder_count_equals_param_count(self):
        """A mismatch here is what the cursor raises on at execute time."""
        sql, params = _select_records(_instance(), _filters(PAYLOAD, project_id=7))

        assert PAYLOAD not in sql
        assert params is not None
        assert sql.count("%s") == len(params)

    def test_uid_search_binds_every_uid_and_keeps_the_trailing_semicolon(self):
        """The UIDS path goes through ``__designSearchMatchKeywords``.

        Its trailing ``;`` is preserved on purpose (it is harmless today because
        ``orderby`` defaults to ``" "``, not ``""``), so pin it: a later reader
        removing it is a behaviour change, not a cleanup.
        """
        filtersdic = _filters(
            None, search_type="UIDs", search_text="D.ABC-1,D.ABC-2'; DROP TABLE samples--"
        )
        sql, params = _select_records(_instance(), filtersdic)

        assert "DROP TABLE" not in sql.upper()
        assert params is not None
        assert any("DROP TABLE" in str(p) for p in params)
        assert sql.count("%s") == len(params)
        assert ";" in sql


class TestProjectScopeIsBound:
    """``D.project_id`` is concatenated by dbtable_sample itself, not search.py."""

    def test_project_id_is_bound_not_interpolated(self):
        sql, params = _select_records(_instance(), _filters("12", project_id=7))

        assert "D.project_id=%s" in sql
        assert "D.project_id=7" not in sql
        assert params is not None
        assert params[-1] == 7

    def test_project_scope_keeps_the_where_paren_rewrite(self):
        """``.replace('WHERE ', 'WHERE (')`` + a closing ``)`` is preserved."""
        sql, _ = _select_records(_instance(), _filters("12", project_id=7))

        assert "WHERE (" in sql
        assert ") AND D.project_id=%s" in sql

    def test_project_id_zero_adds_neither_clause_nor_param(self):
        sql, params = _select_records(_instance(), _filters("12", project_id=0))

        assert "D.project_id" not in sql
        assert params == ["12"]


class TestCountBranchDropsTheWhereClause:
    def test_without_limit_returns_no_params(self):
        """``withLimit=False`` emits ``SELECT count(A.id)`` + FROM and DISCARDS
        the WHERE clause, so it must also discard the WHERE clause's params.
        Returning them would hand the cursor more params than placeholders.
        """
        sql, params = _select_records(
            _instance(), _filters(PAYLOAD, project_id=7), with_limit=False
        )

        assert "WHERE" not in sql.upper()
        assert PAYLOAD not in sql
        assert params == [], "the dropped WHERE clause's params must be dropped too"
        assert sql.count("%s") == len(params)


class TestRetrieveCustomSQLBindsParams:
    """``__retrieveCustomSQL`` is the execute site #93 had to add.

    ``dmac/dbconn_django.py:retrieve_custom_sql`` takes no params argument at
    all, and that file is outside this change's file set (SPEC.md, "The fix
    rejected"), so ``DBtable_sample`` grew a private duplicate that binds.
    """

    @staticmethod
    def _run(params, headers, rows):
        inst = _instance()
        inst.db = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = rows
        alias_conn = MagicMock()
        alias_conn.cursor.return_value = cursor

        # The method does `from django.db import connections` at call time, so
        # patching the module attribute is enough; a plain dict is a good enough
        # stand-in for ConnectionHandler's __getitem__.
        with patch("django.db.connections", {"seek": alias_conn}):
            out = inst._DBtable_sample__retrieveCustomSQL(
                "SELECT A.id FROM samples A WHERE A.sample_type_id=%s",
                headers,
                "seek",
                params,
            )
        return out, cursor, inst.db

    def test_params_are_handed_to_cursor_execute(self):
        out, cursor, db = self._run([PAYLOAD], ["id"], [(1,)])

        cursor.execute.assert_called_once_with(
            "SELECT A.id FROM samples A WHERE A.sample_type_id=%s", [PAYLOAD]
        )
        assert out == [{"id": 1}]
        db.queryToListDics.assert_not_called()

    def test_empty_params_delegates_to_the_untouched_path(self):
        """Back-compat: no placeholders means nothing changes for that caller."""
        inst = _instance()
        inst.db = MagicMock()
        inst.db.queryToListDics.return_value = [{"id": 1}]

        out = inst._DBtable_sample__retrieveCustomSQL(
            "SELECT A.id FROM samples A", ["id"], "seek", []
        )

        inst.db.queryToListDics.assert_called_once_with(
            "SELECT A.id FROM samples A", ["id"], "seek"
        )
        assert out == [{"id": 1}]

    def test_headers_wider_than_the_row_returns_early(self):
        """The dimension check copied verbatim from dbconn_django.py:307-309."""
        out, _, _ = self._run([PAYLOAD], ["id", "title", "uid"], [(1,)])

        assert out == []

    def test_headers_none_is_filled_in_positionally(self):
        out, _, _ = self._run([PAYLOAD], None, [(1, "x")])

        assert out == [{"0": 1, "1": "x"}]


class TestRetrieveRecordsAdvancedThreadsParamsToTheExecuteSite:
    @patch("seek.dbtable_sample.settings")
    def test_the_payload_never_reaches_the_executed_statement(self, mock_settings):
        """End to end through the private chain, whichever execute path runs.

        Pre-#93 the execute is ``self.db.queryToListDics(sql, ...)`` with the
        value already spliced in; post-#93 it is
        ``__retrieveCustomSQL(sql, headers, alias, params)``.  This inspects
        whichever one actually ran, so the assertion is about the statement text
        either way rather than about the new method existing.
        """
        mock_settings.SEEK_DATABASE = "seek"
        inst = _instance()
        inst.db = MagicMock()
        inst.db.queryToListDics.return_value = []
        inst.reformatDataForClient = MagicMock(return_value=[])

        seen = {}

        def _capture(sqlquery, headers, db_alias, params):
            seen["sql"] = sqlquery
            seen["params"] = params
            return []

        inst._DBtable_sample__retrieveCustomSQL = _capture

        inst._DBtable_sample__retrieveRecords_advanced(None, _filters(PAYLOAD))

        if seen:
            sql, params = seen["sql"], seen["params"]
        else:
            sql = inst.db.queryToListDics.call_args[0][0]
            params = None

        assert PAYLOAD not in sql, "the request value was spliced into the executed statement"
        assert "UNION" not in sql.upper()
        assert params is not None, "the executed statement carried no bound params"
        assert PAYLOAD in params


class TestFilterSamplesAdvancedUnpacksTheThreeTuple:
    def test_pubmed_keywords_are_still_extracted(self):
        """``__filterSamples_advanced`` discards the query and wants the keywords.

        ``designSearchPubmed`` became a 3-tuple in #93, so this call site is a
        pure unpack repair — but it is the one that 500s advanced PUBMED search
        if it is missed, so pin the keywords it hands to ``__highlightKeyValues``.
        """
        inst = _instance()
        filtersdic = _filters(0, search_type="PUBMED", search_text="CD8")
        filtersdic["sampletype_id"] = 0
        filtersdic["attribute"] = "none"
        filtersdic["matchType"] = "CONTAIN"

        seen = {}

        def _highlight(dici, terms, matchType):
            seen["terms"] = terms
            return ""

        inst._DBtable_sample__highlightKeyValues = _highlight
        inst._DBtable_sample__getRecordFromJson = lambda metadata: {}

        rows = [{"json_metadata": "{}", "sample_type_id": 12}]
        out = inst._DBtable_sample__filterSamples_advanced(rows, filtersdic)

        assert out == []
        assert seen["terms"] == ["CD8"]
