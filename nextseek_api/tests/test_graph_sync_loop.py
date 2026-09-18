"""The sync loop (nextseek_api/graph_sync/loop.py; the sync design, sections 12 and 13).

No MySQL beyond the two dmac tables in the SQLite test database, no Neo4j and no child process: every function one
pass can call is replaced by a recorder and the child launcher is injected, so each test says what the pass did, in
what order and with which arguments. The outbox rows are real, because what the loop does to a row it could not
finish is most of what these tests are about.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

import pytest

from nextseek_api.graph_sync import drift, loop, run, state, targeted, writer
from nextseek_api.graph_sync.models_db import GraphSyncOutbox, GraphSyncRun

DB = "neo4j"
DRIVER = object()

# A Tuesday at 04:00 UTC: past that day's reconcile boundary (02:00) and drift boundary (02:30), and past the
# weekly full sync's, which was Sunday 2026-09-13 at 03:00, in ISO week 2026-W37.
T0 = datetime(2026, 9, 15, 4, 0, tzinfo=dt_timezone.utc)
TODAY, THIS_WEEK = "slot:2026-09-15", "slot:2026-W37"


def before(**delta) -> datetime:
    return T0 - timedelta(**delta)


def row(kind: str, key: str) -> GraphSyncOutbox:
    return GraphSyncOutbox.objects.get(kind=kind, key=key)


@pytest.fixture
def work(monkeypatch, tmp_path):
    """Every function one pass drives, recorded in ``calls``; the launcher records its children and answers from
    ``exits`` (0 once it runs out).

    A test sets what a step returns, or raises, through the field named after it, and reads the arguments it was
    called with from its recorded call.
    """
    rec = SimpleNamespace(calls=[], launched=[], exits=[], version=writer.SCHEMA_VERSION,
                          sync=None, of_type=None, retire=None, catalog=None, relabel=None, small=None,
                          opts=loop.Options(run_root=str(tmp_path)))

    def step(name, default):
        def call(*args, **kwargs):
            rec.calls.append(SimpleNamespace(name=name, args=args, kwargs=kwargs))
            answer = getattr(rec, name)
            if isinstance(answer, BaseException):
                raise answer
            return default if answer is None else answer
        return call

    monkeypatch.setattr(targeted, "sync_samples", step("sync", {"status": targeted.OK}))
    monkeypatch.setattr(targeted, "sync_samples_of_type", step("of_type", {"status": targeted.OK}))
    monkeypatch.setattr(targeted, "retire_samples", step("retire", {"status": targeted.OK}))
    monkeypatch.setattr(targeted, "relabel_for_maps", step("relabel", {"status": targeted.OK}))
    monkeypatch.setattr(targeted, "sync_small_tables", step("small", {"status": targeted.OK}))
    monkeypatch.setattr(run, "catalog_sync", step("catalog", {"mode": "catalog", "status": "ok"}))
    monkeypatch.setattr(writer, "graphmeta", lambda driver, db: {"schema_version": rec.version})

    def launch(argv, timeout_s):
        rec.launched.append(SimpleNamespace(argv=list(argv), timeout_s=timeout_s))
        return rec.exits.pop(0) if rec.exits else 0

    rec.launch = launch
    return rec


def one_pass(work, *, now=T0, worker_id="w1", launch=None) -> dict:
    return loop.run_pass(DRIVER, DB, worker_id, opts=work.opts, now=now, launch=launch or work.launch)


def names(work) -> list[str]:
    return [c.name for c in work.calls]


def modes(work) -> list[str]:
    """The mode flag of each child the pass started, in order."""
    return [c.argv[3] for c in work.launched]


# --- the schedule --------------------------------------------------------------------------------

@pytest.mark.django_db
def test_a_pass_enqueues_the_due_slots_oldest_boundary_first(work):
    report = one_pass(work)

    slots = GraphSyncOutbox.objects.filter(kind__in=state.SLOT_KINDS).order_by("enqueued_at", "id")
    assert [(r.kind, r.key) for r in slots] == [("full", THIS_WEEK), ("reconcile", TODAY), ("drift", TODAY)]
    assert report["slots_enqueued"] == [{"kind": "full", "key": THIS_WEEK},
                                        {"kind": "reconcile", "key": TODAY},
                                        {"kind": "drift", "key": TODAY}]
    assert modes(work) == ["--full", "--reconcile", "--drift"]


@pytest.mark.django_db
def test_a_slot_a_successful_run_already_covered_is_not_enqueued(work):
    GraphSyncRun.objects.create(kind="reconcile", started_at=before(hours=1), finished_at=before(minutes=50),
                                status="ok", counts_json={"trigger": "loop"})
    one_pass(work)

    assert not GraphSyncOutbox.objects.filter(kind="reconcile").exists()
    assert modes(work) == ["--full", "--drift"]


@pytest.mark.django_db
def test_a_drift_check_that_found_drift_still_satisfies_its_slot(work):
    """``drift`` is the status of a check that ran and reported; only a failure leaves the slot owed."""
    GraphSyncRun.objects.create(kind="drift", started_at=before(hours=1), finished_at=before(minutes=50),
                                status="drift", counts_json={"trigger": "loop"})
    one_pass(work)

    assert not GraphSyncOutbox.objects.filter(kind="drift").exists()


@pytest.mark.django_db
def test_a_slot_already_in_the_outbox_is_left_exactly_as_it_is(work):
    state.enqueue("reconcile", TODAY, now=before(minutes=90))
    claim = state.claim_next("other", now=before(minutes=30))
    state.finish_failed(claim, "boom", 3600, now=before(minutes=30))     # its back-off still runs at T0
    one_pass(work)

    r = row("reconcile", TODAY)
    assert r.attempts == 1 and r.last_error == "boom"          # its back-off is not reset by the schedule
    assert "--reconcile" not in modes(work)                    # and it is not claimable while the back-off runs


# --- the heavy kinds, as child processes ----------------------------------------------------------

@pytest.mark.django_db
def test_a_heavy_kind_runs_as_a_child_with_its_run_directory_and_the_live_flag(work):
    state.enqueue("full", THIS_WEEK, now=before(minutes=1))

    one_pass(work)

    (child,) = [c for c in work.launched if "--full" in c.argv]
    assert child.argv == [sys.executable, str(loop.MANAGE_PY), "graph_sync", "--full",
                          "--run-dir", os.path.join(str(work.opts.run_root), f"full-{T0:%Y%m%dT%H%M%SZ}"),
                          "--trigger", loop.TRIGGER, "--i-mean-the-live-graph"]
    assert child.timeout_s == loop.child_timeout_s("full")
    assert child.timeout_s < state.lease_s("full")             # the row is not claimable while the child runs
    assert names(work) == []                                   # nothing heavy ran in the loop's own process
    assert row("full", THIS_WEEK).done_at is not None


def test_the_child_command_line_points_at_this_checkouts_manage_py():
    """A child is ``<this interpreter> <repo>/manage.py graph_sync ...``, so moving this module up or down a
    directory would silently start launching children that cannot run."""
    assert loop.MANAGE_PY.name == "manage.py" and loop.MANAGE_PY.is_file()


@pytest.mark.django_db
def test_a_child_that_records_no_run_is_told_so(work):
    work.opts = replace(work.opts, record=False)
    one_pass(work)

    assert all("--no-record" in c.argv for c in work.launched)


@pytest.mark.django_db
@pytest.mark.parametrize("code, outcome, done", [(0, loop.DONE, True), (2, loop.DONE, True),
                                                 (1, loop.FAILED, False), (None, loop.FAILED, False)])
def test_a_childs_exit_status_decides_its_row(work, code, outcome, done):
    state.enqueue("drift", TODAY, now=before(minutes=1))       # the oldest row, so it takes the first exit
    work.exits = [code]

    report = one_pass(work)

    (entry,) = [d for d in report["drained"] if d["kind"] == "drift"]
    assert (entry["outcome"], entry["exit"], entry["refused"]) == (outcome, code, code == 2)
    r = row("drift", TODAY)
    assert (r.done_at is not None) is done
    if not done:
        assert r.attempts == 1 and r.claimed_by is None
        assert r.lease_expires_at == T0 + timedelta(seconds=state.backoff_s("drift"))
        assert r.last_error and "graph_sync --drift" in r.last_error


def reporting(work, kind: str, saved, code: int = 1):
    """A launcher whose ``--<kind>`` child saves ``saved`` as the drift result in its run directory, as
    ``manage.py graph_sync --drift --run-dir`` does before it exits, and answers ``code``; every other child exits 0.
    ``saved`` None writes no file; a string is written as it is."""
    def launch(argv, timeout_s):
        work.launched.append(SimpleNamespace(argv=list(argv), timeout_s=timeout_s))
        if f"--{kind}" not in argv:
            return 0
        if saved is not None:
            run_dir = argv[argv.index("--run-dir") + 1]
            os.makedirs(run_dir, exist_ok=True)
            with open(os.path.join(run_dir, drift.RESULT_FILE), "w", encoding="utf-8") as fh:
                fh.write(saved if isinstance(saved, str) else json.dumps(saved))
        return code
    return launch


FOUND_DRIFT = {"status": drift.DRIFT, "pass": False, "checks": [
    {"name": "catalog.assistant_investigations", "expected": 0, "actual": 1, "pass": False},
    {"name": "samples.not_in_mysql", "expected": 0, "actual": 0, "pass": True}]}


@pytest.mark.django_db
def test_a_drift_check_that_found_drift_closes_its_row_and_leaves_the_outbox_fresh(work):
    """Exit 1 from ``--drift`` is the check reporting drift, not failing (the design, section 13): it reports and does
    not repair, so a retry finds the same drift. Failing it kept the slot open, backed off an hour at a time until it
    died, and aged the outbox past its one-hour threshold every day the graph had drifted at all."""
    state.enqueue("drift", TODAY, now=before(minutes=1))

    report = one_pass(work, launch=reporting(work, "drift", FOUND_DRIFT))

    (entry,) = [d for d in report["drained"] if d["kind"] == "drift"]
    assert (entry["outcome"], entry["exit"], entry["refused"]) == (loop.DONE, 1, False)
    assert entry["drift"] == ["catalog.assistant_investigations"]
    r = row("drift", TODAY)
    assert r.done_at is not None and r.claimed_by is None and r.last_error is None
    later = T0 + timedelta(hours=2)
    assert state.outbox_summary(now=later)["oldest_pending"] is None
    assert state.freshness(now=later)["outbox"]["status"] == "ok"


@pytest.mark.django_db
@pytest.mark.parametrize("kind, key, saved", [
    ("drift", TODAY, None),                                        # exit 1 before the check saved anything
    ("drift", TODAY, "not json"),
    ("drift", TODAY, {"status": drift.OK, "pass": True, "checks": []}),
    ("drift", TODAY, {"status": drift.REFUSED, "reason": "1.1", "checks": []}),
    ("reconcile", TODAY, FOUND_DRIFT),                             # only a drift check reports drift
])
def test_an_exit_1_that_is_not_a_drift_report_still_backs_off(work, kind, key, saved):
    state.enqueue(kind, key, now=before(minutes=1))

    report = one_pass(work, launch=reporting(work, kind, saved))

    (entry,) = [d for d in report["drained"] if d["kind"] == kind]
    assert entry["outcome"] == loop.FAILED
    r = row(kind, key)
    assert r.done_at is None and r.attempts == 1
    assert r.lease_expires_at == T0 + timedelta(seconds=state.backoff_s(kind))


# --- the in-process kinds -------------------------------------------------------------------------

@pytest.mark.django_db
def test_every_in_process_kind_calls_its_own_function(work):
    items = [("samples", "sample:7", None), ("samples_of_type", "type:26", None), ("retire", "sample:9", None),
             ("catalog", "*", None), ("assay_map", "*", None), ("protocol_map", "*", None),
             ("isa", "*", None), ("membership", "*", None)]
    for n, (kind, key, payload) in enumerate(items):
        state.enqueue(kind, key, payload, now=before(minutes=30 - n))

    one_pass(work)

    assert names(work) == ["sync", "of_type", "retire", "catalog", "relabel", "relabel", "small", "small"]
    assert [c.args for c in work.calls if c.name == "sync"] == [(DRIVER, DB, [7])]
    assert [c.args for c in work.calls if c.name == "of_type"] == [(DRIVER, DB, 26)]
    assert [c.args for c in work.calls if c.name == "retire"] == [(DRIVER, DB, [9])]
    assert all(r.done_at is not None for r in GraphSyncOutbox.objects.all())


@pytest.mark.django_db
def test_a_batch_row_syncs_the_sample_ids_of_its_payload(work):
    state.enqueue("samples", "batch:9:1", [11, 12, 13], now=before(minutes=1))

    one_pass(work)

    (sync,) = [c for c in work.calls if c.name == "sync"]
    assert sync.args == (DRIVER, DB, [11, 12, 13])
    assert sync.kwargs["run_dir"] == os.path.join(str(work.opts.run_root), f"drain-{T0:%Y%m%dT%H%M%SZ}")
    assert row("samples", "batch:9:1").done_at is not None


# --- what the loop does with work it could not finish ---------------------------------------------

@pytest.mark.django_db
def test_a_lock_timeout_leaves_the_row_pending_without_counting_an_attempt(work):
    """Another graph_sync write holding the lock is not the row's fault: counting it would kill the row after
    ``state.MAX_ATTEMPTS`` of them, with its work never done."""
    state.enqueue("samples", "sample:7", now=before(minutes=1))
    work.sync = {"status": targeted.LOCK_TIMEOUT, "lock_timeout_s": 60}

    report = one_pass(work)

    r = row("samples", "sample:7")
    assert r.done_at is None and r.attempts == 0 and r.claimed_by is None
    assert r.lease_expires_at == T0 + timedelta(seconds=loop.DEFER_BACKOFF_S)
    assert r.last_error and targeted.LOCK_TIMEOUT in r.last_error
    (entry,) = [d for d in report["drained"] if d["kind"] == "samples"]
    assert entry["outcome"] == loop.DEFERRED


@pytest.mark.django_db
def test_a_row_survives_more_lock_timeouts_than_the_attempt_limit(work):
    state.enqueue("samples", "sample:7", now=before(minutes=1))
    work.sync = {"status": targeted.LOCK_TIMEOUT}
    passes = state.MAX_ATTEMPTS + 2

    for n in range(passes):
        one_pass(work, now=T0 + timedelta(minutes=5 * n))

    r = row("samples", "sample:7")
    assert r.done_at is None and r.attempts == 0
    assert len([c for c in work.calls if c.name == "sync"]) == passes


@pytest.mark.django_db
def test_a_graph_below_the_writers_version_drains_only_the_read_only_drift_check(work):
    """The loop writes nothing to a graph the operator has not yet run the first 1.2 full sync on, and never turns
    one into 1.2 by itself; the rows wait, unclaimed, with their attempts untouched."""
    work.version = "1.1"
    state.enqueue("samples", "sample:7", now=before(minutes=2))
    state.enqueue("full", THIS_WEEK, now=before(minutes=1))

    report = one_pass(work)

    assert names(work) == [] and modes(work) == ["--drift"]
    assert (report["graph_schema_version"], report["at_writer_version"]) == ("1.1", False)
    for kind, key in [("samples", "sample:7"), ("full", THIS_WEEK)]:
        r = row(kind, key)
        assert (r.done_at, r.attempts, r.claimed_by, r.lease_expires_at) == (None, 0, None, None)


@pytest.mark.django_db
def test_a_step_that_raises_backs_its_row_off_and_the_pass_goes_on(work):
    state.enqueue("samples", "sample:7", now=before(minutes=2))
    state.enqueue("catalog", "*", now=before(minutes=1))
    work.sync = RuntimeError("neo4j went away")

    report = one_pass(work)

    r = row("samples", "sample:7")
    assert r.done_at is None and r.attempts == 1
    assert r.lease_expires_at == T0 + timedelta(seconds=state.backoff_s("samples"))
    assert "neo4j went away" in r.last_error
    assert row("catalog", "*").done_at is not None
    assert report["counts"][loop.FAILED] == 1 and report["counts"][loop.DONE] == 4


@pytest.mark.django_db
def test_a_refused_catalog_sync_is_deferred_when_the_lock_or_the_version_refused_it(work):
    state.enqueue("catalog", "*", now=before(minutes=1))
    work.catalog = run.PreflightError(["the graph-write lock was not acquired within 60 s: another graph_sync "
                                       "write holds it"], {"problems": ["lock"]})

    one_pass(work)

    r = row("catalog", "*")
    assert r.done_at is None and r.attempts == 0
    assert r.lease_expires_at == T0 + timedelta(seconds=loop.DEFER_BACKOFF_S)


@pytest.mark.django_db
def test_a_catalog_sync_refused_for_any_other_reason_backs_off(work):
    state.enqueue("catalog", "*", now=before(minutes=1))
    work.catalog = run.PreflightError(["SampleType titles collide"], {"problems": ["collide"]})

    one_pass(work)

    r = row("catalog", "*")
    assert r.done_at is None and r.attempts == 1
    assert r.lease_expires_at == T0 + timedelta(seconds=state.backoff_s("catalog"))


# --- the two runs that meet in the outbox ---------------------------------------------------------

@pytest.mark.django_db
def test_a_full_syncs_outbox_closure_leaves_the_drift_slot_pending(work):
    """A successful full sync marks done every row enqueued before it started, because it read what they ask for.

    The drift slot is the one kind it must not close (``run.FULL_SYNC_COVERS``): that check asks about the graph the
    sync leaves behind, so the pass still runs it after the child.
    """
    state.enqueue("full", THIS_WEEK, now=before(minutes=2))
    state.enqueue("drift", TODAY, now=before(minutes=1))
    state.enqueue("samples", "sample:7", now=before(minutes=1))

    def launch(argv, timeout_s):
        work.launched.append(SimpleNamespace(argv=list(argv), timeout_s=timeout_s))
        if "--full" in argv:                    # what run.full_sync does on success
            state.mark_done_before(T0, kinds=run.FULL_SYNC_COVERS, now=T0)
        return 0

    one_pass(work, launch=launch)

    assert modes(work) == ["--full", "--drift", "--reconcile"]
    assert row("drift", TODAY).done_at is not None
    assert row("samples", "sample:7").done_at == T0 and names(work) == []


@pytest.mark.django_db
def test_the_drain_says_the_loop_started_the_runs_it_records(work):
    state.enqueue("catalog", "*", now=before(minutes=1))

    one_pass(work)

    (catalog,) = [c for c in work.calls if c.name == "catalog"]
    assert catalog.kwargs == {"record": True, "trigger": loop.TRIGGER}
    assert all(c.argv[c.argv.index("--trigger") + 1] == loop.TRIGGER for c in work.launched)


# --- housekeeping ---------------------------------------------------------------------------------

@pytest.mark.django_db
def test_a_pass_ends_the_runs_whose_process_died(work):
    GraphSyncRun.objects.create(kind="full", started_at=before(days=1), status="running", counts_json={})

    report = one_pass(work)

    assert report["runs_abandoned"] == 1
    assert GraphSyncRun.objects.get(kind="full", started_at=before(days=1)).status == "abandoned"


@pytest.mark.django_db
def test_run_directories_are_pruned_to_the_newest_of_each_kind(work, tmp_path):
    for kind in loop.PRUNED_KINDS:
        for n in range(loop.KEEP_RUN_DIRS + 3):
            (tmp_path / f"{kind}-20260901T0000{n:02d}Z").mkdir()
    (tmp_path / "graph_sync-20260101T000000Z").mkdir()          # a hand run's directory, not the loop's to delete

    report = one_pass(work)

    assert report["run_dirs_pruned"] == 3 * len(loop.PRUNED_KINDS)
    kept = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("full-"))
    assert len(kept) == loop.KEEP_RUN_DIRS and kept[0] == "full-20260901T000003Z"
    assert (tmp_path / "graph_sync-20260101T000000Z").exists()


# --- the loop itself ------------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_loop_never_exits_on_a_failing_pass(work, monkeypatch):
    passes, slept = [], []

    def one(driver, db, worker_id, *, opts, launch=None, now=None):
        passes.append(worker_id)
        if len(passes) == 1:
            raise RuntimeError("graph_sync_outbox is not there")
        return {"drained": [], "counts": {loop.DONE: 0}}

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(loop, "run_pass", one)
    with pytest.raises(KeyboardInterrupt):
        loop.run_forever(DRIVER, DB, "w1", opts=work.opts, interval_s=5, sleep=sleep)

    assert passes == ["w1"] * 3
    assert slept == [loop.ERROR_INTERVAL_S, 5, 5]


# --- the flags the loop passes on -----------------------------------------------------------------

@pytest.mark.parametrize("value, approved", [(None, False), ("", False), ("report", False), ("apply", True),
                                             (" APPLY ", True), ("applied", False)])
def test_label_changes_are_approved_only_by_the_environment(value, approved):
    env = {} if value is None else {loop.LABEL_CHANGES_ENV: value}
    assert loop.label_changes_approved(env) is approved


@pytest.mark.django_db
def test_approved_label_changes_reach_the_children_and_the_drain(work):
    work.opts = replace(work.opts, apply_label_changes=True)
    state.enqueue("samples", "sample:7", now=before(minutes=1))

    one_pass(work)

    (sync,) = [c for c in work.calls if c.name == "sync"]
    assert sync.kwargs["apply_label_changes"] is True
    assert {c.argv[3]: "--apply-label-changes" in c.argv for c in work.launched} == {
        "--full": True, "--reconcile": True, "--drift": False}


@pytest.mark.django_db
def test_label_changes_stay_off_by_default(work):
    state.enqueue("samples", "sample:7", now=before(minutes=1))

    one_pass(work)

    (sync,) = [c for c in work.calls if c.name == "sync"]
    assert sync.kwargs["apply_label_changes"] is False
    assert not any("--apply-label-changes" in c.argv for c in work.launched)


# --- the run root and the worker id ---------------------------------------------------------------

def test_the_run_root_is_the_run_directory_of_the_environment_or_one_under_the_log_directory(monkeypatch, tmp_path,
                                                                                             settings):
    monkeypatch.setenv(loop.RUN_DIR_ENV, str(tmp_path / "runs"))
    assert loop.default_run_root() == str(tmp_path / "runs")

    monkeypatch.delenv(loop.RUN_DIR_ENV)
    settings.LOG_DIR = str(tmp_path / "logs")
    assert loop.default_run_root() == str(tmp_path / "logs" / "graph_sync")


def test_the_worker_id_names_the_loop_and_fits_its_column():
    first, second = loop.worker_identity(), loop.worker_identity()
    assert first.startswith(f"{loop.TRIGGER}:") and len(first) <= state.WORKER_CHARS
    # A nonce, so two loops restarted into the same pid on the same host are two workers, never one.
    assert first != second
