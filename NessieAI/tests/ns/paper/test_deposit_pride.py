import pytest

from NessieAI.ns.paper.deposits import pride
from NessieAI.tests.ns.paper.fakes import fetcher, js, key
from NessieAI.tests.ns.paper.fixtures import PRIDE_FILES, PRIDE_PROJECT

PROJECT = key(f"{pride.BASE}/projects/PXD012345")


def _page(n):
    return key(f"{pride.BASE}/projects/PXD012345/files", {"pageSize": pride.PAGE, "page": n})


def test_project_and_files_hal_shape():
    f, _ = fetcher({PROJECT: js(PRIDE_PROJECT), _page(0): js({"_embedded": {"files": PRIDE_FILES}}),
                    _page(1): js([])})
    records = pride.fetch("PXD012345", f)
    project, raw = records[0], records[1]
    assert (project.kind, project.title) == ("project", "Tyrosine phosphoproteomics")
    assert project.fields["organisms"] == "Homo sapiens (human)"
    assert project.fields["dataProcessingProtocol"] == "MaxQuant 2.4."
    assert (raw.record_id, raw.kind, raw.fields["fileCategory"]) == ("PXD012345/A1.raw", "file", "RAW")
    assert raw.files[0].url.startswith("ftp://ftp.pride.ebi.ac.uk/")
    assert raw.files[0].checksum_type == "sha1" and raw.files[0].size == 1000
    assert len(records) == 1 + len(PRIDE_FILES)


def test_files_paginate_and_accept_plain_list(monkeypatch):
    monkeypatch.setattr(pride, "PAGE", 2)
    f, _ = fetcher({PROJECT: js(PRIDE_PROJECT), _page(0): js(PRIDE_FILES[:2]), _page(1): js(PRIDE_FILES[2:]),
                    _page(2): js([])})
    assert [r.record_id for r in pride.fetch("PXD012345", f)][1:] == [
        "PXD012345/A1.raw", "PXD012345/A2.raw", "PXD012345/search.mzid.gz", "PXD012345/human.fasta"]


def test_short_page_does_not_end_paging(monkeypatch):
    monkeypatch.setattr(pride, "PAGE", 2)
    f, _ = fetcher({PROJECT: js(PRIDE_PROJECT), _page(0): js(PRIDE_FILES[:1]), _page(1): js(PRIDE_FILES[1:3]),
                    _page(2): js([])})
    assert [r.record_id for r in pride.fetch("PXD012345", f)][1:] == [
        "PXD012345/A1.raw", "PXD012345/A2.raw", "PXD012345/search.mzid.gz"]


def test_page_cap_raises_instead_of_truncating(monkeypatch):
    monkeypatch.setattr(pride, "PAGE", 1)
    monkeypatch.setattr(pride, "MAX_PAGES", 2)
    f, _ = fetcher({PROJECT: js(PRIDE_PROJECT), _page(0): js(PRIDE_FILES[:1]), _page(1): js(PRIDE_FILES[1:2])})
    with pytest.raises(RuntimeError, match="PRIDE PXD012345: more than 2 pages of files"):
        pride.fetch("PXD012345", f)


def test_category_kept_verbatim_and_absent_when_missing():
    odd = dict(PRIDE_FILES[0], fileName="gel.png", fileCategory={"value": "MS_IMAGE"})
    bare = dict(PRIDE_FILES[0], fileName="notes.txt", fileCategory=None)
    f, _ = fetcher({PROJECT: js(PRIDE_PROJECT), _page(0): js([odd, bare]), _page(1): js([])})
    records = pride.fetch("PXD012345", f)[1:]
    assert records[0].fields["fileCategory"] == "MS_IMAGE"
    assert "fileCategory" not in records[1].fields


def test_null_hal_files_and_nameless_entry_are_skipped():
    nameless = {"checksum": "f" * 40}
    f, _ = fetcher({PROJECT: js(PRIDE_PROJECT),
                    _page(0): js({"_embedded": {"files": [nameless, PRIDE_FILES[0]]}}),
                    _page(1): js({"_embedded": {"files": None}})})
    assert [r.record_id for r in pride.fetch("PXD012345", f)][1:] == ["PXD012345/A1.raw"]
