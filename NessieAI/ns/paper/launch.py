"""Stage 0, the launch check (spec §4): seconds, inside paper-reingest-start.

Nothing is queued unless the Methods can be read, and either a Data Availability section
or at least one accession (spec delta 2). A paper already in NExtSEEK (its DOI on a
sample) starts in update mode (spec §6.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from NessieAI.ns.paper import europepmc
from NessieAI.ns.paper.accessions import find_accessions
from NessieAI.ns.paper.fetch import Fetcher
from NessieAI.ns.paper.identify import DOI_RE, clean_doi, identify
from NessieAI.ns.paper.jats import parse_jats
from NessieAI.ns.paper.models import PaperArtifact, PaperInput, PaperRef, PaperText, _Strict
from NessieAI.ns.paper.pdftext import pdf_to_text, split_sections


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
    projects = seams.doi_project_ids(ref.doi) if ref.doi else []
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
    text = split_sections(pdf_to_text(seams.read_input_file(name)))
    head = " ".join(text.all_text()[:5])
    ref, doi = None, None
    if match := DOI_RE.search(head):
        doi = clean_doi(match.group(0))
        ref = europepmc.resolve(PaperInput(kind="doi", value=doi), seams.fetcher)
    if ref is None:
        first = text.all_text()[0][:300] if text.all_text() else name
        ref = PaperRef(title=first, doi=doi)
    ref.source_file = name
    return ref, text, "pdf"
