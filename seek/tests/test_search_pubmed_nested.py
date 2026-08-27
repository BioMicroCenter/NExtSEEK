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
