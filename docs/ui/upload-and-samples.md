# Upload and samples

## What this covers

Every way a signed-in user creates or changes samples, files and templates through the web UI, and
the sample pages you reach from UID search: the two "+ New sample" controls, the assay sheet (batch)
upload page, data and protocol file upload, the Download Templates picker (the templates API), the
sample attributes admin page, the sample tree and table pages, and the sample timeline.

It does not cover the search pages that also delete samples (see [search-and-downloads.md](search-and-downloads.md)),
the sidebar and base template (see [shell.md](shell.md)), or the chat panel that can also start work
(see [chat-frontend.md](chat-frontend.md)). Dead templates are listed in [legacy.md](legacy.md).

## How it works

Every page here is a Django view in the `seek` app that renders a template extending
`themes/NextSeek/templates/base.html` (Django finds templates through `TEMPLATES["DIRS"]` in
`dmac/settings.py`, which points at `themes/NextSeek/templates`, then each app's `templates/`
folder, so the `seek/templates/` pages resolve by bare name). Routes are in `seek/urls.py`; views are in
`seek/views/` (`upload.py`, `samples.py`, `assets.py`, `timeline.py`).

There are two kinds of page:

- Server-rendered shells that do their work with `fetch()` calls to `/nextseek_api/...` (batch
  upload, data upload, attributes admin). The Python view only fills lists of labs, people or
  sample types into `report`; the upload itself is an API call.
- Plain forms or read-only pages (Download Templates posts a normal form; sample tree and table
  pages are filled from the database and from `/seek/retrieve/samples/`).

Most of the older pages use jQuery EasyUI (`easyui-tabs`, `easyui-layout`, `easyui-combobox`,
datagrids) and are desktop-only. `sampleAttributes.html` and `templatesList.html` are newer, plain
Bootstrap 5 with theme tokens and are the only ones that cope with a phone.

```
"+ New sample" (sidebar or home tile)
      |  plain <a href="/seek/samples/upload/">
      v
/seek/samples/upload/  --(logged out)--> /login/?next=/seek/samples/upload/
      |
      v
batchUpload view -> batchUpload.html -> pages/batch_upload.embed.html
      |  fetch /nextseek_api/batch-upload/validate/ , start/ , status/<job> , summary/<job>
      v
background job + log textarea
```

## The two "+ New sample" controls

Both are plain links to `/seek/samples/upload/`. There is no modal, dropdown or script involved, and
no other create control exists (the chat panel has none). A sample is only ever created by uploading
an assay sheet; there is no single-sample form. Below 768px both controls carry a "desktop only" hint
(`.desktop-only-hint`), because the upload pages need a laptop or desktop.

| Control | File and element | Notes |
|---|---|---|
| Sidebar button | `themes/NextSeek/templates/nav.embed.html`, `<a class="qa-cta">` in the quick-access block | On every page that includes the sidebar. On phones the sidebar is an off-canvas drawer (see [shell.md](shell.md)); the link works once the drawer is open. |
| Home tile | `themes/NextSeek/templates/index.html`, `<a class="dash-action accent">` in `aside.dash-actions` | Home page only. |
| Sidebar "Data Entry" submenu | `nav.embed.html`, `#dataEntrySubmenu` | Two links: "Assay Sheet Upload" (`/seek/samples/upload/`) and "Data & Protocol Upload" (`/seek/data/upload/`). Collapse is Bootstrap, wired in `themes/NextSeek/static/js/nextseek.js`. |

Logged-out behaviour: the `batchUpload` and `datafileUpload` views are wrapped in
`requires_seek_login_redirect()` from `seek/decorators.py`, which sends the browser to
`/login/?next=<the page asked for>`, so signing in returns to the upload page.

Styling: `.qa-cta`, its `.qa-plus` and `.desktop-only-hint` in `themes/NextSeek/static/css/nextseek.css`.
The CTA is full width in the drawer, like the Nessie button and the UID box above it.

## Page inventory

| Page | Route (`seek/urls.py`) | View | Template chain | Login gate |
|---|---|---|---|---|
| Assay sheet upload | `/seek/samples/upload/` (name `sampleUpload`) | `seek/views/upload.py: batchUpload` | `batchUpload.html` > `pages/batch_upload.embed.html` | redirect |
| Data and protocol upload | `/seek/data/upload/` | `upload.py: datafileUpload` | `dataFileUpload.html` (inline, no embed) | redirect |
| Download templates | `/seek/templates/`, download POST `/seek/templates/download/` | `seek/views/assets.py: templatesList`, `templatesDownload` | `templatesList.html` (inline CSS and JS) | redirect |
| Sample attributes admin | `/seek/samples/attributes/` | `seek/views/samples.py: sampleAttributes` | `sampleAttributes.html` (inline, Bootstrap 5) | redirect |
| Sample page (tree and info) | `/seek/sample/id=<id>/`, `/seek/sampletree/uid=<uid>/` | `samples.py: sample`, `sampleTree` (which resolves the UID to an id and calls `sample`) | `samples.html` > `pages/samples.embed.html` > `pages/samples_tree_new.embed.html` | redirect, plus a project-scope check (`_sampleVisible`, 404 if not visible) |
| Sample table by type | `/seek/samples/query/`, `/seek/sample_types/id=<id>/` | `samples.py: sampleQuery`, `sample_type` | `sampleQuery.html` > `pages/samples_table.embed.html` | redirect |
| Sample timeline | `/seek/sample_timeline/.*` | none: a bare `TemplateView` | `sample_timeline.html` + built React bundle | see security item SEC-0930-G, tracked privately |

Note the route patterns are unanchored `re_path` regexes (for example `^samples/upload/` has no
trailing `$`), so any suffix also matches.

### Assay sheet upload

Purpose: validate a sample Excel sheet against a project, then upload one or more sheets as a
background job and watch a log.

- View: `batchUpload` fetches SEEK institutions for `lab_options` and the people in each lab for
  `all_lab_users`, and passes them in `report`. Supervisors see every person in a lab; other users
  see only themselves.
- `batchUpload.html` shows an amber `.easyui-mobile-notice` ("desktop-only") and wraps the embed in
  `.easyui-page-wrapper` (scrolls sideways instead of clipping) > `easyui-tabs` (height 800px) >
  `easyui-layout` (height 700px, north region 760px). The notice is hidden by default and shown at
  768px and below by a rule in `nextseek.css`.
- `pages/batch_upload.embed.html` holds two hidden forms, a 70%-wide table with file inputs
  (`width:220px`), EasyUI comboboxes for project, lab and creator, an "update existing" checkbox, and
  a full-width log `<textarea id="messages">`. The script is inline in the same file.

| Step | Endpoint | Method |
|---|---|---|
| Fill project list | `/nextseek_api/projects/` | GET |
| Validate a sheet | `/nextseek_api/batch-upload/validate/` | POST |
| Start the upload job | `/nextseek_api/batch-upload/start/` | POST |
| Poll job (every 1 s) | `/nextseek_api/batch-upload/status/<job>` | GET |
| Download result CSV | `/nextseek_api/batch-upload/summary/<job>` | GET |

The API side is `BatchUploadViewSet` in `nextseek_api` (registered as `batch-upload` in
`nextseek_api/urls.py`).

### Data and protocol upload

Purpose: upload SOP (protocol) or data files to a project, one request per file, with lab and
creator. `dataFileUpload.html` extends `base.html` directly with an inline form and script. It posts
each file to `/nextseek_api/${upload_type}/` where the type is `sops` or `data_files` (routers
`SopViewSet` and `DataFileViewSet`), and loads projects from `/nextseek_api/projects/`. Progress is
written into `<div id="messages">`. It uses the same 70% table and fixed-width inputs as batch
upload, and the same desktop-only notice.

The data-file list and SOP list pages (`dataFilesPage.html`, `sopsPage.html`) are read-only and
belong with [search-and-downloads.md](search-and-downloads.md).

### Download templates (the templates API)

Purpose: pick sample types, get one Excel workbook (a README sheet plus one sheet per type).

- `assets.py: templatesList` renders `templatesList.html` with `_templates_context()`, which adapts
  `build_catalog()` from `nextseek_api/services/template_catalog.py`. `templatesDownload` streams the
  workbook from `render_template_workbook` after `select_entries`. Both views share that service code
  with the API (`TemplatesViewSet` in `nextseek_api/services/templates.py`, registered as `templates`;
  it has `catalog` and `generate` routes), so the page and the API cannot hand out different workbooks.
  One deliberate difference: the page drops unknown codes, the API returns 422.
- The page is a real `<form method="post" action="/seek/templates/download/">` with a CSRF token; it
  makes no fetch calls. Its inline script mirrors the parent and companion rules in
  `nextseek_api/services/type_requirements.py`, so a rule change needs editing both.
- Phone: it has its own breakpoints (two columns, then one) and a sticky selection bar that wraps.
  The "what is this type" info icon (`.tpl-item-info`) is `opacity:0` until hover or focus, so it is
  invisible on touch screens.

### Sample attributes admin

Purpose: view and edit the attribute definitions of a sample type. Writes need a superuser (the
attributes API checks it), and every write is previewed with a dry run in a Bootstrap modal before
it is applied. The view only passes `type_options` and `attribute_types_options`, which the
template injects as `window.NS_ATTR_BOOT`. Everything else is `/nextseek_api/attributes/`:
`search/`, a list GET, `batch-create/`, `batch-patch/` (PATCH) and `batch-delete/`. The sidebar
link is shown to superusers only (`nav.embed.html`, link `/seek/samples/attributes`); access to the
page itself is security item SEC-0930-B, tracked privately.

Phone: the table has `min-width:1120px` inside a scroll wrapper, so it scrolls sideways; the change
tray goes full width under 992px. Usable but not designed for touch.

### Sample tree and sample info

`samples.html` is a four-line wrapper that includes `pages/samples.embed.html`. That embed is an
EasyUI layout of fixed `height:1500px` with an `easyui-tabs` tree panel (`height:600px`) holding
`pages/samples_tree_new.embed.html` (an SVG graph drawn by the ES module `static/js/dag/dag.js`,
with a fullscreen button) and a "Sample info" panel of attribute rows plus a "Download All Samples"
button that calls `nsDownloadSamples(...)` from `static/js/ns_sample_download.js` (the same script
that `searchAdvanced.html` loads; see `docs/sample-download-workflow.md`).

The old v1 tree is included twice, each time inside HTML comments (`<!-- {% include "pages/samples_tree.embed.html" %} -->`),
but Django still renders includes inside HTML comments, so that template is evaluated twice and its
output (including CDN script tags) ships to the browser inside a comment. Only `{# #}` or
`{% comment %}` would stop it. So `samples_tree.embed.html` is not a safe-to-delete orphan until those
two comment blocks are removed.

The table variant is `sampleQuery.html` > `pages/samples_table.embed.html`: an EasyUI datagrid
loaded from `/seek/retrieve/samples/` with an Excel export and a 900px-wide `json_metadata` column.
Static assets for the tree live in the repo-root `static/js/`, not in `themes/`.

### Sample timeline

An event timeline for non-human primate samples, built as a React bundle. `sample_timeline.html`
loads hashed files from `static/js/sample_timeline/assets/` (`index-*.js`, vendor chunks, one CSS
file). There is no source in the repo, only the built output, so
a hash change means editing the template's `extra_head` tags by hand. Its data comes from
`seek/views/timeline.py` (`nhp_info`, `get_nhp_data`, `download_nhp_data`, `fetch_event_data`,
all DRF `@api_view(['GET'])`) at `/seek/nhpinfo/<name>/`, `/seek/nhpdata/<name>/`,
`/seek/nhpdata/<name>/download/` and `/seek/eventdata/<name>/<type>/<date>/`. The mount element is
`<div id="root" id="timeline-root">`: a duplicate `id` attribute, so the `#timeline-root` min-height
rule in the same template never applies. Phone behaviour was not inspected.

## Phone behaviour summary

| Page | On a 390px phone |
|---|---|
| Both "+ New sample" links | Work, and say "desktop only" (the sidebar one is in the drawer, opened from the sticky top bar). |
| Assay sheet upload | Desktop-only notice shows (at 768px and below); the form scrolls sideways (70% table, 220px inputs). |
| Data and protocol upload | Same notice and overflow. |
| Download templates | Works; info icons invisible on touch. |
| Sample attributes | Scrolls sideways; acceptable. |
| Sample tree and table | Fixed 1500/600/500px EasyUI heights with nested scrollbars; no notice. |
| Timeline | Not checked. |

## Orphans

The unrouted templates in this area (`sampleUpload.html`, `sampleDeletion.html`, `samplesTest.html` and their embeds) were deleted on 2026-10-01. Real deletion is the `Delete samples` buttons in the search grids, posting to `/seek/samples/delete/` (`sampleDelete` in `seek/views/samples.py`). Remaining cleanup: [legacy.md](legacy.md).

## Where to edit

| Task | Files and symbols | Easy to miss |
|---|---|---|
| Change the "+ New sample" label or target | `nav.embed.html` (`.qa-cta`), `index.html` (`.dash-action.accent`) | Two separate places. Change `ci/routes.py` if the route changes. |
| Fix the login redirect path | `seek/views/upload.py`, decorator on `batchUpload`; `ci/routes.py` `Route` for `/seek/samples/upload/` | The CI route record encodes the wrong path; edit it in the same commit. |
| Add a field to batch upload | `pages/batch_upload.embed.html` (form and inline script), validate/start handling in `nextseek_api` `BatchUploadViewSet` | Two hidden forms plus the script; the file inputs are tied to forms by the `form=` attribute. |
| Change lab or creator lists | `upload.py: batchUpload` and `datafileUpload` (copy-pasted loops) | Edit both views. |
| Make upload usable on phones | `batchUpload.html`, `pages/batch_upload.embed.html`, `dataFileUpload.html` | Remove the EasyUI tabs/layout wrapper and the fixed widths; then drop the `easyui-mobile-notice` div. |
| Add or rename a template type option | `nextseek_api/services/template_catalog.py`, `type_requirements.py`; page script in `templatesList.html` | The page script duplicates the rules. |
| Edit the attributes page | `sampleAttributes.html`, `nextseek_api` attributes API | The page is one 1500-line file with inline CSS and JS. |
| Change the sample tree | `pages/samples_tree_new.embed.html`, `static/js/dag/dag.js` | Assets are in repo-root `static/`. Committed-bundle and cache notes in [javascript.md](javascript.md). |
| Change the timeline | `sample_timeline.html` and `seek/views/timeline.py` | No source for the bundle; update the hashed file names if the bundle is rebuilt elsewhere. |
| Add a page | route in `seek/urls.py`, view in `seek/views/`, template in `seek/templates/`, sidebar link in `nav.embed.html` | Add the route to `ci/routes.py` or the route gate fails. |

## Gotchas

- `{% include %}` inside an HTML comment still runs (see the sample tree section).
- Django drops anything outside `{% block %}` in a child template, so scripts on these pages must be
  inside `block main` or `extra_head`.
- Route regexes are unanchored; order in `seek/urls.py` matters (`templates/download/$` is listed
  before `templates/` for that reason).
- The desktop-only notice appears at 768px, while the sidebar becomes a drawer at 992px, so tablets
  between those widths get the EasyUI layout without any warning.
- Both upload pages have the notice; adding it elsewhere is one `<div class="easyui-mobile-notice">`.
- `nextseek.css` gets a content-hashed URL, so it updates once the box re-collects static (an app
  restart after `git pull`). The upload pages' own scripts are inline, but anything they load by a
  plain `{{STATIC_URL}}` path can stay cached for 30 days; hard-reload before blaming a deploy.
- `all_lab_users = {}` is reset inside the lab loop for non-supervisors in both upload views, so only
  the last lab has a creator entry.

## Known issues

See [known-issues.md](known-issues.md#upload-and-samples). The ones that matter most:

- After login, "+ New sample" lands on a 404 because the `next` path is wrong.
- The upload pages overflow on phones and only one of the two warns about it.
- Access to the attributes admin page and to the timeline: security items SEC-0930-B and SEC-0930-G
  (tracked privately).
