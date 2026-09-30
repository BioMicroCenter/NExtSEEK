# Projects, catalogs and graphs

## What this covers

The pages that describe what a project holds and what the schema looks like, and every graph or
tree drawing on the site:

- The projects list and the project page (stat cards, the "Sample flow" panel and its Fullscreen
  control, templates for the project, sample types in use), plus the project sample-counts modal.
- The read-only catalogs: sample types (list and detail), assays (list and detail), and the shared
  `catalog_table` and `attribute_definitions_table` includes with `catalog_table_filter.js`.
- The two superuser workbenches for clades and internal assays, and the SOPs and data files pages.
- The graph code: the Sample flow page (Cytoscape), sample tree v2 (d3-dag), the sample timeline
  bundle, the static SVG renderers, and the dead older trees.

It does not cover the sidebar, header and modal plumbing in general ([shell.md](shell.md)), the
sample page that hosts the tree tab ([upload-and-samples.md](upload-and-samples.md)), upload and search ([upload-and-samples.md](upload-and-samples.md),
[search-and-downloads.md](search-and-downloads.md)), or theme tokens ([styles.md](styles.md)).
Dead files are summarised here and listed in [legacy.md](legacy.md).

## How it works

All of these pages are Django templates in `seek/templates/` served by views in `seek/views/`
under the `/seek/` prefix (routes in `seek/urls.py`). Project pages and catalogs are server
rendered with a little inline script. Clades, internal assays, SOPs and data files are still
easyui datagrid pages (see [legacy.md](legacy.md)).

The catalog URLs are `/seek/sampletypes/` and `/seek/assays/` (plural, no underscore). The
underscored `/seek/sample_types/id=<n>/` is a different page (samples of one type). The catalog
rows come from the curated context (`nextseek_api.services.context_catalog`, `load_sample_types`),
not from SEEK; the counts beside them come from `nextseek_api.services.catalog_counts`, which
queries SEEK's tables.

### The Sample flow panel, step by step

```
projectPage.html  ->  <iframe class="project-diagram" src=".../connections/">
                          |
   seek.views.projects.project_connections   (login + membership check, frame allowed same-origin)
                          |
   nextseek_api.services.project_connections.connections_html   (cache, then connection_rows -> Neo4j)
                          |
   nextseek_api.services.sampletype_connections.rows_to_html    (returns a WHOLE html document string)
                          |
   browser: loads cytoscape, dagre, cytoscape-dagre from unpkg, draws into <div id="cy">
```

The project page does not draw anything itself. The "Sample flow" section of
`seek/templates/projectPage.html` holds an `<iframe class="project-diagram" loading="lazy">` whose
`src` is `/seek/projects/<id>/connections/`. The view `project_connections` is decorated with
`@xframe_options_sameorigin` (without it the browser refuses the frame and the page shows a broken
icon while the server logs a clean 200) and `@requires_seek_login_redirect()`, then checks
project membership. `connections_html` caches the rendered document (key `stconn:html:<project
id>`, TTL `PROJECT_CONNECTIONS_CACHE_SECONDS`, default one hour). An empty result is not cached,
because zero rows can also mean Neo4j was briefly unreachable. With no rows the view returns a
small 200 page saying no connections are recorded.

The "Fullscreen" link in the same section has `data-modal-route data-modal-iframe`. The click
handler near the "Modal-over-route" comment in `themes/NextSeek/static/js/nextseek.js` builds an
overlay (`.modal-route-overlay` > `.modal-route-panel` > `.modal-route-body`), puts a second iframe
with the same URL inside it, and `history.pushState`s that URL into the address bar. Escape, the
close button, a click on the backdrop, or Back closes it. The overlay CSS is in
`themes/NextSeek/static/css/nextseek.css` under "Modal-over-route overlay": the panel is
`min(1200px, 95vw)` by `90vh`, and `.modal-route-panel:has(.modal-route-iframe)` turns off
panel scrolling and body padding so the iframe fills it.

### How the frame document is sized and laid out

Everything below is inside the string returned by `rows_to_html` in
`nextseek_api/services/sampletype_connections.py` (the `<style>` block and the script at the end).

**Outer size.** On the project page the iframe is `width:100%; height:460px` (selector
`.project-diagram` in the inline `<style>` of `projectPage.html`). In the fullscreen overlay it is
`100%` by `100%` of the panel (`.modal-route-iframe`). There is no `scrolling` attribute on
either iframe.

**Inside the frame**, the document is laid out like this:

| Part | How it is placed |
|---|---|
| `html, body` | `margin:0; height:100%`. No `overflow:hidden`, so the frame document can scroll. No `<meta name="viewport">`. |
| `<header>` | A normal in-flow flex row (`display:flex; flex-wrap:wrap; gap:16px; padding:10px 18px; border-bottom`). Holds the title, "N sample types, M connections", the clade legend, and the hint text. Its height is not fixed: it is about 43 px on a wide frame and grows by about 34 px for each wrapped line on narrow ones. |
| `#cy` | `position:absolute; top:52px; bottom:0; left:0; right:0`. The 52 px is a hard-coded guess at the header height, not measured. Cytoscape reads this element's box. |
| `#dp` (detail box) | `position:absolute; right:14px; top:66px; width:250px`, hidden until a node or edge is tapped. |

Two consequences follow from this. First, when the header wraps past 52 px (narrow frames), its
second line sits under the canvas, because `#cy` paints later, so the legend and hint are cut off.
Second, nothing prevents the frame document from scrolling: a one pixel rounding overflow (likely
at fractional browser zoom) shows scrollbars.

**Resize handling** (script at the end of `rows_to_html`):

- Cytoscape is created with `pixelRatio:2`, so its canvas backing store is twice the box size in
  each direction.
- A `matchMedia('(resolution: <devicePixelRatio>dppx)')` watcher fires when browser zoom changes
  the device pixel ratio, calls `cy.resize()`, and re-arms itself at the new ratio.
- A plain `window` `resize` listener also calls `cy.resize()`, with no debounce and no check that
  the box really changed.

The suspected flicker: a scrollbar appears, the viewport narrows, `resize` fires, `cy.resize()`
re-rasterises the 2x canvas, and the scroll size changes for a frame. This chain was not observed
in headless Chromium at device pixel ratio 1 (scroll size matched client size from 320 to 980 px
wide); the ingredients are all in the code, and it was seen in a desktop browser on project "NAMs" (UI-081). The safe
fix is to make scrolling impossible and the layout measured instead of guessed:

1. `html,body{overflow:hidden}` (Cytoscape has its own pan and zoom, the page never needs to scroll).
2. `body{display:flex;flex-direction:column}`, `header{flex:0 0 auto}`, `#cy{flex:1 1 auto;min-height:0;position:relative}`
   in place of the absolute `top:52px`; position `#dp` relative to `#cy`.
3. Replace the `window` listener with one `ResizeObserver` on `#cy` that skips unchanged sizes, then
   `cy.resize(); cy.fit(undefined, 30)`. Keep the device pixel ratio watcher.
4. Optionally `scrolling="no"` on the iframe and a height such as `clamp(320px, 60vh, 460px)` in
   `.project-diagram` for phones.

Test at browser zoom 90, 110 and 125 percent and at device pixel ratio 2.

### How edge labels are placed, and why they run under nodes

The layout is `dagre` with `rankDir:'TB'`, `rankSep:85`, `nodeSep:45`, `edgeSep:18`, `padding:30`.
Nodes are 96 by 62 px, so the visible gap between two ranks is about 85 px. The edge style sets
`label:'data(label)'`, `font-size:10px`, `text-rotation:'autorotate'`, a white label background
with 3 px padding, and `text-margin-y:-9px`. Autorotate turns the label to lie along the edge,
which on a top-to-bottom layout is vertical. A long assay name such as "Immunohistochemistry"
(about 105 px at 10 px type) is longer than the 85 px gap, so its ends run beneath the two nodes
(Cytoscape draws node bodies over edge labels by z-order, and cytoscape-dagre does not reserve
room for labels). The label text is built in `rows_to_html` as the first assay name sorted
alphabetically, plus " +N" when the pair has more than one assay, so it is at most one name.
Loop edges (`edge:loop`) already use `text-rotation:'none'`.

Fix options, all in the `style` and `layout` objects of the same function: `text-rotation:'none'`
with `text-wrap:'wrap'` and `text-max-width:'80px'`; a larger `rankSep` (about 140) and `nodeSep`;
or scale `rankSep` with the longest label, computed in Python where `edges` is built
(for example `max(85, 6.2 * longest + 40)`).

### Colours and shapes

`CLADE_STYLES` in `sampletype_connections.py` maps clade to fill and Cytoscape shape. It mirrors
the curation tree template (`curation_skill/templates/SAMPLE_TREE.html.j2`) on purpose.

| Clade | Fill | Shape |
|---|---|---|
| Source | `#2E7D32` | ellipse |
| Processed | `#E65100` | round-rectangle |
| Raw | `#42A5F5` | diamond |
| Analyzed | `#1565C0` | hexagon |
| Unassigned or unknown | `UNASSIGNED_COLOR` | ellipse |

Clades come from `fetch_clade_map` (the tables the clades workbench edits). The legend lists only
clades present in the graph. These diagram colours are separate from the lighter catalog tints
(`--ns-clade-source` and its siblings in the theme CSS, used by `.clade-accent--*` on the catalog
tables); the two palettes are not linked.

## Inventory

### Pages

All routes are under `/seek/`. "Decorator" means `requires_seek_login_redirect` unless noted.

| Page | Route | View | Template | Notes |
|---|---|---|---|---|
| Projects list | `projects/` | `views.projects:projects` | `projectsList.html` | Cards with counts plus a per-clade summary. All CSS is inline. FAIRDATA chip is a `<span onclick>` inside the card `<a>`. |
| Project page | `projects/<id>/` | `views.projects:project_page` | `projectPage.html` | Not decorated: checks login by hand and renders `error.html` for non-members. Stat cards use `.dash-tiles` / `.dash-tile` from the theme CSS. |
| Project sample counts | `projects/<id>/samples/` | `views.projects:project_samples` | `project_samples.html` | Counts per sample type, grouped by clade, from `_project_clade_data` (cached one hour, reads the `PUBLISH_STATS_FILE` spreadsheet). Opened as a modal from the "Total samples" card. |
| Sample flow frame | `projects/<id>/connections/` | `views.projects:project_connections` | none (Python string) | See above. |
| Sample types list | `sampletypes/` | `views.catalog:sampleTypesList` | `sampleTypesList.html` | Grouped by clade in pipeline order, live filter. |
| Sample type detail | `sampletypes/<code>/` | `views.catalog:sampleTypeDetail` | `sampleTypeDetail.html` | Lineage shown as chip lists (no diagram), attribute table, "Download template", "Search samples". 404 if there is no curated row or the row is marked deprecated. |
| Assays list | `assays/` | `views.catalog:assaysList` | `assaysList.html` | Grouped by the clade each assay consumes; Unassigned starts collapsed. |
| Assay detail | `assays/<slug>/` | `views.catalog:assayDetail` | `assayDetail.html` | No script. Shows two columns with a notice if a name appears twice in the assay context. |
| Clades workbench | `admin/clades/` | `views.admin:adminClades` | `clades.html` | Superuser (`requires_supervisor`). easyui datagrids `#dg_clade` and `#stc_dg`. |
| Internal assays workbench | `admin/internal_assays/` | `views.admin:internalAssays` | `internal_assays.html` | Superuser. easyui datagrids, loads `static/js/custom/ns-vocab-workbench.js`. |
| SOPs | `sop/query/` | `views.assets:sopQuery` | `sopsPage.html` | 14-line wrapper around `pages/sops_table.embed.html` inside easyui tabs and layout with pixel heights. |
| Data files | `datafile/query/` | `views.assets:datafileQuery` | `dataFilesPage.html` | Same wrapper around `pages/datafile_table.embed.html`. The project page "Data files" card links here unfiltered. |
| Sample timeline | `sample_timeline/<uid>/` | `TemplateView` in `seek/urls.py` | `sample_timeline.html` | Hosts the built React bundle. (security item SEC-0930-G, tracked privately) |

Admin pages not in this area but near it: the admin retrieve page (see [pages.md](pages.md)).

### Shared catalog includes (`themes/NextSeek/templates/includes/`)

| File | Role |
|---|---|
| `catalog_table.html` | Grouped table for both lists. Context: `columns`, `groups` (each with `clade`, `clade_slug`, `rows`, `collapsed`). Hooks: `[data-cat-filter]` (search box), `[data-cat-count]`, `[data-group]`, `[data-group-toggle]` (real `<button>`s), and `data-filter` on every row. No scroll wrapper around the table. |
| `catalog_table_filter.js` | A 42-line script, included inline with `<script>{% include "includes/catalog_table_filter.js" %}</script>` in `sampleTypesList.html` and `assaysList.html`. Filters rows by lowercase substring of `data-filter`, hides empty groups, recounts, toggles `.is-collapsed` on group heading click. Runs once at load. |
| `attribute_definitions_table.html` | Read-only 7-column attribute table (Pos, Name, Type, Req, Title, Definition, Constraints). Filled in the browser by an inline script that POSTs to `/nextseek_api/attributes/search/` with `credentials:'same-origin'`. It never checks the response status, so a failed request shows "No attribute definitions recorded" (UI-092). Escapes through a DOM `div`. |

Shared styles: `seek/templates/catalog_styles.html` (an include that puts a `<style>` block in the
page, also used by the detail pages) and the `.cat-*`, `.attrs-ro-table` and `.modal-route-*`
rules in `themes/NextSeek/static/css/nextseek.css`. `.ns-page-header` and `.ns-page-title` are
defined both in `catalog_styles.html` and in the inline CSS of `projectsList.html`.

### Graph and tree code

| Name | Where it shows | Library | Loaded from | Data | Status |
|---|---|---|---|---|---|
| Sample flow | Project page iframe and Fullscreen overlay | Cytoscape `@3`, dagre `@0.8`, cytoscape-dagre `@2` (major versions only) | unpkg CDN (SEC-0930-F) | Neo4j via `connection_rows`; clades via `fetch_clade_map` | Live |
| Static connection SVG | `/nextseek_api/sample_types/connections/` (API output) | none, hand-built strings: `rows_to_svg`, `rows_to_svg_radial`, `rows_to_svg_layered`, picked by `choose_layout` | server side | same rows | Live, API only |
| Sample tree v2 | "Sample Tree v2" tab on the sample page (`pages/samples.embed.html`, markup in `pages/samples_tree_new.embed.html`) | d3 7.8.4, d3-dag 1.1.0 (Sugiyama layout) | skypack CDN as ES modules | `d3.json("/nextseek_api/sample-tree/<id-or-uid>/tree")` | Live |
| Sample timeline | `/seek/sample_timeline/<uid>/` | React + MUI + timeline vendor chunk | committed Vite build in `static/js/sample_timeline/assets/js/` (hashed file names listed in `sample_timeline.html`) | `/seek/nhpinfo/`, `/seek/nhpdata/`, `/seek/eventdata/` | Live |
| Sample tree v1 | commented out in `pages/samples.embed.html` | d3 3.5.5, lodash 3.3.1 | cdnjs | older sample data | Dead (see Gotchas) |
| `static/js/buildtree/` | no template references it | d3 2.4.4 and 1.27.2 copies, jquery 1.6.2 | local | `flare.json` | Dead |
| `static/js/dag/d3neo4j.js` | no template references it | d3 7.9.0 | jsDelivr | Neo4j browser style | Dead |

Nothing is shared between these. The Sample flow and the sample tree use different libraries,
different loaders (script tags versus ES modules), different colour logic (`CLADE_STYLES` in
Python versus `d.data.color` from the API in `dag.js`) and different fullscreen code (the overlay
iframe in `nextseek.js` versus `#tree-fullscreen-btn` toggling `.tree-fullscreen` inline in
`samples_tree_new.embed.html`, styled by `#tree_container` rules in `nextseek.css`).

Sample tree v2 notes: `dag.js` takes the sample id or uid from `location.href` with a regular
expression, then awaits the tree JSON at load (top-level `await`). The markup shows "Loading..."
until it resolves, with no error handling, so a blocked CDN or failed API call leaves it on
"Loading..." forever. Node tooltips are hover only.

The libraries' versions for the rest of the site are in [javascript.md](javascript.md).

## Where to edit

| Task | Edit | Easy to miss |
|---|---|---|
| Change how the Sample flow looks (nodes, labels, legend, layout numbers, libraries) | `rows_to_html` in `nextseek_api/services/sampletype_connections.py` | The HTML is cached per project for an hour (`stconn:html:<id>`); clear the cache or wait. Doubled braces `{{ }}` are required inside the f-string. |
| Change clade colours or node shapes in the flow | `CLADE_STYLES` in the same module | Keep in step with the curation template it mirrors. Catalog tints are a separate set of CSS tokens. |
| Fix flicker or scrollbars in the flow | `<style>` and the resize block of `rows_to_html` (see the four steps above) | The project-page iframe (`.project-diagram` in `projectPage.html`) and the overlay iframe (`.modal-route-iframe`) load the same frame document; test both. |
| Fix labels under nodes | `layout` (`rankSep`, `nodeSep`) and the `edge` style in `rows_to_html` | cytoscape-dagre ignores label size. |
| Change cache lifetime or the empty-state message | `_ttl` and `connections_html` in `nextseek_api/services/project_connections.py`; the empty response in `project_connections` | Set `PROJECT_CONNECTIONS_CACHE_SECONDS` in settings for the lifetime. |
| Resize the flow panel on the project page | `.project-diagram` in the inline CSS of `seek/templates/projectPage.html` | Height is fixed at 460 px. |
| Change Fullscreen behaviour | The "Modal-over-route" block in `themes/NextSeek/static/js/nextseek.js`; `.modal-route-*` in `themes/NextSeek/static/css/nextseek.css` | The URL is pushed into the address bar; a reload there shows the bare diagram without site navigation. `nextseek.js` and `nextseek.css` are served from `themes/NextSeek/static/` (see [styles.md](styles.md)). |
| Change stat cards or sections on the project page | `seek/templates/projectPage.html`; data assembled in `project_page` in `seek/views/projects.py` | Each data source (graph rows, bundles, project context, counts) fails soft, so an empty section can mean a failed lookup, not no data. |
| Change the projects list cards | `seek/templates/projectsList.html` | CSS is inline in the template. |
| Change a catalog list table | `includes/catalog_table.html`, `includes/catalog_table_filter.js`, `.cat-*` in `nextseek.css`, view in `seek/views/catalog.py` | The filter script is a template include, so edits need no static collection, but it is not cacheable or minified. |
| Change a detail page | `sampleTypeDetail.html` or `assayDetail.html`; data in `nextseek_api/services/context_catalog.py` | Detail pages 404 when there is no curated row (or it is deprecated), even if SEEK has the type, yet the templates picker and the lineage chips still link such codes (UI-093). |
| Add a column to the attribute table | `includes/attribute_definitions_table.html` (header, row builder, and every `colspan="7"` status row) | The count 7 is hard-coded in the status rows. |
| Change the sample lineage tree | `static/js/dag/dag.js`; `pages/samples_tree_new.embed.html`; `#tree_container` rules in `nextseek.css` | `static/` is at the repo root (the second `STATICFILES_DIRS` entry), not in `themes/`, and is baked into the image: a change needs `./startup.sh rebuild`. |
| Add a page in this area | view in `seek/views/`, route in `seek/urls.py`, template in `seek/templates/` | Also declare the route for CI's route gate ([ci-and-deploy.md](ci-and-deploy.md)). |
| Change the clades or internal assays workbench | `clades.html`, `internal_assays.html`, `static/js/custom/ns-vocab-workbench.js`, views in `seek/views/admin.py` | Both pages are easyui datagrids with fixed pixel heights and no desktop-only notice. |

## Gotchas

- **The flow is an iframe, so page CSS does not reach it.** Theme tokens, dark mode and fonts
  in `nextseek.css` do nothing inside the frame; it has its own hard-coded colours and a
  system-font stack.
- **Both the sample flow and sample tree v2 need a public CDN at page load** (unpkg, skypack). A
  blocked or offline CDN leaves an empty panel with no message. Third-party script loading is
  security item SEC-0930-F, tracked privately.
- **Modals take only `#content`.** A `data-modal-route` link without `data-modal-iframe` fetches
  the target page, parses it, and injects `#content` (or `main`, or `body`). Page-level `<style>`
  from `extra_head` is not carried across, which is why the sample-counts table CSS lives in
  `nextseek.css` and not in `project_samples.html`.
- **Django drops content outside blocks in a child template.** `admin_retrieval.html`, `clades.html`
  and `internal_assays.html` each have a top-level `pages/seek_includes.html` include that never
  renders; `projectsList.html` loads the `index` tag library
  and does not use it. Harmless, but do not copy either.
- **Sample tree v1 still ships its bytes.** `pages/samples.embed.html` includes
  `pages/samples_tree.embed.html` twice, each inside an HTML comment. Django runs `{% include %}`
  even inside `<!-- -->`, so the markup (about 520 lines with four CDN tags) is served on every
  sample page and never executes.
- **`publish.html` and `publishAssets.html` have no route and no view that renders them.** Nothing
  reaches them; do not edit them expecting a visible change.
- **Two "clade" concepts.** The clades workbench edits the clade tables; the flow diagram, the
  catalogs and the project sample counts all read them. Changing a clade name or assignment shows
  up in the flow only after the cached HTML expires.
- **`sopQuery` and `datafileQuery` write to a module-level `report` dict** shared between
  requests (`seek/views/assets.py`). Build a fresh dict if you touch them.
- **The sample timeline is a built bundle.** Editing `sample_timeline.html` does not change the
  app; the source is elsewhere and the hashed files in `static/js/sample_timeline/assets/` are
  committed output. Changing the bundle means replacing those files and their names in the
  template.
- **Catalog tables have no `overflow-x` wrapper**, so three to seven columns overflow sideways on a
  phone. The two admin workbenches and the SOP and data file pages are desktop-only in practice.

## Known issues

See [known-issues.md](known-issues.md#projects-catalogs-graphs). The ones that matter most here:

- The Sample flow frame can show flickering scrollbars and clips its header on narrow widths
  (no `overflow:hidden`, a fixed 52 px offset for `#cy`, unthrottled resize handlers).
- Edge labels on the Sample flow run under nodes (`autorotate` with `rankSep:85`).
- Sample flow and sample tree v2 load their libraries from public CDNs with no fallback message;
  catalog tables overflow on phones.
