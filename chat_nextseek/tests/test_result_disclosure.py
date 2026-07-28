"""
Regression lock for T1.10: capped and substituted results must be disclosed.

Task 797's reply claimed "2,057 records" while api_result_meta.row_count was 1000,
because tool_nextseek_api_request hard-defaults page_size: 1000 and the chatter
faithfully reported the API's `total`. Neither the cap nor the fact that the rows came
from a substituted search text was disclosed anywhere in the payload the chatter saw.

That is not a hallucination — the model reported exactly what it was given. The defect
is the silence.
"""
from __future__ import annotations

from chat_nextseek.helpers.results import (
    DEFAULT_API_PAGE_SIZE,
    build_result_disclosure,
    slim_api_result_for_llm,
)


def _result(total, n_rows):
    return {"ok": True, "data": {"total": total, "rows": [{"uid": f"U-{i}"} for i in range(n_rows)]}}


# ------------------------------------------------------------------- capping


def test_a_capped_result_is_flagged_with_both_numbers():
    """The task 797 shape: 2,057 matched, 1,000 came back."""
    d = build_result_disclosure(_result(2057, 1000), {"queryParameters": {"page_size": 1000}})

    assert d["result_capped"] is True
    assert d["total_matching"] == 2057
    assert d["rows_returned"] == 1000
    assert d["page_size"] == 1000


def test_a_complete_result_is_not_flagged():
    d = build_result_disclosure(_result(12, 12), {"queryParameters": {"page_size": 1000}})

    assert "result_capped" not in d
    assert d["rows_returned"] == 12


def test_a_zero_result_is_not_flagged_as_capped():
    d = build_result_disclosure(_result(0, 0), {})
    assert "result_capped" not in d
    assert d["rows_returned"] == 0


def test_the_implicit_page_size_default_is_reported():
    """The cap is invisible precisely because nobody sets page_size explicitly."""
    d = build_result_disclosure(_result(2057, 1000), {"queryParameters": {}})
    assert d["page_size"] == DEFAULT_API_PAGE_SIZE


def test_the_counts_are_computed_from_the_full_result_not_the_preview():
    """
    slim_api_result_for_llm keeps 5 rows. That IS a cap and is flagged as one, but the
    numbers must describe the real result: 12 found, 5 shown — never "5 found".
    """
    slim = slim_api_result_for_llm(_result(12, 12), api_plan={"queryParameters": {}})

    assert slim["rows_returned"] == 12
    assert slim["total_matching"] == 12
    assert slim["preview_rows"] == 5
    assert slim["result_capped"] is True
    assert len(slim["data"]["rows"]) == 5


def test_a_genuinely_capped_result_survives_slimming():
    slim = slim_api_result_for_llm(_result(2057, 1000), api_plan={"queryParameters": {}})

    assert slim["result_capped"] is True
    assert slim["total_matching"] == 2057
    assert slim["rows_returned"] == 1000


# -------------------------------------------------------------- substitution


def test_a_substituted_search_is_disclosed():
    plan = {
        "queryParameters": {},
        "retry_substituted_search": {
            "original": "NHP-220524FLY-1-PUB NHP-220524FLY-2-PUB",
            "used": "220524FLY",
            "label": "SINGLE",
        },
    }

    d = build_result_disclosure(_result(6, 6), plan)

    assert d["search_text_substituted"]["used"] == "220524FLY"
    assert d["search_text_substituted"]["original"].startswith("NHP-220524FLY-1-PUB")


def test_no_substitution_key_when_the_search_was_the_users_own():
    d = build_result_disclosure(_result(6, 6), {"queryParameters": {}})
    assert "search_text_substituted" not in d


def test_substitution_survives_slimming():
    plan = {"queryParameters": {},
            "retry_substituted_search": {"original": "a b", "used": "a", "label": "SINGLE"}}

    assert slim_api_result_for_llm(_result(6, 6), api_plan=plan)["search_text_substituted"]["used"] == "a"


# ---------------------------------------------------------------- back-compat


def test_omitting_the_plan_preserves_the_previous_behaviour():
    """api_plan is optional; existing callers must be unaffected apart from row counts."""
    slim = slim_api_result_for_llm(_result(12, 12))

    assert "page_size" not in slim
    assert "search_text_substituted" not in slim
    assert slim["data"]["total"] == 12


def test_a_non_list_payload_does_not_crash():
    assert build_result_disclosure({"ok": True, "data": {"total": 5}}, {}) == {}
    assert build_result_disclosure({"ok": False, "error": "boom"}, {}) == {}
    assert build_result_disclosure(None, None) == {}


# --------------------------------------------------------------------------- #
# T1.12 — the assay count
#
# Task 833: api_result_meta.row_count was 324, the reply said "You have access to 5
# assays" and listed the 5 previewed rows. Diagnosed as a code path, not
# summarisation nondeterminism: /nextseek_api/assays/ is a SEEK JSON:API passthrough
# ({"data": [...], "jsonapi":..., "links":..., "meta":...}) that carries no total
# anywhere, and slim_api_result_for_llm trims data["data"] to max_rows=5. The chatter
# was left holding 5 rows and no other number, so "5" was the only answer available.
#
# Note 5 is exactly max_rows — the reported count equalled the preview length.
# --------------------------------------------------------------------------- #

def _assays_jsonapi(n):
    """The real endpoint shape: a JSON:API body with no total field of any kind."""
    return {
        "ok": True,
        "status_code": 200,
        "data": {
            "data": [{"id": str(i), "type": "assays",
                      "attributes": {"title": f"Assay {i}"}} for i in range(n)],
            "jsonapi": {"version": "1.0"},
            "links": {"self": "/assays?page[number]=1&page[size]=100"},
            "meta": {"base_url": "http://seek", "api_version": "v1"},
        },
    }


def test_the_assays_payload_really_has_no_total():
    """Pins the premise of the diagnosis: nothing in the body says 324."""
    body = _assays_jsonapi(324)["data"]
    assert "total" not in body and "total_samples" not in body
    assert "total" not in body["meta"] and "count" not in body["meta"]


def test_the_full_assay_count_survives_slimming():
    slim = slim_api_result_for_llm(_assays_jsonapi(324), api_plan={"queryParameters": {}})

    assert slim["rows_returned"] == 324, "the chatter still cannot see how many there are"
    assert slim["preview_rows"] == 5
    assert slim["result_capped"] is True
    assert slim["total_matching"] == 324
    # And the preview really is trimmed, so this is not just a fat payload.
    assert len(slim["data"]["data"]) == 5


def test_a_short_list_is_not_flagged_as_capped():
    slim = slim_api_result_for_llm(_assays_jsonapi(3), api_plan={"queryParameters": {}})

    assert slim["rows_returned"] == 3
    assert "result_capped" not in slim
    assert "preview_rows" not in slim


def test_preview_rows_is_absent_when_nothing_was_trimmed():
    slim = slim_api_result_for_llm(_result(5, 5), api_plan={"queryParameters": {}})
    assert "preview_rows" not in slim


def test_both_cap_reasons_can_apply_at_once():
    """API paging AND context trimming: total_matching must stay the API's total."""
    slim = slim_api_result_for_llm(_result(2057, 1000), api_plan={"queryParameters": {}})

    assert slim["total_matching"] == 2057, "the API total must win over the row count"
    assert slim["rows_returned"] == 1000
    assert slim["preview_rows"] == 5
    assert slim["result_capped"] is True
