"""Stage 0, the launch check (spec §4): seconds, inside paper-reingest-start.

Nothing is queued unless the Methods can be read, and either a Data Availability section
or at least one accession (spec delta 2). A paper already in NExtSEEK (its DOI on a
sample) starts in update mode (spec §6.3). A DOI read from an attached PDF is the paper's
own only when Europe PMC's title for it is on the PDF's first page; the first DOI there can
be a cited paper's.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Literal

from NessieAI.ns.paper import europepmc
from NessieAI.ns.paper.accessions import find_accessions
from NessieAI.ns.paper.fetch import Fetcher, SourceNotFound
from NessieAI.ns.paper.identify import doi_readings, identify
from NessieAI.ns.paper.jats import parse_jats
from NessieAI.ns.paper.models import PaperArtifact, PaperInput, PaperRef, PaperText, _Strict
from NessieAI.ns.paper.pdftext import pdf_to_text, split_sections


MAX_PDF_DOIS = 3  # printed DOIs looked up per PDF (each with its readings), inside the op's one launch deadline
_MIN_TITLE_CHARS = 10  # a shorter squashed title could be found on any page
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")  # markup in a Europe PMC title (<i>, <sup>)
_GLUED_WORD = re.compile(r"(?<=\d)[a-z]{3,}$")  # pypdf can join a DOI to the next word


class AttachmentTooLarge(ValueError):
    """The attached PDF is over the host's size cap (raised by the host's read_input_file seam)."""


class AttachmentUnreadable(ValueError):
    """The attached file could not be read, or is not a PDF pypdf can read."""


class PdfDoiLookupFailed(ValueError):
    """Europe PMC gave an unreadable answer (a 4xx, or a body of the wrong shape) for a DOI printed in the PDF."""


class CatalogUnavailable(RuntimeError):
    """The DOI lookup against NExtSEEK's samples failed; never read as "not in NExtSEEK"."""


@dataclass
class LaunchSeams:
    fetcher: Fetcher
    input_files: set[str]
    read_input_file: Callable[[str], bytes]
    doi_project_ids: Callable[[str], list[int]]


class LaunchOutcome(_Strict):
    status: Literal["ok", "needs_pdf", "missing_sections", "not_found"]
    paper: PaperArtifact | None = None
    missing: list[str] = []
    reply: str = ""


def launch_check(raw: str, seams: LaunchSeams) -> LaunchOutcome:
    inp = identify(raw, seams.input_files)
    if inp.kind == "file":
        ref, text, source = _from_pdf(inp.value, seams)
    else:
        ref = europepmc.resolve(inp, seams.fetcher)
        if ref is None:
            return LaunchOutcome(status="not_found",
                                 reply=f"Europe PMC has no record for {inp.value}. Check it, or attach the PDF.")
        text, source = _open_text(ref, seams.fetcher), "europepmc"
        if text is None:
            return LaunchOutcome(status="needs_pdf", reply=(
                f"I can't read the full text of “{ref.title}”: it is not open access in Europe PMC. "
                "Attach the PDF and ask again with its file name."))
    missing = _missing(text)
    if missing:
        named = " and ".join(m.replace("_", " ") for m in missing)
        if source == "pdf":
            return LaunchOutcome(status="missing_sections", missing=missing, reply=(
                f"I can't find the {named} section in that PDF. Check it is the full article, not the SI."))
        return LaunchOutcome(status="needs_pdf", missing=missing, reply=(
            f"The open-access text has no {named} section I can read. Attach the PDF and ask again."))
    try:
        projects = seams.doi_project_ids(ref.doi) if ref.doi else []
    except RuntimeError as exc:
        raise CatalogUnavailable("NExtSEEK's sample catalog could not be checked for this paper") from exc
    return LaunchOutcome(status="ok", paper=PaperArtifact(
        ref=ref, text=text, text_source=source,
        mode="update" if projects else "new", existing_project_ids=projects))


def _missing(text: PaperText) -> list[str]:
    missing = [] if text.has("methods") else ["methods"]
    if not text.has("data_availability") and not find_accessions(text.all_text()):
        missing.append("data_availability")
    return missing


def _open_text(ref: PaperRef, fetcher: Fetcher) -> PaperText | None:
    if not (ref.pmcid and ref.open_access):
        return None
    xml = europepmc.full_text_xml(ref.pmcid, fetcher)
    return parse_jats(xml) if xml else None


def _from_pdf(name: str, seams: LaunchSeams) -> tuple[PaperRef, PaperText, str]:
    try:
        data = seams.read_input_file(name)
    except AttachmentTooLarge:
        raise
    except (OSError, ValueError) as exc:
        raise AttachmentUnreadable(f"the attached file {name} could not be read") from exc
    try:
        raw_text = pdf_to_text(data)
    except MemoryError:
        raise
    except Exception as exc:  # pypdf raises its own errors, and others, on a damaged or non-PDF file
        raise AttachmentUnreadable(f"{name} is not a PDF I can read") from exc
    text = split_sections(raw_text)
    ref = _own_ref(raw_text.split("\f", 1)[0], seams.fetcher)
    if ref is None:
        first = text.all_text()[0][:300] if text.all_text() else name
        ref = PaperRef(title=first)
    ref.source_file = name
    return ref, text, "pdf"


def _own_ref(first_page: str, fetcher: Fetcher) -> PaperRef | None:
    """Europe PMC's record for the first DOI on the first page whose title is on that page too.

    A reading Europe PMC answers badly is skipped; when none matched and one was skipped, the start is refused,
    because without the DOI the paper would start in new mode even if it is already in NExtSEEK."""
    page = _squash(first_page)
    failed = None
    for readings in doi_readings(first_page)[:MAX_PDF_DOIS]:
        for doi in dict.fromkeys(d for r in readings for d in (r, _GLUED_WORD.sub("", r))):
            try:
                ref = europepmc.resolve(PaperInput(kind="doi", value=doi), fetcher)
            except (SourceNotFound, json.JSONDecodeError, europepmc.UnreadableAnswer) as exc:
                failed = failed or exc
                continue
            title = _squash(_TAG.sub("", html.unescape(ref.title))) if ref else ""
            if len(title) >= _MIN_TITLE_CHARS and title in page:
                return ref
    if failed is not None:
        raise PdfDoiLookupFailed("Europe PMC's answer for a DOI in the PDF could not be read") from failed
    return None


def _squash(text: str) -> str:
    """Letters and digits only, case-folded: survives line breaks and hyphenation."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text).casefold() if ch.isalnum())
