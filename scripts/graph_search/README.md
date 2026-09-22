# `scripts/graph_search/`

## What this is

Operational scripts for the graph_search proof of concept
([design](../../docs/superpowers/specs/2026-09-14-graph-search-poc-design.md),
[plan](../../docs/superpowers/plans/2026-09-14-graph-search-poc.md), graph schema
[`docs/neo4j-schema.md`](../../docs/neo4j-schema.md) section "v1.1"). They build and prove the merged dataset, the v1.1
graph and the new endpoint in throwaway, memory-capped containers on the operator's workstation. None of them touches
a container of the live `nextseek` compose project.

| File | Does |
|---|---|
| `lane.sh` | the throwaway lane: the scratch MySQL, a throwaway Neo4j, and the app image over a read-only mount of this checkout |
| `merge_tcga.sh`, `merge_tcga.sql`, `verify_merge.sql` | the merged MySQL and its gate M report |
| `load_graph_backup.py`, `dump_graph.sh` | load the local graph backup into the lane's Neo4j; dump the built v1.1 graph |
| `parity.py`, `queries.json` | graph_search against advanced_search, per query and scope (gate E). A compat fixture gives both engines `body`; or advanced_search's view `body` and graph_search `graph_body` (the Sample Search query text, parsed by advanced_search inside `filter_searchText` and by graph_search in `extensions.query`); or, with `engine: "FILTERING"` and `filters`, runs the Simple box's own path against graph_search's `body` |
| `synthetic_parity.sh`, `synthetic_parity.py`, `synthetic_queries.json` | the same comparison with nothing live: an empty MySQL and Neo4j on a private network, memory-capped and removed on exit, loaded with synthetic rows (schema from the seed's DDL, graph by graph_sync's own writer), then `parity.py` over `synthetic_queries.json` in every scope, the README's residual and defect checks, the lineage (Associated with) checks against a walk of the synthetic edges in every scope, EXPLAINs, and timings of a negation and a 12-hop lineage check. `scripts/graph_search/synthetic_parity.sh <exported tree> <out dir> [--scale N]`; never over a worktree |
| `bench.py`, `bench_report.py` | the benchmark harness and its report |
| `load_live.sh` | OPERATOR-RUN (plan task L1): snapshot the live stack's data, load the merged MySQL and the v1.1 graph into it, verify, and restore the snapshot afterwards |
| `nessie_venue.sh`, `nessie_venue_check.py` | OPERATOR-RUN (Nessie follow-up plan, task T8, runbook step 1 and stage P): the evaluation venue `gs-nessie-venue`, a throwaway app container on `nextseek_default` (not `gs-net`: it reaches the live MySQL and Neo4j) that runs a snapshot of HEAD; `prepare`, `up`, `check`, `exec`, `run`, `bg`, `progress`, `stop`, `logs`, `down`, and `--dry-run`. `up` renders the live compose env files the way compose reads them. The venue runs as the image's root user (a non-root `--user` cannot execute the venv's interpreter, which lives under `/root`) and hands what it writes back to the invoking user after each command and at `down`. `python3 scripts/graph_search/nessie_venue_check.py selftest` tests both without a container |
| `verify_labels.py` | read-only (sync plan task V1): the DERIVED_FROM label rule on the merged MySQL against the dev box's TCGA labels (singular fields, ids through `gs_remap`) and the local graph's production labels (every property, per `labels.classify` class), both from their dumps; `--local-host` adds a host the protocol rule reads as local; writes `$GS_RUN_DIR/labels/report.json` and `report.md`, exits 1 unless (a) matches every edge |

Files other than `lane.sh` land with their tasks in the plan; `git ls-files scripts/graph_search` lists what exists.

## Setup

Everything reads one work directory outside the repository, named by `GS_WORK`:

```bash
export GS_WORK=<the graph-search work directory>
```

It holds the seeds (`$GS_WORK/seeds/`), run reports (`$GS_WORK/runs/<task>/`) and `$GS_WORK/lane.env`, a shell file of
`KEY=value` lines that is never tracked and never printed. `lane.sh` reads these keys:

| Key | Default | Meaning |
|---|---|---|
| `GS_MYSQL_CONTAINER` | `gs-scratch-devbox-mysql` | the scratch MySQL container |
| `GS_MYSQL_ROOT_PASSWORD` | required | its root password |
| `GS_NEO4J_PASSWORD` | required | the throwaway Neo4j's password (8 characters or more) |
| `GS_NEO4J_IMAGE` | `neo4j:latest` | the Neo4j image; set it to the live stack's image id so both run the same version |
| `GS_NEO4J_MEMORY` | `4g` | the Neo4j container's memory cap (swap is capped to the same value) |
| `GS_NEO4J_HEAP` | `1500m` | Neo4j heap, initial and maximum |
| `GS_NEO4J_PAGECACHE` | `1500m` | Neo4j page cache |
| `GS_NEO4J_TX_MAX` | `1g` | `db.memory.transaction.max`; keep it below the heap |
| `GS_APP_MEMORY` | `4g` | the app container's memory cap |
| `GS_APP_IMAGE` | `nextseek-nextseek:latest` | the app image |

Secrets never appear on a command line: `lane.sh` exports them and passes bare `-e NAME` flags, which docker fills
from its own environment (`MYSQL_PWD` for the `mysql` client, `NEO4J_PASSWORD` for `cypher-shell`).

## `lane.sh`

```bash
scripts/graph_search/lane.sh <subcommand> [args]
```

| Subcommand | Does |
|---|---|
| `net` | creates the Docker network `gs-net` and attaches the scratch MySQL to it |
| `neo4j-up` | runs `gs-v11-neo4j` (memory-capped, data in the volume `gs-v11-neo4j-data`) and waits until it answers; a no-op when it is already up |
| `neo4j-down` | removes the `gs-v11-neo4j` container and keeps its volume; `docker volume rm gs-v11-neo4j-data` drops the data |
| `neo4j-cypher '<statement>'` | runs one statement through `cypher-shell --format plain`; with no statement (or `-`) it reads statements from stdin |
| `mysql '<sql>' [client options]` | runs SQL as root in the scratch MySQL (`utf8mb4`); with no SQL (or `-`) it reads stdin; options such as `-N` go to the client |
| `app <manage.py args>` | runs `manage.py` in the app image against the lane databases |
| `python <script> [args]` | runs a script in the app image the same way |
| `free-check` | prints `MemAvailable` and exits 3 when less than 2 GiB is free |

`neo4j-up`, `app` and `python` run `free-check` first. The app container is `gs-app-<pid>`, removed on exit.

### Files the app container writes are host-owned

The app image runs as root (its venv and `/root` are unreadable to other uids), so a file the command writes under
`/gswork` starts out root-owned. `lane.sh` therefore runs the command inside a small `sh` wrapper. When the command
ends, the wrapper runs `find /gswork -xdev -uid 0 -exec chown -h` to give every root-owned path under `/gswork` to the
invoking host user (`GS_HOST_UID` and `GS_HOST_GID`, taken from `id -u` and `id -g`), then exits with the command's own
exit code. The container runs with `--init` and `TINI_KILL_PROCESS_GROUP=1`, so a stop signal (`docker kill -s TERM`,
or Ctrl-C through the docker client) reaches the command. The wrapper traps the signal and still does the hand-back.
Only SIGKILL skips it, and the next `app` or `python` run then hands the leftovers back. To fix leftovers without a
lane run:

```bash
docker run --rm --network none --memory 256m -v "$GS_WORK":/gswork nextseek-nextseek:latest \
  find /gswork -xdev -uid 0 -exec chown -h "$(id -u):$(id -g)" {} +
```

### What the app container gets

The checkout is mounted read-only at `/src` and `$GS_WORK` read-write at `/gswork`; `GS_RUN_DIR` is `/gswork/runs`.
The databases come from the environment: `MYSQL_HOST` is the scratch container, `MYSQL_USER` root,
`MYSQL_DATABASE=seek_production`, `NEXTSEEK_MYSQL_DATABASE=dmac`, `NEXTSEEK_NEO4J_HOST=gs-v11-neo4j`.

Two more things are needed because the stack's rendered `dmac/local_settings.py` is not in a checkout:

- **A settings shim.** `DJANGO_SETTINGS_MODULE` is `gs_lane_settings`, which `lane.sh` writes to `$GS_WORK/lane/` on
  every run. It imports `dmac.settings` and adds the names the URLconf reads at import and that only
  `startup/templates/local_settings.py.template` supplies: `PUBLISH_URL`, `ASSISTANT_PARTICIPATING_PROJECTS` (empty),
  `PUBLISH_STATS_FILE` (a path that does not exist), `SMART_SEARCH_URL` and `TEST_CASES` (empty).
- **Inert environment values**, each needed for `manage.py check` to pass:

| Variable | Value | Why |
|---|---|---|
| `SEEK_HOST`, `SEEK_HOSTNAME`, `NEXTSEEK_HOSTNAME` | `gs-no-seek`, `http://gs-no-seek:3000`, `127.0.0.1:8000` | `dmac.settings` defines `SEEK_URL` and `SEEK_DATAFILE_ROOT` only when `SEEK_HOST` is set, and `seek/views/shared.py` reads them at import. No SEEK runs on `gs-net`, so any SEEK call fails fast |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `http://127.0.0.1:8000` | an unset value becomes `[""]`, which fails check `4_0.E001` |
| `DJANGO_SECRET_KEY` | `gs-lane-inert` | keeps an empty `SECRET_KEY` from failing any command that signs; nothing in the lane serves HTTP |
| `PYTHONPATH` | `/src:/gswork/lane` | makes the checkout win over the image's baked copy for `python` scripts, and finds the shim |

`scripts/graph_search/lane.sh app check` passes with only the `models.W042` warnings the stack already carries.

### Dynamic labels on this Neo4j image

The writer sets and removes type labels by name under `CYPHER 25`. On Neo4j Community 2026.07.1 (the live stack's
image), both forms work:

```bash
scripts/graph_search/lane.sh neo4j-cypher 'CYPHER 25 CREATE (n:Probe) SET n:$("T_X") RETURN labels(n)'
# ["Probe", "T_X"]
scripts/graph_search/lane.sh neo4j-cypher 'CYPHER 25 CREATE (n:Probe) SET n:$("T_X") WITH n REMOVE n:$(["T_X"]) RETURN labels(n)'
# ["Probe"]
scripts/graph_search/lane.sh neo4j-cypher 'CYPHER 25 MATCH (n:Probe) REMOVE n:$(["T_X"]) DETACH DELETE n'
```

Rerun them after any change of `GS_NEO4J_IMAGE`.

## Rules

- Every container this folder starts is named `gs-*`, joins `gs-net` and carries `--memory`.
- Never write to, restart, recreate or stop a container of the live `nextseek` compose project; read-only
  `docker ps` and `docker inspect` are fine. Loading into the live stack is the operator's step (plan task L1):
  `load_live.sh`, whose changing steps refuse to run without `--yes`.
- Seed and merged dumps hold real personal data: they stay under `$GS_WORK`, never in the repository.
