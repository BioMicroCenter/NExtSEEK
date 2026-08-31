"""DERIVED_FROM assay-label recompute.

assay_assets is the source of truth; the edge labels are derived from it.
backfill_shared_assays.py states it: "The graph cannot repair itself. What was
dropped exists only in seek_production.assay_assets." A membership write
therefore invalidates the labels on every edge incident to a written sample.
"""

import re
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings

from nextseek_api.assay_registration import graph


class TestCypherShape:
    def test_the_write_is_one_pass_with_a_server_side_map_lookup(self):
        """The obvious UNWIND $rows then MATCH form is a full DERIVED_FROM scan
        PER ROW, because this database has no property indexes at all. It died
        on TransactionTimedOutClientConfiguration at 5,000 rows. Measured, the
        one-pass form is flat in batch size: 0.40s for 3 edges and 0.50s for
        20,000 over a 514,067-edge graph.
        """
        cypher = graph.RECOMPUTE_CYPHER
        assert "UNWIND" not in cypher.upper(), "UNWIND-then-MATCH is the trap"
        assert "$edges[" in cypher, "must index the parameter map server-side"
        assert cypher.upper().count("MATCH") == 1, "exactly one pass"

    def test_the_write_only_touches_the_plural_fields(self):
        """internal_assay_id and internal_assay_title must never move: every
        existing consumer (entity_tree, the download workbook, chat_nextseek
        context, seek/views.py) reads them."""
        cypher = graph.RECOMPUTE_CYPHER
        assert "SET r.internal_assay_ids" in cypher
        assert "r.internal_assay_titles" in cypher
        assert "SET r.internal_assay_id " not in cypher
        assert "SET r.internal_assay_title " not in cypher


class TestRecompute:
    def test_no_samples_is_a_no_op(self):
        driver = MagicMock()
        assert graph.recompute_for_samples(set(), driver, "neo4j") == 0
        driver.session.assert_not_called()

    def test_no_incident_edges_is_a_no_op(self):
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value.data.return_value = []
        assert graph.recompute_for_samples({100}, driver, "neo4j") == 0

    def test_returns_the_edge_count_the_database_reported(self, monkeypatch):
        """monkeypatch, not assignment: a bare `graph.x = ...` leaks into every
        later test in the session."""
        monkeypatch.setattr(graph, "assays_by_sample",
                            lambda ids: {100: {351}, 200: {351}})
        monkeypatch.setattr(graph, "resolve_internal",
                            lambda ids: {351: (9, "Flow Cytometry")})

        edges_result = MagicMock()
        edges_result.data.return_value = [{"child_id": 100, "parent_id": 200}]
        write_result = MagicMock()
        write_result.single.return_value = {"written": 47}

        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.side_effect = [edges_result, write_result]

        assert graph.recompute_for_samples({100}, driver, "neo4j") == 47

        # The second call is the one-pass write, carrying the edges map.
        write_call = session.run.call_args_list[1]
        assert write_call.args[0] is graph.RECOMPUTE_CYPHER
        assert write_call.kwargs["edges"] == {
            "100_200": {"ids": [9], "titles": ["Flow Cytometry"]}
        }

    def test_an_edge_whose_endpoints_share_nothing_is_left_alone(self):
        """Only edges with a shared assay are written. An edge with none keeps
        whatever it had; this recompute never blanks a label."""
        edges_result = MagicMock()
        edges_result.data.return_value = [{"child_id": 100, "parent_id": 200}]
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value = edges_result

        with patch.object(graph, "assays_by_sample",
                          return_value={100: {351}, 200: {999}}), \
             patch.object(graph, "resolve_internal",
                          return_value={351: (9, "A"), 999: (10, "B")}):
            assert graph.recompute_for_samples({100}, driver, "neo4j") == 0


class TestBackfillScriptStillWorks:
    def test_the_script_imports_the_lifted_helpers(self):
        """One implementation, two callers."""
        import nextseek_api.batch_upload.scripts.backfill_shared_assays as script

        assert script.assays_by_sample is graph.assays_by_sample
        assert script.resolve_internal is graph.resolve_internal
        # Added after mutation testing: `_WRITE` is an ALIASED import, the one
        # binding that can silently point at the wrong query. Bound to the
        # read-only edges query instead, the script's --apply would write
        # nothing, report 0 and raise nothing.
        assert script._WRITE is graph.RECOMPUTE_CYPHER


# ---------------------------------------------------------------------------
# Hardening added after mutation testing. Every test below was written because
# a measured mutation of graph.py walked past the tests above; the docstrings
# name the mutation each one kills.
# ---------------------------------------------------------------------------

#: `r.<prop>` on the left of an `=`, in any spelling: spaced, unspaced, or on a
#: continuation line. Nothing in this query family uses `=` outside a SET.
_ASSIGNED = re.compile(r"\br\.(\w+)\s*=(?!=)")


def _norm(cypher: str) -> str:
    """Whitespace-normalised, so a guard cannot be evaded by a line break."""
    return " ".join(cypher.split())


class TestCypherShapeUnderMutation:
    def test_no_spelling_of_a_singular_write_survives(self):
        """`"SET r.internal_assay_id " not in cypher` pins one spelling of one
        line. Measured: putting the singular write back as a second, comma-
        continued clause on its own line passes that guard untouched -- the
        same shape that walked past task 5's deletion guard. Assert on the SET
        of assignment targets instead of on the text of one line.
        """
        assert set(_ASSIGNED.findall(graph.RECOMPUTE_CYPHER)) == {
            "internal_assay_ids", "internal_assay_titles",
        }

    def test_the_null_entry_guard_is_present(self):
        """Drop `WHERE entry IS NOT NULL` and the map lookup yields NULL for
        every edge not in this batch; `entry.ids` is then NULL, and Cypher
        REMOVES a property assigned NULL. The write would blank the plural
        fields on all 514,067 labelled edges instead of the handful asked for.
        Nothing in the brief's tests noticed.
        """
        assert "WHERE entry IS NOT NULL" in _norm(graph.RECOMPUTE_CYPHER)

    def test_the_count_comes_back_under_the_name_the_caller_reads(self):
        """`recompute_for_samples` reads `.single()["written"]`, and every unit
        test mocks that result, so nothing else pins the alias. Renaming it
        would raise only against a live graph."""
        assert "RETURN count(r) AS written" in _norm(graph.RECOMPUTE_CYPHER)

    def test_the_map_key_is_built_child_then_parent(self):
        """The payload is keyed f"{child}_{parent}". Build the key the other way
        round in Cypher and every lookup misses: the write reports 0 written and
        raises nothing, so the labels silently stay stale."""
        assert (
            '$edges[toString(r.child_id) + "_" + toString(r.parent_id)]'
            in _norm(graph.RECOMPUTE_CYPHER)
        )


class TestEdgesQuery:
    def test_incidence_counts_in_either_direction(self):
        """A written sample can be the child OR the parent of an incident edge.
        An AND here recomputes only edges with BOTH endpoints in the batch,
        which is almost none of them, and leaves every other incident edge
        stale. The edges query had no test of any kind."""
        assert (
            "r.child_id IN $sample_ids OR r.parent_id IN $sample_ids"
            in _norm(graph._EDGES_FOR_SAMPLES)
        )

    def test_it_is_the_first_call_and_carries_the_batch_ids(self):
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value.data.return_value = []

        graph.recompute_for_samples({200, 100}, driver, "neo4j")

        driver.session.assert_called_once_with(database="neo4j")
        first = session.run.call_args_list[0]
        assert first.args[0] is graph._EDGES_FOR_SAMPLES
        assert first.kwargs["sample_ids"] == [100, 200]


class TestNothingIsWrittenWithoutASharedAssay:
    def test_the_write_cypher_never_reaches_the_session(self):
        """The brief's version of this asserts the return is 0, which a write
        that reported 0 would also produce. Prove the stronger claim the
        docstring makes: RECOMPUTE_CYPHER is never sent, so the edge keeps
        whatever labels it had."""
        edges_result = MagicMock()
        edges_result.data.return_value = [{"child_id": 100, "parent_id": 200}]
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value = edges_result

        with patch.object(graph, "assays_by_sample",
                          return_value={100: {351}, 200: {999}}), \
             patch.object(graph, "resolve_internal",
                          return_value={351: (9, "A"), 999: (10, "B")}):
            graph.recompute_for_samples({100}, driver, "neo4j")

        assert session.run.call_count == 1
        assert [c.args[0] for c in session.run.call_args_list] == [
            graph._EDGES_FOR_SAMPLES
        ]

    def test_a_shared_assay_that_resolves_to_nothing_is_dropped_not_defaulted(self):
        """`if a in internal` is load-bearing. Defaulting an unresolvable assay
        to a placeholder would write id 0 and an empty title onto a real edge."""
        edges_result = MagicMock()
        edges_result.data.return_value = [{"child_id": 100, "parent_id": 200}]
        write_result = MagicMock()
        write_result.single.return_value = {"written": 1}
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.side_effect = [edges_result, write_result]

        with patch.object(graph, "assays_by_sample",
                          return_value={100: {351, 888}, 200: {351, 888}}), \
             patch.object(graph, "resolve_internal",
                          return_value={351: (9, "Flow Cytometry")}):
            graph.recompute_for_samples({100}, driver, "neo4j")

        assert session.run.call_args_list[1].kwargs["edges"] == {
            "100_200": {"ids": [9], "titles": ["Flow Cytometry"]}
        }


class TestTheSeekAlias:
    def test_the_cursor_is_opened_on_seek_not_on_the_empty_dmac_copy(self):
        """TRAP 1, the module docstring's headline warning, previously with no
        test at all. `dmac.assay_assets` EXISTS but is EMPTY, so the wrong alias
        returns a confident and entirely wrong answer instead of an error --
        once "0% of edges share an assay" for a test set AND its control."""
        conns = MagicMock()
        with patch.object(graph, "connections", conns):
            _cursor, name = graph._seek_cursor()

        assert settings.SEEK_DATABASE != settings.NEXTSEEK_DATABASE
        conns.__getitem__.assert_called_once_with(settings.SEEK_DATABASE)
        assert name == settings.DATABASES[settings.SEEK_DATABASE]["NAME"]


class TestAssaysBySample:
    def test_it_reads_only_Sample_rows_from_the_seek_table(self):
        """Without `asset_type = 'Sample'` the same query also returns the
        assays of a DataFile or Model whose id happens to equal a sample id."""
        cursor = MagicMock()
        cursor.fetchall.return_value = [(100, 351), (100, 352), (200, 351)]
        with patch.object(graph, "_seek_cursor",
                          return_value=(cursor, "seek_production")):
            out = graph.assays_by_sample({200, 100})

        sql, params = cursor.execute.call_args.args
        assert "seek_production.assay_assets" in sql
        assert "asset_type = 'Sample'" in sql
        assert params == [100, 200]
        assert out == {100: {351, 352}, 200: {351}}

    def test_an_empty_result_is_an_empty_map_not_a_SystemExit(self):
        """The behavioural change this task makes. The backfill script raised
        SystemExit here as its guard against trap 1; that guard now lives in the
        script's own plan(), because an endpoint registering a brand-new sample
        can legitimately get nothing back and a SystemExit out of a web request
        would be absurd."""
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        with patch.object(graph, "_seek_cursor",
                          return_value=(cursor, "seek_production")):
            assert graph.assays_by_sample({100}) == {}


class TestResolveInternal:
    def test_the_junction_table_answers_first(self):
        cursor = MagicMock()
        cursor.fetchall.side_effect = [[(351, 9, "Flow Cytometry")], []]
        with patch.object(graph, "_seek_cursor",
                          return_value=(cursor, "seek_production")):
            assert graph.resolve_internal({351}) == {351: (9, "Flow Cytometry")}

    def test_an_assay_with_no_internal_mapping_falls_back_to_its_own_id(self):
        """Drop the fallback pass and an unmapped assay vanishes from the
        recompute, so its edges keep a stale label and no one is told."""
        cursor = MagicMock()
        cursor.fetchall.side_effect = [[], [(742, "Bulk RNA-seq")]]
        with patch.object(graph, "_seek_cursor",
                          return_value=(cursor, "seek_production")):
            assert graph.resolve_internal({742}) == {742: (742, "Bulk RNA-seq")}


class TestTheScriptKeepsItsOwnGuard:
    def test_plan_still_refuses_an_empty_assay_assets(self):
        """The SystemExit moved out of assays_by_sample and into plan(). It must
        still fire there: an empty map across EVERY edge in the graph is the
        signature of querying the empty dmac copy, not real data."""
        import nextseek_api.batch_upload.scripts.backfill_shared_assays as script

        driver = MagicMock()
        driver.execute_query.return_value = (
            [{"child_id": 100, "parent_id": 200,
              "current_id": 9, "current_title": "Flow Cytometry"}],
            None, None,
        )
        with patch.object(script, "assays_by_sample", return_value={}):
            with pytest.raises(SystemExit, match="empty dmac copy"):
                script.plan(driver, "neo4j")
