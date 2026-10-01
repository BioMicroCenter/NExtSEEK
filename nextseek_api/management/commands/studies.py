"""Create a SEEK study if it is missing and move a list of samples into it, in SEEK; the graph follows.

    manage.py studies --mode export   --out FILE [--study-ids IDS]
    manage.py studies --mode plan     (--sheet FILE [--sheet-name NAME] | --dev-export FILE
                                       | --graph-only (all | IDS) | --associations FILE)
                                      [--investigation ID] --seek-login NAME [--seek-password-stdin]
                                      [--run-dir PATH] [--json]
    manage.py studies --mode apply    --run-dir PATH --seek-login NAME [--seek-password-stdin]
                                      [--investigation ID] [--json]
    manage.py studies --mode graph    --run-dir PATH --approve-label-changes [--investigation ID]
                                      [--json] [--i-mean-the-live-graph]
    manage.py studies --mode rollback --run-dir PATH --seek-login NAME [--seek-password-stdin]
                                      [--investigation ID] [--confirm] [--json] [--i-mean-the-live-graph]
    manage.py studies --mode report   --run-dir PATH [--json]

| Mode | Does | Writes |
|---|---|---|
| ``export`` | the dev graph's paper studies and their samples to a file | the file only |
| ``plan`` | one source, then the plan; the run directory | the run directory only |
| ``apply`` | the plan into SEEK, resuming an unfinished run | SEEK, the dmac mapping, the outbox |
| ``graph`` | the graph step, with the operator's approval of the plan's label changes | the graph, through graph_sync |
| ``rollback`` | undo a run in reverse; without ``--confirm`` it only lists | everything apply and graph wrote |
| ``report`` | the plan's summary and the journal's progress | nothing |

The password is typed at the prompt, or given as one line of stdin with ``--seek-password-stdin``; it is never an
argument or an environment variable. ``graph`` and ``rollback`` need ``--i-mean-the-live-graph`` against the live
stack's Neo4j, as graph_sync's writing modes do. Exit status: 0 done; 1 stopped part way (the journal says where; the
same command resumes), which is also what an error raised inside apply, the graph step or rollback gives, since it may
come after a write; 2 refused, nothing written. With ``--json`` stdout holds only the result. The package and its
rules: ``nextseek_api/studies/README.md``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from neo4j.exceptions import AuthError, DriverError

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.management.commands.graph_sync import live_graph_refusal
from nextseek_api.studies import apply, planner, report, rollback
from nextseek_api.studies.journal import JOURNAL_FILE, read_journal
from nextseek_api.studies.models import AssociationSet
from nextseek_api.studies.seek import SeekCredential, SeekError, SeekRefused, SeekSession, read_password
from nextseek_api.studies.snapshot import SnapshotReader
from nextseek_api.studies.sources import dev_export, graph_only, sheet

log = logging.getLogger(__name__)

MODES = ("export", "plan", "apply", "graph", "rollback", "report")
_client_factory = SeekAPIClient


class Refused(Exception):
    """Nothing was written: exit 2."""


class Stopped(Exception):
    """A run phase (apply, the graph step, rollback) raised, perhaps after a write: exit 1."""

    def __init__(self, exc: Exception, run_dir: Path):
        self.run_dir = run_dir
        super().__init__(f"stopped part way: {type(exc).__name__}: {exc}. The journal in {run_dir} says where; "
                         "the same command resumes")


# Raised before any run phase starts, so nothing was written: refused, exit 2.
PRE_WRITE_ERRORS = (Refused, sheet.SheetError, SeekRefused, SeekError, ValueError, OSError, DriverError, AuthError)


def _run_phase(call, run_dir: Path, *args, **kwargs) -> tuple:
    """One run phase, as (exit code, message, counts, run_dir). Whatever it raises may come after a write, so it
    stops, never refuses. The journal lines that could not be read are reported: lines a stop cut short, whose
    writes never began."""
    try:
        result = call(run_dir, *args, **kwargs)
    except Exception as exc:
        log.exception("studies: the run phase stopped")
        raise Stopped(exc, run_dir) from exc
    message, counts = result.message, dict(result.counts)
    bad = read_journal(run_dir / JOURNAL_FILE)[1]
    if bad:
        counts["journal_unreadable_lines"] = bad
        message += (f" ({bad} journal line(s) could not be read and were skipped: lines a stop cut short, whose "
                    "writes never began)")
    return result.exit_code, message, counts, run_dir


def _reader(session, driver, db):
    return SnapshotReader(session, driver, db)


@contextmanager
def _open_driver(config):
    from neo4j import GraphDatabase

    with GraphDatabase.driver(config["URI"], auth=config["AUTH"]) as driver:
        yield driver


def _ids(text: str) -> list[int]:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a comma-separated list of ids: {text!r}")
    return sorted({int(p) for p in parts})


def _graph_only(text: str):
    return "all" if text.strip() == "all" else _ids(text)


def default_run_dir(source: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(settings.LOG_DIR) / "studies" / f"{stamp}-{source}"


class Command(BaseCommand):
    help = "Create a SEEK study if it is missing and move samples into it, in SEEK; plan, apply, graph, rollback."

    def add_arguments(self, parser):
        parser.add_argument("--mode", required=True, choices=MODES)
        source = parser.add_mutually_exclusive_group()
        source.add_argument("--sheet", metavar="FILE", help="plan: a curator sheet (.xlsx, .csv or .json)")
        source.add_argument("--dev-export", metavar="FILE", help="plan: a file --mode export wrote on the dev box")
        source.add_argument("--graph-only", metavar="all|IDS", type=_graph_only,
                            help="plan: the graph-only paper studies, all or these paper ids")
        source.add_argument("--associations", metavar="FILE", help="plan: a saved associations.json, planned again")
        parser.add_argument("--sheet-name", help="plan: the sheet of an .xlsx workbook (default: the first)")
        parser.add_argument("--investigation", type=int, help="plan, apply, graph, rollback: this investigation only")
        parser.add_argument("--seek-login", metavar="NAME", help="the operator's SEEK login name")
        parser.add_argument("--seek-password-stdin", action="store_true",
                            help="read the password as one line of stdin instead of prompting")
        parser.add_argument("--run-dir", metavar="PATH", help="the run directory")
        parser.add_argument("--out", metavar="FILE", help="export: the file to write")
        parser.add_argument("--study-ids", metavar="IDS", type=_ids, help="export: these studies instead of papers")
        parser.add_argument("--approve-label-changes", action="store_true",
                            help="graph: write the label changes the plan listed")
        parser.add_argument("--confirm", action="store_true", help="rollback: undo; without it, only list")
        parser.add_argument("--json", action="store_true", help="print only the result, as JSON, on stdout")
        parser.add_argument("--i-mean-the-live-graph", action="store_true",
                            help="graph, rollback: allow the live stack's Neo4j")

    # --- plumbing ---
    def _session(self, options) -> SeekSession:
        login = options["seek_login"]
        if not login:
            raise Refused(f"--mode {options['mode']} needs --seek-login")
        password = read_password(from_stdin=options["seek_password_stdin"])
        return SeekSession(SeekCredential(login, password), client_factory=_client_factory)

    @contextmanager
    def _graph(self, options, *, live_ok: bool):
        config = getattr(settings, "NEO4J_DATABASE", None) or {}
        refusal = live_graph_refusal(config, allow_live=live_ok or options["i_mean_the_live_graph"])
        if refusal:
            raise Refused(refusal)
        with _open_driver(config) as driver:
            yield driver, config.get("NAME") or "neo4j"

    def _need(self, options, *names):
        for name in names:
            if not options[name]:
                raise Refused(f"--mode {options['mode']} needs --{name.replace('_', '-')}")

    def _emit(self, options, code: int, message: str, counts: dict, run_dir=None) -> None:
        result = {"mode": options["mode"], "exit_code": code, "message": message, "counts": counts,
                  "run_dir": str(run_dir) if run_dir else None}
        if options["json"]:
            self.stdout.write(json.dumps(result, sort_keys=True, default=str))
        else:
            self.stdout.write(message)
            if counts:
                self.stdout.write(json.dumps(counts, sort_keys=True, indent=1, default=str))

    # --- modes ---
    def _associations(self, options, reader, driver, db) -> AssociationSet:
        if options["sheet"]:
            return sheet.sheet_associations(options["sheet"], options["sheet_name"], reader)
        if options["dev_export"]:
            return dev_export.dev_associations(options["dev_export"], reader)
        if options["graph_only"]:
            return graph_only.graph_only_associations(driver, db, options["graph_only"], reader,
                                                      investigation=options["investigation"])
        path = Path(options["associations"])
        aset = AssociationSet.from_file(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return aset.model_copy(update={"source": "replay", "source_ref": f"{path.name} sha256:{digest}"})

    def _plan(self, options):
        given = [k for k in ("sheet", "dev_export", "graph_only", "associations") if options[k]]
        if len(given) != 1:
            raise Refused("--mode plan needs one of --sheet, --dev-export, --graph-only, --associations")
        if options["sheet_name"] and not options["sheet"]:
            raise Refused("--sheet-name goes with --sheet")
        session = self._session(options)
        source = {"sheet": "sheet", "dev_export": "dev_export", "graph_only": "graph_only",
                  "associations": "replay"}[given[0]]
        run_dir = Path(options["run_dir"]) if options["run_dir"] else default_run_dir(source)
        if (run_dir / report.PLAN_FILE).exists():
            raise Refused(f"{run_dir} already holds a plan: give a new --run-dir")
        with self._graph(options, live_ok=True) as (driver, db):
            reader = _reader(session, driver, db)
            aset = self._associations(options, reader, driver, db)
            if options["investigation"] is not None:
                aset = aset.model_copy(update={"targets": [t for t in aset.targets
                                                           if t.investigation_id == options["investigation"]]})
            plan = planner.plan_study_moves(aset, reader, run_id=run_dir.name)
        report.write_plan_files(run_dir, plan, aset)
        next_step = (f"Read {run_dir / report.PLAN_TEXT} and {run_dir / report.UNMATCHED_CSV}, then: "
                     f"manage.py studies --mode apply --run-dir {run_dir} --seek-login {session.login}")
        return 0, next_step, plan.summary, run_dir

    def _dispatch(self, options):
        mode = options["mode"]
        if mode == "export":
            self._need(options, "out")
            with self._graph(options, live_ok=True) as (driver, db):
                counts = dev_export.export_dev_graph(driver, db, options["out"], options["study_ids"])
            return 0, f"exported to {options['out']}", counts, None
        if mode == "plan":
            return self._plan(options)
        self._need(options, "run_dir")
        run_dir = Path(options["run_dir"])
        if not (run_dir / report.PLAN_FILE).is_file():
            raise Refused(f"{run_dir} holds no {report.PLAN_FILE}: give the run directory --mode plan wrote")
        if mode == "report":
            return 0, "progress", report.progress(run_dir), run_dir
        if mode == "apply":
            session = self._session(options).prove()
            with self._graph(options, live_ok=True) as (driver, db):
                code, message, counts, _ = _run_phase(apply.apply_study_moves, run_dir, session, driver, db,
                                                      investigation=options["investigation"])
            if code == 0:
                message += (f". Next: manage.py studies --mode graph --run-dir {run_dir} --approve-label-changes "
                            "--i-mean-the-live-graph, after reading the label changes in plan.txt")
            return code, message, counts, run_dir
        if mode == "graph":
            if not options["approve_label_changes"]:
                raise Refused("--mode graph writes the plan's label changes: give --approve-label-changes")
            with self._graph(options, live_ok=False) as (driver, db):
                return _run_phase(apply.graph_step, run_dir, driver, db, approve_label_changes=True,
                                  investigation=options["investigation"])
        session = self._session(options).prove()
        with self._graph(options, live_ok=False) as (driver, db):
            return _run_phase(rollback.rollback_study_moves, run_dir, session, driver, db, confirm=options["confirm"],
                              investigation=options["investigation"])

    def handle(self, *args, **options):
        try:
            code, message, counts, run_dir = self._dispatch(options)
        except Stopped as exc:
            code, message, counts, run_dir = 1, str(exc), {}, exc.run_dir
        except PRE_WRITE_ERRORS as exc:
            messages = getattr(exc, "messages", None) or [str(exc)]
            code, message, counts, run_dir = 2, "refused, nothing written: " + "; ".join(messages), {}, None
        except planner.PlannerDefect as exc:
            code, message, counts, run_dir = 2, (f"refused, nothing written: the plan would clear {len(exc.edges)} "
                                                 "label(s) through the move itself, a defect of the planner"), {}, None
        self._emit(options, code, message, counts, run_dir)
        if code:
            raise SystemExit(code)
