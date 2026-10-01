# Search and downloads

## What this covers

The Sample Search page (`/seek/search/`): its four desktop EasyUI tabs, its separate phone form,
what each control posts to (mostly `graph_search`), the results grids, the unified download client
(`static/js/ns_sample_download.js` to `/nextseek_api/samples/retrieve/`), and the Data File Query,
Protocol (SOP) Query and old Sample Query pages. It also lists the unlinked `/seek/newsearch/` page
and which search templates and routes are dead.

It does not cover the Nessie chat UI (see [chat-frontend.md](chat-frontend.md)), the sample detail
and tree pages (see [upload-and-samples.md](upload-and-samples.md)), or upload (see [upload-and-samples.md](upload-and-samples.md)).
The workbook that a download produces (README sheet, lineage tree, vocabularies) is described in
[docs/sample-download-workflow.md](../sample-download-workflow.md); this page only says how the
browser reaches it. Dead files are listed in more detail in [legacy.md](legacy.md).

## How it works

`/seek/search/` is served by `seek/views/search.py: searchAdvanced` (login required, redirects back
to `/seek/search/`). The view only supplies the sample-type list (`report.type_options` as a JSON
string, `report.type_option_list` as a Python list for the phone `<select>`). Everything else
happens in the browser. The template is `seek/templates/searchAdvanced.html`, which includes
embeds from `seek/templates/pages/`. The page loads jQuery EasyUI (1.5.2, from
`themes/NextSeek/templates/base.html`) and builds its tabs, grids and pagers there.

Two blocks render on the same page. `.d-only` holds the four EasyUI tabs, `.m-only` holds a small
hand-built form. The CSS switch is at 768px (`themes/NextSeek/static/css/nextseek.css`, the
`.d-only` and `.m-only` rules). Only one of them is visible, but EasyUI still builds the hidden
grids on load.

Request flow for a search and a download:

```
Simple tab box      \
Advanced tab box     >  SampleSearchCore (pages/sampleSearch_core.embed.html)
Phone form (m-only) /     builds the JSON body, formats rows
        |
        v
POST /nextseek_api/samples/graph_search/?page=N&page_size=1000
        (nextseek_api/services/graph_search.py: GraphSearchViewSet)
        |
        v
nsSearchState[key] remembers the last query   -->  #simple_dgtable / #advanced_dgtable
        ^                                            (EasyUI datagrid, pagination off)
        |                                                    |
#simple_pager / #advanced_pager (easyui-pagination)          | user ticks rows
  asks for another page, or loops pages for ALL              v
                                              nsCollectSelectedUids(grid)
                                                     |
                                                     v
                                     nsDownloadSamples(uids, {includeTree})
                                                     |
                                                     v
                             POST /nextseek_api/samples/retrieve/
                               (nextseek_api/services/sample_retrieve.py)
                                                     |
                                                     v
                              blob saved as download-samples-<timestamp>.xlsx
```

Results grids are not server-paged by EasyUI. Each grid has `pagination:false` and a separate
`easyui-pagination` widget asks `graph_search` for a page (size 1000, or "ALL", which reads 1000-row
pages in a loop and asks for confirmation above 10,000 rows; constants `NS_MAX_PAGE_SIZE`,
`NS_ALL_PAGE_SIZE`, `NS_ALL_CONFIRM_AT` in `searchAdvanced.html`). The filter row
(`datagrid-filter.js`) and sorting only act on the rows already loaded, while the header shows the
server total. Selection uses a checkbox column (`ck`) with `singleSelect:true` and
`selectOnCheck:false`; handlers write `row.ck`, so "select all" means the loaded page.

## Inventory

### Pages and routes

Routes are in `seek/urls.py` (mounted at `/seek/`). "Linked" means the theme nav, home page or
another live template links to it.

| Page | Route | View | Template | Linked | Status |
|---|---|---|---|---|---|
| Sample Search | `/seek/search/` | `views/search.py: searchAdvanced` | `searchAdvanced.html` | nav (`themes/NextSeek/templates/nav.embed.html`), home tiles | Live |
| Sample Search, old URL | `/seek/samples/search/` | `search.py: sampleSearch` | none | no | 302 to `/seek/search/` |
| Data File Query | `/seek/datafile/query/` | `views/assets.py: datafileQuery` | `dataFilesPage.html` + `pages/datafile_table.embed.html` | nav, "Data Query" submenu | Live, see Gotchas |
| Protocol (SOP) Query | `/seek/sop/query/` | `assets.py: sopQuery` | `sopsPage.html` + `pages/sops_table.embed.html` | nav, "Data Query" submenu | Live |
| New Search | `/seek/newsearch/` | `search.py: newSearch` | `newSearch.html` + six `*_new*` embeds | no | Live but unlinked; uses `advanced_search`, not `graph_search` |
| Sample Query (old) | `/seek/samples/query/`, `/seek/sample_types/id=<n>/` | `views/samples.py: sampleQuery`, `sample_type` | `sampleQuery.html` + `pages/samples_table.embed.html` | no | Legacy, unlinked; grid loads `/seek/retrieve/samples/` |
| Nessie | `/seek/assistant/` | `search.py: smartSearch` | `smartSearch.html` | sidebar button (`includes/nessie_button.html`) | Live; anonymous users are sent to sign in |

### Tabs on /seek/search/

| Tab (index) | Content | Grid and pager | Embeds |
|---|---|---|---|
| 0 Sample Search | sample type, attribute, rule, From/To, optional "Associated with" lineage filter | `#simple_dgtable`, `#simple_pager` | `samples_search.embed.html`, `samples_stable.embed.html` |
| 1 Advanced Sample Search | boolean query builder (AND/OR/NOT, `term[TYPE]`), Partial/Exact, lineage filter | `#advanced_dgtable`, `#advanced_pager` | `searchAdvanced_search.embed.html`, `searchAdvanced_stable.embed.html` |
| 2 Sample Retrieval | textarea of UIDs, downloads a workbook with lineage | none | inline in `searchAdvanced.html` (`#retrieval_uids`, `nsRetrieveSamples`) |
| 3 Sample Deletion | textarea of UIDs, type DELETE, posts to `/seek/samples/delete/` | none | `searchAdvanced_deletion.embed.html` |

The initial tab comes from `?tab=simple|advanced|retrieve` in the ready handler at the top of
`searchAdvanced.html` (see Gotchas for `new-retrieve` and `delete`).

### Endpoints the page calls

| Call | Made by | Served by |
|---|---|---|
| POST `/nextseek_api/samples/graph_search/` (JSON: `sampletype`, `filter_searchText`, `extensions.query`, `.where`, `.lineage`) | both desktop boxes and the phone form | `nextseek_api/services/graph_search.py: GraphSearchViewSet`, registered in `nextseek_api/urls.py`; page size capped at 1000 (`nextseek_api/graph_search/query.py`, `MAX_PAGE_SIZE`) |
| GET `/seek/attributes/id=<type id>/`, GET `/seek/operators/` | Simple tab attribute and rule comboboxes | `views/samples.py: getAttributes`, `getOperators` |
| POST `/nextseek_api/samples/retrieve/` (`identifiers`, `output_format: excel`, `include_tree`) | every download control, through `nsDownloadSamples` | `nextseek_api/services/sample_retrieve.py: SampleRetrieveViewSet`; `/nextseek_api/admin/samples/retrieve/` is a deprecated alias of the same handler |
| POST `/seek/samples/delete/` (`allids` or `alluids`) | delete buttons | `views/samples.py: sampleDelete` (POST only, login) |
| POST `/seek/samples/export/`, then GET `/seek/exports/<token>/<file>` | "Export samples to Import", Simple toolbar only | `views/samples.py: sampleExport`; `views/exports.py` serves per-user files |
| GET `/seek/sample_timeline/<uid>/` | "View Timeline", Simple toolbar (client only allows UIDs starting `NHP` and containing `FLY`) | `sample_timeline` TemplateView in `seek/urls.py` (security item SEC-0930-G, tracked privately) |
| POST `/nextseek_api/samples/advanced_search/` | `/seek/newsearch/` only | `nextseek_api` (older engine) |
| GET/POST `/nextseek_api/data_files/`, `/sops/`, `.../download/` | Data File and SOP Query | `nextseek_api` services |

### Download flows

All sample downloads go through `nsDownloadSamples(uids, {includeTree, filename})` in
`static/js/ns_sample_download.js` (constant `ENDPOINT`). It shows a progress box, POSTs, saves the
returned blob, and alerts on failure. The file also exports `nsCollectSelectedUids` and
`nsExtractUid` on `window`. The page template loads it with `{{STATIC_URL}}js/ns_sample_download.js`.

| Control | Include associated samples |
|---|---|
| Simple tab "Download samples" | asks the user ("Include all associated samples?") |
| Advanced tab download | always |
| Sample Retrieval tab | always |
| Sample detail page "Download All Samples" | always (`pages/samples.embed.html`; see [upload-and-samples.md](upload-and-samples.md)) |
| `/seek/newsearch/` grids | no (`includeTree:false`) |

Other exports: "Export samples to Import" (ImmPort workbook, Simple toolbar, link file with a
24 hour lifetime), Data File and SOP "Download selected" (a file or a `<date>-data-files.zip`
blob), and the Nessie search-results artifact (chat side).

### Grids

| Grid | Where | Paging | Filter row |
|---|---|---|---|
| `#simple_dgtable`, `#advanced_dgtable` | `/seek/search/` | separate pager, server pages | text filter over loaded rows; no column chooser |
| `#dgtable` | Data File and SOP Query | `pagination:true`, `pageSize:50`, all records loaded once | `id` only |
| `#dgtable` | `/seek/samples/query/` | none, loads `/seek/retrieve/samples/` | `id` only |
| `#simple_dgtable`, `#advanced_dgtable` | `/seek/newsearch/` | client-side only | none |

Grid helpers (`nsEllipsisFormatter`, `nsEnableColumnFilters`, `nsResetSearch`) are in
`static/js/custom/datagrid-custom.js`. `datagrid-export.js` is loaded but no rendered page calls an
export function. The phone results are a hand-built `ul.m-results`, not a grid.

### Phone form

Under 768px only the `.m-only` card shows: a keyword box and a type `<select>`, which run
`runMobileSearch` in `searchAdvanced.html`. A keyword is required (an empty one silently does
nothing). Results are capped at 100 (`MOBILE_RENDER_CAP`) with a "refine search" hint, link to
`/seek/sampletree/uid=<uid>/`, and have no paging, selection, download or delete. Attribute, lineage
and boolean search are desktop only. Data File, SOP, old Sample Query and `/seek/newsearch/` have no
phone layout at all (fixed-height EasyUI tabs and layouts).

### Dead or orphan search templates

None of these are rendered by a live view. Full list and removal notes are in [legacy.md](legacy.md).

| Files (`seek/templates/`) | Why dead |
|---|---|
| `sampleSearch.html` | view only redirects |
| `batchSearch.html`, `pages/batchSearch_*` | no view; `batchSearch_table` is a copy of the SOP grid |
| `publish.html`, `publishAssets.html`, `pages/publish*_*` | no view; their endpoints have no route |
| `sampleDeletion.html` | no view |
| `pages/searchAdvanced_retrieval`, `_rtable`, `_tree` | not included anywhere (but see Gotchas: `retriveAdvanced`) |
| `pages/samples_query` | calls the old `/seek/samples/searching/` |
| `pages/*.bk`, `pages/dmac.logs` | backup and stray files |
| old engine routes `/seek/searchAdvanced/`, `/seek/searchUIDs/`, `/seek/samples/searching/` | still routed, no live caller |

The repo-root `templates/search_results.html` is Mezzanine's stock template and is not in
`TEMPLATES["DIRS"]` (`dmac/settings.py`), so it is never used from this repo. Mezzanine's own
site search, `/search/`, answers 404.

## Where to edit

| Task | Files and symbols | Easy to miss |
|---|---|---|
| Change the nav link to Sample Search | `themes/NextSeek/templates/nav.embed.html` (`href="/seek/search/"`) | Data Query submenu is in the same file |
| Change tabs, page size, pager, ALL loop or phone form | `seek/templates/searchAdvanced.html` | The phone form is in the `.m-only` block at the bottom |
| Change what a box sends to `graph_search`, or how rows look | `seek/templates/pages/sampleSearch_core.embed.html` (`SampleSearchCore`) | Keep it free of DOM code; it is tested by `seek/tests/js/sample_search_cases.js` |
| Simple tab form, grid or toolbar | `pages/samples_search.embed.html`, `pages/samples_stable.embed.html` | The Simple toolbar functions are prefixed `simple_` |
| Advanced tab form, grid or toolbar | `pages/searchAdvanced_search.embed.html`, `pages/searchAdvanced_stable.embed.html` | Global function names collide with the deletion tab (Gotchas) |
| Delete-by-UID tab | `pages/searchAdvanced_deletion.embed.html` | Posts to `/seek/samples/delete/` (`views/samples.py: sampleDelete`) |
| Change a download (request, filename, progress) | `static/js/ns_sample_download.js` | Workbook content is server side: `nextseek_api/services/sample_retrieve.py`; see [sample-download-workflow.md](../sample-download-workflow.md) |
| Add a download button to a grid | call `nsDownloadSamples(nsCollectSelectedUids(grid), {includeTree: ...})` | Do not write a new fetch; a guard test (`nextseek_api/tests/test_download_call_sites.py`) tracks call sites |
| Change the type list or login handling | `seek/views/search.py: searchAdvanced`, `_with_names` | `type_options` is a JSON string, the phone list needs `type_option_list` |
| Change the graph_search engine | `nextseek_api/services/graph_search.py`, `nextseek_api/graph_search/` | Backend, not UI |
| Search layout CSS, mobile switch | `themes/NextSeek/static/css/nextseek.css`: `.d-only`, `.m-only`, `#search_tab`, `.m-results`, `.ns-ellip` | `#search_tab` overrides also hit Data File and SOP Query |
| Filter row, ellipsis, reset helpers | `static/js/custom/datagrid-custom.js` | Loaded twice on the search page (Gotchas) |
| Data File or SOP Query | `views/assets.py`, `dataFilesPage.html`, `sopsPage.html`, `pages/datafile_table.embed.html`, `pages/sops_table.embed.html` | The two table embeds are near copies: edit both |

## Gotchas

- Global function collisions. Every embed on `/seek/search/` defines plain global functions, and a
  later include silently replaces an earlier function of the same name. The grids prefix theirs
  (`simple_`, `advanced_`), and `test_no_two_scripts_on_the_page_define_the_same_function` in
  `seek/tests/test_sample_search_page.py` fails on a new clash. See the collision list in
  [javascript.md](javascript.md).
- "Send to Sample Retrieval" (Advanced toolbar) collects the ticked UIDs with
  `nsCollectSelectedUids`, switches to the Retrieval tab and fills `#retrieval_uids`; the user then
  presses Retrieve Samples.
- `datafile_table.embed.html` and `sops_table.embed.html` are near copies; a toolbar button must call
  the function its own embed defines (`downloadDataFiles`, `downloadSops`).
- Tab query string: in `searchAdvanced.html` the ready handler maps `new-retrieve` to index 3 (which
  is now Sample Deletion) and `delete` to index 4 (no such tab). Only `simple`, `advanced` and
  `retrieve` behave.
- The Advanced grid's filter row lists field `uuid`, but the column field is `uid`, so the UID
  column has no filter box. Duplicate element ids (`north_div`, `center_div`, `north_div2`) exist
  across the four tabs.
- EasyUI sizing: the Advanced grid is built while its tab is hidden, so it needs
  `nsResizeGridsIn` on tab select plus the `#search_tab .layout-panel-center { display:block !important }`
  rule in `nextseek.css`. Neither works alone. The `#search_tab` id is reused by
  `dataFilesPage.html`, `sopsPage.html` and `newSearch.html`, so they inherit the same CSS.
- `datagrid-filter.js`, `datagrid-export.js` and `datagrid-custom.js` are loaded by both
  `searchAdvanced.html` and `samples_stable.embed.html`, so they load twice. `datagrid-filter.js` is
  deliberately not in `base.html`; a page that needs it must load it itself.
- `views/assets.py` keeps `report` as a module-level dict mutated per request. Build a local dict
  if you touch it.
- `sampleQuery.html` places an `{% include %}` outside any block in a child template; Django drops it.
- Wide columns (Attribute:Value is 900px) and native `title` tooltips for full cell text are
  desktop-only affordances; nothing on touch reveals the truncated text.
- Static files: `ns_sample_download.js` and `datagrid-custom.js` live in the repo-root `static/js/`,
  which is baked into the image, so a change needs `./startup.sh rebuild` (the new container collects
  static at start; see [ci-and-deploy.md](ci-and-deploy.md)). The templates load them by an unhashed
  `{{STATIC_URL}}` path, so browsers can keep an old copy for up to 30 days: hard-reload.

## Known issues

See [known-issues.md#search-and-downloads](known-issues.md#search-and-downloads). The ones that
matter most:

- The phone form cannot search without a keyword and offers no download, and the filter row and
  select-all act only on the loaded page, not the full total.
