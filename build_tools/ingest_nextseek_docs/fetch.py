"""Fetch GitBook site-index pages and combine their Markdown content."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

_HTML_PREFIXES = ("<!doctype html", "<html")
# NExtSEEK change over the verbatim dmac-assistant port: in 2026-07 GitBook
# dropped the ": Querying This Documentation" suffix from the trailing
# boilerplate heading. The bare marker is a prefix of the legacy one, so it
# matches both formats.
_AGENT_INSTRUCTIONS_MARKER = "\n---\n\n# Agent Instructions"
_LLMS_BANNER_TOKEN = "llms.txt"
_SLUG_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class GitBookPage:
    """One page entry from GitBook's site-index."""

    title: str
    pathname: str
    markdown_url: str


def fetch_source_bytes(url: str) -> bytes:
    """Fetch raw bytes from a URL, following redirects."""
    logger.info("Fetching source bytes from: %s", url)
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
    logger.info("Fetched %d bytes", len(response.content))
    return response.content


def load_gitbook_markdown_corpus(site_index_url: str) -> str:
    """Load the GitBook site-index and concatenate page Markdown in index order."""
    pages = load_site_index_pages(site_index_url)
    if not pages:
        raise ValueError("GitBook site-index contained zero pages")

    documents: list[str] = []
    for page in pages:
        source = fetch_source_bytes(page.markdown_url)
        markdown = strip_gitbook_llms_banner(source.decode("utf-8"))
        _validate_markdown_page(page, markdown)
        stripped = strip_gitbook_agent_instructions(markdown)
        documents.append(_demote_nested_h1s(stripped).strip())

    corpus = "\n\n".join(documents).strip()
    logger.info("Loaded %d GitBook Markdown pages", len(pages))
    return corpus + "\n"


def load_site_index_pages(site_index_url: str) -> list[GitBookPage]:
    """Return site-index pages with resolved per-page Markdown URLs."""
    raw = fetch_source_bytes(site_index_url)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("GitBook site-index was not valid JSON") from exc

    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list):
        raise ValueError("GitBook site-index missing pages list")

    pages: list[GitBookPage] = []
    for raw_page in raw_pages:
        if not isinstance(raw_page, dict):
            raise ValueError("GitBook site-index page entry was not an object")
        title = raw_page.get("title")
        pathname = raw_page.get("pathname")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("GitBook site-index page missing title")
        if not isinstance(pathname, str) or not pathname.startswith("/"):
            raise ValueError(f"GitBook page {title!r} has invalid pathname")
        pages.append(
            GitBookPage(
                title=title.strip(),
                pathname=pathname,
                markdown_url=_resolve_markdown_url(site_index_url, title, pathname),
            )
        )
    return pages


def strip_gitbook_llms_banner(markdown: str) -> str:
    """Remove GitBook's leading llms.txt banner blockquote (upstream, 2026-07).

    NExtSEEK addition over the verbatim dmac-assistant port: GitBook began
    prepending a `> ... llms.txt ...` blockquote to every page's Markdown
    export, breaking the H1-first page contract. Only a leading blockquote
    that mentions llms.txt is stripped; any other leading content still fails
    validation loudly.
    """
    lines = markdown.splitlines(keepends=True)
    if not lines or not lines[0].startswith(">") or _LLMS_BANNER_TOKEN not in lines[0]:
        return markdown
    idx = 0
    while idx < len(lines) and lines[idx].startswith(">"):
        idx += 1
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    return "".join(lines[idx:])


def strip_gitbook_agent_instructions(markdown: str) -> str:
    """Remove GitBook's repeated trailing agent-query boilerplate."""
    marker_at = markdown.find(_AGENT_INSTRUCTIONS_MARKER)
    if marker_at < 0:
        return markdown
    return markdown[:marker_at].rstrip() + "\n"


def _demote_nested_h1s(markdown: str) -> str:
    """Keep only the page title as H1 so each site-index page is one section."""
    lines = markdown.splitlines(keepends=True)
    seen_h1 = False
    normalized: list[str] = []
    for line in lines:
        if line.startswith("# "):
            if seen_h1:
                normalized.append("#" + line)
                continue
            seen_h1 = True
        normalized.append(line)
    return "".join(normalized)


def _resolve_markdown_url(site_index_url: str, title: str, pathname: str) -> str:
    parsed = urlparse(site_index_url)
    root_path = parsed.path.split("/~gitbook/", 1)[0].rstrip("/")
    origin = f"{parsed.scheme}://{parsed.netloc}"

    if pathname.rstrip("/") == root_path:
        page_path = f"{root_path}/{_slugify_title(title)}.md"
    else:
        page_path = f"{pathname.rstrip('/')}.md"

    return urljoin(origin, page_path)


def _slugify_title(title: str) -> str:
    slug = _SLUG_NONALNUM_RE.sub("-", title.lower()).strip("-")
    if not slug:
        raise ValueError("cannot resolve root GitBook page with empty title slug")
    return slug


def _validate_markdown_page(page: GitBookPage, markdown: str) -> None:
    stripped = markdown.lstrip()
    lower_head = stripped[:200].lower()
    if lower_head.startswith(_HTML_PREFIXES):
        raise ValueError(f"GitBook page {page.title!r} returned HTML")
    first_line = stripped.splitlines()[0] if stripped else ""
    if not first_line.startswith("# "):
        raise ValueError(f"GitBook page {page.title!r} did not start with an H1")
    actual_title = first_line[2:].strip()
    if actual_title == "Page Not Found":
        raise ValueError(f"GitBook page {page.title!r} resolved to Page Not Found")
    if actual_title != page.title:
        raise ValueError(
            f"GitBook page {page.title!r} returned unexpected H1 {actual_title!r}"
        )
