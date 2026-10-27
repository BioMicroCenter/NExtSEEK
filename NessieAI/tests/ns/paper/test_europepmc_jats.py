from datetime import date

import pytest

from NessieAI.ns.paper import europepmc
from NessieAI.ns.paper.dates import parse_date, parse_partial_date
from NessieAI.ns.paper.jats import JatsUnreadable, classify, parse_jats
from NessieAI.ns.paper.models import PaperInput
from NessieAI.tests.ns.paper.fakes import FakeResponse, fetcher, js, key
from NessieAI.tests.ns.paper.fixtures import EPMC_HIT, JATS_XML, epmc_search

SEARCH = f"{europepmc.BASE}/search"


def _q(query):
    return key(SEARCH, {"query": query, "format": "json", "resultType": "core", "pageSize": "1"})


def test_parse_date_forms():
    assert parse_date("2024-02-03") == date(2024, 2, 3)
    assert parse_date("2024-02-03T10:00:00Z") == date(2024, 2, 3)
    assert parse_date("Jan 05 2024") == date(2024, 1, 5)
    assert parse_date("") is None and parse_date(None) is None and parse_date("soon") is None


def test_resolve_by_pmid():
    f, _ = fetcher({_q("EXT_ID:38000001 AND SRC:MED"): js(epmc_search([EPMC_HIT]))})
    ref = europepmc.resolve(PaperInput(kind="pmid", value="38000001"), f)
    assert (ref.pmid, ref.pmcid, ref.doi) == ("38000001", "PMC10000001", "10.1000/xyz123")
    assert ref.open_access and ref.journal == "J Test" and ref.published == "2024-02-03"
    assert [(a.name, a.affiliation) for a in ref.authors] == [("Doe J", "Some Institute"), ("Roe K", "Other Institute")]


def test_parse_partial_date_keeps_only_the_precision_given():
    assert parse_partial_date("2024") == "2024"
    assert parse_partial_date("2024-02") == "2024-02"
    assert parse_partial_date("2024-02-03T10:00:00Z") == "2024-02-03"
    assert parse_partial_date("Jan 05 2024") == "2024-01-05"
    assert parse_partial_date("Feb 2024") == "2024-02" and parse_partial_date("2024 Feb") == "2024-02"
    assert parse_partial_date("February 2024") == "2024-02"
    assert parse_partial_date(date(2024, 2, 3)) == "2024-02-03"
    for bad in ("", None, "soon", "2024-13", "2024-02-30", "24", "2024-2"):
        assert parse_partial_date(bad) is None, bad


def _resolved(hit):
    f, _ = fetcher({_q("EXT_ID:38000001 AND SRC:MED"): js(epmc_search([hit]))})
    return europepmc.resolve(PaperInput(kind="pmid", value="38000001"), f)


def test_partial_publication_date_is_kept_at_its_precision():
    assert _resolved({**EPMC_HIT, "firstPublicationDate": "2024-02"}).published == "2024-02"
    hit = {k: v for k, v in EPMC_HIT.items() if k != "firstPublicationDate"}
    assert _resolved({**hit, "pubYear": "2023"}).published == "2023"
    assert _resolved(hit).published is None


def test_consortium_author_is_kept_and_marked_collective():
    ref = _resolved({**EPMC_HIT, "authorList": {"author": [
        {"fullName": "Doe J"}, {"collectiveName": "Tumour Atlas Consortium"},
        {"lastName": "Poe", "initials": "AB"}, {"affiliation": "nobody"}]}})
    assert [(a.name, a.collective) for a in ref.authors] == [
        ("Doe J", False), ("Tumour Atlas Consortium", True), ("Poe AB", False)]


def test_search_answer_of_the_wrong_shape_is_unreadable():
    for body in ([], {"resultList": {"result": ["x"]}}, {"resultList": {"result": [{**EPMC_HIT, "title": 7}]}}):
        f, _ = fetcher({_q("EXT_ID:38000001 AND SRC:MED"): js(body)})
        with pytest.raises(europepmc.UnreadableAnswer):
            europepmc.resolve(PaperInput(kind="pmid", value="38000001"), f)


def test_resolve_by_doi_and_pmcid_queries():
    f, _ = fetcher({_q('DOI:"10.1000/xyz123"'): js(epmc_search([EPMC_HIT])),
                    _q("PMCID:PMC10000001"): js(epmc_search([EPMC_HIT]))})
    assert europepmc.resolve(PaperInput(kind="doi", value="10.1000/xyz123"), f).pmid == "38000001"
    assert europepmc.resolve(PaperInput(kind="pmcid", value="PMC10000001"), f).pmid == "38000001"


def test_resolve_no_hit_is_none():
    f, _ = fetcher({_q("EXT_ID:1 AND SRC:MED"): js(epmc_search([]))})
    assert europepmc.resolve(PaperInput(kind="pmid", value="1"), f) is None


def test_closed_access_is_not_open():
    hit = {**EPMC_HIT, "isOpenAccess": "N"}
    f, _ = fetcher({_q("EXT_ID:38000001 AND SRC:MED"): js(epmc_search([hit]))})
    assert europepmc.resolve(PaperInput(kind="pmid", value="38000001"), f).open_access is False


def test_full_text_and_zip_missing_are_none():
    f, _ = fetcher({key(f"{europepmc.BASE}/PMC1/fullTextXML"): FakeResponse(200, JATS_XML)})
    assert europepmc.full_text_xml("PMC1", f) == JATS_XML
    assert europepmc.supplementary_zip("PMC1", f) is None


def test_classify():
    assert classify("Materials and Methods") == "methods"
    assert classify("STAR Methods") == "methods"
    assert classify("Experimental model and subject details") == "methods"
    assert classify("Quantification and statistical analysis") == "methods"
    assert classify("Data and code availability") == "data_availability"
    assert classify("Accession numbers") == "data_availability"
    assert classify("", "data-availability") == "data_availability"
    assert classify("Results & Discussion") == "results_discussion"


def test_parse_jats():
    text = parse_jats(JATS_XML)
    by = {s.slug: s for s in text.sections}
    assert [p.text for p in by["introduction"].paragraphs] == ["Intro text."]
    assert [(p.id, p.text) for p in by["methods"].paragraphs] == [
        ("E:methods:p1", "All mice were female."), ("E:methods:p2", "RNA was sequenced.")]
    assert by["data_availability"].paragraphs[0].id == "E:data_availability:p1"
    assert text.has("methods") and text.has("data_availability")
    assert [(c.id, c.text) for c in text.captions] == [("E:cap:1", "Figure 1 Tumour growth.")]
    assert [(s.name, s.caption) for s in text.supplements] == [("NIHMS1_TableS1.xlsx", "Table S1. Samples.")]


def _jats(inner: str) -> bytes:
    return f"<article>{inner}</article>".encode()


def _items(text, slug):
    return [(p.id, p.text) for s in text.sections if s.slug == slug for p in s.paragraphs]


def test_notes_with_nested_sec_are_not_duplicated():
    text = parse_jats(_jats(
        '<back><notes notes-type="data-availability"><title>Data availability</title>'
        "<sec><title>Data</title><p>GEO GSE9.</p></sec></notes></back>"))
    assert [(s.slug, [(p.id, p.text) for p in s.paragraphs]) for s in text.sections] == [
        ("data_availability", [("E:data_availability:p1", "GEO GSE9.")])]


def test_data_availability_nested_in_methods_is_found():
    text = parse_jats(_jats(
        "<body><sec><title>Methods</title><p>Mice.</p>"
        "<sec><title>Data availability</title><p>GSE1.</p></sec></sec></body>"))
    assert [t for _, t in _items(text, "methods")] == ["Mice."]
    assert [t for _, t in _items(text, "data_availability")] == ["GSE1."]
    assert text.has("data_availability")


def test_float_inside_paragraph_stays_out_of_paragraph_text():
    text = parse_jats(_jats(
        "<body><sec><title>Results</title><p>See Figure 1 <fig><label>Figure 1</label>"
        "<caption><p>Cap text.</p></caption></fig>for detail.</p></sec></body>"))
    assert [t for _, t in _items(text, "results")] == ["See Figure 1 for detail."]
    assert [(c.id, c.text) for c in text.captions] == [("E:cap:1", "Figure 1 Cap text.")]


def test_boxed_text_paragraphs_are_kept():
    text = parse_jats(_jats(
        "<body><sec><title>Methods</title><boxed-text><p>Boxed method detail.</p></boxed-text></sec></body>"))
    assert [t for _, t in _items(text, "methods")] == ["Boxed method detail."]


def test_broken_xml_is_unreadable():
    for xml in (b"", b"<article><body><sec>", b"not xml at all", b"<article>&nbsp;</article>"):
        with pytest.raises(JatsUnreadable):
            parse_jats(xml)


def test_xml_that_is_not_a_jats_article_is_unreadable():
    for xml in (b"<html><body><p>Maintenance</p></body></html>",
                b'<article xmlns="http://example.org/other"><body/></article>',
                b"<responseWrapper><error>none</error></responseWrapper>"):
        with pytest.raises(JatsUnreadable):
            parse_jats(xml)


def test_deeply_nested_jats_is_unreadable_not_a_recursion_error():
    deep = b"<article><body><sec><title>Methods</title><p>" + b"<i>" * 5000 + b"x" + b"</i>" * 5000 + b"</p></sec></body></article>"
    with pytest.raises(JatsUnreadable):
        parse_jats(deep)
    secs = b"<article><body>" + b"<sec><title>Methods</title>" * 3000 + b"</sec>" * 3000 + b"</body></article>"
    with pytest.raises(JatsUnreadable):
        parse_jats(secs)


def test_article_with_no_body_is_empty_text_not_an_error():
    text = parse_jats(b"<article><front><article-meta/></front></article>")
    assert text.sections == [] and not text.has("methods")


def test_file_input_is_not_a_query():
    with pytest.raises(ValueError):
        europepmc._query(PaperInput(kind="file", value="x.pdf"))


_XL = 'xmlns:xlink="http://www.w3.org/1999/xlink"'


def test_supplement_listed_in_body_and_back_is_listed_once():
    text = parse_jats(f'<article {_XL}><body><sec><title>R</title><p>x</p><supplementary-material>'
                      '<media xlink:href="mmc1.docx"/><caption><p>Data S1.</p></caption></supplementary-material>'
                      '</sec></body><back><supplementary-material><media xlink:href="mmc1.docx"/>'
                      '</supplementary-material></back></article>'.encode())
    assert [(s.name, s.caption) for s in text.supplements] == [("mmc1.docx", "Data S1.")]


def test_supplement_href_is_url_decoded_to_its_file_name():
    text = parse_jats(f'<article {_XL}><back><supplementary-material>'
                      '<media xlink:href="https://example.org/suppl/Table%20S1.xlsx?dl=1"/>'
                      '</supplementary-material></back></article>'.encode())
    assert [s.name for s in text.supplements] == ["Table S1.xlsx"]
