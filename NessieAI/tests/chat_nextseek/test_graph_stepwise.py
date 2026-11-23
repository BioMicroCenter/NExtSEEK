"""The stepwise graph agent: it looks at the data before it writes the final query.

The one-shot graph agent writes one statement blind and code checks it afterwards. The stepwise agent gets a few look
tools (run a read query, the stored values of a field, which records hold a word, a project by name) and a finish tool,
so a guessed spelling, a dropped word or a text-only match is seen before the answer is final. Every statement goes
through ``tool_neo4j_query`` (the scope), every model statement through the same guards as the one-shot agent, and
anything it cannot finish hands the turn back to the one-shot agent.

Invented types and fields only (ZZZ, StatusFoo): the rule is about a kind of question, never a case.
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from chat_nextseek import orchestrator as orch
from chat_nextseek.agents import graph_stepwise as gs
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan


class _Client:
    """Scripted chat_with_tools; keeps a copy of the messages each call was sent."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.sent: list[list] = []

    def chat_with_tools(self, *, messages, tools, system, model, **kwargs):
        self.sent.append(json.loads(json.dumps(messages, default=str)))
        self.tools = tools
        return self._scripted.pop(0)


class _Cfg:
    LOG_DIR = "/tmp"
    GRAPH_AGENT_MODE = "stepwise"

    def __init__(self, client):
        self._client = client

    def get_agent_model(self, label):
        self.label = label
        return self._client, "global.anthropic.claude-sonnet-5-5", None


def _use(name, inp, i="t1"):
    return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": i, "name": name, "input": inp}]}


def _finish(cypher, parameters=None, explanation="what it matches", i="tf"):
    return _use("finish", {"cypher": cypher, "parameters": parameters or {}, "explanation": explanation}, i)


def _last_tool_result(client, call_index) -> str:
    """The text of the tool results the model was sent on call ``call_index``."""
    last = client.sent[call_index][-1]
    return json.dumps(last["content"])


@pytest.fixture
def wired(monkeypatch):
    """Context and guard stubbed; Neo4j answered by ``state['answer'](cypher, parameters)``."""
    state = {"ran": [], "answer": lambda cypher, params: {"ok": True, "data": [{"n": 0}], "count": 1}}
    monkeypatch.setattr(gs, "_context", lambda config, q, e, p, refine=None: gs._Ctx(
        system="SYS", opening="QUESTION", snapshot=None, scoped=False, procedures=frozenset(),
        project_titles={}, fields_named={}))
    monkeypatch.setattr(gs, "_guard", lambda ctx, cypher, params: (cypher, None))

    def neo(config, cypher, parameters=None, **kw):
        state["ran"].append((cypher, parameters, kw))
        return state["answer"](cypher, parameters)

    monkeypatch.setattr(gs, "tool_neo4j_query", neo)
    return state


def _run(client, question="q"):
    return gs.stepwise_graph_agent(_Cfg(client), question, EntityAgentOutput(), {"mode": "graph_query"})


def test_the_agent_reads_stored_values_before_it_filters(wired):
    final = "MATCH (s:T_ZZZ) WHERE toString(s.StatusFoo) = $v RETURN count(*) AS n"

    def answer(cypher, params):
        if "StatusFoo IS NOT NULL" in cypher:
            return {"ok": True, "data": [{"v": "1", "n": 107}, {"v": "0", "n": 300}], "count": 2}
        return {"ok": True, "data": [{"n": 107}], "count": 1}

    wired["answer"] = answer
    client = _Client([_use("field_values", {"sample_type": "ZZZ", "field": "StatusFoo"}),
                      _finish(final, {"v": "1"})])
    out = _run(client)
    assert out.plan is not None and out.plan.cypher == final
    assert out.result["data"] == [{"n": 107}]
    assert out.parameters == {"v": "1"}
    assert "MATCH (s:T_ZZZ) WHERE s.StatusFoo IS NOT NULL" in wired["ran"][0][0]
    seen = _last_tool_result(client, 1)
    assert '\\"1\\"' in seen and "107" in seen  # the stored value, with its count, reached the model
    assert [s["tool"] for s in out.steps] == ["field_values", "finish"]


def test_find_word_shows_the_text_it_matched_not_the_whole_record(wired):
    def answer(cypher, params):
        return {"ok": True, "data": [{"uuid": "ZZZ-1", "text": "Splenocytes (Balb/C)\nfreezer 3"}], "count": 1,
                "total": 1}

    wired["answer"] = answer
    client = _Client([_use("find_word", {"word": "bal", "sample_types": ["ZZZ"]}),
                      _finish("", explanation="no record holds the word itself")])
    out = _run(client)
    seen = _last_tool_result(client, 1)
    assert "Splenocytes (Balb/C)" in seen
    assert "freezer 3" not in seen
    assert len(wired["ran"]) == 1  # one statement per type: its rows and its total
    assert '\\"records\\": 1' in seen
    assert all(p == {"w": "bal"} for _, p, _ in wired["ran"])
    # A finish with no query is the agent's reasoned refusal: no statement, its reason kept.
    assert out.plan.cypher == "" and out.result is None
    assert "no record holds" in out.plan.explanation


def test_find_project_gives_the_same_answer_every_time(monkeypatch):
    levels = {"study": (), "investigation": ("Alpha Cohort",), "project": ("ALPHAc",)}
    monkeypatch.setattr(gs, "_containers", lambda config: (levels, []))
    first = json.loads(gs._find_project(object(), {"name": "alphac"}))
    second = json.loads(gs._find_project(object(), {"name": "AlphaC"}))
    assert first["resolved"] == second["resolved"] == {"level": "project", "title": "ALPHAc"}
    loose = json.loads(gs._find_project(object(), {"name": "alpha"}))
    assert loose["resolved"] is None
    assert {"level": "investigation", "title": "Alpha Cohort"} in loose["candidates"]
    assert {"level": "project", "title": "ALPHAc"} in loose["candidates"]


def test_a_guard_problem_goes_back_to_the_model_and_nothing_runs(wired, monkeypatch):
    monkeypatch.setattr(gs, "_guard", lambda ctx, cypher, params: (
        cypher, "s.Nope does not exist on T_ZZZ" if "Nope" in cypher else None))
    client = _Client([_use("run_query", {"cypher": "MATCH (s:T_ZZZ) WHERE s.Nope = 1 RETURN count(*) AS n"}),
                      _finish("MATCH (s:T_ZZZ) RETURN count(*) AS n")])
    out = _run(client)
    assert not any("Nope" in c for c, _, _ in wired["ran"])
    assert "does not exist" in _last_tool_result(client, 1)
    assert out.plan.cypher == "MATCH (s:T_ZZZ) RETURN count(*) AS n"


def test_a_final_query_that_fails_goes_back_to_the_model(wired):
    answers = iter([{"ok": False, "error": "SyntaxError: bad", "data": []},
                    {"ok": True, "data": [{"n": 3}], "count": 1}])
    wired["answer"] = lambda cypher, params: next(answers)
    client = _Client([_finish("MATCH (s:T_ZZZ RETURN 1"), _finish("MATCH (s:T_ZZZ) RETURN count(*) AS n", i="t2")])
    out = _run(client)
    assert "SyntaxError" in _last_tool_result(client, 1)
    assert out.plan.cypher == "MATCH (s:T_ZZZ) RETURN count(*) AS n"
    assert out.result["data"] == [{"n": 3}]


def test_rows_shown_to_the_model_are_capped_and_the_total_is_said(wired):
    wired["answer"] = lambda cypher, params: {"ok": True, "data": [{"id": i} for i in range(50)], "count": 50,
                                              "total": 50}
    client = _Client([_use("run_query", {"cypher": "MATCH (s:T_ZZZ) RETURN s.id AS id"}),
                      _finish("MATCH (s:T_ZZZ) RETURN count(*) AS n")])
    _run(client)
    shown = json.loads(json.loads(_last_tool_result(client, 1))[0]["content"])
    assert len(shown["rows"]) == gs.ROWS_SHOWN
    assert shown["total"] == 50


def test_a_look_query_has_a_time_limit(wired):
    client = _Client([_use("run_query", {"cypher": "MATCH (s:T_ZZZ) RETURN count(*) AS n"}),
                      _finish("MATCH (s:T_ZZZ) RETURN count(*) AS n")])
    _run(client)
    assert wired["ran"][0][2].get("timeout_s") == gs.LOOK_TIMEOUT_S


def test_no_finish_within_the_step_cap_hands_back_to_the_one_shot_agent(wired):
    client = _Client([_use("run_query", {"cypher": "MATCH (s:T_ZZZ) RETURN count(*) AS n"}, i=f"t{i}")
                      for i in range(gs.MAX_STEPS)])
    out = _run(client)
    assert out.plan is None and "step" in out.fallback
    assert len(client.sent) == gs.MAX_STEPS
    assert "last step" in json.dumps(client.sent[-1][-1]).lower()


def test_a_model_without_tools_hands_back_to_the_one_shot_agent(wired):
    out = gs.stepwise_graph_agent(_Cfg(object()), "q", EntityAgentOutput(), {})
    assert out.plan is None and out.fallback


def test_the_switch_is_off_unless_set_to_stepwise():
    assert gs.enabled(_Cfg(None)) is True
    assert gs.enabled(MagicMock()) is False
    assert gs.enabled(object()) is False


# --- the orchestrator's graph turn ----------------------------------------------------------------------------


def _turn(monkeypatch, tmp_path, *, mode, outcome=None):
    calls = {"one_shot": 0, "stepwise": 0, "neo4j": 0}

    def one_shot(config, user_text, entity_result, plan, retry_context=None, refine_context=None):
        calls["one_shot"] += 1
        return GraphAgentPlan(cypher="MATCH (s) RETURN count(*) AS n", context_mode="catalog")

    def stepwise(config, user_text, entity_result, plan, refine_context=None):
        calls["stepwise"] += 1
        return outcome

    def neo(config, cypher, params=None, **kw):
        calls["neo4j"] += 1
        return {"ok": True, "count": 1, "data": [{"n": 5}]}

    monkeypatch.setattr(orch, "graph_agent", one_shot)
    monkeypatch.setattr(gs, "stepwise_graph_agent", stepwise)
    monkeypatch.setattr(orch, "tool_neo4j_query", neo)
    monkeypatch.setattr(orch, "chatter_agent_answer", lambda *a, **k: "reply")
    monkeypatch.setattr(orch, "append_turn", lambda *a, **k: None)
    config = MagicMock()
    config.MODEL_MODE = "test"
    if mode:
        config.GRAPH_AGENT_MODE = mode
    debug: dict = {}
    orch._execute_graph_turn(
        config=config, session={}, user_text="how many", entity_result=EntityAgentOutput(),
        plan=ParserPlan(mode="graph_query", intent_summary="count"), log_dir=str(tmp_path),
        artifact_store=MagicMock(register_path=MagicMock(return_value=None)),
        send_event=lambda *a, **k: None, debug_payload=debug, t_total_start=time.perf_counter())
    return debug, calls


def test_switch_off_runs_the_one_shot_agent_only(monkeypatch, tmp_path):
    debug, calls = _turn(monkeypatch, tmp_path, mode=None)
    assert calls == {"one_shot": 1, "stepwise": 0, "neo4j": 1}
    assert "graph_engine" not in debug


def test_switch_on_keeps_the_stepwise_result_and_runs_nothing_again(monkeypatch, tmp_path):
    plan = GraphAgentPlan(cypher="MATCH (s:T_ZZZ) RETURN count(*) AS n", context_mode="catalog")
    outcome = gs.StepwiseOutcome(plan=plan, result={"ok": True, "count": 1, "data": [{"n": 9}]}, elapsed_ms=12,
                                 parameters={}, steps=[{"tool": "finish", "ok": True}])
    debug, calls = _turn(monkeypatch, tmp_path, mode="stepwise", outcome=outcome)
    assert calls == {"one_shot": 0, "stepwise": 1, "neo4j": 0}
    assert debug["graph_engine"] == "stepwise"
    assert debug["graph_steps"] == [{"tool": "finish", "ok": True}]
    assert debug["graph_attempts"][-1]["reason"] == "stepwise_final"


def test_switch_on_falls_back_to_the_one_shot_agent_and_says_why(monkeypatch, tmp_path):
    outcome = gs.StepwiseOutcome(plan=None, fallback="no finish within 6 steps", steps=[{"tool": "run_query"}])
    debug, calls = _turn(monkeypatch, tmp_path, mode="stepwise", outcome=outcome)
    assert calls["one_shot"] == 1 and calls["stepwise"] == 1
    assert debug["graph_engine"] == "single"
    assert debug["graph_stepwise_fallback"] == "no finish within 6 steps"


# --- the real guards, on a small catalog ----------------------------------------------------------------------


def _real_ctx():
    from types import MappingProxyType

    from chat_nextseek import graph_catalog as gcat

    def row(title):
        return gcat.TypeIndexRow(title=title, label="T_" + title, name=None, clade=None, sample_count=10,
                                 deprecated=False, attributes_with_values=1)

    snapshot = gcat.CatalogSnapshot(catalog_hash="h1", synced_at=None, has_usage=False, index=(row("ZZZ"),),
                                    guard=MappingProxyType({"T_ZZZ": frozenset({"StatusFoo"})}))
    return gs._Ctx(system="", opening="", snapshot=snapshot, scoped=False, procedures=frozenset(),
                   project_titles={}, fields_named={})


def test_the_real_guard_refuses_a_field_the_type_does_not_hold():
    _, problem = gs._guard(_real_ctx(), "MATCH (s:T_ZZZ) WHERE s.Nope = 1 RETURN count(*) AS n", {})
    assert problem and "Nope" in problem


def test_the_real_guard_refuses_a_whole_sample_node():
    _, problem = gs._guard(_real_ctx(), "MATCH (s:T_ZZZ) RETURN s", {})
    assert problem


def test_the_real_guard_passes_the_field_values_statement():
    from chat_nextseek.graph_review_counts import values_statement
    cypher, problem = gs._guard(_real_ctx(), values_statement("T_ZZZ", "StatusFoo"), {})
    assert problem is None and "StatusFoo" in cypher


# --- a member's scope, and the real prompt and catalog context ---------------------------------------------------


@pytest.mark.parametrize("cypher", [
    gs.word_statement("T_ZZZ"),
    __import__("chat_nextseek.graph_review_counts", fromlist=["values_statement"]).values_statement("T_ZZZ", "StatusFoo"),
])
def test_the_look_statements_are_scoped_for_a_member_not_refused(cypher):
    from chat_nextseek.cypher_scope import Scoped, scope_cypher
    from chat_nextseek.graph_scope import GraphScope
    out = scope_cypher(cypher, {"w": "x"} if "$w" in cypher else {}, GraphScope.for_projects([1], source="test"))
    assert isinstance(out, Scoped), out


def test_the_context_carries_the_graph_prompt_the_look_rules_and_the_question(monkeypatch):
    from pathlib import Path

    from chat_nextseek import graph_catalog, graph_context
    from chat_nextseek.agents import graph as one_shot

    prompts = Path(gs.__file__).resolve().parent.parent / "prompts"
    snapshot = _real_ctx().snapshot
    catalog = one_shot.CatalogContext(
        snapshot=snapshot, schema="STRUCTURE\n\n" + graph_context.RESOLVED_TYPES_HEADING + "\nZZZ: StatusFoo",
        vocabulary="Investigation: Alpha Cohort", project_titles={"Alpha": "ALPHAc"}, fields_named={})
    monkeypatch.setattr(one_shot, "resolve_catalog_context", lambda *a, **k: catalog)
    monkeypatch.setattr(graph_catalog, "shows_committed_vocabulary", lambda config: True)

    class Cfg:
        GRAPH_AGENT_SYSTEM_PROMPT = (prompts / "graph_agent.txt").read_text(encoding="utf-8")

        def _load_prompt(self, name):
            return (prompts / name).read_text(encoding="utf-8")

    ctx = gs._context(Cfg(), "how many ZZZ hold StatusFoo 1", {}, {"mode": "graph_query"}, refine="UID CHECK: none")
    assert "You are the Graph Agent" in ctx.system
    assert "look, then answer" in ctx.system and str(gs.MAX_STEPS) in ctx.system and "{max_steps}" not in ctx.system
    assert "STRUCTURE" in ctx.system and "ZZZ: StatusFoo" not in ctx.system  # the resolved types vary: opening
    assert "ZZZ: StatusFoo" in ctx.opening and "Alpha Cohort" in ctx.opening and "UID CHECK" in ctx.opening
    assert ctx.opening.endswith("how many ZZZ hold StatusFoo 1")
    assert ctx.project_titles == {"Alpha": "ALPHAc"}
