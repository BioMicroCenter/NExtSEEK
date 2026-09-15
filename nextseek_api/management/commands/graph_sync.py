"""Write graph schema v1.1 from MySQL to Neo4j, or check it (gate G).

    manage.py graph_sync (--full | --catalog | --verify) [--json] [--dry-run] [--chunk N] [--run-dir PATH]
                         [--seed N] [--bench-keys FILE] [--i-mean-the-live-graph]

``--full`` runs ``graph_sync.run.full_sync`` (the design's order, section 6), ``--catalog`` rewrites the catalog
nodes only, and ``--verify`` runs ``graph_sync.verify.gate_g``, which only reads. ``--dry-run`` makes ``--full``
and ``--catalog`` read without writing and print their counts; ``--verify`` always only reads.

Exit status: 0 on success; 1 when ``--verify`` finds a failing check or a run fails part way; 2 on a refusal, which
means nothing was written: settings name no Neo4j URI, the Neo4j host is the live stack's service (``neo4j``) and
``--i-mean-the-live-graph`` is absent, or a sync's preflight found a problem.

With ``--json`` stdout holds only the JSON result; progress goes to stderr. The package, its modules and the graph
it writes: ``nextseek_api/graph_sync/README.md`` and ``docs/neo4j-schema.md`` section "v1.1".
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from neo4j import GraphDatabase

from nextseek_api.graph_sync import run, verify, writer

# The live stack's Neo4j: its compose service and container name.
LIVE_NEO4J_HOSTS = frozenset({"neo4j"})
PROGRESS_LOGGER = "nextseek_api.graph_sync"


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be positive, got {value}")
    return value


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


class Command(BaseCommand):
    help = "Write graph schema v1.1 from MySQL to Neo4j (--full, --catalog) or run gate G (--verify)."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--full", action="store_true", help="Run the whole ordered sync.")
        mode.add_argument("--catalog", action="store_true", help="Rewrite the SampleType and Attribute catalog only.")
        mode.add_argument("--verify", action="store_true", help="Run gate G (read-only); exit 1 when a check fails.")
        parser.add_argument("--json", action="store_true", help="Print the result as JSON on stdout.")
        parser.add_argument("--dry-run", action="store_true",
                            help="With --full or --catalog: read MySQL and the graph, write nothing, print counts.")
        parser.add_argument("--chunk", type=_positive_int, default=writer.SAMPLE_CHUNK,
                            help="Samples per MySQL page and per write transaction (default %(default)s).")
        parser.add_argument("--run-dir", metavar="PATH",
                            help="--full: the directory for full_sync.json, census.json and the CHILD_OF archive "
                                 "(default: a new directory under $GS_RUN_DIR). --catalog and --verify also save "
                                 "their result there.")
        parser.add_argument("--seed", type=int, help="--verify: the seed of the random samples (default: random).")
        parser.add_argument("--bench-keys", metavar="FILE",
                            help="--full: a JSON list of attribute keys or [sample type, attribute] pairs the "
                                 "index budget must cover.")
        parser.add_argument("--i-mean-the-live-graph", action="store_true",
                            help="Allow a Neo4j whose host is the live stack's (neo4j).")

    def handle(self, *args, **options):
        config = getattr(settings, "NEO4J_DATABASE", None)
        refusal = live_graph_refusal(config, options["i_mean_the_live_graph"])
        if refusal:
            raise CommandError(refusal, returncode=2)
        bench_keys = load_bench_keys(options["bench_keys"]) if options["bench_keys"] else frozenset()
        progress = self._attach_progress(options["verbosity"])
        try:
            with GraphDatabase.driver(config["URI"], auth=config["AUTH"]) as driver:
                self._dispatch(driver, config["NAME"], options, bench_keys)
        finally:
            self._detach_progress(progress)

    def _dispatch(self, driver, db, options, bench_keys):
        as_json, run_dir = options["json"], options["run_dir"]
        if options["verify"]:
            result = verify.gate_g(driver, db, seed=options["seed"], chunk=options["chunk"])
            self._emit(result, as_json)
            if run_dir:
                _save(run_dir, "gate_g.json", result)
            if not result.get("pass"):
                checks = result.get("checks", [])
                failed = sum(1 for c in checks if not c.get("pass"))
                raise CommandError(f"gate G failed: {failed} of {len(checks)} checks", returncode=1)
            return
        try:
            if options["catalog"]:
                result = run.catalog_sync(driver, db, dry_run=options["dry_run"])
                if run_dir and not options["dry_run"]:
                    _save(run_dir, "catalog_sync.json", result)
            else:
                result = run.full_sync(driver, db, chunk=options["chunk"], dry_run=options["dry_run"],
                                       run_dir=run_dir, bench_keys=bench_keys)
        except run.PreflightError as exc:
            self._emit(exc.report, as_json)
            raise CommandError(str(exc), returncode=2) from exc
        self._emit(result, as_json)

    def _emit(self, result: dict, as_json: bool) -> None:
        if as_json:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True, default=str))
            return
        if "checks" in result:
            for check in result["checks"]:
                mark = "PASS" if check["pass"] else "FAIL"
                self.stdout.write(f"{mark}  {check['name']}  expected {check['expected']!r}  "
                                  f"actual {check['actual']!r}")
            self.stdout.write(f"gate G: {'PASS' if result['pass'] else 'FAIL'}")
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
