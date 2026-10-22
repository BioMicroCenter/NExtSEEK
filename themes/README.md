# themes/ — the server-rendered surface

## What this is

> Counts and line numbers below are from 2026-09-03. On 2026-10-01 the dead `content.embed.html` copies, the repo-root `templates/` scaffold, `dmac/templates/` and the EasyUI `demo*/` folders were deleted, so the file counts for those are now lower.

Every Django template NExtSEEK renders lives under `themes/NextSeek/templates/` or
`seek/templates/`. `themes/NextSeek/templates` is the single filesystem directory in the
template search path (`dmac/settings.py:105-135`), and `themes.NextSeek` is additionally
registered as an app (`dmac/settings.py:146`) so the app-directories loader finds the same
tree a second time. The repo-root `templates/` scaffold (an untouched `mezzanine-project`
copy that was in no search path) was deleted on 2026-10-01 after every template name in it,
in the theme and in `seek/templates/` (119 names) was resolved through the real loaders and
none landed in it.

This boundary is the server-rendered chrome only. The React chat panel is a
separate boundary that enters through one `{% vite_assets %}` call in a `seek`
template (`seek/templates/smartSearch.html:8`), not through anything here.

## Surface

There are no functions and no imports here, so "the surface" is **the pages the
loader can resolve, the blocks they define, and the order that decides which
copy of a name wins**. Citations therefore land on the line carrying the tag —
a `{% block %}`, an `{% extends %}`, an `{% include %}`, or the settings entry
that puts a directory on the path — not on a `def`.

### Name resolution, in order

Three loaders run in sequence (`dmac/settings.py:128-132`):

1. `mezzanine.template.loaders.host_themes.Loader` — inert here. No assignment
   to `HOST_THEMES` exists in the repo: grepping that identifier across the
   whole worktree excluding `.venv` returns nothing, so the loader has no
   host-to-theme map to consult and always falls through.
2. `django.template.loaders.filesystem.Loader` over `DIRS`, whose sole entry is
   `themes/NextSeek/templates` (`dmac/settings.py:108-110`). This resolves every
   name the theme defines, ahead of everything below.
3. `django.template.loaders.app_directories.Loader` over `INSTALLED_APPS` — in
   practice `seek/templates/` (65 HTML files), then `themes/NextSeek/templates`
   a second time, then the installed Mezzanine, admin and DRF packages.

Five theme names also exist in the installed Mezzanine package, and the theme copy wins
all five: `base.html`, `index.html`, `includes/user_panel.html`,
`accounts/includes/user_panel.html` and `pages/menus/tree.html` shadow the
Mezzanine package's copies. (`content.embed.html` was a sixth, shadowing `seek/templates/`; both copies are deleted.)

`DIRS` is nearly redundant, and it is worth knowing which of the two
registrations is load-bearing. Emptying `DIRS` and re-resolving the twelve
theme names on 2026-09-03 moved exactly one: eleven still landed in the theme,
because the app-directories loader reaches the same tree through
`themes.NextSeek` (`dmac/settings.py:146`), and only `content.embed.html`
(since deleted) flipped to `seek/templates/`, because `"seek"` is listed one line earlier
(`dmac/settings.py:145`) and app order decides ties inside that loader.

### The 12 templates of `themes/NextSeek/templates/`

| Role | File | Reached from |
|---|---|---|
| App chrome | `base.html` | 31 `seek` templates plus `index.html` and `themes/NextSeek/templates/docs/page.html` |
| Auth chrome | `base_auth.html` | `themes/NextSeek/templates/login.html:1` |
| Sidebar nav | `nav.embed.html` | `themes/NextSeek/templates/base.html:62` |
| Footer | `page-footer.embed.html` | `themes/NextSeek/templates/base.html:95` |
| User panel switch | `includes/user_panel.html` | `themes/NextSeek/templates/base.html:68` |
| User panel body | `accounts/includes/user_panel.html` | `themes/NextSeek/templates/includes/user_panel.html:8` |
| Home dashboard | `index.html` | `home` in `dmac/views.py` |
| Sign-in page | `login.html` | `dmac/views.py:168` |
| User docs page | `themes/NextSeek/templates/docs/page.html`, rendering the markdown in `themes/NextSeek/docs/` | `seek/views/pages.py` (`docs_page`) |
| Swagger override | `nextseek/swagger_ui.html` | `nextseek_api/urls.py:75` |
| Mezzanine page menu | `pages/menus/tree.html` | nothing in the live chrome |

The last two are unreachable in the request path, and `CLAUDE.md` in this
directory says what that costs you.

### The blocks

`base.html` defines exactly five: `title` (`themes/NextSeek/templates/base.html:7`),
`extra_head` (`themes/NextSeek/templates/base.html:43`), `left_panel`
(`themes/NextSeek/templates/base.html:61`), `main`
(`themes/NextSeek/templates/base.html:90`) and `extra_js`
(`themes/NextSeek/templates/base.html:106`). `base_auth.html` defines four and only four —
`title`, `extra_head`, `body` (`themes/NextSeek/templates/base_auth.html:28`)
and `extra_js`, counted by grepping `{% block` in that 33-line file — so there
is no `main` here, which is why the sign-in page fills `body` instead
(`themes/NextSeek/templates/login.html:255`).

Across the 31 `seek` templates that extend `base.html`, the overrides counted on
2026-09-03 were 31 `main`, 12 `title`, 10 `extra_head` and 3 `extra_js`.
`left_panel` is overridden by none of them, so every signed-in page shows the
same sidebar. Three further overrides — `meta_title`, `meta_keywords` and
`meta_description`, all three in `pages/denied.html:4-16` in `seek/templates/` (file since deleted) — name
blocks this base does not have.

### `themes/NextSeek/static/`

The repo-root `static/` tree behind it is described in `static/README.md`.

Not documented file by file — 992 files on 2026-09-03, of which 797 are the
vendored `jquery-easyui-1.5.2/` tree. What the templates here actually reach
for is small: `css/nextseek.css` (the whole theme, 45 KB, referenced at
`themes/NextSeek/templates/base.html:24`), `js/nextseek.js` (208 lines,
referenced at `themes/NextSeek/templates/base.html:104`, and the definition
site of `openSidebar`, `closeSidebar` and `toggleUserMenu`, all three called
from inline `onclick` attributes in `base.html`), the four EasyUI files loaded
at `themes/NextSeek/templates/base.html:20-21` and
`themes/NextSeek/templates/base.html:31-32`, and `img/`. Extracting all 15
distinct `{% static %}` arguments from the 12 templates on 2026-09-03 and
testing each for existence under `themes/NextSeek/static/` and then `static/`
reported no misses.

## Running and testing

**This boundary has no test lane of its own.** There is no `test_*.py`,
`*_test.py`, `conftest.py` or `tests/` directory anywhere beneath `themes/` or
`templates/` — a `find` over both directories for those four names returns
nothing — and the only two Python files here are the empty package markers
`themes/__init__.py` and `themes/NextSeek/__init__.py`.

What exercises it is four test modules that live in `seek/tests/`: they read
theme templates as text and render two of them through the real Django engine
(`seek/tests/test_catalog_crosslinks.py:17`,
`seek/tests/test_admin_template_gating.py:114`,
`seek/tests/test_seek_public_links.py:35-36`, and
`seek/tests/test_seek_urls_context.py:60-68`). Run them over this checkout in a
throwaway container so the running stack is untouched:

```
docker run --rm --network=none \
  -v "$PWD":/app:ro,z -v /app/.venv -w /app \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  nextseek-nextseek:latest /app/.venv/bin/python -m pytest \
  seek/tests/test_admin_template_gating.py seek/tests/test_catalog_crosslinks.py \
  seek/tests/test_seek_urls_context.py seek/tests/test_seek_public_links.py \
  --no-migrations -q -p no:cacheprovider
```

Run on 2026-09-03: **47 passed, 0 failed, 3 warnings in 1.28s.** The anonymous
`-v /app/.venv` volume is load-bearing — it keeps the image's Linux virtualenv
visible through the read-only bind of the checkout.

The same container is how you answer "which file does this name load?", which
no grep can tell you. Call `django.template.loader.get_template(name).origin.name`
after `django.setup()`; that is the measurement behind every resolution claim
above.

## Depends on / depended on by

Outbound and inbound take different shapes here. Outbound is loader
configuration, template tags and static assets. Inbound is `{% extends %}` from
other apps' templates plus a handful of view-level renders by name — never an
import, because nothing in this directory is importable beyond two empty
`__init__.py` files.

**This tree depends on:**

- Two independent registrations that each resolve the theme on their own,
  `DIRS` (`dmac/settings.py:108-110`) and the installed app
  (`dmac/settings.py:146`), with the same doubling on the static side where
  `STATICFILES_DIRS` names the directory explicitly (`dmac/settings.py:88-91`);
  the cost is that collectstatic walks its 992 files twice, and a dry run on
  2026-09-03 emitted 2,181 "Found another file" warnings in total, of which
  that doubling plus the 801 paths shared with the repo-root `static/` are the
  bulk.
- The context processors that supply `request` and the Mezzanine settings the
  templates read (`dmac/settings.py:112-127`); dropping
  `django.template.context_processors.request` renders every visitor as signed
  out, because the panel branches on `request.user.is_authenticated`
  (`themes/NextSeek/templates/accounts/includes/user_panel.html:3`).
- Mezzanine's `mezzanine_tags` library for `{% ifinstalled %}`, used to guard a
  Cartridge include that would otherwise raise, since `cartridge.shop` is not
  installed (`themes/NextSeek/templates/includes/user_panel.html:3-5`).
- Three URL names it reverses: `logout`
  (`themes/NextSeek/templates/accounts/includes/user_panel.html:38`),
  `signup_seek` and `mezzanine_password_reset`
  (`themes/NextSeek/templates/login.html:328-329`).
- One third-party template it extends by name,
  `drf_spectacular/swagger_ui.html` (`themes/NextSeek/templates/nextseek/swagger_ui.html:1`),
  and one URL name its injected script fetches
  (`themes/NextSeek/templates/nextseek/swagger_ui.html:42`).
- `themes/NextSeek/static/`, reached through `{% static %}` and served only
  after `collectstatic` copies it into the `/static` volume, which the
  entrypoint runs on every container start (`docker/scripts/entrypoint.sh:13`).
- Three CDN origins for Google Fonts, Bootstrap 5 and Bootstrap Icons
  (`themes/NextSeek/templates/base.html:11-17`), so an air-gapped install loses
  the whole visual layer while still rendering HTML.

**This tree is depended on by:**

- 31 templates under `seek/templates/`, each opening with an extends of
  `base.html` (`seek/templates/projectsList.html:1` is one); counted on
  2026-09-03 by grepping every `{% extends %}` target in that directory, which
  yielded that one target and no other.
- Five template names rendered from Python that resolve here, out of 30
  distinct rendered names swept across the tree on 2026-09-03:
  `home` and `login_seek` in `dmac/views.py`, `seek/views/pages.py:7`,
  `nextseek_api/urls.py:75`, and `seek/tests/test_admin_template_gating.py:114`.
- Every Mezzanine-supplied page, indirectly: Mezzanine's own templates extend
  the bare name `base.html`, which resolves to this theme rather than to
  Mezzanine's copy, so a Mezzanine page's `{% block meta_title %}` never
  reaches the `<title>` element it was written for (the Mezzanine package's own `base.html`).
- The four `seek/tests/` modules named under "Running and testing" above, two
  of which read theme files straight off disk by relative path
  (`seek/tests/test_catalog_crosslinks.py:17`), so moving this directory breaks
  them without touching a single import.
- Docker, which bind-mounts this directory over the image copy at runtime
  (`docker-compose.yml:29`), making it the only app-code path changeable
  without a rebuild (`DEPLOYMENT.md:49-52`).

**What is not an edge, and what was left out:**

- Hits inside `themes/NextSeek/static/jquery-easyui-1.5.2/` are vendored demo
  pages naming template filenames in prose
  (`demo/tree/basic.html:44`, folder since deleted); they
  are excluded, and `static/` at the repo root is a sibling boundary excluded
  entirely.
- Two names rendered from `dmac/views.py` resolve to nothing at all
  (`dmac/views.py:214` and `dmac/views.py:255`); they are consumers of this
  boundary that this boundary does not satisfy, so they are recorded as a
  landmine rather than as a dependency.

See `themes/CLAUDE.md` for the invariants, the two deployment traps and the
test command.
