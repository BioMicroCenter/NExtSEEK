# `nextseek_api/batch_upload/`

## What this is

The bulk sample-ingest pipeline. An uploaded Excel workbook, or a JSON list of rows
posted directly, becomes rows in SEEK's `samples` table, driven by one orchestrator
function (`nextseek_api/batch_upload/orchestrator.py:662`) through stages the code numbers
0 to 7 and logs as such (`nextseek_api/batch_upload/orchestrator.py:681-682`). The graph is
not written here. Stage 5 records each batch's committed ids in the graph_sync outbox and
stage 6 hands them to [`nextseek_api/graph_sync/`](../graph_sync/README.md), which owns
every write to Neo4j.

It is shaped like a Django app and is not one. The package holds no `apps.py`, no
`migrations/` directory, no `urls.py` and no `admin.py`: a `find` over
`nextseek_api/batch_upload` for those four names returns nothing at all. Its `models.py`
is a Pydantic module (`nextseek_api/batch_upload/models.py:116` is the input row), and no
module in the package subclasses `models.Model` or touches `django.db` outside `tests/`:
grep for both strings over every `*.py` under the package matches four lines in all, and all
four sit inside `nextseek_api/batch_upload/tests/test_migration_name_identity.py:356-359`. The
code rides inside the `nextseek_api` app installed at `dmac/settings.py:178`, and its one
HTTP surface is registered from outside the boundary at `nextseek_api/urls.py:37`.

Writes bypass the Django ORM entirely. A SQLAlchemy engine is built from the `seek`
database alias (`nextseek_api/batch_upload/config.py:131`) as a MySQLdb URL
(`nextseek_api/batch_upload/config.py:137`), and every statement is hand-written SQL
against the Rails-owned schema that alias names (`dmac/settings.py:38-45`). The Django ORM
appears only in the ViewSet's identity resolution, which reads SEEK's `users` table through
the mirror model at `seek/models/seek_mirror.py:20-21`.

The package also owns the process's only Celery application
(`nextseek_api/batch_upload/celery_app.py:20`), on which two unrelated subsystems register
their own tasks.

A `find` over the package gives the current file count; most of them are under `tests/`, and
the rest are the top-level stage modules listed below.

## Surface

Three surfaces of different shapes: HTTP actions DRF registers, a stage pipeline of plain
functions, and the table set plus graph labels the pipeline writes. The dependency edges in
the last section are correspondingly mixed: imports in one direction, a fetch-by-URL and a
fixture read by path in the other.

### HTTP

`BatchUploadViewSet` at `nextseek_api/batch_upload/views.py:94` accepts token,
CSRF-exempt session or basic auth (`nextseek_api/batch_upload/views.py:101`) from
authenticated callers only (`nextseek_api/batch_upload/views.py:102`).

| Action | Handler | Shape |
|---|---|---|
| `start` | `nextseek_api/batch_upload/views.py:162` | 202 + a Celery job id |
| `validate` | `nextseek_api/batch_upload/views.py:405` | synchronous, no job, no writes |
| `status/{job_id}` | `nextseek_api/batch_upload/views.py:535` | Celery state + progress meta |
| `cancel/{job_id}` | `nextseek_api/batch_upload/views.py:573` | revoke with terminate |
| `summary/{job_id}` | `nextseek_api/batch_upload/views.py:586` | the summary CSV as a download |
| `list` | `nextseek_api/batch_upload/views.py:617` | the caller's own jobs, paged |

`start` takes two input modes and rows win when both arrive
(`nextseek_api/batch_upload/views.py:177-178`); uploads must end in `.xlsx`
(`nextseek_api/batch_upload/views.py:202`) and are capped by a settings value defaulting to
200 MB (`nextseek_api/batch_upload/views.py:208-209`). `validate` runs the same stages up to
TRANSFORM and stops (`nextseek_api/batch_upload/validation.py:192`), passing
`mutate_project_links=False` (`nextseek_api/batch_upload/validation.py:240`) so the run
issues no INSERT of its own.

Contributor identity is resolved server-side in three phases
(`nextseek_api/batch_upload/views.py:673`) from the SEEK login, never from the Django
primary key. A superuser may name another `person_id`
(`nextseek_api/batch_upload/views.py:773`); anyone else's attempt is logged and discarded in
favour of their own identity (`nextseek_api/batch_upload/views.py:782-790`).

### The pipeline

`nextseek_api/batch_upload/orchestrator.py:182` runs stages 0 through 4 and is shared by
both the upload and the validate entry points; the stage order is spelled out at
`nextseek_api/batch_upload/orchestrator.py:681-682`.

| Stage | Module | Entry point |
|---|---|---|
| 0 CONVERT: format detect, merge, ontology | `nextseek_api/batch_upload/convert.py:75` | `nextseek_api/batch_upload/extract.py:58` streams the sheet |
| 1.25 NAME_CHECK | `nextseek_api/batch_upload/uid_gen.py:453` | matches existing samples by identity |
| 1.5 UID_GEN | `nextseek_api/batch_upload/uid_gen.py:552` | mints UIDs, resolves parent tokens |
| 2 DAG | `nextseek_api/batch_upload/dag.py:78` | assay direction per parent/child pair |
| 2.5 LEVELS | `nextseek_api/batch_upload/levels.py:27` | topological insert order |
| 3 PREFETCH | `nextseek_api/batch_upload/prefetch.py:263` | cached sample-type and assay lookups |
| 4 TRANSFORM | `nextseek_api/batch_upload/transform.py:43` | builds the insertable row |
| 5 INSERT | `nextseek_api/batch_upload/insert.py:171` | the batch loop, per topological level |
| 6 GRAPH SYNC | `nextseek_api/batch_upload/orchestrator.py:637` | hands this job's committed ids to `graph_sync` |
| 7 REPORT | `nextseek_api/batch_upload/report.py:108` | the per-row summary CSV |

Around them: `nextseek_api/batch_upload/ontology.py:15` reads the workbook's controlled
vocabularies and `nextseek_api/batch_upload/ontology.py:154` validates every row against
them in bulk; `nextseek_api/batch_upload/update.py:338`
is the upsert path that deep-merges metadata (`nextseek_api/batch_upload/update.py:37`)
instead of inserting; `nextseek_api/batch_upload/parallel.py:107` runs a level through a
thread pool once it is large enough (`nextseek_api/batch_upload/parallel.py:26`);
`nextseek_api/batch_upload/orphan_resolution.py:50` finds children whose parent arrived in a
later upload and `nextseek_api/batch_upload/orphan_resolution.py:125` repairs their metadata
in MySQL, then enqueues them for the graph sync.
`nextseek_api/batch_upload/checkpoint.py:38` is the resume point;
`nextseek_api/batch_upload/errors.py:54-71` maps each error type to a severity and
`nextseek_api/batch_upload/errors.py:74-76` grades anything absent from that map as an
error.

Two rules live here as single definitions that several stages read. Protocol-to-SOP
resolution is stated once, with its provenance and the three stored value shapes, at
`nextseek_api/batch_upload/helpers.py:53-74`. Non-UID sample identity (which metadata field
stands in for a name, and its hash) is `nextseek_api/batch_upload/identity.py:79` and
`nextseek_api/batch_upload/identity.py:129`.

### What it writes

Against the `seek` alias, by hand-written SQL. Written: `samples`, inserted through either
of two strategies (`nextseek_api/batch_upload/insert_strategies.py:32` and
`nextseek_api/batch_upload/insert_strategies.py:66`) and updated at
`nextseek_api/batch_upload/update.py:99`; `policies`, one per UID
(`nextseek_api/batch_upload/policies.py:59-62`) with the unused ones removed
(`nextseek_api/batch_upload/policies.py:122`); `permissions`
(`nextseek_api/batch_upload/permissions.py:14`); `projects_samples`
(`nextseek_api/batch_upload/associations.py:17`); `assay_assets`
(`nextseek_api/batch_upload/associations.py:62`); and `projects_sample_types`
(`nextseek_api/batch_upload/prefetch.py:130-134`).

Read only: `sample_types` (`nextseek_api/batch_upload/prefetch.py:47`), `assays`
(`nextseek_api/batch_upload/prefetch.py:73`), `sample_attributes`
(`nextseek_api/batch_upload/prefetch.py:286-289`), `sops`
(`nextseek_api/batch_upload/helpers.py:289`), `studies`
(`nextseek_api/batch_upload/neo4j_sync.py:84`) and `investigations`
(`nextseek_api/batch_upload/neo4j_sync.py:117`). `assays_tbl` and `child_assays` are not
tables in that schema at all: they are an in-memory DuckDB registration and a CTE inside
`nextseek_api/batch_upload/dag.py:201-205`.

**In Neo4j it writes nothing.** Stage 5 inserts each batch's committed ids into
`graph_sync_outbox` on the batch's own connection, inside the batch's transaction
(`nextseek_api/batch_upload/insert.py:58`), qualified by the dmac schema's name
(`nextseek_api/batch_upload/insert.py:44`); where that connection may not insert there, the
row is written right after the commit instead
(`nextseek_api/batch_upload/insert.py:383`). Stage 6 then calls
`graph_sync.targeted.sync_samples` for every sample the job touched
(`nextseek_api/batch_upload/orchestrator.py:580`), under the graph-write lock, and closes
this job's outbox rows only when that succeeded
(`nextseek_api/batch_upload/orchestrator.py:612`). The job's totals and the summary CSV
carry the outcome as `graph: synced (N)` or `graph: pending (N)`
(`nextseek_api/batch_upload/orchestrator.py:637`,
`nextseek_api/batch_upload/report.py:182-187`), and `pending` is not a failure: the outbox
rows stand and the sync loop drains them. What is left in
`nextseek_api/batch_upload/neo4j_sync.py` reads MySQL and builds payloads; graph_sync's own
labels and parent lists are coded against it and compared with it on the same fixtures.

### Background work and scripts

Two Celery tasks: the upload driver at `nextseek_api/batch_upload/tasks.py:18` and the
best-effort orphan pass it dispatches afterwards at
`nextseek_api/batch_upload/tasks.py:118`. The app itself routes three task-name patterns
onto two queues (`nextseek_api/batch_upload/celery_app.py:35-39`), carries one beat entry belonging to
another subsystem (`nextseek_api/batch_upload/celery_app.py:40-45`) and clamps a task at two
hours soft, 7800 seconds hard (`nextseek_api/batch_upload/celery_app.py:46-47`).

## Running and testing

The suite is self-contained under `tests/`, and there is no `conftest.py` anywhere beneath
the package: a `find` for that name under `nextseek_api/batch_upload` returns nothing, so
the fixtures these tests get come from `nextseek_api/conftest.py:7-10`. Run it inside the
live container, which is where the dependency set and the DB grant are:

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek sh -c \
  'cd /app && uv run --no-sync python -m pytest nextseek_api/batch_upload/tests/ \
   --no-migrations -q -p no:randomly'
```

Before trusting a green run, note that the container ships its own copy of the code: a
per-file comparison of the package against `/app` in the running `nextseek` container is
what proves the lane exercised this branch's source rather than the image's.

The 26 skips break down, on the same date and with `-rs`, as 23 needing a MariaDB fixture
the environment does not supply
(`nextseek_api/batch_upload/tests/test_migration_name_identity.py:45`), 2 needing the same
for the Wave 3 drift module
(`nextseek_api/batch_upload/tests/test_identity_drift_integration.py:257`) and 1 needing
Redis (`nextseek_api/batch_upload/tests/test_views.py:167`). Two further
lanes exist and were NOT run here: the Wave 3 live module, which needs a Neo4j account
holding CREATE and DROP DATABASE privilege and opts in through
`nextseek_api/batch_upload/tests/test_identity_drift_integration.py:51`, and the live
end-to-end script described at `scripts/test_batch_upload_e2e.py:2-6`, which posts a real
workbook to a running instance and then reads the graph back.

## Depends on / depended on by

Depends on, outside this directory:

- The `seek` database alias defined at `dmac/settings.py:38-45`, resolved into a SQLAlchemy
  URL at `nextseek_api/batch_upload/config.py:127-137`. Repointing that alias sends every
  write in this package at a different schema.
- `settings.NEO4J_DATABASE`, read at `nextseek_api/batch_upload/config.py:87` into a frozen
  model (`nextseek_api/batch_upload/config.py:79`) built at most once per process behind an
  `lru_cache` (`nextseek_api/batch_upload/config.py:81-83`). A missing key clears the enable
  flag (`nextseek_api/batch_upload/config.py:111`), and stage 6 then reports
  `not_configured` rather than failing (`nextseek_api/batch_upload/orchestrator.py:589-592`),
  which the job carries as `graph: pending`.
- `settings.MEDIA_ROOT`, the load-bearing directory this package reads and writes for four
  distinct things: uploaded workbooks (`nextseek_api/batch_upload/views.py:663`), the
  per-user job index (`nextseek_api/batch_upload/job_index.py:12-14`), resume checkpoints
  (`nextseek_api/batch_upload/tasks.py:45-49`) and the summary CSVs
  (`nextseek_api/batch_upload/orchestrator.py:691-697`).
- `nextseek_api/authentication.py:15`, for the CSRF-exempt session authenticator the
  ViewSet installs.
- `nextseek_api/endpoint_descriptions.py:917`, for the OpenAPI prose the actions render.
- `seek/models/seek_mirror.py:20` and `seek/seekdb.py:148`, plus
  `nextseek_api/helpers.py:89`, the three routes by which
  `nextseek_api/batch_upload/views.py:674` turns a Django session into a SEEK person id.
- Optional accelerators, each with a live fallback: `orjson`
  (`nextseek_api/batch_upload/dag.py:10-16`) and `duckdb`, which falls back to pandas above
  the 250,000-row threshold (`nextseek_api/batch_upload/dag.py:189-193`). `polars`
  (`pyproject.toml:77`) and `psutil` (`pyproject.toml:79`) are imported unguarded at module
  scope instead (`nextseek_api/batch_upload/convert.py:10` and
  `nextseek_api/batch_upload/insert.py:10` are two of the five such lines) and `openpyxl`
  (`pyproject.toml:71`) unguarded inside a function
  (`nextseek_api/batch_upload/convert.py:66`).

Depended on by. Grouped by kind, from a repo-wide grep for imports of this package, for its
files by path string, and for its endpoints inside string literals. On 2026-09-03 that
import grep matched 394 lines, of which 366 are the package importing itself and a further
15 are test modules in other packages; both groups are omitted here. What follows covers the
remaining 13 import sites in 10 files, plus the three edges that are not imports at all.

- Wiring. `nextseek_api/views.py:29` re-exports the ViewSet so
  `nextseek_api/urls.py:37` can register it under the `nextseek_api` app.
- The shared Celery app is the widest edge. `nextseek_api/attributes/tasks.py:12` binds the
  attribute-mutation task to it, `nextseek_api/cc_assistant/cc_upload_tasks.py:18` binds the
  CC upload task, and `docker/scripts/entrypoint.sh` runs a second worker process off the
  same app for the `attribute_mutations` queue. Renaming or moving `celery_app` breaks all three.
- Job ownership. `nextseek_api/services/cc_assistant.py:278` registers a CC upload in this
  package's index and `nextseek_api/services/cc_assistant.py:289` gates the status endpoint
  on it, so the CC upload flow inherits this package's ownership model wholesale.
- SQL and helper reuse. `nextseek_api/assay_registration/service.py:10` and
  `nextseek_api/assay_registration/runner.py:43` take the engine;
  `nextseek_api/assay_registration/executor.py:26` takes the assay-asset writer;
  `NessieAI/ns/reingest_qa.py:13` and `nextseek_api/services/samples.py:17` take
  parsing helpers.
- One standalone program, `scripts/test_batch_upload_e2e.py`, which borrows the engine at
  `scripts/test_batch_upload_e2e.py:44` and the graph config at
  `scripts/test_batch_upload_e2e.py:175` to check its own results.
- The browser UI, which is not an import at all: `seek/templates/pages/batch_upload.embed.html:203`
  and `seek/templates/pages/batch_upload.embed.html:319` call the two POST endpoints by URL,
  and `seek/templates/pages/batch_upload.embed.html:142` and
  `seek/templates/pages/batch_upload.embed.html:154` poll status and build the download link.
- CI. `ci/routes.py:786-791` declares `start` as a route it deliberately leaves unprobed,
  and `ci/smoke/test_flows.py:220-223` opens a fixture from inside this boundary by path.
- The container agent's `_batch_upload_*` modules are NOT this package.
  `NessieAI/docker/cc-runtime/build_context/plugins/nextseek/bin/_batch_upload_runner.py:18-21` names
  four sibling files that ship inside the agent image; they reach this code only over HTTP,
  by posting to the validate URL at
  `NessieAI/docker/cc-runtime/build_context/plugins/nextseek/bin/_batch_upload_client.py:217`. The
  `batch_upload_preparation` label that turns up in the same searches is a router task
  family, not a reference to this directory either
  (`NessieAI/dmac_assistant/build_context/route_capabilities.json:319`).

See `nextseek_api/batch_upload/CLAUDE.md` for the invariants, the traps, and the one command
to run.
