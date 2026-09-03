# Working in `nextseek_api/`

Everything below concerns the shell itself. Each child package carries its own pair; work
inside one of those from its own CLAUDE.md, not from this file.

## Invariants

- **Router registrations that share a prefix must stay ordered longest-first.**
  `nextseek_api/urls.py:31` puts the advanced-search prefix ahead of the bare samples
  prefix at `nextseek_api/urls.py:34`, whose detail lookup accepts any segment containing
  no slash (`nextseek_api/services/samples.py:79`). Swap those two lines and the search
  URL resolves into the sample-detail action with the literal word as its lookup value —
  the same swallowing that a bare prefix already demonstrates today for a registration
  publishing no list route, measured with Django's resolver on 2026-09-03.
- **The three documentation routes must each keep an explicit `permission_classes`.**
  drf-spectacular assigns that attribute inside its own serve-view class bodies from a
  package default of `AllowAny`, so this project's `DEFAULT_PERMISSION_CLASSES` never
  reaches them. Register any of the three plainly and every path, parameter, request body
  and model becomes readable by anyone who can reach the host — the reasoning is at
  `nextseek_api/tests/test_api_docs_authentication.py:1-9`, which pins all three routes
  using an account that is neither staff nor superuser.
- **The re-export lines in `nextseek_api/models.py` are what registers this app's ORM
  models.** Each such model is defined in a child module under an explicit `app_label`
  (`nextseek_api/assistant/models_db.py:23`), so the migration graph in
  `nextseek_api/migrations/` is tied to those classes only through
  `nextseek_api/models.py:3-9` and `nextseek_api/models.py:2708`. Drop a name from either
  place and a migration is left managing a table whose class the app registry never loads.
- **A caller's project list resolves to empty on any failure, and empty means "sees
  nothing".** Both scope helpers say exactly that in their own docstrings and return an
  empty container from a bare `except` — `nextseek_api/views.py:115-116` and
  `nextseek_api/views.py:133-134`. Reading an empty list as "unscoped" hands every row to
  a caller whose SEEK lookup merely timed out.
- **Superuser gates key on `is_superuser`, never on `is_staff`.** `dmac/views.py:80` sets
  `is_staff` on every SEEK user during login, on the create branch and again at
  `dmac/views.py:97` on the update branch. A gate written against `is_staff` therefore
  admits every logged-in SEEK user and enforces nothing;
  `nextseek_api/permissions.py:9-14` records that ruling and
  `nextseek_api/permissions.py:21-27` implements the predicate that does work.
- **`nextseek_api/views.py` imports every routed ViewSet unguarded**, in one block at
  `nextseek_api/views.py:43-66`, and a grep for `ImportError` over the 15 modules sitting
  directly in this directory returns exactly one hit, the schema-RAG forward-reference
  rebuild at `nextseek_api/models.py:2192`. No route import is defensive, so a failure
  inside any single service module takes down the whole URL prefix rather than one route.
- **A new route has to be declared in the CI registry in the same change.**
  `ci/gate/test_route_registry.py:29-39` diffs Django's live patterns against
  `ci/routes.py`, and `ci/gate/test_route_registry.py:1-3` calls that job otherwise
  informational and this gate the part that blocks, so a `router.register(...)` added on
  its own turns CI red.
- See `.claude/skills/nextseek-viewset/SKILL.md:18` before adding or changing a ViewSet.

## Landmines

- **`nextseek_api/tests.py` is dead code that still looks live.** A regular package
  shadows a same-named module, so `nextseek_api.tests` resolves to
  `nextseek_api/tests/__init__.py:2` and never to the file beside it, which runs to
  `nextseek_api/tests.py:2171`; confirmed on 2026-09-03 with `importlib.util.find_spec`
  against this checkout. Pytest will not collect it either: the config sets no `python_files` key
  (`pyproject.toml:146-148`), so only the two built-in patterns apply and `tests.py`
  matches neither. Editing it changes nothing that runs, and `.coveragerc:4` already
  omits it.
- **Nothing under this directory collects without a *configured* Django, not merely an
  installed one.** `nextseek_api/conftest.py:3` imports `django.contrib.auth.models` at
  module scope, which needs `INSTALLED_APPS`. On 2026-09-03 a host run of the one module
  whose docstring calls itself hermetic (`nextseek_api/tests/test_viewset_conventions.py:1`)
  died inside that conftest twice: first on a missing `django`, then, with Django
  installed, on `ImproperlyConfigured`. Budget the container, or expect a collection error
  instead of a test result.
- **The convention validator does not exit 0 on this branch.** Run on 2026-09-03 it
  reported 6 violations, every one of them in `nextseek_api/services/cc_assistant.py` or
  `nextseek_api/services/project_export.py`, and `nextseek_api/tests/test_viewset_conventions.py:304-306`
  asserts an empty list, so that test fails with it. Treat those six as the baseline you
  diff against; a clean exit is not available to compare with.
- **A green unit test does not prove the OpenAPI document builds.** Tests call ViewSet
  methods directly, so a decorator emitting no `content` block is invisible to them and
  fatal to the schema route; `nextseek_api/tests/test_viewset_conventions_schema.py:1`
  exists as the only local signal because it generates the whole document. On 2026-09-03
  it failed on three operations missing examples rather than on a build error, so the
  document itself does still generate.
- **`SeekAPIClient` is held as a class attribute, so one `requests.Session` is shared by
  every caller of a proxy ViewSet.** The instance is built once when the class body is
  evaluated; walking the AST of every non-test module under `nextseek_api/` on 2026-09-03
  for a class-level assignment calling `SeekAPIClient` found nine, one being
  `nextseek_api/services/people.py:35`, and each owns a session made at
  `nextseek_api/helpers.py:128`. The smoke suite records the matching live symptom and
  names the cause: two authenticated accounts are reported as the same SEEK person, and
  the marker says it flips green "when the proxy client is fixed"
  (`ci/smoke/test_health.py:94-99`). Copy that pattern into a new service and you copy
  the defect.
- **An unmatched path under this prefix does not produce a DRF 404.** Mezzanine's
  catch-all is included under a bare `^` at `dmac/urls.py:55`, after this app at
  `dmac/urls.py:29`, and `dmac/urls.py:60` points the 404 handler at Mezzanine's page
  view, so a typo'd or retired API path returns HTML. Verified with Django's resolver on
  2026-09-03. A client parsing JSON gets a parse error rather than a status it can act on.
- **Three modules here are superseded sketches, not the SEEK client.**
  `nextseek_api/seek_api.py:15-33` prints its response instead of returning it, and
  `nextseek_api/example.py:6` is a worked example nothing calls. A grep over every `.py`
  file in the tree for imports of `nextseek_api.seek_api`, `nextseek_api.seek_api_helpers`
  or `nextseek_api.example` returns only their own test modules under `nextseek_api/tests/`
  plus `nextseek_api/example.py:2` itself; `.coveragerc:9` already drops one of the three
  from coverage. Reach for `nextseek_api/helpers.py:123` instead, or ship an endpoint that
  silently returns `None`.
- **Two ViewSets in this directory are unreachable over HTTP yet still constrain the
  validator.** `nextseek_api/views.py:395` and `nextseek_api/views.py:549` are registered
  only in the commented-out lines at `nextseek_api/urls.py:15-16`, while five of their
  methods are pinned by name in the grandfather list at
  `scripts/validate_viewset_conventions.py:157-183`. Rename one of those methods and the
  validator goes red over an endpoint no request can reach.
- **`nextseek_api/views.py:12` imports `IsAdminUser` and no line uses it** — a grep for
  that name over this one module returns the import and nothing else. It is a standing
  invitation to a gate that admits every logged-in SEEK user; see the invariant above for
  why that gate is worthless here.
- **A cross-reference in this code is now false.** `nextseek_api/views.py:270-272` says
  the sibling admin export "still does" widen its scope with `is_staff`; that method reads
  `is_superuser` alone at `nextseek_api/views.py:765`, and its own comment at
  `nextseek_api/views.py:751-752` describes the widening in the past tense. Trusting the
  pointer sends you hunting for a hole that was closed.
- **The migration sequence forks three times and is stitched by two merge migrations.**
  Listing `nextseek_api/migrations/` on 2026-09-03 shows two files each for the prefixes
  0005, 0010 and 0011 — `nextseek_api/migrations/0010_attribute_mutation_job.py:1` and
  `nextseek_api/migrations/0010_turn_ledger.py:1` are one such pair — closed by
  `nextseek_api/migrations/0006_merge_extra_state_guards.py:1` and by
  `nextseek_api/migrations/0019_merge_attribute_async_turn_ledger.py:6-11`, the latter an
  empty migration whose only job is to depend on both heads. Add a migration without
  checking the current heads and you create a fork Django refuses to apply.

## Test command

The cheapest gate that actually covers this shell is the conventions pair, run in a
throwaway container over a copy of the checkout:

```
docker run --rm --network none -v "$PWD":/src:ro \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -w / nextseek-nextseek:latest \
  bash -lc 'cp -a /src /build && cd /build && /app/.venv/bin/python -m pytest \
    nextseek_api/tests/test_viewset_conventions.py \
    nextseek_api/tests/test_viewset_conventions_schema.py -q'
```

Ran 2026-09-03: 4 failed, 41 passed, 96 warnings in 9.36s. All four failures are the
baseline described above, not something you broke. The pure-AST half needs no container
and finishes in under a fifth of a second — `/usr/bin/python3 scripts/validate_viewset_conventions.py`,
which printed 6 violations and exited 1 on the same date.

## See also

- See `nextseek_api/README.md` for what each module holds, the router surface, the wider
  test lane with its numbers, and the dependency edges in both directions.
- See `nextseek_api/cc_assistant/CLAUDE.md`, `nextseek_api/assistant/CLAUDE.md`,
  `nextseek_api/attributes/CLAUDE.md`, `nextseek_api/batch_upload/CLAUDE.md` and
  `nextseek_api/eval/CLAUDE.md` for the traps inside each child.
- See `.claude/skills/nextseek-viewset/references/patterns.md:65-83` for the decorator and
  auth recipes a new ViewSet copies.
- See `docs/endpoint-authorization-register.md:1` for the per-endpoint authorization
  ruling the documentation routes are bucketed under.
- See `DEPLOYMENT.md:1` for the runbook this app is deployed by.
