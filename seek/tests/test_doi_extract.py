"""Extraction of publication references from real SEEK study descriptions.

Every fixture string here was taken verbatim from seek_production.studies on
2026-08-21. They are ugly on purpose: that is what the extractor must survive.
"""

import pytest

from seek.doi_extract import (
    Candidate,
    extract_publication_candidates,
    normalize_doi,
)


class TestNormalizeDoi:
    def test_plain_doi(self):
        assert normalize_doi("10.1038/s41590-021-01066-1") == "10.1038/s41590-021-01066-1"

    def test_strips_trailing_period(self):
        assert normalize_doi("10.1371/journal.pone.0249477.") == "10.1371/journal.pone.0249477"

    def test_strips_biorxiv_version_suffix(self):
        assert normalize_doi("10.1101/2022.01.29.22269829v1") == "10.1101/2022.01.29.22269829"

    def test_strips_version_and_full_suffix(self):
        assert normalize_doi("10.1101/2024.05.24.595747v1.full") == "10.1101/2024.05.24.595747"

    def test_strips_url_fragment(self):
        assert normalize_doi("10.1038/s41596-024-01076-x#Sec43") == "10.1038/s41596-024-01076-x"

    def test_lowercases(self):
        assert normalize_doi("10.1039/D2DT03848J") == "10.1039/d2dt03848j"

    def test_truncated_doi_is_rejected(self):
        # Study 31's description contains exactly this and nothing more.
        assert normalize_doi("10.3390/") is None

    def test_non_doi_is_rejected(self):
        assert normalize_doi("not-a-doi") is None


class TestExtractFromRealDescriptions:
    def test_doi_org_url(self):
        cands = extract_publication_candidates(
            "https://doi.org/10.1101/2021.09.30.462577"
        )
        assert cands == [
            Candidate("doi", "10.1101/2021.09.30.462577", "10.1101/2021.09.30.462577", "")
        ]

    def test_medrxiv_content_url(self):
        cands = extract_publication_candidates(
            "https://www.medrxiv.org/content/10.1101/2022.01.29.22269829v1 "
        )
        assert [c.value for c in cands] == ["10.1101/2022.01.29.22269829"]

    def test_science_org_doi_path(self):
        cands = extract_publication_candidates(
            "https://www.science.org/doi/10.1126/sciadv.adq8229"
        )
        assert [c.value for c in cands] == ["10.1126/sciadv.adq8229"]

    def test_acs_doi_full_path(self):
        cands = extract_publication_candidates(
            "https://pubs.acs.org/doi/full/10.1021/acsomega.4c03959"
        )
        assert [c.value for c in cands] == ["10.1021/acsomega.4c03959"]

    def test_truncated_doi_is_flagged_not_dropped(self):
        cands = extract_publication_candidates("https://doi.org/10.3390/")
        assert len(cands) == 1
        assert cands[0].kind == "unresolvable"
        assert cands[0].raw == "10.3390/"

    def test_nature_article_url_maps_to_doi(self):
        cands = extract_publication_candidates(
            "DOI: https://www.nature.com/articles/s41596-024-01076-x#Sec43"
        )
        assert [(c.kind, c.value) for c in cands] == [
            ("doi", "10.1038/s41596-024-01076-x")
        ]

    def test_pmc_url_yields_pmc_id(self):
        cands = extract_publication_candidates(
            "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8439179/"
        )
        assert [(c.kind, c.value) for c in cands] == [("pmc", "PMC8439179")]

    def test_imgur_figure_is_rejected(self):
        assert extract_publication_candidates("![](https://i.imgur.com/dJLbsO4.png)") == []

    def test_geo_accession_is_rejected(self):
        assert extract_publication_candidates(
            "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE267774"
        ) == []

    def test_omero_link_is_rejected(self):
        assert extract_publication_candidates(
            "https://omero.mit.edu/webclient/?show=project-559"
        ) == []

    def test_publisher_url_without_identifier_is_flagged(self):
        cands = extract_publication_candidates(
            "https://www.sciencedirect.com/science/article/abs/pii/S0142961224002655"
        )
        assert [c.kind for c in cands] == ["unresolvable"]

    def test_cell_pii_url_keeps_its_balanced_parens(self):
        # Study 12. The PII contains (24), so a URL pattern that stops at ')'
        # hands the curator a truncated, unusable link.
        cands = extract_publication_candidates(
            "https://www.cell.com/immunity/fulltext/S1074-7613(24)00375-3"
        )
        assert [c.kind for c in cands] == ["unresolvable"]
        assert cands[0].raw == "https://www.cell.com/immunity/fulltext/S1074-7613(24)00375-3"

    def test_markdown_image_paren_is_still_trimmed(self):
        # The counterpart: here the trailing ')' closes the markdown, not the URL.
        cands = extract_publication_candidates(
            "![](https://www.sciencedirect.com/science/article/abs/pii/S014296122400)"
        )
        assert cands[0].raw.endswith("S014296122400")

    def test_figure_alongside_real_doi_does_not_interfere(self):
        cands = extract_publication_candidates(
            "![](https://i.imgur.com/8TPKDA6.jpg)\n\nhttps://doi.org/10.1101/2021.09.30.462577"
        )
        assert [c.value for c in cands] == ["10.1101/2021.09.30.462577"]

    def test_none_description(self):
        # 3 of the 51 studies have description IS NULL.
        assert extract_publication_candidates(None) == []

    def test_empty_description(self):
        assert extract_publication_candidates("") == []

    def test_duplicate_doi_mentioned_twice_yields_one_candidate(self):
        cands = extract_publication_candidates(
            "see 10.1126/sciadv.adq6652 and also https://doi.org/10.1126/sciadv.adq6652"
        )
        assert [c.value for c in cands] == ["10.1126/sciadv.adq6652"]

    def test_supplementary_file_doi_is_rejected(self):
        # Study 26's description cites both the article DOI and its supplementary
        # file, which publishers mint as a sub-DOI. Crossref 404s on the latter,
        # and it produced a spurious duplicate row in the curator review file.
        assert normalize_doi(
            "10.1126/sciadv.adq6652/suppl_file/sciadv.adq6652_sm.pdf"
        ) is None

    def test_article_doi_survives_alongside_its_supplement(self):
        cands = extract_publication_candidates(
            "https://doi.org/10.1126/sciadv.adq6652 and "
            "https://doi.org/10.1126/sciadv.adq6652/suppl_file/sciadv.adq6652_sm.pdf"
        )
        assert [c.value for c in cands] == ["10.1126/sciadv.adq6652"]

    def test_acs_supplementary_suffix_doi_is_rejected(self):
        # ACS mints one DOI per supplementary file by appending .sNNN to the
        # article DOI. Crossref resolves it, so it looks valid — but it is the
        # supplement, not the paper.
        assert normalize_doi("10.1021/acssensors.4c00927.s002") is None

    def test_article_doi_without_the_suffix_is_kept(self):
        assert normalize_doi("10.1021/acssensors.4c00927") == "10.1021/acssensors.4c00927"
