# JavaScript outside the chat app

## What this covers

All browser JavaScript on the Django-rendered pages, except the React chat panel. That is: the
libraries every page loads (jQuery, EasyUI, Bootstrap), the page-specific libraries (Cytoscape and
dagre on the project "Sample flow" diagram, d3 and d3-dag on the sample tree), the one theme-wide file
`themes/NextSeek/static/js/nextseek.js`, the shared files under `static/js/`, the inline `<script>`
blocks inside templates, the endpoints those scripts call, and how they handle CSRF and errors.

It does not cover the React chat app (see [chat-frontend.md](chat-frontend.md)), CSS (see
[styles.md](styles.md)), the sidebar markup (see [shell.md](shell.md)), or what each page does for
the user (see [pages.md](pages.md), [upload-and-samples.md](upload-and-samples.md),
[search-and-downloads.md](search-and-downloads.md), [projects-catalogs-graphs.md](projects-catalogs-graphs.md)).
The sample timeline page is a built Vite bundle owned by another area; only how it is loaded is noted here.

## How it works

There is no JavaScript build step for the Django pages. Everything is hand-written: a few static files
plus a large amount of inline `<script>` in templates. A page gets its scripts from four places:

```
themes/NextSeek/templates/base.html          every theme page
  head:  jQuery 1.11.3, EasyUI 1.5.2          vendored, blocking
  body end: Bootstrap 5.3.3 bundle (CDN), js/nextseek.js
  {% block extra_head %} / {% block extra_js %}   page-specific <script src> tags
seek/templates/<page>.html                    inline <script> in {% block main %}
seek/templates/pages/*.embed.html             partials {% include %}d into the page, each with its own inline script
static/js/...                                 shared files, loaded by a <script src> in a page or partial
```

`themes/NextSeek/templates/base_auth.html` (the login page) is separate: it loads only the Bootstrap
bundle. No jQuery, no EasyUI, no `nextseek.js`.

Partials share one global scope with their host page, so a partial calls helpers that the host page
defined (for example `searchAdvanced.html` defines `nsCsrfToken` and `nsSearchFetch`, and the
`*_stable` partials call them). Include order matters.

Static files are served from two roots (`STATICFILES_DIRS` in `dmac/settings.py`):
`/app/themes/NextSeek/static` first, then `/app/static`. So `{{STATIC_URL}}js/easyui/...` and
`js/nextseek.js` resolve to the theme folder, and `js/custom/...`, `js/ns_sample_download.js`,
`js/dag/...` resolve to the top-level `static/`. `STATIC_URL` inside templates comes from the `static`
context processor; `{% static %}` also works.

## Libraries and where they come from

| Library | Version | Source | Loaded by |
|---|---|---|---|
| jQuery | 1.11.3 | vendored: `themes/NextSeek/static/jquery-easyui-1.5.2/jquery.min.js` | `base.html`, every theme page. The only jQuery the app uses |
| jQuery EasyUI | 1.5.2 (full bundle) | vendored: `themes/NextSeek/static/jquery-easyui-1.5.2/jquery.easyui.min.js` (CSS `themes/NextSeek/static/jquery-easyui-1.5.2/themes/default/easyui.css` and `themes/NextSeek/static/jquery-easyui-1.5.2/themes/icon.css`) | `base.html`, after jQuery |
| Bootstrap JS (bundle with Popper) | 5.3.3 | CDN: `https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js` | `base.html` and `base_auth.html` |
| Bootstrap CSS, Bootstrap Icons | 5.3.3, 1.11.3 | CDN jsDelivr | both bases (styles, listed for completeness) |
| EasyUI plugins | bundled with EasyUI downloads | vendored: `themes/NextSeek/static/js/easyui/` (`datagrid-filter.js`, `datagrid-export.js`, `datagrid-detailview.js`) | per page, see below |
| Cytoscape, dagre, cytoscape-dagre | `@3`, `@0.8`, `@2` (major versions only) | CDN unpkg, written by `rows_to_html` in `nextseek_api/services/sampletype_connections.py` (constant `_CYTO_CDN`) | the project "Sample flow" iframe page only |
| d3, d3-dag | 7.8.4, 1.1.0 | CDN skypack, ES module imports at the top of `static/js/dag/dag.js` | sample tree v2 (`pages/samples_tree_new.embed.html`) |
| d3, lodash | 3.5.5, 3.3.1 | CDN cdnjs, top of `seek/templates/pages/samples_tree.embed.html` | sample tree v1, commented out (see gotchas) |
| d3 | 7.9.0 | CDN jsDelivr `+esm`, top of `static/js/dag/d3neo4j.js` | nothing references this file |
| Mezzanine jQuery, Bootstrap 2/3 JS, html5shiv, respond | jQuery 1.8.3 by default | vendored: `static/mezzanine/js/`, `static/js/bootstrap.js` and siblings | nothing on the site: the stock `templates/base.html` that names them is not on the template path, and `base.html` resolves to the theme's copy |
| Google Fonts | Inter (theme), Playfair Display and Source Sans 3 (login) | CDN | CSS only |

Third-party script loading is security item SEC-0930-F (tracked privately). Bootstrap (jsDelivr),
Bootstrap Icons (jsDelivr) and Google Fonts load on every page: if jsDelivr is unreachable, styling and
the sidebar collapse break. SheetJS, select2, moment, DataTables and vis are not used by any Django page (`xlsx`
appears only inside the React bundle).

A jQuery 1.6.2 copy exists at `static/js/buildtree/jquery-1.6.2.min.js` and is loaded by nothing.

### EasyUI components in use

| Component | Used on |
|---|---|
| datagrid | search pages (`searchAdvanced.html`, `newSearch.html`), `sopsPage.html`, `dataFilesPage.html`, `sampleQuery.html`, `clades.html`, `internal_assays.html` |
| tabs, layout | search pages, `batchUpload.html`, the table pages, admin pages, `pages/samples.embed.html` (tree tabs) |
| combobox | `pages/batch_upload.embed.html`, `dataFileUpload.html`, the `*_search` and `*_newsearch` partials |
| messager (`alert`, `confirm`, `progress`, `show`) | search, upload, vocab workbench, `ns_sample_download.js` |
| linkbutton, textbox, pagination | most EasyUI pages (toolbars, pagers) |

Pages with no EasyUI at all: `sampleAttributes.html`, `templatesList.html`, `projectsList.html`,
`projectPage.html`, `assaysList.html`, `sampleTypesList.html`, `sampleTypeDetail.html`, `index.html`,
`login.html`.

EasyUI plugin loading is per page. `base.html` has a `{% comment %}` explaining why
`datagrid-filter.js` is not loaded globally: it extends `$.fn.datagrid.methods` on its first statement,
so loading it before `jquery.easyui.min.js` threw on every page, and loading it again on pages that
already load it would double-wrap `loadData`. Pages that use the filter (for example `clades.html`,
`internal_assays.html`, `searchAdvanced.html`, and the `*_table` partials) load it themselves, after
EasyUI. `/seek/search/` breaks that rule today: `searchAdvanced.html` and the included
`pages/samples_stable.embed.html` both load `datagrid-filter.js` (plus `datagrid-export.js` and
`datagrid-custom.js`), the double load the `base.html` comment warns about (UI-072).
`datagrid-detailview.js` is loaded by `clades.html` and `internal_assays.html`.
`datagrid-cellediting.js` and `datagrid-groupview.js` exist but no template loads them.

## Own JavaScript files

| File | Loaded by | What it does |
|---|---|---|
| `themes/NextSeek/static/js/nextseek.js` (255 lines) | `base.html` (not `base_auth.html`) | Theme-wide behavior, see next section |
| `static/js/ns_sample_download.js` | `searchAdvanced.html`, `newSearch.html`, `pages/samples.embed.html` | The one download client. Exposes `window.nsDownloadSamples`, `nsCollectSelectedUids`, `nsExtractUid`. POSTs to `/nextseek_api/samples/retrieve/` with `X-CSRFToken` read from the cookie, and has a `.catch` that alerts |
| `static/js/custom/datagrid-custom.js` (530 lines) | about a dozen templates (the "EUI trio": this file plus `datagrid-filter.js` and `datagrid-export.js`) | Legacy EasyUI grid helpers inherited from another app (download, upload, save, delete). All top-level `function`s, all global. The newer `ns*` helpers at the end (`nsEscapeHtml`, `nsEllipsisFormatter`, `nsEnableColumnFilters`, `nsResetSearch`) are the ones to reuse |
| `static/js/custom/ns-vocab-workbench.js` (1156 lines) | `clades.html`, `internal_assays.html` | Curator workbench for EasyUI datagrids: `nsVocabWorkbench(config)`, `nsPostJson`, `nsAcceptPost`, `nsRemovePost`, `NS_WB_*`. jQuery `$.ajax` with the CSRF token passed in as config |
| `static/js/dag/dag.js` (184 lines) | `pages/samples_tree_new.embed.html`, as `type="module"` | Sample tree v2: imports d3 and d3-dag from skypack, then does a top-level `await d3.json("/nextseek_api/sample-tree/<uid>/tree")`. Reads the UID from `location.href` with a regex |
| `themes/NextSeek/templates/includes/catalog_table_filter.js` | `{% include %}`d inside a `<script>` in `assaysList.html` and `sampleTypesList.html` | Not a static file: it is a template include, so editing it needs no `collectstatic` |
| `static/js/sample_timeline/` | `seek/templates/sample_timeline.html` (`extra_head`) | Built Vite bundle (React, MUI, axios), file names hard-coded in the template. A rebuild changes the hashed names |
| `static/js/chat_assistant/assets/` | `seek/templates/smartSearch.html` via the `{% vite_assets %}` tag (`seek/templatetags/vite_assets.py`) | The React chat panel. See [chat-frontend.md](chat-frontend.md) |

Dead or vendored, safe to ignore: `static/js/dag/d3neo4j.js`, `static/js/buildtree/` (old tree builder,
d3 v3 copies), and `static/mezzanine/`, `static/admin/`, `static/filebrowser/`, tinymce.

### What nextseek.js does

Loaded at the end of `<body>` in `base.html`, so the DOM is already parsed. Entry points are a
`DOMContentLoaded` handler, document-level `click` and `keydown` listeners registered at load, and
global functions called from inline `onclick` in templates.

| Feature | Function or selector | Notes |
|---|---|---|
| Sidebar drawer | `initSidebar`, `openSidebar`, `closeSidebar`, ESC and Tab handlers | Called from the `.mobile-toggle` and scrim `onclick`s in `base.html`. Closes on any click outside the sidebar, traps focus while open |
| Submenus | handler on `.sidebar-nav [data-bs-toggle="collapse"]` | Also calls `Collapse.toggle()` on links that already carry `data-bs-toggle`; redundant but harmless |
| Active link highlight | `initActiveNavLink` | Matches `location.pathname` against `.sidebar-nav .nav-link` and opens the parent submenu |
| UID quick search | `navUID()`, Enter key on the UID input | Sends the browser to `/seek/sampletree/uid=<uid>/` |
| User menu | `toggleUserMenu(btn)` | Called from `accounts/includes/user_panel.html` |
| Modal over a route | links with `data-modal-route` (optionally `data-modal-iframe`) | Builds a full-screen overlay; fetches the route and injects its `#content` (or `main`, or body) with `innerHTML`, or shows an iframe for full-document routes such as the project connections diagram. Used by `projectPage.html` |
| About toggle | `[data-about-toggle]` | Collapses the "About this project" block |

`themes/README.md` describes this file as shorter than it is; trust the file.

## Inline template JavaScript

Roughly 40 templates carry inline `<script>` blocks, about 4,900 lines in total, of which about 3,300
are on routed pages. Counts below are approximate inline lines (they drift as the code changes).

| Template | Lines | Status |
|---|---|---|
| `seek/templates/searchAdvanced.html` | about 400, plus about 830 from its embeds | Live: `/seek/search/`. Defines the search globals and mobile `runMobileSearch()` |
| `pages/sampleSearch_core.embed.html` | about 320 | Live. `SampleSearchCore` request builder, has a Node test (`seek/tests/test_sample_search_js.py`) |
| `pages/samples_search`, `searchAdvanced_search`, `samples_stable`, `searchAdvanced_stable`, `searchAdvanced_deletion` | 60 to 260 each | Live (included by `searchAdvanced.html`) |
| `seek/templates/newSearch.html` and `pages/*newsearch*`, `samples_new_stable` | about 175 plus about 180 | Live: `/seek/newsearch/` |
| `pages/batch_upload.embed.html` (inside `batchUpload.html`) | about 275 | Live: "+ New sample", `/seek/samples/upload/` |
| `dataFileUpload.html` | about 150 | Live |
| `sampleAttributes.html` | about 1,020 (vanilla JS, no EasyUI) | Live, admin page |
| `templatesList.html` | about 440 (vanilla JS, uses `json_script`) | Live |
| `pages/sops_table`, `datafile_table` (`.embed.html`) | about 125 each | Live through `sopsPage.html` and `dataFilesPage.html` |
| `clades.html`, `internal_assays.html` | about 65 to 70 each, plus `ns-vocab-workbench.js` | Live, admin pages |
| `pages/samples_table.embed.html`, `samples_tree_new.embed.html`, `samples.embed.html` | 14 to 20 each | Live (`sampleQuery.html`, sample detail page) |
| `samples_tree.embed.html` (v1 tree) | about 210 | Included but commented out, never runs |
| `themes/NextSeek/templates/nextseek/swagger_ui.html`, `includes/attribute_definitions_table.html` | 46, 43 | Live |

## Copy-paste families and global-name collisions

| Family | Members | What repeats |
|---|---|---|
| Downloadable table page | `pages/sops_table`, `datafile_table` (`.embed.html`) | Mostly identical. Each has its own `getCookie`, a download `fetch` and a list `fetch`, and the same `console.log("Error")` |
| Sample search generations | `samples_search`, `searchAdvanced_search`, `samples_newsearch`, `searchAdvanced_newsearch` and the matching `*_stable` | Three generations of the same UI (old, advanced, new) with partial overlap |
| Admin vocab pages | `clades.html`, `internal_assays.html` | Same `nsVocabWorkbench` config and the same un-checked sync `fetch` |
| `getCookie` | `newSearch.html`, `dataFileUpload.html`, `batch_upload.embed.html`, `datafile_table`, `sops_table` | The Django docs snippet pasted verbatim five times |
| Delete samples | `samples_stable`, `searchAdvanced_stable`, `searchAdvanced_deletion`, `newSearch.html` | Four implementations that POST to `/seek/samples/delete/` |
| Full screen | `samples_tree_new.embed.html` and the overlay in `nextseek.js` | Two unrelated implementations |

Global-name collisions to watch when adding a function to any page:

- `datagrid-custom.js` defines plain names such as `accept`, `append`, `reject`, `upload`, `getChanges`,
  `endEditing`, and leaks an undeclared `jsonlist` global from `jsonconvertstrings`. A page-level function
  with one of these names silently replaces or is replaced by it, depending on load order.
- Page templates each define globals such as `getCookie`, `lab_options`, `type_options`, `template_options`,
  `uploadSamples`, `validateSamples`. Two included partials that define the same name overwrite each other
  (the search page has a test for this; see [search-and-downloads.md](search-and-downloads.md) "Gotchas").
- Hidden coupling: `samples_stable.embed.html` and `searchAdvanced_stable.embed.html` call
  `nsSearchState`, `nsSearchFetch`, `nsSearchRun`, `nsCsrfToken`, `nsResizeGridsIn` and `NS_*` constants
  defined in `searchAdvanced.html`. They only work when included from it.
- Prefix new globals with `ns` and put them in a static file rather than a template.

## Endpoints called from the browser

Every `fetch`, `$.ajax`, `$.get`, `$.post`, datagrid `url` and form action in templates and static JS.
"No route" means no entry in `seek/urls.py`, `nextseek_api/urls.py` or `dmac/urls.py`.

### /nextseek_api/ (REST)

| URL | Method | Called from |
|---|---|---|
| `/nextseek_api/samples/retrieve/` | POST | `static/js/ns_sample_download.js` |
| `/nextseek_api/samples/graph_search/` | POST | `pages/sampleSearch_core.embed.html` (`ENDPOINT`), used by `searchAdvanced.html` |
| `/nextseek_api/samples/advanced_search/` | POST | `pages/samples_newsearch.embed.html`, `pages/searchAdvanced_newsearch.embed.html` |
| `/nextseek_api/sample_types/` | GET | `newSearch.html` |
| `/nextseek_api/projects/` | GET | `pages/batch_upload.embed.html`, `dataFileUpload.html` |
| `/nextseek_api/batch-upload/validate/`, `start/` | POST (multipart) | `pages/batch_upload.embed.html` |
| `/nextseek_api/batch-upload/status/<job>` | GET, polled every second | `pages/batch_upload.embed.html` |
| `/nextseek_api/batch-upload/summary/<job>` | GET via a synthetic download link | `pages/batch_upload.embed.html` |
| `/nextseek_api/${upload_type}/` | POST | `dataFileUpload.html` |
| `/nextseek_api/data_files/`, `data_files/download/` | GET, POST | `pages/datafile_table.embed.html` |
| `/nextseek_api/sops/`, `sops/download/` | GET, POST | `pages/sops_table.embed.html` |
| `/nextseek_api/attributes/`, `attributes/search/`, `attributes/batch-create/`, `batch-patch/`, `batch-delete/` | GET, POST, PATCH | `sampleAttributes.html`; `includes/attribute_definitions_table.html` (search) |
| `{% url "nextseek_api:assistant-me" %}` | GET | `nextseek/swagger_ui.html` |
| `/nextseek_api/sample-tree/<uid>/tree` | GET | `static/js/dag/dag.js` |

### /seek/ (Django views)

| URL | Method | Called from |
|---|---|---|
| `/seek/samples/delete/` | POST | `newSearch.html`, `pages/samples_stable`, `searchAdvanced_stable`, `searchAdvanced_deletion` |
| `/seek/attributes/id=<id>`, `/seek/operators/`, `/seek/sample_types/id=<id>/` | GET | the `*_search` and `*_newsearch` partials |
| `/seek/retrieve/samples/` | datagrid url | `pages/samples_table.embed.html` |
| `/seek/samples/export/` | POST | `pages/samples_stable.embed.html` |
| `syncSampleTypes/`, `syncInternalAssays` (relative URLs) | POST | `clades.html`, `internal_assays.html` |
| `/seek/clade/save/`, `clade/delete/`, `clade/sampleTypes/save/`, `internal_assays/*` save and delete | POST JSON | `clades.html`, `internal_assays.html` via `nsAcceptPost` and `nsRemovePost` |
| `/seek/templates/download/` | POST form | `projectPage.html`, `templatesList.html` |
| `/seek/projects/<id>/samples/` | GET (modal route) | `nextseek.js` via `data-modal-route` |
| `/seek/projects/<id>/connections/` | iframe | `projectPage.html` ("Sample flow") |
| `/seek/sample_timeline/`, `/seek/sample/id=<id>/`, `/seek/sampletree/uid=<uid>/` | `window.open` or `location.href` | `pages/samples_stable.embed.html`, `dag.js`, `nextseek.js` (`navUID`) |

### Called but no route exists

None left: the only callers were the unrouted templates, now deleted.

No Django page opens an SSE or EventSource connection; the chat panel streams on its own.

## CSRF patterns

Five patterns are in use, none shared:

| Pattern | Where | Notes |
|---|---|---|
| 1. `getCookie("csrftoken")` into an `X-CSRFToken` header | `newSearch.html`, `batch_upload.embed.html`, `dataFileUpload.html`, two `*_table` partials | Works because the CSRF cookie is not HttpOnly. Five copies of the helper |
| 2. Inline regex on `document.cookie` | `nsCsrfToken()` in `searchAdvanced.html`, `getCsrfToken()` in `ns_sample_download.js` | Same idea, two more copies |
| 3. Token baked into the page by the template: `'{{ csrf_token }}'` in a `$.post` body or header | `samples_stable`, `searchAdvanced_stable`, `searchAdvanced_deletion`, `clades.html`, `internal_assays.html` | Only works inside a template, never in a static file |
| 4. Hidden `{% csrf_token %}` input in a form | `projectPage.html`, `templatesList.html` | Needs a real `<form>` |
| 5. None | `batch_upload.embed.html` validate and start, `sampleAttributes.html`, `attribute_definitions_table.html` | These rely on per-view authentication settings in `nextseek_api` (see `nextseek_api/authentication.py`). The project default (`REST_FRAMEWORK` in `dmac/settings.py`) enforces CSRF, so new code must always send the token |

A bug follows from pattern 3: `static/js/custom/datagrid-custom.js` (function `upload`) does
`formdata.append('csrfmiddlewaretoken', '{{ csrf_token }}')`. Static files are not run through the
template engine, so the server receives the literal text. Only unrouted dmac pages call `upload()` today.

Standardise on pattern 2's shape: read `csrftoken` from the cookie in one shared function in a static
file, and send it as `X-CSRFToken` with `credentials: 'same-origin'`. `ns_sample_download.js` already
does this with a good error path and is the model. Use it for every new call: Django views enforce
CSRF, and so does any `/nextseek_api/` view that has not opted in to the exempt class.

## Error-handling patterns

Mostly silent failures or `alert()`. Roughly 120 `alert()` and `confirm()` calls exist, mixed with
EasyUI `$.messager.alert` dialogs, sometimes in the same file.

Good models to copy: `ns_sample_download.js` (`.catch` then an alert), `batch_upload.embed.html`
validate and start (status text into the log box and `$.messager.progress("close")`), `searchAdvanced.html`
(`errorText`), `sampleAttributes.html` (`showStatus`), `ns-vocab-workbench.js` (`.fail` shows a messager).

Weak spots:

- `clades.html` and `internal_assays.html`: the sync `fetch` has no `.catch` and never checks `response.ok`.
- The `*_table` partials: failures end in `console.log("Error")` and nothing on screen.
- `batch_upload.embed.html`: a failed `/nextseek_api/projects/` call leaves the Project dropdowns empty; an
  upload error only reaches `console.error`.
- `dag.js`: no error handling, so if skypack or the API fails the tree shows "Loading..." forever.
- `nextseek.js` modal route: a 404 or 500 page is still parsed and injected because `r.ok` is not checked.
- Debug leftovers: `alert("downloadTable")` and `alert(newrows.length)` in `datagrid-custom.js`, and
  `console.log` calls such as "Sample Type Clicked!" in `samples_search.embed.html`.
- Server data injected as raw JS with `|safe` (`report.lab_options` and `report.all_lab_users` in
  `batch_upload.embed.html`).
  Use `{{ x|json_script:"id" }}`; `templatesList.html` already does.

## Where to edit

| Task | Edit | Easy to miss |
|---|---|---|
| Sidebar open/close, submenus, active link, UID search, user menu, modal overlay, about toggle | `themes/NextSeek/static/js/nextseek.js` | Theme static file: bind-mounted, so on a box a `git pull` plus an app restart (collectstatic runs at start) is enough. Login page does not load it |
| Change a library version, or add a script to every page | `themes/NextSeek/templates/base.html` (jQuery and EasyUI in `<head>`, Bootstrap and `nextseek.js` before `</body>`) | EasyUI must come after jQuery, plugins after EasyUI. Change `base_auth.html` separately for login |
| Add a script to one page | the page's `{% block extra_head %}` or `{% block extra_js %}`, or a `<script>` inside `{% block main %}` | Anything outside a block is dropped (see gotchas) |
| Load an EasyUI plugin on a page | a `<script src="{{STATIC_URL}}js/easyui/datagrid-filter.js">` in that page, after EasyUI | Do not add it to `base.html` |
| Download button behavior | `static/js/ns_sample_download.js` | Loaded by three templates; check all three |
| Sample search logic | `searchAdvanced.html` (globals, mobile), `pages/sampleSearch_core.embed.html` (request building), then the `*_search` and `*_stable` partials | Run `seek/tests/test_sample_search_js.py` after touching `sampleSearch_core` |
| "+ New sample" page | `seek/templates/batchUpload.html`, `seek/templates/pages/batch_upload.embed.html` (view `batchUpload` in `seek/views/upload.py`) | Fixed-size EasyUI tabs; the page is desktop-only |
| Admin vocab pages | `seek/templates/clades.html`, `internal_assays.html`, `static/js/custom/ns-vocab-workbench.js` | Pass the CSRF token in as config |
| Project "Sample flow" diagram | `rows_to_html` in `nextseek_api/services/sampletype_connections.py` (route `project_connections` in `seek/urls.py`) | It is a Python string containing a whole HTML page; edit it like a template but it is not one |
| Sample tree | `static/js/dag/dag.js`, `seek/templates/pages/samples_tree_new.embed.html`, wrapper `pages/samples.embed.html` | `dag.js` is a module and needs network access to skypack |
| Catalog table filter | `themes/NextSeek/templates/includes/catalog_table_filter.js` | It is an include, not a static file |
| Chat panel or timeline bundle loading | `seek/templates/smartSearch.html`, `sample_timeline.html` | Bundles are built elsewhere (see [chat-frontend.md](chat-frontend.md)) |

After any edit: `seek/templates/` and the repo-root `static/` are baked into the image and need
`./startup.sh rebuild`. `themes/NextSeek/` is bind-mounted, so its templates change on the next request
and its static files after an app restart (collectstatic runs at start); see
[ci-and-deploy.md](ci-and-deploy.md).

## Gotchas

- A Django child template's content outside any `{% block %}` is dropped. When a template does
  `{% extends "base.html" %}`, only the contents of blocks it overrides are rendered; everything else
  at the top level, including scripts and includes, is silently discarded with no error. The example in
  this repo (now fixed): six templates had `{% include "pages/seek_includes.html" %}`
  directly after `{% extends %}`, before `{% block main %}`; the include lines and the file were deleted rather than moved into a block. Put any
  real script inside a block.
- An `{% include %}` inside an HTML comment still runs. `pages/samples.embed.html` includes
  `samples_tree.embed.html` twice, each inside `<!-- -->`; Django renders the partial both times, so
  the old d3 3.5.5 tree (about 210 lines plus two CDN tags) is sent twice on every sample page, but
  the browser treats it as a comment and nothing executes.
- `<script defer>` on an inline block has no effect; the block runs at parse time
  (`batch_upload.embed.html`). Initialise in `$(function () { ... })` instead.
- Loading order is the contract. EasyUI before its plugins; `searchAdvanced.html` globals before the
  partials that call them; `base.html` jQuery before any page script that uses `$`.
- The modal-route overlay sets `innerHTML`, so scripts inside the fetched page never run. A target page
  that needs JS must be shown as an iframe (`data-modal-iframe`).
- Repo-root `static/` edits do not show until the image is rebuilt (the new container collects static
  at start); pages load most of these files by an unhashed `{{STATIC_URL}}` path, so browsers may also
  serve a cached copy for up to 30 days. Hard-reload first.
- Hover-only behavior (sample tree tooltips in `dag.js`, EasyUI filter icons) has no touch equivalent.
  The only JS-level mobile adaptation is `runMobileSearch()` in `searchAdvanced.html`.

## Known issues

See [known-issues.md](known-issues.md#javascript). The ones that matter most:

- Third-party script loading (unpkg, skypack, jsDelivr) is security item SEC-0930-F, tracked
  privately; separately, the sample tree breaks silently if skypack fails.
- Five CSRF patterns coexist, and `datagrid-custom.js` sends a literal `{{ csrf_token }}` from a static file.
