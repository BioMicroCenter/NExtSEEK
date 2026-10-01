"""The planner's targets, samples and clones (tool spec 6.1 to 6.3). The world is conftest's alpha_world."""
import pytest

from nextseek_api.studies import planner as p
from nextseek_api.studies.models import AssociationSet, StudyTarget
from nextseek_api.studies.tests.conftest import AssayRow, FakeReader, StudyRow, uid


def target(ids, *, key="sheet:7:paper one", inv=7, title="Paper One", seek_study_id=None, desc="About paper one",
           doi="10.0000/one", pmid="1111"):
    return StudyTarget(key=key, investigation_id=inv, seek_study_id=seek_study_id, title=title, description=desc,
                       doi=doi, pmid=pmid, sample_ids=ids)


def plan(world, *targets):
    aset = AssociationSet(source="replay", source_ref="test", created_at="t", targets=list(targets))
    return p.plan_study_moves(aset, FakeReader(world), run_id="run-1", now="2026-01-01T00:00:00Z")


def skips(result):
    return sorted((s.sample_id, s.reason) for s in result.skipped)


def test_a_new_target_plans_its_study_and_one_clone_per_source_assay(alpha):
    result = plan(alpha, target([3]))
    [t] = result.targets
    assert t.study.action == "create"
    study = t.study.payload["data"]
    assert study["attributes"]["title"] == "Paper One"
    assert study["attributes"]["description"] == "About paper one"
    assert study["attributes"]["policy"]["access"] == "visible"
    assert study["relationships"]["investigation"]["data"] == {"id": "7", "type": "investigations"}
    [clone] = t.clones
    assert (clone.source_assay_id, clone.action, clone.title, clone.internal_assay_ids, clone.placeholder_id) == (
        101, "create", "RNA-seq run", [900], 302)
    data = clone.payload["data"]
    assert data["relationships"]["study"]["data"]["id"] == p.TARGET_STUDY_REF
    assert data["attributes"]["assay_type"] == {"uri": "http://example.org/assay/1"}
    assert data["attributes"]["assay_class"] == {"key": "EXP"}
    assert "samples" not in data["relationships"] and "data_files" not in data["relationships"]
    assert data["relationships"]["sops"]["data"] == [{"id": "7", "type": "sops"}]
    assert result.seek_next_study_id == 100 and result.buckets == {7: 20, 8: 30}


def test_fill_study_sets_the_target_study_in_a_copy(alpha):
    clone = plan(alpha, target([3])).targets[0].clones[0]
    filled = p.fill_study(clone.payload, 123)
    assert filled["data"]["relationships"]["study"]["data"]["id"] == "123"
    assert clone.payload["data"]["relationships"]["study"]["data"]["id"] == p.TARGET_STUDY_REF


@pytest.mark.parametrize("change, kwargs, reason", [
    (lambda w: w.investigations.update({9: "Gamma Investigation"}), {"inv": 9, "key": "g"}, p.NO_BUCKET),
    (lambda w: w.studies.append(StudyRow(22, 7, "alpha UNPUBLISHED ", None)), {}, p.SEVERAL_BUCKETS),
    (lambda w: None, {"title": "Another Unpublished"}, p.TARGET_IS_BUCKET),
    (lambda w: None, {"seek_study_id": 20, "title": "Alpha Unpublished"}, p.TARGET_IS_BUCKET),
    (lambda w: None, {"title": "Alpha Paper Existing"}, p.STUDY_EXISTS),
    (lambda w: w.studies.append(StudyRow(31, 8, "Beta Paper", None)), {"title": "Beta Paper"},
     p.STUDY_TITLE_IN_OTHER_INVESTIGATION),
    (lambda w: w.studies.append(StudyRow(31, 8, "Beta Paper", None)), {"seek_study_id": 31, "title": "Beta Paper"},
     p.STUDY_NOT_IN_INVESTIGATION),
    (lambda w: None, {"seek_study_id": 404}, p.SEEK_STUDY_NOT_FOUND),
    (lambda w: None, {"title": "x" * 256}, p.TITLE_TOO_LONG),
    (lambda w: setattr(w, "next_study_id", 60), {}, p.SEEK_STUDY_ID_NOT_ABOVE_GRAPH),
])
def test_a_refused_target_skips_every_sample_and_plans_nothing(alpha, change, kwargs, reason):
    change(alpha)
    result = plan(alpha, target([2, 3], **kwargs))
    assert result.targets == [] and result.units == []
    assert skips(result) == [(2, reason), (3, reason)]


def test_an_existing_target_is_not_refused_for_the_graphs_study_ids(alpha):
    alpha.next_study_id = 60
    result = plan(alpha, target([3], seek_study_id=21, title="Alpha Paper Existing"))
    assert result.targets[0].study.action == "existing"


def test_each_sample_reason(alpha):
    alpha.links += [(301, 2, 1), (103, 7, 1)]
    alpha.samples[7] = {"uuid": uid(7), "meta": {}}
    alpha.sample_projects[7] = {3}
    alpha.sample_projects[4] = {9}
    result = plan(alpha, target([2, 3, 4, 5, 7]))
    assert skips(result) == [(2, p.CROSS_INVESTIGATION), (4, p.PROJECT_MISMATCH), (5, p.SAMPLE_IN_NO_ASSAY),
                             (7, p.SOURCE_ASSAY_UNMAPPED)]
    assert [c.source_assay_id for c in result.targets[0].clones] == [101]


def test_a_sample_published_once_is_copied_from_its_paper_assay(alpha):
    alpha.links.append((201, 9, 1))
    alpha.samples[9] = {"uuid": uid(9), "meta": {}}
    alpha.sample_projects[9] = {3}
    [clone] = plan(alpha, target([9])).targets[0].clones
    assert (clone.source_assay_id, clone.action) == (201, "create")


def test_an_existing_target_reuses_its_clone_of_the_same_title_and_mapping(alpha):
    result = plan(alpha, target([3], seek_study_id=21, title="Alpha Paper Existing", desc="New words"))
    [t] = result.targets
    assert (t.study.action, t.study.seek_study_id, t.existing_assay_ids) == ("existing", 21, [201])
    [clone] = t.clones
    assert (clone.action, clone.seek_assay_id, clone.placeholder_id) == ("reuse", 201, None)
    assert [(w.code, w.target_key) for w in result.warnings] == [(p.DESCRIPTION_DIFFERS, t.key)]


def test_two_candidates_in_the_target_are_ambiguous(alpha):
    alpha.assays[202] = AssayRow(202, 21, "rna-seq RUN")
    alpha.mapping[202] = [900]
    result = plan(alpha, target([3], seek_study_id=21, title="Alpha Paper Existing"))
    assert skips(result) == [(3, p.TARGET_ASSAY_AMBIGUOUS)]


def test_two_source_assays_with_one_title_and_mapping_share_one_clone(alpha):
    alpha.assays[104] = AssayRow(104, 20, "rna-seq RUN ")
    alpha.mapping[104] = [900]
    alpha.links.append((104, 3, 2))
    result = plan(alpha, target([3]))
    assert result.skipped == []
    [clone] = result.targets[0].clones
    assert (clone.source_assay_id, clone.group_source_assay_ids, clone.action, clone.title) == (
        101, [101, 104], "create", "RNA-seq run")
    [unit] = result.units
    assert [(x.source_assay_id, x.sample_id, x.role) for x in unit.inserts] == [(101, 3, "mover"), (101, 2, "parent")]
    assert [(r.assay_id, r.sample_id) for r in unit.removals] == [(101, 3), (104, 3)]
    assert unit.source_assay_ids == [101, 104]


def test_same_title_other_mapping_is_two_clones(alpha):
    alpha.assays[104] = AssayRow(104, 20, "RNA-seq run")
    alpha.mapping[104] = [905]
    alpha.links.append((104, 3, 2))
    alpha.assay_reps[104] = alpha.assay_reps[101]
    clones = plan(alpha, target([3])).targets[0].clones
    assert [(c.source_assay_id, c.placeholder_id) for c in clones] == [(101, 302), (104, 303)]


def test_two_targets_naming_one_study_are_both_refused(alpha):
    result = plan(alpha, target([2], key="graph_only:90"), target([3], key="graph_only:91", title=" paper ONE"))
    assert result.targets == [] and result.units == []
    assert skips(result) == [(2, p.TARGET_DOUBLED), (3, p.TARGET_DOUBLED)]
    assert all("graph_only:90" in s.detail and "graph_only:91" in s.detail for s in result.skipped)
    existing = dict(seek_study_id=21, title="Alpha Paper Existing")
    result = plan(alpha, target([2], key="a", **existing), target([3], key="b", **existing))
    assert skips(result) == [(2, p.TARGET_DOUBLED), (3, p.TARGET_DOUBLED)]


def test_two_targets_with_one_key_are_both_refused(alpha):
    result = plan(alpha, target([2]), target([3], title="Paper Two"))
    assert skips(result) == [(2, p.TARGET_DOUBLED), (3, p.TARGET_DOUBLED)]
    assert all("sheet:7:paper one" in s.detail for s in result.skipped)


def test_a_source_with_several_mappings_is_copied_whole_and_warned(alpha):
    alpha.mapping[101] = [900, 905]
    result = plan(alpha, target([3]))
    assert result.targets[0].clones[0].internal_assay_ids == [900, 905]
    assert [(w.code, w.assay_id) for w in result.warnings] == [(p.SOURCE_ASSAY_SEVERAL_MAPPINGS, 101)]


def test_a_clone_payload_seek_would_refuse_refuses_the_target(alpha):
    alpha.assay_reps[101]["data"]["attributes"]["assay_class"] = {}
    assert skips(plan(alpha, target([3]))) == [(3, p.CLONE_PAYLOAD_INVALID)]


def test_a_sample_only_in_the_target_study_is_no_change(alpha):
    alpha.links.append((201, 9, 1))
    alpha.samples[9] = {"uuid": uid(9), "meta": {}}
    alpha.sample_projects[9] = {3}
    result = plan(alpha, target([9], seek_study_id=21, title="Alpha Paper Existing"))
    assert result.no_change == {"sheet:7:paper one": [9]} and result.skipped == []


def test_the_plan_only_reads_seek(alpha):
    reader = FakeReader(alpha)
    p.plan_study_moves(AssociationSet(source="replay", source_ref="t", created_at="t", targets=[target([3])]),
                       reader, run_id="r")
    assert reader.calls == ["GET /assays/101", "GET /studies/20"]


def test_helpers_digest_and_tokens():
    assert p.parent_tokens('{"Parent": "TIS-260101AAA-1; x", "UID": "a"}') == ["TIS-260101AAA-1", "x"]
    assert p.parent_tokens("not json") == [] and p.parent_tokens('["a"]') == []
    one = p.unit_digest([(101, 2, 2), (101, 1, None)], {1: [], 2: ["TIS-260101AAA-1"]})
    two = p.unit_digest([(101, 1, None), (101, 2, 2)], {2: ["TIS-260101AAA-1"], 1: []})
    assert one == two and one != p.unit_digest([(101, 1, 1), (101, 2, 2)], {1: [], 2: ["TIS-260101AAA-1"]})
    assert len(p.code_sha()) == 64


def test_a_membership_in_another_investigation_is_a_share_when_the_sample_shares_its_project(alpha):
    alpha.links.append((301, 2, 1))
    alpha.sample_projects[2] = {3, 4}              # sample 2 was shared into project 4
    result = plan(alpha, target([2, 3]))
    assert skips(result) == []
    movers = {i.sample_id for u in result.units for i in u.inserts if i.role == "mover"}
    assert movers == {2, 3}
    assert all(r.assay_id != 301 for u in result.units for r in u.removals)
    assert [(w.code, w.target_key) for w in result.warnings] == [(p.SHARED_ELSEWHERE, "sheet:7:paper one")]


def test_a_membership_in_another_investigation_without_its_project_is_still_a_misfiling(alpha):
    alpha.links.append((301, 2, 1))                 # sample 2 keeps only project 3
    result = plan(alpha, target([2, 3]))
    assert skips(result) == [(2, p.CROSS_INVESTIGATION)]


def test_a_sample_only_shared_elsewhere_and_in_no_assay_here_is_in_no_assay(alpha):
    alpha.links.append((301, 5, 1))                 # sample 5: in no assay of investigation 7
    alpha.sample_projects[5] = {3, 4}
    assert skips(plan(alpha, target([5]))) == [(5, p.SAMPLE_IN_NO_ASSAY)]


def test_a_clone_payload_with_a_policy_takes_it_and_keeps_every_other_field(alpha):
    rep = alpha.assay_reps[101]
    plain = p.clone_payload(rep)
    policy = {"access": "visible", "permissions": [{"resource": {"id": "5", "type": "projects"}, "access": "view"}]}
    with_policy = p.clone_payload(rep, policy=policy)
    assert with_policy["data"]["attributes"]["policy"] == policy
    with_policy["data"]["attributes"]["policy"] = plain["data"]["attributes"]["policy"]
    assert with_policy == plain
