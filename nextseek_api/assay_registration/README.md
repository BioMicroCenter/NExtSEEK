# `nextseek_api/assay_registration/`

## What this is

Batch registration of samples as members of SEEK assays: three HTTP routes, a durable job
row, a drain worker, and a graph sync it queues rather than writes. It is a plain subpackage of the
`nextseek_api` app rather than a Django app of its own: its single ORM model declares
`app_label = "nextseek_api"` (`nextseek_api/assay_registration/models_db.py:45-46`) and its
table is created by a migration in the parent
(`nextseek_api/migrations/0020_assayregistrationjob.py:13-15`) that chains onto the parent's
merge head (`nextseek_api/migrations/0020_assayregistrationjob.py:9-11`).

Counted on 2026-09-03 by walking this directory: 23 Python files and 6,182 lines, of which
14 files and 2,008 lines sit outside `tests/` and 9 files and 4,174 lines sit inside it. Two
thirds of the package is its own suite.

It exists because a sheet-driven predecessor could not tell the truth about what it had
done, and two module docstrings carry the whole argument. The resolver's records a
production run where a uid matching two `samples` rows resolved to `None`, raised
`TypeError`, and abandoned the batch with 1,220 rows already committed
(`nextseek_api/assay_registration/resolver.py:7-12`). The executor's records that the legacy
writer set a success flag it never refreshed from the database, so a hard insert failure was
printed to the operator's feedback workbook as a success
(`nextseek_api/assay_registration/executor.py:3-11`). Both fixes are structural: resolve and
plan the entire batch before opening a write
(`nextseek_api/assay_registration/planner.py:1-6`), then derive every reported row status
from a read-back of the pairs actually intended
(`nextseek_api/assay_registration/executor.py:13-15`).

The API is additive and removal is inexpressible, which the package treats as a property to
be defended rather than a description. The request models forbid unknown keys
(`nextseek_api/assay_registration/schemas.py:74` and
`nextseek_api/assay_registration/schemas.py:96`), there is no delete verb and no
complete-list array whose omissions could imply removal
(`nextseek_api/assay_registration/schemas.py:3-8`), and the one write call the executor makes
contains no destructive statement (`nextseek_api/assay_registration/executor.py:17-19`).

## Surface

Two shapes, worked out separately. Outward the surface is a **URL tree plus one management
command**; inward it is a **fixed pipeline of modules**, each stage handing a dataclass to
the next. Both lists below are derived from the files themselves.

**The published routes.** One ViewSet, registered from the parent at
`nextseek_api/urls.py:26-27` under the basename `assay-registrations`, reached through the
re-export at `nextseek_api/views.py:51`. This table locates the handlers; the behaviour
claims are the prose around it.

| Route under `/nextseek_api/` | Method | Handler | Prose constant |
|---|---|---|---|
| `assay-registrations/` | POST | `nextseek_api/assay_registration/views.py:193` | `nextseek_api/endpoint_descriptions.py:1123` |
| `assay-registrations/jobs/{job_id}/` | GET | `nextseek_api/assay_registration/views.py:214` | `nextseek_api/endpoint_descriptions.py:1173` |
| `assay-registrations/jobs/{job_id}/cancel/` | POST | `nextseek_api/assay_registration/views.py:233` | `nextseek_api/endpoint_descriptions.py:1190` |

Both job routes are `detail=False` actions carrying their own `url_path` regex, at
`nextseek_api/assay_registration/views.py:213` and
`nextseek_api/assay_registration/views.py:232`.

**The authentication stack, which is unusual and deliberately so.** Session authentication
comes first, in the CSRF-exempt subclass defined at
`nextseek_api/authentication.py:15`, and Basic authentication second
(`nextseek_api/assay_registration/views.py:122`). DRF asks only the first authenticator for a
challenge header and session auth returns none, which would collapse an anonymous 401 into
the same 403 an authenticated non-superuser gets; the override at
`nextseek_api/assay_registration/views.py:145-149` asks every authenticator instead and finds
Basic's challenge. A second override re-shapes both auth failures into this endpoint's own
error envelope so the published schema is not wrong about its own responses
(`nextseek_api/assay_registration/views.py:160-168`).

**The pipeline, in order.** Each module is one stage and the stages do not reach past each
other.

- `resolver.py` turns submitted rows into `(sample_id, assay_id, project_id)` or a typed
  error, in a fixed number of batch queries over the whole submission
  (`nextseek_api/assay_registration/resolver.py:243-244`). It counts uid matches rather than
  testing existence (`nextseek_api/assay_registration/resolver.py:68-71`), reads the SEEK and
  NExtSEEK schema names from settings aliases rather than hardcoding them
  (`nextseek_api/assay_registration/resolver.py:35-40`), chunks every `IN` clause at 1000
  (`nextseek_api/assay_registration/resolver.py:30-32`), and resolves an assay title through
  `internal_assays` into the sample's own project
  (`nextseek_api/assay_registration/resolver.py:147-152`). A title that reaches several assays
  of the sample's project resolves to the one the sample is already in, else to the one in the
  investigation's Unpublished study (the rule of `nextseek_api/studies/buckets.py`; a sample
  several candidates hold gets that one only if it is among them); otherwise it is refused as
  `assay_ambiguous_in_project`.
- `planner.py` splits the resolved rows into what will be written, what is already present,
  and what is skipped (`nextseek_api/assay_registration/planner.py:72-96`), reading existing
  membership ids with `MIN(id)` under a `GROUP BY` because `assay_assets` has no unique
  constraint on the pair (`nextseek_api/assay_registration/planner.py:38-52`). It also
  decides synchronous versus asynchronous from the row count
  (`nextseek_api/assay_registration/planner.py:68-69`).
- `executor.py` inserts and then reports from the read-back
  (`nextseek_api/assay_registration/executor.py:136-146`), writing membership rows with an
  explicit direction of 0 rather than the writer's own default
  (`nextseek_api/assay_registration/executor.py:31-36`). `preview()` produces the same report
  shape while touching nothing (`nextseek_api/assay_registration/executor.py:94-95`).
- `graph.py` is SQL only. It reads the assays a sample carries from `assay_assets`
  (`nextseek_api/assay_registration/graph.py:51`) and resolves each to an internal assay,
  junction table first with an id fallback behind it
  (`nextseek_api/assay_registration/graph.py:70`). It used to recompute the plural assay
  label lists on `DERIVED_FROM` edges and write them itself; one rule owns those labels
  now, and the module docstring carries the argument
  (`nextseek_api/assay_registration/graph.py:1-21`).
- `service.py` composes those four for the ViewSet
  (`nextseek_api/assay_registration/service.py:101`), maps an execution outcome onto a
  status code (`nextseek_api/assay_registration/service.py:29`), and enqueues the graph
  sync after the MySQL transaction closes, never inside it
  (`nextseek_api/assay_registration/service.py:139-140`).
- `schemas.py` holds every request and response contract, plus the error vocabulary: 16
  codes, counted by importing `ERROR_CODES` on 2026-09-03 and taking its length
  (`nextseek_api/assay_registration/schemas.py:31-70`).

**The job path.** A batch larger than `settings.ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD`
(default 5000, `dmac/settings.py:454-456`) is not executed inline: `service.register` creates
a row and answers 202 with a `status_url` reversed from the route name
(`nextseek_api/assay_registration/service.py:131-137`). `jobs.py` is the store,
create, claim, heartbeat, record progress, finish, cancel and read
(`nextseek_api/assay_registration/jobs.py:45-212`), over the model at
`nextseek_api/assay_registration/models_db.py:16`. `runner.py` turns an accepted job into a
receipt: claim, check cancellation once, execute, record, finish
(`nextseek_api/assay_registration/runner.py:117-202`), draining oldest-first with an explicit
tie-break (`nextseek_api/assay_registration/runner.py:69-77`). Cancellation means "will not
start", never "stops halfway", because the write is one transaction with no per-row loop to
interrupt (`nextseek_api/assay_registration/runner.py:8-14`).

**The worker.** `manage.py run_assay_registration_jobs` loops by default and takes `--once`,
`--limit` and `--interval`
(`nextseek_api/assay_registration/management/commands/run_assay_registration_jobs.py:45-53`).
Django's per-app command scan walks only an installed app's own path, and this is a
subpackage, so a same-named shim in the parent re-exports the class
(`nextseek_api/management/commands/run_assay_registration_jobs.py:1-5`); the reasoning is at
`nextseek_api/assay_registration/management/commands/run_assay_registration_jobs.py:21-30`.
It runs as a background process of the `nextseek` container, started by
`docker/scripts/entrypoint.sh` with `--interval 5`; there is no separate compose service.

## Undoing a registration

There is no delete path. The API is additive on purpose (above), no management command removes a
membership, and nothing in the package deletes an `assay_assets` row. Undoing a registration is a
manual two-step job for an operator, and the order matters: step 1 changes the source, step 2 brings the
graph's derived labels back in line with it. Skipping step 2 leaves DERIVED_FROM edges carrying assay
labels computed from rows that no longer exist.

**Step 1: remove the rows (manual SQL, no supported command).** The rows live in `assay_assets` in the
SEEK schema (`seek_production`, the Django alias `seek`), not in `dmac`. The `dmac` database also has
an `assay_assets` table and it is empty
(`nextseek_api/assay_registration/graph.py:16-20`), so a delete there removes nothing and looks fine.

1. Collect the ids. Every row the registration wrote carries the `assay_assets_id` the database assigned
   (`nextseek_api/assay_registration/schemas.py:136-138`). Take them from the response, or from the
   job's `GET assay-registrations/jobs/{job_id}/` result for a 202 batch (the same report is stored in
   the `terminal_result` column of `nextseek_api_assayregistrationjob` in `dmac`,
   `nextseek_api/assay_registration/models_db.py:35`). Take only rows with `status` `written`. A row
   that says `already_present` was a membership that existed before this registration, and undoing the
   registration must leave it alone (`nextseek_api/assay_registration/executor.py:182`, the only place a row becomes `written`).
2. Back up first. The registration writes `assay_assets` only, so dump that one table, with the dump
   kept outside any git checkout and mode 600, as `DEPLOYMENT.md` section 5.3 does for a migration:
   `docker exec <mysql-container> mysqldump -u<user> -p<pass> seek_production assay_assets > backup-assay_assets-pre-undo-<YYYYMMDD>.sql`.
3. Delete by id, with the assay and the type as a second guard, inside one transaction. The block
   stops before the commit on purpose:

   ```sql
   START TRANSACTION;
   DELETE FROM seek_production.assay_assets
    WHERE id IN (<written assay_assets_id values>)
      AND assay_id = <assay id> AND asset_type = 'Sample';
   SELECT ROW_COUNT();
   ```

   If the count equals the number of ids you listed for that assay, type `COMMIT;`. If not, type
   `ROLLBACK;`. Closing the session without either also rolls the delete back.

   Do it once per assay if the batch named several. `assay_assets` has no unique constraint on the
   pair (`nextseek_api/assay_registration/planner.py:38-52`), so never delete by `(assay_id, asset_id)`
   alone: a second row for the same pair that you did not write would go with it.

A registration does not touch `projects_samples` or any other table (the one write is
`nextseek_api/batch_upload/associations.py:122-149`), so nothing else needs undoing. Restoring from the
dump is the way back if you deleted the wrong rows.

**Step 2: bring the graph labels back in line.** A removed membership makes some DERIVED_FROM edges
`changed` or `cleared` against the label rule (`nextseek_api/graph_sync/labels.py:13-20`). Those classes
are counted but not written unless the run is approved
(`nextseek_api/graph_sync/targeted.py:54-56`), so a plain sync will not fix them. The sample's source hash
covers its assay links (`nextseek_api/graph_sync/projection.py:156`), so a sync does see the sample as
moved. The registration endpoint's own label recompute is gone: labels are derived by `graph_sync`
from MySQL for every writer (`nextseek_api/assay_registration/graph.py:1-12`), and the endpoint only
queues an outbox row after a write. Deleting rows by SQL queues nothing, so you run it yourself, from
the `nextseek` container:

1. Dry run, which writes nothing: `manage.py graph_sync --reconcile --dry-run --apply-label-changes --i-mean-the-live-graph`
   (or `--labels --dry-run --i-mean-the-live-graph`, the same label rule over the whole graph). Both
   need `--i-mean-the-live-graph` against the stack's Neo4j: the live-graph check runs first and
   ignores `--dry-run` (`live_graph_refusal` in `graph_sync.py`). It prints the per-class counts.
   It covers the whole graph, not only your samples, so other writers' pending changes appear in the
   counts too. `--dry-run` is refused by `--samples`
   (`nextseek_api/management/commands/graph_sync.py:312-315`), so there is no per-sample dry run on the
   command line.
2. Write: `manage.py graph_sync --samples <sample ids> --apply-label-changes --i-mean-the-live-graph`,
   with the `sample_id` values of the rows you removed, comma-separated. It relabels every DERIVED_FROM
   edge incident to those samples, in both directions, from the post-delete MySQL state.
3. Check: `manage.py graph_sync --drift` (read-only). Without step 2 the nightly reconcile at
   02:00 US Eastern writes these labels only if the loop runs with
   `NEXTSEEK_GRAPH_SYNC_LABEL_CHANGES=apply` (`nextseek_api/graph_sync/loop.py:85-86`, `:150-154`).

This wording holds however the label rule treats an assay with no internal mapping: step 2 recomputes
from whatever `assay_assets` holds, so it needs no change when that rule does.

## Running and testing

The package has a real lane of its own: 8 `test_*.py` modules under
`nextseek_api/assay_registration/tests/`, counted 2026-09-03, needing no database server, no
Neo4j and no network. Every test that would touch MySQL, SQLAlchemy or a bolt driver
substitutes a fake or a patch, so the whole suite runs against SQLite in memory under
`dmac/test_settings.py:21-30`.

**The lane I ran, 2026-09-03.** A throwaway container from the stack image, this worktree
bind-mounted read-only and copied to a writable path inside it, with networking off:

```
docker run --rm --network none -v "$PWD":/src:ro \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -w / nextseek-nextseek:latest \
  bash -lc 'cp -a /src /build && cd /build && /app/.venv/bin/python -m pytest \
    nextseek_api/assay_registration/tests/ -q'
```

Result: **259 passed, 189 warnings in 10.87s**, no failures, no errors, nothing skipped. The
warnings are all third-party deprecations from Pydantic, Django and drf-spectacular.

Two things I tried first and could not use, both worth knowing before you spend the time.
Running the same command with `-w /src` and no copy dies before collection with
`OSError: [Errno 30] Read-only file system: '/src/schema_rag'`, raised from
`dmac/settings.py:498-499`, which creates two directories beside the settings file at import.
And `scripts/run_tests.sh`, the supported wrapper, exits 1 on this worktree with
`missing .../dmac/local_settings.py (gitignored)` from `scripts/run_tests.sh:37-41`; it also
needs a compose directory holding the gitignored `docker/*.env` files
(`scripts/run_tests.sh:16-20`), and neither is present in a fresh checkout.

The convention gate is separate and cheaper. This ViewSet module is one of the five paths
`scripts/validate_viewset_conventions.py:29-35` scans, named at
`scripts/validate_viewset_conventions.py:34`. Run in the same container on 2026-09-03 the
validator printed 6 violations and not one of them names this directory: 5 are in
`nextseek_api/services/cc_assistant.py` and 1 in `nextseek_api/services/project_export.py`.

## Depends on / depended on by

Depends on, outside this directory. Derived by reading every import line in the 14 non-test
modules here, including the two lazy in-function ones:

- `nextseek_api/batch_upload/`, twice and for two different things. The transactional
  SQLAlchemy connection comes from `nextseek_api/assay_registration/service.py:10` and
  `nextseek_api/assay_registration/runner.py:43`, and it commits on clean exit and rolls back
  on any exception (`nextseek_api/batch_upload/db_engine.py:71-84`). The only write call
  comes from `nextseek_api/assay_registration/executor.py:26` and is idempotent by its own
  pre-SELECT (`nextseek_api/batch_upload/associations.py:62-72`).
- That engine binds to the Django alias `"seek"` as a literal
  (`nextseek_api/batch_upload/config.py:127-137`), while this package's own SQL prefixes
  every table with the name behind `settings.SEEK_DATABASE`
  (`nextseek_api/assay_registration/resolver.py:35-36`). The two agree only because
  `dmac/settings.py:518` sets that setting to `"seek"`.
- Django settings read per call rather than at import: the row threshold at
  `nextseek_api/assay_registration/service.py:117`, the two database aliases at
  `nextseek_api/assay_registration/resolver.py:36` and
  `nextseek_api/assay_registration/resolver.py:40`, and the Neo4j connection dict at
  `nextseek_api/assay_registration/service.py:70`.
- The parent shell, for three things only: the permission class
  (`nextseek_api/assay_registration/views.py:20`), the CSRF-exempt session authenticator
  (`nextseek_api/assay_registration/views.py:21`) and the three prose constants
  (`nextseek_api/assay_registration/views.py:15-19`).
- The `neo4j` driver, imported lazily inside the factory rather than at module scope
  (`nextseek_api/assay_registration/service.py:68`), and `sqlalchemy.text` at module scope in
  the two SQL modules (`nextseek_api/assay_registration/resolver.py:26` and
  `nextseek_api/assay_registration/planner.py:12`).
- Nothing here imports `seek/`. A grep for a line beginning `from seek` or `import seek` over
  every `.py` file in this directory and its subdirectories, tests included, returns nothing;
  the SEEK modules named throughout the docstrings, such as the sheet-path writer cited at
  `nextseek_api/assay_registration/schemas.py:6`, are provenance rather than imports.

Depended on by. Derived from a repo-wide grep for `assay_registration`, `assay-registration`
and `AssayRegistration` over every tracked file, then grouped. The package's own `tests/`
modules are omitted, and so is `nextseek_api/tests/test_endpoint_descriptions_safety.py`,
which constrains the prose constants that live in the parent rather than anything here:

- Django wiring. `nextseek_api/views.py:51` re-exports the ViewSet, `nextseek_api/urls.py:26`
  registers it, and `nextseek_api/models.py:2708` re-exports the ORM model so the app
  registry loads the class the parent's migration manages.
- CI, by path string and over HTTP, never by import. `ci/routes.py:746-750`,
  `ci/routes.py:519-523` and `ci/routes.py:751-755` declare the three routes, all three
  scoped `profiles="local,dev"` so none is exercised against production, and
  `ci/smoke/test_write_lane.py:81-104` posts a real `dry_run` and asserts the write-side
  identifiers are absent from the reply.
- Convention and drift gates. `scripts/validate_viewset_conventions.py:34` names this
  ViewSet module, and `NessieAI/tests/cc/test_cc_context_drift_guard.py:463`
  classifies the POST route as a write rather than a read-shaped POST.
- Deployment. `docker/scripts/entrypoint.sh` starts the drain loop inside the `nextseek`
  container, so `./startup.sh rebuild` restarts it with the app
  (`BASE_APP_RUNTIME_SERVICES` in `startup/lib/rebuild_policy.py`), and `DEPLOYMENT.md` §0
  is the operator-facing row.
- The Container-CC agent, as data. `NessieAI/chat_nextseek/src/chat_nextseek/context/min_api_endpoints.json:8-10`,
  which the cc-agent image bakes into its plugin context, publishes the POST route to the
  agent, and the sibling entry at
  `NessieAI/chat_nextseek/src/chat_nextseek/context/min_api_endpoints.json:30`
  redirects "add samples to an assay" here away from a PATCH that would replace the whole
  list.

What a hit here is NOT. `nextseek_api/batch_upload/errors.py:83` defines a class also called
`RowError`; it is an unrelated dataclass, not the pydantic model at
`nextseek_api/assay_registration/schemas.py:102`, and nothing crosses between them.
`ci/smoke/test_health.py:40` names the string `assay-registrations` inside the set of router
keys the API root is asserted to advertise (`ci/smoke/test_health.py:33`); it exercises the
registration in the parent and reaches no code in this directory. `startup/tests/test_rebuild_policy.py` matches
too, but tests the deploy policy module, not this package.
