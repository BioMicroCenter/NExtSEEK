"""What the curator typed → one paper identifier (spec §4, stage 0)."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from NessieAI.ns.paper.models import PaperInput

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")
_PMID = re.compile(r"^(?:pmid[:\s]*)?(\d{1,9})$", re.I)
_PMCID = re.compile(r"^(?:pmcid[:\s]*)?(PMC\d+)$", re.I)
_PMC_PATH = re.compile(r"/(PMC\d+)", re.I)
_PUBMED_PATH = re.compile(r"^/(\d{1,9})/?$")
_EPMC_MED = re.compile(r"/article/MED/(\d{1,9})", re.I)
_TRAILING = ".,;)]}"
_SUFFIXES = (".full", "/full", ".abstract", "/abstract", ".pdf", "/pdf")


class IdentifyError(ValueError):
    pass


def clean_doi(raw: str) -> str:
    doi = unquote(raw).rstrip(_TRAILING)
    for suffix in _SUFFIXES:
        if doi.lower().endswith(suffix):
            doi = doi[: -len(suffix)]
    return doi.lower()


def identify(raw: str, input_files: set[str]) -> PaperInput:
    text = (raw or "").strip()
    if not text:
        raise IdentifyError("give a PMID, PMCID, DOI, article link or the name of an attached PDF")
    if text in input_files:
        return PaperInput(kind="file", value=text)
    if text.lower().endswith(".pdf") and "/" not in text:
        raise IdentifyError(f"no attached file is named {text}")
    if m := _PMID.match(text):
        return PaperInput(kind="pmid", value=m.group(1))
    if m := _PMCID.match(text):
        return PaperInput(kind="pmcid", value=m.group(1).upper())
    if text.lower().startswith(("http://", "https://")):
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        if host == "pubmed.ncbi.nlm.nih.gov" and (m := _PUBMED_PATH.match(parts.path)):
            return PaperInput(kind="pmid", value=m.group(1))
        if host.endswith("europepmc.org") and (m := _EPMC_MED.search(parts.path)):
            return PaperInput(kind="pmid", value=m.group(1))
        if host.endswith("ncbi.nlm.nih.gov") and (m := _PMC_PATH.search(parts.path)):
            return PaperInput(kind="pmcid", value=m.group(1).upper())
    if m := DOI_RE.search(unquote(text)):
        return PaperInput(kind="doi", value=clean_doi(m.group(0)))
    raise IdentifyError("could not find a PMID, PMCID or DOI in that; give one, or attach the PDF")
