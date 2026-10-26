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

from collections import defaultdict


from NessieAI.ns.chat_search_index import RESULT_CAP

from .models_db import ChatSearchEntry, ChatSession

MAX_TERMS = 8
SNIPPET_WIDTH = 160
_LEAD = 40  # characters of context kept before the first hit in a snippet
_EDGE = string.punctuation + "“”‘’"
_MARKDOWN = re.compile(r"[*#|`>]+|-{3,}|={3,}")
_SPACE = re.compile(r"\s+")

TITLE, QUESTION, ANSWER, RECORD, STEPS = 5, 4, 3, 2, 1
_MATCHED_IN = {TITLE: "title", QUESTION: "question", ANSWER: "answer", RECORD: "record", STEPS: "steps"}
WHERE = ("question", "answer", "result")
_SHOWN_IDS = 3  # a record hit's snippet names this many IDs, then "and N more"
_FACETS_IN_LIST = 200  # facets name at most this many matching chats in SQL (see _facets)


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
        turns = set()  # the matched turns, by number (by position for an entry without one)
        for n, entry in enumerate(chat_log if isinstance(chat_log, list) else []):
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
            turn_id = entry.get("turn_id")
            turns.add(turn_id if isinstance(turn_id, int) else ("#", n))
            if best is None or tier > best[0]:
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
            "match_count": 0,
            "_tier": TITLE if title_hit else best[0],
            "_turns": turns,
            "_keys": [],
        })
    return hits


def _entries(scope: int | None, since: datetime | None, **filters):
    """Index rows (``ChatSearchEntry``) of the chats in scope, last active since ``since``."""
    qs = ChatSearchEntry.objects.filter(**filters)
    if scope is not None:
        qs = qs.filter(user_id=scope)
    if since is not None:
        qs = qs.filter(session__updated_at__gte=since)
    return qs


_HIT_FIELDS = ("session_id", "turn_id", "session__title", "session__updated_at", "user__username")


def _hit(source: str, tier: int, row: tuple, scope: int | None, snippet: str) -> dict:
    session_id, turn_id, title, updated_at, username = row[:5]
    return {"source": source, "session_id": session_id, "title": title or "New chat", "updated_at": updated_at,
            "owner": username if scope is None else None, "turn_id": turn_id, "matched_in": _MATCHED_IN[tier],
            "snippet": snippet, "match_count": 0, "_tier": tier, "_turns": set(), "_keys": []}


def search_steps(scope: int | None, terms: list[str], since: datetime | None) -> list[dict]:
    """One hit per chat whose Container-CC agent steps (its words or its tool calls) hold every term."""
    patterns = _patterns(terms)
    qs = _entries(scope, since, source="steps")
    for t in terms:
        qs = qs.filter(text__icontains=t)  # a cheap first cut; the word-start rule is checked below
    hits: dict = {}
    for row in qs.order_by("turn_id", "id").values_list(*_HIT_FIELDS, "text"):
        if not _all(patterns, row[5]):
            continue
        hit = hits.setdefault(row[0], _hit("steps", STEPS, row, scope, make_snippet(row[5], terms)))
        hit["_turns"].add(row[1])
    return list(hits.values())


def _record_hits(qs, scope: int | None) -> list[dict]:
    """One record hit per chat from index rows (uid or project), naming the first IDs and how many more.

    Reads only (chat, turn, key) per row; the chats' titles and owners come in one query for the hit chats.
    """
    found: dict = {}
    for session_id, turn_id, key in qs.order_by("session_id", "turn_id", "id").values_list("session_id", "turn_id", "key"):
        turns, keys, first_turn = found.setdefault(session_id, (set(), {}, turn_id))
        turns.add(turn_id)
        keys[key] = None  # an ordered set
    if not found:
        return []
    capped: dict = {}
    turns_rows = ChatSearchEntry.objects.filter(source="record", kind="turn", session_id__in=list(found))
    for session_id, turn_id, total in turns_rows.values_list("session_id", "turn_id", "text"):
        if turn_id in found[session_id][0] and total.isdigit() and int(total) > RESULT_CAP:
            capped[session_id] = max(capped.get(session_id, 0), int(total))
    hits = []
    meta = ChatSession.objects.order_by().filter(pk__in=list(found))
    for session_id, title, updated_at, username in meta.values_list("session_id", "title", "updated_at", "user__username"):
        turns, keys, first_turn = found[session_id]
        shown = list(keys)[:_SHOWN_IDS]
        more = f" and {len(keys) - _SHOWN_IDS} more" if len(keys) > _SHOWN_IDS else ""
        note = f" (first {RESULT_CAP} of {capped[session_id]} results searched)" if session_id in capped else ""
        hit = _hit("record", RECORD, (session_id, first_turn, title, updated_at, username), scope,
                   ", ".join(shown) + more + note)
        hit["_turns"] = turns
        hits.append(hit)
    return hits


def _starts_word(term: str) -> str:
    """The SQL regex for "``term`` where a word starts" (MySQL 8 and SQLite alike; keys are IDs and codes, ASCII)."""
    return r"(^|[^A-Za-z0-9])" + re.escape(term)


def search_records(scope: int | None, terms: list[str], since: datetime | None) -> list[dict]:
    """One hit per chat that showed a record (an ID, or a project the question named) matching every term."""
    qs = _entries(scope, since, source="record", kind__in=("uid", "project"))
    for t in terms:
        qs = qs.filter(key__iregex=_starts_word(t))  # in SQL: no row ids travel back and forth
    return _record_hits(qs, scope)


Source = Callable[[int | None, list[str], datetime | None], list[dict]]
SOURCES: dict[str, Source] = {"chats": search_chats, "records": search_records, "steps": search_steps}


def _filtered(scope, since, record_type, project, where):
    """(the chats the record filters allow, or None when there is no filter; the rows that made them match).

    Each filter's chats are read as a distinct set (at most one per chat); two filters meet in Python. Not a SQL
    subquery: MySQL 8 ran ``session_id IN (SELECT ...)`` over the index table in 6 s at dev's size.
    """
    parts = []
    if record_type or where:
        parts.append(_entries(scope, since, source="record", kind="uid",
                              **({"record_type": record_type} if record_type else {}),
                              **({"text": where} if where else {})))
    if project:
        parts.append(_entries(scope, since, source="record", kind="project", key=project))
    if not parts:
        return None, None
    allowed = set.intersection(*(set(q.order_by().values_list("session_id", flat=True).distinct()) for q in parts))
    rows = parts[0] if len(parts) == 1 else parts[0].filter(session_id__in=list(allowed))
    return allowed, rows


def _facets(scope, since, session_ids=None, record_type=None, where=None) -> dict:
    """For each record filter value, how many of the chats (all in scope, or ``session_ids``) it would keep.

    A type is counted only on rows that also match the chosen ``where``, and a where only on rows of the chosen
    type: both name the same row, so the count is what choosing that value returns. Read as distinct
    (chat, type, where) and (chat, project) tuples in two queries and counted here. A few matching chats are
    named in the query (``IN``); many are filtered here, because an ``IN`` list of over a thousand chats cost
    MySQL 200-400 ms at dev's size, against ~140 ms for reading every tuple.
    """
    keep = None if session_ids is None else set(session_ids)
    narrow = {"session_id__in": list(keep)} if keep is not None and len(keep) <= _FACETS_IN_LIST else {}
    types, wheres, projects = defaultdict(set), defaultdict(set), defaultdict(set)
    uid_rows = _entries(scope, since, source="record", kind="uid", **narrow).order_by()
    for session_id, rtype, place in uid_rows.values_list("session_id", "record_type", "text").distinct():
        if keep is not None and session_id not in keep:
            continue
        if not where or place == where:
            types[rtype].add(session_id)
        if not record_type or rtype == record_type:
            wheres[place].add(session_id)
    project_rows = _entries(scope, since, source="record", kind="project", **narrow).order_by()
    for session_id, project in project_rows.values_list("session_id", "key").distinct():
        if keep is None or session_id in keep:
            projects[project].add(session_id)

    def counts(found):
        return dict(sorted(((k, len(v)) for k, v in found.items()), key=lambda r: (-r[1], r[0])))

    return {"record_type": counts(types), "project": counts(projects), "where": counts(wheres)}


def _merge(hits: list[dict]) -> list[dict]:
    """One hit per chat: the best tier's hit, with the turns every source matched counted once."""
    best: dict = {}
    for h in hits:
        b = best.get(h["session_id"])
        if b is None or h["_tier"] > b["_tier"]:
            if b is not None:
                h["_turns"] |= b["_turns"]
            best[h["session_id"]] = h
        else:
            b["_turns"] |= h["_turns"]
    for h in best.values():
        h["match_count"] = len(h["_turns"])
    return list(best.values())


def search_sessions(user_id: int, query: str, *, all_users: bool = False,
                    sources: list[str] | tuple[str, ...] = ("chats",), since_days: int | None = None,
                    limit: int = 20, record_type: str | None = None, project: str | None = None,
                    where: str | None = None, facets_only: bool = False) -> dict:
    """Hits from every named source, merged to one per chat, best tier first and newest first within a tier.

    ``user_id``'s chats only, unless ``all_users``: then every user's, each hit naming its owner. The caller
    decides who may ask for that (the view: ``may_read_any``). The record filters (``record_type``, ``project``,
    ``where``) keep only the chats that showed such a record, and need no words; ``facets`` counts, for each
    filter value, the matching chats it would keep. ``facets_only`` returns the counts over every chat in scope.
    """
    if user_id is None:
        raise TypeError("search_sessions needs the caller's user id; pass all_users=True to search every user's")
    terms = parse_terms(query)
    sources = list(dict.fromkeys(sources))
    out = {"query": query, "terms": terms, "sources": sources, "total": 0, "results": [],
           "facets": {"record_type": {}, "project": {}, "where": {}}}
    since = timezone.now() - timedelta(days=since_days) if since_days else None
    scope = None if all_users else user_id
    if facets_only:
        out["facets"] = _facets(scope, since, record_type=record_type, where=where)
        return out
    allowed, filter_rows = _filtered(scope, since, record_type, project, where)
    if terms:
        hits = [h for name in sources for h in SOURCES[name](scope, terms, since)]
        if allowed is not None:
            hits = [h for h in hits if h["session_id"] in allowed]
    elif allowed is not None:
        hits = _record_hits(filter_rows, scope)
    else:
        return out
    hits = _merge(hits)
    hits.sort(key=lambda h: (h["_tier"], h["updated_at"]), reverse=True)
    out["facets"] = _facets(scope, since, [h["session_id"] for h in hits], record_type=record_type, where=where)
    for h in hits:
        for private in ("_tier", "_turns", "_keys"):
            h.pop(private, None)
    out["total"] = len(hits)
    out["results"] = hits[:limit]
    return out
