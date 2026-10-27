from datetime import datetime, timezone

from NessieAI.ns.paper.context import (ContextSeams, ExportRow, ProjectFacts, build_context, infer_project,
                                       lab_from_uids, match_existing)
from NessieAI.ns.paper.curation_models import PrecedentStudy, SampleTypeDef
from NessieAI.ns.paper.models import (Author, DepositRecord, EvidenceArtifact, FileRef, PaperArtifact, PaperRef,
                                      PaperText, SkeletonRow)

REF = PaperRef(doi="10.1000/xyz123", title="Mice and RNA.", authors=[
    Author(name="Doe J", affiliation="Koch Institute, MIT, Cambridge"), Author(name="ACME Consortium", collective=True),
    Author(name="White FM", affiliation="Department of Biological Engineering, MIT")])
PROJECTS = [
    ProjectFacts(project_id=10, title="CSBC", pi_text="White, Forest M. (MIT, contact PI)", members=(("Ann", "Roe"),),
                 institutions=("Massachusetts Institute of Technology",)),
    ProjectFacts(project_id=4, title="MetNet", pi_text="Kamm, Roger", members=(("Jane", "Doe"),),
                 institutions=("MIT",)),
]


def test_a_paper_already_in_a_project_goes_there():
    choice = infer_project(REF, [4], PROJECTS)
    assert choice.chosen.project_id == 4 and choice.chosen.score >= 100


def test_the_pi_surname_wins_and_the_basis_says_why():
    choice = infer_project(REF, [], PROJECTS)
    assert choice.chosen.project_id == 10
    assert any("White" in b for b in choice.chosen.basis)
    assert [c.project_id for c in choice.candidates] == [10, 4]


def test_a_tie_or_a_weak_score_chooses_nothing():
    tie = [ProjectFacts(project_id=1, title="A", pi_text="White"), ProjectFacts(project_id=2, title="B", pi_text="White")]
    assert infer_project(REF, [], tie).chosen is None
    weak = [ProjectFacts(project_id=3, title="C", institutions=("MIT",))]
    assert infer_project(REF, [], weak).chosen is None


EVIDENCE = EvidenceArtifact(
    paper=REF, items=[], gaps=[],
    deposits=[DepositRecord(repo="geo", record_id="GSM1", kind="sample", title="liver rep1")],
    skeleton=[SkeletonRow(row_key="geo:GSM1", repo="geo", record_id="GSM1", kind="raw", title="liver rep1",
                          files=[FileRef(name="SRR11_1.fastq.gz", url="https://x/SRR11_1.fastq.gz")])])


def test_existing_records_match_by_doi_accession_filename_and_name():
    rows = [ExportRow(uid="D.SEQ-240101MIT-1", sample_type="D.SEQ", metadata={"Link_SRA": "GSM1"}),
            ExportRow(uid="D.SEQ-240101MIT-2", sample_type="D.SEQ",
                      metadata={"Link_PrimaryData": "s3://bucket/run/SRR11_1.fastq.gz"}),
            ExportRow(uid="CEL-240101MIT-3", sample_type="CEL", metadata={"Name": "liver rep1"}),
            ExportRow(uid="MUS-240101MIT-4", sample_type="MUS", metadata={"DOI": "https://doi.org/10.1000/XYZ123"}),
            ExportRow(uid="MUS-240101MIT-5", sample_type="MUS", metadata={"Name": "unrelated"})]
    found = {(r.uid, r.matched_on, r.skeleton_row) for r in match_existing(rows, EVIDENCE)}
    assert found == {("D.SEQ-240101MIT-1", "accession", "geo:GSM1"), ("D.SEQ-240101MIT-2", "filename", "geo:GSM1"),
                     ("CEL-240101MIT-3", "name", "geo:GSM1"), ("MUS-240101MIT-4", "doi", None)}


def test_the_lab_code_is_the_projects_most_used():
    assert lab_from_uids(["A-240101MIT-1", "B-240101MIT-2", "C-240101ABC-1", "free"]) == ("MIT", "2 of 3 UIDs in the target project")
    assert lab_from_uids(["A-240101MIT-1", "B-240101ABC-1"])[0] is None
    assert lab_from_uids([])[0] is None


def test_build_context_assembles_the_artifact():
    pulled = []
    seams = ContextSeams(
        projects=lambda: PROJECTS,
        sample_types=lambda: [SampleTypeDef(code="MUS")],
        house_assays=lambda: ["RNA-seq"],
        export_rows=lambda ids: pulled.append(ids) or [ExportRow(uid="MUS-240101MIT-4", sample_type="MUS",
                                                                 metadata={"Name": "x"})],
        precedent=lambda pid: [PrecedentStudy(study_id=7, title="Older paper", assays=["RNA-seq"])],
        now=lambda: datetime(2026, 10, 27, tzinfo=timezone.utc),
        rulebook_sha256="a" * 64)
    paper = PaperArtifact(ref=REF, text=PaperText(), text_source="europepmc", mode="new")
    ctx = build_context(paper, EVIDENCE, seams)
    assert ctx.project.chosen.project_id == 10 and pulled == [[10]]
    assert ctx.lab_code == "MIT" and ctx.export.uids == ["MUS-240101MIT-4"] and ctx.precedent[0].study_id == 7
    assert ctx.rulebook.file_sha256 == "a" * 64 and ctx.house_assays == ["RNA-seq"]
