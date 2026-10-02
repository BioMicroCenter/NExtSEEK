"""One model, three sources (tool spec 11.1): one logical input written as a sheet, a dev export and a graph gives
the same AssociationSet core, byte for byte. The keys, provenance and source reference name the source by design and
are compared apart."""
import json

from nextseek_api.studies.sources import dev_export, graph_only
from nextseek_api.studies.sources.sheet import sheet_associations
from nextseek_api.studies.tests.conftest import FakeDriver, FakeReader, uid

U2, U3 = uid(2, kind="D.SEQ"), uid(3, kind="D.SEQ")


def test_one_logical_input_gives_one_core(tmp_path, alpha):
    reader = FakeReader(alpha)
    sheet = tmp_path / "s.csv"
    sheet.write_text("study_title,investigation_title,sample_uuid,study_description,doi,pmid\n"
                     f"Paper One,Alpha Investigation,{U2},About Paper One,10.0000/one,1111\n"
                     f"Paper One,Alpha Investigation,{U3},,,\n", encoding="utf-8")
    dev = tmp_path / "dev.json"
    dev.write_text(json.dumps({"export_version": 1, "exported_at": "t", "skipped": [], "studies": [
        {"dev_study_id": 12, "title": "Paper One", "description": "About Paper One", "doi": "10.0000/one",
         "pmid": "1111", "investigation_title": "Alpha Dev", "sample_uids": [U2 + "-PUB", U3]}]}))
    driver = FakeDriver({
        graph_only.GRAPH_ONLY_PAPERS: lambda p: [{"id": 90, "title": "Paper One", "description": "About Paper One",
                                                  "doi": "10.0000/one", "pmid": 1111, "investigation_ids": [7],
                                                  "investigation_titles": ["Alpha Investigation"]}],
        graph_only.PAPER_SAMPLES: lambda p: [{"id": 2}, {"id": 3}]})

    one = sheet_associations(sheet, None, reader)
    two = dev_export.dev_associations(dev, reader, investigation_map={"Alpha Dev": "Alpha Investigation"})
    three = graph_only.graph_only_associations(driver, "neo4j", "all", reader)

    assert one.core_json() == two.core_json() == three.core_json()
    assert [t.key for t in (one.targets + two.targets + three.targets)] == [
        "sheet:7:paper one", "dev:12", "graph_only:90"]
