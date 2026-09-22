"""
mask_cypher follows Cypher's rule for backticked names.

A backticked name ends at the first backtick that is not doubled; a doubled backtick is a literal backtick, and a
backslash inside the name is an ordinary character, not an escape. String literals keep their backslash escapes.
When the mask treated a backslash in a backticked name as an escape, the text after a name ending in a backslash was
blanked up to the next backtick, so write_clause could not see a clause the server runs.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 2.4.
"""
from __future__ import annotations

import pytest

from chat_nextseek.cypher_text import mask_cypher, write_clause


def test_a_backslash_ends_nothing_inside_a_backticked_name():
    text = "MATCH (s:Sample) WHERE s.`x\\` = 1 CREATE (n) RETURN 1 //`"
    masked = mask_cypher(text)
    assert len(masked) == len(text)
    assert "CREATE (n) RETURN 1" in masked
    name_start = text.index("`")
    name_end = text.index("`", name_start + 1)
    assert masked[name_start:name_end + 1].strip() == ""
    assert write_clause(text) == "CREATE"


def test_the_text_after_a_name_ending_in_a_backslash_is_scanned():
    text = "MATCH (s:`A\\`) DETACH DELETE s"
    assert "DETACH DELETE s" in mask_cypher(text)
    assert write_clause(text) == "DETACH DELETE"


def test_a_doubled_backtick_is_a_literal_backtick_inside_one_name():
    text = "MATCH (s:Sample) RETURN s.`a``SET``b` AS x"
    masked = mask_cypher(text)
    start = text.index("`")
    end = text.rindex("`")
    assert masked[start:end + 1].strip() == ""
    assert masked.endswith(" AS x")
    assert write_clause(text) is None


def test_string_literals_keep_backslash_escapes():
    text = "MATCH (s:Sample) WHERE s.title = 'it\\'s a SET' RETURN s.id"
    masked = mask_cypher(text)
    assert "SET" not in masked
    assert masked.endswith(" RETURN s.id")
    assert write_clause(text) is None


def test_double_quoted_strings_keep_backslash_escapes():
    text = 'MATCH (s:Sample) WHERE s.title = "a \\" CREATE" RETURN s.id'
    assert write_clause(text) is None


def test_an_unterminated_backticked_name_masks_to_the_end():
    text = "MATCH (s:Sample) RETURN s.`open CREATE"
    masked = mask_cypher(text)
    assert masked.rstrip() == "MATCH (s:Sample) RETURN s."
    assert len(masked) == len(text)


@pytest.mark.parametrize(
    "text",
    [
        "MATCH (s:Sample) RETURN s.`a\\b` AS x",
        "MATCH (s:Sample) RETURN s.`\\` AS x",
        "MATCH (s:Sample) RETURN s.`x\\\\` AS x",
    ],
)
def test_every_backslash_name_is_one_masked_span(text):
    masked = mask_cypher(text)
    assert masked.endswith(" AS x")
    assert "\\" not in masked
    assert len(masked) == len(text)
