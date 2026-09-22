# graph_search follow-up 2: implementation plan (wire every current writer to graph_sync, and CI to check it)

> **For agentic workers:** this plan is executed as four Workflow runs, one after another, with an operator review
> point between runs. Each task is one agent with its own tests; a reviewer may reject one task and approve its
> neighbour. Use superpowers:test-driven-development inside a task: write the failing tests first, watch them fail,
> then implement.

**Goal:** NExtSEEK keeps graph 2.0 up to date by itself, using the current functions: every current writer calls
graph_sync after it writes, batch upload writes the graph through graph_sync, a nightly targeted sync and a weekly
full sync catch what no NExtSEEK function sees, and CI checks the sync and the features the POC already built.

**Spec:** `docs/superpowers/specs/2026-09-15-graph-search-sync-design.md` ("spec N") and its companion
`docs/superpowers/specs/2026-09-15-graph-search-sync-inventory.md` (writer ids `WR-01` to `WR-28`, "inventory N").
Every decision is in spec 18; there is no open question. Read the spec, the inventory,
`nextseek_api/graph_sync/README.md` and `nextseek_api/CLAUDE.md` before any task.

**Tech stack:** Django 5 and DRF, pydantic v2, drf-spectacular, the `neo4j` driver (Community 2026.07.1, `CYPHER 25`),
MySQL 8.0 through Django's `seek` and `default` connections and batch upload's SQLAlchemy engine, bash, the `startup`
Typer project, pytest.

## Global constraints

- Work in the worktree of branch `feat/graph-search-sync`. Its base and merge target is `dev-graph` (formerly
  `feat/graph-search`). Push only that branch, at the end of each run, after the run's gate and a scan of the diff
  for emails, home paths and tokens. The branch was rebased onto `origin/dev-graph` on 2026-09-15 and published the
  same day: one force-push with lease, on the operator's approval, set `origin/feat/graph-search-sync` to `b154ae4b`.
  From now on it moves only by merges and normal fast-forward pushes; never force-push.
- **At the start of every run, merge `origin/dev-graph` into this branch** (`git fetch origin`, then `git merge
  origin/dev-graph`; never rebase the published branch) and do the run-start checks below. `dev-graph` keeps moving:
  a parallel chat still works on it. Resolve a conflict in `ci/routes.py` or `ci/smoke/test_registry_contents.py` by
  recounting, never by taking one side: keep both sides' `Route` entries, set `OWNED_ROUTE_COUNT` to `dev-graph`'s
  count plus the routes this branch has added (T15's one, from Run 3 on), keep both history lines in its comment,
  and from Run 4's T18b on give every `Route` the merge brings an `effect` and `writers` in the merge commit.
- **No task runs a live graph sync or relabel** (`graph_sync --full`, `--catalog`, `--reconcile`, `--samples`, the
  loop, or any relabel script against a live Neo4j). Those are the operator's steps after the runs, each announced to
  the operator for the Nessie chat before it runs: that chat freezes the live local graph from its truth sign-off to
  the end of its paid runs (spec 17 step 5, R16).
- Never touch the live `nextseek` compose containers (`nextseek`, `seek`, `seek-mysql`, `neo4j`): no rebuild, no
  restart, no `./startup.sh`, no `docker exec` into them, no SSH.
- Every test runs in a throwaway container (the Django lane below), in the startup project's own lane, or in the
  graph-search lane (`scripts/graph_search/lane.sh`), one container at a time (the `flock` serializes them). The host
  is memory-starved: run commands in the foreground with timeouts. While `$GS_WORK/.gs-bench-running` exists, start
  no container at all; write code meanwhile.
- Never edit anything under `NessieAI/` (follow-up 1 owns it).
- Keep the existing interfaces: `run.full_sync`, `run.catalog_sync`, `verify.gate_g` and every existing flag of
  `manage.py graph_sync` keep their signatures and meaning. New keyword arguments are optional.
- **No label is written to any live graph before task V1 passes and the operator has seen its numbers.**
- Public repository: no credentials, emails, personal names or home paths in any tracked file; category codes, never
  free text, for CI exclusions. No em-dashes anywhere.
- Stage files by name, never `git add -A`. Conventional commits with module scopes, each ending with exactly the
  trailer lines the executing session gives.
- A new `router.register` goes into `ci/routes.py` and moves `OWNED_ROUTE_COUNT` in the same commit.
- Within a run, no two tasks share a file (the ownership table). A run's stages run in order; a later run starts from
  the earlier runs' commits.

## Commands used throughout

```bash
# Django lane: throwaway container, read-only mount, SQLite test settings (no MySQL, no Neo4j)
mkdir -p schema_rag/duckdb schema_rag/embedding_models
flock "$GS_WORK/.gs-docker.lock" docker run --rm -i --network none --memory 2g -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest <paths> -q -p no:cacheprovider

# The blocking gates, same lane
... /app/.venv/bin/python -m pytest ci/gate -q -p no:cacheprovider

# Smoke registry unit tests, on the host, no Django
CI_BOX_PROFILE=local uv run --no-project --with pytest --with requests pytest \
  ci/smoke/test_registry_unit.py ci/smoke/test_registry_contents.py -q

# startup lane (from startup/)
cd startup && uv run --project . --group test python -m pytest tests/<file> -q \
  -p no:nextseek_api.attributes.tests.attribute_fixtures --ignore=tests/test_schema_fixups.py

# The graph-search lane (the merged MySQL in the scratch container, a throwaway Neo4j): see scripts/graph_search/README.md
scripts/graph_search/lane.sh python <script> [args]

# ViewSet conventions and docs map, on the host
python3 scripts/validate_viewset_conventions.py
python3 ci/docs_map.py
```

`$GS_WORK` is the operator's graph-search work directory, outside the repository.

## Run-start checks (every run, before its first task)

1. `git fetch origin`; `git log --oneline HEAD..origin/dev-graph` and `git diff --stat HEAD...origin/dev-graph` show
   what `dev-graph` changed since the last merge. Merge it as the global constraints say, and write the merged range
   into the run's report.
2. Migrations: `git ls-tree --name-only HEAD nextseek_api/migrations/` on the merged tree. The single leaf is
   `0020_assayregistrationjob` before T1 lands and `0021_graph_sync_outbox_and_run` after; a second 0021 from
   `dev-graph` is renumbered or merged per `nextseek_api/CLAUDE.md` in the merge commit.
3. Routes: `git show origin/dev-graph:ci/smoke/test_registry_contents.py | grep '^OWNED_ROUTE_COUNT'` against the
   merged tree's count. Classify each new `dev-graph` route against inventory 3 (reads, writes other tables, writes a
   graph source); from T18b on, declare it with `effect` and `writers`. A route that writes a watched table is a new
   writer: report it before the run's tasks start.
4. Writers: when the merge touched a Python module outside tests, rerun the writer scan (the inventory's scan until
   T18a lands, `ci/gate/writer_scan.py` after) and compare its sites with inventory 2 (137 on `dev-graph` at
   `367b9217`).
5. Ownership: when the merge changed a file a task of this run owns, that task starts from the merged file and says
   so in its report; the ownership table stays disjoint within the run.
6. Tests: a new `dev-graph` module named like T8's globs (`test_graph_search_*`, `test_graph_sync_*`,
   `test_services_graph_*` in their directories) blocks with no edit; a graph-feature test named otherwise needs a
   glob.

## The four runs

```
       every run first merges origin/dev-graph (run-start checks)
Run 1  CI for what is built, the tables, request handling      T1  T8  T9  S1
          (review point 1)
Run 2  the engine                                              A: T2 T4 T5 T6 T7   B: V1 (HARD STOP)   C: T10 T11 T12
          (review point 2: the operator sees V1's numbers)
Run 3  batch upload, every hook, the status endpoint           H1a H1b H2 H3 H4 H5 H6 H7 T15
          (review point 3)
Run 4  the nightly sync, the loop, the entrypoint,             A: T3 T13 T16 T17 T18a   B: T14 T18b T20
       drift after rebuild, the registry
          (final review; then the merge back into dev-graph, spec 19.1)
```

Placement notes: T3 (the pure schedule) moved from Run 2 to Run 4, where its only consumer (T14) is, to keep Run 2
under ten tasks. T15 (the status endpoint, which needs only T2's state functions) runs in Run 3 and T13 (the nightly
sync) in Run 4, because T15 and T18b both edit `ci/routes.py` and `ci/smoke/test_registry_contents.py` and may not
share a run.

**What `dev-graph`'s Graph Search page (`6177f57e` to `367b9217`) changes, run by run.** Run 1: T8's globs add
`seek/tests/test_graph_search_*.py` and its blocking step requires `node`; T9 adds one browser flow for the page (the
page's GET is already in T0); S1 starts from `dev-graph`'s `seek/views/search.py`, `seek/views/__init__.py` and
`seek/urls.py`. Run 2: no change (the page reads what schema 1.2 keeps, and graph_search reads no `schema_version`).
Run 3: T15 moves `OWNED_ROUTE_COUNT` from 170 to 171, not 169 to 170; H3 and H5 own `seek/views/samples.py` and
`seek/views/admin.py`, which `dev-graph` did not touch. Run 4: T18b declares the page `effect="reads"` with no
writers; T18a's scan finds no new site; no other change.

## File ownership

| Run | Files | Task |
|---|---|---|
| 1 | `nextseek_api/graph_sync/models_db.py`, `nextseek_api/migrations/0021_graph_sync_outbox_and_run.py`, `nextseek_api/models.py` (the re-export line), `nextseek_api/tests/test_graph_sync_models.py` | T1 |
| 1 | `ci/blocking_lanes.py`, `ci/gate/test_blocking_lanes.py`, `.github/workflows/ci-pytest.yml`, `ci/README.md`, `ci/CLAUDE.md` | T8 |
| 1 | `ci/smoke/test_graph_search.py`, `ci/smoke/test_flows.py` (one flow) | T9 |
| 1 | the legacy sample view modules under `seek/views/` and `nextseek_api/batch_upload/views.py` that the operator's private findings name; `nextseek_api/tests/test_request_handling_seek.py`, `nextseek_api/tests/test_request_handling_batch_upload.py` | S1 |
| 2 | `nextseek_api/graph_sync/state.py`, `hooks.py`, `nextseek_api/tests/test_graph_sync_state.py`, `test_graph_sync_hooks.py` | T2 |
| 2 | `nextseek_api/graph_sync/projection.py`, `nextseek_api/tests/test_graph_sync_projection.py` | T4 |
| 2 | `nextseek_api/graph_sync/sources.py`, `nextseek_api/tests/test_graph_sync_sources.py` | T5 |
| 2 | `nextseek_api/graph_sync/writer.py`, `cypher.py`, `docs/neo4j-schema.md`, `nextseek_api/tests/test_graph_sync_writer.py` | T6 |
| 2 | `nextseek_api/graph_sync/labels.py`, `nextseek_api/tests/test_graph_sync_labels.py` | T7 |
| 2 | `nextseek_api/graph_sync/label_check.py`, `scripts/graph_search/verify_labels.py`, `scripts/graph_search/README.md` (one row), `nextseek_api/tests/test_graph_sync_label_check.py` | V1 |
| 2 | `nextseek_api/graph_sync/targeted.py`, `nextseek_api/tests/test_graph_sync_targeted.py` | T10 |
| 2 | `nextseek_api/graph_sync/run.py`, `nextseek_api/tests/test_graph_sync_full.py` | T11 |
| 2 | `nextseek_api/graph_sync/verify.py`, `drift.py`, `nextseek_api/tests/test_graph_sync_drift.py`, `test_graph_sync_verify.py` | T12 |
| 3 | `nextseek_api/batch_upload/orchestrator.py`, `insert.py`, `parallel.py`, `neo4j_sync.py`, `report.py`, `config.py`, the batch-upload tests that exercise them (`test_neo4j_sync.py`, `test_neo4j_integration.py`, `test_orchestrator_*.py`, `test_insert*.py`, `test_parallel.py`, `test_report*.py`), `nextseek_api/tests/test_graph_sync_hook_batch_upload.py` | H1a |
| 3 | `nextseek_api/batch_upload/orphan_resolution.py`, `tasks.py`, `scripts/backfill_parent_titles.py`, `backfill_parent_title_hashes.py`, `backfill_shared_assays.py` (deleted), `tests/test_orphan_resolution.py`, `tests/test_backfill_parent_title_hashes.py`, `nextseek_api/tests/test_graph_sync_hook_orphans.py` | H1b |
| 3 | `nextseek_api/attributes/executor.py`, `nextseek_api/tests/test_graph_sync_hook_attributes.py` | H2 |
| 3 | `seek/sample/upload.py`, `seek/sample/table.py`, `seek/views/samples.py`, the seek tests that exercise the removed Neo4j functions, `nextseek_api/tests/test_graph_sync_hook_legacy.py` | H3 |
| 3 | `nextseek_api/services/samples.py`, `sample_types.py`, `assays.py`, `studies.py`, `investigations.py`, `projects.py`, `sops.py`, `users.py`, `nextseek_api/tests/test_graph_sync_hook_proxies.py` | H4 |
| 3 | `seek/views/admin.py`, `nextseek_api/tests/test_graph_sync_hook_admin.py` | H5 |
| 3 | `nextseek_api/assay_registration/service.py`, `runner.py`, `graph.py`, `nextseek_api/assay_registration/tests/*`, `nextseek_api/tests/test_graph_sync_hook_assay_registration.py` | H6 |
| 3 | `nextseek_api/management/commands/backfill_publication_attributes.py`, `nextseek_api/tests/test_graph_sync_hook_backfill.py` | H7 |
| 3 | `nextseek_api/services/graph_sync_status.py`, `nextseek_api/models.py` (pydantic models appended), `nextseek_api/endpoint_descriptions.py`, `nextseek_api/views.py` (one import), `nextseek_api/urls.py`, `ci/routes.py` (one Route), `ci/smoke/test_registry_contents.py` (the count), `ci/smoke/test_graph_sync_status.py`, `nextseek_api/tests/test_services_graph_sync_status.py` | T15 |
| 4 | `nextseek_api/graph_sync/schedule.py`, `nextseek_api/tests/test_graph_sync_schedule.py` | T3 |
| 4 | `nextseek_api/graph_sync/reconcile.py`, `nextseek_api/tests/test_graph_sync_reconcile.py` | T13 |
| 4 | `docker/scripts/entrypoint.sh`, `nextseek_api/tests/test_graph_sync_entrypoint.py` | T16 |
| 4 | `startup/steps/validate.py`, `startup/cli.py`, `startup/ci/runner.py`, `startup/tests/test_validate_graph_drift.py` | T17 |
| 4 | `ci/writers.py`, `ci/gate/writer_scan.py`, `ci/gate/test_writer_registry.py`, `ci/gate/test_writer_scan_unit.py` | T18a |
| 4 | `nextseek_api/graph_sync/loop.py`, `nextseek_api/management/commands/graph_sync.py`, `nextseek_api/tests/test_graph_sync_loop.py`, `test_graph_sync_command.py` | T14 |
| 4 | `ci/routes.py` (the `effect` and `writers` fields), `ci/gate/live_routes.py`, `ci/gate/test_route_registry.py`, `ci/gate/test_route_effects.py`, `ci/smoke/test_registry_contents.py`, `ci/README.md`, `ci/CLAUDE.md` | T18b |
| 4 | `nextseek_api/graph_sync/README.md`, `nextseek_api/batch_upload/README.md`, `nextseek_api/batch_upload/CLAUDE.md`, `nextseek_api/README.md` (rows only) | T20 |

Test files for the sync are named `nextseek_api/tests/test_graph_sync_*.py` or `test_services_graph_*.py`, so T8's
blocking globs pick them up without another edit. The Graph Search page's tests on `dev-graph` are
`seek/tests/test_graph_search_*.py`, which T8's globs also cover, so a page test `dev-graph` adds under that name
blocks from the next merge on.

## Shared vocabulary (every task codes against these names)

- Outbox kinds and keys: spec 12's table. Writer ids: inventory 2.
- `hooks.enqueue(kind, key, payload=None)` never raises; `state.enqueue(...)` raises.
- `state.graph_write_lock(timeout_s) -> contextmanager[bool]`: MySQL `GET_LOCK('nextseek_graph_write', timeout)` on
  the `default` connection, `RELEASE_LOCK` on exit; on SQLite it yields True and does nothing.
- `projection.source_hash(row, type_title, value_types, project_ids, assay_ids) -> str` (sha256 hex).
- `writer.SCHEMA_VERSION == "1.2"`; `writer.graphmeta(driver, db) -> dict`.
- `targeted.sync_samples(driver, db, ids, *, run_dir=None) -> dict` with `status` in `ok`, `not_at_version`,
  `lock_timeout`; `targeted.retire_samples`, `sync_samples_of_type`, `relabel_for_maps`, `sync_small_tables` (spec 7.1).
- `labels.edge_labels(child_assays, parent_assays, assay_map, protocol) -> dict` with the label keys of spec 5 E8, E9:
  always all five assay keys and the protocol pair.
- `labels.classify(stored, computed) -> str`, one of `new` (no singular assay field stored), `equal`,
  `plural_missing` (singular fields equal, a plural list absent), `changed`, `cleared` (spec 7.3, R14).
- Every label-writing entry point (`targeted.*`, `run.full_sync`, `reconcile.reconcile`, `writer.write_edge_labels`)
  takes `apply_label_changes=False`: without it only `new` edges are written.

---

## Run 1: CI for what is built, the tables, request handling

### Task T1: the two dmac tables (spec 12; C-01)

Starts from the three uncommitted files the first agent left: the re-export line in `nextseek_api/models.py`,
`nextseek_api/graph_sync/models_db.py` and `nextseek_api/migrations/0021_graph_sync_outbox_and_run.py`.

**Interfaces:** Produces `GraphSyncOutbox` (`kind`, `key`, `payload` JSON null, `enqueued_at`, `claimed_by`,
`lease_expires_at`, `attempts`, `last_error`, `done_at`; unique `(kind, key)`; index `(done_at, enqueued_at)`) and
`GraphSyncRun` (`kind`, `started_at`, `finished_at`, `status`, `watermark_from`, `watermark_to`, `counts_json`,
`drift_json`; index `(kind, status, finished_at)`), tables `graph_sync_outbox` and `graph_sync_run`.

**Tests first:** the table names; a second `(kind, key)` insert raises `IntegrityError`; `payload` round-trips a list
of ints; `makemigrations nextseek_api --check --dry-run` reports no change; `0021_graph_sync_outbox_and_run` is the
single leaf of the app's migration graph.

**Implementation:** review the draft against spec 12; add `payload` to the model and the migration; confirm the heads
(`0020_assayregistrationjob` was the only leaf on `origin/dev`, `origin/dev-graph` and `feat/graph-search-nessie` on
2026-09-15, still so after the rebase: `dev-graph`'s newer commits add no migration; re-check with `git ls-tree`
after the run-start merge, and renumber or add a merge migration per
`nextseek_api/CLAUDE.md` if a branch has since added one).

**Commit:** `feat(graph_sync): add the outbox and run tables`

### Task T8: make the graph tests block CI (spec CI-2)

**Files:** `ci/blocking_lanes.py` (stdlib: `BLOCKING_GLOBS = ("nextseek_api/tests/test_graph_sync_*.py",
"nextseek_api/tests/test_graph_search_*.py", "nextseek_api/tests/test_services_graph_*.py",
"seek/tests/test_graph_search_*.py")`, the last being the Graph Search page's view and JavaScript tests from
`dev-graph`, and `main()` printing the expanded paths), `ci/gate/test_blocking_lanes.py` (every glob matches at least
one file; stdlib imports only), `.github/workflows/ci-pytest.yml` (a step after the gate step with the same `env` and
`if:`: first `command -v node`, failing the step with an `::error::` line when node is missing, then `uv run pytest
$(python ci/blocking_lanes.py) -q`, then `uv run python manage.py makemigrations --check --dry-run`), `ci/README.md`
and `ci/CLAUDE.md` ("What can fail a job"; the comment above the gate step).

Why the node line: `seek/tests/test_graph_search_js.py` runs `seek/tests/js/graph_search_cases.js` (node built-ins
only, no npm package) and carries a module-level `skipif(node is None)`, so where node is missing all 15 of its tests
skip and the step would pass without them. GitHub's `ubuntu-latest` image ships node, so on the runner they run; the
line turns a runner without node into a failure instead of a silent skip.

**Acceptance:** `pytest ci/gate` in the Django lane; the expanded file list passes there, where the JavaScript
module's 15 tests skip (the stack image has no node: say so in the task report, and run the module once where node
exists); `makemigrations --check --dry-run` exits 0 under the test settings (if it cannot run there, say why in the
task report and leave that line out). **Commit:** `feat(ci): block on the graph unit tests and on missing migrations`

### Task T9: send graph_search a request (spec CI-3)

**Files:** `ci/smoke/test_graph_search.py`: a POST of `{"sampletype": [<a type from GET
/nextseek_api/sample_types/>], "page": 1, "page_size": 5}` as the smoke client, `profiles("local", "dev")`,
asserting 200, the keys `total`, `rows`, `footer`, `sampleTypes`, and at most 5 rows. `ci/smoke/test_flows.py`: one
flow, `@pytest.mark.profiles("local", "dev")` like the page's `Route`: open `/seek/graph/search/?tab=advanced`, wait
for the Advanced grid's EasyUI data (`#gs_advanced_dgtable`) as Flow A does, search for `SMOKE_SEARCH_TERM`, and
assert that the timing line appears and the grid's request went to `/nextseek_api/samples/graph_search/`; with
`--strict-console` a script error fails it (the class of defect `904f6f0f` fixed on `dev-graph`). It reads only. The
page's GET needs nothing new: T0 already sweeps it (`_callable_routes` keeps it: GET, `auth="web"`, `local,dev`),
which checks the status and that it is not bounced to the login page, and sends no POST. **Acceptance:** the smoke
registry unit tests on the host; both files collect in the no-stack lane; the flow runs with the smoke suite on the
operator's next rebuild (unverified until then). **Commit:** `test(ci): smoke-test graph_search and the Graph Search page`

### Task S1: tighten request handling on legacy sample views and batch-upload admin overrides

The executing session supplies the details from the operator's private findings file at run time; nothing more
belongs in this plan. **Tests first**, one per finding, in the two new test files; then the fix in the owning view.
Commit messages stay neutral, for example `fix(seek): tighten legacy sample view request handling` and
`fix(batch_upload): admin overrides key on is_superuser`. S1 starts from the merged tree: `dev-graph` added
`graphSearch` to `seek/views/search.py`, its import to `seek/views/__init__.py` and its URL to `seek/urls.py`. If S1
edits one of those files, it keeps `seek/tests/test_graph_search_page.py` passing and runs it with its own tests.

**Gate R1:** T1, T8, T9, S1 committed; the Django lane over `$(python3 ci/blocking_lanes.py)` and the new tests;
`pytest ci/gate`; the smoke registry unit tests; docs map clean; push. **Review point 1.**

---

## Run 2: the engine

Stage 2A: T2, T4, T5, T6, T7 in parallel. Stage 2B: V1, a hard stop. Stage 2C: T10, T11, T12 in parallel.

### Task T2: outbox, runs, the lock, hooks (spec 7.4, 12; C-02)

**Interfaces:** Consumes T1's models. Produces `state.enqueue(kind, key, payload=None)`, `ensure_slot(kind, key) ->
bool`, `claim_next(worker_id, *, now=None, kinds=None) -> Claim | None`, `finish_done(claim) -> bool`,
`finish_failed(claim, error, backoff_s)`, `mark_done_before(ts, *, kinds=None) -> int`, `start_run(kind, *,
trigger) -> RunHandle` (`.finish(status, counts=None, drift=None, watermark_from=None, watermark_to=None)`;
best-effort: a missing table logs a warning and returns a no-op handle), `last_runs()`, `freshness(*, now=None,
thresholds=DEFAULT_THRESHOLDS)`, `outbox_summary(*, now=None)`, `reap_abandoned(*, now=None)`,
`graph_write_lock(timeout_s)`; `hooks.enqueue(...)` wraps `state.enqueue`, logs and counts a failure, never raises.

**Tests first:** a slot inserts once; enqueue coalesces and resets a done row; a claim is exclusive and counts an
attempt; an expired lease is claimable again; a failure backs off; a row re-enqueued while claimed stays pending after
`finish_done`; a row at the attempt limit is not claimed; `mark_done_before` leaves later rows alone; `start_run`
tolerates a missing table; abandoned runs are marked; `freshness` reports `never` with no run and counts a full sync
for the reconcile; `hooks.enqueue` swallows a database error; the lock is a no-op on SQLite and issues
`GET_LOCK`/`RELEASE_LOCK` on a stub MySQL connection. **Commit:** `feat(graph_sync): add the outbox, run records, the graph-write lock and hooks`

### Task T4: the projection gains the source hash and the parent lists (spec 6, 10.3; C-03)

**Interfaces:** `source_hash(row, type_title, value_types, project_ids, assay_ids) -> str`: sha256 over `uuid`,
`title`, the type title, the sorted `(title, value_type)` pairs, the raw `json_metadata` UTF-8 bytes, the sorted
project ids and the sorted assay ids, with a `\x1f` separator and length prefixes; `parent_lists(tokens,
identity_by_uuid) -> tuple[list[str], list[str]]` (the rule of `neo4j_sync.py::enrich_parent_titles`, its hash
function imported, not copied); `project_sample(row, sample_type_title, value_types, project_ids, *, assay_ids=(),
parent_lists=None)` adds `source_hash` always and the parent lists when given; `SYSTEM_KEYS` gains `source_hash`.

**Tests first:** the hash is stable under reordering of project and assay ids and changes when any single input
changes (a trailing space in a title and a value type included); a metadata key named `source_hash` is refused; the
parent-list rule matches `enrich_parent_titles` on its own fixtures; every existing projection test passes unchanged.
**Commit:** `feat(graph_sync): project the source hash and the parent lists`

### Task T5: by-id readers and ordered streams (spec 7.2, 10.3; C-04)

**Interfaces:** `samples_by_ids(ids)`, `sample_projects_for(ids)`, `sample_assay_ids_for(ids)`,
`iter_digest_rows(chunk)` (keyset pages of samples, each row carrying `project_ids` and `assay_ids`, merged from
`projects_samples ORDER BY sample_id` and `assay_assets WHERE asset_type='Sample' ORDER BY asset_id`),
`uuid_to_ids_for(tokens)`, `parent_identities(uuids)`, `seek_study_links_for(ids)`, `ids_of_type(type_id, chunk)`,
`samples_naming(uuids, chunk)`, `resolved_assay_map()` (SEEK assay id to `(internal id or None, title)`, smallest
internal id on 1:N, the `assays` fallback, as batch upload's `_resolve_internal_assays`), `sops_map()`, `studies()`.
Chunk every `IN` list at 1,000; bound parameters only.

**Tests first:** each reader against the module's existing fake cursors; the merge attaches the right projects and
assays across page boundaries, including a sample with neither; 1:N keeps the smallest internal id; a missing dmac
table gives an empty map. **Commit:** `feat(graph_sync): add by-id readers and the digest stream`

### Task T6: writer, Cypher and schema 1.2 (spec 6, 9; C-05)

**Interfaces:** `SCHEMA_VERSION = "1.2"`; `_retry` defined in graph_sync (no import from `batch_upload`);
`retire_samples(driver, db, ids, archive_path) -> dict` (spec 9); `relabel_orphans` keeps its signature and delegates
to the same Cypher for id-less and never-synced nodes; `archive_and_drop_undeclared_for_children(driver, db,
child_ids, declared_pairs, archive_path) -> dict`; `edges_incident(driver, db, ids) -> list[dict]`;
`write_edge_labels(driver, db, rows, *, apply_label_changes=False) -> dict` (sets the five assay properties and the
protocol pair together on each edge, never a subset, and removes `assay_title`; by default only on an edge whose
three singular assay fields are all null, guarded by a `WHERE` in the Cypher, so a label written between the read and
the write is kept; with `apply_label_changes=True` only where the stored values still equal those read; returns the
counts written, skipped as already labelled and skipped as changed meanwhile);
`sample_hashes(driver, db)` ordered by id; `graphmeta(driver, db) -> dict`; `write_graphmeta(driver, db,
catalog_hash, label_maps_hash=None)`; `WRITE_SAMPLES` takes the parent lists from the props when present and preserves
the node's otherwise. `docs/neo4j-schema.md` gains a "v1.2" section (spec 6) in the same commit.

**Tests first:** with the existing fake driver: the retire statements for a synced and a never-synced node; the
archive is written before the delete; a retired node keeps no `T_` label; the label SET and the `REMOVE
e.assay_title`; the default SET leaves an edge with any singular assay field set; with `apply_label_changes` an edge
whose stored values changed since the read is left alone; the five assay properties are always set together; `write_graphmeta` without `label_maps_hash` keeps the old parameters; every existing writer test
passes. **Commit:** `feat(graph_sync): schema 1.2 writer: retire rule, edge labels, source hashes`

### Task T7: the DERIVED_FROM label rule (spec 7.3; C-06)

**Interfaces:** `resolve_protocol(protocol_value, sops_by_id, sop_ids_by_title)` through
`nextseek_api/batch_upload/helpers.py::parse_protocol_value` (imported); `edge_labels(child_assays, parent_assays,
assay_map, protocol) -> dict` (all five assay keys and the protocol pair, every time); `classify(stored, computed) ->
str` (spec 7.3: `new`, `equal`, `plural_missing`, `changed`, `cleared`); `label_maps_hash(assay_map, sops_map) ->
str`.

**Tests first:** for fixtures where the sheet's values equal MySQL's, `edge_labels` equals what
`neo4j_sync.py::build_derived_from_payloads_from_db` produces for the same edges (call it in the test: it still exists
in Run 2), covering the shared set, the smallest internal id winning, the SEEK-id fallback, the empty shared set
clearing to nulls and empty lists, the three protocol formats and an ambiguous title resolving to null; the plural
lists always `[internal id]`, `[title]` (or empty) beside the singular fields; each `classify` class, a missing plural
list on matching singular fields being `plural_missing`, never `changed`.
**Commit:** `feat(graph_sync): batch upload's DERIVED_FROM label rule, fed from MySQL`

### Task V1: verify the labels against the dev box and the local graph (spec 1.1, 7.3, 17) **HARD STOP**

**Files:** `nextseek_api/graph_sync/label_check.py` (pure: canonicalise a label map, compare two, count per property,
map ids through a remap), `scripts/graph_search/verify_labels.py` (the read-only driver), a row in
`scripts/graph_search/README.md`, `nextseek_api/tests/test_graph_sync_label_check.py`.

**What it does:** in the graph-search lane, against the merged MySQL, compute the labels of every declared
DERIVED_FROM pair with `labels.edge_labels` and `sources` (T5, T7), then compare them:
- (a) TCGA, on the **singular fields** (`assay_id`, `internal_assay_id`, `internal_assay_title`), with the TCGA edge
  labels in the dev-box graph dump (`$GS_WORK/seeds/devbox-2026-09-14/neo4j.cypher.gz`), streamed with the parser
  the lane already uses, for edges between sample ids 389,935 and 1,308,453. The merge renumbered SEEK and internal
  assay ids. `dmac.gs_remap` is not in the live `dmac`: it exists only in `$GS_WORK/seeds/merged-2026-09-14/dmac.sql.gz`
  and the lane's scratch MySQL (kind `assay` 529 rows, kind `internal_assay` 16, for example dev 33 is local 99). Map
  `assay_id` through kind `assay` and `internal_assay_id` through kind `internal_assay`, or key internal assays by
  title (the 143 titles are unique locally); the report says which it used. `$GS_WORK/runs/relabel/relabel-rows.jsonl`
  (the 2026-09-15 relabel's rows, spec 1.1) holds the dev labels already renumbered and may serve as the reference.
  Expected: 1,213,093 edges, the three singular fields matching on every edge, every pair sharing exactly one assay,
  no protocol label on either side. The **plural lists** are reported apart and are no part of the pass condition:
  the dev box's are inconsistent (974,499 empty, 227,166 holding the SEEK assay id, 11,428 the internal id) and mix
  two id spaces, so they cannot be renumbered;
- (b) production, with the labels on the local production edges in `$GS_WORK/seeds/local-2026-09-14/neo4j.cypher.gz`
  (ids unchanged), every property, the protocol pair included: each edge classified with `labels.classify`, counts
  per class and per property, with a capped list of example pairs. It says how many of the 16,169 unlabelled
  production edges would gain a label (`new`) and how many of the 785,037 labelled ones lack only the plural lists
  (`plural_missing`; none of them carries the lists).

Write `$GS_WORK/runs/labels/report.json` and a short `report.md`. Nothing is written to any graph or table. Memory
stays within the lane's cap: keep computed labels as one digest per encoded pair, not as dicts.

**Tests first:** the comparison logic on small synthetic label sets: equal, a differing title, a remapped id that
matches, a title-keyed match, a missing edge on either side, list order, each `classify` class, and plural lists kept
out of (a)'s pass condition.

**HARD STOP:** the script exits non-zero unless (a) reports 1,213,093 of 1,213,093 matching on the singular fields.
On a non-zero exit the Workflow ends Run 2 here and reports the numbers. (b) is reported, never corrected: its
`changed` and `cleared` edges are the labels an upload sheet supplied or a rename left stale, and the operator sees
them, with the `new` and `plural_missing` counts, at review point 2. **Commit:** `feat(graph_sync): verify the DERIVED_FROM label rule against the dev box and the local graph`

### Task T10: the by-id entry points (spec 7.1; C-07)

**Interfaces:** Consumes T2, T4, T5, T6, T7. Produces the five functions of spec 7.1.

**Tests first** (a fake driver recording statements and fake readers): refuses a graph whose `schema_version` is not
the writer's and writes nothing; returns `lock_timeout` without writing when the lock is not acquired; writes only the
given ids; creates a missing declared edge and labels it in the same call, and archives then deletes an undeclared
one from a given child; labels the unlabelled edges incident to a sample in both directions and reports, without
writing, one whose stored label differs; retires an id MySQL no longer returns; runs `catalog_sync` first when a
sample's type has no SampleType node; adds a `declared: false` Attribute for an undeclared key; writes `IN_STUDY` for
the ids only; `sync_samples_of_type` chunks; `relabel_for_maps` touches only the members of a changed assay, under
the same rule (a renamed title is reported unless `apply_label_changes`), and stamps `label_maps_hash`. **Commit:** `feat(graph_sync): add by-id sync, retire and relabel entry points`

### Task T11: the full and catalog syncs (spec 11; C-08)

**Implementation:** the projection gets assay ids and parent lists; the label step over every edge after the lineage
steps: every edge the run created and every unlabelled edge get their labels, and `changed`, `cleared` and
`plural_missing` edges are counted per property in the report and written only with `apply_label_changes=True` (spec
7.3, R14); `retire_samples` for graph-only
`:Sample` ids in place of `relabel_orphans` (ghosts unchanged); the graph-write lock for the whole run; a
`graph_sync_run` row; `GraphMeta.label_maps_hash`; on success, `state.mark_done_before(started_at)`; SEEK Study
nodes keyed by `id` moved to `seek_study_id`. `full_sync` and `catalog_sync` keep their signatures.

**Tests first:** the step order with the new steps; a never-synced graph-only node becomes an orphan without `T_`
labels and a synced one is retired; a refused run records `refused`; the outbox rows older than the run are marked
done on success only; the label counts appear in the report; a full sync onto an empty graph leaves no declared edge
whose endpoints share an assay unlabelled; a stored label that differs is reported and kept; a missing plural list is
reported and not written. **Commit:** `feat(graph_sync): full sync writes schema 1.2: hashes, labels, the retire rule, the lock`

### Task T12: the drift check and gate G's new checks (spec 13, CI-9; C-09)

**Interfaces:** `drift.detect_sample_drift(driver, db, *, chunk) -> dict` (the read-only merge of spec 10.3: counts
and capped id lists of changed, missing in the graph, not in MySQL, and the new uuids); `drift.drift_check(driver,
db, *, sample_size, seed, chunk, now=None) -> dict` in gate G's shape with the detection counts, gate G's structural
checks and the freshness checks. `verify.gate_g` gains `9.lineage.labels` (fails when a declared edge whose
endpoints share an assay has no label, the gap that got past gates G and E; reports, without failing, the labels
that differ from the rule and the missing plural lists), `10.samples.no_t_label_without_sample` and
`11.samples.parent_lists`, keeping its signature.

**Tests first:** detection on fake streams (equal, a changed digest, a missing node, an extra node, a node without a
hash counts as changed); drift reads only; stale full syncs and reconciles fail; each new gate G check passes on a
correct fake graph and fails on its defect. **Commit:** `feat(graph_sync): add the drift check and three gate G checks`

**Gate R2:** Run 2's tasks committed; the Django lane over the blocking globs; `pytest ci/gate`; docs map; push.
**Review point 2: the operator reads V1's two reports.** No label reaches a live graph before this.

---

## Run 3: batch upload, every hook, the status endpoint

All nine tasks in parallel (they share no file).

### Task H1a: batch upload writes through graph_sync (spec 8; C-13)

**Implementation:** in each stage 5 batch transaction (`insert.py::process_batches`, `parallel.py`), insert the
outbox row `samples` / `batch:<job id>:<batch>` with the committed ids in `payload`, by qualified name
`dmac.graph_sync_outbox` on the batch's own connection; check the grant first, and if the seek user cannot insert
there, enqueue with `hooks.enqueue` right after the batch commits and say so in the task report. In stage 6
(`orchestrator.py`), replace `upload_all` with `targeted.sync_samples(ids)` for every outcome with a `sample_id`,
under `graph_write_lock(60)`; mark the job's rows done on success; put `graph: synced (N)` or `graph: pending (N)`
into the job totals and the summary CSV (`report.py`). `neo4j_only` resolves the sheet's UIDs to ids and calls the
same function. Delete `upload_all` and every writer only it used (inventory 7), keeping any helper another module
still imports (grep first).

**Tests first:** the enqueue happens inside the batch transaction (a rollback leaves no row); stage 6 calls
`sync_samples` with the committed ids; a lock timeout or a refused graph reports `pending` and the job still succeeds;
a cancel after stage 5 leaves the rows pending; `neo4j_only` ignores the sheet's metadata; no module imports a deleted
function. **Commit:** `feat(batch_upload): write the graph through graph_sync, with the outbox as the record`

### Task H1b: orphan resolution and the graph-only scripts (spec 8; C-14, C-20)

**Implementation:** `resolve_orphans` keeps its MySQL rewrite, drops `_DERIVED_FROM_CYPHER`, and enqueues `samples`
for the resolved children after the commit (outside the `with get_connection()` block in `tasks.py`).
`discover_orphans` keeps reading `parent_title_hashes`. Delete the three graph-only scripts and their tests.
**Commit:** `feat(batch_upload): orphan resolution enqueues instead of writing the graph`

### Task H2: the attribute API hook (spec 5 E2, E11; C-15)

**Implementation:** in `DjangoExecutionServices.record_commit`, after its compare-and-set succeeds, enqueue `catalog
*` and `samples_of_type type:<sample_type_id>`. **Tests first:** both rows after a committed plan; none after a failed
CAS; an enqueue failure never reaches the caller. **Commit:** `feat(attributes): enqueue a graph sync after every committed attribute change`

### Task H3: the legacy sample pages (spec 5, 9; C-16)

**Implementation:** `seek/sample/upload.py`: after `_storeSample` commits, and after `_batchUpdateSample` and
`_batchUpdateSampleAssociation`, enqueue `samples sample:<id>`; delete `storeSampleNeo4j` and
`_storeSampleNeo4jGuarded`. `seek/sample/table.py`: after `_deleteOneSample`'s transaction, enqueue `retire
sample:<id>`; delete `deleteSampleNeo4j`. `seek/views/samples.py`: at the end of `sampleAttributeSave` and
`sampleAttributeDelete`, after they write, enqueue `catalog *` and `samples_of_type type:<id>`. No route changes.
**Tests first:** the rows after each path; no Neo4j driver import left in `upload.py` or `table.py`. **Commit:** `feat(seek): legacy sample pages and the attribute editor enqueue graph syncs`

### Task H4: the proxy and users hooks (spec 5; C-17)

**Implementation:** after a 2xx response and the response model validates, enqueue: sample create and patch `samples
sample:<id from the response>`, destroy `retire sample:<id>`; sample type create and patch `catalog *` and
`samples_of_type type:<id>`; assay create and patch `assay_map *` and `isa *`; study, investigation and project create
and patch `isa *`; SOP create and patch `protocol_map *`; users create and patch `membership *`. People and data files
have no graph effect (registered in T18a). **Tests first:** one test per method: the row on 2xx, nothing on 4xx or
5xx, no exception on an enqueue failure. **Commit:** `feat(nextseek_api): enqueue graph syncs after SEEK proxy and users writes`

### Task H5: the admin hooks (spec 5 E8, E10; C-18)

**Implementation:** at the end of `cladeSave`, `cladeDelete`, `cladeSampleTypesSave`, `cladesSyncSampleTypes`
enqueue `catalog *`; at the end of `internalAssaySave`, `internalAssayDelete`, `assayAssociationSave`,
`syncInternalAssays` enqueue `assay_map *`. **Commit:** `feat(seek): enqueue graph syncs after clade and internal-assay edits`

### Task H6: assay registration (spec 7.3; C-19)

**Implementation:** `service.py::_recompute` and `runner.py` enqueue `samples sample:<id>` for every sample whose
links the registration changed, instead of calling `graph.py::recompute_for_samples`; delete that write path (keep any
read helper still imported). The response keeps its shape; its graph outcome reports `queued`. **Commit:** `feat(assay_registration): enqueue graph relabels instead of writing edges`

### Task H7: the publication backfill command (spec 5 E2; C-20)

**Implementation:** after `--apply` commits, enqueue `samples` rows for the updated ids in batches of 5,000
(`batch:backfill:<n>`). **Commit:** `feat(nextseek_api): the publication backfill enqueues a graph sync`

### Task T15: the status endpoint (spec 13; C-12)

Follow `.claude/skills/nextseek-viewset/SKILL.md` step by step. Needs only T2's state functions.

**Files:** see the ownership table. The ViewSet is native, read-only and superuser, reading only the two dmac tables
through `state.last_runs`, `freshness` and `outbox_summary`, with a 503 `JsonApiErrorResponse` when they cannot be
read; `router.register(r"admin/graph-sync", views.GraphSyncStatusViewSet, basename="admin-graph-sync")`; the Route in
`ci/routes.py` for **`local,dev` only**, `auth="write"`, GET; `OWNED_ROUTE_COUNT` 170 to 171 (170 is `dev-graph`'s
count with the Graph Search page; if the run-start merge brought more routes, one more than the merged count), the
history comment extended;
`ci/smoke/test_graph_sync_status.py` (CI-5, `local` and `dev`: the superuser client, no `write` marker, failing when
the credentials are missing; freshness assertions; and CI-3's parity-lite: when the status shows a successful full
sync at the writer's version, the same small body to advanced_search and graph_search gives equal `total`).

**Tests first:** 401, 403 for a non-superuser, 200 with each part, 503, the path in `SchemaGenerator` output.
**Acceptance:** the conventions pair, `python3 scripts/validate_viewset_conventions.py`, `pytest ci/gate`, the smoke
registry unit tests. **Commit:** `feat(nextseek_api): add the graph sync status endpoint`

**Gate R3:** Run 3's tasks committed; the blocking globs and every touched module's existing tests pass in the Django
lane; `pytest ci/gate`; the smoke registry unit tests; push. **Review point 3.**

---

## Run 4: the nightly sync, the loop, the entrypoint, drift after rebuild, the registry

Stage 4A: T3, T13, T16, T17, T18a in parallel. Stage 4B: T14, T18b, T20 in parallel.

### Task T3: the schedule (spec 12; R9)

**Interfaces:** pure: `Cadence(kind, weekday, hour, minute)`, `DEFAULT_CADENCES` (reconcile daily 02:00, drift daily
02:30, full Sunday 03:00, UTC), `last_boundary(cadence, now)`, `slot_key(cadence, boundary)`, `due_slots(cadences,
now, last_ok_started)`. **Tests first:** boundaries around the hour, midnight, the ISO week and year; a full sync
after the boundary satisfies the reconcile; a missed slot is due at the next pass. **Commit:** `feat(graph_sync): add the sync schedule`

### Task T13: the nightly targeted sync (spec 10.3; C-09)

**Interfaces:** `reconcile(driver, db, *, run_dir, chunk=5000, dry_run=False, guard_fraction=0.2,
new_uuid_cap=1000) -> dict`: `catalog_sync`, `sync_small_tables`, `relabel_for_maps` when the maps' digest moved,
`drift.detect_sample_drift`, then `sync_samples` for changed and missing ids and `retire_samples` for extra ids, the
new-parent pass through `sources.samples_naming`, the 20% guard (enqueue `full` and stop), a run record with the
highest `samples.id` and `updated_at` seen. **Tests first:** each step on fakes; the guard stops before any write;
above the uuid cap the pass is skipped and reported; `dry_run` writes nothing. **Commit:** `feat(graph_sync): add the nightly targeted sync`

### Task T16: the entrypoint line (spec 12; C-11)

**Files:** `docker/scripts/entrypoint.sh` (the block of spec 12, before `wait -n`, marked by comments),
`nextseek_api/tests/test_graph_sync_entrypoint.py` (runs the marked block under bash with `uv` and `sleep` stubbed:
with the variable `0` no background job starts; unset or `1` starts the restart loop, which survives the stub
exiting; the block sits before `wait -n`; `bash -n` passes on the whole file). **Commit:** `feat(docker): start the graph sync loop from the entrypoint`

### Task T17: drift after every rebuild (spec CI-4; C-23)

**Files:** `startup/steps/validate.py` (`check_graph_drift(repo_root, env) -> HealthResult`, modelled on
`check_cc_runner`, running `docker compose exec -T nextseek uv run --no-sync python manage.py graph_sync --drift
--json` through `subprocess.run(..., input=b"")`, never `compose_exec`; exit 0 ok, 1 drift with the failing check
names, 2 skipped with the printed reason, anything else failed), `startup/cli.py` (called in `rebuild` after the
health report for app rebuilds on `local` and `dev`, and in `ci`; the profile tuple restated, never imported from
`ci/`; advisory: the suite still runs and `rebuild` exits non-zero at the end), `startup/ci/runner.py`
(`write_report` renders a `## Graph drift` section), `startup/tests/test_validate_graph_drift.py`. **Tests first:**
each exit code; empty stdin; the command line; the report section; the rebuild exit code. **Commit:** `feat(startup): check graph drift after every app rebuild`

### Task T18a: the writer scan and registry (spec CI-1; C-21)

**Files:** `ci/writers.py` (stdlib; `Writer(id, sites, tables, how, hook=None, hook_site=None, reconcile=None)`, one
per inventory row WR-01 to WR-28 plus one per scanned site that belongs to none; reconcile codes `RECONCILE_RAILS`,
`RECONCILE_OPERATOR`, `RECONCILE_INSTALL`, `RECONCILE_DEAD`, `NO_GRAPH_EFFECT`, `GRAPH_SYNC_OWNER`),
`ci/gate/writer_scan.py` (the inventory's scan: SQL strings, the legacy table layer with mixins and
`self.tablemodel`, ORM writes, SeekAPIClient writes, the Rails runner, `.sql` files, Cypher writes; skips tests,
migrations, regex strings and read operations; the generic record layers are infrastructure),
`ci/gate/test_writer_scan_unit.py` (synthetic sources for every shape), `ci/gate/test_writer_registry.py` (scan and
`WRITERS` agree in both directions; every `hook` call is present in its `hook_site` by AST; proxy table lists are
declared, never inferred). **Acceptance:** `pytest ci/gate` in the Django lane, and the scan files on the host with
only pytest. **Commit:** `feat(ci): add the writer registry gate`

### Task T14: the loop and the command (spec 12, 13; C-10)

**Interfaces:** new modes `--loop`, `--once`, `--reconcile`, `--drift`, `--samples ID[,ID...]`; options `--interval`
(5), `--run-root` (default `$GS_RUN_DIR`, else `<LOG_DIR>/graph_sync`), `--no-record`, `--apply-label-changes` (with
`--full`, `--reconcile`, `--samples`: passes `apply_label_changes=True`, spec R14); the loop passes that flag to its
children only when `NEXTSEEK_GRAPH_SYNC_LABEL_CHANGES=apply` (default `report`). `--verify`, `--drift` and
`--loop` accept the live host without `--i-mean-the-live-graph`; `--full`, `--catalog`, `--reconcile` and `--samples`
still need it by hand, and the loop passes it to its children. `loop.run_pass(worker_id, *, now, launch)`:
housekeeping, `schedule.due_slots`, the drain (in-process kinds call `targeted`; `full`, `reconcile`, `drift` run as
child `manage.py graph_sync` processes with `--run-dir <run root>/<kind>-<UTC time>`); child exit 0 done, 2 done with
the refusal recorded, anything else or a timeout failed with back-off; keep the newest 20 run directories per kind.
`--loop` never returns; an exception in a pass is logged.

**Tests first:** every existing command test passes unchanged except the live-refusal test for `--verify`, which now
expects acceptance; `--apply-label-changes` reaches `full_sync` and the loop passes it only with the variable set to
`apply`; a pass schedules due slots in order, runs a heavy kind as a child with the right argv, maps each
exit status, drains each in-process kind, survives an exception and prunes run directories. **Commit:** `feat(graph_sync): add the loop and the reconcile, drift and samples modes`

### Task T18b: every route says what it writes (spec CI-1; C-21)

**Files:** `ci/routes.py` (`effect` required, one of `reads`, `writes`, `external`, `n/a`; `writers` ids from
`ci/writers.py`; `writes` needs writers, `reads` none; validated in `__post_init__`; every entry classified from
inventory 3 and a reading of its view, **including `^seek/^graph/search/`, the Graph Search page (`effect="reads"`, no
writers), and every route a run-start merge of `dev-graph` brought**),
`ci/gate/live_routes.py` (`live_views()` from the same walk; `live_patterns()` unchanged for its callers),
`ci/gate/test_route_registry.py` (the skeleton emits `effect="UNCLASSIFIED"`), `ci/gate/test_route_effects.py`
(every `writers` id exists; every writer entered by a route is named by one; a report-only tripwire listing `reads`
routes whose view reaches a writer site through statically resolvable calls; it does not list the Graph Search page,
whose view reads `sample_types` through `DBtable.getComboboxOptions`, a SELECT), `ci/smoke/test_registry_contents.py`
(every entry has an effect), `ci/README.md`, `ci/CLAUDE.md`. **Acceptance:** `pytest ci/gate`; the smoke registry
unit tests on the host. **Commit:** `feat(ci): classify every route by what it writes`

### Task T20: the docs (spec C-24)

**Files:** `nextseek_api/graph_sync/README.md` (the new modules and modes, the outbox kinds, the lock, the run
records), `nextseek_api/batch_upload/README.md` and `CLAUDE.md` (stage 6 is graph_sync's; the outbox row; the
`graph:` status), `nextseek_api/README.md` (rows only). **Acceptance:** docs map clean. **Commit:** `docs(graph_sync): document the sync loop, the hooks and the batch upload change`

**Gate R4 (final):** the Django lane over the blocking globs and every touched module's tests; `pytest ci/gate`; the
smoke registry unit tests; the startup lane; `python3 scripts/validate_viewset_conventions.py`; `python3
ci/docs_map.py`; the diff scan; push. **Final review.**

---

## After the runs (operator)

- Merge this branch back into `dev-graph` (spec 19.1): one last merge of `origin/dev-graph`, Gate R4's checks on the
  merged tree, then the merge.
- Announce every live full sync or relabel below to the operator for the Nessie chat before it runs (spec 17 step 5).
  During that chat's freeze of the live local graph, deploy with `NEXTSEEK_GRAPH_SYNC_LOOP=0`.
- Per box, local first, then the dev box: deploy, then `graph_sync --full --i-mean-the-live-graph` to write schema
  1.2; the loop refuses to write until then, and uploads report `graph: pending`. That run writes new labels only;
  read its label report before deciding on `--apply-label-changes` (spec R14). The dev box follows once follow-up 1's
  scope work (A1) is live there. A known-good dev commit is the rollback.
- `NEXTSEEK_GRAPH_SYNC_LOOP=0` is the loop's off switch.
