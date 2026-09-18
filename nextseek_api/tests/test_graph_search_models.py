"""graph_search request models: advanced_search's body plus an optional `extensions` block.

GraphSearchRequest subclasses SampleAdvancedSearchRequest, so the shared fields, the
`extra='forbid'` rule and `to_db_filters` behave exactly as they do for advanced_search.
`extensions.where` items are exact, typed attribute conditions on one sample type, and
`extensions.lineage` keeps a sample only when a sample of a given type sits within 1 to 4
DERIVED_FROM hops. Catalog checks (does the attribute exist on the type) belong to the
query builder, not to these models.
"""
import pytest
from pydantic import ValidationError

from nextseek_api.models import (
    GraphSearchExtensions,
    GraphSearchLineage,
    GraphSearchRequest,
    GraphSearchWhere,
    SampleAdvancedSearchRequest,
)

ADVANCED_BODY = {
    "sampletype": ["TIS", "D.SEQ"],
    "attribute": ["Organ", "Tissue"],
    "filter_searchText": ["lung", "liver"],
    "filter_matchType": "EXACT",
    "attribute_logic": "AND",
    "searchText_logic": "OR",
}


def _where(**overrides):
    item = {"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": "Lung"}
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# The advanced_search body is accepted unchanged
# ---------------------------------------------------------------------------

def test_graph_search_request_is_an_advanced_search_request():
    assert issubclass(GraphSearchRequest, SampleAdvancedSearchRequest)


def test_plain_advanced_search_body_validates():
    req = GraphSearchRequest.model_validate(ADVANCED_BODY)
    assert req.extensions is None
    assert req.filter_searchText == ["lung", "liver"]
    assert req.filter_matchType.value == "EXACT"


def test_minimal_advanced_search_body_validates():
    req = GraphSearchRequest.model_validate({"filter_searchText": "granuloma"})
    assert req.extensions is None
    assert req.sampletype is None


def test_unknown_top_level_key_fails():
    with pytest.raises(ValidationError):
        GraphSearchRequest.model_validate({"filter_searchText": "x", "cypher": "MATCH (n) RETURN n"})


def test_filter_search_text_stays_required():
    with pytest.raises(ValidationError):
        GraphSearchRequest.model_validate({"extensions": {"where": [_where()]}})


def test_empty_search_text_with_where_validates():
    req = GraphSearchRequest.model_validate({"filter_searchText": "", "extensions": {"where": [_where()]}})
    assert req.filter_searchText == ""
    assert len(req.extensions.where) == 1


def test_to_db_filters_still_works_on_the_subclass():
    body = dict(ADVANCED_BODY, extensions={"where": [_where()]})
    resolver = {"TIS": "7", "D.SEQ": "12"}.get
    got = GraphSearchRequest.model_validate(body).to_db_filters(sampletype_resolver=resolver)
    expected = SampleAdvancedSearchRequest.model_validate(ADVANCED_BODY).to_db_filters(sampletype_resolver=resolver)
    assert got == expected
    assert got["sampletype_ids"] == [7, 12]
    assert got["attribute_list"] == ["organ", "tissue"]
    assert "extensions" not in got


# ---------------------------------------------------------------------------
# extensions.where
# ---------------------------------------------------------------------------

def test_where_items_keep_their_value_types():
    ext = GraphSearchExtensions.model_validate({"where": [
        _where(),
        _where(attribute="CellCount", op=">=", value=10000000),
        _where(attribute="Weight", op="<", value=2.5),
        _where(attribute="Organ", op="IN", value=["Lung", 3, 4.5]),
    ]})
    values = [w.value for w in ext.where]
    assert values == ["Lung", 10000000, 2.5, ["Lung", 3, 4.5]]
    assert type(values[1]) is int
    assert type(values[2]) is float


@pytest.mark.parametrize("op", ["=", "<>", "<", "<=", ">", ">=", "CONTAINS", "NOT CONTAINS", "STARTS WITH"])
def test_every_scalar_operator_accepts_a_scalar(op):
    assert GraphSearchWhere.model_validate(_where(op=op)).op == op


def test_in_with_a_list_validates():
    item = GraphSearchWhere.model_validate(_where(op="IN", value=["Lung", "Liver"]))
    assert item.value == ["Lung", "Liver"]


def test_in_with_a_scalar_fails():
    with pytest.raises(ValidationError, match="IN"):
        GraphSearchWhere.model_validate(_where(op="IN", value="Lung"))


@pytest.mark.parametrize("op", ["=", "<>", "<", "<=", ">", ">=", "CONTAINS", "NOT CONTAINS", "STARTS WITH"])
def test_scalar_operator_with_a_list_fails(op):
    with pytest.raises(ValidationError, match="scalar"):
        GraphSearchWhere.model_validate(_where(op=op, value=["Lung", "Liver"]))


@pytest.mark.parametrize("op", ["IS TRUE", "IS FALSE"])
def test_the_truth_operators_take_no_value(op):
    """The Simple box's True and False rules (seek/dbtable_sampleattribute.py BOOL_RULES) name no value."""
    item = _where(op=op)
    del item["value"]
    assert GraphSearchWhere.model_validate(item).value is None


@pytest.mark.parametrize("value", ["true", 1, ["x"]])
@pytest.mark.parametrize("op", ["IS TRUE", "IS FALSE"])
def test_a_truth_operator_with_a_value_fails(op, value):
    with pytest.raises(ValidationError, match="takes no value"):
        GraphSearchWhere.model_validate(_where(op=op, value=value))


@pytest.mark.parametrize("op", ["=", "CONTAINS", "NOT CONTAINS", "IN"])
def test_every_other_operator_needs_a_value(op):
    item = _where(op=op)
    del item["value"]
    with pytest.raises(ValidationError, match="requires a value"):
        GraphSearchWhere.model_validate(item)


@pytest.mark.parametrize("op", ["==", "LIKE", "in", "contains", "=~", "", "NOT", "not contains", "IS NULL"])
def test_unknown_operator_fails(op):
    with pytest.raises(ValidationError):
        GraphSearchWhere.model_validate(_where(op=op))


@pytest.mark.parametrize("missing", ["sample_type", "attribute", "op", "value"])
def test_where_fields_are_required(missing):
    item = _where()
    del item[missing]
    with pytest.raises(ValidationError):
        GraphSearchWhere.model_validate(item)


def test_where_item_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        GraphSearchWhere.model_validate(_where(project_ids=[1, 2]))


def test_where_items_on_the_same_sample_type_validate():
    ext = GraphSearchExtensions.model_validate({"where": [
        _where(),
        _where(attribute="CellCount", op=">=", value=10000000),
    ]})
    assert [w.attribute for w in ext.where] == ["Organ", "CellCount"]


def test_where_items_on_different_sample_types_fail():
    with pytest.raises(ValidationError, match="same sample_type"):
        GraphSearchExtensions.model_validate({"where": [
            _where(),
            _where(sample_type="D.SEQ", attribute="Platform", value="NovaSeq"),
        ]})


def test_different_sample_types_fail_through_the_request_too():
    body = {"filter_searchText": "", "extensions": {"where": [
        _where(),
        _where(sample_type="D.SEQ", attribute="Platform", value="NovaSeq"),
    ]}}
    with pytest.raises(ValidationError):
        GraphSearchRequest.model_validate(body)


# ---------------------------------------------------------------------------
# extensions.lineage and the extensions block itself
# ---------------------------------------------------------------------------

def test_lineage_defaults_to_four_hops():
    lineage = GraphSearchLineage.model_validate({"direction": "descendant", "sample_type": "D.SEQ"})
    assert lineage.max_hops == 4


@pytest.mark.parametrize("hops", [1, 2, 3, 4])
def test_lineage_accepts_one_to_four_hops(hops):
    lineage = GraphSearchLineage.model_validate({"direction": "ancestor", "sample_type": "TIS", "max_hops": hops})
    assert lineage.max_hops == hops


@pytest.mark.parametrize("hops", [0, 5, -1])
def test_lineage_hops_out_of_range_fail(hops):
    with pytest.raises(ValidationError):
        GraphSearchLineage.model_validate({"direction": "descendant", "sample_type": "D.SEQ", "max_hops": hops})


def test_lineage_hops_must_be_an_integer():
    with pytest.raises(ValidationError):
        GraphSearchLineage.model_validate({"direction": "descendant", "sample_type": "D.SEQ", "max_hops": 2.5})


def test_lineage_unknown_direction_fails():
    with pytest.raises(ValidationError):
        GraphSearchLineage.model_validate({"direction": "sibling", "sample_type": "D.SEQ"})


def test_lineage_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        GraphSearchLineage.model_validate({"direction": "descendant", "sample_type": "D.SEQ", "min_hops": 1})


def test_extensions_default_to_no_conditions():
    ext = GraphSearchExtensions.model_validate({})
    assert ext.where == []
    assert ext.lineage is None
    assert ext.query is None


def test_extensions_take_the_sample_search_query_text():
    req = GraphSearchRequest.model_validate({"filter_searchText": "", "extensions": {"query": "lung NOT granuloma"}})
    assert req.extensions.query == "lung NOT granuloma"


def test_the_query_text_is_bounded():
    GraphSearchExtensions.model_validate({"query": "x" * 2000})
    with pytest.raises(ValidationError):
        GraphSearchExtensions.model_validate({"query": "x" * 2001})
    with pytest.raises(ValidationError):
        GraphSearchExtensions.model_validate({"query": ["lung"]})


def test_extensions_forbid_unknown_keys():
    with pytest.raises(ValidationError):
        GraphSearchRequest.model_validate({"filter_searchText": "x", "extensions": {"scope": [1]}})


def test_full_request_with_where_and_lineage_validates():
    req = GraphSearchRequest.model_validate({
        "sampletype": "TIS",
        "filter_searchText": "",
        "extensions": {
            "where": [
                _where(),
                _where(attribute="CellCount", op=">=", value=10000000),
            ],
            "lineage": {"direction": "descendant", "sample_type": "D.SEQ", "max_hops": 4},
        },
    })
    assert req.extensions.lineage.direction == "descendant"
    assert req.extensions.lineage.max_hops == 4
    assert [w.op for w in req.extensions.where] == ["=", ">="]


def test_json_schema_carries_the_extensions_block():
    schema = GraphSearchRequest.model_json_schema()
    assert "extensions" in schema["properties"]
    assert "filter_searchText" in schema["required"]
    assert {"GraphSearchExtensions", "GraphSearchWhere", "GraphSearchLineage"} <= set(schema["$defs"])
