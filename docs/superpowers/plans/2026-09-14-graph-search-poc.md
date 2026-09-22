# graph_search proof of concept: implementation plan

> **For agentic workers:** this plan is executed by a Workflow over the task graph below (the operator's choice, not
> subagent-driven development). Each task is one agent with its own tests; a reviewer may reject one task and approve
> its neighbour. Use superpowers:test-driven-development inside a task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** a new endpoint, `POST /nextseek_api/samples/graph_search/`, that answers `advanced_search`'s questions (and
new ones) from a Neo4j graph holding every sample's metadata, a sample-type and attribute catalog, and people and
projects, proven against `advanced_search` on about 1.08M samples.

**Architecture:** two new packages. `nextseek_api/graph_sync/` reads MySQL and writes graph schema v1.1 (one
management command, `graph_sync`). `nextseek_api/graph_search/` holds the endpoint's scope resolver, query builder,
catalog cache and hydration, behind a thin ViewSet in `nextseek_api/services/graph_search.py`. Operational scripts
(the throwaway lane, the TCGA merge, parity and the benchmark) live in `scripts/graph_search/`. Everything is built
and proven in throwaway, memory-capped containers; only the operator touches the live local stack.

**Tech stack:** Django 5 and DRF, pydantic v2, drf-spectacular, the `neo4j` Python driver (Neo4j Community
2026.07.1, `CYPHER 25`), MySQL 8.0 via Django's `seek` and `default` connections, bash and Docker for the lane.

**Spec:** `docs/superpowers/specs/2026-09-14-graph-search-poc-design.md`. Graph schema: `docs/neo4j-schema.md`
("v1.1"). Read both before any task.

## Global constraints

- Work in the worktree `wt-graph-search`, branch `feat/graph-search`, based on `origin/dev` at `867aa100`. Never push.
- Never rebuild, recreate, restart or stop any container of the `nextseek` compose project. Never write to the live
  stack's MySQL or Neo4j. Tasks marked **OPERATOR** are run by the operator, not an agent.
- Throwaway containers are named `gs-*`, join the Docker network `gs-net`, and always carry `--memory`. Before any
  step that loads data, run `free -g`; stop and report if less than 2 GB is available.
- `$GS_WORK` is the operator's graph-search work directory, outside the repository. Seeds live under
  `$GS_WORK/seeds/`; lane secrets live in `$GS_WORK/lane.env` (never tracked, never printed). Reports produced by a
  run go to `$GS_WORK/runs/<task>/`.
- Seed and merged dumps hold real personal data: never copy them into the repository, never upload them.
- Public repository: no credentials, emails, personal names or home paths in tracked files. No em-dashes anywhere.
- No SSH to the dev box or production.
- Spec numbers, verbatim: merged samples 1,084,754; `projects_samples` rows 1,127,894; `assay_assets` rows 1,546,204;
  TCGA samples 918,519; TCGA JSON bytes 896,626,185; TCGA DERIVED_FROM pairs 1,213,093; merged sample types 118;
  declared attributes 3,530; undeclared observed keys 40; page size default 100, maximum 1,000; Cypher timeout 60 s;
  write chunk default 5,000; index budget threshold 1,000 samples and 4,000 characters.
- New `router.register` goes into `ci/routes.py` and bumps `OWNED_ROUTE_COUNT` in the same commit.
- Stage files by name, never `git add -A`. Conventional commits with module scopes (`feat(graph_sync): ...`,
  `feat(graph_search): ...`, `feat(scripts): ...`, `docs(...)`, `test(...)`), each ending with exactly:

```
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017dhzDS7bs3wKsyxWgTYtkB
```

## Commands used throughout

```bash
# Django unit lane: throwaway container, read-only mount, SQLite settings (no MySQL, no Neo4j)
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest <paths> -q -p no:cacheprovider

# ViewSet conventions
python3 scripts/validate_viewset_conventions.py

# Docs map
python3 ci/docs_map.py

# The data lane (task T0 builds it): Django against the throwaway MySQL and Neo4j
scripts/graph_search/lane.sh app <manage.py args>
```

## Task graph

```
T0 lane + package scaffolding
 |-- M1 merged MySQL (gate M)
 |-- G1 projection (pure) ---- G3 MySQL sources --+
 |-- G2 catalog (pure) --------------------------+-- G4 Neo4j writer -- G5 verify + command -- G6 build run (gate G)
 |-- E1 request models --+                                                                          |
 |-- E2 scope resolver --+-- E5 ViewSet + wiring ----------------------------------------------------+-- E6 parity (gate E)
 |-- E3 query builder ---+                                                                                   |
 |-- E4 hydration -------+                                                                                   B1 benchmark harness
                                                                                   OPERATOR L1 live load (gate L) -- B2 benchmark run (gate B)
```

T0 runs first. M1, G1, G2, E1, E2, E3 and E4 can run in parallel after it; they own disjoint files.

## File ownership

| File | Task |
|---|---|
| `scripts/graph_search/README.md`, `scripts/graph_search/lane.sh`, `scripts/README.md` (one row), `nextseek_api/graph_sync/__init__.py`, `nextseek_api/graph_sync/README.md`, `nextseek_api/graph_search/__init__.py`, `nextseek_api/graph_search/README.md`, `nextseek_api/README.md` (two rows) | T0 |
| `scripts/graph_search/merge_tcga.sql`, `scripts/graph_search/merge_tcga.sh`, `scripts/graph_search/verify_merge.sql` | M1 |
| `nextseek_api/graph_sync/projection.py`, `nextseek_api/tests/test_graph_sync_projection.py` | G1 |
| `nextseek_api/graph_sync/catalog.py`, `nextseek_api/tests/test_graph_sync_catalog.py` | G2 |
| `nextseek_api/graph_sync/sources.py`, `nextseek_api/tests/test_graph_sync_sources.py` | G3 |
| `nextseek_api/graph_sync/writer.py`, `nextseek_api/graph_sync/cypher.py`, `nextseek_api/tests/test_graph_sync_writer.py` | G4 |
| `nextseek_api/graph_sync/verify.py`, `nextseek_api/graph_sync/run.py`, `nextseek_api/management/commands/graph_sync.py`, `nextseek_api/tests/test_graph_sync_command.py` | G5 |
| `scripts/graph_search/load_graph_backup.py`, `scripts/graph_search/dump_graph.sh` | G6 |
| `nextseek_api/models.py` (graph_search models only, appended after `SampleAdvancedSearchResult`), `nextseek_api/tests/test_graph_search_models.py` | E1 |
| `nextseek_api/graph_search/scope.py`, `nextseek_api/tests/test_graph_search_scope.py` | E2 |
| `nextseek_api/graph_search/query.py`, `nextseek_api/graph_search/lucene.py`, `nextseek_api/tests/test_graph_search_query.py` | E3 |
| `nextseek_api/graph_search/hydrate.py`, `nextseek_api/graph_search/catalog_cache.py`, `nextseek_api/tests/test_graph_search_hydrate.py` | E4 |
| `nextseek_api/graph_search/service.py`, `nextseek_api/services/graph_search.py`, `nextseek_api/endpoint_descriptions.py` (one constant), `nextseek_api/views.py` (one import), `nextseek_api/urls.py` (one line), `ci/routes.py` (one Route), `ci/smoke/test_registry_contents.py` (the count), `nextseek_api/tests/test_services_graph_search.py` | E5 |
| `scripts/graph_search/parity.py`, `scripts/graph_search/queries.json` | E6 |
| `scripts/graph_search/bench.py`, `scripts/graph_search/bench_report.py` | B1 |

---

### Task T0: the throwaway lane and package scaffolding

**Files:** create `scripts/graph_search/README.md`, `scripts/graph_search/lane.sh`,
`nextseek_api/graph_sync/__init__.py`, `nextseek_api/graph_sync/README.md`, `nextseek_api/graph_search/__init__.py`,
`nextseek_api/graph_search/README.md`; modify `scripts/README.md` (a row for the new folder), `nextseek_api/README.md`
(two rows in the children table).

**Interfaces:**
- Produces: `scripts/graph_search/lane.sh` subcommands used by every later task:
  `net`, `neo4j-up`, `neo4j-down`, `neo4j-cypher '<statement>'`, `mysql '<sql>'`, `app <manage.py args>`,
  `python <script> [args]` (runs a script inside the app image against the lane databases), `free-check`.
- Produces: `$GS_WORK/lane.env` keys read by `lane.sh`: `GS_MYSQL_CONTAINER` (default `gs-scratch-devbox-mysql`),
  `GS_MYSQL_ROOT_PASSWORD`, `GS_NEO4J_PASSWORD`, `GS_NEO4J_IMAGE` (the stack's Neo4j image id).

- [ ] **Step 1: Write `lane.sh`.**

```bash
#!/usr/bin/env bash
# Throwaway lane for the graph_search work: a MySQL (the existing scratch container) and a Neo4j,
# both on the gs-net network, and the app image run over a read-only mount of this checkout.
# Secrets come from $GS_WORK/lane.env and are never printed.
set -euo pipefail
: "${GS_WORK:?set GS_WORK to the graph-search work directory}"
# shellcheck disable=SC1091
source "$GS_WORK/lane.env"
MYSQL_C="${GS_MYSQL_CONTAINER:-gs-scratch-devbox-mysql}"
NEO4J_C=gs-v11-neo4j
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

free_check() {
  local avail; avail=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
  echo "MemAvailable ${avail} GiB"
  if (( avail < 2 )); then echo "refusing: less than 2 GiB available" >&2; exit 3; fi
}

case "${1:-}" in
  net)
    docker network inspect gs-net >/dev/null 2>&1 || docker network create gs-net
    docker network connect gs-net "$MYSQL_C" 2>/dev/null || true ;;
  neo4j-up)
    free_check
    docker run -d --name "$NEO4J_C" --network gs-net --memory 4g --memory-swap 4g \
      -e NEO4J_AUTH="neo4j/${GS_NEO4J_PASSWORD}" \
      -e NEO4J_server_memory_heap_initial__size=1500m -e NEO4J_server_memory_heap_max__size=1500m \
      -e NEO4J_server_memory_pagecache_size=1500m -e NEO4J_db_memory_transaction_max=1g \
      -e NEO4J_db_transaction_timeout=300s \
      -v gs-v11-neo4j-data:/data "${GS_NEO4J_IMAGE}" >/dev/null
    until docker exec "$NEO4J_C" cypher-shell -u neo4j -p "$GS_NEO4J_PASSWORD" 'RETURN 1' >/dev/null 2>&1; do
      sleep 3; done; echo "neo4j up" ;;
  neo4j-down)
    docker rm -f "$NEO4J_C" >/dev/null ;;
  neo4j-cypher)
    docker exec -i "$NEO4J_C" cypher-shell -u neo4j -p "$GS_NEO4J_PASSWORD" --format plain "$2" ;;
  mysql)
    docker exec -i "$MYSQL_C" sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4' <<<"$2" ;;
  app|python)
    free_check
    mkdir -p "$REPO/schema_rag/duckdb" "$REPO/schema_rag/embedding_models"
    run=(docker run --rm -i --network gs-net --memory 4g --memory-swap 4g
      -v "$REPO":/src:ro -v "$GS_WORK":/gswork -w /src
      -e DJANGO_SETTINGS_MODULE=dmac.settings -e LOG_DIR=/tmp/nextseek-logs -e PYTHONDONTWRITEBYTECODE=1
      -e MYSQL_HOST="$MYSQL_C" -e MYSQL_USER=root -e MYSQL_PASSWORD="$GS_MYSQL_ROOT_PASSWORD"
      -e MYSQL_DATABASE=seek_production -e NEXTSEEK_MYSQL_DATABASE=dmac
      -e NEXTSEEK_NEO4J_HOST="$NEO4J_C" -e NEXTSEEK_NEO4J_PASSWORD="$GS_NEO4J_PASSWORD"
      -e GS_RUN_DIR=/gswork/runs nextseek-nextseek:latest)
    if [[ "$1" == app ]]; then "${run[@]}" /app/.venv/bin/python manage.py "${@:2}"
    else "${run[@]}" /app/.venv/bin/python "${@:2}"; fi ;;
  free-check) free_check ;;
  *) echo "usage: lane.sh net|neo4j-up|neo4j-down|neo4j-cypher|mysql|app|python|free-check" >&2; exit 2 ;;
esac
```

- [ ] **Step 2: Verify the lane.** Run `lane.sh net`, `lane.sh neo4j-up`, then `lane.sh app check`. Expected:
  `System check identified no issues` (or only warnings). If settings import needs more environment variables,
  add them to the `run=(...)` array with inert values and record each one in `scripts/graph_search/README.md`.
  Run `lane.sh neo4j-cypher 'CYPHER 25 CREATE (n:Probe) SET n:$("T_X") RETURN labels(n)'` and
  `lane.sh neo4j-cypher 'CYPHER 25 MATCH (n:Probe) REMOVE n:$(["T_X"]) DETACH DELETE n'` to prove dynamic labels
  (set and remove by list) work on this image; record the result in the README.
- [ ] **Step 3: Scaffold the packages.** Empty `__init__.py` files; each README states what the package holds, its
  entry points, and links `docs/neo4j-schema.md` and the spec. Add the two children-table rows to
  `nextseek_api/README.md` and the folder row to `scripts/README.md`.
- [ ] **Step 4: Check the docs map.** `python3 ci/docs_map.py` exits 0.
- [ ] **Step 5: Commit.** `git add scripts/graph_search/README.md scripts/graph_search/lane.sh scripts/README.md
  nextseek_api/graph_sync/__init__.py nextseek_api/graph_sync/README.md nextseek_api/graph_search/__init__.py
  nextseek_api/graph_search/README.md nextseek_api/README.md` and commit `feat(scripts): add the graph_search
  throwaway lane and package scaffolding`.

---

### Task M1: the merged MySQL (gate M)

**Files:** create `scripts/graph_search/verify_merge.sql`, `scripts/graph_search/merge_tcga.sql`,
`scripts/graph_search/merge_tcga.sh`.

**Interfaces:**
- Consumes: `lane.sh mysql`; the scratch container holding the dev dump as `seek_production` and `dmac`; the local
  backup at `$GS_WORK/seeds/local-2026-09-14/`.
- Produces: in the scratch container, the dev data renamed to `dev_seek` and `dev_dmac`, and the merged dataset as
  `seek_production` and `dmac` (so code that names those schemas works unchanged); dumps at
  `$GS_WORK/seeds/merged-2026-09-14/` (`seek_production.core.sql.gz`, `dmac.sql.gz`, `MANIFEST.md`); the gate M
  report `$GS_WORK/runs/M1/gate_m.json`; a remap table `dmac.gs_remap(kind, old_id, new_id)` kept in the merged dmac
  for later tasks.
- Produces: two accounts in the merged data: SEEK login `tcgamember` (new person and user, member of the TCGA
  project) and the seed `user` (person 144, not a TCGA member). Django `auth_user` rows for both, with the password
  hash copied from the seed `user`'s Django row, so both use the seed password.

- [ ] **Step 1: Write `verify_merge.sql` first.** One SELECT per gate M check (spec section 4), each returning
  `check_name, expected, actual, pass`. Checks: the three merged counts; per-table row counts and a
  `CHECKSUM TABLE` for every pre-existing local table against a snapshot taken right after the local backup loads
  (`dmac.gs_premerge_checksums`); orphan counts for every remapped foreign key (samples.sample_type_id,
  samples.policy_id, assay_assets.assay_id, assay_assets.asset_id for Sample, permissions.policy_id,
  projects_samples both columns, studies.investigation_id, assays.study_id, investigations_projects both columns,
  dmac.assays_internal_assays both columns); TCGA `SUM(LENGTH(json_metadata))` = 896,626,185; per-sample
  `CRC32(json_metadata)` equal between `seek_production.samples` and `dev_seek.samples` for every TCGA id (count of
  mismatches = 0); every TCGA JSON key declared on its mapped type (a `JSON_KEYS` scan joined to
  `sample_attributes`, mismatches = 0); duplicate `samples.uuid` count = 14.
- [ ] **Step 2: Rename and load.** In `merge_tcga.sh`: `free-check`; create `dev_seek` and `dev_dmac` and move every
  table with `RENAME TABLE seek_production.t TO dev_seek.t` (and the same for dmac); recreate empty
  `seek_production` and `dmac`; stream `$GS_WORK/seeds/local-2026-09-14/seek_production.core.sql.gz` and `dmac.sql.gz`
  into them (`zcat | docker exec -i`); snapshot `CHECKSUM TABLE` for every table into `dmac.gs_premerge_checksums`.
  Run `verify_merge.sql`: every TCGA check fails (nothing merged yet) and every local check passes.
- [ ] **Step 3: Write `merge_tcga.sql`**, one transaction per block, following the spec's remap list:
  1. Remap scaffolding: `dmac.gs_remap`; the TCGA investigation id is found by title `TCGA` in `dev_seek`.
  2. Project: insert one `projects` row (next id, 16), one `work_groups` row (a local institution), and the default
     policy; record in `gs_remap`.
  3. Investigation, `investigations_projects`, studies, assays: new ids from AUTO_INCREMENT, foreign keys through
     `gs_remap`; contributor rewritten to person 145.
  4. Sample types: the 11 shared codes map to local ids by title; A.MET, A.RPPA and D.ARR are inserted with new ids;
     `projects_sample_types` rows for project 16.
  5. Sample attributes: the 344 missing (type title, attribute title) pairs are inserted, `sample_attribute_type_id`
     mapped by `sample_attribute_types.title`, `pos` after the type's local maximum, `is_title` 0 on existing types.
  6. Samples: `INSERT ... SELECT` from `dev_seek.samples` for the TCGA ids, keeping `id`, `uuid`, `policy_id`,
     `json_metadata` byte for byte; `sample_type_id` and `contributor_id` through `gs_remap`. Before inserting, assert
     that no TCGA text column holds a four-byte character (the local tables are utf8mb3).
  7. `projects_samples` (project 16), policies (kept ids), permissions (new ids, `contributor_id` = project 16),
     `assay_assets` (kept ids, `assay_id` through `gs_remap`).
  8. dmac: 6 new `internal_assays` titles, `assays_internal_assays` rows through `gs_remap`, 3
     `sample_types_clades` rows by clade title.
  9. Accounts: person and user `tcgamember` (password hash and salt copied from users.login = 'user'), a
     `group_memberships` row into the project 16 work group; the Django `auth_user` row with the same password hash
     as the Django row for `user`, `is_superuser` 0.
- [ ] **Step 4: Run and verify.** `merge_tcga.sh` runs `merge_tcga.sql`, then `verify_merge.sql`, writes
  `gate_m.json`, and exits non-zero if any check fails. Expected: every check passes.
- [ ] **Step 5: Dump.** `mysqldump --single-transaction --quick --routines --triggers` of the merged
  `seek_production` (all tables) and `dmac` to `$GS_WORK/seeds/merged-2026-09-14/`, mode 600, with a `MANIFEST.md`
  (sizes, sha256, counts from gate M).
- [ ] **Step 6: Commit** the three scripts: `feat(scripts): merge TCGA into the local production snapshot for
  graph_search`.

---

### Task G1: sample projection (pure)

**Files:** create `nextseek_api/graph_sync/projection.py`, `nextseek_api/tests/test_graph_sync_projection.py`.

**Interfaces:**
- Produces:
  - `label_for(title: str) -> str`
  - `value_type_for(base_type: str | None) -> str` returning `"float" | "integer" | "date" | "string"`
  - `cast_value(value, value_type: str) -> tuple[object, bool]` (the stored value, whether the cast succeeded)
  - `is_empty(value) -> bool`
  - `SYSTEM_KEYS: frozenset[str]` = `{"id", "uuid", "type", "title", "project_ids", "search_text", "synced_at",
    "parent_titles", "parent_title_hashes"}`
  - `SKIPPED_METADATA_KEYS: frozenset[str]` = `{"UID"}`
  - `@dataclass SampleProjection(id: int, sample_type_id: int, label: str, props: dict, cast_failures: list[str])`
  - `project_sample(row: dict, sample_type_title: str, value_types: dict[str, str], project_ids: list[int]) ->
    SampleProjection` where `row` has `id`, `uuid`, `title`, `sample_type_id`, `json_metadata` (str), and
    `value_types` maps attribute title to value_type for that sample type.

- [ ] **Step 1: Write the failing tests.**

```python
import json
from datetime import date

import pytest

from nextseek_api.graph_sync import projection as p


@pytest.mark.parametrize("title, label", [
    ("TIS", "T_TIS"), ("D.SEQ", "T_D_SEQ"), ("A.VCF", "T_A_VCF"), ("X Y-1", "T_X_Y_1"),
])
def test_label_for(title, label):
    assert p.label_for(title) == label


@pytest.mark.parametrize("base, vt", [
    ("Float", "float"), ("Integer", "integer"), ("Date", "date"), ("DateTime", "date"),
    ("Text", "string"), ("String", "string"), (None, "string"),
])
def test_value_type_for(base, vt):
    assert p.value_type_for(base) == vt


@pytest.mark.parametrize("value, vt, expected", [
    ("12000000", "float", (12000000.0, True)),
    ("3", "integer", (3, True)),
    ("3.0", "integer", (3, True)),
    ("n/a", "float", ("n/a", False)),
    ("2024-01-31", "date", (date(2024, 1, 31), True)),
    ("2024-01-31 10:22:00", "date", (date(2024, 1, 31), True)),
    ("1/31/2024", "date", (date(2024, 1, 31), True)),
    ("2019", "date", ("2019", False)),
    ("Lung", "string", ("Lung", True)),
    (7, "string", (7, True)),
])
def test_cast_value(value, vt, expected):
    assert p.cast_value(value, vt) == expected


@pytest.mark.parametrize("value, empty", [
    (None, True), ("", True), ([], True), ({}, True), (" ", False), (0, False), ("0", False),
])
def test_is_empty(value, empty):
    assert p.is_empty(value) is empty


def _row(meta):
    return {"id": 5, "uuid": "TIS-220119FLY-7", "title": "t", "sample_type_id": 26,
            "json_metadata": json.dumps(meta)}


def test_project_sample_keeps_non_empty_values_verbatim_and_typed():
    meta = {"UID": "TIS-220119FLY-7", "Organ": "Lung", "Type": "PBMC", "Media supplement ": "x",
            "CellCount": "12000000", "Empty": "", "Nothing": None, "Parent": "MUS-1;MUS-2"}
    proj = p.project_sample(_row(meta), "TIS", {"CellCount": "float"}, [6, 2, 2])
    assert proj.label == "T_TIS"
    assert proj.props["id"] == 5 and proj.props["uuid"] == "TIS-220119FLY-7"
    assert proj.props["type"] == "TIS" and proj.props["project_ids"] == [2, 6]
    assert proj.props["Organ"] == "Lung" and proj.props["Type"] == "PBMC"
    assert proj.props["Media supplement "] == "x"
    assert proj.props["CellCount"] == 12000000.0
    assert "UID" not in proj.props and "Empty" not in proj.props and "Nothing" not in proj.props
    assert proj.props["Parent"] == "MUS-1;MUS-2"


def test_search_text_holds_values_only_one_per_line_unstripped():
    meta = {"Organ": "Lung", "Notes": " granuloma  ", "CellCount": "5"}
    proj = p.project_sample(_row(meta), "TIS", {"CellCount": "float"}, [])
    lines = proj.props["search_text"].split("\n")
    assert sorted(lines) == sorted(["Lung", " granuloma  ", "5"])
    assert "Organ" not in proj.props["search_text"]


def test_cast_failure_keeps_raw_string_and_is_reported():
    proj = p.project_sample(_row({"CellCount": "lots"}), "TIS", {"CellCount": "float"}, [])
    assert proj.props["CellCount"] == "lots"
    assert proj.cast_failures == ["CellCount"]


def test_non_primitive_values_become_json_strings():
    proj = p.project_sample(_row({"Tags": ["a", "b"]}), "TIS", {}, [])
    assert proj.props["Tags"] == '["a", "b"]'
```

- [ ] **Step 2: Run them to see them fail.** Django unit lane with
  `nextseek_api/tests/test_graph_sync_projection.py`. Expected: import error.
- [ ] **Step 3: Implement.**

```python
"""Project one SEEK sample row onto its graph schema v1.1 node (docs/neo4j-schema.md, v1.1)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

SYSTEM_KEYS = frozenset({"id", "uuid", "type", "title", "project_ids", "search_text", "synced_at",
                         "parent_titles", "parent_title_hashes"})
SKIPPED_METADATA_KEYS = frozenset({"UID"})
_LABEL_RE = re.compile(r"[^A-Za-z0-9_]")
_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_MDY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def label_for(title: str) -> str:
    return "T_" + _LABEL_RE.sub("_", title)


def value_type_for(base_type):
    return {"Float": "float", "Integer": "integer", "Date": "date", "DateTime": "date"}.get(base_type or "", "string")


def is_empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _parse_date(s: str):
    s = s.strip()
    m = _MDY_RE.match(s)
    if m:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return datetime.fromisoformat(s[:10]).date()
    raise ValueError(s)


def cast_value(value, value_type: str):
    if value_type == "string" or not isinstance(value, str):
        if value_type in ("float", "integer") and isinstance(value, (int, float)) and not isinstance(value, bool):
            return (float(value) if value_type == "float" else int(value)), True
        return value, True
    try:
        if value_type in ("float", "integer"):
            if not _NUM_RE.match(value.strip()):
                raise ValueError(value)
            f = float(value)
            if value_type == "integer":
                if not f.is_integer():
                    raise ValueError(value)
                return int(f), True
            return f, True
        return _parse_date(value), True
    except (ValueError, OverflowError):
        return value, False


@dataclass
class SampleProjection:
    id: int
    sample_type_id: int
    label: str
    props: dict
    cast_failures: list = field(default_factory=list)


def project_sample(row, sample_type_title, value_types, project_ids):
    meta = json.loads(row["json_metadata"] or "{}")
    props = {"id": int(row["id"]), "uuid": row["uuid"], "type": sample_type_title, "title": row.get("title"),
             "project_ids": sorted(set(int(x) for x in project_ids))}
    values, failures = [], []
    for key, raw in meta.items():
        if key in SKIPPED_METADATA_KEYS or is_empty(raw):
            continue
        if isinstance(raw, (list, dict)):
            raw = json.dumps(raw)
        typed, ok = cast_value(raw, value_types.get(key, "string"))
        if not ok:
            failures.append(key)
        props[key] = typed
        values.append(str(raw))
    props["search_text"] = "\n".join(values)
    return SampleProjection(int(row["id"]), int(row["sample_type_id"]), label_for(sample_type_title), props, failures)
```

- [ ] **Step 4: Run the tests.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(graph_sync): project a SEEK sample onto its v1.1 graph node`.

---

### Task G2: the catalog (pure)

**Files:** create `nextseek_api/graph_sync/catalog.py`, `nextseek_api/tests/test_graph_sync_catalog.py`.

**Interfaces:**
- Consumes: `label_for`, `value_type_for` (G1).
- Produces:
  - `build_sample_types(types: list[dict], context: dict[str, dict], clades: dict[int, str], deprecated:
    set[str]) -> list[dict]`. Each input type has `id`, `title`, `uuid`, `description`; `context` maps a title
    (byte-exact) to its `sample_types_context` row (`name`, `description`, `tags`, `parent_sampletypes`,
    `child_sampletypes`); `clades` maps a type id to a clade title. Each output dict is the node's full property map:
    `id`, `title`, `label`, `uuid`, `seek_description`, `deprecated`, `has_context`, and when context exists `name`,
    `summary`, `tags` (list of strings), `curated_parents`, `curated_children` (strings joined by `" | "`), `clade`.
  - `build_attributes(attrs: list[dict], attr_types: dict[int, dict], meanings: dict[str, str], type_titles:
    dict[int, str]) -> list[dict]`. Each input attribute has `id`, `sample_type_id`, `title`, `pos`, `required`,
    `is_title`, `sample_attribute_type_id`, `description`. Output property maps carry `key`
    (`"<sample_type_id>:<title>"`), `id`, `sample_type_id`, `sample_type`, `title`, `pos`, `required`, `is_title`,
    `base_type`, `value_type`, `declared` (True), `seek_description`, `meaning` (byte-exact title lookup), `role`,
    `unit_key`, `needs_backticks`.
  - `undeclared_attribute(sample_type_id: int, sample_type: str, title: str) -> dict` (`declared` False, no `id`).
  - `role_for(title: str) -> str`: `"lineage"` when `"parent"` is in the lowercased title, `"file"` for titles
    starting `File_`, `Checksum_` or `Link_`, `"identifier"` for `UID`, `"unit"` for titles ending `Units`, `_Units`,
    `Unit` or `_Unit`, else `"data"`.
  - `catalog_hash(sample_types: list[dict], attributes: list[dict]) -> str`: sha256 of the canonical JSON of the
    sorted (type id, title, label) and (key, value_type, declared) tuples.
  - `assert_labels_unique(sample_types) -> None` raises `ValueError` naming the colliding titles.

- [ ] **Step 1: Write the failing tests**: label collision raises; `meaning` never matches case-insensitively
  (`"Organ"` does not take the meaning of `"organ"`); a trailing-space title keeps its space in `key` and `title`;
  `unit_key` is set on `CellCountUnits` only when `CellCount` exists on the same type; `has_context` is False and no
  context property is present when the title has no context row; `catalog_hash` is stable under input order and
  changes when one `value_type` changes; `role_for` covers each branch.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** the functions above as pure code over the inputs. Parse `tags` and the curated lists
  with `nextseek_api.services.context_catalog.parse_list` and `parse_alternation` if they exist under those names
  (read the module first); otherwise split on the delimiters that module documents.
- [ ] **Step 4: Run the tests.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(graph_sync): build the SampleType and Attribute catalog`.

---

### Task G3: MySQL sources

**Files:** create `nextseek_api/graph_sync/sources.py`, `nextseek_api/tests/test_graph_sync_sources.py`.

**Interfaces:**
- Consumes: Django connections `seek` (seek_production) and `default` (dmac).
- Produces:
  - `iter_samples(chunk: int = 5000, after_id: int = 0) -> Iterator[list[dict]]`: keyset pages of
    `id, uuid, title, sample_type_id, json_metadata` ordered by `id`.
  - `sample_projects() -> dict[int, list[int]]`: distinct `projects_samples` pairs.
  - `sample_types()`, `sample_attributes()`, `sample_attribute_types() -> dict[int, dict]`,
    `type_context() -> dict[str, dict]` (through the ORM model whose `tags` field maps to the `Tags` column; empty
    dict when the table is absent), `attribute_meanings() -> dict[str, str]` (`sample_attributes_unique`, the
    `sample_type = ''` row per field name, byte-exact keys compared in Python), `type_clades() -> dict[int, str]`,
    `deprecated_titles() -> set[str]` (from `nextseek_api.services.template_catalog`).
  - `projects() -> list[dict]` (`id`, `title`), `memberships() -> list[dict]` (`person_id`, `project_id`,
    `has_left`, `time_left_at`, from `group_memberships` joined to `work_groups`), `investigation_projects() ->
    list[dict]`, `investigations() -> list[dict]`.
  - `seek_study_links() -> list[dict]`: (`sample_id`, `study_id`, `study_title`, `investigation_id`) through
    `assay_assets` (`asset_type = 'Sample'`) and `assays`.
  - `uuid_to_ids() -> dict[str, list[int]]`.
  - `declared_lineage(sample_rows: Iterable[dict], uuid_index) -> Iterator[tuple[int, int]]`: (child id, parent id)
    from `collect_parent_tokens` and `UID_RE` in `nextseek_api/batch_upload/helpers.py`.
  - `table_exists(alias: str, table: str) -> bool`.

- [ ] **Step 1: Write the failing tests** with a fake cursor (a small class recording `execute` calls and returning
  canned rows): `iter_samples` issues `WHERE id > %s ORDER BY id LIMIT %s` and advances `after_id` to the last id;
  `sample_projects` de-duplicates repeated pairs and sorts; `declared_lineage` yields only UID tokens that exist in
  `uuid_index`, one pair per matching parent id, and none for a sample naming itself; a missing
  `sample_types_context` gives `{}`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** with `connections["seek"].cursor()` and `connections["default"].cursor()`, bound
  parameters only.
- [ ] **Step 4: Run the tests.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(graph_sync): read the v1.1 sources from MySQL`.

---

### Task G4: the Neo4j writer

**Files:** create `nextseek_api/graph_sync/cypher.py`, `nextseek_api/graph_sync/writer.py`,
`nextseek_api/tests/test_graph_sync_writer.py`.

**Interfaces:**
- Consumes: G1 and G2 outputs; a `neo4j.Driver` and a database name.
- Produces (`writer.py`), every function taking `(driver, db, ...)` and returning a counts dict:
  - `find_ghosts(driver, db, mysql_ids: set[int], mysql_uuids: set[str]) -> dict` (`ghost_element_ids`: nodes whose
    `id` is shared by two nodes and whose `uuid` is not in MySQL; `orphan_ids`: other Sample ids not in MySQL)
  - `delete_ghosts(driver, db, element_ids)`, `relabel_orphans(driver, db, ids)`
  - `archive_and_drop_child_of(driver, db, out_path: str, declared_pairs: set[tuple[str, str]])` (writes one TSV row
    per pair: child uuid, parent uuid, `declared`; then deletes in batches of 50,000)
  - `ensure_constraints_v11(driver, db)`, `ensure_index_budget(driver, db, census: dict) -> list[str]`,
    `ensure_fulltext(driver, db)`
  - `write_sample_types(driver, db, rows)`, `write_attributes(driver, db, rows)` (also deletes Attribute nodes whose
    `key` is not in `rows`), `write_projects(driver, db, rows)`, `write_people_and_memberships(driver, db, rows)`
    (replaces every MEMBER_OF), `write_investigation_projects(driver, db, investigations, links)`
  - `write_samples(driver, db, projections: list[SampleProjection]) -> dict`
  - `write_missing_lineage(driver, db, pairs: list[tuple[int, int]])`
  - `write_seek_studies(driver, db, links)` (only samples with no IN_STUDY)
  - `write_attribute_counts(driver, db, counts: dict[str, int])`, `write_graphmeta(driver, db, catalog_hash)`
- `cypher.py` holds every statement as a module constant, so tests assert on text.

Key statements (`cypher.py`):

```python
CONSTRAINTS_V11 = [
    "CREATE CONSTRAINT sample_id_unique IF NOT EXISTS FOR (s:Sample) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT sample_type_id_unique IF NOT EXISTS FOR (t:SampleType) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT sample_type_title_unique IF NOT EXISTS FOR (t:SampleType) REQUIRE t.title IS UNIQUE",
    "CREATE CONSTRAINT sample_type_label_unique IF NOT EXISTS FOR (t:SampleType) REQUIRE t.label IS UNIQUE",
    "CREATE CONSTRAINT attribute_key_unique IF NOT EXISTS FOR (a:Attribute) REQUIRE a.key IS UNIQUE",
    "CREATE CONSTRAINT attribute_id_unique IF NOT EXISTS FOR (a:Attribute) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT project_id_unique IF NOT EXISTS FOR (p:Project) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT person_id_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT study_id_unique IF NOT EXISTS FOR (s:Study) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT investigation_id_unique IF NOT EXISTS FOR (i:Investigation) REQUIRE i.id IS UNIQUE",
    "CREATE INDEX sample_uuid IF NOT EXISTS FOR (s:Sample) ON (s.uuid)",
    "CREATE INDEX sample_type IF NOT EXISTS FOR (s:Sample) ON (s.type)",
    "CREATE INDEX study_seek_study_id IF NOT EXISTS FOR (s:Study) ON (s.seek_study_id)",
]
FULLTEXT = ("CREATE FULLTEXT INDEX sample_search_text IF NOT EXISTS "
            "FOR (s:Sample) ON EACH [s.search_text]")

BACKFILL_SAMPLE_TYPE_ID = """
UNWIND $rows AS r
MATCH (t:SampleType {title: r.title}) WHERE t.id IS NULL
SET t.id = r.id
"""
MERGE_SAMPLE_TYPES = """
UNWIND $rows AS r
MERGE (t:SampleType {id: r.id})
SET t = r
"""
MERGE_ATTRIBUTES = """
UNWIND $rows AS r
MERGE (a:Attribute {key: r.key})
SET a = r
WITH a, r
MATCH (t:SampleType {id: r.sample_type_id})
MERGE (t)-[:HAS_ATTRIBUTE]->(a)
"""
DELETE_GONE_ATTRIBUTES = "MATCH (a:Attribute) WHERE NOT a.key IN $keys DETACH DELETE a"

WRITE_SAMPLES = """
CYPHER 25
UNWIND $rows AS r
MERGE (s:Sample {id: r.id})
WITH s, r, s.parent_titles AS pt, s.parent_title_hashes AS pth
SET s = r.props
SET s.parent_titles = pt, s.parent_title_hashes = pth, s.synced_at = datetime()
SET s:$(r.label)
WITH s, r, [l IN labels(s) WHERE l STARTS WITH 'T_' AND l <> r.label] AS stale
REMOVE s:$(stale)
WITH s, r
CALL (s) { MATCH (s)-[o:OF_TYPE|IN_PROJECT]->() DELETE o }
WITH s, r
MATCH (t:SampleType {id: r.sample_type_id})
MERGE (s)-[:OF_TYPE]->(t)
WITH s, r
UNWIND r.props.project_ids AS pid
MATCH (p:Project {id: pid})
MERGE (s)-[:IN_PROJECT]->(p)
"""
WRITE_MISSING_LINEAGE = """
UNWIND $rows AS r
MATCH (c:Sample {id: r[0]})
MATCH (p:Sample {id: r[1]})
MERGE (c)-[e:DERIVED_FROM]->(p)
ON CREATE SET e.child_id = r[2], e.parent_id = r[3]
"""
```

`WRITE_MISSING_LINEAGE` sets `child_id` and `parent_id` in whatever form existing DERIVED_FROM edges use: read one
existing edge first (`MATCH ()-[e:DERIVED_FROM]->() RETURN e LIMIT 1`) and pass ids or uuids accordingly as `r[2]`,
`r[3]`.

- [ ] **Step 1: Write the failing tests** with a fake driver whose `execute_query(q, params, database_=...)`
  records calls: `write_samples` sends `WRITE_SAMPLES` once per chunk with `rows` carrying `id`, `label`,
  `sample_type_id` and `props`; `write_attributes` sends `DELETE_GONE_ATTRIBUTES` with every key; `ensure_index_budget`
  creates one index per qualifying (label, title) pair, quotes titles with backticks (doubling any backtick in a
  title), never indexes a `lineage` or `file` role, and names each index `gs_<label>_<first 10 hex of sha1(title)>`;
  `archive_and_drop_child_of` writes the TSV before any delete statement.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.** Reuse `_retry` from `nextseek_api/batch_upload/neo4j_sync.py` for every write.
  Index budget: a (type, title) pair qualifies when its `value_type` is float, integer or date and it has values;
  or its `value_type` is string, it has at least 1,000 samples and no value longer than 4,000 characters; or it is in
  the benchmark key list `scripts/graph_search/queries.json` names (passed in as a set by the caller).
- [ ] **Step 4: Run the tests.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(graph_sync): write graph schema v1.1 to Neo4j`.

---

### Task G5: verification and the `graph_sync` command

**Files:** create `nextseek_api/graph_sync/verify.py`, `nextseek_api/graph_sync/run.py`,
`nextseek_api/management/commands/graph_sync.py`, `nextseek_api/tests/test_graph_sync_command.py`.

**Interfaces:**
- Consumes: G1 to G4.
- Produces:
  - `run.full_sync(driver, db, chunk=5000, dry_run=False, run_dir: str | None = None, bench_keys=frozenset()) ->
    dict` in the spec's order (section 6), accumulating the census (per (type id, title): sample count, maximum value
    length, cast failures) during the sample pass, then attribute counts, undeclared Attribute nodes, the index
    budget, the fulltext index and GraphMeta.
  - `run.catalog_sync(driver, db) -> dict` (catalog nodes only).
  - `verify.gate_g(driver, db, sample_size=1000) -> dict`: `{"checks": [{"name", "expected", "actual", "pass"}],
    "pass": bool}` covering the eight gate G checks.
  - Command: `manage.py graph_sync (--full | --catalog | --verify) [--json] [--dry-run] [--chunk N]
    [--run-dir PATH] [--i-mean-the-live-graph]`. Exit 0 on success, 1 when `--verify` fails, 2 on a refusal.
- Refusal: when the host part of `settings.NEO4J_DATABASE["URI"]` is `neo4j` (the live stack's service name) and
  `--i-mean-the-live-graph` is absent, the command prints the reason and exits 2 before connecting.

Gate G queries (`verify.py`), each paired with its MySQL side:
1. Lineage: every pair from `sources.declared_lineage` over all samples exists as
   `(:Sample {id: c})-[:DERIVED_FROM]->(:Sample {id: p})`; extra graph pairs are counted and must touch an
   `OrphanSample`.
2. Scope: for every project, `MATCH (s:Sample) WHERE $pid IN s.project_ids RETURN count(s)` equals the distinct
   `projects_samples` count; for 1,000 random samples, `s.project_ids` equals the MySQL list.
3. Catalog coverage: `MATCH (s:Sample) WITH s LIMIT $n ...` over 1,000 random samples, every key outside
   `SYSTEM_KEYS` is the `title` of an Attribute reachable from the sample's SampleType; plus one aggregate over all
   samples per type (`UNWIND keys(s)` grouped by `s.type`) compared with the Attribute titles.
4. `MATCH (s:Sample) RETURN count(s)` equals `COUNT { (:Sample)-[:OF_TYPE]->() }` equals the MySQL sample count;
   `MATCH (s:Sample) WHERE size([l IN labels(s) WHERE l STARTS WITH 'T_']) <> 1 RETURN count(s)` is 0.
5. Attribute nodes with an `id` equal `sample_attributes` by id, and their titles byte-exact.
6. For every person with a membership and for `tcgamember` and `user`, the graph count
   (`any(p IN s.project_ids WHERE p IN $projects)`) equals the SQL `EXISTS projects_samples` count.
7. For 1,000 random samples, `properties(s)` minus system keys equals `projection.project_sample(...).props` minus
   system keys (compare JSON with dates as ISO strings).
8. `SHOW CONSTRAINTS` and `SHOW INDEXES` states are ONLINE; 0 label collisions; 0 SampleTypes without `id` or `label`.

- [ ] **Step 1: Write the failing tests**: the refusal (host `neo4j`, no flag: exit 2, no driver created); argument
  exclusivity (`--full` with `--verify` is an error); `--dry-run --full` calls `full_sync(dry_run=True)` and prints
  counts; `--verify --json` prints the gate dict and exits 1 when `pass` is false (patch `verify.gate_g`).
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.** The command builds the driver from `settings.NEO4J_DATABASE` exactly as
  `nextseek_api/services/entity_tree.py` does.
- [ ] **Step 4: Run the tests**, then the whole new set:
  `nextseek_api/tests/test_graph_sync_projection.py nextseek_api/tests/test_graph_sync_catalog.py
  nextseek_api/tests/test_graph_sync_sources.py nextseek_api/tests/test_graph_sync_writer.py
  nextseek_api/tests/test_graph_sync_command.py`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(graph_sync): add the graph_sync command and its gate G verification`.

---

### Task G6: build the v1.1 graph (gate G)

**Files:** create `scripts/graph_search/load_graph_backup.py`, `scripts/graph_search/dump_graph.sh`.

**Interfaces:**
- Consumes: M1's merged MySQL; G5's command; the local graph backup
  `$GS_WORK/seeds/local-2026-09-14/neo4j.cypher.gz`.
- Produces: the v1.1 graph in `gs-v11-neo4j`; `$GS_WORK/runs/G6/full_sync.json`, `gate_g.json`,
  `child_of_archive.tsv`; an offline dump `$GS_WORK/seeds/v11-graph-2026-09-14/neo4j.dump` for the operator's load.

- [ ] **Step 1: Load the backup.** `load_graph_backup.py` streams the backup file statement by statement and
  groups CREATE statements by label into `UNWIND ... CREATE` batches of 5,000, the same technique as
  `startup/steps/seed.py` (import its parser if it exposes one; otherwise reimplement the grouping, streaming instead of
  reading the whole file). Run inside the lane: `lane.sh python scripts/graph_search/load_graph_backup.py
  /gswork/seeds/local-2026-09-14/neo4j.cypher.gz`. Expected: node and relationship counts equal the backup
  manifest's (166,737 nodes; 1,719,518 relationships).
- [ ] **Step 2: Dry run.** `lane.sh app graph_sync --full --dry-run --json`. Expected: 1,084,754 samples projected,
  0 label collisions, the ghost list has 79 element ids.
- [ ] **Step 3: Full run.** `lane.sh app graph_sync --full --json --run-dir /gswork/runs/G6`. Watch `free -g`.
- [ ] **Step 4: Gate G.** `lane.sh app graph_sync --verify --json > $GS_WORK/runs/G6/gate_g.json`. Expected: `pass`
  true. On a failure, fix the writer in its task (G4 or G5), rerun from Step 1, never patch the graph by hand.
- [ ] **Step 5: Dump.** `dump_graph.sh` stops `gs-v11-neo4j`, runs `neo4j-admin database dump neo4j` in a throwaway
  container over the `gs-v11-neo4j-data` volume into `$GS_WORK/seeds/v11-graph-2026-09-14/`, and starts it again.
- [ ] **Step 6: Commit** the two scripts: `feat(scripts): load the graph backup and dump the v1.1 graph`.

---

### Task E1: graph_search request models

**Files:** modify `nextseek_api/models.py` (append after `SampleAdvancedSearchResult`); create
`nextseek_api/tests/test_graph_search_models.py`.

**Interfaces:**
- Produces:

```python
class GraphSearchWhere(BaseModel):
    sample_type: str
    attribute: str
    op: Literal["=", "<>", "<", "<=", ">", ">=", "IN", "CONTAINS", "STARTS WITH"]
    value: Union[str, int, float, List[Union[str, int, float]]]
    model_config = ConfigDict(extra="forbid")


class GraphSearchLineage(BaseModel):
    direction: Literal["ancestor", "descendant"]
    sample_type: str
    max_hops: int = Field(default=4, ge=1, le=4)
    model_config = ConfigDict(extra="forbid")


class GraphSearchExtensions(BaseModel):
    where: List[GraphSearchWhere] = Field(default_factory=list)
    lineage: Optional[GraphSearchLineage] = None
    model_config = ConfigDict(extra="forbid")


class GraphSearchRequest(SampleAdvancedSearchRequest):
    extensions: Optional[GraphSearchExtensions] = None
```

  `IN` requires a list value; every other operator requires a scalar (a model validator raises otherwise). All
  `where` items must name the same `sample_type` (validator). `filter_searchText` stays required, as in the parent,
  and may be `""` when `extensions.where` is present.

- [ ] **Step 1: Write the failing tests**: a plain advanced_search body validates; an unknown top-level key fails;
  `IN` with a scalar fails; `=` with a list fails; two `where` items on different sample types fail; `max_hops` 5
  fails; `to_db_filters` still works on the subclass.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** the models above with the validators.
- [ ] **Step 4: Run the tests.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(graph_search): add the graph_search request models`.

---

### Task E2: the scope resolver

**Files:** create `nextseek_api/graph_search/scope.py`, `nextseek_api/tests/test_graph_search_scope.py`.

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class Scope:
    is_admin: bool
    person_id: Optional[int]
    project_ids: tuple[int, ...]


class ScopeUnavailable(Exception):
    """The caller maps to no SEEK person."""


def resolve_scope(user) -> Scope: ...
```

  Superuser: `Scope(True, None, ())` with no query. Otherwise `SELECT person_id FROM users WHERE login = %s` on
  connection `seek` with `user.username`; no row or a NULL person: raise `ScopeUnavailable`. Then
  `SELECT DISTINCT wg.project_id FROM group_memberships gm JOIN work_groups wg ON wg.id = gm.work_group_id WHERE
  gm.person_id = %s ORDER BY wg.project_id` (former members included, as SEEK REST does today).

- [ ] **Step 1: Write the failing tests** with a patched `connections["seek"].cursor()`: superuser runs no SQL;
  `is_staff` alone is not admin; a known login returns its sorted project ids; an unknown login raises
  `ScopeUnavailable`; a person with no membership returns an empty tuple.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(graph_search): resolve a caller's project scope from MySQL`.

---

### Task E3: the query builder

**Files:** create `nextseek_api/graph_search/lucene.py`, `nextseek_api/graph_search/query.py`,
`nextseek_api/tests/test_graph_search_query.py`.

**Interfaces:**
- Consumes: `Scope` (E2); a `Catalog` value (defined here, filled by E4's cache).
- Produces:

```python
@dataclass(frozen=True)
class Catalog:
    type_title_by_id: dict[int, str]
    label_by_title: dict[str, str]
    titles_by_type: dict[str, frozenset[str]]        # sample type title -> attribute titles
    value_type: dict[tuple[str, str], str]           # (type title, attribute title) -> value_type


class GraphSearchInvalid(ValueError):
    """A request the catalog rejects; the view answers 422 with str(exc)."""


@dataclass(frozen=True)
class BuiltQuery:
    page_cypher: str
    count_cypher: str
    ids_cypher: str          # every matching id, no paging (parity harness)
    params: dict


def build(filters: dict, extensions, scope: Scope, catalog: Catalog, page: int, page_size: int) -> BuiltQuery: ...
```

  `filters` is `SampleAdvancedSearchRequest.to_db_filters(...)`'s output. `lucene.candidate_query(term: str) ->
  str | None` returns the fulltext query for one term: split on characters outside `[A-Za-z0-9]`, lowercase, drop
  tokens shorter than 3 characters, escape Lucene specials, wrap each token as `*tok*`, AND the tokens; `None` when
  no token survives.

Rules (the spec's matching table), implemented as Cypher fragments over one node variable `s`:
- Terms: split exactly as `SampleAdvancedSearchViewSet.create` does (list or single string, stripped, empties
  dropped); UID terms (`UID_RE`) go to `s.uuid IN $uids`.
- Candidate source, in order of preference:
  1. text terms, each with a candidate query: `CALL db.index.fulltext.queryNodes('sample_search_text', $lucene)
     YIELD node AS s`, with the per-term queries joined by ` OR ` or ` AND ` per `searchText_logic`;
  2. UID terms only: `MATCH (s:Sample) WHERE s.uuid IN $uids`;
  3. text and UID terms together: `CALL () { <1> RETURN s UNION MATCH (s:Sample) WHERE s.uuid IN $uids RETURN s }`;
  4. `extensions.where` with no terms: `MATCH (s:<label>)`, the label taken from the catalog, backticked;
  5. types only: `MATCH (s:Sample) WHERE s.type IN $types`;
  6. a text term with no candidate query (every token under 3 characters): as 5 when types are given, else
     `MATCH (s:Sample)` (a full scan, logged).
- Filters, ANDed after the source (`WITH s WHERE ...`):
  - types: `s.type IN $types` (titles from `type_title_by_id`);
  - scope: `any(p IN s.project_ids WHERE p IN $projects)` unless `scope.is_admin`;
  - text verification per term `$tN` (lowercased): PARTIAL `toLower(s.search_text) CONTAINS $tN`; EXACT
    `$tN IN split(toLower(s.search_text), '\n')`; combined by `searchText_logic`; a UID term's verification is
    `s.uuid = $uN`;
  - `attribute` stage (only with terms): each requested name resolves to every catalog title on the requested types
    (all types when none) whose lowercase equals the lowercased name; per term, PARTIAL
    `toLower(toString(s.\`T\`)) CONTAINS $tN`, EXACT `trim(toLower(toString(s.\`T\`))) = trim($tN)`, combined across a
    name's variants by OR, across names by `attribute_logic` (single name when absent), across terms by
    `searchText_logic`; a name with no catalog title matches nothing;
  - `extensions.where`: each item's attribute must be in `titles_by_type[item.sample_type]`, else
    `GraphSearchInvalid`; the value is cast with G1's `cast_value` by `value_type`; the fragment is
    ``s.`Title` <op> $wN``;
  - `extensions.lineage`: descendant `EXISTS { (s)<-[:DERIVED_FROM*1..H]-(:<label>) }`, ancestor
    `EXISTS { (s)-[:DERIVED_FROM*1..H]->(:<label>) }`, `H` the validated integer, the label from the catalog.
- Outputs: page `... WITH s ORDER BY s.id SKIP $skip LIMIT $limit RETURN s.id AS id`; count `... RETURN count(s) AS
  total, collect(DISTINCT s.type) AS types`; ids `... RETURN s.id AS id ORDER BY id`. All statements start with
  `CYPHER 25`.
- Property names are backticked with any backtick doubled. No value is ever interpolated.

- [ ] **Step 1: Write the failing tests.** One test per rule above, asserting fragments of the Cypher text and the
  exact `params`. Include: a non-admin gets the scope fragment and an admin does not; `sampletype` ids map to titles;
  `"C57BL/6J"` gives the candidate query `*c57bl*` (the token `6j` is under 3 characters and is left to the
  verification step); a term of only short tokens falls back to the type scan; `attribute: "organ"` resolves to `Organ` and `organ`
  when both exist on the type; `where` on an unknown attribute raises `GraphSearchInvalid`; a `where` CellCount `>=`
  with the string `"10000000"` is cast to a float; lineage `max_hops` is interpolated as an integer and the label is
  backticked; a title containing a backtick is doubled.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** `lucene.py` and `query.py`. Read `seek/sample/search.py::SampleSearchMixin._filterSamples_advanced`
  and the view's attribute stage first, and keep the EXACT two-stage rule and the attribute crossing identical.
- [ ] **Step 4: Run the tests.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(graph_search): build scoped, paged Cypher for graph_search`.

---

### Task E4: catalog cache and hydration

**Files:** create `nextseek_api/graph_search/catalog_cache.py`, `nextseek_api/graph_search/hydrate.py`,
`nextseek_api/tests/test_graph_search_hydrate.py`.

**Interfaces:**
- Consumes: `Catalog` (E3).
- Produces:
  - `catalog_cache.get_catalog(driver, db) -> Catalog`: one read of
    `MATCH (t:SampleType) OPTIONAL MATCH (t)-[:HAS_ATTRIBUTE]->(a:Attribute) RETURN t.id, t.title, t.label,
    collect([a.title, a.value_type])`, cached per process and re-read when `MATCH (g:GraphMeta) RETURN g.catalog_hash`
    changes; the hash is re-checked at most every 60 s.
  - `hydrate.hydrate(ids: list[int]) -> list[dict]`: rows in the given order, from connection `seek`:

```sql
SELECT A.id, A.title, A.sample_type_id, B.title AS sample_type, A.uuid, A.contributor_id,
       C.first_name, A.created_at, A.json_metadata
  FROM samples A
  LEFT JOIN sample_types B ON A.sample_type_id = B.id
  LEFT JOIN people C ON A.contributor_id = C.id
 WHERE A.id IN (%s, ...)
```

    then `SELECT D.asset_id, E.title FROM assay_assets D JOIN assays E ON E.id = D.assay_id WHERE D.asset_type =
    'Sample' AND D.asset_id IN (%s, ...) ORDER BY D.asset_id, E.title`, joined into a comma-separated `assays` string
    (no `GROUP_CONCAT`, so no 1,024-byte truncation). `json_metadata` is parsed to a dict; `attributeValue` is `""`;
    `created_at` is rendered as advanced_search renders it (check one advanced_search row).

- [ ] **Step 1: Write the failing tests**: order is preserved for ids `[9, 2, 5]`; an id missing from MySQL is
  dropped and logged; `assays` joins several titles; an empty id list issues no SQL; the cache re-reads only when the
  hash changes (fake driver counting calls, patched clock).
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(graph_search): cache the catalog and hydrate a page from MySQL`.

---

### Task E5: the service, the ViewSet and its wiring

**Files:** create `nextseek_api/graph_search/service.py`, `nextseek_api/services/graph_search.py`,
`nextseek_api/tests/test_services_graph_search.py`; modify `nextseek_api/endpoint_descriptions.py` (add
`GRAPH_SEARCH_DESC`), `nextseek_api/views.py` (one import), `nextseek_api/urls.py` (register
`samples/graph_search` directly above `samples/advanced_search`), `ci/routes.py` (one Route beside advanced_search's:
`pattern=r"^nextseek_api/^^samples/graph_search/$"`, `path="/nextseek_api/samples/graph_search/"`,
`methods=("POST",)`, `profiles="local,dev"`, `auth="smoke"`, `expect=200`, note "a search expressed as a POST, so the
prod guard refuses it"), `ci/smoke/test_registry_contents.py` (`OWNED_ROUTE_COUNT` 168 to 169).

**Interfaces:**
- Consumes: E1 to E4.
- Produces:
  - `service.search(req: GraphSearchRequest, scope: Scope, page: int, page_size: int, *, driver, db) -> dict` with
    `total`, `ids`, `sample_types`, `timings` (`cypher_ms`, `count_ms`);
  - `service.all_ids(req, scope, *, driver, db) -> list[int]` (parity only);
  - `GraphSearchViewSet.create`: `authentication_classes = [CsrfExemptSessionAuthentication, BasicAuthentication]`,
    `permission_classes = [IsAuthenticated]`; validates with `GraphSearchRequest` (422 on error, advanced_search's
    envelope); applies advanced_search's "nothing to search on" 422 unless `extensions.where` is present; resolves
    scope (403 on `ScopeUnavailable`); returns `total: 0` with no Cypher when a non-admin has no projects; runs both
    queries with `session.execute_read` and a 60 s timeout; hydrates the page; validates the envelope with
    `SampleAdvancedSearchResult`; appends `{"debug": {...}}` to `footer` when `?debug_meta=1`; an out-of-range page
    returns an empty `rows`.

- [ ] **Step 1: Write the failing tests**, mirroring `nextseek_api/tests/test_advanced_search_needs_a_filter.py`
  (APIRequestFactory, a MagicMock user): unauthenticated is 401; an unknown body key is 422; nothing to search on is
  422; `ScopeUnavailable` is 403; a non-admin with no projects gets `total` 0 and the driver is never called; a
  happy path with a fake driver and a patched `hydrate` returns the envelope and validates; `debug_meta=1` adds the
  timings; `GraphSearchInvalid` is 422 with its message; the path appears in `SchemaGenerator().get_schema()` and
  `GraphSearchRequest` lands in `components.schemas`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** the service and the ViewSet, with `@extend_schema(operation_id="Graph Search Samples",
  description=GRAPH_SEARCH_DESC, request=GraphSearchRequest, responses={200: SampleAdvancedSearchResult},
  tags=["Samples"], examples=[...])` and at least three `OpenApiExample`s (the advanced_search type-plus-term example,
  a paired `where`, a lineage condition). `GRAPH_SEARCH_DESC` follows the required section order (SUMMARY, USE WHEN,
  DO NOT USE WHEN, ACCEPTS, RETURNS, TRIGGER PHRASES, EXAMPLES).
- [ ] **Step 4: Run the checks.** The new tests; the conventions pair
  (`nextseek_api/tests/test_viewset_conventions.py nextseek_api/tests/test_viewset_conventions_schema.py`);
  `python3 scripts/validate_viewset_conventions.py`; the route gate (`ci/gate`) in the Django unit lane; the smoke
  registry unit tests (`CI_BOX_PROFILE=local uv run --no-project --with pytest --with requests pytest
  ci/smoke/test_registry_unit.py ci/smoke/test_registry_contents.py -q`). Expected: all pass.
- [ ] **Step 5: Commit** `feat(graph_search): add POST /nextseek_api/samples/graph_search/`.

---

### Task E6: parity (gate E)

**Files:** create `scripts/graph_search/queries.json`, `scripts/graph_search/parity.py`.

**Interfaces:**
- Consumes: G6's graph in the lane; M1's merged MySQL; E5's `service.all_ids`.
- Produces: `$GS_WORK/runs/E6/parity.json` and `parity.md`.

- [ ] **Step 1: Write `queries.json`**: an array of `{"name", "body", "kind", "note"}` covering the spec's benchmark
  shapes (section 9), picked on the merged data: narrow equality (a TCGA type attribute and a production one); broad
  type-only (one type near 100k samples; A.VCF); crossed attributes; keyword across all types (for example
  "granuloma"); Nessie's OR-retry shape (three terms, OR); multi-type plus text; 50 UIDs (25 TCGA, 25 production);
  graph-only shapes (a paired `where`, a numeric range, a lineage condition). `kind` is `compat` or `graph_only`.
- [ ] **Step 2: Write `parity.py`**, run with `lane.sh python scripts/graph_search/parity.py`. For each compat query
  and each scope (superuser; each distinct project set held by any person; `tcgamember`; `user`):
  - G side: `service.all_ids`.
  - A side: for shapes whose SQL match count (an id-only `SELECT A.id` with advanced_search's WHERE, built by
    `seek/sample/queries.py` and captured) is at most 50,000, call `SampleAdvancedSearchViewSet().create` with
    `SeekDB.getCurrentUser` patched to return the scope's projects, `page_size=1000`, paging until done; above
    50,000, the id-only SQL form is the A side (type-only shapes have no Python stage).
  - Record totals, the symmetric difference, and classify each difference against the spec's declared list.
  Graph-only queries record totals and timings only. The process runs under the lane's 4 GB cap; report a shape the
  cap kills instead of retrying it.
- [ ] **Step 3: Run it.** Expected: 0 undeclared differences. An undeclared difference is a bug in E3, fixed there
  and rerun, or a new declared difference the operator approves in the spec.
- [ ] **Step 4: Read-only check.** In the same script, `session.execute_read(lambda tx: tx.run("CREATE (:Probe)"))`
  must raise; record the result.
- [ ] **Step 5: Commit** `feat(scripts): add the graph_search parity harness and query set`.

---

### Task L1: load into the live local stack (OPERATOR, gate L)

Run by the operator after gates M, G and E pass. The agent prepares nothing on the live stack.

1. Turn local Nessie off for the window (for example, do not open the chat panel and stop any harness; the
   operator's call).
2. `free -g` and `df -h /`.
3. Take a fresh backup if the stage 1 backup is older than the live data.
4. Load the merged MySQL: stream `$GS_WORK/seeds/merged-2026-09-14/seek_production.core.sql.gz` and `dmac.sql.gz`
   into the live `seek-mysql`, keeping the live `settings` row.
5. Synthesize TCGA `sample_auth_lookup` rows by SQL (dense: every TCGA sample times every user with a person plus
   anonymous, `can_view` by the policy rule), so SEEK's own pages stay consistent.
6. Load the graph: stop the live `neo4j`, `neo4j-admin database load neo4j --from-path=... --overwrite-destination`
   from `$GS_WORK/seeds/v11-graph-2026-09-14/`, start it.
7. Recreate `nextseek` so the compose memory cap applies, and deploy the branch: `./startup.sh rebuild` from the
   worktree (or merge the branch into the checkout the stack runs from).
8. Gate L: `docker compose exec nextseek python manage.py graph_sync --verify --json </dev/null` passes (the live
   host needs `--i-mean-the-live-graph` only for writes, not for `--verify`); both endpoints answer for `demo`,
   `tcgamember` and `user`; `parity.py` rerun against the live stack (read-only) shows 0 undeclared differences.

Rollback: restore the stage 1 backup per its MANIFEST, then rebuild from `origin/dev`.

---

### Task B1: the benchmark harness

**Files:** create `scripts/graph_search/bench.py`, `scripts/graph_search/bench_report.py`.

**Interfaces:**
- Consumes: `queries.json` (E6); account passwords from environment variables `GS_DEMO_PASSWORD`,
  `GS_TCGAMEMBER_PASSWORD`, `GS_USER_PASSWORD` (never in files); the stack URL (`GS_BASE_URL`, default
  `http://127.0.0.1:8000`).
- Produces: `$GS_WORK/runs/B2/results.json` and `report.md`.

- [ ] **Step 1: Write `bench.py`** (standard library plus `requests`, run with `uv run --no-project --with
  requests`): for each query, arm (A: advanced_search, G: graph_search) and account, warm (1 warm-up plus 5 timed
  runs), recording status, `total`, wall time, response bytes and G's `debug_meta` timings; a concurrency mode with 4
  clients; a cold mode that runs only after the operator restarts the database containers (the harness prompts and
  waits). Every non-superuser run is paired with the same query as `demo`.
- [ ] **Step 2: The S arm.** On the throwaway merged MySQL only: add `idx_samples_sample_type_id` on
  `samples(sample_type_id)`, then run each compat query's captured advanced_search SQL with `ORDER BY A.id LIMIT 100`
  (and the same for a count), timing each (warm and first run).
- [ ] **Step 3: Memory.** For A and G on the two broadest shapes, run the view in the lane (4 GB cap) with
  `resource.getrusage` peak RSS before and after, against the merged data; never against the live worker.
- [ ] **Step 4: Dry run** against the lane for G and S only (no live stack): the harness completes and writes
  results.
- [ ] **Step 5: `bench_report.py`** renders `report.md`: one table per shape with p50, p95 and range per arm and
  account, the id-set difference against A, peak RSS growth, and the configuration (MySQL buffer pool, Neo4j page
  cache) of each run.
- [ ] **Step 6: Commit** `feat(scripts): add the graph_search benchmark harness`.

### Task B2: the benchmark run (gate B)

After gate L, with the operator's approval to run against the live local stack: run `bench.py` for the as-deployed
configuration, then again with the equal-budget configuration the operator sets, then `bench_report.py`. The operator
reviews `report.md` against the spec's pass criteria. Afterwards the operator restores the stage 1 backup and turns
Nessie back on, or keeps the merged data.

---

## Self-review

- Spec section 3 decisions: 1 (E2, E3 scope), 2 (E2), 3 (G4 `WRITE_SAMPLES`), 4 (G1, G4), 5 (G2, G4, G5), 6 (E3),
  7 (L1), 8 (M1), 9 (T0 lane, L1), 10 (B1), 11 (G2, G3), 12 (this plan's location).
- Technical defaults: keys (G4 constraints), undeclared keys (G5 census), UID skip (G1), casts (G1), index budget
  (G4), ghosts and orphans (G4, G5), CHILD_OF archive (G4), curated lists as strings (G2), label rule (G1, G2).
- Gates: M (M1), S (the spec and `docs/neo4j-schema.md`, approved before T0), G (G5, G6), E (E5, E6), L (L1), B (B2).
- Names used across tasks: `project_sample`, `SampleProjection`, `label_for`, `cast_value`, `Catalog`, `Scope`,
  `ScopeUnavailable`, `GraphSearchInvalid`, `BuiltQuery`, `build`, `get_catalog`, `hydrate`, `search`, `all_ids`,
  `full_sync`, `gate_g`: each defined once, in the task that owns its file.
