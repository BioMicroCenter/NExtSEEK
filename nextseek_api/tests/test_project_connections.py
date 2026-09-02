"""The project-scoped connections helper: query, cache, and failure behaviour."""

from unittest.mock import patch

import pytest
from django.core.cache import cache
from neo4j.exceptions import Neo4jError

from nextseek_api.services import project_connections as pc

ROWS = [
    {"parent_sample_type": "NHP", "child_sample_type": "PAV",
     "internal_assay": "Necropsy", "n_edges": 706},
    {"parent_sample_type": "PAV", "child_sample_type": "TIS",
     "internal_assay": "Tissue Collection", "n_edges": 5210},
]


@pytest.fixture(autouse=True)
def _clear_cache():
    """Autouse, not setup_function.

    setup_function runs only for module-level test functions and never for a
    method on a class, so with it the class-based cases below shared one warm
    cache and three of them passed or failed on whatever ran before them.
    """
    cache.clear()
    yield
    cache.clear()


class TestConnectionRows:
    @patch("nextseek_api.services.project_connections.run_connections_query",
           return_value=ROWS)
    def test_the_project_id_is_passed_as_the_seek_investigation_selector(self, run):
        assert pc.connection_rows(2) == ROWS
        selector = run.call_args[0][0]
        assert selector.seek_inv_id == 2
        assert selector.graph_inv_id is None
        assert selector.sample_type is None

    @patch("nextseek_api.services.project_connections.run_connections_query")
    def test_a_graph_failure_is_no_rows_not_an_exception(self, run):
        run.side_effect = Neo4jError("connection refused")
        assert pc.connection_rows(2) == []

    @patch("nextseek_api.services.project_connections.run_connections_query")
    def test_any_other_failure_is_also_swallowed(self, run):
        run.side_effect = RuntimeError("boom")
        assert pc.connection_rows(2) == []


class TestConnectionsHtml:
    @patch("nextseek_api.services.project_connections.rows_to_html",
           return_value="<html>diagram</html>")
    @patch("nextseek_api.services.project_connections.fetch_clade_map", return_value={})
    @patch("nextseek_api.services.project_connections.run_connections_query",
           return_value=ROWS)
    def test_a_second_call_is_served_from_cache(self, run, _clades, _html):
        assert pc.connections_html(2) == "<html>diagram</html>"
        assert pc.connections_html(2) == "<html>diagram</html>"
        assert run.call_count == 1

    @patch("nextseek_api.services.project_connections.rows_to_html")
    @patch("nextseek_api.services.project_connections.fetch_clade_map", return_value={})
    @patch("nextseek_api.services.project_connections.run_connections_query",
           return_value=[])
    def test_no_rows_is_an_empty_string_and_is_not_cached(self, run, _clades, html):
        """An empty result is usually Neo4j being down, and caching that for an
        hour would turn a blip into an outage the page cannot recover from."""
        assert pc.connections_html(2) == ""
        assert pc.connections_html(2) == ""
        assert run.call_count == 2
        html.assert_not_called()

    @patch("nextseek_api.services.project_connections.rows_to_html",
           return_value="<html>diagram</html>")
    @patch("nextseek_api.services.project_connections.fetch_clade_map", return_value={})
    @patch("nextseek_api.services.project_connections.run_connections_query",
           return_value=ROWS)
    def test_each_project_caches_separately(self, run, _clades, _html):
        pc.connections_html(2)
        pc.connections_html(3)
        assert run.call_count == 2

    @patch("nextseek_api.services.project_connections.rows_to_html",
           return_value="<html>diagram</html>")
    @patch("nextseek_api.services.project_connections.fetch_clade_map", return_value={})
    @patch("nextseek_api.services.project_connections.run_connections_query",
           return_value=ROWS)
    def test_the_title_names_the_project(self, run, _clades, html):
        pc.connections_html(2, title="IMPAcTb sample flow")
        assert html.call_args[1]["title"] == "IMPAcTb sample flow"


class TestTypesInUse:
    def test_both_sides_of_every_edge_are_collected_and_deduped(self):
        assert pc.types_in_use(ROWS) == ["NHP", "PAV", "TIS"]

    def test_no_rows_is_no_types(self):
        assert pc.types_in_use([]) == []


class TestProjectBundles:
    KNOWN = {"NHP", "PAV", "TIS"}

    @patch("nextseek_api.services.project_connections._curated_bundles")
    def test_curation_wins_outright_and_does_not_merge_with_the_fallback(self, curated):
        curated.return_value = [{"label": "Tissue Collection", "codes": ["NHP", "PAV", "TIS"]}]
        assert pc.project_bundles(2, ROWS, self.KNOWN) == [
            {"label": "Tissue Collection", "codes": ["NHP", "PAV", "TIS"]}
        ]

    @patch("nextseek_api.services.project_connections._curated_bundles", return_value=[])
    def test_with_no_curation_bundles_are_derived_one_per_assay(self, _curated):
        assert pc.project_bundles(2, ROWS, self.KNOWN) == [
            {"label": "Necropsy", "codes": ["NHP", "PAV"]},
            {"label": "Tissue Collection", "codes": ["PAV", "TIS"]},
        ]

    @patch("nextseek_api.services.project_connections._curated_bundles")
    def test_an_unknown_code_is_dropped_from_a_curated_bundle(self, curated):
        curated.return_value = [{"label": "Tissue Collection",
                                 "codes": ["NHP", "GONE", "TIS"]}]
        assert pc.project_bundles(2, ROWS, self.KNOWN) == [
            {"label": "Tissue Collection", "codes": ["NHP", "TIS"]}
        ]

    @patch("nextseek_api.services.project_connections._curated_bundles")
    def test_a_bundle_emptied_by_that_drop_is_not_rendered(self, curated):
        curated.return_value = [
            {"label": "Ghost", "codes": ["GONE", "ALSO_GONE"]},
            {"label": "Real", "codes": ["TIS"]},
        ]
        assert pc.project_bundles(2, ROWS, self.KNOWN) == [
            {"label": "Real", "codes": ["TIS"]}
        ]

    @patch("nextseek_api.services.project_connections._curated_bundles", return_value=[])
    def test_no_rows_and_no_curation_is_no_bundles(self, _curated):
        assert pc.project_bundles(2, [], self.KNOWN) == []
