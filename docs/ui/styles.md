# Styles

## What this covers

How the site is styled: the CSS layers every page loads and their order, what the login page loads
instead, the dead Bootstrap 3 / Mezzanine chain, the design tokens (`--ns-*`), fonts, icons,
breakpoints, where page-level `<style>` blocks live, and how `nextseek.css` retunes EasyUI. It ends
with a recommended token set and breakpoint set to converge on (a recommendation, not the tree).

It does not cover the page shell markup (sidebar, footer, blocks): see [shell.md](shell.md). It does
not cover JavaScript behaviour, including the EasyUI widgets themselves: see [javascript.md](javascript.md).
The React chat app has its own styling: see [chat-frontend.md](chat-frontend.md). Unused stock
Mezzanine templates are covered in [legacy.md](legacy.md).

Paths are relative to the repo root. Facts checked at caa55340.

## How it works

Every app page extends `themes/NextSeek/templates/base.html`. Its `<head>` loads these, in this
order, and later wins at equal specificity:

```
1  Google Fonts: Inter 400/500/600/700
2  Bootstrap 5.3.3 css             (CDN, jsdelivr)
3  Bootstrap Icons 1.11.3 css      (CDN, jsdelivr)
4  jquery-easyui-1.5.2/themes/default/easyui.css   (local static)
5  jquery-easyui-1.5.2/themes/icon.css             (local static)
6  css/nextseek.css                (local static, the theme)
7  {% block extra_head %}          (page-level <link>/<style>, after everything)
```

So there are three CSS layers: Bootstrap, EasyUI, then `nextseek.css`. The theme file recolours a few
Bootstrap classes (`.btn-primary`, `.btn-outline-primary`, `.form-control`, `.form-select`,
`.alert-danger`, `.card`, in the "Bootstrap Component Overrides" section) and retunes EasyUI
(`.panel`, `.tabs`, `.window`, `.combo`, `.textbox`, `.l-btn`, `.datagrid*`), mostly by using
`!important` (about 260 uses in the file). Bootstrap and EasyUI overlap in names and do not know about
each other, so the override has to win by force rather than by specificity.

`<body class="nextseek-app">` is set in `base.html`. Only the base `body.nextseek-app` rule uses it;
it is available as a scoping hook.

The auth pages use `themes/NextSeek/templates/base_auth.html` instead (only `login.html` extends it).
It loads Playfair Display and Source Sans 3 (not Inter), Bootstrap 5.3.3, Bootstrap Icons and
`nextseek.css`. It does not load EasyUI or jQuery. `login.html` carries about 245 lines of its own
`<style>` for the two-panel layout.

Static files come from two roots, `themes/NextSeek/static/` first and then the repo-root `static/`
(`STATICFILES_DIRS` in `dmac/settings.py`; the theme copy wins a name clash). They are served from the
`/static` volume after `collectstatic`, which the entrypoint runs on container start, under
content-hashed names for anything referenced through `{% static %}`.
Other CSS comes from the `seek` app templates as inline `<style>` blocks; there is no other linked
theme stylesheet. The timeline page links a Vite-built bundle (`static/js/sample_timeline/`).

### The dead Bootstrap 3 / Mezzanine chain

`static/css/` at the repo root holds `bootstrap.css`, `bootstrap-theme.css`, `bootstrap-rtl.css` and
`mezzanine.css` (Bootstrap 3 era, about 8,600 lines). They are linked only from the stock
`templates/base.html`, which the theme's `base.html` shadows, so no app page loads them. The same stock
`base.html` links `cartridge.css` and `cartridge.rtl.css`. (The stock `templates/mobile/` folder, which linked more missing files, was deleted.)

The visible leftover is Bootstrap 3 vocabulary in live templates: `glyphicon-*` icon classes (no font is
loaded, so they render blank) and `.well` (removed in Bootstrap 5; `nextseek.css` restyles it by hand).

## Design tokens

All tokens live in one `:root` block at the top of `themes/NextSeek/static/css/nextseek.css`. Templates
use `var(--ns-*)` widely (muted, border and crimson are the most used), so a change there reaches the
whole site. Some templates still carry raw hex values beside the token.

| Group | Properties | Notes |
|---|---|---|
| Brand | `--ns-crimson` #A31F34, `--ns-crimson-hover` #8a1a2b, `--ns-crimson-soft` #fdf0f2, `--ns-crimson-border` #f3c6ce | MIT red. `login.html` repeats the raw hex a few times |
| Clade | `--ns-clade-source`, `-processed`, `-raw`, `-analyzed` and a `-tint` of each | Same colours as `dmac.clades`; used by catalog and project pages (`.cat-*`, `.clade-*`) |
| Neutrals | `--ns-bg`, `--ns-white`, `--ns-ink` #1a1a2e, `--ns-body` #2d2d33, `--ns-muted` #6b7280, `--ns-border` #eaeaea | `--ns-ink` as rgb also drives every shadow |
| Surfaces | `--ns-sidebar-bg`, `--ns-sidebar-hover`, `--ns-sidebar-active`, `--ns-footer-bg`, `--ns-header-bg` | `--ns-header-bg` is unused (comment says so) |
| Type | `--ns-font-display`, `--ns-font-body`, `--ns-font-mono` | Display and body are the same stack, `'Inter', 'Source Sans 3', system-ui, sans-serif` |
| Layout | `--sidebar-width` 256px, `--header-height` 0, `--footer-height` 50px | Note: no `--ns-` prefix. `--header-height` and `--footer-height` are read by `sample_timeline.html` |
| Elevation | `--ns-shadow-xs`, `-sm`, `--ns-shadow`, `--ns-shadow-md` | There is no `--ns-shadow-lg`; the modal uses a raw shadow |
| Motion | `--ns-ease`, `--ns-transition` | |
| EasyUI compat | `--easyui-bg`, `-border`, `-hover`, `-selected`, `-active`, `-text` | Used inside the EasyUI override sections |
| Legacy aliases | `--primary-color`, `--sidebar-bg`, `--sidebar-text`, `--sidebar-hover`, `--sidebar-active`, `--content-bg`, `--header-bg`, `--footer-bg` | No use found in the theme, `seek/templates` or `templates`. Safe to delete after a fresh grep |

There are no tokens for spacing, radii, font sizes, row stripe colour, focus ring or status colours.
Those values are repeated as literals:

| Scale | What is in use today |
|---|---|
| Font sizes | 30+ distinct rem values, many within 0.01rem of each other (0.82, 0.83, 0.85, 0.875); body is 15px / 1.65; headings weight 600 |
| Radii | 3, 4, 5, 6, 7, 8, 9, 10, 11 (pill chips), 999px, 50%; effective scale 4 / 6 / 8 / 10 / pill |
| Spacing | rem steps 0.25, 0.35, 0.4, 0.5, 0.6, 0.75, 1, 1.25, 1.5, 2, 3; content padding 1.5rem desktop |
| Focus ring | `0 0 0 3px rgba(163,31,52,0.1)` typed out several times, sometimes `var(--ns-crimson-soft)` |
| Zebra row | `#e6f2ff` declared in nine template `<style>` blocks, but never shown: `nextseek.css` forces `.datagrid-row-alt` to `var(--ns-bg)` with `!important` |
| Status | Off-token one-offs: notice `#fff8e1` / `#f0c040` / `#78600a`, green `#1a7f4b`, dark reds `#8b1a2e` and `#8d1c2d` |

## Fonts

| Font | Where loaded | Used by |
|---|---|---|
| Inter 400 to 700 | `base.html`, Google Fonts | All app pages, through `--ns-font-body` |
| Playfair Display 400/600/700, Source Sans 3 300 to 700 (+ italic 400) | `base_auth.html` only | Login page. Because Inter is not loaded there, the `--ns-font-*` stacks fall through to Source Sans 3 |
| JetBrains Mono, Fira Code | Never loaded | Named first in `--ns-font-mono`, so monospace text falls back to Courier New |
| Other stacks | `login.html` (raw `'Source Sans 3'`), `seek/templates/pages/samples_tree.embed.html` (Lucida Grande), the Sample flow iframe page generated in `nextseek_api/services/sampletype_connections.py` (system sans) | Not tokenised |

The header comment in `nextseek.css` still says "Playfair retired"; that is true for the app shell
only.

## Icons

| Source | Class | State |
|---|---|---|
| Bootstrap Icons 1.11.3 (CDN) | `bi bi-*` | The house icon set, used across the shell and newer pages |
| EasyUI sprites | `icon-*` from `jquery-easyui-1.5.2/themes/icon.css` | Used by EasyUI buttons, tabs and tree nodes |
| Bootstrap 3 glyphicons | `glyphicon glyphicon-*` | Dead: no font loaded. Still in `seek/templates/pages/searchAdvanced_search.embed.html`, `samples_search.embed.html`, `searchAdvanced_newsearch.embed.html`, and stock `templates/accounts/includes/user_panel*.html`, `generic/includes/comment.html`, `twitter/tweets.html` |

No Font Awesome. All CDN loads are a dependency for styling (see the security item in Known issues).

## Breakpoints

Bootstrap's own cut points are 576 / 768 / 992 (written as max-width 575.98 / 767.98 / 991.98). The
file mixes those with off-grid values. Every `@media` in use:

| Where | max-width | What changes |
|---|---|---|
| `nextseek.css` "Mobile / Responsive" | 991.98px | Sidebar becomes an off-canvas drawer (`#main-wrapper` margin-left 0), smaller content padding |
| same | 575.98px | Smaller content padding |
| `nextseek.css` "Utility" section (`.easyui-mobile-notice`) | 768px | Shows the "desktop only" notice on EasyUI-heavy pages |
| `nextseek.css` "Home dashboard" | 900px (two blocks) | `.dp-grid` gap; `.dash-tiles` and `.dash-row` go to one column |
| `nextseek.css` "Mobile view v1" | 767.98px | `.d-only` hidden, `.m-only` shown; all text inputs forced to 16px (stops iOS zoom); `.m-stats-cards` shown; help TOC and project card touch targets |
| same | 991.98px | 44px touch targets for the drawer toggle, nav links, user menu, quick search, `.qa-cta` |
| same | 575.98px / 400px | Dashboard heading sizes, row wrapping, footer padding |
| `themes/NextSeek/templates/login.html` | 767.98px | Auth shell stacks, marketing copy hidden |
| `seek/templates/projectPage.html` | 575.98px | Avatar and header grid |
| `seek/templates/projectsList.html` | 600px | Card grid |
| `seek/templates/templatesList.html` | 900px, 600px | Two columns, then one |
| `seek/templates/catalog_styles.html` | 767.98px | `.assay-compare` to one column |
| `seek/templates/sampleAttributes.html` | 991.98px | Attribute tray insets |

Distinct cut points: 400, 575.98, 600, 767.98, 768, 900, 991.98. Between 768 and 991px the drawer is
already on, but the 16px inputs and the desktop notice are not. The mobile class pair `.d-only` /
`.m-only` (defined in "Mobile view v1") is how a page renders two versions of one block; check that a
page using `.d-only` has an `.m-only` twin. `.easyui-page-wrapper` (a horizontal-scroll wrapper defined
near the notice) has no users in templates.

The sidebar uses `height: 100vh; overflow: hidden` and no `dvh` unit is used anywhere, so on phones
the bottom of the drawer (the Sign in button) can sit under the browser toolbar.

## Where page-level CSS lives

Shared CSS is supposed to go in `nextseek.css`, but many pages carry their own `<style>` block. About
20 templates do, plus about 300 `style="..."` attributes across `seek/templates`,
`themes/NextSeek/templates` and `templates`.

| Template | Block holds |
|---|---|
| `themes/NextSeek/templates/login.html` | Whole login layout (largest: about 245 lines) |
| `seek/templates/sampleAttributes.html` | Attribute editor (about 390 lines; shared parts were moved into `nextseek.css` "attrs-* shared components") |
| `seek/templates/projectsList.html`, `projectPage.html` | Project cards, clade table, header, `.project-diagram` iframe box |
| `seek/templates/templatesList.html` | Templates grid (`.tpl-*`) |
| `seek/templates/catalog_styles.html` | The `.cat-*` family, included by `assaysList.html`, `sampleTypesList.html`, `sampleTypeDetail.html`, `assayDetail.html` and `project_samples.html` through `{% block extra_head %}` |
| `seek/templates/pages/samples_tree.embed.html`, `samples.embed.html`, `*_stable.embed.html`, `*_table.embed.html` | Small EasyUI grid tweaks |
| `themes/NextSeek/templates/nextseek/swagger_ui.html` | Swagger page |
| `seek/templates/searchAdvanced.html`, `sample_timeline.html` | Small blocks |
| `nextseek_api/services/sampletype_connections.py` | The Sample flow iframe page builds its own `<style>` as a Python string |

Also `seek/templates/error.html` uses `--ns-*` tokens
inline in the markup.

Copy-pasted rule families (change one, change all):

| Family | Copies |
|---|---|
| `.datagrid-row-alt { background: #e6f2ff; }` | Six templates: `searchAdvanced.html` and `pages/` `samples_table`, `datafile_table`, `sops_table`, `samples_stable`, `samples_new_stable` (`.embed.html`). All dead: the `!important` rule in `nextseek.css` wins |
| `.ns-page-title` | Four identical copies (1.8rem): `projectsList.html`, `sampleAttributes.html`, `templatesList.html`, and as `.cat-page .ns-page-title` in `catalog_styles.html` |
| Pill chips (11px radius, mono 0.72 to 0.74rem) | `.cat-chip`, `.tpl-chip`, `.stat-chip`, `.attrs-chip` (in `nextseek.css`), `.project-types a` |
| Focus-ring shadow | Literals in `nextseek.css` and templates |

## The EasyUI retuning sections of nextseek.css

Find these by their banner comments (line numbers move):

| Section banner | What it does |
|---|---|
| "EasyUI Theme Overrides" | Panels, tabs, windows, buttons, combos, textboxes; the largest block |
| "EasyUI datagrid retune" | Header, cell, row and pager look for every datagrid |
| Comment "Search tabs: flow the rigid EasyUI region layout" | Scoped to `#search_tab`; lets the criteria form reflow. A following rule must stay after it |
| "Sample search UI rework" | Search page look |
| "Association workbench" through "toolbar" | Workbench grid geometry, row expander caret, page skin for `internal_assays.html` and `clades.html`. Uses `!important` on purpose to beat earlier datagrid rules |
| "Utility" | `.easyui-mobile-notice` and its 768px display rule (the later "EasyUI desktop-only notice" banner holds only a comment pointing back here) |

EasyUI sets layout with inline pixel sizes set by its JS, so Bootstrap's grid and the CSS above cannot
reflow those pages on a phone. `seek/templates/batchUpload.html` ("+ New sample") is the worst case
(tabs 800px tall, layout regions 700 and 760px).

Only the `default` EasyUI theme is linked. `themes/NextSeek/static/jquery-easyui-1.5.2/themes/` also
holds black, bootstrap, gray, material and metro theme folders and a `mobile.css`, unused.

## Recommended direction (not the current tree)

Keep the `--ns-*` names and prune and extend the single `:root` block. As a target:

- Keep: the brand, clade, neutral, shadow, motion and font tokens. Keep `--easyui-*` while the EasyUI
  overrides remain.
- Add: `--ns-crimson-focus` (the focus ring),
  status colours (`--ns-warn-bg/-border/-text`, `--ns-danger-bg/-text`, `--ns-ok`), a type scale
  (`--ns-text-xs` 0.72rem, `-sm` 0.82rem, `-base` 0.9rem, `-lg` 1.05rem, `-h3` 1.15rem, `-h2` 1.5rem,
  `-h1` 1.8rem), radii (`--ns-radius-sm` 5px, `--ns-radius` 8px, `--ns-radius-lg` 10px,
  `--ns-radius-pill` 999px) and spacing (`--ns-space-1` to `-6`, 0.25rem to 2rem). Fix the mono stack
  (load the font or use `ui-monospace, SFMono-Regular, Menlo, monospace`).
- Remove: the legacy aliases and `--ns-header-bg`. Keep `--header-height` until `sample_timeline.html` stops reading it.
- Delete the nine dead `.datagrid-row-alt` template rules; rows already use `--ns-bg`.
- Breakpoints: converge on Bootstrap's 575.98 / 767.98 / 991.98 and choose one of them as "mobile" for
  both nav and forms. Custom properties cannot be used inside `@media`, so the values can only be
  documented, not tokenised.
- Structure: move shared families (page title, chips) into `nextseek.css`, keep only
  page-specific blocks in templates, and consider scoping EasyUI overrides under `.nextseek-app` to
  drop `!important`.

## Where to edit

| Task | Where and how |
|---|---|
| Change the brand colour | Edit `--ns-crimson`, `-hover`, `-soft`, `-border` in the `:root` block of `themes/NextSeek/static/css/nextseek.css`. Then grep for the raw `#A31F34`, `#8a1a2b`, `#8b1a2e`, `rgba(163,31,52` (login.html, focus rings, EasyUI sections, the legacy and `--easyui-active` aliases) because not everything uses the token |
| Add a token | Add it to that same `:root` block, name it `--ns-*`, and use it with `var(--ns-...)`. Do not define tokens in a template `<style>`; `base.html` loads the theme first so templates can read them |
| Style a new page | Extend `base.html`, use Bootstrap 5 classes and `--ns-*` tokens, put page-only rules in `{% block extra_head %}` (inside `<style>`), and put anything a second page will need in `nextseek.css`. Do not copy `.ns-page-title` or chip rules again. Content outside a block is dropped by Django |
| Restyle EasyUI grids | Edit "EasyUI datagrid retune" in `nextseek.css` for all grids (row colours are set there with `!important`; the per-template `#e6f2ff` rules have no effect). Expect to need `!important` |
| Add a phone layout for a page | Use `@media (max-width: 767.98px)` (or 991.98px if it must match the drawer). Either add a `.d-only` / `.m-only` pair, or make the block reflow. Do not add a new cut point. For EasyUI pages, replace inline pixel sizes (the real fix), since CSS cannot override sizes set by EasyUI JS |
| Load another font or library version | `themes/NextSeek/templates/base.html` (app) and `base_auth.html` (login) separately; they are not shared |
| Change the login look | `themes/NextSeek/templates/login.html` (`<style>` block) |
| Change the Sample flow diagram look | The `<style>` string in `nextseek_api/services/sampletype_connections.py`; the iframe box is `.project-diagram` in `seek/templates/projectPage.html` |
| Change drawer, toggle or touch targets | Sections "Sidebar", "Mobile / Responsive", "Mobile toggle", "Mobile view v1" and "Touch targets" in `nextseek.css`; markup is in [shell.md](shell.md) |

## Gotchas

- A CSS change reaches users only after `collectstatic`, which runs on container start: restart the app
  after a `git pull` of a theme CSS change. `nextseek.css` is loaded through `{% static %}`, so its URL
  carries a content hash and browsers fetch the new file at once. A file referenced by a plain path
  (`{{STATIC_URL}}...` or a hard-coded `/static/...`) keeps its URL and nginx lets browsers cache it
  for 30 days, so hard-reload when one of those looks stale. `themes/CLAUDE.md` still calls the theme
  CSS unhashed; that is out of date (UI-185).
- Local bind mounts can hide theme edits; see `themes/README.md` and `themes/CLAUDE.md` for the
  loader and mount rules before debugging "my CSS change does nothing".
- Load order decides ties: Bootstrap, EasyUI, then `nextseek.css`, then the page's `extra_head`. A rule
  that seems ignored is usually losing to a later or `!important` one.
- Some workbench rules deliberately depend on source order inside `nextseek.css` (comment "MUST stay
  after the rule above"). Do not reorder blocks.
- The app and login pages load different font families, so a token that looks right in the app can
  render differently on login.
- Bootstrap, Bootstrap Icons and the fonts come from CDNs; EasyUI and `nextseek.css` are local.
  Offline or blocked networks lose Bootstrap, icons and fonts but still get the HTML. A `glyphicon`
  class never renders.
- Fixed pixel sizes in templates (file inputs at 220px, the
  `.attrs-table` at 1120px inside a scroll wrapper) are what break phone layouts, not the stylesheet.

Owner docs: `themes/README.md` (what the theme depends on, static and CDN notes), `themes/CLAUDE.md`.

## Known issues

See [known-issues.md#styles](known-issues.md#styles). The ones that matter most:

- The drawer's Sign in button can be unreachable on phones (`100vh`, no scrolling).
- Three different breakpoint conventions and more than 30 font sizes make responsive and visual
  changes hard to keep consistent.
- Login and app load different fonts; mono never loads; `glyphicon` icons render blank.
- Third-party CSS and font loading from CDNs (security item SEC-0930-F, tracked privately).
