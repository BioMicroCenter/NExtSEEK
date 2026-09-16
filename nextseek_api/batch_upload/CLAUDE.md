# Working in `nextseek_api/batch_upload/`

## Invariants

Each of these is load-bearing. Breaking one corrupts data, leaks access, or silently loses
lineage, and none of them fails loudly.

- **UID minting is serialized by a MySQL named lock, and only for as long as it takes to
  read the maximum.** `nextseek_api/batch_upload/uid_gen.py:159-166` takes one lock per UID
  prefix with a ten-second timeout, and `nextseek_api/batch_upload/uid_gen.py:175` releases
  it before any row is inserted: the INSERT is stage 5, in a later connection
  (`nextseek_api/batch_upload/orchestrator.py:392-398` closes stage 1.5's). Two jobs whose
  UID_GEN both finish before either commits therefore read the same maximum
  (`nextseek_api/batch_upload/uid_gen.py:168`) and mint the same identifiers. Widening or
  removing that lock turns a rare collision into the normal case.
- **Every key in `json_metadata` must already exist as an attribute of the row's
  SampleType.** There is no skip list, not even for the SEEK-conventional UID, Parent and
  Protocol keys: `nextseek_api/batch_upload/transform.py:75-88` rejects the row on the
  first undeclared key. Adding a column to a curator's sheet before adding the attribute to
  the sample type fails every row that carries it.
- **New sample policies are created private and access is granted separately.**
  `nextseek_api/batch_upload/policies.py:71-73` writes `access_type = 0`, and the upsert
  path puts touched policies back to that value at
  `nextseek_api/batch_upload/update.py:300-306`. Changing that constant silently publishes
  every sample a batch touches to the whole project.
- **Project permission granting is on by the pipeline's default and off by the class's.**
  `nextseek_api/batch_upload/config.py:35-37` defaults the switch to true, and
  `nextseek_api/batch_upload/insert.py:272-276` is the one place that passes it through
  along with the project as contributor; the class itself defaults to disabled
  (`nextseek_api/batch_upload/permissions.py:22`) and returns zero when it is
  (`nextseek_api/batch_upload/permissions.py:38-39`). A second call site that omits that
  keyword grants nothing and leaves every sample it wrote private to its policy.
- **The caller's SEEK identity is resolved server-side, and only a superuser may override
  it.** `nextseek_api/batch_upload/views.py:771-776` accepts a supplied `person_id` from a
  superuser alone, never from staff, because the SEEK login sets `is_staff` on every
  account; `nextseek_api/batch_upload/views.py:796-797` refuses the request outright when no
  identity resolves rather than falling back to the Django primary key. Accepting the client
  value would attribute samples to any person id a caller names.
- **Job ownership is the only authorization on status, cancel and summary.**
  `nextseek_api/batch_upload/views.py:105-112` turns a non-owner into a 404 by consulting
  the per-user index file (`nextseek_api/batch_upload/job_index.py:80-94`). A job id is a
  bare Celery UUID, so losing that check exposes every other user's progress metadata and
  summary CSV to anyone who has one.
- **The validate endpoint must stay free of writes.** Grepping the eleven modules stages 0
  to 4 use for `INSERT`, `UPDATE ` and `DELETE FROM` turns up exactly one statement,
  `nextseek_api/batch_upload/prefetch.py:131`, and it is gated at
  `nextseek_api/batch_upload/prefetch.py:123`; the cache is deliberately not updated for
  links that were not created (`nextseek_api/batch_upload/prefetch.py:141-145`) so a later
  real upload still makes them. A second write added to those stages without the same gate
  turns a dry run into a mutation.
- **A blocking verdict is read off severities, not off the error list being empty.**
  `nextseek_api/batch_upload/validation.py:88-91` inspects the collector, and anything not
  in the two-member non-blocking set blocks, including a severity added later
  (`nextseek_api/batch_upload/validation.py:32-43`). Inverting that to an allowlist would
  let an unclassified new error type pass validation.
- **Attribute-set caches are invalidated by a database generation stamp, never by a hook.**
  `nextseek_api/batch_upload/prefetch.py:243-249` reads a count and a maximum timestamp, and
  `nextseek_api/batch_upload/orchestrator.py:489` calls it once per batch rather than per
  row. The caches are plain module dicts
  (`nextseek_api/batch_upload/prefetch.py:18-24`), so any in-process invalidation hook
  looks correct on one worker and leaves the others rejecting rows against a stale schema.
- **Parallel insertion is disabled whenever a run is resuming.**
  `nextseek_api/batch_upload/orchestrator.py:810-811` requires both a level of at least
  `PARALLEL_THRESHOLD` rows and a null resume UID, because the checkpoint file is a single
  append-only sequence (`nextseek_api/batch_upload/checkpoint.py:11-18`) whose last line is
  taken as the high-water mark (`nextseek_api/batch_upload/checkpoint.py:38-51`). Threads
  appending out of order would make a resume skip rows that were never written.
- **One definition of Protocol-to-SOP resolution, shared by three call sites.**
  `nextseek_api/batch_upload/helpers.py:53-74` records why: resolving by URL alone wrote a
  null protocol on nearly every upload, and the three-format rule reproduced the stored
  value on 200,000 of 200,000 sampled edges. A second copy of that logic reintroduces the
  null.
- **The outbox row stage 5 writes is the record of what the graph owes this job; stage 6 is
  an optimisation on top of it.** Each batch inserts its committed ids into
  `graph_sync_outbox` on its own connection, inside its own transaction
  (`nextseek_api/batch_upload/insert.py:58`), so a batch that rolls back leaves no row and a
  batch that commits cannot lose one. Stage 6 syncs those ids inline
  (`nextseek_api/batch_upload/orchestrator.py:637`) and closes the rows only when the sync
  returned `ok` (`nextseek_api/batch_upload/orchestrator.py:612`). Writing the graph from
  here without the row, or closing the rows without reading the status, turns a cancelled or
  crashed job into samples that no search can find and nothing will repair before the next
  nightly sync.

## Landmines

- **`config_overrides` travels from the request body into the pipeline config unfiltered.**
  `nextseek_api/batch_upload/views.py:241` reads it, `nextseek_api/batch_upload/views.py:305`
  forwards it, and `nextseek_api/batch_upload/tasks.py:40` splats it into the constructor,
  so any authenticated caller can set any tunable the constructor pulls out of `overrides`
  (`nextseek_api/batch_upload/config.py:20-59`): the permission switch and its access type
  among them. Both of those consult an environment variable first
  (`nextseek_api/batch_upload/config.py:35-40`), so an instance that sets those variables is
  covered and one that leaves them unset is not. Adding a tunable here adds a request
  parameter whether you meant to or not.
- **A graph failure does not fail the job, and the job reports it in one word.**
  `nextseek_api/batch_upload/orchestrator.py:606-609` logs the exception and returns a
  status, so the task still reports SUCCESS with the SQL rows committed. What tells you
  which happened is the `graph:` line in the totals and the summary CSV: `synced (N)` means
  those samples are in the graph, `pending (N)` means the outbox rows are still owed and the
  sync loop holds them. A graph not yet at schema 1.2, a busy graph-write lock, a Neo4j that
  is down and no Neo4j configured at all all read as `pending`, so a run of `pending` jobs
  is a question about the loop, not about this package.
- **A parent this sheet does not resolve is still reported here, but the edge is no longer
  this package's to write.** `nextseek_api/batch_upload/neo4j_sync.py:689`, called at
  `nextseek_api/batch_upload/neo4j_sync.py:1062`, puts every child whose parent could not be
  resolved into the job's errors. The edge itself is created by graph_sync from what MySQL's
  parent tokens declare, and gate G check 1 fails on a declared pair the graph lacks, so a
  parent that arrives in a later upload is repaired by orphan resolution and the nightly
  sync instead of being dropped by a Cypher MATCH that found nothing. Reading this reporter
  as the writer sends you looking for a statement that is gone.
- **Nothing under `MEDIA_ROOT` survives a container rebuild.**
  `dmac/settings.py:95` puts it at a path the `nextseek` service never mounts: a
  case-insensitive grep for `media` over the whole of `docker-compose.yml` matched nothing on
  2026-09-03, and the service's mount list at `docker-compose.yml:25-53` is nine entries
  covering other paths.
  Rebuilding therefore destroys the job index, the uploaded workbooks, the checkpoints and
  every downloadable summary at once, and the ownership check above then 404s a user on
  their own jobs.
- **`nextseek_api/batch_upload/job_index.py:12-14` reads `MEDIA_ROOT` at import time.**
  Overriding the setting later, in a test or at runtime, does not move the directory; that
  is what the explicit `jobs_dir` parameter at
  `nextseek_api/batch_upload/job_index.py:20-23` exists for. A test that overrides settings
  and omits it writes into the real index.
- **The ownership index expires on a seven-day lazy sweep.**
  `nextseek_api/batch_upload/job_index.py:15-18` sets the window and
  `nextseek_api/batch_upload/job_index.py:64-68` rewrites the file without the expired
  entries whenever the list endpoint runs. After that, an older job the user still holds the
  id for answers 404 rather than 200.
- **`nextseek_api/batch_upload/prefetch.py:26` builds a config object at import.** Every
  cache size is therefore frozen at the environment as it stood when the module was first
  imported; changing those variables in a running worker has no effect.
- **Importing `nextseek_api/batch_upload/celery_app.py:12-16` calls `django.setup()` as a
  side effect**, and `nextseek_api/batch_upload/celery_app.py:55-56` then imports two
  modules from a sibling package purely to register their tasks. The import is expensive
  and can fail: a consumer already wraps it in a bare `except` with a Celery fallback
  (`nextseek_api/cc_assistant/cc_upload_tasks.py:18-20`). Never import it for a cheap look
  at a task name.
- **`nextseek_api/batch_upload/policies.py:76-79` changes the SQL it issues when pytest is
  running**, taking the non-RETURNING branch and, at
  `nextseek_api/batch_upload/policies.py:107-113`, handing back synthetic ids. A green test
  over the policy path is therefore not evidence about the statement production runs.
- **The job result carries at most 50 errors.**
  `nextseek_api/batch_upload/orchestrator.py:1021` slices the collector before returning,
  and the status action hands that stored result straight back
  (`nextseek_api/batch_upload/views.py:551-553`), while
  `nextseek_api/batch_upload/report.py:206` builds a summary row for every input row.
  Debugging a large failed upload from the JSON alone will silently miss errors; read the
  CSV.
- **`neo4j_only` mode trusts the caller.** It skips INSERT entirely
  (`nextseek_api/batch_upload/orchestrator.py:739-745`) and turns any UID absent from
  `samples` into a failed row (`nextseek_api/batch_upload/orchestrator.py:105-119`), so a
  sheet with regenerated UIDs produces a run that reports failures for every row and writes
  nothing. The UIDs it does resolve get an outbox row like any other batch
  (`nextseek_api/batch_upload/orchestrator.py:135`) and are synced by stage 6.
- **`nextseek_api/batch_upload/tests/fixtures/wave3_default_mode.xlsx` is read from outside
  this boundary** by `ci/smoke/test_flows.py:220-223`, which skips rather than fails when it
  is absent (`ci/smoke/test_flows.py:224-225`). Renaming or moving it removes a smoke check
  without turning anything red.
- **One file here still tells you to run it from a path that is not in the image.**
  `nextseek_api/batch_upload/tests/fixtures/_generate_wave3_fixtures.py:15` names an
  interpreter under `/opt/NExtSEEK`, while the image puts the application and its
  virtualenv under `/app` (`scripts/run_tests.sh:45-47`, the `nextseek` healthcheck in
  `docker-compose.yml`). Copy-pasting that command fails on a missing interpreter.
  `nextseek_api/batch_upload/tests/WAVE3_LIVE_TESTING.md` already runs its lane with
  `/app/.venv/bin/python` inside the `nextseek` container.
- **`nextseek_api/batch_upload/neo4j_sync.py` writes nothing to Neo4j, although its name
  still says it does.** `upload_all`, the bulk merges, the constraint and index DDL, both
  `DERIVED_FROM` deleters and the two read-only endpoint audits were deleted when stage 6
  moved to `nextseek_api/graph_sync/targeted.py`. What stays reads MySQL and builds
  payloads, and `nextseek_api/graph_sync/labels.py` and `nextseek_api/graph_sync/sources.py`
  are checked against it on the same fixtures, so it is the reference for a lineage edge's
  labels rather than a second writer. Reach in here for a writer and you will find a
  payload builder no graph ever sees.

## Test command

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek sh -c \
  'cd /app && uv run --no-sync python -m pytest nextseek_api/batch_upload/tests/ \
   --no-migrations -q -p no:randomly'
```

Never widen the path to the whole `nextseek_api/` tree in one go: that pulls in hundreds of
unrelated environmental failures that are not regressions.

## See also

- See `nextseek_api/batch_upload/README.md` for the stage table, the HTTP actions, the table
  set written, and the dependency map in both directions.
- See `nextseek_api/graph_sync/README.md` for what stage 6 calls, the outbox kinds and keys,
  the graph-write lock, and the loop that drains what stage 6 could not do.
- See `NessieAI/cc/CLAUDE.md` for the other subsystem that registers tasks on this
  package's Celery application, through its Django shell `nextseek_api/cc_assistant/`.
- See the repo-root `CLAUDE.md` for the stack layout, the rebuild commands, and the
  in-container test recipe this command specializes.
- See `docker/scripts/entrypoint.sh:67-70` for how the worker that runs these tasks is
  started alongside the web server.
- See `docs/endpoint-authorization-register.md:229-231` for the register entry covering
  these endpoints' authorization posture.
