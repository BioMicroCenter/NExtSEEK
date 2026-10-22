"""Static informational pages: the user docs under /docs/."""

import re
from pathlib import Path

import markdown
from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.templatetags.static import static

# The docs are markdown in the theme, which is bind-mounted on the boxes, so a text edit goes
# live with a git pull. README.md there is the table of contents: "## Section" lines and
# "- [Title](slug.md)" lines, in order. Links and images in the pages are real relative paths
# (other.md, ../static/docs/img/x.png) so they also work on GitHub and pass ci/docs_map.py.
DOCS_DIR = Path(settings.PROJECT_ROOT) / "themes" / "NextSeek" / "docs"
_TOC_PAGE = re.compile(r"^- \[(.+?)\]\(([\w-]+)\.md\)")
_PAGE_LINK = re.compile(r'href="([\w-]+)\.md(#[^"]*)?"')
_STATIC_REF = re.compile(r'(src|href)="\.\./static/([^"]+)"')


def getting_started(request):
    """The old help page. The docs replaced it."""
    return redirect("/docs/", permanent=True)


def docs_toc():
    """[(section title, [(slug, page title), ...]), ...] from the docs README."""
    sections = []
    for line in (DOCS_DIR / "README.md").read_text().splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections and (m := _TOC_PAGE.match(line)):
            sections[-1][1].append((m[2], m[1]))
    return sections


def docs_page(request, slug=None):
    """One docs page; /docs/ itself shows the first page of the table of contents."""
    toc = docs_toc()
    pages = [page for _, section_pages in toc for page in section_pages]
    slugs = [s for s, _ in pages]
    i = slugs.index(slug) if slug in slugs else (0 if slug is None else None)
    if i is None:
        raise Http404("No such docs page")
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "attr_list", "admonition", "toc"],
        extension_configs={"toc": {"toc_depth": "2-2"}},
    )
    body = md.convert((DOCS_DIR / f"{slugs[i]}.md").read_text())
    body = _PAGE_LINK.sub(lambda m: f'href="/docs/{m[1]}/{m[2] or ""}"', body)
    body = _STATIC_REF.sub(lambda m: f'{m[1]}="{static(m[2])}"', body)
    return render(request, "docs/page.html", {
        "toc": toc,
        "slug": slugs[i],
        "title": pages[i][1],
        "body": body,
        "outline": md.toc_tokens,
        "prev": pages[i - 1] if i > 0 else None,
        "next": pages[i + 1] if i + 1 < len(pages) else None,
    })
