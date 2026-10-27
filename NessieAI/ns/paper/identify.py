"""What the curator typed → one paper identifier (spec §4, stage 0)."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlsplit

from NessieAI.ns.paper.models import PaperInput

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")
_PMID = re.compile(r"^(?:pmid[:\s]*)?(\d{1,9})$", re.I)
_PMCID = re.compile(r"^(?:pmcid[:\s]*)?(PMC\d+)$", re.I)
_PMC_PATH = re.compile(r"/(PMC\d+)", re.I)
_PUBMED_PATH = re.compile(r"^/(\d{1,9})/?$")  # pubmed.ncbi.nlm.nih.gov/<id>
_LEGACY_PUBMED = re.compile(r"^/pubmed/(\d{1,9})/?$", re.I)  # www.ncbi.nlm.nih.gov/pubmed/<id>
_PUBMED_SEARCH = re.compile(r"^/(?:pubmed/?)?$", re.I)  # …/pubmed/?term=<id>
_PMC_BY_PMID = re.compile(r"/pmc/articles/pmid/(\d{1,9})(?!\d)", re.I)
# Europe PMC: /article/<SRC>/<id> today, /abstract/<SRC>/<id> and /articles/PMC<n> before.
_EPMC_MED = re.compile(r"/(?:article|abstract)/MED/(\d{1,9})(?!\d)", re.I)
_EPMC_PMC = re.compile(r"/(?:article|abstract)/PMC/(?:PMC)?(\d{1,9})(?!\d)", re.I)
_TRAILING = ".,;)]}/"
_LINE_BREAK_HYPHEN = re.compile(r"-[ \t]*\r?\n[ \t]*(?=\S)")
_MARK = "\x00"
_SUFFIXES = (".full", "/full", ".abstract", "/abstract", ".pdf", "/pdf", "+html")


class IdentifyError(ValueError):
    pass


def _host_is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def clean_doi(raw: str) -> str:
    doi = unquote(re.split(r"[?#]", raw, maxsplit=1)[0])
    while True:
        stripped = doi.rstrip(_TRAILING)
        for suffix in _SUFFIXES:
            if stripped.lower().endswith(suffix):
                stripped = stripped[: -len(suffix)]
                break
        if stripped == doi:
            return doi.lower()
        doi = stripped


def doi_candidates(text: str) -> list[str]:
    """Every DOI in raw (PDF) text, cleaned, in order, without repeats. A DOI broken over two lines at a
    hyphen is read across the break twice: first keeping the hyphen (it is often the DOI's own, as in
    10.1038/s41467-024-00001-x), then without it (a typesetter's hyphen). The caller checks which is real."""
    marked = _LINE_BREAK_HYPHEN.sub("-" + _MARK, text.replace(_MARK, ""))
    found = []
    for m in DOI_RE.finditer(marked):
        doi = m.group(0)
        found.append(clean_doi(doi.replace(_MARK, "")))
        if _MARK in doi:
            found.append(clean_doi(doi.replace("-" + _MARK, "")))
    return list(dict.fromkeys(found))


def _url_doi(parts) -> str | None:
    """A DOI in a URL's path or query values, never in the raw URL string."""
    sources = [unquote(parts.path), *(value for _, value in parse_qsl(parts.query))]
    for source in sources:
        if m := DOI_RE.search(source):
            return m.group(0)
    return None


def _pubmed_or_pmc(host: str, parts) -> PaperInput | None:
    """A PMID or PMCID in a PubMed, PMC or Europe PMC link, current or legacy form."""
    path = parts.path
    pubmed = host == "pubmed.ncbi.nlm.nih.gov"
    if pubmed or host in ("www.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"):
        if m := (_PUBMED_PATH if pubmed else _LEGACY_PUBMED).match(path):
            return PaperInput(kind="pmid", value=m.group(1))
        if _PUBMED_SEARCH.match(path):
            term = dict(parse_qsl(parts.query)).get("term", "").strip()
            if m := _PMID.match(term):
                return PaperInput(kind="pmid", value=m.group(1))
    europepmc = _host_is(host, "europepmc.org") or (
        host == "www.ebi.ac.uk" and path.lower().startswith("/europepmc/"))
    if europepmc:
        if m := _EPMC_MED.search(path):
            return PaperInput(kind="pmid", value=m.group(1))
        if m := _EPMC_PMC.search(path):
            return PaperInput(kind="pmcid", value=f"PMC{m.group(1)}")
    if _host_is(host, "ncbi.nlm.nih.gov") and (m := _PMC_BY_PMID.search(path)):
        return PaperInput(kind="pmid", value=m.group(1))
    if (europepmc or _host_is(host, "ncbi.nlm.nih.gov")) and (m := _PMC_PATH.search(path)):
        return PaperInput(kind="pmcid", value=m.group(1).upper())
    return None


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
        if found := _pubmed_or_pmc(host, parts):
            return found
        doi = _url_doi(parts)
    else:
        m = DOI_RE.search(unquote(text))
        doi = m.group(0) if m else None
    if doi:
        return PaperInput(kind="doi", value=clean_doi(doi))
    raise IdentifyError("could not find a PMID, PMCID or DOI in that; give one, or attach the PDF")
