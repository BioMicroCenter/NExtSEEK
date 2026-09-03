# `nextseek_api/`

## What this is

The Django app that owns every URL under the `/nextseek_api/` prefix, mounted once at
`dmac/urls.py:29`. It is installed under the bare label `nextseek_api`
(`dmac/settings.py:178`), and its `AppConfig` does nothing but set a default primary-key
type (`nextseek_api/apps.py:4-6`).

Almost none of the behaviour lives here. This directory is the **aggregation shell**: a
DRF router, one shared SEEK HTTP client, one permission class, one pydantic schema
module, and four legacy ViewSets. Everything else is a subpackage, and five of those
carry their own README/CLAUDE pair. This file routes to them and does not repeat them.

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

**`nextseek_api/views.py` is a re-export hub, not a view module.** It runs to
`nextseek_api/views.py:960`, and the block at `nextseek_api/views.py:43-66` does nothing but import
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
serve routes — schema, Swagger UI and ReDoc — each with an explicitly declared permission
class (`nextseek_api/urls.py:65`, `nextseek_api/urls.py:72-76`, `nextseek_api/urls.py:77`).
The Swagger route overrides the stock template to add an effective-identity banner, for
the reason set out at `nextseek_api/urls.py:66-71`. `app_name` is set at
`nextseek_api/urls.py:7`, so every reverse of these routes is namespaced.

**The shared library, by module.** 15 `.py` files sit directly in this directory as of
2026-09-03. This table locates them; the behaviour claims are in the prose around it.

| Module | What it holds |
|---|---|
| `nextseek_api/helpers.py` | `SeekAPIClient` (`nextseek_api/helpers.py:123`), the auth resolver `resolve_seek_auth` (`nextseek_api/helpers.py:89`), the UTF-8-safe Basic header builder (`nextseek_api/helpers.py:18-40`), pagination (`nextseek_api/helpers.py:392-395` and `nextseek_api/helpers.py:398`), and a title-to-SEEK-id resolver (`nextseek_api/helpers.py:409`) |
| `nextseek_api/models.py` | pydantic request/response schemas, plus the ORM re-exports described above |
| `nextseek_api/endpoint_descriptions.py` | the `*_DESC` prose constants, in the fixed heading order its docstring sets out (`nextseek_api/endpoint_descriptions.py:1-8`) |
| `nextseek_api/permissions.py` | `IsSuperUser`, and the docstring explaining why it is not DRF's `IsAdminUser` (`nextseek_api/permissions.py:6-17`) |
| `nextseek_api/serializers.py` | the few DRF serializers that predate the pydantic convention (`nextseek_api/serializers.py:6` and `nextseek_api/serializers.py:64-74`) |
| `nextseek_api/views.py` | the re-export block, two project-scope helpers (`nextseek_api/views.py:107` and `nextseek_api/views.py:129`), and four locally defined ViewSets |
| `nextseek_api/urls.py` | the router and the three documentation routes |
| `nextseek_api/apps.py`, `nextseek_api/admin.py` | the app config; an admin module that registers nothing (`nextseek_api/admin.py:1-3`) |
| `nextseek_api/conftest.py` | DRF client and mock-SEEK fixtures shared by every test below this directory |
| `nextseek_api/seek_api.py`, `nextseek_api/seek_api_helpers.py`, `nextseek_api/example.py` | superseded SEEK-call sketches; see CLAUDE.md for why they are not live |
| `nextseek_api/tests.py`, `nextseek_api/tests/` | see CLAUDE.md — only one of these two is reachable |

Two facts about the description module, both established 2026-09-03: it holds 73
constants assigned at column zero, running from `nextseek_api/endpoint_descriptions.py:14`
to `nextseek_api/endpoint_descriptions.py:1190`, and it contains no import statement at
all — a grep for a line beginning with `import` or `from` over that one file returns
nothing, which is why a description edit can never break an import cycle.

**The four ViewSets defined here.** `SampleTreeViewSet` (`nextseek_api/views.py:180`) and
`AdminSampleViewSet` (`nextseek_api/views.py:648`) are routed and live. `NHPViewSet`
(`nextseek_api/views.py:395`) and `SampleQueryViewSet` (`nextseek_api/views.py:549`) are
not: their registrations are the two commented-out lines at `nextseek_api/urls.py:15-16`.
Both live ViewSets scope data per caller rather than by Django role, and each says so
where it decides — `nextseek_api/views.py:268-274` for the tree, and
`nextseek_api/views.py:749-756` for the export.

**The subpackages.** Each is documented in its own directory; one clause each, no more:

- [`cc_assistant/`](cc_assistant/README.md) — the per-turn route decision and the
  sandboxed agent that serves the `container_cc` route, and the only child installed as a
  Django app in its own right (`dmac/settings.py:179`).
- [`assistant/`](assistant/README.md) — the deterministic NExtSEEK chat engine's Django
  side: its ORM models, its progress websocket (`dmac/asgi.py:23`), and the granular ops
  contract at `nextseek_api/assistant/CONTRACT.md:1`.
- [`attributes/`](attributes/README.md) — the native Attribute API package
  (`nextseek_api/attributes/__init__.py:1`).
- [`batch_upload/`](batch_upload/README.md) — the batch sample upload pipeline
  (`nextseek_api/batch_upload/__init__.py:1`).
- [`eval/`](eval/README.md) — the judgment, disposition and conservation machinery for
  routing evaluation (`nextseek_api/eval/__init__.py:1`).

Four more subdirectories have no pair of their own and are described only here.
`nextseek_api/services/` holds 25 Python modules, counted 2026-09-03, and is the
directory a new ViewSet module belongs in
(`.claude/skills/nextseek-viewset/SKILL.md:102`). `nextseek_api/assay_registration/`
is the batch assay-registration job, whose ViewSet
(`nextseek_api/assay_registration/views.py:121`) is superuser-gated
(`nextseek_api/assay_registration/views.py:123`). `nextseek_api/schema_rag/` ingests the
OpenAPI document into a per-session DuckDB and, as a side effect of being imported,
resolves forward references back in this package's schema module
(`nextseek_api/schema_rag/__init__.py:1-11`). `nextseek_api/batch_delete/` is pydantic
only: a find over that directory returns three files — an empty `__init__.py`, a
`models.py` whose classes all subclass `BaseModel` (`nextseek_api/batch_delete/models.py:17`),
and one test module — with no `views.py` among them, and a grep for `django.db` or
`models.Model` over the same directory returns nothing.

## Running and testing

There is no lane scoped to the shell alone. The app's tests live in `nextseek_api/tests/`
— 71 `test_*.py` modules, counted 2026-09-03 — and cover this directory and `services/`
together.

**The lane I ran, 2026-09-03.** A throwaway container from the stack image, with this
worktree bind-mounted read-only, copied to a writable path inside the container, run
under `dmac.test_settings` (SQLite in memory, `dmac/test_settings.py:20-30`) and with no
network:

```
docker run --rm --network none -v "$PWD":/src:ro -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
  -w / nextseek-nextseek:latest bash -lc \
  'cp -a /src /build && cd /build && /app/.venv/bin/python -m pytest nextseek_api/tests/ -q'
```

Result: 80 failed, 2066 passed, 2 skipped, 3 errors, 10 subtests passed in 40.29s. The
copy step is what makes a read-only checkout usable at all: `dmac/settings.py:497-498`
creates two directories beside the settings file at import time, so a plain read-only
mount raises `OSError` before Django finishes loading. Not every failure is
environmental; the two families I reproduced are written up in CLAUDE.md.

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
- `ci/routes.py:1-14` is the CI route registry — 86 of its lines name a path under this
  prefix, counted 2026-09-03 — and `ci/gate/test_route_registry.py:29-39` blocks CI when
  Django resolves a route the registry does not declare.
- `.coveragerc:2` sets this package as the sole coverage source, and `.coveragerc:4-9`
  names the files excluded from it.
- Two consumers reach this app over HTTP and not by import:
  `chat_frontend/src/lib/services/chatApi.ts:78` builds request URLs against the prefix,
  and the CC agent's plugin catalog stores endpoint paths as data at
  `docker/cc-runtime/build_context/plugins/nextseek/context/ops.json:1`.
- `chat_nextseek/src/chat_nextseek/context/nextseek_api.yaml:1-5` is a captured copy of
  the document this app's schema route generates, not a live read of it, so it drifts.

What a hit here is NOT. A grep for this package name returns far more than the list
above, and three groups were excluded deliberately. Everything under `build_tools/` and
`nessie_tests/` imports `nextseek_api.cc_assistant.*` or `nextseek_api.assistant.*` and
never a module of this shell. `chat_nextseek/src/chat_nextseek/helpers/tools/nextseek_api.py`
is named after this app but is a chat-side HTTP tool that imports nothing from it.
`dmac/asgi.py:23` and `dmac/attribute_performance_settings.py:36` reach into
`nextseek_api.assistant` and `nextseek_api.attributes` respectively, which are their
edges and not this one's.

See `nextseek_api/CLAUDE.md` for the invariants this structure rests on and the traps it
sets.
