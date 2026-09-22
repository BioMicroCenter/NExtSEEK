"""The Sample Search page's core JavaScript, run under node.

Both search boxes on /seek/search/ send their searches to
POST /nextseek_api/samples/graph_search/. Everything the page decides without a
browser lives in one <script> block, seek/templates/pages/sampleSearch_core.embed.html;
seek/tests/js/sample_search_cases.js lifts it out verbatim and runs it on fixed
inputs, and this module asserts on what it returns: the body each box sends (the
Advanced box's query text included), which Simple rules graph_search can express,
the paging URL, and the UID link and Attribute:Value cells the grids show.

node is not in the stack image, so this module skips there. Run it on a host with node:

    node seek/tests/js/sample_search_cases.js
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "seek" / "tests" / "js" / "sample_search_cases.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

KEY = '<span style="color:blue;font-weight:bold;">{}</span>'
HIT = '<span style="color:red;">{}</span>'
NO_HIGHLIGHT = {"terms": [], "matchType": None, "attribute": None}


@pytest.fixture(scope="module")
def r():
    proc = subprocess.run([NODE, str(CASES)], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


# ---- the Advanced box: its query text as graph_search's extensions.query ----

def _query(text, match="PARTIAL", **extra):
    return {"filter_searchText": "", "filter_matchType": match, "extensions": {"query": text}, **extra}


def _terms(terms, match="PARTIAL"):
    return {"terms": terms, "matchType": match, "attribute": None}


def test_the_query_text_is_sent_trimmed_as_extensions_query(r):
    assert r["query_one_term"] == {"body": _query("granuloma"), "highlight": _terms(["granuloma"])}
    assert r["query_and_terms"] == {"body": _query("lung AND granuloma"), "highlight": _terms(["lung", "granuloma"])}
    assert r["query_or_terms_exact"] == {"body": _query("lung OR granuloma", "EXACT"),
                                         "highlight": _terms(["lung", "granuloma"], "EXACT")}


def test_the_add_buttons_parentheses_go_as_written_and_are_not_highlighted(r):
    assert r["query_as_search_add_builds_it"] == {
        "body": _query("((lung AND left lobe) AND granuloma)"),
        "highlight": _terms(["lung", "left lobe", "granuloma"])}


def test_not_mixed_logic_and_tags_are_sent_not_refused(r):
    """graph_search reads NOT, AND and OR together through parentheses, and term[TYPE] tags, with
    advanced_search's rows; the page no longer refuses them. Every term is highlighted, negated ones
    too, as the old engine highlighted every keyword of the text."""
    assert r["query_not"] == {"body": _query("lung NOT granuloma"), "highlight": _terms(["lung", "granuloma"])}
    assert r["query_not_a_phrase"]["body"] == _query("lung NOT (left lobe)")
    assert r["query_not_a_phrase"]["highlight"]["terms"] == ["lung", "left lobe"]
    assert r["query_mixed_logic"] == {"body": _query("(lung AND granuloma) OR liver"),
                                      "highlight": _terms(["lung", "granuloma", "liver"])}
    assert r["query_leading_not"] == {"body": _query("NOT(granuloma)"), "highlight": _terms(["granuloma"])}
    assert r["query_tags_of_two_types"] == {"body": _query("lung[TIS] AND reads[D.SEQ]"),
                                            "highlight": _terms(["lung", "reads"])}
    assert r["query_or_partly_tagged"] == {"body": _query("lung[TIS] OR granuloma"),
                                           "highlight": _terms(["lung", "granuloma"])}


def test_a_tag_alone_highlights_nothing(r):
    assert r["query_only_a_tag"] == {"body": _query("[TIS]"), "highlight": NO_HIGHLIGHT}


def test_brackets_that_are_not_one_pair_stay_in_the_highlighted_term(r):
    assert r["query_brackets_that_are_not_a_tag"]["highlight"]["terms"] == ["lung[a][b]", "x]"]


def test_any_whitespace_around_an_operator_splits_the_highlight(r):
    assert r["query_operators_on_new_lines"]["highlight"]["terms"] == ["lung", "liver"]


def test_a_lower_case_and_is_part_of_the_term_as_it_was_for_the_old_parser(r):
    assert r["query_lower_case_and_is_part_of_the_term"]["highlight"]["terms"] == ["salt and pepper"]


def test_the_mobile_forms_sample_type_joins_the_search(r):
    assert r["query_with_the_chosen_type"]["body"] == _query("lung", sampletype="TIS")
    # The chosen type and a tag are both conditions; graph_search ANDs them.
    assert r["query_chosen_type_and_another_tag"]["body"] == _query("lung[TIS]", sampletype="D.SEQ")


def test_only_an_empty_box_is_refused_on_the_page(r):
    """Text graph_search cannot read goes to graph_search, whose 422 says why (errorText shows it)."""
    assert r["query_empty"] == {"error": "No search term entered."}
    assert r["query_text_graph_search_cannot_read"]["body"] == _query("a OR b AND c")


# ---- the Simple box: one sample type, one attribute, one rule ----

def test_simple_between_becomes_two_typed_conditions(r):
    assert r["simple_numeric_between"] == {
        "body": {"filter_searchText": "", "extensions": {"where": [
            {"sample_type": "RNA", "attribute": "RIN", "op": ">=", "value": 7},
            {"sample_type": "RNA", "attribute": "RIN", "op": "<=", "value": 9}]}},
        "highlight": {"terms": [], "matchType": None, "attribute": "RIN"}}


def test_simple_date_rules_pass_the_date_through_for_graph_search_to_cast(r):
    assert r["simple_date_before"]["body"] == {"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "SampleCreationDate", "op": "<=", "value": "12/31/2019"}]}}


def test_simple_contain_is_graph_search_contains_and_is_case_sensitive_like_the_old_rule(r):
    assert r["simple_string_contain"]["body"] == {"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "CONTAINS", "value": "Lung"}]}}


def test_simple_contain_with_no_value_keeps_every_sample_with_the_attribute_as_before(r):
    assert r["simple_contain_without_value"]["body"] == {"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "CONTAINS", "value": ""}]}}


def test_simple_not_equal_is_the_cypher_not_equal(r):
    where = r["simple_numeric_not_equal"]["body"]["extensions"]["where"][0]
    assert (where["op"], where["value"]) == ("<>", 8.5)


def test_simple_with_no_filter_or_no_attribute_asks_for_the_whole_sample_type(r):
    """No Filter on a chosen attribute ignores From, as the old engine did."""
    type_only = {"body": {"sampletype": "TIS", "filter_searchText": ""}, "highlight": NO_HIGHLIGHT}
    assert r["simple_no_filter_ignores_the_value"] == type_only
    assert r["simple_attribute_none"] == type_only
    assert r["simple_no_attribute"] == type_only


def test_simple_attribute_none_with_a_value_searches_every_value_of_the_type(r):
    assert r["simple_attribute_none_with_value"] == {
        "body": {"sampletype": "TIS", "filter_searchText": "Lung", "filter_matchType": "PARTIAL"},
        "highlight": {"terms": ["Lung"], "matchType": "PARTIAL", "attribute": None}}


def test_simple_not_contain_is_graph_search_not_contains_on_the_trimmed_value(r):
    assert r["simple_not_contain"] == {
        "body": {"filter_searchText": "", "extensions": {"where": [
            {"sample_type": "TIS", "attribute": "Organ", "op": "NOT CONTAINS", "value": "Lung"}]}},
        "highlight": {"terms": [], "matchType": None, "attribute": "Organ"}}


def test_simple_not_contain_with_no_value_is_sent_as_the_old_engine_ran_it(r):
    """advanced_search: '' is in every value, so Not Contain '' matched nothing; graph_search agrees."""
    assert r["simple_not_contain_without_value"]["body"]["extensions"]["where"] == [
        {"sample_type": "TIS", "attribute": "Organ", "op": "NOT CONTAINS", "value": ""}]


def test_simple_true_and_false_are_graph_search_is_true_and_is_false_with_no_value(r):
    """The old engine ignored From for True and False (toBinaryTinyInt of the value alone)."""
    for case, op in (("simple_true", "IS TRUE"), ("simple_false", "IS FALSE")):
        assert r[case] == {
            "body": {"filter_searchText": "", "extensions": {"where": [
                {"sample_type": "TIS", "attribute": "Viable", "op": op}]}},
            "highlight": {"terms": [], "matchType": None, "attribute": "Viable"}}, case


def test_simple_refuses_what_graph_search_cannot_express_or_is_incomplete(r):
    assert r["simple_unknown_rule"] == {"error": "“Sounds Like” has no graph_search operator."}
    assert r["simple_no_type"] == {"error": "No sample type is selected."}
    assert r["simple_numeric_not_a_number"] == {"error": "Warning: Not a valid numeric value: high"}
    assert r["simple_between_without_to"] == {"error": "Between needs both a From and a To value."}


def test_every_rule_the_old_engine_offered_is_offered_again(r):
    """Not Contain, True and False are back: graph_search has an operator for each."""
    assert r["rules_offered"] == {
        "string": ["Contain", "Not Contain", "No Filter"],
        "bool": ["No Filter", "True", "False"],
        "unknown": ["No Filter", "Contain"],
        "numeric": ["No Filter", "Equal", "Not Equal", "Less", "Greater", "Between"],
        "date": ["No Filter", "Equal", "Not Equal", "Before", "After", "Between"],
    }


def test_the_simple_box_sends_the_title_of_the_chosen_type_id(r):
    """The combobox value is an id; graph_search's where items name a type by title."""
    assert r["type_title_of_a_chosen_id"] == ["D.SEQ", "TIS", "", "", "", ""]


# ---- Associated with: graph_search's extensions.lineage ----

LINEAGE = {"direction": "either", "sample_type": "D.SEQ", "max_hops": 12}


def test_the_associated_with_dropdown_shows_each_types_name_and_code(r):
    assert r["associated_options"] == [
        {"title": "TIS", "label": "Tissue (TIS)", "group": "Experimental type"},
        {"title": "D.SEQ", "label": "D.SEQ", "group": "Data type"},
        {"title": "RNA", "label": "RNA", "group": "Experimental type"},
    ]
    assert r["associated_options_without_types"] == []


def test_the_lineage_condition_covers_the_whole_tree_in_the_chosen_direction(r):
    """12 hops is the whole tree: the longest DERIVED_FROM chain is 11. Either is the default."""
    assert r["lineage_none"] is None
    assert r["lineage_either_by_default"] == LINEAGE
    assert r["lineage_ancestors"] == {"direction": "ancestor", "sample_type": "MUS", "max_hops": 12}
    assert r["lineage_descendants"] == {"direction": "descendant", "sample_type": "D.SEQ", "max_hops": 12}
    assert r["lineage_unknown_direction_is_either"]["direction"] == "either"


def test_associated_with_joins_the_simple_box_search_without_changing_it(r):
    assert r["associated_with_the_simple_box"]["joined"] == {
        "body": {"filter_searchText": "", "extensions": {
            "where": [{"sample_type": "TIS", "attribute": "Organ", "op": "NOT CONTAINS", "value": "Lung"}],
            "lineage": LINEAGE}},
        "highlight": {"terms": [], "matchType": None, "attribute": "Organ"}}
    assert r["associated_with_the_simple_box"]["untouched"] is True
    assert r["associated_with_a_whole_type"]["body"] == {
        "sampletype": "TIS", "filter_searchText": "",
        "extensions": {"lineage": {"direction": "descendant", "sample_type": "D.SEQ", "max_hops": 12}}}


def test_associated_with_joins_the_advanced_box_query(r):
    assert r["associated_with_the_query_text"]["body"] == {
        "filter_searchText": "", "filter_matchType": "PARTIAL",
        "extensions": {"query": "lung NOT granuloma",
                       "lineage": {"direction": "ancestor", "sample_type": "MUS", "max_hops": 12}}}


def test_no_associated_type_leaves_the_search_alone_and_a_refusal_stays_one(r):
    assert r["associated_with_nothing_chosen"]["body"] == {
        "filter_searchText": "", "filter_matchType": "PARTIAL", "extensions": {"query": "lung"}}
    assert r["associated_with_a_refusal"] == {"error": "No search term entered."}


# ---- paging, rows and cells ----

def test_each_page_is_its_own_request(r):
    assert r["url_page_3"] == "/nextseek_api/samples/graph_search/?page=3&page_size=100"


def test_the_uid_links_to_the_sample_page_in_a_new_tab_and_is_escaped(r):
    """The anchor the old engine built (seek/sample/core.py _getSamplelink)."""
    assert r["uid_link"] == '<a href="/seek/sample/id=5/" target="_blank">TIS-1&lt;b&gt;</a>'


def test_attribute_value_cells_highlight_the_matching_values_as_the_old_engine_did(r):
    """seek/sample/queries.py _highlightKeyValues: sorted keys, every value holding a term."""
    assert r["cells_for_terms"] == (
        KEY.format("Notes") + ":" + HIT.format("lung") + " &amp; liver,   "
        + KEY.format("Organ") + ":Left " + HIT.format("Lung"))
    assert r["cells_for_exact_terms"] == KEY.format("Organ") + ":" + HIT.format("Lung")
    assert r["cells_for_an_attribute"] == KEY.format("RIN") + ":" + HIT.format("8.1")
    assert r["cells_for_a_type_only_search"] == ""


def test_attribute_value_cells_escape_the_metadata(r):
    assert r["cells_escape_the_metadata"] == (
        KEY.format("&lt;k&gt;") + ":&lt;" + HIT.format("img") + " src=x&gt;")


def test_rows_get_the_uid_link_and_cells_and_their_text_escaped(r):
    assert r["rows_prepared"] == [{
        "id": 7, "uuid": "TIS-2", "uid": '<a href="/seek/sample/id=7/" target="_blank">TIS-2</a>',
        "sample_type": "TIS", "assays": "A&lt;1&gt;", "first_name": "D&amp;D", "title": "T",
        "json_metadata": {"Organ": "Lung"},
        "attributeValue": KEY.format("Organ") + ":" + HIT.format("Lung")}]
    assert r["rows_prepared_without_rows"] == []


# ---- what the page says when graph_search refuses ----

def test_errors_carry_the_status_and_the_api_detail(r):
    assert r["error_from_envelope"] == "graph_search answered 422: attribute X is not on TIS"
    assert r["error_from_scope"] == "graph_search answered 403: Cannot determine project scope for this caller"
    assert r["error_without_body"] == "graph_search answered 502"
