"""Keep the Neo4j sample graph in step with MySQL, or check that it is (graph schema v1.2).

    manage.py graph_sync (--full | --catalog | --reconcile | --samples IDS | --verify | --drift | --loop | --once
                          | --investigation-counts --instance {local,dev,prod})
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

``--dry-run`` makes ``--full``, ``--catalog`` and ``--reconcile`` read without writing and print their counts.
``--apply-label-changes`` (``--full``, ``--reconcile``, ``--samples``) is the operator's approval to write the
DERIVED_FROM labels that differ from the rule, which are otherwise only counted (the sync design, R14); the loop
takes that approval from ``NEXTSEEK_GRAPH_SYNC_LABEL_CHANGES=apply`` instead. ``--no-record`` keeps the run out of
``graph_sync_run``, and ``--trigger`` names who started it there (the loop passes ``loop``).

``--verify``, ``--drift``, ``--investigation-counts`` and ``--loop`` run against the live stack's Neo4j without
``--i-mean-the-live-graph``: the first three only read, and the loop is what the app container runs against its own
graph. Every other mode still
needs the flag by hand, and the loop passes it to its children.

Exit status: 0 on success; 1 when a check fails or a run failed part way; 2 on a refusal, which means nothing was
written; 3 when ``--drift`` could not complete. With ``--json`` stdout holds only the JSON result; progress goes to
stderr. The package, its modules and the graph it writes: ``nextseek_api/graph_sync/README.md`` and
``docs/neo4j-schema.md`` section "v1.2".
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

from nextseek_api.graph_sync import drift, loop, reconcile, run, state, targeted, verify, writer

# The live stack's Neo4j: its compose service and container name.
LIVE_NEO4J_HOSTS = frozenset({"neo4j"})
PROGRESS_LOGGER = "nextseek_api.graph_sync"

MODES = ("full", "catalog", "verify", "reconcile", "drift", "samples", "loop", "once", "investigation_counts")
# The modes that may reach the live graph without the flag: the three that only read, and the loop itself.
LIVE_OK_MODES = frozenset({"verify", "drift", "investigation_counts", "loop"})
LABEL_CHANGE_MODES = frozenset({"full", "reconcile", "samples"})
DRIFT_FILE = "drift.json"
TRIGGER_CHARS = 64

# A by-id sync that could not take the lock, like one refused for the graph's version, wrote nothing at all.
_SAMPLES_EXIT = MappingProxyType({targeted.OK: 0, targeted.NOT_AT_VERSION: 2, targeted.LOCK_TIMEOUT: 2})
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
        parser.add_argument("--instance", choices=drift.INSTANCES,
                            help="--investigation-counts: the instance this graph is (no default).")
        parser.add_argument("--json", action="store_true", help="Print the result as JSON on stdout.")
        parser.add_argument("--dry-run", action="store_true",
                            help="With --full, --catalog or --reconcile: read MySQL and the graph, write nothing, "
                                 "print the counts.")
        parser.add_argument("--chunk", type=_positive_int, default=writer.SAMPLE_CHUNK,
                            help="Samples per MySQL page and per write transaction (default %(default)s).")
        parser.add_argument("--run-dir", metavar="PATH",
                            help="--full: the directory for full_sync.json, census.json and the CHILD_OF archive "
                                 "(default: a new directory under $GS_RUN_DIR). --catalog, --verify, --drift, "
                                 "--reconcile and --samples also save their result there.")
        parser.add_argument("--run-root", metavar="PATH",
                            help="Where --reconcile, --samples and the loop make their own run directories "
                                 "(default: $GS_RUN_DIR, else graph_sync under the log directory).")
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
        return self._sync(driver, db, mode, options, bench_keys)

    # --- the modes that write ---------------------------------------------------------------------

    def _sync(self, driver, db, mode, options, bench_keys):
        as_json, run_dir = options["json"], options["run_dir"]
        record, trigger = not options["no_record"], options["trigger"]
        try:
            if mode == "catalog":
                result = run.catalog_sync(driver, db, dry_run=options["dry_run"], record=record, trigger=trigger)
                if run_dir and not options["dry_run"]:
                    _save(run_dir, "catalog_sync.json", result)
            else:
                result = run.full_sync(driver, db, chunk=options["chunk"], dry_run=options["dry_run"],
                                       run_dir=run_dir, bench_keys=bench_keys,
                                       apply_label_changes=options["apply_label_changes"],
                                       record=record, trigger=trigger)
        except run.PreflightError as exc:
            self._emit(exc.report, as_json)
            raise CommandError(str(exc), returncode=2) from exc
        self._emit(result, as_json)

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


def _save(run_dir: str, name: str, payload: dict) -> None:
    os.makedirs(run_dir, exist_ok=True)
    run._write_json(os.path.join(run_dir, name), payload)
