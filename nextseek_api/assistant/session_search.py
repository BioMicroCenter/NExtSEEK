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

from django.db.models import Q
from django.utils import timezone

from collections import defaultdict
from functools import lru_cache


from NessieAI.ns.chat_search_index import RESULT_CAP

from .models_db import ChatSearchEntry, ChatSession

MAX_TERMS = 8
SNIPPET_WIDTH = 160
_LEAD = 40  # characters of context kept before the first hit in a snippet
_EDGE = string.punctuation + "“”‘’"
_MARKDOWN = re.compile(r"[*#|`>]+|-{3,}|={3,}")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")  # [text](url) and ![alt](src): the text stays
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


def _whole(terms: list[str]) -> list[re.Pattern]:
    """Each term as a whole word: it starts a word and ends one (``tis-220831fly-2`` is not inside ``...-26``)."""
    return [re.compile(r"(?<![^\W_])" + re.escape(t) + r"(?![^\W_])", re.IGNORECASE) for t in terms]


def _all(patterns: list[re.Pattern], text: str) -> bool:
    return all(p.search(text) for p in patterns)


def make_snippet(text: str, terms: list[str], width: int = SNIPPET_WIDTH) -> str:
    """Plain text around the first hit: markdown marks and link targets dropped, whitespace collapsed, ``…`` on a cut end."""
    clean = _SPACE.sub(" ", _MARKDOWN.sub(" ", _LINK.sub(r"\1", text or ""))).strip()
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
    patterns, whole = _patterns(terms), _whole(terms)
    hits = []
    for session_id, title, updated_at, username, chat_log in _chat_logs(user_id, since):
        best = None  # (exact, tier, turn_id, snippet source)
        turns = set()  # the matched turns, by number (by position for an entry without one)
        for n, entry in enumerate(chat_log if isinstance(chat_log, list) else []):
            if not isinstance(entry, dict) or not _text(entry.get("user_query")):
                continue
            question = entry["user_query"]
            answer = _text(entry.get("assistant_reply")) or _text(entry.get("assistant_reply_preview"))
            if not answer and entry.get("bundle_id") is None:
                continue  # turn_rows hides it (PD-6): a hit there would open on nothing
            if _all(patterns, question):
                tier, source, exact = QUESTION, question, _all(whole, question)
            elif _all(patterns, question + "\n" + answer):
                tier, source, exact = ANSWER, answer, _all(whole, question + "\n" + answer)
            else:
                continue
            turn_id = entry.get("turn_id")
            turns.add(turn_id if isinstance(turn_id, int) else ("#", n))
            if best is None or (exact, tier) > best[:2]:
                best = (exact, tier, turn_id if isinstance(turn_id, int) else None, source)
        title_hit = bool(title) and _all(patterns, title)
        if not title_hit and best is None:
            continue
        # The title wins unless a turn matched whole words and the title only their starts.
        rank = (_all(whole, title), TITLE) if title_hit else best[:2]
        if title_hit and best is not None and best[:2] > rank:
            rank = best[:2]
        hits.append({
            "source": "chat",
            "session_id": session_id,
            "title": title or "New chat",
            "updated_at": updated_at,
            "owner": username if user_id is None else None,
            "turn_id": best[2] if best else None,
            "matched_in": _MATCHED_IN[rank[1]],
            "snippet": make_snippet(best[3], terms) if best else "",
            "match_count": 0,
            "_tier": rank[1],
            "_exact": rank[0],
            "_turns": turns,
            "_keys": [],
        })
    return hits


@lru_cache(maxsize=16)
def _since_ids(scope: int | None, since: datetime) -> frozenset:
    """The chats in scope last active since ``since``; one read per search (``since`` is that search's own time).

    Used in Python, never sent back to SQL: an ``IN`` list of a thousand chat ids slowed every index query.
    """
    qs = ChatSession.objects.order_by().filter(updated_at__gte=since)
    if scope is not None:
        qs = qs.filter(user_id=scope)
    return frozenset(qs.values_list("session_id", flat=True))


def _in_window(session_id, scope: int | None, since: datetime | None) -> bool:
    return since is None or session_id in _since_ids(scope, since)


def _entries(scope: int | None, **filters):
    """Index rows (``ChatSearchEntry``) of the chats in scope. The date window is applied to the hits, in Python.

    Never joined to ``assistant_chat_session``: on dev and prod that table is legacy latin1 and ~1 GB, and MySQL
    ran such a join from a scan of it (10-26).
    """
    qs = ChatSearchEntry.objects.filter(**filters)
    if scope is not None:
        qs = qs.filter(user_id=scope)
    return qs


def _with_meta(found: dict, source: str, tier: int, scope: int | None) -> list[dict]:
    """Hits for ``found`` ({chat: {"turn", "snippet", "turns", ...}}), titles and owners read in one query."""
    if not found:
        return []
    hits = []
    meta = ChatSession.objects.order_by().filter(pk__in=list(found))
    for session_id, title, updated_at, username in meta.values_list("session_id", "title", "updated_at", "user__username"):
        f = found[session_id]
        hits.append({"source": source, "session_id": session_id, "title": title or "New chat",
                     "updated_at": updated_at, "owner": username if scope is None else None,
                     "turn_id": f["turn"], "matched_in": _MATCHED_IN[tier], "snippet": f.get("snippet", ""),
                     "match_count": 0, "_tier": tier, "_turns": set(f["turns"]), "_keys": [],
                     "_record": f.get("record"), "_exact": f.get("exact", False)})
    return hits


def search_steps(scope: int | None, terms: list[str], since: datetime | None) -> list[dict]:
    """One hit per chat whose Container-CC agent steps (its words or its tool calls) hold every term."""
    patterns, whole = _patterns(terms), _whole(terms)
    qs = _entries(scope, source="steps")
    for t in terms:
        qs = qs.filter(text__icontains=t)  # a cheap first cut; the word-start rule is checked below
    found: dict = {}
    for session_id, turn_id, text in qs.order_by("turn_id", "id").values_list("session_id", "turn_id", "text"):
        if _in_window(session_id, scope, since) and _all(patterns, text):
            exact = _all(whole, text)
            f = found.setdefault(session_id, {"turn": turn_id, "snippet": make_snippet(text, terms), "turns": set(),
                                              "exact": exact})
            if exact and not f["exact"]:
                f.update(turn=turn_id, snippet=make_snippet(text, terms), exact=True)
            f["turns"].add(turn_id)
    return _with_meta(found, "steps", STEPS, scope)


def _record_hits(qs, match: Q, scope: int | None, since: datetime | None) -> list[dict]:
    """One record hit per chat from index rows (uid or project), counted in SQL.

    The snippet (the first IDs, how many more, a capped turn's total) is written later, for the result page only
    (``_record_snippet``): a type such as TIS matches tens of thousands of rows on dev.
    """
    turns = defaultdict(set)
    for session_id, turn_id in qs.order_by().values_list("session_id", "turn_id").distinct():
        if _in_window(session_id, scope, since):
            turns[session_id].add(turn_id)
    if not turns:
        return []
    # One pass over the user's index rows (~100 ms a pass on a dev-sized table); the ID counts come per page hit.
    found = {sid: {"turn": min(t), "turns": t, "record": (match, frozenset(t))} for sid, t in turns.items()}
    return _with_meta(found, "record", RECORD, scope)


def _record_snippets(page: list[dict]) -> None:
    """Write each record hit's snippet on the result page: 'ID, ID, ID and N more', plus '(first 500 of T results
    searched)' when a matched turn went past the cap. Each query is by chat id (the session index), never by user:
    a small one per record hit for its first IDs, and one for the page's turn totals."""
    hits = {h["session_id"]: h for h in page if h["source"] == "record"}
    if not hits:
        return
    first: dict = defaultdict(dict)
    n_keys: dict = {}
    for h in hits.values():
        mine = ChatSearchEntry.objects.filter(h["_record"][0], session_id=h["session_id"])
        # A row per (ID, where): the first 9 rows of a chat hold its first 3 IDs.
        first[h["session_id"]] = dict.fromkeys(
            mine.order_by("turn_id", "id").values_list("key", flat=True)[:_SHOWN_IDS * 3])
        n_keys[h["session_id"]] = mine.order_by().values("key").distinct().count()
    totals: dict = defaultdict(list)
    turn_rows = ChatSearchEntry.objects.filter(session_id__in=list(hits), source="record", kind="turn")
    for session_id, turn_id, total in turn_rows.values_list("session_id", "turn_id", "text"):
        if turn_id in hits[session_id]["_record"][1] and total.isdigit() and int(total) > RESULT_CAP:
            totals[session_id].append(int(total))
    for session_id, h in hits.items():
        shown = list(first[session_id])[:_SHOWN_IDS]
        n = n_keys[session_id]
        more = f" and {n - len(shown)} more" if n > len(shown) else ""
        note = f" (first {RESULT_CAP} of {max(totals[session_id])} results searched)" if totals[session_id] else ""
        h["snippet"] = ", ".join(shown) + more + note


#: What separates the words of an ID or a code (TIS-220831FLY-26, D.SEQ-..., P.FLY-..._x.docx, a project name).
_KEY_SEPARATORS = ("-", ".", "_", "/", " ")


def _starts_word(term: str) -> Q:
    """``term`` where a word of the key starts, as case-insensitive LIKEs: MySQL's REGEXP cost ~70 ms a pass over
    dev's 100k index rows, and the record search makes two passes."""
    q = Q(key__istartswith=term)
    for sep in _KEY_SEPARATORS:
        q |= Q(key__icontains=sep + term)
    return q


def search_records(scope: int | None, terms: list[str], since: datetime | None) -> list[dict]:
    """One hit per chat that showed a record (an ID, or a project the question named) matching every term."""
    match = Q(source="record", kind__in=("uid", "project"))
    for t in terms:
        match &= _starts_word(t)  # in SQL: no row ids travel back and forth
    hits = _record_hits(_entries(scope).filter(match), match, scope, since)
    if hits:
        ids = [h["session_id"] for h in hits]
        # Chats where every term is a whole ID or code, or an ID's type (TIS in TIS-220831FLY-26, D.SEQ in
        # D.SEQ-...): both anchored at the key's start, so each is a range on the (user, key) index.
        exact = None
        for t in terms:
            rows = _entries(scope, source="record", kind__in=("uid", "project"), session_id__in=ids).filter(
                Q(key__iexact=t) | Q(key__istartswith=t + "-"))
            found = set(rows.order_by().values_list("session_id", flat=True).distinct())
            exact = found if exact is None else exact & found
        for h in hits:
            h["_exact"] = h["session_id"] in exact
    return hits


Source = Callable[[int | None, list[str], datetime | None], list[dict]]
SOURCES: dict[str, Source] = {"chats": search_chats, "records": search_records, "steps": search_steps}


def _filtered(scope, since, record_type, project, where):
    """(the chats the record filters allow, in the date window; the match of the first filter), or (None, None).

    Each filter's chats are read as a distinct set (at most one per chat); two filters meet in Python. Not a SQL
    subquery: MySQL 8 ran ``session_id IN (SELECT ...)`` over the index table in 6 s at dev's size.
    """
    parts = []
    if record_type or where:
        parts.append(Q(source="record", kind="uid", **({"record_type": record_type} if record_type else {}),
                       **({"text": where} if where else {})))
    if project:
        parts.append(Q(source="record", kind="project", key=project))
    if not parts:
        return None, None
    sets = [set(_entries(scope).filter(q).order_by().values_list("session_id", flat=True).distinct()) for q in parts]
    return {sid for sid in set.intersection(*sets) if _in_window(sid, scope, since)}, parts[0]


def _facets(scope, since, session_ids=None, record_type=None, where=None) -> dict:
    """For each record filter value, how many of the chats (all in scope, or ``session_ids``) it would keep.

    A type is counted only on rows that also match the chosen ``where``, and a where only on rows of the chosen
    type: both name the same row, so the count is what choosing that value returns. Read as distinct
    (chat, type, where) and (chat, project) tuples in two queries and counted here. A few matching chats are
    named in the query (``IN``); many are filtered here, because an ``IN`` list of over a thousand chats cost
    MySQL 200-400 ms at dev's size, against ~140 ms for reading every tuple.
    """
    keep = (set(session_ids) if session_ids is not None
            else set(_since_ids(scope, since)) if since is not None else None)
    narrow = {"session_id__in": list(keep)} if keep is not None and len(keep) <= _FACETS_IN_LIST else {}
    types, wheres, projects = defaultdict(set), defaultdict(set), defaultdict(set)
    uid_rows = _entries(scope, source="record", kind="uid", **narrow).order_by()
    for session_id, rtype, place in uid_rows.values_list("session_id", "record_type", "text").distinct():
        if keep is not None and session_id not in keep:
            continue
        if not where or place == where:
            types[rtype].add(session_id)
        if not record_type or rtype == record_type:
            wheres[place].add(session_id)
    project_rows = _entries(scope, source="record", kind="project", **narrow).order_by()
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
        if b is None or (h["_exact"], h["_tier"]) > (b["_exact"], b["_tier"]):
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
                    where: str | None = None, facets_only: bool = False, facets: bool = False) -> dict:
    """Hits from every named source, merged to one per chat, best tier first and newest first within a tier.

    ``user_id``'s chats only, unless ``all_users``: then every user's, each hit naming its owner. The caller
    decides who may ask for that (the view: ``may_read_any``). The record filters (``record_type``, ``project``,
    ``where``) keep only the chats that showed such a record, and need no words. With ``facets`` the response
    counts, for each filter value, the matching chats it would keep (the sidebar asks only while its options panel
    is open: the counts were the largest part of a search on dev); ``facets_only`` returns the counts over every
    chat in scope.
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
    allowed, filter_match = _filtered(scope, since, record_type, project, where)
    if terms:
        hits = [h for name in sources for h in SOURCES[name](scope, terms, since)]
        if allowed is not None:
            hits = [h for h in hits if h["session_id"] in allowed]
    elif allowed is not None:
        rows = _entries(scope).filter(filter_match)
        if len(allowed) <= _FACETS_IN_LIST:  # few chats left: count only theirs
            rows = rows.filter(session_id__in=list(allowed))
        hits = [h for h in _record_hits(rows, filter_match, scope, since) if h["session_id"] in allowed]
    else:
        return out
    hits = _merge(hits)
    # Whole-word matches first (an exact ID before IDs it starts), then title > question > answer > record > steps.
    hits.sort(key=lambda h: (h["_exact"], h["_tier"], h["updated_at"]), reverse=True)
    if facets:
        out["facets"] = _facets(scope, since, [h["session_id"] for h in hits], record_type=record_type, where=where)
    page = hits[:limit]
    _record_snippets(page)
    for h in hits:
        for private in ("_tier", "_turns", "_keys", "_record", "_exact"):
            h.pop(private, None)
    out["total"] = len(hits)
    out["results"] = page
    return out
