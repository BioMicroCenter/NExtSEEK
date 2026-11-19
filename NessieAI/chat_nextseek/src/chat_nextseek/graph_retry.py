"""The wording of the graph turn's zero-row re-query, shared by the NS orchestrator and the CC aggregate op.

One query that ran and matched nothing gets exactly one more go, and the reply is told when the second query's
filter changed the answer, so it can qualify what the result covers without narrating the retry
(``_execute_graph_turn`` in ``orchestrator.py``). The CC ``aggregate`` op (``NessieAI/ns/aggregate.py``) retries
a zero part the same way, so both engines ask the graph agent in the same words and disclose a changed filter in
the same words.
"""
from __future__ import annotations

import re

from .graph_scope import HIDDEN_SAMPLE_PROPERTIES

_TIMED_OUT = re.compile(r"TransactionTimedOut|25N14|not completed within")


def timed_out(result: dict) -> bool:
    """True when a failed graph result is Neo4j stopping the statement for time."""
    return not result.get("ok") and bool(_TIMED_OUT.search(str(result.get("error") or "")))


def normalized_cypher(cypher: str | None) -> str:
    """The statement with whitespace collapsed, to tell a repeat from a new statement."""
    return " ".join((cypher or "").split())


def timeout_retry_context(cypher: str) -> str:
    """The retry message for a statement Neo4j stopped for time. The generic error text hid that it was a cost
    problem, and the agent wrote the same walk again (production review 4 Oct: three statements, 237 s)."""
    return (
        "Your previous Cypher query was stopped for time, so it was too expensive for this graph:\n"
        f"{cypher}\n\n"
        "Do not rerun it or a variant of it. Start from the narrow side: when an assay, an edge property "
        "or a named value picks out few records, match those first and carry them forward "
        "(for example MATCH ... WHERE ... WITH DISTINCT ...), then walk from them to the type you need. "
        "Never scan every sample of a type and walk from each one."
    )


def zero_row_retry_context(cypher: str) -> str:
    """The retry message for a query that ran without error and matched nothing.

    Pilot A v2 (2026-09-18), ChIP-seq: this message used to say "use the closest value that really exists". The
    first query had correctly found nothing; the agent took the invitation, swapped in every Chromatin Sequencing
    Analysis sample, and the reply led with 12 Hi-C samples. A retry may repair a guess. It may not answer a
    different question.
    """
    return (
        "Your previous Cypher query ran without error and matched 0 records:\n"
        f"{cypher}\n\n"
        "Find the one filter that was a guess and change only that one: a field you "
        "inferred, a whole-value match on free text, a code or name you did not read "
        "from the catalog, a capitalisation or punctuation you assumed. Keep every "
        "term the user actually wrote, and never replace the thing the user asked for "
        "with a different one: not another technique, assay, sample type, person or "
        "sample. A named technique, product or UID that matches nothing under its own "
        "spellings is a real zero. If every filter was certain, return the SAME query "
        "unchanged - zero is a valid answer and a second guess would be worse than it."
    )


#: Told to the reply when the first query matched nothing and a second query with a changed filter found something.
#:
#: It used to end "so say that the original filter found nothing and what was used instead", and the reply did
#: exactly that: "Is there a scientist named Kate Bridges associated with the Impact project?" (production turn 461,
#: 2026-09-23) answered "An initial search returned no matches, so a graph query over the sample network was run
#: instead, matching ...". Operator ruling the same day: the reply never narrates the retry path. It says the
#: finding, and qualifies it only when what the result MEANS differs from the question.
RETRY_CHANGED_ANSWER_NOTE = (
    "This result comes from a query with a changed filter, run after an earlier one matched "
    "nothing. Do not narrate that: never say that a first or initial search found nothing, "
    "that the search was retried, or that another query was run instead. State the finding. "
    "Qualify it, in one clause after the answer, only when the result means something "
    "different from what the user asked: it matched the name as free text anywhere in the "
    "record rather than in the field the user named, it matched titles that merely contain "
    "the name, or it is truncated or capped. Then say what the result covers, not how it "
    "was reached."
)


#: Scope refusals a rewrite can cure: each is about how the statement is written, not about data the caller may not
#: see. A statement refused for any other code (a label or relationship a member may not read, a variable-length walk,
#: no scope) gets no rewrite. Production (who-asked-2, R1): members' counts refused for these codes were answered by a
#: word search that counted something else.
SCOPE_REPAIR_CODES = frozenset({"unjoined_node", "hidden_property", "pattern_expression", "function_not_allowed",
                                "dynamic_property", "whole_properties"})

_HIDDEN = ", ".join(sorted(HIDDEN_SAMPLE_PROPERTIES))
#: What the graph agent is told for each curable code, beside the check's own reasons.
_SCOPE_REPAIR_HINTS = {
    "unjoined_node": (
        "A Study is read only through a sample it holds, (s:Sample)-[:IN_STUDY]->(st:Study); an Investigation "
        "through such a study or through its Project; a Person through MEMBER_OF to a Project. Never walk down from "
        "a Project or an Investigation to its studies: to count or list studies, reach them from the samples."),
    "hidden_property": f"These sample properties are hidden and may not be read: {_HIDDEN}. Leave them out.",
    "pattern_expression": "Test a pattern with EXISTS { MATCH ... }, never a pattern inside an expression.",
    "function_not_allowed": "Use only built-in Cypher functions; no apoc and no procedure.",
    "dynamic_property": "Name each property (s.Name or s.`Some name`); never subscript a node with a key, s[k].",
    "whole_properties": "Name each property you need; never properties() or a map projection of every property.",
}


def scope_repair_context(result: dict) -> str | None:
    """The one rewrite a scope-refused statement gets, told the check's own reasons; None when the result is not a
    refusal or any of its codes is one a rewrite cannot cure (``SCOPE_REPAIR_CODES``)."""
    scope = result.get("scope") if isinstance(result, dict) else None
    if not isinstance(scope, dict) or scope.get("decision") != "refused":
        return None
    codes = list(dict.fromkeys(scope.get("codes") or ()))
    if not codes or not set(codes) <= SCOPE_REPAIR_CODES:
        return None
    reasons = "; ".join(str(r) for r in scope.get("reasons") or ()) or ", ".join(codes)
    hints = " ".join(_SCOPE_REPAIR_HINTS[c] for c in codes)
    return (
        "Your previous Cypher query was not run: it could not be confirmed to stay within the user's projects. "
        f"The check's reasons: {reasons}. {hints} Rewrite it once to answer the same question with the same "
        "filters, in a form the check accepts."
    )


_COUNT_WORDS = re.compile(r"\bhow\s+many\b|\bnumber\s+of\b|^\W*count\b|\bcount\s+(?:of|the|all|every|each)\b"
                          r"|\bbreak(?:s|ing)?\s+(?:\w+\s+){0,4}?down\b"
                          r"|\bbroken\s+down\b|\bbreakdown\b|\btally\b", re.I)
_COUNT_CALL = re.compile(r"\bcount\s*\(", re.I)


def asks_for_a_count(question: str | None, cypher: str | None) -> bool:
    """Is this a count or a breakdown: the question asks for one, or the statement written for it counts?

    A refused count is never answered by the word search (who-asked-2, R1): that search counts the records that
    mention a word, which is another number. The statement is read masked, so a ``count(`` inside a string or a
    comment is not a count, and a ``COUNT { }`` subquery is a filter, not one.
    """
    if _COUNT_WORDS.search(question or ""):
        return True
    from .cypher_text import mask_cypher
    return bool(_COUNT_CALL.search(mask_cypher(cypher or "")))
