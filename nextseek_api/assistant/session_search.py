"""Search a user's saved chats by what was asked or answered in them (``GET /assistant/sessions/search/``).

A source turns the query's terms into hits of one shape; ``search_sessions`` merges the sources, ranks and cuts.
Only ``chats`` exists today; a transcripts or records source joins ``SOURCES`` with its own function and hit
``source`` value, and the endpoint and the sidebar take it without a change of shape.

The chats source reads each chat's title and ``extra_state['chat_log']`` (one entry per turn: ``turn_id``,
``user_query``, ``assistant_reply``). It reads through ``SpillJSONField``, so a value the size guard moved to a file
comes back as its content and a ``{"$spill": ...}`` link is never matched as text. ``results_history`` (the data
bundles) is never read: chats from before ``chat_log`` existed are found by their title only.

ponytail: a per-user scan in Python (dev's heaviest user, 1,549 chats: ~180 ms on 2026-10-26). When one user's
search passes ~500 ms, keep a per-turn text table (session, turn_id, text) with a FULLTEXT index, written beside
``DictSessionAdapter.save`` and the rename, and point this source at it.
"""
from __future__ import annotations

import re
import string
from datetime import datetime, timedelta
from typing import Callable

from django.utils import timezone

from .models_db import ChatSession

MAX_TERMS = 8
SNIPPET_WIDTH = 160
_LEAD = 40  # characters of context kept before the first hit in a snippet
_EDGE = string.punctuation + "“”‘’"
_MARKDOWN = re.compile(r"[*#|`>]+|-{3,}|={3,}")
_SPACE = re.compile(r"\s+")

TITLE, QUESTION, ANSWER = 3, 2, 1
_MATCHED_IN = {TITLE: "title", QUESTION: "question", ANSWER: "answer"}


def parse_terms(query: str) -> list[str]:
    """The query's words, lowercased, with punctuation trimmed off their ends; at most ``MAX_TERMS``."""
    terms = [w.strip(_EDGE) for w in (query or "").lower().split()]
    return [t for t in terms if t][:MAX_TERMS]


def _patterns(terms: list[str]) -> list[re.Pattern]:
    # A term matches where a word starts (after anything but a letter or digit, in any script): "seq" finds
    # "RNA-seq" and "type" finds "counts_by_type", "dress" does not find "address" nor "ve" "naïve".
    return [re.compile(r"(?<![^\W_])" + re.escape(t), re.IGNORECASE) for t in terms]


def _all(patterns: list[re.Pattern], text: str) -> bool:
    return all(p.search(text) for p in patterns)


def make_snippet(text: str, terms: list[str], width: int = SNIPPET_WIDTH) -> str:
    """Plain text around the first hit: markdown marks dropped, whitespace collapsed, ``…`` on a cut end."""
    clean = _SPACE.sub(" ", _MARKDOWN.sub(" ", text or "")).strip()
    if len(clean) <= width:
        return clean
    hits = [m.start() for p in _patterns(terms) if (m := p.search(clean))]
    start = max(0, min(hits, default=0) - _LEAD)
    start = min(start, len(clean) - width)
    end = start + width
    return ("…" if start else "") + clean[start:end].strip() + ("…" if end < len(clean) else "")


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _chat_logs(user_id: int | None, since: datetime | None) -> list[tuple]:
    """(session_id, title, updated_at, owner username, chat_log) for the chats in scope."""
    # .order_by(): the model's default ordering puts the JSON column in MySQL's sort buffer (error 1038).
    qs = ChatSession.objects.order_by()
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    if since is not None:
        qs = qs.filter(updated_at__gte=since)
    rows = list(qs.values_list("session_id", "title", "updated_at", "user__username", "extra_state__chat_log"))
    # A chat whose whole extra_state went to one file has no chat_log key in the row: read those whole.
    whole = [r[0] for r in rows if r[4] is None]
    if whole:
        found = dict(
            ChatSession.objects.order_by()
            .filter(session_id__in=whole, extra_state__has_key="$spill")
            .values_list("session_id", "extra_state")
        )
        rows = [r if r[0] not in found else (*r[:4], (found[r[0]] or {}).get("chat_log")) for r in rows]
    return rows


def search_chats(user_id: int | None, terms: list[str], since: datetime | None) -> list[dict]:
    """One hit per chat whose title, or one turn's question and answer, hold every term."""
    patterns = _patterns(terms)
    hits = []
    for session_id, title, updated_at, username, chat_log in _chat_logs(user_id, since):
        best = None  # (tier, turn_id, snippet source)
        count = 0
        for entry in chat_log if isinstance(chat_log, list) else []:
            if not isinstance(entry, dict) or not _text(entry.get("user_query")):
                continue
            question = entry["user_query"]
            answer = _text(entry.get("assistant_reply")) or _text(entry.get("assistant_reply_preview"))
            if not answer and entry.get("bundle_id") is None:
                continue  # turn_rows hides it (PD-6): a hit there would open on nothing
            if _all(patterns, question):
                tier, source = QUESTION, question
            elif _all(patterns, question + "\n" + answer):
                tier, source = ANSWER, answer
            else:
                continue
            count += 1
            if best is None or tier > best[0]:
                turn_id = entry.get("turn_id")
                best = (tier, turn_id if isinstance(turn_id, int) else None, source)
        title_hit = bool(title) and _all(patterns, title)
        if not title_hit and best is None:
            continue
        hits.append({
            "source": "chat",
            "session_id": session_id,
            "title": title or "New chat",
            "updated_at": updated_at,
            "owner": username if user_id is None else None,
            "turn_id": best[1] if best else None,
            "matched_in": _MATCHED_IN[TITLE if title_hit else best[0]],
            "snippet": make_snippet(best[2], terms) if best else "",
            "match_count": count,
            "_tier": TITLE if title_hit else best[0],
        })
    return hits


Source = Callable[[int | None, list[str], datetime | None], list[dict]]
SOURCES: dict[str, Source] = {"chats": search_chats}


def search_sessions(user_id: int, query: str, *, all_users: bool = False,
                    sources: list[str] | tuple[str, ...] = ("chats",), since_days: int | None = None,
                    limit: int = 20) -> dict:
    """Hits from every named source, best tier first and newest first within a tier.

    ``user_id``'s chats only, unless ``all_users``: then every user's, each hit naming its owner. The caller
    decides who may ask for that (the view: ``may_read_any``).
    """
    if user_id is None:
        raise TypeError("search_sessions needs the caller's user id; pass all_users=True to search every user's")
    terms = parse_terms(query)
    sources = list(dict.fromkeys(sources))
    out = {"query": query, "terms": terms, "sources": sources, "total": 0, "results": []}
    if not terms:
        return out
    since = timezone.now() - timedelta(days=since_days) if since_days else None
    scope = None if all_users else user_id
    hits = [h for name in sources for h in SOURCES[name](scope, terms, since)]
    hits.sort(key=lambda h: (h["_tier"], h["updated_at"]), reverse=True)
    for h in hits:
        del h["_tier"]
    out["total"] = len(hits)
    out["results"] = hits[:limit]
    return out
