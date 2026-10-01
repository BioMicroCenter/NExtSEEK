# Docs and help surfaces

## What this covers

Every place the site tells a user how to use it: the user docs at `/docs/` (markdown pages in the
repo, which replaced the external GitBook site and the old in-app Getting Started page), the API docs
pages (Swagger, ReDoc, schema), the docs, People and Contact links in the sidebar and footer, and the
fact that Nessie's own knowledge of the docs is built from the same markdown. It also says how to write
and add a docs page.

It does not cover the sidebar and footer markup in general (see [shell.md](shell.md)), other pages
([pages.md](pages.md)), or the chat UI ([chat-frontend.md](chat-frontend.md)).

## How it works

The user docs are one markdown file per page in `themes/NextSeek/docs/`. One view renders them inside
the site shell; the same files feed Nessie's docs snapshot.

```
themes/NextSeek/docs/README.md   (table of contents: "## Section", "- [Title](slug.md)")
themes/NextSeek/docs/<slug>.md   (one page each, opens with "# Title")
themes/NextSeek/static/docs/img/<page>/*.png
        |
        +--> seek/views/pages.py:docs_page --> themes/NextSeek/templates/docs/page.html --> /docs/<slug>/
        |
        +--> NessieAI/build_tools/ingest_nextseek_docs --> NessieAI/docker/cc-runtime/docs/nextseek/*.md
                                                           (what Nessie's Claude Code container reads)
```

`docs_page` reads the README for the order, renders the page with Python-Markdown (extensions
`tables`, `fenced_code`, `attr_list`, `admonition`, `toc` with depth 2), then rewrites two kinds of
relative link in the HTML: `other.md#x` becomes `/docs/other/#x`, and `../static/<path>` becomes the
hashed `{% static %}` URL. Writing links as real relative paths keeps the pages readable on GitHub too.
`/docs/` shows the first page in the README; a slug not in the README is a 404. The template builds
the docs menu (with People and Contact), an "On this page" outline from the `##` headings and
previous/next links. The content is trusted repo source, like a template, so it is not sanitised;
raw HTML (a `<figure>`) passes through.

Because `themes/NextSeek/` is bind-mounted on the boxes, a text edit to a page goes live with a
`git pull`. A new image needs an app restart (static is collected at container start), and a change
to the view needs `./startup.sh rebuild`.

## Inventory

### Pages and routes

| Surface | Template or view | Route | Who can see it |
|---|---|---|---|
| User docs | `docs_page` in `seek/views/pages.py`; `themes/NextSeek/templates/docs/page.html` (extends `base.html`) | `/docs/` and `/docs/<slug>/`, one `re_path` named `docs` in `dmac/urls.py`, outside `i18n_patterns` and ahead of the Mezzanine catch-all | Anyone, no login (`ci/routes.py`, `auth="anon"`) |
| Old Getting Started | `getting_started` in `seek/views/pages.py`, a permanent redirect | `/seek/help/` (name `getting_started` in `seek/urls.py`) | 301 to `/docs/` |
| Swagger UI | drf-spectacular `SpectacularSwaggerView` with template override `themes/NextSeek/templates/nextseek/swagger_ui.html`, in `nextseek_api/urls.py` | `/nextseek_api/swagger/` | Logged-in users (`permission_classes=[IsAuthenticated]`); a logged-out browser gets a DRF auth error, not a login redirect |
| ReDoc | `SpectacularRedocView`, no template of ours | `/nextseek_api/redoc/` | Logged-in users |
| OpenAPI schema | `SpectacularAPIView` | `/nextseek_api/schema/` | Logged-in users |
| About page | none, by the operator's ruling: the People link goes to the BMC wiki | none | n/a |

### Where the docs are linked

| From | Element | Link text |
|---|---|---|
| `themes/NextSeek/templates/nav.embed.html`, "Resources" sidebar section | `<a href="/docs/">` | "Getting Started" |
| `nav.embed.html`, "Useful Info" submenu (`#usefulInfoSubmenu`) | `<a href="/docs/">` | "Documentation" |
| `themes/NextSeek/templates/page-footer.embed.html` | `.footer-links` | "Docs", "People" (BMC wiki, new tab), "Contact" (`mailto:` the team address) |
| `themes/NextSeek/templates/docs/page.html`, docs menu | "Get in touch" | "People", "Contact the data team" |

The dead `content.embed.html` still carries the old GitBook link (see [legacy.md](legacy.md)); nothing
renders it.

### Nessie's copy of the docs

| File | Role |
|---|---|
| `NessieAI/build_tools/ingest_nextseek_docs/` | Reads `themes/NextSeek/docs/` in README order, splits at H1s, writes the snapshot below and the `NEXTSEEK-DOCS` block of `NessieAI/docker/cc-runtime/container/CLAUDE.md` |
| `NessieAI/docker/cc-runtime/docs/nextseek/` | The generated snapshot Nessie reads; its README says not to edit by hand |
| `NessieAI/tests/build_tools/` | The ingester's unit and integration tests |

The snapshot is only as current as the last ingester run, and it ships in the cc-agent image. Changing
it changes what Nessie knows, so treat a regenerated snapshot as a Nessie brain change: show the
before and after to the operator.

### Libraries

| Need | Installed | Declared in |
|---|---|---|
| Markdown to HTML | `Markdown` (direct) | `pyproject.toml`; versions in `uv.lock` |
| Project descriptions (not the docs) | `django-markdownify` with `bleach`, `{{ description \| markdownify }}` in `seek/templates/projectPage.html` | `pyproject.toml`, `dmac/settings.py` |

## Writing a docs page

| Rule | How |
|---|---|
| Add a page | Create `themes/NextSeek/docs/` plus the slug and `.md`, opening with `# Title` (one H1 per page: Nessie's ingester splits pages at H1s), and add its line under a `## Section` line in that folder's `README.md`. The README order is the menu order |
| Link another page | The other page's file name, optionally with a heading id (the heading lowercased, punctuation dropped, spaces to hyphens) |
| Link an app page | The site path. No hostnames: the docs are served on every box |
| Add an image | Put it in `themes/NextSeek/static/docs/img/` under a folder named for the page, and link it by its relative path from the page, or use a raw `figure` with a caption. Real alt text on every image |
| A note box | `!!! note` or `!!! warning`, then the text indented four spaces |
| Public repo | No email addresses (the contact address lives in the templates), no personal names or usernames, no internal hostnames, paths or secrets. Check screenshots for login names |
| Check it | `seek/tests/test_docs_pages.py`: every README entry has a file and every file an entry; every page renders and opens with its H1; every docs link, heading anchor and image resolves; every image has alt text; no email or GitBook link |

The same rules as markdown, in a page:

```markdown
# Uploading

See [the data model](data-model.md) and [where to upload](uploading.md#where-to-upload).
Templates are on the [Templates page](/seek/templates/).

![The Sample Search form with NHP selected](../static/docs/img/searching-downloading/sample-search.png)

<figure>
  <img src="../static/docs/img/data-model/clades.png" alt="Four layers: Source, Processed, Raw, Analyzed" loading="lazy">
  <figcaption>The four clades.</figcaption>
</figure>

!!! note
    Text indented four spaces.
```

`ci/docs_map.py` skips `themes/NextSeek/docs/` (its relative-link rule would reject site paths like
`/seek/templates/`); the test above covers those pages instead.

## Where to edit

| Task | Files and steps |
|---|---|
| Change a docs page | `themes/NextSeek/docs/<slug>.md`; then refresh Nessie's snapshot (below) |
| Change the docs layout, menu or outline | `themes/NextSeek/templates/docs/page.html`; styles in the "User docs" section of `themes/NextSeek/static/css/nextseek.css` |
| Change how markdown is rendered | `seek/views/pages.py` (`docs_page`, `docs_toc`); `seek/tests/test_docs_pages.py` pins the behaviour |
| Change the sidebar docs or Contact Support entries | `themes/NextSeek/templates/nav.embed.html` (Resources section and `#usefulInfoSubmenu`) |
| Change footer links | `themes/NextSeek/templates/page-footer.embed.html` |
| Change the Swagger look | `themes/NextSeek/templates/nextseek/swagger_ui.html`; route in `nextseek_api/urls.py` |
| Refresh what Nessie knows of the docs | From the repo root, `python -m NessieAI.build_tools.ingest_nextseek_docs`; review the diff under `NessieAI/docker/cc-runtime/` with the operator; then rebuild the cc-agent image |
| Add any other public page | Declare a `Route` in `ci/routes.py` and, for a root-level route, its pattern in `_PROJECT_LEVEL` (`ci/gate/live_routes.py`); see [ci-and-deploy.md](ci-and-deploy.md) |

## Gotchas

- The docs route is one pattern with an optional slug, `^docs/(?:(?P<slug>[\w-]+)/)?$`; the gate and
  `ci/routes.py` must carry that exact string.
- Only slugs listed in the docs README are served. A page file missing from the README is invisible on
  the site, and the test fails.
- nginx caches `/static/` for 30 days. Images are referenced through `{% static %}`, so they get hashed
  names, but `nextseek.css` changes still need a hard reload if anything references them unhashed.
- Mezzanine's catch-all in `dmac/urls.py` swallows any route mounted after it. Mount new pages
  before it.
- `docs/ui/` is for developers, `themes/NextSeek/docs/` is for users. Do not cross-link developer
  docs from the user pages.

## Known issues

See [known-issues.md](known-issues.md#docs-and-help). The ones that matter most here:

- "Contact" and "Contact Support" are `mailto:` links only, which do nothing without a mail client.
- The Swagger, ReDoc and schema pages answer logged-out visitors with an API auth error instead of a
  login redirect.
