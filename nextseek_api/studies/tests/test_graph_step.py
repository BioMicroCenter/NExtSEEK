"""The graph step (tool spec 7.6): approval, the live preview against the plan, the paper links, the sync."""
from contextlib import contextmanager

import pytest

from nextseek_api.graph_sync import labels, paper_studies, sources, state, targeted
from nextseek_api.studies import apply as a
from nextseek_api.studies import preflight
from nextseek_api.studies.models import StudyTarget
from nextseek_api.studies.tests.conftest import journal_events, truncate_journal_after


def _edge(child, parent, cls, properties=()):
    return {"child_id": child, "parent_id": parent, "element_id": f"{child}-{parent}", "class": cls,
            "properties": list(properties), "stored": {}, "computed": {}}


PLANNED_LIVE = [_edge(3, 2, labels.CHANGED, ["assay_id"]), _edge(2, 1, labels.EQUAL)]


@pytest.fixture
def graph_env(apply_env, monkeypatch):
    world = apply_env.world
    world.stored = [{"child_id": 3, "parent_id": 2, "stored": world.labels(3, 2)},
                    {"child_id": 2, "parent_id": 1, "stored": world.labels(2, 1)}]
    calls = []
    env = apply_env
    env.calls = calls
    env.live = list(PLANNED_LIVE)
    env.placed = []
    env.sync_status = targeted.OK
    monkeypatch.setattr(targeted, "_refusal", lambda d, db: None)
    monkeypatch.setattr(targeted, "preview_labels", lambda d, db, ids: calls.append(("preview", list(ids))) or env.live)

    def sync(d, db, ids, *, run_dir=None, apply_label_changes=False, **kw):
        calls.append(("sync", list(ids), apply_label_changes, run_dir))
        return {"status": env.sync_status, "labels_written": 1, "labels_changed": 1, "in_study_added": 2,
                "in_study_removed": 1}

    monkeypatch.setattr(targeted, "sync_samples", sync)
    monkeypatch.setattr(paper_studies, "retire_paper_links",
                        lambda d, db, pid, ids, path: calls.append(("retire", pid, list(ids), path.name))
                        or {"paper_links_found": len(ids), "paper_links_retired": len(ids)})
    monkeypatch.setattr(paper_studies, "delete_empty_paper_study_nodes",
                        lambda d, db, paper_ids, *, archive_path: calls.append(("delete_nodes", list(paper_ids)))
                        or {"study_nodes_empty": 1, "study_nodes_deleted": 1})
    monkeypatch.setattr(sources, "seek_study_links_for",
                        lambda ids: [{"sample_id": s, "study_id": 100} for s in ids if s in env.placed])

    @contextmanager
    def lock(timeout):
        yield env.__dict__.get("lock_free", True)

    monkeypatch.setattr(state, "graph_write_lock", lock)
    env.graph = lambda run_dir, **kw: a.graph_step(run_dir, None, "neo4j", **{"approve_label_changes": True, **kw})
    return env


def _applied(env, *targets):
    run_dir, plan = env.make(*targets)
    assert env.apply(run_dir).status == a.DONE
    return run_dir, plan


@pytest.mark.django_db
def test_the_graph_step_writes_what_the_plan_listed(graph_env):
    run_dir, plan = _applied(graph_env)
    assert [(c.child_id, c.parent_id, c.after_class) for c in plan.graph.move] == [(3, 2, labels.CHANGED)]
    result = graph_env.graph(run_dir)
    assert (result.status, result.exit_code) == (a.DONE, 0)
    assert graph_env.calls == [("preview", [2, 3]), ("sync", [2, 3], True, str(run_dir / a.GRAPH_DIR))]
    assert result.counts["labels_changed"] == 1 and result.counts["in_study_removed"] == 1
    assert journal_events(run_dir)[-1] == ("graph", "done")


@pytest.mark.django_db
def test_an_edge_outside_the_plan_refuses_with_nothing_written(graph_env):
    run_dir, _plan = _applied(graph_env)
    graph_env.live = PLANNED_LIVE + [_edge(9, 8, labels.CHANGED, ["assay_id"])]
    result = graph_env.graph(run_dir)
    assert (result.status, result.exit_code) == (a.REFUSED, 2)
    assert [c[0] for c in graph_env.calls] == ["preview"]
    assert (run_dir / a.GRAPH_DIR / a.LABELS_OUTSIDE_FILE).exists()
    assert journal_events(run_dir)[-1] == ("graph", "refused")


@pytest.mark.django_db
def test_an_edge_of_another_class_than_planned_refuses(graph_env):
    run_dir, _plan = _applied(graph_env)
    graph_env.live = [_edge(3, 2, labels.CLEARED, ["assay_id"])]
    assert graph_env.graph(run_dir).status == a.REFUSED


@pytest.mark.django_db
def test_a_planned_edge_that_now_reads_equal_is_fine(graph_env):
    run_dir, _plan = _applied(graph_env)
    graph_env.live = [_edge(3, 2, labels.EQUAL)]
    assert graph_env.graph(run_dir).status == a.DONE


@pytest.mark.django_db
def test_a_rename_or_a_filled_protocol_outside_the_plan_does_not_refuse(graph_env):
    """The loop writes renamed and protocol_filled edges without approval (the studies release), so the step's
    approval does not cover them and they never refuse it."""
    run_dir, _plan = _applied(graph_env)
    graph_env.live = PLANNED_LIVE + [_edge(9, 8, labels.RENAMED, ["internal_assay_title"]),
                                     _edge(7, 6, labels.PROTOCOL_FILLED, ["protocol_id", "protocol_title"])]
    assert graph_env.graph(run_dir).status == a.DONE


@pytest.mark.django_db
def test_the_graph_step_needs_the_approval_and_committed_units(graph_env):
    run_dir, _plan = _applied(graph_env)
    assert a.graph_step(run_dir, None, "neo4j", approve_label_changes=False).status == a.REFUSED
    truncate_journal_after(run_dir, "links", "prepared")
    result = graph_env.graph(run_dir)
    assert result.status == a.REFUSED and "not committed" in result.message


@pytest.mark.django_db
def test_the_graph_step_needs_the_release_and_the_version(graph_env, monkeypatch):
    run_dir, _plan = _applied(graph_env)
    monkeypatch.setattr(preflight, "_switch_follows", lambda: False)
    assert graph_env.graph(run_dir).status == a.REFUSED
    monkeypatch.setattr(preflight, "_switch_follows", lambda: True)
    monkeypatch.setattr(targeted, "_refusal", lambda d, db: {"status": "not_at_version", "schema_version": "1.1",
                                                            "writer_version": "1.2"})
    assert graph_env.graph(run_dir).status == a.REFUSED
    assert graph_env.calls == []


@pytest.mark.django_db
def test_a_graph_only_paper_retires_the_links_mysql_moved_then_syncs(graph_env):
    run_dir, plan = _applied(graph_env, StudyTarget(key="graph_only:90", investigation_id=7, title="Paper One",
                                                    doi="10.0000/one", sample_ids=[3]))
    assert [(x.paper_id, x.sample_ids) for x in plan.graph.paper_links] == [(90, [3])]
    graph_env.placed = [3]
    assert graph_env.graph(run_dir).status == a.DONE
    assert [c[0] for c in graph_env.calls] == ["preview", "retire", "delete_nodes", "sync"]
    assert graph_env.calls[1] == ("retire", 90, [3], "in_study_removed.tsv")


@pytest.mark.django_db
def test_a_sample_mysql_does_not_place_in_the_papers_study_keeps_its_paper_link(graph_env):
    run_dir, _plan = _applied(graph_env, StudyTarget(key="graph_only:90", investigation_id=7, title="Paper One",
                                                     doi="10.0000/one", sample_ids=[3]))
    graph_env.placed = []
    graph_env.graph(run_dir)
    assert graph_env.calls[1] == ("retire", 90, [], "in_study_removed.tsv")


@pytest.mark.django_db
def test_a_busy_graph_lock_or_a_refused_sync_stops_the_step(graph_env):
    run_dir, _plan = _applied(graph_env, StudyTarget(key="graph_only:90", investigation_id=7, title="Paper One",
                                                     doi="10.0000/one", sample_ids=[3]))
    graph_env.lock_free = False
    assert graph_env.graph(run_dir).status == a.STOPPED
    graph_env.lock_free = True
    graph_env.sync_status = targeted.LOCK_TIMEOUT
    assert graph_env.graph(run_dir).exit_code == 1


@pytest.mark.django_db
def test_the_sync_goes_in_calls_of_at_most_sync_call_ids(graph_env, monkeypatch):
    run_dir, _plan = _applied(graph_env)
    monkeypatch.setattr(a, "SYNC_CALL_IDS", 1)
    graph_env.graph(run_dir)
    assert [c[1] for c in graph_env.calls if c[0] == "sync"] == [[2], [3]]


def test_labels_outside_plan_compares_class_and_properties():
    from nextseek_api.studies.models import LabelChange

    planned = [LabelChange(child_id=3, parent_id=2, before_class="equal", after_class="changed",
                           properties=["assay_id"], stored={}, after={})]
    assert a.labels_outside_plan([_edge(3, 2, labels.CHANGED, ["assay_id"])], planned) == []
    assert a.labels_outside_plan([_edge(3, 2, labels.CHANGED, ["assay_id", "internal_assay_id"])], planned)
    assert a.labels_outside_plan([_edge(5, 4, labels.NEW)], planned) == []
    assert a.labels_outside_plan([_edge(5, 4, labels.RENAMED, ["internal_assay_title"])], planned) == []
    assert a.labels_outside_plan([_edge(5, 4, labels.CHANGED, ["assay_id"])], planned)
