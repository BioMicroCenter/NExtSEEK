# Dead and legacy UI

## What this covers

This page lists the UI files that look editable but are not (or no longer) what users see: templates that nothing renders, backup copies, template folders that Django never searches, Mezzanine features that are switched on or off, duplicated vendored assets, unreferenced images, and view functions that point at missing templates. Read it before editing any template or static file you did not find through a live route, so you do not spend an hour changing a file that has no effect.

It does not cover the live templates and how they are wired (see [shell.md](shell.md) and [pages.md](pages.md)), the live static assets (see [styles.md](styles.md) and [javascript.md](javascript.md)), or the deploy and CI rules for deleting things (see [ci-and-deploy.md](ci-and-deploy.md)). Every problem is tracked in [known-issues.md](known-issues.md); this page only says where each one lives.

State of the tree: branch `feat/ui-docs-2609`, origin/dev `caa55340`, checked 2026-09-30. The "certain" rows were deleted on 2026-10-01 (branch `feat/UI-FIXES-dead`); the tables below list only what is left.

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

One folder is on neither list, so nothing in it can ever render: the repo-root `templates/` (66 files, a copy of the Mezzanine scaffold). the old `dmac` templates folder (`dmac` is the project package and is not in `INSTALLED_APPS`) was removed for the same reason. `themes/README.md` already warns about the root folder.

### How "dead" was established

For the templates under `seek/templates` and `themes/NextSeek/templates`, reachability was computed in two steps:

1. Seeds: every template-name string literal that appears in `seek/views/*.py`, `dmac/views.py` and `nextseek_api` (render calls, `TemplateView(template_name=...)` in `seek/urls.py`). This over-counts: a name in a commented-out `render` still counts as a seed (the now deleted `sampleSearch.html` was one), so each seed was checked by eye.
2. Walk: follow every `{% include "..." %}` and `{% extends "..." %}` from the seeds. Every include and extends argument in these two folders is a string literal (no variable template names), so the walk is complete for templates. A template that is never reached is listed as unreachable.

Limits of the method: it cannot see a template name built in Python at run time (none were found), and for static files it only searches names, so a URL assembled in JavaScript would be missed. That is why static candidates are marked "likely", not "certain". The rerun checks are at the end of this page.

### Live duplicates that are not dead

Several old and new variants are both live. Do not delete a file only because a newer twin exists.

| Old | New | Status |
|---|---|---|
| `searchAdvanced.html` and its embeds (`samples_search`, `samples_stable`, `searchAdvanced_search`, `searchAdvanced_stable`, `searchAdvanced_deletion`) | `newSearch.html` and its embeds (`samples_newsearch`, `samples_new_stable`, `searchAdvanced_newsearch`, `searchAdvanced_new_stable`, `searchAdvanced_newretrieval`, `searchAdvanced_newdeletion`) | Both live. `/seek/search/` renders the old set, `/seek/newsearch/` the new one (`seek/views/search.py`, routes in `seek/urls.py`). No theme or seek template links to `/seek/newsearch/`; it is reachable by typing the URL. `nextseek_api/tests/test_download_call_sites.py` lists some templates of each set in `LIVE`. |
| `pages/samples_tree.embed.html` (Sample Tree v1) | `pages/samples_tree_new.embed.html` (v2, loads `static/js/dag/dag.js`) | v2 is the visible tab. v1 is still executed, see the next section. |

### The v1 sample tree is commented out but still runs

`seek/templates/pages/samples.embed.html` wraps the Sample Tree v1 tab in an HTML comment (`<!-- ... -->`) and, further down, has a second `<!-- {% include "pages/samples_tree.embed.html" %} -->`. Django does not know about HTML comments: it executes both `{% include %}` tags and emits the whole v1 template (261 lines, starting with two CDN script tags) into the page source twice, where the browser then hides it. Every sample detail page therefore carries about 520 lines of v1 markup that nobody sees. Only the "Sample Tree v2" tab is live.

To really disable it, use `{# ... #}` or `{% comment %}` around the includes, or delete the includes and then `pages/samples_tree.embed.html`. Until then `samples_tree.embed.html` is not safe to delete: it is reached by those two includes.

## Inventory

### Backup, scratch and pasted files

| Path | What it is | Evidence | Confidence |
|---|---|---|---|
| `static/test/image.jpg` | A test image, published under `/static/test/` | No reference found | likely |
| `dmac/conversion.pyc`, `dmac/__init__.pyc` | Stale bytecode tracked in git next to the sources | `git ls-files dmac` lists them | check-first (not UI) |

### Templates that nothing reaches

All of these are under `seek/templates/` unless noted. "Parent dead" means the only file that includes it is itself unreachable.

| Template | Why unreachable |
|---|---|
| `themes/NextSeek/templates/pages/menus/tree.html` | Only used if a theme template calls Mezzanine's `page_menu`; none does (navigation is hand-written in `nav.embed.html`) |

Not dead, despite looking like leftovers: `pages/sampleSearch_core.embed.html` (included by `searchAdvanced.html`), `pages/samples_search.embed.html` (see above), `themes/NextSeek/templates/accounts/includes/user_panel.html` (reached from the theme's `includes/user_panel.html`), and `themes/NextSeek/templates/includes/catalog_table_filter.js` (included by the assays and sample types list pages).

### Repo-root `templates/` (66 files, 54 `.html`)

A stock Mezzanine scaffold. None of it resolves: the folder is not in `DIRS` and is not inside an installed app. Whatever renders for these names comes from the Mezzanine package, so deleting the folder changes nothing users see (a live check on 2026-09-30 confirmed `/search/` and `/accounts/login/` answer from package templates).

| Subfolder | Files | Note |
|---|---|---|
| `templates/accounts/` | 8 | Package copies render instead |
| `templates/blog/` | 3 | The blog itself is live, see below; these copies are not what it uses |
| `templates/email/` | 20 | Account emails come from the Mezzanine package versions |
| `templates/errors/` | 2 | |
| `templates/generic/` | 7 | |
| `templates/includes/` | 10 | |
| `templates/pages/` | 13 | Includes 8 menu templates. The theme overrides only `pages/menus/tree.html` |
| `templates/base.html`, `index.html`, `search_results.html` | 3 | The real base is `themes/NextSeek/templates/base.html` |

The root `base.html` is also the only file that loads the stock Bootstrap and `mezzanine.css` files under `static/` (see unreferenced static below).

### Mezzanine features: live, dead, unknown

`INSTALLED_APPS` in `dmac/settings.py` has `mezzanine.boot, conf, core, generic, pages, blog, forms, galleries, accounts`. `mezzanine.twitter` is commented out. `mezzanine.urls` is included in `dmac/urls.py` as a catch-all `^` pattern, last. Above it, a 404 route shadows Mezzanine's public pages (`/blog/`, `/search/`, `/accounts/...`, `/password_reset/`, `/reset/...`); `/accounts/login/` (the SEEK login), `/accounts/signup/` and `/accounts/logout/` are registered before that.

| Feature | State | Notes |
|---|---|---|
| Blog (`mezzanine.blog`) | Installed, routes answer 404 | The app, its models and tables stay; `/blog/` and its feeds are shadowed by the 404 route in `dmac/urls.py` |
| Pages, forms, galleries | Installed, no theme templates | Only a Page object created in Mezzanine admin would render, using package templates |
| Accounts (`mezzanine.accounts`) | Sign-out only | `/accounts/logout/` is Mezzanine's logout view (the user menu's link); `/accounts/login/` and `/accounts/signup/` are the SEEK views; every other `/accounts/...` URL answers 404 |

Removing `mezzanine.blog` from `INSTALLED_APPS` is a check-first item: it has migrations and model tables, and the catch-all include may also be wired to other Mezzanine apps. Ask the operator before removing any installed app.

### Vendored EasyUI: two copies

| Item | Count |
|---|---|
| `themes/NextSeek/static/jquery-easyui-1.5.2/` | 458 files |
| `static/jquery-easyui-1.5.2/` | 458 files, `diff -rq` reports it identical to the theme copy; tracked in git |

`STATICFILES_DIRS` lists both `/app/themes/NextSeek/static` and `/app/static`, `STATIC_ROOT` is `/static`, and nginx serves `/static/` straight from that folder (`location /static/` in `docker/nginx.conf`). collectstatic therefore merges the two copies into one published tree. The `demo/` and `demo-mobile/` folders (security item SEC-0930-E, tracked privately) were deleted from both copies on 2026-10-01; a box that already ran collectstatic keeps the old files in `STATIC_ROOT` until that folder is cleaned.

The live app uses only `jquery.min.js`, `jquery.easyui.min.js`, the default theme's `easyui.css` and the shared `icon.css` from this tree (`themes/NextSeek/templates/base.html`), plus the `locale/`, `src/` and `plugins/` folders if a page loads them. The rest of the vendored tree must stay.

Separately, `themes/NextSeek/static/js/easyui/` holds EasyUI plugin files. Templates load only `datagrid-filter.js`, `datagrid-export.js` and `datagrid-detailview.js` from it. The other files there (`datagrid-cellediting.js`, `datagrid-groupview.js`, `filter.png`) are unreferenced. The plugin documentation `.html` pages were deleted.

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
| `sampleSearch` in `seek/views/search.py` | Builds a `report` dict, discards it, redirects to `/seek/search/` | Yes, but it only redirects |

## Deletion candidates

Work from this table. "Certain" means nothing in the tree reaches it and no test needs it; "likely" means no reference by name but the method has a blind spot; "check-first" means a human or a live check must decide.

| Confidence | Candidate | Evidence | Extra step before deleting |
|---|---|---|---|
| likely | Rest of repo-root `templates/` (accounts, blog, email, errors, generic, includes, pages, base, index, search_results) | Not on loader path; package copies render | `themes/README.md` mentions it, update it |
| likely | One of the two identical EasyUI copies | `diff -rq` identical | Decide which `STATICFILES_DIRS` entry keeps it. The theme copy is the one `themes/README.md` documents |
| likely | Unreferenced images and subfolders in the static table above | No name hits | Open the live pages in a browser after; a JS-built path would show as a broken image |
| likely | `static/js/dag/d3neo4j.*`, `static/test/image.jpg` | No references | Load `/seek/sample_timeline/` and a sample page afterwards |
| likely | `themes/NextSeek/templates/pages/menus/tree.html` | No `page_menu` call in the theme | Only matters if a Mezzanine Page is ever created |
| check-first | `static/js/buildtree/` | Unreferenced but a large experiment | Ask the operator |
| check-first | `static/css/bootstrap*.css`, `static/js/bootstrap*.js`, `html5shiv.js`, `respond.min.js`, `mezzanine.css`, `glyphicons-*` | Only the dead root base loads them; Mezzanine admin may too | Load the Mezzanine admin pages and compare |
| check-first | `mezzanine.blog` app and `templates/blog/` | Routes answer 404; the app's migrations and tables remain | Migration and data check before removing the app |
| check-first | `newSearch.html` and its six `*_new*` embeds | Live by URL; the test's `LIVE` list names some of them | Decide to promote (add a nav link) or retire; update the test either way |
| check-first | `pages/samples_tree.embed.html` | Still included (inside HTML comments) by `pages/samples.embed.html` | Remove or convert both include lines first |
| check-first | `dmac/conversion.pyc`, `dmac/__init__.pyc` | Tracked bytecode, not UI | Separate cleanup |

### Checks to rerun before deleting anything

Run these at the commit you are cleaning up from. Do not trust this page's dates.

1. Name grep across code and templates. For a template `X.html` or `pages/X.embed.html`, search every `.py` and `.html` file (including the theme, and the `NessieAI` tree if it could render Django templates) for the bare file name, for example `grep -rn "samples_query.embed" --include=*.py --include=*.html .` (skip `node_modules` and `.venv`). A hit inside a `#` or `{# #}` comment does not count, but a hit inside `<!-- -->` does, because Django still runs it.
2. URL includes. Confirm no route renders it: check `seek/urls.py`, `dmac/urls.py` and the `template_name=` arguments (`grep -rn "template_name" seek dmac nextseek_api`).
3. Extends and include chain. A template with no direct hit may be included by a file that is itself live. Rerun the two-step walk (seeds, then follow `include` and `extends`) rather than checking one file at a time, and remember the theme folder shadows `seek/templates` for the same name.
4. Tests that name the file. `grep -rn "<file name>" nextseek_api/tests seek/tests ci`. `nextseek_api/tests/test_download_call_sites.py` parametrizes over its `LIVE` files, so deleting one of those fails that test until the list is edited.
5. Static files. `grep -rn "<name>"` over templates, CSS, `nextseek.js`, `static/js/custom`, `static/js/chat_assistant` and `NessieAI/chat_frontend/src`. Also grep for the parent folder name, since JS may build paths. The chat bundle under `static/` is committed and minified, so grep it too.
6. collectstatic. After deleting static files, run `collectstatic` (on the box, not a laptop; see [ci-and-deploy.md](ci-and-deploy.md)) and compare the published tree with the previous one. Both `static/` and `themes/NextSeek/static/` feed `STATIC_ROOT`, so a file deleted from one folder may still be published from the other.
7. Live probe after deploy. Load the home page, `/seek/search/`, `/seek/newsearch/`, a sample detail page, `/seek/sample_timeline/` and a Mezzanine admin page, and confirm no broken images or 500s.

## Where to edit

| Task | Files and steps |
|---|---|
| Really hide Sample Tree v1 | `pages/samples.embed.html`: replace the two `<!-- ... -->` blocks around the `samples_tree.embed.html` includes with `{# #}` or `{% comment %}`, or delete the includes |
| Retire an old template | Run the checks above and delete the file. If it is in the `LIVE` list of `nextseek_api/tests/test_download_call_sites.py`, edit that list too |
| Clean up the repo-root `templates/` | Delete the folder in one commit, and update the "81 files" statements in `themes/README.md` |
| Promote or retire `/seek/newsearch/` | Add a link in `themes/NextSeek/templates/nav.embed.html` to promote, or remove the route in `seek/urls.py`, the `newSearch` view in `seek/views/search.py` and the six `*_new*` embeds; update the test's `LIVE` list and `LOADERS` |
| Remove the blog | Operator decision first. Then `mezzanine.blog` in `INSTALLED_APPS` (`dmac/settings.py`), migration state, and the `templates/blog/` copies |

## Gotchas

- Editing a dead file has no effect and gives no error. Before changing any template, confirm a route reaches it (find the `render` or `template_name`, then the include chain). Names that look right are the trap: the deleted `sampleSearch.html`, `sampleUpload.html` and `samplesTest.html` all looked like real pages.
- Changing the repo-root `templates/*` never changes the site. The real scaffold overrides live only in `themes/NextSeek/templates/`.
- HTML comments do not stop Django: a `{% include %}` or `{{ var }}` inside `<!-- -->` still runs, costs time, and can raise an error. Use `{# #}` or `{% comment %}`.
- The theme folder shadows `seek/templates` for the same file name, so a stale copy in one folder can hide the real file in the other.
- Deleting a static file from the repo does not remove it from `STATIC_ROOT` on a box that already ran collectstatic; the stale file keeps being served until that folder is cleaned (see [ci-and-deploy.md](ci-and-deploy.md)). The two static roots (`static/`, `themes/NextSeek/static/`) are merged, so deleting from only one of a duplicated pair changes nothing.
- The committed chat bundle and the Sample Timeline build under `static/js/` are build outputs; do not hand-edit them, and do not delete the current hashed files named in `seek/templates/sample_timeline.html`.
- Missing-template bugs surface as HTTP 500, not as a missing page.

## Known issues

Tracked in [known-issues.md](known-issues.md#legacy). The ones that matter most for this area:

- The two EasyUI copies are identical and both published (UI-201, remaining part).
- Sample Tree v1 is hidden with HTML comments but still rendered into every sample detail page source.
