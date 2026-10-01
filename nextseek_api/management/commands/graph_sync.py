"""Keep the Neo4j sample graph in step with MySQL, or check that it is (graph schema v1.2).

    manage.py graph_sync (--full | --catalog | --reconcile | --samples IDS | --verify | --drift | --loop | --once
                          | --investigation-counts --instance {local,dev,prod} | --requeue-dead [--kind KIND]
                          | --labels | --merge-studies [IDS] | --unmerge-studies PATH[,PATH...] | --studies
                          | --small-tables)
                         [--json] [--dry-run] [--chunk N] [--run-dir PATH] [--run-root PATH] [--seed N]
                         [--bench-keys FILE] [--apply-label-changes] [--no-record] [--trigger NAME]
                         [--interval S] [--i-mean-the-live-graph]

| Mode | Does | Writes the graph |
|---|---|---|
| ``--full`` | the whole ordered sync (``run.full_sync``) | yes |
| ``--catalog`` | the SampleType and Attribute catalog only | yes |
| ``--reconcile`` | the nightly targeted sync: what changed since the graph was written | yes |
| ``--samples IDS`` | those samples by id, a comma-separated list | yes |
| ``--verify`` | gate G | no |
| ``--drift`` | the drift check: does the graph still equal MySQL | no |
| ``--loop`` | the schedule and the outbox drain, pass after pass, for ever (``graph_sync/loop.py``) | yes |
| ``--once`` | one pass of that loop | yes |
| ``--investigation-counts`` | every Investigation title with its nodes and samples, the counts file that ``scripts/context_gen.py --emit capabilities --counts`` reads; ``--instance`` names where it was measured and has no default | no |
| ``--requeue-dead`` | dead outbox rows back to pending, claimable at once (``--kind``, ``--dry-run``) | no |
| ``--labels`` | every DERIVED_FROM label against the rule, the whole graph (``run.relabel_all``): new ones, renames and filled protocols written, the rest counted unless approved | yes |
| ``--merge-studies [IDS]`` | merge each SEEK study of the approval line (``id:kind``, as the dry run prints it) or rekey its legacy node in place, held to its approved kind, journaled to ``study_merge.tsv``; bare, ``all`` or bare ids only with ``--dry-run`` | yes |
| ``--unmerge-studies PATH[,PATH...]`` | reverse those journals' merges and re-create the IN_STUDY links their archives hold; refused unless given every merge journal under the run root that names the same ids | yes |
| ``--studies`` | make every sample's IN_STUDY follow SEEK once, removal included whatever the switch says | yes |
| ``--small-tables`` | rewrite SEEK's small tables once, as an ``isa`` row and the nightly reconcile do | yes |

``--dry-run`` makes ``--full``, ``--catalog``, ``--reconcile``, ``--labels``, ``--merge-studies``,
``--unmerge-studies`` and ``--studies`` read without writing and print their counts, and ``--requeue-dead`` list the
rows it would put back; every other mode refuses it, exit 2, before connecting. A written ``--full`` and the
three study modes make their own run directory, ``<kind>-<UTC time>`` under the loop's run root, when ``--run-dir``
names none, and print it; ``--unmerge-studies`` saves its result there whether it ends ok, partial or part way.
``--apply-label-changes`` (``--full``, ``--reconcile``, ``--samples``, ``--labels``) is the operator's approval to
write the DERIVED_FROM labels that change which assay an edge carries, which are otherwise only counted (the sync
design, R14; a rename or a filled protocol is written without it); the loop takes that approval from
``NEXTSEEK_GRAPH_SYNC_LABEL_CHANGES=apply`` instead. ``--no-record`` keeps the run out of ``graph_sync_run``, and
``--trigger`` names who started it there (the loop passes ``loop``).

``--verify``, ``--drift``, ``--investigation-counts`` and ``--loop`` run against the live stack's Neo4j without
``--i-mean-the-live-graph``: the first three only read, and the loop is what the app container runs against its own
graph. Every other mode still
needs the flag by hand, and the loop passes it to its children. ``--requeue-dead`` writes the dmac outbox only and
opens no Neo4j connection at all.

Exit status: 0 on success; 1 when a check fails, a run failed part way, or ``--full``, ``--catalog`` or
``--reconcile`` could not take the graph-write lock, which another write held past its wait (the loop retries it);
2 on a refusal, which means nothing was written (``--dry-run`` given to a mode that does not honour it is one); 3
when ``--drift`` could not complete. ``--merge-studies``, ``--unmerge-studies`` and ``--studies`` exit 1 when they
stop part way or find the graph-write lock busy, and 2 on a refusal (the graph's version, an id the merge does not
act on, an approved id with no kind, an id that reads another kind than its approved one before anything was
written, two Study nodes sharing a ``seek_study_id``, a path that holds no journal). With ``--json`` stdout holds
only the JSON result; progress goes to stderr. The package, its modules and the graph it writes:
``nextseek_api/graph_sync/README.md`` and ``docs/neo4j-schema.md`` section "v1.2".
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from types import MappingProxyType
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from neo4j import GraphDatabase

from nextseek_api.graph_sync import (
    drift, loop, reconcile, run, state, study_links, study_merge, targeted, verify, writer,
)

# The live stack's Neo4j: its compose service and container name.
LIVE_NEO4J_HOSTS = frozenset({"neo4j"})
PROGRESS_LOGGER = "nextseek_api.graph_sync"

MODES = ("full", "catalog", "verify", "reconcile", "drift", "samples", "loop", "once", "investigation_counts",
         "requeue_dead", "labels", "merge_studies", "unmerge_studies", "studies", "small_tables")
# The modes that may reach the live graph without the flag: the three that only read, and the loop itself.
LIVE_OK_MODES = frozenset({"verify", "drift", "investigation_counts", "loop"})
LABEL_CHANGE_MODES = frozenset({"full", "reconcile", "samples", "labels"})
# The modes that honour --dry-run. Every other one refuses it before connecting: --samples, --small-tables and a pass
# of the loop would write anyway, and the read-only modes have nothing to leave out.
DRY_RUN_MODES = ("full", "catalog", "reconcile", "labels", "merge_studies", "unmerge_studies", "studies",
                 "requeue_dead")
DRIFT_FILE = drift.RESULT_FILE
TRIGGER_CHARS = 64

# A by-id sync that could not take the lock, like one refused for the graph's version, wrote nothing at all.
_SAMPLES_EXIT = MappingProxyType({targeted.OK: 0, targeted.NOT_AT_VERSION: 2, targeted.LOCK_TIMEOUT: 2})
_STUDIES_EXIT = MappingProxyType({study_links.OK: 0, study_links.LOCK_TIMEOUT: 1, study_links.REFUSED: 2})
# A merge that stopped before writing anything in this run (an id whose kind changed since its approval) is a
# refusal; one that stopped after merging an id, or found the lock busy, is part way.
_MERGE_EXIT = MappingProxyType({study_merge.OK: 0, study_merge.REFUSED: 2, study_merge.FAILED: 1, "lock_timeout": 1})
# A reconcile is several write units, so a lock lost part way is exit 1, not a refusal: earlier steps may have
# written, and the loop's child must be retried rather than marked done.
_RECONCILE_EXIT = MappingProxyType({reconcile.OK: 0, reconcile.DRY_RUN: 0, reconcile.GUARD_TRIPPED: 0,
                                    reconcile.LOCK_TIMEOUT: 1, reconcile.NOT_AT_VERSION: 2, reconcile.REFUSED: 2})


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be positive, got {value}")
    return value


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be positive, got {value}")
    return value


def _sample_ids(text: str) -> list[int]:
    """``--samples``: a comma-separated list of sample ids, in the order given and without repeats."""
    ids: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part.isdigit():
            raise argparse.ArgumentTypeError(f"--samples: not a sample id: {part!r}")
        value = int(part)
        if value not in ids:
            ids.append(value)
    if not ids:
        raise argparse.ArgumentTypeError("--samples: needs at least one sample id")
    return ids


def _merge_ids(text: str):
    """``--merge-studies``: ``all``, or the approval line the dry run prints, ``id:kind`` comma-separated
    (``study_merge.parse_approval``), in the order given and without repeats: ``{id: kind}``. A bare id maps to None,
    which only a dry run accepts."""
    text = (text or "").strip()
    if text.lower() == "all":
        return "all"
    try:
        return study_merge.parse_approval(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--merge-studies: {exc}") from exc


def _paths(text: str) -> list[str]:
    """``--unmerge-studies``: a comma-separated list of journals or run directories."""
    paths = [part.strip() for part in text.split(",") if part.strip()]
    if not paths:
        raise argparse.ArgumentTypeError("--unmerge-studies: needs at least one journal or run directory")
    return paths


def _trigger_name(text: str) -> str:
    name = text.strip()
    if not name or len(name) > TRIGGER_CHARS:
        raise argparse.ArgumentTypeError(f"--trigger: not a name of at most {TRIGGER_CHARS} characters: {text!r}")
    return name


def _text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _scalars(result: dict) -> dict:
    """The report's scalar counts, which a run record can hold; its id lists stay in the run directory."""
    return {k: v for k, v in result.items() if v is None or isinstance(v, (bool, int, float, str))}


def live_graph_refusal(config, allow_live: bool) -> str | None:
    """Why the command must not connect to ``config`` (``settings.NEO4J_DATABASE``), or None."""
    uri = (config or {}).get("URI")
    if not uri:
        return "settings.NEO4J_DATABASE names no URI, so there is no graph to connect to"
    host = urlsplit(uri).hostname
    if host is None:
        return "cannot read a host from settings.NEO4J_DATABASE['URI'], so cannot rule out the live graph"
    if host.lower() in LIVE_NEO4J_HOSTS and not allow_live:
        return (f"settings.NEO4J_DATABASE points at the live stack's Neo4j (host {host!r}); "
                "pass --i-mean-the-live-graph to run against it")
    return None


def load_bench_keys(path: str) -> frozenset:
    """A JSON list whose items are attribute keys ("<type id>:<title>") or [sample type, attribute] pairs."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CommandError(f"--bench-keys: cannot read {path}: {exc}") from exc
    if not isinstance(data, list):
        raise CommandError("--bench-keys: the file must hold a JSON list")
    keys = set()
    for item in data:
        if isinstance(item, str):
            keys.add(item)
        elif isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item):
            keys.add((item[0], item[1]))
        else:
            raise CommandError(f"--bench-keys: not an attribute key or a [sample type, attribute] pair: {item!r}")
    return frozenset(keys)


def _flag(mode: str) -> str:
    return mode.replace("_", "-")


def _mode(options: dict) -> str:
    """Which mode was asked for; argparse has already made sure exactly one was."""
    for name in MODES:
        if options.get(name):
            return name
    raise CommandError(f"one of {', '.join('--' + m for m in MODES)} is required")


class Command(BaseCommand):
    help = "Write graph schema v1.2 from MySQL to Neo4j, check it, or run the sync loop."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--full", action="store_true", help="Run the whole ordered sync.")
        mode.add_argument("--catalog", action="store_true", help="Rewrite the SampleType and Attribute catalog only.")
        mode.add_argument("--verify", action="store_true", help="Run gate G (read-only); exit 1 when a check fails.")
        mode.add_argument("--reconcile", action="store_true",
                          help="Run the nightly targeted sync: apply what changed since the graph was written.")
        mode.add_argument("--drift", action="store_true",
                          help="Check the graph against MySQL (read-only); exit 1 on drift, 2 on a refusal.")
        mode.add_argument("--samples", metavar="ID[,ID...]", type=_sample_ids,
                          help="Sync these samples by id, and retire an id MySQL no longer holds.")
        mode.add_argument("--loop", action="store_true",
                          help="Run the schedule and drain the outbox, pass after pass, for ever.")
        mode.add_argument("--once", action="store_true", help="Make one pass of that loop and exit.")
        mode.add_argument("--investigation-counts", action="store_true",
                          help="Print every Investigation title with its nodes and samples (read-only), as the "
                               "counts file scripts/context_gen.py --emit capabilities --counts reads.")
        mode.add_argument("--requeue-dead", action="store_true",
                          help="Put dead outbox rows (at the attempt limit) back to pending, claimable at once, "
                               "once their cause is fixed. Writes the dmac outbox only, never the graph.")
        mode.add_argument("--labels", action="store_true",
                          help="Classify every DERIVED_FROM label against the rule and write the new ones, the "
                               "renames and the filled protocols (every class with --apply-label-changes); for a "
                               "backlog the by-id and nightly paths do not reach.")
        mode.add_argument("--merge-studies", nargs="?", const="all", type=_merge_ids, metavar="IDS",
                          help="Merge each SEEK study of the approval line (id:kind, comma-separated, as the dry run "
                               "prints it) into one Study node, or give a legacy node its seek_study_id in place, "
                               "journaled to study_merge.tsv; an id that reads another kind than its approved one "
                               "stops the run. Bare, all, or ids without kinds only with --dry-run, which prints each "
                               "id's kind and the approval line: every id the merge acts on, with its kind.")
        mode.add_argument("--unmerge-studies", type=_paths, metavar="PATH[,PATH...]",
                          help="Reverse the merges journaled in these run directories or journals, and re-create "
                               "the IN_STUDY links their in_study_removed.tsv archives hold; its result is saved in "
                               "its own unmerge_studies-<UTC time> run directory.")
        mode.add_argument("--studies", action="store_true",
                          help="Make every sample's IN_STUDY follow SEEK once, removing stale links whatever "
                               f"{study_links.SWITCH_ENV} says, each archived to in_study_removed.tsv first.")
        mode.add_argument("--small-tables", action="store_true",
                          help="Rewrite SEEK's small tables once (projects, investigations, people, memberships and "
                               "every SEEK study's node), as an isa outbox row and the nightly reconcile do; a "
                               "refusal (the graph's version, a busy lock) wrote nothing and exits 2.")
        parser.add_argument("--kind", metavar="KIND",
                            help="--requeue-dead: only the dead rows of this outbox kind.")
        parser.add_argument("--instance", choices=drift.INSTANCES,
                            help="--investigation-counts: the instance this graph is (no default).")
        parser.add_argument("--json", action="store_true", help="Print the result as JSON on stdout.")
        parser.add_argument("--dry-run", action="store_true",
                            help="With --full, --catalog, --reconcile, --labels, --merge-studies, --unmerge-studies or "
                                 "--studies: read MySQL and the graph, write nothing, print the counts. With "
                                 "--requeue-dead: list the dead rows, change nothing. Any other mode refuses it, "
                                 "exit 2.")
        parser.add_argument("--chunk", type=_positive_int, default=writer.SAMPLE_CHUNK,
                            help="Samples per MySQL page and per write transaction (default %(default)s).")
        parser.add_argument("--run-dir", metavar="PATH",
                            help="--full and the three study modes: their run directory, for their report, journal "
                                 "and archives (default: <kind>-<UTC time> under the run root). --catalog, --verify, "
                                 "--drift, --reconcile and --samples also save their result there.")
        parser.add_argument("--run-root", metavar="PATH",
                            help="Where --reconcile, --samples, --small-tables, the loop, a written --full and the "
                                 "study modes make their own run directories (default: $GS_RUN_DIR, else graph_sync "
                                 "under the log directory); --unmerge-studies also refuses unless given every merge "
                                 "journal there that names one of its ids.")
        parser.add_argument("--seed", type=int, help="--verify, --drift: the seed of the random samples.")
        parser.add_argument("--bench-keys", metavar="FILE",
                            help="--full: a JSON list of attribute keys or [sample type, attribute] pairs the "
                                 "index budget must cover.")
        parser.add_argument("--apply-label-changes", action="store_true",
                            help="--full, --reconcile, --samples: also write the DERIVED_FROM labels that differ "
                                 "from the rule, which are otherwise only counted. The operator's approval, per "
                                 f"run; the loop reads {loop.LABEL_CHANGES_ENV}={loop.APPROVED} instead.")
        parser.add_argument("--no-record", action="store_true",
                            help="Do not write this run to graph_sync_run.")
        parser.add_argument("--trigger", type=_trigger_name, default="command",
                            help="Who started this run, as its run record keeps it (default %(default)s; the loop "
                                 "passes its own name to its children).")
        parser.add_argument("--interval", type=_positive_float, default=loop.DEFAULT_INTERVAL_S,
                            help="--loop: seconds between passes (default %(default)s).")
        parser.add_argument("--i-mean-the-live-graph", action="store_true",
                            help="Allow a Neo4j whose host is the live stack's (neo4j).")

    def handle(self, *args, **options):
        mode = _mode(options)
        if mode == "investigation_counts" and not options["instance"]:
            raise CommandError("--investigation-counts needs --instance local, dev or prod: a counts file that does "
                               "not say where it was measured cannot clear a name")
        if options["instance"] and mode != "investigation_counts":
            raise CommandError("--instance belongs to --investigation-counts")
        if options["apply_label_changes"] and mode not in LABEL_CHANGE_MODES:
            raise CommandError(
                "--apply-label-changes belongs to " + ", ".join(f"--{m}" for m in sorted(LABEL_CHANGE_MODES))
                + f"; the loop takes that approval from {loop.LABEL_CHANGES_ENV}={loop.APPROVED} instead")
        if mode == "merge_studies" and not options["dry_run"]:
            self._check_approval(options["merge_studies"])
        if options["kind"] is not None and mode != "requeue_dead":
            raise CommandError("--kind belongs to --requeue-dead")
        if options["dry_run"] and mode not in DRY_RUN_MODES:
            raise CommandError(f"--{_flag(mode)} has no --dry-run and would ignore it; refused, nothing done. "
                               "--dry-run belongs to " + ", ".join(f"--{_flag(m)}" for m in DRY_RUN_MODES)
                               + " (--drift reads what a write would change)", returncode=2)
        if mode == "requeue_dead":
            # The dmac outbox only: no Neo4j settings are read and no driver is opened.
            return self._requeue_dead(options)
        config = getattr(settings, "NEO4J_DATABASE", None)
        refusal = live_graph_refusal(config, options["i_mean_the_live_graph"] or mode in LIVE_OK_MODES)
        if refusal:
            raise CommandError(refusal, returncode=2)
        bench_keys = load_bench_keys(options["bench_keys"]) if options["bench_keys"] else frozenset()
        progress = self._attach_progress(options["verbosity"])
        try:
            with GraphDatabase.driver(config["URI"], auth=config["AUTH"]) as driver:
                self._dispatch(driver, config["NAME"], mode, options, bench_keys)
        finally:
            self._detach_progress(progress)

    def _dispatch(self, driver, db, mode, options, bench_keys):
        if mode == "verify":
            return self._gate(driver, db, options)
        if mode == "drift":
            return self._drift(driver, db, options)
        if mode == "investigation_counts":
            return self._investigation_counts(driver, db, options)
        if mode in ("loop", "once"):
            return self._loop(driver, db, mode, options)
        if mode == "samples":
            return self._samples(driver, db, options)
        if mode == "reconcile":
            return self._reconcile(driver, db, options)
        if mode == "labels":
            return self._labels(driver, db, options)
        if mode == "merge_studies":
            return self._merge_studies(driver, db, options)
        if mode == "unmerge_studies":
            return self._unmerge_studies(driver, db, options)
        if mode == "studies":
            return self._studies(driver, db, options)
        if mode == "small_tables":
            return self._small_tables(driver, db, options)
        return self._sync(driver, db, mode, options, bench_keys)

    # --- the modes that write ---------------------------------------------------------------------

    def _sync(self, driver, db, mode, options, bench_keys):
        as_json, run_dir = options["json"], options["run_dir"]
        record, trigger = not options["no_record"], options["trigger"]
        try:
            if mode == "catalog":
                result = run.catalog_sync(driver, db, dry_run=options["dry_run"], record=record, trigger=trigger,
                                          run_dir=run_dir)
                if run_dir and not options["dry_run"]:
                    _save(run_dir, "catalog_sync.json", result)
            else:
                if not options["dry_run"]:
                    run_dir = self._manual_run_dir(options, "full")
                result = run.full_sync(driver, db, chunk=options["chunk"], dry_run=options["dry_run"],
                                       run_dir=run_dir, bench_keys=bench_keys,
                                       apply_label_changes=options["apply_label_changes"],
                                       record=record, trigger=trigger)
        except run.PreflightError as exc:
            self._emit(exc.report, as_json)
            # A lock another write held past the wait is not a refusal of this graph: exit 1, so the loop retries
            # its child rather than closing the slot as done (loop.py).
            raise CommandError(str(exc), returncode=1 if isinstance(exc, run.LockTimeout) else 2) from exc
        self._emit(result, as_json)

    def _labels(self, driver, db, options):
        """``--labels``: ``run.relabel_all``; a refusal exits 2 (a busy lock 1), as ``--full`` does."""
        try:
            result = run.relabel_all(driver, db, dry_run=options["dry_run"],
                                     apply_label_changes=options["apply_label_changes"],
                                     record=not options["no_record"], trigger=options["trigger"],
                                     chunk=options["chunk"])
        except run.PreflightError as exc:
            self._emit(exc.report, options["json"])
            raise CommandError(str(exc), returncode=1 if isinstance(exc, run.LockTimeout) else 2) from exc
        self._emit(result, options["json"])

    def _reconcile(self, driver, db, options):
        result = reconcile.reconcile(driver, db, run_dir=self._run_dir(options, "reconcile"),
                                     chunk=options["chunk"], dry_run=options["dry_run"],
                                     apply_label_changes=options["apply_label_changes"],
                                     record=not options["no_record"], trigger=options["trigger"])
        self._emit(result, options["json"])
        self._exit_by_status(_RECONCILE_EXIT, result.get("status"), "--reconcile")

    def _samples(self, driver, db, options):
        """The by-id sync, with its own run record: ``targeted.sync_samples`` keeps none of its own."""
        handle = state.start_run("samples", trigger=options["trigger"]) if not options["no_record"] else None
        try:
            result = targeted.sync_samples(driver, db, options["samples"], run_dir=self._run_dir(options, "samples"),
                                           apply_label_changes=options["apply_label_changes"],
                                           chunk=options["chunk"])
        except Exception as exc:
            if handle is not None:
                handle.finish("failed", counts={"error": _text(exc)})
            raise
        status = result.get("status")
        if handle is not None:
            handle.finish("ok" if status == targeted.OK else "refused", counts=_scalars(result))
        self._emit(result, options["json"])
        self._exit_by_status(_SAMPLES_EXIT, status, "--samples")

    def _small_tables(self, driver, db, options):
        """One small-tables write (``targeted.sync_small_tables``, the code an ``isa`` row and the nightly reconcile
        run), with its own run record; exits as ``--samples`` does. ``./startup.sh`` runs it on local and dev right
        before the post-rebuild drift, so an edit made in SEEK's own UI since the nightly does not read as drift."""
        handle = None if options["no_record"] else state.start_run("small_tables", trigger=options["trigger"])
        try:
            result = targeted.sync_small_tables(driver, db,
                                                run_dir=self._run_dir(options, loop.SMALL_TABLES_DIR_KIND))
        except Exception as exc:
            if handle is not None:
                handle.finish("failed", counts={"error": _text(exc)})
            raise
        status = result.get("status")
        if handle is not None:
            handle.finish("ok" if status == targeted.OK else "refused", counts=_scalars(result))
        self._emit(result, options["json"])
        self._exit_by_status(_SAMPLES_EXIT, status, "--small-tables")

    # --- the studies release ----------------------------------------------------------------------

    @staticmethod
    def _check_approval(wanted) -> None:
        """A writing ``--merge-studies`` takes the approval line as the dry run prints it: never ``all``, every id
        with its kind, and only kinds the merge acts on (or ``already_merged``). Refused, exit 2, before connecting."""
        if wanted == "all":
            raise CommandError("--merge-studies without ids, or with all, runs only with --dry-run: the merge acts on "
                               "the approval line the operator approved", returncode=2)
        bare = [str(x) for x, kind in wanted.items() if kind is None]
        if bare:
            raise CommandError("--merge-studies writes only from the approval line the dry run prints, each id with "
                               f"its kind (for example 3:merge); no kind given for {', '.join(bare)}", returncode=2)
        bad = [f"study {x} is approved as {kind}, which the merge does not act on"
               for x, kind in wanted.items() if kind not in study_merge.APPROVABLE]
        if bad:
            raise CommandError("graph_sync --merge-studies: refused, nothing written: " + "; ".join(bad),
                               returncode=2)

    def _merge_studies(self, driver, db, options):
        """The study merge (``study_merge``): a dry run plans; otherwise refusals first (the graph's version, an id
        that reads a kind the merge does not act on), then ``apply`` of the approved kinds under the graph-write
        lock, recorded as a run. Whether each id still reads its approved kind is ``apply``'s check, under the lock."""
        wanted, as_json = options["merge_studies"], options["json"]
        ids = None if wanted == "all" else list(wanted)
        if options["dry_run"]:
            report = study_merge.plan(driver, db, ids)
            report.update(mode="merge_studies", status=study_merge.DRY_RUN)
            self._emit(report, as_json)
            return
        refusal = targeted._refusal(driver, db)
        if refusal is not None:
            self._emit(dict(refusal, mode="merge_studies"), as_json)
            raise CommandError(f"graph_sync --merge-studies: {refusal['status']}", returncode=2)
        approved = dict(wanted)
        report = study_merge.plan(driver, db, ids)
        blocked = {x: kind for x, kind in report["kinds"].items() if kind not in study_merge.APPROVABLE}
        if blocked:
            problems = [f"study {x} reads {kind} now, which the merge does not act on"
                        for x, kind in sorted(blocked.items())]
            report.update(mode="merge_studies", status=study_merge.REFUSED, approved=approved, problems=problems)
            self._emit(report, as_json)
            raise CommandError("graph_sync --merge-studies: refused, nothing written: " + "; ".join(problems),
                               returncode=2)
        run_dir = self._manual_run_dir(options, "merge_studies")
        handle = None if options["no_record"] else state.start_run("merge_studies", trigger=options["trigger"])
        result = {"mode": "merge_studies", "run_dir": run_dir, "approved": approved, "plan": report}
        try:
            with state.graph_write_lock(targeted.LOCK_WAIT_S) as held:
                if held:
                    result.update(study_merge.apply(driver, db, approved, run_dir=run_dir))
                else:
                    result.update(status="lock_timeout", lock_timeout_s=targeted.LOCK_WAIT_S)
        except Exception as exc:
            result.update(status=study_merge.FAILED, error=_text(exc))
        _save(run_dir, study_merge.REPORT_FILE, result)
        status = result["status"]
        if handle is not None:
            handle.finish(status if status in (study_merge.OK, study_merge.REFUSED) else "failed",
                          counts=_scalars(result))
        self._emit(result, as_json)
        if status != study_merge.OK:
            why = result.get("problem") or result.get("error") or "the graph-write lock was busy"
            raise CommandError(f"graph_sync --merge-studies: {status}: {why}", returncode=_MERGE_EXIT.get(status, 1))

    def _unmerge_studies(self, driver, db, options):
        """Undo merges from their journals (``study_merge.undo``). Every path is checked before anything is read or
        written, and so is the run root for other merge journals naming the same ids. Otherwise the undo runs under
        the graph-write lock, recorded as a run, and its result is saved as ``study_merge.json`` in its own run
        directory, ``unmerge_studies-<UTC time>``, whether it ends ok, partial or with an exception."""
        paths, as_json = options["unmerge_studies"], options["json"]
        run_root = options["run_root"] or loop.default_run_root()
        try:
            journals, _ = study_merge.read_journals(paths)
            missing = study_merge.unlisted_journals(run_root, paths, journals)
        except ValueError as exc:
            raise CommandError(f"graph_sync --unmerge-studies: {exc}", returncode=2) from exc
        if missing:
            raise CommandError("graph_sync --unmerge-studies: refused, nothing written: other merge journals under "
                               f"{run_root} name the same study ids; give them too: " + ", ".join(missing),
                               returncode=2)
        if options["dry_run"]:
            result = study_merge.undo(driver, db, paths, dry_run=True, run_root=run_root)
            result["mode"] = "unmerge_studies"
            self._emit(result, as_json)
            return
        run_dir = self._unmerge_run_dir(options, paths)
        handle = None if options["no_record"] else state.start_run("unmerge_studies", trigger=options["trigger"])
        result = {"mode": "unmerge_studies", "run_dir": run_dir, "paths": [os.path.abspath(p) for p in paths]}
        try:
            with state.graph_write_lock(targeted.LOCK_WAIT_S) as held:
                result.update(study_merge.undo(driver, db, paths, run_root=run_root) if held
                              else {"status": "lock_timeout", "lock_timeout_s": targeted.LOCK_WAIT_S})
        except ValueError as exc:
            # The undo's own checks of its paths, journals and archives, which run before it writes: an archive it
            # cannot read, or a merge journal naming the same ids that appeared while this run waited for the lock.
            result.update(status=study_merge.REFUSED, error=_text(exc))
            self._finish_unmerge(handle, run_dir, result)
            raise CommandError(f"graph_sync --unmerge-studies: refused, nothing written: {exc}", returncode=2) from exc
        except Exception as exc:
            result.update(status=study_merge.FAILED, error=_text(exc))
            self._finish_unmerge(handle, run_dir, result)
            raise CommandError(f"graph_sync --unmerge-studies failed part way: {_text(exc)}; its report is in "
                               f"{run_dir}", returncode=1) from exc
        self._finish_unmerge(handle, run_dir, result)
        self._emit(result, as_json)
        if result["status"] != study_merge.OK:
            raise CommandError(f"graph_sync --unmerge-studies: {result['status']}: {_undo_problems(result)}; its "
                               f"report is in {run_dir}", returncode=1)

    def _unmerge_run_dir(self, options, paths) -> str:
        """The undo's run directory (``_manual_run_dir``). A ``--run-dir`` that the undo reads, or that holds a
        merge's journal or report (whose ``study_merge.json`` the undo's report would replace), is refused, exit 2."""
        target = os.path.realpath(options["run_dir"]) if options["run_dir"] else None
        clash = target is not None and (
            target in {os.path.realpath(p) for p in paths}
            or any(os.path.exists(os.path.join(target, name))
                   for name in (study_merge.JOURNAL_FILE, study_merge.REPORT_FILE)))
        if clash:
            raise CommandError(f"graph_sync --unmerge-studies: refused, nothing written: the run directory {target} "
                               "holds a merge's journal or report, or is one of the paths read; name another with "
                               "--run-dir, or leave it out", returncode=2)
        return self._manual_run_dir(options, "unmerge_studies")

    @staticmethod
    def _finish_unmerge(handle, run_dir: str, result: dict) -> None:
        _save(run_dir, study_merge.REPORT_FILE, result)
        if handle is not None:
            status = result["status"]
            handle.finish(status if status in (study_merge.OK, study_merge.REFUSED) else "failed",
                          counts=_scalars(result))

    def _studies(self, driver, db, options):
        """Every sample's IN_STUDY follows SEEK once (``study_links.rebuild_in_study``), removal included whatever
        the switch says, under the graph-write lock for the whole run, recorded as a ``study_links`` run."""
        as_json = options["json"]
        refusal = targeted._refusal(driver, db)
        if refusal is not None:
            self._emit(dict(refusal, mode="studies"), as_json)
            raise CommandError(f"graph_sync --studies: {refusal['status']}", returncode=2)
        duplicates = writer.seek_study_id_duplicates(driver, db)
        if duplicates:
            self._emit({"mode": "studies", "status": study_links.REFUSED, "seek_study_id_duplicates": duplicates},
                       as_json)
            raise CommandError("graph_sync --studies: refused, nothing written: two Study nodes share a "
                               "seek_study_id", returncode=2)
        if options["dry_run"]:
            report = study_links.rebuild_in_study(driver, db, remove=True, run_dir=None, dry_run=True)
            report["mode"] = "studies"
            self._emit(report, as_json)
            return
        run_dir = self._manual_run_dir(options, "study_links")
        handle = None if options["no_record"] else state.start_run("study_links", trigger=options["trigger"])
        try:
            with state.graph_write_lock(targeted.LOCK_WAIT_S) as held:
                report = (study_links.rebuild_in_study(driver, db, remove=True, run_dir=run_dir, lock=None,
                                                       path="studies")
                          if held else {"status": study_links.LOCK_TIMEOUT, "lock_timeout_s": targeted.LOCK_WAIT_S})
        except Exception as exc:
            if handle is not None:
                handle.finish("failed", counts={"error": _text(exc)})
            raise CommandError(f"graph_sync --studies failed part way: {_text(exc)}", returncode=1) from exc
        report.update(mode="studies", run_dir=run_dir)
        _save(run_dir, study_links.REPORT_FILE, report)
        if handle is not None:
            handle.finish("ok" if report["status"] == study_links.OK else "refused", counts=_scalars(report))
        self._emit(report, as_json)
        self._exit_by_status(_STUDIES_EXIT, report.get("status"), "--studies")

    # --- the modes that only read -----------------------------------------------------------------

    def _gate(self, driver, db, options):
        result = verify.gate_g(driver, db, seed=options["seed"], chunk=options["chunk"])
        self._emit(result, options["json"])
        if options["run_dir"]:
            _save(options["run_dir"], "gate_g.json", result)
        if not result.get("pass"):
            checks = result.get("checks", [])
            failed = sum(1 for c in checks if not c.get("pass"))
            raise CommandError(f"gate G failed: {failed} of {len(checks)} checks", returncode=1)

    def _drift(self, driver, db, options):
        trigger = None if options["no_record"] else options["trigger"]
        try:
            result = drift.drift_check(driver, db, seed=options["seed"], chunk=options["chunk"], trigger=trigger)
        except Exception as exc:
            # Exit 3: the check itself could not run, which is neither drift nor a refusal (the design, section 13).
            raise CommandError(f"graph_sync --drift could not complete: {_text(exc)}", returncode=3) from exc
        self._emit(result, options["json"], label="drift")
        if options["run_dir"]:
            _save(options["run_dir"], DRIFT_FILE, result)
        status = result.get("status")
        if status == drift.REFUSED:
            raise CommandError(result.get("reason") or "graph_sync --drift refused to compare this graph",
                               returncode=2)
        if status != drift.OK:
            checks = result.get("checks", [])
            failed = [c.get("name", "?") for c in checks if not c.get("pass")]
            raise CommandError(f"the graph has drifted: {len(failed)} of {len(checks)} checks failed: "
                               + ", ".join(failed), returncode=1)

    def _investigation_counts(self, driver, db, options):
        result = drift.investigation_counts(driver, db, options["instance"])
        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
            return
        self.stdout.write(f"measured on {result['measured_on']} at {result['measured_at']}")
        for title, counts in sorted(result["investigations"].items()):
            self.stdout.write(f"{title}  nodes {counts['nodes']}  samples {counts['samples']}")

    # --- the outbox by hand -----------------------------------------------------------------------

    def _requeue_dead(self, options):
        """``--requeue-dead``: the dmac outbox only; never a Neo4j connection (``state.requeue_dead``)."""
        kind, dry_run = options["kind"], options["dry_run"]
        if kind is not None and kind not in state.KINDS:
            raise CommandError(f"--kind: not a graph_sync outbox kind: {kind!r}; one of {', '.join(state.KINDS)}",
                               returncode=2)
        rows = state.requeue_dead(kind, dry_run=dry_run)
        if options["json"]:
            self.stdout.write(json.dumps({"dry_run": dry_run, "kind": kind, "requeued": len(rows), "rows": rows},
                                         indent=2, sort_keys=True))
            return
        for r in rows:
            self.stdout.write(f"{r['kind']} {r['key']}: attempts {r['attempts']}: {r['error'] or 'no error recorded'}")
        verb = "would put back" if dry_run else "put back"
        self.stdout.write(f"{verb} to pending: {len(rows)} dead rows")

    # --- the loop ---------------------------------------------------------------------------------

    def _loop(self, driver, db, mode, options):
        opts = loop.Options(run_root=options["run_root"] or loop.default_run_root(),
                            apply_label_changes=loop.label_changes_approved(),
                            record=not options["no_record"])
        worker_id = loop.worker_identity()
        if mode == "once":
            self._emit(loop.run_pass(driver, db, worker_id, opts=opts), options["json"])
            return
        loop.run_forever(driver, db, worker_id, opts=opts, interval_s=options["interval"])

    # --- plumbing ---------------------------------------------------------------------------------

    @staticmethod
    def _run_dir(options, kind: str) -> str:
        return options["run_dir"] or loop.run_dir_for(options["run_root"] or loop.default_run_root(), kind)

    def _manual_run_dir(self, options, kind: str) -> str:
        """The run directory of a writing mode run by hand: ``--run-dir``, else ``<kind>-<UTC time>`` under the loop's
        run root (``--run-root``, ``$GS_RUN_DIR``, else ``graph_sync`` under the log directory), made now and named on
        stderr. One that cannot be made refuses, exit 2, before anything is read, written or recorded."""
        path = self._run_dir(options, kind)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            raise CommandError(f"graph_sync: cannot make the run directory {path}: {exc}", returncode=2) from exc
        self.stderr.write(f"run directory: {path}")
        return path

    @staticmethod
    def _exit_by_status(codes, status, mode: str) -> None:
        code = codes.get(status, 1)
        if code:
            raise CommandError(f"graph_sync {mode}: {status}", returncode=code)

    def _emit(self, result: dict, as_json: bool, label: str = "gate G") -> None:
        if as_json:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True, default=str))
            return
        if "checks" in result:
            for check in result["checks"]:
                mark = "PASS" if check["pass"] else "FAIL"
                self.stdout.write(f"{mark}  {check['name']}  expected {check['expected']!r}  "
                                  f"actual {check['actual']!r}")
            self.stdout.write(f"{label}: {'PASS' if result['pass'] else 'FAIL'}")
            return
        for key in sorted(result):
            value = result[key]
            if isinstance(value, (list, tuple, set, dict)):
                value = f"<{len(value)} items>"
            self.stdout.write(f"{key}: {value}")

    @staticmethod
    def _attach_progress(verbosity: int):
        """Progress lines on stderr for the length of the command (Django's LOGGING has no console handler)."""
        if verbosity < 1:
            return None
        logger = logging.getLogger(PROGRESS_LOGGER)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        handler.setLevel(logging.INFO)
        previous = logger.level
        logger.addHandler(handler)
        if logger.getEffectiveLevel() > logging.INFO:
            logger.setLevel(logging.INFO)
        return handler, previous

    @staticmethod
    def _detach_progress(progress) -> None:
        if progress is None:
            return
        handler, previous = progress
        logger = logging.getLogger(PROGRESS_LOGGER)
        logger.removeHandler(handler)
        logger.setLevel(previous)


def _undo_problems(result: dict) -> str:
    """What an undo that did not end ok could not do, in words: each refused id with its reason, each Investigation
    not restored, each study whose late arrivals stayed on the legacy node, or the busy lock."""
    parts = [f"study {r['study_id']} refused: {r['reason']}" for r in result.get("refused") or []]
    parts += [f"study {i['study_id']}: its {i['node']} node's Investigation {i['investigation'].get('id')} was not "
              "restored" for i in result.get("investigation_not_restored") or []]
    parts += [f"study {s['study_id']}: {len(s['arrived_left_on_legacy'])} sources that reached it after the merge "
              "stayed on the legacy node" for s in result.get("studies") or [] if s.get("arrived_left_on_legacy")]
    if result.get("status") == "lock_timeout":
        parts.append("the graph-write lock was busy")
    return "; ".join(parts) or "see its report"


def _save(run_dir: str, name: str, payload: dict) -> None:
    os.makedirs(run_dir, exist_ok=True)
    run._write_json(os.path.join(run_dir, name), payload)
