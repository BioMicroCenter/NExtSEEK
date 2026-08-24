"""Graph payload construction and failure tolerance.

No Neo4j: the driver is stubbed. The property write must cover every study, not
only the published ones, so that clearing a DOI in MySQL clears it in the graph.
"""

from seek import publications_graph as pg


class TestBuildStudyRows:
    def test_maps_columns_to_properties(self):
        rows = [{"id": 3, "doi": "10.1/a", "pmid": 7}]
        assert pg.build_study_rows(rows) == [{"study_id": 3, "doi": "10.1/a", "pmid": "7"}]

    def test_unpublished_study_becomes_empty_strings_not_nulls(self):
        # Included on purpose: this is what clears a DOI removed in MySQL. The
        # sentinel is '' because that is what dev and prod already hold; writing
        # null would leave two different sentinels in one property.
        rows = [{"id": 4, "doi": None, "pmid": None}]
        assert pg.build_study_rows(rows) == [{"study_id": 4, "doi": "", "pmid": ""}]

    def test_pmid_is_written_as_a_string(self):
        # Keeps the property type stable against the existing '' values rather
        # than making PMID sometimes an int and sometimes a string.
        assert pg.build_study_rows([{"id": 5, "doi": "10.1/a", "pmid": 42}])[0]["pmid"] == "42"

    def test_empty(self):
        assert pg.build_study_rows([]) == []


class TestCypher:
    def test_matches_study_by_id(self):
        assert "MATCH (st:Study {id: row.study_id})" in pg.STUDY_PROPERTY_CYPHER

    def test_sets_both_properties_using_the_instances_casing(self):
        # Uppercase on purpose: that is how the properties exist on dev and prod.
        # A wrong-cased property returns null in Cypher rather than erroring.
        assert "st.DOI = row.doi" in pg.STUDY_PROPERTY_CYPHER
        assert "st.PMID = row.pmid" in pg.STUDY_PROPERTY_CYPHER

    def test_does_not_create_studies(self):
        # MERGE here would invent Study nodes that MySQL has but the graph does not.
        assert "MERGE" not in pg.STUDY_PROPERTY_CYPHER


class _FakeDriver:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute_query(self, *a, **k):
        self.calls.append((a, k))
        return None


class TestFailureTolerance:
    def test_try_sync_returns_false_when_neo4j_is_down(self, monkeypatch):
        def explode(*args, **kwargs):
            raise OSError("connection refused")

        monkeypatch.setattr(pg, "_driver", explode)
        monkeypatch.setattr(pg, "_study_rows", lambda: [{"id": 1, "doi": "x", "pmid": None}])
        assert pg.try_sync_study_publications() is False

    def test_try_sync_returns_true_on_success(self, monkeypatch):
        monkeypatch.setattr(pg, "_driver", lambda: _FakeDriver())
        monkeypatch.setattr(pg, "_study_rows", lambda: [])
        assert pg.try_sync_study_publications() is True

    def test_counts_are_reported(self, monkeypatch):
        monkeypatch.setattr(pg, "_driver", lambda: _FakeDriver())
        monkeypatch.setattr(pg, "_study_rows", lambda: [
            {"id": 1, "doi": "10.1/a", "pmid": 5},
            {"id": 2, "doi": None, "pmid": None},
            {"id": 3, "doi": "10.1/c", "pmid": None},
        ])
        assert pg.sync_study_publications() == {
            "studies": 3, "with_doi": 2, "with_pmid": 1
        }
