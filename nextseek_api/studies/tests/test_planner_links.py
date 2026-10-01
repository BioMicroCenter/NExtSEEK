"""The planner's links (tool spec 6.4): movers, parents added, the stays fixpoint, units, digests, no_change."""
from nextseek_api.studies import planner as p
from nextseek_api.studies.models import AssociationSet, StudyTarget
from nextseek_api.studies.tests.conftest import FakeReader, add_sample, apply_to_world, uid


def target(ids, *, key="sheet:7:paper one", title="Paper One", seek_study_id=None):
    return StudyTarget(key=key, investigation_id=7, seek_study_id=seek_study_id, title=title, doi="10.0000/one",
                       sample_ids=ids)


def plan(world, *targets):
    return p.plan_study_moves(AssociationSet(source="replay", source_ref="t", created_at="t", targets=list(targets)),
                              FakeReader(world), run_id="run-1", now="t")


def moves(unit):
    return ([(x.sample_id, x.direction, x.role) for x in unit.inserts],
            [(r.assay_id, r.sample_id) for r in unit.removals])


def test_a_leaf_moves_its_parent_is_added_and_stays(alpha):
    [unit] = plan(alpha, target([3])).units
    assert moves(unit) == ([(3, 2, "mover"), (2, 1, "parent")], [(101, 3)])
    assert (unit.unit, unit.source_assay_ids, unit.sync_ids) == (1, [101], [2, 3])
    expected = p.unit_digest([(101, 1, 1), (101, 2, 2), (101, 3, 2)],
                             {1: [], 2: [uid(1)], 3: [uid(2, kind="D.SEQ")]})
    assert unit.digest == expected


def test_a_mover_with_a_staying_child_stays_in_its_bucket_assay(alpha):
    result = plan(alpha, target([2]))
    [unit] = result.units
    assert moves(unit) == ([(2, 2, "mover"), (1, 1, "parent")], [])
    assert unit.sync_ids == [1, 2, 3]
    assert result.no_change == {}


def test_a_diamond(alpha):
    add_sample(alpha, 11, parents=(1,))
    add_sample(alpha, 12, parents=(2, 11))
    [unit] = plan(alpha, target([2, 3, 11, 12])).units
    assert sorted(moves(unit)[1]) == [(101, 2), (101, 3), (101, 11), (101, 12)]
    [unit] = plan(alpha, target([2])).units
    assert moves(unit)[1] == []


def test_a_cycle_ends(alpha):
    add_sample(alpha, 13, kind="IMG")
    add_sample(alpha, 14, parents=(13,), kind="IMG")
    alpha.samples[13]["meta"]["Parent"] = alpha.samples[14]["uuid"]
    assert moves(plan(alpha, target([13])).units[0])[1] == []
    assert sorted(moves(plan(alpha, target([13, 14])).units[0])[1]) == [(101, 13), (101, 14)]


def test_a_hub_parent_goes_into_each_clone_and_never_leaves(alpha):
    result = plan(alpha, target([2], key="sheet:7:paper a", title="Paper A"),
                  target([4], key="sheet:7:paper b", title="Paper B"))
    a, b = result.units
    assert (1, 1, "parent") in moves(a)[0] and (1, 1, "parent") in moves(b)[0]
    assert all(r.sample_id != 1 for u in result.units for r in u.removals)
    assert moves(b)[1] == [(102, 4)]


def test_a_sample_skipped_for_itself_is_still_added_as_a_parent(alpha):
    alpha.links.append((301, 2, 1))
    result = plan(alpha, target([2, 3]))
    assert [(s.sample_id, s.reason) for s in result.skipped] == [(2, p.CROSS_INVESTIGATION)]
    assert moves(result.units[0]) == ([(3, 2, "mover"), (2, 1, "parent")], [(101, 3)])


def test_a_parent_sharing_no_project_skips_its_child(alpha):
    alpha.sample_projects[2] = {9}
    result = plan(alpha, target([3]))
    assert [(s.sample_id, s.reason) for s in result.skipped] == [(3, p.PARENT_PROJECT_MISMATCH)]
    assert result.units == [] and result.targets[0].clones == []


def test_a_sample_in_two_targets_leaves_its_bucket_assay_in_the_last_unit(alpha):
    result = plan(alpha, target([3], key="sheet:7:paper a", title="Paper A"),
                  target([3], key="sheet:7:paper b", title="Paper B"))
    a, b = result.units
    assert (3, 2, "mover") in moves(a)[0] and (3, 2, "mover") in moves(b)[0]
    assert moves(a)[1] == [] and moves(b)[1] == [(101, 3)]


def test_a_copy_source_is_never_emptied(alpha):
    add_sample(alpha, 9, assays=((201, 1),), kind="TIS")
    result = plan(alpha, target([9]))
    assert moves(result.units[0]) == ([(9, 1, "mover")], [])
    assert result.empty_bucket_assays == []


def test_each_digest_is_taken_after_the_earlier_units(alpha):
    result = plan(alpha, target([3], key="sheet:7:paper a", title="Paper A"),
                  target([2], key="sheet:7:paper b", title="Paper B"))
    a, b = result.units
    assert moves(a)[1] == [(101, 3)] and moves(b)[1] == [(101, 2)]
    assert b.digest == p.unit_digest([(101, 1, 1), (101, 2, 2)], {1: [], 2: [uid(1)]})


def test_a_bucket_assay_left_with_no_member_is_listed(alpha):
    result = plan(alpha, target([1, 2, 3]))
    assert result.empty_bucket_assays == [101]


def test_a_replan_after_a_complete_run_is_all_no_change(alpha):
    first = plan(alpha, target([2, 3]))
    apply_to_world(alpha, first)
    again = plan(alpha, target([2, 3], seek_study_id=100))
    assert again.units == [] and again.skipped == []
    assert again.no_change == {"sheet:7:paper one": [2, 3]}


def test_a_mover_the_fixpoint_keeps_is_no_change_on_the_replan(alpha):
    first = plan(alpha, target([2]))
    apply_to_world(alpha, first)
    again = plan(alpha, target([2], seek_study_id=100))
    assert again.units == [] and again.no_change == {"sheet:7:paper one": [2]}
    assert again.targets[0].clones[0].action == "reuse"
