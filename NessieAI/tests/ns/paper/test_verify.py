import copy
from datetime import date, datetime, timezone

import pytest

from NessieAI.ns.paper.curation_models import (Blank, ContextArtifact, CurationArtifact, ExportSummary,
                                               ProjectCandidate, ProjectChoice, RulebookVersion, SampleTypeDef)
from NessieAI.ns.paper.models import EvidenceArtifact, EvidenceItem, PaperRef, SkeletonRow
from NessieAI.ns.paper.rulebook import load_rulebook
from NessieAI.ns.paper.verify import CurationUnusable, needs_bounce, verify

EVIDENCE = EvidenceArtifact(
    paper=PaperRef(doi="10.1000/xyz123", published="2024-05-20"),
    items=[EvidenceItem(id="E:methods:p1", text="All mice were female C57BL/6J.", source="Methods p1")],
    deposits=[], gaps=[],
    skeleton=[SkeletonRow(row_key="geo:GSM1", repo="geo", record_id="GSM1", kind="raw", submitted=date(2024, 2, 3)),
              SkeletonRow(row_key="geo:GSM2", repo="geo", record_id="GSM2", kind="raw", submitted=date(2024, 2, 3))])
CONTEXT = ContextArtifact(
    mode="new", project=ProjectChoice(chosen=ProjectCandidate(project_id=4, title="MetNet", score=20)),
    sample_types=[SampleTypeDef(code="MUS", clade="Source", attributes=[{"title": "Sex"}]),
                  SampleTypeDef(code="TIS", clade="Processed", parents=["MUS"]),
                  SampleTypeDef(code="D.SEQ", clade="Raw", parents=["TIS"])],
    house_assays=["RNA-seq", "Tissue Collection"], lab_code="MIT",
    export=ExportSummary(project_ids=[4], pulled_at=datetime.now(timezone.utc), samples=0, uids=[]),
    rulebook=RulebookVersion(file_sha256="0" * 64))
RULEBOOK = load_rulebook()
BASE = {
    "tree": [{"node_id": "mus", "sample_type": "MUS", "label": "mice"},
             {"node_id": "liver", "sample_type": "TIS", "label": "liver",
              "parents": [{"node_id": "mus", "assay": "Tissue Collection"}]},
             {"node_id": "seq", "sample_type": "D.SEQ", "label": "seq", "skeleton_rows": ["geo:GSM1", "geo:GSM2"],
              "parents": [{"node_id": "liver", "assay": "RNA-seq"}]}],
    "rows": [{"row_key": "node:mus:1", "node_id": "mus",
              "values": {"Sex": {"value": "female", "evidence": ["E:methods:p1"]}}},
             {"row_key": "node:liver:1", "node_id": "liver"},
             {"row_key": "geo:GSM1", "node_id": "seq"}, {"row_key": "geo:GSM2", "node_id": "seq"}],
}


def _cur(mutate=None):
    data = copy.deepcopy(BASE)
    if mutate:
        mutate(data)
    return CurationArtifact.model_validate(data)


def test_a_clean_curation_passes_with_a_uid_for_every_row():
    out = verify(_cur(), EVIDENCE, CONTEXT, RULEBOOK, bounced=False)
    assert out.findings == [] and out.held == [] and len(out.uid_plan) == 4 and not out.bounced


def test_a_value_not_in_its_source_is_blanked():
    out = verify(_cur(lambda d: d["rows"][0]["values"]["Sex"].update(value="male")), EVIDENCE, CONTEXT, RULEBOOK,
                 bounced=True)
    assert out.curation.rows[0].values["Sex"] == Blank(blank="not_in_sources") and needs_bounce(out.findings)


def test_an_unknown_attribute_is_dropped_and_reported():
    out = verify(_cur(lambda d: d["rows"][0]["values"].update(Diet={"value": "female", "evidence": ["E:methods:p1"]})),
                 EVIDENCE, CONTEXT, RULEBOOK, bounced=True)
    assert "Diet" not in out.curation.rows[0].values and any(g.topic == "attribute" for g in out.gaps)


def test_a_bad_edge_holds_the_nodes_rows_and_they_get_no_uid():
    out = verify(_cur(lambda d: d["tree"][2]["parents"][0].update(node_id="mus")), EVIDENCE, CONTEXT, RULEBOOK,
                 bounced=True)
    assert {h.row_key for h in out.held} == {"geo:GSM1", "geo:GSM2"}
    assert {p.row_key for p in out.uid_plan} == {"node:mus:1", "node:liver:1"}


def test_an_uncovered_deposit_row_is_excluded_and_a_gap():
    def drop(d):
        d["rows"] = d["rows"][:3]
    out = verify(_cur(drop), EVIDENCE, CONTEXT, RULEBOOK, bounced=True)
    assert [e.row_key for e in out.curation.excluded] == ["geo:GSM2"]
    assert any("geo:GSM2" in g.detail for g in out.gaps)


def test_a_reuse_without_evidence_is_minted_new():
    out = verify(_cur(lambda d: d["rows"][0].update(reuse_uid="MUS-200101ABC-1")), EVIDENCE, CONTEXT, RULEBOOK,
                 bounced=False)
    assert out.curation.rows[0].reuse_uid is None
    assert not needs_bounce(out.findings)  # a warning alone does not bounce


def test_structural_problems_make_the_curation_unusable():
    with pytest.raises(CurationUnusable) as err:
        verify(_cur(lambda d: d["rows"][1].update(node_id="ghost")), EVIDENCE, CONTEXT, RULEBOOK, bounced=True)
    assert err.value.findings[0].code == "node_unknown"
