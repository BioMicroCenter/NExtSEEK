"""The detail page reads through the same helper as search, so a sample cannot
show one paper in the results table and a different one on its own page."""

from seek import publications as pub


def test_detail_context_shape(monkeypatch):
    monkeypatch.setattr(
        pub, "publications_for_samples",
        lambda ids: {5: [pub.PublicationRef(9, "A paper", "10.1/a", 77)]},
    )
    got = pub.publications_for_sample(5)
    assert got[0]["doi_url"] == "https://doi.org/10.1/a"
    assert got[0]["pmid_url"] == "https://pubmed.ncbi.nlm.nih.gov/77/"
    assert got[0]["citation"] == "A paper"


def test_detail_context_empty_for_unpublished(monkeypatch):
    monkeypatch.setattr(pub, "publications_for_samples", lambda ids: {})
    assert pub.publications_for_sample(5) == []


def test_accepts_string_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(pub, "publications_for_samples",
                        lambda ids: captured.update(ids=list(ids)) or {})
    pub.publications_for_sample("5")
    assert captured["ids"] == [5]
