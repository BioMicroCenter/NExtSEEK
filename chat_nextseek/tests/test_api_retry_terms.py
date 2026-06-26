"""advanced_search retry should be lab-code-aware: when a lab-scoped search (whose
3-letter code the api_agent may have fused into filter_searchText with other terms)
returns 0, the OR->SINGLE fallback must fire and try the individual terms — sourcing
them from the SENT filter_searchText tokens + filters.keywords + filters.lab_codes,
not from filters.keywords alone."""
from chat_nextseek.helpers.tools.nextseek_api import (
    _retry_terms,
    _should_retry_advanced_search,
    _advanced_search_retry_attempts,
)

ADV = "/nextseek_api/samples/advanced_search/"


def _plan(keywords=None, lab_codes=None):
    return {"filters": {"keywords": keywords or [], "lab_codes": lab_codes or []}}


def _api_plan(searchtext):
    return {"endpoint": ADV, "method": "POST",
            "requestBody": {"filter_searchText": searchtext}}


def _empty():
    return {"ok": True, "data": {"total": 0, "rows": []}}


def test_retry_terms_includes_lab_codes_and_searchtext_tokens():
    # Q1 shape: keywords empty, lab_codes=[KAM], api_agent sent "KAM MetNet".
    terms = _retry_terms(_plan(keywords=[], lab_codes=["KAM"]), _api_plan("KAM MetNet"))
    assert terms == ["KAM", "MetNet"]  # split sent text + lab_codes, deduped in order


def test_should_retry_fires_for_labcode_fused_search_on_zero():
    # The exact Q1 failure: "KAM MetNet" (2 tokens) -> 0 results -> retry MUST fire.
    assert _should_retry_advanced_search(
        _plan(lab_codes=["KAM"]), _api_plan("KAM MetNet"), _empty()) is True


def test_should_not_retry_single_term_zero():
    # A genuine single-term search with no fallback term must NOT retry.
    assert _should_retry_advanced_search(
        _plan(keywords=["zebrafish"]), _api_plan("zebrafish"), _empty()) is False


def test_retry_attempts_include_each_single_term():
    texts = {t for _, t in _advanced_search_retry_attempts(["KAM", "MetNet"])}
    assert {"KAM", "MetNet", "KAM OR MetNet"} <= texts
