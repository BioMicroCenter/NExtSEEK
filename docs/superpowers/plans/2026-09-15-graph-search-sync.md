# graph_search follow-up 2: implementation plan

- Spec: `docs/superpowers/specs/2026-09-15-graph-search-sync-design.md` (section numbers below are the spec's).
- Branch: `feat/graph-search-sync`. Increment 1 is tasks T1 to T10; increments 2 to 5 are outlined at the end.
- Every Django test runs in a throwaway container over a read-only mount of the worktree (`ci/README.md` "Running
  and testing"), never against a live stack. The writer-registry scanner and its unit tests are standard library and
  also run on the host.

The Django lane, from the worktree root:

```bash
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none --memory 2g -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest <paths> -q -p no:cacheprovider
```

## Increment 1

### T1. The two tables (spec section 4)

- Files: `nextseek_api/graph_sync/models_db.py` (`GraphSyncOutbox`, `GraphSyncRun`, `db_table` `graph_sync_outbox`
  and `graph_sync_run`, `app_label` `nextseek_api`); the re-export line in `nextseek_api/models.py`;
  `nextseek_api/migrations/0021_graph_sync_outbox_and_run.py` depending on `0020_assayregistrationjob` (the one head;
  check with `ls nextseek_api/migrations` and the `dependencies` of every file first).
- Test first: `nextseek_api/tests/test_graph_sync_state.py` asserts the table names, the `(kind, key)` uniqueness and
  that `makemigrations --check --dry-run` reports no change.
- Accept: the migration applies on the SQLite test database; `makemigrations --check` is clean.

### T2. Outbox and run bookkeeping (spec section 4)

- File: `nextseek_api/graph_sync/state.py`: `enqueue`, `ensure_slot`, `claim_next`, `finish_done`, `finish_failed`,
  `start_run` (best-effort, returns a handle whose `finish` writes the row), `last_ok_run`, `last_watermark`,
  `last_runs`, `reap_abandoned`, `outbox_summary`, `freshness`, and the watermark codec.
- Tests (same file): a slot is inserted once; enqueue coalesces and resets a done row; a claim is exclusive and
  counts an attempt; an expired lease is claimable again; a failure backs off; a row re-enqueued while claimed stays
  pending after `finish_done`; a dead row is not claimed; recording tolerates a missing table; abandoned runs are
  marked; freshness says `never` with no run and uses a full sync for the delta's freshness.

### T3. The schedule (spec S6)

- File: `nextseek_api/graph_sync/schedule.py`, pure: `Cadence`, `DEFAULT_CADENCES`, `last_boundary`, `slot_key`,
  `due_slots(cadences, now, last_ok_started)`.
- Tests: `nextseek_api/tests/test_graph_sync_schedule.py`: boundaries before and after the hour, across midnight and
  across the ISO week; a full sync after the boundary satisfies the delta and the catalog job; a missed slot is due on
  the next pass.

### T4. New readers and writer functions (spec section 6)

- `sources.py`: `iter_samples_since`, `samples_by_ids`, `sample_projects_for`, `uuid_ids_for` (byte-exact in Python),
  `sample_ids`, `samples_high_water`.
- `writer.py`: `archive_and_drop_undeclared_for_children`, `add_attributes`; `cypher.py`: the two statements they
  send, plus `GRAPHMETA_STATE`.
- Tests in `test_graph_sync_sources.py` and `test_graph_sync_writer.py`, with the existing fakes.

### T5. The delta (spec section 6)

- File: `nextseek_api/graph_sync/delta.py`: `delta_sync(driver, db, since, chunk, run_dir)`, `DeltaRefused`.
- Tests: `nextseek_api/tests/test_graph_sync_delta.py` over the command tests' fixed world: refuses a non-v1.1 graph
  and a missing watermark; writes only the changed samples; creates a declared missing edge and archives then
  deletes an undeclared one from a changed child; relabels a graph-only id; writes a MySQL id with no node; adds an
  undeclared attribute and restamps the catalog; returns the last row's watermark.

### T6. The drift check (spec section 7)

- File: `nextseek_api/graph_sync/drift.py`: `drift_check(driver, db, sample_size, seed, chunk, freshness)`.
- Tests: `nextseek_api/tests/test_graph_sync_drift.py`: passes on the graph a correct sync writes; reads only; fails
  a missing node, a graph-only node, an unstamped node, a changed title, an attribute set that differs, a catalog hash
  that differs, a stale full sync and a stale delta; never runs the POC's account checks.

### T7. The command (spec sections 5 to 7, S9)

- File: `nextseek_api/management/commands/graph_sync.py`: modes `--loop`, `--once`, `--delta`, `--drift`; options
  `--interval`, `--run-root`, `--no-record`, `--no-bootstrap`; `--full` and `--catalog` record their run and take
  `--no-bootstrap`. `nextseek_api/graph_sync/loop.py` holds the pass: housekeeping, schedule, drain, the child
  launcher and the run-directory pruning.
- Tests: extend `test_graph_sync_command.py` (the existing flags behave exactly as before) and add
  `nextseek_api/tests/test_graph_sync_loop.py`: a pass schedules the due slots in order, runs a heavy kind as a child
  with the right argv, maps each exit status as the spec's table says, runs the catalog in process behind the v1.1
  guard, fails an unknown kind without raising, and survives an exception in a pass.

### T8. The entrypoint line (spec section 5)

- File: `docker/scripts/entrypoint.sh`, a marked block before `wait -n`.
- Test: `nextseek_api/tests/test_graph_sync_entrypoint.py` runs the block under bash with `uv` and `sleep` stubbed:
  with the variable unset no background job starts; with it set the loop is restarted after it exits; the block sits
  before `wait -n`; `bash -n` passes on the whole file.

### T9. The status endpoint (spec section 8)

- Files: `nextseek_api/services/graph_sync_status.py`, the pydantic models in `nextseek_api/models.py`,
  `GRAPH_SYNC_STATUS_DESC` in `nextseek_api/endpoint_descriptions.py`, the import in `nextseek_api/views.py`, the
  registration in `nextseek_api/urls.py`, the route in `ci/routes.py`, `OWNED_ROUTE_COUNT` 169 to 170.
- Tests: `nextseek_api/tests/test_services_graph_sync_status.py`: 401, 403 for a non-superuser, 200 with the last
  runs, freshness, outbox and drift, 503 when the tables cannot be read, and the path in `SchemaGenerator` output;
  then the conventions pair, `scripts/validate_viewset_conventions.py`, `ci/gate`, and the smoke registry unit tests.

### T10. The writer registry gate (spec section 9)

- Files: `ci/writers.py` (declarations), `ci/gate/writer_scan.py` (discovery), `ci/gate/test_writer_registry.py`
  (the blocking two-way diff and the hook check), `ci/gate/test_writer_scan_unit.py` (the scanner on synthetic
  sources); rows in `ci/README.md` and `ci/CLAUDE.md`.
- Order: write the scanner's unit tests, then the scanner, then run it on the tree and register every site it finds
  with its category code, reading each site to choose the code.
- Accept: `pytest ci/gate` passes in the Django lane and, for the two writer files, on the host with only pytest.

### Finish

- `python3 ci/docs_map.py` clean; the new spec and plan are negated in `.gitignore` and listed in `docs/INDEX.md`.
- `nextseek_api/graph_sync/README.md` documents the new modes and modules.
- Scan the diff against `origin/feat/graph-search` for emails, home paths and tokens; push only this branch.

## Increment 2: hooks (B5, CI5, S5)

- `nextseek_api/graph_sync/hooks.py::enqueue(kind, key)`: never raises into the caller; logs and counts a failure.
- Add each hook at the site the spec's section 10 names, one commit per writer family, each switching its registry
  entry from `RECONCILE_HOOK_PENDING` to `hook=` so the gate proves the call is there.
- The drain's hook kinds: `samples`, `samples_of_type`, `delete`, `membership`, `isa`, through the delta's per-id
  path; a type rename relabels every sample of the type.
- Batch upload's stage 6 stops writing Sample and SampleType properties; its node projection is the POC's.
- CI5, in `ci/smoke/test_write_lane.py` behind `CI_WRITE_DESTRUCTIVE=1`: create an attribute, poll the status
  endpoint until the outbox row is done and `GraphMeta.catalog_hash` moved.
- S5: route the legacy attribute page to the native API and delete the two GET views and their registry rows.

## Increment 3: reconciler and indexes (B6, B7, B2)

- In the loop, every 5 minutes: compute the catalog hash from MySQL (about 6,000 rows) and run the catalog job when it
  differs from `GraphMeta.catalog_hash`. Nightly: diff `projects_samples` and memberships in full and rewrite the
  samples and people that differ.
- `startup/steps/schema_fixups.py`: make `idx_updated_id` and `idx_samples_sample_type_id` managed indexes applied on
  `rebuild` too (the `ManagedIndex` ownership pattern; fixups run only on `install` today).
- Per-type `catalog_hash` on SampleType nodes, and the catalog job writing only the types whose hash moved.

## Increment 4: statistics and gates (B3, CI2 to CI4, CI6)

- B3: per-project `(:Attribute)-[:USED_IN {sample_count, top_values, top_counts}]->(:Project)`, the sensitive flag,
  an email scrub, recomputed for touched types by the drain and in full weekly, stamped `stats_computed_at`.
- CI2: after every rebuild on local and dev, `startup/ci/runner.py` runs
  `docker compose exec nextseek python manage.py graph_sync --drift --json --i-mean-the-live-graph </dev/null` (the
  redirect matters: `compose exec -T` swallows a piped script) and fails on drift or a stale sync; or a smoke test
  reads the status endpoint with the superuser client.
- CI3 catalog coverage (report-only first), CI4 Nessie context gates, CI6 graph_search in both read-safe lists and a
  test that `execute_read` refuses a write.
- CI1 part 2: every `UPDATE` of `samples` sets `updated_at` or is registered hook-only.

## Increment 5: cleanups (B9)

- `seek/views/admin.py::cladeSave` writes a clade's order into its colour; `syncSampleTypes` inserts null clades; the
  `is_deprecated` docstring; `entity_tree` joins the empty `dmac.assays`.
