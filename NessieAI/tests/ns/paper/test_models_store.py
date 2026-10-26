import pytest
from pydantic import ValidationError

from NessieAI.ns.paper.models import FileRef, Paragraph, PaperRef, PaperText, Section
from NessieAI.ns.paper.store import load_artifact, save_artifact


def test_paper_text_roles_need_paragraphs():
    text = PaperText(sections=[
        Section(slug="methods", title="Methods", paragraphs=[Paragraph(id="E:methods:p1", text="We did x.")]),
        Section(slug="results", title="Results", paragraphs=[]),
    ], captions=[Paragraph(id="E:cap:1", text="Figure 1 A.")])
    assert text.has("methods")
    assert not text.has("results")
    assert not text.has("data_availability")
    assert text.all_text() == ["We did x.", "Figure 1 A."]


def test_models_refuse_unknown_keys():
    with pytest.raises(ValidationError):
        FileRef(name="a", url="u", colour="red")


def test_store_round_trip_and_stable_key(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    ref = PaperRef(pmid="1", title="T")
    key = save_artifact("abc123", "paper", ref)
    assert key.startswith("paper.") and len(key) == len("paper.") + 16
    assert load_artifact("abc123", key, PaperRef) == ref
    assert save_artifact("abc123", "paper", ref) == key
    assert save_artifact("abc123", "paper", PaperRef(pmid="2")) != key


def test_store_refuses_unsafe_names(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_PAPER_REINGEST_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        save_artifact("../x", "paper", PaperRef())
    with pytest.raises(ValueError):
        save_artifact("abc", "pa/per", PaperRef())
    with pytest.raises(ValueError):
        load_artifact("abc", "../../etc/passwd", PaperRef)


def _evidence(items, evidence_ids):
    from NessieAI.ns.paper.models import EvidenceArtifact, EvidenceItem, SkeletonRow

    row = SkeletonRow(row_key="geo:GSM1", repo="geo", record_id="GSM1", kind="raw", evidence_ids=evidence_ids)
    return EvidenceArtifact(paper=PaperRef(pmid="1"), items=[EvidenceItem(id=i, text="t", source="s") for i in items],
                            deposits=[], skeleton=[row], gaps=[])


def test_evidence_item_ids_must_be_unique():
    with pytest.raises(ValidationError, match="E:geo:GSM1:title"):
        _evidence(["E:geo:GSM1:title", "E:geo:GSM1:title"], ["E:geo:GSM1:title"])


def test_every_skeleton_evidence_id_must_be_an_item():
    with pytest.raises(ValidationError, match="E:geo:GSM1:missing"):
        _evidence(["E:geo:GSM1:title"], ["E:geo:GSM1:title", "E:geo:GSM1:missing"])
    assert _evidence(["E:geo:GSM1:title"], ["E:geo:GSM1:title"]).skeleton[0].row_key == "geo:GSM1"
