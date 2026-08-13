"""Regression tests for #93: client-supplied search values are bound, never concatenated.

The security property under test is *text invariance*: for a fixed request shape
(how many filter rules, how many UID terms, which boolean operators) the emitted
statement text is a constant, and every attacker-reachable value travels in the
parameter list instead. A value containing ``'``, ``--``, ``;`` or ``UNION``
must change only the params.

Hermetic by construction: ``seek.search`` imports nothing at module scope that
needs a database or the Django app registry, and none of the paths exercised
here reach ``__getCategoryClause`` (the one builder that queries the DB).
"""

import pytest

from seek import search as search_module
from seek.search import Search


def _sample_filter_mapping():
    """Import the real mapping lazily.

    ``seek.dbtable_sample`` drags in the Django app registry; keeping the import
    inside the test body means this module still *collects* without it.
    """
    from seek.dbtable_sample import SAMPLE_FILTER_MAPPING

    return SAMPLE_FILTER_MAPPING


def _render(query, params):
    """Substitute params into ``%s`` placeholders left-to-right, as the driver would.

    Every placeholder is spelled identically, so the only way a test can observe
    whether the params line up with the text is to put them back. This also
    asserts the counts match in both directions.
    """
    out = []
    rest = query
    for param in params:
        head, sep, rest = rest.partition("%s")
        assert sep == "%s", "more params than placeholders: %r %r" % (query, params)
        out.append(head)
        out.append("'" + str(param) + "'")
    assert "%s" not in rest, "more placeholders than params: %r %r" % (query, params)
    out.append(rest)
    return "".join(out)


class TestIdentifierAllowlist:
    def test_rejects_an_identifier_outside_the_allowlist(self):
        with pytest.raises(ValueError):
            search_module._assert_identifier("A.id; DROP TABLE samples")

    def test_accepts_a_known_column(self):
        assert search_module._assert_identifier("A.json_metadata") == "A.json_metadata"

    def test_allowlist_is_a_superset_of_sample_filter_mapping(self):
        mapping = _sample_filter_mapping()
        missing = set(mapping.values()) - set(search_module.ALLOWED_SEARCH_IDENTIFIERS)
        assert missing == set()


class TestDesignSearchPubmedBinding:
    def test_single_keyword_is_bound_with_wildcards_on_the_value(self):
        query, params, keywords = Search("").designSearchPubmed("CD8")
        assert query == "WHERE json_metadata LIKE %s "
        assert params == ["%CD8%"]
        assert keywords == ["CD8"]

    def test_quote_payload_never_reaches_the_statement_text(self):
        payload = "O'Brien'; DROP TABLE samples--"
        query, params, keywords = Search("").designSearchPubmed(payload)
        assert "'" not in query
        assert "DROP TABLE" not in query
        assert query == "WHERE json_metadata LIKE %s "
        assert params == ["%" + payload + "%"]

    def test_none_search_text_returns_a_three_tuple(self):
        # Used to return a bare '' -- every caller unpacking it raised ValueError.
        assert Search("").designSearchPubmed(None) == ("", [], [])

    def test_mismatched_parens_returns_a_three_tuple(self):
        assert Search("").designSearchPubmed("(CD8") == ("", [], [])

    def test_nested_or_binds_every_leaf_in_placeholder_order(self):
        terms = ["D.IMG-230913ENG-%d-PUB" % (1722 + i) for i in range(3)]
        expr = "(%s) OR ((%s) OR (%s))" % tuple(terms)
        query, params, keywords = Search("").designSearchPubmed(expr)
        assert query.count("%s") == len(params)
        assert params == ["%" + t + "%" for t in terms]
        rendered = _render(query, params)
        positions = [rendered.index("'%" + t + "%'") for t in terms]
        assert positions == sorted(positions)
        assert "'" not in query
        assert sorted(keywords) == sorted(terms)

    def test_nested_and_binds_every_leaf_in_placeholder_order(self):
        terms = ["D.IMG-230913ENG-%d-PUB" % (1722 + i) for i in range(3)]
        expr = "(%s) AND ((%s) AND (%s))" % tuple(terms)
        query, params, keywords = Search("").designSearchPubmed(expr)
        assert query.count("%s") == len(params)
        rendered = _render(query, params)
        positions = [rendered.index("'%" + t + "%'") for t in terms]
        assert positions == sorted(positions)

    def test_not_expression_emits_not_like_placeholder_and_keeps_param_order(self):
        query, params, keywords = Search("").designSearchPubmed("(alpha) NOT beta")
        assert "NOT LIKE %s" in query
        assert "'" not in query
        rendered = _render(query, params)
        assert rendered.index("'%alpha%'") < rendered.index("'%beta%'")

    def test_unparseable_expression_falls_back_to_a_bound_whole_expression(self):
        # Two operators -> __validateExpression rejects it -> designSearchPubmed's
        # fallback used to splice the whole raw expression into the statement.
        expr = "a' AND b OR c"
        query, params, keywords = Search("").designSearchPubmed(expr)
        assert query == "WHERE json_metadata LIKE %s "
        assert params == ["%" + expr + "%"]


class TestDesignSearchMatchKeywordsBinding:
    def test_uid_list_is_bound_one_placeholder_per_uid(self):
        spi = Search("")
        fragment, params = spi._Search__designSearchMatchKeywords(
            ["D.SEQ-240910LAU", "a' OR '1'='1"], "A.uuid"
        )
        assert fragment == " WHERE A.uuid in (%s, %s);"
        assert params == ["D.SEQ-240910LAU", "a' OR '1'='1"]
        assert "'" not in fragment

    def test_empty_uid_list_emits_no_clause(self):
        assert Search("")._Search__designSearchMatchKeywords([], "A.uuid") == (" ", [])


class TestDesignSearchContainKeywordsBinding:
    def test_all_terms_are_bound(self):
        fragment, params = Search("")._Search__designSearchContainKeywords(
            ["a'b", "c&d"], "A.json_metadata"
        )
        assert "'" not in fragment
        assert fragment.count("%s") == len(params)
        assert params == ["%a'b%", "%c%", "%d%"]

    def test_or_branch_binds_the_raw_term_not_the_cleaned_keyword(self):
        # Pre-existing quirk, preserved verbatim: the second-position ':'-bearing
        # term binds the RAW term while every sibling site binds the cleaned
        # keyword. #93 is a binding change, not a search-semantics change.
        fragment, params = Search("")._Search__designSearchContainKeywords(
            ["a&b", "ns:1:beta"], "A.json_metadata"
        )
        assert params == ["%a%", "%b%", "%ns:1:beta%"]


class TestDesignSearchFiltersBinding:
    def test_equal_rule_binds_a_hostile_value(self):
        filtersdic = {
            "filterRules": [
                {"field": "sample_type_id", "op": "equal", "value": "1' OR '1'='1"}
            ]
        }
        fragment, params = Search("")._Search__designSearchFilters(
            filtersdic, _sample_filter_mapping()
        )
        assert fragment == " WHERE A.sample_type_id=%s "
        assert params == ["1' OR '1'='1"]

    def test_two_rules_bind_in_left_to_right_order(self):
        filtersdic = {
            "filterRules": [
                {"field": "sample_type_id", "op": "equal", "value": "7"},
                {"field": "title", "op": "contains", "value": "x' --"},
            ]
        }
        fragment, params = Search("")._Search__designSearchFilters(
            filtersdic, _sample_filter_mapping()
        )
        assert fragment == " WHERE A.sample_type_id=%s  AND A.title LIKE %s "
        assert params == ["7", "%x' --%"]
        assert "'" not in fragment

    def test_unmapped_field_contributes_neither_fragment_nor_param(self):
        filtersdic = {
            "filterRules": [{"field": "not_a_column", "op": "equal", "value": "x"}]
        }
        fragment, params = Search("")._Search__designSearchFilters(
            filtersdic, _sample_filter_mapping()
        )
        assert fragment == ""
        assert params == []

    def test_statement_text_is_invariant_to_the_value(self):
        mapping = _sample_filter_mapping()

        def build(value):
            filtersdic = {
                "filterRules": [{"field": "title", "op": "contains", "value": value}]
            }
            # Unpack rather than index: indexing a pre-#93 bare string would
            # silently compare its first character and pass vacuously.
            fragment, params = Search("")._Search__designSearchFilters(
                filtersdic, mapping
            )
            return fragment, params

        benign_fragment, benign_params = build("CD8")
        hostile_fragment, hostile_params = build("x'; DROP TABLE samples; -- ")
        assert benign_fragment == hostile_fragment
        assert benign_params != hostile_params
        assert hostile_params == ["%x'; DROP TABLE samples; -- %"]


class TestDesignSearchAdvancedBinding:
    def test_missing_search_type_returns_a_two_tuple(self):
        assert Search("").designSearchAdvanced({}, {}) == (" ", [])

    def test_missing_search_text_returns_a_two_tuple(self):
        assert Search("").designSearchAdvanced({"searchType": "PUBMED"}, {}) == (" ", [])

    def test_filtering_path_returns_fragment_and_params(self):
        filtersdic = {
            "searchType": "FILTERING",
            "filterRules": [
                {"field": "sample_type_id", "op": "equal", "value": "1' OR '1'='1"}
            ],
        }
        fragment, params = Search("").designSearchAdvanced(
            filtersdic, _sample_filter_mapping()
        )
        assert fragment == " WHERE A.sample_type_id=%s "
        assert params == ["1' OR '1'='1"]

    def test_pubmed_path_returns_fragment_and_params(self):
        filtersdic = {
            "searchType": "PUBMED",
            "searchText": "CD8",
            "tableField": "json_metadata",
            "categoryField": "sample_type_id",
        }
        fragment, params = Search("").designSearchAdvanced(filtersdic, {})
        assert fragment == "WHERE json_metadata LIKE %s "
        assert params == ["%CD8%"]

    def test_uids_path_returns_fragment_and_params(self):
        filtersdic = {
            "searchType": "UIDS",
            "searchText": "a'b",
            "tableField": "A.uuid",
            "categoryField": "sample_type_id",
        }
        fragment, params = Search("").designSearchAdvanced(filtersdic, {})
        assert fragment == " WHERE A.uuid in (%s);"
        assert params == ["a'b"]
