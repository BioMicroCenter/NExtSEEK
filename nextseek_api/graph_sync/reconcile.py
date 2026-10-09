"""The nightly targeted sync (the sync design, section 10.3; C-09).

What no NExtSEEK function sees, a night at a time: the SEEK Rails UI and REST API, Rails jobs, hand SQL and the
operator's scripts all write MySQL without a hook, and a lost outbox row leaves nothing behind either. This run finds
those changes by content, not by a watermark (section 10.1: ``samples.updated_at`` has no ``ON UPDATE`` default, the
link tables have no timestamp and a delete leaves no trace), and applies them through the same by-id entry points
every other path uses.

**Its order** (section 10.3), each step its own write unit under the graph-write lock:

1. ``run.catalog_sync`` rebuilds the SampleType and Attribute catalog (about 6,000 rows) and the Assay nodes with
   their catalog edges;
2. ``targeted.sync_small_tables`` rewrites projects, investigations, people, memberships and every SEEK study's node;
3. ``targeted.relabel_for_maps`` relabels what a change to the resolved assay map or to ``sops`` affects, and does
   nothing when their digest still equals ``GraphMeta.label_maps_hash``;
4. ``targeted.sync_assays`` rewrites the members of every SEEK assay whose mapping moved, RUN_IN and the Assays gone
   from ``internal_assays`` (graph schema 1.3), which catches a mapping context_gen's SQL or SEEK's UI changed;
5. ``drift.detect_sample_drift`` merges MySQL's samples with the graph's ``source_hash`` values, so every change to a
   row's bytes, its type's title and value types, its project links or its assay links is found however it was made;
6. ``targeted.sync_samples`` for the changed and missing ids, ``targeted.retire_samples`` for the ids only the graph
   holds (the deletion rule, section 9);
7. the new-parent pass: when the changed samples carry uuids no node carried before, ``sources.samples_naming`` finds
   the older rows naming them and they are synced too, so a parent that arrived after its child gets its lineage.
   Above ``new_uuid_cap`` new uuids the pass is skipped and reported: the weekly full sync covers it.
8. ``study_links.rebuild_in_study`` makes every sample's IN_STUDY follow SEEK (an assay moved to another study in
   SEEK's UI or by hand SQL changes no source hash, so only this step sees it); it takes the lock per chunk, writes
   no outbox row and is outside the guard's count.

**The guard** (R9). When more than ``guard_fraction`` of the samples differ, the run stops before writing a single
sample, enqueues a ``full`` slot and returns ``guard_tripped``. A reconcile that would rewrite a fifth of the graph is
a graph the weekly full sync should rebuild, not a nightly one that should stream it. The ``study_links`` step does
not run then; the full sync the guard enqueued runs the same rule.

**Refusals.** A graph that is not at the writer's schema version is refused before anything runs (``not_at_version``),
as in ``targeted``. A step that cannot take the graph-write lock stops the run (``lock_timeout``, ``stopped_at``), the
catalog step included; a catalog sync that refuses for any other reason gives ``refused`` with its ``problems``, and one
that raises gives ``failed``. Either way the small tables, the relabel and ``sync_assays``, which read no catalog, still
run, and detection and the sample steps, which build it, do not. Each of them leaves what earlier steps wrote, which is
correct in itself, and the next run finishes the rest.

One refusal is retried: a catalog refused for nothing but SampleType titles held under other ids. A type
deleted in SEEK with its samples, which no hook sees, and recreated under its old title leaves its old node holding
Sample nodes only the retire step removes. So detection and the guard run, the ids only the graph holds are retired,
and the catalog is tried once more (``catalog_retry``, the first refusal's problems in ``catalog_retried``); the run
goes on from the sample sync, or stops as ``refused`` when the conflicts stay.

``dry_run`` reads MySQL and the graph, writes nothing, takes no lock, touches no file and records no run: it reports
the same detection counts, whether the guard would trip and what it would sync.

The run is recorded in ``graph_sync_run`` (``record``, ``trigger``), best-effort as every run record, with the
highest ``samples.id`` and ``updated_at`` the detection saw as its watermark; the report is written to
``reconcile.json`` in the run directory, which also receives the archives the by-id syncs append.

Labels follow R14: only new labels, renames and filled protocols are written unless the operator approves more
(``apply_label_changes``), and the differences are counted per class and property in each step's report.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from django.utils import timezone as dj_timezone

from nextseek_api.graph_sync import drift, hooks, run, sources, state, study_links, targeted, writer

log = logging.getLogger(__name__)

REPORT_FILE = "reconcile.json"
LIST_CAP = 1_000            # longest id list copied into the report; the counts are exact
GUARD_FRACTION = 0.2        # above this share of differing samples the run schedules a full sync instead (R9)
NEW_UUID_CAP = 1_000        # above this many new uuids the new-parent pass is skipped (section 10.3, step 4)
GUARD_SLOT_SUFFIX = "-guard"

OK, NOT_AT_VERSION, LOCK_TIMEOUT = targeted.OK, targeted.NOT_AT_VERSION, targeted.LOCK_TIMEOUT
REFUSED, GUARD_TRIPPED, DRY_RUN, FAILED = "refused", "guard_tripped", "dry_run", "failed"
# What the catalog and small-tables steps tell the run: go on, retire then retry the catalog, or stop.
_GO, _RETRY, _STOP = "go", "retry", "stop"

# The detection's id lists, kept whole while the run syncs them and capped in the report.
_DETECTION_LISTS = ("changed_ids", "missing_in_graph_ids", "not_in_mysql_ids", "new_uuid_list")
# The detection counts the report carries at its top level, beside the capped detection itself.
_DETECTION_COUNTS = ("mysql_samples", "graph_samples", "changed", "missing_in_graph", "not_in_mysql", "new_uuids",
                     "untyped", "max_id", "max_updated_at")
# What a step that stopped the run carries into the report beside its status.
_STOP_KEYS = ("schema_version", "writer_version", "lock_timeout_s")
# Report details the run record keeps beside the scalar counts; the id lists stay in the run directory's report.
_RECORDED_DETAILS = ("timings_s", "problems", "new_parent_pass")


@dataclass(frozen=True)
class _Options:
    """One run's settings, so every step reads the same ones."""

    run_dir: str
    chunk: int
    dry_run: bool
    guard_fraction: float
    new_uuid_cap: int
    apply_label_changes: bool
    record: bool
    trigger: str


# --- plumbing ------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _timed(report: dict, name: str, fn, *args, **kwargs):
    log.info("reconcile: %s", name)
    started = time.monotonic()
    out = fn(*args, **kwargs)
    report["timings_s"][name] = round(time.monotonic() - started, 1)
    return out


def guard_slot_key(now: datetime | None = None) -> str:
    """The outbox key of the full sync the guard schedules: one slot per UTC day, told apart from the weekly slot so
    that neither coalesces with the other."""
    moment = now or datetime.now(timezone.utc)
    return f"slot:{moment.astimezone(timezone.utc).date().isoformat()}{GUARD_SLOT_SUFFIX}"


def _capped(found: dict) -> dict:
    """The detection as the report keeps it: every count exact, every id list cut to ``LIST_CAP``."""
    report = dict(found)
    for key in _DETECTION_LISTS:
        report[key] = list(found.get(key) or [])[:LIST_CAP]
    report["id_cap"] = LIST_CAP
    return report


def _step(report: dict, name: str, fn, *args, **kwargs) -> bool:
    """Run one write step, keep its counts under ``steps`` and say whether the run goes on."""
    result = _timed(report, name, fn, *args, **kwargs)
    report["steps"][name] = result
    status = result.get("status")
    if status == OK:
        return True
    report["status"] = status
    report["stopped_at"] = name
    report.update({key: value for key, value in result.items() if key in _STOP_KEYS})
    log.warning("reconcile: the %s step answered %r; the rest of the run is left to the next one", name, status)
    return False


def _counts(report: dict) -> dict:
    """What the run record keeps: the report's scalars and a few details, never its id lists."""
    counts = {key: value for key, value in report.items()
              if value is None or isinstance(value, (bool, int, float, str))}
    for key in _RECORDED_DETAILS:
        if key in report:
            counts[key] = report[key]
    return counts


def _write_report(report: dict) -> None:
    """Save the report beside the run's archives. A directory that cannot be written loses the file, never the run."""
    try:
        run._write_json(os.path.join(report["run_dir"], REPORT_FILE), report)
    except OSError as exc:
        log.warning("reconcile: could not write %s in %s: %s", REPORT_FILE, report["run_dir"], exc)


def _finish_record(handle, report: dict) -> None:
    if handle is None:
        return
    status = report.get("status")
    outcome = "ok" if status == OK else FAILED if status == FAILED else REFUSED
    handle.finish(outcome, counts=_counts(report), watermark_to=report.get("watermark_to"))


# --- the steps -----------------------------------------------------------------------------------

def _catalog(driver, db, report: dict, opts: _Options, name: str = "catalog") -> tuple[bool, bool]:
    """Run the catalog sync as the step ``name``. Returns whether it answered ``ok``, and whether it refused for
    nothing but title conflicts (``run.only_title_conflicts``). A refusal (``refused``, its ``problems``; a busy lock
    is ``lock_timeout``) or an error (``failed``, its ``error``) is recorded with ``stopped_at: name``."""
    try:
        return _step(report, name, run.catalog_sync, driver, db, record=opts.record, trigger=opts.trigger,
                     run_dir=opts.run_dir), False
    except run.PreflightError as exc:
        # A busy lock stops the run as it does at every other step, not as a refusal of this graph.
        status = LOCK_TIMEOUT if isinstance(exc, run.LockTimeout) else REFUSED
        report.update(status=status, stopped_at=name, problems=list(exc.problems))
        log.warning("reconcile: the %s step refused: %s", name, "; ".join(exc.problems))
        return False, status == REFUSED and run.only_title_conflicts(exc.problems, exc.report)
    except Exception as exc:  # noqa: BLE001 (recorded; the steps that read no catalog still run)
        report.update(status=FAILED, stopped_at=name, error=f"{type(exc).__name__}: {exc}")
        log.exception("reconcile: the %s step failed", name)
        return False, False


def _catalog_and_small_tables(driver, db, report: dict, opts: _Options) -> str:
    """The catalog, the small tables, the label maps and the assay layer: the whole of options D and E (section
    10.2), and graph schema 1.3's ``sync_assays``. Returns ``go`` when the run goes on to detection and the sample
    steps, which build the catalog (only after an ``ok`` catalog step), ``retry`` when the catalog refused for nothing
    but title conflicts (retire, then retry it once), and ``stop`` otherwise.

    The small tables, the relabel and ``sync_assays`` read no catalog, so they run whatever the catalog step
    answered: a catalog that refuses (``refused``, its ``problems``) or raises (``failed``, its ``error``) is recorded
    with ``stopped_at: "catalog"``, the three steps run, and the run then ends with the catalog's status. A busy lock
    at any step stops the run there, as it does at every other step."""
    catalog_ok, conflicts_only = _catalog(driver, db, report, opts)
    if report.get("status") == LOCK_TIMEOUT:
        return _STOP
    catalog_stop = {key: report[key] for key in ("status", "stopped_at") if key in report}
    if not _step(report, "small_tables", targeted.sync_small_tables, driver, db, run_dir=opts.run_dir):
        return _STOP
    if not _step(report, "relabel", targeted.relabel_for_maps, driver, db,
                 apply_label_changes=opts.apply_label_changes):
        return _STOP
    if not _step(report, "assays", targeted.sync_assays, driver, db, run_dir=opts.run_dir, chunk=opts.chunk):
        return _STOP
    report.update(catalog_stop)
    return _GO if catalog_ok else _RETRY if conflicts_only else _STOP


def _retire_then_retry_catalog(driver, db, report: dict, opts: _Options, gone: list[int]) -> bool:
    """The catalog refused for nothing but title conflicts. The ids only the graph holds are retired, and the
    catalog is tried once more as ``catalog_retry``. Returns whether the run goes on to the sample sync."""
    if gone and not _step(report, "retire", targeted.retire_samples, driver, db, gone, run_dir=opts.run_dir):
        return False
    return _catalog(driver, db, report, opts, name="catalog_retry")[0]


def _detect(driver, db, report: dict, opts: _Options) -> dict:
    """Merge MySQL with the graph's source hashes and put what differs into the report. Reads only."""
    cat = _timed(report, "build_catalog", run.build_catalog)
    found = _timed(report, "detection", drift.detect_sample_drift, driver, db, chunk=opts.chunk, cap=None, cat=cat)
    report["detection"] = _capped(found)
    report.update({key: found[key] for key in _DETECTION_COUNTS})
    report["watermark_to"] = f"id={found['max_id']} updated_at={found['max_updated_at']}"
    return found


def _guard(report: dict, opts: _Options, found: dict, now: datetime | None) -> bool:
    """The 20 percent guard (R9): whether the run goes on to write samples."""
    total = max(found["mysql_samples"], found["graph_samples"])
    differing = found["changed"] + found["missing_in_graph"] + found["not_in_mysql"]
    fraction = (differing / total) if total else 0.0
    report.update(differing=differing, differing_fraction=round(fraction, 6),
                  guard_tripped=fraction > opts.guard_fraction)
    if not report["guard_tripped"]:
        return True
    log.warning("reconcile: %d of %d samples differ (%.1f%%), above the %.1f%% guard; a full sync is scheduled "
                "instead and no sample is written", differing, total, fraction * 100, opts.guard_fraction * 100)
    if opts.dry_run:
        return False
    report["status"] = GUARD_TRIPPED
    report["full_sync_enqueued"] = hooks.enqueue("full", guard_slot_key(now))
    return False


def _new_parents(driver, db, report: dict, opts: _Options, found: dict, synced: list[int]) -> bool:
    """Section 10.3, step 4: the older rows naming a uuid no node carried before. Returns whether the run goes on."""
    summary = {"status": "none", "new_uuids": found["new_uuids"], "cap": opts.new_uuid_cap}
    report["new_parent_pass"] = summary
    uuids = found["new_uuid_list"]
    if not uuids:
        return True
    if found["new_uuids"] > opts.new_uuid_cap:
        summary.update(status="skipped",
                       reason=f"{found['new_uuids']} new uuids is above the cap of {opts.new_uuid_cap}, so the pass "
                              "over every sample's metadata is left to the weekly full sync")
        log.info("reconcile: %s", summary["reason"])
        return True
    naming = _timed(report, "samples_naming", sources.samples_naming, uuids, chunk=opts.chunk)
    extra = sorted(set(naming) - set(synced))
    summary.update(status=OK, naming_samples=len(naming), to_sync=len(extra), ids=extra[:LIST_CAP])
    if not extra or opts.dry_run:
        return True
    return _step(report, "new_parents", targeted.sync_samples, driver, db, extra, run_dir=opts.run_dir,
                 apply_label_changes=opts.apply_label_changes, chunk=opts.chunk)


def _study_links(driver, db, report: dict, opts: _Options) -> bool:
    """The last step: every sample's IN_STUDY follows SEEK (``study_links.rebuild_in_study``), removal as the box's
    switch says, archived in the run directory, the graph-write lock taken per chunk so the drain gets it in between.
    It writes IN_STUDY and Study nodes only, never a sample: it adds no outbox row, never counts toward the guard and
    never enqueues a full sync. A dry run puts the counts under ``study_links_preview`` and writes nothing."""
    remove = study_links.follows_seek()
    if opts.dry_run:
        report["study_links_preview"] = _timed(report, "study_links", study_links.rebuild_in_study, driver, db,
                                               remove=remove, run_dir=None, dry_run=True, lock=None)
        return True
    return _step(report, "study_links", study_links.rebuild_in_study, driver, db, remove=remove,
                 run_dir=opts.run_dir, lock="chunk")


def _reconcile(driver, db, report: dict, opts: _Options, now: datetime | None = None) -> None:
    """The run itself; every branch that stops it sets ``status``, and the happy path leaves it to the caller."""
    refusal = targeted._refusal(driver, db)
    if refusal is not None:
        report.update(refusal)
        return
    catalog = _GO if opts.dry_run else _catalog_and_small_tables(driver, db, report, opts)
    if catalog == _STOP:
        return
    if catalog == _RETRY:
        # Kept apart: the run is not refused unless the retry is too.
        report["catalog_retried"] = report.pop("problems")
        report.pop("status")
        report.pop("stopped_at")
    found = _detect(driver, db, report, opts)
    if not _guard(report, opts, found, now):
        return

    ids = sorted({*found["changed_ids"], *found["missing_in_graph_ids"]})
    gone = sorted(set(found["not_in_mysql_ids"]))
    report.update(samples_to_sync=len(ids), samples_to_retire=len(gone))
    if not opts.dry_run:
        if catalog == _RETRY and not _retire_then_retry_catalog(driver, db, report, opts, gone):
            return
        if ids and not _step(report, "samples", targeted.sync_samples, driver, db, ids, run_dir=opts.run_dir,
                             apply_label_changes=opts.apply_label_changes, chunk=opts.chunk):
            return
        if gone and catalog != _RETRY and not _step(report, "retire", targeted.retire_samples, driver, db, gone,
                                                    run_dir=opts.run_dir):
            return
    if not _new_parents(driver, db, report, opts, found, ids):
        return
    if not _study_links(driver, db, report, opts) or opts.dry_run:
        return
    # A study's node the sample steps or the IN_STUDY rebuild just emptied goes tonight, not at the next run (the
    # small tables deleted the ones already empty in step 2), so the drift after the reconcile reads none.
    _step(report, "seek_studies_gone_after_links", targeted.sync_gone_study_nodes, driver, db, run_dir=opts.run_dir)


# --- the entry point -----------------------------------------------------------------------------

def reconcile(driver, db, *, run_dir, chunk: int = writer.SAMPLE_CHUNK, dry_run: bool = False,
              guard_fraction: float = GUARD_FRACTION, new_uuid_cap: int = NEW_UUID_CAP,
              apply_label_changes: bool = False, record: bool = True, trigger: str = "command") -> dict:
    """Bring the graph up to date from MySQL without a full sync (module docstring). Returns the report.

    ``run_dir`` receives ``reconcile.json`` and the archives the by-id syncs append (``retired.tsv``,
    ``derived_from_undeclared_archive.tsv``). ``chunk`` is MySQL's page size and the sample write chunk;
    ``guard_fraction`` the share of differing samples above which the run schedules a full sync instead;
    ``new_uuid_cap`` the number of new uuids above which the new-parent pass is skipped; ``apply_label_changes`` the
    operator's approval of label changes (R14); ``record`` and ``trigger`` the ``graph_sync_run`` row.

    ``status`` is ``ok``, ``dry_run``, ``guard_tripped``, ``lock_timeout`` (with ``stopped_at``), ``not_at_version``
    or ``refused`` (with ``problems``); an error part way records the run as ``failed``, writes the report and is
    raised. Raises ValueError, before anything is read, on a chunk that is not positive, a guard fraction outside 0
    to 1 or a negative cap.
    """
    if chunk <= 0:
        raise ValueError(f"chunk must be positive, got {chunk}")
    if not 0 <= guard_fraction <= 1:
        raise ValueError(f"guard_fraction must be between 0 and 1, got {guard_fraction}")
    if new_uuid_cap < 0:
        raise ValueError(f"new_uuid_cap must not be negative, got {new_uuid_cap}")
    opts = _Options(run_dir=os.path.abspath(run_dir), chunk=chunk, dry_run=dry_run, guard_fraction=guard_fraction,
                    new_uuid_cap=new_uuid_cap, apply_label_changes=apply_label_changes, record=record,
                    trigger=trigger)
    report = {"mode": "reconcile", "dry_run": dry_run, "chunk": chunk, "guard_fraction": guard_fraction,
              "new_uuid_cap": new_uuid_cap, "apply_label_changes": apply_label_changes,
              "schema_version": writer.SCHEMA_VERSION, "started_at": _now(), "timings_s": {}, "steps": {}}
    if dry_run:
        try:
            _reconcile(driver, db, report, opts)
            report.setdefault("status", DRY_RUN)
            return report
        finally:
            report["finished_at"] = _now()

    started = dj_timezone.now()
    report["started_at"] = _iso(started)
    report["run_dir"] = opts.run_dir
    handle = state.start_run("reconcile", trigger=trigger, now=started) if record else None
    try:
        os.makedirs(opts.run_dir, exist_ok=True)
        _reconcile(driver, db, report, opts, now=started)
        report.setdefault("status", OK)
        return report
    except Exception as exc:
        report["status"] = FAILED
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_at"] = _now()
        _write_report(report)
        _finish_record(handle, report)
