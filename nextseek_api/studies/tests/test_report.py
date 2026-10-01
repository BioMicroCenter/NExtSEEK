"""The run directory's files (tool spec 6.8)."""
import csv
import json

from nextseek_api.studies import planner as p
from nextseek_api.studies import report
from nextseek_api.studies.models import AssociationSet, StudyMovePlan, StudyTarget, Unmatched
from nextseek_api.studies.tests.conftest import FakeReader


def _aset():
    return AssociationSet(
        source="sheet", source_ref="s.csv sha256:0", created_at="t",
        targets=[StudyTarget(key="sheet:7:paper one", investigation_id=7, title="Paper One", doi="10.0000/one",
                             sample_ids=[3, 5], provenance={"3": ["row 2"], "5": ["row 3"]})],
        unmatched=[Unmatched(reason="sample_uid_not_found", target_key="sheet:7:paper one",
                             submitted="TIS-260101ZZZ-9", provenance=["row 4"])])


def test_write_plan_files(tmp_path, alpha):
    aset = _aset()
    alpha.stored = [{"child_id": 3, "parent_id": 2, "stored": alpha.labels(3, 2)}]
    plan = p.plan_study_moves(aset, FakeReader(alpha), run_id=tmp_path.name, now="t")
    written = report.write_plan_files(tmp_path, plan, aset)
    assert sorted(x.name for x in written) == sorted([report.ASSOCIATIONS_FILE, report.PLAN_FILE, report.PLAN_TEXT,
                                                      report.UNMATCHED_JSON, report.UNMATCHED_CSV])
    assert StudyMovePlan.from_file(tmp_path / report.PLAN_FILE) == plan
    assert AssociationSet.from_file(tmp_path / report.ASSOCIATIONS_FILE).sha256() == plan.associations_sha256
    rows = list(csv.DictReader((tmp_path / report.UNMATCHED_CSV).open(encoding="utf-8")))
    assert [(r["reason"], r["submitted"], r["sample_id"], r["study_title"]) for r in rows] == [
        ("sample_in_no_assay", "row 3", "5", "Paper One"),
        ("sample_uid_not_found", "TIS-260101ZZZ-9", "", "Paper One")]
    assert json.loads((tmp_path / report.UNMATCHED_JSON).read_text())[1]["reason"] == "sample_uid_not_found"
    text = (tmp_path / report.PLAN_TEXT).read_text()
    for needle in ("Targets (1)", "create", "101 -> create", "unit 1: 2 inserts (1 movers, 1 parents), 1 removals",
                   "Label changes the graph step will write", "3 -> 2  changed  assay_id",
                   "Skipped samples by reason", "sample_in_no_assay: 1", "Publications: 2 rows"):
        assert needle in text, needle


def test_progress_before_apply(tmp_path, alpha):
    aset = _aset()
    plan = p.plan_study_moves(aset, FakeReader(alpha), run_id=tmp_path.name, now="t")
    report.write_plan_files(tmp_path, plan, aset)
    got = report.progress(tmp_path)
    assert got["units"] == {"planned": 1, "committed": 0, "undone": 0}
    assert (got["apply_done"], got["graph_done_for"], got["undone"], got["journal_unreadable_lines"]) == (
        False, [], False, 0)


def test_progress_names_the_investigations_the_graph_step_finished(tmp_path, alpha):
    from nextseek_api.studies.journal import JOURNAL_FILE, Journal

    aset = _aset()
    plan = p.plan_study_moves(aset, FakeReader(alpha), run_id=tmp_path.name, now="t")
    report.write_plan_files(tmp_path, plan, aset)
    journal = Journal(tmp_path / JOURNAL_FILE, run_id=plan.run_id)
    journal.append("graph", "done", investigation=7, counts={})
    assert report.progress(tmp_path)["graph_done_for"] == [7]
    journal.append("graph", "done", investigation=None, counts={})
    assert report.progress(tmp_path)["graph_done_for"] == ["all"]
