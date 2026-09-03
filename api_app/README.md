# `api_app/`

## What this is

`api_app` is NExtSEEK's original REST API app, kept in the tree after the API surface
moved to `nextseek_api`. It is 20 tracked files totalling 6,660 lines, all Python
(counted 2026-09-03 with `git ls-files api_app | xargs wc -l`).

Its central fact is a single commented-out line. The app is still installed at
`dmac/settings.py:173`, and `dmac/urls.py:13` still imports its URLconf at module scope,
but the `include()` that would mount that URLconf under `/api/` is commented out at
`dmac/urls.py:28` — while the replacement app is mounted live one line below, at
`dmac/urls.py:29`. So `api_app/urls.py` is executed on every boot and its seven patterns
are built and then discarded: **no HTTP request reaches any code in this directory.**
Measured against the running local stack on 2026-09-03, `/api/samples/` and
`/api/rest-auth/login/` both return 404 while `/nextseek_api/samples/` returns 401.

The directory holds three groups that have almost nothing to do with each other:

1. **The Django/DRF app** — `urls.py`, `views.py`, `serializers.py`, plus empty
   `models.py`, `admin.py` and `tests.py` scaffolding. Imported at boot, never routed.
2. **Seven standalone command-line utilities** written against Python 2: four HTTP
   clients of group 1, two checksum tools, and one schema reporter.
3. **A sample-lineage batch job** — `dbconn_mysql.py` and `updateTrees.py` — that talks
   raw MySQL and rewrites the `seek_sample_tree` table, plus an older duplicate of both
   under `remoteJob/`.

Nine of the twenty files carry a `__main__` guard, so the boundary is better read as a
script pile with a vestigial Django app attached than as an app with helper modules.

## Surface

"Surface" has three separate meanings here and they give three different answers, so
they are listed separately rather than merged into one module table.

### The routed surface: empty

`api_app/urls.py:12-20` declares seven patterns — two sample routes, two data-file
routes, a `dj_rest_auth` mount, and two upload endpoints — wrapped by
`format_suffix_patterns` at `api_app/urls.py:22`. None of them is registered anywhere.
Nothing else in the tree mounts them: an exhaustive `/usr/bin/grep -rn "api_app"` over
the worktree, excluding `.git/`, this session's `.superpowers/` scratch and this pair's
own two documents, leaves `dmac/urls.py:28` as the sole line placing this URLconf inside
an `include()`, and every surviving hit is confirmed tracked with `git ls-files`.

That absence has one consequence outside the boundary. `dj_rest_auth` is in
`INSTALLED_APPS` at `dmac/settings.py:172` and is mounted exactly once, at
`api_app/urls.py:17`, so this deployment publishes no token-issuing endpoint at all even
though `TokenAuthentication` is the first default authenticator at `dmac/settings.py:370`.
The CI/CD spec reached the same conclusion independently at
`docs/superpowers/specs/2026-09-01-nextseek-ci-cd-full-spec.md:315-317`.

### The imported surface: what actually executes at boot

`import api_app.urls` at `dmac/urls.py:13` pulls in `api_app/views.py`, which pulls in
`api_app/serializers.py` and four `seek` modules. So these load on every start:

| File | What loads | Live? |
|---|---|---|
| `api_app/urls.py` | seven patterns, then thrown away | imported, unrouted |
| `api_app/views.py` | ten view classes, six named by the URLconf | imported, unreachable |
| `api_app/serializers.py` | three serializer classes | imported, unreachable |
| `api_app/apps.py` | `ApiAppConfig` (`api_app/apps.py:5-6`) | live, does nothing |
| `api_app/models.py` | imports `models`, defines none: `api_app/models.py:4-6` | live, empty |
| `api_app/admin.py` | imports `admin`, registers nothing: `api_app/admin.py:4-6` | live, empty |

Ten classes are defined in `api_app/views.py`, and the URLconf names six of them. Four
are dead even by this directory's standards, unreferenced by any line in the repo outside
their own definitions: `api_app/views.py:90`, `api_app/views.py:103`,
`api_app/views.py:128` and `api_app/views.py:141`. Of the six that are named, the two
hand-written ones do the real work — `SamplesViews` at `api_app/views.py:45` accepts an
xlsx upload or a JSON body and calls into SEEK's sample writer, and `DatafileViews` at
`api_app/views.py:183` matches an uploaded file to an existing sample before storing it.
The other four are plain DRF generics over the mirrored SEEK tables
(`api_app/views.py:158-179`).

### The CLI surface: nine `__main__` entry points

Each is invoked as `python <file> …`; none is importable from Django code, and none is
referenced by any config, script or compose file in the tree (searched with
`/usr/bin/grep -rn` for each module basename over the worktree, excluding `.git/`,
`.superpowers/` and this pair; the only outside hits are prose in
`docker/cc-runtime/docs/nextseek/09-nextseek.md:142` and a commented-out line at
`seek/sample/upload.py:527`).

**The `/api/` client scripts** — `api_app/api_calls.py:416-417`,
`api_app/api_fileSubmit.py:519-520`, `api_app/api_sampleSubmit.py:523-524` and
`api_app/api_sampleParser.py:1298-1299`. Each hardcodes the same placeholder base URL —
`api_app/api_calls.py:14`, `api_app/api_fileSubmit.py:14`,
`api_app/api_sampleSubmit.py:13`, `api_app/api_sampleParser.py:15` — and each logs in
against the same route under the unmounted prefix: `api_app/api_calls.py:18`,
`api_app/api_fileSubmit.py:18`, `api_app/api_sampleSubmit.py:17`,
`api_app/api_sampleParser.py:19`. They read and post sample and data-file records; the largest,
`api_app/api_sampleParser.py`, additionally parses BMC assay spreadsheets and sequencing
infosites into an upload sheet (`api_app/api_sampleParser.py:1220`).

**The checksum utilities** — `api_app/cal_checksum.py:132-133` takes a file, a file list
or an SRA accession and prints MD5/SHA1; `api_app/api_calls_pride.py:89-90` downloads a
PRIDE file by name and verifies its size and SHA1 against the PRIDE API.

**The schema/graph reporters** — `api_app/db_network_calls.py:275-276` prints a
sample-type adjacency table derived from live SEEK metadata
(`api_app/db_network_calls.py:226-249`).

**The lineage rebuilder** — `api_app/updateTrees.py:1186-1187` dispatches four
subcommands (`api_app/updateTrees.py:1161-1172`): `update` fills gaps,
`renew` and `generate` rebuild, and `cronjob` exports then wipes and regenerates the
whole `seek_sample_tree` table (`api_app/updateTrees.py:1142-1150`). Its sibling
`api_app/remoteJob/updateTrees.py:1149-1150` offers three of the same four.

### `remoteJob/` is a superseded snapshot, not a second deployment target

Two filenames appear twice in this boundary, and reading them settles which copy wins.

- `api_app/remoteJob/dbconn_mysql.py` is imported by nothing. Both lineage scripts import
  the top-level class instead — `api_app/updateTrees.py:18` and
  `api_app/remoteJob/updateTrees.py:19` name the identical module path. An exhaustive
  `/usr/bin/grep -rnE "^[[:space:]]*(from|import)[[:space:]]+api_app"` over the worktree,
  excluding `.git/` and `.superpowers/`, returns exactly three lines: those two plus
  `dmac/urls.py:13`. So the `remoteJob` connector is unreachable by any import in the
  repo, and it does not shadow the top-level one.
- `api_app/updateTrees.py` is the successor and `api_app/remoteJob/updateTrees.py` the
  ancestor. The successor reads the parent key as `Parent`
  (`api_app/updateTrees.py:20`), which is the spelling SEEK itself uses at
  `seek/sample/constants.py:68`; the ancestor reads `parent`
  (`api_app/remoteJob/updateTrees.py:21`) and matches it with a case-sensitive substring
  test at `api_app/remoteJob/updateTrees.py:36`. The successor also replaces a full-table
  `LIKE` scan (`api_app/remoteJob/updateTrees.py:58`) with a `json_extract` lookup
  (`api_app/updateTrees.py:58`), and carries `sample_id` into the stored row through a
  wider signature at `api_app/updateTrees.py:470` where the ancestor's at
  `api_app/remoteJob/updateTrees.py:469` does not.

## Running and testing

**This boundary has no test lane of its own.** `api_app/tests.py:4-6` imports
`django.test.TestCase` and declares no test class, and a `find api_app -name 'test_*.py' -o
-name '*_test.py'` returns nothing, so neither collector finds anything. Both were run on
2026-09-03 inside the `nextseek` container against this worktree's code:

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest api_app --no-migrations -q'
```
gave `no tests collected in 0.11s`, and

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run manage.py test api_app -v 2'
```
gave `Ran 0 tests in 0.000s` / `NO TESTS RAN` after `System check identified 39 issues`.
Both lanes therefore finished with 0 passed, 0 failed, 0 skipped and 0 errors. The pytest
result is structural, not incidental: `pyproject.toml:146-148` sets no `python_files` key,
so pytest's defaults never match a module named `tests.py`.

What *does* exercise part of this code is a fork living elsewhere. Lines 1-61 of
`nextseek_api/serializers.py` are byte-identical to lines 1-61 of
`api_app/serializers.py` (compared with `diff` on `head -61` of each, 2026-09-03), and
`nextseek_api/tests/test_serializers.py:42` covers that copy. Nothing covers this one.

The only way to observe this directory's own behaviour is to run one of the nine scripts
by hand. Five of them cannot be imported at all — see the other file of the pair for
which, and for what happens when you run the lineage rebuilder.

## Depends on / depended on by

This is a Python package, so both edges are import edges; they were derived by grepping
the worktree rather than recalled, with `.git/` and `.superpowers/` excluded and every
surviving hit checked against `git ls-files`.

**Depended on by — the complete inbound list is one line.**

- `dmac/urls.py:13` imports `api_app.urls` at module scope, which is why this dead code
  still costs import time and still breaks the boot if it raises.
- `dmac/settings.py:173` lists the app, which is config rather than an import, and is
  what makes Django load `api_app/models.py` and `api_app/apps.py` as well.
- Nothing else: that same `/usr/bin/grep -rnE` import search over the worktree returns
  three lines in total, and the other two — `api_app/updateTrees.py:18` and
  `api_app/remoteJob/updateTrees.py:19` — are internal to this boundary.

Three hits look like inbound edges and are not, so they are excluded rather than listed:

- `nextseek_api/attributes/tests/test_physical_safeguards_db.py:38` matches the string
  `api_app` only because its function name ends `..._nextseek_api_app`.
- `docker/cc-runtime/docs/nextseek/09-nextseek.md:141-142` documents a `CRONJOBS` entry
  calling `api_app.updateTrees.renewTreesCronjob`; that is prose baked into an agent's
  reference docs, and no `CRONJOBS` setting exists in this repo — outside `.git/`,
  `.superpowers/` and this pair, that identifier occurs on that markdown line alone.
- `seek/sample/upload.py:527` names `updateTrees` inside a comment, and that file imports
  no such symbol (`seek/sample/upload.py:3-17`).

**Depends on — SEEK application code, several third-party libraries, and MySQL directly.**

- `api_app/views.py:11-14` imports four `seek` entry points, so the Django group cannot
  load unless `seek` does; `seek/dbtable_sample.py:2` is itself a re-export shim onto
  `seek/sample/table.py:31`.
- `seek/sample/api.py:1` states that its mixin exists for the `api_app` and
  `nextseek_api` packages, but three of its methods are called only from here —
  `apiUploadSamples` (`seek/sample/api.py:244`), `searchFileInSample`
  (`seek/sample/api.py:38`) and `getSampleUIDInfo` (`seek/sample/api.py:280`) each have
  exactly one call site outside their own definition, all in `api_app/views.py`, found by
  grepping each name over the worktree with `.git/`, `.superpowers/` and this pair
  excluded.
- `api_app/serializers.py:2-3` binds the serializers to the mirrored SEEK tables
  `Samples` (`seek/models/seek_mirror.py:167`) and `Data_files`
  (`seek/models/seek_mirror.py:278`), and to the file-link resolver at
  `seek/dbtable_data_files.py:487`.
- `api_app/dbconn_mysql.py:5` imports Django's settings object and
  `api_app/dbconn_mysql.py:8-9` dereferences it at module scope into the two aliases that
  `dmac/settings.py:517-518` define, so this module cannot be imported outside a
  configured Django process.
- `api_app/dbconn_mysql.py:3` uses `MySQLdb` directly rather than the ORM, so the lineage
  group bypasses Django's connection handling entirely.
- `api_app/api_sampleParser.py:21-24` needs `openpyxl` and `xlwt`, and
  `api_app/views.py:71-72` imports `pyexcel_xlsx` lazily inside the request handler
  rather than at module scope.
- `api_app/api_sampleParser.py:906-907` needs `jaydebeapi` and `jpype`, imported inside
  the function so the rest of the module loads without a JVM.
- The lineage group writes one table, `seek_sample_tree`, in the NExtSEEK schema
  (`api_app/updateTrees.py:1148`), and reads the `samples` table from the SEEK schema
  (`api_app/updateTrees.py:412-418`).

## See also

See `api_app/CLAUDE.md` for the invariants, the five modules that cannot be imported, and
the traps in the lineage rebuilder.
