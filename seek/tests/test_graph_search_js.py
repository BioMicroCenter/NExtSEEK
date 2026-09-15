"""The graph search page's core JavaScript, run under node.

seek/tests/js/graph_search_cases.js lifts the one <script> block out of
seek/templates/pages/graphSearch_core.embed.html verbatim and runs it on fixed inputs;
this module asserts on what it returns. Everything the page decides without a browser
is covered: the request body each tab sends to POST /nextseek_api/samples/graph_search/,
which Simple tab operators graph_search can express, the paging URL, the UID link, and
the timing and error lines.

node is not in the stack image, so this module skips there. Run it on a host with node:

    node seek/tests/js/graph_search_cases.js
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "seek" / "tests" / "js" / "graph_search_cases.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(scope="module")
def r():
    proc = subprocess.run([NODE, str(CASES)], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_advanced_sends_the_terms_as_a_list_with_one_logic_and_the_type(r):
    assert r["advanced_two_terms_and_a_type"] == {"body": {
        "filter_searchText": ["lung", "granuloma"], "searchText_logic": "AND",
        "filter_matchType": "PARTIAL", "sampletype": "TIS"}}


def test_advanced_sends_one_trimmed_term_as_a_string_and_no_type_when_none_is_chosen(r):
    assert r["advanced_one_term_no_type"] == {"body": {
        "filter_searchText": "granuloma", "searchText_logic": "OR", "filter_matchType": "EXACT"}}


def test_advanced_refuses_an_empty_query(r):
    assert r["advanced_no_terms"] == {"error": "Enter at least one search term, one per line."}


def test_simple_between_becomes_two_typed_conditions(r):
    assert r["simple_numeric_between"] == {"body": {"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "RNA", "attribute": "RIN", "op": ">=", "value": 7},
        {"sample_type": "RNA", "attribute": "RIN", "op": "<=", "value": 9}]}}}


def test_simple_date_rules_pass_the_date_through_for_graph_search_to_cast(r):
    assert r["simple_date_before"] == {"body": {"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "SampleCreationDate", "op": "<=", "value": "12/31/2019"}]}}}


def test_simple_contain_is_graph_search_contains(r):
    assert r["simple_string_contain"] == {"body": {"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "CONTAINS", "value": "Lung"}]}}}


def test_simple_not_equal_is_the_cypher_not_equal(r):
    assert r["simple_numeric_not_equal"]["body"]["extensions"]["where"][0]["op"] == "<>"
    assert r["simple_numeric_not_equal"]["body"]["extensions"]["where"][0]["value"] == 8.5


def test_simple_with_no_filter_or_no_attribute_asks_for_the_whole_sample_type(r):
    type_only = {"body": {"sampletype": "TIS", "filter_searchText": ""}}
    assert r["simple_no_filter"] == type_only
    assert r["simple_no_attribute"] == type_only


def test_simple_refuses_what_graph_search_cannot_express_or_is_incomplete(r):
    assert r["simple_not_contain"] == {"error": "“Not Contain” has no graph_search operator."}
    assert r["simple_no_type"] == {"error": "Choose a sample type."}
    assert r["simple_numeric_not_a_number"] == {"error": "RIN needs a number."}
    assert r["simple_between_without_to"] == {"error": "Between needs both a From and a To value."}
    assert r["simple_contain_without_value"] == {"error": "Enter a value in From."}


def test_only_rules_graph_search_can_express_are_offered(r):
    assert r["rules_offered"] == {
        "string": ["Contain", "No Filter"],
        "bool": ["No Filter"],
        "numeric": ["No Filter", "Equal", "Not Equal", "Less", "Greater", "Between"],
        "date": ["No Filter", "Equal", "Not Equal", "Before", "After", "Between"],
    }


def test_each_page_is_its_own_request_with_the_timings_asked_for(r):
    assert r["url_page_3"] == "/nextseek_api/samples/graph_search/?page=3&page_size=100&debug_meta=1"


def test_the_uid_links_to_the_sample_page_and_is_escaped(r):
    assert r["uid_link"] == '<a href="/seek/sample/id=5/">TIS-1&lt;b&gt;</a>'


def test_the_timing_line_reports_the_browser_and_server_times(r):
    assert r["timing_with_debug"] == ("107,412 samples in 250 ms "
                                      "(graph_search on the server: 80 ms; query 60, count 10, rows 3)")
    assert r["timing_without_debug"] == "1 sample in 120 ms"


def test_errors_carry_the_status_and_the_api_detail(r):
    assert r["error_from_envelope"] == "graph_search answered 422: attribute X is not on TIS"
    assert r["error_without_body"] == "graph_search answered 502"
