# `nextseek_api/services/`

## What this is

The ViewSet and service layer: 25 Python modules and one JSON data file, 12,551 lines of
Python counted with `wc -l` over `nextseek_api/services/*.py` on 2026-09-03. Summing the same
count per directory over every `.py` file in the repo outside test directories on that date
makes this the largest directory of non-test Python here, ahead of `scripts/` at 11,824 lines.
Nearly a fifth of it is two modules: `nextseek_api/services/assistant.py` at 1,429 lines and
`nextseek_api/services/cc_assistant.py` at 996.

It is **not a Python package**. There is no `__init__.py` here, so `nextseek_api.services`
resolves as a PEP 420 namespace package: importing it inside the stack image on 2026-09-03
printed `<module 'nextseek_api.services' (namespace)>` with `__file__` set to `None`. Every
one of its eleven sibling directories under `nextseek_api/` has an `__init__.py`; this one
alone does not. See `nextseek_api/services/CLAUDE.md` for what that costs a tool that
wants to walk this directory.

Two populations live here and they answer to different callers. Seventeen modules define
20 ViewSet classes carrying 85 request handlers between them — the standard CRUD method
names plus every `@action` — counted by walking the AST of all 25 modules on 2026-09-03.
The remaining eight modules define no ViewSet and no URL: they are pure functions that the
**legacy `seek/` Django app** imports and renders into HTML pages and Excel workbooks.
`nextseek_api/services/context_catalog.py:3-6` states that division explicitly for its own
half of it.

The one file every reader eventually lands in is `nextseek_api/services/assistant.py`, and
not for the assistant. It defines `CsrfExemptSessionAuthentication`
(`nextseek_api/services/assistant.py:140`) and the shared error envelope
(`nextseek_api/services/assistant.py:156`), and nine non-test modules import from it, so it
is the de-facto shared library of this directory as well as its largest chat engine.

## Surface

This directory is a namespace package, so the surface is read in two directions that take
different shapes. Outward to HTTP, the surface is the **ViewSet classes** that
`nextseek_api/views.py` aliases and `nextseek_api/urls.py` registers; the mapping below was
derived on 2026-09-03 by parsing the `from .services.X import Y as Z` lines in
`nextseek_api/views.py` and matching each alias against the `router.register(...)` calls.
Inward from the legacy app, the surface is a set of **plain functions imported by name**,
listed at the end of this section. Nothing here is reached through an `__init__` re-export,
because there is none.

**The routed half.** 20 of the 25 active router registrations resolve to a class defined in
this directory, spanning `nextseek_api/urls.py:18` to `nextseek_api/urls.py:42`. They fall
into three families.

*SEEK proxies*, which forward JSON:API calls upstream and let SEEK do the filtering:
`nextseek_api/services/assays.py:36`, `nextseek_api/services/data_files.py:45`,
`nextseek_api/services/investigations.py:37`, `nextseek_api/services/people.py:32`,
`nextseek_api/services/projects.py:39`, `nextseek_api/services/sops.py:47`,
`nextseek_api/services/studies.py:37`, `nextseek_api/services/sample_types.py:113` and
`nextseek_api/services/samples.py:74`. Each holds its upstream client as a class attribute
(`nextseek_api/services/assays.py:38` is one) and each accepts any slash-free path segment
as a detail lookup (`nextseek_api/services/assays.py:41`).

*Native readers and writers*, which talk to MySQL, Neo4j or the SEEK container directly:
`nextseek_api/services/entity_tree.py:75`, `nextseek_api/services/sampletype_connections.py:796`,
`nextseek_api/services/sample_types.py:270`, `nextseek_api/services/sample_types.py:383`,
`nextseek_api/services/samples.py:357`, `nextseek_api/services/schema_rag.py:43`,
`nextseek_api/services/users.py:359` and `nextseek_api/services/project_export.py:256`.

*The chat pair*: `nextseek_api/services/assistant.py:417` with 18 actions, and
`nextseek_api/services/cc_assistant.py:446` with 8. Their query endpoints share a shape —
create a `QueryTask`, hand a callback to the engine, hand the work to a background thread,
return 202 — and their overlap is deliberate reuse rather than a fork, imported at
`nextseek_api/services/cc_assistant.py:48-54`. `nextseek_api/services/evaluator.py:413`
reads what those two wrote back out, normalized for retry.

**The library half.** Eight modules with no ViewSet, grouped by what consumes them.

| Module | Public names | Consumed by |
|---|---|---|
| `nextseek_api/services/sample_workbook.py` | `write_samples_workbook` at `nextseek_api/services/sample_workbook.py:494`, `write_template_workbook` at `nextseek_api/services/sample_workbook.py:590`, `load_sample_type_context` at `nextseek_api/services/sample_workbook.py:191` | the legacy `seek/` download and template views |
| `nextseek_api/services/template_catalog.py` | `load_catalog` at `nextseek_api/services/template_catalog.py:87`, `load_relationships` at `nextseek_api/services/template_catalog.py:186`, `is_deprecated` at `nextseek_api/services/template_catalog.py:157`, `GROUPS` at `nextseek_api/services/template_catalog.py:28` | the Download Templates page |
| `nextseek_api/services/context_catalog.py` | `load_sample_types` at `nextseek_api/services/context_catalog.py:141`, `load_project_context` at `nextseek_api/services/context_catalog.py:354`, `assay_slug_for_name` at `nextseek_api/services/context_catalog.py:315`, `CLADE_ORDER` at `nextseek_api/services/context_catalog.py:32` | the catalog and project pages |
| `nextseek_api/services/project_connections.py` | `connection_rows` at `nextseek_api/services/project_connections.py:44`, `connections_html` at `nextseek_api/services/project_connections.py:70`, `types_in_use` at `nextseek_api/services/project_connections.py:92`, `project_bundles` at `nextseek_api/services/project_connections.py:155` | the project page |
| `nextseek_api/services/sample_provenance.py` | pure graph functions, no ORM and no openpyxl, per `nextseek_api/services/sample_provenance.py:1-4` | the workbook writer |
| `nextseek_api/services/type_requirements.py` | `classify` at `nextseek_api/services/type_requirements.py:69`, `classify_companions` at `nextseek_api/services/type_requirements.py:130` | a management command |
| `nextseek_api/services/content_blobs.py` | `download_single` at `nextseek_api/services/content_blobs.py:191`, `download_batch` at `nextseek_api/services/content_blobs.py:276`, `upload_content_blobs` at `nextseek_api/services/content_blobs.py:417` | the SOP and DataFile proxies |
| `nextseek_api/services/seek_rails_runner.py` | `run_seek_rails_runner` at `nextseek_api/services/seek_rails_runner.py:60` | the Users admin ViewSet |

**The data file.** `nextseek_api/services/controlled_vocabularies.json` is load-bearing
input, not scratch: `nextseek_api/services/sample_workbook.py:76` binds it by
`Path(__file__).with_name(...)` and every dropdown in every downloaded workbook comes from
it. The loader swallows a read failure and returns empty maps
(`nextseek_api/services/sample_workbook.py:105-107`), so moving or renaming the file removes
the dropdowns from every download and logs, without failing a request. It is the only
non-`.py` file in this directory, and it is the only file shipped with the repo that any
module here opens: a `grep` for `open(`, `read_text` and `Path(__file__)` across all 25
modules returns `nextseek_api/services/sample_workbook.py:76` and
`nextseek_api/services/sample_workbook.py:103` for this file, and otherwise only per-request
artifact reads and writes such as `nextseek_api/services/assistant.py:1088` and
`nextseek_api/services/cc_assistant.py:883`.

## Running and testing

This directory has no test lane of its own. There is no `__init__.py`, no `conftest.py` and
no file whose name begins with `test` anywhere beneath it — a `find` over
`nextseek_api/services` for those three names returns nothing. Its tests live one level up,
in `nextseek_api/tests/`, where 15 modules are named after a module here
(`nextseek_api/tests/test_services_assays.py:2` names its subject in its docstring), and in
`nextseek_api/cc_assistant/tests/`, which owns the routing tests for
`nextseek_api/services/cc_assistant.py`.

**The lane I ran, 2026-09-03.** A throwaway container from the stack image, with this
worktree bind-mounted read-only and copied to a writable path inside the container, run
under `dmac.test_settings` and with no network:

```
docker run --rm --network none -v "$PWD":/src:ro -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
  -w / nextseek-nextseek:latest bash -lc \
  'cp -a /src /build && cd /build && /app/.venv/bin/python -m pytest nextseek_api/tests/test_services_*.py -q'
```

Result: **7 failed, 794 passed, 868 warnings, 4 subtests passed in 12.64s**. The copy step is
what makes a read-only checkout usable: `dmac/settings.py:497-498` creates directories beside
the settings file at import time. Six of the seven failures share one cause and the seventh is
a real gate; both are written up in CLAUDE.md, along with an eighth test that passes in this
run and fails when the same module is run alone.

`scripts/run_tests.sh:44-47` is the supported wrapper for the same idea and needs two things a
fresh worktree does not have: a `dmac/local_settings.py` inside the checkout
(`scripts/run_tests.sh:37-41`) and a compose directory holding the gitignored `docker/*.env`
files (`scripts/run_tests.sh:20`).

The cheap gate, and the one this directory fails today, is the AST-only convention validator.
Its command and its current output are in CLAUDE.md.

## Depends on / depended on by

Depends on, outside this directory. Derived on 2026-09-03 by walking the AST of all 25
modules and splitting module-scope imports from in-function ones:

- `seek/`, the legacy Django app, at module scope in eight of these modules, so the SEEK app
  must import cleanly before any routed ViewSet here loads: `nextseek_api/services/samples.py:14`,
  `nextseek_api/services/sample_types.py:18`, `nextseek_api/services/projects.py:22`,
  `nextseek_api/services/users.py:39`, `nextseek_api/services/context_catalog.py:24`,
  `nextseek_api/services/template_catalog.py:20`, `nextseek_api/services/project_connections.py:28`
  and `nextseek_api/services/sample_workbook.py:26`.
- `chat_nextseek/`, the vendored assistant, imported at module scope by exactly two of the 25
  modules: `nextseek_api/services/assistant.py:102-103`, and
  `nextseek_api/services/cc_assistant.py:56` with two more at
  `nextseek_api/services/cc_assistant.py:75` and `nextseek_api/services/cc_assistant.py:77`.
- `nextseek_api/cc_assistant/`, the sibling engine package, imported by name across
  `nextseek_api/services/cc_assistant.py:58-69` and again at
  `nextseek_api/services/cc_assistant.py:76`.
- The `neo4j` driver, at module scope in four modules and constructed per request at seven
  call sites, of which `nextseek_api/services/entity_tree.py:302` and
  `nextseek_api/services/sample_workbook.py:285-290` are two.
- `MySQLdb`, imported unconditionally at `nextseek_api/services/entity_tree.py:15`, alongside
  Django's own connection registry used everywhere else here.
- `pandas` and `openpyxl`, at `nextseek_api/services/sample_workbook.py:16-17`, which is why
  importing the workbook writer is not free.
- The `docker` SDK, imported lazily and turned into a typed error when absent
  (`nextseek_api/services/seek_rails_runner.py:48-57`), so a runtime without the Docker socket
  degrades rather than failing to import.
- Django settings read at module import rather than per request:
  `nextseek_api/services/assistant.py:39-40` binds two, and
  `nextseek_api/services/entity_tree.py:48-49` binds the two database aliases.
- Exactly one module-scope import in this directory is guarded against failure —
  `nextseek_api/services/samples.py:35-38`, which falls back to `None`. A grep for `except
  ImportError` and for a `try:` at column zero across all 25 modules returns that block and the
  in-function one at `nextseek_api/services/seek_rails_runner.py:52`, and nothing else.

Depended on by. Derived from a repo-wide grep for `nextseek_api.services` and `from .services`
over every `.py` file, then a second pass for the string `nextseek_api/services/` in
non-Python and non-import contexts, then grouped. Test modules are omitted:

- `nextseek_api/views.py:43-66` aliases 21 names from here — 20 ViewSet classes and one
  helper function at `nextseek_api/views.py:58` — into the module `nextseek_api/urls.py:5`
  imports, which is how everything routed here reaches HTTP.
- `seek/`, in the reverse direction, and this is the edge most likely to surprise: the legacy
  app imports this directory's library half at `seek/sample/download.py:17`,
  `seek/views/admin.py:25`, `seek/views/assets.py:17-24`, `seek/views/catalog.py:16` and
  `seek/views/projects.py:19-22`. Both directions are module-scope, so the two apps are
  mutually dependent at import time.
- Three sibling packages import one class from `nextseek_api/services/assistant.py`:
  `nextseek_api/assay_registration/views.py:21`, `nextseek_api/attributes/auth.py:15` and
  `nextseek_api/batch_upload/views.py:21`.
- `nextseek_api/cc_assistant/cc_sweep.py:38` imports two private helpers back out of
  `nextseek_api/services/cc_assistant.py`, inside the Celery body rather than at module scope,
  which is what keeps that package's import hermetic
  (`nextseek_api/cc_assistant/cc_sweep.py:32`).
- `nextseek_api/management/commands/derive_sample_type_requirements.py:20-21` imports the
  requirement classifier and one workbook constant.
- `scripts/validate_viewset_conventions.py:30` scans this directory by path and
  `scripts/validate_viewset_conventions.py:40` names one file in it as skipped; the same script's
  grandfather table pins three method names in two modules here, at
  `scripts/validate_viewset_conventions.py:139-156`.
- `ci/routes.py` declares the HTTP routes this directory serves and annotates them with source
  locations as prose, not imports — `ci/routes.py:800` and `ci/routes.py:629` are two.

What a hit here is NOT. Four groups were excluded deliberately, each for a different reason.
`nextseek_api/tests.py` imports service ViewSets on more than 40 lines, `nextseek_api/tests.py:875`
among them, and none of them run; see `nextseek_api/CLAUDE.md` for why that module is
unreachable. `chat_nextseek/e2e/playwright/poll.py:11` and `nessie_tests/manifest.py:169` name
modules here inside docstrings, which is documentation and not a dependency.
`seek/timeline/services/` is a different `services` package entirely, imported at
`nextseek_api/views.py:27-28`, and has nothing to do with this directory. And
`nextseek_api/services/schema_rag.py:27` is an import *into* this directory from the
`nextseek_api/schema_rag/` package, not the reverse — the ViewSet is here, the engine is there.

See `nextseek_api/services/CLAUDE.md` for the invariants, the traps, and the one command to run.
