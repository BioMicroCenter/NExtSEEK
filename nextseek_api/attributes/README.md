# `nextseek_api/attributes/`

## What this is

The native attribute API: a read/search catalog over SEEK's sample-type attribute
definitions, and a plan-then-execute mutation path that creates, patches and deletes
those definitions while rewriting every affected sample's `json_metadata` to match.

It is a plain Python package, not a Django app. Its two durable models declare
`app_label = "nextseek_api"` (`nextseek_api/attributes/models_db.py:206` and
`nextseek_api/attributes/models_db.py:328`), as does the dispatcher heartbeat
(`nextseek_api/attributes/models_async.py:19`), so the parent app owns their
schema: `nextseek_api/migrations/0010_attribute_mutation_job.py:1` creates the job
and partition tables and `nextseek_api/migrations/0011_attribute_async_orchestration.py:11`
depends on it to add the heartbeat. There is no `migrations` package inside this
boundary — a find for a directory named `migrations`, or for any file matching
`0*.py`, anywhere beneath `nextseek_api/attributes` returns nothing. It registers no
URLs either; the single HTTP surface is routed from outside, at
`nextseek_api/urls.py:25`.

Counted 2026-09-03 over `*.py` beneath the package: 62 Python files, 39 of them under
`tests/`. The one non-Python file is a Ruby oracle used to reproduce SEEK's own
canonicalization, `nextseek_api/attributes/tests/rails_auth_oracle.rb:1`.

Nothing mutates without a plan first. `MutationPlanner.plan_mutation`
(`nextseek_api/attributes/planner.py:646`) turns a validated envelope into an
immutable `MutationPlan` (`nextseek_api/attributes/planner.py:488`) that acquires no
lock and writes nothing. One number then decides how that plan runs: if the plan's
total affected sample rows exceed the configured threshold the mode is
`asynchronous`, otherwise `synchronous` (`nextseek_api/attributes/planner.py:697`),
with the threshold defaulting to 5000 (`dmac/settings.py:445-447`). The caller never
chooses; it only learns which happened, from a `202` carrying a `job_id`
(`nextseek_api/attributes/service.py:200-207`) or a completed body.

Both modes create the same durable rows through `MutationJobService.create`
(`nextseek_api/attributes/jobs.py:537`), in one transaction. The difference is one
column: an asynchronous job is written with `outbox_state="pending"`, a synchronous
one with `"not_required"` (`nextseek_api/attributes/jobs.py:576`). That single value
is what routes the job to the transactional outbox or leaves it to be executed inside
the web request, and it is also what the recovery scanner keys on
(`nextseek_api/attributes/management/commands/recover_attribute_sync_jobs.py:44-45`).

## Surface

This boundary is an ordinary Python package, so its surface is the public entry points
and the modules behind them. It also ships three long-running processes as management
commands and one Celery task, and those are entered by name rather than by import.

**HTTP.** `AttributeViewSet` (`nextseek_api/attributes/views.py:115`) is the only
request handler — list, retrieve, `search`, `batch-create`, `batch-patch`,
`batch-delete`, plus job status (`nextseek_api/attributes/views.py:294`) and job
cancellation (`nextseek_api/attributes/views.py:316`). Each view body is a few lines
around `nextseek_api/attributes/views.py:37`, which builds a fresh composition facade
per request; the facade itself is `AttributeServices`
(`nextseek_api/attributes/service.py:143`).

| Concern | Where |
|---|---|
| Strict request/response contract | `nextseek_api/attributes/schemas.py:68` |
| Identifier grammar, database-free | `nextseek_api/attributes/resolver.py:32` |
| Bounded read path and SEEK gateway | `nextseek_api/attributes/repository.py:1305` |
| Page shape and its bounds | `nextseek_api/attributes/pagination.py:15-16` |
| Query/path scalar parsing | `nextseek_api/attributes/scalars.py:26` |
| Deterministic write-free planning | `nextseek_api/attributes/planner.py:646` |
| The one per-type execution kernel | `nextseek_api/attributes/executor.py:93` |
| Bulk `json_metadata` rewrite | `nextseek_api/attributes/metadata.py:150` |
| Durable job and partition rows | `nextseek_api/attributes/models_db.py:117` |
| Leases, outbox, stored-job worker | `nextseek_api/attributes/jobs.py:295` |
| SEEK-person authentication | `nextseek_api/attributes/auth.py:153` |
| Cross-process fault injection | `nextseek_api/attributes/faults.py:34` |
| Swagger auth scheme and examples | `nextseek_api/attributes/openapi.py:46` |

**One kernel, three callers.** `execute_type_plan`
(`nextseek_api/attributes/executor.py:93`) is the single per-type execution kernel; it
imports no Django or database API of its own and reaches every side effect through an
injected `services` adapter (`nextseek_api/attributes/executor.py:98-101`).
The synchronous request path drives it through `execute_batch`
(`nextseek_api/attributes/executor.py:266`) with a per-plan claim factory
(`nextseek_api/attributes/executor.py:796`); the Celery worker drives the same kernel
after claiming a partition (`nextseek_api/attributes/jobs.py:251`); the recovery
scheduler drives it through that worker helper too
(`nextseek_api/attributes/management/commands/recover_attribute_sync_jobs.py:137`).

**The workers are separate compose services, not threads in the app container.** All
three run the shared app image `${COMPOSE_PROJECT_NAME:-nextseek}-nextseek:latest`
(`docker-compose.yml:340`) as their own services: a Celery worker bound to the
`attribute_mutations` queue only (`docker-compose.yml:334`, command at
`docker-compose.yml:351`), the outbox dispatcher (`docker-compose.yml:368`, command at
`docker-compose.yml:385`), and the synchronous-job recovery scheduler
(`docker-compose.yml:403`, command at `docker-compose.yml:417`). The dispatcher and
worker share a SQLite Celery broker on a named volume (`docker-compose.yml:348`);
the recovery scheduler deliberately has neither, so it can consume no queue at all
(`docker-compose.yml:399-402`).

**Django cannot see these commands where they live.** Its per-app command scan walks
only each `INSTALLED_APPS` entry's own path, and this is a subpackage rather than an
app, so each command is re-exported by a same-named shim in the directory Django does
scan — `nextseek_api/management/commands/dispatch_attribute_outbox.py:9`,
`nextseek_api/management/commands/check_attribute_outbox_heartbeat.py:5`, and
`nextseek_api/management/commands/recover_attribute_sync_jobs.py:5`. The reasoning is
set out at
`nextseek_api/attributes/management/commands/dispatch_attribute_outbox.py:5-14`.

**How a job survives its executor dying.** Each of the two failure modes has its own
recovery, and they do not overlap. An asynchronous message a killed worker never
acknowledged is redelivered, because the task is declared with late acknowledgment and
worker-loss rejection (`nextseek_api/attributes/tasks.py:15`); the redelivery is safe
because a second claim attempt against a live lease simply returns `not_claimed`
(`nextseek_api/attributes/jobs.py:305-306`). A synchronous job whose web process died
is picked up instead by the recovery scheduler, which claims only jobs with an expired
lease and a stale heartbeat
(`nextseek_api/attributes/management/commands/recover_attribute_sync_jobs.py:43-46`)
and refuses to replay a partition that is not provably untouched
(`nextseek_api/attributes/management/commands/recover_attribute_sync_jobs.py:72-81`).

## Running and testing

The boundary has its own suite under `tests/`, its own `conftest.py`, and no runner of
its own; it is driven by the repo's ordinary in-container pytest lane. There are two
lanes, and they are not the same lane with a flag.

1. **Database-free lane.** Everything that does not request the disposable-MySQL
   fixture. This is what an ordinary in-container run gives you.
2. **Real-MySQL lane.** Every case taking the `disposable_attribute_db` fixture
   (`nextseek_api/attributes/tests/attribute_fixtures.py:79-81`) builds and drops a
   throwaway schema whose name must start with `attribute_test_`
   (`nextseek_api/attributes/tests/real_boundary.py:480`). It needs
   `ATTRIBUTE_TEST_DB_HOST`, `ATTRIBUTE_TEST_DB_USER` and `ATTRIBUTE_TEST_DB_PASSWORD`
   in the environment (`nextseek_api/attributes/tests/real_boundary.py:483-486`), a
   MySQL account that may create and drop databases, and
   `ATTRIBUTE_EVIDENCE_RUN_ROOT` pointing at a run directory holding a frozen
   boundary identity (`nextseek_api/attributes/tests/attribute_fixtures.py:82-85`).

I ran the whole directory in the live container on 2026-09-03 with none of those
variables set. 856 cases collect in 1.23s. 484 passed and 12 failed; the remaining 360
errored in fixture setup, every one on the same missing host variable. Four of those 12
failures trace to a single line — the kernel writes
`outcome["counts"]["updated_samples"]` at `nextseek_api/attributes/executor.py:155-157`
while the unit suite's own service double returns an outcome with no `counts` key
(`nextseek_api/attributes/tests/test_executor.py:99`). See
`nextseek_api/attributes/CLAUDE.md` for the exact command and its full tally.

A separate settings module exists for the benchmark lane,
`dmac/attribute_performance_settings.py:27-30`, which points both database aliases at
real MariaDB and refuses an in-memory one (`dmac/attribute_performance_settings.py:8-9`).

## Depends on / depended on by

Depends on. Three different shapes, worked out separately.

- A **table set in SEEK's MySQL schema**, reached by raw SQL through
  `connections[settings.SEEK_DATABASE]` and never through an ORM model. Definitions in
  `sample_attributes` are read at `nextseek_api/attributes/repository.py:455` by a
  layer that declares it issues no write at all
  (`nextseek_api/attributes/repository.py:13-15`).
- Four statements, all inside `apply_definitions`, are what actually change
  `sample_attributes`: a delete (`nextseek_api/attributes/executor.py:636-639`), an
  insert (`nextseek_api/attributes/executor.py:642-649`), a position-only update
  (`nextseek_api/attributes/executor.py:668-671`) and a content update
  (`nextseek_api/attributes/executor.py:681-688`).
- `sample_types` is the lock anchor for a mutation, taken at
  `nextseek_api/attributes/executor.py:608` before any definition is read.
- `samples.json_metadata` is read under lock in primary-key pages at
  `nextseek_api/attributes/metadata.py:138-139` and rewritten one chunk per statement
  at `nextseek_api/attributes/metadata.py:202-205`.
- The four relationship lookup tables are named in one place,
  `nextseek_api/attributes/repository.py:70`.
- `people`, `users` and `roles` back identity and the SEEK admin role, at
  `nextseek_api/attributes/auth.py:103-104` and `nextseek_api/attributes/auth.py:188`.
- `INFORMATION_SCHEMA.COLUMNS` is queried for the live collation of
  `sample_attributes.title` (`nextseek_api/attributes/repository.py:826-827`), because
  the database's collation, not Python, decides title equality
  (`nextseek_api/attributes/resolver.py:6-8`).
- **Django settings**, all read at call time: the SEEK alias
  (`dmac/settings.py:518`), the synchronous/asynchronous threshold
  (`dmac/settings.py:445-447`), and the in-job thread count
  (`dmac/settings.py:448-450`).
- **Ordinary Python imports** outside the boundary: the Celery app
  (`nextseek_api/attributes/tasks.py:12`), the superuser permission
  (`nextseek_api/attributes/views.py:31`), the OpenAPI description constants
  (`nextseek_api/attributes/views.py:18-27`), and the SEEK HTTP client plus the
  CSRF-exempt session authenticator (`nextseek_api/attributes/auth.py:14-15`).
- `seek.dbrouters` is a dependency this package never imports: the models simply omit
  a `_DATABASE` attribute (`nextseek_api/attributes/models_db.py:9-10`), and the
  router's `getattr` fallback is what sends them to `default`
  (`seek/dbrouters.py:4`). Renaming that fallback moves these tables onto SEEK.

Depended on by. A different shape again: a few real imports, and a larger set of
edges made of service definitions and names in string form.

- `nextseek_api/views.py:50` re-exports the ViewSet into the aggregator the router
  imports at `nextseek_api/urls.py:5`.
- `nextseek_api/models.py:4-8` imports all three models into the parent app's module,
  which is what ties them to that app's migration graph.
- `nextseek_api/batch_upload/celery_app.py:52` autodiscovers this package's tasks by
  package name, which is how `nextseek_api/attributes/tasks.py:15` gets registered.
- Nothing but the three command shims imports the `management/commands` subpackage:
  `nextseek_api/management/commands/dispatch_attribute_outbox.py:9` is one of them, and a
  grep over every `*.py` in the worktree for
  `nextseek_api.attributes.management` returns only those three files and one test
  (`nextseek_api/attributes/tests/test_sync_recovery.py:118`).
- Compose reaches the commands purely by name (`docker-compose.yml:385`,
  `docker-compose.yml:417`) and the worker by queue name
  (`docker-compose.yml:351`).
- `startup/lib/rebuild_policy.py:16-20` names the three services as literal strings,
  which is what `./startup.sh rebuild` recreates (`startup/cli.py:599-607`).
- `docker/scripts/attribute_runtime_healthcheck.py:22` is not an importer: it hardcodes
  the heartbeat table name and reads MySQL directly, precisely to avoid starting a
  second Django process inside the service containers
  (`docker/scripts/attribute_runtime_healthcheck.py:4-6`).
- `nextseek_api/tests/conftest.py:1` and `startup/tests/conftest.py:7` register this
  package's fixture module as a pytest plugin, so both of those suites import test code
  from inside this boundary.
- `scripts/run_attribute_mutants.py:88` loads
  `nextseek_api.attributes.tests.mutation_driver` as a pytest plugin, again by string.
- `dmac/attribute_performance_settings.py:36` imports a module from this boundary's
  `tests/` package that does not exist anywhere in the worktree.

Omitted from the list above: the 39 Python files under this boundary's `tests/`, which
import their own siblings freely. Also excluded as a false match, `chat_nextseek/src/chat_nextseek/context/min_api_endpoints.json:145`
contains the word "attributes" in SEEK sample-creation prose and has no relationship to
this package.

See `nextseek_api/attributes/CLAUDE.md` for the invariants, the traps, and the one
command to run.
