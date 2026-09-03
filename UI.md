# NExtSEEK UI Reference

Quick reference for all UI pages, their routes, views, and templates.

> **Refreshed 2026-09-03** against the worktree on branch `docs/repo-wide-refresh` at `ad226f1`. `seek/views.py` no longer exists — the views are a package of 11 modules (`seek/views/__init__.py:11-20`), so every view citation below names its owning module. The authority for the template layer is [`themes/README.md`](themes/README.md); for the pages and their views, [`seek/README.md`](seek/README.md); for the chat UI, [`chat_frontend/README.md`](chat_frontend/README.md). Where this file and one of those pairs disagree, the pair wins.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend                              │
├─────────────────────────────┬───────────────────────────────┤
│  Django Templates           │  React Chat App               │
│  (themes/NextSeek/ + seek/) │  (chat_frontend/)             │
│  - Bootstrap 5 (CDN)        │  - Vite 7 + TypeScript        │
│  - jQuery EasyUI 1.5.2      │  - Tailwind 4 + shadcn/ui     │
│  - Server-rendered          │  - localhost:5173             │
└─────────────────────────────┴───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Django Backend                           │
├───────────────┬─────────────────┬───────────────────────────┤
│  seek/        │  nextseek_api/  │  api_app/                 │
│  Main app     │  Chat/AI API    │  Legacy API (UNROUTED:    │
│  views/ pkg   │  services/      │  dmac/urls.py:28)         │
└───────────────┴─────────────────┴───────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Databases                               │
├─────────────────────────────┬───────────────────────────────┤
│  MySQL (SEEK + NExtSEEK)    │  Neo4j (Graph)                │
└─────────────────────────────┴───────────────────────────────┘
```

`api_app`'s URL include is commented out (`dmac/urls.py:28`), so nothing under `/api/` resolves today.

---

## Chat Frontend (React)

Separate React app for the AI chat assistant interface.

**Location**: `chat_frontend/`

**Stack**: React 19.2 + Vite 7.2 + TypeScript + Tailwind CSS 4.1 + shadcn/ui (`chat_frontend/package.json`)

**Dev Server**: `http://localhost:5173`

**API**: Talks to `/nextseek_api/` endpoints; the chat turn goes to `/nextseek_api/cc-assistant/query/async/` (`chat_frontend/src/lib/services/chatApi.ts:78`)

> The Dockerfile has no build step — the committed Vite bundle is what ships, so a UI change is a two-step commit (source + rebuilt bundle). See `chat_frontend/README.md`.

### Structure
```
chat_frontend/src/
├── App.tsx                    # Main app entry
├── AppLayout.tsx              # Layout wrapper
├── EmbeddedApp.tsx            # Embeddable version for Django pages
├── main.tsx                   # Standalone entry
├── main.embedded.tsx          # Embedded entry (the one Django loads)
├── index.css                  # Standalone styles
├── index.embedded.css         # Embedded styles
├── components/
│   ├── ChatPanel/             # Chat interface components
│   ├── DebugPanel/            # Debug/testing tools
│   ├── Layout/                # Layout components
│   ├── Sessions/              # Session list / rename / delete
│   ├── TestRunner/            # Test utilities
│   ├── __tests__/             # Component tests
│   └── ui/                    # shadcn/ui components (buttons, dialogs, etc.)
├── hooks/                     # React hooks
├── lib/                       # Utilities and services (chatApi.ts, sessionAuth.ts)
└── test/                      # Test setup
```

### Commands
```bash
cd chat_frontend
npm install          # Install deps
npm run dev          # Dev server (localhost:5173)
npm run build        # Production build (tsc -b && vite build)
npm run build:embedded  # Build for embedding in Django
npm test             # Run tests (vitest run)
npm run test:e2e     # Playwright E2E tests
```

All seven are declared in `chat_frontend/package.json` under `scripts`.

### Key Components
| Component | Path | Purpose |
|-----------|------|---------|
| ChatPanel | `src/components/ChatPanel/` | Main chat interface |
| Sessions | `src/components/Sessions/` | Session list, rename, delete |
| DebugPanel | `src/components/DebugPanel/` | Debug tools |
| UI Components | `src/components/ui/` | Reusable shadcn components |

---

## Django Theme (NextSeek)

## Theme Structure

`themes/NextSeek/templates/` holds **12** files (listed with `find` on 2026-09-03) — there is no `header.embed.html`:

```
themes/NextSeek/templates/
├── base.html                        # Master layout (sidebar, header, footer)
├── base_auth.html                   # Full-screen auth layout
├── nav.embed.html                   # Sidebar navigation  (themes/NextSeek/templates/base.html:62)
├── page-footer.embed.html           # Footer               (themes/NextSeek/templates/base.html:95)
├── includes/user_panel.html         # User panel switch    (themes/NextSeek/templates/base.html:68)
├── accounts/includes/user_panel.html# User panel body
├── index.html                       # Home dashboard       (dmac/views.py:333)
├── login.html                       # Sign-in page         (dmac/views.py:168)
├── help/getting_started.html        # Help page            (seek/views/pages.py:7)
├── nextseek/swagger_ui.html         # Swagger override     (nextseek_api/urls.py:75)
├── content.embed.html               # DEAD — nothing renders or includes it
└── pages/menus/tree.html            # DEAD in the live chrome
└── static/
    ├── css/nextseek.css       # Main theme CSS  (themes/NextSeek/templates/base.html:24)
    └── js/nextseek.js         # Sidebar toggle, nav state (themes/NextSeek/templates/base.html:104)
```

Pages that extend `base.html` inject content via `{% block main %}`. `base.html` defines exactly five blocks — `title` (`themes/NextSeek/templates/base.html:7`), `extra_head` (`themes/NextSeek/templates/base.html:43`), `left_panel` (`themes/NextSeek/templates/base.html:61`), `main` (`themes/NextSeek/templates/base.html:90`), `extra_js` (`themes/NextSeek/templates/base.html:106`). `base_auth.html` defines four and has **no `main`** — `title` (`themes/NextSeek/templates/base_auth.html:7`), `extra_head` (`themes/NextSeek/templates/base_auth.html:24`), `body` (`themes/NextSeek/templates/base_auth.html:28`), `extra_js` (`themes/NextSeek/templates/base_auth.html:31`).

> **The repo-root `templates/` tree is unreachable.** All 81 of its files resolve to nothing through the real Django loader: `themes/NextSeek/templates` is the only filesystem directory in the search path (`dmac/settings.py:108-110`) and `themes.NextSeek` is separately an installed app (`dmac/settings.py:146`), so the root scaffold is never consulted. Do not edit it expecting a rendered change. The measurement and the six shadowed names are in `themes/README.md`.

---

## Pages by Category

Every URL below is unprefixed: `USE_I18N = False` (`dmac/settings.py:56`), so the `i18n_patterns` wrapper in `dmac/urls.py:21` adds no language segment.

### Home

| Page | URL | View | Template |
|------|-----|------|----------|
| Home/Dashboard | `/` | `dmac.views.home` (`dmac/urls.py:48`, `dmac/views.py:285`) | `themes/NextSeek/templates/index.html` (`dmac/views.py:333`) |

`content.embed.html` is **not** the home fragment — nothing renders or includes either copy of it (`themes/NextSeek/templates/content.embed.html` or `seek/templates/content.embed.html`).

---

### Data Entry

| Page | URL | View | Template | Embeds |
|------|-----|------|----------|--------|
| Batch Upload (Assay Sheets) | `/seek/samples/upload/` | `views.batchUpload` (`seek/views/upload.py:26`) | `seek/templates/batchUpload.html` (`seek/views/upload.py:52`) | `pages/batch_upload.embed.html` |
| Data/Protocol File Upload | `/seek/data/upload/` | `views.datafileUpload` (`seek/views/upload.py:300`) | `seek/templates/dataFileUpload.html` (`seek/views/upload.py:327`) | none — the template includes nothing |
| Download Templates | `/seek/templates/` | `views.templatesList` (`seek/views/assets.py:107`) | `seek/templates/templatesList.html` (`seek/views/assets.py:113`) | - |

`pages/datafile_upload.embed.html` is orphaned: no template includes it (searched every `.html` under `seek/templates`, `themes/NextSeek/templates` and `dmac/templates` for its name on 2026-09-03; zero hits).

---

### Data Query / Search

| Page | URL | View | Template | Embeds |
|------|-----|------|----------|--------|
| Sample Search (main) | `/seek/search/` | `views.searchAdvanced` (`seek/views/search.py:94`) | `seek/templates/searchAdvanced.html` (`seek/views/search.py:100`) | `pages/samples_search`, `samples_stable`, `searchAdvanced_search`, `searchAdvanced_stable`, `searchAdvanced_deletion` |
| Sample Search (legacy) | `/seek/samples/search/` | `views.sampleSearch` (`seek/views/search.py:13`) | **none — 302 to `/seek/search/`** (`seek/views/search.py:20`) | - |
| New Search | `/seek/newsearch/` | `views.newSearch` (`seek/views/search.py:117`) | `seek/templates/newSearch.html` (`seek/views/search.py:118`) | six `pages/*_new*` embeds |
| Data File Query | `/seek/datafile/query/` | `views.datafileQuery` (`seek/views/assets.py:57`) | `seek/templates/dataFilesPage.html` (`seek/views/assets.py:60`) | `pages/datafile_table.embed.html` |
| Protocol (SOP) Query | `/seek/sop/query/` | `views.sopQuery` (`seek/views/assets.py:51`) | `seek/templates/sopsPage.html` (`seek/views/assets.py:54`) | `pages/sops_table.embed.html` |
| Smart Query (AI) | `/seek/assistant/` | `views.smartSearch` (`seek/views/search.py:110`) | `seek/templates/smartSearch.html` (`seek/views/search.py:114`) | - (mounts the React app at `seek/templates/smartSearch.html:7-8`) |

`sampleSearch.html` still exists on disk but its only `render` call is commented out (`seek/views/search.py:19`).

---

### Catalogs

| Page | URL | View | Template |
|------|-----|------|----------|
| Sample Types List | `/seek/sampletypes/` | `views.sampleTypesList` (`seek/views/catalog.py:54`) | `seek/templates/sampleTypesList.html` (`seek/views/catalog.py:57`) |
| Sample Type Detail | `/seek/sampletypes/<code>/` | `views.sampleTypeDetail` (`seek/views/catalog.py:64`) | `seek/templates/sampleTypeDetail.html` (`seek/views/catalog.py:69`) |
| Assays List | `/seek/assays/` | `views.assaysList` (`seek/views/catalog.py:94`) | `seek/templates/assaysList.html` (`seek/views/catalog.py:100`) |
| Assay Detail | `/seek/assays/<slug>/` | `views.assayDetail` (`seek/views/catalog.py:108`) | `seek/templates/assayDetail.html` (`seek/views/catalog.py:113`) |

All four include the shared `catalog_styles.html`. The URL prefix is deliberately `sampletypes` (no underscore) — `sample_types/id=<id>/` is a different page that lists samples *of* a type (`seek/urls.py:55-58`).

---

### Sample Views

| Page | URL | View | Template | Embeds |
|------|-----|------|----------|--------|
| Sample by ID | `/seek/sample/id=<id>/` | `views.sample` (`seek/views/samples.py:37`) | `seek/templates/samples.html` (`seek/views/samples.py:73`) | `pages/samples.embed.html` → `samples_tree`, `samples_tree_new` |
| Sample Tree by UID | `/seek/sampletree/uid=<uid>/` | `views.sampleTree` (`seek/views/samples.py:75`) | same — delegates to `sample()` (`seek/views/samples.py:79`) | as above |
| Samples of a Sample Type | `/seek/sample_types/id=<id>/` | `views.sample_type` (`seek/views/samples.py:84`) | `seek/templates/sampleQuery.html` (`seek/views/samples.py:107`) | `pages/seek_includes.html`, `pages/samples_table.embed.html` |
| Sample Timeline (NHP) | `/seek/sample_timeline/` | `TemplateView` (`seek/urls.py:86`) | `seek/templates/sample_timeline.html` | - |
| NHP Info | `/seek/nhpinfo/<name>/` | `views.nhp_info` (`seek/views/timeline.py:15`) | (JSON) | - |

---

### Projects

| Page | URL | View | Template |
|------|-----|------|----------|
| Projects List | `/seek/projects/` | `views.projects` (`seek/views/projects.py:29`) | `seek/templates/projectsList.html` (`seek/views/projects.py:70`) |
| Project Detail | `/seek/projects/<id>/` | `views.project_page` (`seek/views/projects.py:74`) | `seek/templates/projectPage.html` (`seek/views/projects.py:134`) |
| Project Connections (iframe) | `/seek/projects/<id>/connections/` | `views.project_connections` (`seek/views/projects.py:164`) | **none** — returns a complete HTML document (`seek/views/projects.py:194`) |

---

### Admin

| Page | URL | View | Template |
|------|-----|------|----------|
| Django Admin | `/admin/` | Django admin (`dmac/urls.py:26`) | (Django built-in) |
| Clades Management | `/seek/admin/clades/` | `views.adminClades` (`seek/views/admin.py:149`) | `seek/templates/clades.html` (`seek/views/admin.py:154`) |
| Sample Attributes | `/seek/samples/attributes/` | `views.sampleAttributes` (`seek/views/samples.py:288`) | `seek/templates/sampleAttributes.html` (`seek/views/samples.py:296`) |
| Admin Sample Retrieval | `/seek/admin/retrieve/` | `views.adminRetrieveSamples` (`seek/views/admin.py:31`) | `seek/templates/admin_retrieval.html` (`seek/views/admin.py:65`) |
| Internal Assays | `/seek/admin/internal_assays/` | `views.internalAssays` (`seek/views/admin.py:218`) | `seek/templates/internal_assays.html` (`seek/views/admin.py:224`) |

---

### Authentication

| Page | URL | View | Template |
|------|-----|------|----------|
| Login | `/login/` (and `/accounts/login/`, `dmac/urls.py:56`) | `views.login_seek` (`dmac/views.py:110`) | `themes/NextSeek/templates/login.html` (`dmac/views.py:168`), which extends `base_auth.html` (`themes/NextSeek/templates/login.html:1`) |
| Signup | `/signup/` (and `/accounts/signup/`, `dmac/urls.py:54`) | `views.signup_seek` (`dmac/views.py:267`) | **none — 302 to SEEK's own `/signup`** (`dmac/views.py:280`) |
| Logout | `/logout` | `views.logout_seek` (`dmac/views.py:170`) | (redirect to `reverse('index')`, `dmac/views.py:174`) |

<!-- UNVERIFIED: no `name="index"` URL is declared anywhere in this repository (searched every tracked `.py` for `name="index"` on 2026-09-03; zero hits), and Mezzanine is not installed in this worktree's `.venv`, so whether `logout_seek`'s reverse resolves or raises could not be established here. CI excludes the route (`ci/routes.py:234-236`). -->

### Two routed views that cannot render

`dmac/views.py:214` renders `home.html` and `dmac/views.py:255` renders `seek_login.html`. **Neither template exists anywhere on disk** (`find` over the whole worktree for both names on 2026-09-03 returned nothing), so both calls raise `TemplateDoesNotExist`. They live in `login_full` (`dmac/views.py:176`) and `index` (`dmac/views.py:218`), neither of which is named in `dmac/urls.py`.

---

### Publishing

| Page | URL | View | Template | Embeds |
|------|-----|------|----------|--------|
| Publish Samples | none | none | `seek/templates/publish.html` | `pages/publish_search`, `publish_stable` |
| Publish Assets | none | none | `seek/templates/publishAssets.html` | `pages/publishAssets_search`, `publishAssets_stable` |

Both wrappers are **unreachable**: neither name appears in any `render(...)` or `template_name=` in `seek/views/`, `seek/urls.py` or `seek/sample/` (extracted every such literal on 2026-09-03 — 24 names, and these two are not among them), and no template extends or includes them.

---

### Other

| Page | URL | View | Template |
|------|-----|------|----------|
| Help / Getting Started | `/seek/help/` | `views.getting_started` (`seek/views/pages.py:5`) | `themes/NextSeek/templates/help/getting_started.html` (`seek/views/pages.py:7`) |
| Error page | (on a failed access check) | - | `seek/templates/error.html` (`seek/views/search.py:113`, `seek/views/projects.py:93`) |
| Batch Search | none | none | `seek/templates/batchSearch.html` — **unreachable**, no view renders it |
| Sample Deletion | none | none | `seek/templates/sampleDeletion.html` — **unreachable** |
| Sample Upload (old) | none | none | `seek/templates/sampleUpload.html` — **unreachable** |
| Samples Test | none | none | `seek/templates/samplesTest.html` — **unreachable** |
| 404 | not wired | `handler404 = mezzanine.core.views.page_not_found` (`dmac/urls.py:60`) | `seek/templates/pages/404.html` is **dead** — its name appears in no `.py` or `.html` in the tree |
| Denied | not wired | - | `seek/templates/pages/denied.html` is **dead** — same search, zero hits |

---

## Key Files

### Views
- **Main views**: `seek/views/` — a package of 11 modules (`admin`, `assets`, `catalog`, `pages`, `projects`, `samples`, `search`, `shared`, `timeline`, `upload`, `__init__`). `seek/views.py` **does not exist**. Every name the URL conf uses is re-exported at `seek/views/__init__.py:11-20`; patching `seek.views.X` no longer reaches the call site — patch the owning module (`seek/views/__init__.py:6-8`).
- **Auth views**: `dmac/views.py` — login (`dmac/views.py:110`), logout (`dmac/views.py:170`), signup (`dmac/views.py:267`), home (`dmac/views.py:285`).
- The sample table layer split the same way: `seek/dbtable_sample.py` is now a two-line shim over `seek/sample/` (`seek/dbtable_sample.py:1-2`).

### URL Routing
- **Seek URLs**: `seek/urls.py` — 62 `re_path` entries (counted with `grep -c 're_path('` on 2026-09-03), all `/seek/*`.
- **Root URLs**: `dmac/urls.py` — auth (`dmac/urls.py:22-24`), admin (`dmac/urls.py:26`), `^seek/` (`dmac/urls.py:27`), `^nextseek_api/` (`dmac/urls.py:29`), `/media/` (`dmac/urls.py:39`), home (`dmac/urls.py:48`), Mezzanine catch-all (`dmac/urls.py:55`).
- **Route registry**: `ci/routes.py` declares every application URL once, with the expected status per profile — the fastest way to see what a page is supposed to return.

### Templates Structure
```
seek/templates/
├── *.html                    # Page wrappers (extend base.html) — 32 files
└── pages/
    └── *.embed.html          # Page content (included in wrappers)

themes/NextSeek/templates/    # 12 files — the ONLY directory on the search path
├── base.html                 # Master layout
├── base_auth.html            # Auth layout (block `body`, not `main`)
├── *.embed.html              # Theme components
├── includes/user_panel.html  # Auth state display
├── help/, nextseek/, accounts/, pages/menus/

templates/                    # 81 files — UNREACHABLE mezzanine scaffold
```

`seek/templates/` holds 65 `.html` files in total (`seek/README.md`), reached by the app-directories loader (`dmac/settings.py:131`).

---

## UI Components

### jQuery EasyUI (retained)
Used for datagrids, tabs, and panels throughout the app.
- **CSS**: `static/jquery-easyui-1.5.2/themes/default/easyui.css` (`themes/NextSeek/templates/base.html:20`)
- **JS**: `static/jquery-easyui-1.5.2/jquery.easyui.min.js` (`themes/NextSeek/templates/base.html:32`)
- **Overrides**: `themes/NextSeek/static/css/nextseek.css`

### Bootstrap 5 (new)
Layout and utility classes, loaded from a CDN in `base.html` alongside Google Fonts and Bootstrap Icons (`themes/NextSeek/templates/base.html:11-17`). An air-gapped install loses the whole visual layer while still rendering HTML.

---

## Updating a Page

1. Find the page in the tables above
2. Locate the view in the right `seek/views/*.py` module (not `seek/views.py` — it is gone)
3. Find the template and any embeds
4. Edit the template/embed HTML
5. For styling, update `themes/NextSeek/static/css/nextseek.css`
6. Run `collectstatic` after CSS/JS changes — the entrypoint does this on every container start (`docker/scripts/entrypoint.sh:13`)

Compose bind-mounts `./themes/NextSeek` over the image copy (`docker-compose.yml:29`), so theme edits land without a rebuild; `seek/templates/` does not get that treatment.

---

## NextSeek API (Chat Backend)

API endpoints used by the chat frontend.

**Location**: `nextseek_api/`

**Base URL**: `/nextseek_api/` (`dmac/urls.py:29`)

### Key Modules
| Module | Path | Purpose |
|--------|------|---------|
| Views alias layer | `nextseek_api/views.py` | aliases the ViewSet classes `urls.py` registers |
| Models | `nextseek_api/models.py` | Data models |
| Services | `nextseek_api/services/` | the ViewSet + service layer (20 routed ViewSets) |
| Assistant | `nextseek_api/assistant/` | shared library: ORM models, granular ops, WS consumer |
| CC Assistant | `nextseek_api/cc_assistant/` | route decision + the per-turn sandbox |
| Batch Upload | `nextseek_api/batch_upload/` | File upload handling |
| Attributes | `nextseek_api/attributes/` | native attribute API |
| Assay Registration | `nextseek_api/assay_registration/` | batch assay membership |
| Schema RAG | `nextseek_api/schema_rag/` | Schema-based retrieval for AI |
| Eval | `nextseek_api/eval/` | HiBayes evaluation pipeline |

### URL Config
`nextseek_api/urls.py` — the DRF router registrations run `nextseek_api/urls.py:14`–`nextseek_api/urls.py:42`; the OpenAPI routes are `/schema/` (`nextseek_api/urls.py:65`), `/swagger/` (`nextseek_api/urls.py:72`, template overridden to `nextseek/swagger_ui.html` at `nextseek_api/urls.py:75`) and `/redoc/` (`nextseek_api/urls.py:77`), all three behind `IsAuthenticated`. The router URLs are included at `nextseek_api/urls.py:80`.

---

## Notes

- All `seek/` page templates extend `base.html` — 31 of them, and that is the only `{% extends %}` target in the directory (`themes/README.md`)
- Auth pages extend `base_auth.html`, which has **no `main` block**; the sign-in page fills `body` instead (`themes/NextSeek/templates/login.html:255`)
- Content goes in `{% block main %}{% endblock %}` (`themes/NextSeek/templates/base.html:90`)
- Extra head content: `{% block extra_head %}` (`themes/NextSeek/templates/base.html:43`)
- Extra JS: `{% block extra_js %}` (`themes/NextSeek/templates/base.html:106`)
- `left_panel` (`themes/NextSeek/templates/base.html:61`) is overridden by none of the 31 pages, so every signed-in page shows the same sidebar
- Chat frontend is a separate React app; the Django page mounts the embedded build

---

## EasyUI Component Inventory

> **Future replacement roadmap.** **46** template files use an `easyui-*` component class, established on 2026-09-03 by grepping `easyui-` across `seek/templates/`, `dmac/templates/` and `themes/NextSeek/templates/` and then testing each hit for each of the eight component names. Excluded: `themes/NextSeek/static/jquery-easyui-1.5.2/` (vendored demo pages) and `themes/NextSeek/static/js/easyui/*.html` (two export helpers, not templates). `themes/NextSeek/templates/base.html` matches the string `easyui` only in asset paths (`themes/NextSeek/templates/base.html:20-21`, `themes/NextSeek/templates/base.html:31-32`) and a comment (`themes/NextSeek/templates/base.html:36`), and uses no component class.
>
> **Four rows in the previous edition of this table were wrong** and have been removed: `seek/templates/pages/samples_atable.embed.html` and `seek/templates/pages/samples_attributes.embed.html` do not exist, and `seek/templates/sampleAttributes.html` and `seek/templates/smartSearch.html` contain no `easyui-` class at all.

### Priority 1 - Core Datagrids (highest user impact)

| # | Template | Path | Components | Reachable? |
|---|----------|------|------------|------------|
| 1 | samples_table | `seek/templates/pages/samples_table.embed.html` | datagrid, linkbutton | yes (sampleQuery.html) |
| 2 | searchAdvanced_stable | `seek/templates/pages/searchAdvanced_stable.embed.html` | datagrid, linkbutton | yes |
| 3 | searchAdvanced_new_stable | `seek/templates/pages/searchAdvanced_new_stable.embed.html` | datagrid, linkbutton | yes (newSearch.html) |
| 4 | batchSearch_table | `seek/templates/pages/batchSearch_table.embed.html` | datagrid, linkbutton | only via the dead `batchSearch.html` |
| 5 | datafile_table | `seek/templates/pages/datafile_table.embed.html` | datagrid, linkbutton | yes |
| 6 | sops_table | `seek/templates/pages/sops_table.embed.html` | datagrid, linkbutton | yes |
| 7 | publishAssets_stable | `seek/templates/pages/publishAssets_stable.embed.html` | datagrid, linkbutton | no — parent is unreachable |
| 8 | publish_stable | `seek/templates/pages/publish_stable.embed.html` | datagrid, linkbutton | no — parent is unreachable |
| 9 | searchAdvanced_rtable | `seek/templates/pages/searchAdvanced_rtable.embed.html` | datagrid, linkbutton | **no — orphan, nothing includes it** |
| 10 | clades | `seek/templates/clades.html` | datagrid, layout, tabs, linkbutton | yes |
| 11 | internal_assays | `seek/templates/internal_assays.html` | datagrid, layout, tabs, linkbutton | yes |
| 12 | datagrid_custom_table | `dmac/templates/pages/datagrid_custom_table.embed.html` | datagrid, linkbutton, dialog | **no — orphan** |

### Priority 2 - Layout/Tabs Wrappers (page shells)

| # | Template | Path | Components | Reachable? |
|---|----------|------|------------|------------|
| 13 | searchAdvanced | `seek/templates/searchAdvanced.html` | layout, tabs, textbox | yes |
| 14 | batchSearch | `seek/templates/batchSearch.html` | layout, tabs | no |
| 15 | batchUpload | `seek/templates/batchUpload.html` | layout, tabs | yes |
| 16 | sampleUpload | `seek/templates/sampleUpload.html` | layout, tabs | no |
| 17 | sampleSearch | `seek/templates/sampleSearch.html` | layout, tabs | no — render commented out |
| 18 | sampleQuery | `seek/templates/sampleQuery.html` | layout, tabs | yes |
| 19 | sampleDeletion | `seek/templates/sampleDeletion.html` | layout, tabs | no |
| 20 | dataFilesPage | `seek/templates/dataFilesPage.html` | layout, tabs | yes |
| 21 | newSearch | `seek/templates/newSearch.html` | layout, tabs | yes |
| 22 | sopsPage | `seek/templates/sopsPage.html` | layout, tabs | yes |
| 23 | publish | `seek/templates/publish.html` | layout, tabs | no |
| 24 | publishAssets | `seek/templates/publishAssets.html` | layout, tabs | no |
| 25 | samples | `seek/templates/pages/samples.embed.html` | layout, tabs | yes |
| 26 | dataFileUpload | `seek/templates/dataFileUpload.html` | combobox, linkbutton | yes |
| 27 | admin_retrieval | `seek/templates/admin_retrieval.html` | layout, tabs, textbox | yes |

### Priority 3 - Forms & Inputs (comboboxes, textboxes)

| # | Template | Path | Components | Reachable? |
|---|----------|------|------------|------------|
| 28 | batch_upload | `seek/templates/pages/batch_upload.embed.html` | combobox, linkbutton | yes |
| 29 | batchSearch_query | `seek/templates/pages/batchSearch_query.embed.html` | combobox, linkbutton | **no — orphan** |
| 30 | batchSearch_search | `seek/templates/pages/batchSearch_search.embed.html` | combobox, linkbutton, textbox | **no — orphan** |
| 31 | samples_search | `seek/templates/pages/samples_search.embed.html` | combobox | yes |
| 32 | samples_newsearch | `seek/templates/pages/samples_newsearch.embed.html` | combobox, linkbutton | yes |
| 33 | samples_upload | `seek/templates/pages/samples_upload.embed.html` | combobox, linkbutton | only via `sampleUpload.html` |
| 34 | searchAdvanced_search | `seek/templates/pages/searchAdvanced_search.embed.html` | combobox, linkbutton, textbox | yes |
| 35 | searchAdvanced_newsearch | `seek/templates/pages/searchAdvanced_newsearch.embed.html` | combobox, linkbutton | yes |
| 36 | searchAdvanced_retrieval | `seek/templates/pages/searchAdvanced_retrieval.embed.html` | linkbutton, textbox | **no — orphan** |
| 37 | searchAdvanced_deletion | `seek/templates/pages/searchAdvanced_deletion.embed.html` | linkbutton, textbox | yes |
| 38 | publishAssets_search | `seek/templates/pages/publishAssets_search.embed.html` | combobox, linkbutton | no |
| 39 | publish_search | `seek/templates/pages/publish_search.embed.html` | combobox, linkbutton | no |
| 40 | datafile_upload | `seek/templates/pages/datafile_upload.embed.html` | linkbutton, textbox | **no — orphan** |
| 41 | dialog_custom_upload | `dmac/templates/pages/dialog_custom_upload.embed.html` | linkbutton, dialog | **no — orphan** |

### Priority 4 - Display / Action Only

| # | Template | Path | Components | Reachable? |
|---|----------|------|------------|------------|
| 42 | samples_new_stable | `seek/templates/pages/samples_new_stable.embed.html` | linkbutton | yes |
| 43 | samples_stable | `seek/templates/pages/samples_stable.embed.html` | linkbutton | yes |
| 44 | searchAdvanced_tree | `seek/templates/pages/searchAdvanced_tree.embed.html` | linkbutton, tree | **no — orphan** |
| 45 | searchAdvanced_newretrieval | `seek/templates/pages/searchAdvanced_newretrieval.embed.html` | linkbutton | yes |
| 46 | searchAdvanced_newdeletion | `seek/templates/pages/searchAdvanced_newdeletion.embed.html` | linkbutton | yes |

### Summary

Counts are file counts over the 46 templates above, each produced by `grep -rl "easyui-<name>"` across the three template directories on 2026-09-03.

| Component | Count | Notes |
|-----------|-------|-------|
| `easyui-datagrid` | 12 | Core data tables - highest migration complexity |
| `easyui-layout` | 16 | Page shells - replaceable with Bootstrap |
| `easyui-tabs` | 16 | Same 16 files as `layout` |
| `easyui-combobox` | 11 | Dropdown selectors - replaceable with Select2 or native `<select>` |
| `easyui-linkbutton` | 31 | Styled buttons - simple to replace with Bootstrap `.btn` |
| `easyui-textbox` | 7 | Enhanced inputs - replaceable with `.form-control` |
| `easyui-dialog` | 2 | Modals - both in orphaned `dmac/templates/` files |
| `easyui-tree` | 1 | Tree navigation - in an orphaned file |

**Ten of the 46 are orphans or sit under an unreachable wrapper.** Deleting the orphans (`#9`, `#12`, `#29`, `#30`, `#36`, `#40`, `#41`, `#44`) and the dead wrappers before a migration removes roughly a fifth of the work.
