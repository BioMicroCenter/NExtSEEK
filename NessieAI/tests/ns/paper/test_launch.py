from NessieAI.ns.paper import europepmc
from NessieAI.ns.paper.launch import LaunchSeams, launch_check
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
