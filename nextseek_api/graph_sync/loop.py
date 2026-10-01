"""The sync loop: the schedule, the outbox drain, and the heavy runs as child processes (the sync design, 12, 13).

``manage.py graph_sync --loop`` is one pass after another, for ever; ``--once`` is one pass. A pass:

1. **housekeeping**: ends the runs whose process died (``state.reap_abandoned``) and deletes all but the newest
   ``KEEP_RUN_DIRS`` run directories of each kind the loop writes, and of ``--small-tables``;
2. **the schedule**: ``schedule.due_slots`` says which scheduled runs are owed, and each becomes one outbox row.
   ``(kind, key)`` is unique, so a slot already there is left exactly as it is: the schedule asks, the outbox
   decides, and a slot missed while the loop was down runs once at the next pass, not once per day it missed;
3. **the drain**: claim the oldest claimable row and apply it. ``samples``, ``samples_of_type``, ``retire``,
   ``catalog``, ``assay_map``, ``protocol_map``, ``isa`` and ``membership`` run in this process, through the same
   by-id entry points every other path uses. ``full``, ``reconcile`` and ``drift`` run as child
   ``manage.py graph_sync`` processes, so their memory returns when they end and a crash cannot kill the loop.
   A claimed single-sample ``samples`` row (key ``sample:<id>``) takes up to ``writer.SAMPLE_CHUNK - 1`` more such
   rows with it into ONE by-id sync, and that sync's outcome closes, defers or fails every row it drained; a
   ``batch:`` row is one sync of its own. Every drained row counts toward ``MAX_ROWS_PER_PASS``. A row claimed
   ``ALONE_AFTER_ATTEMPTS`` times since it was last written drains alone, so one sample whose sync raises cannot keep
   failing the rows merged with it; a row whose last failure was a gap traced to it stays mergeable
   (``TRACED_GAP_ERROR``), and so does a row waiting for the catalog (``TITLE_CONFLICT_DEFERRAL``). A structural
   link a by-id sync left unwritten fails only the samples its report names: such a sample's own row fails, a row of
   many samples (a batch, a sample type) is closed and hands each such sample on as a ``sample:<id>`` row of its own
   that keeps the row's attempts, failing time and back-off (``state.hand_on_failed``), and every other row is done.
   A sample the sync left out because its type's SampleType node cannot be written yet (``catalog_waiting_samples``)
   defers every row holding it, and only those.

**A graph below the writer's schema version is only read.** Until the operator's first ``graph_sync --full`` at 1.2,
the loop claims nothing but the read-only drift check: the writing rows wait in the outbox, unclaimed, with their
attempts untouched, and the loop never turns a 1.1 graph into a 1.2 one by itself (the design, section 12; R2).

**Work it could not do is put back, not punished.** A claim counts an attempt and a row dies at
``state.MAX_ATTEMPTS``, so for a row drained in this process the two outcomes that are not the row's fault, the
graph-write lock being held by another graph_sync write and a graph below the writer's version, are *deferred*: the
row is re-enqueued, which resets its attempts, and released with a short back-off (``DEFER_BACKOFF_S``). So is a
row whose samples wait for SampleType titles held under other ids to clear, which the nightly reconcile does, with a
longer back-off (``TITLE_CONFLICT_BACKOFF_S``): a by-id sync leaves those samples out and names them, and a
``catalog`` row whose catalog sync is refused for those titles alone waits whole. A real failure backs off by the
kind's own ``state.backoff_s`` with its attempt counted.

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
from django.db import DatabaseError, connections
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
MERGED_KIND = "samples"                # the one kind whose single-sample rows the drain merges
MERGED_KEY_PREFIX = "sample:"          # the key of a single-sample row; a batch row is never merged
# A single-sample row claimed this many times since it was last written drains alone, and no other row takes it in:
# a sync that raises on one sample fails every row merged with it, and the group, claimable again at one moment, would
# otherwise re-merge until all of it was dead. A transient failure costs one merged retry.
ALONE_AFTER_ATTEMPTS = 2
# The start of the last_error of a row a structural gap failed, the gap traced to that row's own sample. Such a row
# stays mergeable whatever its attempts: a merged sync fails each gapped sample on its own row and closes the rest, so
# merging it cannot fail a healthy row, and isolating it would cost one sync per gapped sample, ahead of every fresh
# write. An untraced gap names every sample of its chunk, healthy ones too, so its rows are isolated as a raise is.
TRACED_GAP_ERROR = "structural links left unwritten: "
# The start of the last_error of a row deferred because SampleType titles are held under other ids in the graph
# (``run.only_title_conflicts``: a type recreated in SEEK under its old title), so the catalog cannot be written: a
# by-id sync left one of its samples out (``catalog_waiting_samples``), or its catalog sync was refused for that alone.
# The nightly reconcile clears it: it retires the graph-only samples holding the old title and writes the catalog
# again. So the row is deferred, not failed: no attempt counted, never dead, never failing. It waits
# TITLE_CONFLICT_BACKOFF_S and stays mergeable: a by-id sync names the samples it left out, so a waiting row merged
# with fresh writes defers only itself, and the waiting rows come back together as one sync, not one each.
TITLE_CONFLICT_DEFERRAL = "waiting for SampleType titles held under other ids to clear: "
TITLE_CONFLICT_BACKOFF_S = 30 * 60

# Closes every Django connection before each pass. Django refreshes connections only around a web request, so a
# loop that lives for days keeps each one until MySQL drops it for idling, and every drain on it then fails with
# "Server has gone away" (2006) and waits out its backoff. Closing them all costs one reconnect per pass, whatever
# CONN_MAX_AGE a box sets.
refresh_connections = connections.close_all

DONE, DEFERRED, FAILED = "done", "deferred", "failed"

CHILD_KINDS = ("reconcile", "full", "drift")          # run as child processes
READ_ONLY_KINDS = ("drift",)                          # the kinds a graph below the writer's version still allows
LABEL_CHANGE_KINDS = ("full", "reconcile")            # the children that take --apply-label-changes
DRAIN_DIR_KIND = "drain"                              # the run directory the in-process kinds archive into
SMALL_TABLES_DIR_KIND = "small_tables"                # graph_sync --small-tables, before every local and dev drift
PRUNED_KINDS = CHILD_KINDS + (DRAIN_DIR_KIND, SMALL_TABLES_DIR_KIND)

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

    The row id is what keeps two children of one pass apart: ``run_pass`` names every directory of a pass from the
    pass start, so two slots of one kind drained in the same pass would otherwise share a directory, write over each
    other's files, and a drift child that saved nothing would be judged on the other one's result
    (``drift_reported``)."""
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
    """Delete all but the newest ``keep`` run directories of each kind the loop writes, and of the small-tables write
    the startup CLI runs before every local and dev drift, and return how many went.

    A directory of any other name, a hand run's ``graph_sync-<UTC time>`` among them, is left alone: the loop
    deletes only what it made, and those.
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


def _merges(claim) -> bool:
    """Whether the drain merges more rows into this one: a single-sample ``samples`` row."""
    return claim.kind == MERGED_KIND and claim.key.startswith(MERGED_KEY_PREFIX)


def _apply(driver, db, claim, opts: Options, run_dir: str, merged=()) -> dict:
    """Do what one row asks, in this process, and return the entry point's report. ``merged`` are more
    single-sample rows claimed with a ``samples`` row: their ids join its one by-id sync, in claim order."""
    kind = claim.kind
    if kind == "samples":
        ids = list(dict.fromkeys(i for c in (claim, *merged) for i in _ids_of(c)))
        return targeted.sync_samples(driver, db, ids, run_dir=run_dir,
                                     apply_label_changes=opts.apply_label_changes)
    if kind == "samples_of_type":
        return targeted.sync_samples_of_type(driver, db, int(claim.key.split(":", 1)[1]), run_dir=run_dir,
                                             apply_label_changes=opts.apply_label_changes)
    if kind == "retire":
        return targeted.retire_samples(driver, db, _ids_of(claim), run_dir=run_dir)
    if kind == "catalog":
        return run.catalog_sync(driver, db, record=opts.record, trigger=opts.trigger, run_dir=run_dir)
    if kind in ("assay_map", "protocol_map"):
        return targeted.relabel_for_maps(driver, db, apply_label_changes=opts.apply_label_changes)
    if kind in ("isa", "membership"):
        return targeted.sync_small_tables(driver, db, run_dir=run_dir)
    raise ValueError(f"the drain has no entry point for outbox kind {kind!r}")


def _text(error) -> str:
    return f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)


def _put_back(c, reason: str, backoff_s: float, *, now: datetime) -> None:
    """Put one claimed row back as it was: pending, no attempt counted against it, ``reason`` its ``last_error`` and a
    back-off of ``backoff_s``.

    ``finish_failed`` alone would keep the attempt the claim counted, and ``state.MAX_ATTEMPTS`` of them leave the
    row dead with its work never done. Re-enqueueing resets the attempts; the back-off then releases the claim."""
    try:
        state.enqueue(c.kind, c.key, c.payload, now=now)
    except (DatabaseError, ValueError) as exc:
        log.warning("graph_sync: could not put %s %s back after %s: %s", c.kind, c.key, reason, exc)
    state.finish_failed(c, reason, backoff_s, now=now, failure=False)


def _defer(claim, reason: str, entry: dict, *, now: datetime, merged=(), backoff_s: float = DEFER_BACKOFF_S) -> dict:
    """Put the row back (``_put_back``) with a back-off of ``backoff_s``, short by default; the rows ``merged`` into
    it go back the same way."""
    for c in (claim, *merged):
        _put_back(c, reason, backoff_s, now=now)
    log.info("graph_sync: %s %s waits: %s", claim.kind, claim.key, reason)
    entry["outcome"] = DEFERRED
    return entry


def _fail(claim, error, entry: dict, *, now: datetime, merged=()) -> dict:
    """Back the row off by its kind's back-off with its attempt counted; the rows ``merged`` into it too, each keeping
    its own attempts and ``failing_since``."""
    for c in (claim, *merged):
        state.finish_failed(c, error, state.backoff_s(c.kind), now=now)
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


def _child(claim, opts: Options, entry: dict, *, started: datetime, clock, launch) -> dict:
    run_dir = run_dir_for(opts.run_root, claim.kind, started, row_id=claim.id)
    timeout_s = child_timeout_s(claim.kind)
    code = launch(child_argv(claim.kind, run_dir, opts), timeout_s)
    # The child can have run for hours in a blocking call, and the loop's own connections sat idle all that time:
    # start the rest of this pass on fresh ones, whatever the child's outcome.
    refresh_connections()
    # The child's outcome is stamped when it ended, not when it was claimed: a child that ran for hours and failed
    # has been failing since now, and its back-off runs from now.
    now = clock()
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


def _refused(claim, exc, entry: dict, *, now: datetime, merged=()) -> dict:
    """A run that refused before writing: deferred when the lock or the graph's version refused it, deferred for
    longer when a catalog sync met SampleType titles held under other ids and nothing else
    (``TITLE_CONFLICT_DEFERRAL``; a ``catalog`` row, since a by-id sync leaves out the samples such a refusal holds
    back and names them instead), failed when it was any other problem of the data (a label collision, say), which
    the next attempt would meet again."""
    problems = list(getattr(exc, "problems", None) or [])
    report = getattr(exc, "report", None) or {}
    version = report.get("graph_schema_version")
    if LOCK_REFUSAL in " ".join(problems) or (version is not None and version != writer.SCHEMA_VERSION):
        return _defer(claim, f"{claim.kind} {claim.key}: {_text(exc)}", entry, now=now, merged=merged)
    if run.only_title_conflicts(problems, report):
        return _defer(claim, f"{TITLE_CONFLICT_DEFERRAL}{_text(exc)}", entry, now=now, merged=merged,
                      backoff_s=TITLE_CONFLICT_BACKOFF_S)
    return _fail(claim, exc, entry, now=now, merged=merged)


def _gap_error(row: str, why: str) -> str:
    """The ``last_error`` of a row a structural gap failed: it starts with ``TRACED_GAP_ERROR`` only when the gap was
    traced to the row's own sample, which keeps the row mergeable."""
    if targeted.UNTRACED_MARK in why:
        return f"{row}: structural links left unwritten, not traced to it: {why}"
    return f"{TRACED_GAP_ERROR}{row}: {why}"


def _may_merge(claim) -> bool:
    """Whether more single-sample rows join this claim's sync: a single-sample ``samples`` row claimed at most
    ``ALONE_AFTER_ATTEMPTS`` times, or one whose last failure was a gap traced to it (``TRACED_GAP_ERROR``). A row
    waiting for the catalog was put back with no attempt, so it merges."""
    return _merges(claim) and (claim.attempts <= ALONE_AFTER_ATTEMPTS
                               or (claim.last_error or "").startswith(TRACED_GAP_ERROR))


def _waiting_for_catalog(rows, waiting: dict[int, str], entry: dict, *, now: datetime, label: str) -> list:
    """Defer every row of ``rows`` holding a sample the sync left out because its type's SampleType node cannot be
    written while SampleType titles are held under other ids (``waiting``, id to why), and return the others. A
    ``samples`` row is deferred when it holds such a sample, a ``batch:`` row whole; a sample type's row when the sync
    named any. Deferred as ``_defer`` does, with ``TITLE_CONFLICT_DEFERRAL`` and its back-off: no attempt counted,
    never failing."""
    rest, deferred = [], 0
    for c in rows:
        held = [i for i in _ids_of(c) if i in waiting] if c.kind == MERGED_KIND else sorted(waiting)
        if not held:
            rest.append(c)
            continue
        _put_back(c, f"{TITLE_CONFLICT_DEFERRAL}{c.kind} {c.key}: {waiting[held[0]]}", TITLE_CONFLICT_BACKOFF_S,
                  now=now)
        deferred += 1
    shown = "; ".join(f"{i}: {why}" for i, why in sorted(waiting.items())[:targeted.EXAMPLES])
    log.info("graph_sync: %s left %d samples out until SampleType titles held under other ids clear, and %d rows "
             "holding them wait: %s", label, len(waiting), deferred, shown)
    entry.update(outcome=DEFERRED, rows_deferred=deferred, samples_waiting=len(waiting))
    return rest


def _gapped(claim, result: dict, entry: dict, *, now: datetime, merged=(), label: str) -> dict:
    """A sync that left a structural link unwritten (``targeted.STRUCTURAL_GAP_KEYS``) is not done for the samples
    its report names in ``structural_gap_samples``, and is for every other one. A single-sample row of a named sample
    fails on its own back-off, its ``last_error`` saying why (the project ids SEEK lacks, say); a row of many samples
    (a ``batch:`` row, a sample type) is closed and hands each named sample in it on as a ``sample:<id>`` row that
    failed as it did (``state.hand_on_failed``); every other row is done. So a gap that never heals retries, shows in
    the health line and dies at the attempt limit one sample at a time, and the healthy samples it rode with are not
    synced again for it. A report that names no sample fails every row it drained, as one failure."""
    named = {int(k): str(v) for k, v in (result.get("structural_gap_samples") or {}).items()}
    if not named:
        parts = ", ".join(f"{k} {v}" for k, v in sorted((result.get("structural_gap_parts") or {}).items()))
        _fail(claim, f"{claim.kind} {label}: {result['structural_gaps']} structural links left unwritten ({parts})",
              entry, now=now, merged=merged)
        entry["rows_failed"] = 1 + len(merged)
        return entry
    rows_failed = handed_on = 0
    for c in (claim, *merged):
        ids = _ids_of(c) if c.kind == MERGED_KIND else sorted(named)
        gapped = [i for i in ids if i in named]
        if not gapped:
            state.finish_done(c, now=now)
        elif _merges(c):
            state.finish_failed(c, _gap_error(f"{c.kind} {c.key}", named[gapped[0]]), state.backoff_s(c.kind),
                                now=now)
            rows_failed += 1
        else:
            handed_on += state.hand_on_failed(
                c, MERGED_KIND,
                {f"{MERGED_KEY_PREFIX}{i}": _gap_error(f"{MERGED_KIND} {MERGED_KEY_PREFIX}{i} (from {c.kind} {c.key})",
                                                       named[i]) for i in gapped},
                state.backoff_s(MERGED_KIND), now=now)
            state.finish_done(c, now=now)
    shown = "; ".join(f"{i}: {why}" for i, why in sorted(named.items())[:targeted.EXAMPLES])
    log.warning("graph_sync: %s %s left structural links unwritten for %d samples, which fail on rows of their own: "
                "%s", claim.kind, label, len(named), shown)
    entry.update(outcome=FAILED, rows_failed=rows_failed, handed_on=handed_on, samples_failed=len(named))
    return entry


def _drain_one(driver, db, claim, opts: Options, *, now: datetime, launch, started: datetime | None = None,
               clock=None, merged=()) -> dict:
    """Drain one claimed row, with the single-sample rows ``merged`` into it: one by-id sync over all their
    ids, and every row closed, deferred or failed with that sync's outcome, except the samples a sync left out for
    the catalog, which defer only the rows holding them (``_waiting_for_catalog``), and a structural gap, which fails
    only the samples it names (``_gapped``). ``now`` is the time of the claim and
    stamps an in process row's outcome; ``started`` (the pass start, ``now`` when not given) names the run
    directories, so a pass makes one drain directory; ``clock`` gives the time a child ended (``now`` when not
    given). The entry's ``rows`` is how many outbox rows it drained."""
    started = started or now
    clock = clock or (lambda: now)
    merged = tuple(merged)
    entry = {"kind": claim.kind, "key": claim.key, "rows": 1 + len(merged)}
    if claim.kind in CHILD_KINDS:
        return _child(claim, opts, entry, started=started, clock=clock, launch=launch)
    label = claim.key if not merged else f"{claim.key} and {len(merged)} more single-sample rows"
    try:
        result = _apply(driver, db, claim, opts, run_dir_for(opts.run_root, DRAIN_DIR_KIND, started), merged)
    except run.PreflightError as exc:
        return _refused(claim, exc, entry, now=now, merged=merged)
    except Exception as exc:                       # noqa: BLE001
        log.exception("graph_sync: %s %s failed", claim.kind, label)
        return _fail(claim, exc, entry, now=now, merged=merged)
    status = (result or {}).get("status")
    entry["status"] = status
    if status in DEFER_STATUSES:
        return _defer(claim, f"{claim.kind} {label}: {status}", entry, now=now, merged=merged)
    if status not in (targeted.OK, "dry_run"):
        return _fail(claim, f"{claim.kind} {label}: {status}", entry, now=now, merged=merged)
    rows = [claim, *merged]
    waiting = {int(k): str(v) for k, v in (result.get("catalog_waiting_samples") or {}).items()}
    if waiting:
        rows = _waiting_for_catalog(rows, waiting, entry, now=now, label=label)
        if not rows:
            return entry
    if result.get("structural_gaps"):
        return _gapped(rows[0], result, entry, now=now, merged=rows[1:], label=label)
    for c in rows:
        state.finish_done(c, now=now)
    entry.setdefault("outcome", DONE)
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
    pinned = now is not None
    now = now or dj_timezone.now()
    started = now
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
    rows = 0
    while rows < MAX_ROWS_PER_PASS:
        # A child can run for hours, so a caller that pinned no time gets the clock as it is at each claim: a row a
        # write enqueued meanwhile must not be stamped (claim, failure time, back-off) with the pass's start.
        tick = now if pinned else dj_timezone.now()
        claim = state.claim_next(worker_id, now=tick, kinds=kinds)
        if claim is None:
            break
        merged = ()
        if _may_merge(claim):
            # Single-sample rows run as one by-id sync of up to SAMPLE_CHUNK ids, not one sync each. Merged
            # rows count toward MAX_ROWS_PER_PASS like any other. A row that has failed twice merges with nothing,
            # unless its last failure was a gap traced to it.
            limit = min(writer.SAMPLE_CHUNK - 1, MAX_ROWS_PER_PASS - rows - 1)
            merged = state.claim_more(worker_id, MERGED_KIND, MERGED_KEY_PREFIX, limit, now=tick,
                                      below_attempts=ALONE_AFTER_ATTEMPTS, or_last_error_prefix=TRACED_GAP_ERROR)
        entry = _drain_one(driver, db, claim, opts, now=tick, launch=launch, started=started,
                           clock=(lambda: now) if pinned else dj_timezone.now, merged=merged)
        report["drained"].append(entry)
        if "rows_failed" in entry or "rows_deferred" in entry:
            # A structural gap fails only the rows of the samples it names, and samples left out for the catalog
            # defer only the rows holding them; the rest of the rows it drained are done.
            failed, deferred = entry.get("rows_failed", 0), entry.get("rows_deferred", 0)
            report["counts"][FAILED] += failed
            report["counts"][DEFERRED] += deferred
            report["counts"][DONE] += entry["rows"] - failed - deferred
        else:
            report["counts"][entry["outcome"]] += entry["rows"]
        rows += entry["rows"]
    report["finished_at"] = _iso(dj_timezone.now())
    return report


def run_forever(driver, db, worker_id: str, *, opts: Options | None = None,
                interval_s: float = DEFAULT_INTERVAL_S, launch=None, sleep=time.sleep, refresh=None) -> None:
    """Pass after pass, for ever (module docstring). Only a signal ends it.

    Every pass starts on fresh database connections (``refresh``, ``refresh_connections`` by default).
    """
    opts = opts or Options()
    refresh = refresh or refresh_connections
    log.info("graph_sync: the sync loop drains as %s every %s s, run root %s", worker_id, interval_s, opts.run_root)
    while True:
        wait = interval_s
        try:
            refresh()
            report = run_pass(driver, db, worker_id, opts=opts, launch=launch)
            if report["drained"]:
                log.info("graph_sync: pass %s", report["counts"])
        except Exception:                          # noqa: BLE001
            # Swallowed on purpose, as the assay-registration drain does: a pass that failed must not stop every
            # later pass, and the outbox rows it did not reach are still there.
            log.exception("graph_sync: the sync pass failed; the loop goes on")
            wait = max(interval_s, ERROR_INTERVAL_S)
        sleep(wait)
