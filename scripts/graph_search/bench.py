"""graph_search benchmark harness (gate B): advanced_search (A), graph_search (G) and the SQL control (S).

Design section 9 and plan task B1 (docs/superpowers/specs/2026-09-14-graph-search-poc-design.md,
docs/superpowers/plans/2026-09-14-graph-search-poc.md). The query set is queries.json (E6). The first argument picks a
mode; every mode appends one run to ``<out-dir>/results.json`` (``bench_report.py`` renders it) and logs each sample to
``<out-dir>/progress.jsonl``. ``--out-dir`` defaults to ``$GS_RUN_DIR/B2`` in the lane and ``$GS_WORK/runs/B2`` on the
host.

``http``: A and G over HTTP, on the host, standard library plus requests::

    GS_DEMO_PASSWORD=... GS_TCGAMEMBER_PASSWORD=... GS_USER_PASSWORD=... \\
      uv run --no-project --with requests scripts/graph_search/bench.py http [--mode warm concurrent] [options]

  - ``--base-url`` (default ``$GS_BASE_URL``, else http://127.0.0.1:8000). Accounts sign in with HTTP Basic; each
    password comes from ``GS_<LOGIN>_PASSWORD`` in the environment, never from a file or the command line.
  - ``warm``: per query and account, 1 warm-up and 5 timed requests per arm, the arms interleaved.
  - ``concurrent``: per query, arm and account, 1 warm-up and then 4 clients sending the same request at once.
  - ``cold``: before each query and arm, the harness asks the operator to restart the database containers and waits
    for Enter. It checks with ``docker inspect`` (read-only) that every container named by ``--containers`` has a new
    start time, and runs demo first, so only demo's request is cold (``since_restart`` 1). The app's own caches (G's
    catalog) survive a database restart.
  - Every non-superuser request is paired with the same query as demo: demo is added when missing and always runs
    first. graph_only queries run on G only (advanced_search's body has no ``extensions``).
  - Per request: HTTP status, ``total``, wall time, response bytes, the page's ids, and for G the ``?debug_meta=1``
    timings (``cypher_ms``, ``count_ms``, ``hydrate_ms``, ``total_ms``). A is never sent ``debug_meta``.
  - The configuration is ``--config KEY=VALUE`` (for example ``mysql_buffer_pool=128M``), plus what ``docker
    inspect`` shows for ``--containers``: image, start time, memory limit and the Neo4j memory settings (no other
    environment is read).

``sql``: the S arm, in the lane, on the throwaway merged MySQL only (a ``gs-*`` host)::

    scripts/graph_search/lane.sh python scripts/graph_search/bench.py sql [options]

  Adds ``idx_samples_sample_type_id`` on ``samples(sample_type_id)`` when it is missing, then, for each compat query
  and account, takes the statements advanced_search's engine builds for that scope (``parity.engine_statements``, the
  builder in ``seek/sample/queries.py``) and times three forms: ``page``, the engine's statement with ``ORDER BY A.id
  LIMIT 100``; ``count``, ``SELECT COUNT(A.id)`` with the same FROM and WHERE; and ``engine``, the statement exactly
  as advanced_search runs it (no ORDER BY, no LIMIT, every row streamed and discarded), which is A's captured SQL
  time. Run 1 of each form is the first run; the others are warm. S's count is the SQL stage's; advanced_search's
  Python stage can still drop rows (``python_stage``).

``memory``: peak RSS growth of one request through each view, in the lane (never a live worker)::

    scripts/graph_search/lane.sh python scripts/graph_search/bench.py memory [--shapes ...] [--budget-mb 1536]

  Each (arm, shape) runs in its own child process: a small warm-up request (uids_50), then ``getrusage`` peak RSS
  before and after the measured request (page 1, page size 100). The child's address space is capped at its size
  before the request plus ``--budget-mb``, so a request that would outgrow the budget fails there and is reported as
  ``over_budget`` (its growth is a lower bound) instead of pressing on the lane's container cap. The default shapes
  are the two broadest, by match count: type_only_avcf and type_only_near_100k.

``serve``: the lane's graph_search over HTTP, for a dry run of ``http`` with no live stack::

    scripts/graph_search/lane.sh python scripts/graph_search/bench.py serve [--workers 1 --threads 4]

  gunicorn on gs-net (port 8000 of the lane's app container, reachable from the host at the container's gs-net
  address). advanced_search needs SEEK, which the lane does not run, so only ``--arms G`` works against it.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
DEFAULT_QUERIES = HERE / "queries.json"
SUPERUSER = "demo"
DEFAULT_ACCOUNTS = ("demo", "tcgamember", "user")
ARM_PATHS = {"A": "/nextseek_api/samples/advanced_search/", "G": "/nextseek_api/samples/graph_search/"}
PAGE_SIZE = 100
INDEX_NAME = "idx_samples_sample_type_id"
BROADEST = ("type_only_avcf", "type_only_near_100k")
MEMORY_WARMUP = "uids_50"
FORMAT = 1
MIB = 1024 * 1024
# Exception text that means an allocation was refused (MemoryError itself is matched by type).
_MEMORY_TEXT = ("out of memory", "not enough memory", "cannot allocate", "memory allocation")
# docker inspect: the only environment entries read, so no secret leaves a container's configuration.
_MEMORY_ENV = ("NEO4J_server_memory_", "NEO4J_dbms_memory_", "NEO4J_db_memory_", "NEXTSEEK_MEMORY")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _log(message: str) -> None:
    print(f"{_now()} {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------------------------------------------
# Queries, results and configuration
# ---------------------------------------------------------------------------------------------------------------

def load_queries(path: Path, only) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    queries = json.loads(raw)
    if only:
        unknown = set(only) - {q["name"] for q in queries}
        if unknown:
            raise SystemExit(f"unknown query names: {sorted(unknown)}")
        queries = [q for q in queries if q["name"] in only]
    return queries, hashlib.sha256(raw).hexdigest()


def default_out_dir() -> Path | None:
    if os.environ.get("GS_RUN_DIR"):
        return Path(os.environ["GS_RUN_DIR"]) / "B2"
    if os.environ.get("GS_WORK"):
        return Path(os.environ["GS_WORK"]) / "runs" / "B2"
    return None


class Results:
    """``results.json`` (a list of runs, appended under a lock) and ``progress.jsonl`` (one line per sample)."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        self.path = out_dir / "results.json"
        self.progress = out_dir / "progress.jsonl"

    def event(self, event: dict) -> None:
        with self.progress.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": _now(), **event}, default=str) + "\n")

    def append_run(self, run: dict) -> None:
        with (self.out_dir / ".results.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = {"format": FORMAT, "runs": []}
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
            data["runs"].append(run)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
            os.replace(tmp, self.path)


def parse_config(pairs) -> dict:
    out = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"--config takes KEY=VALUE, not {pair!r}")
        out[key.strip()] = value.strip()
    return out


def new_run(kind: str, mode: str, args, queries_sha: str) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return {
        "run_id": f"{stamp}-{kind}-{mode}",
        "kind": kind,
        "mode": mode,
        "config_label": args.config_label,
        "config": {"given": parse_config(args.config)},
        "started_at": _now(),
        "queries": str(args.queries),
        "queries_sha256": queries_sha,
        "argv": sys.argv[1:],
        "host": {"python": platform.python_version(), "node": platform.node()},
        "cells": [],
    }


def _docker_inspect(name: str) -> dict | None:
    if not shutil.which("docker"):
        return None
    proc = subprocess.run(["docker", "inspect", name], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)[0]
    except (ValueError, IndexError):
        return None


def describe_containers(names) -> dict:
    """What ``docker inspect`` shows about each container's memory: image, start time, limit and memory settings."""
    out = {}
    for name in names or []:
        info = _docker_inspect(name)
        if info is None:
            out[name] = {"inspected": False}
            continue
        env = [e for e in (info.get("Config") or {}).get("Env") or [] if e.startswith(_MEMORY_ENV)]
        cmd = [c for c in (info.get("Config") or {}).get("Cmd") or [] if "buffer" in c or "innodb" in c]
        limit = (info.get("HostConfig") or {}).get("Memory") or 0
        out[name] = {
            "inspected": True,
            "image": (info.get("Config") or {}).get("Image"),
            "started_at": (info.get("State") or {}).get("StartedAt"),
            "memory_limit_mb": round(limit / MIB) if limit else None,
            "memory_env": env,
            "memory_cmd": cmd,
        }
    return out


def container_started(names) -> dict:
    return {name: ((_docker_inspect(name) or {}).get("State") or {}).get("StartedAt") for name in names or []}


# ---------------------------------------------------------------------------------------------------------------
# http: A and G over HTTP
# ---------------------------------------------------------------------------------------------------------------

def ordered_accounts(accounts) -> list[str]:
    """demo first; demo added when a non-superuser account would otherwise run unpaired."""
    accounts = list(dict.fromkeys(accounts))
    if SUPERUSER not in accounts:
        _log(f"adding {SUPERUSER}: every non-superuser run is paired with the same query as {SUPERUSER}")
    return [SUPERUSER] + [a for a in accounts if a != SUPERUSER]


def account_auth(accounts) -> dict[str, tuple[str, str]]:
    out, missing = {}, []
    for login in accounts:
        var = f"GS_{login.upper()}_PASSWORD"
        value = os.environ.get(var)
        if value:
            out[login] = (login, value)
        else:
            missing.append(var)
    if missing:
        raise SystemExit(f"set {', '.join(missing)} in the environment (never in a file or on the command line)")
    return out


def arms_for(q: dict, arms) -> list[str]:
    return [a for a in arms if a == "G" or q["kind"] == "compat"]


def post(session, ctx, arm: str, body: dict, auth) -> tuple[dict, list | None]:
    """One request; returns the sample and the page's ids (None when the answer is not a 200 envelope)."""
    import requests

    params = {"page": 1, "page_size": ctx.page_size}
    if arm == "G":
        params["debug_meta"] = 1
    url = ctx.base_url.rstrip("/") + ARM_PATHS[arm]
    start = time.perf_counter()
    try:
        resp = session.post(url, params=params, json=body, auth=auth, timeout=ctx.timeout)
        content = resp.content
    except requests.RequestException as exc:
        return {"status": None, "wall_ms": _ms(start), "error": f"{type(exc).__name__}: {exc}"[:300]}, None
    sample = {"status": resp.status_code, "wall_ms": _ms(start), "bytes": len(content)}
    try:
        data = json.loads(content)
    except ValueError:
        sample["error"] = "the body is not JSON: " + content[:160].decode("utf-8", "replace")
        return sample, None
    if resp.status_code != 200 or not isinstance(data, dict):
        sample["error"] = json.dumps(data)[:300]
        return sample, None
    ids = [row.get("id") for row in data.get("rows") or [] if isinstance(row, dict)]
    sample.update({"total": data.get("total"), "rows": len(ids)})
    debug = next((f["debug"] for f in data.get("footer") or [] if isinstance(f, dict) and "debug" in f), None)
    if arm == "G" and isinstance(debug, dict):
        sample["db"] = {k: debug.get(k) for k in ("cypher_ms", "count_ms", "hydrate_ms", "total_ms")}
    return sample, ids


class HttpContext:
    def __init__(self, args, results: Results, run: dict):
        import requests

        self.requests = requests
        self.base_url = args.base_url
        self.page_size = args.page_size
        self.timeout = args.timeout
        self.warmups = args.warmups
        self.runs = args.runs
        self.clients = args.clients
        self.rounds = args.rounds
        self.containers = args.containers
        self.results = results
        self.run = run
        self.sessions: dict[str, object] = {}

    def session(self, account: str):
        if account not in self.sessions:
            self.sessions[account] = self.requests.Session()
        return self.sessions[account]


def _cell(q: dict, arm: str, account: str) -> dict:
    return {"query": q["name"], "kind": q["kind"], "arm": arm, "account": account,
            "paired_with": None if account == SUPERUSER else SUPERUSER, "samples": [], "page_ids": None}


def _record(ctx: HttpContext, cell: dict, sample: dict, ids) -> None:
    cell["samples"].append(sample)
    if cell["page_ids"] is None and ids is not None:
        cell["page_ids"] = ids
    ctx.results.event({"run_id": ctx.run["run_id"], "query": cell["query"], "arm": cell["arm"],
                       "account": cell["account"], **{k: v for k, v in sample.items()}})


def warm_mode(queries, arms, accounts, auth, ctx: HttpContext) -> list[dict]:
    cells = []
    for q in queries:
        for account in accounts:
            q_arms = arms_for(q, arms)
            by_arm = {arm: _cell(q, arm, account) for arm in q_arms}
            session = ctx.session(account)
            for i in range(ctx.warmups + ctx.runs):
                for arm in q_arms:
                    sample, ids = post(session, ctx, arm, q["body"], auth[account])
                    sample.update({"run": i + 1, "warmup": i < ctx.warmups})
                    _record(ctx, by_arm[arm], sample, ids)
            for arm, cell in by_arm.items():
                _log(f"warm {q['name']} {arm} {account}: " + _brief(cell))
            cells.extend(by_arm.values())
    return cells


def concurrent_mode(queries, arms, accounts, auth, ctx: HttpContext) -> list[dict]:
    cells = []
    for q in queries:
        for account in accounts:
            for arm in arms_for(q, arms):
                cell = _cell(q, arm, account)
                cell["clients"] = ctx.clients
                cell["makespan_ms"] = []
                for i in range(ctx.warmups):
                    sample, ids = post(ctx.session(account), ctx, arm, q["body"], auth[account])
                    sample.update({"run": i + 1, "warmup": True})
                    _record(ctx, cell, sample, ids)
                for rnd in range(ctx.rounds):
                    barrier = threading.Barrier(ctx.clients)
                    out: list = [None] * ctx.clients

                    def client(n: int, barrier=barrier, out=out, arm=arm, body=q["body"], cred=auth[account]) -> None:
                        session = ctx.requests.Session()
                        try:
                            barrier.wait()
                            began = time.perf_counter()
                            sample, ids = post(session, ctx, arm, body, cred)
                            out[n] = (began, time.perf_counter(), sample, ids)
                        finally:
                            session.close()

                    threads = [threading.Thread(target=client, args=(n,)) for n in range(ctx.clients)]
                    for t in threads:
                        t.start()
                    for t in threads:
                        t.join()
                    done = [o for o in out if o is not None]
                    if done:
                        cell["makespan_ms"].append(round((max(o[1] for o in done) - min(o[0] for o in done)) * 1000, 3))
                    for n, o in enumerate(out):
                        if o is None:
                            _record(ctx, cell, {"status": None, "error": "the client thread failed",
                                                "round": rnd + 1, "client": n + 1, "warmup": False}, None)
                            continue
                        sample, ids = o[2], o[3]
                        sample.update({"round": rnd + 1, "client": n + 1, "warmup": False})
                        _record(ctx, cell, sample, ids)
                _log(f"concurrent {q['name']} {arm} {account}: " + _brief(cell)
                     + f", makespan {cell['makespan_ms']} ms")
                cells.append(cell)
    return cells


def wait_for_restart(ctx: HttpContext, label: str) -> dict:
    """Prompt the operator to restart the database containers and wait until each named one has restarted."""
    before = container_started(ctx.containers)
    names = ", ".join(ctx.containers) or "the database containers"
    while True:
        print(f"\nCold run, {label}: restart {names} now, wait until the stack answers, then press Enter "
              "(q stops the run).", file=sys.stderr, flush=True)
        try:
            line = input()
        except EOFError:
            raise SystemExit("cold mode needs an operator at the terminal: stdin is closed") from None
        if line.strip().lower() == "q":
            raise SystemExit("cold run stopped by the operator")
        after = container_started(ctx.containers)
        stale = [c for c in ctx.containers if after.get(c) is None or after.get(c) == before.get(c)]
        if not ctx.containers:
            return {"verified": False, "started_at": {}}
        if not stale:
            return {"verified": True, "started_at": after}
        print(f"not restarted yet (start time unchanged or not inspectable): {', '.join(stale)}",
              file=sys.stderr, flush=True)


def cold_mode(queries, arms, accounts, auth, ctx: HttpContext) -> list[dict]:
    cells = []
    for q in queries:
        for arm in arms_for(q, arms):
            restart = wait_for_restart(ctx, f"{q['name']} on {arm}")
            for position, account in enumerate(accounts, start=1):
                cell = _cell(q, arm, account)
                cell["restart"] = restart
                session = ctx.requests.Session()
                try:
                    sample, ids = post(session, ctx, arm, q["body"], auth[account])
                finally:
                    session.close()
                sample.update({"run": 1, "warmup": False, "since_restart": position})
                _record(ctx, cell, sample, ids)
                _log(f"cold {q['name']} {arm} {account} (request {position} since the restart): " + _brief(cell))
                cells.append(cell)
    return cells


def _brief(cell: dict) -> str:
    timed = [s for s in cell["samples"] if not s.get("warmup")]
    ok = sorted(s["wall_ms"] for s in timed if s.get("status") == 200)
    statuses = sorted({str(s.get("status")) for s in timed})
    total = next((s.get("total") for s in timed if s.get("status") == 200), None)
    mid = ok[len(ok) // 2] if ok else None
    err = next((s.get("error") for s in timed if s.get("error")), "")
    return (f"status {'/'.join(statuses)}, total {total}, median {mid} ms over {len(ok)}"
            + (f"; {err[:120]}" if err else ""))


def http_main(args) -> int:
    if not args.out_dir:
        raise SystemExit("set GS_WORK (or pass --out-dir)")
    queries, sha = load_queries(args.queries, args.only)
    accounts = ordered_accounts(args.accounts)
    auth = account_auth(accounts)
    results = Results(args.out_dir)
    status = 0
    for mode in args.mode:
        run = new_run("http", mode, args, sha)
        run["config"].update({"base_url": args.base_url, "page_size": args.page_size, "arms": args.arms,
                              "accounts": accounts, "warmups": args.warmups, "runs": args.runs,
                              "clients": args.clients if mode == "concurrent" else None,
                              "rounds": args.rounds if mode == "concurrent" else None,
                              "containers": describe_containers(args.containers)})
        ctx = HttpContext(args, results, run)
        _log(f"run {run['run_id']} against {args.base_url}: {len(queries)} queries, arms {args.arms}, "
             f"accounts {accounts}")
        worker = {"warm": warm_mode, "concurrent": concurrent_mode, "cold": cold_mode}[mode]
        try:
            run["cells"] = worker(queries, args.arms, accounts, auth, ctx)
        except KeyboardInterrupt:
            run["interrupted"] = True
            status = 130
        run["finished_at"] = _now()
        results.append_run(run)
        _log(f"run {run['run_id']} written to {results.path}")
        if status:
            break
    return status


# ---------------------------------------------------------------------------------------------------------------
# Lane helpers (Django): sql, memory, serve
# ---------------------------------------------------------------------------------------------------------------

def _lane():
    """parity (which sets Django up and quiets its loggers); only inside the lane."""
    if os.environ.get("DJANGO_SETTINGS_MODULE") != "gs_lane_settings":
        raise SystemExit("this mode runs in the lane: scripts/graph_search/lane.sh python "
                         "scripts/graph_search/bench.py ...")
    sys.path.insert(0, str(HERE))
    import parity  # django.setup() and the logger levels happen at its import

    return parity


def lane_scope(login: str):
    """The account's Django user and graph_search Scope, read from the lane's MySQL."""
    from django.contrib.auth import get_user_model

    from nextseek_api.graph_search.scope import resolve_scope

    user = get_user_model().objects.get(username=login)
    return user, resolve_scope(user)


def _seek_raw_connection():
    from django.conf import settings
    from django.db import connections

    alias = settings.SEEK_DATABASE
    host = str(settings.DATABASES[alias].get("HOST") or "")
    if not host.startswith("gs-"):
        raise SystemExit(f"refusing: this runs on the throwaway merged MySQL only (a gs-* host), not {host!r}")
    conn = connections[alias]
    conn.ensure_connection()
    return conn.connection, host


def _one(raw, sql: str, params=None):
    cur = raw.cursor()
    try:
        cur.execute(sql, params) if params else cur.execute(sql)
        return cur.fetchall()
    finally:
        cur.close()


def ensure_index(raw, skip: bool) -> dict:
    present = _one(raw, "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() "
                        "AND table_name = 'samples' AND index_name = %s", [INDEX_NAME])[0][0]
    if present:
        return {"name": INDEX_NAME, "present": True, "created": False}
    if skip:
        return {"name": INDEX_NAME, "present": False, "created": False}
    _one(raw, "SET SESSION lock_wait_timeout = 120")
    start = time.perf_counter()
    _one(raw, f"ALTER TABLE samples ADD INDEX {INDEX_NAME} (sample_type_id), ALGORITHM=INPLACE, LOCK=NONE")
    return {"name": INDEX_NAME, "present": True, "created": True, "create_ms": _ms(start)}


def s_forms(statements: list[dict], select_list: str, page_size: int) -> dict:
    """The page, count and engine forms of the engine's id-only statements (``SELECT A.id`` + FROM + WHERE)."""
    rests = []
    for st in statements:
        if not st["sql"].startswith("SELECT A.id "):
            raise RuntimeError(f"unexpected statement shape: {st['sql'][:80]!r}")
        rests.append((st["sql"][len("SELECT A.id"):], list(st["params"])))
    engine = [(select_list + rest, params) for rest, params in rests]
    if len(rests) == 1:
        (rest, params), = rests
        page = (select_list + rest + f" ORDER BY A.id LIMIT {int(page_size)}", params)
        count = ("SELECT COUNT(A.id)" + rest, params)
    else:  # a mixed UID-plus-text search: the view unions its partial searches
        params = [p for _rest, ps in rests for p in ps]
        page = ("SELECT * FROM (" + " UNION ".join(f"({select_list}{rest})" for rest, _p in rests)
                + f") u ORDER BY u.id LIMIT {int(page_size)}", params)
        count = ("SELECT COUNT(*) FROM (" + " UNION ".join(f"(SELECT A.id{rest})" for rest, _p in rests)
                 + ") u", params)
    return {"page": [page], "count": [count], "engine": engine}


def run_form(raw, form: str, statements: list[tuple[str, list]]) -> dict:
    """One timed execution of a form; the engine form streams every row through a server-side cursor."""
    import MySQLdb
    import MySQLdb.cursors

    rows, value = 0, None
    start = time.perf_counter()
    try:
        for sql, params in statements:
            cur = raw.cursor(MySQLdb.cursors.SSCursor) if form == "engine" else raw.cursor()
            try:
                cur.execute(sql, params) if params else cur.execute(sql)
                if form == "engine":
                    while True:
                        chunk = cur.fetchmany(2000)
                        if not chunk:
                            break
                        rows += len(chunk)
                else:
                    fetched = cur.fetchall()
                    rows += len(fetched)
                    if form == "count":
                        value = int(fetched[0][0])
            finally:
                cur.close()
    except MySQLdb.OperationalError as exc:
        code = exc.args[0] if exc.args else None
        return {"ms": _ms(start), "status": "timeout" if code in (3024, 1317) else "error",
                "error": f"{code}: {exc.args[1] if len(exc.args) > 1 else exc}"[:200]}
    return {"ms": _ms(start), "status": "ok", "rows": rows, **({"value": value} if form == "count" else {})}


def explain(raw, sql: str, params) -> list[dict]:
    cur = raw.cursor()
    try:
        cur.execute("EXPLAIN " + sql, params) if params else cur.execute("EXPLAIN " + sql)
        names = [d[0] for d in cur.description]
        return [{k: row[names.index(k)] for k in ("table", "type", "key", "rows", "Extra") if k in names}
                for row in cur.fetchall()]
    finally:
        cur.close()


def sql_main(args) -> int:
    parity = _lane()
    from seek.dbtable_sample import DBtable_sample

    if not args.out_dir:
        raise SystemExit("GS_RUN_DIR is not set (or pass --out-dir)")
    queries, sha = load_queries(args.queries, args.only)
    compat = [q for q in queries if q["kind"] == "compat"]
    accounts = ordered_accounts(args.accounts)
    results = Results(args.out_dir)
    run = new_run("sql", "s_arm", args, sha)
    raw, host = _seek_raw_connection()
    pool, version = _one(raw, "SELECT @@innodb_buffer_pool_size, @@version")[0]
    samples = _one(raw, "SELECT COUNT(*) FROM samples")[0][0]
    _one(raw, f"SET SESSION max_execution_time = {int(args.statement_timeout * 1000)}")
    index = ensure_index(raw, args.no_index)
    run["config"].update({"mysql_host": host, "mysql_version": version, "mysql_buffer_pool_mb": int(pool) // MIB,
                          "samples": int(samples), "index": index, "runs": 1 + args.runs,
                          "page_size": args.page_size, "statement_timeout_s": args.statement_timeout,
                          "accounts": accounts})
    _log(f"S arm on {host} (MySQL {version}, buffer pool {int(pool) // MIB} MiB, {samples:,} samples); "
         f"index {index}")
    select_list = DBtable_sample()._sqlQuery_select_records_select()
    scopes = {login: lane_scope(login)[1] for login in accounts}
    for q in compat:
        for account in accounts:
            scope = scopes[account]
            cell = {"query": q["name"], "kind": "compat", "arm": "S", "account": account,
                    "paired_with": None if account == SUPERUSER else SUPERUSER,
                    "scope": {"is_admin": scope.is_admin, "project_ids": list(scope.project_ids)}, "forms": {}}
            try:
                filters = parity.advanced_filters(q["body"])
                with parity._quiet():
                    statements, python_stage = parity.engine_statements(q["body"], filters, scope)
                forms = s_forms(statements, select_list, args.page_size)
                cell.update({"python_stage": python_stage, "statements": len(statements)})
                for form in ("page", "count", "engine"):
                    runs = []
                    for i in range(1 + args.runs):
                        sample = run_form(raw, form, forms[form])
                        sample.update({"run": i + 1, "first": i == 0})
                        runs.append(sample)
                        results.event({"run_id": run["run_id"], "query": q["name"], "arm": "S", "account": account,
                                       "form": form, **sample})
                        if sample["status"] != "ok":
                            break
                    entry = {"samples": runs}
                    if form in ("page", "count"):
                        entry["explain"] = explain(raw, *forms[form][0])
                    cell["forms"][form] = entry
            except Exception as exc:
                cell["error"] = f"{type(exc).__name__}: {exc}"[:300]
            run["cells"].append(cell)
            _log(f"S {q['name']} {account}: " + ", ".join(
                f"{form} {_median([s['ms'] for s in e['samples'][1:] if s['status'] == 'ok'])} ms"
                f" (first {e['samples'][0]['ms'] if e['samples'] else None})"
                for form, e in cell["forms"].items())
                + f", count {((cell['forms'].get('count') or {}).get('samples') or [{}])[0].get('value')}"
                + (f"; {cell['error']}" if cell.get("error") else ""))
    run["finished_at"] = _now()
    results.append_run(run)
    _log(f"run {run['run_id']} written to {results.path}")
    return 0 if all(not c.get("error") for c in run["cells"]) else 1


def _median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else None


# ---------------------------------------------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------------------------------------------

def _proc_status_kb(field: str) -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(field + ":"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def _rss_peak_mb() -> float:
    import resource

    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def view_call(parity, arm: str, body: dict, user, scope, page_size: int = PAGE_SIZE) -> dict:
    """One request through the arm's view, as the stack would serve it (page 1), without HTTP."""
    from unittest import mock

    from rest_framework.test import APIRequestFactory

    from nextseek_api.services import samples as advanced
    from nextseek_api.services.graph_search import GraphSearchViewSet

    query = urlencode({"page": 1, "page_size": page_size})
    request = APIRequestFactory().post(ARM_PATHS[arm] + "?" + query, data=json.dumps(body),
                                       content_type="application/json")
    request.user = user
    request.data = body
    request.query_params = request.GET
    with contextlib.ExitStack() as stack:
        if arm == "A":
            # advanced_search asks SEEK REST for a non-superuser's projects; the lane has no SEEK, so it is handed
            # the projects the scope resolver reads (parity.py does the same).
            stack.enter_context(mock.patch.object(advanced, "resolve_seek_auth",
                                                  return_value=((user.username, "gs-bench"), None)))
            stack.enter_context(mock.patch.object(advanced, "SeekDB", parity._seekdb_returning(scope.project_ids)))
            stack.enter_context(parity._quiet())
            response = advanced.SampleAdvancedSearchViewSet().create(request)
        else:
            response = GraphSearchViewSet().create(request)
    out = {"status": response.status_code, "bytes": len(response.content)}
    if response.status_code == 200:
        data = json.loads(response.content)
        out.update({"total": data.get("total"), "rows": len(data.get("rows") or [])})
    else:
        out["error"] = response.content[:200].decode("utf-8", "replace")
    return out


def memory_child_main(args) -> int:
    """One measurement in a fresh process; prints one JSON line."""
    import resource

    parity = _lane()
    queries, _sha = load_queries(args.queries, None)
    by_name = {q["name"]: q for q in queries}
    q = by_name[args.query]
    user, scope = lane_scope(args.account)
    out = {"query": q["name"], "arm": args.arm, "account": args.account, "budget_mb": args.budget_mb}
    if MEMORY_WARMUP in by_name and MEMORY_WARMUP != q["name"]:
        warm = view_call(parity, args.arm, by_name[MEMORY_WARMUP]["body"], user, scope)
        out["warmup"] = {"query": MEMORY_WARMUP, "status": warm["status"]}

    # advanced_search's view turns any exception into a 502, so a refused allocation looks like any other failure.
    # A sys.monitoring RAISE hook sees every exception raised in a Python frame (a C call's included), wherever the
    # view later catches it, so the cause survives. A large allocation refused at the cap never raises VmPeak.
    memory_errors: list[str] = []
    raised: dict[str, int] = {}  # every distinct exception raised during the request, for the record
    monitoring = sys.monitoring
    tool = next(i for i in range(6) if monitoring.get_tool(i) is None)

    def on_raise(code, _offset, exc):
        with contextlib.suppress(Exception):
            key = f"{type(exc).__module__}.{type(exc).__name__} in {code.co_qualname}: {str(exc)[:120]}"
            if key in raised or len(raised) < 40:
                raised[key] = raised.get(key, 0) + 1
            # orjson reports a refused buffer as JSONDecodeError "Not enough memory to allocate buffer ..."
            text = str(exc).lower()
            if isinstance(exc, MemoryError) or any(m in text for m in _MEMORY_TEXT):
                memory_errors.append(f"{type(exc).__name__} in {code.co_qualname}: {str(exc)[:80]}")

    vm_kb = _proc_status_kb("VmSize") or 0
    limit = vm_kb * 1024 + args.budget_mb * MIB
    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    out.update({"rss_before_mb": _rss_peak_mb(), "vm_before_mb": round(vm_kb / 1024, 1),
                "vm_limit_mb": round(limit / MIB, 1)})
    monitoring.use_tool_id(tool, "gs-bench-memory")
    monitoring.register_callback(tool, monitoring.events.RAISE, on_raise)
    start = time.perf_counter()
    try:
        monitoring.set_events(tool, monitoring.events.RAISE)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
        try:
            result = view_call(parity, args.arm, q["body"], user, scope)
        finally:
            resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
            monitoring.set_events(tool, 0)
    except MemoryError:
        result = {"status": None, "error": "MemoryError outside the view's own handlers"}
    finally:
        monitoring.register_callback(tool, monitoring.events.RAISE, None)
        monitoring.free_tool_id(tool)
    out["wall_ms"] = _ms(start)
    out.update(result)
    out.update({"rss_after_mb": _rss_peak_mb(), "vm_peak_mb": round((_proc_status_kb("VmPeak") or 0) / 1024, 1)})
    out["growth_mb"] = round(out["rss_after_mb"] - out["rss_before_mb"], 1)
    if memory_errors:
        out["memory_error"] = memory_errors[0]
    if out.get("status") != 200:
        out["raised"] = raised  # what failed; a clean request raises dozens of incidental exceptions
    hit_limit = (bool(memory_errors) or out["vm_peak_mb"] >= out["vm_limit_mb"] - 64
                 or "MemoryError" in str(out.get("error", "")))
    out["status_kind"] = "ok" if out.get("status") == 200 and not hit_limit else (
        "over_budget" if hit_limit else "error")
    print(json.dumps(out, default=str), flush=True)
    return 0


def memory_main(args) -> int:
    if os.environ.get("DJANGO_SETTINGS_MODULE") != "gs_lane_settings":
        raise SystemExit("memory runs in the lane: scripts/graph_search/lane.sh python "
                         "scripts/graph_search/bench.py memory ...")
    if not args.out_dir:
        raise SystemExit("GS_RUN_DIR is not set (or pass --out-dir)")
    queries, sha = load_queries(args.queries, None)
    names = {q["name"] for q in queries}
    unknown = set(args.shapes) - names
    if unknown:
        raise SystemExit(f"unknown query names: {sorted(unknown)}")
    results = Results(args.out_dir)
    run = new_run("memory", "rss", args, sha)
    cgroup = {}
    for name in ("memory.max", "memory.peak"):
        with contextlib.suppress(OSError):
            cgroup[name] = Path("/sys/fs/cgroup", name).read_text().strip()
    run["config"].update({"budget_mb": args.budget_mb, "account": args.account, "page_size": PAGE_SIZE,
                          "shapes": args.shapes, "arms": args.arms, "container_memory_max": cgroup.get("memory.max")})
    for shape in args.shapes:
        for arm in args.arms:
            cmd = [sys.executable, str(Path(__file__).resolve()), "memory-child", "--query", shape, "--arm", arm,
                   "--account", args.account, "--budget-mb", str(args.budget_mb), "--queries", str(args.queries)]
            start = time.perf_counter()
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
                lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
                cell = json.loads(lines[-1]) if lines else {
                    "query": shape, "arm": arm, "account": args.account, "status_kind": "killed",
                    "error": f"the child exited {proc.returncode} without a result: {proc.stderr[-300:]}"}
                cell["returncode"] = proc.returncode
            except subprocess.TimeoutExpired:
                cell = {"query": shape, "arm": arm, "account": args.account, "status_kind": "timeout",
                        "error": f"no result within {args.timeout} s"}
            cell["child_ms"] = _ms(start)
            with contextlib.suppress(OSError):
                cell["container_memory_peak_mb"] = round(int(Path("/sys/fs/cgroup/memory.peak").read_text()) / MIB, 1)
            run["cells"].append(cell)
            results.event({"run_id": run["run_id"], **cell})
            _log(f"memory {shape} {arm}: {cell.get('status_kind')} status {cell.get('status')} total "
                 f"{cell.get('total')} growth {cell.get('growth_mb')} MiB (budget {args.budget_mb}) "
                 f"{cell.get('memory_error', '') or cell.get('error', '')}"[:300])
    run["finished_at"] = _now()
    results.append_run(run)
    _log(f"run {run['run_id']} written to {results.path}")
    return 0 if all(c.get("status_kind") in ("ok", "over_budget") for c in run["cells"]) else 1


# ---------------------------------------------------------------------------------------------------------------
# serve: the lane's endpoint over HTTP, for a dry run
# ---------------------------------------------------------------------------------------------------------------

def lane_wsgi():
    """gunicorn application factory (``bench:lane_wsgi()``): Django's WSGI app with seek's URLconf imported first,
    so its DEBUG ``basicConfig`` is overridden before the first request."""
    from django.core.wsgi import get_wsgi_application

    application = get_wsgi_application()
    from django.urls import get_resolver

    get_resolver().url_patterns  # noqa: B018 (imports every URLconf now)
    import logging

    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("neo4j").setLevel(logging.WARNING)
    logging.getLogger("seek").setLevel(logging.INFO)
    return application


def serve_main(args) -> int:
    if (os.environ.get("DJANGO_SETTINGS_MODULE") != "gs_lane_settings"
            or not os.environ.get("NEXTSEEK_NEO4J_HOST", "").startswith("gs-")):
        raise SystemExit("serve is for the lane only: scripts/graph_search/lane.sh python "
                         "scripts/graph_search/bench.py serve")
    gunicorn = Path(sys.executable).with_name("gunicorn")
    env = dict(os.environ, DJANGO_ALLOWED_HOSTS=args.allowed_hosts)
    argv = [str(gunicorn), "--pythonpath", str(HERE), "--bind", args.bind, "--workers", str(args.workers),
            "--threads", str(args.threads), "--timeout", "1200", "--log-level", "info",
            "--access-logfile", "-", "bench:lane_wsgi()"]
    _log(f"serving the lane on {args.bind}: {' '.join(argv[1:])}")
    os.execve(argv[0], argv, env)
    return 0  # not reached


# ---------------------------------------------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, out=True):
        sp.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
        if out:
            sp.add_argument("--out-dir", type=Path, default=default_out_dir())
            sp.add_argument("--config-label", default="as-deployed",
                            help="names the configuration in the report (for example as-deployed, equal-budget)")
            sp.add_argument("--config", action="append", default=[], metavar="KEY=VALUE",
                            help="a configuration fact to record, for example mysql_buffer_pool=128M")

    h = sub.add_parser("http", help="A and G over HTTP (host)")
    common(h)
    h.add_argument("--base-url", default=os.environ.get("GS_BASE_URL", "http://127.0.0.1:8000"))
    h.add_argument("--mode", nargs="+", choices=("warm", "concurrent", "cold"), default=["warm", "concurrent"])
    h.add_argument("--arms", nargs="+", choices=("A", "G"), default=["A", "G"])
    h.add_argument("--accounts", nargs="+", default=list(DEFAULT_ACCOUNTS))
    h.add_argument("--only", nargs="*", default=None, help="query names (default: all)")
    h.add_argument("--warmups", type=int, default=1)
    h.add_argument("--runs", type=int, default=5, help="timed requests per query, arm and account (warm)")
    h.add_argument("--clients", type=int, default=4, help="concurrent clients (concurrent)")
    h.add_argument("--rounds", type=int, default=1, help="rounds of concurrent requests (concurrent)")
    h.add_argument("--page-size", type=int, default=PAGE_SIZE)
    h.add_argument("--timeout", type=float, default=600, help="seconds per request")
    h.add_argument("--containers", nargs="*", default=["seek-mysql", "neo4j", "nextseek"],
                   help="containers inspected for the configuration, and checked for a restart in cold mode")

    s = sub.add_parser("sql", help="the S arm (lane)")
    common(s)
    s.add_argument("--accounts", nargs="+", default=list(DEFAULT_ACCOUNTS))
    s.add_argument("--only", nargs="*", default=None)
    s.add_argument("--runs", type=int, default=5, help="warm runs after the first run of each form")
    s.add_argument("--page-size", type=int, default=PAGE_SIZE)
    s.add_argument("--statement-timeout", type=float, default=300, help="seconds (max_execution_time)")
    s.add_argument("--no-index", action="store_true", help=f"do not add {INDEX_NAME} when it is missing")

    m = sub.add_parser("memory", help="peak RSS growth per request through each view (lane)")
    common(m)
    m.add_argument("--shapes", nargs="+", default=list(BROADEST))
    m.add_argument("--arms", nargs="+", choices=("A", "G"), default=["A", "G"])
    m.add_argument("--account", default=SUPERUSER)
    m.add_argument("--budget-mb", type=int, default=1536, help="address-space growth allowed per child")
    m.add_argument("--timeout", type=float, default=900, help="seconds per child")

    c = sub.add_parser("memory-child", help=argparse.SUPPRESS)
    common(c, out=False)
    c.add_argument("--query", required=True)
    c.add_argument("--arm", choices=("A", "G"), required=True)
    c.add_argument("--account", default=SUPERUSER)
    c.add_argument("--budget-mb", type=int, default=1536)

    v = sub.add_parser("serve", help="gunicorn over the lane, for a dry run (lane)")
    v.add_argument("--bind", default="0.0.0.0:8000")
    v.add_argument("--workers", type=int, default=1)
    v.add_argument("--threads", type=int, default=4)
    v.add_argument("--allowed-hosts", default="*", help="DJANGO_ALLOWED_HOSTS for the lane server")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    handler = {"http": http_main, "sql": sql_main, "memory": memory_main, "memory-child": memory_child_main,
               "serve": serve_main}[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
