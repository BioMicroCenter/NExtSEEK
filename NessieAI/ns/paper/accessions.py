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


# Repositories whose IDs never hold a hyphen. "GSE1234-56" is a range or an ID broken over a line, so these
# are also read with a hyphen between digits dropped (GSE123456), when that gives no more digits than any ID has.
_HYPHEN_FREE = frozenset({"geo", "ena", "pride", "zenodo", "figshare"})
_DIGIT_HYPHEN = re.compile(r"(?<=\d)-(?=\d)")
_MAX_ID_DIGITS = 10


def find_accessions(texts: Iterable[str]) -> list[Accession]:
    seen: set[tuple[str, str]] = set()
    found: list[Accession] = []
    for text in texts:
        text = text or ""
        joined = _DIGIT_HYPHEN.sub("", text)
        for reading, joined_reading in ((text, False), (joined, True)):
            if joined_reading and joined == text:
                break
            for repo, pattern in _PATTERNS:
                if joined_reading and repo not in _HYPHEN_FREE:
                    continue
                for match in pattern.finditer(reading):
                    acc = match.group(1).lower() if repo == "sdr" else match.group(1)
                    if joined_reading and sum(ch.isdigit() for ch in acc) > _MAX_ID_DIGITS:
                        continue
                    if (repo, acc) not in seen:
                        seen.add((repo, acc))
                        found.append(Accession(repo=repo, id=acc))
    return found
