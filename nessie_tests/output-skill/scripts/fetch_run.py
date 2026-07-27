#!/usr/bin/env python3
"""Pull a nessie_tests run's evidence off the dev box: manifest + per-turn router/engine data.

Everything here is READ-ONLY. It never writes to the dev box or the database.

Why this exists: the harness manifest records criterion *names* only, never the
observed values, so a failure list on its own cannot be triaged. The observed
values are recoverable from `assistant_query_task`, which stores the full
progress event stream (including `query_complete.data.debug`) and the result for
every turn. This script joins those two sources.

Usage
-----
    python fetch_run.py --out ./run-2026-07-24 \
        --manifest /app/nessie_out_full/manifest.json \
        --since "2026-07-24 20:05:00" --until "2026-07-24 20:45:00"

    # or address the turns by primary key directly
    python fetch_run.py --out ./run --id-min 721 --id-max 774

Writes  <out>/manifest.json  and  <out>/turns.json.

The MySQL root password is read live from the db container's environment on the
remote host. It is never passed on a command line here and never stored.
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import subprocess
import sys

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

echo "@@@TURNS@@@"
q -e "SELECT JSON_OBJECT(
        'id',      id,
        'q',       query,
        'status',  status,
        'created', CAST(created_at AS CHAR),
        'updated', CAST(updated_at AS CHAR),
        'route',   JSON_UNQUOTE(JSON_EXTRACT(progress,'\$[0].data.route')),
        'src',     JSON_UNQUOTE(JSON_EXTRACT(progress,'\$[0].data.source')),
        'why',     JSON_UNQUOTE(JSON_EXTRACT(progress,'\$[0].data.reasoning')),
        'mode',    JSON_UNQUOTE({D}'\$[0].parser_plan.mode')),
        'cypher',  JSON_UNQUOTE({D}'\$[0].graph_plan.cypher')),
        'cnt',     {D}'\$[0].graph_result.count'),
        'ep',      JSON_UNQUOTE({D}'\$[0].api_plan.endpoint')),
        'code',    {D}'\$[0].api_result_meta.status_code'),
        'apiok',   {D}'\$[0].api_result_meta.ok'),
        'rplan',   {D}'\$[0].reporter_plan')
      ) FROM assistant_query_task WHERE {where} ORDER BY id;"
"""


def run_remote(host: str, user: str, script: str) -> str:
    """Base64 the script so ssh/sudo/bash quoting cannot mangle it."""
    b64 = base64.b64encode(script.encode()).decode()
    cmd = ["ssh", "-o", "ConnectTimeout=30", host]
    if user:
        cmd += ["sudo", "-n", "-u", user]
    cmd += ["bash", "-c", f'"echo {b64} | base64 -d | bash"']
    proc = subprocess.run(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=300,
    )
    if proc.returncode != 0:
        sys.exit(f"remote command failed ({proc.returncode}): {proc.stderr.decode(errors='replace')[:500]}")
    # Query text can carry non-UTF8 bytes (a latin-1 em dash shows up in the corpus).
    return proc.stdout.decode("utf-8", errors="replace")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="fairdata-dev")
    ap.add_argument("--user", default="service-account", help="sudo -u target; pass '' to skip sudo")
    ap.add_argument("--app-container", default="nextseek")
    ap.add_argument("--db-container", default="seek-mysql")
    ap.add_argument("--db", default="dmac")
    ap.add_argument("--manifest", default="/app/nessie_out_full/manifest.json")
    ap.add_argument("--id-min", type=int)
    ap.add_argument("--id-max", type=int)
    ap.add_argument("--since", help='e.g. "2026-07-24 20:05:00"')
    ap.add_argument("--until", help='e.g. "2026-07-24 20:45:00"')
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.id_min and args.id_max:
        where = f"id BETWEEN {args.id_min} AND {args.id_max}"
    elif args.since and args.until:
        where = f"created_at BETWEEN '{args.since}' AND '{args.until}'"
    else:
        sys.exit("give either --id-min/--id-max or --since/--until")

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    raw = run_remote(args.host, args.user, REMOTE.format(
        dbc=args.db_container, app=args.app_container, db=args.db,
        manifest=args.manifest, where=where, D=DEBUG,
    ))

    if "@@@TURNS@@@" not in raw:
        sys.exit("unexpected remote output; got:\n" + raw[:800])
    head, tail = raw.split("@@@TURNS@@@", 1)
    manifest_txt = head.split("@@@MANIFEST@@@", 1)[1].strip()

    if manifest_txt and manifest_txt != "MISSING":
        (out / "manifest.json").write_text(manifest_txt, encoding="utf-8")
        n = len(json.loads(manifest_txt).get("entries", []))
        print(f"manifest.json  {n} entries")
    else:
        print(f"WARNING: no manifest at {args.manifest} (is that the right --out dir from the run?)")

    turns = [json.loads(ln) for ln in tail.splitlines() if ln.strip().startswith("{")]
    (out / "turns.json").write_text(json.dumps(turns, indent=1), encoding="utf-8")

    routes, srcs = {}, {}
    for t in turns:
        routes[t.get("route")] = routes.get(t.get("route"), 0) + 1
        srcs[t.get("src")] = srcs.get(t.get("src"), 0) + 1
    print(f"turns.json     {len(turns)} turns")
    print(f"  routes  {routes}")
    print(f"  sources {srcs}   <- any 'pipeline' here means the router was bypassed")
    print(f"  cypher  {sum(1 for t in turns if t.get('cypher'))}   "
          f"rest {sum(1 for t in turns if t.get('ep'))}")


if __name__ == "__main__":
    main()
