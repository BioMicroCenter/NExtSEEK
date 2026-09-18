"""The Sample Search page's query text (the Advanced box) parsed into a tree, for graph_search's extensions.query.

advanced_search read this text with seek/search.py::Search.designSearchPubmed. The parser keeps its vocabulary
(upper-case AND, OR and NOT, parentheses, term[TYPE] tags) and reads every shape the Add button writes the same way;
it refuses, with a reason, the shapes advanced_search turned into a literal phrase or garbage
(nextseek_api/graph_search/README.md, "What advanced_search returned").
"""
import re

import pytest

from nextseek_api.graph_search.text_query import (
    MAX_TERMS,
    All,
    Any,
    Not,
    QueryTextInvalid,
    Term,
    parse,
    terms,
)


def t(text, tag=None, index=0):
    return Term(text=text, tag=tag, index=index)


# --- terms and tags ---------------------------------------------------------------------------------------------------


def test_one_term_is_trimmed():
    assert parse("  granuloma ") == t("granuloma")


def test_inner_spaces_and_lower_case_operator_words_are_part_of_the_term():
    assert parse("salt and pepper") == t("salt and pepper")
    assert parse("left  lobe") == t("left  lobe")


def test_a_tag_is_trimmed_and_upper_cased_and_the_term_before_it_trimmed():
    assert parse("left lobe [ tis ]") == t("left lobe", "TIS")


def test_text_after_the_tag_is_dropped_as_advanced_search_dropped_it():
    assert parse("lung[TIS]x") == t("lung", "TIS")


def test_a_tag_alone_is_a_term_with_no_text():
    assert parse("[TIS]") == t("", "TIS")


def test_an_empty_tag_is_still_a_tag():
    # advanced_search looked up the empty title and found nothing (id -1): the term matched nothing.
    assert parse("lung[]") == t("lung", "")


@pytest.mark.parametrize("raw", ["lung[a][b]", "lung]x[", "lung[x", "lung]"])
def test_brackets_that_are_not_one_pair_are_text(raw):
    assert parse(raw) == t(raw)


# --- operators ----------------------------------------------------------------------------------------------------------


def test_and_or_and_not_between_terms():
    assert parse("lung AND granuloma") == All((t("lung", index=0), t("granuloma", index=1)))
    assert parse("lung OR granuloma") == Any((t("lung", index=0), t("granuloma", index=1)))


def test_binary_not_is_and_not():
    assert parse("lung NOT granuloma") == All((t("lung", index=0), Not(t("granuloma", index=1))))


def test_any_whitespace_delimits_an_operator():
    assert parse("lung\nAND\tgranuloma") == All((t("lung", index=0), t("granuloma", index=1)))


def test_the_add_buttons_shapes_read_as_advanced_search_read_them():
    # searchAdd(): `curText OP keyword`, the text so far and a phrase with a space each wrapped in parentheses.
    assert parse("(lung AND granuloma) OR liver") == Any((
        All((t("lung", index=0), t("granuloma", index=1))), t("liver", index=2)))
    assert parse("((lung AND granuloma) OR liver) NOT kidney") == All((
        Any((All((t("lung", index=0), t("granuloma", index=1))), t("liver", index=2))), Not(t("kidney", index=3))))
    assert parse("lung[TIS] NOT granuloma[TIS]") == All((t("lung", "TIS", 0), Not(t("granuloma", "TIS", 1))))


def test_not_before_parentheses_negates_the_group():
    # advanced_search dropped this NOT (it ran `lung AND (left lobe)`): a parser defect graph_search does not keep.
    assert parse("lung NOT (left lobe)") == All((t("lung", index=0), Not(t("left lobe", index=1))))
    assert parse("lung NOT (a OR b)") == All((t("lung", index=0), Not(Any((t("a", index=1), t("b", index=2))))))


def test_groups_that_advanced_search_corrupted_parse():
    assert parse("(lung AND granuloma)") == All((t("lung", index=0), t("granuloma", index=1)))
    assert parse("((lung AND granuloma) OR liver) AND (left lobe)") == All((
        Any((All((t("lung", index=0), t("granuloma", index=1))), t("liver", index=2))), t("left lobe", index=3)))
    assert parse("(a) AND (b)") == All((t("a", index=0), t("b", index=1)))


def test_one_operator_repeated_on_a_level_is_one_n_ary_node():
    assert parse("a AND b AND c") == All((t("a", index=0), t("b", index=1), t("c", index=2)))
    assert parse("a OR b OR c") == Any((t("a", index=0), t("b", index=1), t("c", index=2)))


def test_and_and_not_mix_on_a_level_because_not_is_and_not():
    assert parse("a AND b NOT c") == All((t("a", index=0), t("b", index=1), Not(t("c", index=2))))


def test_a_leading_not_negates_the_next_operand():
    assert parse("NOT granuloma") == Not(t("granuloma"))
    assert parse("lung AND NOT granuloma") == All((t("lung", index=0), Not(t("granuloma", index=1))))
    assert parse("NOT a OR b") == Any((Not(t("a", index=0)), t("b", index=1)))
    assert parse("NOT(a OR b)") == Not(Any((t("a", index=0), t("b", index=1))))


def test_terms_are_the_leaves_in_order():
    tree = parse("(lung[TIS] AND granuloma) NOT kidney")
    assert terms(tree) == [t("lung", "TIS", 0), t("granuloma", index=1), t("kidney", index=2)]


# --- refusals ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, reason", [
    ("", "no search term"),
    ("   ", "no search term"),
    ("a OR b AND c", "parentheses"),
    ("a OR b NOT c", "parentheses"),
    ("a AND b OR c", "parentheses"),
    ("lung NOT", "needs a term after it"),
    ("lung AND", "needs a term after it"),
    ("AND lung", "needs a term before it"),
    ("lung AND OR liver", "needs a term"),
    ("(a AND b", "no matching ')'"),
    ("a AND b)", "no matching '('"),
    ("a) AND (b", "no matching '('"),
    ("()", "empty parentheses"),
    ("lung (left lobe)", "AND, OR or NOT"),
    ("(lung) liver", "AND, OR or NOT"),
    ("(lung)(liver)", "AND, OR or NOT"),
])
def test_what_graph_search_cannot_read_is_refused_with_the_reason(text, reason):
    with pytest.raises(QueryTextInvalid, match=re.escape(reason)):
        parse(text)


def test_the_number_of_terms_is_bounded():
    parse(" OR ".join(f"t{i}" for i in range(MAX_TERMS)))
    with pytest.raises(QueryTextInvalid, match="at most"):
        parse(" OR ".join(f"t{i}" for i in range(MAX_TERMS + 1)))


def test_nesting_is_bounded():
    with pytest.raises(QueryTextInvalid, match="parentheses"):
        parse("(" * 40 + "a" + ")" * 40)
