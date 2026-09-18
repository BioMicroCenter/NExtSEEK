"""Fill ground-truth files by running their read-only oracles (graph_search Nessie POC, spec E5).

Runs inside the operator's venue, which reaches the live stack read-only as the
superuser `demo`:

    scripts/graph_search/nessie_venue.sh exec NessieAI/tests/nessie_tests/scripts/derive_truth.py \\
        --truth /venue/truth/<file>.json [--only id1,id2]
    ... derive_truth.py --summary --truth /venue/truth
    ... derive_truth.py --fingerprint-only --truth /venue/truth     # non-zero when the graph moved

The oracles (`engine_truth.Oracle`):

- graph_search: POST to the venue's own graph_search endpoint as `demo`, the password read
  from GS_DEMO_PASSWORD (never printed); the answer is the response's `total`.
- cypher: through `session.execute_read` with a timeout, on the settings' Neo4j, after a
  textual write check.
- sql: through `connections["seek"]` inside START TRANSACTION READ ONLY, refused unless it is
  one statement starting with SELECT or WITH (`check_sql`).
- measured: a number copied from a named results file, relative to the truth file.

It fills `expected.value` and, for counts, `required_numbers`; runs `second_oracle` when there
is one and records a disagreement on the turn; stamps `derived_at` and the file's data
fingerprint; and writes nothing but the truth file itself (mode 600, replaced atomically).
Django and Neo4j are imported only when an oracle needs them, so `--summary` and
measured-only files also run on the host (`python -m NessieAI.tests.nessie_tests.scripts.derive_truth`).
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from NessieAI.tests.nessie_tests import engine_truth as et

GRAPH_SEARCH_PATH = "/nextseek_api/samples/graph_search/"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
PASSWORD_ENV = "GS_DEMO_PASSWORD"
CYPHER_TIMEOUT_S = 120
SQL_TIMEOUT_MS = 120_000
HTTP_TIMEOUT_S = 180
# A list answer becomes the reply's required items only when it is short enough for a
# reply to state in full; a longer list keeps its value and needs hand-picked items.
MAX_AUTO_ITEMS = 25

FINGERPRINT_META = ("MATCH (m:GraphMeta) RETURN m.catalog_hash AS catalog_hash, "
                    "m.synced_at AS synced_at, m.schema_version AS schema_version")
FINGERPRINT_COUNT = "MATCH (s:Sample) RETURN count(s) AS n"
# What --fingerprint-only and a refill's fingerprint_changed compare: the data identity.
FINGERPRINT_FIELDS = ("sample_count", "catalog_hash")
# Stamped as well, for information only. GraphMeta.synced_at moves on every full, catalog or
# label-map sync, the nightly reconcile and syncs that change nothing among them, so comparing it
# failed the gate on a graph whose data had not moved (D16). The two fields above do not see an
# edge relabel or a new Study node either: re-derive after any full sync before a scored run.
STAMP_FIELDS = FINGERPRINT_FIELDS + ("synced_at",)


class OracleRefused(ValueError):
    """The oracle's statement is not a plain read; it was never sent."""


class OracleFailed(RuntimeError):
    """The oracle ran (or could not run) and gave no usable answer."""


# ── read-only guards ─────────────────────────────────────────────────────────

_SQL_MASK = re.compile(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\"|`[^`]*`"
                       r"|--[^\n]*|#[^\n]*|/\*.*?\*/", re.DOTALL)
_SQL_FORBIDDEN = ("INTO", "UPDATE", "INSERT", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER",
                  "TRUNCATE", "GRANT", "REVOKE", "LOCK", "UNLOCK", "SET", "CALL", "LOAD",
                  "HANDLER", "RENAME", "DO", "PREPARE", "EXECUTE", "DEALLOCATE")


def check_sql(statement: str) -> str:
    """The statement without a trailing semicolon, or OracleRefused.

    One statement, starting with SELECT or WITH, with no write keyword, no INTO (OUTFILE,
    DUMPFILE or variables) and no locking read, checked on text whose literals, quoted
    names and comments are blanked. A MySQL executable comment (`/*! ... */`) is refused
    outright, because the server runs what it hides. The READ ONLY transaction the
    executor opens is the second, independent guard.
    """
    text = (statement or "").strip()
    if "/*!" in text:
        raise OracleRefused("a MySQL executable comment (/*! ... */) is not allowed")
    masked = _SQL_MASK.sub(" ", text).strip()
    while masked.endswith(";"):
        masked = masked[:-1].rstrip()
    if not masked:
        raise OracleRefused("an empty statement")
    if ";" in masked:
        raise OracleRefused("more than one statement")
    first = re.match(r"\s*([A-Za-z]+)", masked)
    if not first or first.group(1).upper() not in ("SELECT", "WITH"):
        raise OracleRefused("a SQL oracle must start with SELECT or WITH")
    for word in _SQL_FORBIDDEN:
        if re.search(rf"\b{word}\b", masked, re.IGNORECASE):
            raise OracleRefused(f"{word} is not allowed in a read-only oracle")
    return text.rstrip().rstrip(";").rstrip()


_CYPHER_MASK = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`|//[^\n]*|/\*.*?\*/",
                          re.DOTALL)
_CYPHER_FORBIDDEN = ("CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "FOREACH",
                     "LOAD", "GRANT", "DENY", "REVOKE", "USE", "SHOW", "TERMINATE", "ALTER")
_CYPHER_ALLOWED_CALL = re.compile(r"\bCALL\s*(?:\{|\(|db\.index\.fulltext\.queryNodes\s*\()",
                                  re.IGNORECASE)


def check_cypher(statement: str) -> str:
    """The statement, or OracleRefused for an obvious write, a second statement or a
    procedure other than the fulltext search. The READ transaction is the real guard."""
    text = (statement or "").strip()
    masked = _CYPHER_MASK.sub(" ", text).strip().rstrip(";")
    if not masked:
        raise OracleRefused("an empty statement")
    if ";" in masked:
        raise OracleRefused("more than one statement")
    for word in _CYPHER_FORBIDDEN:
        if re.search(rf"\b{word}\b", masked, re.IGNORECASE):
            raise OracleRefused(f"{word} is not allowed in a read-only oracle")
    calls = len(re.findall(r"\bCALL\b", masked, re.IGNORECASE))
    if calls != len(_CYPHER_ALLOWED_CALL.findall(masked)):
        raise OracleRefused("the only procedure allowed is db.index.fulltext.queryNodes")
    return text.rstrip().rstrip(";")


# ── oracle answers ───────────────────────────────────────────────────────────

def _plain(value):
    """A driver value as JSON-able data: Decimal to int or float, temporal types to text."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return str(value)


def oracle_value(engine: str, raw, kind: str):
    """Reduce an executor's raw answer to the truth's value.

    graph_search answers `{"total": n}`; cypher and sql answer rows (a list of dicts). A
    list or set answer takes the single column of every row; any other kind needs exactly
    one row with one column. `none` with no rows is 0.
    """
    if engine == "graph_search" and isinstance(raw, dict):
        if "total" not in raw:
            raise OracleFailed("the graph_search answer carries no total")
        raw = raw["total"]
    if not isinstance(raw, list):
        return _plain(raw)
    rows = [r if isinstance(r, dict) else {"value": r} for r in raw]
    if kind in ("list", "set"):
        if any(len(r) != 1 for r in rows):
            raise OracleFailed(f"a {kind} oracle must return one column")
        return [_plain(next(iter(r.values()))) for r in rows]
    if kind == "none" and not rows:
        return 0
    if len(rows) != 1 or len(rows[0]) != 1:
        raise OracleFailed(f"a {kind} oracle must return one value (one row, one column); "
                           f"got {len(rows)} row(s) of {len(rows[0]) if rows else 0} column(s)")
    return _plain(next(iter(rows[0].values())))


def run_oracle(oracle: et.Oracle, kind: str, executors, base_dir) -> object:
    try:
        run = executors[oracle.engine]
    except KeyError:
        raise OracleFailed(f"no executor for the {oracle.engine} engine here") from None
    return oracle_value(oracle.engine, run(oracle, base_dir), kind)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def values_agree(a, b) -> bool:
    if _is_number(a) and _is_number(b):
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-9)
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    return a == b


def _apply(expected: et.Expected, value) -> None:
    expected.value = value
    if _is_number(value):
        if expected.kind in ("count", "value"):
            expected.required_numbers = [value]
    elif isinstance(value, list):
        if (expected.kind in ("list", "set") and not expected.required_items
                and len(value) <= MAX_AUTO_ITEMS):
            expected.required_items = [str(v) for v in value]
    elif isinstance(value, str) and value and expected.kind == "value" and not expected.required_items:
        expected.required_items = [value]


def _needs_second(question: et.TruthQuestion, turn: et.TruthTurn) -> bool:
    flagged = {et.FLAG_CHANGED_BY_MERGE, et.FLAG_INTERPRETIVE} & set(question.flags)
    return bool(flagged) and turn.expected.kind == "count" and not turn.single_source


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fill_truth(truth: et.TruthFile, executors, *, only=None, now=None, base_dir=None) -> dict:
    """Run every oracle of `truth` (or of the `only` ids) and fill the answers in place.

    Excluded questions are skipped. An oracle failure is recorded and the rest carry on,
    so one bad statement costs one question, not the file. The file's fingerprint is
    stamped from the live graph; `fingerprint_changed` reports when it moved since the
    last stamp (a partial `--only` run over a moved graph mixes two data states).
    """
    now = now or _utc_now()
    report = {"file": truth.name, "filled": [], "errors": [], "disagreements": [],
              "needs_second_oracle": [], "no_oracle": [], "fingerprint_changed": None}
    for question in truth.questions:
        if only is not None and question.id not in only:
            continue
        if not question.scorable:
            continue
        filled = False
        for turn in question.turns:
            if turn.oracle is None:
                report["no_oracle"].append(question.id)
                continue
            try:
                value = run_oracle(turn.oracle, turn.expected.kind, executors, base_dir)
            except Exception as exc:  # one failed oracle must not cost the file
                report["errors"].append({"id": question.id, "turn": turn.label,
                                         "engine": turn.oracle.engine,
                                         "error": f"{type(exc).__name__}: {exc}"})
                continue
            _apply(turn.expected, value)
            turn.derived_at = now
            filled = True
            if turn.second_oracle is None:
                turn.second_value, turn.disagreement = None, None
                if _needs_second(question, turn):
                    report["needs_second_oracle"].append(question.id)
                continue
            try:
                second = run_oracle(turn.second_oracle, turn.expected.kind, executors, base_dir)
            except Exception as exc:
                report["errors"].append({"id": question.id, "turn": turn.label, "second": True,
                                         "engine": turn.second_oracle.engine,
                                         "error": f"{type(exc).__name__}: {exc}"})
                continue
            turn.second_value = second
            if values_agree(value, second):
                turn.disagreement = None
            else:
                turn.disagreement = (f"{turn.oracle.engine} gave {value!r}, "
                                     f"{turn.second_oracle.engine} gave {second!r}")
                report["disagreements"].append({"id": question.id, "turn": turn.label,
                                                "disagreement": turn.disagreement})
        if filled:
            report["filled"].append(question.id)
    try:
        live = executors["fingerprint"]()
    except Exception as exc:
        report["errors"].append({"id": None, "error": f"fingerprint: {type(exc).__name__}: {exc}"})
        return report
    previous = truth.fingerprint
    truth.fingerprint = et.Fingerprint(derived_at=now, **{k: live.get(k) for k in STAMP_FIELDS})
    if previous is not None:
        moved = _fingerprint_diff(previous, truth.fingerprint)
        report["fingerprint_changed"] = moved or None
    return report


def _fingerprint_diff(stamped: et.Fingerprint, live) -> dict:
    live = live.model_dump() if isinstance(live, et.Fingerprint) else dict(live)
    return {k: {"truth": getattr(stamped, k), "live": live.get(k)}
            for k in FINGERPRINT_FIELDS if getattr(stamped, k) != live.get(k)}


# ── measured numbers ─────────────────────────────────────────────────────────

def measured_value(oracle: et.Oracle, base_dir) -> object:
    """A number from a results file.

    A benchmark results file (`{"runs": [{"cells": [...]}]}`, as the graph_search
    benchmark writes them) is read by `params.query`, `params.arm` (default "G", the
    graph_search arm) and `params.account` (default "demo"): the last run holding that
    cell wins (a later rerun supersedes), and its successful samples must agree on one
    total. Any other JSON is read by a dotted `params.path`.
    """
    if not oracle.source:
        raise OracleFailed("a measured oracle names its results file in `source`")
    path = Path(oracle.source)
    if not path.is_absolute():
        path = Path(base_dir or ".") / path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OracleFailed(f"cannot read {path.name}: {type(exc).__name__}") from None
    params = oracle.params or {}
    if "path" in params:
        value = data
        for part in str(params["path"]).split("."):
            try:
                value = value[int(part)] if isinstance(value, list) else value[part]
            except (KeyError, IndexError, ValueError, TypeError):
                raise OracleFailed(f"{path.name} has nothing at {params['path']!r}") from None
        return _plain(value)
    if not (isinstance(data, dict) and isinstance(data.get("runs"), list)):
        raise OracleFailed(f"{path.name} is not a benchmark results file; give params.path")
    query = params.get("query")
    if not query:
        raise OracleFailed("a benchmark results file needs params.query (the query's name)")
    arm, account = params.get("arm", "G"), params.get("account", "demo")
    totals = None
    for run in data["runs"]:
        for cell in run.get("cells") or []:
            if (cell.get("query"), cell.get("arm"), cell.get("account")) != (query, arm, account):
                continue
            seen = {s.get("total") for s in cell.get("samples") or []
                    if s.get("status") == 200 and s.get("total") is not None}
            if seen:
                totals = seen
    if not totals:
        raise OracleFailed(f"no {arm}/{account} cell for {query!r} in {path.name}")
    if len(totals) > 1:
        raise OracleFailed(f"the samples for {query!r} disagree: {sorted(totals)}")
    return _plain(totals.pop())


# ── the live executors (the venue only) ──────────────────────────────────────

def _setup_django() -> None:
    import django
    from django.apps import apps
    if not apps.ready:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
        django.setup()


class _Live:
    """Lazily opened handles on the live stack. Nothing connects until an oracle needs it."""

    def __init__(self, base_url: str, user: str):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self._driver = None
        self._database = None

    def graph_search(self, oracle, base_dir):
        password = os.environ.get(PASSWORD_ENV)
        if not password:
            raise OracleFailed(f"{PASSWORD_ENV} is unset; the graph_search oracle signs in as "
                               f"{self.user} with it")
        query = urllib.parse.urlencode({"page_size": 1, **(oracle.params or {})})
        token = base64.b64encode(f"{self.user}:{password}".encode()).decode()
        request = urllib.request.Request(
            f"{self.base_url}{GRAPH_SEARCH_PATH}?{query}",
            data=json.dumps(oracle.body or {}).encode(), method="POST",
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise OracleFailed(f"graph_search answered HTTP {exc.code}") from None

    def _neo4j(self):
        if self._driver is None:
            _setup_django()
            import neo4j
            from django.conf import settings
            conf = settings.NEO4J_DATABASE
            self._driver = neo4j.GraphDatabase.driver(conf["URI"], auth=tuple(conf["AUTH"]))
            self._database = conf.get("NAME") or "neo4j"
        return self._driver, self._database

    def cypher(self, oracle, base_dir):
        import neo4j
        statement = check_cypher(oracle.statement or "")
        driver, database = self._neo4j()
        params = dict(oracle.params or {})

        @neo4j.unit_of_work(timeout=CYPHER_TIMEOUT_S)
        def work(tx):
            return [record.data() for record in tx.run(statement, params)]

        with driver.session(database=database) as session:
            return session.execute_read(work)

    def sql(self, oracle, base_dir):
        statement = check_sql(oracle.statement or "")
        _setup_django()
        from django.db import connections
        with connections["seek"].cursor() as cursor:
            cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(SQL_TIMEOUT_MS)}")
            cursor.execute("START TRANSACTION READ ONLY")
            try:
                if oracle.params:
                    cursor.execute(statement, oracle.params)
                else:
                    cursor.execute(statement)
                columns = [c[0] for c in cursor.description or []]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
            finally:
                cursor.execute("ROLLBACK")

    def fingerprint(self):
        meta = self.cypher(et.Oracle(engine="cypher", statement=FINGERPRINT_META), None)
        if len(meta) != 1:
            raise OracleFailed(f"expected one GraphMeta node, found {len(meta)}")
        count = self.cypher(et.Oracle(engine="cypher", statement=FINGERPRINT_COUNT), None)
        return {"sample_count": int(count[0]["n"]),
                "catalog_hash": str(meta[0].get("catalog_hash")),
                "synced_at": None if meta[0].get("synced_at") is None else str(meta[0]["synced_at"])}

    def close(self):
        if self._driver is not None:
            with contextlib.suppress(Exception):
                self._driver.close()


def live_executors(base_url: str = DEFAULT_BASE_URL, user: str = "demo") -> dict:
    live = _Live(base_url, user)
    return {"graph_search": live.graph_search, "cypher": live.cypher, "sql": live.sql,
            "measured": measured_value, "fingerprint": live.fingerprint}


# ── summary ──────────────────────────────────────────────────────────────────

def summarize(truths) -> dict:
    """What gate T reads: totals per group, source and family, and every list to review."""
    summary = {"files": [], "groups": {}, "changed_by_merge": [], "interpretive": [],
               "single_source": [], "entity_vocabulary_gap": [], "rest_routed_today": [],
               "broad_match": [], "excluded": {}, "merged": {}, "disagreements": [],
               "unfilled": [], "second_oracle": {"counts": 0, "with_second": 0}}
    for truth in truths:
        fp = truth.fingerprint.model_dump() if truth.fingerprint else None
        summary["files"].append({"name": truth.name, "group": truth.group,
                                 "questions": len(truth.questions), "fingerprint": fp})
        for q in truth.questions:
            g = summary["groups"].setdefault(q.group, {"questions": 0, "scorable": 0,
                                                       "by_source": {}, "by_family": {}})
            g["questions"] += 1
            g["by_source"][q.source] = g["by_source"].get(q.source, 0) + 1
            g["by_family"][q.family] = g["by_family"].get(q.family, 0) + 1
            for flag in ("changed_by_merge", "interpretive", "entity_vocabulary_gap",
                         "rest_routed_today", "broad_match"):
                if flag in q.flags:
                    summary[flag].append(q.id)
            if any(t.single_source for t in q.turns):
                summary["single_source"].append(q.id)
            if q.merged_into:
                summary["merged"][q.id] = q.merged_into
            if not q.scorable:
                summary["excluded"][q.id] = q.exclusion
            if not q.scorable or q.merged_into:
                continue
            g["scorable"] += 1
            for t in q.turns:
                if t.disagreement:
                    summary["disagreements"].append({"id": q.id, "disagreement": t.disagreement})
                if t.expected.value is None and t.expected.kind != "none":
                    summary["unfilled"].append(q.id)
                if t.expected.kind == "count":
                    summary["second_oracle"]["counts"] += 1
                    summary["second_oracle"]["with_second"] += t.second_oracle is not None
    return summary


# ── the command line ─────────────────────────────────────────────────────────

def _write_private(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _fingerprint_only(paths, executors, out) -> int:
    try:
        live = executors["fingerprint"]()
    except Exception as exc:
        print(f"STOP: the live fingerprint could not be read: {type(exc).__name__}: {exc}", file=out)
        return 2
    rc = 0
    for path in paths:
        truth = et.load_truth(path)
        if truth.fingerprint is None:
            print(f"{truth.name}: no fingerprint stamped (derive it first)", file=out)
            rc = 1
            continue
        diff = _fingerprint_diff(truth.fingerprint, live)
        for field, pair in diff.items():
            print(f"{truth.name}: {field} differs (truth {pair['truth']!r}, live {pair['live']!r})",
                  file=out)
        rc = rc or (1 if diff else 0)
    if rc == 0:
        print(f"fingerprint unchanged across {len(paths)} truth file(s): sample_count "
              f"{live.get('sample_count')}, catalog_hash {live.get('catalog_hash')}", file=out)
    return rc


def _print_report(report: dict, out) -> None:
    print(f"{report['file']}: filled {len(report['filled'])}, errors {len(report['errors'])}, "
          f"disagreements {len(report['disagreements'])}, needs a second oracle "
          f"{len(report['needs_second_oracle'])}, no oracle {len(report['no_oracle'])}", file=out)
    for err in report["errors"]:
        print(f"  error {err.get('id')}: {err['error']}", file=out)
    for d in report["disagreements"]:
        print(f"  disagreement {d['id']}: {d['disagreement']}", file=out)
    if report["needs_second_oracle"]:
        print(f"  needs a second oracle: {', '.join(report['needs_second_oracle'])}", file=out)
    if report["fingerprint_changed"]:
        print(f"  the fingerprint moved since the last stamp: {report['fingerprint_changed']}",
              file=out)


def main(argv=None, *, executors=None, now=None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(
        prog="derive_truth.py",
        description="Fill graph_search Nessie POC truth files from read-only oracles.")
    parser.add_argument("--truth", required=True,
                        help="a truth file, or a directory of them")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--summary", action="store_true",
                      help="print totals and the review lists; runs no oracle")
    mode.add_argument("--fingerprint-only", action="store_true",
                      help="compare the live data fingerprint with each file's; non-zero if moved")
    parser.add_argument("--only", default=None, help="comma-separated question ids to derive")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"where graph_search answers (default {DEFAULT_BASE_URL}, the venue)")
    parser.add_argument("--user", default="demo", help="the superuser graph_search signs in as")
    args = parser.parse_args(argv)

    paths = et.truth_paths(args.truth)
    if not paths:
        print(f"no truth file at {args.truth}", file=out)
        return 2
    if args.summary:
        truths = [et.load_truth(p) for p in paths]
        for path, truth in zip(paths, truths):
            print(f"{path.name} ({truth.name}): group {truth.group}, "
                  f"{len(truth.questions)} question(s)", file=out)
        print(json.dumps(summarize(truths), indent=2), file=out)
        return 0
    if executors is None:
        executors = live_executors(args.base_url, args.user)
    if args.fingerprint_only:
        return _fingerprint_only(paths, executors, out)
    only = {i.strip() for i in args.only.split(",") if i.strip()} if args.only else None
    rc = 0
    for path in paths:
        truth = et.load_truth(path)
        report = fill_truth(truth, executors, only=only, now=now, base_dir=path.parent)
        _write_private(path, truth.model_dump_json(indent=2))
        _print_report(report, out)
        if report["errors"]:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
