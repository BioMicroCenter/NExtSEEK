# Page inventory

## What this covers

Every URL that returns HTML to a browser, at origin/dev caa55340: where it is routed, which view and template render it, who may see it, where it is linked from, and which guide page explains it in depth. It also says how to add a page end to end, and lists the external links the UI carries.

It does not explain how a page works inside. For the shell (`base.html`, sidebar, user panel) see [shell.md](shell.md). For upload and sample pages see [upload-and-samples.md](upload-and-samples.md), for search and downloads [search-and-downloads.md](search-and-downloads.md), for projects, catalogs and graphs [projects-catalogs-graphs.md](projects-catalogs-graphs.md), for the chat page [chat-frontend.md](chat-frontend.md), for help and API docs [docs-and-help.md](docs-and-help.md), for Mezzanine and dead files [legacy.md](legacy.md), and for CI details [ci-and-deploy.md](ci-and-deploy.md). JSON-only routes (`/seek/samples/searching/`, `/nextseek_api/...` viewsets and the like) are not listed here.

## How it works

Three URL confs stack up. `dmac/urls.py` is the root. It declares login, logout, signup, `^admin/`, `^seek/` (which includes `seek/urls.py`), `^nextseek_api/` (which includes `nextseek_api/urls.py`), the private `^media/` route, the home route, and then one catch-all, `re_path("^", include("mezzanine.urls"))`. Anything after that include is unreachable, because `^` matches every path. `USE_I18N` is off, so `i18n_patterns` adds no language prefix.

```
/                        dmac/urls.py  -> dmac.views.home
/login  /signup/         dmac/urls.py  -> dmac.views
/admin/...               Django admin
/seek/...                seek/urls.py  -> seek/views/*.py (65 re_path entries)
/nextseek_api/...        nextseek_api/urls.py (JSON viewsets + 3 doc pages)
/media/...               dmac.media.serve_media (login required)
<everything else>        Mezzanine: blog, /search/, /accounts/*, CMS pages, 404
```

Templates resolve through two places. `themes/NextSeek/templates/` is first in `TEMPLATES["DIRS"]` (`dmac/settings.py`) and holds the shell and the home, login and help pages. `seek/templates/` is found by the app-directories loader and holds the rest (`searchAdvanced.html`, `projectPage.html`, `sampleTypesList.html` and so on). Both are addressed by bare name, for example `render(request, "projectPage.html")`. A name that exists in both places resolves to the theme copy. Only `themes/NextSeek/` is bind-mounted into the running container, so a theme template edit shows on the next request and a `seek/templates/` edit needs a rebuild (see [ci-and-deploy.md](ci-and-deploy.md)).

Auth is decided per view, not in the URL conf. The labels used in the tables below:

| Label | Meaning | Mechanism |
|---|---|---|
| anonymous | no check | none |
| SEEK login | the SEEK session is verified; on failure the browser is redirected to `/login/?next=<the page asked for, with its query string>` | `@requires_seek_login_redirect()` in `seek/decorators.py`, or `login_redirect(request)` from the same module in an inline check |
| Django user | the Django session user | `request.user.is_authenticated` (the assistant page only) |
| superuser | SEEK login plus superuser | `@requires_supervisor` in `seek/decorators.py` (superuser, not `is_staff`; every SEEK user becomes `is_staff` at login) |
| member | SEEK login plus project membership | checked in the view (`_may_see_project` and similar) |

The string passed to `requires_seek_login_redirect` is the `next` target after login. Several views pass a literal that is not their own URL, so the user lands somewhere else after signing in; those are in [known-issues.md](known-issues.md#pages).

## Inventory

### Home and auth

| URL | URL name | View | Template | Who | Linked from | Covered in |
|---|---|---|---|---|---|---|
| `/` | `home` | `dmac/views.py:home` | `themes/NextSeek/templates/index.html` (block `main`) | anonymous (see known issues) | sidebar wordmark and "Home" in `nav.embed.html`, login page wordmark | [shell.md](shell.md), [projects-catalogs-graphs.md](projects-catalogs-graphs.md) |
| `/login` | `login_seek` | `dmac/views.py:login_seek` | `login.html` (extends `base_auth.html`, styles inline in the file) | anonymous | "Sign in" in `accounts/includes/user_panel.html`, every login redirect | [shell.md](shell.md) |
| `/signup/` and `/accounts/signup/` | `signup_seek` (both; reverse resolves to the `accounts/` one) | `dmac/views.py:signup_seek` | none: 302 to `SEEK_PUBLIC_URL + /signup` | anonymous | "Sign up" on the login page | [shell.md](shell.md) |
| `/media/<path>` | none | `dmac/media.py:serve_media` | file response | logged-in only; anonymous gets 302 to `/login/` | download links produced by exports | [search-and-downloads.md](search-and-downloads.md) |
| `/admin/` | Django admin | `django.contrib.admin` | Django admin templates | Django staff login | sidebar "Admin Panel" (superusers only) | [legacy.md](legacy.md) |

The login route is registered twice: `^login/?$` and `^accounts/login/` (the second sits after the Mezzanine include, so Mezzanine's own `/accounts/login/` answers first; see the Mezzanine table). The login view follows `next` only to a path on this site. The home page for logged-out visitors: SEC-0930-C.

### Search and downloads

| URL | URL name | View | Template (main embeds) | Who | Linked from | Covered in |
|---|---|---|---|---|---|---|
| `/seek/search/` | `searchAdvanced` | `seek/views/search.py:searchAdvanced` | `searchAdvanced.html` + `pages/searchAdvanced_search.embed.html`, `searchAdvanced_stable`, `searchAdvanced_deletion`, `samples_search`, `samples_stable`, `sampleSearch_core` | SEEK login (`next=/seek/search/`) | sidebar "Sample Search", home tiles and "View all", sample type detail | [search-and-downloads.md](search-and-downloads.md) |
| `/seek/sample/id=<id>/` | `sample` | `seek/views/samples.py:sample` | `samples.html` + `pages/samples.embed.html` (tree tab `pages/samples_tree_new.embed.html`); the SEEK page body is fetched through `seek/seekapi.py` (`getPageRequests`) | SEEK login (inline) plus project scope (404 outside it) | result rows on the search page | [upload-and-samples.md](upload-and-samples.md) |
| `/seek/sampletree/uid=<uid>/` | `sampleTree` | `samples.py:sampleTree` (calls `sample`) | same as above | same | sidebar "Search by UID" box (`themes/NextSeek/static/js/nextseek.js`), Nessie UID links in the chat bundle, phone search results | [upload-and-samples.md](upload-and-samples.md) |
| `/seek/sample_types/id=<id>/` | `sample_type` | `samples.py:sample_type` | `sampleQuery.html` + `pages/samples_table.embed.html` | SEEK login (inline) | none (the search embeds build this URL but never open it) | [search-and-downloads.md](search-and-downloads.md) |
| `/seek/sample_timeline/<anything>` | none | `TemplateView` in `seek/urls.py` | `sample_timeline.html` (Vite bundle under `static/js/sample_timeline/`) | see security item SEC-0930-G, tracked privately | timeline button in `pages/samples_stable.embed.html` | [upload-and-samples.md](upload-and-samples.md) |
| `/seek/samples/search/` | `sampleSearch` | `search.py:sampleSearch` | none: 302 to `/seek/search/` | SEEK login | old bookmarks | [legacy.md](legacy.md) |
| `/seek/samples/query/` | `sampleQuery` | `samples.py:sampleQuery` (calls `sample_type(0)`) | `sampleQuery.html` | SEEK login (inline) | none; used as a `next` target | [legacy.md](legacy.md) |
| `/seek/newsearch/` (no `$`) | `newSearch` | `search.py:newSearch` | `newSearch.html` + `pages/*_new*.embed.html` | SEEK login (bare) | none; a testing ground | [legacy.md](legacy.md) |

### Upload and templates

| URL | URL name | View | Template (main embeds) | Who | Linked from | Covered in |
|---|---|---|---|---|---|---|
| `/seek/samples/upload/` | `sampleUpload` | `seek/views/upload.py:batchUpload` | `batchUpload.html` + `pages/batch_upload.embed.html`; says "desktop-only" on small screens | SEEK login | sidebar "Assay Sheet Upload" and "+ New sample", home action card | [upload-and-samples.md](upload-and-samples.md) |
| `/seek/data/upload/` | `datafileUpload` | `upload.py:datafileUpload` | `dataFileUpload.html` | SEEK login | sidebar "Data & Protocol Upload" | [upload-and-samples.md](upload-and-samples.md) |
| `/seek/templates/` (no `$`, so `/seek/templates/anything` also matches) | `templatesList` | `seek/views/assets.py:templatesList` | `templatesList.html` | SEEK login (`next=/seek/templates`) | sidebar "Templates" | [upload-and-samples.md](upload-and-samples.md) |

`/seek/templates/download/` (`templatesDownload`) is the POST target of the templates page form, not a page of its own.

### Projects

| URL | URL name | View | Template | Who | Linked from | Covered in |
|---|---|---|---|---|---|---|
| `/seek/projects/` | `projects` | `seek/views/projects.py:projects` | `projectsList.html` | SEEK login | sidebar "Projects", home tile and "View all" | [projects-catalogs-graphs.md](projects-catalogs-graphs.md) |
| `/seek/projects/<id>/` | `project_page` | `projects.py:project_page` | `projectPage.html`; a non-member gets `error.html` with HTTP 200 | SEEK login (inline) plus member | project list cards, home project strip | [projects-catalogs-graphs.md](projects-catalogs-graphs.md) |
| `/seek/projects/<id>/samples/` | `project_samples` | `projects.py:project_samples` | `project_samples.html` (+ `catalog_styles.html`); opened as a modal by `data-modal-route` | SEEK login plus member | project page | [projects-catalogs-graphs.md](projects-catalogs-graphs.md) |
| `/seek/projects/<id>/connections/` | `project_connections` | `projects.py:project_connections` | none: returns a whole HTML document built in code; `@xframe_options_sameorigin` | SEEK login plus member | iframe and "full screen" link on the project page | [projects-catalogs-graphs.md](projects-catalogs-graphs.md) |

### Catalogs

| URL | URL name | View | Template | Who | Linked from | Covered in |
|---|---|---|---|---|---|---|
| `/seek/sampletypes/` | `sampleTypesList` | `seek/views/catalog.py:sampleTypesList` | `sampleTypesList.html` + `catalog_styles.html`, `includes/catalog_table.html`, `includes/catalog_table_filter.js` | SEEK login | sidebar "Sample Types", home action card, help icons on the search page | [projects-catalogs-graphs.md](projects-catalogs-graphs.md) |
| `/seek/sampletypes/<code>/` | `sampleTypeDetail` | `catalog.py:sampleTypeDetail` | `sampleTypeDetail.html` + `includes/attribute_definitions_table.html` | SEEK login | catalog rows, assay pages, project page | [projects-catalogs-graphs.md](projects-catalogs-graphs.md) |
| `/seek/assays/` | `assaysList` | `catalog.py:assaysList` | `assaysList.html` | SEEK login | sidebar "Assays", home action card | [projects-catalogs-graphs.md](projects-catalogs-graphs.md) |
| `/seek/assays/<slug>/` | `assayDetail` | `catalog.py:assayDetail` | `assayDetail.html` | SEEK login | assay list rows, sample type detail | [projects-catalogs-graphs.md](projects-catalogs-graphs.md) |

### Data files and SOPs

| URL | URL name | View | Template (main embeds) | Who | Linked from | Covered in |
|---|---|---|---|---|---|---|
| `/seek/datafile/query/` | `datafileQuery` | `seek/views/assets.py:datafileQuery` | `dataFilesPage.html` + `pages/datafile_table.embed.html` | SEEK login | sidebar "Data File Query", home tile, project page | [search-and-downloads.md](search-and-downloads.md) |
| `/seek/sop/query/` | `sopQuery` | `assets.py:sopQuery` | `sopsPage.html` + `pages/sops_table.embed.html` | SEEK login | sidebar "Protocol Query" | [search-and-downloads.md](search-and-downloads.md) |

### Admin pages

The sidebar Admin block is rendered only for superusers (`{% if request.user.is_superuser %}` in `nav.embed.html`). The Gate column says what each view is decorated with.

| URL | URL name | View | Template | Gate | Linked from | Covered in |
|---|---|---|---|---|---|---|
| `/seek/samples/attributes/` (no `$`) | `sampleAttributes` | `seek/views/samples.py:sampleAttributes` | `sampleAttributes.html` | security item SEC-0930-B, tracked privately | sidebar (superusers), search help icons | [upload-and-samples.md](upload-and-samples.md) |
| `/seek/admin/clades/` | `adminClades` | `seek/views/admin.py:adminClades` | `clades.html` | SEEK login plus `@requires_supervisor` | sidebar (superusers) | [projects-catalogs-graphs.md](projects-catalogs-graphs.md) |
| `/seek/admin/internal_assays/` | `internalAssays` | `admin.py:internalAssays` | `internal_assays.html` | SEEK login plus `@requires_supervisor` | sidebar (superusers) | [projects-catalogs-graphs.md](projects-catalogs-graphs.md) |
| `/seek/admin/retrieve/` (no `$`) | `adminRetrieveSamples` | `admin.py:adminRetrieveSamples` | `admin_retrieval.html` (GET) or an xlsx (POST) | security item SEC-0930-B, tracked privately | nothing links to it; the Sample Retrieval tab on `/seek/search/` (through `ns_sample_download.js`) replaced its form | [search-and-downloads.md](search-and-downloads.md) |
| `/admin/` | Django admin | `django.contrib.admin` | Django templates | Django staff | sidebar "Admin Panel" | [legacy.md](legacy.md) |

The two `admin/` pages with a decorator pass `next='/seek/samples/attributes/'`, so after login the user lands on Sample Attributes, not the page requested.

### Assistant

| URL | URL name | View | Template | Who | Linked from | Covered in |
|---|---|---|---|---|---|---|
| `/seek/assistant/` (no `$`; `/seek/assistant/<anything>` is used by the React router) | `assistant` | `seek/views/search.py:smartSearch` | `smartSearch.html`, which mounts the Vite bundle with `{% vite_assets "src/main.embedded.tsx" "js/chat_assistant" %}` | Django user (`request.user.is_authenticated`); an anonymous visitor is sent to sign in | `includes/nessie_button.html` (included by `nav.embed.html` and the home page) | [chat-frontend.md](chat-frontend.md) |

### Help and API docs

| URL | URL name | View | Template | Who | Linked from | Covered in |
|---|---|---|---|---|---|---|
| `/seek/help/` | `getting_started` | `seek/views/pages.py:getting_started` | `themes/NextSeek/templates/help/getting_started.html` | anonymous | sidebar Resources, "Getting Started" | [docs-and-help.md](docs-and-help.md) |
| `/nextseek_api/swagger/` | `swagger-ui` (namespace `nextseek_api`) | `SpectacularSwaggerView` in `nextseek_api/urls.py` | `themes/NextSeek/templates/nextseek/swagger_ui.html` | authenticated (`IsAuthenticated`; an anonymous browser gets an API auth error, not a login redirect) | nothing | [docs-and-help.md](docs-and-help.md) |
| `/nextseek_api/redoc/` | `redoc` | `SpectacularRedocView` | drf-spectacular's own | authenticated | nothing | [docs-and-help.md](docs-and-help.md) |
| `/nextseek_api/schema/` | `schema` | `SpectacularAPIView` | JSON or YAML | authenticated | used by the two pages above | [docs-and-help.md](docs-and-help.md) |

There is no separate Nessie README or chat page in `nextseek_api`; the chat page is `/seek/assistant/`.

### Mezzanine routes that are still live

Mezzanine 6.0.0 is installed (`mezzanine.blog`, `.forms`, `.galleries`, `.accounts`, `.pages`, `.generic` in `INSTALLED_APPS`) and its whole URL conf is mounted under the `^` include. None of these pages is linked from the theme and none is declared in `ci/routes.py`. They render inside `base.html` with stock Mezzanine templates. Mezzanine's own include of these paths: security item SEC-0930-H, tracked privately. Details in [legacy.md](legacy.md).

| URL | What answers | Notes |
|---|---|---|
| `/accounts/login/` | `mezzanine.accounts.views.login` (Mezzanine's own form, not the SEEK login) | shadows the second `login_seek` registration (UI-026) |
| `/accounts/logout/` | `mezzanine.accounts.views.logout` | the live "Sign out" link in `accounts/includes/user_panel.html` (`{% url 'logout' %}`) |
| `/accounts/update/`, `/accounts/verify/...`, `/accounts/password/...` | Mezzanine account views | "Update profile" in the user menu points at `/accounts/update/` |
| `/password_reset/...`, `/reset/...` | Django `auth_views` (present because `django.contrib.admin` is installed) | the login page links to SEEK's reset instead when `SEEK_PUBLIC_URL` is set |
| `/blog/...` | `mezzanine.blog.views` | empty blog in the app chrome |
| `/search/` | `mezzanine.core.views.search` | site search, not sample search; do not confuse with `/seek/search/` |
| `/edit/`, `/rating/`, `/comment/`, `/set_site/`, `/jsi18n/...`, `/sitemap.xml` | Mezzanine core and generic | mostly POST or XML |
| any other path ending in `/` | `mezzanine.pages.views.page` | renders a CMS Page row if one exists, else Mezzanine's 404 |

`handler404` and `handler500` in `dmac/urls.py` point at Mezzanine's views, so the 404 page is Mezzanine's `errors/404.html`. `seek/templates/pages/404.html` is never rendered.

### Linked-from summary

The link sources are: the sidebar (`themes/NextSeek/templates/nav.embed.html`, included from `base.html` inside `{% block left_panel %}`), the user panel (`accounts/includes/user_panel.html`, via `includes/user_panel.html`), the home page (`index.html`), the login page, and the footer. The footer (`page-footer.embed.html`) carries a logo and copyright text and no links. The sidebar sections are: Data (Home, Sample Search, Data Entry submenu, Data Query submenu, Projects, Useful Info submenu), Quick Access (Ask Nessie, UID box, "+ New sample"), Admin (superusers), Resources (Getting Started, Published Studies, Contact Support).

### Routes that render HTML but nothing links to

| URL | State |
|---|---|
| `/seek/newsearch/`, `/seek/samples/query/`, `/seek/samples/search/` | work; see the search table |
| `/seek/admin/retrieve/` | works; superseded by the Sample Retrieval tab on `/seek/search/` |
| `/seek/remote/` (`search.py:remote`) and `/seek/url/<name>/` (`samples.py:seek`) | broken: the views raise an error. Both are xfailed in `ci/routes.py`. Delete rather than repair |
| `/seek/sample/id=<id>/edit`, `/manage` | 302 to `SEEK_PUBLIC_URL/samples/<id>/edit` or `/manage` |

Templates with no view at all (`publish.html`, `publishAssets.html`, `batchSearch.html`, `sampleDeletion.html`, `sampleUpload.html`, `samplesTest.html`, `sampleSearch.html`, `pages/denied.html`) are listed in [legacy.md](legacy.md).

## How to add a page

The example is a new page for signed-in users under `/seek/`. Project-level pages (outside `/seek/`) need extra steps, noted at each step.

1. **URL.** Add a `re_path` to `seek/urls.py`. Anchor it with `^` and `$` (several existing patterns have no `$` and answer prefixes). Give it a `name`. For a project-level page instead, add the route to `dmac/urls.py` above the `re_path("^", include("mezzanine.urls"))` line; anything below it is never reached. Use `re_path`, not `path()` with a converter: the CI gate refuses converters.
2. **View.** Add a function to the matching module in `seek/views/` (`pages.py` for static pages, `catalog.py`, `projects.py`, `assets.py` and so on) and re-export it in `seek/views/__init__.py` (both the import line and `__all__`). `seek/urls.py` refers to it as `views.<name>`, so a missing re-export fails at import. Decorate with `@requires_seek_login_redirect('<your own URL>')` from `seek/decorators.py` for a signed-in page, and add `@requires_supervisor('message')` for an admin page. Pass your own URL as `next`; do not copy another view's literal.
3. **Template.** Put the file in `seek/templates/` (or `themes/NextSeek/templates/` for shell-level and help pages). Start with `{% extends "base.html" %}`, set `{% block title %}`, and put all content inside `{% block main %}`; extra stylesheet or head tags go in `{% block extra_head %}`, scripts in `{% block extra_js %}`. Django drops anything outside a block in a child template. Use the existing design tokens and classes (see [styles.md](styles.md)) and Bootstrap 5 from the base template; do not load a second copy of a library.
4. **Navigation.** To add a sidebar link, edit `themes/NextSeek/templates/nav.embed.html` (inside the right `sidebar-section`; a submenu entry is an `<li>` in the `collapse submenu` list). For a home action card or tile edit `index.html`. Superuser-only links go inside the existing `{% if request.user.is_superuser %}` block, never `is_staff`; `seek/tests/test_admin_template_gating.py` enforces that.
5. **CI route registration.** An unlisted route fails the gate (`ci/gate/test_route_registry.py`), which diffs the resolver's pattern strings against the registry in both directions.
   - Add a `Route(...)` to `REGISTRY` in `ci/routes.py`. `pattern` must be the resolver string verbatim, including the include prefix (for example `^seek/^help/$`). Set `effect="reads"` for a page, `methods=("GET",)`, `profiles="local,dev,prod"`, `auth="web"` for a signed-in page (`"anon"` for a public one), and `expect=200` (`302` when the probe is redirected). A `{placeholder}` in `path` must be declared in `PLACEHOLDERS`; prefer a fixed path that exists on every box.
   - Bump `OWNED_ROUTE_COUNT` in `ci/smoke/test_registry_contents.py` by the number of routes you added (it was 176 at this commit).
   - Project-level route only: also add the resolver pattern string to `_PROJECT_LEVEL` in `ci/gate/live_routes.py`. Without it the gate does not see the route, stays green, and the route is never probed. `ci/README.md` states the count of project-level patterns; update it.
   - A Mezzanine CMS Page created in the admin needs no CI change and is not probed.
   - `ci/routes.py` may import only the standard library.
6. **Render test.** Add a test in `seek/tests/` that renders the view or template without a browser, in the style of `test_templates_page.py` or `test_navbar.py`: build a request with `RequestFactory`, patch the SEEK login (`@patch` on the `SeekDB` used by `seek/decorators.py`), call the view, and assert on the response status, a redirect for the anonymous case, and a marker string in the HTML. Test settings use plain static storage, so `{% static %}` works without a manifest. Run it in a throwaway container (recipe in `themes/CLAUDE.md`), not on the host.
7. **Docs.** Add the page to the right table above, and if you added or moved Markdown files, run `python3 ci/docs_map.py` from the repo root.

## External links the UI carries

| Link | Where it appears | Notes |
|---|---|---|
| `https://koch-institute-mit.gitbook.io/mit-data-management-analysis-core/` (GitBook) | `nav.embed.html` (Useful Info > Documentation), `help/getting_started.html` (near the end); also in the unused `content.embed.html` | opens in a new tab; the only docs link |
| `https://fairdomhub.org/programmes/206` | `nav.embed.html` (Resources > Published Studies) | new tab |
| `mailto:` the team's support address | `nav.embed.html` (Resources > Contact Support), `help/getting_started.html` | team address |
| `SEEK_PUBLIC_URL` + `/signup` | `dmac/views.py:signup_seek` (302), reached from the "Sign up" link in `login.html` | falls back to `SEEK_URL`, the internal docker host, when `SEEK_PUBLIC_URL` is empty; a browser cannot resolve that |
| `SEEK_PUBLIC_URL` + `/forgot_password` | `login.html` ("Reset it"), value from `dmac/context_processors.py` (`seek_forgot_password_url`) | falls back to Mezzanine's own reset URL (`mezzanine_password_reset`) when `SEEK_PUBLIC_URL` is unset |
| `ctx.nih_reporter_link`, `ctx.fairdomhub_published_link` | `projectPage.html` (project header) | data from the project context rows, not hard-coded |
| `SEEK_PUBLIC_URL/assets/avatar-images/<id>-500.png` | `dmac/views.py` (project logos on the home page) | |
| `SEEK_PUBLIC_URL/samples/<id>/edit` and `/manage` | `seek/views/samples.py:editSample`, `manageSample` (302 targets) | |
| Bootstrap 5.3.3 and Bootstrap Icons 1.11.3 (jsdelivr), Google Fonts (Inter; Playfair Display and Source Sans 3 on the login page) | `base.html`, `base_auth.html` | the whole UI needs these CDNs |
| d3 and lodash (cdnjs) | `pages/samples_tree.embed.html` | sample tree |
| 113 SEEK script tags pointing at the production SEEK host | `seek/templates/pages/seek_includes.html` | never rendered: every include of it sits between `{% extends %}` and the first block, so Django drops it (UI-203). Delete it rather than fix it |

## Where to edit

| Task | Files and symbols |
|---|---|
| Add a page | steps above: `seek/urls.py`, `seek/views/<area>.py` + `seek/views/__init__.py`, template, `nav.embed.html`, `ci/routes.py`, `ci/smoke/test_registry_contents.py` (`OWNED_ROUTE_COUNT`), `seek/tests/` |
| Change sidebar, home or footer links | `themes/NextSeek/templates/nav.embed.html`, `index.html`, `page-footer.embed.html` |
| Change the sign in, sign out or user menu | `themes/NextSeek/templates/accounts/includes/user_panel.html` (markup), `themes/NextSeek/static/js/nextseek.js` (`toggleUserMenu`), `nextseek.css` (`.user-panel`, `.btn-signin`) |
| Change the login page | `themes/NextSeek/templates/login.html` (inline styles), `dmac/views.py:login_seek` (logic), `base_auth.html` (shell) |
| Change where login sends people back | the `next` literal on each decorated view, and `requires_seek_login_redirect` in `seek/decorators.py` |
| Reorder or add root routes | `dmac/urls.py` (the Mezzanine include swallows anything after it) |
| Turn Mezzanine features off | `INSTALLED_APPS` and `ACCOUNTS_*` in `dmac/settings.py` |
| Fix 404 and 500 pages | `handler404` and `handler500` in `dmac/urls.py`; add themed 404 and 500 templates in an errors folder under the theme templates; `seek/templates/error.html` is the access-error page |
| Change the API docs pages | `nextseek_api/urls.py` (swagger, redoc, schema), `themes/NextSeek/templates/nextseek/swagger_ui.html` |
| Update the route registry | `ci/routes.py` (`REGISTRY`), `ci/gate/live_routes.py` (`_PROJECT_LEVEL`) |

## Gotchas

- A route placed after the Mezzanine include in `dmac/urls.py` never matches. The signup route is deliberately placed before it; the second login registration is not, which is why it is dead.
- Many `seek/urls.py` patterns have no trailing `$` (`^templates/`, `^newsearch/`, `^search/`, `^searchUIDs/`, `^samples/upload/` and others; two `nhp` patterns also have no `^`), so they answer any longer path. `^assistant/` is open on purpose, for the chat's deep links. `^templates/download/$` works only because it is listed before `^templates/`; keep that order when editing.
- `/seek/search/` is sample search. `/search/` is Mezzanine's site search. `/seek/sampletypes/` is the catalog (describes types); `/seek/sample_types/id=<id>/` lists samples of a type. The names differ by one underscore on purpose.
- `seek/urls.py` has 65 routes. `seek/README.md` and the retired UI snapshot (`docs/archive/2026-09/2026-09-03-ui-snapshot.md`) say 62.
- `error.html` is returned with HTTP 200 for "not in this project", so monitors and `ci/routes.py` cannot tell it from a page.
- A theme template edit shows on the next request; a `seek/templates/` edit needs `./startup.sh rebuild`. On a worktree, the compose bind mount serves the compose directory's theme, not the worktree's.
- The user panel is included from `base.html` through `includes/user_panel.html`, which includes `accounts/includes/user_panel.html`. The "Profile" menu item there never renders because the profile URLs are disabled in Mezzanine.
- Adding a route to `dmac/urls.py` without `_PROJECT_LEVEL` leaves CI green and the route unowned.

## Known issues

See [known-issues.md](known-issues.md#pages). The ones that matter most for this page:

- `/accounts/login/` is answered by Mezzanine, not by the SEEK login view.
- Unlinked Mezzanine pages (blog, site search, account forms) are live in the app chrome (security item SEC-0930-H), and two admin pages are security item SEC-0930-B (both tracked privately).
