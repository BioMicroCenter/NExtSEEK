"""Fulltext candidate queries for graph_search's text terms.

The graph's fulltext index ``sample_search_text`` (docs/neo4j-schema.md, v1.1 rule 6) holds every non-empty metadata
value of a sample. advanced_search matches a term as a case-insensitive substring of a value, which Lucene cannot do
directly: Lucene matches tokens. So the query builder asks Lucene for a superset of candidates and then verifies the
substring on ``search_text`` itself. This module builds the candidate query for one term.

Why the candidate set is a superset: every run of ASCII letters and digits in the term lies inside one run of letters
and digits in any value that contains the term, and the index's standard analyzer never breaks such a run (Unicode
word-break rules WB5, WB8 to WB10), so each run of the term is a substring of one indexed token, which ``*run*``
matches. Tokens are lowercased, as the analyzer lowercases the index.

Known limit, left to parity to measure: the analyzer splits a token longer than 255 characters, so a term crossing
such a split is missed.
"""
from __future__ import annotations

import re
from typing import Optional

# Tokens shorter than this are too broad for a wildcard query; the verification step still checks them.
MIN_TOKEN_LENGTH = 3

_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
# Lucene's classic query parser specials. A token here is [a-z0-9] only, so this is defence in depth.
_LUCENE_SPECIAL_RE = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def escape(text: str) -> str:
    """Backslash-escape every Lucene query-parser special character."""
    return _LUCENE_SPECIAL_RE.sub(r"\\\1", text)


def tokens(term: str) -> list[str]:
    """The term's ASCII letter-and-digit runs, lowercased, at least ``MIN_TOKEN_LENGTH`` long, first-seen order."""
    runs = (run.lower() for run in _SPLIT_RE.split(term or ""))
    return list(dict.fromkeys(run for run in runs if len(run) >= MIN_TOKEN_LENGTH))


def candidate_query(term: str) -> Optional[str]:
    """The fulltext query for one term: its tokens as ``*tok*``, ANDed. ``None`` when no token survives.

    ``"C57BL/6J"`` gives ``*c57bl*``: ``6j`` is under 3 characters and left to the verification step.
    """
    toks = tokens(term)
    if not toks:
        return None
    return " AND ".join(f"*{escape(tok)}*" for tok in toks)
