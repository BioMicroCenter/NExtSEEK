"""The zero-row retry message: unchanged without evidence, one block more with it."""
from chat_nextseek.graph_retry import zero_row_retry_context

CY = "MATCH (s:T_QQQ) RETURN count(*)"

OLD = (
    "Your previous Cypher query ran without error and matched 0 records:\n"
    f"{CY}\n\n"
    "Find the one filter that was a guess and change only that one: a field you "
    "inferred, a whole-value match on free text, a code or name you did not read "
    "from the catalog, a capitalisation or punctuation you assumed. Keep every "
    "term the user actually wrote, and never replace the thing the user asked for "
    "with a different one: not another technique, assay, sample type, person or "
    "sample. A named technique, product or UID that matches nothing under its own "
    "spellings is a real zero. If every filter was certain, return the SAME query "
    "unchanged - zero is a valid answer and a second guess would be worse than it."
)


def test_no_evidence_keeps_the_retry_words_unchanged():
    assert zero_row_retry_context(CY) == zero_row_retry_context(CY, []) == OLD


def test_evidence_is_appended_after_the_old_words():
    out = zero_row_retry_context(CY, ["`A` on T_QQQ holds: 'x'"])
    assert out.startswith(OLD) and "- `A` on T_QQQ holds: 'x'" in out and "What the graph holds" in out
