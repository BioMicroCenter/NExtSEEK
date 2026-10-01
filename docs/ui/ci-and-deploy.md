# CI, static files and deploy

## What this covers

Everything between editing a UI file and seeing it live: the CI contracts a UI change must satisfy
(route registry, docs map, which checks block GitHub), the static-file pipeline (hashed names,
directory order, nginx caching), how to preview a change, how a change reaches the dev box and
production, and the rule for the chat bundle.

It does not cover what the templates and CSS contain (see [shell.md](shell.md), [pages.md](pages.md),
[styles.md](styles.md)) or how the chat panel is built (see [chat-frontend.md](chat-frontend.md)).
Owner docs stay authoritative for their folders: `ci/README.md`, `ci/CLAUDE.md`, `themes/README.md`,
`themes/CLAUDE.md`, `DEPLOYMENT.md`, `NessieAI/chat_frontend/README.md`.

## How it works

### The path from edit to browser

```
edit file
  |
  |-- themes/NextSeek/templates/*.html   bind mount -> next request (box only)
  |-- themes/NextSeek/static/*           bind mount, collected at container start -> app restart
  |-- seek/templates, static/, dmac/, seek/*.py   baked into image -> ./startup.sh rebuild
  |-- chat_frontend/src                  npm run build:embedded -> commit bundle -> rebuild
  v
git push origin/dev -> box: git pull -> ./startup.sh rebuild --no-ci -> ./startup.sh ci
```

App code is baked into the `nextseek` image (`COPY . /app/` in the `Dockerfile`). The only paths
bind-mounted over the running container are `themes/NextSeek/` (in `docker-compose.yml`) and
`dmac/local_settings.py`. Anything else needs a rebuild (`DEPLOYMENT.md`, "Key facts").

### What CI checks, and where

| Lane | Runs | What it checks for a UI change | Blocks GitHub |
|---|---|---|---|
| `ci/gate` (step in `.github/workflows/ci-pytest.yml`) | every push to dev/main and every PR | route registry complete both ways, route effects, writer registry, docs map | yes |
| Blocking unit tests (`ci/blocking_lanes.py`) | same | graph tests plus `seek/tests/test_sample_search_*.py` | yes |
| Informational pytest | same | the rest of pytest, diffed against `ci/pytest-baseline.txt` | no |
| Smoke lane (`./startup.sh ci`, `ci/smoke/`) | by hand, or after a rebuild on a box | every registry route is reachable, Playwright flows, deploy-live checks | no (needs a live box) |

Only the gate, the docs map and the blocking tests can turn GitHub red. A UI change that passes them
can still break a page that nothing probes (see Gotchas).

### Static files

`STORAGES["staticfiles"]` in `dmac/settings.py` is `dmac.storage.ForgivingManifestStaticFilesStorage`,
a subclass of Django's `ManifestStaticFilesStorage` with `manifest_strict = False`. `{% static %}`
therefore renders a content-hashed URL such as `/static/css/nextseek.<hash>.css`, and a reference the
manifest does not know falls back to the plain URL instead of raising. `collectstatic` keeps the
unhashed original next to each hashed copy, so old unhashed URLs still resolve.

`STATICFILES_DIRS` in `dmac/settings.py` is two entries, in this order: `/app/themes/NextSeek/static`,
then `/app/static`. The first wins on a duplicate relative path, so editing the root twin of a file
that also exists under the theme changes nothing that is served (`themes/CLAUDE.md` counts the
overlap). `collectstatic` runs on every container start and aborts the start on failure
(`docker/scripts/entrypoint.sh`), so a rebuild or restart re-collects.

nginx (`docker/nginx.conf`, `location /static/`) sends `expires 30d` for everything under `/static/`,
hashed or not, with no `immutable` and no separate rule for hashed names. Consequences:

- A hashed URL changes when the content changes, so a normal theme deploy is visible at once.
- Anything that names an unhashed URL (a hard-coded `/static/...` in a template or JS string, a
  `url()` the manifest could not map) can stay stale in a visitor's browser for 30 days.
- HTML pages are not cached by nginx.

The chat bundle bypasses `{% static %}`: `seek/templatetags/vite_assets.py` (`_load_manifest`) builds
the URL from Vite's `.vite/manifest.json` read out of `STATICFILES_DIRS` (the source tree, not the
collected volume) and caches it per process unless DEBUG is on. A rebuilt bundle needs an app restart
to be picked up.

Tests use plain `StaticFilesStorage` (`dmac/test_settings.py`), so a render test never sees hashed
names and never exercises the forgiving storage.

## Inventory

### Registries a UI change may touch

| Registry | File and symbol | Touch it when |
|---|---|---|
| Route registry | `ci/routes.py`, list `REGISTRY` of `Route` | you add or remove an owned URL pattern |
| Owned-pattern filter | `ci/gate/live_routes.py`, `_PROJECT_LEVEL` (and `OWNED_PREFIXES`) | the new pattern is at the URL root, not under `seek/` or `nextseek_api/` |
| Route count | `ci/smoke/test_registry_contents.py`, `OWNED_ROUTE_COUNT` (asserted in `test_the_registry_covers_the_measured_route_count`) | the number of resolver-owned routes changes |
| Placeholder vocabulary | `ci/routes.py`, `PLACEHOLDERS` | a `path` contains `{name}` |
| Writer registry | `ci/writers.py` | a route has `effect="writes"` |
| Docs map | `ci/docs_map.py` (rules R1 to R10) | you add or move docs, READMEs, folders or skills |

### Tests that render or drive UI

| Test | What it covers | Runs in |
|---|---|---|
| `ci/smoke/test_reachability.py` | one test per registry route: status, no silent bounce to login. No body checks | smoke, needs a box |
| `ci/smoke/test_flows.py` | 7 Playwright flows: advanced search results, Nessie page wiring and send, sample page, upload blocked with no file, upload validate, simple search via graph_search | smoke, post-deploy |
| `ci/smoke/test_ui_shell.py` | HTTP: every signed-in page sends a visitor to `/login/?next=<that page>`; the visitor home has the top bar and hides signed-in links. Browser at 390x844 (iPhone Safari user agent, touch) and 1440x900: Sign in reachable from the top bar, hero and drawer; the menu stays after scrolling; no sideways scroll on the main pages (catalog pages are expected failures, UI-085); "+ New sample" full width with its desktop-only hint; the project Sample flow opens full screen on a phone and its frame never overflows on a desktop; the Nessie page is one full-height frame; signing in returns to the page asked for and ignores an off-site `next`; every inline `onclick` handler names a defined function | smoke, post-deploy (`flow` cases need Playwright) |
| `ci/smoke/test_deploy_live.py` | the box serves the committed chat bundle bytes; chat page names the committed entry; `/seek/search/` dropdown renders fast | smoke, all profiles |
| `seek/tests/test_navbar.py`, `test_catalog_pages.py`, `test_catalog_tables.py`, `test_home_dashboard.py`, `test_project_page.py`, `test_templates_page.py`, `test_sample_search_page.py`, `test_seek_public_links.py` | `render_to_string` of templates and views; some read `themes/NextSeek/static/css/nextseek.css` directly to check tokens | pytest |
| `seek/tests/test_admin_template_gating.py` | source guard: theme templates gate admin UI on `is_superuser`, never `is_staff` | pytest |
| `seek/tests/test_sample_search_js.py` with `seek/tests/js/sample_search_cases.js` | Sample Search page logic under `node` (skipped without node) | pytest, blocking glob |
| `NessieAI/chat_frontend` vitest and Playwright e2e | chat panel components and flows | host, node |

Not covered by any test: visual layout, CSS beyond token presence, the theme scripts under
`themes/NextSeek/static/js/`, responsive behaviour, and any Mezzanine CMS page.

### Where assets live

| Kind | Location | Note |
|---|---|---|
| Theme CSS | `themes/NextSeek/static/css/nextseek.css` | first `STATICFILES_DIRS` entry |
| Theme JS | `themes/NextSeek/static/js/nextseek.js`, `themes/NextSeek/static/js/easyui/` | same |
| Theme images | `themes/NextSeek/static/img/` | includes many legacy variants; every file is collected and served |
| Root static | `static/img/`, `static/js/`, `static/css/` | second entry; shadowed by a theme file of the same path |
| Chat bundle | `static/js/chat_assistant/` (`assets/`, `.vite/manifest.json`) | committed, see the bundle rule below |
| Vendored third party | `static/admin`, `static/grappelli`, `static/filebrowser`, `static/mezzanine`, `static/fonts`, both `jquery-easyui-1.5.2/` copies | collected like anything else; the theme copy of easyui wins |

## Worked example: register a public `/docs/<slug>/` route

First decide which side of the ownership boundary the route is on.

| Kind of route | CI work |
|---|---|
| A Mezzanine CMS page (a Page row made in the admin, rendered by `mezzanine.urls`) | None. The gate drops anything it does not own, so the page is also never probed. |
| A `re_path` in `dmac/urls.py` at the project level | Four edits (below). |
| A route in `seek/urls.py` | `Route` entries and the count bump. Owned by prefix, so skip `_PROJECT_LEVEL`. The pattern string carries the include prefix, for example `^seek/^help/$`. |

For a project-level index plus detail page:

1. Add the routes to `dmac/urls.py` as `re_path`, above the `re_path("^", include("mezzanine.urls"))`
   catch-all (the comment there explains why order matters). The gate refuses `path()` converter
   syntax such as `<slug:slug>` (it raises `NotImplementedError` in `ci/gate/live_routes.py`), so
   use `re_path`.
2. Add the resolver's pattern strings to `_PROJECT_LEVEL` in `ci/gate/live_routes.py`. Without this
   the gate stays green but the route is undeclared and unprobed.

```python
# ci/gate/live_routes.py, in _PROJECT_LEVEL
"^docs/$",
"^docs/(?P<slug>[\\w-]+)/$",
```

3. Add a `Route` per pattern in the `# project-level` block of `ci/routes.py`. Copy the shape of the
   `^seek/^help/$` entry.

```python
Route(pattern=r"^docs/$", path="/docs/",
      effect="reads",
      methods=("GET",), profiles="local,dev,prod", auth="anon", expect=200),
Route(pattern=r"^docs/(?P<slug>[\w-]+)/$", path="/docs/getting-started/",
      effect="reads",
      methods=("GET",), profiles="local,dev,prod", auth="anon", expect=200,
      note="fixed slug; a missing slug answers 404"),
```

4. Bump `OWNED_ROUTE_COUNT` in `ci/smoke/test_registry_contents.py` by the number of routes you added
   (two here). The gate is the authority on the right number; run it if unsure.
5. `ci/README.md` (gate section) says "seven project-level patterns". Update it to the new count.
6. If you also added Markdown under `docs/`, run the docs map (below).

Rules the tests enforce:

- `pattern` is the resolver's string verbatim, anchors included. The gate diffs exact strings both
  ways, so a wrong pattern fails as both "missing" and "stale". Its failure message prints a
  paste-ready skeleton (or run `scripts/dump_routes.py` in the container).
- The skeleton says `effect="UNCLASSIFIED"`, which the dataclass refuses. Use `reads` for a static
  page. `writes` needs writer ids in `ci/writers.py`.
- A `{placeholder}` in `path` must be in `PLACEHOLDERS` and discovered by the smoke fixtures, or the
  route is skipped. A fixed sample path avoids this but must exist on every box, so prefer a page
  that ships in the image.
- `expect` is the status when healthy: 200, or 302 for a login redirect. `auth="anon"` for public
  pages, `auth="web"` needs the session login. Prod routes must be GET-only.
- Each pattern appears once (checked at import). `ci/routes.py` may import only the standard library.

### Running the gate and docs map

The gate cannot run on the host (mysqlclient does not build). Use a throwaway container over the
worktree:

```bash
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest ci/gate -q -p no:cacheprovider
```

The docs map is plain Python, run from the repo root:

```bash
python3 ci/docs_map.py
```

For new Markdown it checks, among others: every tracked subfolder is named in its DOCS-MAP block (R1),
`docs/INDEX.md` has a row for each `docs/` subfolder (R3), relative links and backticked repo paths
resolve (R4), every README or CLAUDE.md is linked from another live doc (R6), the root `CLAUDE.md`
stays under its size cap (R7), and no emails or personal home paths appear (R10). `docs/superpowers/`
and `docs/archive/` are excluded. A new `docs/ui/` folder therefore needs a row in `docs/INDEX.md`.

## Previewing a change

There is no `runserver` loop and no dev server for Django templates, and nobody runs a full stack on
a laptop (rebuilds and CI run on the dev box). The working loops are:

| You edit | Loop |
|---|---|
| A template in `themes/NextSeek/templates/` | On a box, live on the next request: the directory is bind-mounted over `/app/themes/NextSeek` and no cached template loader is configured (`loaders` in `dmac/settings.py`). A `git pull` on the box is enough. |
| CSS, JS or images in `themes/NextSeek/static/` | The source changes live but browsers fetch the collected copy. Restart the app (`docker compose restart nextseek`; collectstatic runs at start). An exec'd `collectstatic` alone may leave running workers on the old manifest (see Gotchas). |
| A template in `seek/templates/`, anything in `dmac/`, `seek/`, `nextseek_api/`, `static/` | `./startup.sh rebuild` (baked into the image). |
| Template rendering only, no stack | The throwaway test container, below. |

Render tests in a worktree, without a stack:

```bash
docker run --rm --network=none -v "$PWD":/app:ro,z -v /app/.venv -w /app \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest seek/tests/test_navbar.py --no-migrations -q -p no:cacheprovider
```

The anonymous `-v /app/.venv` volume is required (`themes/README.md` explains why). A worktree run
through `scripts/run_tests.sh` mounts the compose directory's theme, not the worktree's, so a theme
template edited in a worktree does not show up there. To look at a change in a browser, push it and
view it on the dev box.

## Getting a UI change to the dev box and production

Run these on the box: the dev box first; production is a separate host, and nothing is tested there.

```bash
df -h /                      # the box has filled up before
git status --short           # other sessions share the tree; never stash or revert their files
git pull                     # theme template-only change: stop here, the bind mount serves it
                             # theme static only: docker compose restart nextseek, then stop
./startup.sh rebuild --no-ci # app code, seek/templates, static: rebuild the nextseek image
docker compose exec nextseek uv run manage.py collectstatic --noinput   # only if static/ changed
./startup.sh ci              # smoke lane, run separately after the rebuild
```

1. Commit and push to `origin/dev` (finished work goes straight to dev).
2. On the box, pull, then rebuild as needed. The rebuild recreates the container, which re-collects
   static at boot, so the explicit `collectstatic` is belt and braces.
3. `./startup.sh rebuild` also runs CI afterwards unless `--no-ci` is given (and `--no-nessie` skips
   only the paid Nessie lane). The default for UI work is `--no-ci`, then run `./startup.sh ci`
   when wanted.
4. Hard reload the browser (Ctrl+Shift+R). Hashed URLs update by themselves; a hard reload is for any
   unhashed reference.
5. Prod: the same commands on the prod host, deployed from dev or main only after the dev box is
   checked. The rebuild stops for review if free disk is below 20 GB (local, dev) or 30 GB (prod).
6. After a pull that touches `docker/nginx.conf`, the nginx bind mount can orphan. Do not recreate
   nginx unless asked.

### The chat bundle rule

A chat UI change is two commits: the source under `NessieAI/chat_frontend/src`, then the rebuilt
bundle in `static/js/chat_assistant/`. The Dockerfile has no npm step, so the committed bundle is
what ships.

```bash
(cd NessieAI/chat_frontend && npm run build:embedded)   # tsc -b --noEmit, then vite build (embedded config)
git add static/js/chat_assistant/                       # from the repo root; commit the bundle after the source
```

Then rebuild the app image as above (the new container collects static at start). Enforcement is
weak: `NessieAI/tests/build_tools/unit/test_committed_chat_bundle.py` only checks that the entry JS
contains two specific source strings, and `ci/smoke/test_deploy_live.py` checks that the box serves
the committed bytes (it catches a missing collectstatic or a stale image, not a bundle that was never
rebuilt from the source). No CI step runs the build and diffs.

## Where to edit

| Task | Files and symbols | Easy to miss |
|---|---|---|
| Register a new owned route | `ci/routes.py` (`REGISTRY`), `ci/gate/live_routes.py` (`_PROJECT_LEVEL`, root-level only), `ci/smoke/test_registry_contents.py` (`OWNED_ROUTE_COUNT`) | `re_path` only; pattern string verbatim; update the "seven" in `ci/README.md` |
| Add or move a docs page | `ci/docs_map.py` rules, `docs/INDEX.md` | run `python3 ci/docs_map.py`; new folders need an index row |
| Change static storage behaviour | `dmac/storage.py`, `STORAGES` in `dmac/settings.py` | tests use plain storage, so they will not show a regression |
| Change static directory order | `STATICFILES_DIRS` in `dmac/settings.py` | theme wins; 801 duplicate paths exist |
| Change `/static/` cache headers | `docker/nginx.conf`, `location /static/` | mounted file; needs an nginx recreate, ask the operator first |
| Change collect-on-boot | `docker/scripts/entrypoint.sh` | it is fail-fast on purpose |
| Add or replace an image or stylesheet | `themes/NextSeek/static/...` | edit the theme copy, not the root twin; reference it with `{% static %}` |
| Change the chat UI | `NessieAI/chat_frontend/src`, then `npm run build:embedded` | commit the bundle as a second commit |
| Change bundle checks | `NessieAI/tests/build_tools/unit/test_committed_chat_bundle.py`, `ci/smoke/test_deploy_live.py` | |

## Gotchas

- A new project-level route is silently unowned unless it is in `_PROJECT_LEVEL`: the gate stays green
  and no reachability probe runs. Nothing tells you.
- `OWNED_ROUTE_COUNT` is a hand-kept number checked in the smoke lane, which cannot tell you the right
  value. The gate is the authority.
- Mezzanine CMS pages (for example `/about/`) are deliberately outside CI. A broken one is not caught.
- `themes/CLAUDE.md` still describes the staticfiles backend as plain and unhashed. That is out of
  date: the backend hashes, and the 30-day expiry is harmless for hashed names. The comments in
  `dmac/storage.py` and `STORAGES` in `dmac/settings.py` also still say the vendored easyui tree is
  unreachable by `collectstatic`; the dev box serves it hashed.
- Exec-ing `collectstatic` into a running app writes new hashed files, but running workers may keep the
  old manifest until the app restarts (from reading `ManifestStaticFilesStorage`, not measured). A
  restart or rebuild avoids it.
- Only `themes/NextSeek/` is bind-mounted. A template edit under `seek/templates/` looks like it should
  work live and does not.
- Hard-coded unhashed `/static/...` URLs in templates or JS are the one way a theme deploy stays
  invisible for 30 days.
- The deploy-live smoke test fails with the exact `collectstatic` command if the chat bundle was not
  collected.

## Known issues

See [known-issues.md](known-issues.md#ci-and-deploy). The ones that matter most here:

- nginx gives unhashed and hashed static URLs the same 30-day expiry, so a stale unhashed reference
  can persist for a month.
- A new project-level route is silently unowned unless the author knows to edit `_PROJECT_LEVEL`.
- A chat change can ship without its rebuilt bundle; the only guard checks two source strings.
