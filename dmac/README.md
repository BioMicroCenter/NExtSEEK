# `dmac/`

## What this is

The Django **project package** for NExtSEEK: the settings modules, the root URLconf,
the ASGI and WSGI entry points the container starts, one DRF schema hook, one template
context processor, and the view functions that hand authentication to SEEK.

It is deliberately not a Django *application*. It declares no models and no migrations,
and its own name is absent from `INSTALLED_APPS` (`dmac/settings.py:144-180`), so nothing
here is discovered by app autoloading; every module reaches the runtime either by an
explicit dotted-path string in settings or by an import from another package.

It is also, unexpectedly, a library. Beside the project wiring sit twelve modules of
pre-Django data access and file conversion: raw MySQLdb connections, an EasyUI datagrid
backend, Excel and CSV helpers, and a large pile of type coercion. Twenty-five non-test
modules elsewhere import them, nearly all under `seek/`.

Measured 2026-09-03 with `find` and `wc -l` over this directory: 24 Python files,
5,571 lines. Ten of them are the project wiring (874 lines), `dmac/views.py` is the
SEEK auth and home view module (333 lines), `dmac/__init__.py` is empty, and the
remaining twelve are the legacy layer (4,364 lines).

Two files here are compiled Python 2.7 bytecode — `dmac/conversion.pyc` and
`dmac/__init__.pyc`, whose first four bytes are the 2.7 magic number `03 f3 0d 0a`,
read with `od`. A `find` for `*.pyc` outside any `__pycache__` across the whole worktree
returns exactly those two.

## Surface

The surface here has two shapes, and they need separating.

**As a Django project package**, the surface is not functions but *names that
configuration strings resolve*: the settings module a process selects, the URLconf that
module names, and the ASGI or WSGI callable the web server imports. The edge in that
direction runs through env vars and process arguments, not imports.

**As a Python library**, the legacy modules have an ordinary import surface, and the edge
is `from dmac.X import Y` lines in other packages.

### Settings modules

Four settings modules live here, plus one gitignored overlay this package defines the
contract for but does not contain.

| Module | Role | Selected by |
|---|---|---|
| `dmac.settings` | The real one | `manage.py:9`, `dmac/wsgi.py:6-7`, `dmac/asgi.py:12-15`, `pyproject.toml:147` |
| `dmac.test_settings` | SQLite in memory | `DJANGO_SETTINGS_MODULE`, e.g. `scripts/run_tests.sh:46` |
| `dmac.test_settings_realstack` | Real MySQL, fast hashers | `DJANGO_SETTINGS_MODULE`, e.g. `startup/dev/run_full_test_lane.sh:244` |
| `dmac.attribute_performance_settings` | Real MariaDB benchmark DBs | `scripts/attribute_api_test.sh:468` |
| `dmac/local_settings.py` | Per-host overlay, gitignored | `dmac/settings.py:249-253` |

`dmac.settings` is canonical: the three entry points above all default
`DJANGO_SETTINGS_MODULE` to it, and it is what a bare `pytest` uses, because
`pyproject.toml:147` names it rather than the test module. Two of the others open with a
star-import of it, at `dmac/test_settings.py:18` and `dmac/test_settings_realstack.py:26`;
the third opens with a star-import of the test module instead
(`dmac/attribute_performance_settings.py:3`) and so inherits both. None of the four is a
fresh settings file; each is a diff.

`dmac.test_settings` swaps both database aliases to `:memory:` SQLite
(`dmac/test_settings.py:21-30`), switches to MD5 password hashing
(`dmac/test_settings.py:33-35`), and supplies the values other packages read bare from
`django.conf.settings` at import (`dmac/test_settings.py:38`, `dmac/test_settings.py:50-68`).
`dmac.test_settings_realstack` exists because a non-`nextseek_api` migration emits
MySQL-only `CHARACTER SET` DDL that SQLite cannot parse, which its docstring records at
`dmac/test_settings_realstack.py:4-7`.
`dmac.attribute_performance_settings` refuses an in-memory database outright
(`dmac/attribute_performance_settings.py:6-10`) and points both aliases at real MariaDB
(`dmac/attribute_performance_settings.py:27-30`).

`dmac/local_settings.example.py` is a five-name sample — participating projects, test
cases, publish URL, publish stats file and smart-search URL
(`dmac/local_settings.example.py:3-25`). It is the surface `seek/views.py` needs at
import, which is why two test modules reproduce exactly those names by hand rather than
copying the file: `seek/tests/test_seek_public_links.py:98-103` and
`seek/tests/test_views_children_uids_sql.py:50-55`. It is *not* the surface a working
install needs; see `dmac/CLAUDE.md` for what it omits and what that costs. The overlay a
real install runs is rendered from `startup/templates/local_settings.py.template` by
`startup/steps/config.py:165-170`.

`dmac/settings.py` reads the deployment from the environment throughout: debug
(`dmac/settings.py:14`), secret key (`dmac/settings.py:20`), both MySQL aliases
(`dmac/settings.py:28-46`), Neo4j behind a guard (`dmac/settings.py:539-544`), and the
whole SEEK URL family behind another (`dmac/settings.py:546-564`). The two guards exist so
a native, env-less host keeps the values `local_settings.py` set, which the comment at
`dmac/settings.py:509-516` explains. Cross-app routing between the two aliases is
delegated out of this package entirely, to `seek.dbrouters.CustomRouter`
(`dmac/settings.py:299`).

### URL root

`dmac/settings.py:103` computes `ROOT_URLCONF` from the package name, which resolves to
`dmac.urls`. Three blocks register patterns — `dmac/urls.py:21-30`, `dmac/urls.py:38-40`
and `dmac/urls.py:47-58` — for eleven top-level entries, counted 2026-09-03 off Django's
own resolver inside the application image. A fourth block at `dmac/urls.py:42-45` adds a
language-switch route only when `USE_MODELTRANSLATION` is on, and it is off here, so it
contributes none of the eleven. Because
`USE_I18N` is `False` (`dmac/settings.py:56`), the `i18n_patterns` wrapper at
`dmac/urls.py:21` adds no language prefix; verified 2026-09-03 by resolving `/logout`
inside the application image, which reached `dmac.views.logout_seek` with no prefix.

| Pattern | Goes to |
|---|---|
| `^login`, `^logout$`, `^signup/` | `dmac/views.py:110`, `dmac/views.py:170`, `dmac/views.py:267` |
| `^admin/` | `dmac/urls.py:26` |
| `^seek/` | `seek.urls`, at `dmac/urls.py:27` |
| `^nextseek_api/` | `nextseek_api.urls`, at `dmac/urls.py:29` |
| `^media/(?P<path>.*)$` | Django's static serve, at `dmac/urls.py:37-40` |
| `^$` | `dmac/views.py:285` |
| `^accounts/signup/` | `dmac/views.py:267` again, at `dmac/urls.py:54` |
| `^` | `mezzanine.urls`, at `dmac/urls.py:55` |
| `^accounts/login/` | `dmac/views.py:110` again, at `dmac/urls.py:56` |

The media route is unusual and its reason is recorded in place: `DEBUG` is off under
Docker, so Django's `static()` helper is a no-op and the serve view is wired directly
(`dmac/urls.py:32-36`). Both error handlers are Mezzanine's (`dmac/urls.py:60-61`).

There is one legacy include, and it is disabled rather than deleted: `^api/` pointing at
`api_app.urls` is commented out at `dmac/urls.py:28`, while `dmac/urls.py:13` still
imports that URLconf at module scope and `dmac/settings.py:173` still installs the app.
So `api_app`'s models and its URLconf module both load on every boot, and none of its
routes are reachable.

### Server entry points

`dmac/wsgi.py:9` builds the WSGI callable and `dmac/asgi.py:25-29` the ASGI one. The ASGI
module is the interesting half: it must call `get_asgi_application()` before importing
channels routing, so Django's app registry is populated first, and it says so at
`dmac/asgi.py:17-18`. It then mounts `nextseek_api.assistant.routing.websocket_urlpatterns`
(`dmac/asgi.py:23`) behind channels' auth stack. `dmac/settings.py:183` names
`dmac.asgi.application` as `ASGI_APPLICATION` and `dmac/settings.py:184-188` configures an
in-process channel layer.

The deployment picks between them at run time. `docker/scripts/entrypoint.sh:61-65`
defaults to `daphne` on `dmac.asgi:application` and falls back to `gunicorn dmac.wsgi`
when `NEXTSEEK_SERVER=gunicorn`; the comment at `docker/scripts/entrypoint.sh:55-60`
records that the WSGI path loses the assistant WebSocket and the frontend then polls.
Gunicorn's own settings — four workers, a 1200-second timeout — are in `gunicorn.conf.py:1-6`.

### The legacy data-access layer

Four groups. Three of them are imported by name from other packages; the connection
layer is internal and is reached through the table layer.

- **Type and file coercion.** `dmac/conversion.py` (59 module-level functions),
  `dmac/csv_excel.py` (28) and `dmac/iocsv.py` (18), counted 2026-09-03 by grepping each
  file for lines beginning `def `. Together they account for 33 of the 60 external import
  lines counted the same day, `dmac/conversion.py` alone for 18.
- **The connection abstraction.** `dmac/dbconnection.py:9` is a facade that picks a
  backend from a string at `dmac/dbconnection.py:13-24`; `dmac/dbconn_django.py:22` is the
  ORM-and-raw-cursor implementation, and `dmac/dbconn_mysql.py:13` a direct MySQLdb one.
  Only test modules name any of the three from outside, among them
  `nextseek_api/tests/test_search_filters_sql_binding.py:182` and
  `nextseek_api/tests/test_search_filters_sql_binding.py:250`.
- **The table abstraction.** `dmac/dbtable.py:33` is the base class, wiring a form, a
  field mapping and a `DBconnection` together at `dmac/dbtable.py:51`. Twelve of the
  thirteen `seek/dbtable_*.py` modules subclass it; the exception is
  `seek/dbtable_sample.py`, which a grep of that file for `from dmac.dbtable` shows does
  not import it.
- **Four concrete tables that bypass all of the above.** `dmac/dbtable_clades.py:22`,
  `dmac/dbtable_internalassays.py:21`, `dmac/dbtable_assaysinternalassays.py:20` and
  `dmac/dbtable_sampletypesclades.py:21` each open their own MySQLdb connection per query,
  for example at `dmac/dbtable_sampletypesclades.py:42-46`, and pull the credentials from
  the settings module directly rather than from `django.conf`. See `dmac/CLAUDE.md` for
  why that matters.

`dmac/datagrid_custom.py:26` is the EasyUI datagrid backend behind `dmac/dbtable.py`.
Its one SQL builder binds every client value (`dmac/datagrid_custom.py:138-179`), and its
own docstring notes it has no in-repo caller (`dmac/datagrid_custom.py:152-155`), which
still holds: grepping every `*.py` in the worktree for `sqlQuery_select_filters` returns
six lines, and the only one that calls the method is the test helper at
`seek/tests/test_datagrid_sql_binding.py:38`. The other five are the definition itself
(`dmac/datagrid_custom.py:138`), two prose mentions beside it
(`dmac/datagrid_custom.py:87` and `dmac/datagrid_custom.py:153`), and two docstring lines
in that test module (`seek/tests/test_datagrid_sql_binding.py:1` and
`seek/tests/test_datagrid_sql_binding.py:8`).

### Two named hooks

`dmac/openapi_hooks.py:1` drops `/seek/` and assistant paths from the generated OpenAPI
schema; it is reached only by the dotted string at `dmac/settings.py:424-426`.
`dmac/context_processors.py:11` exposes the browser-facing SEEK base URL and a derived
password-reset link to every template; it is reached only by the dotted string at
`dmac/settings.py:126`. No production code imports either module by name: grepping every
`*.py` in the worktree for `openapi_hooks` returns the settings line and a comment at
`scripts/validate_viewset_conventions.py:37`, and grepping for `dmac.context_processors`
returns the settings line plus `seek/tests/test_seek_urls_context.py:19` and
`seek/tests/test_seek_urls_context.py:57`, which are tests.

## Running and testing

This directory has no test package of its own. A `find` beneath `dmac/` for any
`conftest.py`, any `tests` directory, or any file matching `test_*.py` or `*_test.py`
returns only `dmac/test_settings.py` and `dmac/test_settings_realstack.py`, which are
settings modules and contain no tests.

What exercises it lives elsewhere: `ci/gate/` walks the resolver this package builds, and
five test modules under `seek/tests/` and `nextseek_api/tests/` import the legacy layer
directly.

The lane, run over a read-only mount of this worktree in the application image:

```
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest ci/gate \
    seek/tests/test_dbtables.py seek/tests/test_datagrid_sql_binding.py \
    seek/tests/test_seek_urls_context.py seek/tests/test_clade_counts_group_by.py \
    nextseek_api/tests/test_search_filters_sql_binding.py \
    -q -p no:cacheprovider --no-header --no-migrations
```

I ran it on 2026-09-03: **11 failed, 61 passed, 1 xfailed in 7.57s**, 13.1s wall including
container start. None of the eleven failures is in this boundary and none is caused by
`--no-migrations`. Ten are `nextseek_api/tests/test_search_filters_sql_binding.py`, whose
helper at `nextseek_api/tests/test_search_filters_sql_binding.py:74` calls a name-mangled
private method that `seek.sample.table.DBtable_sample` no longer has. The eleventh,
`seek/tests/test_dbtables.py:64`, asserts at least 13 discovered `DBtable` subclasses and
finds 12. Both are stale expectations against `seek/`.

The `mkdir` on the first line is load-bearing, not tidying; `dmac/CLAUDE.md` says what
happens without it.

## Depends on / depended on by

Depends on, outside this directory:

- Mezzanine, at import in three entry points, for `real_project_name` —
  `manage.py:7`, `dmac/wsgi.py:4`, `dmac/asgi.py:10` — and for the dynamic-settings pass
  at `dmac/settings.py:265-270` that rewrites `INSTALLED_APPS` and `MIDDLEWARE`.
- `seek/`, which this package imports at module scope in both directions of the
  legacy layer: `dmac/views.py:25-26` for the SEEK login client and the `People` model,
  and `dmac/dbtable_clades.py:14-15` and its three siblings for `seek.seekapi` and
  `seek.models`. That makes the dependency mutual, not one-way.
- `seek.dbrouters.CustomRouter`, named as a string at `dmac/settings.py:299`, which
  decides which of the two database aliases every ORM query uses.
- `nextseek_api.assistant.routing`, imported at `dmac/asgi.py:23` for the assistant
  WebSocket patterns, and `nextseek_api.urls`, imported at `dmac/urls.py:14`.
- `nextseek_api.attributes.tests.performance_worker_telemetry`, imported at
  `dmac/attribute_performance_settings.py:35-36`. See `dmac/CLAUDE.md` about this one.
- `mysqlclient`, imported as `MySQLdb` at module scope in seven of this directory's
  files, including `dmac/dbconnection.py:3`. That single import is why the legacy layer
  cannot be exercised on a host without a C toolchain.
- `pandas` and `numpy`, at module scope in each of the four raw-MySQLdb table modules,
  for example `dmac/dbtable_clades.py:11-12`, and `pandas` again at `dmac/iocsv.py:1`.
- `dateutil.parser`, at `dmac/conversion.py:2`, whose declaration in `pyproject.toml:85`
  is commented out. It resolves only as a transitive dependency, recorded in `uv.lock:2618`.
- `openpyxl` at `dmac/csv_excel.py:2`, `xlwt` at `dmac/iocsv.py:3`, and `simplejson` at
  `dmac/csv_excel.py:5` and `dmac/views.py:9`.

Depended on by. Derived 2026-09-03 by grepping every `*.py` in the worktree for a line
beginning with an import of `dmac`, then removing this directory's own 27 internal hits
with a pattern anchored at `dmac/` with no leading `./`. That detail matters: this host's
`grep -rn` emits paths without a leading `./`, so the same exclusion written with one
removes zero lines — measured the same day, 87 hits before any filter, 87 after the `./`
form, 60 after the anchored one. Those 60 lines sit in 30 files, 7 of the lines in test
modules.

- `seek/` is the overwhelming consumer: 24 of the 25 non-test importer files.
  `seek/dbtable_projects.py:7` is representative of the twelve subclass modules, seven
  modules under `seek/sample/` take the Excel and CSV helpers
  (`seek/sample/download.py:3-15`), `seek/views/admin.py:5-8` takes all four
  raw-MySQLdb table classes, and `seek/views/projects.py:5-7` takes two of them.
- `nextseek_api/services/entity_tree.py:153` is the only non-`seek` non-test importer, and
  it is a lazy in-function import inside an `except` fallback that runs only when a direct
  SQL query has already failed (`nextseek_api/services/entity_tree.py:149-152`).
- Django itself reaches four modules by dotted string rather than import:
  `dmac.urls` via `dmac/settings.py:103`, `dmac.asgi.application` via
  `dmac/settings.py:183`, `dmac.context_processors.seek_urls` via `dmac/settings.py:126`,
  and `dmac.openapi_hooks.exclude_seek_paths` via `dmac/settings.py:424-426`.
- The settings module names are configuration, not imports, and appear in
  `pyproject.toml:147`, `.github/workflows/ci-pytest.yml:50`, `scripts/run_tests.sh:46`,
  `ci/gate/live_routes.py:18`, `scripts/dump_routes.py:20`,
  `nextseek_api/batch_upload/celery_app.py:12` and `nessie_tests/sources.py:249`.
- `dmac/local_settings.py`, which this directory never contains, is bind-mounted by path
  into the `nextseek` service at `docker-compose.yml:30` and into four attribute and assay
  worker services at `docker-compose.yml:346`, `docker-compose.yml:380`,
  `docker-compose.yml:415` and `docker-compose.yml:450`. It is written by
  `startup/steps/config.py:167`, inspected by `startup/steps/validate.py:63`, and required
  by `scripts/run_tests.sh:37-41`.

Three kinds of hit are excluded and are worth naming. `api_app/dbconn_mysql.py:13` defines
its own `DBconn_mysql` class and is a separate copy, not a consumer of
`dmac/dbconn_mysql.py`. `nextseek_api/eval/task6_settings.py` is a fifth settings module
in this repo and does not live here. And `nextseek_api/permissions.py:10` names
`dmac.views.userSynchronization` inside a comment explaining a security gate, not in code.

See `dmac/CLAUDE.md` for the invariants, the traps, and the one command to run.
