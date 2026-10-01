"""The SQL readers left in `graph.py` after its graph write path moved to graph_sync.

This module used to compute DERIVED_FROM assay labels and write them itself.
One rule now owns those labels for every writer (the sync spec's section 7.3):
a membership write enqueues a `samples` row and the drain relabels every edge
incident to those samples, both directions, from the same MySQL sources. So
`RECOMPUTE_CYPHER`, `_EDGES_FOR_SAMPLES` and `recompute_for_samples` are gone,
and the tests that pinned them went with them --
`nextseek_api/tests/test_graph_sync_hook_assay_registration.py` covers what
replaced them, and `nextseek_api/tests/test_graph_sync_labels.py` covers the
rule itself.

What is left is SQL. assay_assets is still the source of truth, and these two
readers still resolve a sample's assays and their internal-assay mapping, so
trap 1 in the module docstring is still live and still the reason every one of
them is tested: the `default` alias is dmac, whose assay_assets table EXISTS but
is EMPTY, so the wrong alias returns a confident and entirely wrong answer.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings

from nextseek_api.assay_registration import graph


class TestTheSeekAlias:
    #: The two aliases resolve to the SAME name (":memory:") under test settings,
    #: so asserting the resolved name against the real settings proves nothing:
    #: it holds for either alias. Substitute a settings object whose two aliases
    #: resolve differently, and the resolved name becomes discriminating.
    _SETTINGS = SimpleNamespace(
        SEEK_DATABASE="seek",
        NEXTSEEK_DATABASE="default",
        DATABASES={"seek": {"NAME": "seek_production"}, "default": {"NAME": "dmac"}},
    )

    def test_the_real_settings_have_the_shape_the_stub_models(self):
        """The stub below hardcodes the alias names, so pin them here rather
        than let the stub quietly stop describing the real config."""
        assert settings.SEEK_DATABASE == "seek"
        assert settings.NEXTSEEK_DATABASE == "default"

    def test_the_cursor_is_opened_on_seek_not_on_the_empty_dmac_copy(self):
        """TRAP 1, the module docstring's headline warning, previously with no
        test at all. `dmac.assay_assets` EXISTS but is EMPTY, so the wrong alias
        returns a confident and entirely wrong answer instead of an error --
        once "0% of edges share an assay" for a test set AND its control."""
        conns = MagicMock()
        with patch.object(graph, "settings", self._SETTINGS), \
             patch.object(graph, "connections", conns):
            _cursor, name = graph._seek_cursor()

        conns.__getitem__.assert_called_once_with("seek")
        assert name == "seek_production"

    def test_it_logs_the_resolved_database_for_the_operator(self, caplog):
        """The operator's LIVE confirmation of trap 1 during a backfill dry run.
        Reading the resolved database name off the log is what catches a run
        that silently went to the empty dmac copy; without it the dry run looks
        identical either way. The lift dropped this line once already."""
        conns = MagicMock()
        with caplog.at_level(logging.INFO, logger=graph.log.name):
            with patch.object(graph, "settings", self._SETTINGS), \
                 patch.object(graph, "connections", conns):
                graph._seek_cursor()

        # `assert "seek" in caplog.text` would be vacuous here: "seek_production"
        # contains it, and so does the logger name. Assert the record's args, which
        # pins BOTH halves exactly -- alias and resolved name -- and so also catches
        # a line that logs the database without saying which alias produced it.
        assert len(caplog.records) == 1
        assert caplog.records[0].args == ("seek", "seek_production")
        assert "seek_production" in caplog.text, "and it survives formatting"


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
        """The backfill script raised SystemExit here as its guard against trap
        1; that guard lives in the script's own plan(), because a caller asking
        about a brand-new sample can legitimately get nothing back and a
        SystemExit out of a web request would be absurd."""
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
        answer, so its caller sees a sample as belonging to nothing at all."""
        cursor = MagicMock()
        cursor.fetchall.side_effect = [[], [(742, "Bulk RNA-seq")]]
        with patch.object(graph, "_seek_cursor",
                          return_value=(cursor, "seek_production")):
            assert graph.resolve_internal({742}) == {742: (742, "Bulk RNA-seq")}


class TestTheChunkConstant:
    def test_the_chunk_size_is_named_and_is_the_size_actually_used(self):
        """`BATCH_SIZE = 1000` was dead while the real chunk was a repeated 5000
        literal, so tuning the named constant changed nothing. SQL_CHUNK, not
        CHUNK: resolver.py defines its own CHUNK = 1000 in this same package."""
        assert graph.SQL_CHUNK == 5000
        assert not hasattr(graph, "BATCH_SIZE")

    def test_assays_by_sample_chunks_at_that_size(self):
        """Proves the constant is wired to the loop rather than shadowed by a
        literal: shrink it and the number of round trips must change."""
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        with patch.object(graph, "SQL_CHUNK", 2), \
             patch.object(graph, "_seek_cursor",
                          return_value=(cursor, "seek_production")):
            graph.assays_by_sample({1, 2, 3, 4, 5})

        assert cursor.execute.call_count == 3
        assert [c.args[1] for c in cursor.execute.call_args_list] == [
            [1, 2], [3, 4], [5],
        ]

    def test_resolve_internal_chunks_at_that_size_in_BOTH_loops(self):
        """The previous version of this test exercised only assays_by_sample, so
        shadowing SQL_CHUNK at either of resolve_internal's two loops survived.
        That was a gap in mutation SELECTION, not in the arithmetic. Every row
        comes back unresolved, so both the junction loop and the id-fallback loop
        run over all five ids: three chunks each, six round trips.
        """
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        with patch.object(graph, "SQL_CHUNK", 2), \
             patch.object(graph, "_seek_cursor",
                          return_value=(cursor, "seek_production")):
            graph.resolve_internal({1, 2, 3, 4, 5})

        assert cursor.execute.call_count == 6
        assert [c.args[1] for c in cursor.execute.call_args_list] == [
            [1, 2], [3, 4], [5], [1, 2], [3, 4], [5],
        ]


class TestTheWritePathIsGone:
    """The one rule that owns DERIVED_FROM labels lives in graph_sync now.

    Asserted rather than merely deleted: a second writer of those labels is how
    the dev box ended up with 1,213,093 edges whose singular fields were right
    and whose plural lists were written by something else (the sync spec's
    section 1.1). If any of these names comes back here, there are two rules
    again.
    """

    def test_no_cypher_and_no_recompute_are_left_in_this_module(self):
        for name in ("RECOMPUTE_CYPHER", "_EDGES_FOR_SAMPLES",
                     "recompute_for_samples"):
            assert not hasattr(graph, name), f"{name} belongs to graph_sync now"

    def test_the_module_opens_no_graph_session(self):
        """`driver.session(...)` was the write path's only door to Neo4j.

        Scanned below the module docstring on purpose: that docstring says what
        left and where it went, so a scan over the whole file would fail on its
        own explanation and invite the next reader to delete the explanation
        rather than the code.
        """
        import inspect

        code = inspect.getsource(graph).split('"""', 2)[2]
        assert "session(" not in code
        assert "DERIVED_FROM" not in code
        assert "MATCH (" not in code
