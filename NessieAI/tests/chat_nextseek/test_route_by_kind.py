"""R4 routing: the parser may pick only three REST things, and each question goes to the engine that answers its KIND.

Made-up entities only (project Zeta, UIDs ZZZ-990101ABC-1-PUB and QQQ-990202XYZ-4-PUB).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chat_nextseek.agents.parser import (
    _apply_multi_parser_guardrails,
    _apply_parser_guardrails,
    _route_by_kind,
)
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.schemas import EntityAgentOutput, MultiParserPlan, ParserCandidate, ParserFilters, ParserPlan

CATALOG = Path(__file__).resolve().parents[2] / "chat_nextseek/src/chat_nextseek/context/min_api_endpoints_enriched.json"
ADMIN = SimpleNamespace(GRAPH_SCOPE=GraphScope.admin("test"))
MEMBER = SimpleNamespace(GRAPH_SCOPE=GraphScope.for_projects([7]))
ASSAYS, SAMPLE_TYPES, TREE = "/nextseek_api/assays/", "/nextseek_api/sample_types/", "/nextseek_api/sample-tree/ZZZ-990101ABC-1-PUB/tree/"
INVESTIGATIONS, PROJECTS = "/nextseek_api/investigations/", "/nextseek_api/projects/"
SOPS, PEOPLE, RETRIEVE = "/nextseek_api/sops/", "/nextseek_api/people/", "/nextseek_api/samples/retrieve/"


def plan(mode="new_search", endpoint=None, projects=(), **filters):
    return ParserPlan(mode=mode, target_endpoint=endpoint, intent_summary="x", filters=ParserFilters(**filters),
                      resolved=EntityAgentOutput(projects=list(projects)))


def route(q, p, config=ADMIN):
    return _apply_parser_guardrails(q, p, config=config)


@pytest.mark.parametrize("q", ["How many assay kinds have samples, and how many have none?",
                               "Which assay kinds were never run on any sample?"])
def test_assay_list_with_a_sample_predicate_goes_to_the_graph(q):
    assert route(q, plan(endpoint=ASSAYS)).mode == "graph_query"


@pytest.mark.parametrize("q", ["How many Raw sample types are there?", "How many Processed sample types does the catalog define?"])
def test_sample_type_list_count_goes_to_the_system_agent(q):
    out = route(q, plan(endpoint=SAMPLE_TYPES))
    assert out.mode == "system_question" and out.target_endpoint is None


@pytest.mark.parametrize("uid", ["ZZZ-990101ABC-1-PUB", "QQQ-990202XYZ-4-PUB"])
def test_tree_goes_to_the_graph_and_keeps_the_uid(uid):
    out = route(f"Show me the tree of {uid}", plan(endpoint=f"/nextseek_api/sample-tree/{uid}/tree/"))
    assert out.mode == "graph_query" and out.filters.uids == [uid]


def test_investigation_and_project_lists_go_to_the_graph():
    assert route("Which investigations can I see?", plan(endpoint=INVESTIGATIONS)).mode == "graph_query"
    assert route("List the projects", plan(endpoint=PROJECTS)).mode == "graph_query"


@pytest.mark.parametrize("q,mode", [("Which projects am I a member of?", "new_search"),
                                    ("Am I an admin, and which groups do I belong to?", "graph_query"),
                                    ("Who am I signed in as?", "system_question")])
def test_caller_questions_go_to_the_system_agent(q, mode):
    out = route(q, plan(mode, PROJECTS if mode == "new_search" else None))
    assert out.mode == "system_question" and out.target_endpoint is None


# Review F2: ordinary graph questions the caller and catalog-count rules used to capture. The review lists 13; the
# last two are more of the same shapes.
GRAPH_QUESTIONS = [
    "How many mice are in my projects?", "List the studies in my projects", "Which investigations are in my projects?",
    "What tissues do my projects hold?", "Which of my projects has the most mice?",
    "What sample types are in my projects?", "How many protocols do my projects use?",
    "How many assays were run in the Zeta project?", "How many assay kinds did project Zeta use?",
    "How many assays has ZZZ-990101ABC-1-PUB gone through?", "How many sample types does project Zeta have?",
    "What assays exist for the Zeta tissue?", "How many clades of tissue are in Zeta?",
    "Show the mice in my projects", "How many assay kinds did lab ZETA run?",
]
GRAPH_SIBLINGS = ["How many assays were run in the Quill project?", "How many assays has YYY-990102DEF-2-PUB gone through?",
                  "What assays exist for the Quill tissue?"]


@pytest.mark.parametrize("config", [ADMIN, MEMBER], ids=["admin", "member"])
@pytest.mark.parametrize("q", GRAPH_QUESTIONS + GRAPH_SIBLINGS)
def test_an_ordinary_graph_question_is_not_a_caller_or_catalog_question(q, config):
    p = plan("graph_query")
    assert route(q, p, config) is p


@pytest.mark.parametrize("q,p", [
    ("How many sample types are there in project Zeta?", plan("graph_query", projects=["Zeta"])),
    ("How many assays are there for ZZZ-990101ABC-1-PUB?", plan("graph_query", uids=["ZZZ-990101ABC-1-PUB"])),
    ("How many assay kinds exist in lab QUILL?", plan("graph_query", lab_codes=["QUILL"])),
    ("Which projects am I in for YYY-990102DEF-2-PUB?", plan("graph_query", uids=["YYY-990102DEF-2-PUB"])),
], ids=["project", "uid", "lab", "caller_with_uid"])
def test_a_plan_that_names_a_project_lab_or_uid_skips_the_caller_and_catalog_rules(q, p):
    assert route(q, p) is p


@pytest.mark.parametrize("q,projects", [("Am I a member of Quill?", ()), ("Am I a member of Zeta?", ("Zeta",)),
                                        ("Who is logged in?", ()), ("What are my projects?", ()),
                                        ("Which projects am I in?", ()), ("Am I an admin?", ())])
def test_membership_and_login_questions_go_to_the_system_agent(q, projects):
    out = route(q, plan("graph_query", projects=projects))
    assert out.mode == "system_question" and out.target_endpoint is None


def test_a_sample_question_that_mentions_me_stays_on_the_graph():
    p = plan("graph_query")
    assert route("How many samples can I see in Zeta?", p) is p


def test_catalog_count_on_the_graph_moves_but_a_sample_count_stays():
    assert route("How many Raw sample types are there?", plan("graph_query")).mode == "system_question"
    p = plan("graph_query")
    assert route("How many Raw samples are there?", p) is p


def test_sop_list_and_search_go_to_the_graph():
    assert route("What SOPs are on file?", plan(endpoint=SOPS, keywords=["SOP"])).mode == "graph_query"
    assert route("Which SOPs mention fixation?", plan(endpoint=SOPS, keywords=["fixation"])).mode == "graph_query"


def test_sops_of_a_project_are_the_project_report():
    out = route("List the SOPs of Zeta", plan(endpoint=SOPS, projects=["Zeta"]))
    assert out.mode == "reporter" and out.report_mode == "summary"


def _kept_sop(out, ref):
    assert (out.mode, out.target_endpoint, out.filters.keywords) == ("new_search", SOPS, [ref])


def test_downloading_one_named_sop_stays_on_rest():
    _kept_sop(route("Download SOP 142", plan(endpoint=SOPS)), "142")
    _kept_sop(route("Get the file of SOP 9001", plan(endpoint=SOPS, keywords=["SOP 9001"])), "9001")


@pytest.mark.parametrize("q,ref", [("Give me the file for the SOP titled Zeta Fixation Protocol", "Zeta Fixation Protocol"),
                                   ('Download "Quill Staining Protocol"', "Quill Staining Protocol"),
                                   ("Download Quill's SOP 'Zeta Fixation Protocol'", "Zeta Fixation Protocol")])
def test_downloading_one_sop_named_by_its_title_stays_on_rest(q, ref):
    """Review F5: the API resolves an exact title itself, so a title download is a REST download too."""
    _kept_sop(route(q, plan(endpoint=SOPS)), ref)


def test_a_named_sop_wins_over_a_project():
    _kept_sop(route("Download SOP 142 from project Zeta", plan(endpoint=SOPS, projects=["Zeta"])), "142")


def test_a_title_with_no_file_word_is_not_a_download():
    assert route("Which SOP is called for in the Quill staining step?", plan(endpoint=SOPS)).mode == "graph_query"


def test_people_list_stays_but_a_condition_moves_it():
    p = plan(endpoint=PEOPLE, keywords=["registered users"])
    assert route("Who are the registered users?", p) is p
    assert route("Who are the users in Zeta?", plan(endpoint=PEOPLE, projects=["Zeta"])).mode == "graph_query"
    assert route("Which users work on fixation?", plan(endpoint=PEOPLE, keywords=["fixation"])).mode == "graph_query"


def test_retrieve_and_its_alias_stay():
    for ep in (RETRIEVE, "/nextseek_api/admin/samples/retrieve/"):
        p = plan(endpoint=ep, uids=["ZZZ-990101ABC-1-PUB"])
        assert route("Export ZZZ-990101ABC-1-PUB", p) is p


Q_ATTR = "Which attributes of the sample types are required?"


def test_attribute_question_splits_by_who_asks():
    assert route(Q_ATTR, plan("graph_query"), ADMIN).mode == "graph_query"
    assert route(Q_ATTR, plan(endpoint=SAMPLE_TYPES), ADMIN).mode == "graph_query"
    assert route(Q_ATTR, plan("graph_query"), MEMBER).mode == "system_question"
    assert route("Which fields of the Zeta sample type hold a date?", plan(endpoint=SAMPLE_TYPES), MEMBER).mode == "system_question"


def test_unknown_endpoint_fails_safe_to_the_graph():
    assert route("Show zzz", plan(endpoint="/nextseek_api/zzz/")).mode == "graph_query"


def test_a_guard_that_raises_sends_a_removed_endpoint_to_the_graph(monkeypatch):
    import chat_nextseek.agents.parser as parser

    monkeypatch.setattr(parser, "_route_by_kind_steps", lambda *a, **k: 1 / 0)
    assert _route_by_kind("q", plan(endpoint=ASSAYS)).mode == "graph_query"
    p = plan(endpoint=RETRIEVE)
    assert _route_by_kind("q", p) is p


def test_plan_mode_candidates_get_the_same_mapping():
    c = ParserCandidate(mode="new_search", target_endpoint=ASSAYS, filters=ParserFilters())
    keep = ParserCandidate(mode="new_search", target_endpoint=RETRIEVE, filters=ParserFilters(uids=["ZZZ-990101ABC-1-PUB"]))
    out = _apply_multi_parser_guardrails("Which assay kinds were never run on any sample?",
                                         MultiParserPlan(intent_summary="x", candidates=[c, keep]), ADMIN)
    assert out.candidates[0].mode == "graph_query" and out.candidates[0].target_endpoint is None
    assert out.candidates[1] is keep


def test_the_catalog_holds_exactly_the_three_pairs():
    pairs = {(r["method"], r["path"]) for r in json.loads(CATALOG.read_text(encoding="utf-8"))}
    assert pairs == {("POST", RETRIEVE), ("GET", SOPS), ("GET", PEOPLE)}
