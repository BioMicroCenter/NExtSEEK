"""Extract publication references from free-text SEEK study descriptions.

Pure functions: no database, no network, no Django settings. The input is prose
written by many people over several years, so every rule here is derived from a
string that actually appears in seek_production.studies rather than from what a
well-formed citation ought to look like.

See docs/2026-08-21-publication-links-design.md, "Backfill".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Hosts that appear in study descriptions but never denote a paper.
REJECTED_HOSTS: tuple[str, ...] = (
    "i.imgur.com",
    "imgur.com",
    "omero.mit.edu",
)

#: A GEO accession lives on an NCBI host but is data, not a publication.
_GEO_PATH = "/geo/"

#: Deliberately permissive after the slash (``*`` not ``+``) so that a truncated
#: DOI such as ``10.3390/`` still matches and can be reported as unresolvable
#: rather than silently vanishing.
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>)\]]*")

#: Parentheses are allowed here and trimmed afterwards by :func:`_trim_url`.
#: Cell and ScienceDirect PIIs embed balanced parens — S1074-7613(24)00375-3 —
#: so excluding ')' outright truncates the URL mid-identifier.
_URL_RE = re.compile(r"https?://[^\s\"'<>\]]+")
_NATURE_RE = re.compile(r"nature\.com/articles/([A-Za-z0-9\-.]+)")
_PMC_RE = re.compile(r"/pmc/articles/(PMC\d+)", re.IGNORECASE)

#: bioRxiv/medRxiv append a version, and sometimes a view suffix, to the DOI.
_VERSION_SUFFIX_RE = re.compile(r"v\d+(\.full(-text)?)?$", re.IGNORECASE)

_TRAILING_JUNK = ".,;:)]}>\"'"

#: Path segments that mark a DOI as pointing at supplementary material rather
#: than the paper. Publishers mint these as sub-DOIs of the article DOI, e.g.
#: 10.1126/sciadv.adq6652/suppl_file/sciadv.adq6652_sm.pdf — Crossref 404s on
#: them, and the article's own DOI is normally present in the same description.
_SUPPLEMENT_MARKERS = ("/suppl_file/", "/suppl/", "/supplementary/", "/media/")


@dataclass(frozen=True)
class Candidate:
    """One publication reference found in a description.

    ``kind`` is ``"doi"`` (``value`` is a normalized DOI), ``"pmc"`` (``value``
    is a PMC id needing a lookup), or ``"unresolvable"`` (``value`` is empty and
    a human must decide — ``note`` says why).
    """

    kind: str
    value: str
    raw: str
    note: str = ""


def normalize_doi(raw: str) -> str | None:
    """Normalize a DOI-ish string, or return None if it is not usable.

    Returns None for a prefix with no suffix (``10.3390/``), which is a real case
    in the data and must never be guessed at.
    """
    doi = raw.strip()
    doi = doi.split("#", 1)[0]
    doi = doi.rstrip(_TRAILING_JUNK)
    doi = _VERSION_SUFFIX_RE.sub("", doi)
    doi = doi.rstrip(_TRAILING_JUNK)

    if not doi.lower().startswith("10."):
        return None
    _, _, suffix = doi.partition("/")
    if not suffix:
        return None
    lowered = doi.lower()
    if any(marker in lowered for marker in _SUPPLEMENT_MARKERS):
        return None
    return lowered


def _trim_url(url: str) -> str:
    """Strip trailing characters that belong to the surrounding prose.

    Descriptions embed figures as markdown ``![](url)``, so a trailing ``)``
    usually closes the markdown — unless the URL opened a paren itself, which
    Cell and ScienceDirect PIIs do. Counting decides which case this is.
    """
    while url and url[-1] in _TRAILING_JUNK:
        if url[-1] == ")" and url.count("(") >= url.count(")"):
            break
        url = url[:-1]
    return url


def _is_rejected(url: str) -> bool:
    """True for URLs that are figures, image servers, or data accessions."""
    lowered = url.lower()
    if any(host in lowered for host in REJECTED_HOSTS):
        return True
    return "ncbi.nlm.nih.gov" in lowered and _GEO_PATH in lowered


def extract_publication_candidates(description: str | None) -> list[Candidate]:
    """Find publication references in a study description.

    A DOI anywhere in the text wins. Only when no DOI is present do we fall back
    to interpreting bare URLs, because a description usually contains figure
    links alongside its citation.
    """
    if not description:
        return []

    found: list[Candidate] = []
    seen: set[tuple[str, str]] = set()

    for match in _DOI_RE.finditer(description):
        raw = match.group(0)
        doi = normalize_doi(raw)
        if doi is None:
            candidate = Candidate("unresolvable", "", raw, "DOI prefix with no suffix")
        else:
            candidate = Candidate("doi", doi, raw)
        key = (candidate.kind, candidate.value or candidate.raw)
        if key not in seen:
            seen.add(key)
            found.append(candidate)

    if any(c.kind == "doi" for c in found):
        return [c for c in found if c.kind == "doi"]
    if found:
        return found

    for raw_url in _URL_RE.findall(description):
        url = _trim_url(raw_url)
        if _is_rejected(url):
            continue
        nature = _NATURE_RE.search(url)
        if nature:
            article_id = nature.group(1).rstrip(_TRAILING_JUNK).lower()
            found.append(
                Candidate(
                    "doi",
                    f"10.1038/{article_id}",
                    url,
                    "derived from nature.com article id",
                )
            )
            continue
        pmc = _PMC_RE.search(url)
        if pmc:
            found.append(Candidate("pmc", pmc.group(1).upper(), url))
            continue
        found.append(
            Candidate("unresolvable", "", url, "publisher URL with no extractable identifier")
        )

    return found
