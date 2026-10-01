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
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone as dt_timezone
from importlib import import_module
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections

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
    run_dir = os.path.join(str(work.opts.run_root), f"full-{T0:%Y%m%dT%H%M%SZ}-{row('full', THIS_WEEK).id}")
    assert child.argv == [sys.executable, str(loop.MANAGE_PY), "graph_sync", "--full", "--run-dir", run_dir,
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
    """2 is a refusal the operator must read (a graph below the writer's version, a preflight problem), so the row is
    done and the refusal is in its run record. A graph-write lock that stayed busy is exit 1, not 2: see
    ``test_a_child_that_found_the_graph_write_lock_busy_keeps_its_slot_owed``."""
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


_REAL_CATALOG_SYNC = run.catalog_sync          # ``work`` stubs it; a reconcile child run for real needs the real one


@contextmanager
def _lock_never_free(timeout_s):
    """The graph-write lock as a write that outlasts every wait leaves it: never acquired."""
    yield False


def in_process(work, monkeypatch, settings):
    """A launcher that runs each ``--full`` and ``--reconcile`` child as the real ``manage.py graph_sync`` command, in
    this process, and answers the status it exits with. Every other child exits 0."""
    command = import_module("nextseek_api.management.commands.graph_sync")
    monkeypatch.setattr(command, "GraphDatabase", SimpleNamespace(driver=lambda uri, auth=None: nullcontext(DRIVER)))
    monkeypatch.setattr(run, "catalog_sync", _REAL_CATALOG_SYNC)
    settings.NEO4J_DATABASE = {"NAME": DB, "URI": "neo4j://neo4j:7687", "AUTH": ("neo4j", "x")}

    def launch(argv, timeout_s):
        work.launched.append(SimpleNamespace(argv=list(argv), timeout_s=timeout_s))
        if argv[3] not in ("--full", "--reconcile"):
            return 0
        try:
            call_command(*argv[2:], stdout=StringIO(), stderr=StringIO())
        except CommandError as exc:
            return exc.returncode
        return 0
    return launch


@pytest.mark.django_db
def test_a_child_that_found_the_graph_write_lock_busy_keeps_its_slot_owed(work, monkeypatch, settings):
    """Another write holding the lock past a child's wait is not a refusal of this graph: nothing was written, nothing
    is wrong, and the same run succeeds once the lock is free. So the child exits 1 and its slot backs off and runs
    again. It used to exit 2, from ``--full`` and from a reconcile whose catalog step lost the lock, and the slot was
    closed as done: that week's full sync, or that night's reconcile, was recorded done having done nothing."""
    monkeypatch.setattr(state, "graph_write_lock", _lock_never_free)

    report = one_pass(work, launch=in_process(work, monkeypatch, settings))

    for kind, key in (("full", THIS_WEEK), ("reconcile", TODAY)):
        (entry,) = [d for d in report["drained"] if d["kind"] == kind]
        assert (entry["outcome"], entry["exit"], entry["refused"]) == (loop.FAILED, 1, False), kind
        r = row(kind, key)
        assert r.done_at is None and r.attempts == 1 and r.claimed_by is None, kind
        assert r.lease_expires_at == T0 + timedelta(seconds=state.backoff_s(kind)), kind
    assert {r.kind: r.status for r in GraphSyncRun.objects.filter(kind__in=("full", "catalog", "reconcile"))} == {
        "full": "refused", "catalog": "refused", "reconcile": "refused"}


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
def test_two_drift_slots_in_one_pass_are_judged_each_by_its_own_report(work):
    """``run_pass`` fixes ``now`` once, so both children start in the same second. Each gets its own run directory,
    or the second, exiting 1 before it saved anything, would be closed on the first one's report."""
    state.enqueue("drift", "slot:2026-09-14", now=before(hours=26))      # an older slot, re-enqueued by hand
    state.enqueue("drift", TODAY, now=before(minutes=1))
    answers = [(FOUND_DRIFT, 1), (None, 1)]                           # the second crashes before saving

    def launch(argv, timeout_s):
        work.launched.append(SimpleNamespace(argv=list(argv), timeout_s=timeout_s))
        if "--drift" not in argv:
            return 0
        saved, code = answers.pop(0)
        if saved is not None:
            run_dir = argv[argv.index("--run-dir") + 1]
            os.makedirs(run_dir, exist_ok=True)
            with open(os.path.join(run_dir, drift.RESULT_FILE), "w", encoding="utf-8") as fh:
                json.dump(saved, fh)
        return code

    report = one_pass(work, launch=launch)

    first, second = [d for d in report["drained"] if d["kind"] == "drift"]
    assert first["run_dir"] != second["run_dir"]
    assert (first["outcome"], second["outcome"]) == (loop.DONE, loop.FAILED)
    assert row("drift", "slot:2026-09-14").done_at is not None
    assert row("drift", TODAY).done_at is None and row("drift", TODAY).attempts == 1


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


@pytest.mark.django_db
def test_a_deferred_row_is_not_failing_and_a_failed_row_is(work):
    """A deferral is not the row's fault (loop docstring), so only the real failure starts the clock."""
    state.enqueue("samples", "sample:7", now=before(minutes=2))
    state.enqueue("samples_of_type", "type:3", now=before(minutes=1))
    work.sync = {"status": targeted.LOCK_TIMEOUT}
    work.of_type = RuntimeError("OperationalError: (2006, 'Server has gone away')")

    one_pass(work)

    assert row("samples", "sample:7").failing_since is None
    assert row("samples_of_type", "type:3").failing_since == T0


@pytest.mark.django_db
def test_a_sync_that_left_a_structural_link_unwritten_fails_its_row_naming_the_parts(work):
    state.enqueue("samples", "sample:7", now=before(minutes=2))
    work.sync = {"status": targeted.OK, "structural_gaps": 2,
                 "structural_gap_parts": {"in_project_missing": 1, "seek_study_investigation_missing": 1}}

    report = one_pass(work)

    r = row("samples", "sample:7")
    assert r.done_at is None and r.failing_since == T0 and r.attempts == 1
    assert "in_project_missing 1, seek_study_investigation_missing 1" in r.last_error
    assert report["counts"]["failed"] == 1


@pytest.mark.django_db
def test_a_parent_not_yet_uploaded_alone_still_closes_the_row(work):
    state.enqueue("samples", "sample:7", now=before(minutes=2))
    work.sync = {"status": targeted.OK, "lineage_dropped": 3, "labels_edges_missing": 1, "structural_gaps": 0,
                 "structural_gap_parts": {}}

    one_pass(work)

    assert row("samples", "sample:7").done_at is not None


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
    assert catalog.kwargs == {"record": True, "trigger": loop.TRIGGER,
                              "run_dir": loop.run_dir_for(work.opts.run_root, loop.DRAIN_DIR_KIND, T0)}
    assert all(c.argv[c.argv.index("--trigger") + 1] == loop.TRIGGER for c in work.launched)


@pytest.mark.django_db
def test_the_small_tables_drain_archives_into_the_drain_directory(work):
    state.enqueue("isa", "*", now=before(minutes=1))

    one_pass(work)

    (small,) = [c for c in work.calls if c.name == "small"]
    assert small.kwargs == {"run_dir": loop.run_dir_for(work.opts.run_root, loop.DRAIN_DIR_KIND, T0)}


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
        loop.run_forever(DRIVER, DB, "w1", opts=work.opts, interval_s=5, sleep=sleep, refresh=lambda: None)

    assert passes == ["w1"] * 3
    assert slept == [loop.ERROR_INTERVAL_S, 5, 5]


@pytest.mark.django_db
def test_every_pass_starts_on_fresh_database_connections(work, monkeypatch):
    # A loop that lives for days keeps each connection until MySQL drops it for idling; the next drain on it then
    # fails with "Server has gone away" (2006). So the connections are refreshed before every pass, a failed one too.
    events = []

    def one(driver, db, worker_id, *, opts, launch=None, now=None):
        events.append("pass")
        if events.count("pass") == 1:
            raise RuntimeError("(2006, 'Server has gone away')")
        return {"drained": [], "counts": {loop.DONE: 0}}

    def sleep(seconds):
        if events.count("pass") == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(loop, "run_pass", one)
    with pytest.raises(KeyboardInterrupt):
        loop.run_forever(DRIVER, DB, "w1", opts=work.opts, interval_s=5, sleep=sleep,
                         refresh=lambda: events.append("refresh"))

    assert events == ["refresh", "pass"] * 3


def test_the_loop_refreshes_every_django_connection_by_default():
    assert loop.refresh_connections.__self__ is connections and loop.refresh_connections.__name__ == "close_all"


def test_run_forever_calls_refresh_connections_before_each_pass_when_given_no_refresh(monkeypatch, tmp_path):
    # The default path: no ``refresh`` argument, so the loop must look up ``refresh_connections`` itself.
    events = []

    def one(driver, db, worker_id, *, opts, launch=None, now=None):
        events.append("pass")
        return {"drained": [], "counts": {loop.DONE: 0}}

    def sleep(seconds):
        if events.count("pass") == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(loop, "run_pass", one)
    monkeypatch.setattr(loop, "refresh_connections", lambda: events.append("refresh"))
    with pytest.raises(KeyboardInterrupt):
        loop.run_forever(DRIVER, DB, "w1", opts=loop.Options(run_root=str(tmp_path)), interval_s=5, sleep=sleep)

    assert events == ["refresh", "pass"] * 2


@pytest.mark.django_db
@pytest.mark.parametrize("exit_code", [0, 1])
def test_connections_are_refreshed_right_after_a_child_returns_whether_it_worked_or_not(work, monkeypatch, exit_code):
    # A child can run for hours inside a blocking subprocess call, and the parent's connections sit idle all that
    # time; a failed child must not skip the refresh either.
    events = []

    def launch(argv, timeout_s):
        events.append(f"child {argv[3]}")
        return exit_code

    monkeypatch.setattr(loop, "refresh_connections", lambda: events.append("refresh"))

    one_pass(work, launch=launch)

    assert events, "the pass started no child"
    for i, event in enumerate(events):
        if event.startswith("child "):
            assert events[i + 1:i + 2] == ["refresh"], events


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


@pytest.mark.django_db
def test_a_row_written_during_a_long_child_fails_at_the_time_it_failed_not_at_the_pass_start(work, monkeypatch):
    # With no ``now`` given, each claim and its outcome use the clock as it is then. A row a write enqueued while a 2 h
    # child ran must not get a failing_since from before it was written (it would be overdue after one failure).
    clock = [T0]
    during = T0 + timedelta(hours=2)
    monkeypatch.setattr(loop.dj_timezone, "now", lambda: clock[0])

    def launch(argv, timeout_s):
        if argv[3] == "--reconcile":
            clock[0] = during
            state.enqueue("samples_of_type", "type:3", now=during)
        return 0

    work.of_type = RuntimeError("OperationalError: (2006, 'Server has gone away')")

    loop.run_pass(DRIVER, DB, "w1", opts=work.opts, launch=launch)

    r = row("samples_of_type", "type:3")
    assert r.attempts == 1
    assert r.failing_since == during
    assert r.lease_expires_at == during + timedelta(seconds=state.backoff_s("samples_of_type"))


@pytest.mark.django_db
def test_rows_archived_across_several_seconds_of_one_pass_share_one_drain_directory(work, monkeypatch):
    # A bulk delete is one retire row per sample; each archives a retired.tsv into the drain directory, and the next
    # pass keeps only the newest KEEP_RUN_DIRS of them. The directory is named from the pass start, one per pass.
    clock = [T0]

    def tick():
        clock[0] += timedelta(seconds=1)
        return clock[0]

    monkeypatch.setattr(loop.dj_timezone, "now", tick)
    for n in range(30):
        state.enqueue("retire", f"sample:{n + 1}", now=before(minutes=5))
    work.opts = loop.Options(run_root=work.opts.run_root, cadences=())

    loop.run_pass(DRIVER, DB, "w1", opts=work.opts, launch=work.launch)

    dirs = [c.kwargs["run_dir"] for c in work.calls if c.name == "retire"]
    assert len(dirs) == 30
    assert len(set(dirs)) == 1


@pytest.mark.django_db
def test_a_child_that_fails_is_backed_off_from_the_time_it_ended(work, monkeypatch):
    # The outcome of a child that ran for hours is stamped when it ended, not when it was claimed: its failing_since
    # is the failure time and its back-off runs from there.
    clock = [T0]
    ended = T0 + timedelta(hours=2)
    monkeypatch.setattr(loop.dj_timezone, "now", lambda: clock[0])

    def launch(argv, timeout_s):
        if argv[3] == "--reconcile":
            clock[0] = ended
        return 1

    loop.run_pass(DRIVER, DB, "w1", opts=work.opts, launch=launch)

    r = GraphSyncOutbox.objects.get(kind="reconcile")
    assert r.failing_since == ended
    assert r.lease_expires_at == ended + timedelta(seconds=state.backoff_s("reconcile"))
    backoff = timedelta(seconds=state.backoff_s("reconcile"))
    assert state.claim_next("w2", now=ended + backoff - timedelta(seconds=1), kinds=["reconcile"]) is None


# --- A13: single-sample rows drain as one sync -----------------------------------------------------

def _single_rows(n: int, *, first_id: int = 1000) -> list[int]:
    ids = list(range(first_id, first_id + n))
    for k, sample_id in enumerate(ids):
        state.enqueue("samples", f"sample:{sample_id}", now=before(minutes=30) + timedelta(milliseconds=k))
    return ids


@pytest.mark.django_db
def test_the_drain_runs_300_single_sample_rows_as_one_sync_and_closes_all_300(work):
    ids = _single_rows(300)

    report = one_pass(work)

    (sync,) = [c for c in work.calls if c.name == "sync"]
    assert sync.args == (DRIVER, DB, ids)
    assert GraphSyncOutbox.objects.filter(kind="samples", done_at__isnull=True).count() == 0
    (entry,) = [d for d in report["drained"] if d["kind"] == "samples"]
    assert entry["rows"] == 300 and entry["outcome"] == loop.DONE
    assert report["counts"][loop.DONE] == 300 + 3          # the three schedule slots of T0 are children


@pytest.mark.django_db
def test_a_failed_merged_sync_backs_off_every_row_it_drained(work):
    _single_rows(3)
    work.sync = RuntimeError("neo4j went away")

    report = one_pass(work)

    rows = GraphSyncOutbox.objects.filter(kind="samples")
    assert {(r.done_at, r.attempts, r.failing_since) for r in rows} == {(None, 1, T0)}
    assert {r.lease_expires_at for r in rows} == {T0 + timedelta(seconds=state.backoff_s("samples"))}
    assert all("neo4j went away" in r.last_error for r in rows)
    assert report["counts"][loop.FAILED] == 3


@pytest.mark.django_db
def test_a_merged_sync_that_left_a_structural_link_unwritten_fails_every_row(work):
    _single_rows(2)
    work.sync = {"status": targeted.OK, "structural_gaps": 1, "structural_gap_parts": {"untyped": 1}}

    one_pass(work)

    rows = GraphSyncOutbox.objects.filter(kind="samples")
    assert {(r.done_at, r.attempts) for r in rows} == {(None, 1)}
    assert all("untyped 1" in r.last_error for r in rows)


def _gap(*ids, reason="in_project_missing (project ids SEEK lacks: 77)"):
    """A by-id sync's report that left a structural link unwritten for ``ids``, and names them."""
    return {"status": targeted.OK, "structural_gaps": len(ids),
            "structural_gap_parts": {"in_project_missing": len(ids)},
            "structural_gap_samples": {i: reason for i in ids}}


@pytest.mark.django_db
def test_a_merged_sync_with_a_gapped_sample_fails_only_its_row(work):
    _single_rows(3)
    work.sync = _gap(1001)

    report = one_pass(work)

    rows = {r.key: r for r in GraphSyncOutbox.objects.filter(kind="samples")}
    assert rows["sample:1000"].done_at is not None and rows["sample:1002"].done_at is not None
    bad = rows["sample:1001"]
    assert (bad.done_at, bad.attempts, bad.failing_since) == (None, 1, T0)
    assert bad.lease_expires_at == T0 + timedelta(seconds=state.backoff_s("samples"))
    assert "sample:1001" in bad.last_error and "project ids SEEK lacks: 77" in bad.last_error
    assert report["counts"][loop.FAILED] == 1 and report["counts"][loop.DONE] == 2 + 3


@pytest.mark.django_db
def test_a_batch_row_with_a_gapped_sample_closes_and_hands_only_that_sample_on(work):
    state.enqueue("samples", "batch:reg:1:0", [11, 12, 13], now=before(minutes=1))
    work.sync = _gap(12)

    report = one_pass(work)

    assert row("samples", "batch:reg:1:0").done_at is not None
    r = row("samples", "sample:12")
    assert (r.done_at, r.claimed_by, r.attempts, r.failing_since) == (None, None, 1, T0)
    assert r.lease_expires_at == T0 + timedelta(seconds=state.backoff_s("samples"))
    assert "sample:12" in r.last_error and "batch:reg:1:0" in r.last_error and "77" in r.last_error
    assert not GraphSyncOutbox.objects.filter(key__in=["sample:11", "sample:13"]).exists()
    (entry,) = [d for d in report["drained"] if d["kind"] == "samples"]
    assert (entry["outcome"], entry["handed_on"]) == (loop.FAILED, 1)

    work.sync = _gap(12)                                   # an hour on, only the gapped sample is synced again
    one_pass(work, now=T0 + timedelta(seconds=state.backoff_s("samples")))
    assert [c.args[2] for c in work.calls if c.name == "sync"] == [[11, 12, 13], [12]]
    assert row("samples", "sample:12").attempts == 2


@pytest.mark.django_db
def test_a_sample_type_row_hands_its_gapped_samples_on_and_closes(work):
    state.enqueue("samples_of_type", "type:26", now=before(minutes=1))
    work.of_type = _gap(10, 12)

    one_pass(work)

    assert row("samples_of_type", "type:26").done_at is not None
    assert {r.key for r in GraphSyncOutbox.objects.filter(kind="samples", done_at__isnull=True)} == {
        "sample:10", "sample:12"}


@pytest.mark.django_db
def test_a_handed_on_sample_keeps_the_attempts_and_the_failing_clock_of_its_batch_row(work):
    state.enqueue("samples", "batch:reg:1:0", [11, 12], now=before(hours=3))
    GraphSyncOutbox.objects.filter(key="batch:reg:1:0").update(attempts=2, failing_since=before(hours=2))
    work.sync = _gap(12)

    one_pass(work)

    r = row("samples", "sample:12")
    assert (r.attempts, r.failing_since) == (3, before(hours=2))


@pytest.mark.django_db
def test_a_row_that_failed_twice_drains_alone_so_the_healthy_rows_merged_with_it_close(work, monkeypatch):
    """A sync that raises on one sample fails every row merged with it, and the group becomes claimable again at
    one moment: without isolation it re-merges until all of it is dead. A row that has failed twice drains alone, so
    the poison row fails by itself and the healthy rows close on their next claim."""
    _single_rows(4)                                    # 1000 fails every sync it is in; 1001 to 1003 are healthy
    synced = []

    def sync(driver, db, ids, **kwargs):
        synced.append(list(ids))
        if 1000 in ids:
            raise RuntimeError("sample 1000 cannot be written")
        return {"status": targeted.OK}

    monkeypatch.setattr(targeted, "sync_samples", sync)
    hour = timedelta(seconds=state.backoff_s("samples"))
    one_pass(work, now=T0)
    one_pass(work, now=T0 + hour)
    state.enqueue("samples", "sample:2000", now=T0 + hour + timedelta(minutes=30))     # a new write meanwhile
    one_pass(work, now=T0 + 2 * hour)

    assert synced == [[1000, 1001, 1002, 1003]] * 2 + [[1000], [1001], [1002], [1003], [2000]]
    rows = {r.key: r for r in GraphSyncOutbox.objects.filter(kind="samples")}
    assert all(rows[f"sample:{i}"].done_at is not None for i in (1001, 1002, 1003, 2000))
    assert (rows["sample:1000"].done_at, rows["sample:1000"].attempts) == (None, 3)


@pytest.mark.django_db
def test_a_gap_that_names_many_samples_stays_one_merged_sync_and_a_fresh_edit_is_not_held_back(work, monkeypatch):
    """A merged sync fails each sample its gap names on that sample's own row and closes the rest, so a row whose
    last failure was a gap traced to it stays mergeable: forty gapped samples cost one sync a pass, not forty, and a
    healthy edit enqueued meanwhile drains in the first pass after it arrives."""
    gapped = set(range(1000, 1040))
    state.enqueue("samples", "batch:reg:1:0", sorted(gapped) + [1040, 1041], now=before(minutes=1))
    synced = []

    def sync(driver, db, ids, **kwargs):
        synced.append(list(ids))
        named = {i: "in_project_missing (project ids SEEK lacks: 77)" for i in ids if i in gapped}
        return {"status": targeted.OK, "structural_gaps": len(named),
                "structural_gap_parts": {"in_project_missing": len(named)}, "structural_gap_samples": named}

    monkeypatch.setattr(targeted, "sync_samples", sync)
    hour = timedelta(seconds=state.backoff_s("samples"))
    for n in range(3):
        one_pass(work, now=T0 + n * hour)
    state.enqueue("samples", "sample:5000", now=T0 + 2 * hour + timedelta(minutes=30))     # a healthy edit
    one_pass(work, now=T0 + 3 * hour)

    assert [len(ids) for ids in synced] == [42, 40, 40, 41] and synced[-1][-1] == 5000
    assert row("samples", "sample:5000").done_at is not None
    rows = GraphSyncOutbox.objects.filter(key__in=[f"sample:{i}" for i in gapped])
    assert {(r.done_at, r.attempts) for r in rows} == {(None, 4)}
    assert all(r.last_error.startswith(loop.TRACED_GAP_ERROR) for r in rows)


@pytest.mark.django_db
def test_a_gap_the_sync_could_not_trace_is_isolated_like_a_raise(work):
    """An untraced gap names every sample of its chunk, healthy ones included, so merging such rows again could fail
    a healthy one every time: they drain alone from their third claim, as a raise does."""
    _single_rows(2)
    reason = targeted.UNTRACED_GAP.format(part="in_study_samples_missing", count=1)
    work.sync = {"status": targeted.OK, "structural_gaps": 1, "structural_gap_parts": {"in_study_samples_missing": 1},
                 "structural_gap_samples": {1000: reason, 1001: reason}}
    hour = timedelta(seconds=state.backoff_s("samples"))

    for n in range(3):
        one_pass(work, now=T0 + n * hour)

    assert [c.args[2] for c in work.calls if c.name == "sync"] == [[1000, 1001], [1000, 1001], [1000], [1001]]
    assert not row("samples", "sample:1000").last_error.startswith(loop.TRACED_GAP_ERROR)


@pytest.mark.django_db
def test_a_transient_failure_costs_one_merged_retry(work, monkeypatch):
    _single_rows(3)
    synced, failures = [], [RuntimeError("neo4j went away")]

    def sync(driver, db, ids, **kwargs):
        synced.append(list(ids))
        if failures:
            raise failures.pop()
        return {"status": targeted.OK}

    monkeypatch.setattr(targeted, "sync_samples", sync)
    one_pass(work, now=T0)
    one_pass(work, now=T0 + timedelta(seconds=state.backoff_s("samples")))

    assert synced == [[1000, 1001, 1002]] * 2
    assert GraphSyncOutbox.objects.filter(kind="samples", done_at__isnull=True).count() == 0


@pytest.mark.django_db
def test_a_deferred_merged_sync_puts_every_row_back_without_an_attempt(work):
    _single_rows(2)
    work.sync = {"status": targeted.LOCK_TIMEOUT}

    one_pass(work)

    rows = GraphSyncOutbox.objects.filter(kind="samples")
    assert {(r.done_at, r.attempts, r.claimed_by, r.failing_since) for r in rows} == {(None, 0, None, None)}


@pytest.mark.django_db
def test_a_batch_row_is_never_merged_with_single_sample_rows(work):
    state.enqueue("samples", "batch:reg:1:0", [11, 12], now=before(minutes=40))
    _single_rows(2)
    state.enqueue("samples", "batch:reg:1:1", [13], now=before(minutes=20))

    one_pass(work)

    assert [c.args[2] for c in work.calls if c.name == "sync"] == [[11, 12], [1000, 1001], [13]]


@pytest.mark.django_db
def test_merged_rows_count_toward_the_rows_a_pass_may_drain(work, monkeypatch):
    monkeypatch.setattr(loop, "MAX_ROWS_PER_PASS", 5)
    ids = _single_rows(8)

    report = one_pass(work)

    assert [c.args[2] for c in work.calls if c.name == "sync"] == [ids[:5]]
    assert GraphSyncOutbox.objects.filter(kind="samples", done_at__isnull=True).count() == 3
    assert sum(d["rows"] for d in report["drained"]) == 5
