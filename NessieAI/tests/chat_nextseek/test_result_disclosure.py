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


# ------------------------------------------- api_result_meta truncation (2026-07-28)
#
# T1.10 put result_capped/total_matching into the SLIMMED LLM payload, so the chatter
# is told. `api_result_meta` — the field the nessie harness asserts on — never got an
# equivalent, so no criterion could see a REST cap and none ever has. In the seed-0 run
# `advanced.find_me_mice` reported row_count exactly 1000 (the hard-coded page size)
# and scored green. That is the same silence T1.10 fixed, one layer up.

from chat_nextseek.helpers.results import api_result_meta_truncation  # noqa: E402


def test_meta_reports_truncation_for_a_capped_result():
    """The seed-0 find_me_mice shape: 1,179 matched, 1,000 came back."""
    meta = api_result_meta_truncation(_result(1179, 1000), {"queryParameters": {"page_size": 1000}})

    assert meta["truncated"] is True
    assert meta["total_matching"] == 1179


def test_meta_reports_no_truncation_for_a_complete_result():
    meta = api_result_meta_truncation(_result(12, 12), {"queryParameters": {"page_size": 1000}})

    assert meta["truncated"] is False
    assert meta["total_matching"] is None


def test_meta_reports_no_truncation_for_an_honest_zero():
    """Zero rows is complete, not capped."""
    meta = api_result_meta_truncation(_result(0, 0), {})

    assert meta["truncated"] is False


def test_meta_is_safe_on_a_result_with_no_total():
    """SEEK JSON:API passthroughs carry no total; absence must not read as capped."""
    meta = api_result_meta_truncation({"ok": True, "data": {"rows": [{"uid": "U-1"}]}}, {})

    assert meta["truncated"] is False


def test_api_result_meta_carries_row_count_and_truncation_together():
    """The shape orchestrator.py writes into debug_payload['api_result_meta'].

    Extracted so the truncation fields have a seam that can be tested without
    standing up the orchestrator; wiring it inline is what left the graph side's
    equivalent untested for two waves.
    """
    from chat_nextseek.helpers.results import build_api_result_meta

    full = {"ok": True, "status_code": 200,
            "url": "http://127.0.0.1:8000/nextseek_api/samples/advanced_search/",
            "data": {"total": 1179, "rows": [{"uid": f"MUS-{i}"} for i in range(1000)]}}
    meta = build_api_result_meta(full, {"queryParameters": {"page_size": 1000}}, bundle_id=1)

    assert meta["ok"] is True
    assert meta["status_code"] == 200
    assert meta["bundle_id"] == 1
    assert meta["row_count"] == 1000
    # the two fields no criterion could see before
    assert meta["truncated"] is True
    assert meta["total_matching"] == 1179


def test_api_result_meta_on_a_complete_result_is_not_truncated():
    from chat_nextseek.helpers.results import build_api_result_meta

    full = {"ok": True, "status_code": 200, "url": "u",
            "data": {"total": 28, "rows": [{"uid": f"AB-{i}"} for i in range(28)]}}
    meta = build_api_result_meta(full, {}, bundle_id=2)

    assert meta["row_count"] == 28
    assert meta["truncated"] is False


# --------------------------------------------------------------------------- #
# The grouped-by-sample-type shape (`POST samples/retrieve/`)
#
# `AdminSampleRetrieveResponse` (nextseek_api/models.py) is
# `{total_samples, total_sample_types, total_children, failed_uids,
#   data: [{sample_type, n_samples, samples: [...]}]}`.
#
# So `data["data"]` IS a list and `preview_key` resolves to "data" — but the list
# holds GROUPS, not records. Measured on a faithful 101-sample / 3-type payload:
#
#   * `max_rows=5` trimmed 3 groups to 3 groups and changed nothing, so the
#     >max_chars branch fired and the "slimmed" payload came out LARGER than the
#     raw one (28,232 chars in, 28,342 out). The chatter's 5,000-char budget was
#     exceeded by more than five times, silently.
#   * `rows_returned` was 3 — the number of sample TYPES — while `total_matching`
#     was 1,904. The system prompt tells the chatter `rows_returned` "is how many
#     records the search actually found", so it was handed a number that means
#     nothing and told it was the answer.
#
# This is the endpoint B7 ran on: wesselr 462 asked for the PAT samples under
# MDL-250912LAU-1, and the reply reported 1,904 lineage records with none of them
# PAT. The per-type breakdown that would have let the reply say "40 of them are
# PAT" was in the payload and was buried under 28 KB of sample metadata.
# --------------------------------------------------------------------------- #

def _retrieve_grouped(groups, total_samples=None):
    """A faithful samples/retrieve/ body, wrapped as the REST tool wraps it."""
    body_groups = []
    for sample_type, n in groups:
        body_groups.append({
            "sample_type": sample_type,
            "n_samples": n,
            "samples": [
                {"id": str(i), "uuid": f"{sample_type}-2509{i:02d}LAU-{i}",
                 "sample_type_id": 7,
                 "metadata": {"Scientist": "wesselr", "Organ": "Lung",
                              "Notes": "x" * 120, "Treatment": "vehicle"}}
                for i in range(n)
            ],
        })
    n_total = total_samples if total_samples is not None else sum(n for _, n in groups)
    return {
        "ok": True, "status_code": 200, "method": "POST",
        "url": "http://127.0.0.1:8000/nextseek_api/samples/retrieve/",
        "data": {
            "total_samples": n_total,
            "total_sample_types": len(groups),
            "total_children": max(0, n_total - 1),
            "failed_uids": 0,
            "data": body_groups,
        },
    }


def test_a_grouped_result_is_counted_in_records_not_groups():
    """`rows_returned` must mean samples. It reported the number of sample types."""
    full = _retrieve_grouped([("MDL", 1), ("PAT", 40), ("D.SEQ", 60)], total_samples=1904)

    d = build_result_disclosure(full, {"queryParameters": {}})

    assert d["rows_returned"] == 101
    assert d["total_matching"] == 1904


def test_a_grouped_result_actually_fits_the_chatter_budget():
    """The size cap has to bind. It did not: 28,232 chars in, 28,342 chars out."""
    import json as _json

    full = _retrieve_grouped([("MDL", 1), ("PAT", 40), ("D.SEQ", 60)], total_samples=1904)
    raw_len = len(_json.dumps(full))
    slim = slim_api_result_for_llm(full, api_plan={"queryParameters": {}})

    assert raw_len > 5000, "the fixture must be big enough to trip the cap"
    assert len(_json.dumps(slim)) <= 5000


def test_a_grouped_result_keeps_the_per_type_breakdown():
    """B7's missing sentence: 1,904 records, 40 of them PAT."""
    full = _retrieve_grouped([("MDL", 1), ("PAT", 40), ("D.SEQ", 60)], total_samples=1904)

    slim = slim_api_result_for_llm(full, api_plan={"queryParameters": {}})

    assert slim["data"]["samples_by_type"] == {"MDL": 1, "PAT": 40, "D.SEQ": 60}
    assert slim["data"]["total_samples"] == 1904


def test_a_grouped_result_previews_records_not_groups():
    """B8's fix: every previewed record names the type it came from."""
    full = _retrieve_grouped([("MDL", 1), ("PAT", 40), ("D.SEQ", 60)], total_samples=1904)

    slim = slim_api_result_for_llm(full, api_plan={"queryParameters": {}})
    preview = slim["data"]["samples_preview"]

    assert 0 < len(preview) <= 5
    assert all(isinstance(row, dict) and row.get("sample_type") for row in preview)
    assert {row["uuid"] for row in preview} & {"MDL-250900LAU-0"}


def test_a_small_grouped_result_is_not_flagged_as_capped():
    full = _retrieve_grouped([("MDL", 1), ("PAT", 2)])

    slim = slim_api_result_for_llm(full, api_plan={"queryParameters": {}})

    assert slim["rows_returned"] == 3
    assert slim["data"]["samples_by_type"] == {"MDL": 1, "PAT": 2}
    assert "result_capped" not in slim


def test_a_grouped_row_count_reaches_api_result_meta():
    """The harness asserts on row_count, and it read the sample-type count."""
    from chat_nextseek.helpers.results import build_api_result_meta

    full = _retrieve_grouped([("MDL", 1), ("PAT", 40), ("D.SEQ", 60)], total_samples=1904)
    meta = build_api_result_meta(full, {"queryParameters": {}}, bundle_id=6)

    assert meta["row_count"] == 101
    assert meta["total_matching"] == 1904
    assert meta["truncated"] is True


def test_an_oversized_flat_preview_is_shrunk_until_it_fits():
    """The >max_chars branch never reduced anything; it only relabelled the keys."""
    import json as _json

    fat = {"ok": True, "data": {"total": 9,
                                "rows": [{"uid": f"U-{i}", "blob": "y" * 3000} for i in range(9)]}}
    slim = slim_api_result_for_llm(fat, api_plan={"queryParameters": {}})

    assert len(_json.dumps(slim)) <= 5000
    assert slim["rows_returned"] == 9


def test_the_oversize_branch_keeps_the_truncation_key_name_it_always_had():
    """`rows_truncated`, not `rows_preview_truncated`: the label is not the key."""
    fat = {"ok": True, "data": {"total": 9,
                               "rows": [{"uid": f"U-{i}", "blob": "y" * 3000} for i in range(9)]}}

    slim = slim_api_result_for_llm(fat, api_plan={"queryParameters": {}})

    assert "rows_truncated" in slim["data"]
    assert "rows_preview_truncated" not in slim["data"]


def test_a_grouped_preview_shrunk_for_size_says_it_was_truncated():
    """The flag is set before the shrink runs, so a shrink could leave it False.

    Silently dropping records the payload claims are all of them is exactly the
    class of quiet lie the disclosure flags exist to close.
    """
    import json as _json

    # Three samples in total, so `max_rows` trims nothing -- only the size cap can.
    full = {"ok": True, "status_code": 200,
            "data": {"total_samples": 3, "total_sample_types": 1, "failed_uids": 0,
                     "data": [{"sample_type": "PAT", "n_samples": 3,
                               "samples": [{"uuid": f"PAT-{i}", "metadata": {"Notes": "z" * 3000}}
                                           for i in range(3)]}]}}

    slim = slim_api_result_for_llm(full, api_plan={"queryParameters": {}})

    assert len(_json.dumps(slim)) <= 5000
    assert len(slim["data"]["samples_preview"]) < 3
    assert slim["data"]["samples_truncated"] is True
