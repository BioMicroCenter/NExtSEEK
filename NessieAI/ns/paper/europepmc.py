"""Europe PMC: metadata, open-access full text (JATS) and the supplementary zip.

Europe PMC serves the supplement as one zip, so publisher pages (and their reCAPTCHA)
are never touched (spec delta 5).
"""
from __future__ import annotations

from pydantic import ValidationError

from NessieAI.ns.paper.dates import parse_partial_date
from NessieAI.ns.paper.fetch import Fetcher, SourceNotFound
from NessieAI.ns.paper.models import Author, PaperInput, PaperRef

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


class UnreadableAnswer(ValueError):
    """Europe PMC answered with JSON, but not in the shape of a search result."""


def _query(inp: PaperInput) -> str:
    if inp.kind == "pmid":
        return f"EXT_ID:{inp.value} AND SRC:MED"
    if inp.kind == "pmcid":
        return f"PMCID:{inp.value}"
    if inp.kind == "doi":
        return f'DOI:"{inp.value}"'
    raise ValueError(f"Europe PMC cannot look up a {inp.kind!r} input")


def _affiliation(author: dict) -> str:
    if author.get("affiliation"):
        return str(author["affiliation"])
    details = (author.get("authorAffiliationDetailsList") or {}).get("authorAffiliation") or []
    return "; ".join(str(d["affiliation"]) for d in details if isinstance(d, dict) and d.get("affiliation"))


def _authors(hit: dict) -> list[Author]:
    """Every named author: a person by fullName (or lastName and initials), a consortium by collectiveName."""
    out = []
    for a in (hit.get("authorList") or {}).get("author") or []:
        if not isinstance(a, dict):
            continue
        person = a.get("fullName") or " ".join(str(a.get(k) or "") for k in ("lastName", "initials")).strip()
        if person:
            out.append(Author(name=str(person), affiliation=_affiliation(a)))
        elif a.get("collectiveName"):
            out.append(Author(name=str(a["collectiveName"]), affiliation=_affiliation(a), collective=True))
    return out


def _published(hit: dict) -> str | None:
    """The first publication date at the precision Europe PMC gives it; the year alone when that is all."""
    for value in (hit.get("firstPublicationDate"), hit.get("pubYear")):
        found = parse_partial_date(value)
        if found:
            return found
    return None


def _text(hit: dict, k: str) -> str:
    value = hit.get(k)
    if value is not None and not isinstance(value, str):
        raise UnreadableAnswer(f"Europe PMC gave a {type(value).__name__} for {k}")
    return value or ""


def resolve(inp: PaperInput, fetcher: Fetcher) -> PaperRef | None:
    data = fetcher.get_json(f"{BASE}/search", params={
        "query": _query(inp), "format": "json", "resultType": "core", "pageSize": "1"})
    try:
        hits = (data.get("resultList") or {}).get("result") or []
        hit = hits[0] if hits else None
        if hit is not None and not isinstance(hit, dict):
            raise UnreadableAnswer("Europe PMC's search result is not a record")
    except AttributeError as exc:
        raise UnreadableAnswer("Europe PMC's search answer is not a result list") from exc
    if hit is None:
        return None
    try:
        return PaperRef(
            pmid=_text(hit, "pmid") or None, pmcid=_text(hit, "pmcid") or None,
            doi=_text(hit, "doi").lower() or None,
            title=_text(hit, "title"),
            journal=((hit.get("journalInfo") or {}).get("journal") or {}).get("title") or "",
            published=_published(hit),
            authors=_authors(hit),
            open_access=hit.get("isOpenAccess") == "Y" and hit.get("inEPMC") == "Y",
        )
    except (AttributeError, ValidationError) as exc:
        raise UnreadableAnswer("Europe PMC's record is not in the shape I read") from exc


def _optional(fetcher: Fetcher, url: str) -> bytes | None:
    try:
        return fetcher.get(url)
    except SourceNotFound:
        return None


def full_text_xml(pmcid: str, fetcher: Fetcher) -> bytes | None:
    return _optional(fetcher, f"{BASE}/{pmcid}/fullTextXML")


def supplementary_zip(pmcid: str, fetcher: Fetcher) -> bytes | None:
    return _optional(fetcher, f"{BASE}/{pmcid}/supplementaryFiles")
