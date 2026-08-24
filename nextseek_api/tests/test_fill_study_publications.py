"""Review-file construction and the approval gate.

Network resolution is injected, so these tests never call Crossref or NCBI.
"""

import pytest

from nextseek_api.management.commands import fill_study_publications as cmd


class TestTitleSimilarity:
    def test_identical(self):
        assert cmd.title_similarity("A paper", "A paper") == 1.0

    def test_unrelated(self):
        assert cmd.title_similarity("Endometrium organoids", "Tuberculosis granulomas") < 0.5

    def test_handles_none(self):
        assert cmd.title_similarity(None, "x") == 0.0
        assert cmd.title_similarity("x", None) == 0.0


class TestBuildReviewRows:
    def test_resolved_doi_row(self):
        studies = [{"id": 2, "title": "Organoid co-culture model",
                    "description": "https://doi.org/10.1101/2021.09.30.462577"}]

        def resolver(kind, value):
            return {"doi": "10.1101/2021.09.30.462577", "pmid": 34981053,
                    "title": "Organoid co-culture model", "journal": "bioRxiv",
                    "year": 2021}

        rows = cmd.build_review_rows(studies, resolver)
        assert len(rows) == 1
        assert rows[0]["normalized_doi"] == "10.1101/2021.09.30.462577"
        assert rows[0]["pmid"] == 34981053
        assert rows[0]["title_similarity"] == pytest.approx(1.0)
        assert rows[0]["approve"] == ""

    def test_low_similarity_is_visible_not_hidden(self):
        studies = [{"id": 9, "title": "Study about mice",
                    "description": "https://doi.org/10.1038/s41590-021-01066-1"}]

        def resolver(kind, value):
            return {"doi": "10.1038/s41590-021-01066-1", "pmid": None,
                    "title": "Something else entirely", "journal": None, "year": None}

        rows = cmd.build_review_rows(studies, resolver)
        assert rows[0]["title_similarity"] < 0.5
        assert rows[0]["approve"] == ""

    def test_truncated_doi_is_reported_as_manual(self):
        studies = [{"id": 31, "title": "T", "description": "https://doi.org/10.3390/"}]
        rows = cmd.build_review_rows(studies, lambda kind, value: None)
        assert rows[0]["proposed_action"] == "manual"
        assert "no suffix" in rows[0]["notes"]

    def test_study_without_reference_produces_no_row(self):
        studies = [{"id": 44, "title": "Mike Chao Barcoding Placeholder",
                    "description": "![](https://i.imgur.com/x.png)"}]
        assert cmd.build_review_rows(studies, lambda kind, value: None) == []

    def test_null_description_produces_no_row(self):
        studies = [{"id": 40, "title": "T", "description": None}]
        assert cmd.build_review_rows(studies, lambda kind, value: None) == []

    def test_offline_resolution_still_records_the_doi(self):
        studies = [{"id": 2, "title": "T",
                    "description": "https://doi.org/10.1038/s41590-021-01066-1"}]
        rows = cmd.build_review_rows(studies, lambda kind, value: None)
        assert rows[0]["proposed_action"] == "unresolved"
        assert rows[0]["normalized_doi"] == "10.1038/s41590-021-01066-1"


class TestApprovalGate:
    def _write(self, path, rows):
        header = "\t".join(cmd.REVIEW_COLUMNS)
        lines = [header]
        for study_id, approve in rows:
            values = {c: "" for c in cmd.REVIEW_COLUMNS}
            values["study_id"] = str(study_id)
            values["normalized_doi"] = f"10.1038/s{study_id}"
            values["approve"] = approve
            lines.append("\t".join(values[c] for c in cmd.REVIEW_COLUMNS))
        path.write_text("\n".join(lines) + "\n")

    def test_only_yes_is_applied(self, tmp_path):
        path = tmp_path / "review.tsv"
        self._write(path, [(1, "yes"), (2, "no"), (3, ""), (4, "YES")])
        assert [r["study_id"] for r in cmd.parse_review_file(str(path))] == ["1", "4"]

    def test_unreviewed_file_applies_nothing(self, tmp_path):
        path = tmp_path / "review.tsv"
        self._write(path, [(1, ""), (2, "")])
        assert cmd.parse_review_file(str(path)) == []


class TestNcbiParsing:
    """NCBI's idconv endpoint answers 403, so DOI/PMID resolution goes through
    E-utilities. These cover the parsing, not the network."""

    def test_esearch_pmid(self):
        payload = {"esearchresult": {"idlist": ["35483355", "99999"]}}
        assert cmd.parse_esearch_pmid(payload) == 35483355

    def test_esearch_no_match(self):
        assert cmd.parse_esearch_pmid({"esearchresult": {"idlist": []}}) is None

    def test_esearch_malformed(self):
        assert cmd.parse_esearch_pmid({}) is None

    def test_esummary_ids(self):
        payload = {"result": {"8439179": {
            "title": "CometChip enables parallel analysis",
            "articleids": [
                {"idtype": "pmid", "value": "34365116"},
                {"idtype": "pmcid", "value": "PMC8439179"},
                {"idtype": "doi", "value": "10.1016/J.DNAREP.2021.103176"},
            ]}}}
        got = cmd.parse_esummary_ids(payload, "8439179")
        assert got["doi"] == "10.1016/j.dnarep.2021.103176"
        assert got["pmid"] == 34365116
        assert got["title"].startswith("CometChip")

    def test_esummary_missing_record(self):
        assert cmd.parse_esummary_ids({"result": {}}, "1") == {
            "doi": None, "pmid": None, "title": None}

    def test_esummary_non_numeric_pmid_is_dropped(self):
        payload = {"result": {"1": {"articleids": [{"idtype": "pmid", "value": "n/a"}]}}}
        assert cmd.parse_esummary_ids(payload, "1")["pmid"] is None
