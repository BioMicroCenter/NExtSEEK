# themes/ and templates/ — what will bite you

## Invariants

- A filename added under `themes/NextSeek/templates/` claims that name for the
  whole site, because its directory is the only entry in `DIRS` and the
  filesystem loader runs ahead of the app-directories one
  (`dmac/settings.py:128-132`); dropping in a `projectsList.html` here would
  silently replace `seek/templates/projectsList.html` on every route that
  renders it.
- `{% block title %}` in this theme fills the HTML `<title>` element
  (`themes/NextSeek/templates/base.html:7`), which inverts the upstream
  Mezzanine convention preserved at `templates/base.html:10` where `meta_title`
  is the document title and `title` is the page `<h1>`
  (`templates/base.html:87`); a child template written to the upstream
  convention loses its document title silently, since Django discards an
  override of a block the parent does not define.
- The sidebar reverses nothing: grepping for the string `{% url` in
  `nav.embed.html` returns zero matches, and every destination is instead a
  literal absolute path (`themes/NextSeek/templates/nav.embed.html:16`), so
  renaming a route in `seek/urls.py` cannot raise at render time and instead
  ships a 404 link that only a browser visit reveals. All 21 such hrefs, in
  that file and in `themes/NextSeek/templates/index.html:55`, resolved against
  the live URLconf on 2026-09-03 — three only via the `APPEND_SLASH` redirect.
- The Admin sidebar section gates on `is_superuser`
  (`themes/NextSeek/templates/nav.embed.html:106`) and never on `is_staff`,
  which is set on every SEEK user by the login path itself (`dmac/views.py:97`);
  weakening it to `is_staff` shows Admin Panel, Clades and Internal Assays to
  every researcher, and a source guard fails the build if you do
  (`seek/tests/test_admin_template_gating.py:29`).
- The sign-out link resolves through the URL *name* `logout`
  (`themes/NextSeek/templates/accounts/includes/user_panel.html:38`), which
  belongs to Mezzanine's accounts URLconf and not to this project's own
  `logout_seek` pattern (`dmac/urls.py:23`); repointing that single tag at
  `logout_seek` would send every signed-in user through the project view for
  the first time, so treat it as a routing change, not a template tidy-up.
- Multi-line commentary in these templates must use `{% comment %}`, because
  Django's `{# #}` form is single-line and a multi-line one renders as visible
  page text — the reason is written into the template itself
  (`themes/NextSeek/templates/nav.embed.html:103-104`).
- `themes/NextSeek/static` is the first entry of `STATICFILES_DIRS`
  (`dmac/settings.py:88-91`), so for the 801 relative paths that exist in both
  it and the repo-root `static/` (measured 2026-09-03) the theme copy is the
  one collected and served; editing the root-level twin of a file changes
  nothing a browser will ever fetch.
- No compiled-template cache stands between an edit and the next render: the
  loaders list carries no `django.template.loaders.cached.Loader`
  (`dmac/settings.py:128-132`), which is what makes the runtime bind mount of
  this directory (`docker-compose.yml:29`) worth having.

## Landmines

- The compose service bind-mounts `./themes/NextSeek` over `/app/themes/NextSeek`
  relative to the directory compose runs in (`docker-compose.yml:29`), and
  `scripts/run_tests.sh` runs compose from `$NEXTSEEK_COMPOSE_DIR` while
  mounting a different checkout at `/app` (`scripts/run_tests.sh:43-47`); the
  nested mount is the more specific one, so the theme served inside that
  container comes from the compose directory's checkout and your edits to this
  worktree's `base.html` are invisible no matter how many times you rebuild.
  Use the throwaway-container command below, which has no compose service and
  therefore no nested mount.
- Theme CSS is served under a name that never changes — the staticfiles backend
  is plain `StaticFilesStorage` with no content hashing
  (`dmac/settings.py:288-295`) and the reference is a bare
  `{% static 'css/nextseek.css' %}` (`themes/NextSeek/templates/base.html:24`) —
  while nginx stamps `expires 30d` on everything under `/static/`
  (`docker/nginx.conf:41-44`); a restyle is therefore live and invisible for a
  month of browser cache, and the tell is the chat bundle updating while the
  theme does not, since that bundle's filenames carry Vite content hashes.
- A theme *template* edit takes effect on the next request, but a theme
  *static* edit does not: nginx serves the `nextseek-static-files` volume, which
  only `collectstatic` writes, and that runs at container start
  (`docker/scripts/entrypoint.sh:13`). Editing CSS without restarting the
  container leaves the old file in the volume and looks exactly like a caching
  problem.
- The entire repo-root `templates/` directory is inert. Resolving every one of
  its 81 filenames through the real Django loader on 2026-09-03 put 0 of them in
  that directory, so a fix applied to `templates/accounts/includes/user_panel.html`
  or `templates/base.html:101` changes nothing; the live copies are under
  `themes/NextSeek/templates/`.
- Two of this theme's own twelve templates are equally inert, so time spent
  restyling either is wasted. `content.embed.html` is a superseded home
  fragment: grepping `content.embed` across every `.py` and `.html` file in the
  worktree outside the vendored `static/` trees matches no `{% extends %}`, no
  `{% include %}` and no render call, and matches nothing else either. Its one
  button calls `homeNavUID()`
  (`themes/NextSeek/templates/content.embed.html:144`), a name that grepping
  `homeNavUID` across every `.js`, `.html` and `.py` file in the worktree
  matches on that one line alone, so the handler has no definition at all.
- The other inert one is `pages/menus/tree.html`, entered only by a
  `{% page_menu %}` tag, and no such tag survives in the live chrome: grepping
  `page_menu` across `themes/NextSeek/templates/` and `seek/templates/` finds
  it in that file's own recursive self-call and nowhere else
  (`themes/NextSeek/templates/pages/menus/tree.html:20`), so an edit here
  reaches no page.
- The theme's dead `content.embed.html` also shadows the live-looking
  `seek/templates/content.embed.html:2`, so wiring up an include of that name
  gets the theme's copy, not the `seek` file you were reading.
- Two template names this project renders exist in no directory on the search
  path, verified on 2026-09-03 by calling `get_template` on all 30 rendered
  names swept out of the tree: `dmac/views.py:214` and `dmac/views.py:255` both
  raise `TemplateDoesNotExist`. Adding a file of either name here would quietly
  turn two dead code paths back on.
- `themes/media/` sits on no path Django serves: `STATICFILES_DIRS` names only
  the theme's own static dir and the repo-root one (`dmac/settings.py:88-91`),
  and `MEDIA_ROOT` is the container path `/media` (`dmac/settings.py:95`). A
  `{% static %}` or media URL aimed at one of its 8 logo files therefore
  renders a broken image instead of failing loudly.
- `UI.md:117` still describes the home page as `direct_to_template` rendering
  `index.html` into `content.embed.html`. Both halves are stale — the route is
  `dmac.views.home` (`dmac/urls.py:48`) and the fragment is dead — so do not
  use that table to decide which file to edit.

## Test command

```
docker run --rm --network=none -v "$PWD":/app:ro,z -v /app/.venv -w /app \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest seek/tests/test_admin_template_gating.py \
  seek/tests/test_catalog_crosslinks.py seek/tests/test_seek_urls_context.py \
  seek/tests/test_seek_public_links.py --no-migrations -q -p no:cacheprovider
```

2026-09-03: 47 passed, 0 failed, 1.28s. Nothing under this directory collects
under pytest on its own. See `themes/README.md` for why the anonymous
`/app/.venv` volume is required and what each of those four modules checks.

## See also

- See `themes/README.md` for the loader precedence, the block table and the
  full inbound/outbound edge list.
- See `dmac/CLAUDE.md` for the routing half of the sign-out link that
  `themes/NextSeek/templates/accounts/includes/user_panel.html:38` reverses.
- See `chat_frontend/README.md` for the React panel, which this tree neither
  loads nor styles.
- See `UI.md` for the page-by-page route and view map, read with the staleness
  warning above.
- See `DEPLOYMENT.md` for what a rebuild does and does not replace.
