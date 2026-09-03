# Working in `dmac/`

Everything in this directory runs on every boot of every process in the stack, and most
of the repo's other documented boundaries cite a file here. A mistake in this package does
not fail locally; it fails somewhere else.

## Invariants

- **Changing `dmac/settings.py` changes all four settings modules at once.** The other
  three star-import it — `dmac/test_settings.py:18` and
  `dmac/test_settings_realstack.py:26` directly, and
  `dmac/attribute_performance_settings.py:3` through the test module. A name that raises
  during its import takes out CI, every container lane and the running site together.
- **A new env-driven setting belongs inside one of the two guards.** `dmac/settings.py:539`
  gates the Neo4j block and `dmac/settings.py:546` the SEEK block on an env var being
  present. An assignment moved above a guard overwrites whatever `local_settings.py`
  put there on any host where that variable is unset, which is exactly the regression the
  comment at `dmac/settings.py:509-516` was written to prevent.
- **`SEEK_PUBLIC_URL` keeps its unconditional module-level assignment.**
  `dmac/settings.py:536` defines it outside every guard, and `dmac/settings.py:556`
  reassigns the real value inside the SEEK guard. Consumers read the attribute bare, so
  deleting the outer assignment turns an env-less boot into an `AttributeError` before a
  single request is served.
- **The local overlay is executed into the settings namespace, not imported.**
  `dmac/settings.py:249-253` `exec`s the file with `globals()`, so it both defines
  settings and can read everything defined above it. The Schema RAG defaults depend on
  that ordering: `dmac/settings.py:485-486` and `dmac/settings.py:491-492` only assign
  when the overlay has not already. Rewriting this as a star-import silently reverses
  who wins.
- **Permission checks read `is_superuser`, never `is_staff`.** This module hands every
  SEEK account staff rights: `dmac/views.py:80` on the registration branch and
  `dmac/views.py:97` on the update branch, so the flag marks "has logged in", not "is an
  admin". A gate written against it admits every authenticated user.
- **The URLconf and the ASGI application are resolved from strings built out of the
  directory name.** `dmac/settings.py:68-69` derives the package name from the filesystem
  path and `dmac/settings.py:103` interpolates it into `ROOT_URLCONF`. Renaming this
  directory changes which URLconf Django loads, with nothing to catch it at edit time.
- **Nothing may be registered after the Mezzanine catch-all.** `dmac/urls.py:55` includes
  `mezzanine.urls` under a bare `^`, which matches every path. The comment at
  `dmac/urls.py:49-53` states the rule and `dmac/urls.py:54` obeys it; a project route
  appended to the end of the list resolves to Mezzanine's page handler instead, and looks
  like a 404 rather than a wiring bug.

## Landmines

- **`dmac/settings.py:498-499` calls `os.makedirs` while the settings module is still
  importing, on two paths under the checkout.** Over a read-only mount that is fatal
  before pytest collects anything. Measured 2026-09-03 by bind-mounting an empty
  directory over `/src/schema_rag` in the application image: `OSError: [Errno 30]
  Read-only file system: '/src/schema_rag/duckdb'`, raised from `dmac/settings.py:498`
  inside the star-import at `dmac/test_settings.py:18`. The workaround is to create both
  directories on the host first, which is why the gate recipe opens with a `mkdir` —
  `ci/gate/live_routes.py:16` — and `exist_ok=True` then swallows the read-only failure.
  With that `mkdir` done the identical command passed: 5 passed in 8.28s, same day.
- **`dmac/settings.py:303-304` does the same thing to `LOG_DIR`, defaulting it to
  `/app/logs`.** That path exists only inside the image, so a GitHub runner or a bare host
  checkout hits a `PermissionError` at import. `dmac/test_settings.py:12-16` is the fix and
  explains itself; anything that imports `dmac.settings` directly must set `LOG_DIR`
  itself, as `.github/workflows/ci-pytest.yml:53` does.
- **`dmac/test_settings.py:51-55` supplies a Neo4j that is complete but fictional,** so a
  test gating on "configured" rather than "reachable" runs instead of skipping. Exactly
  one module in the tree gates that way: `nextseek_api/batch_upload/tests/test_neo4j_integration.py:29-31`
  builds its flag from `Neo4jConfig.from_django_settings`, which reads the same dict at
  `nextseek_api/batch_upload/config.py:87`. Measured 2026-09-03 with `--network none` and
  `-x`: one test, 1 error in 61.60s, spent in the driver's retry ladder. Grepping every
  `*.py` for `skipif` and then narrowing to the files that also mention Neo4j returns six
  modules, and that is the only one whose flag comes from the Django setting;
  `nextseek_api/batch_upload/tests/test_identity_drift_integration.py:250-251` looks
  similar but adds an opt-in env gate and does skip.
- **Neither the example overlay nor the test settings defines `NEXTSEEK_CHAT_CONFIG`,
  and the assistant reads it bare.** `nextseek_api/services/assistant.py:243` returns
  `settings.NEXTSEEK_CHAT_CONFIG` with no `getattr` default; grepping
  `dmac/local_settings.example.py` and `dmac/test_settings.py` for that name returns
  nothing in either file. Measured 2026-09-03 under `dmac.test_settings`: attribute access
  and `_select_chat_config` both raise `AttributeError: 'Settings' object has no attribute
  'NEXTSEEK_CHAT_CONFIG'`. The only in-repo file that builds one for a real install is
  `startup/templates/local_settings.py.template:19`; the standalone lane copy is
  `startup/dev/lane_local_settings.py:19`. Copy the example file and Django boots, `seek/`
  serves, and the first assistant turn 500s.
- **Four modules here read the settings module directly instead of `django.conf`.**
  `dmac/dbtable_clades.py:10` is representative, and `dmac/dbtable_clades.py:19-20` binds
  the database dicts at import. Under `dmac.test_settings`, measured 2026-09-03,
  `django.conf.settings.DATABASES['default']['ENGINE']` is `django.db.backends.sqlite3`
  while that module's own copy is `django.db.backends.mysql` — so
  `dmac/dbtable_clades.py:42-46` opens a raw MySQLdb socket to the real server from inside
  a suite that believes it is hermetic. `override_settings` cannot reach these four
  either. The siblings are `dmac/dbtable_internalassays.py:12`,
  `dmac/dbtable_assaysinternalassays.py:11` and `dmac/dbtable_sampletypesclades.py:12`.
- **Importing `dmac.views` writes a file into the process's current directory.**
  `dmac/views.py:17-22` calls `logging.basicConfig` with the relative filename
  `dmac.logs`, outside the `LOGGING` dict at `dmac/settings.py:306-366`. It is a no-op
  only when something has already attached a handler to the root logger, which pytest
  does — which is why the gate lane survives a read-only mount and a plain
  `python -c 'import dmac.views'` from that same mount does not. Both measured 2026-09-03;
  the second raised `OSError: [Errno 30] Read-only file system: '/src/dmac.logs'`, and
  from a writable directory it left a `dmac.logs` behind.
- **`GET /logout` raises `NameError` on every request.** `dmac/views.py:174` calls
  `reverse`, and a grep for that name over `dmac/views.py` matches only that line — it is
  never imported. Confirmed 2026-09-03 by calling `logout_seek` with a stub session in the
  application image: `NameError: name 'reverse' is not defined`. There is also no URL named
  `index` to reverse: grepping every `*.py` in the worktree for `name="index"` and
  `name='index'` returns nothing. The route is live at `dmac/urls.py:23`, so the 500 is
  reachable; today's navbar link goes to Mezzanine's logout instead
  (`themes/NextSeek/templates/accounts/includes/user_panel.html:38`), which is why nobody
  has hit it.
- **The failing line runs *after* a shell-out.** `dmac/views.py:172` executes
  `rm -r <username>` relative to the server's working directory whenever the session
  carries a username, and only then reaches the crash. Anything that reintroduces a
  `/logout` link deletes a directory before returning a 500.
- **`{% url "login_seek" %}` produces a URL this package does not serve.**
  `dmac/urls.py:56` registers that name last, so reversing it wins there, but the pattern
  sits after the catch-all and never resolves. Measured 2026-09-03: the name reverses to
  `/accounts/login/`, which resolves to `mezzanine.accounts.views.login`, while the working
  view is at `/login`. Latent for now — a grep over every `*.html` and `*.py` in the
  worktree finds no template reversing `login_seek`, and only
  `themes/NextSeek/templates/login.html:328` reverses `signup_seek`, which was fixed by
  registering it at `dmac/urls.py:54`.
- **`dmac/templates/pages/` is dead and cannot be loaded.** This package is not in
  `INSTALLED_APPS` (`dmac/settings.py:144-180`), so the app-directories loader at
  `dmac/settings.py:131` never sees it, and the only filesystem directory configured is
  the theme's (`dmac/settings.py:108-110`). Measured 2026-09-03: `get_template` raises
  `TemplateDoesNotExist` for all three names, and grepping every `*.py` and `*.html` in the
  worktree for those filenames returns nothing. Editing them changes no page.
- **What you read in `INSTALLED_APPS` and `MIDDLEWARE` is not what runs.** Mezzanine's
  `set_dynamic_settings` (`dmac/settings.py:265-270`) rewrites both. Measured 2026-09-03
  inside the image: 33 declared apps become 36, gaining `filebrowser_safe`,
  `grappelli_safe` and `django_comments` and moving `django.contrib.admin` to the end; the
  three entries of `OPTIONAL_APPS` at `dmac/settings.py:229-235` that are not installed are
  dropped. Sixteen declared middleware become 14, losing both Mezzanine cache layers
  (`dmac/settings.py:196` and `dmac/settings.py:210`) because no `CACHES` is configured.
  `django.middleware.common.CommonMiddleware` is declared twice, at
  `dmac/settings.py:195` and `dmac/settings.py:199`, and both survive into the running
  stack. Reason about ordering from a dump, never from the source tuple.
- **`dmac/attribute_performance_settings.py:35-36` imports a module that does not exist.**
  A `find` for any file named `performance_worker_telemetry*` anywhere in the worktree
  returns nothing, and grepping every `*.py` for that name matches only that import line.
  Setting `ATTRIBUTE_WORKER_TELEMETRY_RESULTS` under that settings module therefore fails
  during settings import, before Django starts.
- **The home page hides its own failures.** `dmac/views.py:306-331` wraps each model
  lookup in `except Exception: pass` over a context pre-seeded with zeros at
  `dmac/views.py:294-302`. A broken database renders a clean dashboard reading zero
  samples, zero projects and zero files rather than an error, so "the counts are wrong"
  is the only symptom you will get.
- **`dmac/dbconn_mysql.py` is 285 lines of unreachable code.** Its sole reference is the
  lazy import at `dmac/dbconnection.py:20`, taken only on the branch at
  `dmac/dbconnection.py:19`. Grepping every `*.py` in the worktree for the quoted string
  `"MYSQL"` or `'MYSQL'` returns two lines: that branch, and an unrelated assertion at
  `chat_nextseek/tests/test_e2e_playwright_trio.py:38`. No call site passes it. Its module scope
  still reads settings at `dmac/dbconn_mysql.py:10-11`, so it costs an import without ever
  serving a query. `api_app/dbconn_mysql.py:13` is a different class in a different
  package; do not treat the two as one.
- **The SEEK password is written into the Django session.** `dmac/views.py:128` stores it
  alongside the username. Sessions are database-backed — `django.contrib.sessions` is
  installed at `dmac/settings.py:152` and no `SESSION_ENGINE` overrides the default — and
  `seek/dbrouters.py:3-7` sends any model without a `_DATABASE` attribute to the `default`
  alias, which is MySQL (`dmac/settings.py:28-37`). A dump of that table, or any code path
  that logs a session, carries plaintext SEEK credentials for every logged-in user.
- **`dateutil` is used but not declared.** `dmac/conversion.py:2` imports it, and the
  declaration in `pyproject.toml:85` is commented out; it resolves only as a transitive
  dependency, listed under other packages in `uv.lock:2618`. Dropping `pandas` therefore
  breaks `dmac/conversion.py:2`, which carried 18 of the 60 external import lines when
  they were counted on 2026-09-03, more than any other module here.

## Test command

The cheapest check that this package still boots and still builds the URL tree, over a
read-only mount of the worktree:

```
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest ci/gate -q -p no:cacheprovider
```

Run 2026-09-03: 5 passed, 3 warnings in 8.28s, about 14s wall. It imports both settings
modules, executes the whole URLconf and diffs the live resolver against the CI registry,
so a broken include or a renamed view fails it. It proves nothing about the legacy layer.

## See also

- See `dmac/README.md` for the four settings modules and how each is selected, the URL
  table, the two server entry points, the legacy module groups, and the wider lane with
  its results.
- See `ci/CLAUDE.md` for the gate's own landmines and the state of the pytest baseline.
- See `nextseek_api/CLAUDE.md` for how the API app sits under this URLconf.
- See `startup/README.md` for what renders the gitignored overlay this package expects.
- See the repo-root `CLAUDE.md` for the stack, the two assistant engines and the
  development workflow.
