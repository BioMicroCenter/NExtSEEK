"""The user docs at /docs/: the renderer, and every page in themes/NextSeek/docs/."""

import re

import pytest
from django.test import Client

from seek.views import pages

THEME_STATIC = pages.DOCS_DIR.parent / "static"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def tiny_docs(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text(
        "# Docs\n\nIntro, ignored.\n\n## First\n\n- [Page A](a.md)\n\n## Second\n\n- [Page B](b.md)\n"
    )
    (tmp_path / "a.md").write_text(
        "# Page A\n\nSee [B](b.md#part-two) and ![a figure](../static/docs/img/x.png).\n\n"
        "## Part one\n\n!!! note\n    Careful.\n"
    )
    (tmp_path / "b.md").write_text("# Page B\n\n## Part two\n\nText.\n")
    monkeypatch.setattr(pages, "DOCS_DIR", tmp_path)
    return tmp_path


@pytest.mark.django_db
class TestRenderer:
    def test_docs_root_shows_the_first_page(self, client, tiny_docs):
        r = client.get("/docs/")
        assert r.status_code == 200
        assert r.context["slug"] == "a"

    def test_links_and_images_are_rewritten(self, client, tiny_docs):
        body = client.get("/docs/a/").context["body"]
        assert 'href="/docs/b/#part-two"' in body
        assert 'src="/static/docs/img/x.png"' in body
        assert 'class="admonition note"' in body

    def test_outline_prev_next(self, client, tiny_docs):
        a = client.get("/docs/a/").context
        assert [t["id"] for t in a["outline"]] == ["part-one"]
        assert a["prev"] is None and a["next"] == ("b", "Page B")
        assert client.get("/docs/b/").context["prev"] == ("a", "Page A")

    def test_sections_come_from_the_readme(self, tiny_docs):
        assert pages.docs_toc() == [("First", [("a", "Page A")]), ("Second", [("b", "Page B")])]

    @pytest.mark.parametrize("path", ["/docs/nope/", "/docs/README/"])
    def test_unknown_pages_are_404(self, client, tiny_docs, path):
        assert client.get(path).status_code == 404


@pytest.mark.django_db
def test_old_help_page_redirects(client):
    r = client.get("/seek/help/")
    assert r.status_code == 301 and r["Location"] == "/docs/"


# --- the real pages -------------------------------------------------------------------------

TOC_SLUGS = [slug for _, section in pages.docs_toc() for slug, _ in section]


def test_every_page_file_is_in_the_toc():
    files = {p.stem for p in pages.DOCS_DIR.glob("*.md")} - {"README"}
    assert files == set(TOC_SLUGS)


@pytest.mark.django_db
def test_every_page_renders_and_every_link_resolves(client):
    # A TOC entry with no file is reported by test_every_page_file_is_in_the_toc.
    written = [slug for slug in TOC_SLUGS if (pages.DOCS_DIR / f"{slug}.md").is_file()]
    rendered = {slug: client.get(f"/docs/{slug}/") for slug in written}
    used = set()
    ids = {slug: set(re.findall(r'id="([^"]+)"', r.context["body"])) for slug, r in rendered.items()}
    problems = []
    for slug, r in rendered.items():
        body = r.context["body"]
        if r.status_code != 200:
            problems.append(f"{slug}: status {r.status_code}")
        if not body.startswith("<h1") or body.count("<h1") != 1:
            problems.append(f"{slug}: must open with its H1 title and have no other H1")
        for target, anchor in re.findall(r'href="/docs/([\w-]+)/(?:#([^"]*))?"', body):
            if target not in ids:
                problems.append(f"{slug}: links to missing page {target}")
            elif anchor and anchor not in ids[target]:
                problems.append(f"{slug}: links to missing heading {target}#{anchor}")
        for ref in re.findall(r'(?:src|href)="/static/([^"]+)"', body):
            used.add(ref)
            if not (THEME_STATIC / ref).is_file():
                problems.append(f"{slug}: missing static file {ref}")
        for ref in re.findall(r'(?:src|href)="([^"]+)"', body):
            # An absolute link to a repository file on GitHub (NExtSTEPS.md#...) is not a page link.
            if ref.startswith(("http://", "https://")):
                continue
            if ref.endswith(".md") or ".md#" in ref or ref.startswith("../"):
                problems.append(f"{slug}: unrewritten relative link {ref}")
        if re.search(r'<img(?![^>]*\balt="[^"]+")', body):
            problems.append(f"{slug}: an image without alt text")
    if set(TOC_SLUGS) == set(written):
        # The repo is public and every byte stays in its history: no image nobody shows.
        on_disk = {p.relative_to(THEME_STATIC).as_posix() for p in (THEME_STATIC / "docs").rglob("*") if p.is_file()}
        problems += [f"unused image {ref}" for ref in sorted(on_disk - used)]
    assert not problems, "\n".join(problems)


def test_no_emails_or_gitbook_in_the_docs():
    # The contact address lives in the template, not the markdown (public repo).
    sources = list(pages.DOCS_DIR.glob("*.md")) + [
        pages.DOCS_DIR.parent / "templates" / "nav.embed.html",
        pages.DOCS_DIR.parent / "templates" / "page-footer.embed.html",
    ]
    for path in sources:
        text = path.read_text()
        assert "gitbook.io" not in text and "gitbook.com" not in text, path
        if path.suffix == ".md":
            assert not EMAIL_RE.findall(text), path

