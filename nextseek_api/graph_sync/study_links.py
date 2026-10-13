"""IN_STUDY follows SEEK (docs/neo4j-schema.md, v1.2 "Study nodes and IN_STUDY").

For one sample, SEEK's set is the studies of the SEEK assays it is in (``sources.iter_seek_study_links``). A sample with
an IN_STUDY to a Study that has no ``seek_study_id`` (a graph-only paper study) is a paper sample. The rule:

- add: an IN_STUDY to the Study of every study in SEEK's set, except, for a paper sample, the studies of its paper's
  own investigation (matched by id and title, as the studies tool does, ``writer.paper_split``); when that
  investigation cannot be matched, every link is withheld and the sample is counted in
  ``paper_investigation_unknown``;
- remove: where the box's switch says ``follow`` (``follows_seek``), and always in ``graph_sync --studies``, its
  IN_STUDY to every Study whose ``seek_study_id`` is not in SEEK's set, paper samples included; a sample SEEK places
  in no study keeps its links and is reported;
- an IN_STUDY to a Study with no ``seek_study_id`` is never touched.

Only Sample nodes are read and written; IN_STUDY from any other node is counted apart. Every removal is appended to
``in_study_removed.tsv`` in the caller's run directory, and flushed, before its delete
(``writer.replace_seek_in_study``). The statements are in ``cypher.py``.

- ``follows_seek``: the per-box switch ``NEXTSEEK_GRAPH_SYNC_STUDY_LINKS``: ``follow`` turns removal on; any other
  value, unset included, reads as ``add``.
- ``seek_tables``: SEEK's studies, investigations, investigation projects and projects, read once per call.
- ``diff_in_study``: a read-only merge of the graph's keyset pages with SEEK's ordered links, one ``Difference`` per
  sample that breaks the rule, is a paper sample, or keeps links while SEEK places it in no study. A SEEK link of a
  sample with no node is skipped; a SEEK stream out of sample order raises.
- ``rebuild_in_study``: the step ``graph_sync --studies``, the full sync and the nightly reconcile run: a Study node for
  every SEEK study, with SEEK's title, description and investigation (its Investigation node written first), then the
  diff, then one write per ``REL_CHUNK`` samples that need one. It refuses a graph where two Study nodes share a
  ``seek_study_id`` and checks no schema version: the full sync runs it before it writes GraphMeta. It takes the
  graph-write lock only with ``lock="chunk"`` (once for the nodes, once per chunk, so the drain gets it in between);
  with ``lock=None`` its caller holds it. It records no run: its callers do.
- ``preview_in_study``: the rebuild's counts with the box's switch, read only (``graph_sync --full --dry-run``).
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from nextseek_api.graph_sync import sources, state, writer

log = logging.getLogger(__name__)

SWITCH_ENV = "NEXTSEEK_GRAPH_SYNC_STUDY_LINKS"
FOLLOW, ADD = "follow", "add"
ARCHIVE_FILE = "in_study_removed.tsv"
REPORT_FILE = "study_links.json"
OK, REFUSED, LOCK_TIMEOUT = "ok", "refused", "lock_timeout"
LOCK_WAIT_S = 60            # as long as a by-id write waits for the graph-write lock
LIST_CAP = 1_000
LOCK_MODES = (None, "chunk")
DIFF_COUNTS = ("samples_read", "samples_differing", "to_add", "to_remove", "paper_samples", "withheld",
               "paper_investigation_unknown", "kept_no_seek_study")
NODE_COUNTS = ("seek_studies", "seek_study_nodes_written", "seek_study_investigation_missing",
               "investigations_written", "investigation_links", "investigation_links_dropped",
               "investigation_ids_not_in_seek")


def follows_seek(env=None) -> bool:
    """Whether this box removes stale IN_STUDY links on its unattended paths: ``NEXTSEEK_GRAPH_SYNC_STUDY_LINKS`` is
    ``follow`` (case and surrounding whitespace aside)."""
    env = os.environ if env is None else env
    return str(env.get(SWITCH_ENV) or "").strip().lower() == FOLLOW


def switch_value(env=None) -> str:
    return FOLLOW if follows_seek(env) else ADD


def seek_tables() -> writer.SeekTables:
    """SEEK's small tables the Study layer is written from, read once per call (tens to hundreds of rows each)."""
    return writer.SeekTables(studies=tuple(sources.studies()), investigations=tuple(sources.investigations()),
                             investigation_projects=tuple(sources.investigation_projects()),
                             projects=tuple(sources.projects()))


@dataclass(frozen=True)
class Difference:
    """One sample the rule has something to say about. ``add`` holds SEEK's studies to link it to that it has no link
    to (a paper sample's other-investigation studies included), ``withheld`` a paper sample's SEEK studies the rule
    does not link (its paper's own investigation, or every one when that is ``investigation_unknown``), ``remove`` its
    links to SEEK-keyed Studies outside SEEK's set (none when SEEK places it in no study), each ``{"element_id",
    "seek_study_id", "id", "investigations"}``."""

    sample_id: int
    seek_study_ids: tuple
    add: tuple
    remove: tuple
    paper: bool
    no_seek_study: bool
    withheld: tuple = ()
    investigation_unknown: bool = False

    @property
    def breaks_rule(self) -> bool:
        return bool(self.add or self.remove)


class _SeekLinks:
    """SEEK's (sample id, study id) stream, taken one graph page at a time."""

    def __init__(self, rows):
        self._rows = iter(rows)
        self._pending = None
        self._last = None

    def take_through(self, last_id) -> dict:
        found: dict = {}
        while True:
            row, self._pending = self._pending, None
            if row is None:
                row = next(self._rows, None)
                if row is None:
                    return found
                sample_id = int(row[0])
                if self._last is not None and sample_id < self._last:
                    raise RuntimeError(f"SEEK's study links came back out of sample-id order ({sample_id} after "
                                       f"{self._last}); the IN_STUDY diff would lose links")
                self._last = sample_id
            if int(row[0]) > last_id:
                self._pending = row
                return found
            found.setdefault(int(row[0]), set()).add(int(row[1]))

    def close(self) -> None:
        close = getattr(self._rows, "close", None)
        if close is not None:
            close()


def _difference(sample_id, links, seek_ids, scope: writer.PaperScope) -> Difference | None:
    seek_links = [link for link in links if link["seek_study_id"] is not None]
    held = {link["seek_study_id"] for link in seek_links}
    split = writer.paper_split(links, seek_ids, scope)
    add = tuple(sorted(set(split.written) - held))
    if seek_ids:
        remove = tuple(link for link in seek_links if link["seek_study_id"] not in seek_ids)
        no_seek = False
    else:
        remove, no_seek = (), bool(seek_links)
    if not (add or remove or split.paper or no_seek):
        return None
    return Difference(sample_id, tuple(sorted(seek_ids)), add, remove, split.paper, no_seek, split.withheld,
                      split.paper and split.own is None)


def diff_in_study(driver, db, *, page: int = writer.HASH_PAGE, stats: dict | None = None,
                  scope: writer.PaperScope | None = None) -> Iterator[Difference]:
    """Every Sample the rule has something to say about, in id order. Read-only; memory holds one page. ``scope`` is
    read from SEEK's studies and investigations when not given."""
    stats = {} if stats is None else stats
    stats["samples_read"] = 0
    if scope is None:
        scope = writer.paper_scope(sources.studies(), sources.investigations())
    seek = _SeekLinks(sources.iter_seek_study_links())
    try:
        for rows in writer.sample_study_pages(driver, db, page=page):
            wanted = seek.take_through(rows[-1]["id"])
            stats["samples_read"] += len(rows)
            for row in rows:
                found = _difference(row["id"], row["studies"], wanted.get(row["id"], set()), scope)
                if found is not None:
                    yield found
    finally:
        seek.close()


@contextmanager
def _hold(lock, timeout_s):
    if lock is None:
        yield True
        return
    with state.graph_write_lock(timeout_s) as held:
        yield held


def _tally(report: dict, found: Difference) -> None:
    report["samples_differing"] += found.breaks_rule
    report["to_add"] += len(found.add)
    if found.paper:
        report["paper_samples"] += 1
        report["withheld"] += len(found.withheld)
        report["paper_investigation_unknown"] += found.investigation_unknown
    report["to_remove"] += len(found.remove)
    report["kept_no_seek_study"] += found.no_seek_study


def _needs_write(found: Difference, remove: bool) -> bool:
    return bool(found.add or (found.remove and remove))


def _stop(report: dict, lock_timeout_s, where: str) -> bool:
    report.update(status=LOCK_TIMEOUT, lock_timeout_s=lock_timeout_s, stopped_at=where)
    log.warning("study links: the graph-write lock was busy for %s s at %s; the next run finishes", lock_timeout_s,
                where)
    return False


def _write_nodes(driver, db, report, tables, lock, lock_timeout_s) -> bool:
    with _hold(lock, lock_timeout_s) as held:
        if not held:
            return _stop(report, lock_timeout_s, "study_nodes")
        report.update(writer.write_seek_study_nodes(driver, db, tables.studies, tables=tables))
    return True


def _flush(driver, db, report, rows, remove, archive, scope, lock, lock_timeout_s, path) -> bool:
    with _hold(lock, lock_timeout_s) as held:
        if not held:
            return _stop(report, lock_timeout_s, "in_study")
        part = writer.replace_seek_in_study(driver, db, rows, remove=remove, archive_path=archive, scope=scope,
                                            path=path)
    for key, value in part.items():
        report[key] = report.get(key, 0) + value
    return True


def _rebuild(driver, db, *, remove, run_dir, dry_run, lock, lock_timeout_s, path) -> dict:
    if lock not in LOCK_MODES:
        raise ValueError(f"lock must be None or 'chunk', got {lock!r}")
    report = {"status": OK, "remove": bool(remove), "dry_run": bool(dry_run), "archive_path": None,
              **dict.fromkeys(DIFF_COUNTS, 0), **dict.fromkeys(writer.IN_STUDY_COUNTS, 0),
              **dict.fromkeys(NODE_COUNTS, 0), "orphan_in_study": 0}
    duplicates = writer.seek_study_id_duplicates(driver, db)
    if duplicates:
        report.update(status=REFUSED, seek_study_id_duplicates=duplicates[:LIST_CAP],
                      problems=[f"{len(duplicates)} seek_study_id values are held by more than one Study node, so a "
                                "write keyed on seek_study_id would reach them all"])
        return report
    tables = seek_tables()
    scope = writer.paper_scope(tables.studies, tables.investigations)
    archive = os.path.join(os.path.abspath(run_dir), ARCHIVE_FILE) if run_dir else None
    if not dry_run and not _write_nodes(driver, db, report, tables, lock, lock_timeout_s):
        return report
    stats: dict = {}
    pending: list = []
    for found in diff_in_study(driver, db, stats=stats, scope=scope):
        _tally(report, found)
        if dry_run or not _needs_write(found, remove):
            continue
        pending.append({"sample_id": found.sample_id, "study_ids": list(found.seek_study_ids)})
        if len(pending) >= writer.REL_CHUNK:
            if not _flush(driver, db, report, pending, remove, archive, scope, lock, lock_timeout_s, path):
                return report
            pending = []
    if pending and not _flush(driver, db, report, pending, remove, archive, scope, lock, lock_timeout_s, path):
        return report
    report["samples_read"] = stats.get("samples_read", 0)
    report["orphan_in_study"] = writer.orphan_in_study(driver, db)
    if report["in_study_removed"]:
        report["archive_path"] = archive
    return report


def rebuild_in_study(driver, db, *, remove: bool, run_dir: str | None, dry_run: bool = False,
                     lock: str | None = None, lock_timeout_s: float = LOCK_WAIT_S, path: str = "rebuild") -> dict:
    """Apply the rule to every Sample (module docstring). ``remove`` deletes stale links, archived first to
    ``in_study_removed.tsv`` in ``run_dir``; ``dry_run`` reads and counts, writing nothing; ``lock`` is None (the
    caller holds the graph-write lock) or ``"chunk"`` (taken for the Study nodes and per chunk, waiting
    ``lock_timeout_s``); ``path`` names the caller in the archive. ``status``: ``ok``, ``refused`` (two Study nodes
    share a seek_study_id; nothing written) or ``lock_timeout`` (``stopped_at``; the next run finishes it)."""
    return _rebuild(driver, db, remove=remove, run_dir=run_dir, dry_run=dry_run, lock=lock,
                    lock_timeout_s=lock_timeout_s, path=path)


def preview_in_study(driver, db) -> dict:
    """``rebuild_in_study``'s counts with this box's switch, read only."""
    return _rebuild(driver, db, remove=follows_seek(), run_dir=None, dry_run=True, lock=None,
                    lock_timeout_s=LOCK_WAIT_S, path="preview")
