# graph_search follow-up 2: keep the graph in sync, enforced by CI

- Date: 2026-09-15
- Branch: `feat/graph-search-sync`, cut from `feat/graph-search` at `4b3e087a`
- Status: draft for operator approval. Nothing here is built. The POC already ships `graph_sync --full`, `--catalog`
  and `--verify`; increment 1 exists only as a draft model and migration for the two tables, untested and uncommitted,
  which the plan's first task starts from.
- Tracking: none yet. File an issue per `docs/ISSUE-CONVENTIONS.md` after approval.
- Parent design: `docs/superpowers/specs/2026-09-14-graph-search-poc-design.md`, sections 11.2 (sync) and 11.3 (CI),
  with section 6 (the writer this work schedules). Plan: `docs/superpowers/plans/2026-09-15-graph-search-sync.md`.
- The recon behind it (a two-stage read-only recon of 2026-09-14) stays outside the repository because it cites local
  data. Its findings are restated below where they decide something.

## 1. Goal

The POC built `nextseek_api/graph_sync/`, a writer that rebuilds graph schema v1.1 from MySQL, and ran it once by
hand. This work keeps the graph equal to MySQL afterwards, and makes CI notice when it is not:

1. **A scheduler** that runs the writer on its own: an outbox drained every 5 seconds, a sample delta, a catalog
   rebuild and a read-only drift check every night, and a full sync every week.
2. **A record** of every run, and a read-only status endpoint that reports it.
3. **A drift check** that compares the graph with MySQL and reports staleness.
4. **Per-writer hooks** so that a write reaches the graph in seconds instead of by the next night (B5), and a
   reconciler for the writes no hook can see (B6).
5. **CI gates**: a blocking registry of every writer (CI1), a drift gate after every rebuild (CI2), and the rest of
   section 11.3.

Increment 1 (planned, not built) is items 1 to 3 and CI1. Items 4 and 5's other gates are increments 2 to 4 (section 12).

Not the goal: changing what the graph holds (schema v1.1 is fixed by the parent design), Nessie (follow-up 1), or
per-project statistics on the catalog (B3, planned).

## 2. What the recon found that shapes this

| Fact | Consequence |
|---|---|
| Celery beat is configured but no beat process runs anywhere; the house pattern for background work is a management command looping inside the app container, started by `docker/scripts/entrypoint.sh` and leased in MySQL (`run_assay_registration_jobs`, `recover_attribute_sync_jobs --loop`, `dispatch_attribute_outbox`) | the scheduler is `manage.py graph_sync --loop`, not a beat entry |
| The entrypoint ends in `wait -n`: any background process that exits takes the whole container down, web server included | the loop must never exit on a failure, and its launch line must be guarded so that even a crash of the process cannot reach `wait -n` |
| The app container shares one memory cap between the web server, two Celery workers and every loop | a full sync (per-sample indexes for about 1.08M samples) must not live in the long-running process |
| 27 writer paths change samples or the catalog (18 sample writers, 9 catalog writers). Six catalog writers and most sample writers have a Django hook site; the SEEK Rails UI and REST API, hand SQL and the context-table seed have none | per-writer hooks cover most writes; a reconciler and the weekly full sync bound the rest |
| `samples.updated_at` has no `ON UPDATE` default. The native attribute API and Rails `refresh_samples` rewrite `json_metadata` without bumping it; `projects_samples`, `investigations_projects` and `group_memberships` leaves carry no usable timestamp | a watermark delta sees only some writes; the full sync is the backstop, and the drift check measures what the delta misses |
| `idx_updated_id (updated_at, id)` exists on the local snapshot but no repository file creates it and the dev box lacks it; schema fixups run only on `install` | the nightly delta is an index range scan locally and a full scan on the dev box until B7 lands |
| The whole catalog is about 6,000 rows | the catalog needs no watermark: rebuild it every run |
| The POC's full sync at 1.08M samples took about 10 minutes of steps (samples 180 s, await indexes 77 s, lineage 35 s plus 41 s, preflight 26 s); gate G took about 50 s | a weekly full sync and a nightly drift check are affordable |
| The dev box graph must not receive sample metadata before follow-up 1's scope work (A1) lands | the loop must never turn a v1.0 graph into v1.1 on its own |

## 3. Decisions

"Operator to confirm" marks a recommendation the operator has not ruled on; the build follows the recommendation until
told otherwise.

| # | Question | Decision | Status |
|---|---|---|---|
| S1 | Where does the scheduler live? (recon Q18) | A leased `manage.py graph_sync --loop` started from `docker/scripts/entrypoint.sh`. Rejected: a Celery beat entry (no beat process exists), a task on the `batch_upload` queue (concurrency 1, broker lost on recreate), SEEK's supercronic (Rails only), host cron (outside the repo, invisible to CI), a new compose service on the app image (reopens the stale-image failure that folding the attribute workers into the app container removed) | operator to confirm |
| S2 | Does the loop start everywhere? | **Opt-in.** The entrypoint starts it only when `NEXTSEEK_GRAPH_SYNC_LOOP=1`. A box without the variable runs exactly as before | operator to confirm |
| S3 | May the loop build v1.1 on a graph that is not v1.1? | **Never.** Scheduled full syncs and the catalog job refuse unless `GraphMeta.schema_version` already equals the writer's; the delta requires it too. The first v1.1 build stays a manual `graph_sync --full`, the operator's step. So setting the variable on the dev box before A1 cannot put metadata there | decided (safety) |
| S4 | Stats freshness: exact per touched type, or as of the weekly run? (recon Q12) | Structure (nodes, properties, labels, edges, the catalog) exact through hooks and the nightly delta. Statistics (`Attribute.sample_count`, `SampleType.sample_count`, and B3's per-project `USED_IN`) recomputed by the drain for the types a hook touched, and in full by the weekly sync, stamped with `stats_computed_at`. Increment 1 refreshes `SampleType.sample_count` after every delta (a graph-side count) and `Attribute.sample_count` only in the weekly full sync | operator to confirm |
| S5 | Retire the legacy attribute editor, a GET that writes and skips Rails validation? (recon Q22) | Yes: route its page to the native attribute API and remove the two GET views, once the writer registry lists it (it does from increment 1). Until then it is a registered writer with a pending hook | operator to confirm |
| S6 | Schedule (UTC) | Delta 02:00, catalog 02:05, drift 02:10 every night; full sync Sundays 03:00. A slot missed while the loop was down runs when the loop next starts | operator to confirm |
| S7 | Freshness thresholds | Last successful full sync within 8 days (192 h); last successful delta or full sync within 26 h; oldest pending outbox row within 1 h | operator to confirm |
| S8 | How are heavy jobs run? | The full sync, the delta and the drift check run as child processes of the loop (`manage.py graph_sync --full`, `--delta`, `--drift`), so their memory returns to the system when they end and a crash in one cannot kill the loop. The catalog job (about 6,000 rows) runs in the loop's own process | decided |
| S9 | Which runs are recorded? | Every run of `--full`, `--catalog`, `--delta` and `--drift`, whoever starts it, writes one `graph_sync_run` row, so a manual run counts for freshness and for the schedule. Recording is best-effort: a database without the table (the POC's throwaway lane) prints a warning on stderr and the run goes on. `--no-record` turns it off; `--verify` records nothing | decided |
| S10 | Hook placement | Hooks enqueue an outbox row in the dmac database after the writer's own commit and never call Neo4j inline; section 10 names every site | operator to confirm |
| S11 | Drift results: repair or report? | Report only. The drift check never writes; the weekly full sync and B6's reconciler repair | decided |

## 4. State: two dmac tables

Both live in the Django `default` database (dmac), created by migration `nextseek_api/migrations/0021_graph_sync_outbox_and_run.py`
(which follows `0020_assayregistrationjob`, the single head). The models are `nextseek_api/graph_sync/models_db.py`,
re-exported from `nextseek_api/models.py` so the app registry loads them.

**`graph_sync_outbox`**: work waiting for the loop.

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint | primary key |
| `kind` | varchar(32) | what to do: `full`, `delta`, `catalog`, `drift` in increment 1; B5 adds `samples`, `samples_of_type`, `delete`, `membership` |
| `key` | varchar(191) | what to do it to: `slot:<date or ISO week>` for a scheduled run, `type:<id>`, `sample:<id>` or `*` for a hook |
| `enqueued_at` | datetime | when the row was last asked for |
| `claimed_by` | varchar(255), null | the worker holding it (host, pid and a nonce) |
| `lease_expires_at` | datetime, null | the claim's end; after a failure, the earliest retry |
| `attempts` | int | claims since the last success; a row at the maximum is dead until enqueued again |
| `last_error` | text, null | the last failure, truncated to 4,000 characters |
| `done_at` | datetime, null | when it last succeeded; null while pending |

`(kind, key)` is unique. That one constraint gives both behaviours the table needs:
- **A scheduled slot runs once.** The loop inserts `(full, slot:2026-W38)` if it is absent; two loops racing both
  try, and the loser's insert fails on the constraint.
- **Hooks coalesce.** Enqueueing an existing row resets it to pending (`done_at` null, `attempts` 0, `last_error`
  null) and moves `enqueued_at` to now. A type touched ten times in a minute is one row. A worker remembers the
  `enqueued_at` it claimed; it marks the row done only if that value is unchanged, so a write that arrived while the
  row was running leaves it pending and it runs again.

A claim is a compare-and-set `UPDATE` on a due row (`done_at` null, `attempts` below the maximum, and no live lease),
which also increments `attempts`. A failure clears the claim and sets `lease_expires_at` to now plus a back-off (6 h
for a full sync, 1 h otherwise), so the row is retried after it. A worker that dies mid-run leaves a lease that
expires, and the row is claimed again.

**`graph_sync_run`**: one row per run.

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint | primary key |
| `kind` | varchar(32) | `full`, `delta`, `catalog`, `drift` |
| `started_at`, `finished_at` | datetime | `finished_at` is null while running |
| `status` | varchar(16) | `running`, `ok`, `failed`, `refused`, `abandoned`; a drift run is `ok` when every check passed and `drift` when one failed |
| `watermark_from`, `watermark_to` | varchar(64), null | the sample watermark `(updated_at, id)` as `<ISO datetime>|<id>`: the delta's start and end; for a full sync, `watermark_to` is the newest `(updated_at, id)` read before it started, so the next delta re-reads anything written during the sync |
| `counts_json` | JSON, null | the run's scalar counts and timings, its run directory, and its error or refusal |
| `drift_json` | JSON, null | the drift check's result (section 7) |

A `running` row older than its kind's longest plausible run (12 h for a full sync, 3 h otherwise) is marked
`abandoned` by the next loop pass.

## 5. The loop: `manage.py graph_sync --loop`

```
manage.py graph_sync --loop [--interval 5] [--run-root PATH] [--i-mean-the-live-graph]
manage.py graph_sync --once [--run-root PATH] [--i-mean-the-live-graph]
```

A pass does three things:
1. **Housekeeping** (at most once a minute): close stale database connections, mark abandoned runs.
2. **Schedule** (at most once a minute): for each job in S6, find the most recent boundary at or before now. When no
   successful run that satisfies the job started at or after that boundary (a full sync satisfies the delta and the
   catalog job too), insert its slot row. A slot missed while the loop was down is therefore inserted on the next
   start.
3. **Drain**: claim due rows oldest first and run each: `full`, `delta` and `drift` as a child `manage.py graph_sync`
   process with `--run-dir <run root>/<kind>-<UTC time>` (and `--no-bootstrap` for the full sync), `catalog` in
   process behind the S3 guard. The child records its own `graph_sync_run` row; the loop reads its exit status.

| Kind | Child exit status | The slot becomes |
|---|---|---|
| `full`, `delta` | 0 | done |
| `full`, `delta` | 2 (refused: the graph is not v1.1, no watermark yet, a preflight problem) | done; the refusal is in `graph_sync_run` and the status endpoint |
| `full`, `delta` | anything else, or a timeout (6 h full, 2 h delta) | failed; retried after the back-off |
| `drift` | 0 (no drift) or 1 (drift found) or 2 (refused) | done |
| `drift` | anything else (it could not complete: exit 3) | failed; retried after the back-off |

`--loop` sleeps `--interval` seconds between passes and never returns: every exception in a pass is logged and the
next pass starts. `--once` makes one pass, drains every due row, prints one line per row and exits 1 when one failed.
The run root defaults to `$GS_RUN_DIR`, else `<LOG_DIR>/graph_sync`; the loop keeps the newest 20 run directories
per kind and deletes older ones.

While a child runs, the loop waits for it, so a hook's row waits behind a full sync (about 10 minutes at 1.08M
samples). That is the price of one worker, and the reason hooks go through the outbox rather than calling the writer.

**The launch line** (`docker/scripts/entrypoint.sh`, before `wait -n`):

```bash
if [ "${NEXTSEEK_GRAPH_SYNC_LOOP:-0}" = "1" ]; then
  ( while :; do uv run --no-sync python manage.py graph_sync --loop --i-mean-the-live-graph; sleep "${GRAPH_SYNC_RESTART_DELAY:-60}"; done ) &
fi
```

The subshell restarts the loop after any exit and never ends itself, so nothing about the loop can end `wait -n`. It
must stay an `if` block: `[ ... ] && ( ... ) &` backgrounds the test itself, which exits at once when the variable is
unset and takes the container down. A test runs the block under bash with a stubbed `uv` and asserts both.

`--i-mean-the-live-graph` is needed because the stack's Neo4j host is `neo4j`, the name the command refuses by
default. It is safe here because of S2 and S3.

## 6. The sample delta: `manage.py graph_sync --delta`

`graph_sync.delta.delta_sync` patches a v1.1 graph with the samples MySQL changed since the last watermark. It refuses
(exit 2) when the graph is not v1.1 or no successful full sync or delta has recorded a watermark.

1. **Read** samples in keyset pages on `(updated_at, id)`: `WHERE updated_at > %s OR (updated_at = %s AND id > %s)
   ORDER BY updated_at, id` (the row-constructor form defeats the index).
2. **Write** each page through the full sync's own path: the POC's projection, then `writer.write_samples` (the whole
   property map replaced, the type label set, `OF_TYPE` and `IN_PROJECT` rebuilt), with the page's project links read
   by id.
3. **Lineage** for the page's samples as children: every declared parent pair is created if missing
   (`writer.write_missing_lineage`); every other `DERIVED_FROM` from those children to a `Sample` is archived to
   `derived_from_undeclared_archive.tsv` in the run directory and deleted (`writer.archive_and_drop_undeclared_for_children`),
   the same rule the full sync applies to the whole graph.
4. **Deletes and gaps**: MySQL's id set against the graph's. A graph-only id becomes `:OrphanSample`, as in the full
   sync. A MySQL id with no node (a sample written with an old `updated_at`) is read by id and written.
5. **Catalog**: a key found on a changed sample that its type does not declare and the graph does not yet hold becomes
   a `declared: false` Attribute node (`writer.add_attributes`); when any was added, `run.catalog_sync` restamps
   `GraphMeta.catalog_hash`. `SampleType.sample_count` is recomputed.

What the delta cannot see, and what covers it:

| Change | Why the delta misses it | Covered by |
|---|---|---|
| a `json_metadata` rewrite by the native attribute API or Rails `refresh_samples` | no `updated_at` bump | B5's hook on the attribute API; the weekly full sync; the drift check's metadata sample |
| a `projects_samples` change alone | no timestamp | B5's hooks; B6's reconciler; the weekly full sync; drift check 2 |
| a parent whose uuid changed | its children did not change | the weekly full sync; drift check 1 |
| membership (`group_memberships`) | a leave is a flag flip | B5's users hook; the weekly full sync |

Until B7 creates `idx_updated_id` on every box, each delta page is a full scan of `samples` where the index is missing
(the dev box). A delta that finds most of the table (a large ingest) is correct but slow there.

## 7. The drift check: `manage.py graph_sync --drift [--json]`

`graph_sync.drift.drift_check` reads only. It returns gate G's shape (`checks`, `pass`, `stats`) and reuses gate G's
checks where they apply, keeping their names, so a drift report and a gate G report read the same way.

| Check | Compares |
|---|---|
| `drift.samples.missing_in_graph`, `drift.samples.not_in_mysql` | the full id sets of `samples` and `:Sample` nodes |
| `drift.samples.unstamped` | `:Sample` nodes without `synced_at` (not written by this writer) |
| `drift.samples.sampled_title_mismatch` | `title` on the random samples |
| `1.lineage.*` (gate G 1) | DERIVED_FROM between Sample nodes against the pairs `collect_parent_tokens` declares |
| `2.scope.*` (gate G 2) | `project_ids` against the distinct `projects_samples` pairs: per project, and exactly on the random samples |
| `3.catalog.*` (gate G 3) | every property key on a `T_X` node is an Attribute title on SampleType X |
| `4.samples.*` (gate G 4) | Sample and OF_TYPE counts, one type label per sample |
| `5.attributes.*` (gate G 5) | Attribute nodes with an `id` against `sample_attributes`, titles byte-exact |
| `drift.catalog.sample_types` | SampleType nodes against `sample_types` by id, titles byte-exact |
| `drift.catalog.types_with_attribute_set_diff` | per SampleType, the declared `(id, title)` pairs it `HAS_ATTRIBUTE` against `sample_attributes` |
| `drift.catalog.hash` | `GraphMeta.catalog_hash` against the hash `run.catalog_sync` would write now |
| `6.scope.person_scopes_mismatched` (gate G 6) | per distinct membership scope, the graph-visible count against the SQL `EXISTS projects_samples` count; the two POC test accounts are not checked |
| `7.metadata.*` (gate G 7) | a metadata hash over 1,000 random samples against the projection of their `json_metadata` |
| `8.*` (gate G 8) | constraints and indexes present and ONLINE, no label collisions, one GraphMeta at the writer's version |
| `drift.freshness.full_sync`, `drift.freshness.delta`, `drift.outbox.oldest_pending` | S7's thresholds, from `graph_sync_run` and `graph_sync_outbox` |

Exit status: 0 no drift, 1 drift, 2 refused (the live-graph rule), 3 the check could not complete. The result goes
to `graph_sync_run.drift_json`, to stdout with `--json`, and to `drift.json` in `--run-dir`. Cost: about gate G's (one
MySQL scan of `samples`, one stream of the DERIVED_FROM edges and one of the Sample ids), about a minute at 1.08M.

## 8. The status endpoint: `GET /nextseek_api/admin/graph-sync/status/`

A native, read-only ViewSet (`nextseek_api/services/graph_sync_status.py`) that reads only the two dmac tables: no
Neo4j, no SEEK, no MySQL `samples`, so it is cheap and safe on every box.

- Authentication `CsrfExemptSessionAuthentication`, `BasicAuthentication`; permission `IsAuthenticated` plus
  `IsDjangoSuperuser` (401, 403).
- 200: `generated_at`; `last_runs` (the latest row of each kind); `freshness` (S7, per job); `outbox` (pending, dead,
  oldest pending and its age); `drift` (the latest drift run's result, or null).
- 503 with the JSON:API error envelope when the tables cannot be read (an unmigrated database).
- Router prefix `admin/graph-sync` with one `status` action and no list route, so the API root does not advertise it
  (the root lists only prefixes with a list route) and `ci/smoke/test_health.py` keeps its expected set. Declared in
  `ci/routes.py` for `local,dev` under the superuser client, like the other superuser GETs; `OWNED_ROUTE_COUNT`
  moves from 169 to 170.

## 9. The writer registry gate (CI1)

`ci/writers.py` (standard library only, like `ci/routes.py`) declares every place in the tree that writes a watched
table; `ci/gate/writer_scan.py` finds them; `ci/gate/test_writer_registry.py` diffs the two in both directions and
blocks. Watched tables: `samples`, `sample_attributes`, `sample_types`, `assay_assets`, `projects_samples`,
`group_memberships`.

A `Writer` names a site (`path::Qualified.name`, or a `.sql` file), the watched tables it writes, how it writes
(`sql`, `orm`, `dbtable`, `seek_client`, `rails_runner`, `sql_file`), and exactly one of:
- `hook`: the sync hook it calls, and optionally `hook_site`, the function where that call must appear when it is not
  the site itself (a view that wraps a legacy table class). The gate parses that function and fails when the call is
  missing.
- `reconcile`: a category code, never free text (the repository is public):

| Code | Means |
|---|---|
| `RECONCILE_HOOK_PENDING` | a Django hook site exists and B5 adds the hook; until then the weekly full sync and the drift check cover it |
| `RECONCILE_RAILS` | SEEK Rails commits the write (a proxy call or the Rails runner); Django sees only the response, and Rails' own follow-up jobs are invisible |
| `RECONCILE_OPERATOR` | an operator-run command, script or SQL file, never on a request path |
| `RECONCILE_DEAD` | code that no URL or command reaches |

What the scanner finds (`nextseek_api`, `seek`, `dmac`, `api_app`, `NessieAI`, `scripts`, `startup`; tests and
migrations excluded):
1. **SQL in Python strings**: `INSERT INTO`, `REPLACE INTO`, `UPDATE ... SET`, `DELETE FROM` and `TRUNCATE` on a
   watched table, schema prefix allowed, in plain, implicitly concatenated, `+`-joined and f-strings; docstrings are
   skipped. The site is the innermost enclosing function.
2. **Legacy table classes**: a class that sets `self.tablename` to a watched table, and every class it is composed of,
   is bound to that table; a method of one that calls `storeOneRecord`, `deleteOneRecord`, `deleteRecordsConstraint`
   or `processRecords(..., "save" | "delete")` on `self` is a site, and so is a function that makes an instance and
   calls one of those on it. The generic record layer (`dmac/dbtable.py`, `dmac/dbconnection.py` and the connection
   classes under it) is infrastructure, not a site.
3. **ORM writes** on the models whose `db_table` is watched: `Model.objects...create|update|delete|bulk_create|
   bulk_update|get_or_create|update_or_create`, and `save()` or `delete()` on a variable assigned from the model.
4. **SEEK client writes**: a call to any `create_*`, `update_*` or `delete_*` method that `SeekAPIClient` defines.
5. **Rails runner**: a call to `run_seek_rails_runner`.
6. **SQL files** (`*.sql`, not dumps) that write a watched table.

Deliberately out of scope for increment 1 (plan, CI1 part 2): a write on a table whose name is only in a variable
outside the legacy class pattern, and the rule that every `UPDATE` of `samples` sets `updated_at` or is registered as
hook-only.

## 10. Hook placement (B5, planned)

A hook is one call, `graph_sync.hooks.enqueue(kind, key)`, made after the writer's own commit, outside any SEEK
transaction, that writes one outbox row in dmac and never raises into the writer (a failure is logged; the weekly full
sync covers the lost row). The table below is the recommendation; the registry records the same sites today with
`RECONCILE_HOOK_PENDING`.

| Writer (recon number) | Hook site | Enqueues |
|---|---|---|
| Native attribute API (C1, W9) | `nextseek_api/attributes/executor.py::DjangoExecutionServices.record_commit`, which the reconciled-replay path also reaches; an ambiguous recovery stays reconciler-only | `catalog type:<id>`, `samples_of_type type:<id>` |
| Legacy attribute editor (C2, W10) | end of `seek/views/samples.py::sampleAttributeSave` and `sampleAttributeDelete`, after the table class reports success; or retired (S5) | `catalog type:<id>`, `samples_of_type type:<id>` |
| SEEK sample-type proxy (C3, W7) | `nextseek_api/services/sample_types.py::SampleTypeProxyViewSet.create` and `partial_update`, after a 2xx, with the id Rails returns; Rails `refresh_samples` stays reconciler-only | `catalog type:<id>`, `samples_of_type type:<id>` (a rename relabels every sample) |
| Clade admin (C7) | end of `seek/views/admin.py::cladeSave`, `cladeDelete`, `cladeSampleTypesSave`, `cladesSyncSampleTypes` | `catalog *` |
| Batch upload (C8, W1, W2) | after stage 5 commits, per chunk of sample ids; stage 6 stops writing Sample and SampleType properties (the drain writes them with the POC's projection) and keeps only what the projection does not own | `samples sample:<id>` per id |
| Orphan resolution (W3) | after its MySQL update, per resolved child | `samples sample:<id>` |
| Legacy sheet upload and its update paths (W4) | after `_storeSample` and the update paths commit | `samples sample:<id>` |
| Legacy sample delete (W5) | after the delete transaction; its own swallowed Neo4j delete goes | `delete sample:<id>` |
| SEEK sample proxy (W6) | `SampleProxyViewSet.create`, `partial_update`, `destroy` after a 2xx | `samples` or `delete sample:<id>` |
| ISA proxies (W8) | studies, investigations and projects `create` and `partial_update` after a 2xx | `isa *` (a small full diff of those nodes) |
| Assay registration (W11) | after its transaction commits, beside its own label recompute | `samples sample:<id>` for the samples whose links changed |
| Users admin (W13) | after the Rails runner returns | `membership *` |
| Rails UI and REST (C4, W16), context seed and hand SQL (C5, C9), operator scripts (W17) | none possible | B6's reconciler and the weekly full sync |

The drain handles `samples` and `delete` by id through the same projection and writer as the delta, `samples_of_type`
by streaming the type's ids, `catalog` with `run.catalog_sync`, `membership` with
`writer.write_people_and_memberships`, and `isa` with the POC's project and investigation writers.

## 11. What follow-up 1 and the POC need to know

- The command keeps `--full`, `--catalog` and `--verify` and every existing option. `--full` and `--catalog` will also
  write a `graph_sync_run` row when the dmac table exists (S9); `--no-record` skips it. New modes: `--loop`,
  `--once`, `--delta`, `--drift`; new options: `--interval`, `--run-root`, `--no-record`, `--no-bootstrap`.
- `run.full_sync`, `run.catalog_sync` and `verify.gate_g` are unchanged. `writer` and `sources` gain functions and
  change none.
- Once 0021 lands, a migration added on another branch must depend on `0021_graph_sync_outbox_and_run`.
- Nessie reads nothing new. Its catalog reader (A2) can trust `GraphMeta.catalog_hash` to move when the catalog does,
  within a night today and within seconds once B5 lands.

## 12. Increments

1. **Planned, not built:** the two tables, the loop, the delta, the drift check, the status endpoint, the writer registry gate
   (all sites registered, no hooks yet), the entrypoint line.
2. **Hooks (B5)** and the drain's hook kinds, the write-lane hook test (CI5), and S5's retirement of the legacy
   editor.
3. **Reconciler and indexes (B6, B7):** a cheap catalog-hash poll every few minutes (catches Rails and hand SQL on the
   catalog), a `projects_samples` and membership full diff nightly, `idx_updated_id` and
   `idx_samples_sample_type_id` managed on `rebuild`, per-type `catalog_hash` on SampleType nodes (B2).
4. **Statistics and gates (B3, CI2 to CI4, CI6):** per-project `USED_IN` and `top_values` with the sensitive flag; the
   drift gate after every rebuild (`startup/ci/runner.py` runs `graph_sync --drift --json </dev/null` in the app
   container, or a smoke test reads the status endpoint); catalog coverage, report-only first; Nessie context gates;
   graph_search in both read-safe lists.
5. **Cleanups (B9):** `cladeSave` writes a clade's order into its colour; `syncSampleTypes` inserts null clades; the
   `is_deprecated` docstring; `entity_tree` joins the empty `dmac.assays`.

## 13. Risks

- **Memory.** A child full sync holds per-sample indexes for about 1.08M samples inside the app container's cap. It
  streams sample data, and it ends when the run ends, but it competes with the web server while it runs.
- **Staleness until B5.** Without hooks, a write reaches the graph by the next night at best, and a hook-only writer
  (the attribute API, Rails `refresh_samples`) by the next full sync, up to 7 days. The drift check makes that
  visible; it does not shorten it.
- **The dev box delta** is a full scan per page until B7.
- **A single unprojectable sample** makes the weekly full sync refuse (the POC's preflight rule); the delta skips it
  and counts it. The status endpoint shows both.
- **The scanner is a heuristic.** It finds today's shapes; a write through a variable table name outside the legacy
  class pattern would pass unseen. The two-way diff at least makes every listed site answer for itself.
