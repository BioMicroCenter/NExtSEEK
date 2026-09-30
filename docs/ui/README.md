# NExtSEEK UI guide

Start here for any change to what a user sees in NExtSEEK: the page shell, a page, a grid, a style,
the project graphs, the Nessie chat page, or the help pages. Each page below says how its part of
the UI works, where to edit it, and what silently bites. Every problem found so far is in one list,
[known-issues.md](known-issues.md).

Last checked against the tree at origin/dev `caa55340` on 2026-09-30. Pages cite files and symbols
(template blocks, element ids, CSS selectors, function names) rather than line numbers, so they stay
useful as the code moves. If a citation no longer matches, trust the code and fix the page.

## The UI in one picture

```
browser
  |
nginx ---- /static/...  served straight from the collected static volume (expires 30d)
  |
Django  dmac/urls.py
  |-- /  and /login/            dmac/views.py        -> themes/NextSeek/templates/index.html, login.html
  |-- /seek/...                 seek/urls.py         -> seek/views/<module>.py -> seek/templates/*.html
  |                                                     (+ seek/templates/pages/*.embed.html partials)
  |-- /seek/assistant/          smartSearch.html     -> mounts the React app (NessieAI/chat_frontend,
  |                                                     shipped as the committed build in static/js/chat_assistant/)
  |-- /seek/projects/<id>/connections/               -> a whole HTML page built in Python
  |                                                     (nextseek_api/services/sampletype_connections.py),
  |                                                     shown in an iframe on the project page
  |-- /nextseek_api/...         JSON the pages' JavaScript calls; swagger and redoc
  `-- everything else           Mezzanine's catch-all (still serves /blog/ and /accounts/...)

every server-rendered page = themes/NextSeek/templates/base.html
  sidebar  nav.embed.html + includes/user_panel.html       (a drawer below 992px)
  main     {% block main %} filled by the page template
  footer   page-footer.embed.html
  CSS      Bootstrap 5.3.3 (CDN), EasyUI 1.5.2 default theme, themes/NextSeek/static/css/nextseek.css
  JS       jQuery 1.11.3 + EasyUI 1.5.2 (vendored), Bootstrap 5.3.3 bundle (CDN), js/nextseek.js,
           then each page's own inline scripts
```

Template lookup order: `themes/NextSeek/templates/` first, then each installed app's `templates/`
folder (`seek/templates/` is one), then Mezzanine's packaged templates. The repo-root `templates/`
folder is not on the path at all: editing it changes nothing. See [shell.md](shell.md).

## I want to change...

| Task | Read | Main files |
|---|---|---|
| Sidebar links, Quick Access (Ask Nessie, UID search, + New sample) | [shell.md](shell.md) | `themes/NextSeek/templates/nav.embed.html` |
| Sign in / sign out controls, the user card | [shell.md](shell.md) | `themes/NextSeek/templates/accounts/includes/user_panel.html` |
| The login page | [shell.md](shell.md) | `themes/NextSeek/templates/login.html`, `base_auth.html`; view `dmac/views.py` `login_seek` |
| The home page | [shell.md](shell.md) | `themes/NextSeek/templates/index.html`; view `dmac/views.py` `home` |
| The footer | [shell.md](shell.md) | `themes/NextSeek/templates/page-footer.embed.html` |
| Add a new page | [pages.md](pages.md) "How to add a page" | a url in `seek/urls.py` (or before the Mezzanine catch-all in `dmac/urls.py`), a view re-exported in `seek/views/__init__.py`, a template extending `base.html`, a `ci/routes.py` entry and the `OWNED_ROUTE_COUNT` bump |
| Find which view and template render a URL | [pages.md](pages.md) inventory | `seek/urls.py`, `dmac/urls.py`, then the view in `seek/views/<area>.py` |
| Fix a UI bug from the work list | [known-issues.md](known-issues.md) "Fix first" | the files named in each batch |
| Error and access-denied pages | [pages.md](pages.md) "Where to edit" | `seek/templates/error.html`; `handler404`, `handler500` in `dmac/urls.py` |
| Sample upload, templates, sample attributes, sample tree | [upload-and-samples.md](upload-and-samples.md) | `seek/views/upload.py`, `seek/templates/batchUpload.html` |
| Sample search, results grids, downloads | [search-and-downloads.md](search-and-downloads.md) | `seek/templates/searchAdvanced.html` and its embeds, `static/js/ns_sample_download.js` |
| Project pages, catalogs, the Sample flow graph | [projects-catalogs-graphs.md](projects-catalogs-graphs.md) | `seek/templates/projectPage.html`, `nextseek_api/services/sampletype_connections.py` (`rows_to_html`) |
| The sample page and its lineage tree | [upload-and-samples.md](upload-and-samples.md) | `seek/templates/pages/samples.embed.html`, `pages/samples_tree_new.embed.html`, `static/js/dag/dag.js` |
| Colours, fonts, spacing, breakpoints | [styles.md](styles.md) | `themes/NextSeek/static/css/nextseek.css` (the `--ns-*` tokens in `:root`) |
| Shared or page JavaScript, AJAX endpoints | [javascript.md](javascript.md) | `themes/NextSeek/static/js/nextseek.js`, `static/js/`, inline scripts |
| The Nessie chat page | [chat-frontend.md](chat-frontend.md) | `NessieAI/chat_frontend/src/`, then rebuild and commit `static/js/chat_assistant/` |
| Help, Getting Started, links to the user docs | [docs-and-help.md](docs-and-help.md) | `themes/NextSeek/templates/help/getting_started.html` |
| Remove old code | [legacy.md](legacy.md) | the deletion-candidate table |
| Preview, CI checks, deploy a UI change | [ci-and-deploy.md](ci-and-deploy.md) | `ci/routes.py`, `startup.sh` |

## Rules that bite

1. **Where a change goes live.** `themes/NextSeek/` is bind-mounted into the app container on the
   boxes, so a theme template edit shows on the next request after a `git pull`. A theme static
   file (`nextseek.css`, `nextseek.js`, images) also needs the app restarted, because static is
   collected at container start. Everything else (`seek/templates/`, the repo-root `static/`,
   Python, `nextseek_api/`) needs `./startup.sh rebuild`. See [ci-and-deploy.md](ci-and-deploy.md).
2. **The chat bundle is committed.** The Dockerfile has no Node step. After any change under
   `NessieAI/chat_frontend/src/`, run `npm run build:embedded` and commit the rebuilt
   `static/js/chat_assistant/` right after the source (two commits, per
   `NessieAI/chat_frontend/CLAUDE.md`), or the boxes keep serving the old build. See
   [chat-frontend.md](chat-frontend.md).
3. **Static files are cached for 30 days.** Anything loaded through `{% static %}` (including
   `nextseek.css` and `nextseek.js`) gets a content-hashed name and updates as soon as the box
   re-collects static; anything requested by an unhashed name (`{{STATIC_URL}}...` or a hard-coded
   `/static/` path, common in `seek/templates/`) can stay stale in a browser. Hard-reload before
   deciding a deploy failed.
4. **Every route is registered in CI.** A new URL needs a `ci/routes.py` entry, or the blocking
   route gate fails, and a bump of `OWNED_ROUTE_COUNT` in `ci/smoke/test_registry_contents.py`. A
   root-level route also goes into `_PROJECT_LEVEL` in `ci/gate/live_routes.py` (without it the gate
   stays green and never sees the route) and must be mounted before Mezzanine's catch-all in
   `dmac/urls.py`. See [ci-and-deploy.md](ci-and-deploy.md) and [pages.md](pages.md).
5. **Content outside a block is dropped.** In a template that `{% extends %}` another, anything not
   inside a `{% block %}` is silently discarded. Six templates include `pages/seek_includes.html`
   this way, and it never renders.
6. **HTML comments do not stop Django.** An `{% include %}` inside `<!-- -->` still renders and ships
   to the browser. Use `{% comment %}` to remove markup.
7. **Phones get the same templates.** There is no device detection. Below 992px the sidebar becomes
   a drawer; pages that need a phone layout must add it themselves (see [styles.md](styles.md)).
8. **The repo is public.** Do not put security detail, personal names, emails of individuals or
   account names in docs, commits or issues. Security items are tracked privately and referred to by
   code (SEC-0930-A and so on).
9. **No full stack on a laptop.** Render tests run in the throwaway test container; rebuilds and
   integrated checks run on the dev box. See [ci-and-deploy.md](ci-and-deploy.md).

## Pages in this guide

| Page | Covers |
|---|---|
| [shell.md](shell.md) | base template, sidebar and drawer, user panel, footer, login and auth layout, home, breakpoints, what a phone sees |
| [pages.md](pages.md) | every HTML route: view, template, who can see it, where it is linked; how to add a page |
| [upload-and-samples.md](upload-and-samples.md) | + New sample, batch upload, data file upload, templates, sample attributes, sample tree and timeline |
| [search-and-downloads.md](search-and-downloads.md) | Sample Search (desktop tabs and phone form), results grids, downloads, data file and SOP queries |
| [projects-catalogs-graphs.md](projects-catalogs-graphs.md) | projects, sample type and assay catalogs, SOPs, data files, every graph and tree visualization |
| [styles.md](styles.md) | CSS layers, tokens, fonts, icons, breakpoints, inline style debt, the token set to converge on |
| [javascript.md](javascript.md) | libraries and versions, nextseek.js, shared scripts, inline scripts, endpoints, CSRF patterns |
| [chat-frontend.md](chat-frontend.md) | the Nessie chat page: stack, mounting, transport, components, phone behaviour, build rule |
| [ci-and-deploy.md](ci-and-deploy.md) | route registry and CI checks, static pipeline and caching, preview loops, deploying |
| [legacy.md](legacy.md) | dead templates, backups, Mezzanine leftovers, vendored demo folders, deletion candidates |
| [docs-and-help.md](docs-and-help.md) | Getting Started, API docs pages, the GitBook links, the planned move of the user docs |
| [known-issues.md](known-issues.md) | every open UI problem, ranked, with evidence and a fix idea |

Owner docs stay authoritative for their folders and go deeper: [`themes/README.md`](../../themes/README.md),
[`seek/README.md`](../../seek/README.md), [`NessieAI/chat_frontend/README.md`](../../NessieAI/chat_frontend/README.md),
[`ci/README.md`](../../ci/README.md).

## Keeping this guide true

- Change the page that covers an area in the same commit as the code change.
- When you fix a known issue, delete its row from [known-issues.md](known-issues.md) in the same
  commit (the commit message carries the history).
- Re-run the phone check after shell or layout changes: a 390px wide viewport, logged out and logged
  in, on the pages in [pages.md](pages.md).
