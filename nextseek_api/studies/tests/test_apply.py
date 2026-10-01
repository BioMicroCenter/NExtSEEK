"""apply_study_moves (tool spec 7.1 to 7.5) over the alpha world, SEEK faked, its links on SQLite."""
import json

import pytest
from seek.models import Assays_internal_assays

from nextseek_api.graph_sync.models_db import GraphSyncOutbox
from nextseek_api.management.commands import backfill_publication_attributes as backfill
from nextseek_api.studies import apply as a
from nextseek_api.studies import links, mapping, preflight
from nextseek_api.studies.journal import JOURNAL_FILE, read_journal
from nextseek_api.studies.seek import SeekError
from nextseek_api.studies.tests.conftest import (AssayRow, journal_events, links_of, outbox_of, seed,
                                                 truncate_journal_after)

MOVED = [(101, 1, 1), (101, 2, 2), (102, 1, 1), (102, 4, 2), (301, 6, 1), (302, 3, 2), (302, 2, 1)]
FULL = [("run", "start"), ("study", "intent"), ("study", "done"), ("clone", "intent"), ("clone", "done"),
        ("map", "intent"), ("map", "done"), ("links", "intent"), ("links", "prepared"), ("links", "committed"),
        ("pubs", "intent"), ("pubs", "done"), ("apply", "done")]


def _outbox():
    return sorted((r.kind, r.key) for r in GraphSyncOutbox.objects.all())


@pytest.mark.django_db
def test_apply_writes_seek_in_journaled_steps(apply_env):
    run_dir, plan = apply_env.make()
    result = apply_env.apply(run_dir)
    assert (result.status, result.exit_code) == (a.DONE, 0)
    assert apply_env.session.posts == [("study", "Paper One"), ("assay", "RNA-seq run")]
    assert apply_env.session.assays == {302: (100, "RNA-seq run")}
    assert list(Assays_internal_assays.objects.values_list("assay_id", "internal_assay_id")) == [(302, 900)]
    assert links_of(apply_env.engine) == MOVED
    assert outbox_of(apply_env.engine) == [("samples", "batch:studies:run-1:1", [2, 3])]
    for sid in (2, 3):
        assert (apply_env.meta.meta(sid)["DOI"], apply_env.meta.meta(sid)["PMID"]) == ("10.0000/one", "1111")
    assert _outbox() == [("assay_map", "*"), ("isa", "*"), ("samples", "batch:studies:run-1:pubs:0")]
    assert journal_events(run_dir) == FULL
    start = read_journal(run_dir / JOURNAL_FILE)[0][0]
    assert (start["login"], start["person_id"]) == ("operator", 42)


@pytest.mark.django_db
def test_a_second_apply_writes_nothing_again(apply_env):
    run_dir, _plan = apply_env.make()
    apply_env.apply(run_dir)
    before = (list(apply_env.session.posts), links_of(apply_env.engine))
    assert apply_env.apply(run_dir).status == a.DONE
    assert (apply_env.session.posts, links_of(apply_env.engine)) == before


@pytest.mark.django_db
def test_a_raised_post_is_adopted_on_the_first_lookup(apply_env):
    run_dir, _plan = apply_env.make()
    apply_env.session.script["study"] = ["late:0"]
    assert apply_env.apply(run_dir).status == a.DONE
    assert apply_env.session.posts.count(("study", "Paper One")) == 1
    assert ("study", "adopted") in journal_events(run_dir)


@pytest.mark.django_db
def test_a_raised_post_is_adopted_on_a_later_lookup_within_the_wait(apply_env, monkeypatch):
    run_dir, _plan = apply_env.make()
    slept = []
    monkeypatch.setattr(a, "_sleep", slept.append)
    apply_env.session.script["study"] = ["late:2"]
    assert apply_env.apply(run_dir).status == a.DONE
    assert apply_env.session.posts.count(("study", "Paper One")) == 1 and slept == [a.ADOPT_POLL_S] * 2


@pytest.mark.django_db
def test_a_raised_post_not_adopted_posts_again(apply_env):
    run_dir, _plan = apply_env.make()
    apply_env.session.script["study"] = ["lost"]
    assert apply_env.apply(run_dir).status == a.DONE
    assert apply_env.session.posts.count(("study", "Paper One")) == 2
    assert [e for e in journal_events(run_dir) if e[0] == "study"] == [
        ("study", "intent"), ("study", "intent"), ("study", "done")]


@pytest.mark.django_db
def test_a_clone_journaled_for_another_source_assay_is_never_adopted(apply_env):
    world = apply_env.world
    world.assays[104] = AssayRow(104, 20, "RNA-seq run")
    world.mapping[104] = [905]
    world.links.append((104, 3, 2))
    world.assay_reps[104] = world.assay_reps[101]
    seed(apply_env.engine, world)
    run_dir, _plan = apply_env.make()
    apply_env.session.script["assay"] = ["ok", "lost"]
    assert apply_env.apply(run_dir).status == a.DONE
    assert apply_env.session.posts.count(("assay", "RNA-seq run")) == 3
    assert sorted(apply_env.session.assays) == [302, 303]


@pytest.mark.django_db
def test_two_matches_stop_the_run(apply_env):
    run_dir, _plan = apply_env.make()
    apply_env.session.studies = {90: (7, "Paper One"), 91: (7, "paper one")}
    apply_env.session.script["study"] = ["lost"]
    result = apply_env.apply(run_dir)
    assert (result.status, result.exit_code) == (a.STOPPED, 1) and "several" in result.message


@pytest.mark.django_db
@pytest.mark.parametrize("error", [SeekError("unauthorized", "401", 401), SeekError("forbidden", "403", 403),
                                   SeekError("rejected", "title is too long", 422),
                                   SeekError("seek_error", "503", 503)])
def test_seek_refusals_stop_the_run(apply_env, error):
    run_dir, _plan = apply_env.make()
    apply_env.session.script["study"] = [error]
    result = apply_env.apply(run_dir)
    assert result.exit_code == 1 and error.message in result.message
    assert links_of(apply_env.engine)[2] == (101, 3, 2)


class Fault(Exception):
    pass


def _once(fn):
    state = {"raised": False}

    def wrapper(*args, **kwargs):
        if not state["raised"]:
            state["raised"] = True
            raise Fault("the process died here")
        return fn(*args, **kwargs)
    return wrapper


@pytest.mark.django_db
@pytest.mark.parametrize("where", ["create_assay", "insert_clone_mappings", "run_link_unit",
                                   "write_publication_attributes"])
def test_apply_resumes_at_every_step_boundary(apply_env, monkeypatch, where):
    run_dir, _plan = apply_env.make()
    owner = {"create_assay": apply_env.session, "insert_clone_mappings": mapping, "run_link_unit": links,
             "write_publication_attributes": backfill}[where]
    monkeypatch.setattr(owner, where, _once(getattr(owner, where)))
    with pytest.raises(Fault):
        apply_env.apply(run_dir)
    assert apply_env.apply(run_dir).status == a.DONE
    assert apply_env.session.posts == [("study", "Paper One"), ("assay", "RNA-seq run")]
    assert Assays_internal_assays.objects.filter(assay_id=302).count() == 1
    assert links_of(apply_env.engine) == MOVED


@pytest.mark.django_db
def test_a_prepared_unit_with_its_outbox_row_is_recovered_as_committed(apply_env):
    run_dir, _plan = apply_env.make()
    apply_env.apply(run_dir)
    truncate_journal_after(run_dir, "links", "prepared")
    assert apply_env.apply(run_dir).status == a.DONE
    lines = read_journal(run_dir / JOURNAL_FILE)[0]
    assert [l for l in lines if l["event"] == "committed"][-1]["recovered"] is True
    assert links_of(apply_env.engine) == MOVED


@pytest.mark.django_db
def test_a_prepared_unit_without_its_outbox_row_rolled_back_and_runs_again(apply_env):
    run_dir, _plan = apply_env.make()
    apply_env.apply(run_dir)
    truncate_journal_after(run_dir, "links", "prepared")
    seed(apply_env.engine, apply_env.world)
    with apply_env.engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM dmac.graph_sync_outbox")
    assert apply_env.apply(run_dir).status == a.DONE
    assert links_of(apply_env.engine) == [(101, 1, 1), (101, 2, 2), (102, 1, 1), (102, 4, 2), (301, 6, 1),
                                          (302, 3, 2), (302, 2, 1)]


def _after_commit_run(apply_env):
    with apply_env.engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO dmac.graph_sync_outbox (kind, key, payload, attempts) "
                             "VALUES ('samples', 'batch:studies:run-1:1', '[]', 0)")
    run_dir, _plan = apply_env.make()
    apply_env.apply(run_dir)
    truncate_journal_after(run_dir, "links", "prepared")
    assert read_journal(run_dir / JOURNAL_FILE)[0][-1]["outbox"] == "after_commit"
    return run_dir


@pytest.mark.django_db
def test_an_after_commit_unit_is_recovered_by_the_state(apply_env):
    run_dir = _after_commit_run(apply_env)
    assert apply_env.apply(run_dir).status == a.DONE
    assert ("samples", "batch:studies:run-1:1") in _outbox()


@pytest.mark.django_db
def test_an_after_commit_unit_whose_digest_still_matches_runs_again(apply_env):
    run_dir = _after_commit_run(apply_env)
    seed(apply_env.engine, apply_env.world)
    assert apply_env.apply(run_dir).status == a.DONE
    assert links_of(apply_env.engine) == MOVED


@pytest.mark.django_db
def test_an_after_commit_unit_in_neither_state_stops_for_the_operator(apply_env):
    run_dir = _after_commit_run(apply_env)
    with apply_env.engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM assay_assets WHERE assay_id = 302 AND asset_id = 2")
    result = apply_env.apply(run_dir)
    assert result.status == a.STOPPED and "neither" in result.message


@pytest.mark.django_db
def test_a_digest_mismatch_stops_before_any_link_write(apply_env):
    run_dir, _plan = apply_env.make()
    with apply_env.engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO assay_assets (assay_id, asset_id, asset_type, direction) "
                             "VALUES (101, 5, 'Sample', 1)")
    result = apply_env.apply(run_dir)
    assert result.status == a.STOPPED and "plan the wave again" in result.message
    assert ("links", "refused") in journal_events(run_dir)
    assert (101, 3, 2) in links_of(apply_env.engine) and not any(r[0] == 302 for r in links_of(apply_env.engine))


@pytest.mark.django_db
def test_a_publication_row_changed_since_stops_the_run(apply_env):
    run_dir, _plan = apply_env.make()
    apply_env.apply(run_dir)
    truncate_journal_after(run_dir, "pubs", "intent")
    apply_env.meta.metadata[3] = json.dumps({"UID": "x", "DOI": "10.0000/other"})
    result = apply_env.apply(run_dir)
    assert result.status == a.STOPPED and "changed since" in result.message


@pytest.mark.django_db
def test_unreadable_metadata_is_skipped_and_reported(apply_env):
    apply_env.world.samples[2]["meta"] = "not json"
    apply_env.meta.metadata[2] = "not json"
    seed(apply_env.engine, apply_env.world)
    run_dir, _plan = apply_env.make()
    assert apply_env.apply(run_dir).status == a.DONE
    done = [l for l in read_journal(run_dir / JOURNAL_FILE)[0] if (l["step"], l["event"]) == ("pubs", "done")][0]
    assert done["unreadable"] == [2] and apply_env.meta.metadata[2] == "not json"
    assert apply_env.meta.meta(3)["DOI"] == "10.0000/one"


@pytest.mark.django_db
def test_preflight_refusals_write_nothing(apply_env, monkeypatch):
    run_dir, _plan = apply_env.make()
    monkeypatch.setattr(preflight, "_switch_follows", lambda: False)
    result = apply_env.apply(run_dir)
    assert (result.status, result.exit_code) == (a.REFUSED, 2)
    assert apply_env.session.posts == [] and not (run_dir / JOURNAL_FILE).exists()
    monkeypatch.setattr(preflight, "_switch_follows", lambda: True)
    monkeypatch.setattr(preflight, "_acting_merge_ids", lambda d, db: [3])
    assert apply_env.apply(run_dir).status == a.REFUSED


@pytest.mark.django_db
def test_a_plan_from_other_code_is_refused(apply_env, monkeypatch):
    run_dir, _plan = apply_env.make()
    monkeypatch.setattr(a, "code_sha", lambda: "0" * 64)
    assert apply_env.apply(run_dir).status == a.REFUSED


@pytest.mark.django_db
def test_a_busy_lock_is_refused(apply_env, monkeypatch):
    from contextlib import contextmanager

    run_dir, _plan = apply_env.make()

    @contextmanager
    def busy():
        yield False

    monkeypatch.setattr(preflight, "run_lock", busy)
    result = apply_env.apply(run_dir)
    assert result.status == a.REFUSED and "lock" in result.message


def test_merge_publications_keeps_what_the_sample_holds_and_appends_in_order():
    raw = json.dumps({"DOI": "10.0000/a; ", "PMID": "1"})
    assert a.merge_publications(raw, ["10.0000/b", "10.0000/A"], ["2", "9"]) == ("10.0000/a; 10.0000/b", "1; 2")
    assert a.merge_publications("not json", ["10.0000/b"], [""]) is None
    assert a.merge_publications(None, ["10.0000/b"], [""]) == ("10.0000/b", "")


@pytest.mark.django_db
def test_the_unit_outbox_check_reads_the_outbox_table():
    GraphSyncOutbox.objects.create(kind="samples", key="batch:studies:r:1", payload=[1])
    assert a._unit_outbox_exists_orm("batch:studies:r:1") and not a._unit_outbox_exists_orm("batch:studies:r:2")
