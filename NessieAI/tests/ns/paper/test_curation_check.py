import copy

import pytest

from NessieAI.ns.paper import curation_check as cc
from NessieAI.ns.paper.rulebook import load_rulebook

EVIDENCE = {
    "paper": {"doi": "10.1000/xyz123", "pmid": "38000001", "title": "Mice and RNA."},
    "items": [
        {"id": "E:methods:p1", "text": "All mice were female C57BL/6J, 8-week old.", "source": "Methods p1"},
        {"id": "E:methods:p2", "text": "RNA was extracted with TRIzol and sequenced.", "source": "Methods p2"},
        {"id": "E:results:p1", "text": "We saw things.", "source": "Results p1"},
        {"id": "E:geo:GSM1:title", "text": "liver rep1", "source": "GEO GSM1"},
    ],
    "deposits": [],
    "skeleton": [{"row_key": "geo:GSM1", "repo": "geo", "record_id": "GSM1", "kind": "raw", "title": "liver rep1",
                  "files": [{"name": "a.fastq.gz", "url": "https://x/a.fastq.gz"}], "evidence_ids": []},
                 {"row_key": "geo:GSM2", "repo": "geo", "record_id": "GSM2", "kind": "raw", "title": "liver rep2",
                  "files": [], "evidence_ids": []}],
    "gaps": [],
}
CONTEXT = {
    "sample_types": [
        {"code": "MUS", "clade": "Source", "parents": [], "attributes": [{"title": "Sex"}, {"title": "Strain"},
                                                                         {"title": "Age"}]},
        {"code": "TIS", "clade": "Processed", "parents": ["MUS"], "attributes": [{"title": "Organ"}]},
        {"code": "CEL", "clade": "Source", "parents": ["MUS", "TIS", "CEL"], "attributes": []},
        {"code": "D.SEQ", "clade": "Raw", "parents": ["TIS", "CEL"],
         "attributes": [{"title": "DOI"}, {"title": "PMID"}, {"title": "Name"}]},
        {"code": "A.GEX", "clade": "Analyzed", "parents": ["D.SEQ"], "attributes": []},
        {"code": "AB", "clade": "Source", "parents": [], "attributes": []},
    ],
    "house_assays": ["RNA-seq", "Tissue Collection"],
}
RULEBOOK = load_rulebook().model_dump(mode="json")


def _curation():
    return {
        "tree": [
            {"node_id": "mus", "sample_type": "MUS", "label": "mice"},
            {"node_id": "liver", "sample_type": "TIS", "label": "liver",
             "parents": [{"node_id": "mus", "assay": "Tissue Collection"}]},
            {"node_id": "seq", "sample_type": "D.SEQ", "label": "RNA-seq", "skeleton_rows": ["geo:GSM1"],
             "parents": [{"node_id": "liver", "assay": "RNA-seq"}]},
        ],
        "rows": [
            {"row_key": "node:mus:1", "node_id": "mus", "values": {
                "Sex": {"value": "female", "evidence": ["E:methods:p1"]},
                "Strain": {"value": "C57BL/6J", "evidence": ["E:methods:p1"]},
                "Age": {"value": "8 weeks", "evidence": ["E:methods:p1"]}}},
            {"row_key": "node:liver:1", "node_id": "liver", "values": {"Organ": {"blank": "not_in_sources"}}},
            {"row_key": "geo:GSM1", "node_id": "seq", "values": {
                "Name": {"value": "liver rep1", "evidence": ["E:geo:GSM1:title"]},
                "DOI": {"value": "10.1000/xyz123", "evidence": []}}},
        ],
        "excluded": [{"row_key": "geo:GSM2", "reason": "no raw file deposited", "rule": None}],
        "protocols": [{"assay": "RNA-seq", "title": "RNA extraction", "paragraphs": ["E:methods:p2"]}],
        "decisions": [], "gaps": [], "errata": [],
    }


def _codes(findings):
    return sorted({f["code"] for f in findings})


def run(cur, project_id=None, context=CONTEXT, evidence=EVIDENCE):
    return cc.check_all(cur, evidence, context, RULEBOOK, project_id=project_id)


def test_a_clean_curation_has_no_findings():
    assert run(_curation()) == []


def test_squash_keeps_letters_digits_and_dots():
    assert cc.squash("  C57BL/6J,  8-Week ") == "c57bl6j8week"
    assert cc.squash("ﬁbroblast") == "fibroblast"


def test_a_value_matches_whole_words_in_order():
    assert cc._in_text("female", ["All mice were female."])
    assert not cc._in_text("male", ["All mice were female."])
    assert cc._in_text("8 weeks", ["8-week old mice"])
    assert not cc._in_text("weeks 8", ["8-week old mice"])


@pytest.mark.parametrize("value, ok", [("female", True), ("Female", True), ("8-week", True), ("8 weeks", True),
                                       ("male", False), ("12 weeks", False)])
def test_a_quoted_value_must_be_in_its_cited_text(value, ok):
    cur = _curation()
    cur["rows"][0]["values"]["Sex"]["value"] = value
    assert ("value_not_in_sources" not in _codes(run(cur))) is ok


def test_an_unresolved_evidence_id_is_reported():
    cur = _curation()
    cur["rows"][0]["values"]["Sex"]["evidence"] = ["E:methods:p99"]
    assert "evidence_unresolved" in _codes(run(cur))


def test_a_decision_backed_value_is_checked_against_the_decision():
    cur = _curation()
    cur["rows"][0]["values"]["Strain"] = {"value": "Mus musculus", "evidence": [], "decision": "D-1"}
    assert "decision_unknown" in _codes(run(cur))
    cur["decisions"] = [{"id": "D-1", "topic": "organism", "choice": "Organism is Mus musculus: C57BL/6J mice.",
                         "basis": "evidence", "refs": ["E:methods:p1"]}]
    assert run(cur) == []
    cur["decisions"][0]["choice"] = "Organism from the strain."
    assert "value_not_in_sources" in _codes(run(cur))


def test_a_rule_backed_value_needs_a_value_rule():
    cur = _curation()
    cur["rows"][0]["values"]["Strain"] = {"value": "C57BL/6J", "evidence": ["E:methods:p1"], "rule": "R-NOPE"}
    assert "rule_unknown" in _codes(run(cur))
    cur["rows"][0]["values"]["Strain"]["rule"] = "R-TIER-MUS-TIS-CEL"
    assert "rule_not_a_value_rule" in _codes(run(cur))
    cur["rows"][0]["values"]["Strain"]["rule"] = "R-VERBATIM"
    assert run(cur) == []


def test_an_attribute_the_type_does_not_have():
    cur = _curation()
    cur["rows"][0]["values"]["Diet"] = {"value": "female", "evidence": ["E:methods:p1"]}
    found = run(cur)
    assert "attribute_unknown" in _codes(found)
    assert any(f["code"] == "constraint" and f["rule"] == "R-NO-NEW-ATTRIBUTES" for f in found)


def test_an_edge_the_hierarchy_does_not_allow_and_an_unknown_assay():
    cur = _curation()
    cur["tree"][2]["parents"] = [{"node_id": "mus", "assay": "RNA-seq"}]
    assert "edge_not_allowed" in _codes(run(cur))
    cur = _curation()
    cur["tree"][2]["parents"][0]["assay"] = "RNAseq (made up)"
    assert "assay_unknown" in _codes(run(cur))


def test_every_skeleton_row_is_placed_or_excluded():
    cur = _curation()
    cur["excluded"] = []
    found = run(cur)
    assert any(f["code"] == "skeleton_uncovered" and f["row_key"] == "geo:GSM2" for f in found)
    cur = _curation()
    cur["rows"].append({"row_key": "geo:GSM9", "node_id": "seq", "values": {}})
    assert "row_key_unknown" in _codes(run(cur))


def test_doi_only_on_the_papers_own_rows():
    cur = _curation()
    cur["rows"][2]["values"]["DOI"]["value"] = "10.9999/other"
    assert "doi_misplaced" in _codes(run(cur))
    cur = _curation()
    cur["rows"][2]["reuse_uid"] = "D.SEQ-240101MIT-1"
    cur["rows"][2]["reuse_evidence"] = ["E:geo:GSM1:title"]
    assert "doi_misplaced" in _codes(run(cur))


def test_structural_problems_are_structural():
    cur = _curation()
    cur["rows"][0]["node_id"] = "ghost"
    cur["tree"][1]["parents"][0]["node_id"] = "ghost"
    found = run(cur)
    assert {"node_unknown", "parent_unknown"} <= set(_codes(found))
    assert all(f["code"] in cc.STRUCTURAL for f in found if f["code"] in ("node_unknown", "parent_unknown"))
    cur = _curation()
    cur["tree"][0]["parents"] = [{"node_id": "seq", "assay": "RNA-seq"}]
    assert "cycle" in _codes(run(cur))


def test_a_bad_shape_stops_before_the_other_checks():
    found = run({"tree": "not a list"})
    assert found and {f["code"] for f in found} == {"shape"}


def test_a_reuse_without_evidence_is_a_warning():
    cur = _curation()
    cur["rows"][0]["reuse_uid"] = "MUS-240101MIT-3"
    found = [f for f in run(cur) if f["code"] == "reuse_without_evidence"]
    assert found and found[0]["severity"] == "warn"


@pytest.mark.parametrize("mutate, rule", [
    (lambda c: c["rows"][0]["values"].__setitem__("Strain", {"value": "TBD", "evidence": []}), "R-NO-PLACEHOLDER"),
    (lambda c: c["tree"].append({"node_id": "cel", "sample_type": "CEL", "label": "cells",
                                 "parents": [{"node_id": "mus", "assay": "Tissue Collection"}]}),
     "R-TIER-MUS-TIS-CEL"),
    (lambda c: c["tree"].append({"node_id": "ab", "sample_type": "AB", "label": "antibody"}),
     "R-REAGENTS-ARE-ATTRIBUTES"),
    (lambda c: (c["tree"].append({"node_id": "gex", "sample_type": "A.GEX", "label": "counts",
                                  "parents": [{"node_id": "seq", "assay": "RNA-seq"}]}),
                c["rows"].append({"row_key": "node:gex:1", "node_id": "gex", "values": {}})),
     "R-ANALYSIS-NEEDS-FILE"),
    (lambda c: c["protocols"].append({"assay": "RNA-seq", "title": "made up", "paragraphs": ["E:results:p1"]}),
     "R-PROTOCOL-VERBATIM"),
])
def test_each_constraint_rule_has_a_fixture_that_fails(mutate, rule):
    """Spec §11: each constraint rule has a fixture that must fail stage 4."""
    cur = _curation()
    mutate(cur)
    assert any(f["code"] == "constraint" and f["rule"] == rule for f in run(cur)), rule


def test_a_project_scoped_exclusion_applies_only_in_its_project():
    cur = _curation()
    cur["excluded"] = []
    cur["tree"][2]["skeleton_rows"].append("geo:GSM2")
    cur["rows"].append({"row_key": "geo:GSM2", "node_id": "seq", "values": {}})
    assert not any(f.get("rule") == "R-CSBC-RAW-FILE" for f in run(cur, project_id=4))
    assert any(f.get("rule") == "R-CSBC-RAW-FILE" and f["row_key"] == "geo:GSM2" for f in run(cur, project_id=10))


def test_the_check_module_imports_only_the_standard_library():
    import ast
    import pathlib
    import sys

    tree = ast.parse(pathlib.Path(cc.__file__).read_text())
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert names <= set(sys.stdlib_module_names) | {"__future__"}, names
