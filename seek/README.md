# `seek/`

## What this is

A Django app that maps another application's database. The tables it reads and
writes belong to the upstream **SEEK** Rails platform (fairdom/seek), which runs
beside NExtSEEK as its own container and owns the `seek_production` MySQL
schema. Nothing in this directory is the SEEK application: there is no Rails
code here, no SEEK model logic, and no authority over the schema. What is here
is a set of `models.Model` classes whose `Meta.db_table` names a table SEEK
created, a database router that sends them to a second Django connection alias,
a hand-rolled table layer above them, and the NExtSEEK pages that present the
result. When SEEK's schema changes under it, this directory finds out by
breaking.

The name is therefore a trap for a newcomer. This is NExtSEEK's own code — 90
Python files and 16,388 lines as of 2026-09-03, counted with `find seek -name
'*.py'` over this worktree — and it is where most of NExtSEEK's own server-
rendered pages live. It also carries eleven tables NExtSEEK itself owns
(`seek/models/nextseek.py:1-6`), so the app straddles both schemas rather than
being purely a mirror.

The second half of the SEEK integration does not go through the database at all.
`seek/seekdb.py:12-24` logs a request's user in to SEEK over HTTP and holds the
resulting identity, and `seek/seekapi.py:11-23` is the transport underneath it —
a `curl` command line assembled as a string. Reads of schema-shaped data go
through the ORM; identity, permissions and asset writes go through SEEK's own
JSON API as the logged-in user.

## Surface

The surface here is **the model set and what each model maps onto**, not an
import graph, because an ORM shim's public contract is the table binding. Two
things follow: the outbound dependency is a table set owned by another
application, and the models below are load-bearing exactly to the extent that
the upstream column list still matches.

### The mirror models

`seek/models/seek_mirror.py:1-11` declares the rule: 19 classes, each setting
`_DATABASE = SEEK_DATABASE` (`seek/models/seek_mirror.py:17`) and naming an
existing Rails table. Routed by `_DATABASE` at `seek/dbrouters.py:3-7`, wired in
as the project's only router at `dmac/settings.py:299`.

| SEEK table | Model | Declared at |
|---|---|---|
| `users`, `people` | `Users`, `People` | `seek/models/seek_mirror.py:43`, `seek/models/seek_mirror.py:68` |
| `samples`, `sample_types`, `sample_attributes`, `sample_attribute_types` | `Samples`, `Sample_types`, `Sample_attributes`, `Sample_attribute_types` | `seek/models/seek_mirror.py:187`, `seek/models/seek_mirror.py:117`, `seek/models/seek_mirror.py:145`, `seek/models/seek_mirror.py:165` |
| `assays`, `assay_assets`, `assets_creators` | `Assays`, `Assay_assets`, `Assets_creators` | `seek/models/seek_mirror.py:94`, `seek/models/seek_mirror.py:343`, `seek/models/seek_mirror.py:414` |
| `data_files`, `documents`, `sops`, `content_blobs` | `Data_files`, `Documents`, `Sops`, `Content_blobs` | `seek/models/seek_mirror.py:301`, `seek/models/seek_mirror.py:276`, `seek/models/seek_mirror.py:399`, `seek/models/seek_mirror.py:325` |
| `projects`, `projects_samples`, `projects_sops`, `data_files_projects` | `Projects`, `Projects_samples`, `Projects_sops`, `Data_files_projects` | `seek/models/seek_mirror.py:449`, `seek/models/seek_mirror.py:208`, `seek/models/seek_mirror.py:230`, `seek/models/seek_mirror.py:252` |
| `policies`, `permissions` | `Policies`, `Permissions` | `seek/models/seek_mirror.py:360`, `seek/models/seek_mirror.py:376` |

The three project join tables are read-only by construction: `save()` and
`delete()` are each overridden to return without doing anything, in
`Projects_samples` (`seek/models/seek_mirror.py:200-205`), `Projects_sops`
(`seek/models/seek_mirror.py:222-227`) and `Data_files_projects`
(`seek/models/seek_mirror.py:244-249`).

`seek/models/nextseek.py` holds the other half — 11 classes for tables NExtSEEK
defines. Three of them are `managed = False` because their DDL is applied out of
band rather than by a migration: `Sample_attributes_unique`
(`seek/models/nextseek.py:151-158`), `Sample_type_requirements`
(`seek/models/nextseek.py:198-200`) and `Project_template_bundles`
(`seek/models/nextseek.py:222-224`).

### The table layer

13 `seek/dbtable_*.py` modules front the table layer. Twelve of them subclass
`dmac.dbtable.DBtable` (`dmac/dbtable.py:33`), a hand-written CRUD/datagrid
layer rather than Django's manager API. Each declares its table name, its field
list and its unique key in `__init__`, and all but one also bind a Django model
— `seek/dbtable_projects.py:13-16` is the pattern. The thirteenth module,
`seek/dbtable_sample.py:1-2`, is a two-line backwards-compatibility shim left
behind when the sample table moved to `seek/sample/`
(`seek/sample/__init__.py:1-9`), which splits `DBtable_sample` into eight mixins
combined at `seek/sample/table.py:31`.

`seek/dbtable_ontology.py:23` is the one table module that names a table
(`sample_controlled_vocab_terms`) without binding a model to it, a gap its own
test records and pins with a strict xfail (`seek/tests/test_dbtables.py:44-47`).

### Pages, search and export

`seek/urls.py` declares 62 `re_path` entries, mounted under `^seek/` by
`dmac/urls.py:27`. The views are a **package of 11 modules**, not a single file:
`seek/views/__init__.py:1-8` re-exports every name the URL conf uses and states
the consequence for patching. `seek/search.py:16-35` holds the identifier
allowlist for the PubMed-style search grammar, enforced by
`seek/search.py:39-47`. `seek/decorators.py:1-15` holds the login and supervisor
preambles the views used to repeat inline — three public decorators, at
`seek/decorators.py:68`, `seek/decorators.py:92` and `seek/decorators.py:117` —
and `seek/responses.py:1-19` the single JSON envelope shape.
`seek/doi_extract.py:1-7` is a pure-function publication-reference extractor
with no database, network or settings dependency.

`seek/timeline/` is a 10-module NHP timeline subtree with its own MySQL
connection pool (`seek/timeline/core/database.py:7-16`) rather than Django's,
reached through `seek/views/timeline.py:1-11`.

### Templates

`seek/templates/` holds 65 `.html` files as of 2026-09-03 (`find
seek/templates -name '*.html'`), reached by Django's app-directories loader
(`dmac/settings.py:131`). 24 template names are passed directly to a `render`
call or a `TemplateView` in `seek/views/` and `seek/urls.py`, measured 2026-09-03
by extracting every `render(request, "...")` and `template_name="..."` literal
from those files; 23 of those files live here and one, `help/getting_started.html`,
does not. The remaining 42 are reached by `{% extends %}` and `{% include %}`,
or are dead. The inline JavaScript inside them is not untested: a Node harness
lifts the script body out of `templatesList.html` verbatim and runs it against a
stub DOM (`seek/tests/js/harness.js:3-9`).

## Running and testing

The suite is 23 test modules under `seek/tests/`. Two of them are structural
nets rather than feature tests: `seek/tests/test_imports.py:1-5` imports every
module in the package, and `seek/tests/test_relative_imports.py:1-21` walks
every AST for a relative import that resolves to the wrong module or a name that
does not exist — the defect that shipped twice when `views.py` and
`dbtable_sample.py` became packages. Both walk the filesystem through
`seek/tests/discovery.py:1-6` rather than `pkgutil`, because `seek/timeline/`
has no top-level `__init__.py`.

`mysqlclient` does not build on this host, so the host lane is unavailable and a
throwaway container over a read-only mount of the worktree is the lane that
works. Run on 2026-09-03 with the recipe printed at `ci/gate/live_routes.py:16`,
substituting `seek` for `ci/gate`:

```
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest seek --continue-on-collection-errors -q \
  -p no:cacheprovider
```

Result: **34 failed, 488 passed, 29 skipped, 2 xfailed, 1 error in 10.24s.**
The two `mkdir` lines are not optional — `dmac/settings.py:498-499` creates
those directories at import time and the mount is read-only.

`ci/pytest-baseline.txt:307-308` records only two known failures for this
package, so nearly all of that red is drift since the baseline was taken. See
`seek/CLAUDE.md` for what each cluster is and which refactor caused it.

## Depends on / depended on by

**Depends on — the table set, and who owns it.** This is the direction an
importer grep cannot see.

- The `seek` connection alias points at whatever `MYSQL_DATABASE` names
  (`dmac/settings.py:38-44`); `startup/templates/db.env.template:8` renders that
  as `seek_production`. Repointing it at another schema silently changes which
  application's data every mirror model reads.
- The 19 tables in the table above are created and migrated by the upstream SEEK
  Rails container — `seek/models/seek_mirror.py:5-7` states the ownership rule and
  `docker-compose.yml:265` declares the image as `fairdom/seek:1.15.1`.
  All 19 are present in the committed dump `startup/seed/seek_production.sql.gz`,
  which carries 205 `CREATE TABLE` statements as of 2026-09-03; the other 186 are
  unmapped here.
- Two SEEK tables this package deletes from have no model at all —
  `sample_resource_links` and `sample_auth_lookup`, both hit by raw SQL at
  `seek/sample/table.py:71-73`, and both present in that same dump. The delete
  set is hand-maintained, so a SEEK upgrade that adds a table referencing
  `samples` leaves orphan rows behind.
- Nothing here creates a SEEK table on the live SEEK schema. `manage.py migrate`
  is run without a `--database` argument at `docker/scripts/entrypoint.sh:47`, so
  it targets the default alias only, and `seek/migrations/0001_initial.py:345-346`
  therefore builds its `Samples` shadow in the NExtSEEK schema instead.
  Established by searching every tracked `.sh`, `.py`, `Dockerfile` and `.yml` in
  the tree for `manage.py migrate` or `migrate --database`: five files match, and
  none of them passes a database argument.
- `seek/migrations/0002_samples_name_identity.py:25-26` is the exception worth
  knowing: it opens a raw `ALTER TABLE samples`, executed at
  `seek/migrations/0002_samples_name_identity.py:73-80`, so the app does alter a
  SEEK-named table — on the default alias, for the shadow copy, under the same
  no-argument `migrate`.
- Three NExtSEEK-owned tables come from committed SQL rather than a migration,
  applied by `startup/steps/schema_fixups.py:117-118` and its two neighbours,
  called only from `startup/cli.py:288`.
- SEEK's HTTP JSON API, reached as the logged-in user through
  `seek/seekapi.py:60-64` and wrapped by `seek/seekdb.py:11-31`. This is the
  authorization boundary: project membership and supervisor status come from
  SEEK, not from Django's auth tables.
- Neo4j, for sample lineage — `seek/sample/table.py:61-65` opens the driver
  directly from `settings.NEO4J_DATABASE`.
- `dmac/`, mutually: 14 modules here import `dmac.dbtable.DBtable`, and
  `seek/views/admin.py:5-8` imports four `dmac.dbtable_*` modules that
  themselves import back from `seek.models` (`dmac/dbtable_clades.py:15`).
- `nextseek_api.services`, mutually and at module scope, for the catalog and
  workbook logic the pages render — `seek/views/catalog.py:16`,
  `seek/views/projects.py:19-22`, `seek/views/assets.py:17-18`,
  `seek/views/admin.py:25`, `seek/sample/download.py:17`.

**Depended on by.** Generated 2026-09-03 by grepping every `.py` file in the
worktree for a line beginning with an import of `seek` or of any `seek.`
submodule, then dropping matches under `seek/` itself and under `.superpowers/`.
125 lines matched before that filter; the test modules of other packages are
omitted from the list below.

- Django itself: `dmac/settings.py:145` installs the app, and `dmac/urls.py:12`
  imports its URL conf.
- `api_app/` — `api_app/views.py:11-14` and `api_app/serializers.py:2-3` take
  `Samples`, `Data_files`, `DBtable_sample`, `DBtable_data_files` and `SeekDB`.
- `nextseek_api/` is the heaviest consumer, reaching the models
  (`nextseek_api/services/users.py:39`,
  `nextseek_api/services/template_catalog.py:20`), the SEEK login wrapper
  (`nextseek_api/seek_api_helpers.py:5`, `nextseek_api/views.py:25`), the
  table layer (`nextseek_api/models.py:16-17`) and the timeline services
  (`nextseek_api/views.py:27-28`).
  `nextseek_api/management/commands/fill_study_publications.py:29` is the only
  consumer of `doi_extract`, and
  `nextseek_api/management/commands/derive_sample_type_requirements.py:22` the
  only writer of `Sample_type_requirements`.
- `nextseek_api/cc_assistant/cc_provision.py:151` imports `SeekDB` lazily inside
  a factory, host-side only, to resolve the caller's project.
- What is NOT a consumer, by import: `chat_nextseek/`, `nessie_tests/`,
  `startup/`, `scripts/`, `build_tools/`, `ci/` and `themes/` contain none.
  Searching all `.py` files under those seven directories for an import
  statement naming `seek` returns zero lines. `ci/` reaches the package by URL
  prefix string instead, declared at `ci/gate/live_routes.py:37`.
- What is NOT an import: `nextseek_api/views.py:81` names `seek.views.get_clade_color`
  in a docstring, and no such function exists here — searching every `.py` file
  under this directory for `get_clade_color` returns nothing. The function it describes lives at
  `nextseek_api/views.py:80`.

See `seek/CLAUDE.md` for the invariants these edges rest on and the traps in
them.
