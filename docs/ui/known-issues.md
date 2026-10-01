# Known UI issues

This is the one ranked, deduplicated list of open problems in the NExtSEEK web UI: the Django pages,
the theme shell, the static JavaScript and CSS, and the embedded Nessie chat. The other pages in this
guide describe how things work and point here for problems; nothing else in `docs/ui/` keeps a
problems table.

How to use it:

- Start with [Fix first](#fix-first). It picks the items that hurt users most and groups them into
  small batches that touch the same files, in the order a fixer should take them.
- Every other open item is in the area tables below. Each row has an ID (`UI-NNN`), a severity, what a
  user sees (or, for debt, the maintenance risk), the evidence, where it lives and a one-line fix idea.
- Severity: **broken** means a control or page does not do what it says; **confusing** means it works
  but misleads, hides or looks wrong; **debt** means no user symptom today, only a maintenance risk.
- Evidence: **live 2026-09-30** means it was seen on nextseek-dev on that date (phone means 390x844
  with an iPhone user agent); **code** means it was confirmed by reading the tree at `caa55340` but not
  reproduced in a browser.
- Delete a row in the same commit that fixes it. Do not renumber: IDs are referenced from other pages
  and from commit messages. New items take the next free number.
- Cite files and symbols, not line numbers. If a row no longer matches the code, trust the code and fix
  the row.
- Security-relevant items are not described here (the repo is public). They are tracked privately and
  listed by code only in the [last section](#security-items-tracked-privately).

Area pages: [shell](shell.md), [pages](pages.md), [upload and samples](upload-and-samples.md),
[search and downloads](search-and-downloads.md), [projects, catalogs and graphs](projects-catalogs-graphs.md),
[styles](styles.md), [javascript](javascript.md), [chat frontend](chat-frontend.md),
[CI and deploy](ci-and-deploy.md), [legacy](legacy.md), [docs and help](docs-and-help.md).

## Fix first

Empty. The seven batches picked on 2026-09-30 (phone shell and sign-in, login redirects, "+ New sample"
on phones, the search toolbar buttons, the login logos, the Sample flow frame, Nessie on phones) were
fixed on 2026-10-01, with the operator's rulings on the publish buttons, the visitor menu, the
Mezzanine pages and the dead code; `ci/smoke/test_ui_shell.py` guards them on a deployed box. Pick
the next batch from the **broken** rows below, grouped by the files they touch.

## shell

The sidebar, drawer, top bar, footer, user panel and home page. See [shell.md](shell.md).

| ID | Severity | What a user sees | Evidence | Where | Fix idea |
|---|---|---|---|---|---|
| UI-008 | confusing | The sidebar is `role="dialog" aria-modal="true"` at all times, including on desktop where it is not a dialog; screen readers treat the rest of the page as inert | code | `base.html` `aside#sidebar` | Set the dialog roles from JS only while the phone drawer is open |
| UI-010 | debt | Sidebar submenus are toggled twice: by Bootstrap's `data-bs-toggle` and by a manual click handler that calls `Collapse.toggle()` | code | `themes/NextSeek/static/js/nextseek.js` (submenu handler on `[data-bs-toggle="collapse"]`) | Remove the manual handler |
| UI-011 | debt | Active-link highlighting uses `currentPath.startsWith(href)`. No two sidebar links overlap today, but a future link whose href is a prefix of another's (for example `/seek/samples/` beside `/seek/samples/upload/`) would light both | code | `nextseek.js` active-link loop over `.sidebar-nav .nav-link` | Pick the longest matching prefix, or set `aria-current` server-side |

## pages

Routes, views, login redirects, error pages and the Mezzanine pages that share the chrome. See
[pages.md](pages.md).

| ID | Severity | What a user sees | Evidence | Where | Fix idea |
|---|---|---|---|---|---|
| UI-028 | confusing | Access errors ("You are not in this project") render `error.html` with HTTP 200, so bookmarks, monitors and CI cannot tell an error from a page; the themed 404 page is never used | code | `seek/views/projects.py` `project_page`; `dmac/urls.py` `handler404` (Mezzanine's view; the theme has no errors folder) | Pass `status=403`; add themed 404 and 500 templates in an errors folder under the theme templates |
| UI-029 | broken | `/seek/remote/` and `/seek/url/<x>/` raise NameError (500). Unlinked, xfailed in CI | code | `seek/views/search.py` `remote` (calls undefined `samples`); `seek/views/samples.py` `seek` (calls undefined `getPageRequests`) | Delete both routes and views |
| UI-030 | debt | Several `seek/urls.py` patterns have no end anchor, so longer paths such as `/seek/templates/zzz` and `/seek/search/anything` also answer. | code | `seek/urls.py` `^templates/`, `^newsearch/`, `^search/`, `^searchUIDs/`, `^samples/upload/` and others (`^assistant/` is open on purpose: the chat's `chat/<uuid>` deep links need it) | Add `$` to each (keep `^templates/download/$` above `^templates/`), update the matching `ci/routes.py` patterns and re-run the route gate |
| UI-031 | debt | `project_page` checks the SEEK login by hand instead of using the shared decorator | code | `seek/views/projects.py` `project_page` | Use `requires_seek_login_redirect` |

## upload-and-samples

Assay sheet upload, data and protocol upload, templates, the attributes editor, sample pages and the
timeline. See [upload-and-samples.md](upload-and-samples.md).

| ID | Severity | What a user sees | Evidence | Where | Fix idea |
|---|---|---|---|---|---|
| UI-042 | confusing | For a non-supervisor, the creator list on the upload pages has an entry only for the last lab in the list, whatever labs the user belongs to (the dict is reset inside the lab loop) | code | `seek/views/upload.py` `batchUpload` and `datafileUpload` (`all_lab_users`) | Initialise `all_lab_users` once, before the loop |
| UI-044 | confusing | The info icon next to each template in the templates picker appears only on hover, so touch users never see it | code | `seek/templates/templatesList.html` `.tpl-item-info` | Show it always under `@media (hover: none)` |
| UI-045 | debt | The timeline root element carries two `id` attributes; the `#timeline-root` rule never applies | code | `seek/templates/sample_timeline.html` (`<div id="root" id="timeline-root">`) | Keep one id, or wrap |
| UI-046 | debt | The sample timeline is a built React bundle with no source in the repo, so it cannot be changed or rebuilt | code | `static/js/sample_timeline/` | Find and commit the source, or document where it lives |

## search-and-downloads

`/seek/search/` (Simple, Advanced, Retrieval, Deletion tabs), the phone search form, query pages and
downloads. See [search-and-downloads.md](search-and-downloads.md).

| ID | Severity | What a user sees | Evidence | Where | Fix idea |
|---|---|---|---|---|---|
| UI-064 | confusing | `?tab=new-retrieve` opens the Deletion tab and `?tab=delete` selects a tab index that does not exist | code | `searchAdvanced.html` tab-selection block | Map `retrieve` to 2 and `delete` to 3; drop `new-retrieve` |
| UI-065 | confusing | The Advanced grid's UID column has no filter box: the filter list names field `uuid`, the column is `uid` | code | `searchAdvanced.html` `nsEnableColumnFilters(dg, ['uuid', ...])` | Use `'uid'` |
| UI-066 | confusing | The filter row and Select-all act only on the loaded page of rows, while the header shows the full total | code | `searchAdvanced.html`; `pages/samples_stable.embed.html` | Label them "this page", or load all before filtering |
| UI-067 | confusing | Phone search with an empty keyword does nothing and says nothing | code | `searchAdvanced.html` `runMobileSearch` (returns early on empty `m_keyword`) | Show "Enter a keyword" (or allow a type-only search) |
| UI-068 | confusing | "View Timeline" works only for UIDs that start with `NHP` and contain `FLY`; every other sample gets "Please select a valid UID" | code | `pages/samples_stable.embed.html` (timeline button handler) | Hide the button for other samples, or explain the limit |
| UI-069 | confusing | Search-form icons render blank (Bootstrap 3 `glyphicon` classes, whose font is not loaded) | code | `pages/samples_search.embed.html`, `pages/searchAdvanced_search.embed.html` | Replace with `bi bi-*` icons |
| UI-070 | broken | On the unlinked `/seek/newsearch/` page, deleting samples succeeds on the server but the page reports "Error, try again." and keeps the rows (`dg` is undefined in the delete callback) | code | `seek/templates/newSearch.html` delete functions | Pass the grid in, or retire the page (see UI-073) |
| UI-071 | debt | Duplicate element ids (`north_div`, `center_div`, `north_div2`) across the four tabs | code | `searchAdvanced.html` tab layouts | Give each tab unique ids |
| UI-072 | debt | `datagrid-filter.js`, `datagrid-export.js` and `datagrid-custom.js` are loaded twice on the search page; the `base.html` comment warns that a second `datagrid-filter.js` double-wraps `loadData` and `autoSizeColumn` | code | `pages/samples_stable.embed.html` and `searchAdvanced.html` script tags | Load once in the page |
| UI-073 | debt | A second, unlinked search implementation (`/seek/newsearch/` plus six `*_new*` embeds) drifts from `/seek/search/` (no paging, no lineage filter); the download call-site test lists templates from both as live | code | `seek/templates/newSearch.html`; `seek/views/search.py` `newSearch`; `nextseek_api/tests/test_download_call_sites.py` | Decide: link and converge, or delete |
| UI-074 | debt | Old engine routes (`/seek/searchAdvanced/`, `/seek/searchUIDs/`, `/seek/samples/searching/`) were used only by templates that are now deleted | code | `seek/urls.py`; `seek/views/search.py` | Retire the routes |
| UI-076 | confusing | SOP Query links each SOP to its SEEK page, which returns 403 unless the user is also signed in to SEEK itself; Data File Query builds the same kind of link | live 2026-09-30 (SOPs); code (data files) | `pages/sops_table.embed.html`, `pages/datafile_table.embed.html` (`seek_url` plus `links.self`) | Download through the NExtSEEK SOP endpoint, or label the link "opens in SEEK (sign in there)" |
| UI-075 | debt | Page views write into a module-level `report` dict shared by every request | code | `seek/views/shared.py` `report`, used by `seek/views/assets.py` `sopQuery`, `datafileQuery` and `seek/views/upload.py` `batchUpload`, `datafileUpload` (lab and creator lists) | Build a local dict per request |

## projects-catalogs-graphs

Project list and page, the Sample flow diagram, catalog pages, admin catalogs and the sample tree. See
[projects-catalogs-graphs.md](projects-catalogs-graphs.md).

| ID | Severity | What a user sees | Evidence | Where | Fix idea |
|---|---|---|---|---|---|
| UI-084 | confusing | The Fullscreen overlay puts `/seek/projects/<id>/connections/` in the address bar; a reload there shows the bare diagram with no site navigation | code | `themes/NextSeek/static/js/nextseek.js` modal-route handler | Add a "Back to project" link in the frame header, or do not push the URL |
| UI-085 | confusing | Catalog tables (3 to 7 columns) overflow sideways on phones | code | `themes/NextSeek/templates/includes/attribute_definitions_table.html`; `nextseek.css` catalog table rules | Wrap in an `overflow-x: auto` container; hide Description under 768px |
| UI-086 | confusing | On the project list, the FAIRDATA link is a `<span onclick>` inside the card's `<a>`: not keyboard reachable, and a tap may open the project page instead | code | `seek/templates/projectsList.html` `.project-card`, `.fairdata-link` | Make the card a `<div>` with a stretched-link title and a real sibling `<a target="_blank" rel="noopener">` |
| UI-087 | confusing | Clades, Internal assays, SOPs and Data files pages use fixed pixel heights and EasyUI grids with no desktop-only notice on phones. On a phone, Data File Query, SOP Query, Sample Query and `/seek/newsearch/` lay out EasyUI tab strips and grid headers thousands of pixels wide, clipped at the edge | live 2026-09-30 (the four query pages); code (others) | `seek/templates/clades.html`, `internal_assays.html`, `sopsPage.html`, `dataFilesPage.html` | Add `.easyui-mobile-notice` like the upload pages |
| UI-088 | confusing | The project page "Data files" tile shows a per-project count but links to the global data-file search | code | `seek/templates/projectPage.html` Data files tile | Link with a project filter once the page supports one |
| UI-089 | confusing | The sample tree (v2) shows "Loading..." forever if its CDN imports or the tree API call fail; its edge tooltips are hover-only, so unreachable or sticky on touch | code | `static/js/dag/dag.js` | Wrap in try/catch and show a message; show tooltips on tap |
| UI-090 | debt | Sample flow and the sample tree load their libraries from public CDNs (unpkg, skypack) with no fallback, so a blocked CDN leaves an empty panel with no message (the Sample flow's are pinned with integrity hashes; the tree's are not). Third-party script loading is security item SEC-0930-F, tracked privately | code | `sampletype_connections.py` `_CYTO_CDN`; `static/js/dag/dag.js` imports | Vendor the libraries under `static/js/` and show a message when loading fails |
| UI-092 | broken | Seven sample type detail pages (MDL, SLD, SNSR, SUB, VIR, D.ADDCP, D.ADMP on dev) get HTTP 409 from the attribute definitions request; the script never checks the status, so the table wrongly says no attribute definitions are recorded | live 2026-09-30 | `themes/NextSeek/templates/includes/attribute_definitions_table.html` (POST `/nextseek_api/attributes/search/?page_size=5000`); the attributes search view in `nextseek_api` | Find why the search returns 409 for these types; show an error in the table instead of nothing |
| UI-093 | broken | Links to `/seek/sampletypes/A.MET/`, `A.RPPA/`, `D.ARR/` and `LYS/` return 404. A.MET, A.RPPA and D.ARR are SEEK types with no curated row; LYS has a row marked deprecated, which the detail view drops | live 2026-09-30 | `seek/templates/templatesList.html` info link (`.tpl-item-info`, fed by `template_catalog.load_catalog`, which keeps SEEK types with no context row); `sampleTypeDetail.html` lineage chips, whose `known` set in `context_catalog.load_sample_types` includes deprecated rows | Link only codes the detail page can show, or render a "not in the catalog" page instead of a 404 |
| UI-094 | confusing | The samples page of the largest project did not answer within 30 seconds | live 2026-09-30 (`/seek/projects/1/samples/`) | `seek/views/projects.py` `project_samples` | Page the query server-side; show the first page fast |
| UI-091 | debt | The catalog table filter script is an inline include, so it cannot be cached or linted; `.ns-page-header` and `.ns-page-title` are defined twice | code | `themes/NextSeek/templates/includes/catalog_table_filter.js`; `projectsList.html` and `catalog_styles.html` style blocks | Move the script to `themes/NextSeek/static/js/` and the shared styles to `nextseek.css` |

## styles

`nextseek.css`, tokens, fonts, breakpoints and page-level styles. See [styles.md](styles.md).

| ID | Severity | What a user sees | Evidence | Where | Fix idea |
|---|---|---|---|---|---|
| UI-100 | confusing | Three breakpoint conventions: the drawer switches below 992px, the 16 px inputs below 768px (767.98) and the desktop-only notice at 768px and below, and the "Mobile view v1" banner comment lists 768 / 576 / 400. Between 768 and 991px the drawer is active but inputs are small and the notice is hidden | code | `nextseek.css` "Mobile / Responsive" block, "Mobile view v1" and touch-target blocks, `.easyui-mobile-notice` in "Utility" | Standardise on 575.98 / 767.98 / 991.98 and pick one "mobile" cut point |
| UI-102 | confusing | The login page uses Source Sans 3 and Playfair Display; the app uses Inter. The `--ns-font-*` tokens name Inter first, which is not loaded on auth pages | code | `themes/NextSeek/templates/base_auth.html` font link vs `base.html` | Load the same font link, or add an explicit auth font token |
| UI-103 | confusing | Monospace text renders as Courier New: JetBrains Mono and Fira Code are named but never loaded | code | `nextseek.css` `:root` `--ns-font-mono` | Use `ui-monospace, SFMono-Regular, Menlo, monospace`, or load the font |
| UI-105 | debt | About 260 `!important` declarations make overrides order-dependent | code | `nextseek.css` ("EasyUI Theme Overrides" holds about half; the datagrid retune, search-tab and workbench sections most of the rest) | Scope EasyUI overrides under one class and drop `!important` where specificity suffices |
| UI-106 | debt | Nine templates declare a blue zebra-row colour (`.datagrid-row-alt { background: #e6f2ff }`) that never shows, because `nextseek.css` forces `.datagrid-row-alt` to `var(--ns-bg)` with `!important`; readers think rows are blue-striped | code | `.datagrid-row-alt` rules in page `<style>` blocks (list in [styles.md](styles.md)) | Delete the nine dead rules |
| UI-107 | debt | Unused legacy variables in `:root` (`--primary-color`, `--sidebar-bg`, `--sidebar-text`, `--sidebar-hover`, `--sidebar-active`, `--content-bg`, `--header-bg`, `--footer-bg`, `--ns-header-bg`). `--sidebar-width` and `--header-height` are in use | code | `nextseek.css` `:root` | Delete after a fresh grep |
| UI-108 | debt | The login page has about 245 lines of inline CSS with hard-coded crimson literals instead of tokens | code | `login.html` inline `<style>` | Move to `nextseek.css` and use `var(--ns-crimson*)` |
| UI-109 | debt | About 20 templates carry their own `<style>` blocks and about 300 inline `style=` attributes; duplicate chip and title families | code | `seek/templates/` | Move shared families into `nextseek.css` |

## javascript

Static scripts, inline page scripts, CSRF handling and error handling. See [javascript.md](javascript.md).

| ID | Severity | What a user sees | Evidence | Where | Fix idea |
|---|---|---|---|---|---|
| UI-120 | confusing | Admin sync buttons and several table fetches fail silently: nothing on screen when the request fails | code | `seek/templates/clades.html`, `internal_assays.html`; `pages/datafile_table.embed.html`, `pages/sops_table.embed.html`; `pages/batch_upload.embed.html` | A shared fetch helper that checks `ok` and shows a message |
| UI-121 | debt | `upload()` sends the literal text `{{ csrf_token }}` because a static file is not templated; any page that calls it would get a 403 (only unrouted pages do today) | code | `static/js/custom/datagrid-custom.js` `upload` | Read `csrftoken` from the cookie like `ns_sample_download.js` |
| UI-122 | debt | Five CSRF patterns coexist, with six copies of a `getCookie` helper | code | see the CSRF patterns table in [javascript.md](javascript.md) | One helper in a static file, used everywhere |
| UI-123 | debt | Debug `alert()` calls and `console.log` leftovers in dead helpers | code | `static/js/custom/datagrid-custom.js` `downloadTable` | Delete the helpers |
| UI-124 | debt | Globals leak into the page namespace (`accept`, `append`, `reject`, `upload`, `jsonlist`, `getCookie`, `lab_options`) | code | `static/js/custom/datagrid-custom.js`; page scripts | Wrap in modules or a single namespace |
| UI-125 | debt | The modal route injects the target page with `innerHTML`, so scripts in it never run, and it does not check `r.ok` | code | `themes/NextSeek/static/js/nextseek.js` modal-route handler | Check the response; use an iframe for targets that need JS |
| UI-126 | debt | Page data is passed into scripts with `\|safe` JSON, and an inline `<script defer>` races the EasyUI parser | code | `pages/batch_upload.embed.html` | `{{ x\|json_script:"id" }}` and init inside `$(function(){ ... })` |
| UI-127 | debt | Four separate delete-samples implementations and three near-identical table pages | code | `searchAdvanced_stable`, `searchAdvanced_deletion`, `samples_stable` embeds, `newSearch.html`; `datafile_table`, `sops_table`, `batchSearch_table` | Converge on one implementation per job |

## chat-frontend

The embedded Nessie chat (React bundle) and its Django host page. See [chat-frontend.md](chat-frontend.md).

| ID | Severity | What a user sees | Evidence | Where | Fix idea |
|---|---|---|---|---|---|
| UI-164 | debt | Replies appear whole after the turn completes; only the step list moves while waiting | code | `src/components/ChatPanel/MessageBubble.tsx`; `src/lib/services/chatApi.ts` | Needs partial-text events from the backend first |
| UI-165 | debt | Dark tokens ship but nothing applies `.dark`, so the chat is always light | code | `src/index.embedded.css` (`#chat-assistant-root.dark`) | Decide with the Django theme; toggle the class on the root if wanted |
| UI-166 | debt | No Tailwind preflight and unprefixed utilities: Bootstrap base styles leak into chat elements and chat utilities are global on the page | code | `src/index.embedded.css` imports | Prefix Tailwind or add a scoped reset under `#chat-assistant-root` |
| UI-167 | debt | The shipped chat is only what is committed under `static/js/chat_assistant/`; the guard test checks two source strings, so a change can ship without its rebuilt bundle and users see the old UI | code | `NessieAI/chat_frontend/package.json` `build:embedded`; `NessieAI/tests/build_tools/unit/test_committed_chat_bundle.py` | Build the bundle in the Dockerfile, or record a source hash in the build and check it |
| UI-168 | debt | Progress, send and download handling is duplicated by hand between the embedded and standalone shells (it has already caused a shipped bug) | code | `src/EmbeddedApp.tsx` vs `src/AppLayout.tsx` | Extract one hook both shells call, or drop the standalone shell |
| UI-169 | debt | The Vite manifest is cached per process outside DEBUG. Harmless today, because a bundle only reaches a box through a rebuild (the image's `static/` is not bind-mounted), but a bundle copied into a running container would not be picked up until a restart | code | `seek/templatetags/vite_assets.py` `_load_manifest`, `_manifest_cache` | Key the cache on the manifest mtime, or leave it and keep deploying bundles by rebuild |
| UI-170 | debt | `tailwind.config.js` looks like theme config but Tailwind 4 never reads it; `src/index.css` still has v3 directives; `index.html` points at a missing `/vite.svg` | code | `NessieAI/chat_frontend/tailwind.config.js`, `src/index.css`, `index.html` | Delete or `@config` it; tidy the rest |
| UI-171 | debt | Dead components (`LeftSidebar`, test-runner list, unused `ui/` primitives) | code | `src/components/Layout/LeftSidebar.tsx`, `src/components/TestRunner/`, `src/components/ui/` | Delete after confirming no imports |
| UI-172 | debt | The `xlsx` dependency is pinned to an unmaintained npm release (0.18.5), and its lazy chunk is never loaded: its only user, `downloadSearchAsExcel`, has no caller | code | `NessieAI/chat_frontend/package.json`; `src/lib/services/chatApi.ts` `downloadSearchAsExcel` | Delete `downloadSearchAsExcel` and the dependency |
| UI-173 | debt | Images a CC turn produces cannot be shown inline, only downloaded | code | `src/components/ChatPanel/ReportArtifacts.tsx`, `MessageBubble.tsx` | Add an image artifact kind rendered with `<img>` |
| UI-174 | broken (to confirm) | Tables from graph answers probably never render in the chat: the server sends `{type, title, columns, rows: [[...]]}`, while the chat keeps only artifacts with `artifact_type === "table"` and a `data` list of records. One graph turn on dev confirms it | code | `nextseek_api/assistant/excel_export.py` `_graph_table_artifacts` (reached from `extract_table_artifacts` for `graph_query`); `NessieAI/chat_frontend/src/components/ChatPanel/ReportArtifacts.tsx` | Fix the shape on the server, or add an adapter in the chat until it is fixed |

## ci-and-deploy

Static files, caching, the route gate and the tests that guard the UI. See [ci-and-deploy.md](ci-and-deploy.md).

| ID | Severity | What a user sees | Evidence | Where | Fix idea |
|---|---|---|---|---|---|
| UI-180 | confusing | nginx gives hashed and unhashed static URLs the same 30-day expiry, so a hard-coded unhashed URL can serve a stale file for a month | code | `docker/nginx.conf` `location /static/` | Long expiry only for hashed names; short expiry otherwise |
| UI-181 | debt | A new project-level page is silently unowned (gate stays green, no reachability probe) unless the author edits `_PROJECT_LEVEL` | code | `ci/gate/live_routes.py` `_PROJECT_LEVEL` | Fail the gate when a route above the Mezzanine catch-all is in neither list |
| UI-182 | debt | Mezzanine's remaining routes (admin helpers, CMS pages) sit behind its include and are undeclared in the route registry | code | `ci/routes.py` `REGISTRY` | Declare the ones the admin uses |
| UI-183 | debt | No test runs `collectstatic` against the real tree: CSS tests read source files and the test settings swap to plain storage | code | `seek/tests/test_catalog_tables.py`; `dmac/test_settings.py` storage override | One test that collects into a temp dir and resolves every `{% static %}` in the base templates |
| UI-184 | debt | A hand-kept route count goes red in a lane that cannot say the right number | code | `ci/smoke/test_registry_contents.py` | Derive from the gate or drop the constant |
| UI-185 | debt | The theme landmine about unhashed CSS and the storage comments about the vendored EasyUI tree are out of date, so agents add workarounds that already exist | code | `themes/CLAUDE.md` landmines; `dmac/storage.py`; `dmac/settings.py` static settings comments | Rewrite both to describe `ForgivingManifestStaticFilesStorage` as it behaves today |

## legacy

Dead templates, duplicate vendored libraries and published leftovers. See [legacy.md](legacy.md).

| ID | Severity | What a user sees | Evidence | Where | Fix idea |
|---|---|---|---|---|---|
| UI-201 | confusing | The vendored EasyUI tree exists as two identical copies, both collected and published | live 2026-09-30 | `static/jquery-easyui-1.5.2/`, `themes/NextSeek/static/jquery-easyui-1.5.2/` | Delete one of the two copies (the `demo/` and `demo-mobile/` folders are already gone) |
| UI-202 | debt | Sample tree v1 is included twice, each inside an HTML comment, but both `{% include %}` tags still render, so every sample page carries about 520 lines of hidden markup and four CDN script tags | code | `seek/templates/pages/samples.embed.html` (two commented blocks including `pages/samples_tree.embed.html`) | Remove both include lines, then `samples_tree.embed.html` |
| UI-205 | debt | The repo-root `templates/` tree (66 files) is not on the loader path; editing it has no effect | code | `templates/`; `dmac/settings.py` `TEMPLATES` | Delete, or add a README saying it is unreachable |
| UI-208 | debt | An old nested timeline build copy is published alongside the current one | code | nested `static/js/sample_timeline/sample_timeline/` | Delete |
| UI-209 | debt | Unused shipped JS and CSS: `static/js/buildtree/`, `static/js/dag/d3neo4j.*`, Bootstrap 3 `static/css/bootstrap*.css` and `static/js/bootstrap*.js`, `pages/menus/tree.html` | code | as listed | Remove after a reference check |
| UI-210 | debt | About 190 images (55 at the top of `themes/NextSeek/static/img/`, about 130 in its 15 subfolders, 4 in `static/img/`), many unreferenced (logo variants and others), all collected and served | code | `themes/NextSeek/static/img/`, `static/img/` | Move unreferenced images out |
| UI-211 | debt | `projectsList.html` loads a template-tag library (`{% load index %}`) it never uses | code | `seek/templates/projectsList.html` first lines | Delete the line |

## docs-and-help

The Getting Started page, API docs pages and help links. See [docs-and-help.md](docs-and-help.md).

| ID | Severity | What a user sees | Evidence | Where | Fix idea |
|---|---|---|---|---|---|
| UI-220 | confusing | The help text tells users to type into an "Ask Nessie..." sidebar input and use a "Talk to Nessie" link; neither exists (the sidebar has an Ask Nessie button, and the text box under Quick Access is a UID search) | code | `themes/NextSeek/templates/help/getting_started.html` | Rewrite the paragraph to match the sidebar and home page |
| UI-221 | confusing | Two docs entries with no explanation: "Getting Started" (in-app) and "Documentation" (external GitBook) | code | `themes/NextSeek/templates/nav.embed.html` | Merge into one Docs entry once in-repo docs land |
| UI-222 | confusing | The footer has no About, Contact, Docs or source links; "Contact Support" in the sidebar is a `mailto:` only, which does nothing without a mail client | code | `page-footer.embed.html`; `nav.embed.html` Contact Support | Add footer links and a visible contact line with the team's support address |
| UI-223 | debt | No page links to Swagger, ReDoc or the schema, and logged-out visitors get a DRF auth error rather than a login redirect or a clear page | code | `nextseek_api/urls.py` (Spectacular views, `IsAuthenticated`); `themes/NextSeek/templates/nextseek/swagger_ui.html` | Link from Getting Started or Resources; redirect anonymous HTML requests to `/login/?next=...` |
| UI-224 | debt | The help text is hand-written HTML inside translation tags, a second copy of the GitBook "how to upload and search" | code | `help/getting_started.html` | Replace with the in-repo docs pages when they land |
| UI-225 | debt | Nessie's knowledge of the docs depends on a third-party site-index format that has already changed twice | code | `NessieAI/build_tools/ingest_nextseek_docs/fetch.py` | Point the ingest at the in-repo markdown |

## Security items (tracked privately)

| Code | Area |
|---|---|
| SEC-0930-B | two admin pages (sample attributes, admin retrieve) |
| SEC-0930-C | the home page for logged-out visitors |
| SEC-0930-E | the vendored EasyUI demo folders |
| SEC-0930-F | third-party script loading (CDN tags) |
| SEC-0930-G | the sample timeline route |
