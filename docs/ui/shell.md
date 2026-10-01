# Page shell, navigation, auth layout and home

## What this covers

The frame every page sits in: which template directory wins, `base.html` (sidebar, drawer, scrim,
hamburger, main, footer, the blocks child templates fill), `nav.embed.html` (the sidebar links and
Quick Access), the user panel (signed in and signed out), the footer, the auth layout
(`base_auth.html`, `login.html`), the home page, the responsive behaviour of the shell and what a
phone sees, the active-link script, and how the Ask Nessie link attaches.

It does not cover the content of individual pages ([pages.md](pages.md)), the CSS token system
([styles.md](styles.md)), the other scripts ([javascript.md](javascript.md)), the embedded chat app
([chat-frontend.md](chat-frontend.md)) or the dead template inventory ([legacy.md](legacy.md)).
Start at [README.md](README.md) if you are new to the guide.

## How it works

### Which template wins

`TEMPLATES` in `dmac/settings.py` has one backend with three loaders, searched in this order:

1. `mezzanine.template.loaders.host_themes.Loader` (per-theme dirs of installed theme apps).
2. `django.template.loaders.filesystem.Loader` with `DIRS` = only `themes/NextSeek/templates`.
3. `django.template.loaders.app_directories.Loader`: the `templates/` folder of each entry in
   `INSTALLED_APPS`, in order (`seek`, `themes.NextSeek`, admin, ..., `mezzanine.*`).

So the theme folder beats everything for a shared name. `base.html`, `index.html`,
`accounts/includes/user_panel.html` and `pages/menus/tree.html` resolve to
`themes/NextSeek/templates/`, never to Mezzanine's packaged copies. Mezzanine's own templates are the
last fallback. Pages that the theme does not override (for example the profile pages behind the
`profile` and `profile_update` URL names) come from Mezzanine's packaged templates and extend the
theme's `base.html`.

The repo-root `templates/` folder (a Mezzanine scaffold: `base.html`, `accounts/`, `mobile/`,
`pages/`) is in neither `DIRS` nor any app, so it is never loaded. Editing it changes nothing. There
is also no device detection: `MIDDLEWARE` has no `TemplateForDeviceMiddleware` and nothing defines
`DEVICE_USER_AGENTS`, so Mezzanine's `mobile/` templates are never chosen. One responsive template
set serves phones and desktops.

Context processors that shell templates rely on: `request`, `auth`, `mezzanine.conf.context_processors.settings`,
`mezzanine.pages.context_processors.page`, and `dmac.context_processors.seek_urls` (supplies
`seek_forgot_password_url` to `login.html`).

### Shell structure

```
body.nextseek-app
  aside#sidebar.sidebar        fixed, left, width var(--sidebar-width) = 256px, z-index 1000
    .sidebar-header            BMC logo + NExtSEEK wordmark (link to /)
    nav#sidebarNav.sidebar-nav {% block left_panel %} -> include nav.embed.html   (scrolls)
    .sidebar-foot              include includes/user_panel.html                   (pinned)
  .sidebar-scrim               z-index 999, visible only while body.sidebar-open
  div#main-wrapper             margin-left 256px; 0 below 992px
    div.mobile-topbar.d-lg-none      phone top bar, sticky: hamburger (button.mobile-toggle),
                                     wordmark, and Sign in for visitors
    main#content.content     {% block main %}
    footer#footer.footer     include page-footer.embed.html
  <script> bootstrap bundle (CDN), js/nextseek.js, then {% block extra_js %}
```

Libraries loaded by `base.html`: Bootstrap 5.3.3 and Bootstrap Icons 1.11.3 (jsdelivr), Inter (Google
Fonts), jQuery EasyUI 1.5.2 (static), and `css/nextseek.css`. `datagrid-filter.js` is deliberately not
loaded in the shell (a comment in `base.html` explains why); pages that need it load it themselves
after EasyUI. Third-party script tags here are security item SEC-0930-F (tracked privately).

### Blocks child templates fill

| Template | Block | Purpose |
|---|---|---|
| `base.html` | `title` | `<title>` text |
| `base.html` | `extra_head` | extra `<link>`/`<style>` in `<head>` |
| `base.html` | `left_panel` | sidebar nav; defaults to `nav.embed.html`, no page overrides it today |
| `base.html` | `main` | the page content |
| `base.html` | `extra_js` | scripts after `nextseek.js` |
| `base_auth.html` | `title`, `extra_head`, `body`, `extra_js` | auth shell has no `main` and no sidebar |

Django drops anything in a child template that sits outside a block, so content must go in `main`.

### Sidebar contents (`nav.embed.html`)

| Section | Items |
|---|---|
| Data | Home `/`; Sample Search `/seek/search/`; Data Entry (collapse `#dataEntrySubmenu`: Assay Sheet Upload `/seek/samples/upload/`, Data & Protocol Upload `/seek/data/upload/`); Data Query (collapse `#dataQuerySubmenu`: `/seek/datafile/query/`, `/seek/sop/query/`); Projects `/seek/projects/`; Useful Info (collapse `#usefulInfoSubmenu`: external docs site, `/seek/templates/`, `/seek/sampletypes/`, `/seek/assays/`) |
| Quick Access | Ask Nessie (`includes/nessie_button.html`), UID search input `#search-uid`, "+ New sample" link `.qa-cta` to `/seek/samples/upload/` |
| Admin | only when `request.user.is_superuser`: `/admin`, `/seek/samples/attributes`, `/seek/admin/clades/`, `/seek/admin/internal_assays/` |
| Resources | Getting Started `/seek/help/`, Published Studies (external), Contact Support (mailto to the team address) |

The Admin test is `is_superuser`, not `is_staff`, on purpose: the login code sets `is_staff` for every
SEEK user, so `is_staff` would show the section to everyone (see the comment in the template). The
user panel uses the same rule for its "Admin" or "Researcher" badge. Signed-out visitors see only the
links that work without a sign-in (Home, Documentation, Resources): every other Data link and the
whole Quick Access group sit inside `{% if request.user.is_authenticated %}`.

### The user panel

`includes/user_panel.html` is a switch that includes `accounts/includes/user_panel.html` when
`mezzanine.accounts` is installed (and the shop panel if `cartridge.shop` is, which it is not). The
accounts panel has two states:

| State | Markup | Links |
|---|---|---|
| Signed in | `.user-panel`: avatar (first two letters of the username), name, role badge, three-dot button `.user-menu-btn` calling `toggleUserMenu()` | Profile (`{% url "profile" username %}`), Update profile (`profile_update`), Sign out (`{% url 'logout' %}?next=<path>`, Mezzanine's logout) |
| Signed out | `.user-panel--anon` with `a.btn-signin` | `/login/?next=<current path>` |

### Footer

`page-footer.embed.html`: the FAIRDATA logo and "(c) <year> NExtSEEK - MIT BioMicro Center", rendered
inside `footer#footer` by `base.html`.

### Auth layout and login

`login.html` extends `base_auth.html` (body class `nextseek-auth`, fonts Playfair Display and Source
Sans 3 instead of Inter, no jQuery or EasyUI, different favicon tag). It is a two-panel page
(`.auth-shell`): a crimson brand panel (`.auth-panel-left`: wordmark, tagline, one line of copy, the
FAIRDATA logo) and the form (`.auth-panel-right`: username, password, "Stay signed in", Sign in,
then Sign up and Reset links). All of its CSS is inline in `{% block extra_head %}` of `login.html`,
not in `nextseek.css`.

- Served by `dmac.views.login_seek`, routed as `^login` and again as `^accounts/login/` in
  `dmac/urls.py` (security items SEC-0930-A and SEC-0930-I, tracked privately).
- Sign up: there is no signup template. `signup_seek` (in `dmac/views.py`) redirects to SEEK's own
  `/signup` on `SEEK_PUBLIC_URL`. The `^accounts/signup/` route is placed before the Mezzanine
  catch-all on purpose, so Mezzanine's local signup form stays unreachable.
- Reset password: the link goes to SEEK (`seek_forgot_password_url`), falling back to Mezzanine's
  reset URL if `SEEK_PUBLIC_URL` is unset.
- Protected pages redirect anonymous users with `requires_seek_login_redirect` in
  `seek/decorators.py`, which redirects to `/login/?next=<the literal the view passes>` (or a bare
  `/login/` when it passes none) when the SEEK login check fails. The target is fixed at decoration
  time, not taken from the request.
- Legacy `/logout`: `logout_seek` in `dmac/views.py` (route `^logout$`) is broken and returns HTTP 500
  (security item SEC-0930-D, tracked privately). The sidebar does not use it.

### Home page

`dmac.views.home` (route `^$` in `dmac/urls.py`) renders `themes/NextSeek/templates/index.html` with
counts (`total_samples_count`, `total_projects_count`, `total_data_files_count` and the weekly
deltas), `recent_samples` (last four by id), and `home_projects` from `_home_projects()` (up to 8
projects the caller may see: all for a superuser, membership-scoped otherwise, empty for anyone
unresolved). Each block has its own try/except, so a failing lookup leaves a zero instead of a 500.
Logged-out visitors get the page too (security item SEC-0930-C, tracked privately).

Template sections: `.dash-hero` (signed in: "Welcome back", "Welcome, <username>"; signed out:
"Welcome to NExtSEEK" and a Sign in button, `.dash-signin`),
`.dash-tiles` (three count tiles), `.dash-row` with `.dash-card` (recent samples) and `.dash-actions`
(New sample, the Ask Nessie button include, Sample Types, Assays), and `.dash-projects` (logo grid).
Styles are the `.dash-*` rules in `nextseek.css`.

### Breakpoints the shell uses

| Width | Effect | Where |
|---|---|---|
| 992px and up | sidebar fixed and always visible; hamburger hidden by `d-lg-none` | `nextseek.css` `.sidebar`, `#main-wrapper`; `base.html` |
| up to 991.98px | sidebar becomes an off-canvas drawer (`translateX(-100%)`, opens with `body.sidebar-open`), `#main-wrapper` margin 0, `.content` padding 1.25rem 1rem; 44px touch targets for `.mobile-toggle`, `.sidebar-nav .nav-link`, `.user-menu-btn`, `.qa-input input`, `.qa-cta` | `nextseek.css` "Mobile / Responsive" and "Touch targets" media blocks |
| up to 767.98px | `.d-only` hides and `.m-only` shows; every text-like input forced to 16px (stops iOS zoom); login page stacks and hides tagline, copy and logo | `nextseek.css`; `login.html` inline CSS |
| up to 575.98px | `.content` padding 1rem 0.875rem | `nextseek.css` |
| 400px and other page rules | page-specific, see [styles.md](styles.md) | `nextseek.css` |

The drawer breakpoint lives in three places that must stay in step: the two `max-width: 991.98px`
blocks in `nextseek.css` and `d-lg-none` on the hamburger in `base.html`. A comment in `nextseek.css`
near the responsive section lists "768 / 576 / 400" and omits 992, so do not trust it.

### Drawer and active-link script (`themes/NextSeek/static/js/nextseek.js`)

| Function | What it does |
|---|---|
| `initSidebar` | outside-click closes the drawer; wires submenu toggles (see gotchas) |
| `initActiveNavLink` | on DOMContentLoaded, adds `.active` to each `.sidebar-nav .nav-link` whose `href` is a prefix of `location.pathname` (and is not `/` or `#`), opens the parent `.submenu` and sets `aria-expanded`; `/` is active only on exactly `/`; `.active` is removed from Home on any other path |
| `openSidebar` / `closeSidebar` | toggle `body.sidebar-open`, lock body scroll, set `aria-expanded` on the hamburger, move focus into the drawer and back |
| keydown handlers | Esc closes; a Tab focus trap runs while the drawer is open |
| `navUID` | Enter in `#search-uid` goes to `/seek/sampletree/uid=<value>/` |
| `toggleUserMenu` | opens and closes the user menu in the sidebar foot |

Active state is client-side only. Absolute `https:` and `mailto:` hrefs never match. Because it is a
prefix test, a link whose href is a prefix of another page's path also lights up there.

### Ask Nessie link

There is no React mount in the shell. Ask Nessie is a plain anchor, `includes/nessie_button.html`
(`a.nessie-btn`, href `/seek/assistant/`, logo image that hides itself on error), included by
`nav.embed.html` (Quick Access) and by `index.html` (home actions). The route goes to the assistant
page, which is covered in [chat-frontend.md](chat-frontend.md). Styles are the "Nessie promoted
button" rules in `nextseek.css` (`.nessie-btn`). `nextseek.js` needs no handler for it.

## What a phone (390px) sees

| Case | What is on screen | How to reach sign in or sign out |
|---|---|---|
| Home, signed out | sticky top bar (hamburger, wordmark, Sign in), then the dashboard with a Sign in button in the hero | Sign in in the top bar or the hero; also at the bottom of the drawer |
| Home, signed in | sticky top bar (hamburger, wordmark), heading says "Welcome, <username>" | hamburger, drawer foot card, three-dot menu: Profile, Update profile, Sign out |
| Protected page, signed out | redirected (302) to `/login/`, usually with a `next` target; `/seek/assistant/` shows an access error instead (UI-020) | n/a |
| `/login/` | brand strip (wordmark only) above the form; no sidebar, no hamburger | n/a |
| `/seek/help/` | top bar, article, footer (logo above the copyright line below 576px) | top bar |

The drawer shows the wordmark, the nav sections, Quick Access (signed in only) and the user panel
pinned at the bottom. It is `height: 100dvh` (with a `100vh` fallback for old browsers), so the pinned
panel stays inside the visible area while a phone browser shows its toolbar. The drawer mode also
applies to tablets and landscape phones (anything under 992px).

The viewport meta tag is present in both full-document templates (`base.html`, `base_auth.html`).
There are no other full-document templates in the theme or in `seek/templates`.

## Where to edit

| Task | Files and symbols | Easy to miss |
|---|---|---|
| Change, add or reorder sidebar links | `themes/NextSeek/templates/nav.embed.html` | Active highlighting is automatic (prefix match) but only for hrefs starting with `/`. Submenus need a unique `id` matching the trigger's `href="#id"` |
| Show a link to admins only | same file, inside `{% if request.user.is_superuser %}` | never use `is_staff` |
| Give one page its own sidebar | override `{% block left_panel %}` in that page | no page does this today |
| Change the phone top bar or hamburger | `base.html` (`div.mobile-topbar`, `button.mobile-toggle`), `nextseek.css` `.mobile-topbar`, `.mobile-toggle` and the touch-target block | keep `d-lg-none` and the CSS breakpoint in step; `nextseek.js` finds the hamburger by `.mobile-toggle` |
| Change the drawer breakpoint | `nextseek.css` (two `991.98px` blocks) and `d-lg-none` in `base.html` | all three together |
| Change sidebar width or colours | `nextseek.css` `:root` (`--sidebar-width`, `--ns-sidebar-bg`) | `#main-wrapper` margin uses the same variable |
| Sign in, Sign out, Profile links | `themes/NextSeek/templates/accounts/includes/user_panel.html` | the `includes/user_panel.html` switch must keep including it |
| Login page layout and copy | `themes/NextSeek/templates/login.html` (inline CSS plus `{% block body %}`) | CSS is inline, not in `nextseek.css`; fonts differ from the shell |
| Login behaviour | `dmac.views.login_seek`; protected-page redirect: `seek/decorators.py` `requires_seek_login_redirect` | changes here are security-sensitive, see known issues |
| Footer | `themes/NextSeek/templates/page-footer.embed.html` | |
| Home page content | `themes/NextSeek/templates/index.html`; data in `dmac.views.home` and `_home_projects` | add new context keys in the view, each in its own try/except |
| Home styles | `.dash-*` rules in `nextseek.css` | |
| Ask Nessie button | `themes/NextSeek/templates/includes/nessie_button.html`; `.nessie-btn` in `nextseek.css` | one file feeds both the sidebar and the home page |
| Site head, fonts, CDN libs, favicon | `base.html` head (auth pages: `base_auth.html`) | the two favicon tags differ (png vs ico) |
| Drawer or active-link behaviour | `themes/NextSeek/static/js/nextseek.js`: `initSidebar`, `initActiveNavLink`, `openSidebar`, `closeSidebar` | |
| Add a page that uses the shell | new template with `{% extends "base.html" %}` and `{% block main %}` | put it in `themes/NextSeek/templates/` or `seek/templates/`; name clashes are resolved theme-first |

## Gotchas

- Editing the repo-root `templates/` folder does nothing. Edit `themes/NextSeek/templates/`.
- Theme beats app: a template with the same name in `themes/NextSeek/templates/` silently shadows one
  in `seek/templates/` or Mezzanine.
- nginx caches `/static/` for 30 days. `nextseek.css` and `nextseek.js` are loaded through
  `{% static %}`, so they get content-hashed names and change as soon as the box re-collects static
  (an app restart). Anything loaded by an unhashed path can stay stale in the browser; hard-reload
  before deciding a deploy failed. See [ci-and-deploy.md](ci-and-deploy.md). (`themes/CLAUDE.md`
  still says the theme CSS is unhashed; that is out of date, UI-185.)
- Locally, the bind-mount setup can make edits to theme templates invisible (the nested mount wins).
  Check which copy the container actually reads before debugging.
- Django `{# #}` comments are single-line only. Multi-line ones render as visible text, so the
  templates use `{% comment %}` blocks.
- Submenu toggles are bound twice: by Bootstrap's `data-bs-toggle="collapse"` and by a manual
  handler in `initSidebar`. It works because Bootstrap ignores the second call during the
  transition. Remove one, not both, if you touch it.
- Nav links do not close the drawer, which is fine because navigation reloads the page.
- The sidebar carries `role="dialog" aria-modal="true"` permanently, including on desktop where it is
  not a dialog.
- `base_auth.html` has no jQuery, EasyUI or `nextseek.js`. Do not put shell widgets on the login page.
- `content.embed.html` and `pages/menus/tree.html` in the theme are not used by the live shell; see
  [legacy.md](legacy.md).

## Known issues

Full list: [known-issues.md](known-issues.md#shell). The ones that matter most here:

- Login, legacy logout, login route and home page security items: SEC-0930-A, -C, -D and -I
  (tracked privately).
