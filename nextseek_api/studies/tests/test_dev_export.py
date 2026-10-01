"""The dev export and its adapter (tool spec 4.3, T26)."""
import json

import pytest

from nextseek_api.studies.sources import dev_export, matching
from nextseek_api.studies.tests.conftest import FakeDriver, FakeReader, uid

U2, U3 = uid(2, kind="D.SEQ"), uid(3, kind="D.SEQ")


def _study(element_id, *, id=None, seek_study_id=None, title="Paper One", doi=None, pmid=None, invs=("Alpha Dev",)):
    return {"element_id": element_id, "id": id, "seek_study_id": seek_study_id, "title": title,
            "description": f"About {title}", "doi": doi, "pmid": pmid, "investigation_titles": list(invs)}


def _driver(studies, samples):
    return FakeDriver({dev_export.EXPORT_STUDIES: lambda p: studies,
                       dev_export.EXPORT_STUDY_SAMPLES: lambda p: samples.get(p["element_id"], [])})


@pytest.mark.parametrize("raw, expected", [(U2 + "-PUB", U2), (U2 + "-PUB3", U2), (U2, U2),
                                           (U2 + "-PUBX", U2 + "-PUBX")])
def test_canonical_uid_strips_a_trailing_pub_only(raw, expected):
    assert dev_export.canonical_uid(raw) == expected


def test_the_export_writes_paper_studies_only_and_reads_the_graph_read_only(tmp_path):
    driver = _driver(
        [_study("e1", id=12, doi="10.0000/one", pmid=1111), _study("e2", seek_study_id=55, title="NAMs Like Study"),
         _study("e3", id=13, title="Alpha Dev Unpublished", doi="10.0000/x"),
         _study("e4", id=14, title="Two Homes", doi="10.0000/y", invs=("A", "B"))],
        {"e1": [{"uid": U2 + "-PUB", "doi": None, "pmid": None}, {"uid": U3, "doi": None, "pmid": None}]})
    out = tmp_path / "dev.json"
    summary = dev_export.export_dev_graph(driver, "neo4j", out, now="2026-01-01T00:00:00Z")
    doc = json.loads(out.read_text())
    assert [s["dev_study_id"] for s in doc["studies"]] == [12]
    assert doc["studies"][0]["sample_uids"] == sorted([U2 + "-PUB", U3])
    assert doc["studies"][0]["pmid"] == "1111"
    assert {(s["dev_study_id"], s["reason"]) for s in doc["skipped"]} == {(13, "bucket"), (14, "investigation_count")}
    assert summary["studies"] == 1
    assert all(call.read for call in driver.calls)


def test_a_named_study_without_a_doi_takes_the_value_most_of_its_samples_carry(tmp_path):
    samples = [{"uid": uid(n, kind="D.SEQ"), "doi": "10.0000/maj", "pmid": "22"} for n in (2, 3, 4)]
    samples.append({"uid": uid(5), "doi": "10.0000/other", "pmid": None})
    driver = _driver([_study("e2", seek_study_id=55, title="Named Study"), _study("e1", id=12, doi="10.0000/one")],
                     {"e2": samples, "e1": []})
    out = tmp_path / "dev.json"
    dev_export.export_dev_graph(driver, "neo4j", out, study_ids=[55])
    [study] = json.loads(out.read_text())["studies"]
    assert (study["dev_study_id"], study["doi"], study["pmid"]) == (55, "10.0000/maj", "22")


def test_a_named_bucket_is_still_never_exported(tmp_path):
    driver = _driver([_study("e3", id=13, title="Alpha Dev Unpublished")], {"e3": []})
    out = tmp_path / "dev.json"
    dev_export.export_dev_graph(driver, "neo4j", out, study_ids=[13])
    assert json.loads(out.read_text())["studies"] == []


def test_the_adapter_maps_the_investigation_strips_pub_and_reports_as_given(tmp_path, alpha):
    path = tmp_path / "dev.json"
    path.write_text(json.dumps({"export_version": 1, "exported_at": "t", "skipped": [], "studies": [
        {"dev_study_id": 12, "title": "Paper One", "description": "About", "doi": "10.0000/one", "pmid": "1111",
         "investigation_title": "Alpha Dev", "sample_uids": [U2 + "-PUB", U2, "TIS-260101ZZZ-9-PUB1"]},
        {"dev_study_id": 13, "title": "Paper Two", "description": None, "doi": "10.0000/two", "pmid": None,
         "investigation_title": "Unmapped Dev", "sample_uids": [U3]}]}))
    aset = dev_export.dev_associations(path, FakeReader(alpha), investigation_map={"alpha dev": "Alpha Investigation"})
    [t] = aset.targets
    assert (t.key, t.investigation_id, t.sample_ids, t.pmid) == ("dev:12", 7, [2], "1111")
    assert sorted(t.provenance["2"]) == [f"dev uid {U2}", f"dev uid {U2}-PUB"]
    assert sorted((u.reason, u.submitted) for u in aset.unmatched) == [
        (matching.INVESTIGATION_UNKNOWN, U3), (matching.SAMPLE_UID_NOT_FOUND, "TIS-260101ZZZ-9-PUB1")]


def test_the_committed_map_holds_title_pairs_only():
    doc = json.loads(dev_export.DEV_INVESTIGATIONS.read_text(encoding="utf-8"))
    assert set(doc) == {"pairs"}
    assert all(isinstance(pair, list) and len(pair) == 2 and all(isinstance(t, str) and not t.isdigit() for t in pair)
               for pair in doc["pairs"])
    assert dev_export.load_investigation_map()


def test_an_unknown_export_version_is_refused(tmp_path, alpha):
    path = tmp_path / "dev.json"
    path.write_text(json.dumps({"export_version": 99, "studies": []}))
    with pytest.raises(ValueError, match="export_version"):
        dev_export.dev_associations(path, FakeReader(alpha), investigation_map={})
