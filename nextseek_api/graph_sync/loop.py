"""The sync loop: the schedule, the outbox drain, and the heavy runs as child processes (the sync design, 12, 13).

``manage.py graph_sync --loop`` is one pass after another, for ever; ``--once`` is one pass. A pass:

1. **housekeeping**: ends the runs whose process died (``state.reap_abandoned``) and deletes all but the newest
   ``KEEP_RUN_DIRS`` run directories of each kind the loop writes;
2. **the schedule**: ``schedule.due_slots`` says which scheduled runs are owed, and each becomes one outbox row.
   ``(kind, key)`` is unique, so a slot already there is left exactly as it is: the schedule asks, the outbox
   decides, and a slot missed while the loop was down runs once at the next pass, not once per day it missed;
3. **the drain**: claim the oldest claimable row and apply it. ``samples``, ``samples_of_type``, ``retire``,
   ``catalog``, ``assay_map``, ``protocol_map``, ``isa`` and ``membership`` run in this process, through the same
   by-id entry points every other path uses. ``full``, ``reconcile`` and ``drift`` run as child
   ``manage.py graph_sync`` processes, so their memory returns when they end and a crash cannot kill the loop.

**A graph below the writer's schema version is only read.** Until the operator's first ``graph_sync --full`` at 1.2,
the loop claims nothing but the read-only drift check: the writing rows wait in the outbox, unclaimed, with their
attempts untouched, and the loop never turns a 1.1 graph into a 1.2 one by itself (the design, section 12; R2).

**Work it could not do is put back, not punished.** A claim counts an attempt and a row dies at
``state.MAX_ATTEMPTS``, so for a row drained in this process the two outcomes that are not the row's fault, the
graph-write lock being held by another graph_sync write and a graph below the writer's version, are *deferred*: the
row is re-enqueued, which resets its attempts, and released with a short back-off (``DEFER_BACKOFF_S``). A real
failure backs off by the kind's own ``state.backoff_s`` with its attempt counted.

**A child's exit status decides its row**: 0 done, 2 done with the refusal recorded (a graph below the writer's
version, a preflight problem: nothing was written, its own run record says why, and a retry would meet it again),
anything else, a timeout included, failed with its kind's back-off. A graph-write lock another write held past the
child's wait is exit 1, not 2, whichever step met it: ``--full`` and a reconcile's catalog step raise
``run.LockTimeout``, which the command exits 1 on, and a later reconcile step answers ``lock_timeout``. So its slot
backs off and runs again instead of being closed as done having done nothing; an exit status says no more than that,
so unlike a deferred row it keeps the attempt its claim counted. The one exit 1 that is done is a drift check that
ran to its end and found drift, proven by the result it saved in its run directory: the check reports and does not
repair, so a retry would find the same drift an hour later, until the row died, with the outbox reported stale all
along.

**Nothing but a signal ends the loop.** A failing pass is logged and the next one runs: a loop that exits on one bad
row stops draining every other one, and the entrypoint would only restart it into the same failure a minute later.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone as dj_timezone

from nextseek_api.graph_sync import drift, run, schedule, state, targeted, writer

log = logging.getLogger(__name__)

UTC = timezone.utc

TRIGGER = "loop"                       # what a run record says started the work the loop drives
MANAGE_PY = Path(__file__).resolve().parents[2] / "manage.py"
RUN_DIR_ENV = "GS_RUN_DIR"
LABEL_CHANGES_ENV = "NEXTSEEK_GRAPH_SYNC_LABEL_CHANGES"
APPROVED = "apply"                     # the one value of that variable that lets the loop write label changes (R14)

DEFAULT_INTERVAL_S = 5.0
ERROR_INTERVAL_S = 60.0                # after a failed pass, so a broken box logs once a minute and not every 5 s
KEEP_RUN_DIRS = 20                     # run directories kept per kind
DEFER_BACKOFF_S = 60                   # how long a deferred row waits; it counts no attempt
MAX_ROWS_PER_PASS = 1_000              # a pass with more work than this finishes it at the next one

DONE, DEFERRED, FAILED = "done", "deferred", "failed"

CHILD_KINDS = ("reconcile", "full", "drift")          # run as child processes
READ_ONLY_KINDS = ("drift",)                          # the kinds a graph below the writer's version still allows
LABEL_CHANGE_KINDS = ("full", "reconcile")            # the children that take --apply-label-changes
DRAIN_DIR_KIND = "drain"                              # the run directory the in-process kinds archive into
PRUNED_KINDS = CHILD_KINDS + (DRAIN_DIR_KIND,)

# What ``_defer`` answers to, by status and by refusal: neither is the row's fault.
DEFER_STATUSES = (targeted.LOCK_TIMEOUT, targeted.NOT_AT_VERSION)
LOCK_REFUSAL = "graph-write lock was not acquired"     # run._lock_problem's words, in a PreflightError's problems

# A child's ceiling, under its row's lease (``state.lease_s``) so that no other worker claims the row while a child
# that overran is still being killed.
_CHILD_TIMEOUT_S = MappingProxyType({"full": 5 * 3600, "reconcile": 2 * 3600, "drift": 90 * 60})

# The run statuses that count as "this kind ran" for the schedule. A drift check that found drift did its job: it
# reports, it does not repair, and only a failure leaves its slot owed.
_SATISFYING_STATUSES = MappingProxyType({"drift": ("ok", "drift")})
# ``manage.py graph_sync --drift``'s exit when the check ran and found drift (the design, section 13). The drain
# closes that row, as the schedule counts that run, but only on the result the child saved (``drift_reported``).
DRIFT_FOUND_EXIT = 1


def child_timeout_s(kind: str) -> float:
    return _CHILD_TIMEOUT_S.get(kind, state.lease_s(kind) / 2)


def label_changes_approved(env=None) -> bool:
    """Whether the operator has approved writing label changes for this loop (R14): the environment says so, and
    the default (``report``, or nothing) does not."""
    env = os.environ if env is None else env
    return str(env.get(LABEL_CHANGES_ENV, "")).strip().lower() == APPROVED


def default_run_root() -> str:
    """Where the loop's run directories go: ``$GS_RUN_DIR``, else ``graph_sync`` under the log directory, which in
    the app container is a mounted volume the operator can read after the fact."""
    return os.environ.get(RUN_DIR_ENV) or os.path.join(getattr(settings, "LOG_DIR", os.getcwd()), "graph_sync")


def run_dir_for(run_root: str, kind: str, now: datetime | None = None, row_id: int | None = None) -> str:
    """``<run root>/<kind>-<UTC time>``, and ``-<row id>`` for a child the drain launches. The stamp sorts as it
    runs, which is what ``prune_run_dirs`` counts on.

    The row id is what keeps two children of one pass apart: ``run_pass`` fixes its time once, so two slots of one
    kind drained in the same pass would otherwise share a directory, write over each other's files, and a drift
    child that saved nothing would be judged on the other one's result (``drift_reported``)."""
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(run_root, f"{kind}-{stamp}" + ("" if row_id is None else f"-{row_id}"))


def worker_identity() -> str:
    """``loop:<host>:<pid>:<nonce>``, within ``graph_sync_outbox.claimed_by``.

    The nonce matters: two loops restarted into the same pid on the same host would otherwise share an id, and a
    claim is held against exactly that string.
    """
    return f"{TRIGGER}:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"[:state.WORKER_CHARS]


@dataclass(frozen=True)
class Options:
    """One loop's settings, so every pass and every child reads the same ones."""

    run_root: str = field(default_factory=default_run_root)
    apply_label_changes: bool = False
    record: bool = True
    trigger: str = TRIGGER
    keep_run_dirs: int = KEEP_RUN_DIRS
    cadences: tuple = schedule.DEFAULT_CADENCES


# --- the children ---------------------------------------------------------------------------------

def child_argv(kind: str, run_dir: str, opts: Options) -> list[str]:
    """The command line of one heavy run.

    It carries ``--i-mean-the-live-graph``: the loop is what the app container runs against its own graph, and the
    flag exists to stop a *hand* run from reaching it by accident.
    """
    argv = [sys.executable, str(MANAGE_PY), "graph_sync", f"--{kind}", "--run-dir", run_dir,
            "--trigger", opts.trigger, "--i-mean-the-live-graph"]
    if not opts.record:
        argv.append("--no-record")
    if opts.apply_label_changes and kind in LABEL_CHANGE_KINDS:
        argv.append("--apply-label-changes")
    return argv


def launch_child(argv: list[str], timeout_s: float) -> int | None:
    """Run one child to its end and return its exit status, or None when it had to be killed.

    Its output is not captured: the container's log is where an operator reads a sync's progress, and a full sync
    prints for minutes.
    """
    try:
        return subprocess.run(argv, cwd=str(MANAGE_PY.parent), timeout=timeout_s, check=False).returncode
    except subprocess.TimeoutExpired:
        log.error("graph_sync: %s did not finish within %s s and was killed", " ".join(argv[3:]), timeout_s)
        return None


# --- the run directories --------------------------------------------------------------------------

def prune_run_dirs(run_root: str, *, keep: int = KEEP_RUN_DIRS, kinds=PRUNED_KINDS) -> int:
    """Delete all but the newest ``keep`` run directories of each kind the loop writes, and return how many went.

    A directory of any other name, a hand run's ``graph_sync-<UTC time>`` among them, is left alone: the loop
    deletes only what it made.
    """
    try:
        names = sorted(os.listdir(run_root))
    except OSError:
        return 0
    removed = 0
    for kind in kinds:
        owned = [n for n in names if n.startswith(f"{kind}-") and os.path.isdir(os.path.join(run_root, n))]
        for name in owned[:max(0, len(owned) - keep)]:
            try:
                shutil.rmtree(os.path.join(run_root, name))
                removed += 1
            except OSError as exc:
                log.warning("graph_sync: could not delete the run directory %s: %s", name, exc)
    return removed


# --- the schedule ---------------------------------------------------------------------------------

def last_ok_started() -> dict[str, datetime]:
    """When the last run of each kind that satisfies its slot began (``state.last_runs`` keeps ISO text)."""
    found = {}
    for kind, record in state.last_runs().items():
        if record["status"] in _SATISFYING_STATUSES.get(kind, ("ok",)) and record["started_at"]:
            found[kind] = datetime.fromisoformat(record["started_at"])
    return found


def _schedule(opts: Options, now: datetime) -> list[dict]:
    written = []
    for slot in schedule.due_slots(opts.cadences, now, last_ok_started()):
        if state.ensure_slot(slot.kind, slot.key, now=now):
            written.append({"kind": slot.kind, "key": slot.key})
    return written


# --- the drain ------------------------------------------------------------------------------------

def _ids_of(claim) -> list[int]:
    """The sample ids a row asks for: the id in its key, or the ids a batch left in its payload."""
    if claim.key.startswith("sample:"):
        return [int(claim.key.split(":", 1)[1])]
    return [int(i) for i in (claim.payload or [])]


def _apply(driver, db, claim, opts: Options, run_dir: str) -> dict:
    """Do what one row asks, in this process, and return the entry point's report."""
    kind = claim.kind
    if kind == "samples":
        return targeted.sync_samples(driver, db, _ids_of(claim), run_dir=run_dir,
                                     apply_label_changes=opts.apply_label_changes)
    if kind == "samples_of_type":
        return targeted.sync_samples_of_type(driver, db, int(claim.key.split(":", 1)[1]), run_dir=run_dir,
                                             apply_label_changes=opts.apply_label_changes)
    if kind == "retire":
        return targeted.retire_samples(driver, db, _ids_of(claim), run_dir=run_dir)
    if kind == "catalog":
        return run.catalog_sync(driver, db, record=opts.record, trigger=opts.trigger)
    if kind in ("assay_map", "protocol_map"):
        return targeted.relabel_for_maps(driver, db, apply_label_changes=opts.apply_label_changes)
    if kind in ("isa", "membership"):
        return targeted.sync_small_tables(driver, db)
    raise ValueError(f"the drain has no entry point for outbox kind {kind!r}")


def _text(error) -> str:
    return f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)


def _defer(claim, reason: str, entry: dict, *, now: datetime) -> dict:
    """Put the row back as it was: pending, no attempt counted against it, a short back-off.

    ``finish_failed`` alone would keep the attempt the claim counted, and ``state.MAX_ATTEMPTS`` of them leave the
    row dead with its work never done. Re-enqueueing resets the attempts; the back-off then releases the claim.
    """
    try:
        state.enqueue(claim.kind, claim.key, claim.payload, now=now)
    except (DatabaseError, ValueError) as exc:
        log.warning("graph_sync: could not put %s %s back after %s: %s", claim.kind, claim.key, reason, exc)
    state.finish_failed(claim, reason, DEFER_BACKOFF_S, now=now)
    log.info("graph_sync: %s %s waits: %s", claim.kind, claim.key, reason)
    entry["outcome"] = DEFERRED
    return entry


def _fail(claim, error, entry: dict, *, now: datetime) -> dict:
    state.finish_failed(claim, error, state.backoff_s(claim.kind), now=now)
    entry.update(outcome=FAILED, error=_text(error))
    return entry


def drift_reported(run_dir: str) -> list[str] | None:
    """The failed checks of a ``--drift`` child that ran to its end and found drift, read from the result it saved in
    ``run_dir`` before exiting 1; None when there is no such result, so an exit 1 that is a crash stays a failure."""
    try:
        with open(os.path.join(run_dir, drift.RESULT_FILE), encoding="utf-8") as fh:
            result = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(result, dict) or result.get("status") != drift.DRIFT:
        return None
    return [str(c.get("name", "?")) for c in result.get("checks") or [] if isinstance(c, dict) and not c.get("pass")]


def _child(claim, opts: Options, entry: dict, *, now: datetime, launch) -> dict:
    run_dir = run_dir_for(opts.run_root, claim.kind, now, row_id=claim.id)
    timeout_s = child_timeout_s(claim.kind)
    code = launch(child_argv(claim.kind, run_dir, opts), timeout_s)
    entry.update(run_dir=run_dir, exit=code, refused=code == 2)
    found = drift_reported(run_dir) if claim.kind == "drift" and code == DRIFT_FOUND_EXIT else None
    if found is not None:
        # The check did its job: it reports and does not repair, so a retry would only find the same drift, and the
        # row would age the outbox past its threshold and die at the attempt limit (_SATISFYING_STATUSES).
        state.finish_done(claim, now=now)
        entry.update(outcome=DONE, drift=found)
        log.warning("graph_sync: the drift check found drift in %s; its run record and %s say what",
                    ", ".join(found) or "no named check", os.path.join(run_dir, drift.RESULT_FILE))
        return entry
    if code in (0, 2):
        state.finish_done(claim, now=now)
        entry["outcome"] = DONE
        if code == 2:
            log.info("graph_sync: the %s run refused this graph and wrote nothing; its run record says why",
                     claim.kind)
        return entry
    reason = (f"manage.py graph_sync --{claim.kind} did not finish within {timeout_s} s"
              if code is None else f"manage.py graph_sync --{claim.kind} exited {code}")
    return _fail(claim, reason, entry, now=now)


def _refused(claim, exc, entry: dict, *, now: datetime) -> dict:
    """A run that refused before writing: deferred when the lock or the graph's version refused it, failed when it
    was the data (a SampleType title held twice, say), which the next attempt would meet again."""
    problems = " ".join(getattr(exc, "problems", None) or [])
    version = (getattr(exc, "report", None) or {}).get("graph_schema_version")
    if LOCK_REFUSAL in problems or (version is not None and version != writer.SCHEMA_VERSION):
        return _defer(claim, f"{claim.kind} {claim.key}: {_text(exc)}", entry, now=now)
    return _fail(claim, exc, entry, now=now)


def _drain_one(driver, db, claim, opts: Options, *, now: datetime, launch) -> dict:
    entry = {"kind": claim.kind, "key": claim.key}
    if claim.kind in CHILD_KINDS:
        return _child(claim, opts, entry, now=now, launch=launch)
    try:
        result = _apply(driver, db, claim, opts, run_dir_for(opts.run_root, DRAIN_DIR_KIND, now))
    except run.PreflightError as exc:
        return _refused(claim, exc, entry, now=now)
    except Exception as exc:                       # noqa: BLE001
        log.exception("graph_sync: %s %s failed", claim.kind, claim.key)
        return _fail(claim, exc, entry, now=now)
    status = (result or {}).get("status")
    entry["status"] = status
    if status in DEFER_STATUSES:
        return _defer(claim, f"{claim.kind} {claim.key}: {status}", entry, now=now)
    if status not in (targeted.OK, "dry_run"):
        return _fail(claim, f"{claim.kind} {claim.key}: {status}", entry, now=now)
    state.finish_done(claim, now=now)
    entry["outcome"] = DONE
    return entry


# --- one pass, and the loop -----------------------------------------------------------------------

def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def run_pass(driver, db, worker_id: str, *, opts: Options | None = None, now: datetime | None = None,
             launch=None) -> dict:
    """One pass: housekeeping, the schedule, then the drain until nothing is claimable. Returns its report.

    ``launch`` runs one child (``launch_child`` by default) and answers its exit status, or None for a timeout.
    """
    opts = opts or Options()
    now = now or dj_timezone.now()
    launch = launch or launch_child
    report = {"worker_id": worker_id, "started_at": _iso(now), "drained": [],
              "counts": {DONE: 0, DEFERRED: 0, FAILED: 0}}
    report["runs_abandoned"] = state.reap_abandoned(now=now)
    report["run_dirs_pruned"] = prune_run_dirs(opts.run_root, keep=opts.keep_run_dirs)
    report["slots_enqueued"] = _schedule(opts, now)

    version = writer.graphmeta(driver, db).get("schema_version")
    at_version = version == writer.SCHEMA_VERSION
    report.update(graph_schema_version=version, writer_version=writer.SCHEMA_VERSION, at_writer_version=at_version)
    if not at_version:
        log.info("graph_sync: the graph is at schema version %r, not %s; until the operator's first full sync at "
                 "%s the loop runs %s only and every other row waits",
                 version, writer.SCHEMA_VERSION, writer.SCHEMA_VERSION, ", ".join(READ_ONLY_KINDS))

    kinds = None if at_version else list(READ_ONLY_KINDS)
    for _ in range(MAX_ROWS_PER_PASS):
        claim = state.claim_next(worker_id, now=now, kinds=kinds)
        if claim is None:
            break
        entry = _drain_one(driver, db, claim, opts, now=now, launch=launch)
        report["drained"].append(entry)
        report["counts"][entry["outcome"]] += 1
    report["finished_at"] = _iso(dj_timezone.now())
    return report


def run_forever(driver, db, worker_id: str, *, opts: Options | None = None,
                interval_s: float = DEFAULT_INTERVAL_S, launch=None, sleep=time.sleep) -> None:
    """Pass after pass, for ever (module docstring). Only a signal ends it."""
    opts = opts or Options()
    log.info("graph_sync: the sync loop drains as %s every %s s, run root %s", worker_id, interval_s, opts.run_root)
    while True:
        wait = interval_s
        try:
            report = run_pass(driver, db, worker_id, opts=opts, launch=launch)
            if report["drained"]:
                log.info("graph_sync: pass %s", report["counts"])
        except Exception:                          # noqa: BLE001
            # Swallowed on purpose, as the assay-registration drain does: a pass that failed must not stop every
            # later pass, and the outbox rows it did not reach are still there.
            log.exception("graph_sync: the sync pass failed; the loop goes on")
            wait = max(interval_s, ERROR_INTERVAL_S)
        sleep(wait)
