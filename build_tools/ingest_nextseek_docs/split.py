"""Fence-aware H1 splitter for GitBook-derived markdown."""
from __future__ import annotations

import re
from dataclasses import dataclass

_H1_RE = re.compile(r"^#\s+(\S.*)$")
_FENCE_RE = re.compile(r"^\s*```")
_SLUG_NONALNUM_RE = re.compile(r"[^a-z0-9]+")

MAX_DESCRIPTION_LEN = 140
EMPTY_DESCRIPTION_FALLBACK = "(section overview)"


@dataclass
class Section:
    """One top-level GitBook page."""

    ordinal: int
    title: str
    slug: str
    body: str
    description: str


def split_by_h1(markdown: str) -> list[Section]:
    """Split markdown into sections at H1 boundaries, ignoring fenced code."""
    if not markdown.strip():
        return []

    lines = markdown.splitlines(keepends=True)
    inside_fence = False
    pending_title: str | None = None
    pending_body_lines: list[str] = []
    raw_sections: list[tuple[str, str]] = []

    def flush() -> None:
        if pending_title is not None:
            raw_sections.append((pending_title, "".join(pending_body_lines)))

    for line in lines:
        if _FENCE_RE.match(line):
            inside_fence = not inside_fence
            if pending_title is not None:
                pending_body_lines.append(line)
            continue
        if not inside_fence:
            match = _H1_RE.match(line.rstrip("\n"))
            if match:
                flush()
                pending_title = match.group(1).strip()
                pending_body_lines = [line]
                continue
        if pending_title is not None:
            pending_body_lines.append(line)

    flush()

    sections: list[Section] = []
    used_slugs: dict[str, int] = {}
    for ordinal, (title, body) in enumerate(raw_sections, start=1):
        sections.append(
            Section(
                ordinal=ordinal,
                title=title,
                slug=_slugify(title, used_slugs),
                body=body,
                description=_extract_description(body),
            )
        )
    return sections


def _slugify(title: str, used_slugs: dict[str, int]) -> str:
    base = _SLUG_NONALNUM_RE.sub("-", title.lower()).strip("-")
    if not base:
        base = "section"

    candidate = base
    suffix = 2
    while candidate in used_slugs:
        candidate = f"{base}-{suffix}"
        suffix += 1

    used_slugs[candidate] = 1
    return candidate


def _extract_description(body_with_h1: str) -> str:
    """Return the first non-empty paragraph after the H1, truncated if needed."""
    lines = body_with_h1.splitlines()
    index = 1
    while index < len(lines) and not lines[index].strip():
        index += 1

    paragraph_lines: list[str] = []
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            break
        if line.lstrip().startswith("#"):
            break
        paragraph_lines.append(line.strip())
        index += 1

    paragraph = " ".join(paragraph_lines).strip()
    if not paragraph:
        return EMPTY_DESCRIPTION_FALLBACK
    return _truncate_at_word_boundary(paragraph, MAX_DESCRIPTION_LEN)


def _truncate_at_word_boundary(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text

    head = text[:max_len]
    last_space = head.rfind(" ")
    if last_space <= 0:
        return head.rstrip() + "…"
    return head[:last_space].rstrip() + "…"
