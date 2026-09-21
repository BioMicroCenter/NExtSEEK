"""The wording of the graph turn's zero-row re-query, shared by the NS orchestrator and the CC aggregate op.

One query that ran and matched nothing gets exactly one more go, and the reply is told when the second query's
filter changed the answer (``_execute_graph_turn`` in ``orchestrator.py``). The CC ``aggregate`` op
(``NessieAI/ns/aggregate.py``) retries a zero part the same way, so both engines ask the graph agent in the same
words and disclose a changed filter in the same words.
"""
from __future__ import annotations


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
RETRY_CHANGED_ANSWER_NOTE = (
    "The first query for this question matched nothing. This result comes from a "
    "second query with a changed filter, so say that the original filter found "
    "nothing and what was used instead."
)
