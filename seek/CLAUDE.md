# Working in `seek/`

## Invariants

- **A model that names a SEEK table must set `_DATABASE`.** The router returns
  `"default"` for any model that lacks the attribute
  (`seek/dbrouters.py:3-4`), and the NExtSEEK schema holds an empty shadow copy
  of all 19 mirrored tables — `seek/migrations/0001_initial.py:345-346` builds
  the `Samples` one, and comparing the `CREATE TABLE` against the `INSERT INTO`
  table names in `startup/seed/dmac.sql.gz` on 2026-09-03 found all 19 created
  and not one of them populated. Dropping the attribute therefore does not
  raise; it silently starts reading an empty table.
- **The reverse also holds: a NExtSEEK-owned table must not be given
  `SEEK_DATABASE`.** `seek/models/nextseek.py:1-5` records that three of the
  eleven deliberately set no `_DATABASE` at all, and says why moving them would
  move data between schemas.
- **A model whose DDL is applied out of band must stay `managed = False`.**
  `seek/models/nextseek.py:151-158` gives the reason in full: because
  `allow_migrate` returns `None` for this app label (`seek/dbrouters.py:14-16`),
  a managed model would let the next unrelated `makemigrations` create the table
  on both aliases.
- **Every column identifier reaching the search SQL builder is allowlisted.**
  `seek/search.py:16-35` is the set and `seek/search.py:39-47` raises on a miss,
  deliberately rather than dropping the clause — a silently dropped filter emits
  a `WHERE` with no filter in it, which is a data leak rather than an error.
- **Values in that SQL are bound, never interpolated.** The one interpolation
  left in the children-UID path is the schema name from settings, and the
  comment at `seek/views/admin.py:86-91` says exactly that. Reintroducing a
  value into the statement text reopens issue #78.
- **`_deleteOneSample` owns cascade integrity, and SEEK does not.** Deleting a
  sample means eight raw statements across eight tables in one transaction
  (`seek/sample/table.py:68-86`), because the ORM has no relations here to
  cascade along.
- **SEEK is the authorization boundary, not Django auth.** Project membership
  and supervisor status come back from SEEK's own API through
  `seek/decorators.py:1-15`; the three decorators there are the only sanctioned
  preamble, and their JSON envelope is byte-compatible with what the JavaScript
  under `seek/templates/` tests.
- **`seek/responses.py:1-19` pins key order, not just key names.** It uses
  `simplejson` with `default=str` on purpose; swapping in the stdlib `json`
  changes `Decimal` and `NaN` handling and reorders the envelope the frontend
  parses.

## Landmines

- **Four modules open a real MySQL socket that Django's test database never
  intercepts.** They build the connection from `settings.DATABASES` by hand —
  `seek/sample/core.py:33-36` and `seek/sample/core.py:229-230` are two sites in
  the first, then `seek/views/admin.py:82-83`, `seek/views/upload.py:153-154`
  and `seek/timeline/core/database.py:7-16`. Django fills the sqlite alias's missing keys with
  empty strings, so under `dmac.test_settings` the call does not fail on a
  KeyError — measured 2026-09-03 in the throwaway container, it raises
  `OperationalError (2002, "Can't connect to local server through socket
  '/run/mysqld/mysqld.sock'")`. On any machine where MySQL is listening on the
  default socket, a suite everyone believes is hermetic reads and writes a real
  database.
- **`seek/timeline/` takes its database NAME from an environment variable that
  nothing sets.** `seek/timeline/core/database.py:19-21` overwrites the pool's
  database with `os.getenv(database_name)`, defaulting to `DB_NAME`, and
  `seek/timeline/services/nhp_service.py:34` asks for `DB_NAME1`. Neither is ever
  set: searching the whole worktree for `DB_NAME` or `DB_NAME1` followed by `=`
  or `:`, excluding `.git/`, `.superpowers/`, `startup/.venv/` and the unrelated
  `SESSION_DB_NAME`, returns zero lines. The pool therefore connects with no
  schema selected, and only the fully-qualified queries can work.
- **Importing `seek.urls` reconfigures the root logger for the whole process.**
  `seek/timeline/services/timeline_service.py:15` calls `logging.basicConfig` at
  module scope, and it is pulled in by `seek/views/timeline.py:6`, which
  `seek/views/__init__.py:18` imports, which `seek/urls.py:3` imports. Django's
  own logging config is applied before the URLconf loads, so this wins.
- **The two module-level dicts are shared across requests and across users.**
  `seek/views/shared.py:26` is the render context that `seek/views/upload.py:21`
  and `seek/views/assets.py:28` both mutate — the docstring at
  `seek/views/shared.py:1-8` records it as a deliberately preserved leak — and
  `seek/timeline/services/nhp_service.py:15` is a second one. Two concurrent
  uploads write the same object.
- **`viewtablename` is dead in 11 of the 13 table classes and will raise if
  used.** Every table class here is constructed with the literal
  `'seek_development'` as its database name -- 13 such calls as of 2026-09-03,
  from `grep -rn "'seek_development'" seek/ --include=*.py`, of which
  `seek/dbtable_projects.py:11` is one -- which is not the schema the alias
  points at, and eleven of them then build
  `viewtablename` as a string (`seek/dbtable_projects.py:16`). `retrieveRecords`
  hands that string to code that does `tablemodel.objects`
  (`dmac/dbtable.py:211`, `dmac/dbconn_django.py:405-408`), so calling it raises
  `AttributeError` on a `str`. Only `seek/dbtable_content_blobs.py:35` and
  `seek/sample/table.py:39` assign the model instead.
- **`Sample_types_context.tags` is the only `db_column` override in the whole
  package**, mapping the field `tags` onto a capitalised column
  (`seek/models/nextseek.py:124`) that the seed really does spell `Tags`.
  Searching every `.py` file under this directory for `db_column` returns that
  one line. A caller that names the column instead of the field gets a
  `FieldError`, and a caller that swallows it renders an empty page rather than
  an error.
- **34 tests fail and one module cannot be collected**, against two entries in
  `ci/pytest-baseline.txt:307-308`, one of which names
  `seek/timeline/services/nhp_cache_test.py` — a file that no longer exists
  (`find seek -name 'nhp_cache_test.py'` returns nothing; it was renamed at
  `seek/timeline/services/nhp_cache_cli.py:2-4`). Five causes, all from the two
  package splits, and none of them a product defect:
  - 17 failures patch name-mangled privates such as
    `_DBtable_sample__storeSample`, which the mixin split had to rename to a
    single underscore to survive — `seek/sample/upload.py:95` and
    `seek/sample/upload.py:370` are two of them.
  - 6 more import `SAMPLE_FILTER_MAPPING` from the old module path; the shim at
    `seek/dbtable_sample.py:2` re-exports only two names, and the constant now
    lives at `seek/sample/constants.py:12`.
  - 10 patch module-level names on the views package —
    `seek.views.SeekDB` and `seek.views.settings` — which
    `seek/views/__init__.py:6-8` warns explicitly does not reach the call site.
  - `seek/tests/test_dbtables.py:64` asserts the walk still finds 13 `DBtable`
    subclasses; it finds 12, because the thirteenth now declares its class in
    `seek/sample/table.py:31` and the walk filters on declaring module.
  - `seek/tests/test_urls.py:15` uses a relative import, and the empty
    `__init__.py` at the worktree root makes pytest name the module after the
    checkout directory, so Django rejects the freshly re-imported models.
- **`seek/views.py` does not exist.** The views are the package
  `seek/views/`, and a citation of the old path cannot be resolved to anything.
  44 files across the tree still spell it, measured 2026-09-03 by
  `grep -rl 'seek/views\.py'` excluding `.superpowers/`, `.git/` and this
  directory's own two documents; some of those are deliberate history, but
  `architecture.md:61` is a live pointer at a line number in a file that is
  gone.
- **Credentials go into a shell command line, and TLS verification is off.**
  `seek/seekapi.py:21` interpolates the SEEK username and password into a `curl`
  string that `seek/seekapi.py:37-40` runs with `shell=True`; `-k` there and
  `verify=False` at `seek/seekapi.py:113-115` disable certificate checks. The
  password is visible in the process table and a metacharacter in it changes the
  command.
- **`seek/templates/content.embed.html` is never the file that renders.**
  `themes/NextSeek/templates` is the filesystem `DIRS` entry
  (`dmac/settings.py:108-110`) and its loader runs before the app-directories one
  (`dmac/settings.py:129-131`), so the theme copy wins. Comparing the two
  directories on 2026-09-03 found this is the only name they share, so it is the
  only file here you can edit with no visible effect.
- **`seek/templates/pages/dmac.logs` is a committed production stack trace**,
  not a template — it leaks the deployment's filesystem layout
  (`seek/templates/pages/dmac.logs:3-5`) into a repository that is public. Two
  `.html.bk` files sit beside it in the same directory.
- **`seek/tests/test_dbtables.py:46` cites `/workspace/deslopify/LATENT_BUGS.md`**,
  an absolute path outside this repository. `seek/views/shared.py:6` and
  `seek/models/nextseek.py:32` cite the same document by numbered entry. Nothing
  in the tree resolves those numbers, so the bug descriptions in the comments are
  all a reader gets.

## Test command

```
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest seek --continue-on-collection-errors -q \
  -p no:cacheprovider
```

Run 2026-09-03: 34 failed, 488 passed, 29 skipped, 2 xfailed, 1 error in 10.24s.
`--continue-on-collection-errors` is load-bearing; without it the single
collection error aborts the session and nothing executes. Do not reach for
`uv run pytest` on this host — `uv sync` cannot build `mysqlclient` here.

## See also

- See `seek/README.md` for the model-to-table map, the two SEEK integration
  paths, and the dependency lists in both directions.
- See `dmac/README.md` for the `DBtable` base class and the conversion helpers
  every module here leans on.
- See `nextseek_api/README.md` for the consumer that reaches furthest into this
  package.
- See `startup/README.md` for the install step that applies the out-of-band DDL.
- See `ci/gate/live_routes.py:11-25` for the container recipe the test command
  above is taken from.
