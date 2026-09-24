# `nextseek_api/`

## What this is

The Django app that owns every URL under the `/nextseek_api/` prefix, mounted once at
`dmac/urls.py:29`. It is installed under the bare label `nextseek_api`
(`dmac/settings.py:178`), and its `AppConfig` does nothing but set a default primary-key
type (`nextseek_api/apps.py:4-6`).

Almost none of the behaviour lives here. This directory is the **aggregation shell**: a
DRF router, one shared SEEK HTTP client, one permission class, one pydantic schema
module, and four legacy ViewSets. Everything else is a subpackage, and three of those
(`assistant/`, `attributes/`, `batch_upload/`) carry their own README/CLAUDE pair. This
file routes to them and does not repeat them.

Two structural facts explain most of what is surprising here.

**`nextseek_api/models.py` declares no Django model.** 204 of its 207 classes subclass
pydantic's `BaseModel`, starting at `nextseek_api/models.py:24`, and the other three are
string enums (`nextseek_api/models.py:1355`); counted 2026-09-03. The file is the
request/response schema library that `@extend_schema` publishes, not an ORM module. The
ORM models Django registers under this app label are defined in child packages and
merely re-exported here, at `nextseek_api/models.py:3-9` and again on the file's last
line, `nextseek_api/models.py:2708`, whose own comment says the import exists "so Django
discovers it". Enumerating `get_models()` for this app config on 2026-09-03 returned 17
models, none of them defined in `nextseek_api/models.py`.

**`nextseek_api/views.py` is a re-export hub, not a view module.** The block at `nextseek_api/views.py:43-66` does nothing but import
ViewSets from `services/` and from the child packages and alias them into this namespace,
so that `nextseek_api/urls.py:5` can register them all from one module.

## Surface

This boundary is a Python package whose real public surface is a **URL tree**, so the
surface is read in two directions: the router registrations that publish it, and the
shared library modules every child imports. Both are derived below from the files
themselves rather than recalled.

**The published surface.** `nextseek_api/urls.py:10` builds one `DefaultRouter`, and the
registration block that follows runs from `nextseek_api/urls.py:14` to
`nextseek_api/urls.py:42`: 25 active `router.register(...)` calls and 2 more commented out
(`nextseek_api/urls.py:15-16`), counted 2026-09-03. The router is included last, under a
bare `^` prefix, at `nextseek_api/urls.py:80`. Ahead of it sit the three drf-spectacular
serve routes (schema, Swagger UI and ReDoc), each with an explicitly declared permission
class (`nextseek_api/urls.py:65`, `nextseek_api/urls.py:72-76`, `nextseek_api/urls.py:77`).
The Swagger route overrides the stock template to add an effective-identity banner, for
the reason set out at `nextseek_api/urls.py:66-71`. `app_name` is set at
`nextseek_api/urls.py:7`, so every reverse of these routes is namespaced.

**The shared library, by module.** This table locates the `.py` files that sit directly in
this directory; the behaviour claims are in the prose around it.

| Module | What it holds |
|---|---|
| `nextseek_api/helpers.py` | `SeekAPIClient` (`nextseek_api/helpers.py:123`), the auth resolver `resolve_seek_auth` (`nextseek_api/helpers.py:89`), the UTF-8-safe Basic header builder (`nextseek_api/helpers.py:18-40`), pagination (`nextseek_api/helpers.py:392-395` and `nextseek_api/helpers.py:398`), and a title-to-SEEK-id resolver (`nextseek_api/helpers.py:409`) |
| `nextseek_api/models.py` | pydantic request/response schemas, plus the ORM re-exports described above |
| `nextseek_api/endpoint_descriptions.py` | the `*_DESC` prose constants, in the fixed heading order its docstring sets out (`nextseek_api/endpoint_descriptions.py:1-8`) |
| `nextseek_api/authentication.py` | `CsrfExemptSessionAuthentication`, the session authenticator most ViewSets install, and the `{"errors": [...]}` envelope builder `_error_response`; `nextseek_api/services/assistant.py` re-exports both |
| `nextseek_api/permissions.py` | `IsSuperUser`, and the docstring explaining why it is not DRF's `IsAdminUser` (`nextseek_api/permissions.py:6-17`) |
| `nextseek_api/serializers.py` | the few DRF serializers that predate the pydantic convention (`nextseek_api/serializers.py:6` and `nextseek_api/serializers.py:64-74`) |
| `nextseek_api/views.py` | the re-export block, two project-scope helpers (`nextseek_api/views.py:107` and `nextseek_api/views.py:129`), and four locally defined ViewSets |
| `nextseek_api/urls.py` | the router and the three documentation routes |
| `nextseek_api/apps.py`, `nextseek_api/admin.py` | the app config; an admin module that registers nothing (`nextseek_api/admin.py:1-3`) |
| `nextseek_api/conftest.py` | DRF client and mock-SEEK fixtures shared by every test below this directory |
| `nextseek_api/seek_api.py`, `nextseek_api/seek_api_helpers.py`, `nextseek_api/example.py` | superseded SEEK-call sketches; see CLAUDE.md for why they are not live |
| `nextseek_api/tests.py`, `nextseek_api/tests/` | see CLAUDE.md: only one of these two is reachable |

Two facts about the description module, both established 2026-09-03: it holds 73
constants assigned at column zero, running from `nextseek_api/endpoint_descriptions.py:14`
to `nextseek_api/endpoint_descriptions.py:1190`, and it contains no import statement at
all: a grep for a line beginning with `import` or `from` over that one file returns
nothing, which is why a description edit can never break an import cycle.

**The four ViewSets defined here.** `SampleTreeViewSet` and `AdminSampleViewSet` are routed
and live; `AdminSampleViewSet` is only the deprecated `admin/samples/retrieve/` alias of the
download API, whose handler and data path are in `nextseek_api/services/sample_retrieve.py`. `NHPViewSet`
(`nextseek_api/views.py:395`) and `SampleQueryViewSet` (`nextseek_api/views.py:549`) are
not: their registrations are the two commented-out lines at `nextseek_api/urls.py:15-16`.
Both live ViewSets scope data per caller rather than by Django role, and each says so
where it decides: `nextseek_api/views.py:268-274` for the tree, and `handle_retrieve` in
`nextseek_api/services/sample_retrieve.py` for the download.

**The subpackages.** Each is documented in its own directory; one row each.

<!-- BEGIN DOCS-MAP:folders -->
| Folder | What it does | Docs |
|---|---|---|
| `assay_registration/` | batch registration of samples as SEEK assay members: three superuser-gated routes, a job row, a drain loop, a Neo4j label recompute | `nextseek_api/assay_registration/README.md` |
| `assistant/` | the API half of the assistant: ORM models (including the `eval_*` tables), wire models, the progress WebSocket consumer, session and pipeline adapters, OpenAPI descriptions, `excel_export.py`, and the granular-op HTTP contract `CONTRACT.md` | `nextseek_api/assistant/README.md` |
| `attributes/` | the native attribute API: a catalog plus plan-then-execute mutations | `nextseek_api/attributes/README.md` |
| `batch_delete/` | pydantic models for delete eligibility; no views, no ORM | this row |
| `batch_upload/` | bulk sample ingest from a workbook or JSON rows, stages 0 to 7, stage 6 syncing this job's samples through `graph_sync/`; owns the shared Celery app | `nextseek_api/batch_upload/README.md` |
| `cc_assistant/` | Django shell for Container-CC; engine at `NessieAI/cc/`. Never rename the app label or the Celery tasks `cc_assistant.upload` and `cc_assistant.sweep_cc_summaries` | `NessieAI/cc/README.md` |
| `graph_search/` | the engine behind `POST /nextseek_api/samples/graph_search/`: scope from MySQL membership, a Cypher query builder, the catalog cache and page hydration | `nextseek_api/graph_search/README.md` |
| `graph_sync/` | the one writer of the Neo4j sample graph (schema v1.2) and the sync that keeps it equal to MySQL: the outbox every other writer enqueues to, the drain loop, the nightly targeted sync, the weekly full sync, the drift check and the `graph_sync` command | `nextseek_api/graph_sync/README.md` |
| `management/` | management commands, including the four loops the app entrypoint starts by name (`dispatch_attribute_outbox`, `recover_attribute_sync_jobs`, `run_assay_registration_jobs` and `graph_sync --loop`), the harness entry point `nessie` and the staging-sweep recovery `cc_sweep_staging`; deleting a loop's shim removes a command the entrypoint calls | this row |
| `migrations/` | the one migration chain for the app and every subpackage; it forks, so check the heads first | `nextseek_api/CLAUDE.md` |
| `services/` | the ViewSet and service layer; a new ViewSet module goes here | `nextseek_api/services/README.md` |
| `tests/` | the app's tests, including `repo_guards/` (repo infrastructure guards: compose, the app entrypoint, the settings env, the build context and the issue conventions) | this row |
<!-- END DOCS-MAP:folders -->

The AI engine that `assistant/` and `cc_assistant/` serve is in `NessieAI/`: see `NessieAI/README.md`.

## Running and testing

There is no lane scoped to the shell alone. The app's test modules live in
`nextseek_api/tests/` and cover this directory and `services/` together.

Run them in a throwaway container from the stack image, with the checkout bind-mounted
read-only, copied to a writable path inside the container, run under `dmac.test_settings`
(SQLite in memory, `dmac/test_settings.py:20-30`) and with no network:

```
docker run --rm --network none -v "$PWD":/src:ro -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
  -w / nextseek-nextseek:latest bash -lc \
  'cp -a /src /build && cd /build && /app/.venv/bin/python -m pytest nextseek_api/tests/ -q'
```

The copy step is what makes a read-only checkout usable at all: `dmac/settings.py:507-508`
creates two directories beside the settings file at import time, so a plain read-only
mount raises `OSError` before Django finishes loading. The suite is not green, and not
every failure is environmental; the known failure families are written up in CLAUDE.md.

`scripts/run_tests.sh:44-47` is the supported wrapper for the same idea, and is what to
use once you have the two things it needs: a `dmac/local_settings.py` inside the checkout
(`scripts/run_tests.sh:37-41`) and a compose directory holding the gitignored `docker/*.env`
files (`scripts/run_tests.sh:20`). Neither is present in a fresh worktree.

The convention gate for this directory is a separate and much cheaper command, given with
its own current result in CLAUDE.md.

## Depends on / depended on by

Depends on, outside this directory. Derived by reading every import line in the 15
modules directly under `nextseek_api/`:

- `seek/`, at module scope in six of those modules, so the SEEK app must import cleanly
  before this one loads at all: `nextseek_api/helpers.py:10`, `nextseek_api/models.py:16-17`,
  `nextseek_api/serializers.py:2-3`, `nextseek_api/seek_api_helpers.py:5-6`,
  `nextseek_api/example.py:1`, and `nextseek_api/views.py:24-28`.
- Django settings read at import time rather than per request: `nextseek_api/views.py:33-34`
  binds two database aliases, and `nextseek_api/seek_api.py:7` binds the SEEK base URL.
- `MySQLdb` and the `neo4j` driver, both imported unconditionally, at
  `nextseek_api/views.py:6` and `nextseek_api/views.py:19-22`.
- `drf_spectacular`, which supplies the three documentation views imported at
  `nextseek_api/urls.py:4` as well as the `@extend_schema` decorator used throughout.
- Its own children, which is the direction that makes this package a shell: the ViewSet
  imports at `nextseek_api/views.py:43-66` and the ORM re-exports at
  `nextseek_api/models.py:3-9`.

Depended on by. Derived from a repo-wide grep for the package name over every `.py` file
plus a second pass over non-Python files, then grouped. Test modules are omitted, and so
is everything that reaches only into a child package, which is that child's edge and not
this one's:

- Django itself. `dmac/urls.py:14` imports this app's URLconf, and `dmac/settings.py:471`
  scopes the entire CORS configuration to this URL prefix by regular expression, so
  remounting the app elsewhere silently drops CORS for the frontend dev server.
- `seek/`, in the reverse direction, which is why the `seek` imports above must stay put:
  `seek/seekapi.py:111` and `seek/seekapi.py:126` import `basic_auth_header` from inside
  method bodies, and the comment at `seek/seekapi.py:109-110` says a module-scope import
  there would cycle.
- `scripts/validate_viewset_conventions.py:22-26` reads three description modules by path,
  one of which is `nextseek_api/endpoint_descriptions.py`, and
  `scripts/validate_viewset_conventions.py:157-183` pins five function names in
  `nextseek_api/views.py` as grandfathered.
- `ci/routes.py:1-14` is the CI route registry, which declares every path under this
  prefix, and `ci/gate/test_route_registry.py:29-39` blocks CI when Django resolves a
  route the registry does not declare.
- `.coveragerc:2` sets this package as the sole coverage source, and `.coveragerc:4-9`
  names the files excluded from it.
- Two consumers reach this app over HTTP and not by import:
  `NessieAI/chat_frontend/src/lib/services/chatApi.ts:78` builds request URLs against the prefix,
  and the CC agent's plugin catalog stores endpoint paths as data at
  `NessieAI/docker/cc-runtime/build_context/plugins/nextseek/context/ops.json:1`.
- `NessieAI/chat_nextseek/src/chat_nextseek/context/nextseek_api.yaml:1-5` is a captured copy of
  the document this app's schema route generates, not a live read of it, so it drifts.

What a hit here is NOT. A grep for this package name returns far more than the list
above, and three groups were excluded deliberately. Everything under `NessieAI/` reaches this
shell only through the back-edges named in `NessieAI/CLAUDE.md`: `nextseek_api.models` (from
`NessieAI/schema_rag/`) and `nextseek_api.conftest` (from the harness container tests). All
other edges go into child packages. `NessieAI/chat_nextseek/src/chat_nextseek/helpers/tools/nextseek_api.py`
is named after this app but is a chat-side HTTP tool that imports nothing from it.
`dmac/asgi.py:23` and `dmac/attribute_performance_settings.py:36` reach into
`nextseek_api.assistant` and `nextseek_api.attributes` respectively, which are their
edges and not this one's.

See `nextseek_api/CLAUDE.md` for the invariants this structure rests on and the traps it
sets.
