"""Europe PMC: metadata, open-access full text (JATS) and the supplementary zip.

Europe PMC serves the supplement as one zip, so publisher pages (and their reCAPTCHA)
are never touched (spec delta 5).
"""
from __future__ import annotations

from NessieAI.ns.paper.dates import parse_date
from NessieAI.ns.paper.fetch import Fetcher, SourceNotFound
from NessieAI.ns.paper.models import Author, PaperInput, PaperRef

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def _query(inp: PaperInput) -> str:
    if inp.kind == "pmid":
        return f"EXT_ID:{inp.value} AND SRC:MED"
    if inp.kind == "pmcid":
        return f"PMCID:{inp.value}"
    return f'DOI:"{inp.value}"'


def _affiliation(author: dict) -> str:
    if author.get("affiliation"):
        return author["affiliation"]
    details = (author.get("authorAffiliationDetailsList") or {}).get("authorAffiliation") or []
    return "; ".join(d.get("affiliation", "") for d in details if d.get("affiliation"))


def resolve(inp: PaperInput, fetcher: Fetcher) -> PaperRef | None:
    data = fetcher.get_json(f"{BASE}/search", params={
        "query": _query(inp), "format": "json", "resultType": "core", "pageSize": "1"})
    hits = (data.get("resultList") or {}).get("result") or []
    if not hits:
        return None
    hit = hits[0]
    authors = (hit.get("authorList") or {}).get("author") or []
    return PaperRef(
        pmid=hit.get("pmid"), pmcid=hit.get("pmcid"),
        doi=(hit.get("doi") or "").lower() or None,
        title=hit.get("title", ""),
        journal=((hit.get("journalInfo") or {}).get("journal") or {}).get("title", ""),
        published=parse_date(hit.get("firstPublicationDate")),
        authors=[Author(name=a.get("fullName", ""), affiliation=_affiliation(a)) for a in authors],
        open_access=hit.get("isOpenAccess") == "Y" and hit.get("inEPMC") == "Y",
    )


def _optional(fetcher: Fetcher, url: str) -> bytes | None:
    try:
        return fetcher.get(url)
    except SourceNotFound:
        return None


def full_text_xml(pmcid: str, fetcher: Fetcher) -> bytes | None:
    return _optional(fetcher, f"{BASE}/{pmcid}/fullTextXML")


def supplementary_zip(pmcid: str, fetcher: Fetcher) -> bytes | None:
    return _optional(fetcher, f"{BASE}/{pmcid}/supplementaryFiles")
