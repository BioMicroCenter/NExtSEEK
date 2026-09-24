"""The REST sample searches are retired: every sample question goes to the graph (routing review 6 and 6a, 2026-09-24).

The NS API agent's catalog keeps eight endpoints. REST answers only what the graph does not hold: SEEK records
(SOPs), downloads by UID (retrieve, sample-tree) and catalog lists. advanced_search, parents_by_child_types and
graph_search leave the catalog the parser chooses from. graph_search's entry moves, unchanged, to
``scope_fallback_endpoints.json``: a graph question refused for its project scope is still answered through it, and
the API agent still builds that request from its template.

Removing them from the catalog was not enough on its own, because nothing checked the parser's endpoint against
the catalog, five code defaults still named advanced_search, and old chats show a stored advanced_search result
back to the parser. So:

- one guard at the end of the parser's guardrails sends a new_search that names a retired search, or no endpoint,
  to graph_query with its filters kept, and re-runs a refine of a stored retired-search result on the graph;
- the defaults (multi-parser failure, a missing candidate, planner synthesis, planner failure) are graph_query;
- ``fix_sample_endpoint`` sends a retrieve with no UIDs to the graph instead of to advanced_search;
- a planner step with no endpoint, or a retired search, runs as a graph step.

The evaluation switch's api arm still ends on REST: it runs last and is the only way onto advanced_search.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from chat_nextseek.agents import api as api_mod
from chat_nextseek.agents import parser as parser_mod
from chat_nextseek.agents.parser import (
    ADVANCED_SEARCH_PATH,
    FORCE_NOTE_MARKER,
    RETIRED_SAMPLE_SEARCH_ENDPOINTS,
    RETIRED_SEARCH_NOTE,
    _apply_parser_guardrails,
    _candidate_to_parser_plan,
    _fallback_multi_parser_plan,
    _refine_prior_endpoint,
    _route_retired_sample_search,
    _synthesize_top_candidate_plan,
)
from chat_nextseek.agents.planner import agent as planner_agent_mod
from chat_nextseek.agents.planner import tools as tools_mod
from chat_nextseek.helpers.tools.nextseek_api import fix_sample_endpoint
from chat_nextseek.schemas import (
    APIRequestPlan,
    MultiParserPlan,
    ParserFilters,
    ParserPlan,
    PlanStep,
    StepExecutionPayload,
)

NESSIE = Path(__file__).resolve().parents[2]
PACKAGE = NESSIE / "chat_nextseek" / "src" / "chat_nextseek"
CONTEXT = PACKAGE / "context"
PROMPTS = PACKAGE / "prompts"
CATALOG = CONTEXT / "min_api_endpoints_enriched.json"
FALLBACK = CONTEXT / "scope_fallback_endpoints.json"

ADVANCED_SEARCH = "/nextseek_api/samples/advanced_search/"
PARENTS_BY_CHILD_TYPES = "/nextseek_api/sample_types/get_parents/parents_by_child_types/"
GRAPH_SEARCH = "/nextseek_api/samples/graph_search/"
RETIRED = (ADVANCED_SEARCH, PARENTS_BY_CHILD_TYPES, GRAPH_SEARCH)
RETRIEVE = "/nextseek_api/samples/retrieve/"
RETRIEVE_ALIAS = "/nextseek_api/admin/samples/retrieve/"
SOPS = "/nextseek_api/sops/"

# The eight (method, path) pairs the operator approved on 2026-09-24.
CATALOG_PAIRS = {
    ("POST", RETRIEVE),
    ("GET", "/nextseek_api/sample-tree/{uid}/tree/"),
    ("GET", SOPS),
    ("GET", "/nextseek_api/people/"),
    ("GET", "/nextseek_api/investigations/"),
    ("GET", "/nextseek_api/projects/"),
    ("GET", "/nextseek_api/assays/"),
    ("GET", "/nextseek_api/sample_types/"),
}
CATALOG_PATHS = sorted(path for _, path in CATALOG_PAIRS)

# A question that trips none of the other guards (no UID, no bulk export wording).
QUERY = "Which lung tissue samples were stored in RNAlater?"
FILTERS = ParserFilters(sampletype_code="TIS", keywords=["RNAlater"], lab_codes=["ABC"])


class _Session(dict):
    """Stand-in for SessionState: `.get` is all the parser's guards use."""


def _plan(mode="new_search", **over) -> ParserPlan:
    base = dict(mode=mode, target_endpoint=None, intent_summary="lung tissue in RNAlater", filters=FILTERS)
    base.update(over)
    return ParserPlan(**base)


def _bundle(bid, *, endpoint=None, search_endpoint=None, mode="new_search") -> dict:
    """A stored result, shaped as the orchestrator writes it."""
    bundle = {"id": bid, "mode": mode, "user_query": "tissue samples",
              "parser_plan": {"mode": mode, "target_endpoint": endpoint,
                              "filters": {"sampletype_code": "TIS", "keywords": ["RNAlater"]}}}
    if search_endpoint is not None:
        bundle["search_context"] = {"endpoint": search_endpoint}
    return bundle


def _catalog() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _fallback() -> list[dict]:
    return json.loads(FALLBACK.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- the catalog


def test_the_catalog_holds_exactly_the_eight_approved_pairs():
    rows = _catalog()
    pairs = [((r.get("method") or "").upper(), r.get("path")) for r in rows]
    assert len(pairs) == 8, pairs
    assert set(pairs) == CATALOG_PAIRS


def test_no_retired_sample_search_is_left_in_the_catalog():
    assert not {r["path"] for r in _catalog()} & set(RETIRED)


def test_the_parser_s_retired_set_is_the_three_sample_searches():
    assert RETIRED_SAMPLE_SEARCH_ENDPOINTS == frozenset(RETIRED)


def test_the_scope_fallback_file_holds_graph_search_s_entry_and_nothing_else():
    rows = _fallback()
    assert isinstance(rows, list) and len(rows) == 1
    entry = rows[0]
    assert entry["path"] == GRAPH_SEARCH and entry["method"] == "POST"
    assert set(entry["request_body"]) >= {"sampletype", "filter_searchText", "filter_matchType", "extensions"}
    assert set(entry["request_body"]["extensions"]) == {"where", "lineage"}


def test_the_two_files_keep_the_catalog_s_formatting():
    """Two-space JSON with a final newline, non-ASCII kept, as the catalog has always been written."""
    for path in (CATALOG, FALLBACK):
        text = path.read_text(encoding="utf-8")
        assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n", path.name


# --------------------------------------------------------------------------- the guard: new_search


@pytest.mark.parametrize("endpoint", [*RETIRED, None, ""])
def test_a_new_search_on_a_retired_search_or_no_endpoint_goes_to_the_graph(endpoint):
    plan = _plan(target_endpoint=endpoint)

    out = _apply_parser_guardrails(QUERY, plan)

    assert out.mode == "graph_query"
    assert out.target_endpoint is None
    assert out.filters == FILTERS, "the filters the parser resolved are kept"
    assert out.notes == RETIRED_SEARCH_NOTE
    assert plan.mode == "new_search" and plan.target_endpoint == endpoint, "the input plan is not mutated"


def test_the_note_keeps_an_existing_note_ahead_of_it():
    out = _route_retired_sample_search(None, _plan(target_endpoint=ADVANCED_SEARCH, notes="parser rationale"))
    assert out.notes == f"parser rationale | {RETIRED_SEARCH_NOTE}"


def test_the_note_never_names_a_retired_search():
    """A note naming advanced_search once steered the graph agent wrong."""
    for name in ("advanced_search", "parents_by_child_types", "graph_search", "/nextseek_api/"):
        assert name not in RETIRED_SEARCH_NOTE, name
    assert RETIRED_SEARCH_NOTE == (
        "sent to graph_query: every sample question goes to the graph (the REST sample searches are retired)"
    )


@pytest.mark.parametrize("endpoint", [*CATALOG_PATHS, RETRIEVE_ALIAS])
def test_a_new_search_on_a_catalog_endpoint_is_left_alone(endpoint):
    """The old retrieve alias stays readable too, so saved chats replay."""
    plan = _plan(target_endpoint=endpoint)
    assert _route_retired_sample_search(None, plan) is plan
    assert _apply_parser_guardrails(QUERY, plan) is plan


@pytest.mark.parametrize("mode", ["graph_query", "ask_about_last_results", "system_question", "reporter",
                                  "unsupported"])
def test_other_modes_are_left_alone(mode):
    plan = _plan(mode, target_endpoint=ADVANCED_SEARCH)
    assert _route_retired_sample_search(None, plan) is plan


def test_an_unscoped_bulk_export_is_still_unsupported():
    for endpoint in (None, ADVANCED_SEARCH):
        out = _apply_parser_guardrails("download all samples", _plan(target_endpoint=endpoint, filters=ParserFilters()))
        assert out.mode == "unsupported"
        assert RETIRED_SEARCH_NOTE not in out.notes


# --------------------------------------------------------------------------- the guard: refine


@pytest.mark.parametrize("endpoint", RETIRED)
def test_a_refine_naming_a_retired_search_is_rerun_on_the_graph(endpoint):
    session = _Session(results_history=[_bundle(1, endpoint=SOPS)])

    out = _apply_parser_guardrails(QUERY, _plan("refine_last_search", target_endpoint=endpoint), session=session)

    assert out.mode == "refine_last_search", "the orchestrator's graph refine keeps the prior turn's filters"
    assert out.refine_engine == "graph"
    assert out.target_endpoint is None
    assert out.filters == FILTERS
    assert out.notes == RETIRED_SEARCH_NOTE


def test_a_refine_with_no_endpoint_over_a_stored_advanced_search_result_goes_to_the_graph():
    session = _Session(results_history=[_bundle(1, endpoint=ADVANCED_SEARCH)])

    out = _apply_parser_guardrails(QUERY, _plan("refine_last_search"), session=session)

    assert out.mode == "refine_last_search" and out.refine_engine == "graph"
    assert out.target_endpoint is None


def test_the_stored_endpoint_falls_back_to_the_search_context_as_the_orchestrator_reads_it():
    session = _Session(results_history=[_bundle(1, endpoint=None, search_endpoint=PARENTS_BY_CHILD_TYPES)])
    assert _refine_prior_endpoint(session, _plan("refine_last_search")) == PARENTS_BY_CHILD_TYPES

    out = _apply_parser_guardrails(QUERY, _plan("refine_last_search"), session=session)
    assert out.refine_engine == "graph"


def test_the_parser_plan_s_endpoint_wins_over_the_search_context():
    session = _Session(results_history=[_bundle(1, endpoint=SOPS, search_endpoint=ADVANCED_SEARCH)])
    assert _refine_prior_endpoint(session, _plan("refine_last_search")) == SOPS


def test_the_refine_reads_the_bundle_the_parser_named_not_the_newest():
    history = [_bundle(1, endpoint=ADVANCED_SEARCH), _bundle(2, endpoint=SOPS)]

    named_old = _apply_parser_guardrails(QUERY, _plan("refine_last_search", target_result_id=1),
                                         session=_Session(results_history=history))
    assert named_old.refine_engine == "graph"

    newest = _plan("refine_last_search")
    assert _apply_parser_guardrails(QUERY, newest, session=_Session(results_history=history)) is newest


def test_a_refine_of_a_stored_sops_result_is_left_alone():
    plan = _plan("refine_last_search")
    session = _Session(results_history=[_bundle(1, endpoint=SOPS)])
    assert _apply_parser_guardrails(QUERY, plan, session=session) is plan


def test_a_refine_of_a_stored_graph_result_is_left_alone():
    plan = _plan("refine_last_search")
    session = _Session(results_history=[_bundle(1, endpoint=None, mode="graph_query")])
    assert _apply_parser_guardrails(QUERY, plan, session=session) is plan


def test_a_refine_already_bound_for_the_graph_is_left_alone():
    plan = _plan("refine_last_search", target_endpoint=ADVANCED_SEARCH, refine_engine="graph")
    session = _Session(results_history=[_bundle(1, endpoint=ADVANCED_SEARCH)])
    assert _apply_parser_guardrails(QUERY, plan, session=session) is plan


def test_the_stored_endpoint_lookup_survives_no_session_and_a_broken_one():
    class _Broken:
        def get(self, *a, **k):
            raise RuntimeError("session backend down")

    assert _refine_prior_endpoint(None, _plan("refine_last_search")) is None
    assert _refine_prior_endpoint(_Broken(), _plan("refine_last_search")) is None
    assert _refine_prior_endpoint(_Session(results_history=[]), _plan("refine_last_search")) is None


def test_a_refine_with_no_bundle_is_a_fresh_search_and_then_goes_to_the_graph():
    out = _apply_parser_guardrails(QUERY, _plan("refine_last_search", target_endpoint=ADVANCED_SEARCH),
                                   session=_Session(results_history=[]))
    assert out.mode == "graph_query"
    assert out.notes.endswith(RETIRED_SEARCH_NOTE)


# --------------------------------------------------------------------------- the evaluation switch


def test_the_evaluation_api_arm_still_ends_on_rest():
    """The guard runs before the switch, so the api arm is still the one way onto advanced_search."""
    out = _apply_parser_guardrails(QUERY, _plan(target_endpoint=ADVANCED_SEARCH), force_mode="api")

    assert out.mode == "new_search"
    assert out.target_endpoint == ADVANCED_SEARCH_PATH
    assert out.notes.index(RETIRED_SEARCH_NOTE) < out.notes.index(FORCE_NOTE_MARKER)
    assert "parser chose graph_query" in out.notes


def test_the_evaluation_api_arm_on_a_graph_plan_still_ends_on_rest():
    out = _apply_parser_guardrails(QUERY, _plan("graph_query"), force_mode="api")
    assert out.mode == "new_search" and out.target_endpoint == ADVANCED_SEARCH_PATH
    assert RETIRED_SEARCH_NOTE not in out.notes


# --------------------------------------------------------------------------- the defaults


def test_the_multi_parser_failure_fallback_is_a_graph_query():
    plan = _fallback_multi_parser_plan(QUERY, {}, RuntimeError("model down"))

    [candidate] = plan.candidates
    assert candidate.mode == "graph_query"
    assert candidate.target_endpoint is None


def test_the_multi_parser_failure_path_logs_and_returns_a_graph_query(monkeypatch, capsys):
    def _boom(**_kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(parser_mod, "call_llm_structured", _boom)
    monkeypatch.setattr(parser_mod, "build_recent_results_summary", lambda session: "")
    import chat_nextseek.chat_memory as chat_memory
    monkeypatch.setattr(chat_memory, "history_block", lambda session: "")
    config = SimpleNamespace(MULTI_PARSER_SYSTEM_PROMPT="system", MIN_API_ENDPOINTS=[], MIN_GRAPH_SCHEMA={},
                             ENDPOINT_INDEX=None, get_agent_model=lambda name: (object(), "model", None))

    plan = parser_mod._canonical_multi_parse(_Session(), config, QUERY, {})

    assert [c.mode for c in plan.candidates] == ["graph_query"]
    out = capsys.readouterr().out
    assert "falling back to single graph_query candidate" in out
    assert "single new_search candidate" not in out


def test_a_missing_candidate_projects_to_a_graph_query():
    plan = _candidate_to_parser_plan(_Session(results_history=[]), QUERY, MultiParserPlan())
    assert plan.mode == "graph_query"
    assert plan.target_endpoint is None
    assert plan.endpoint_candidates == []


def test_planner_synthesis_without_candidates_is_a_graph_step():
    out = _synthesize_top_candidate_plan(MultiParserPlan(), QUERY)

    [step] = out.steps
    assert step.tool == "graph_query"
    assert not step.target_endpoint
    assert step.execution.mode == "graph_query" and step.execution.target_endpoint is None


def test_a_failed_planner_with_no_candidates_falls_back_to_a_graph_step(monkeypatch):
    def _boom(**_kwargs):
        raise RuntimeError("planner model down")

    monkeypatch.setattr(planner_agent_mod, "call_llm_structured", _boom)
    monkeypatch.setattr(planner_agent_mod, "build_recent_results_summary", lambda session: "")
    config = SimpleNamespace(PLANNER_SYSTEM_PROMPT="system", get_agent_model=lambda name: (object(), "model", None))

    decision = planner_agent_mod.planner_agent(_Session(), config, QUERY, {}, parser_plan=None)

    assert decision.action == "execute_step"
    assert decision.step.tool == "graph_query"
    assert not decision.step.target_endpoint
    assert decision.step.execution.target_endpoint is None


def test_no_code_default_names_advanced_search():
    """The literal survives only in the evaluation switch and the intersection heuristic, never as a default."""
    parser_src = (PACKAGE / "agents" / "parser.py").read_text(encoding="utf-8")
    assert not re.search(r'target_endpoint\s*=\s*"/nextseek_api/samples/advanced_search/"', parser_src)
    agent_src = (PACKAGE / "agents" / "planner" / "agent.py").read_text(encoding="utf-8")
    assert "advanced_search" not in agent_src
    tools_src = (PACKAGE / "agents" / "planner" / "tools.py").read_text(encoding="utf-8")
    assert 'or "/nextseek_api/samples/advanced_search/"' not in tools_src


# --------------------------------------------------------------------------- fix_sample_endpoint


@pytest.mark.parametrize("endpoint", [RETRIEVE, RETRIEVE_ALIAS])
def test_a_new_search_retrieve_with_no_uids_goes_to_the_graph(endpoint):
    plan = {"mode": "new_search", "target_endpoint": endpoint, "notes": "",
            "filters": {"sampletype_code": "D.SEQ", "keywords": ["RNA"], "uids": []}}

    out = fix_sample_endpoint(plan)

    assert out["mode"] == "graph_query"
    assert out["target_endpoint"] is None
    assert out["notes"] == "retrieve needs UIDs; sent to the graph"
    assert out["filters"] == {"sampletype_code": "D.SEQ", "keywords": ["RNA"], "uids": []}
    assert "advanced_search" not in json.dumps(out)


def test_a_refine_retrieve_with_no_uids_is_rerun_on_the_graph():
    plan = {"mode": "refine_last_search", "target_endpoint": RETRIEVE, "notes": "same samples as Excel",
            "filters": {"uids": []}}

    out = fix_sample_endpoint(plan)

    assert out["mode"] == "refine_last_search"
    assert out["refine_engine"] == "graph"
    assert out["target_endpoint"] is None
    assert out["notes"] == "same samples as Excel | retrieve needs UIDs; sent to the graph"


@pytest.mark.parametrize("mode", ["new_search", "refine_last_search"])
def test_a_retrieve_with_uids_is_left_alone(mode):
    plan = {"mode": mode, "target_endpoint": RETRIEVE, "notes": "",
            "filters": {"uids": ["TIS-240612ABC-1-PUB"]}}
    before = json.loads(json.dumps(plan))

    assert fix_sample_endpoint(plan) == before


@pytest.mark.parametrize("mode, endpoint", [("new_search", SOPS), ("graph_query", RETRIEVE),
                                            ("reporter", RETRIEVE)])
def test_everything_else_passes_through(mode, endpoint):
    plan = {"mode": mode, "target_endpoint": endpoint, "notes": "", "filters": {}}
    before = json.loads(json.dumps(plan))
    assert fix_sample_endpoint(plan) == before


# --------------------------------------------------------------------------- the planner's search step


def _step(tool="new_search", endpoint=None, **over) -> PlanStep:
    base = dict(
        step_id=2,
        tool=tool,
        context_prompt=QUERY,
        target_endpoint=endpoint or "",
        execution=StepExecutionPayload(mode=tool, target_endpoint=endpoint, tool_query=QUERY,
                                       filters={"sampletype_code": "TIS", "keywords": ["RNAlater"]}),
    )
    base.update(over)
    return PlanStep(**base)


@pytest.fixture
def planner_calls(monkeypatch):
    calls = SimpleNamespace(graph=[], api=[])

    def graph(config, session, step, query, entity_result, log_dir, enriched_context):
        calls.graph.append(step)
        return {"ok": True, "tool": "graph_query", "output": {"data": [], "count": 0}, "error": None}

    def build(config, plan):
        calls.api.append(plan)
        return APIRequestPlan(endpoint=plan["target_endpoint"], method="GET", requestBody={}, queryParameters={})

    monkeypatch.setattr(tools_mod, "_plan_tool_graph_query", graph)
    monkeypatch.setattr(tools_mod, "api_agent_build_request", build)
    monkeypatch.setattr(tools_mod, "tool_nextseek_api_request",
                        lambda **kw: {"ok": True, "status_code": 200, "data": {"data": [{"title": "SOP 1"}]}})
    monkeypatch.setattr(tools_mod, "_retry_advanced_search_if_empty",
                        lambda config, plan, api_plan, result: (api_plan, result))
    return calls


def _run_step(step, history=None):
    session = _Session(results_history=list(history or []))
    return tools_mod._plan_tool_new_search(SimpleNamespace(), session, step, QUERY, {}, None, {})


@pytest.mark.parametrize("endpoint", [None, ADVANCED_SEARCH, PARENTS_BY_CHILD_TYPES])
def test_a_planner_search_step_with_no_endpoint_or_a_retired_one_runs_on_the_graph(planner_calls, endpoint):
    out = _run_step(_step(endpoint=endpoint))

    assert out["tool"] == "graph_query"
    assert planner_calls.api == [], "no REST request is built"
    [graph_step] = planner_calls.graph
    assert not graph_step.target_endpoint and graph_step.execution.target_endpoint is None
    assert graph_step.execution.filters["sampletype_code"] == "TIS"
    assert graph_step.execution.filters["keywords"] == ["RNAlater"]


def test_a_planner_sops_step_still_builds_a_rest_request(planner_calls):
    out = _run_step(_step(endpoint=SOPS))

    assert planner_calls.graph == []
    [plan] = planner_calls.api
    assert plan["target_endpoint"] == SOPS
    assert out["output"]["endpoint"] == SOPS


def test_graph_search_stays_a_rest_step_because_it_is_the_scope_fallback(planner_calls):
    """SCOPE_REFUSAL_HINT tells the planner to use graph_search for a refused graph step."""
    assert GRAPH_SEARCH in tools_mod.SCOPE_REFUSAL_HINT
    _run_step(_step(endpoint=GRAPH_SEARCH))

    assert planner_calls.graph == []
    assert [p["target_endpoint"] for p in planner_calls.api] == [GRAPH_SEARCH]


def test_a_planner_refine_over_a_stored_advanced_search_result_runs_on_the_graph(planner_calls):
    history = [{"id": 1, "user_query": "tissue", "search_context": {"endpoint": ADVANCED_SEARCH},
                "parser_plan": {"target_endpoint": ADVANCED_SEARCH, "filters": {"sampletype_code": "TIS"}}}]

    _run_step(_step("refine_last_search", execution=StepExecutionPayload(mode="refine_last_search",
                                                                         tool_query=QUERY)),
              history=history)

    assert planner_calls.api == []
    [graph_step] = planner_calls.graph
    assert graph_step.execution.filters["sampletype_code"] == "TIS", "the stored result's scope is kept"


def test_a_planner_refine_over_a_stored_sops_result_stays_on_rest(planner_calls):
    history = [{"id": 1, "user_query": "sops", "search_context": {"endpoint": SOPS},
                "parser_plan": {"target_endpoint": SOPS, "filters": {}}}]

    _run_step(_step("refine_last_search", execution=StepExecutionPayload(mode="refine_last_search",
                                                                         tool_query=QUERY)),
              history=history)

    assert planner_calls.graph == []
    assert [p["target_endpoint"] for p in planner_calls.api] == [SOPS]


# --------------------------------------------------------------------------- the scope fallback's template


class _ApiCfg:
    API_AGENT_SYSTEM_PROMPT = "sys"

    def __init__(self, *, fallback=True):
        self.MIN_API_ENDPOINTS = _catalog()
        if fallback:
            self.FALLBACK_API_ENDPOINTS = _fallback()

    def get_schema_for_endpoint(self, endpoint):
        return None

    def get_agent_model(self, _name):
        return (None, "model", None)


def _capture_api_messages(monkeypatch, returned):
    seen = []

    def fake(**kwargs):
        seen.append(kwargs["messages"])
        if isinstance(returned, Exception):
            raise returned
        return returned

    monkeypatch.setattr(api_mod, "call_llm_structured", fake)
    return seen


def test_the_api_agent_builds_graph_search_from_the_fallback_entry(monkeypatch):
    seen = _capture_api_messages(monkeypatch, APIRequestPlan(
        endpoint=GRAPH_SEARCH, method="POST", requestBody={"filter_searchText": "lung"}, queryParameters={}))

    plan = api_mod.api_agent_build_request(
        _ApiCfg(), {"mode": "new_search", "target_endpoint": GRAPH_SEARCH, "filters": {"keywords": ["lung"]}})

    assert plan.endpoint == GRAPH_SEARCH
    schema_text = seen[0][1]["content"]
    assert "Enriched endpoint definition" in schema_text
    assert json.dumps(_fallback()[0], indent=2) in schema_text


def test_a_failed_parse_for_graph_search_still_refuses_an_empty_body(monkeypatch):
    """The fallback entry's request_body is what marks graph_search as needing a body."""
    _capture_api_messages(monkeypatch, RuntimeError("schema parse blew up"))

    plan = api_mod.api_agent_build_request(_ApiCfg(), {"target_endpoint": GRAPH_SEARCH, "filters": {}})

    assert plan.endpoint is None
    assert "requires a request body" in plan.notes


def test_a_config_without_the_fallback_list_still_builds(monkeypatch):
    """Existing stand-ins carry only MIN_API_ENDPOINTS; the lookup must not need the new attribute."""
    seen = _capture_api_messages(monkeypatch, APIRequestPlan(
        endpoint=SOPS, method="GET", requestBody={}, queryParameters={}))

    plan = api_mod.api_agent_build_request(_ApiCfg(fallback=False), {"target_endpoint": SOPS, "filters": {}})

    assert plan.endpoint == SOPS
    assert "Enriched endpoint definition" in seen[0][1]["content"]


def test_the_config_loads_the_fallback_file_beside_the_catalog():
    from chat_nextseek.config import ChatConfig

    src = (PACKAGE / "config.py").read_text(encoding="utf-8")
    assert re.search(
        r'self\.FALLBACK_API_ENDPOINTS\s*=\s*self\._load_json_list\(\s*"scope_fallback_endpoints\.json"', src)

    cfg = ChatConfig.__new__(ChatConfig)  # __init__ dials the database
    cfg.CONTEXT_DIR = str(CONTEXT)
    assert cfg._load_json_list("scope_fallback_endpoints.json", "fallback API endpoints") == _fallback()


# --------------------------------------------------------------------------- the prompts


def test_the_parser_routing_core_names_no_retired_search():
    core = (PROMPTS / "parser_core_routing.txt").read_text(encoding="utf-8")
    for name in ("advanced_search", "parents_by_child_types", "graph_search"):
        assert name not in core, name
    flat = " ".join(core.split())
    assert "sample-tree is GET-per-UID. Use graph_query and put every UID in filters.uids." in flat
    assert ("The catalog has no sample search: a sample-metadata question is graph_query, and so is a refine of "
            'a sample search an earlier turn ran on REST (set "refine_engine": "graph").') in flat
    assert "Do not reinterpret unscoped bulk export as an unfiltered sample search." in flat
    assert "That is lineage: use graph_query, which checks each required descendant type directly." in flat


def test_the_api_prompt_drops_the_parents_by_child_types_block_and_keeps_the_graph_search_rules():
    api = (PROMPTS / "api_agent.txt").read_text(encoding="utf-8")
    assert "parents_by_child_types" not in api
    assert "Endpoint Guardrail" not in api
    assert '"child_sample_types" MUST be populated' not in api
    # Kept: graph_search's fallback body follows advanced_search's rules.
    assert 'For the /nextseek_api/samples/advanced_search/ endpoint, do NOT include any "attribute" field' in api
    assert "What advanced_search can and cannot express" in api
    assert "could not be confirmed to stay within the caller's projects" in api
    assert "Output Format:" in api
