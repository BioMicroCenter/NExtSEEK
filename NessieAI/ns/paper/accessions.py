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
