#!/usr/bin/env python3
"""Pull what Nessie did off ANY instance: per-turn router/engine data, plus optionally the
harness manifest, the raw task rows and the output files each turn wrote.

Everything here is READ-ONLY. It never writes to the instance or its database.

Why this exists: the harness manifest records criterion *names* only, never the
observed values, so a failure list on its own cannot be triaged. The observed
values are recoverable from `assistant_query_task`, which stores the full
progress event stream (including `query_complete.data.debug`) and the result for
every turn. That table holds EVERY turn, not just harness ones, so the same pull
reviews the questions real users asked on production.

Instances
---------
    --instance local   the workstation's own docker daemon, no ssh
    --instance dev     fairdata-dev, ssh + `sudo -n -u service-account` (the default)
    --instance prod    fairdata, direct key login as service-account, no sudo

`--host` / `--user` still override the preset; `--host ""` is the older spelling of local.

Usage
-----
    # a nessie_tests run on the dev box
    python fetch_run.py --out ./run-2026-07-24 \
        --manifest /app/nessie_out_full/manifest.json \
        --since "2026-07-24 20:05:00" --until "2026-07-24 20:45:00"

    # or address the turns by primary key directly
    python fetch_run.py --out ./run --id-min 721 --id-max 774

    # real users' questions on production, with every piece of evidence copied down
    python fetch_run.py --instance prod --out ./prod-2026-09-10 --raw --outputs \
        --since "2026-08-28 00:00:00" --until "2026-09-10 23:59:59"

Writes into <out>:
    turns.json           one record per turn (always)
    pull.json            what was pulled, from where, and the instance's clock offset
    manifest.json        only when the harness manifest exists on the instance
    case_costs.json      with the manifest: each case's cost, summed over its turns
    tasks/<id>.json      --raw: the full task row, progress event stream + result
    outputs/<run_root>/  --outputs: every run root a turn wrote to, copied verbatim
    outputs_index.json   --outputs: task id -> the files it wrote, matched by mtime

`turns.json` carries, per turn: who asked (`user`), the chat `session`, the routing
decision (route / source / the router's own reasoning), the parser mode, and the
**full** engine call — the graph plan with its bound parameters and result meta, or
the API plan with its request body and result meta — plus the reporter plan, CC
model id, cost, the `run_root` the turn wrote to, and `error`: the FIRST error the
progress stream recorded. The first one is the specific cause (a provider 503, a
timeout); the final `result.error` is usually the generic "Internal pipeline error".
Result rows are stripped from `graph_result` (`$.data`) so the payload stays small;
only the counts and the query are kept. `--raw` keeps everything.

Money and models, per turn: `cost` is the engine's `total_cost_usd` and `router_cost`
the router's `router_cost_usd` off the `route_decided` event, with their partial flags,
`models_used`, `model_fallback`, `router_model` and `router_fallback`. `turn_cost` and
`turn_cost_partial` are those summed by the harness's own rule (`turn_cost.py`, loaded
from this checkout), and `fell_back` says whether any model of the turn fell back. When
the manifest is on the instance, `case_costs.json` sums each case over its turns the
same way the harness does, so a grade and the run report the same number.

Timestamps (`created`/`updated`, run-root folder names, file-name stamps) are the
app container's clock, UTC on dev and prod. Its offset is recorded in pull.json and
is what `outputs_index.json` uses to match file mtimes to turns.

The MySQL root password is read live from the db container's environment on the
remote host. It is never passed on a command line here and never stored.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

def _load_turn_cost():
    """The harness's own summing rule, from this checkout, by path.

    One rule, not a copy: a pull that summed a case differently from the run would
    grade it against a number the run never reported. The module is standard library
    only, so this script still needs nothing but python3.
    """
    path = pathlib.Path(__file__).resolve().parents[2] / "turn_cost.py"
    spec = importlib.util.spec_from_file_location("_nessie_turn_cost", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


turn_cost = _load_turn_cost()

INSTANCES = {
    "local": {"host": "", "user": ""},
    "dev": {"host": "fairdata-dev", "user": "service-account"},
    "prod": {"host": "fairdata", "user": ""},
}

# The debug object hangs off the query_complete event inside the progress array.
# The `$` is backslash-escaped because this lands inside a double-quoted shell
# string, where bare `$[...]` is deprecated arithmetic expansion and errors out.
DEBUG = r"JSON_EXTRACT(JSON_EXTRACT(progress,'\$[*].data.debug'),"

REMOTE = r"""
set -u
PW=$(docker exec {dbc} sh -c 'echo $MYSQL_ROOT_PASSWORD')
q() {{ docker exec {dbc} mysql -uroot -p"$PW" {db} -N --raw "$@" 2>/dev/null; }}

echo "@@@MANIFEST@@@"
docker exec {app} cat {manifest} 2>/dev/null || echo "MISSING"

echo "@@@TZ@@@"
docker exec {app} date +%z 2>/dev/null || echo "UNKNOWN"

echo "@@@TURNS@@@"
q -e "SELECT JSON_OBJECT(
        'id',      t.id,
        'q',       t.query,
        'status',  t.status,
        'created', CAST(t.created_at AS CHAR),
        'updated', CAST(t.updated_at AS CHAR),
        'user',    (SELECT u.username FROM auth_user u WHERE u.id = t.user_id),
        'session', t.session_id,
        'task_uuid', t.task_id,
        'run_root', JSON_UNQUOTE(JSON_EXTRACT(JSON_EXTRACT(progress,'\$[*].data.run_root'),'\$[0]')),
        'error',   COALESCE(
                     JSON_UNQUOTE(JSON_EXTRACT(JSON_EXTRACT(progress,'\$[*].data.error'),'\$[0]')),
                     JSON_UNQUOTE(JSON_EXTRACT(result,'\$.error'))),
        'route',   JSON_UNQUOTE(JSON_EXTRACT(progress,'\$[0].data.route')),
        'src',     JSON_UNQUOTE(JSON_EXTRACT(progress,'\$[0].data.source')),
        'why',     JSON_UNQUOTE(JSON_EXTRACT(progress,'\$[0].data.reasoning')),
        'model',   JSON_UNQUOTE(JSON_EXTRACT(progress,'\$[1].data.model_id')),
        'mode',    JSON_UNQUOTE({D}'\$[0].parser_plan.mode')),
        'aplan',   {D}'\$[0].api_plan'),
        'ameta',   {D}'\$[0].api_result_meta'),
        'gplan',   {D}'\$[0].graph_plan'),
        'gmeta',   JSON_REMOVE({D}'\$[0].graph_result'),'\$.data'),
        'rplan',   {D}'\$[0].reporter_plan'),
        'reply',   COALESCE(
                     JSON_UNQUOTE(JSON_EXTRACT(JSON_EXTRACT(progress,'\$[*].data.reply'),'\$[0]')),
                     JSON_UNQUOTE(JSON_EXTRACT(result,'\$.reply'))),
        'cost',    JSON_EXTRACT(result,'\$.total_cost_usd'),
        'cost_partial',   JSON_EXTRACT(result,'\$.cost_partial'),
        'models_used',    JSON_EXTRACT(result,'\$.models_used'),
        'model_fallback', JSON_EXTRACT(result,'\$.model_fallback'),
        'router_cost',         JSON_EXTRACT(JSON_EXTRACT(progress,'\$[*].data.router_cost_usd'),'\$[0]'),
        'router_cost_partial', JSON_EXTRACT(JSON_EXTRACT(progress,'\$[*].data.router_cost_partial'),'\$[0]'),
        'router_model',        JSON_EXTRACT(JSON_EXTRACT(progress,'\$[*].data.router_model'),'\$[0]'),
        'router_fallback',     JSON_EXTRACT(JSON_EXTRACT(progress,'\$[*].data.router_fallback'),'\$[0]')
      ) FROM assistant_query_task t WHERE {where} ORDER BY t.id;"
"""

# The whole row, for `--raw`. A progress stream can run to several MB (a graph
# result's rows live inside it), which is exactly why turns.json strips them.
RAW = r"""
set -u
PW=$(docker exec {dbc} sh -c 'echo $MYSQL_ROOT_PASSWORD')
docker exec {dbc} mysql -uroot -p"$PW" {db} -N --raw -e "SELECT JSON_OBJECT(
        'id', id, 'task_uuid', task_id, 'session', session_id, 'user_id', user_id,
        'query', query, 'status', status,
        'created', CAST(created_at AS CHAR), 'updated', CAST(updated_at AS CHAR),
        'progress', IF(JSON_VALID(progress), CAST(progress AS JSON), progress),
        'result',   IF(JSON_VALID(result), CAST(result AS JSON), result)
      ) FROM assistant_query_task WHERE {where} ORDER BY id;" 2>/dev/null
"""

# A run root is a folder the app names <YYMMDD>_<HHMMSS>_<username>. Anything else is
# refused rather than handed to `tar` on the instance.
RUN_ROOT_NAME = re.compile(r"[0-9]{6}_[0-9]{6}_[A-Za-z0-9._@+-]+")
# Written to by every turn a process serves, so an mtime says nothing about which
# turn. They are listed per turn as `shared`, never matched.
SHARED = ("api_requests.json", "console.txt")


def resolve_target(instance: str, host: str | None = None, user: str | None = None) -> tuple[str, str]:
    """(ssh host, sudo user) for an instance; an explicit host/user wins over the preset."""
    preset = INSTANCES[instance]
    return (preset["host"] if host is None else host,
            preset["user"] if user is None else user)


def remote_cmd(host: str, user: str, script: str) -> list[str]:
    """Base64 the script so ssh/sudo/bash quoting cannot mangle it.

    ``host=""`` runs the same script against the LOCAL docker daemon instead of
    SSHing (`ssh localhost` is not a fallback: there is no sshd). ``user=""`` skips
    sudo, which is what production needs: its key logs in as service-account
    directly. The script body is identical everywhere; only the transport differs.
    """
    b64 = base64.b64encode(script.encode()).decode()
    if not host:
        return ["bash", "-c", f"echo {b64} | base64 -d | bash"]
    cmd = ["ssh", "-o", "ConnectTimeout=30", host]
    if user:
        cmd += ["sudo", "-n", "-u", user]
    return cmd + ["bash", "-c", f'"echo {b64} | base64 -d | bash"']


def run_remote(host: str, user: str, script: str) -> str:
    proc = subprocess.run(
        remote_cmd(host, user, script), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=600,
    )
    if proc.returncode != 0:
        sys.exit(f"remote command failed ({proc.returncode}): {proc.stderr.decode(errors='replace')[:500]}")
    # Query text can carry non-UTF8 bytes (a latin-1 em dash shows up in the corpus).
    return proc.stdout.decode("utf-8", errors="replace")


def run_root_name(run_root: str | None, outputs_dir: str = "/app/outputs") -> str | None:
    """The folder name of a turn's run root, or None if it is not a plain outputs folder."""
    if not run_root:
        return None
    p = PurePosixPath(run_root)
    if str(p.parent) != outputs_dir.rstrip("/") or not RUN_ROOT_NAME.fullmatch(p.name):
        return None
    return p.name


def tz_offset_minutes(text: str) -> int | None:
    """`date +%z` output ("-0400") as minutes east of UTC."""
    m = re.fullmatch(r"([+-])(\d{2})(\d{2})", (text or "").strip())
    if not m:
        return None
    sign = 1 if m.group(1) == "+" else -1
    return sign * (int(m.group(2)) * 60 + int(m.group(3)))


#: The per-call ledger and the response log. They record what a provider incident needs --
#: stop_reason, request_id, retry_attempts, which structured path ran -- and they are written
#: to LOG_DIR, a SIBLING of the outputs directory. Every pull before this took outputs only,
#: so when a turn failed on an empty completion the one file that would have explained it was
#: the one file the evidence could not reach.
LEDGER_FILES = ("llm_calls.jsonl", "llm_responses.jsonl")


def pull_logs(host: str, user: str, app: str, logs_dir: str, dest: pathlib.Path) -> list[str]:
    """Copy the LLM ledger out of the app container. Best effort: a box may have neither file.

    Returns the names actually copied. Never exits: the ledger is evidence, and a pull that
    got the turns and the outputs is still worth having without it.
    """
    dest.mkdir(parents=True, exist_ok=True)
    listing = subprocess.run(
        remote_cmd(host, user, f"docker exec {app} sh -c 'ls -1 {logs_dir} 2>/dev/null'"),
        stdin=subprocess.DEVNULL, capture_output=True,
    )
    present = [n for n in LEDGER_FILES if n in listing.stdout.decode(errors="replace").split()]
    if not present:
        print(f"logs/          no ledger in {logs_dir} (looked for {', '.join(LEDGER_FILES)})")
        return []
    script = f"docker exec {app} tar -C {logs_dir} -cf - {' '.join(present)}"
    src = subprocess.Popen(remote_cmd(host, user, script), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sink = subprocess.run(["tar", "-xf", "-", "-C", str(dest)], stdin=src.stdout,
                          stderr=subprocess.PIPE)
    src.stdout.close()
    err = src.stderr.read().decode(errors="replace")
    if src.wait() != 0 or sink.returncode != 0:
        print(f"logs/          copy failed, continuing without it: {err[:200]}")
        return []
    print(f"logs/          {', '.join(present)}")
    return present


def pull_outputs(host: str, user: str, app: str, outputs_dir: str, names: list[str],
                 dest: pathlib.Path) -> None:
    """Stream the named run roots out of the app container with tar, mtimes intact."""
    dest.mkdir(parents=True, exist_ok=True)
    script = f"docker exec {app} tar -C {outputs_dir} -cf - {' '.join(names)}"
    src = subprocess.Popen(remote_cmd(host, user, script), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sink = subprocess.run(["tar", "-xf", "-", "-C", str(dest)], stdin=src.stdout,
                          stderr=subprocess.PIPE)
    src.stdout.close()
    err = src.stderr.read().decode(errors="replace")
    if src.wait() != 0 or sink.returncode != 0:
        sys.exit(f"output copy failed: {err[:400]} {sink.stderr.decode(errors='replace')[:400]}")


def index_outputs(turns: list[dict], outputs_root: pathlib.Path, offset_min: int,
                  before_s: int = 2, after_s: int = 5) -> dict:
    """task id -> the files in its run root whose mtime falls inside the turn.

    A run root is per gunicorn PROCESS, not per turn or per user, so one folder
    holds many turns and a file can only be tied to a turn by when it was written.
    `created`/`updated` are the app container's clock; mtimes are converted with
    that container's own offset so the two compare.
    """
    tz = timezone(timedelta(minutes=offset_min))
    files: dict[str, list[pathlib.Path]] = {}
    for p in outputs_root.rglob("*"):
        if p.is_file():
            files.setdefault(p.relative_to(outputs_root).parts[0], []).append(p)
    index = {}
    for t in turns:
        name = run_root_name(t.get("run_root"))
        if not name or not t.get("created") or not t.get("updated"):
            continue
        start = datetime.fromisoformat(t["created"]) - timedelta(seconds=before_s)
        end = datetime.fromisoformat(t["updated"]) + timedelta(seconds=after_s)
        hits = []
        for p in files.get(name, []):
            if p.name in SHARED:
                continue
            written = datetime.fromtimestamp(p.stat().st_mtime, tz).replace(tzinfo=None)
            if start <= written <= end:
                hits.append(str(p.relative_to(outputs_root)))
        index[str(t["id"])] = {
            "run_root": name,
            "files": sorted(hits),
            "shared": [f"{name}/{s}" for s in SHARED if (outputs_root / name / s).exists()],
        }
    return index


def price_turns(turns: list[dict]) -> list[dict]:
    """Add `turn_cost`, `turn_cost_partial` and `fell_back` to each pulled turn.

    The router fields are read raw (no JSON_UNQUOTE, which turns a JSON null into the
    string "null"), so a missing value arrives as None and is unobserved, not zero.
    """
    for t in turns:
        t["turn_cost"], t["turn_cost_partial"] = turn_cost.turn_total(
            engine_cost=turn_cost.usd(t.get("cost")),
            router_cost=turn_cost.usd(t.get("router_cost")),
            route=t.get("route"), source=t.get("src"),
            cost_partial=t.get("cost_partial") is True,
            router_cost_partial=t.get("router_cost_partial") is True)
        t["fell_back"] = turn_cost.fell_back(t)
    return turns


def case_costs(manifest: dict, turns: list[dict]) -> dict:
    """Each manifest case's cost, summed over its pulled turns by the harness's rule.

    A case is joined by its task ids: the entry's `task_ids`, or for a consistency
    group, which records them per query, its `turns_meta`. A turn is missing when the
    pull did not return its task (outside the window), or when the run sent it and
    its driver raised, leaving no task id at all: the entry's `turns_sent` counts
    those. A missing turn makes a number partial, and so does the run's own
    `cost_partial` for the case. A case that sent no turn is left out.
    """
    by_task = {t.get("task_uuid"): t for t in turns if t.get("task_uuid")}
    out = {}
    for e in manifest.get("entries") or []:
        ids = list(e.get("task_ids") or []) or [
            m.get("task_id") for m in (e.get("turns_meta") or []) if m.get("task_id")]
        sent = max(len(ids), e.get("turns_sent") or 0)
        if not sent:
            continue
        rows = [by_task[i] for i in ids if i in by_task]
        missing = max(0, sent - len(rows))
        cost, partial = turn_cost.case_total(
            [(r["turn_cost"], r["turn_cost_partial"]) for r in rows], missing_turns=missing)
        out[e["id"]] = {"cost": cost,
                        "cost_partial": bool(cost is not None and (partial or e.get("cost_partial"))),
                        "turns": len(rows), "missing_turns": missing,
                        "fallback_turns": sum(1 for r in rows if r["fell_back"])}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", choices=sorted(INSTANCES), default="dev",
                    help="local | dev (fairdata-dev) | prod (fairdata). Default dev.")
    ap.add_argument("--host", default=None,
                    help='ssh target; overrides --instance. "" = the LOCAL docker daemon')
    ap.add_argument("--user", default=None,
                    help="sudo -u target; overrides --instance. '' = no sudo")
    ap.add_argument("--app-container", default="nextseek")
    ap.add_argument("--db-container", default="seek-mysql")
    ap.add_argument("--db", default="dmac")
    ap.add_argument("--manifest", default="/app/nessie_out_full/manifest.json",
                    help="harness manifest path in the app container; absent is fine "
                         "when reviewing real users' turns")
    ap.add_argument("--id-min", type=int)
    ap.add_argument("--id-max", type=int)
    ap.add_argument("--since", help='app-container clock (UTC on dev/prod), e.g. "2026-07-24 20:05:00"')
    ap.add_argument("--until", help='app-container clock (UTC on dev/prod), e.g. "2026-07-24 20:45:00"')
    ap.add_argument("--raw", action="store_true",
                    help="also write tasks/<id>.json: the full row, progress stream + result")
    ap.add_argument("--outputs", action="store_true",
                    help="also copy every run root a turn wrote to, and index files per turn")
    ap.add_argument("--outputs-dir", default="/app/outputs")
    ap.add_argument("--logs-dir", default="/app/logs",
                    help="where the LLM ledger lives; pulled with --outputs")
    ap.add_argument("--no-ledger", action="store_true",
                    help="skip the LLM ledger (llm_calls.jsonl, llm_responses.jsonl)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.id_min and args.id_max:
        where = f"id BETWEEN {args.id_min} AND {args.id_max}"
    elif args.since and args.until:
        where = f"created_at BETWEEN '{args.since}' AND '{args.until}'"
    else:
        sys.exit("give either --id-min/--id-max or --since/--until")

    host, user = resolve_target(args.instance, args.host, args.user)
    print(f"instance {args.instance}: host={host or '(local docker)'} sudo={user or '(none)'}")

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    raw = run_remote(host, user, REMOTE.format(
        dbc=args.db_container, app=args.app_container, db=args.db,
        manifest=args.manifest, where=where, D=DEBUG,
    ))

    if "@@@TURNS@@@" not in raw:
        sys.exit("unexpected remote output; got:\n" + raw[:800])
    head, tail = raw.split("@@@TURNS@@@", 1)
    head, tz_txt = head.split("@@@TZ@@@", 1)
    manifest_txt = head.split("@@@MANIFEST@@@", 1)[1].strip()
    offset = tz_offset_minutes(tz_txt)

    manifest = None
    if manifest_txt and manifest_txt != "MISSING":
        (out / "manifest.json").write_text(manifest_txt, encoding="utf-8")
        manifest = json.loads(manifest_txt)
        n = len(manifest.get("entries", []))
        print(f"manifest.json  {n} entries")
    else:
        print(f"no manifest at {args.manifest} (expected when reviewing real users' turns)")

    turns = price_turns([json.loads(ln) for ln in tail.splitlines()
                         if ln.strip().startswith("{")])
    (out / "turns.json").write_text(json.dumps(turns, indent=1), encoding="utf-8")

    routes = Counter(t.get("route") for t in turns)
    srcs = Counter(t.get("src") for t in turns)
    print(f"turns.json     {len(turns)} turns")
    print(f"  users   {dict(Counter(t.get('user') for t in turns).most_common())}")
    print(f"  status  {dict(Counter(t.get('status') for t in turns))}   "
          f"turns carrying an error: {sum(1 for t in turns if t.get('error'))}")
    print(f"  routes  {dict(routes)}")
    print(f"  sources {dict(srcs)}   <- any 'pipeline' here means the router was bypassed")
    print(f"  graph calls {sum(1 for t in turns if t.get('gplan'))}   "
          f"rest calls {sum(1 for t in turns if t.get('aplan'))}   "
          f"reporter {sum(1 for t in turns if t.get('rplan'))}")
    priced = [t for t in turns if t["turn_cost"] is not None]
    print(f"  cost    ${sum(t['turn_cost'] for t in priced):.4f} on {len(priced)} of "
          f"{len(turns)} turns, {sum(1 for t in priced if t['turn_cost_partial'])} of them "
          f"partial; fell back {sum(1 for t in turns if t['fell_back'])}")
    if manifest is not None:
        cases = case_costs(manifest, turns)
        (out / "case_costs.json").write_text(json.dumps(cases, indent=1), encoding="utf-8")
        known = [c for c in cases.values() if c["cost"] is not None]
        print(f"case_costs.json {len(cases)} cases, ${sum(c['cost'] for c in known):.4f} on "
              f"{len(known)}, {sum(1 for c in known if c['cost_partial'])} partial, "
              f"{len(cases) - len(known)} unmeasured")

    pull = {"instance": args.instance, "host": host, "where": where,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "instance_utc_offset": tz_txt.strip(), "turns": len(turns)}

    if args.raw:
        rows = [json.loads(ln) for ln in run_remote(host, user, RAW.format(
            dbc=args.db_container, db=args.db, where=where)).splitlines()
            if ln.strip().startswith("{")]
        (out / "tasks").mkdir(exist_ok=True)
        for r in rows:
            (out / "tasks" / f"{r['id']}.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
        print(f"tasks/         {len(rows)} full task rows")
        pull["raw_rows"] = len(rows)

    if args.outputs:
        names = sorted({n for n in (run_root_name(t.get("run_root"), args.outputs_dir)
                                    for t in turns) if n})
        odd = sorted({t["run_root"] for t in turns
                      if t.get("run_root") and not run_root_name(t["run_root"], args.outputs_dir)})
        if odd:
            print(f"  skipped run roots outside {args.outputs_dir}: {odd}")
        if names:
            pull_outputs(host, user, args.app_container, args.outputs_dir, names, out / "outputs")
            if offset is None:
                print(f"  WARNING: instance UTC offset unknown ({tz_txt.strip()!r}); indexing as UTC")
            index = index_outputs(turns, out / "outputs", offset or 0)
            (out / "outputs_index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
            print(f"outputs/       {len(names)} run roots; "
                  f"{sum(1 for v in index.values() if v['files'])} of {len(index)} turns "
                  f"matched at least one file")
        else:
            print("outputs/       no turn recorded a run root (container_cc turns write elsewhere)")
        pull["run_roots"] = names
        if not args.no_ledger:
            pull["ledger"] = pull_logs(host, user, args.app_container, args.logs_dir, out / "logs")

    (out / "pull.json").write_text(json.dumps(pull, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
