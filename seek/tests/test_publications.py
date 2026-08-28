"""Read layer for study-level publication attributes.

No database: `_rows` is monkeypatched. What is tested here is the SQL text and
the Python-side shaping, which is where the mistakes actually live.
"""

import pytest

from seek import publications as pub


class TestPublicationRef:
    def test_doi_url(self):
        ref = pub.PublicationRef(1, "S", "10.1/a", None)
        assert ref.doi_url == "https://doi.org/10.1/a"

    def test_doi_url_is_none_without_doi(self):
        assert pub.PublicationRef(1, "S", None, 5).doi_url is None

    def test_pmid_url(self):
        assert pub.PublicationRef(1, "S", None, 12345).pmid_url == \
            "https://pubmed.ncbi.nlm.nih.gov/12345/"

    def test_citation_prefers_the_study_title(self):
        assert pub.PublicationRef(1, "A paper", "10.1/a", None).citation() == "A paper"

    def test_citation_falls_back_to_the_doi(self):
        assert pub.PublicationRef(1, None, "10.1/a", None).citation() == "10.1/a"

    def test_as_dict_is_json_safe(self):
        import json
        json.dumps(pub.PublicationRef(1, "S", "10.1/a", 2).as_dict())


class TestPublicationsForSamples:
    def test_empty_input_does_not_query(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("should not have queried")

        monkeypatch.setattr(pub, "_rows", explode)
        assert pub.publications_for_samples([]) == {}

    def test_groups_by_sample(self, monkeypatch):
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [
            {"sample_id": 1, "study_id": 10, "study_title": "A", "doi": "10.1/a", "pmid": 111},
            {"sample_id": 2, "study_id": 10, "study_title": "A", "doi": "10.1/a", "pmid": 111},
        ])
        got = pub.publications_for_samples([1, 2, 3])
        assert sorted(got) == [1, 2]
        assert got[1][0].doi == "10.1/a"

    def test_sample_in_two_published_studies_shows_both(self, monkeypatch):
        # 18 samples really do belong to two studies.
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [
            {"sample_id": 7, "study_id": 10, "study_title": "A", "doi": "10.1/a", "pmid": None},
            {"sample_id": 7, "study_id": 20, "study_title": "B", "doi": "10.1/b", "pmid": None},
        ])
        assert len(pub.publications_for_samples([7])[7]) == 2

    def test_query_only_returns_published_studies(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(pub, "_rows",
                            lambda sql, params=None: captured.update(sql=sql) or [])
        pub.publications_for_samples([1])
        assert "s.doi IS NOT NULL OR s.pmid IS NOT NULL" in captured["sql"]
        assert "'Sample'" in captured["sql"]

    def test_query_is_parameterized_on_sample_ids(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(pub, "_rows",
                            lambda sql, params=None: captured.update(params=params) or [])
        pub.publications_for_samples([4, 5])
        assert captured["params"] == [4, 5]


class TestResolveStudyIds:
    def test_doi_lookup_is_parameterized_and_lowercased(self, monkeypatch):
        captured = {}

        def fake_rows(sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
            return [{"id": 3}]

        monkeypatch.setattr(pub, "_rows", fake_rows)
        # A real DOI shape: the extractor requires 10.<4-9 digits>/, so a
        # made-up "10.1/a" would fall through to the title branch instead.
        assert pub.resolve_study_ids("https://doi.org/10.1038/S41590-021-01066-1") == [3]
        assert "%s" in captured["sql"]
        assert captured["params"] == ["10.1038/s41590-021-01066-1"]

    def test_pmid_lookup(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(pub, "_rows",
                            lambda sql, params=None: captured.update(params=params) or [{"id": 4}])
        assert pub.resolve_study_ids("99") == [4]
        assert captured["params"] == ["99"]

    def test_title_lookup(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(pub, "_rows",
                            lambda sql, params=None: captured.update(params=params) or [{"id": 5}])
        assert pub.resolve_study_ids("Some paper title") == [5]
        assert captured["params"] == ["Some paper title"]

    def test_several_matches_return_all(self, monkeypatch):
        # A paper spanning two studies is legitimate; return both rather than erroring.
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [{"id": 1}, {"id": 2}])
        assert pub.resolve_study_ids("10.1038/s41590-021-01066-1") == [1, 2]

    def test_unknown_returns_empty(self, monkeypatch):
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [])
        assert pub.resolve_study_ids("nothing") == []


class TestSubqueries:
    def test_sample_ids_subquery_rejects_non_integers(self):
        with pytest.raises(ValueError):
            pub.sample_ids_subquery(["1 OR 1=1"])

    def test_sample_ids_subquery_splices_integers(self):
        sql = pub.sample_ids_subquery([7, 8])
        assert "IN (7,8)" in sql.replace(" ", "").replace("IN(", "IN (")

    def test_published_subquery_filters_on_doi_or_pmid(self):
        assert "s.doi IS NOT NULL OR s.pmid IS NOT NULL" in pub.published_sample_ids_subquery()


class TestPublicationPredicate:
    def test_no_filter_yields_no_predicate(self):
        assert pub.publication_predicate(None, False) == ""
        assert pub.publication_predicate("", False) == ""

    def test_predicate_carries_no_leading_keyword(self):
        # An unfiltered search emits no WHERE, so the caller decides between
        # " WHERE " and ") AND ". A leading AND here would produce invalid SQL.
        clause = pub.publication_predicate(None, True)
        assert not clause.lstrip().upper().startswith(("AND", "WHERE"))
        assert clause.startswith("A.id IN (")

    def test_resolved_query_constrains_to_its_studies(self, monkeypatch):
        monkeypatch.setattr(pub, "resolve_study_ids", lambda q: [7])
        clause = pub.publication_predicate("10.1/a", False)
        assert "A.id IN (" in clause
        assert "7" in clause

    def test_unknown_publication_matches_nothing(self, monkeypatch):
        monkeypatch.setattr(pub, "resolve_study_ids", lambda q: [])
        assert pub.publication_predicate("no such paper", False) == "1=0"

    def test_injection_attempt_cannot_reach_sql(self, monkeypatch):
        monkeypatch.setattr(pub, "resolve_study_ids", lambda q: [])
        clause = pub.publication_predicate("'; DROP TABLE samples; --", False)
        assert "DROP" not in clause


class TestAttachPublications:
    def test_adds_key_to_every_row(self, monkeypatch):
        monkeypatch.setattr(
            pub, "publications_for_samples",
            lambda ids: {1: [pub.PublicationRef(9, "A", "10.1/a", None)]},
        )
        rows = [{"id": 1}, {"id": 2}]
        pub.attach_publications(rows)
        assert rows[0]["publications"][0]["doi"] == "10.1/a"
        assert rows[1]["publications"] == []

    def test_one_query_per_page_not_per_row(self, monkeypatch):
        calls = []
        monkeypatch.setattr(pub, "publications_for_samples",
                            lambda ids: calls.append(list(ids)) or {})
        pub.attach_publications([{"id": 1}, {"id": 2}, {"id": 3}])
        assert calls == [[1, 2, 3]]

    def test_empty_rows(self, monkeypatch):
        monkeypatch.setattr(pub, "publications_for_samples", lambda ids: {})
        assert pub.attach_publications([]) == []


class TestEnsureColumns:
    def test_no_ddl_when_both_columns_exist(self, monkeypatch):
        executed = []
        monkeypatch.setattr(pub, "_rows",
                            lambda sql, params=None: [{"COLUMN_NAME": "doi"},
                                                      {"COLUMN_NAME": "pmid"}])
        monkeypatch.setattr(pub, "_execute", lambda sql: executed.append(sql))
        assert pub.ensure_study_publication_columns() == []
        assert executed == []

    def test_adds_only_the_missing_column(self, monkeypatch):
        executed = []
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [{"COLUMN_NAME": "doi"}])
        monkeypatch.setattr(pub, "_execute", lambda sql: executed.append(sql))
        assert pub.ensure_study_publication_columns() == ["pmid"]
        assert len(executed) == 1
        assert "ADD COLUMN pmid" in executed[0]

    def test_adds_both_when_absent(self, monkeypatch):
        executed = []
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [])
        monkeypatch.setattr(pub, "_execute", lambda sql: executed.append(sql))
        assert pub.ensure_study_publication_columns() == ["doi", "pmid"]
        assert len(executed) == 2
