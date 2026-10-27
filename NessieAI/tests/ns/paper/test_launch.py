import pytest

from NessieAI.ns.paper import europepmc
from NessieAI.ns.paper.identify import IdentifyError
from NessieAI.ns.paper.launch import CatalogUnavailable, LaunchSeams, launch_check
from NessieAI.tests.ns.paper.fakes import FakeResponse, fetcher, js, key
from NessieAI.tests.ns.paper.fixtures import EPMC_HIT, JATS_XML, PDF_TEXT, epmc_search

FULLTEXT = key(f"{europepmc.BASE}/PMC10000001/fullTextXML")


def _search(query):
    return key(f"{europepmc.BASE}/search", {"query": query, "format": "json", "resultType": "core", "pageSize": "1"})


def _seams(routes, projects=(), files=None):
    f, _ = fetcher(routes)
    files = files or {}
    return LaunchSeams(fetcher=f, input_files=set(files), read_input_file=lambda n: files[n],
                       doi_project_ids=lambda doi: list(projects))


PMID_Q = _search("EXT_ID:38000001 AND SRC:MED")


def test_open_access_pmid_is_ok_and_new():
    out = launch_check("38000001", _seams({PMID_Q: js(epmc_search([EPMC_HIT])), FULLTEXT: FakeResponse(200, JATS_XML)}))
    assert out.status == "ok"
    assert out.paper.mode == "new" and out.paper.text_source == "europepmc"
    assert out.paper.ref.doi == "10.1000/xyz123"


def test_doi_already_in_nextseek_is_update():
    out = launch_check("38000001", _seams({PMID_Q: js(epmc_search([EPMC_HIT])), FULLTEXT: FakeResponse(200, JATS_XML)},
                                          projects=[7]))
    assert out.paper.mode == "update" and out.paper.existing_project_ids == [7]


def test_closed_access_needs_pdf():
    out = launch_check("38000001", _seams({PMID_Q: js(epmc_search([{**EPMC_HIT, "isOpenAccess": "N"}]))}))
    assert out.status == "needs_pdf" and out.paper is None and "Attach the PDF" in out.reply


def test_unknown_paper_not_found():
    out = launch_check("38000001", _seams({PMID_Q: js(epmc_search([]))}))
    assert out.status == "not_found"


def test_missing_methods_needs_pdf():
    xml = JATS_XML.replace(b'sec-type="methods"><title>Materials and Methods', b'><title>Results')
    out = launch_check("38000001", _seams({PMID_Q: js(epmc_search([EPMC_HIT])), FULLTEXT: FakeResponse(200, xml)}))
    assert out.status == "needs_pdf" and out.missing == ["methods"]


def test_no_data_availability_but_accession_in_methods_is_ok():
    xml = (JATS_XML.replace(b'<sec sec-type="data-availability"><title>Data availability</title>'
                            b'<p>Reads are in GEO under GSE123456.</p></sec>', b"")
           .replace(b"RNA was sequenced.", b"RNA was sequenced (GSE123456)."))
    out = launch_check("38000001", _seams({PMID_Q: js(epmc_search([EPMC_HIT])), FULLTEXT: FakeResponse(200, xml)}))
    assert out.status == "ok" and out.missing == []


def test_attached_pdf(monkeypatch):
    monkeypatch.setattr("NessieAI.ns.paper.launch.pdf_to_text", lambda data: PDF_TEXT)
    seams = _seams({_search('DOI:"10.1000/xyz123"'): js(epmc_search([EPMC_HIT]))}, files={"paper.pdf": b"%PDF"})
    out = launch_check("paper.pdf", seams)
    assert out.status == "ok" and out.paper.text_source == "pdf"
    assert out.paper.ref.pmid == "38000001" and out.paper.ref.source_file == "paper.pdf"


DAS = (b'<sec sec-type="data-availability"><title>Data availability</title>'
       b'<p>Reads are in GEO under GSE123456.</p></sec>')
PMID_ROUTES = {PMID_Q: js(epmc_search([EPMC_HIT]))}


def _no_methods(xml):
    return xml.replace(b'sec-type="methods"><title>Materials and Methods', b'><title>Results')


def test_doi_lookup_failure_raises_catalog_unavailable():
    def down(doi):
        raise RuntimeError("down")

    seams = _seams({**PMID_ROUTES, FULLTEXT: FakeResponse(200, JATS_XML)})
    seams.doi_project_ids = down
    with pytest.raises(CatalogUnavailable) as err:
        launch_check("38000001", seams)
    assert isinstance(err.value.__cause__, RuntimeError) and "down" not in str(err.value)


def test_paper_without_doi_skips_the_doi_lookup():
    def never(doi):
        raise AssertionError("doi_project_ids must not be called without a DOI")

    hit = {k: v for k, v in EPMC_HIT.items() if k != "doi"}
    seams = _seams({PMID_Q: js(epmc_search([hit])), FULLTEXT: FakeResponse(200, JATS_XML)})
    seams.doi_project_ids = never
    out = launch_check("38000001", seams)
    assert out.status == "ok" and out.paper.ref.doi is None
    assert out.paper.mode == "new" and out.paper.existing_project_ids == []


def test_no_data_availability_and_no_accession_needs_pdf():
    xml = JATS_XML.replace(DAS, b"")
    assert xml != JATS_XML
    out = launch_check("38000001", _seams({**PMID_ROUTES, FULLTEXT: FakeResponse(200, xml)}))
    assert out.status == "needs_pdf" and out.missing == ["data_availability"]


def test_both_sections_missing_are_listed_in_order():
    xml = _no_methods(JATS_XML.replace(DAS, b""))
    out = launch_check("38000001", _seams({**PMID_ROUTES, FULLTEXT: FakeResponse(200, xml)}))
    assert out.status == "needs_pdf" and out.missing == ["methods", "data_availability"]


def test_pdf_without_methods_heading_is_missing_sections(monkeypatch):
    text = PDF_TEXT.replace("2 Materials and Methods\nMice were bred.\n", "")
    assert text != PDF_TEXT
    monkeypatch.setattr("NessieAI.ns.paper.launch.pdf_to_text", lambda data: text)
    seams = _seams({_search('DOI:"10.1000/xyz123"'): js(epmc_search([EPMC_HIT]))}, files={"paper.pdf": b"%PDF"})
    out = launch_check("paper.pdf", seams)
    assert out.status == "missing_sections" and out.paper is None
    assert "methods" in out.missing and "PDF" in out.reply


def test_pdf_without_doi_uses_first_paragraph_as_title(monkeypatch):
    text = "L" * 400 + "\n" + PDF_TEXT.replace("doi:10.1000/xyz123", "")
    monkeypatch.setattr("NessieAI.ns.paper.launch.pdf_to_text", lambda data: text)
    out = launch_check("paper.pdf", _seams({}, files={"paper.pdf": b"%PDF"}))
    assert out.status == "ok"
    assert out.paper.ref.doi is None and out.paper.ref.source_file == "paper.pdf"
    assert out.paper.ref.title == "L" * 300
    assert out.paper.mode == "new"


def _never(doi):
    raise AssertionError("an unconfirmed DOI must not be looked up in NExtSEEK")


def test_pdf_doi_not_in_europepmc_is_not_used(monkeypatch):
    monkeypatch.setattr("NessieAI.ns.paper.launch.pdf_to_text", lambda data: PDF_TEXT)
    seams = _seams({_search('DOI:"10.1000/xyz123"'): js(epmc_search([]))}, files={"paper.pdf": b"%PDF"})
    seams.doi_project_ids = _never
    out = launch_check("paper.pdf", seams)
    assert out.status == "ok" and out.paper.ref.doi is None and out.paper.ref.pmid is None
    assert out.paper.ref.source_file == "paper.pdf"
    assert out.paper.mode == "new" and out.paper.existing_project_ids == []


CITED_HIT = {**EPMC_HIT, "pmid": "12345", "pmcid": "PMC12345", "doi": "10.2000/cited", "title": "An older paper."}
CITED_FIRST = PDF_TEXT.replace("J Test 2024 doi:10.1000/xyz123\n",
                               "As shown before (doi:10.2000/cited).\nJ Test 2024 doi:10.1000/xyz123\n")


def test_pdf_skips_a_cited_doi_whose_title_is_not_on_the_first_page(monkeypatch):
    monkeypatch.setattr("NessieAI.ns.paper.launch.pdf_to_text", lambda data: CITED_FIRST)
    seams = _seams({_search('DOI:"10.2000/cited"'): js(epmc_search([CITED_HIT])),
                    _search('DOI:"10.1000/xyz123"'): js(epmc_search([EPMC_HIT]))},
                   projects=[3], files={"paper.pdf": b"%PDF"})
    out = launch_check("paper.pdf", seams)
    assert out.status == "ok" and out.paper.ref.doi == "10.1000/xyz123" and out.paper.ref.pmid == "38000001"
    assert out.paper.mode == "update" and out.paper.existing_project_ids == [3]


def test_pdf_with_only_a_cited_doi_has_no_doi(monkeypatch):
    text = CITED_FIRST.replace("J Test 2024 doi:10.1000/xyz123\n", "J Test 2024\n")
    monkeypatch.setattr("NessieAI.ns.paper.launch.pdf_to_text", lambda data: text)
    seams = _seams({_search('DOI:"10.2000/cited"'): js(epmc_search([CITED_HIT]))}, files={"paper.pdf": b"%PDF"})
    seams.doi_project_ids = _never
    out = launch_check("paper.pdf", seams)
    assert out.status == "ok" and out.paper.ref.doi is None and out.paper.ref.pmid is None
    assert out.paper.ref.title.startswith("As shown before") and out.paper.mode == "new"


def test_pdf_title_match_ignores_case_markup_and_line_breaks(monkeypatch):
    hit = {**EPMC_HIT, "title": "<i>Mice</i> and single-cell RNA."}
    text = PDF_TEXT.replace("Mice and RNA.\n", "MICE AND SINGLE-\nCELL RNA\n")
    monkeypatch.setattr("NessieAI.ns.paper.launch.pdf_to_text", lambda data: text)
    seams = _seams({_search('DOI:"10.1000/xyz123"'): js(epmc_search([hit]))}, files={"paper.pdf": b"%PDF"})
    out = launch_check("paper.pdf", seams)
    assert out.paper.ref.pmid == "38000001" and out.paper.ref.doi == "10.1000/xyz123"


def test_pdf_title_must_be_on_the_first_page(monkeypatch):
    text = PDF_TEXT.replace("J Test 2024 doi:10.1000/xyz123\nMice and RNA.\n",
                            "J Test 2024 doi:10.1000/xyz123\nA cover page.\n\fMice and RNA.\n")
    monkeypatch.setattr("NessieAI.ns.paper.launch.pdf_to_text", lambda data: text)
    seams = _seams({_search('DOI:"10.1000/xyz123"'): js(epmc_search([EPMC_HIT]))}, files={"paper.pdf": b"%PDF"})
    seams.doi_project_ids = _never
    out = launch_check("paper.pdf", seams)
    assert out.status == "ok" and out.paper.ref.doi is None


def test_pdf_doi_glued_to_the_next_word_is_tried_without_it(monkeypatch):
    text = PDF_TEXT.replace("doi:10.1000/xyz123", "doi:10.1000/xyz123drugdelivery")
    monkeypatch.setattr("NessieAI.ns.paper.launch.pdf_to_text", lambda data: text)
    seams = _seams({_search('DOI:"10.1000/xyz123drugdelivery"'): js(epmc_search([])),
                    _search('DOI:"10.1000/xyz123"'): js(epmc_search([EPMC_HIT]))}, files={"paper.pdf": b"%PDF"})
    out = launch_check("paper.pdf", seams)
    assert out.paper.ref.doi == "10.1000/xyz123" and out.paper.ref.pmid == "38000001"


def test_pdf_looks_up_at_most_three_dois(monkeypatch):
    dois = [f"10.3000/c{i}" for i in range(5)]
    text = "".join(f"see doi:{d}\n" for d in dois) + PDF_TEXT.replace("doi:10.1000/xyz123", "")
    monkeypatch.setattr("NessieAI.ns.paper.launch.pdf_to_text", lambda data: text)
    f, session = fetcher({_search(f'DOI:"{d}"'): js(epmc_search([])) for d in dois})
    seams = LaunchSeams(fetcher=f, input_files={"paper.pdf"}, read_input_file=lambda n: b"%PDF",
                        doi_project_ids=_never)
    out = launch_check("paper.pdf", seams)
    assert out.status == "ok" and out.paper.ref.doi is None and len(session.calls) == 3


def test_unrecognisable_input_raises():
    with pytest.raises(IdentifyError):
        launch_check("a nice paper", _seams({}))


def test_open_access_text_with_no_body_needs_pdf():
    out = launch_check("38000001", _seams({**PMID_ROUTES, FULLTEXT: FakeResponse(200, b"<article><front/></article>")}))
    assert out.status == "needs_pdf" and out.missing == ["methods", "data_availability"]
