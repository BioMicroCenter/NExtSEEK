# Working in `nextseek_api/batch_upload/`

## Invariants

Each of these is load-bearing. Breaking one corrupts data, leaks access, or silently loses
lineage — none of them fails loudly.

- **UID minting is serialized by a MySQL named lock, and only for as long as it takes to
  read the maximum.** `nextseek_api/batch_upload/uid_gen.py:159-166` takes one lock per UID
  prefix with a ten-second timeout, and `nextseek_api/batch_upload/uid_gen.py:175` releases
  it before any row is inserted — the INSERT is stage 5, in a later connection
  (`nextseek_api/batch_upload/orchestrator.py:375-381` closes stage 1.5's). Two jobs whose
  UID_GEN both finish before either commits therefore read the same maximum
  (`nextseek_api/batch_upload/uid_gen.py:168`) and mint the same identifiers. Widening or
  removing that lock turns a rare collision into the normal case.
- **Every key in `json_metadata` must already exist as an attribute of the row's
  SampleType.** There is no skip list, not even for the SEEK-conventional UID, Parent and
  Protocol keys — `nextseek_api/batch_upload/transform.py:75-88` rejects the row on the
  first undeclared key. Adding a column to a curator's sheet before adding the attribute to
  the sample type fails every row that carries it.
- **New sample policies are created private and access is granted separately.**
  `nextseek_api/batch_upload/policies.py:71-73` writes `access_type = 0`, and the upsert
  path puts touched policies back to that value at
  `nextseek_api/batch_upload/update.py:300-306`. Changing that constant silently publishes
  every sample a batch touches to the whole project.
- **Project permission granting is on by the pipeline's default and off by the class's.**
  `nextseek_api/batch_upload/config.py:35-37` defaults the switch to true, and
  `nextseek_api/batch_upload/insert.py:208-213` is the one place that passes it through
  along with the project as contributor; the class itself defaults to disabled
  (`nextseek_api/batch_upload/permissions.py:22`) and returns zero when it is
  (`nextseek_api/batch_upload/permissions.py:38-39`). A second call site that omits that
  keyword grants nothing and leaves every sample it wrote private to its policy.
- **The caller's SEEK identity is resolved server-side and a non-admin cannot override it.**
  `nextseek_api/batch_upload/views.py:779-794` accepts a supplied `person_id` only from
  staff or a superuser, and `nextseek_api/batch_upload/views.py:799-800` refuses the request
  outright when no identity resolves rather than falling back to the Django primary key.
  Accepting the client value would attribute samples to any person id a caller names.
- **Job ownership is the only authorization on status, cancel and summary.**
  `nextseek_api/batch_upload/views.py:104-111` turns a non-owner into a 404 by consulting
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
  `nextseek_api/batch_upload/orchestrator.py:464` calls it once per batch rather than per
  row. The caches are plain module dicts
  (`nextseek_api/batch_upload/prefetch.py:18-24`), so any in-process invalidation hook
  looks correct on one worker and leaves the others rejecting rows against a stale schema.
- **Parallel insertion is disabled whenever a run is resuming.**
  `nextseek_api/batch_upload/orchestrator.py:695-698` requires both a level of at least
  `PARALLEL_THRESHOLD` rows and a null resume UID, because the checkpoint file is a single
  append-only sequence (`nextseek_api/batch_upload/checkpoint.py:11-18`) whose last line is
  taken as the high-water mark (`nextseek_api/batch_upload/checkpoint.py:38-51`). Threads
  appending out of order would make a resume skip rows that were never written.
- **One definition of Protocol-to-SOP resolution, shared by three call sites.**
  `nextseek_api/batch_upload/helpers.py:53-74` records why: resolving by URL alone wrote a
  null protocol on nearly every upload, and the three-format rule reproduced the stored
  value on 200,000 of 200,000 sampled edges. A second copy of that logic reintroduces the
  null.

## Landmines

- **`config_overrides` travels from the request body into the pipeline config unfiltered.**
  `nextseek_api/batch_upload/views.py:241` reads it, `nextseek_api/batch_upload/views.py:306`
  forwards it, and `nextseek_api/batch_upload/tasks.py:40` splats it into the constructor,
  so any authenticated caller can set any tunable the constructor pulls out of `overrides`
  (`nextseek_api/batch_upload/config.py:20-59`) — the permission switch and its access type
  among them. Both of those consult an environment variable first
  (`nextseek_api/batch_upload/config.py:35-40`), so an instance that sets those variables is
  covered and one that leaves them unset is not. Adding a tunable here adds a request
  parameter whether you meant to or not.
- **A Neo4j failure does not fail the job.**
  `nextseek_api/batch_upload/orchestrator.py:875-876` logs the exception and continues, so
  the task still reports SUCCESS with the SQL rows committed and the graph never written.
  The only trace is a warning in the worker log.
- **A `DERIVED_FROM` row whose child or parent node is absent is dropped by Cypher with no
  error.** The two MATCHes produce no rows, and the docstring at
  `nextseek_api/batch_upload/neo4j_sync.py:164-177` records roughly 90,000 edges found in
  MySQL and missing from the graph for exactly this reason. The shortfall is now counted
  and logged (`nextseek_api/batch_upload/neo4j_sync.py:209-215`) and
  `nextseek_api/batch_upload/neo4j_sync.py:220` names the culprits, but nothing repairs
  them: treat a nonzero drop count as data loss, not noise.
- **Nothing under `MEDIA_ROOT` survives a container rebuild.**
  `dmac/settings.py:95` puts it at a path the `nextseek` service never mounts — a
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
- **`nextseek_api/batch_upload/tests/test_neo4j_integration.py:31` does not skip when Neo4j
  is merely unreachable.** It skips only on missing configuration, and
  `dmac/test_settings.py:51-55` supplies a complete but fictional Neo4j, so under the test
  settings the driver retries until it gives up: measured 2026-09-03, 3 errors and about
  125 of the suite's 128 seconds. Pass `--ignore` for that module unless a real graph is
  reachable from wherever you run it.
- **`nextseek_api/batch_upload/policies.py:76-79` changes the SQL it issues when pytest is
  running**, taking the non-RETURNING branch and, at
  `nextseek_api/batch_upload/policies.py:107-113`, handing back synthetic ids. A green test
  over the policy path is therefore not evidence about the statement production runs.
- **The job result carries at most 50 errors.**
  `nextseek_api/batch_upload/orchestrator.py:914-917` slices the collector before returning,
  and the status action hands that stored result straight back
  (`nextseek_api/batch_upload/views.py:551-553`), while
  `nextseek_api/batch_upload/report.py:226` builds a summary row for every input row.
  Debugging a large failed upload from the JSON alone will silently miss errors; read the
  CSV.
- **`neo4j_only` mode trusts the caller.** It skips INSERT entirely
  (`nextseek_api/batch_upload/orchestrator.py:626-631`) and turns any UID absent from
  `samples` into a failed row (`nextseek_api/batch_upload/orchestrator.py:102-105`), so a
  sheet with regenerated UIDs produces a run that reports failures for every row and writes
  nothing.
- **`nextseek_api/batch_upload/tests/fixtures/wave3_default_mode.xlsx` is read from outside
  this boundary** by `ci/smoke/test_flows.py:220-223`, which skips rather than fails when it
  is absent (`ci/smoke/test_flows.py:224-225`). Renaming or moving it removes a smoke check
  without turning anything red.
- **Four files here still tell you to run them from a path that is not in the image.**
  `nextseek_api/batch_upload/scripts/backfill_parent_titles.py:12`,
  `nextseek_api/batch_upload/scripts/backfill_parent_title_hashes.py:12`,
  `nextseek_api/batch_upload/tests/fixtures/_generate_wave3_fixtures.py:15` and
  `nextseek_api/batch_upload/tests/WAVE3_LIVE_TESTING.md:43` all name an interpreter under
  `/opt/NExtSEEK`, while the image puts the application and its virtualenv under `/app`
  (`scripts/run_tests.sh:45-47`, `docker-compose.yml:354`). Copy-pasting any of those four
  commands fails on a missing interpreter.
- **`nextseek_api/batch_upload/tests/WAVE3_LIVE_TESTING.md:1` is half true, which is worse
  than plainly wrong.** Every environment variable it documents is still read by the module:
  the two `WAVE3_*` names at
  `nextseek_api/batch_upload/tests/test_identity_drift_integration.py:51-52` and the four
  `SPIKE_DB_*` overrides at
  `nextseek_api/batch_upload/tests/test_identity_drift_integration.py:57-60`, so the file
  reads as maintained, while the command that would run the lane
  (`nextseek_api/batch_upload/tests/WAVE3_LIVE_TESTING.md:59`) uses the missing interpreter
  named above. Check every other claim in it against source before acting on it.
- **`nextseek_api/batch_upload/neo4j_sync.py:1849` is the last line of the largest module
  here**, and a `grep` of its top-level `def` lines on 2026-09-03 counted 31, among them 8
  bulk merges from `nextseek_api/batch_upload/neo4j_sync.py:99` to
  `nextseek_api/batch_upload/neo4j_sync.py:501`, 2 read-only endpoint audits
  (`nextseek_api/batch_upload/neo4j_sync.py:220` and
  `nextseek_api/batch_upload/neo4j_sync.py:334`) and 2 edge deleters. Only the narrower
  deleter is wired: `nextseek_api/batch_upload/neo4j_sync.py:1767` calls it, and a grep of
  the whole repo for the wider one at `nextseek_api/batch_upload/neo4j_sync.py:529` finds
  its own definition, one cross-reference in a neighbouring docstring
  (`nextseek_api/batch_upload/neo4j_sync.py:537`) and calls from
  `nextseek_api/batch_upload/tests/test_neo4j_sync.py:622` and nowhere else. Reach for the
  more obvious name and you will delete every lineage edge a sample has instead of the
  stale ones.

## Test command

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek sh -c \
  'cd /app && uv run --no-sync python -m pytest nextseek_api/batch_upload/tests/ \
   --no-migrations -q -p no:randomly \
   --ignore=nextseek_api/batch_upload/tests/test_neo4j_integration.py'
```

Ran 2026-09-03: 1223 passed, 26 skipped, 3.82s. Drop the `--ignore` only when a reachable
Neo4j is configured; see the landmine above for what happens otherwise. Never widen the path
to the whole `nextseek_api/` tree in one go — that pulls in hundreds of unrelated
environmental failures that are not regressions.

## See also

- See `nextseek_api/batch_upload/README.md` for the stage table, the HTTP actions, the table
  set written, and the dependency map in both directions.
- See `nextseek_api/cc_assistant/CLAUDE.md` for the other subsystem that registers tasks on
  this package's Celery application.
- See the repo-root `CLAUDE.md` for the stack layout, the rebuild commands, and the
  in-container test recipe this command specializes.
- See `docker/scripts/entrypoint.sh:67-70` for how the worker that runs these tasks is
  started alongside the web server.
- See `docs/endpoint-authorization-register.md:229-231` for the register entry covering
  these endpoints' authorization posture.
