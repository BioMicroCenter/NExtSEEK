"""The wording of the graph turn's zero-row re-query, shared by the NS orchestrator and the CC aggregate op.

One query that ran and matched nothing gets exactly one more go, and the reply is told when the second query's
filter changed the answer, so it can qualify what the result covers without narrating the retry
(``_execute_graph_turn`` in ``orchestrator.py``). The CC ``aggregate`` op (``NessieAI/ns/aggregate.py``) retries
a zero part the same way, so both engines ask the graph agent in the same words and disclose a changed filter in
the same words.
"""
from __future__ import annotations

import re
from typing import NamedTuple

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


# ---------------------------------------------------------------- identifier spellings ----------------------------
# Production 2026-11-16: "sample tree for patient QXRT-4" found 0 because the patient is stored as ZZ1.QXRT4, and the
# zero-row retry (the graph agent, above) kept 'QXRT-4' because a UID that matches nothing reads as a real zero. A day
# later a reworded question made the agent add 'QXRT4' itself and found 343. Whether the other spelling was tried was
# the model's call; this makes it code. On a zero, a free-text match on an identifier is re-run once with the
# separators optional, before the agent's retry, and the reply is told which spelling matched.

#: The separators an identifier may be written with, or without.
_ID_SEP = r"[-._ /]"
#: ``<var>.search_text ... CONTAINS <term>``: the free-text match this rewrites (a property or a literal term, with
#: the agent's usual toLower( / trim( around it).
_ID_CONTAINS = re.compile(r"\b\w+\.search_text\s*\)*\s*(CONTAINS\s+((?:toLower\(\s*)?(?:trim\(\s*)?)(\$\w+|'[^']*'))",
                          re.I)
#: The parameter the rewritten statement binds its pattern to.
ID_SPELLING_PARAM = "id_spelling"


class IdSpelling(NamedTuple):
    cypher: str       # the statement with that match as a separator-tolerant regular expression
    parameters: dict  # the original parameters plus ``id_spelling``
    term: str         # the identifier as the statement had it
    joined: str       # the same identifier written another way, for the reply ("QXRT4" for "QXRT-4")


def _id_runs(term: str) -> list[str] | None:
    """The letter and digit runs of an identifier-like term, or None when the term is not one.

    An identifier mixes letters and digits ("QXRT-4", "QXRT4", "PAT-990101QXR-4"), or is two digit groups of two or
    more joined by one separator ("71-305"). Words, a single number, a date ("2026-11-12") and a decimal ("0.5") are
    not: a looser match on those finds other values, not other spellings."""
    t = term.strip()
    if not 2 <= len(t) <= 40 or re.search(r"[^A-Za-z0-9\-._ /]", t):
        return None
    runs = re.findall(r"[A-Za-z]+|[0-9]+", t)
    if not 2 <= len(runs) <= 6:
        return None
    has_letters = any(r.isalpha() for r in runs)
    has_digits = any(r.isdigit() for r in runs)
    if has_letters and has_digits:
        return runs
    seps = re.findall(r"[-._ /]+", t)
    if not has_letters and len(runs) == 2 and all(len(r) >= 2 for r in runs) and seps == ["-"]:
        return runs
    return None


def _id_pattern(runs: list[str]) -> str:
    """A whole-string (Neo4j ``=~``) case-insensitive pattern: the runs in order with an optional separator between
    them, and no letter or digit of the same kind glued on either end (QXRT-4 finds QXRT4 and ZZ1.QXRT4, never
    QXRT40, QXRT 4.5 or XQXRT4)."""
    body = (_ID_SEP + "?").join(re.escape(r.lower()) for r in runs)
    before = r"(?<![0-9])" if runs[0][0].isdigit() else r"(?<![a-z])"
    after = r"(?![0-9]|[.,][0-9])" if runs[-1][-1].isdigit() else r"(?![a-z])"
    return "(?is).*" + before + body + after + ".*"


def id_spelling_variant(cypher: str | None, parameters: dict | None) -> IdSpelling | None:
    """The statement with its free-text matches on an identifier made separator-tolerant, or None.

    ``toLower(s.search_text) CONTAINS toLower($term)`` becomes ``toLower(s.search_text) =~ $id_spelling``; the rest
    of the statement (its scope, its RETURN, its LIMIT) is unchanged and runs through the same tool and prover. Every
    match on the same term is rewritten, so each arm of a UNION (the matches, their ancestors, their descendants)
    reads the same spelling; a match on another term is left alone."""
    cy, params = cypher or "", dict(parameters or {})
    hits, first = [], None
    for m in _ID_CONTAINS.finditer(cy):
        tok = m.group(3)
        value = params.get(tok[1:]) if tok.startswith("$") else tok[1:-1]
        if not isinstance(value, str):
            continue
        if first is None:
            runs = _id_runs(value)
            if runs is None:
                continue
            first = value
        elif value.strip().lower() != first.strip().lower():
            continue
        end = m.end(3)
        for _ in range(m.group(2).count("(")):        # close the toLower( / trim( the term was wrapped in
            close = re.match(r"\s*\)", cy[end:])
            if close is None:
                break
            end += close.end()
        hits.append((m.start(1), end))
    if first is None:
        return None
    for start, end in reversed(hits):                 # right to left, so earlier offsets stay valid
        cy = cy[:start] + f"=~ ${ID_SPELLING_PARAM}" + cy[end:]
    params[ID_SPELLING_PARAM] = _id_pattern(runs)
    joined = "".join(runs) if re.search(_ID_SEP, first) else "-".join(runs)
    return IdSpelling(cy, params, first, joined)


def quoted_in(question: str | None, term: str) -> bool:
    """The question writes the identifier in quotes: it asks for exactly that spelling, so nothing else is tried."""
    q = question or ""
    return bool(re.search(r"[\"'‘’“”]\s*" + re.escape(term.strip()) + r"\s*[\"'‘’“”]",
                          q, re.I))


def id_spelling_note(v: IdSpelling) -> str:
    """Told to the reply when the separator-tolerant match found what the exact one did not."""
    return (f"No record contains '{v.term}' exactly as written; this result matches it written with other "
            f"separators or none (for example '{v.joined}'). Say that in one short clause after the answer, so the "
            "user knows which spelling matched.")
