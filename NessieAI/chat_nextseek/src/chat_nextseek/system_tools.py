"""The system agent's lookups: the NExtSEEK user docs and the curated catalogs, as tools.

The system agent used to be handed every sample type and assay row in full on every call, and never the
user docs, so "How do I upload samples?" was answered from the capability notes alone (dev launch
2026-10-01). It now gets an index of each (``docs_index``, ``catalog_index``) and looks the rest up:

* ``read_doc``: one page of ``themes/NextSeek/docs`` (``ChatConfig.DOCS_DIR``), or one section of it.
  The pages are the ones the docs README lists, read on every call: on the boxes the folder is
  bind-mounted, so it is always the text the site serves at ``/docs/<slug>/``.
* ``get_catalog_entry``: the full row of one sample type, assay, project, investigation or lab.
* ``list_catalog``: sample types or assays that match, with the count computed here, not by the model.

Each returns a JSON-able dict with ``ok``. ``docs_footer`` turns the pages an answer cited into links,
keeping only the pages ``read_doc`` returned this turn.
"""
from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Same rule as seek/views/pages.py:_TOC_PAGE: "- [Title](slug.md)" lines, in order.
_TOC_PAGE = re.compile(r"^- \[(.+?)\]\(([\w-]+)\.md\)")
_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$")

KINDS = ("sample_type", "assay", "project", "investigation", "lab")
LIST_KINDS = ("sample_type", "assay")
NEAR_MAX = 5


@dataclass(frozen=True)
class DocPage:
    slug: str
    title: str
    text: str
    headings: tuple[tuple[int, str], ...]  # (level, heading) for each ## and ### line

    @property
    def url(self) -> str:
        return f"/docs/{self.slug}/"


def load_docs(docs_dir: Any) -> dict[str, DocPage]:
    """The pages the docs README lists, in its order, keyed by slug. Empty when there is no docs folder.

    A listed page with no file is skipped rather than failing the turn.
    """
    if not isinstance(docs_dir, (str, os.PathLike)) or not docs_dir:
        return {}
    root = Path(docs_dir)
    try:
        toc = (root / "README.md").read_text(encoding="utf-8")
    except OSError:
        return {}
    pages: dict[str, DocPage] = {}
    for line in toc.splitlines():
        m = _TOC_PAGE.match(line)
        if not m or m[2] in pages:
            continue
        try:
            text = (root / f"{m[2]}.md").read_text(encoding="utf-8")
        except OSError:
            continue
        headings = tuple(
            (len(h[1]), h[2]) for h in (_HEADING.match(ln) for ln in text.splitlines()) if h
        )
        pages[m[2]] = DocPage(slug=m[2], title=m[1], text=text, headings=headings)
    return pages


def docs_index(pages: dict[str, DocPage]) -> str:
    """One line per page: its title, address, slug and section headings."""
    if not pages:
        return "(No user docs are available on this instance.)"
    return "\n".join(
        f"- {p.title}: {p.url} (slug {p.slug}). Sections: {'; '.join(h for _, h in p.headings) or 'none'}"
        for p in pages.values()
    )


def read_doc(pages: dict[str, DocPage], slug: Any, heading: Any = None) -> dict:
    """A page's text, or the section under ``heading`` (case-insensitive) down to the next heading at its level."""
    page = pages.get(str(slug or "").strip().lower())
    if page is None:
        if not pages:
            return {"ok": False, "error": "The user docs are not available on this instance."}
        return {"ok": False, "error": f"No docs page {slug!r}.", "slugs": list(pages)}
    out = {"ok": True, "slug": page.slug, "title": page.title, "url": page.url}
    if not heading or not str(heading).strip():
        return {**out, "text": page.text}
    want = str(heading).strip().lower()
    lines = page.text.splitlines()
    start = level = None
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if not m:
            continue
        if start is None and m[2].lower() == want:
            start, level = i, len(m[1])
        elif start is not None and len(m[1]) <= level:
            return {**out, "heading": lines[start], "text": "\n".join(lines[start:i]).strip()}
    if start is None:
        return {"ok": False, "error": f"No section {heading!r} on {page.slug}.",
                "headings": [h for _, h in page.headings]}
    return {**out, "heading": lines[start], "text": "\n".join(lines[start:]).strip()}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_map(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _names(value: Any) -> list[str]:
    """Alternative names, whether stored as a list or as one comma-separated string."""
    if isinstance(value, str):
        return [n.strip() for n in value.split(",") if n.strip()]
    return [n for n in _as_list(value) if isinstance(n, str) and n.strip()]


def _rows(config, kind: str) -> list[tuple[list[str], dict]]:
    """(the keys a row answers to, the row) for one kind of catalog entry."""
    if kind == "sample_type":
        rows = _as_map(getattr(config, "FULL_SAMPLETYPES_MAP", None)).values()
        return [([r.get("SampleType"), r.get("Name")], r) for r in rows]
    if kind == "assay":
        rows = _as_map(getattr(config, "FULL_ASSAYS_MAP", None)).values()
        return [([r.get("Name"), *_names(r.get("Alternative Assay Names"))], r) for r in rows]
    if kind in ("project", "investigation"):
        attr = "FULL_PROJECTS_MAP" if kind == "project" else "FULL_INVESTIGATIONS_MAP"
        rows = _as_map(getattr(config, attr, None)).values()
        return [([r.get("name"), *_names(r.get("alternative_names"))], r) for r in rows]
    if kind == "lab":
        return [([r.get("code"), r.get("name")], r) for r in _as_list(getattr(config, "LABS", None))
                if isinstance(r, dict)]
    return []


def get_catalog_entry(config, kind: Any, key: Any) -> dict:
    """The row whose code, name or alternative name equals ``key`` (case-insensitive), else near names."""
    if kind not in KINDS:
        return {"ok": False, "error": f"kind must be one of {list(KINDS)}."}
    want = str(key or "").strip().lower()
    rows = _rows(config, kind)
    for keys, row in rows:
        if any(isinstance(k, str) and k.strip().lower() == want for k in keys):
            return {"ok": True, "kind": kind, "row": row}
    names = [k for keys, _ in rows for k in keys if isinstance(k, str) and k.strip()]
    by_lower = {n.lower(): n for n in names}
    near = difflib.get_close_matches(want, list(by_lower), n=NEAR_MAX, cutoff=0.5)
    return {"ok": False, "kind": kind, "error": f"No {kind} named {key!r} in the catalog.",
            "near": [by_lower[n] for n in near]}


def list_catalog(config, kind: Any, clade: Any = None, contains: Any = None) -> dict:
    """Sample types (optionally of one clade) or assays whose code, name or alternative name contains
    ``contains``, with their exact count."""
    if kind not in LIST_KINDS:
        return {"ok": False, "error": f"kind must be one of {list(LIST_KINDS)}."}
    want_clade = str(clade or "").strip().lower()
    want_text = str(contains or "").strip().lower()
    out = []
    for keys, row in _rows(config, kind):
        if kind == "sample_type" and want_clade and str(row.get("Clade") or "").lower() != want_clade:
            continue
        if want_text and not any(isinstance(k, str) and want_text in k.lower() for k in keys):
            continue
        if kind == "sample_type":
            out.append({"code": row.get("SampleType"), "name": row.get("Name"), "clade": row.get("Clade")})
        else:
            out.append({"name": row.get("Name")})
    filters = {k: v for k, v in (("clade", clade), ("contains", contains)) if v}
    if kind == "assay" and clade:
        filters["note"] = "clade filters sample types only; it was ignored for assays."
    return {"ok": True, "kind": kind, "filters": filters, "count": len(out), "rows": out}


def catalog_index(config) -> str:
    """Every sample type (code, name, clade), every assay, project and investigation name: one per line."""
    types = [r for _, r in _rows(config, "sample_type")]
    assays = [r for _, r in _rows(config, "assay")]
    projects = [r.get("name") for _, r in _rows(config, "project")]
    investigations = [r.get("name") for _, r in _rows(config, "investigation")]
    parts = [f"Sample types ({len(types)}):"]
    parts += [f"- {r.get('SampleType')} {r.get('Name')} [{r.get('Clade') or 'no clade'}]" for r in types]
    parts += [f"Assays ({len(assays)}):"] + [f"- {r.get('Name')}" for r in assays]
    parts += [f"Projects: {', '.join(n for n in projects if n) or 'none'}"]
    parts += [f"Investigations: {', '.join(n for n in investigations if n) or 'none'}"]
    return "\n".join(parts)


def docs_footer(pages: dict[str, DocPage], cited: Any, read: set[str], narrative: str) -> tuple[str, list[str], list[str]]:
    """(the "See:" line, the cited slugs kept, the cited slugs dropped).

    A slug is kept only when ``read_doc`` returned that page this turn; a kept page the narrative already
    links gets no second link. The line is empty when nothing needs adding.
    """
    kept, dropped = [], []
    for slug in _names(cited) if isinstance(cited, str) else _as_list(cited):
        slug = str(slug).strip().lower()
        if slug in kept or slug in dropped:
            continue
        (kept if slug in read and slug in pages else dropped).append(slug)
    links = [f"[{pages[s].title}]({pages[s].url})" for s in kept if pages[s].url not in narrative]
    return (f"See: {', '.join(links)}" if links else ""), kept, dropped


def tool_schemas(*, final: bool = False) -> list[dict]:
    """The system agent's tools. ``final`` offers only ``answer`` (the loop's last pass)."""
    answer = {
        "name": "answer",
        "description": "Give your final answer. Every turn ends with this call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["get_capabilities", "get_entities", "get_searches"]},
                "narrative": {"type": "string", "description": "The full user-facing answer, in markdown."},
                "entities_consulted": {"type": "array", "items": {"type": "string"}},
                "docs_cited": {"type": "array", "items": {"type": "string"},
                               "description": "The slugs of the docs pages the answer used."},
                "notes": {"type": "string", "description": "A brief internal reasoning note."},
            },
            "required": ["mode", "narrative"],
        },
    }
    if final:
        return [answer]
    return [
        {
            "name": "read_doc",
            "description": "Read one page of the NExtSEEK user docs, or one section of it.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "The page's slug, from DOCS_INDEX."},
                    "heading": {"type": "string", "description": "A section heading from DOCS_INDEX. Omit for the whole page."},
                },
                "required": ["slug"],
            },
        },
        {
            "name": "get_catalog_entry",
            "description": "The full catalog row for one sample type, assay, project, investigation or lab, "
                           "by its code, name or alternative name.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "key": {"type": "string"},
                },
                "required": ["kind", "key"],
            },
        },
        {
            "name": "list_catalog",
            "description": "List sample types or assays, with their exact count. clade (Source, Processed, Raw, "
                           "Analyzed) filters sample types; contains matches a code, name or alternative name.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(LIST_KINDS)},
                    "clade": {"type": "string"},
                    "contains": {"type": "string"},
                },
                "required": ["kind"],
            },
        },
        answer,
    ]
