"""plan_share (tool spec 16.2, 16.7, T32 to T35): the share's dry run over conftest's share_world."""
import pytest

from nextseek_api.graph_sync import labels
from nextseek_api.studies import report
from nextseek_api.studies import share as sh
from nextseek_api.studies.models import ProjectInsert, ShareInput
from nextseek_api.studies.tests.conftest import AssayRow, FakeReader, add_sample, apply_to_world, uid

U1, U2, U3, U4, U5, U6 = (uid(1), uid(2, kind="D.SEQ"), uid(3, kind="D.SEQ"), uid(4, kind="IMG"), uid(5),
                          uid(6, "BBB"))


def inp(*uids, p=3, q=5, d=40):
    return ShareInput(sample_uids=list(uids), source_project_id=p, destination_project_id=q, destination_study_id=d,
                      created_at="t")


def plan(world, *uids, **kw):
    return sh.plan_share(inp(*uids, **kw), FakeReader(world), run_id="share-1", now="t")


def skips(result):
    return sorted((s.sample_id, s.reason) for s in result.skipped)


def inserts(result):
    return [(x.source_assay_id, x.sample_id, x.direction, x.role) for u in result.units for x in u.inserts]


def test_same_project_refuses(share):
    with pytest.raises(sh.ShareRefused) as exc:
        plan(share, U3, q=3)
    assert exc.value.code == sh.SAME_PROJECT


def test_unknown_projects_and_study_refuse(share):
    for kwargs, code in (({"p": 99}, sh.SOURCE_PROJECT_UNKNOWN), ({"q": 99}, sh.DESTINATION_PROJECT_UNKNOWN),
                         ({"d": 999}, sh.DESTINATION_STUDY_UNKNOWN)):
        with pytest.raises(sh.ShareRefused) as exc:
            plan(share, U3, **kwargs)
        assert exc.value.code == code


def test_a_study_outside_the_destination_project_refuses(share):
    with pytest.raises(sh.ShareRefused) as exc:
        plan(share, U3, d=20)
    assert exc.value.code == sh.DESTINATION_STUDY_NOT_IN_DESTINATION_PROJECT


def test_a_group_with_a_matching_destination_assay_is_reused(share):
    result = plan(share, U4)
    [clone] = result.targets[0].clones
    assert (clone.action, clone.seek_assay_id, clone.source_assay_id, clone.group_source_assay_ids) == (
        "reuse", 401, 102, [102])
    assert inserts(result) == [(102, 4, 2, "mover"), (102, 1, 1, "parent")]


def test_a_group_without_one_is_cloned_with_the_destination_studys_policy(share):
    result = plan(share, U3)
    [clone] = result.targets[0].clones
    assert (clone.action, clone.payload, clone.policy_from_study, clone.placeholder_id) == ("create", None, 40, 402)
    assert clone.policy == share.policies[40] and result.summary["clone_policy"] == share.policies[40]
    assert "access view" in report.render_share_text(result)
    assert (clone.title, clone.internal_assay_ids) == ("RNA-seq run", [900])
    t = result.targets[0]
    assert (t.key, t.investigation_id, t.study.action, t.study.seek_study_id) == ("share:40", 9, "existing", 40)
    assert result.mode == "share" and result.share.destination_study_id == 40


def test_two_source_assays_of_one_title_and_mapping_make_one_clone(share):
    share.assays[104] = AssayRow(104, 20, "rna-seq RUN")
    share.mapping[104] = [900]
    share.links.append((104, 5, 1))
    result = plan(share, U3, U5)
    [clone] = result.targets[0].clones
    assert (clone.source_assay_id, clone.group_source_assay_ids, clone.action) == (101, [101, 104], "create")
    assert [(x.source_assay_id, x.sample_id) for x in result.units[0].inserts if x.role == "mover"] == [
        (101, 3), (101, 5)]


def test_two_destination_assays_of_a_group_make_its_samples_ambiguous(share):
    share.assays[403] = AssayRow(403, 40, "Imaging Run")
    share.mapping[403] = [901]
    result = plan(share, U4)
    assert skips(result) == [(4, sh.TARGET_ASSAY_AMBIGUOUS)] and result.units == []


def test_a_sample_outside_the_source_project_is_listed(share):
    assert skips(plan(share, U6, U3)) == [(6, sh.NOT_IN_SOURCE_PROJECT)]


def test_a_sample_in_no_source_assay_is_listed(share):
    assert skips(plan(share, U5)) == [(5, sh.NO_SOURCE_ASSAY)]


def test_an_unmapped_source_assay_is_listed(share):
    share.links.append((103, 5, 1))
    assert skips(plan(share, U5)) == [(5, sh.SOURCE_ASSAY_UNMAPPED)]


def test_a_uid_on_no_row_or_on_two_is_listed(share):
    share.samples[99] = {"uuid": U2, "meta": {}}
    result = plan(share, U2, "TIS-260101ZZZ-9")
    assert sorted((s.reason, s.detail) for s in result.skipped) == [
        (sh.SAMPLE_UID_NOT_FOUND, "TIS-260101ZZZ-9"), (sh.SAMPLE_UID_NOT_UNIQUE, U2)]


def test_a_direct_parent_in_the_source_assay_comes_as_an_input_with_the_project(share):
    result = plan(share, U2)
    assert inserts(result) == [(101, 2, 2, "mover"), (101, 1, 1, "parent")]
    assert result.units[0].project_inserts == [ProjectInsert(project_id=5, sample_id=2, role="mover"),
                                               ProjectInsert(project_id=5, sample_id=1, role="parent")]
    assert result.units[0].sync_ids == [1, 2]


def test_a_grandparent_does_not_come(share):
    result = plan(share, U3)
    assert inserts(result) == [(101, 3, 2, "mover"), (101, 2, 1, "parent")]
    assert all(row.sample_id != 1 for row in result.units[0].project_inserts)


def test_a_parent_already_in_the_destination_assay_is_not_inserted(share):
    share.links.append((401, 1, 1))
    result = plan(share, U4)
    assert inserts(result) == [(102, 4, 2, "mover")]
    assert ProjectInsert(project_id=5, sample_id=1, role="parent") in result.units[0].project_inserts


def test_a_sample_already_shared_is_no_change(share):
    share.links += [(401, 4, 2), (401, 1, 1)]
    share.sample_projects[4] |= {5}
    share.sample_projects[1] |= {5}
    result = plan(share, U4)
    assert result.units == [] and result.no_change == {"share:40": [4]} and result.skipped == []


def test_the_plan_removes_nothing_and_writes_no_publication(share):
    result = plan(share, U2, U3, U4)
    assert all(u.removals == [] for u in result.units) and result.publications == []
    assert result.graph.paper_links == []


def test_the_digest_covers_only_planned_samples(share):
    first = plan(share, U3).units[0].digest
    share.samples[9] = {"uuid": uid(9), "meta": {}}
    share.sample_projects[9] = {3}
    share.links.append((101, 9, 1))
    assert plan(share, U3).units[0].digest == first
    share.links.append((101, 2, 1))
    assert plan(share, U3).units[0].digest != first


def test_the_summary_lists_parents_groups_and_outcomes(share):
    result = plan(share, U3, U4, U5, "TIS-260101ZZZ-9")
    s = result.summary
    assert {k: v for k, v in s["outcomes"].items() if v} == {"shared": 2, "sample_uid_not_found": 1,
                                                             "no_source_assay": 1}
    assert s["uids"]["shared"] == sorted([U3, U4]) and s["uids"]["no_source_assay"] == [U5]
    assert [(g["source_assay_ids"], g["action"], g["destination_assay_id"]) for g in s["groups"]] == [
        ([102], "reuse", 401), ([101], "create", None)]
    assert s["links"] == {"mover": 2, "parent": 2} and s["project_rows"] == 4
    assert {(x["uid"], x["child_uid"], x["source_assay_id"]) for x in s["parents"]} == {(U2, U3, 101), (U1, U4, 102)}
    text = report.render_share_text(result)
    assert "create (destination's policy)" in text and "reuse 401" in text
    got = report.share_summary(result, run_dir_name="x-share", plan_sha256="f" * 64)
    assert (got["run_dir"], got["plan_sha256"]) == ("x-share", "f" * 64)


def test_a_rename_pending_on_an_edge_needs_no_approval(share):
    share.stored = [{"child_id": 3, "parent_id": 2,
                     "stored": {**share.labels(3, 2), "internal_assay_title": "Old", "internal_assay_titles": ["Old"]}}]
    result = plan(share, U3)
    assert [c.after_class for c in result.graph.pending] == [labels.RENAMED]
    assert result.summary["label_changes_needing_approval"] == {}


def test_a_rerun_after_apply_is_all_no_change(share):
    first = plan(share, U3, U4)
    apply_to_world(share, first)
    again = plan(share, U3, U4)
    assert again.units == [] and again.no_change == {"share:40": [3, 4]}
    assert [c.action for c in again.targets[0].clones] == []


def test_the_run_directory_files(share, tmp_path):
    result = plan(share, U3, "TIS-260101ZZZ-9")
    written = report.write_share_run(tmp_path, result, result.summary)
    assert sorted(p.name for p in written) == sorted([report.SHARE_FILE, report.PLAN_FILE, report.PLAN_TEXT,
                                                      report.UNMATCHED_JSON, report.UNMATCHED_CSV,
                                                      report.PARENTS_CSV])
    assert "TIS-260101ZZZ-9" in (tmp_path / report.UNMATCHED_CSV).read_text()


def test_an_unreadable_destination_policy_refuses_a_share_that_creates_an_assay(share):
    del share.policies[40]
    with pytest.raises(sh.ShareRefused) as exc:
        plan(share, U3)
    assert exc.value.code == sh.DESTINATION_POLICY_UNREADABLE
    result = plan(share, U4)                              # only a reused group: no policy needed
    assert [c.action for c in result.targets[0].clones] == ["reuse"] and result.summary["clone_policy"] is None


def test_a_parent_outside_the_source_project_is_skipped_and_listed_with_its_projects(share):
    share.sample_projects[1] = {8}                       # parent 1 sits in project 8 only
    result = plan(share, U2, U1)
    assert skips(result) == [(1, sh.NOT_IN_SOURCE_PROJECT)]
    assert inserts(result) == [(101, 2, 2, "mover")] and result.units[0].sync_ids == [2]
    assert [(r.sample_id, r.role) for r in result.units[0].project_inserts] == [(2, "mover")]
    s = result.summary
    assert (s["parents_count"], s["parents"]) == (0, [])
    assert s["parents_outside_source_project_count"] == 1 and s["parents_outside_source_project"] == [
        {"uid": U1, "child_uid": U2, "source_assay_id": 101, "projects": [8]}]
    assert "outside the source project" in report.render_share_text(result)


def test_a_parent_that_is_not_in_the_childs_source_assay_does_not_come(share):
    share.links.remove((101, 1, 1))                      # parent 1 is in assay 102 only
    result = plan(share, U2)
    assert inserts(result) == [(101, 2, 2, "mover")] and result.summary["parents_count"] == 0


def test_the_run_directory_lists_every_parent(share, tmp_path):
    children = []
    for n in range(55):
        add_sample(share, 1000 + n, kind="TIS", assays=((101, 1),))
        children.append(add_sample(share, 2000 + n, parents=(1000 + n,), assays=((101, 2),)))
    result = plan(share, *children)
    assert result.summary["parents_count"] == 55 and len(result.summary["parents"]) == 50
    report.write_share_run(tmp_path, result, result.summary)
    lines = (tmp_path / report.PARENTS_CSV).read_text().splitlines()
    assert len(lines) == 56 and lines[0].startswith("uid,sample_id,child_uid")


class CountingReader(FakeReader):
    def __init__(self, world):
        super().__init__(world)
        self.read_rows: set = set()

    def sample_rows(self, ids):
        self.read_rows |= set(ids)
        return super().sample_rows(ids)

    def assay_rows(self, assay_ids):
        raise AssertionError(f"a share never reads whole assays: {sorted(assay_ids)}")


def test_a_share_reads_only_its_own_samples_and_their_parents(share):
    for n in range(30):                                  # a big source assay
        add_sample(share, 3000 + n, assays=((101, 2),))
    reader = CountingReader(share)
    result = sh.plan_share(inp(U3), reader, run_id="share-1", now="t")
    assert inserts(result) == [(101, 3, 2, "mover"), (101, 2, 1, "parent")]
    assert reader.read_rows <= {2, 3}
    assert result.units[0].digest == plan(share, U3).units[0].digest
