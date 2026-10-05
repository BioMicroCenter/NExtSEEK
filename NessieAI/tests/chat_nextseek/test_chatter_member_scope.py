"""G2: a non-admin's zero must be explained by the caller's project scope.

Synthetic names only: "Alpha" is a catalog project the caller is not in, "Own One"/"Own Two" are the caller's.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.graph_scope import GraphScope, with_scope
from chat_nextseek.helpers.results import slim_api_result_for_llm
from chat_nextseek.schemas.entity import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan

PROMPTS = Path(chatter_mod.__file__).resolve().parents[1] / "prompts"

CATALOG = [
    {"name": "Alpha", "alternative_names": ["Alpha Center"], "entity_type": "project", "project_id": 7,
     "parent_project": None},
    {"name": "Alpha Inv", "alternative_names": ["Alpha"], "entity_type": "investigation", "project_id": 7,
     "parent_project": "Alpha"},
    {"name": "Own One", "alternative_names": [], "entity_type": "project", "project_id": 13, "parent_project": None},
    {"name": "PUBLISHED", "alternative_names": ["Published Data", "Published"], "entity_type": "project",
     "project_id": 6, "parent_project": None},
]


class _StubConfig:
    CHATTER_SYSTEM_PROMPT = "SYSTEM PROMPT"
    LOG_DIR = ""
    FULL_PROJECTS = CATALOG
    FULL_PROJECTS_MAP = {r["name"]: r for r in CATALOG if r["entity_type"] == "project"}
    FULL_INVESTIGATIONS_MAP = {r["name"]: r for r in CATALOG if r["entity_type"] == "investigation"}
    CALLER = {"username": "member", "is_admin": False, "project_count": 2,
              "projects": [{"id": 13, "name": "Own One"}, {"id": 14, "name": "Own Two"}]}

    def get_agent_model(self, agent_label):
        return (object(), "stub-model", None)


def _member():
    return with_scope(_StubConfig(), GraphScope.for_projects([13, 14]))


def _admin():
    cfg = with_scope(_StubConfig(), GraphScope.admin("test"))
    cfg.CALLER = {"username": "admin", "is_admin": True, "projects": [], "project_count": 0}
    return cfg


@pytest.fixture
def captured(monkeypatch):
    box: dict = {}

    def _fake(config, *, messages, model_name, client, agent_label, temperature=0, thinking_budget=None,
              usage_label=None):
        box["user_content"] = messages[-1]["content"]
        return "stub reply"

    monkeypatch.setattr(chatter_mod, "call_llm_text", _fake)
    return box


def _graph_turn(config, projects, n, question="How many samples are in the Alpha project?"):
    meta = {"decision": "admin"} if config.CALLER["is_admin"] else {"decision": "proven", "project_ids": [13, 14]}
    chatter_mod.chatter_agent_answer(
        config, question,
        EntityAgentOutput(projects=projects).model_dump(), ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (s:Sample)-[:IN_STUDY]->(:Study)-[:IN_INVESTIGATION]->(i:Investigation) "
                              "WHERE i.title = $t RETURN count(DISTINCT s) AS n",
                    "parameters": {"t": "Alpha"}, "explanation": "counts"},
        graph_result={"ok": True, "count": 1, "total": 1, "data": [{"n": n}], "scope": meta},
        log_dir="",
    )


def test_a_members_zero_names_the_projects_the_search_covered(captured):
    _graph_turn(_member(), [], 0)
    text = captured["user_content"]
    assert "Own One" in text and "Own Two" in text


def test_a_members_zero_on_a_catalog_project_outside_scope_says_so(captured):
    _graph_turn(_member(), ["Alpha"], 0)
    text = captured["user_content"]
    assert "Alpha" in text.split("What the query actually did", 1)[1]
    assert "not a member" in text


def test_a_project_the_question_never_writes_gets_no_foreign_note(captured):
    # The entity step reads "Published Data" into a -PUB UID; the question names no project.
    chatter_mod.chatter_agent_answer(
        _member(), "Show me the record for NHP-220630FLY-1-PUB",
        EntityAgentOutput(projects=["PUBLISHED"], uids=["NHP-220630FLY-1-PUB"]).model_dump(),
        ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (s:Sample {uuid: $uid}) RETURN s.uuid AS uuid, s.type AS type",
                    "parameters": {"uid": "NHP-220630FLY-1-PUB"}, "explanation": "one sample"},
        graph_result={"ok": True, "count": 1, "total": 1, "data": [{"uuid": "NHP-220630FLY-1-PUB", "type": "NHP"}],
                      "scope": {"decision": "proven", "project_ids": [13, 14]}},
        log_dir="",
    )
    assert "not a member" not in captured["user_content"]


def test_two_names_of_one_foreign_project_give_one_note(captured):
    _graph_turn(_member(), ["Alpha", "Alpha Center"], 0, question="How many samples are in Alpha (Alpha Center)?")
    assert captured["user_content"].count("not a member") == 1


def test_an_admin_zero_carries_no_scope_note(captured):
    _graph_turn(_admin(), ["Alpha"], 0)
    assert "not a member" not in captured["user_content"]
    assert "Own One" not in captured["user_content"]


def test_a_members_answered_count_in_own_project_carries_no_scope_note(captured):
    _graph_turn(_member(), ["Own One"], 120)
    assert "not a member" not in captured["user_content"]
    assert "Own Two" not in captured["user_content"]


def _rest_turn(config, question, endpoint, body):
    api_plan = {"endpoint": endpoint, "method": "GET", "requestBody": {}, "queryParameters": {}}
    api_full = {"ok": True, "status_code": 200, "data": body}
    chatter_mod.chatter_agent_answer(
        config, question, EntityAgentOutput().model_dump(),
        ParserPlan(mode="new_search", target_endpoint=endpoint).model_dump(),
        api_plan, slim_api_result_for_llm(api_full, api_plan=api_plan), api_full, None, log_dir="",
    )


_SOP_RECORD = {"data": {"id": "142", "type": "sops", "attributes": {
    "title": "P.X-1_Protocol.docx",
    "content_blobs": [{"original_filename": "p.docx", "link": "https://seek.example/sops/142/content_blobs/9"}]}}}


@pytest.mark.parametrize("question, endpoint, body", [
    ("Download SOP 142", "/nextseek_api/sops/142/", _SOP_RECORD),
    ("List the registered users", "/nextseek_api/people/", {"data": []}),
])
def test_a_members_sop_record_or_people_list_carries_no_scope_zero_note(captured, question, endpoint, body):
    _rest_turn(_member(), question, endpoint, body)
    assert "not an admin" not in captured["user_content"]


def test_a_members_uid_retrieve_with_no_rows_names_the_projects_it_covered(captured):
    _rest_turn(_member(), "Show me the record for NHP-X-1", "/nextseek_api/samples/retrieve/",
               {"total_samples": 0, "total_sample_types": 0, "total_children": 0, "failed_uids": ["NHP-X-1"],
                "data": [], "lineage_complete": True})
    text = captured["user_content"]
    assert "not an admin" in text and "Own One" in text


def test_the_graph_zero_rule_no_longer_invites_a_spelling_guess_under_scope():
    text = (PROMPTS / "chatter_agent.txt").read_text()
    assert "own projects" in text


def test_the_system_agent_is_told_searches_are_limited_to_caller_projects():
    text = " ".join((PROMPTS / "system_agent.txt").read_text().split())
    assert "limits every sample search, graph query" in text
