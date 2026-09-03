# `nextseek_api/batch_upload/`

## What this is

The bulk sample-ingest pipeline. An uploaded Excel workbook, or a JSON list of rows
posted directly, becomes rows in SEEK's `samples` table and nodes and edges in Neo4j, driven
by one orchestrator function (`nextseek_api/batch_upload/orchestrator.py:548`) through
stages the code numbers 0 to 7 and logs as such
(`nextseek_api/batch_upload/orchestrator.py:636`).

It is shaped like a Django app and is not one. The package holds no `apps.py`, no
`migrations/` directory, no `urls.py` and no `admin.py`: a `find` over
`nextseek_api/batch_upload` for those four names returns nothing at all. Its `models.py`
is a Pydantic module (`nextseek_api/batch_upload/models.py:116` is the input row), and no
module in the package subclasses `models.Model` or touches `django.db` outside `tests/` —
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

Derived 2026-09-03 by `find` over the package: 87 Python files, 51 of them under `tests/`,
32 top-level modules and 4 under `scripts/`.

## Surface

Three surfaces of different shapes: HTTP actions DRF registers, a stage pipeline of plain
functions, and the table set plus graph labels the pipeline writes. The dependency edges in
the last section are correspondingly mixed — imports in one direction, a fetch-by-URL and a
fixture read by path in the other.

### HTTP

`BatchUploadViewSet` at `nextseek_api/batch_upload/views.py:93` accepts token,
CSRF-exempt session or basic auth (`nextseek_api/batch_upload/views.py:100`) from
authenticated callers only (`nextseek_api/batch_upload/views.py:101`).

| Action | Handler | Shape |
|---|---|---|
| `start` | `nextseek_api/batch_upload/views.py:161` | 202 + a Celery job id |
| `validate` | `nextseek_api/batch_upload/views.py:406` | synchronous, no job, no writes |
| `status/{job_id}` | `nextseek_api/batch_upload/views.py:536` | Celery state + progress meta |
| `cancel/{job_id}` | `nextseek_api/batch_upload/views.py:574` | revoke with terminate |
| `summary/{job_id}` | `nextseek_api/batch_upload/views.py:587` | the summary CSV as a download |
| `list` | `nextseek_api/batch_upload/views.py:618` | the caller's own jobs, paged |

`start` takes two input modes and rows win when both arrive
(`nextseek_api/batch_upload/views.py:168`); uploads must end in `.xlsx`
(`nextseek_api/batch_upload/views.py:201`) and are capped by a settings value defaulting to
200 MB (`nextseek_api/batch_upload/views.py:207-208`). `validate` runs the same stages up to
TRANSFORM and stops (`nextseek_api/batch_upload/validation.py:192`), passing
`mutate_project_links=False` (`nextseek_api/batch_upload/validation.py:240`) so the run
issues no INSERT of its own.

Contributor identity is resolved server-side in three phases
(`nextseek_api/batch_upload/views.py:674`) from the SEEK login, never from the Django
primary key. An admin may name another `person_id`; a non-admin's attempt is logged and
discarded in favour of their own identity (`nextseek_api/batch_upload/views.py:785-788`).

### The pipeline

`nextseek_api/batch_upload/orchestrator.py:165` runs stages 0 through 4 and is shared by
both the upload and the validate entry points; the stage order is spelled out at
`nextseek_api/batch_upload/orchestrator.py:567-568`.

| Stage | Module | Entry point |
|---|---|---|
| 0 CONVERT — format detect, merge, ontology | `nextseek_api/batch_upload/convert.py:75` | `nextseek_api/batch_upload/extract.py:58` streams the sheet |
| 1.25 NAME_CHECK | `nextseek_api/batch_upload/uid_gen.py:453` | matches existing samples by identity |
| 1.5 UID_GEN | `nextseek_api/batch_upload/uid_gen.py:552` | mints UIDs, resolves parent tokens |
| 2 DAG | `nextseek_api/batch_upload/dag.py:78` | assay direction per parent/child pair |
| 2.5 LEVELS | `nextseek_api/batch_upload/levels.py:27` | topological insert order |
| 3 PREFETCH | `nextseek_api/batch_upload/prefetch.py:263` | cached sample-type and assay lookups |
| 4 TRANSFORM | `nextseek_api/batch_upload/transform.py:43` | builds the insertable row |
| 5 INSERT | `nextseek_api/batch_upload/insert.py:116` | the batch loop, per topological level |
| 6 NEO4J | `nextseek_api/batch_upload/neo4j_sync.py:1582` | bulk MERGE of nodes and edges |
| 7 REPORT | `nextseek_api/batch_upload/report.py:108` | the per-row summary CSV |

Around them: `nextseek_api/batch_upload/ontology.py:15` reads the workbook's controlled
vocabularies and `nextseek_api/batch_upload/ontology.py:154` validates every row against
them in bulk; `nextseek_api/batch_upload/update.py:338`
is the upsert path that deep-merges metadata (`nextseek_api/batch_upload/update.py:37`)
instead of inserting; `nextseek_api/batch_upload/parallel.py:107` runs a level through a
thread pool once it is large enough (`nextseek_api/batch_upload/parallel.py:26`);
`nextseek_api/batch_upload/orphan_resolution.py:44` finds edges whose parent arrived in a
later upload and `nextseek_api/batch_upload/orphan_resolution.py:182` repairs them.
`nextseek_api/batch_upload/checkpoint.py:38` is the resume point;
`nextseek_api/batch_upload/errors.py:54-71` maps each error type to a severity and
`nextseek_api/batch_upload/errors.py:74-76` grades anything absent from that map as an
error.

Two rules live here as single definitions that several stages read. Protocol-to-SOP
resolution is stated once, with its provenance and the three stored value shapes, at
`nextseek_api/batch_upload/helpers.py:53-74`. Non-UID sample identity — which metadata field
stands in for a name, and its hash — is `nextseek_api/batch_upload/identity.py:79` and
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
(`nextseek_api/batch_upload/neo4j_sync.py:405`) and `investigations`
(`nextseek_api/batch_upload/neo4j_sync.py:438`). `assays_tbl` and `child_assays` are not
tables in that schema at all: they are an in-memory DuckDB registration and a CTE inside
`nextseek_api/batch_upload/dag.py:201-205`.

In Neo4j it merges four node labels — `Sample`
(`nextseek_api/batch_upload/neo4j_sync.py:99`), `SampleType`
(`nextseek_api/batch_upload/neo4j_sync.py:134`), `Study`
(`nextseek_api/batch_upload/neo4j_sync.py:453`) and `Investigation`
(`nextseek_api/batch_upload/neo4j_sync.py:477`) — and four edge types: `DERIVED_FROM`
(`nextseek_api/batch_upload/neo4j_sync.py:161`), `OF_TYPE`
(`nextseek_api/batch_upload/neo4j_sync.py:260`), `IN_STUDY`
(`nextseek_api/batch_upload/neo4j_sync.py:287`) and `IN_INVESTIGATION`
(`nextseek_api/batch_upload/neo4j_sync.py:501`).

### Background work and scripts

Two Celery tasks: the upload driver at `nextseek_api/batch_upload/tasks.py:18` and the
best-effort orphan pass it dispatches afterwards at
`nextseek_api/batch_upload/tasks.py:118`. The app itself routes three task-name patterns
onto two queues (`nextseek_api/batch_upload/celery_app.py:35-39`), carries one beat entry belonging to
another subsystem (`nextseek_api/batch_upload/celery_app.py:40-45`) and clamps a task at two
hours soft, 7800 seconds hard (`nextseek_api/batch_upload/celery_app.py:46-47`).

`scripts/` holds three one-time Neo4j backfills, each of which calls `django.setup()` itself
and is run as a standalone program. Two repair sample-node parent fields
(`nextseek_api/batch_upload/scripts/backfill_parent_titles.py:50` writes both lists in
lockstep, `nextseek_api/batch_upload/scripts/backfill_parent_title_hashes.py:77` fills the
hash list alone); the third recomputes the full shared-assay set onto `DERIVED_FROM` edges
from SQL and is the only one with a dry-run gate
(`nextseek_api/batch_upload/scripts/backfill_shared_assays.py:160-161`).

## Running and testing

The suite is self-contained under `tests/`, and there is no `conftest.py` anywhere beneath
the package — a `find` for that name under `nextseek_api/batch_upload` returns nothing, so
the fixtures these tests get come from `nextseek_api/conftest.py:7-10`. Run it inside the
live container, which is where the dependency set and the DB grant are:

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek sh -c \
  'cd /app && uv run --no-sync python -m pytest nextseek_api/batch_upload/tests/ \
   --no-migrations -q -p no:randomly'
```

Run 2026-09-03: 1223 passed, 26 skipped, 3 errors in 128.50s. Before trusting that, note
that the container ships its own copy of the code; on that date a per-file md5 comparison of
all 87 `*.py` files under `nextseek_api/batch_upload` against `/app` in the running
`nextseek` container showed no difference, so the lane exercised this branch's source.

The three errors all come from the module-scoped driver fixture at
`nextseek_api/batch_upload/tests/test_neo4j_integration.py:34-40`, and they cost almost the
whole runtime: the same command with that one module ignored
finished in 3.82s with an identical 1223 passed, 26 skipped. See
`nextseek_api/batch_upload/CLAUDE.md` for why that module errors instead of skipping.

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
  flag (`nextseek_api/batch_upload/config.py:111`) and stage 6 is skipped rather than
  failing (`nextseek_api/batch_upload/orchestrator.py:862`).
- `settings.MEDIA_ROOT`, the load-bearing directory this package reads and writes for four
  distinct things: uploaded workbooks (`nextseek_api/batch_upload/views.py:663`), the
  per-user job index (`nextseek_api/batch_upload/job_index.py:12-14`), resume checkpoints
  (`nextseek_api/batch_upload/tasks.py:45-49`) and the summary CSVs
  (`nextseek_api/batch_upload/orchestrator.py:579-584`).
- `nextseek_api/services/assistant.py:140`, for the CSRF-exempt session authenticator the
  ViewSet installs.
- `nextseek_api/endpoint_descriptions.py:917`, for the OpenAPI prose the actions render.
- `seek/models/seek_mirror.py:20` and `seek/seekdb.py:148`, plus
  `nextseek_api/helpers.py:89`, the three routes by which
  `nextseek_api/batch_upload/views.py:674` turns a Django session into a SEEK person id.
- `nextseek_api/assay_registration/graph.py`, imported by
  `nextseek_api/batch_upload/scripts/backfill_shared_assays.py:56-60` — the reverse of the
  edge below, and the only place the dependency runs this way.
- Optional accelerators, each with a live fallback: `orjson`
  (`nextseek_api/batch_upload/dag.py:10-16`) and `duckdb`, which falls back to pandas above
  the 250,000-row threshold (`nextseek_api/batch_upload/dag.py:189-193`). `polars`
  (`pyproject.toml:77`) and `psutil` (`pyproject.toml:79`) are imported unguarded at module
  scope instead — `nextseek_api/batch_upload/convert.py:10` and
  `nextseek_api/batch_upload/insert.py:10` are two of the five such lines — and `openpyxl`
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
  CC upload task, and `docker-compose.yml:351` runs a separate worker process off the same
  app for a different queue. Renaming or moving `celery_app` breaks all three.
- Job ownership. `nextseek_api/services/cc_assistant.py:888` registers a CC upload in this
  package's index and `nextseek_api/services/cc_assistant.py:900` gates the status endpoint
  on it, so the CC upload flow inherits this package's ownership model wholesale.
- SQL and helper reuse. `nextseek_api/assay_registration/service.py:10` and
  `nextseek_api/assay_registration/runner.py:43` take the engine;
  `nextseek_api/assay_registration/executor.py:26` takes the assay-asset writer;
  `nextseek_api/assistant/reingest_qa.py:13` and `nextseek_api/services/samples.py:17` take
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
  `docker/cc-runtime/build_context/plugins/nextseek/bin/_batch_upload_runner.py:18-21` names
  four sibling files that ship inside the agent image; they reach this code only over HTTP,
  by posting to the validate URL at
  `docker/cc-runtime/build_context/plugins/nextseek/bin/_batch_upload_client.py:217`. The
  `batch_upload_preparation` label that turns up in the same searches is a router task
  family, not a reference to this directory either
  (`dmac_assistant/build_context/route_capabilities.json:319`).

See `nextseek_api/batch_upload/CLAUDE.md` for the invariants, the traps, and the one command
to run.
