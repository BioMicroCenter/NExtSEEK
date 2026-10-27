from datetime import date, datetime, timezone

from NessieAI.ns.paper.curation_models import (ContextArtifact, CurationArtifact, ExportSummary, ProjectChoice,
                                               RulebookVersion)
from NessieAI.ns.paper.models import EvidenceArtifact, PaperRef, SkeletonRow
from NessieAI.ns.paper.uid_plan import STAMP_DECISION, plan_uids, used_counters


def _evidence(submitted=(date(2024, 2, 3), date(2024, 1, 15)), published="2024-05-20"):
    rows = [SkeletonRow(row_key=f"geo:GSM{i}", repo="geo", record_id=f"GSM{i}", kind="raw", submitted=day)
            for i, day in enumerate(submitted, start=1)]
    return EvidenceArtifact(paper=PaperRef(doi="10.1/x", published=published), items=[], deposits=[],
                            skeleton=rows, gaps=[])


def _context(uids=(), lab="MIT"):
    return ContextArtifact(mode="new", project=ProjectChoice(), sample_types=[], house_assays=[], lab_code=lab,
                           export=ExportSummary(project_ids=[1], pulled_at=datetime.now(timezone.utc),
                                                samples=len(uids), uids=list(uids)),
                           rulebook=RulebookVersion(file_sha256="0" * 64))


def _curation(rows=None):
    return CurationArtifact.model_validate({
        "tree": [{"node_id": "mus", "sample_type": "MUS", "label": "mice"},
                 {"node_id": "seq", "sample_type": "D.SEQ", "label": "seq"}],
        "rows": rows or [{"row_key": "node:mus:1", "node_id": "mus"}, {"row_key": "geo:GSM1", "node_id": "seq"},
                         {"row_key": "geo:GSM2", "node_id": "seq"}],
    })


def test_data_rows_take_their_deposit_date_and_upstream_rows_the_earliest():
    plan, decisions, held = plan_uids(_curation(), _evidence(), _context())
    uids = {p.row_key: p.uid for p in plan}
    assert uids == {"node:mus:1": "MUS-240115MIT-1", "geo:GSM1": "D.SEQ-240203MIT-1", "geo:GSM2": "D.SEQ-240115MIT-1"}
    assert decisions == [] and held == []


def test_the_counter_continues_past_the_highest_number_in_use():
    plan, _, _ = plan_uids(_curation(), _evidence(), _context(uids=["D.SEQ-240203MIT-7", "D.SEQ-240203MIT-2-PUB",
                                                                     "D.SEQ-240203ABC-40", "free text"]))
    assert {p.row_key: p.uid for p in plan}["geo:GSM1"] == "D.SEQ-240203MIT-8"


def test_used_counters():
    assert used_counters(["CEL-230101MIT-3", "CEL-230101MIT-12-PUB", "x"]) == {("CEL", "230101", "MIT"): 12}


def test_no_deposit_date_falls_back_to_the_publication_date_as_a_decision():
    plan, decisions, held = plan_uids(_curation(), _evidence(submitted=(None, None), published="2024-05"),
                                      _context())
    assert {p.uid for p in plan} == {"MUS-240501MIT-1", "D.SEQ-240501MIT-1", "D.SEQ-240501MIT-2"}
    assert [d.id for d in decisions] == [STAMP_DECISION] and held == []


def test_a_year_alone_holds_the_new_rows():
    plan, _, held = plan_uids(_curation(), _evidence(submitted=(None, None), published="2024"), _context())
    assert plan == [] and len(held) == 3 and "year" in held[0].reason


def test_no_lab_code_holds_the_new_rows_and_a_reused_row_keeps_its_uid():
    rows = [{"row_key": "node:mus:1", "node_id": "mus", "reuse_uid": "MUS-200101ABC-4"},
            {"row_key": "geo:GSM1", "node_id": "seq"}]
    plan, _, held = plan_uids(_curation(rows), _evidence(), _context(lab=None))
    assert [(p.row_key, p.uid, p.reused) for p in plan] == [("node:mus:1", "MUS-200101ABC-4", True)]
    assert [h.row_key for h in held] == ["geo:GSM1"]
