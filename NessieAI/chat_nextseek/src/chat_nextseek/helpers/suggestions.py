"""Suggestion chips (#128): the contract and the guardrails.

The graph-result reviewer (``graph_review``) can return a ``suggest`` verdict with one concrete next question. This
module turns it into a chip: a label the chat panel shows and a ``query`` that one click sends as the user's next
message. The shape (SPEC 3.2), stored under ``debug.suggestions``::

    {"id": "b<bundle>-r<i>", "source": "reviewer", "kind", "label" (<= 60), "query" (<= 300), "reason",
     "expected_count"?, "alt"? (debug only, never shown)}

The guardrails a chip must pass (``check_suggestion``):

* a non-empty ``label`` and ``query`` within their limits, and a query the reviewer did not cut short;
* C7, self-contained: the router's follow-up cue check must not fire on the query. The router sends a message that
  refers back ("those", "just the X ones") to Container-CC, so a chip that refers back would turn a click into a CC
  turn. The check is ``NessieAI.router.followup.followup_cue`` unless the caller passes another; when it cannot be
  imported, every suggestion is rejected rather than risk a CC turn;
* C2, no write verbs: ``create add update edit delete remove rename upload register write`` as whole words, any case
  ("added" and "uploaded" pass);
* C8, no Cypher, field names or operators, in the label and the query (``CHIP_TEXT_RULES``): the upper-case keywords
  ``MATCH``, ``RETURN``, ``WHERE`` and ``CONTAINS``; a ``$param``; ``->`` and ``<-``; a node pattern ``(s:Label`` or
  ``(:Label``, which also catches lower-case Cypher; a graph label ``T_HUMAN``; a ``variable.property`` form with a
  one or two letter lower-case variable (``s.Classification``, ``st.sample_count``; ``e.g`` and ``i.e`` excepted,
  upper-case codes such as ``D.SEQ`` untouched); any ``=`` (so ``!=``, ``>=``, ``<=``, ``=~``) and ``<>``; a
  snake_case name directly before ``<`` or ``>`` (``sample_count > 0``); ``IS NULL`` and ``IS NOT NULL`` in any case.
  The rules are narrow on purpose, so what they let through is known: lower-case "where", "match" and "return" are
  English; a plain ``<`` or ``>`` passes (``age > 60``); a longer variable (``sample.Classification``) passes; and a
  snake_case name on its own (``internal_assay_title``) passes, because stored values such as ``whole_blood`` are
  snake_case too and would lose their chips.

``pending_for`` remembers the chips offered on a turn in the session; ``accept`` recognises the next message as a
click (its text is exactly a chip's query, on the very next turn) and always clears what was pending (C5).

Pure: no Django, no I/O. ``session`` is anything with ``get``, ``pop`` and item assignment.
"""
from __future__ import annotations

import re
from typing import Any, Callable

MAX_SUGGESTIONS = 2
MAX_LABEL = 60
MAX_QUERY = 300

#: What ``graph_review._clip`` appends to text it cuts short (U+2026). A clipped query has lost its end, which for a
#: relaxed variant is the very instruction the chip adds, so the click would re-run the original search.
CLIP_MARKER = "\u2026"

SESSION_KEY = "pending_suggestions"
SOURCE = "reviewer"

WRITE_VERB = re.compile(r"\b(create|add|update|edit|delete|remove|rename|upload|register|write)\b", re.IGNORECASE)
#: C8, (rule, pattern) in the order a rejection names them. See the module docstring for what each lets through.
CHIP_TEXT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # upper-case only: "where", "match" and "return" are ordinary English in a question
    ("keyword", re.compile(r"\b(?:MATCH|RETURN|WHERE)\b|CONTAINS")),
    ("parameter", re.compile(r"\$\w+")),
    ("relationship arrow", re.compile(r"->|<-")),
    ("node pattern", re.compile(r"\(\s*\w*:[A-Za-z_]")),
    ("graph label", re.compile(r"\bT_[A-Z0-9_]+\b")),
    ("field name", re.compile(r"(?<![\w.])(?!(?:e\.g|i\.e)\b)[a-z]{1,2}\.[A-Za-z_]\w*")),
    ("comparison operator", re.compile(r"=|<>")),
    ("compared field", re.compile(r"\b[A-Za-z][A-Za-z0-9]*_\w*\s*[<>]")),
    ("null test", re.compile(r"\bIS\s+(?:NOT\s+)?NULL\b", re.IGNORECASE)),
)

RefersBack = Callable[[str], Any]


def _router_followup_cue() -> RefersBack | None:
    """The router's follow-up cue check, or None when it cannot be loaded (which rejects every suggestion)."""
    try:
        from NessieAI.router import followup
        return followup.followup_cue
    except Exception:
        return None


def _resolve(refers_back: RefersBack | None) -> RefersBack | None:
    return refers_back if refers_back is not None else _router_followup_cue()


def _text(value: Any) -> str | None:
    """``value`` with its whitespace collapsed, or None when it is not text."""
    return " ".join(value.split()) if isinstance(value, str) else None


def _reject_reason(s: Any, cue: RefersBack | None) -> str | None:
    if not isinstance(s, dict):
        return "the suggestion is not a mapping"
    label, query = _text(s.get("label")), _text(s.get("query"))
    if not label:
        return "the label is empty"
    if len(label) > MAX_LABEL:
        return f"the label is over {MAX_LABEL} characters"
    if not query:
        return "the query is empty"
    if len(query) > MAX_QUERY:
        return f"the query is over {MAX_QUERY} characters"
    if query.endswith(CLIP_MARKER):
        return "the query was cut short"
    for text in (label, query):
        m = WRITE_VERB.search(text)
        if m:
            return f"write verb '{m.group(0).lower()}'"
        for rule, pattern in CHIP_TEXT_RULES:
            m = pattern.search(text)
            if m:
                return f"Cypher in the chip text: {rule} '{m.group(0)}'"
    if cue is None:
        return "the router's follow-up check is not available"
    try:
        hit = cue(query)
    except Exception as exc:
        return f"the follow-up check failed: {type(exc).__name__}"
    if hit:
        return f"the query refers back to an earlier answer ({hit})"
    return None


def check_suggestion(s: dict, *, refers_back: RefersBack | None = None) -> str | None:
    """Why ``s`` cannot be a chip, or None when it can.

    ``refers_back`` returns something truthy for a query that refers back to an earlier answer; it defaults to the
    router's ``followup_cue``. Label and query are checked with their whitespace collapsed, as the chip will carry them.
    """
    return _reject_reason(s, _resolve(refers_back))


def suggestions_from_review(review: dict, *, bundle_id: int, refers_back: RefersBack | None = None) -> list[dict]:
    """The chips for one reviewed graph turn: at most ``MAX_SUGGESTIONS``, every one past ``check_suggestion``.

    Only a ``suggest`` verdict with a suggestion makes chips. ``ok`` makes none, and so does ``note`` (breakage the
    reply states plainly) and a ``suggest`` that carries only a disclosure (a zero, a premise or an unapplied value).
    ``review`` is the reviewer's dict (``graph_review.as_debug``); its ``suggestion`` is one dict or a list of them.
    """
    if not isinstance(review, dict) or review.get("verdict") != "suggest":
        return []
    raw = review.get("suggestion")
    candidates = raw if isinstance(raw, list) else [raw]
    cue = _resolve(refers_back)
    out: list[dict] = []
    seen: set[str] = set()
    for s in candidates:
        if len(out) >= MAX_SUGGESTIONS:
            break
        if _reject_reason(s, cue) is not None:
            continue
        query = _text(s["query"])
        if query in seen:  # two chips with one text would make a click ambiguous
            continue
        seen.add(query)
        chip = {"id": f"b{bundle_id}-r{len(out)}", "source": SOURCE, "kind": s.get("kind"),
                "label": _text(s["label"]), "query": query, "reason": s.get("reason") or ""}
        n = s.get("expected_count")
        if isinstance(n, int) and not isinstance(n, bool):
            chip["expected_count"] = n
        if isinstance(s.get("alt"), dict):
            chip["alt"] = dict(s["alt"])
        out.append(chip)
    return out


def pending_for(session, items: list[dict], *, turn_id: int) -> None:
    """Remember the chips offered on ``turn_id``, replacing anything pending. No chips clears the entry."""
    if not items:
        session.pop(SESSION_KEY, None)
        return
    session[SESSION_KEY] = {"for_turn": turn_id, "items": [dict(i) for i in items]}


def accept(session, user_text: str, *, last_turn_id: int) -> dict | None:
    """The chip this message clicked, or None. Always clears what was pending, so a chip is good for one turn.

    A click is a message whose stripped text equals a chip's ``query``, sent right after the turn that offered it
    (``last_turn_id`` is that turn's id). Any turn in between, a CC turn included, cancels the offer.
    """
    pending = session.pop(SESSION_KEY, None)
    if not isinstance(pending, dict) or not isinstance(user_text, str):
        return None
    for_turn = pending.get("for_turn")
    if for_turn is None or for_turn != last_turn_id:
        return None
    text = user_text.strip()
    if not text:
        return None
    for item in pending.get("items") or []:
        if isinstance(item, dict) and item.get("query") == text:
            return item
    return None
