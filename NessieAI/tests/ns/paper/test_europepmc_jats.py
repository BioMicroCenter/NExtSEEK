from datetime import date

from NessieAI.ns.paper import europepmc
from NessieAI.ns.paper.dates import parse_date
from NessieAI.ns.paper.jats import classify, parse_jats
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
    assert ref.open_access and ref.journal == "J Test" and ref.published == date(2024, 2, 3)
    assert [(a.name, a.affiliation) for a in ref.authors] == [("Doe J", "Some Institute"), ("Roe K", "Other Institute")]


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
