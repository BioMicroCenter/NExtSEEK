"""Regression tests for right-nested boolean expressions with designSearchPubmed."""

import pytest

from nextseek_api.services.samples import _nest_boolean_search_terms
from seek.search import Search


class TestNestBooleanSearchTerms:
    def test_two_terms_or(self):
        assert _nest_boolean_search_terms(["a", "b"], " OR ") == "(a) OR (b)"

    def test_three_terms_or(self):
        assert _nest_boolean_search_terms(["a", "b", "c"], " OR ") == "(a) OR ((b) OR (c))"

    def test_three_terms_and(self):
        assert _nest_boolean_search_terms(["a", "b", "c"], " AND ") == "(a) AND ((b) AND (c))"


class TestDesignSearchPubmedNested:
    @pytest.mark.parametrize("n", list(range(3, 11)))
    def test_nested_or_expression_extracts_all_keywords(self, n):
        terms = [f"D.IMG-230913ENG-{1722 + i}-PUB" for i in range(n)]
        expr = _nest_boolean_search_terms(terms, " OR ")
        spi = Search("")
        # (query, params, keywords) since #93: the values are bound, not spliced.
        query, params, keywords = spi.designSearchPubmed(expr)
        assert query.startswith("WHERE ")
        assert "term_" not in query
        assert sorted(keywords) == sorted(terms)

    @pytest.mark.parametrize("n", list(range(3, 11)))
    def test_nested_and_expression_extracts_all_keywords(self, n):
        # Use realistic UID-shaped terms; short tokens like "term-0" break the
        # legacy PubMed parser's parenthesis-replacement pass for AND expressions.
        terms = [f"D.IMG-230913ENG-{1722 + i}-PUB" for i in range(n)]
        expr = _nest_boolean_search_terms(terms, " AND ")
        spi = Search("")
        # (query, params, keywords) since #93: the values are bound, not spliced.
        query, params, keywords = spi.designSearchPubmed(expr)
        assert query.startswith("WHERE ")
        assert sorted(keywords) == sorted(terms)


class TestDesignSearchPubmedShortTerms:
    """#79: short terms used to break the parenthesis-replacement pass (stale
    indices after a placeholder longer than the term). Real parser, no mock."""

    @pytest.mark.parametrize("op", [" AND ", " OR "])
    @pytest.mark.parametrize("terms", [["a", "b", "c"], ["alpha", "beta", "gamma"]])
    def test_short_terms_three(self, op, terms):
        expr = _nest_boolean_search_terms(terms, op)
        query, params, keywords = Search("").designSearchPubmed(expr)
        assert query.startswith("WHERE ")
        assert "term_" not in query
        assert sorted(keywords) == sorted(terms)
        assert sorted(p.strip("%") for p in params) == sorted(terms)


class TestDesignSearchPubmedPlaceholderCollisions:
    """#79 review: the parser's group placeholders must never equal text a user
    types. Real parser, no mock."""

    def test_placeholder_named_term_inside_a_group_is_a_plain_term(self):
        # term_N placeholders made this recurse without end (RecursionError).
        query, params, keywords = Search("").designSearchPubmed("(term_0 AND x) OR y")
        assert query.count("LIKE %s") == 3
        assert params == ["%term_0%", "%x%", "%y%"]
        assert sorted(keywords) == ["term_0", "x", "y"]

    def test_placeholder_named_term_outside_a_group_is_a_plain_term(self):
        # term_N placeholders silently turned this into "(a OR b) AND (a OR b)".
        query, params, keywords = Search("").designSearchPubmed("(a OR b) AND term_0")
        assert query.count("LIKE %s") == 3
        assert params == ["%a%", "%b%", "%term_0%"]
        assert sorted(keywords) == ["a", "b", "term_0"]

    def test_nul_in_user_text_cannot_forge_a_placeholder(self):
        query, params, keywords = Search("").designSearchPubmed("(a OR b) AND \x000\x00")
        assert params == ["%a%", "%b%", "%0%"]
        assert sorted(keywords) == ["0", "a", "b"]

    def test_flat_form_falls_back_to_the_literal_text_not_placeholders(self):
        text = "(alpha) OR (beta) OR (gamma)"
        query, params, keywords = Search("").designSearchPubmed(text)
        assert params == ["%" + text + "%"]
        assert keywords == [text]

    @pytest.mark.parametrize("text", ["(a AND b)", "((a AND b))", "((a)) AND b"])
    def test_a_group_that_is_a_whole_operand_never_leaks_its_placeholder(self, text):
        query, params, keywords = Search("").designSearchPubmed(text)
        assert params == ["%a%", "%b%"]
        assert sorted(keywords) == ["a", "b"]
