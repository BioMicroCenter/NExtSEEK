# Working in `api_app/`

## Invariants

- **This package must keep importing cleanly even though it serves no traffic.**
  `dmac/urls.py:13` binds it into the root URLconf at module scope, so any exception
  raised while loading `api_app/urls.py`, `api_app/views.py` or `api_app/serializers.py`
  takes the whole site down at boot — including the routes registered at
  `dmac/urls.py:29` that have nothing to do with this app.
- **Do not move `MySQLdb` out of `api_app/dbconn_mysql.py:3` into a lazy import.** That
  module is the only connector either lineage script constructs (`api_app/updateTrees.py:18`,
  `api_app/remoteJob/updateTrees.py:19`), and both run outside Django's request cycle
  where an ORM connection is not available; replacing it with the ORM would silently
  change which of the two schemas at `dmac/settings.py:517-518` is written.
- **`/var/lib/mysql-files/` is load-bearing input, not scratch.**
  `api_app/dbconn_mysql.py:11` pins it and `api_app/dbconn_mysql.py:356` writes the only
  backup of `seek_sample_tree` there through `SELECT … INTO OUTFILE`, which the MySQL
  server executes with its own filesystem; because `startup/templates/db.env.template:2`
  puts that server in a separate container, changing the path or the container's
  `secure_file_priv` makes the wipe at `api_app/updateTrees.py:1148` unrecoverable.
- **Deleting this app is not free.** Three methods in the SEEK sample mixin have their
  only call sites in `api_app/views.py:75`, `api_app/views.py:202` and
  `api_app/views.py:210`; removing the directory without also removing
  `seek/sample/api.py:244`, `seek/sample/api.py:38` and `seek/sample/api.py:280` leaves
  three uncallable public methods behind, and removing `dmac/urls.py:13` and
  `dmac/settings.py:173` is required or Django refuses to start.

## Landmines

- **Five of the nine scripts cannot even be imported, so "it's dead code" is not the
  whole story — some of it never ran on this interpreter at all.** Probed 2026-09-03
  inside the `nextseek` container on Python 3.14.7: `api_app/api_calls.py`,
  `api_app/api_fileSubmit.py`, `api_app/api_sampleSubmit.py` and
  `api_app/db_network_calls.py` raise `ModuleNotFoundError: No module named 'urllib2'`
  from `api_app/api_calls.py:8`, `api_app/api_fileSubmit.py:8`,
  `api_app/api_sampleSubmit.py:8` and `api_app/db_network_calls.py:8`, and
  `api_app/api_sampleParser.py` raises `SyntaxError` before that, at
  `api_app/api_sampleParser.py:963`, on a Python 2 `print` statement. Any port that
  starts by "just fixing the imports" is porting code with no working baseline to
  compare against.
- **A FileMaker credential is committed here in plain text.** `api_app/api_sampleParser.py:913`
  passes a username and password literal to a JDBC driver, alongside an MIT-internal
  host at `api_app/api_sampleParser.py:912` and a jar path on a lab fileshare at
  `api_app/api_sampleParser.py:908`. It is in git history regardless of what the file
  does today, so treat it as needing rotation rather than deletion.
- **`api_app/serializers.py:45` references an undefined name and raises `NameError` on
  every call.** The fork of this file at `nextseek_api/serializers.py:45` carries the
  identical defect and is pinned there by a passing test
  (`nextseek_api/tests/test_serializers.py:74-81`), so fixing one copy without the other
  turns that test red.
- **Lines 1-61 of this package's serializer module were copied into `nextseek_api`.**
  Any edit to `api_app/serializers.py:24-61` that is not mirrored into
  `nextseek_api/serializers.py:24-61` silently forks the live API's representation of a
  sample away from this one.
- **Two view methods read an attribute DRF's request object does not have.**
  `api_app/views.py:97` and `api_app/views.py:117` name `request.DATA`, and the pinned
  3.17.1 (`uv.lock:842-843`) exposes only the lowercase form — checked 2026-09-03 with
  `hasattr(rest_framework.request.Request, "DATA")` in the `nextseek` container, which
  returned False against True for `data`. Re-enabling the URLconf therefore gives those
  two handlers an `AttributeError` on every POST and PUT rather than a validation
  error.
- **The token path authenticates to SEEK with a Django password hash.**
  `api_app/views.py:26` hands `user.password` to `SeekDB`, which stores it at
  `seek/seekdb.py:23` and forwards it to the SEEK client at `seek/seekdb.py:24`, so every
  SEEK call made on behalf of a token holder presents a PBKDF2 digest where a password is
  expected and fails upstream rather than locally.
- **`api_app/updateTrees.py:58` interpolates a sample UID straight into SQL**, and the
  value comes from `json_metadata` rows read at `api_app/updateTrees.py:425`, so a
  crafted UID already stored in SEEK reaches the database as query text on the next
  lineage rebuild.
- **The rebuilder exits non-zero after succeeding.** `api_app/updateTrees.py:1173` calls
  `exit(1)` unconditionally at the end of the dispatch block, so any cron entry, CI step
  or shell `&&` chain wrapping this script records a failure on a completely successful
  run and, if it retries, wipes and rebuilds the table again.
- **Whether `cronjob` deletes the table depends on a filesystem check that cannot
  succeed.** `api_app/dbconn_mysql.py:353-354` looks for the export on the *client*
  filesystem while the file itself is produced by the MySQL server, which
  `startup/templates/db.env.template:2` puts in its own container;
  `api_app/dbconn_mysql.py:360` then removes a backup name that was never created, so the
  `except` at `api_app/dbconn_mysql.py:364-367` returns 0 and the guard
  at `api_app/updateTrees.py:1143-1145` aborts before the delete. The destructive path is
  currently blocked by a bug, not by a design decision, and repairing the export makes
  `api_app/updateTrees.py:1148` reachable.
- **`api_app/dbconn_mysql.py:24` returns before 23 lines of live-looking code.**
  Everything from `api_app/dbconn_mysql.py:26` to `api_app/dbconn_mysql.py:48` — the
  `dbconnect.txt` lookup and its production/development switch — is unreachable, so a
  reader who believes that block is what selects the database will be wrong about which
  schema every raw query in this package hits.
- **An unknown database selector silently resolves to NExtSEEK rather than raising.**
  `api_app/dbconn_mysql.py:50-56` falls through to the NExtSEEK alias for any value that
  is not exactly `SEEK` or `NEXTSEEK`, and `api_app/db_network_calls.py:227` and
  `api_app/db_network_calls.py:252` both pass `SEEK_IN`, so that reporter reads the wrong
  schema and prints an empty or wrong adjacency table instead of failing.
- **`api_app/db_network_calls.py:104-119` uses four names the module never defines** —
  a `mysql.connector` alias, `DB_NAME`, `errorcode` and `cnx` — so its
  `CREATE DATABASE` helper is a `NameError` waiting behind an `except` clause that
  catches a class it also cannot resolve.
- **The `remoteJob/` copy of the connector raises where the top-level one does not.**
  `api_app/remoteJob/dbconn_mysql.py:345` calls `datetime.datetime.now()` but the module
  imports no `datetime` (`api_app/remoteJob/dbconn_mysql.py:2-5`), and the reference sits
  *above* the `try` at `api_app/remoteJob/dbconn_mysql.py:352`, so the failure escapes
  instead of returning 0 like its counterpart.
- **The two connector copies quote SQL values differently.**
  `api_app/dbconn_mysql.py:139-146` emits single-quoted strings and bare numerics while
  `api_app/remoteJob/dbconn_mysql.py:138-145` double-quotes everything including numbers,
  so copying an INSERT helper between them changes how MySQL types the column under
  `ANSI_QUOTES`.
- **140 lines of the rebuilder are disabled by triple-quoting rather than by `#`.**
  Five bare string blocks — `api_app/updateTrees.py:761-776`,
  `api_app/updateTrees.py:826-841`, `api_app/updateTrees.py:907-957`,
  `api_app/updateTrees.py:993-1033` and `api_app/updateTrees.py:1085-1100`, measured
  2026-09-03 by tokenizing the file — hold code that `grep` and ordinary syntax
  highlighting both present as live, so a matched line read without checking for an
  enclosing bare quote gives a wrong account of what this script does.
- **The `remoteJob/` script indexes a metadata key SEEK does not write.** Its live save
  loop at `api_app/remoteJob/updateTrees.py:952` reads the lowercase spelling, while the
  successor's own SQL at `api_app/updateTrees.py:58` and SEEK's own writer at
  `seek/sample/upload.py:480` both spell it `UID`, and its matching loop at
  `api_app/updateTrees.py:990` reads it that way, so the older script raises `KeyError`
  on the first row rather than rebuilding anything. One
  lowercase read survives in the successor too, at `api_app/updateTrees.py:365`, but only
  inside a helper whose sole reference sits at `api_app/updateTrees.py:405` in a function
  nothing calls; wiring that function back in reintroduces the same failure.
- **This app owns no models but still has a migrations package.** `api_app/models.py:4-6`
  declares none and a `find api_app/migrations -name '*.py' ! -name '__init__.py'`
  returns nothing, so `makemigrations` will happily create the app's first migration out
  of a model someone adds here and it will apply against whichever schema
  `dmac/settings.py:517-518` routes to, not the SEEK one this code actually reads.
- **A reference doc still describes a cron wiring this repo does not have.**
  `docker/cc-runtime/docs/nextseek/09-nextseek.md:141-142` shows a `CRONJOBS` list
  invoking the rebuilder nightly; no such setting exists in this branch — a
  `/usr/bin/grep -rn "CRONJOBS"` over the worktree, discounting `.git/`, `.superpowers/`
  and this pair, finds that identifier nowhere else — so an agent reading that doc will
  assume the lineage table is refreshed automatically when nothing refreshes it.

## Test command

There is no lane for this boundary. Both collectors were run against it on 2026-09-03
inside the `nextseek` container and neither found anything:

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest api_app --no-migrations -q'
```

reported `no tests collected in 0.11s`, and the Django runner reported
`Ran 0 tests in 0.000s` followed by `NO TESTS RAN`. Use the pytest form for a regression
check on the fork instead — it is the only automated coverage that touches code
identical to this package's:

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/tests/test_serializers.py --no-migrations -q'
```

2026-09-03: 28 passed, 1 warning in 0.07s.

## See also

See `api_app/README.md` for the routing evidence, the three module groups, the full
inbound and outbound import lists, and why `remoteJob/` is the older snapshot.
