"""Hermetic tests for graph_search's query builder and its fulltext candidate queries.

The builder is pure: a request's filters, a Scope and a Catalog in, Cypher text and parameters out. These tests
assert fragments of the Cypher and the exact parameters. The rules come from the design's matching table (section 7)
and advanced_search's own engine: seek/sample/search.py::SampleSearchMixin._filterSamples_advanced plus the attribute
stage of nextseek_api/services/samples.py::SampleAdvancedSearchViewSet.create.
"""
import logging
from datetime import date

import pytest

from nextseek_api.graph_search import lucene
from nextseek_api.graph_search.query import (
    BuiltQuery,
    Catalog,
    PY_WHITESPACE,
    GraphSearchInvalid,
    build,
    quote_name,
    split_terms,
)
from nextseek_api.graph_search.scope import Scope
from nextseek_api.models import GraphSearchExtensions, GraphSearchRequest

TYPE_IDS = {"TIS": 26, "MUS": 15, "D.SEQ": 31, "Odd`Type": 40}

CATALOG = Catalog(
    type_title_by_id={v: k for k, v in TYPE_IDS.items()},
    label_by_title={"TIS": "T_TIS", "MUS": "T_MUS", "D.SEQ": "T_D_SEQ", "Odd`Type": "T_Odd_Type"},
    titles_by_type={
        "TIS": frozenset({"UID", "Organ", "organ", "Media supplement ", "CellCount", "Collected", "Name`x", "Viable"}),
        "MUS": frozenset({"UID", "Sex", "Strain", "Organ"}),
        "D.SEQ": frozenset({"UID", "Parent", "Reads"}),
        "Odd`Type": frozenset(),
    },
    value_type={
        ("TIS", "CellCount"): "float",
        ("TIS", "Collected"): "date",
        ("D.SEQ", "Reads"): "integer",
    },
)

ADMIN = Scope(is_admin=True, person_id=None, project_ids=())
MEMBER = Scope(is_admin=False, person_id=144, project_ids=(2, 6))

FULLTEXT = "CALL db.index.fulltext.queryNodes('sample_search_text', $lucene) YIELD node AS s"


def _resolver(title):
    value = TYPE_IDS.get(title)
    return None if value is None else str(value)


def _req(body):
    return GraphSearchRequest.model_validate(body)


def _build(body, scope=ADMIN, page=1, page_size=100, catalog=CATALOG):
    req = _req(body)
    return build(req.to_db_filters(sampletype_resolver=_resolver), req.extensions, scope, catalog, page, page_size)


def _where_line(query: BuiltQuery) -> str:
    lines = [line for line in query.page_cypher.split("\n") if line.startswith("WITH s WHERE ")]
    assert len(lines) == 1, query.page_cypher
    return lines[0][len("WITH s WHERE "):]


def _source(query: BuiltQuery) -> str:
    return query.page_cypher.split("\n")[1]


# --- lucene.candidate_query ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("term, expected", [
    ("lung", "*lung*"),
    ("Granuloma", "*granuloma*"),
    ("C57BL/6J", "*c57bl*"),
    ("lung tissue", "*lung* AND *tissue*"),
    ("CD8 Depletion", "*cd8* AND *depletion*"),
    ("lung-LUNG", "*lung*"),
    ("File_R1.fastq", "*file* AND *fastq*"),
    ("café au lait", "*caf* AND *lait*"),
])
def test_candidate_query_wraps_each_long_token_and_ands_them(term, expected):
    assert lucene.candidate_query(term) == expected


@pytest.mark.parametrize("term", ["6J", "a b", "BL/6", "", "   ", "--", "ééé"])
def test_candidate_query_is_none_when_no_token_survives(term):
    assert lucene.candidate_query(term) is None


def test_lucene_escape_backslashes_every_special():
    assert lucene.escape('a+b-c&d|e!f(g)h{i}j[k]l^m"n~o*p?q:r\\s/t') == (
        'a\\+b\\-c\\&d\\|e\\!f\\(g\\)h\\{i\\}j\\[k\\]l\\^m\\"n\\~o\\*p\\?q\\:r\\\\s\\/t'
    )


# --- terms ----------------------------------------------------------------------------------------------------------


def test_split_terms_matches_advanced_search():
    assert split_terms(["  lung ", "", None, "   ", "liver"]) == ["lung", "liver"]
    assert split_terms("  lung granuloma ") == ["lung granuloma"]
    assert split_terms("") == []
    assert split_terms(None) == []


# --- outputs, paging --------------------------------------------------------------------------------------------------


def test_every_statement_starts_with_cypher_25_and_pages_in_the_database():
    q = _build({"sampletype": "TIS", "filter_searchText": ""}, page=3, page_size=50)
    for statement in (q.page_cypher, q.count_cypher, q.ids_cypher):
        assert statement.startswith("CYPHER 25\n")
    assert q.page_cypher.endswith("WITH s ORDER BY s.id SKIP $skip LIMIT $limit RETURN s.id AS id")
    assert q.count_cypher.endswith("RETURN count(s) AS total, collect(DISTINCT s.type) AS types")
    assert q.ids_cypher.endswith("RETURN s.id AS id ORDER BY id")
    assert q.params == {"types": ["TIS"], "skip": 100, "limit": 50}


def test_the_three_statements_share_one_match():
    q = _build({"sampletype": "TIS", "filter_searchText": "lung"}, scope=MEMBER)
    body = q.page_cypher.rsplit("\n", 1)[0]
    assert q.count_cypher.rsplit("\n", 1)[0] == body
    assert q.ids_cypher.rsplit("\n", 1)[0] == body


@pytest.mark.parametrize("page, page_size", [(0, 100), (-1, 100), (1, 0), (1, -5)])
def test_a_non_positive_page_or_page_size_is_invalid(page, page_size):
    with pytest.raises(GraphSearchInvalid):
        _build({"sampletype": "TIS", "filter_searchText": ""}, page=page, page_size=page_size)


def test_page_size_is_capped_at_1000():
    q = _build({"sampletype": "TIS", "filter_searchText": ""}, page=2, page_size=5000)
    assert q.params["limit"] == 1000 and q.params["skip"] == 1000


# --- sample types -------------------------------------------------------------------------------------------------------


def test_types_only_scans_the_type_index_and_filters_nothing_else():
    q = _build({"sampletype": "TIS", "filter_searchText": ""})
    assert _source(q) == "MATCH (s:Sample) WHERE s.type IN $types"
    assert "WITH s WHERE" not in q.page_cypher
    assert "fulltext" not in q.page_cypher


def test_sampletype_ids_and_titles_map_to_catalog_titles_in_request_order():
    q = _build({"sampletype": ["26", "MUS", "TIS"], "filter_searchText": ""})
    assert q.params["types"] == ["TIS", "MUS"]


def test_an_id_the_catalog_does_not_know_still_filters_to_nothing():
    q = _build({"sampletype": "999", "filter_searchText": "lung"})
    assert q.params["types"] == []
    assert "s.type IN $types" in _where_line(q)


def test_types_filter_applies_after_a_fulltext_source():
    q = _build({"sampletype": ["TIS", "MUS"], "filter_searchText": "lung"})
    assert _source(q) == FULLTEXT
    assert "s.type IN $types" in _where_line(q)
    assert q.params["types"] == ["TIS", "MUS"]


# --- scope --------------------------------------------------------------------------------------------------------------


def test_a_non_admin_gets_the_scope_clause_and_an_admin_does_not():
    member = _build({"sampletype": "TIS", "filter_searchText": ""}, scope=MEMBER)
    assert "any(p IN s.project_ids WHERE p IN $projects)" in _where_line(member)
    assert member.params["projects"] == [2, 6]

    admin = _build({"sampletype": "TIS", "filter_searchText": ""}, scope=ADMIN)
    assert "project_ids" not in admin.page_cypher
    assert "projects" not in admin.params


def test_a_non_admin_with_no_projects_is_scoped_to_nothing_never_unscoped():
    q = _build({"sampletype": "TIS", "filter_searchText": ""}, scope=Scope(False, 144, ()))
    assert "any(p IN s.project_ids WHERE p IN $projects)" in _where_line(q)
    assert q.params["projects"] == []


def test_scope_is_in_every_statement():
    q = _build({"filter_searchText": "lung"}, scope=MEMBER)
    for statement in (q.page_cypher, q.count_cypher, q.ids_cypher):
        assert "any(p IN s.project_ids WHERE p IN $projects)" in statement


# --- text terms ---------------------------------------------------------------------------------------------------------


def test_one_partial_term_uses_fulltext_candidates_then_verifies_the_substring():
    q = _build({"filter_searchText": "Granuloma"})
    assert _source(q) == FULLTEXT
    assert _where_line(q) == "toLower(s.search_text) CONTAINS $t0"
    assert q.params == {"lucene": "*granuloma*", "t0": "granuloma", "skip": 0, "limit": 100}


def test_short_tokens_are_left_to_the_verification_step():
    q = _build({"filter_searchText": "C57BL/6J"})
    assert q.params["lucene"] == "*c57bl*"
    assert q.params["t0"] == "c57bl/6j"
    assert "toLower(s.search_text) CONTAINS $t0" in _where_line(q)


def test_partial_terms_or_joins_candidates_and_verifications_by_or():
    q = _build({"filter_searchText": ["kidney", "Liver"], "searchText_logic": "OR"})
    assert q.params["lucene"] == "(*kidney*) OR (*liver*)"
    assert _where_line(q) == "(toLower(s.search_text) CONTAINS $t0 OR toLower(s.search_text) CONTAINS $t1)"
    assert q.params["t0"] == "kidney" and q.params["t1"] == "liver"


def test_a_list_of_terms_defaults_to_or():
    q = _build({"filter_searchText": ["kidney", "liver"]})
    assert q.params["lucene"] == "(*kidney*) OR (*liver*)"


def test_partial_terms_and_joins_candidates_and_verifications_by_and():
    q = _build({"filter_searchText": ["lung", "granuloma"], "searchText_logic": "AND"})
    assert q.params["lucene"] == "(*lung*) AND (*granuloma*)"
    assert _where_line(q) == "(toLower(s.search_text) CONTAINS $t0 AND toLower(s.search_text) CONTAINS $t1)"


def test_exact_single_term_is_untrimmed_equality_with_any_value():
    q = _build({"filter_searchText": "Lung", "filter_matchType": "EXACT"})
    assert _where_line(q) == "$t0 IN split(toLower(s.search_text), '\\n')"
    assert q.params["t0"] == "lung"


def test_exact_or_needs_any_term_equal_to_a_value():
    q = _build({"filter_searchText": ["F", "Lung"], "searchText_logic": "OR", "filter_matchType": "EXACT"})
    assert _where_line(q) == (
        "($t0 IN split(toLower(s.search_text), '\\n') OR $t1 IN split(toLower(s.search_text), '\\n'))"
    )


def test_exact_and_is_advanced_searchs_engine_rule_every_term_contained_and_one_equal():
    # The engine ANDs `json_metadata LIKE %term%` per term in SQL, then keeps a row when ANY term equals a value.
    q = _build({"filter_searchText": ["lung", "C57BL/6J"], "searchText_logic": "AND", "filter_matchType": "EXACT"})
    assert _where_line(q) == (
        "((toLower(s.search_text) CONTAINS $t0 AND toLower(s.search_text) CONTAINS $t1)"
        " AND ($t0 IN split(toLower(s.search_text), '\\n') OR $t1 IN split(toLower(s.search_text), '\\n')))"
    )
    assert q.params["lucene"] == "(*lung*) AND (*c57bl*)"


def test_a_term_of_only_short_tokens_falls_back_to_the_type_scan():
    q = _build({"sampletype": "TIS", "filter_searchText": "6J"})
    assert _source(q) == "MATCH (s:Sample) WHERE s.type IN $types"
    assert "lucene" not in q.params
    assert "toLower(s.search_text) CONTAINS $t0" in _where_line(q)


def test_a_term_of_only_short_tokens_without_types_is_a_logged_full_scan(caplog):
    with caplog.at_level(logging.WARNING, logger="nextseek_api.graph_search.query"):
        q = _build({"filter_searchText": "6J"})
    assert _source(q) == "MATCH (s:Sample)"
    assert "full scan" in caplog.text


def test_or_with_one_short_term_cannot_use_fulltext():
    q = _build({"sampletype": "MUS", "filter_searchText": ["lung", "F"], "searchText_logic": "OR"})
    assert _source(q) == "MATCH (s:Sample) WHERE s.type IN $types"
    assert "lucene" not in q.params


def test_and_with_one_short_term_uses_the_other_terms_candidates():
    q = _build({"filter_searchText": ["granuloma", "F"], "searchText_logic": "AND"})
    assert _source(q) == FULLTEXT
    assert q.params["lucene"] == "*granuloma*"
    assert "toLower(s.search_text) CONTAINS $t1" in _where_line(q)


def test_no_value_is_ever_interpolated():
    nasty = "lung' OR 1=1 // `x` $t0 }"
    q = _build({"filter_searchText": nasty, "attribute": "organ"})
    for statement in (q.page_cypher, q.count_cypher, q.ids_cypher):
        assert "1=1" not in statement and "lung'" not in statement
    assert q.params["t0"] == nasty.lower().strip()


# --- UID terms -----------------------------------------------------------------------------------------------------------


def test_uid_terms_only_seek_the_uuid():
    q = _build({"filter_searchText": ["TIS-220119FLY-7", "TIS-220119FLY-8"]})
    assert _source(q) == "MATCH (s:Sample) WHERE s.uuid IN $uids"
    assert q.params["uids"] == ["TIS-220119FLY-7", "TIS-220119FLY-8"]
    assert "lucene" not in q.params and "t0" not in q.params
    assert "WITH s WHERE" not in q.page_cypher


def test_uid_and_text_terms_union_whatever_the_logic():
    q = _build({"filter_searchText": ["TIS-220119FLY-7", "lung"], "searchText_logic": "AND"})
    assert _source(q) == (
        f"CALL () {{ {FULLTEXT} RETURN s UNION MATCH (s:Sample) WHERE s.uuid IN $uids RETURN s }}"
    )
    assert _where_line(q) == "(s.uuid IN $uids OR toLower(s.search_text) CONTAINS $t1)"
    assert q.params["uids"] == ["TIS-220119FLY-7"]
    assert q.params["t1"] == "lung" and "t0" not in q.params


def test_a_lowercase_uid_is_a_text_term():
    q = _build({"filter_searchText": "tis-220119fly-7"})
    assert "uids" not in q.params and q.params["t0"] == "tis-220119fly-7"


def test_uid_terms_under_a_scan_source_keep_their_own_match():
    q = _build({"sampletype": "TIS", "filter_searchText": ["TIS-220119FLY-7", "ab"], "searchText_logic": "OR"})
    assert _source(q) == "MATCH (s:Sample) WHERE s.type IN $types"
    assert "(s.uuid IN $uids OR toLower(s.search_text) CONTAINS $t1)" in _where_line(q)


# --- attribute stage -------------------------------------------------------------------------------------------------------


def test_attribute_resolves_to_every_case_variant_on_the_requested_type():
    q = _build({"sampletype": "TIS", "filter_searchText": "lung", "attribute": "organ"})
    assert (
        "(toLower(toString(s.`Organ`)) CONTAINS $t0 OR toLower(toString(s.`organ`)) CONTAINS $t0)"
    ) in _where_line(q)


def test_attribute_resolution_is_limited_to_the_requested_types():
    q = _build({"sampletype": "MUS", "filter_searchText": "lung", "attribute": "organ"})
    assert "s.`organ`" not in q.page_cypher
    assert "toLower(toString(s.`Organ`)) CONTAINS $t0" in _where_line(q)


def test_attribute_resolution_spans_every_type_when_none_is_requested():
    q = _build({"filter_searchText": "lung", "attribute": "ORGAN"})
    assert "s.`Organ`" in q.page_cypher and "s.`organ`" in q.page_cypher


def test_attribute_names_match_titles_trimmed_as_advanced_search_trims_keys():
    q = _build({"sampletype": "TIS", "filter_searchText": "x", "attribute": "media supplement"})
    assert "toLower(toString(s.`Media supplement `)) CONTAINS $t0" in _where_line(q)


def test_exact_attribute_stage_is_trimmed_equality():
    q = _build({"sampletype": "TIS", "filter_searchText": "Lung", "attribute": "Organ",
                "filter_matchType": "EXACT"})
    where = _where_line(q)
    assert "$t0 IN split(toLower(s.search_text), '\\n')" in where
    assert "btrim(toLower(toString(s.`Organ`)), $ws) = $t0" in where


def test_py_whitespace_is_exactly_what_str_strip_removes():
    import sys
    assert set(PY_WHITESPACE) == {chr(c) for c in range(sys.maxunicode + 1) if chr(c).isspace()}


def test_partial_attribute_stage_needs_no_whitespace_param():
    q = _build({"sampletype": "TIS", "filter_searchText": "lung", "attribute": "Organ"})
    assert "ws" not in q.params


def test_crossed_attributes_or_across_names_and_across_terms():
    # advanced_search's crossed shape: each term must match Sex OR Strain, and every term must (AND).
    q = _build({"sampletype": "MUS", "filter_searchText": ["F", "C57BL/6J"], "searchText_logic": "AND",
                "attribute": ["Sex", "Strain"], "attribute_logic": "OR", "filter_matchType": "EXACT"})
    sex0, strain0 = "btrim(toLower(toString(s.`Sex`)), $ws) = $t0", "btrim(toLower(toString(s.`Strain`)), $ws) = $t0"
    sex1, strain1 = "btrim(toLower(toString(s.`Sex`)), $ws) = $t1", "btrim(toLower(toString(s.`Strain`)), $ws) = $t1"
    assert f"(({sex0} OR {strain0}) AND ({sex1} OR {strain1}))" in _where_line(q)
    assert q.params["t0"] == "f" and q.params["t1"] == "c57bl/6j"
    assert q.params["ws"] == PY_WHITESPACE


def test_attribute_logic_and_needs_every_name():
    q = _build({"sampletype": "MUS", "filter_searchText": "f", "attribute": ["Sex", "Strain"],
                "attribute_logic": "AND"})
    assert (
        "(toLower(toString(s.`Sex`)) CONTAINS $t0 AND toLower(toString(s.`Strain`)) CONTAINS $t0)"
    ) in _where_line(q)


def test_a_name_with_no_catalog_title_matches_nothing():
    q = _build({"sampletype": "TIS", "filter_searchText": "lung", "attribute": "Nope"})
    assert _where_line(q).endswith("AND false")


def test_attribute_without_a_term_is_ignored():
    q = _build({"sampletype": "TIS", "filter_searchText": "", "attribute": "Organ"})
    assert "Organ" not in q.page_cypher


def test_uid_terms_take_part_in_the_attribute_stage():
    q = _build({"filter_searchText": ["TIS-220119FLY-7", "lung"], "attribute": "Organ"})
    where = _where_line(q)
    assert "toLower(toString(s.`Organ`)) CONTAINS $t0" in where
    assert "toLower(toString(s.`Organ`)) CONTAINS $t1" in where
    assert q.params["t0"] == "tis-220119fly-7"


def test_the_uid_attribute_reads_the_uuid_property():
    q = _build({"sampletype": "TIS", "filter_searchText": "220119FLY", "attribute": "uid"})
    assert "toLower(toString(s.uuid)) CONTAINS $t0" in _where_line(q)
    assert "s.`UID`" not in q.page_cypher


def test_a_title_containing_a_backtick_is_doubled():
    q = _build({"sampletype": "TIS", "filter_searchText": "lung", "attribute": "name`x"})
    assert "s.`Name``x`" in q.page_cypher
    assert quote_name("a`b``c") == "`a``b````c`"


# --- extensions.where ---------------------------------------------------------------------------------------------------------


def test_where_without_terms_scans_the_type_label():
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": "Lung"}]}})
    assert _source(q) == "MATCH (s:`T_TIS`)"
    assert _where_line(q) == "s.`Organ` = $w0"
    assert q.params == {"w0": "Lung", "skip": 0, "limit": 100}


def test_where_casts_a_numeric_string_by_value_type():
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "CellCount", "op": ">=", "value": "10000000"}]}})
    assert "s.`CellCount` >= $w0" in _where_line(q)
    assert q.params["w0"] == 10000000.0 and isinstance(q.params["w0"], float)


def test_where_casts_dates_and_every_in_item():
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "D.SEQ", "attribute": "Reads", "op": "IN", "value": ["3", "4.0", 5]}]}})
    assert "s.`Reads` IN $w0" in _where_line(q)
    assert q.params["w0"] == [3, 4, 5]

    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Collected", "op": "<", "value": "2024-01-31"}]}})
    assert q.params["w0"] == date(2024, 1, 31)


def test_where_keeps_a_value_its_type_cannot_hold():
    # Values that fail the cast are stored raw, so a raw query value can still find them.
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "CellCount", "op": "=", "value": "n/a"}]}})
    assert q.params["w0"] == "n/a"


def test_where_string_operators_take_the_value_as_text():
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "CellCount", "op": "STARTS WITH", "value": 12},
        {"sample_type": "TIS", "attribute": "Organ", "op": "CONTAINS", "value": "Lu"}]}})
    assert "toString(s.`CellCount`) STARTS WITH $w0" in _where_line(q)
    assert "toString(s.`Organ`) CONTAINS $w1" in _where_line(q)
    assert q.params["w0"] == "12" and q.params["w1"] == "Lu"


def test_where_string_operators_read_the_text_of_a_value_stored_as_a_number():
    # The Simple box's Contain was `From in str(value).strip()` (seek/dbtable_sampleattribute.py STRING_RULES), so a
    # number held by a string attribute matched by its digits. The graph keeps such a value as a number, and Cypher's
    # CONTAINS on a number is null, so the operator reads the property through toString().
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "D.SEQ", "attribute": "Reads", "op": "CONTAINS", "value": "12"}]}})
    assert _where_line(q) == "toString(s.`Reads`) CONTAINS $w0"
    assert q.params["w0"] == "12"


def test_where_not_contains_is_the_negation_of_contains_over_samples_that_hold_the_value():
    # advanced_search's Not Contain: `From not in str(value).strip()`, and only on rows whose metadata holds the
    # attribute with a non-null value (_filterSamples keeps a row only when _highlightKeyValues finds it).
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "NOT CONTAINS", "value": "Lung"}]}})
    assert _where_line(q) == "(s.`Organ` IS NOT NULL AND NOT (toString(s.`Organ`) CONTAINS $w0))"
    assert q.params["w0"] == "Lung"


def test_where_not_contains_takes_a_number_as_text():
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "D.SEQ", "attribute": "Reads", "op": "NOT CONTAINS", "value": 12}]}})
    assert q.params["w0"] == "12"


TRUTHY = (
    "CASE WHEN s.`Viable` IS :: BOOLEAN NOT NULL THEN s.`Viable` "
    "WHEN s.`Viable` IS :: INTEGER NOT NULL THEN s.`Viable` = 1 "
    "WHEN s.`Viable` IS :: FLOAT NOT NULL THEN s.`Viable` >= 1.0 AND s.`Viable` < 2.0 "
    "WHEN s.`Viable` IS :: STRING NOT NULL THEN btrim(s.`Viable`, $ws) =~ '[+]?(0_?)*1' "
    "OR toLower(btrim(s.`Viable`, $ws)) IN ['true', 'yes'] "
    "ELSE false END"
)


def test_where_is_true_is_advanced_searchs_to_binary_tiny_int_rule():
    # dmac/conversion.py::toBinaryTinyInt(value) == 1: int(value) is 1 (a boolean true, the integer 1, a float that
    # truncates to 1, a string int() reads as 1), or the trimmed, lower-cased text is "true" or "yes".
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Viable", "op": "IS TRUE"}]}})
    assert _where_line(q) == TRUTHY
    assert q.params == {"ws": PY_WHITESPACE, "skip": 0, "limit": 100}


def test_where_is_false_is_every_other_value_the_sample_holds():
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Viable", "op": "IS FALSE"}]}})
    assert _where_line(q) == f"(s.`Viable` IS NOT NULL AND NOT ({TRUTHY}))"
    assert "w0" not in q.params and q.params["ws"] == PY_WHITESPACE


def test_where_truth_and_value_operators_mix():
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "CONTAINS", "value": "Lu"},
        {"sample_type": "TIS", "attribute": "Viable", "op": "IS TRUE"}]}})
    assert _where_line(q) == f"toString(s.`Organ`) CONTAINS $w0 AND {TRUTHY}"
    assert q.params["w0"] == "Lu" and "w1" not in q.params


def test_where_items_are_anded():
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": "Lung"},
        {"sample_type": "TIS", "attribute": "CellCount", "op": ">=", "value": 10000000}]}})
    assert _where_line(q) == "s.`Organ` = $w0 AND s.`CellCount` >= $w1"
    assert q.params["w1"] == 10000000.0


def test_where_with_terms_keeps_its_type_as_a_label_predicate():
    q = _build({"filter_searchText": "granuloma", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": "Lung"}]}})
    assert _source(q) == FULLTEXT
    assert "s:`T_TIS`" in _where_line(q)
    assert "s.`Organ` = $w0" in _where_line(q)


def test_where_with_short_terms_scans_the_label_not_everything():
    q = _build({"filter_searchText": "6J", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": "Lung"}]}})
    assert _source(q) == "MATCH (s:`T_TIS`)"


def test_where_on_the_uid_attribute_reads_the_uuid_property():
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "UID", "op": "=", "value": "TIS-220119FLY-7"}]}})
    assert _where_line(q) == "s.uuid = $w0"


def test_where_title_with_a_backtick_is_doubled():
    q = _build({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Name`x", "op": "=", "value": "a"}]}})
    assert _where_line(q) == "s.`Name``x` = $w0"


@pytest.mark.parametrize("item", [
    {"sample_type": "TIS", "attribute": "Nope", "op": "=", "value": "x"},
    {"sample_type": "TIS", "attribute": "organ ", "op": "=", "value": "x"},
    {"sample_type": "TIS", "attribute": "", "op": "=", "value": "x"},
    {"sample_type": "NOPE", "attribute": "Organ", "op": "=", "value": "x"},
    {"sample_type": "MUS", "attribute": "CellCount", "op": "=", "value": 1},
])
def test_where_on_an_attribute_the_type_does_not_have_is_invalid(item):
    with pytest.raises(GraphSearchInvalid):
        _build({"filter_searchText": "", "extensions": {"where": [item]}})


def test_where_rechecks_what_it_interpolates():
    filters = _req({"filter_searchText": ""}).to_db_filters(sampletype_resolver=_resolver)
    for bad in (
        {"where": [{"sample_type": "TIS", "attribute": "Organ", "op": "=~", "value": ".*"}]},
        {"where": [{"sample_type": "TIS", "attribute": "Organ", "op": "IN", "value": "x"}]},
        {"where": [{"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": ["x"]}]},
        {"where": [{"sample_type": "D.SEQ", "attribute": "Reads", "op": "=", "value": 2 ** 70}]},
        {"where": [{"sample_type": "TIS", "attribute": "Viable", "op": "IS TRUE", "value": "yes"}]},
        {"where": [{"sample_type": "TIS", "attribute": "Organ", "op": "NOT CONTAINS", "value": ["x"]}]},
        {"where": [{"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": None}]},
        {"where": [{"sample_type": "TIS", "attribute": "Organ", "op": "="}]},
        {"where": [{"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": "a"},
                   {"sample_type": "MUS", "attribute": "Sex", "op": "=", "value": "F"}]},
    ):
        with pytest.raises(GraphSearchInvalid):
            build(filters, bad, ADMIN, CATALOG, 1, 100)


def test_extensions_may_be_the_model_or_a_plain_dict():
    body = {"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": "Lung"}]}}
    req = _req(body)
    filters = req.to_db_filters(sampletype_resolver=_resolver)
    from_model = build(filters, req.extensions, ADMIN, CATALOG, 1, 100)
    from_dict = build(filters, body["extensions"], ADMIN, CATALOG, 1, 100)
    assert isinstance(req.extensions, GraphSearchExtensions)
    assert from_model == from_dict


# --- extensions.lineage --------------------------------------------------------------------------------------------------


def test_lineage_descendant_interpolates_hops_as_an_integer_and_backticks_the_label():
    q = _build({"sampletype": "TIS", "filter_searchText": "",
                "extensions": {"lineage": {"direction": "descendant", "sample_type": "D.SEQ", "max_hops": 3}}})
    assert "EXISTS { (s)<-[:DERIVED_FROM*1..3]-(:`T_D_SEQ`) }" in _where_line(q)


def test_lineage_ancestor_points_the_other_way_and_defaults_to_4_hops():
    q = _build({"sampletype": "D.SEQ", "filter_searchText": "",
                "extensions": {"lineage": {"direction": "ancestor", "sample_type": "MUS"}}})
    assert "EXISTS { (s)-[:DERIVED_FROM*1..4]->(:`T_MUS`) }" in _where_line(q)


@pytest.mark.parametrize("lineage", [
    {"direction": "descendant", "sample_type": "NOPE", "max_hops": 2},
    {"direction": "sideways", "sample_type": "MUS", "max_hops": 2},
    {"direction": "ancestor", "sample_type": "MUS", "max_hops": 5},
    {"direction": "ancestor", "sample_type": "MUS", "max_hops": 0},
    {"direction": "ancestor", "sample_type": "MUS", "max_hops": "2"},
    {"direction": "ancestor", "sample_type": "MUS", "max_hops": True},
])
def test_lineage_rechecks_what_it_interpolates(lineage):
    filters = _req({"sampletype": "TIS", "filter_searchText": ""}).to_db_filters(sampletype_resolver=_resolver)
    with pytest.raises(GraphSearchInvalid):
        build(filters, {"lineage": lineage}, ADMIN, CATALOG, 1, 100)


# --- nothing to search on ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {"filter_searchText": ""},
    {"filter_searchText": ["", "  "]},
    {"filter_searchText": "", "attribute": "Organ"},
    {"filter_searchText": "", "extensions": {"where": []}},
    {"filter_searchText": "", "extensions": {"lineage": {"direction": "ancestor", "sample_type": "MUS"}}},
])
def test_nothing_to_search_on_is_invalid(body):
    with pytest.raises(GraphSearchInvalid):
        _build(body)


def test_a_full_request_has_exactly_these_params():
    q = _build({"sampletype": ["TIS"], "filter_searchText": ["TIS-220119FLY-7", "Lung"], "attribute": "organ",
                "filter_matchType": "EXACT",
                "extensions": {"where": [{"sample_type": "TIS", "attribute": "CellCount", "op": ">", "value": "5"}],
                               "lineage": {"direction": "descendant", "sample_type": "D.SEQ", "max_hops": 2}}},
               scope=MEMBER, page=2, page_size=10)
    assert q.params == {
        "lucene": "*lung*",
        "uids": ["TIS-220119FLY-7"],
        "t0": "tis-220119fly-7",
        "t1": "lung",
        "types": ["TIS"],
        "projects": [2, 6],
        "ws": PY_WHITESPACE,
        "w0": 5.0,
        "skip": 10,
        "limit": 10,
    }
