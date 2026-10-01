"""The planner's publications and graph plan (tool spec 6.5, 6.6)."""
import pytest

from nextseek_api.graph_sync import labels
from nextseek_api.studies import planner as p
from nextseek_api.studies import report
from nextseek_api.studies.models import AssociationSet, StudyTarget
from nextseek_api.studies.tests.conftest import LABEL_KEYS, FakeReader, apply_to_world


def target(ids, *, key="sheet:7:paper one", title="Paper One", doi="10.0000/one", pmid="1111", seek_study_id=None):
    return StudyTarget(key=key, investigation_id=7, title=title, doi=doi, pmid=pmid, sample_ids=ids,
                       seek_study_id=seek_study_id)


def plan(world, *targets, now="t"):
    return p.plan_study_moves(AssociationSet(source="replay", source_ref="t", created_at="t", targets=list(targets)),
                              FakeReader(world), run_id="run-1", now=now)


def stored(world, child, parent, **override):
    values = world.labels(child, parent)
    values.update(override)
    return {"child_id": child, "parent_id": parent, "stored": values}


def test_one_publication_row_per_sample_across_targets_in_unit_order(alpha):
    result = plan(alpha, target([3], key="sheet:7:paper a", title="Paper A", doi="10.0000/a", pmid="1"),
                  target([3], key="sheet:7:paper b", title="Paper B", doi="10.0000/b", pmid=None))
    rows = {r.sample_id: (r.dois, r.pmids, r.investigation_id) for r in result.publications}
    assert rows == {2: (["10.0000/a", "10.0000/b"], ["1", ""], 7), 3: (["10.0000/a", "10.0000/b"], ["1", ""], 7)}


def test_one_doi_twice_is_one_entry_values_not_text(alpha):
    result = plan(alpha, target([3], key="sheet:7:paper a", title="Paper A", doi="10.0000/A"),
                  target([3], key="sheet:7:paper b", title="Paper B", doi=" 10.0000/a "))
    assert {r.sample_id: r.dois for r in result.publications}[3] == ["10.0000/A"]


def test_a_pmid_without_a_doi_writes_nothing_and_warns(alpha):
    result = plan(alpha, target([3], doi=None, pmid="1111"))
    assert result.publications == []
    assert [w.code for w in result.warnings] == [p.PMID_WITHOUT_DOI]


def test_the_moves_own_label_changes_are_listed_and_an_equal_edge_is_not(alpha):
    alpha.stored = [stored(alpha, 3, 2), stored(alpha, 2, 1)]
    result = plan(alpha, target([3]))
    [change] = result.graph.move
    assert (change.child_id, change.parent_id, change.before_class, change.after_class, change.properties) == (
        3, 2, labels.EQUAL, labels.CHANGED, ["assay_id"])
    assert change.after["assay_id"] == 302 and change.after["internal_assay_id"] == 900
    assert result.graph.pending == []
    assert result.summary["label_changes"] == {"move": {"changed": 1}, "pending": {}}


def test_a_difference_pending_today_is_listed_apart(alpha):
    alpha.stored = [stored(alpha, 3, 2), stored(alpha, 2, 1, internal_assay_ids=None, internal_assay_titles=None)]
    [change] = plan(alpha, target([3])).graph.pending
    assert (change.child_id, change.before_class, change.after_class) == (2, labels.PLURAL_MISSING,
                                                                          labels.PLURAL_MISSING)


def test_a_rename_pending_today_is_listed_for_information_only(alpha):
    alpha.stored = [stored(alpha, 3, 2), stored(alpha, 2, 1, internal_assay_title="Old RNA-seq",
                                                internal_assay_titles=["Old RNA-seq"])]
    result = plan(alpha, target([3]))
    [change] = result.graph.pending
    assert (change.child_id, change.parent_id, change.before_class, change.after_class) == (
        2, 1, labels.RENAMED, labels.RENAMED)
    assert "written by the loop without approval" in report.render_plan_text(result)


def test_a_label_the_move_would_clear_refuses_the_plan(alpha):
    equal = alpha.labels(1, 2)
    edges = [{"child_id": 1, "parent_id": 2, "element_id": "e", "stored": equal}]
    amap = alpha.assay_map()
    with pytest.raises(p.PlannerDefect):
        p._label_changes(edges, {1: {101}, 2: {101}}, {1: set(), 2: {101}}, amap, {}, {})
    old = {k: equal[k] for k in LABEL_KEYS}
    edges = [{"child_id": 1, "parent_id": 2, "element_id": "e", "stored": old}]
    move, pending = p._label_changes(edges, {1: set(), 2: {101}}, {1: set(), 2: {101}}, amap, {}, {})
    assert move == [] and [c.after_class for c in pending] == [labels.CLEARED]


def test_a_graph_only_target_lists_its_paper_links(alpha):
    result = plan(alpha, target([3, 5], key="graph_only:90"))
    [links] = result.graph.paper_links
    assert (links.paper_id, links.target_key, links.sample_ids) == (90, "graph_only:90", [3])
    assert result.targets[0].paper_id == 90


def test_the_summary_counts(alpha):
    result = plan(alpha, target([3, 5]))
    s = result.summary
    assert (s["targets"], s["studies_to_create"], s["clones_to_create"], s["units"], s["inserts"],
            s["removals"], s["publication_rows"]) == (1, 1, 1, 1, 2, 1, 2)
    assert s["skipped_by_reason"] == {p.SAMPLE_IN_NO_ASSAY: 1}
    assert s["per_investigation"] == {"7": {"targets": 1, "units": 1, "inserts": 2, "removals": 1, "skipped": 1}}


def test_a_replan_lists_what_is_pending_for_its_no_change_samples(alpha):
    alpha.stored = [stored(alpha, 3, 2)]
    first = plan(alpha, target([2, 3]))
    apply_to_world(alpha, first)
    again = plan(alpha, target([2, 3], seek_study_id=100))
    assert again.units == [] and again.graph.no_change_sync_ids == {"sheet:7:paper one": [2, 3]}
    [change] = again.graph.pending
    assert (change.child_id, change.parent_id, change.after_class, change.properties) == (
        3, 2, labels.CHANGED, ["assay_id"])


def test_the_same_snapshot_gives_the_same_plan(alpha):
    alpha.stored = [stored(alpha, 3, 2)]
    assert plan(alpha, target([2, 3]), now="x").to_json() == plan(alpha, target([2, 3]), now="x").to_json()
