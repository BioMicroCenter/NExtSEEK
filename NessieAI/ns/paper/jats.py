"""JATS full text → sections, captions and the supplement list (spec §4.1 evidence IDs).

Paragraph IDs are E:<slug>:p<n>, numbered per slug in document order; captions are
E:cap:<n>. Floats (figures, tables, supplementary material) are read only as captions
and supplements, never as section paragraphs. Anything that is not a readable JATS <article>
(broken XML, another root, nesting too deep to walk) raises JatsUnreadable; an article with no
body is read as empty text, which the launch check reports as missing sections.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit

from NessieAI.ns.paper.models import Paragraph, PaperText, Section, SupplementRef

_XLINK = "{http://www.w3.org/1999/xlink}href"
_FLOATS = frozenset({"fig", "fig-group", "table-wrap", "table-wrap-group", "supplementary-material"})
_SKIP = _FLOATS | {"title", "label"}
_ROLES = [
    ("data_availability", re.compile(
        r"data[- ](?:and (?:code|materials|software) )?availab|availability of (?:data|materials)"
        r"|accession (?:codes|numbers)|data access|deposited data", re.I)),
    ("methods", re.compile(
        r"method|experimental (?:procedures|section)|materials and"
        r"|experimental model and subject details|quantification and statistical analysis", re.I)),
]


class JatsUnreadable(ValueError):
    """The full text is not a JATS article this module can read."""


def classify(title: str, sec_type: str = "") -> str:
    probe = f"{sec_type} {title}"
    for slug, pattern in _ROLES:
        if pattern.search(probe):
            return slug
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40] or "section"


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def _plain_text(el) -> str:
    """Text and tails of el, leaving out float subtrees (their tails are kept)."""
    parts = [el.text or ""]
    for child in el:
        if child.tag not in _FLOATS:
            parts.append(_plain_text(child))
        parts.append(child.tail or "")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _file_name(href: str) -> str:
    """The file an href names: its last path part, without query or fragment, URL-decoded."""
    path = urlsplit(href.strip()).path if "://" in href else href.strip().split("?", 1)[0].split("#", 1)[0]
    return unquote(path.rsplit("/", 1)[-1]).strip()


_ROLE_SLUGS = frozenset(slug for slug, _ in _ROLES)


def parse_jats(xml: bytes) -> PaperText:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise JatsUnreadable(f"the full text is not well-formed XML ({exc})") from exc
    if root.tag != "article":
        raise JatsUnreadable(f"the full text's root is <{root.tag[:80]}>, not a JATS <article>")
    try:
        return _read(root)
    except RecursionError as exc:
        raise JatsUnreadable("the full text is nested too deeply to read") from exc


def _read(root) -> PaperText:
    sections: dict[str, Section] = {}
    counters: dict[str, int] = {}

    def add(el, kind_attr: str, inherited: str | None = None) -> None:
        title = _text(el.find("title"))
        own = classify(title, el.get(kind_attr, ""))
        slug = own if inherited is None or own in _ROLE_SLUGS else inherited
        section = sections.setdefault(slug, Section(slug=slug, title=title, paragraphs=[]))

        def walk(parent) -> None:
            for child in parent:
                if child.tag in _SKIP:
                    continue
                if child.tag == "p":
                    text = _plain_text(child)
                    if text:
                        counters[slug] = counters.get(slug, 0) + 1
                        section.paragraphs.append(Paragraph(id=f"E:{slug}:p{counters[slug]}", text=text))
                elif child.tag == "sec":
                    add(child, "sec-type", slug)
                else:
                    walk(child)

        walk(el)

    body = root.find("body")
    if body is not None:
        for child in body:
            if child.tag == "sec":
                add(child, "sec-type")
        if any(child.tag == "p" for child in body):
            loose = ET.Element("sec")
            loose.extend(child for child in body if child.tag == "p")
            add(loose, "sec-type")
    back = root.find("back")
    if back is not None:
        for child in back:
            if child.tag == "sec":
                add(child, "sec-type")
            elif child.tag == "notes":
                add(child, "notes-type")

    captions = []
    for el in root.iter():
        if el.tag in ("fig", "table-wrap"):
            text = f"{_text(el.find('label'))} {_text(el.find('caption'))}".strip()
            if text:
                captions.append(Paragraph(id=f"E:cap:{len(captions) + 1}", text=text))

    supplements: dict[str, SupplementRef] = {}  # a file listed twice (in the body and the back) is one supplement
    for el in root.iter("supplementary-material"):
        media = el.find("media")
        href = (media.get(_XLINK) if media is not None else None) or el.get(_XLINK)
        name = _file_name(href or "")
        if name:
            caption = _text(el.find("caption"))
            if name not in supplements or (caption and not supplements[name].caption):
                supplements[name] = SupplementRef(name=name, caption=caption)

    return PaperText(sections=list(sections.values()), captions=captions, supplements=list(supplements.values()))
