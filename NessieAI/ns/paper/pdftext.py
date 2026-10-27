"""An attached PDF → text → sections, with the same evidence IDs JATS gets.

Headings are found by name (optionally numbered) on a line of their own. A References
heading skips the reference list only: reading resumes at the next recognised heading, because
Cell Press papers put STAR Methods and Data availability after the references. pypdf is
imported only here and only when a PDF is read, so nothing else in the package needs it.

A line broken at a hyphen inside an identifier is rejoined as printed (``_join_broken``), so an
accession or DOI split over two lines is read whole; a hyphenated word is joined with a space, as before.
"""
from __future__ import annotations

import io
import re

from NessieAI.ns.paper.jats import classify
from NessieAI.ns.paper.models import Paragraph, PaperText, Section

_HEADING = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?("
    r"abstract|introduction|results(?: and discussion)?|discussion|conclusions?"
    r"|(?:materials and |online |experimental )?methods?(?: details)?|experimental procedures"
    r"|star\s*[\u2605*]?\s*methods?|key resources table|resource availability|lead contact"
    r"|materials availability|experimental model and subject details"
    r"|quantification and statistical analysis"
    r"|data (?:and (?:code|materials) )?availability(?: statement)?|code availability"
    r"|acknowledge?ments?|references|supplementary (?:information|materials?)"
    r")\s*:?\s*$", re.I)


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    # A form feed between pages: splitlines() reads it as a line break, and the launch check
    # finds the first page by it.
    return "\f".join((page.extract_text() or "") for page in reader.pages)


def _join_broken(prev: str, line: str) -> str | None:
    """``prev`` and ``line`` as one line when ``prev`` ends in a hyphen inside an identifier; None otherwise.

    A DOI or URL ("/" in the last word), or digits running on into digits ("GSE1234-" over "56"), keeps the
    hyphen with no space, as printed: the hyphen may be the identifier's own or a range's ("GSM100-105"), so
    both readings are left to the readers (``identify.doi_readings``, ``accessions.find_accessions``).
    Anything else, such as "well-" over "known" or "pre-" over "and", is left to the space join.
    """
    if not prev.endswith("-"):
        return None
    tail = prev.rsplit(None, 1)[-1]
    if "/" in tail or (line[:1].isdigit() and any(ch.isdigit() for ch in tail)):
        return prev + line
    return None


def chunk_paragraphs(lines: list[str], limit: int = 1000) -> list[str]:
    paras, current = [], []
    for line in list(lines) + [""]:
        if line.strip():
            joined = _join_broken(current[-1], line.strip()) if current else None
            if joined is None:
                current.append(line.strip())
            else:
                current[-1] = joined
        elif current:
            paras.append(" ".join(current))
            current = []
    chunks = []
    for para in paras:
        while len(para) > limit:
            cut = para.rfind(". ", 0, limit)
            if cut > 0:
                cut += 1
            else:  # no sentence end: cut between words, so no accession or DOI is cut in two
                space = para.rfind(" ", 0, limit + 1)
                cut = space if space > 0 else limit
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
        if para:
            chunks.append(para)
    return chunks


def split_sections(text: str) -> PaperText:
    blocks: list[tuple[str, list[str]]] = [("front", [])]
    skipping = False  # inside a reference list: lines are dropped until the next heading
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            title = match.group(1).strip()
            skipping = title.lower() == "references"
            if not skipping:
                blocks.append((title, []))
        elif not skipping:
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
