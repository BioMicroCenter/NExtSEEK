"""Follow-ups go to Container-CC: the deterministic half of the 2026-09-23 ruling.

The operator retired the NExtSEEK engine's follow-up path from routing: "have all follow
ups go to container_cc, and ENSURE that container_cc has access to the previous run's
artifacts". The BAML router is told the same thing in ``router.baml``; this module is the
backstop that holds the ruling when the model does not (or when BAML is down and the
keyword heuristic decides), applied by ``policy._decide_route`` to an NS-bound turn only.

A turn is a follow-up when BOTH hold:

1. the chat already has an answered engine turn in the router's history window (an
   ``nextseek_query`` or ``container_cc`` turn that completed). A first message, or one
   after only ``unrelated`` asides, has nothing to follow up on, and
2. the message REFERS BACK to that turn: "of those", "which of them", "those 73",
   "break that down", "remind me", "what did you find", "what query did you run",
   "download those", "plot that", "that chart", "the file", "same search but",
   "just the D.SEQ ones", "what about the liver?", "and how many are female?".

A message that refers back to nothing is SELF-CONTAINED, and that is the whole
definition: the cues are anaphora and explicit back-references, never subject matter,
so "How many HeLa samples do we have?" matches none of them and stays with the router's
choice, in a fresh chat or in one already on container_cc (2026-09-23 ruling: a
self-contained question is routed normally again even after CC turns). A miss costs one turn on the NS
engine, which still has its own follow-up code; a false hit costs one CC turn that is
handed the whole previous result. Both are recoverable, and the second is the direction
the ruling asks for.

Pure: no Django, no BAML, no I/O, so it is importable anywhere the router is.
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable

_ROUTE_NS = "nextseek_query"
_ROUTE_CC = "container_cc"

#: Things a previous turn returned that a follow-up can point back at.
_RESULT_NOUNS = (
    r"(?:samples?|results?|ones|rows?|records?|entries|items|hits|matches|uids?|mice|mouse|"
    r"monkeys?|nhps?|animals?|patients?|donors?|subjects?|tissues?|cells?|cell lines?|"
    r"datasets?|files?|runs?|studies|study|assays?|projects?|types?|species|labs?|"
    r"numbers?|counts?|values?|list|table|groups?)"
)

#: (label, pattern). The label names the cue in the route decision's reasoning, so a
#: surprising CC turn can be traced to the phrase that sent it there.
_CUES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        # "of those", "among these", "which of them", "for the results", "follow up on those"
        # Not "from them": "data derived from them" points inside its own sentence.
        ("of-those", r"\b(?:of|among|amongst|within|across|for|in|on|about|with|to)\s+"
                     r"(?:those|these|them|the\s+(?:results?|above))\b"
                     r"|\bfrom\s+(?:those|these|the\s+(?:results?|above))\b"),
        # "those 73", "these samples", "those NHP samples"
        ("those-noun", r"\b(?:those|these)\s+(?:[\d,]+\s+)?(?:[A-Za-z.]+\s+){0,2}?" + _RESULT_NOUNS + r"\b"),
        # "the previous search", "your last answer", "that same query", "the earlier list"
        ("previous-result", r"\b(?:the|your|that|this|my)\s+(?:very\s+)?(?:previous|last|prior|earlier|"
                            r"above|same|original)\s+(?:search|query|cypher|result|results|list|"
                            r"answer|turn|question|table|file|download|count|number|output)\b"),
        # "same search but", "that search again", "that number"
        ("that-search", r"\b(?:same|that)\s+(?:search|query|cypher|filter|result|results|list|table|"
                        r"file|number|count|answer)\b"),
        # "what query did you run", "which cypher did you use", "how did you search"
        ("how-did-you", r"\b(?:what|which)\s+(?:query|queries|cypher|search|filters?|endpoint)\s+"
                        r"(?:did|do|have)\s+you\b|\bhow\s+did\s+you\s+(?:search|find|get|count|query)\b"),
        # "what did you find", "what did you return"
        ("what-did-you", r"\bwhat\s+(?:did|have)\s+you\s+(?:just\s+)?(?:find|found|return|returned|get|got|"
                         r"run|search|show|count)\b"),
        ("remind-me", r"\bremind\s+me\b|\b(?:was|is)\s+(?:that|it)\s+again\b|\bthat\s+again\b"),
        # "break that down", "break down those", "group them by", "split those up"
        ("break-down", r"\b(?:break|split|group|sort|filter|narrow|slice|bucket)\s+(?:down\s+|up\s+)?"
                       r"(?:that|those|these|them|it)\b"),
        # "download those", "plot that", "export them", "show me those", "chart it"
        ("act-on-it", r"\b(?:download|export|save|plot|chart|graph|visuali[sz]e|tabulate|summari[sz]e|"
                      r"compare|list|show|give|send|write)\s+(?:me\s+)?(?:all\s+)?(?:of\s+)?"
                      r"(?:those|these|them|that|it)\b(?!\s+(?:is|was|has|have)\b)"),
        # "just the D.SEQ ones", "only the 4 week ones", "the ones from China instead",
        # "the female ones"
        ("the-ones", r"\b(?:just|only)\s+the\s+.{1,40}?\s+ones\b|\bthe\s+ones\s+(?:from|with|that|in|where|"
                     r"which|without|over|under|above|below)\b|\bthe\s+(?:[\w.-]+\s+){1,3}ones\b"),
        # "that chart", "this table", "send me the file", "the csv". Not "the list" or
        # "the report" or "the graph": those open self-contained questions too.
        ("that-artifact", r"\b(?:that|this)\s+(?:chart|plot|graph|figure|file|spreadsheet|workbook|csv|"
                          r"table|download|report|list|breakdown|summary)\b|\bthe\s+(?:chart|plot|figure|"
                          r"file|spreadsheet|workbook|csv|download)\b"),
        ("which-ones", r"\bwhich\s+ones\b"),
        # "are they all female?", "were any of those treated?"
        ("pronoun-subject", r"\b(?:are|were|do|did|have|has)\s+(?:any\s+of\s+|all\s+of\s+)?"
                            r"(?:they|those|these)\b"),
        # "what about the liver?", "and how about 2023?"
        ("what-about", r"^\s*(?:and\s+|but\s+|so\s+|ok(?:ay)?[,.]?\s+)?(?:what|how)\s+about\b"),
        # "And how many are female?", "Now only the lung", "then split by lab"
        ("continues", r"^\s*(?:and|now|also|then)\b[\s,]+(?:only|just|how|which|what|show|list|filter|"
                      r"split|group|break|plot|give|sort|count|exclude|include|keep|drop|restrict)\b"),
    )
)


def followup_cue(query: str | None) -> str | None:
    """The label of the first back-reference cue in ``query``, or None."""
    text = " ".join(str(query or "").split())
    if not text:
        return None
    for label, pattern in _CUES:
        if pattern.search(text):
            return label
    return None


def _get(turn: Any, key: str) -> Any:
    if isinstance(turn, dict):
        return turn.get(key)
    return getattr(turn, key, None)


def has_answered_engine_turn(history: Iterable[Any] | None) -> bool:
    """True when the history holds a completed ``nextseek_query`` or ``container_cc`` turn.

    Accepts the router's ``HistoryTurn`` objects or raw ``chat_log`` dicts. A legacy
    ``chat_log`` entry with no ``router_choice`` is an NS turn (``router_context``'s own
    derivation), and one with no ``status`` completed.
    """
    for turn in history or []:
        choice = _get(turn, "router_choice")
        if choice is None and isinstance(turn, dict):
            choice = _ROUTE_CC if turn.get("mode") == "cc" else _ROUTE_NS
        status = _get(turn, "status") or "completed"
        if choice in (_ROUTE_NS, _ROUTE_CC) and status == "completed":
            return True
    return False


def followup_reason(query: str | None, history: Iterable[Any] | None) -> str | None:
    """Why this turn is a follow-up (the cue's label), or None when it is not one."""
    history = list(history or [])
    if not has_answered_engine_turn(history):
        return None
    return followup_cue(query)


# ------------------------------------------------------------------------------------------------
# The follow-up split (operator 2026-09-24, routing review section 5).
#
# A follow-up NExtSEEK can answer from the earlier result or by re-running the earlier search stays
# on nextseek_query; one that needs a file, a chart, code, a comparison, summary or analysis goes to
# container_cc; once a chat has a completed container_cc turn, anything that refers back stays there.
# The rule text the router reads (RouterInput.followup_rule) and the guard in policy._decide_route
# both read NESSIE_FOLLOWUP_ROUTING through this module, so they cannot disagree. "cc" restores the
# 2026-09-23 rule (every follow-up to container_cc) word for word, with no rebuild: set it in the
# box's env file and recreate the app container. ``followup_cue`` above is unchanged; the reviewer's
# chip guard depends on it.
# ------------------------------------------------------------------------------------------------

FOLLOWUP_ROUTING_ENV = "NESSIE_FOLLOWUP_ROUTING"


def followup_mode() -> str:
    """``"split"`` (the default) or ``"cc"``; any other value is ``"split"``."""
    value = str(os.environ.get(FOLLOWUP_ROUTING_ENV) or "").strip().lower()
    return "cc" if value == "cc" else "split"


# Words that make a follow-up need more than NExtSEEK's re-run of the earlier search: a file or a
# download, a chart, code, or a comparison, summary or analysis. "graph" is left out on purpose:
# "how many of those are in the graph" is an NExtSEEK question.
_CC_SHAPE = re.compile(
    r"\b(?:download\w*|export\w*|save|saved|files?|csv|excel|xlsx|spreadsheets?|workbooks?|"
    r"plot\w*|charts?|figures?|visuali[sz]\w*|code|scripts?|python|"
    r"compare|comparing|comparison|summari[sz]\w*|summary|report|analy[sz]\w*|send|write)\b",
    re.IGNORECASE,
)


def followup_shape(query: str | None) -> str | None:
    """Which engine a follow-up needs: ``"cc"``, ``"ns"``, or None when ``query`` is not a follow-up.

    ``"cc"`` when the message names a file or download, a chart, code, or a comparison, summary or
    analysis, or points at an artifact ("that chart", "the file"); every other follow-up is ``"ns"``.
    """
    cue = followup_cue(query)
    if cue is None:
        return None
    text = " ".join(str(query or "").split())
    if cue == "that-artifact" or _CC_SHAPE.search(text):
        return "cc"
    return "ns"


#: The 2026-09-23 rule, word for word (router.baml before 2026-09-24).
FOLLOWUP_RULE_CC = """\
Follow-ups go to `container_cc`. When the CURRENT message refers back to
something an earlier turn in this chat returned or did — "of those", "which
of them", "those 73 samples", "break that down", "plot that", "download
those", "remind me", "what did you find", "what query did you run", "same
search but only D.SEQ", "just the 4 week ones" — select `container_cc`,
whichever route answered the earlier turn. That route is handed the earlier
turns' queries, parameters and result files. A new, self-contained question
that names its own subject and needs nothing from an earlier answer is routed
on its own merits, even in a chat that has earlier turns.

A chat that reaches `container_cc` stays there for anything that refers back:
after a completed `container_cc` turn, a message about its results, its chart
or file, or the conversation so far ("those", "that chart", "the file", "what
did you find") is `container_cc`. A self-contained question later in the same
chat — one that refers back to no earlier turn, such as "How many HeLa samples
do we have?" — is routed exactly as it would be in a new chat. An out-of-scope
message is still `unrelated`."""

#: The operator's rule of 2026-09-24 (routing review 5.1).
FOLLOWUP_RULE_SPLIT = """\
A follow-up refers back to something an earlier turn in this chat returned or
did: "of those", "which of them", "those 73 samples", "break that down",
"remind me", "what did you find", "what query did you run", "same search but
only D.SEQ", "just the 4 week ones".

A follow-up goes to `nextseek_query` when NExtSEEK can answer it from the
earlier result or by re-running the earlier search: it counts or filters those
results, breaks them down by one or two fields, recalls what an earlier turn
found or which query it ran, or re-runs the earlier search with one filter
added, removed or swapped.

A follow-up goes to `container_cc` when it needs more than that: a file or a
table to download, a chart or plot, code, a comparison or summary across
several earlier results, a join with a source the graph does not hold, or
open-ended analysis. That route is handed the earlier turns' queries,
parameters and result files.

Once a chat has a completed `container_cc` turn, a message that refers back
goes to `container_cc` whatever it asks ("those", "that chart", "the file",
"what did you find"): NExtSEEK cannot see Container-CC's results.

A new, self-contained question that names its own subject and needs nothing
from an earlier answer is routed on its own merits, even in a chat that has
earlier turns, such as "How many HeLa samples do we have?". An out-of-scope
message is still `unrelated`."""


def followup_rule_text() -> str:
    """The follow-up paragraph the router prompt renders, for the current mode."""
    return FOLLOWUP_RULE_SPLIT if followup_mode() == "split" else FOLLOWUP_RULE_CC
