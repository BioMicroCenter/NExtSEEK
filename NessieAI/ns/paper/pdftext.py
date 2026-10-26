"""An attached PDF → text → sections, with the same evidence IDs JATS gets.

Headings are found by name (optionally numbered) on a line of their own; reading stops
at References. pypdf is imported only here and only when a PDF is read, so nothing
else in the package needs it.
"""
from __future__ import annotations

import io
import re

from NessieAI.ns.paper.jats import classify
from NessieAI.ns.paper.models import Paragraph, PaperText, Section

_HEADING = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?("
    r"abstract|introduction|results(?: and discussion)?|discussion|conclusions?"
    r"|(?:materials and |online |star |experimental )?methods?(?: details)?|experimental procedures"
    r"|data (?:and (?:code|materials) )?availability(?: statement)?|code availability"
    r"|acknowledge?ments?|references|supplementary (?:information|materials?)"
    r")\s*:?\s*$", re.I)


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def chunk_paragraphs(lines: list[str], limit: int = 1000) -> list[str]:
    paras, current = [], []
    for line in list(lines) + [""]:
        if line.strip():
            current.append(line.strip())
        elif current:
            paras.append(" ".join(current))
            current = []
    chunks = []
    for para in paras:
        while len(para) > limit:
            cut = para.rfind(". ", 0, limit)
            cut = cut + 1 if cut > 0 else limit
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
        if para:
            chunks.append(para)
    return chunks


def split_sections(text: str) -> PaperText:
    blocks: list[tuple[str, list[str]]] = [("front", [])]
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            title = match.group(1).strip()
            if title.lower() == "references":
                break
            blocks.append((title, []))
        else:
            blocks[-1][1].append(line)
    sections: dict[str, Section] = {}
    counters: dict[str, int] = {}
    for title, lines in blocks:
        slug = classify(title)
        for chunk in chunk_paragraphs(lines):
            counters[slug] = counters.get(slug, 0) + 1
            sections.setdefault(slug, Section(slug=slug, title=title, paragraphs=[])).paragraphs.append(
                Paragraph(id=f"E:{slug}:p{counters[slug]}", text=chunk))
    return PaperText(sections=list(sections.values()))
