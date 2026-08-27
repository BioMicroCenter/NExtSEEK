"""
Regression lock for T0.2: safe_parse_json must close a truncated object.

The fallback stage was `text[text.find("{") : text.rfind("}") + 1]`. When a model's
output is cut off mid-object, `rfind` does not return -1 — it finds an *inner* closing
brace (in the observed payload, the one from `"queryParameters": {}`, at index 266), so
the slice is still unbalanced and the function gave up and returned None.

`json.loads(raw + "}")` yields the correct plan, so the information was there all along.

The repair is append-only: it may add closing brackets, never values. A payload repaired
into a valid-but-incomplete object would be worse than a clean failure, so anything that
would require inventing a value (truncation inside a string literal) still returns None.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from chat_nextseek.helpers.json_io import safe_parse_json
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.tools import APIRequestPlan


# The observed case-1 payload: an APIRequestPlan cut off after the last list value,
# missing the two closing braces for requestBody and the object itself.
CASE_1_RAW = (
    '{"endpoint": "/nextseek_api/samples/advanced_search/", '
    '"method": "POST", '
    '"queryParameters": {}, '
    '"requestBody": {"child_sample_types": ["D.SEQ", "D.IMG"], '
    '"parent_sample_type_filters": ["PAT"]'
)


def test_the_naive_slice_really_is_unbalanced():
    """Pins the reason the old fallback failed: rfind lands on an inner brace, not -1."""
    start = CASE_1_RAW.find("{")
    end = CASE_1_RAW.rfind("}")

    assert end != -1, "rfind found no brace at all — that is not the observed failure"
    assert end > start
    with pytest.raises(json.JSONDecodeError):
        json.loads(CASE_1_RAW[start : end + 1])

    # The inner brace found is the empty queryParameters object.
    assert CASE_1_RAW[end - 1 : end + 1] == "{}"


def test_case_1_payload_is_repaired_into_the_correct_plan():
    parsed = safe_parse_json(CASE_1_RAW)

    assert parsed is not None, "truncated object was not repaired"
    assert parsed["requestBody"] == {
        "child_sample_types": ["D.SEQ", "D.IMG"],
        "parent_sample_type_filters": ["PAT"],
    }
    assert parsed["endpoint"] == "/nextseek_api/samples/advanced_search/"
    assert parsed["queryParameters"] == {}

    # And it validates against the real schema the api agent uses.
    plan = APIRequestPlan.model_validate(parsed)
    assert plan.method == "POST"


def test_repair_matches_naive_brace_append():
    """The information was always there — this is what the fix recovers."""
    assert safe_parse_json(CASE_1_RAW) == json.loads(CASE_1_RAW + "}}")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"a": [1, 2', {"a": [1, 2]}),
        ('{"a": {"b": {"c": 1', {"a": {"b": {"c": 1}}}),
        ('{"a": 1,', {"a": 1}),
        ('{"a": 1, ', {"a": 1}),
        ('{"a": [1, 2,', {"a": [1, 2]}),
        ('{"a": 1, "b":', {"a": 1}),
        ('{"a": 1, "b": ', {"a": 1}),
    ],
)
def test_appends_closers_and_trims_dangling_separators(raw, expected):
    assert safe_parse_json(raw) == expected


def test_truncation_inside_a_string_literal_is_not_repaired():
    """Closing the quote would invent a value boundary. A clean failure is correct."""
    assert safe_parse_json('{"a": "hello wor') is None
    assert safe_parse_json('{"explanation": "counts mouse samples in the Imp') is None


def test_escaped_quotes_do_not_confuse_the_scanner():
    assert safe_parse_json('{"a": "he said \\"hi\\"", "b": [1') == {"a": 'he said "hi"', "b": [1]}


def test_braces_inside_strings_are_not_counted():
    assert safe_parse_json('{"cypher": "MATCH (s) WHERE s.id = {x} RETURN s"') == {
        "cypher": "MATCH (s) WHERE s.id = {x} RETURN s"
    }


def test_repair_does_not_mask_a_missing_required_field():
    """A repaired-but-incomplete object must still be rejected downstream."""
    raw = '{"explanation": "counts mouse samples", "parameters": {"project": "Impact"'

    parsed = safe_parse_json(raw)
    assert parsed == {"explanation": "counts mouse samples", "parameters": {"project": "Impact"}}

    # `cypher` is required on GraphAgentPlan and was lost to truncation. The repair
    # recovers structure only, so pydantic still catches the real defect.
    with pytest.raises(ValidationError):
        GraphAgentPlan.model_validate(parsed)


def test_absurdly_deep_truncation_is_refused():
    """Cap the repair — beyond a handful of closers we are guessing at structure."""
    assert safe_parse_json("{" + '"a": {' * 12) is None


def test_existing_behaviour_is_unchanged():
    assert safe_parse_json(None) is None
    assert safe_parse_json("") is None
    assert safe_parse_json("   ") is None
    assert safe_parse_json("no json here at all") is None
    assert safe_parse_json('{"a": 1}') == {"a": 1}
    assert safe_parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert safe_parse_json('here is the plan: {"a": 1} hope that helps') == {"a": 1}


def test_repair_is_logged(capsys):
    safe_parse_json(CASE_1_RAW)

    assert "[JSON_REPAIR]" in capsys.readouterr().out, "silent recovery hides model defects"
