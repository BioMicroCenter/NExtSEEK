"""R4 U3b: the fixed-shape REST requests are built in code, the people list is flagged and tabled whole, and a
resolved UID's stored spelling replaces the typed one in the parser's filters. Made-up entities only."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat_nextseek.agents import api as api_mod
from chat_nextseek.helpers.results import slim_api_result_for_llm
from chat_nextseek.helpers.uid_check import UidCheck, plan_with_stored_uids
from chat_nextseek.schemas import ParserFilters, ParserPlan

PEOPLE, SOPS, RETRIEVE = "/nextseek_api/people/", "/nextseek_api/sops/", "/nextseek_api/samples/retrieve/"
GRAPH_SEARCH = "/nextseek_api/samples/graph_search/"


class _NoLLM:
    """A config whose model path must not be reached for a kept endpoint."""

    MIN_API_ENDPOINTS: list = []

    def get_schema_for_endpoint(self, endpoint):  # pragma: no cover - reached only if the code path is wrong
        raise AssertionError("the model path was reached")


def build(endpoint, intent="x", **filters):
    return api_mod.api_agent_build_request(_NoLLM(), {"target_endpoint": endpoint, "intent_summary": intent, "filters": filters})


@pytest.mark.parametrize("uids", [["ZZZ-990101ABC-1-PUB"], ["QQQ-990202XYZ-4-PUB", "ZZZ-990101ABC-1-PUB"]])
def test_retrieve_is_a_post_of_the_named_uids_with_no_model_call(uids):
    plan = build(RETRIEVE, uids=uids)
    assert (plan.endpoint, plan.method, plan.requestBody) == (RETRIEVE, "POST", {"identifiers": uids})


def test_people_is_an_empty_get_with_no_model_call():
    plan = build(PEOPLE)
    assert (plan.endpoint, plan.method, plan.requestBody) == (PEOPLE, "GET", {})


@pytest.mark.parametrize("intent,filters,sop_id", [("Download SOP 142", {}, "142"), ("the file", {"keywords": ["9001"]}, "9001")])
def test_an_sop_download_reads_the_one_record(intent, filters, sop_id):
    plan = build(SOPS, intent, **filters)
    assert (plan.endpoint, plan.method) == (f"{SOPS}{sop_id}/", "GET")


def test_an_sop_with_no_id_is_not_a_request():
    assert build(SOPS, "what SOPs exist").endpoint is None


def test_a_retrieve_with_no_uids_and_graph_search_still_use_the_model(monkeypatch):
    with pytest.raises(AssertionError):
        build(RETRIEVE)
    with pytest.raises(AssertionError):
        build(GRAPH_SEARCH)


def _people(n):
    return {"ok": True, "data": {"data": [{"id": str(i), "type": "people", "attributes": {"title": f"Quill {i}"}} for i in range(n)]}}


def test_a_people_list_is_flagged_full_with_its_total_whatever_the_preview():
    slim = slim_api_result_for_llm(_people(37), api_plan={"endpoint": PEOPLE})
    assert slim["full_list_attached"] is True and slim["rows_returned"] == 37


def test_a_sample_result_is_not_flagged_and_an_sop_record_lists_its_file():
    assert "full_list_attached" not in slim_api_result_for_llm(_people(3), api_plan={"endpoint": RETRIEVE})
    record = {"ok": True, "data": {"data": {"id": "142", "attributes": {"content_blobs": [
        {"original_filename": "zeta_protocol.pdf", "link": "http://x/sops/142/content_blobs/9"}]}}}}
    slim = slim_api_result_for_llm(record, api_plan={"endpoint": f"{SOPS}142/"})
    assert slim["download_links"] == [{"file": "zeta_protocol.pdf", "link": "http://x/sops/142/content_blobs/9"}]


def test_the_people_table_holds_every_row_even_past_the_inline_cap():
    from nextseek_api.assistant.excel_export import MAX_INLINE_ROWS, extract_table_artifacts

    n = MAX_INLINE_ROWS + 50
    bundle = {"mode": "new_search", "endpoint": PEOPLE, "api_result_full": _people(n)}
    (table,) = extract_table_artifacts(bundle)
    assert table["total_rows"] == n and len(table["rows"]) == n and table["rows"][3] == ["3", "Quill 3"]
    assert extract_table_artifacts({"mode": "new_search", "endpoint": RETRIEVE, "api_result_full": _people(3)}) == []


def test_the_chatter_is_told_a_full_list_is_in_the_table():
    from pathlib import Path

    text = (Path(api_mod.__file__).resolve().parent.parent / "prompts" / "chatter_agent.txt").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "**`full_list_attached: true`** means every row is in the table under your reply." in flat
    assert "Never write \"the first N\"" in flat
    assert flat.index("full_list_attached") < flat.index("`result_capped: true`")


@pytest.mark.parametrize("asked,stored", [("MUS-990101ABC-23", "MUS-990101ABC-23-PUB"), ("D.SEQ-990202XYZ-67", "D.SEQ-990202XYZ-67-PUB")])
def test_the_parser_filters_carry_the_stored_spelling_after_the_uid_check(asked, stored):
    plan = ParserPlan(mode="graph_query", filters=ParserFilters(uids=[asked, "ZZZ-990101ABC-1-PUB"]))
    out = plan_with_stored_uids(plan, [UidCheck(asked=asked, stored=stored), UidCheck(asked="ZZZ-990101ABC-1-PUB", stored="ZZZ-990101ABC-1-PUB")])
    assert out.filters.uids == [stored, "ZZZ-990101ABC-1-PUB"]
    assert plan.filters.uids[0] == asked  # the input is not mutated


def test_an_unresolved_or_unchecked_uid_leaves_the_plan_alone():
    plan = ParserPlan(mode="graph_query", filters=ParserFilters(uids=["ZZZ-990101ABC-9"]))
    assert plan_with_stored_uids(plan, [UidCheck(asked="ZZZ-990101ABC-9", stored=None)]) is plan
    assert plan_with_stored_uids(plan, None) is plan
