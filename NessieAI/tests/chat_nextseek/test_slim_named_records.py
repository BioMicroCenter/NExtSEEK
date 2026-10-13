"""Fix 1: a retrieve by identifier previews every record the request named. Synthetic data only."""
from __future__ import annotations

import json

from chat_nextseek.helpers.results import slim_api_result_for_llm


def _rec(uid, **extra):
    return {"uid": uid, "name": uid.lower(), **extra}


def _body(groups):
    return {"data": [{"sample_type": t, "n_samples": len(r), "samples": r} for t, r in groups]}


def _plan(*ids):
    return {"requestBody": {"identifiers": list(ids)}}


def _uids(slim):
    return [r["uid"] for r in slim["data"]["samples_preview"]]


def test_second_named_record_in_later_type_is_kept():
    groups = [
        ("AAA", [_rec(f"AAA-{i}") for i in range(1, 5)]),
        ("BBB", [_rec(f"BBB-{i}") for i in range(1, 5)]),
        ("CCC", [_rec(f"CCC-{i}") for i in range(1, 5)]),
        ("DDD", [_rec(f"DDD-{i}") for i in range(1, 5)]),
        ("EEE", [_rec(f"EEE-{i}") for i in range(1, 5)]),
        ("FFF", [_rec(f"FFF-{i}") for i in range(1, 5)]),
    ]
    slim = slim_api_result_for_llm({"data": _body(groups)}, api_plan=_plan("AAA-1", "FFF-4"))
    assert _uids(slim)[:2] == ["AAA-1", "FFF-4"]


def test_three_named_over_two_types_even_past_row_cap():
    groups = [("XX", [_rec(f"XX-{i}") for i in range(1, 8)]), ("YY", [_rec(f"YY-{i}") for i in range(1, 8)])]
    named = ["XX-7", "YY-6", "YY-7", "xx-5", "YY-5", "XX-6"]
    slim = slim_api_result_for_llm({"data": _body(groups)}, api_plan=_plan(*named))
    assert set(_uids(slim)) >= {n.upper() for n in named}


def test_named_record_with_very_long_field_is_trimmed_not_dropped():
    groups = [("AA", [_rec("AA-1", notes="x" * 20000)]), ("BB", [_rec("BB-1", notes="y" * 20000)])]
    slim = slim_api_result_for_llm({"data": _body(groups)}, api_plan=_plan("AA-1", "BB-1"))
    assert _uids(slim) == ["AA-1", "BB-1"]
    assert len(json.dumps(slim)) < 6000 and slim["data"]["fields_trimmed"] is True


def test_empty_fields_are_not_sent():
    groups = [("AA", [_rec("AA-1", a="", b=None, c=[], d={}, e="kept", f=0)])]
    rec = slim_api_result_for_llm({"data": _body(groups)})["data"]["samples_preview"][0]
    assert rec == {"sample_type": "AA", "uid": "AA-1", "name": "aa-1", "e": "kept", "f": 0}


def test_unnamed_bulk_result_behaves_as_before():
    groups = [("AA", [_rec(f"AA-{i}") for i in range(1, 10)]), ("BB", [_rec(f"BB-{i}") for i in range(1, 10)])]
    slim = slim_api_result_for_llm({"data": _body(groups)}, api_plan={"queryParameters": {}})
    assert _uids(slim) == ["AA-1", "BB-1", "AA-2", "BB-2", "AA-3"]
    assert slim["data"]["samples_truncated"] is True
