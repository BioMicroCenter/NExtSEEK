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


# ---- the Advanced box: the query text the Add button builds, as one graph_search body ----

def test_one_term_is_sent_as_a_trimmed_string(r):
    assert r["query_one_term"] == {
        "body": {"filter_searchText": "granuloma", "filter_matchType": "PARTIAL"},
        "highlight": {"terms": ["granuloma"], "matchType": "PARTIAL", "attribute": None}}


def test_terms_joined_by_one_logic_are_a_list_with_that_logic(r):
    assert r["query_and_terms"]["body"] == {
        "filter_searchText": ["lung", "granuloma"], "searchText_logic": "AND", "filter_matchType": "PARTIAL"}
    assert r["query_or_terms_exact"] == {
        "body": {"filter_searchText": ["lung", "granuloma"], "searchText_logic": "OR", "filter_matchType": "EXACT"},
        "highlight": {"terms": ["lung", "granuloma"], "matchType": "EXACT", "attribute": None}}


def test_the_parentheses_the_add_button_writes_are_grouping_not_text(r):
    """searchAdd() wraps a phrase with a space, and the text so far, in parentheses."""
    assert r["query_as_search_add_builds_it"]["body"] == {
        "filter_searchText": ["lung", "left lobe", "granuloma"], "searchText_logic": "AND",
        "filter_matchType": "PARTIAL"}


def test_a_lower_case_and_is_part_of_the_term_as_it_was_for_the_old_parser(r):
    assert r["query_lower_case_and_is_part_of_the_term"]["body"] == {
        "filter_searchText": "salt and pepper", "filter_matchType": "PARTIAL"}


def test_a_sample_type_tag_becomes_the_searchs_sample_type(r):
    """term[TYPE] limited that term to the type; with AND that limits the whole search."""
    assert r["query_one_tag_applies_to_the_search"]["body"] == {
        "filter_searchText": ["lung", "granuloma"], "searchText_logic": "AND",
        "filter_matchType": "PARTIAL", "sampletype": "TIS"}
    assert r["query_tag_is_upper_cased"]["body"] == {
        "filter_searchText": "lung", "filter_matchType": "PARTIAL", "sampletype": "TIS"}
    assert r["query_or_every_term_tagged_alike"]["body"] == {
        "filter_searchText": ["lung", "granuloma"], "searchText_logic": "OR",
        "filter_matchType": "PARTIAL", "sampletype": "TIS"}


def test_a_tag_alone_asks_for_every_sample_of_the_type(r):
    assert r["query_only_a_tag"] == {"body": {"filter_searchText": "", "sampletype": "TIS"},
                                     "highlight": NO_HIGHLIGHT}


def test_the_mobile_forms_sample_type_joins_the_search(r):
    assert r["query_with_the_chosen_type"]["body"] == {
        "filter_searchText": "lung", "filter_matchType": "PARTIAL", "sampletype": "TIS"}
    assert r["query_chosen_type_agrees_with_the_tag"]["body"] == r["query_with_the_chosen_type"]["body"]


def test_queries_graph_search_cannot_express_are_refused_with_the_reason(r):
    assert r["query_empty"] == {"error": "No search term entered."}
    assert r["query_not"] == {
        "error": "NOT is not supported: graph_search combines search terms with AND or OR only."}
    assert r["query_mixed_logic"] == {
        "error": "Use AND or OR, not both: graph_search combines every search term the same way."}
    assert r["query_and_two_types"] == {
        "error": "Terms tagged [TIS] and [D.SEQ] cannot match the same sample: a sample has one sample type."}
    assert r["query_or_partly_tagged"] == {
        "error": "With OR, tag every term with the same sample type or tag none: "
                 "graph_search applies one sample type to the whole search."}
    assert r["query_chosen_type_disagrees_with_the_tag"] == {
        "error": "The sample type chosen (D.SEQ) and the one in the search text ([TIS]) differ."}


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


def test_simple_refuses_what_graph_search_cannot_express_or_is_incomplete(r):
    assert r["simple_not_contain"] == {"error": "“Not Contain” has no graph_search operator."}
    assert r["simple_no_type"] == {"error": "No sample type is selected."}
    assert r["simple_numeric_not_a_number"] == {"error": "Warning: Not a valid numeric value: high"}
    assert r["simple_between_without_to"] == {"error": "Between needs both a From and a To value."}


def test_only_rules_graph_search_can_express_are_offered(r):
    assert r["rules_offered"] == {
        "string": ["Contain", "No Filter"],
        "bool": ["No Filter"],
        "numeric": ["No Filter", "Equal", "Not Equal", "Less", "Greater", "Between"],
        "date": ["No Filter", "Equal", "Not Equal", "Before", "After", "Between"],
    }


def test_the_simple_box_sends_the_title_of_the_chosen_type_id(r):
    """The combobox value is an id; graph_search's where items name a type by title."""
    assert r["type_title_of_a_chosen_id"] == ["D.SEQ", "TIS", "", "", "", ""]


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
