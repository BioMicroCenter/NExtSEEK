"""Deposit accessions and repository links in paper text (spec §4, stage 1)."""
from __future__ import annotations

import re
from typing import Iterable

from NessieAI.ns.paper.models import Accession

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("geo", re.compile(r"\b(GSE\d{3,9}|GSM\d{3,10})\b")),
    ("ena", re.compile(r"\b(PRJ(?:NA|EB|DB)\d{3,}|[SED]R[PXR]\d{5,})\b")),
    ("pride", re.compile(r"\b(PXD\d{6})\b")),
    ("zenodo", re.compile(r"(?:10\.5281/zenodo\.|zenodo\.org/records?/)(\d{3,})", re.I)),
    ("figshare", re.compile(r"(?:10\.6084/m9\.figshare\.|figshare\.com/articles/(?:[\w-]+/)*)(\d{5,})", re.I)),
    ("sdr", re.compile(r"purl\.stanford\.edu/([a-z]{2}\d{3}[a-z]{2}\d{4})", re.I)),
]


# The start of an accession whose repository never puts a hyphen in its IDs, at the end of a line
# that was broken at a hyphen ("GSE1234-" over "56"). DOIs and URLs can hold real hyphens, so they
# are not here.
_SPLIT_TAIL = re.compile(
    r"(?:(?<![A-Za-z0-9])(?:GSE|GSM|PRJ(?:NA|EB|DB)|[SED]R[PXR]|PXD)|zenodo\.|zenodo\.org/records?/"
    r"|figshare\.|figshare\.com/articles/(?:[\w-]+/)*)(\d+)-$", re.I)
_HEAD_DIGITS = re.compile(r"\d+")
_MAX_ID_DIGITS = 10  # more digits than any of these IDs has: a range ("GSM7000001-7000010"), left alone


def rejoin_split_accession(prev: str, line: str) -> str | None:
    """``prev`` and the next ``line`` as one, the line-break hyphen dropped, when ``prev`` ends in the first
    digits of a hyphen-free accession and ``line`` starts with the rest; None otherwise."""
    tail, head = _SPLIT_TAIL.search(prev), _HEAD_DIGITS.match(line)
    if not (tail and head) or len(tail.group(1)) + len(head.group(0)) > _MAX_ID_DIGITS:
        return None
    return prev[:-1] + line


def find_accessions(texts: Iterable[str]) -> list[Accession]:
    seen: set[tuple[str, str]] = set()
    found: list[Accession] = []
    for text in texts:
        for repo, pattern in _PATTERNS:
            for match in pattern.finditer(text or ""):
                acc = match.group(1).lower() if repo == "sdr" else match.group(1)
                if (repo, acc) not in seen:
                    seen.add((repo, acc))
                    found.append(Accession(repo=repo, id=acc))
    return found
