# Dead and legacy UI

## What this covers

This page lists the UI files that look editable but are not (or no longer) what users see: templates that nothing renders, backup copies, template folders that Django never searches, Mezzanine features that are switched on or off, duplicated vendored assets, unreferenced images, and view functions that point at missing templates. Read it before editing any template or static file you did not find through a live route, so you do not spend an hour changing a file that has no effect.

It does not cover the live templates and how they are wired (see [shell.md](shell.md) and [pages.md](pages.md)), the live static assets (see [styles.md](styles.md) and [javascript.md](javascript.md)), or the deploy and CI rules for deleting things (see [ci-and-deploy.md](ci-and-deploy.md)). Every problem is tracked in [known-issues.md](known-issues.md); this page only says where each one lives.

State of the tree: branch `feat/ui-docs-2609`, origin/dev `caa55340`, checked 2026-09-30. Nothing here was deleted; this is an inventory for a cleanup pass.

## How it works

### Which template folders Django searches

`TEMPLATES` in `dmac/settings.py` has one `DIRS` entry, `themes/NextSeek/templates`, then three loaders in this order: `mezzanine.template.loaders.host_themes.Loader`, the filesystem loader (which reads `DIRS`), and the app-directories loader. The app-directories loader only sees a `templates/` folder inside an app listed in `INSTALLED_APPS`. So a template name resolves, in order, to:

1. `themes/NextSeek/templates/` (the theme),
2. `templates/` inside an installed app: `seek/templates/`, the theme again (`themes.NextSeek` is itself an installed app), and every Mezzanine package (`mezzanine.core`, `blog`, `pages`, `accounts`, and so on).

```
request -> view -> render("x.html")
                      |
        themes/NextSeek/templates/x.html   (DIRS, wins)
                      |  not found
        <installed app>/templates/x.html   (seek, mezzanine.*, ...)
                      |  not found
        TemplateDoesNotExist -> HTTP 500
```

Two folders are on neither list, so nothing in them can ever render: the repo-root `templates/` (81 files, a copy of the Mezzanine scaffold) and `dmac/templates/` (3 files; `dmac` is the project package and is not in `INSTALLED_APPS`). `themes/README.md` already warns about the root folder.

### How "dead" was established

For the templates under `seek/templates` and `themes/NextSeek/templates`, reachability was computed in two steps:

1. Seeds: every template-name string literal that appears in `seek/views/*.py`, `dmac/views.py` and `nextseek_api` (render calls, `TemplateView(template_name=...)` in `seek/urls.py`). This over-counts: a name in a commented-out `render` still counts as a seed (`sampleSearch.html` is one, see below), so each seed was checked by eye.
2. Walk: follow every `{% include "..." %}` and `{% extends "..." %}` from the seeds. Every include and extends argument in these two folders is a string literal (no variable template names), so the walk is complete for templates. A template that is never reached is listed as unreachable.

Limits of the method: it cannot see a template name built in Python at run time (none were found), and for static files it only searches names, so a URL assembled in JavaScript would be missed. That is why static candidates are marked "likely", not "certain". The rerun checks are at the end of this page.

### Live duplicates that are not dead

Several old and new variants are both live. Do not delete a file only because a newer twin exists.

| Old | New | Status |
|---|---|---|
| `searchAdvanced.html` and its embeds (`samples_search`, `samples_stable`, `searchAdvanced_search`, `searchAdvanced_stable`, `searchAdvanced_deletion`) | `newSearch.html` and its embeds (`samples_newsearch`, `samples_new_stable`, `searchAdvanced_newsearch`, `searchAdvanced_new_stable`, `searchAdvanced_newretrieval`, `searchAdvanced_newdeletion`) | Both live. `/seek/search/` renders the old set, `/seek/newsearch/` the new one (`seek/views/search.py`, routes in `seek/urls.py`). No theme or seek template links to `/seek/newsearch/`; it is reachable by typing the URL. `nextseek_api/tests/test_download_call_sites.py` lists some templates of each set in `LIVE`. |
| `pages/samples_tree.embed.html` (Sample Tree v1) | `pages/samples_tree_new.embed.html` (v2, loads `static/js/dag/dag.js`) | v2 is the visible tab. v1 is still executed, see the next section. |
| `sampleUpload.html` with `pages/samples_upload.embed.html` | `batchUpload.html` with `pages/batch_upload.embed.html` | `/seek/samples/upload/` (URL name `sampleUpload`) renders `batchUpload.html` through the `batchUpload` view (`seek/views/upload.py`). The old pair is dead. |
| `pages/searchAdvanced_retrieval.embed.html` | `pages/searchAdvanced_newretrieval.embed.html` | Only the new one is included (`newSearch.html`). The old one is dead. |
| `sampleSearch.html` | `searchAdvanced.html` | `sampleSearch` in `seek/views/search.py` now redirects to `/seek/search/`; its `render` line is commented out. `sampleSearch.html` is dead, but `pages/samples_search.embed.html` (which it includes) is live through `searchAdvanced.html`. |

### The v1 sample tree is commented out but still runs

`seek/templates/pages/samples.embed.html` wraps the Sample Tree v1 tab in an HTML comment (`<!-- ... -->`) and, further down, has a second `<!-- {% include "pages/samples_tree.embed.html" %} -->`. Django does not know about HTML comments: it executes both `{% include %}` tags and emits the whole v1 template (261 lines, starting with two CDN script tags) into the page source twice, where the browser then hides it. Every sample detail page therefore carries about 520 lines of v1 markup that nobody sees. Only the "Sample Tree v2" tab is live.

To really disable it, use `{# ... #}` or `{% comment %}` around the includes, or delete the includes and then `pages/samples_tree.embed.html`. Until then `samples_tree.embed.html` is not safe to delete: it is reached by those two includes.

## Inventory

### Backup, scratch and pasted files

| Path | What it is | Evidence | Confidence |
|---|---|---|---|
| `seek/templates/pages/samples_stable.embed.html.bk` | Older copy of the live `samples_stable.embed.html`; content differs | In no include. Named as an orphan in `nextseek_api/tests/test_download_call_sites.py` (`ORPHANS`), and that test asserts the file exists | certain (but see Gotchas) |
| `seek/templates/pages/samples_tree.embed.html.bk` | Older copy of `samples_tree.embed.html` | Zero references in `.py` or `.html` | certain |
| `static/js/sample_timeline.bk/` | An old Vite build of the timeline app (`index.html`, `assets/index-*.css`, `assets/index-*.js`, favicon, manifest, robots) | `seek/templates/sample_timeline.html` loads only `js/sample_timeline/assets/js/*` and `assets/asset/style-*.css`. The folder is tracked in git and published by collectstatic | certain |
| `static/js/sample_timeline/` leftovers: `assets/index-*.css`, `assets/index-*.js`, `favicon.ico`, `index.html`, `manifest.json`, `robots.txt`, and the nested `sample_timeline/sample_timeline/` copy | Files from an older build layout next to the current `assets/js/` and `assets/asset/` | The template names only the `assets/js/*` and `assets/asset/*` files | likely |
| `seek/templates/samplesTest.html` | Scratch copy of `samples.html` (same 4 lines: extends base, includes `pages/samples.embed.html`) | No view, url or include names it | certain |
| `seek/templates/pages/dmac.logs` | A pasted cron traceback (a `django_crontab` error for `seek.cron_job.my_cron_job`) with server paths, sitting in the template tree | Not a template; nothing reads it | certain |
| `static/test/image.jpg` | A test image, published under `/static/test/` | No reference found | likely |
| `themes/NextSeek/static/img/cover-bak.png`, `cover - Copy.png`, `fairdata_logo01-300x52-bak.png`, `-test.png`, `-v2.png` | Backup or variant images (one has a space in its name) | No name hit in any template, CSS or JS | certain |
| `dmac/conversion.pyc`, `dmac/__init__.pyc` | Stale bytecode tracked in git next to the sources | `git ls-files dmac` lists them | check-first (not UI) |

### Templates that nothing reaches

All of these are under `seek/templates/` unless noted. "Parent dead" means the only file that includes it is itself unreachable.

| Template | Why unreachable |
|---|---|
| `samplesTest.html`, `sampleUpload.html`, `sampleDeletion.html`, `batchSearch.html`, `publish.html`, `publishAssets.html` | No view renders them and nothing includes or extends them |
| `sampleSearch.html` | Its only render in `seek/views/search.py` is commented out |
| `pages/404.html`, `pages/denied.html` | `handler404` in `dmac/urls.py` is `mezzanine.core.views.page_not_found`; neither name appears in any `.py` or `.html` |
| `content.embed.html` | Includes `content-title.embed.html`, which does not exist anywhere. Shadowed by the theme copy of the same name |
| `themes/NextSeek/templates/content.embed.html` | The old home fragment with an inline `<style>` block. Home renders `index.html` (`home` in `dmac/views.py`). Nothing includes this file |
| `pages/batchSearch_table.embed.html` | Parent `batchSearch.html` is dead |
| `pages/batchSearch_query.embed.html`, `batchSearch_search.embed.html` | Nothing includes them (`batchSearch.html` includes only `_table`, and it is dead itself) |
| `pages/seek_includes.html` | Six templates include it, but every include sits between `{% extends %}` and the first `{% block %}`, so Django drops it and it never renders |
| `pages/publish_search.embed.html`, `publish_stable.embed.html` | Parent `publish.html` is dead |
| `pages/publishAssets_search.embed.html`, `publishAssets_stable.embed.html` | Parent `publishAssets.html` is dead |
| `pages/samples_upload.embed.html` | Parent `sampleUpload.html` is dead |
| `pages/samples_query.embed.html` | `sampleQuery.html` includes `samples_table.embed.html` instead |
| `pages/datafile_upload.embed.html` | `dataFileUpload.html` includes nothing |
| `pages/searchAdvanced_retrieval.embed.html` | Replaced by `searchAdvanced_newretrieval.embed.html` |
| `pages/searchAdvanced_rtable.embed.html`, `pages/searchAdvanced_tree.embed.html` | Nothing includes them. Both are named orphans in the test |
| `themes/NextSeek/templates/pages/menus/tree.html` | Only used if a theme template calls Mezzanine's `page_menu`; none does (navigation is hand-written in `nav.embed.html`) |
| `dmac/templates/pages/datagrid_custom_table.embed.html`, `dialog_custom_upload.embed.html`, `login.embed.html` | `dmac/templates` is not on the loader path |

Not dead, despite looking like leftovers: `pages/sampleSearch_core.embed.html` (included by `searchAdvanced.html`), `pages/samples_search.embed.html` (see above), `themes/NextSeek/templates/accounts/includes/user_panel.html` (reached from the theme's `includes/user_panel.html`), and `themes/NextSeek/templates/includes/catalog_table_filter.js` (included by the assays and sample types list pages).

### Repo-root `templates/` (81 files, 69 `.html`)

A stock Mezzanine scaffold. None of it resolves: the folder is not in `DIRS` and is not inside an installed app. Whatever renders for these names comes from the Mezzanine package, so deleting the folder changes nothing users see (a live check on 2026-09-30 confirmed `/search/` and `/accounts/login/` answer from package templates).

| Subfolder | Files | Note |
|---|---|---|
| `templates/accounts/` | 8 | Package copies render instead |
| `templates/blog/` | 3 | The blog itself is live, see below; these copies are not what it uses |
| `templates/email/` | 20 | Account emails come from the Mezzanine package versions |
| `templates/errors/` | 2 | |
| `templates/generic/` | 7 | |
| `templates/includes/` | 10 | |
| `templates/mobile/` | 14 | Mezzanine 6 (locked in `uv.lock`) has no device-template switching, so `mobile/` is never requested |
| `templates/pages/` | 13 | Includes 8 menu templates. The theme overrides only `pages/menus/tree.html` |
| `templates/twitter/` | 1 | `mezzanine.twitter` is commented out in `INSTALLED_APPS` |
| `templates/base.html`, `index.html`, `search_results.html` | 3 | The real base is `themes/NextSeek/templates/base.html` |

The root `base.html` is also the only file that loads the stock Bootstrap and `mezzanine.css` files under `static/` (see unreferenced static below).

### Mezzanine features: live, dead, unknown

`INSTALLED_APPS` in `dmac/settings.py` has `mezzanine.boot, conf, core, generic, pages, blog, forms, galleries, accounts`. `mezzanine.twitter` is commented out. `mezzanine.urls` is included in `dmac/urls.py` as a catch-all `^` pattern; only the unreachable `accounts/login/` entry follows it.

| Feature | State | Notes |
|---|---|---|
| Blog (`mezzanine.blog`) | Live but unlinked | A live check on 2026-09-30 saw `/blog/` and `/blog/feeds/rss/` answer 200 on dev, rendered by the package templates inside the theme `base.html`. No theme nav link. Whether the blog has any content was not checked. Tracked with Mezzanine's own url includes (security item SEC-0930-H, tracked privately) |
| Pages, forms, galleries | Installed, no theme templates | Only a Page object created in Mezzanine admin would render, using package templates |
| Accounts (`mezzanine.accounts`) | Live | `accounts/signup/` is routed to `signup_seek` before the catch-all (`dmac/urls.py`). The project's `accounts/login/` route sits after the catch-all and never matches, so Mezzanine's own login view answers there (UI-026), as do the other Mezzanine account URLs |
| Twitter | Dead | App disabled; `templates/twitter/` has no effect |
| Mobile templates | Dead | See the table above |

Removing `mezzanine.blog` from `INSTALLED_APPS` is a check-first item: it has migrations and model tables, and the catch-all include may also be wired to other Mezzanine apps. Ask the operator before removing any installed app.

### Vendored EasyUI: two copies and public demo folders

| Item | Count |
|---|---|
| `themes/NextSeek/static/jquery-easyui-1.5.2/` | 797 files |
| `static/jquery-easyui-1.5.2/` | 797 files, `diff -rq` reports it identical to the theme copy; tracked in git |
| `demo/` inside it | 288 files (261 `.html`) |
| `demo-mobile/` inside it | 51 files |

`STATICFILES_DIRS` lists both `/app/themes/NextSeek/static` and `/app/static`, `STATIC_ROOT` is `/static`, and nginx serves `/static/` straight from that folder (`location /static/` in `docker/nginx.conf`). collectstatic therefore merges the two copies into one published tree that includes both demo folders. That is security item SEC-0930-E, tracked privately; do not add notes about it to this page.

The live app uses only `jquery.min.js`, `jquery.easyui.min.js`, the default theme's `easyui.css` and the shared `icon.css` from this tree (`themes/NextSeek/templates/base.html`), plus the `locale/`, `src/` and `plugins/` folders if a page loads them. No template, Python file or CSS refers to `demo/` or `demo-mobile/`. The rest of the vendored tree must stay.

Separately, `themes/NextSeek/static/js/easyui/` holds EasyUI plugin files. Templates load only `datagrid-filter.js`, `datagrid-export.js` and `datagrid-detailview.js` from it. The other files there (`datagrid-cellediting.js` and `.html`, `datagrid-export.html`, `datagrid-export-pdf.html`, `datagrid-filter.html`, `datagrid-groupview.js`, `filter.png`) are unreferenced; the `.html` ones are plugin documentation pages that get published.

### Unreferenced static files

A name search over every `.html`, `.py`, `.css` and `.js` in `seek`, `themes/NextSeek/templates`, `themes/NextSeek/static/css`, `themes/NextSeek/static/js/nextseek.js`, `static/js/custom`, `static/js/chat_assistant`, `dmac` and `nextseek_api` found no reference to these. Confidence is "likely" because a path built in JavaScript would not match.

| Group | Files |
|---|---|
| Logos and partner images in `themes/NextSeek/static/img/` | `logo.png`, `400px-Bmclogo2020.png`, `800px-BMC_Header_2020_3.png`, `BMC_Header_2020_3-i6GmrgvN.png`, `BTC.jpg`, `BTC_LOGO_RGB.webp`, `CSBC.jpg`, `CSBC.png`, `Impact.png`, `Impact_logo.svg`, `Metnet.png`, `Srp.png`, `ki_logo01-300x52.{jpg,png}`, `logo-{blacknwhite,blue,o,pale,white}.png`, `cover.png`, `cover-impactb.png`, `glass.jpg`, `glass-image-copyright.txt`, `mybg.png`, `violate.jpg`, `supporter-small.png` |
| Generic widget leftovers, same folder | `ajax-loader.gif`, `alpha.png`, `blank.gif`, `clear.png`, `loading.gif`, `hue.png`, `saturation.png`, `ribbon.png`, `vt-menu.png`, `mappin-default.png`, `select2-spinner.gif`, `minus.png`, `plus.png`, five `sort_*.png` |
| Subfolders of that folder | `colorblind-friendly/`, `dropzone/`, `flags/`, `gradient/`, `invoice/`, `jcrop/`, `jqueryui/`, `partners/`, `pattern/`, `realestate/`, `splash/`, `superbox/`, `versions/`, `voicecommand/`, `favicon/` |
| Stock Bootstrap and Mezzanine | `static/css/bootstrap*.css`, `static/css/mezzanine.css`, `static/fonts/glyphicons-*`, `static/js/bootstrap*.js`, `static/js/html5shiv.js`, `static/js/respond.min.js`. Only the unreachable root `templates/base.html` loads them. Mezzanine admin may load its own copies, hence check-first |
| D3 experiment | `static/js/buildtree/` (d3 libraries, `dndTree*.js`, `flare.json`, a saved "Tree Layout in D3.js" page) |
| Extra DAG files | `static/js/dag/d3neo4j.js`, `static/js/dag/d3neo4j.css`. Only `dag/dag.js` is loaded (`pages/samples_tree_new.embed.html`) |

Live images that look like leftovers: `img/favicon.png` (the favicon links in the theme `base.html`), `img/favicon.ico` (`base_auth.html`), `img/bmc-header-800.png`, `img/nessie-logo.png`, `img/timeline-icon-external-link.png`. Keep them.

### Views and routes tied to missing templates

| Symbol | Problem | Routed? |
|---|---|---|
| `login_full` in `dmac/views.py` | Renders `home.html`, which does not exist anywhere | No: `dmac/urls.py` routes `login_seek` |
| `index` in `dmac/views.py` | Renders `seek_login.html`, which does not exist | No. Nothing imports it except tests touching `home` |
| `sampleSearch` in `seek/views/search.py` | Builds a `report` dict, discards it, redirects to `/seek/search/` | Yes, but it only redirects |

## Deletion candidates

Work from this table. "Certain" means nothing in the tree reaches it and no test needs it; "likely" means no reference by name but the method has a blind spot; "check-first" means a human or a live check must decide.

| Confidence | Candidate | Evidence | Extra step before deleting |
|---|---|---|---|
| certain | `seek/templates/pages/samples_tree.embed.html.bk` | Zero references | None |
| certain | `seek/templates/pages/samples_stable.embed.html.bk` | In no include | Edit `ORPHANS` in `test_download_call_sites.py` first (the test asserts it exists) |
| certain | `static/js/sample_timeline.bk/` | Timeline template loads a different folder | Check collectstatic output afterwards |
| certain | `seek/templates/samplesTest.html`, `sampleUpload.html`, `batchSearch.html`, `publish.html`, `publishAssets.html` | No renderer, unreachable | None |
| certain | `seek/templates/sampleDeletion.html`, `sampleSearch.html` | No renderer | Edit `ORPHANS` in the test first |
| certain | `seek/templates/pages/404.html`, `pages/denied.html` | Names appear nowhere | None |
| certain | `pages/seek_includes.html` | Never renders (every include is outside a block) | Delete the six include lines first (`admin_retrieval.html`, `clades.html`, `internal_assays.html`, `sampleQuery.html`, `sampleDeletion.html`, `sampleSearch.html`) |
| certain | Dead embeds: `pages/batchSearch_{query,search,table}`, `publish_{search,stable}`, `publishAssets_{search,stable}`, `samples_upload`, `samples_query`, `datafile_upload`, `searchAdvanced_retrieval` (all `.embed.html`) | Parents dead or nothing includes them | Delete with their parents |
| certain | `pages/searchAdvanced_rtable.embed.html`, `pages/searchAdvanced_tree.embed.html` | Nothing includes them | Edit `ORPHANS` in the test first |
| certain | `seek/templates/content.embed.html` and `themes/NextSeek/templates/content.embed.html` | Nothing includes either; the seek copy includes a missing file | Delete both together |
| certain | `seek/templates/pages/dmac.logs` | Pasted log, not a template | None |
| certain | `dmac/templates/` (3 files) | Not on loader path | None |
| certain | `templates/twitter/`, `templates/mobile/` | App disabled, feature absent in Mezzanine 6 | None |
| certain | `login_full` and `index` in `dmac/views.py` | Unrouted, render missing templates | Grep tests for `views.index` first |
| certain | `cover-bak.png`, `cover - Copy.png`, `fairdata_logo01-300x52-{bak,test,v2}.png` | Backup images, no references | None |
| certain (demo only) | `demo/` and `demo-mobile/` under both EasyUI copies, and the `.html` files in `themes/NextSeek/static/js/easyui/` | No reference; also closes security item SEC-0930-E (tracked privately) | Keep a full copy of the rest; confirm the pages that use EasyUI still load |
| likely | Rest of repo-root `templates/` (accounts, blog, email, errors, generic, includes, pages, base, index, search_results) | Not on loader path; package copies render | `themes/README.md` mentions it, update it |
| likely | One of the two identical EasyUI copies | `diff -rq` identical | Decide which `STATICFILES_DIRS` entry keeps it. The theme copy is the one `themes/README.md` documents |
| likely | Unreferenced images and subfolders in the static table above | No name hits | Open the live pages in a browser after; a JS-built path would show as a broken image |
| likely | `static/js/sample_timeline/` leftovers and the nested duplicate, `static/js/dag/d3neo4j.*`, `static/test/image.jpg` | No references | Load `/seek/sample_timeline/` and a sample page afterwards |
| likely | `themes/NextSeek/templates/pages/menus/tree.html` | No `page_menu` call in the theme | Only matters if a Mezzanine Page is ever created |
| check-first | `static/js/buildtree/` | Unreferenced but a large experiment | Ask the operator |
| check-first | `static/css/bootstrap*.css`, `static/js/bootstrap*.js`, `html5shiv.js`, `respond.min.js`, `mezzanine.css`, `glyphicons-*` | Only the dead root base loads them; Mezzanine admin may too | Load the Mezzanine admin pages and compare |
| check-first | `mezzanine.blog` app and `templates/blog/` | `/blog/` is live | Migration and data check; operator decision |
| check-first | `newSearch.html` and its six `*_new*` embeds | Live by URL; the test's `LIVE` list names some of them | Decide to promote (add a nav link) or retire; update the test either way |
| check-first | `pages/samples_tree.embed.html` | Still included (inside HTML comments) by `pages/samples.embed.html` | Remove or convert both include lines first |
| check-first | `dmac/conversion.pyc`, `dmac/__init__.pyc` | Tracked bytecode, not UI | Separate cleanup |

### Checks to rerun before deleting anything

Run these at the commit you are cleaning up from. Do not trust this page's dates.

1. Name grep across code and templates. For a template `X.html` or `pages/X.embed.html`, search every `.py` and `.html` file (including the theme and `dmac/templates`, and the `NessieAI` tree if it could render Django templates) for the bare file name, for example `grep -rn "samples_query.embed" --include=*.py --include=*.html .` (skip `node_modules` and `.venv`). A hit inside a `#` or `{# #}` comment does not count, but a hit inside `<!-- -->` does, because Django still runs it.
2. URL includes. Confirm no route renders it: check `seek/urls.py`, `dmac/urls.py` and the `template_name=` arguments (`grep -rn "template_name" seek dmac nextseek_api`).
3. Extends and include chain. A template with no direct hit may be included by a file that is itself live. Rerun the two-step walk (seeds, then follow `include` and `extends`) rather than checking one file at a time, and remember the theme folder shadows `seek/templates` for the same name.
4. Tests that name the file. `grep -rn "<file name>" nextseek_api/tests seek/tests ci`. `nextseek_api/tests/test_download_call_sites.py` asserts that its `ORPHANS` files exist and parametrizes over `LIVE` files, so deleting a listed orphan fails that test until the list is edited.
5. Static files. `grep -rn "<name>"` over templates, CSS, `nextseek.js`, `static/js/custom`, `static/js/chat_assistant` and `NessieAI/chat_frontend/src`. Also grep for the parent folder name, since JS may build paths. The chat bundle under `static/` is committed and minified, so grep it too.
6. collectstatic. After deleting static files, run `collectstatic` (on the box, not a laptop; see [ci-and-deploy.md](ci-and-deploy.md)) and compare the published tree with the previous one. Both `static/` and `themes/NextSeek/static/` feed `STATIC_ROOT`, so a file deleted from one folder may still be published from the other.
7. Live probe after deploy. Load the home page, `/seek/search/`, `/seek/newsearch/`, a sample detail page, `/seek/sample_timeline/` and a Mezzanine admin page, and confirm no broken images or 500s.

## Where to edit

| Task | Files and steps |
|---|---|
| Really hide Sample Tree v1 | `pages/samples.embed.html`: replace the two `<!-- ... -->` blocks around the `samples_tree.embed.html` includes with `{# #}` or `{% comment %}`, or delete the includes |
| Retire an old template | Run the checks above, delete the file, edit `ORPHANS` in `nextseek_api/tests/test_download_call_sites.py` if it is listed, and fix the stale citation of the old single-module views file in that file's docstring (views is a package now; the commented render is in `seek/views/search.py`) |
| Clean up the repo-root `templates/` | Delete the folder in one commit, and update the "81 files" statements in `themes/README.md` |
| Remove the EasyUI demo folders | Delete `demo/` and `demo-mobile/` in both `static/jquery-easyui-1.5.2/` and `themes/NextSeek/static/jquery-easyui-1.5.2/`; run collectstatic; the stale copies in `STATIC_ROOT` on a box persist until cleaned (see Gotchas) |
| Promote or retire `/seek/newsearch/` | Add a link in `themes/NextSeek/templates/nav.embed.html` to promote, or remove the route in `seek/urls.py`, the `newSearch` view in `seek/views/search.py` and the six `*_new*` embeds; update the test's `LIVE` list and `LOADERS` |
| Remove the blog | Operator decision first. Then `mezzanine.blog` in `INSTALLED_APPS` (`dmac/settings.py`), migration state, and the `templates/blog/` copies |

## Gotchas

- Editing a dead file has no effect and gives no error. Before changing any template, confirm a route reaches it (find the `render` or `template_name`, then the include chain). Names that look right are the trap: `sampleSearch.html`, `sampleUpload.html` and `samplesTest.html` all look like real pages.
- Changing the repo-root `templates/*` or `dmac/templates/*` never changes the site. The real scaffold overrides live only in `themes/NextSeek/templates/`.
- HTML comments do not stop Django: a `{% include %}` or `{{ var }}` inside `<!-- -->` still runs, costs time, and can raise an error. Use `{# #}` or `{% comment %}`.
- The theme folder shadows `seek/templates` for the same file name. `content.embed.html` exists in both; the theme copy wins, and neither is used.
- A test pins some orphans. Deleting `samples_stable.embed.html.bk`, `sampleSearch.html`, `sampleDeletion.html`, `searchAdvanced_rtable.embed.html` or `searchAdvanced_tree.embed.html` fails `test_orphan_list_still_matches_reality` until `ORPHANS` is edited. The same file lists the live set, so it is also a good list of which search templates matter.
- Deleting a static file from the repo does not remove it from `STATIC_ROOT` on a box that already ran collectstatic; the stale file keeps being served until that folder is cleaned (see [ci-and-deploy.md](ci-and-deploy.md)). The two static roots (`static/`, `themes/NextSeek/static/`) are merged, so deleting from only one of a duplicated pair changes nothing.
- The committed chat bundle and the Sample Timeline build under `static/js/` are build outputs; do not hand-edit them, and do not delete the current hashed files named in `seek/templates/sample_timeline.html`.
- Missing-template bugs surface as HTTP 500, not as a missing page. `login_full` and `index` in `dmac/views.py` would do this if ever routed.

## Known issues

Tracked in [known-issues.md](known-issues.md#legacy). The ones that matter most for this area:

- Vendored EasyUI demo folders are published and duplicated across two static roots (security item SEC-0930-E, tracked privately).
- Sample Tree v1 is hidden with HTML comments but still rendered into every sample detail page source.
