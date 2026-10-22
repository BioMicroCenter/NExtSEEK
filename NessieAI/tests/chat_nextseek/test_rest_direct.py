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


@pytest.mark.parametrize("intent,ref,path", [
    ("the SOP titled Zeta Fixation Protocol", "Zeta Fixation Protocol", "Zeta%20Fixation%20Protocol"),
    ("the Quill SOP", "Quill Staining Protocol", "Quill%20Staining%20Protocol"),
    ("Get me the file for SOP #142", "142", "142"),
])
def test_an_sop_download_reads_the_record_the_parser_named_by_id_or_title(intent, ref, path):
    plan = build(SOPS, intent, keywords=[ref])
    assert (plan.endpoint, plan.method) == (f"{SOPS}{path}/", "GET")


def test_the_sop_id_the_parser_found_reaches_the_request():
    """Review F6: the parser found "SOP #142" in the question while the intent said "SOP with ID 142"."""
    from chat_nextseek.agents.parser import _apply_parser_guardrails
    from chat_nextseek.schemas import EntityAgentOutput

    parsed = ParserPlan(mode="new_search", target_endpoint=SOPS, intent_summary="SOP with ID 142",
                        filters=ParserFilters(), resolved=EntityAgentOutput())
    routed = _apply_parser_guardrails("Get me the file for SOP #142", parsed)
    assert api_mod.api_agent_build_request(_NoLLM(), routed).endpoint == f"{SOPS}142/"


def test_an_sop_with_no_id_is_not_a_request():
    assert build(SOPS, "what SOPs exist").endpoint is None


def test_a_retrieve_with_no_uids_and_graph_search_still_use_the_model(monkeypatch):
    with pytest.raises(AssertionError):
        build(RETRIEVE)
    with pytest.raises(AssertionError):
        build(GRAPH_SEARCH)


def _people(n):
    return {"ok": True, "data": {"data": [{"id": str(i), "type": "people", "attributes": {"title": f"Quill {i}"}} for i in range(n)]}}


@pytest.mark.parametrize("keywords,terms", [(["quill_j"], ["quill_j"]), (["user Jane Quill"], ["jane quill"]),
                                            (["registered users"], [])])
def test_the_people_request_carries_the_names_to_match(keywords, terms):
    plan = build(PEOPLE, keywords=keywords)
    assert (plan.endpoint, plan.method, plan.requestBody, plan.queryParameters) == (PEOPLE, "GET", {}, {})
    assert plan.model_dump().get("match_terms", []) == terms


ACCOUNTS = {"ok": True, "data": {"data": [{"id": "1", "attributes": {"title": "Quill, Jane"}},
                                          {"id": "2", "attributes": {"title": "Zeta, Omar"}}]}}


@pytest.mark.parametrize("terms,rows", [(["quill"], [{"id": "1", "title": "Quill, Jane"}]),
                                        (["omar zeta"], [{"id": "2", "title": "Zeta, Omar"}]),
                                        (["fixation"], [])])
def test_the_people_list_marks_the_accounts_whose_name_matches(terms, rows):
    """Review F8: the names are matched in code against the whole list, which stays attached."""
    slim = slim_api_result_for_llm(ACCOUNTS, api_plan={"endpoint": PEOPLE, "match_terms": terms})
    assert slim["matching_rows"] == rows and slim["match_terms"] == terms
    assert slim["full_list_attached"] is True and slim["rows_returned"] == 2


def test_the_people_list_with_no_names_to_match_has_no_matching_rows():
    assert "matching_rows" not in slim_api_result_for_llm(ACCOUNTS, api_plan={"endpoint": PEOPLE})


def test_the_chatter_answers_a_name_search_from_matching_rows():
    from pathlib import Path

    text = (Path(api_mod.__file__).resolve().parent.parent / "prompts" / "chatter_agent.txt").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert ("**`matching_rows`** lists the registered accounts whose name contains what the user asked for "
            "(`match_terms`). Answer with those rows.") in flat
    assert flat.index("full_list_attached") < flat.index("matching_rows") < flat.index("`result_capped: true`")


def test_a_people_list_is_flagged_full_with_its_total_whatever_the_preview():
    slim = slim_api_result_for_llm(_people(37), api_plan={"endpoint": PEOPLE})
    assert slim["full_list_attached"] is True and slim["rows_returned"] == 37


def test_a_sample_result_is_not_flagged_and_an_sop_record_lists_its_file():
    assert "full_list_attached" not in slim_api_result_for_llm(_people(3), api_plan={"endpoint": RETRIEVE})
    record = {"ok": True, "data": {"data": {"id": "142", "attributes": {"content_blobs": [
        {"original_filename": "zeta_protocol.pdf", "link": "http://x/sops/142/content_blobs/9"}]}}}}
    slim = slim_api_result_for_llm(record, api_plan={"endpoint": f"{SOPS}142/"})
    assert slim["download_links"] == [{"file": "zeta_protocol.pdf", "link": "http://x/sops/142/content_blobs/9/download"}]


def test_a_link_that_is_not_a_content_blob_is_passed_through():
    """Review N8: SEEK serves the file at <blob link>/download; any other link is left as the record states it."""
    record = {"ok": True, "data": {"data": {"id": "9001", "attributes": {"content_blobs": [
        {"original_filename": "quill.pdf", "link": "http://x/files/a.pdf"}]}}}}
    slim = slim_api_result_for_llm(record, api_plan={"endpoint": f"{SOPS}9001/"})
    assert slim["download_links"] == [{"file": "quill.pdf", "link": "http://x/files/a.pdf"}]


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


# ------------------------------------------------------------------ fix round 4.1: kind words are not names
@pytest.mark.parametrize("keywords,terms", [(["researchers", "registered"], []), (["registered users"], []),
                                            (["scientists"], []), (["members"], []), (["quill_j"], ["quill_j"])])
def test_a_word_naming_the_kind_listed_is_never_a_name_to_match(keywords, terms):
    assert build(PEOPLE, keywords=keywords).model_dump().get("match_terms", []) == terms


def _people_scope(question, entity_keywords, match_terms=()):
    from chat_nextseek.helpers.query_scope import describe_query_scope

    return describe_query_scope(
        entity_result={"keywords": list(entity_keywords)},
        parser_plan={"mode": "new_search", "target_endpoint": PEOPLE, "filters": {"keywords": []}},
        api_plan={"endpoint": PEOPLE, "method": "GET", "requestBody": {}, "queryParameters": {},
                  "match_terms": list(match_terms)},
        user_query=question)


@pytest.mark.parametrize("question,keywords", [("Who are the researchers registered?", ["researchers"]),
                                               ("Who are the researchers registered in NExtSEEK?", ["researchers", "registered"]),
                                               ("List the registered users", ["registered users"])])
def test_the_whole_people_list_drops_no_constraint(question, keywords):
    """The dev smoke's false caveat: a kind word the entity step resolved was reported as a filter not applied."""
    assert _people_scope(question, keywords).not_applied == []


def test_a_name_matched_in_code_counts_as_applied():
    scope = _people_scope("Is there a user called quill_j?", ["quill_j"], match_terms=["quill_j"])
    assert scope.not_applied == [] and any("quill_j" in label for label in scope.applied)


def test_the_chatter_gets_no_not_applied_line_and_no_kind_keyword_for_the_people_list(monkeypatch):
    from chat_nextseek.agents import chatter as chatter_mod

    box = {}

    class _Config:
        CHATTER_SYSTEM_PROMPT, LOG_DIR = "SYSTEM PROMPT", ""

        def get_agent_model(self, agent_label):
            return (object(), "stub-model", None)

    def _fake(config, *, messages, **_):
        box["user"] = messages[-1]["content"]
        return "stub reply"

    monkeypatch.setattr(chatter_mod, "call_llm_text", _fake)
    rows = {"ok": True, "data": {"data": [{"id": "1", "type": "people", "attributes": {"title": "Quill, Jane"}}]}}
    api_plan = {"endpoint": PEOPLE, "method": "GET", "requestBody": {}, "queryParameters": {}, "match_terms": []}
    chatter_mod.chatter_agent_answer(
        _Config(), "Who are the researchers registered in NExtSEEK?", {"keywords": ["researchers"]},
        {"mode": "new_search", "target_endpoint": PEOPLE, "filters": {"keywords": []}}, api_plan,
        slim_api_result_for_llm(rows, api_plan=api_plan), rows, log_dir="")
    assert "NOT APPLIED" not in box["user"] and "- Keywords: (none)" in box["user"]


def test_the_sop_list_on_the_graph_drops_no_constraint():
    """Item 2's sibling: the unfiltered protocol list is not reported as dropping the word SOP."""
    from chat_nextseek.helpers.query_scope import describe_query_scope

    scope = describe_query_scope(
        entity_result={"keywords": ["SOP"]}, parser_plan={"mode": "graph_query", "filters": {"keywords": []}},
        graph_plan={"cypher": "MATCH (c:Sample)-[r:DERIVED_FROM]->(:Sample) WHERE r.protocol_title IS NOT NULL "
                              "RETURN DISTINCT r.protocol_title AS protocol_title", "parameters": {}},
        user_query="What SOPs are on file?")
    assert scope.not_applied == []
