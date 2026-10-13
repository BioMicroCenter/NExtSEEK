"""The follow-up loop sees the stored query, and a capped or UID-less seed is rebuilt from it.

A follow-up such as "of those, how many are from the liver?" is scoped to the previous
result by binding its stored UIDs as ``$uids``. That is only right when the stored copy
holds every one of them. Two shapes broke it:

* capped (L2): the stored copy holds fewer rows than the total, for example 5,000 of
  36,622 tissues. The loop seeded the 5,000, reported ``uids_applied == uids_available``
  and carried no caveat, so the answer was about a partial set presented as the whole.
* UID-less (L1): a count, or a breakdown such as ``{type, n}`` rows, keeps no sample UIDs
  at all, so the set was rebuilt from the question text alone, which loses any filter the
  text does not spell out (a regex, a quoted term).

Both are fixed the same way: the query that produced the stored result is carried as
``stored_query``, and a seeded query whose seed is capped or empty starts from it instead
of binding a partial list. A complete seed still binds every UID, as before.

Every agent and tool is stubbed; no model, Neo4j or network call is made.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from chat_nextseek import orchestrator as orch
from chat_nextseek.agents.followup import (
    FOLLOWUP_AGENT_KEY,
    build_followup_tool_schemas,
    describe_stored_result,
    run_followup,
)
from chat_nextseek.graph_scope import SCOPE_PARAM
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan

SEED_MODES = {"uids", "stored_query", "none"}

#: The earlier query: a text match the question text alone would not reproduce.
STORED_CYPHER = ("MATCH (s:Sample) WHERE s.type = $type AND s.uuid =~ '.*SHA.*' "
                 "RETURN s.uuid AS uid LIMIT 20")
STORED_PARAMS = {"type": "D.SEQ"}
#: What the graph agent writes for the follow-up (stubbed): the earlier filters plus the new one.
REBUILT_CYPHER = ("MATCH (s:Sample) WHERE s.type = $type AND s.uuid =~ '.*SHA.*' "
                  "AND s.tissue = 'liver' RETURN count(s) AS n")
SEEDED_CYPHER = "MATCH (s:Sample) WHERE s.uuid IN $uids RETURN s.type AS type, count(s) AS n"
REFINE_LEAD = "Start from this earlier query and add the new condition; keep every filter it has:\n"
CAPPED_NOTE = (
    "The stored copy holds fewer rows than the total. For a question about the whole set, run a "
    "new query that repeats the stored query's filters (stored_query) and adds the new condition; "
    "do not seed with the stored UIDs."
)


def _graph_bundle(*, stored=20, total=250, cypher=STORED_CYPHER, parameters=None, truncated=False,
                  bundle_id=3):
    """A graph turn's bundle as the orchestrator writes it: plan dump plus the tool's result."""
    params = dict(STORED_PARAMS if parameters is None else parameters)
    return {
        "id": bundle_id, "mode": "graph_query", "user_query": "D.SEQ samples with SHA in the UID",
        "graph_plan": {"cypher": cypher, "explanation": "", "parameters": params,
                       "keyword_fields": {}, "context_mode": "catalog"},
        "graph_result": {"ok": True, "count": stored, "total": total, "truncated": truncated,
                         "data": [{"uid": f"D.SEQ-{i}"} for i in range(stored)],
                         "cypher": cypher + " /* scoped */", "submitted_cypher": cypher,
                         "parameters": {**params, SCOPE_PARAM: [2, 3]}},
    }


def _count_bundle():
    """A count-only turn: one aggregate row, no UIDs."""
    cypher = "MATCH (s:Sample) WHERE s.type = 'MUS' RETURN count(s) AS n"
    return {
        "id": 2, "mode": "graph_query", "user_query": "how many mouse samples",
        "graph_plan": {"cypher": cypher, "parameters": {}},
        "graph_result": {"ok": True, "count": 1, "total": 1, "data": [{"n": 705}]},
    }


def _followup_count_bundle():
    """A follow-up's own bundle for a count: its query filters on ``$uids``, which the bundle
    keeps only as a count, and its one row is the number, so it holds no UIDs either."""
    cypher = ("MATCH (m:Sample) WHERE m.uuid IN $uids MATCH (d:Sample)-[:DERIVED_FROM*1..]->(m) "
              "RETURN count(DISTINCT d) AS n")
    return {
        "id": 5, "mode": "graph_query", "user_query": "how many samples came from those mice",
        "graph_plan": {"cypher": cypher, "parameters": {"uids": "<1549 UIDs of bundle 1>"}},
        "graph_result": {"ok": True, "count": 1, "total": 1, "data": [{"n": 705}]},
        "search_context": {"endpoint": "neo4j", "followup_of_bundle": 1},
    }


def _rest_count_bundle():
    """A REST turn that kept a total and no rows."""
    return {
        "id": 6, "mode": "new_search", "user_query": "how many mouse samples",
        "api_plan": {"endpoint": "/nextseek_api/samples/advanced_search/", "requestBody": {"type": "MUS"}},
        "api_result_slim": {"data": {"total": 705}},
        "memory_payload": {"data": []},
    }


def _rest_bundle():
    """A REST turn: an API plan and rows, no graph plan."""
    return {
        "id": 4, "mode": "new_search", "user_query": "D.SEQ samples",
        "api_plan": {"endpoint": "/nextseek_api/samples/advanced_search/", "requestBody": {"type": "D.SEQ"}},
        "api_result_slim": {"data": {"total": 250}},
        "memory_payload": {"data": [{"uid": f"D.SEQ-{i}"} for i in range(20)]},
    }


# --------------------------------------------------------------------------- #
# describe_stored_result carries the stored query
# --------------------------------------------------------------------------- #

def test_a_graph_bundle_carries_the_query_that_produced_it():
    described = describe_stored_result(_graph_bundle())
    assert described["stored_query"] == {"cypher": STORED_CYPHER, "parameters": STORED_PARAMS}


def test_the_stored_query_is_the_submitted_statement_not_the_scoped_one():
    """The scoped text carries the server's scope; the loop must start from what the model wrote."""
    described = describe_stored_result(_graph_bundle())
    assert "/* scoped */" not in described["stored_query"]["cypher"]


def test_the_stored_query_drops_every_reserved_scope_parameter():
    """The scope prover refuses a caller-supplied reserved name, so carrying one would refuse
    every rebuilt query for a caller who is not an admin."""
    bundle = _graph_bundle(parameters={"type": "D.SEQ", SCOPE_PARAM: [2, 3], "__SCOPE_extra": 1})
    described = describe_stored_result(bundle)

    assert described["stored_query"]["parameters"] == {"type": "D.SEQ"}
    assert SCOPE_PARAM in bundle["graph_plan"]["parameters"], "the stored bundle itself is not changed"


@pytest.mark.parametrize("bundle", [
    _rest_bundle(),
    {**_graph_bundle(), "graph_plan": None},
    {**_graph_bundle(), "graph_plan": {"cypher": "", "parameters": {}}},
    {**_graph_bundle(), "graph_plan": {"cypher": "   ", "parameters": {"type": "D.SEQ"}}},
], ids=["rest", "no-plan", "empty-cypher", "blank-cypher"])
def test_a_result_with_no_graph_query_has_no_stored_query(bundle):
    assert describe_stored_result(bundle)["stored_query"] is None


@pytest.mark.parametrize("bundle,rebuildable", [
    (_graph_bundle(), True),
    (_count_bundle(), True),
    (_followup_count_bundle(), False),
    (_graph_bundle(cypher="MATCH (s:Sample) WHERE s.uuid IN $uids RETURN s.uuid AS uid LIMIT 20",
                   parameters={"uids": "<1549 UIDs of bundle 1>"}), False),
    (_rest_bundle(), False),
], ids=["graph", "graph-count", "followup-count", "followup-capped", "rest"])
def test_the_description_says_whether_the_stored_query_can_be_rebuilt_from(bundle, rebuildable):
    """The same rule the note and the seam use, so the model is not told a set is rebuilt
    when the seam will not rebuild it."""
    described = describe_stored_result(bundle)
    assert described["stored_query_rebuildable"] is rebuildable
    if bundle.get("graph_plan"):
        assert described["stored_query"] is not None, "the query stays visible either way"


def test_a_capped_result_says_to_rebuild_from_the_stored_query_not_to_seed():
    described = describe_stored_result(_graph_bundle(stored=20, total=250))

    assert described["capped"] is True
    assert CAPPED_NOTE in described["note"]
    assert "seeded with the UIDs" not in described["note"], "the old instruction produced the partial seed"
    assert "seed_uids true" in described["note"], (
        "seed_uids is still how the model scopes a query; it must not read 'do not seed' as 'set it false'")


OLD_CAPPED_NOTE = ("The stored copy holds fewer rows than the total, so it cannot answer a "
                   "question about the whole set. Run a new query seeded with the UIDs.")


def test_a_capped_rest_result_keeps_the_seeding_note():
    """R2: a REST result has no query to rebuild from, so nothing about it changes."""
    described = describe_stored_result(_rest_bundle())
    assert described["capped"] is True
    assert described["note"] == OLD_CAPPED_NOTE


def test_a_capped_result_whose_query_needs_the_earlier_uids_keeps_the_seeding_note():
    """A follow-up's own query filters on ``$uids``, which its bundle keeps only as a count."""
    bundle = _graph_bundle(cypher="MATCH (s:Sample) WHERE s.uuid IN $uids RETURN s.uuid AS uid LIMIT 20",
                           parameters={"uids": "<1549 UIDs of bundle 1>"})
    described = describe_stored_result(bundle)
    assert described["stored_query"] is not None, "still the query that produced it"
    assert described["note"] == OLD_CAPPED_NOTE


def test_a_complete_result_keeps_its_note():
    described = describe_stored_result(_graph_bundle(stored=20, total=20))
    assert described["capped"] is False
    assert "holds every row" in described["note"]


# --------------------------------------------------------------------------- #
# run_followup hands the stored query to a scoped query, and only to a scoped one
# --------------------------------------------------------------------------- #

class _ScriptedClient:
    provider = "bedrock"

    def __init__(self, script):
        self.script = list(script)
        self.turns: list[dict] = []

    def chat_with_tools(self, *, messages, tools, system, model, **kwargs):
        self.turns.append({"messages": json.loads(json.dumps(messages, default=str)), "tools": tools})
        return self.script.pop(0)


class _Cfg:
    LOG_DIR = "/tmp"
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None}
    AGENT_MODEL_CATALOG: dict = {}
    MODEL_MODE = "test"

    def __init__(self, client):
        self._client = client
        self.LLM_CLIENT = client
        self.LLM_MODEL = "m"
        self.LLM_CLIENTS = {"anth": client}

    def get_agent_model(self, label):
        assert label == FOLLOWUP_AGENT_KEY
        return self._client, "us.anthropic.claude-opus-4-7", None

    def _load_prompt(self, name):
        return "SYSTEM PROMPT"


def _tool_use(name, payload, tid="t1"):
    return {"stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": payload}]}


def _drive(bundle, *, seed):
    client = _ScriptedClient([
        _tool_use("run_new_query", {"question": "how many D.SEQ samples with SHA in the UID are from liver",
                                    "seed_uids": seed}),
        _tool_use("answer", {"text": "12.", "caveats": []}),
    ])
    calls = []

    def _run_query(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "count": 12, "seed_mode": "stored_query"}

    run_followup(_Cfg(client), user_text="how many of those are from liver?", bundle=bundle, run_query=_run_query)
    return calls


def test_a_scoped_query_is_handed_the_stored_query_and_every_stored_uid():
    bundle = _graph_bundle(parameters={"type": "D.SEQ", SCOPE_PARAM: [2]})
    calls = _drive(bundle, seed=True)

    assert len(calls) == 1
    assert calls[0]["stored_query"] == {"cypher": STORED_CYPHER, "parameters": {"type": "D.SEQ"}}
    assert calls[0]["seed_uids"] == [f"D.SEQ-{i}" for i in range(20)]


def test_a_fresh_question_is_handed_neither_uids_nor_the_stored_query():
    """seed_uids false is a question about the database, not about the earlier result."""
    calls = _drive(_graph_bundle(), seed=False)
    assert calls[0]["seed_uids"] == []
    assert calls[0]["stored_query"] is None


def test_a_rest_result_hands_no_stored_query():
    calls = _drive(_rest_bundle(), seed=True)
    assert calls[0]["stored_query"] is None
    assert len(calls[0]["seed_uids"]) == 20


def test_run_followup_says_explicitly_whether_a_scope_was_asked_for():
    """The seam cannot tell "no seed asked for" from "asked for, but there was nothing to
    seed with" by the seed alone: both hand it no UIDs and no stored query."""
    assert _drive(_graph_bundle(), seed=True)[0]["scoped"] is True
    assert _drive(_graph_bundle(), seed=False)[0]["scoped"] is False
    assert _drive(_rest_count_bundle(), seed=True)[0]["scoped"] is True


def test_reading_the_stored_result_shows_the_stored_query():
    client = _ScriptedClient([
        _tool_use("read_stored_result", {}),
        _tool_use("answer", {"text": "done", "caveats": []}),
    ])
    run_followup(_Cfg(client), user_text="q", bundle=_graph_bundle(), run_query=lambda **kw: {})

    served = json.loads(client.turns[1]["messages"][-1]["content"][0]["content"])
    assert served["stored_query"]["cypher"] == STORED_CYPHER
    assert CAPPED_NOTE in served["note"]


def test_the_tool_surface_explains_that_a_capped_seed_is_rebuilt():
    tools = {t["name"]: t for t in build_followup_tool_schemas()}
    seed = tools["run_new_query"]["input_schema"]["properties"]["seed_uids"]["description"]
    assert "stored_query" in seed and "capped" in seed
    assert "stored_query" in tools["read_stored_result"]["description"]


def test_the_prompt_tells_the_model_to_follow_the_scope_note():
    prompt = (Path(orch.__file__).parent / "prompts" / "followup_agent.txt").read_text()
    assert "scope_note" in prompt
    assert "stored_query" in prompt


#: How a sentence may make "rebuilt" conditional: on the flag that says the stored query can
#: be rebuilt from, or on the query result's own seed_mode, which reports what happened. Not
#: on stored_query merely being there: a follow-up's own ``$uids`` query is shown and cannot
#: be rebuilt from.
_REBUILD_CONDITIONS = ("stored_query_rebuildable is true", "seed_mode is stored_query")


def _sentences_saying_rebuilt(text):
    flat = " ".join(text.split())
    return [s for s in flat.replace(": ", ". ").split(". ") if "rebuil" in s]


@pytest.mark.parametrize("source", ["prompt", "seed_uids"])
def test_rebuilt_is_only_claimed_when_there_is_a_stored_query(source):
    """Without a stored query nothing is rebuilt, and a prompt that says otherwise gives the
    model a reason to present a whole-graph number as "those"."""
    if source == "prompt":
        text = (Path(orch.__file__).parent / "prompts" / "followup_agent.txt").read_text()
    else:
        tools = {t["name"]: t for t in build_followup_tool_schemas()}
        text = tools["run_new_query"]["input_schema"]["properties"]["seed_uids"]["description"]
    sentences = _sentences_saying_rebuilt(text)
    assert sentences, "the rebuild is described"
    for sentence in sentences:
        assert any(c in sentence for c in _REBUILD_CONDITIONS), sentence
    flat = " ".join(text.split())
    assert "stored_query_rebuildable is true" in flat
    assert "is false" in flat and "cannot be scoped" in flat, "the other branch is described too"


def test_read_stored_result_names_the_rebuildable_flag():
    tools = {t["name"]: t for t in build_followup_tool_schemas()}
    assert "stored_query_rebuildable" in tools["read_stored_result"]["description"]


def test_the_prompt_says_what_happens_when_there_is_nothing_to_scope_by():
    prompt = " ".join((Path(orch.__file__).parent / "prompts" / "followup_agent.txt").read_text().split())
    assert "cannot be scoped" in prompt


# --------------------------------------------------------------------------- #
# The seam: _run_followup_agent's _run_query
# --------------------------------------------------------------------------- #

def _seam(monkeypatch, tmp_path, bundle, *, cypher, parameters=None, calls):
    """Run ``calls(run_query)`` inside _run_followup_agent with the graph agent and the Neo4j
    tool stubbed; return what each saw and what each call returned."""
    seen = SimpleNamespace(refine=[], cyphers=[], params=[], payloads=[])

    def graph_agent(config, question, entity, plan, refine_context=None, **kw):
        seen.refine.append(refine_context)
        return GraphAgentPlan(cypher=cypher, parameters=dict(parameters or {}), context_mode="catalog")

    def neo4j(config, statement, params=None):
        seen.cyphers.append(statement)
        seen.params.append(dict(params or {}))
        return {"ok": True, "count": 1, "total": 1, "truncated": False, "data": [{"n": 12}],
                "cypher": statement, "submitted_cypher": statement, "parameters": dict(params or {})}

    monkeypatch.setattr(orch, "graph_agent", graph_agent)
    monkeypatch.setattr(orch, "tool_neo4j_query", neo4j)

    def fake_followup(config, *, user_text, bundle, run_query, log_dir, **_):
        seen.payloads.extend(calls(run_query))
        return {"reply": "x", "queries": [], "tool_calls": []}

    monkeypatch.setattr(orch, "run_followup", fake_followup)
    orch._run_followup_agent(MagicMock(), session={}, user_text="q", bundle=bundle, log_dir=str(tmp_path))
    return seen


def _stored_query_of(bundle):
    return describe_stored_result(bundle)["stored_query"]


def test_a_capped_seed_is_rebuilt_from_the_stored_query_and_binds_no_uids(monkeypatch, tmp_path):
    """5,000 of 36,622 stored: the count must be over the 36,622, not the 5,000."""
    bundle = _graph_bundle(stored=20, total=250)
    uids = [r["uid"] for r in bundle["graph_result"]["data"]]
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=REBUILT_CYPHER, parameters=STORED_PARAMS,
                 calls=lambda rq: [rq(question="liver", seed_uids=uids, stored_query=_stored_query_of(bundle))])

    refine = seen.refine[0]
    assert refine.startswith(REFINE_LEAD + STORED_CYPHER)
    assert json.dumps(STORED_PARAMS, sort_keys=True) in refine, "the stored filters' values come with it"
    assert "$uids" not in refine, "no partial UID list is offered"
    assert "uids" not in seen.params[0], "a capped seed binds no $uids"
    assert seen.cyphers == [REBUILT_CYPHER], "the model's new query runs, through the same Neo4j tool"

    payload = seen.payloads[0]
    assert payload["seed_mode"] == "stored_query"
    assert payload["uids_available"] == 20
    assert payload["uids_applied"] is None
    assert payload["scope_note"] and "rebuilt" in payload["scope_note"]
    assert "20 of its 250" in payload["scope_note"]


def test_a_capped_seed_binds_no_uids_even_when_the_new_query_asks_for_them(monkeypatch, tmp_path):
    bundle = _graph_bundle(stored=20, total=250)
    uids = [r["uid"] for r in bundle["graph_result"]["data"]]
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=SEEDED_CYPHER,
                 calls=lambda rq: [rq(question="q", seed_uids=uids, stored_query=_stored_query_of(bundle))])

    assert "uids" not in seen.params[0]
    assert seen.payloads[0]["seed_mode"] == "stored_query"


def test_the_rebuilt_query_is_told_a_limit_is_not_a_filter(monkeypatch, tmp_path):
    """A capped result always hit its LIMIT; a count that keeps it counts the capped rows again."""
    bundle = _graph_bundle(stored=20, total=250)
    uids = [r["uid"] for r in bundle["graph_result"]["data"]]
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=REBUILT_CYPHER,
                 calls=lambda rq: [rq(question="q", seed_uids=uids, stored_query=_stored_query_of(bundle))])
    assert "LIMIT" in seen.refine[0].split(STORED_CYPHER, 1)[1]


def test_a_result_truncated_at_its_limit_with_no_known_total_is_rebuilt(monkeypatch, tmp_path):
    """The total probe can fail, which leaves total None and ``truncated`` the only sign of a cap."""
    bundle = _graph_bundle(stored=20, total=None, truncated=True)
    uids = [r["uid"] for r in bundle["graph_result"]["data"]]
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=REBUILT_CYPHER,
                 calls=lambda rq: [rq(question="q", seed_uids=uids, stored_query=_stored_query_of(bundle))])

    assert seen.payloads[0]["seed_mode"] == "stored_query"
    assert "uids" not in seen.params[0]


def test_a_uid_less_seed_is_rebuilt_from_the_stored_query(monkeypatch, tmp_path):
    """"Which labs are those mouse samples from?" after a count, which kept no UIDs."""
    bundle = _count_bundle()
    stored = _stored_query_of(bundle)
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=REBUILT_CYPHER,
                 calls=lambda rq: [rq(question="labs of the mouse samples", seed_uids=[], stored_query=stored)])

    assert seen.refine[0].startswith(REFINE_LEAD + stored["cypher"])
    payload = seen.payloads[0]
    assert payload["seed_mode"] == "stored_query"
    assert payload["uids_available"] == 0
    assert payload["scope_note"] and "no sample UIDs" in payload["scope_note"]


def test_a_complete_seed_binds_every_uid_as_before(monkeypatch, tmp_path):
    """R5: nothing changes when the stored copy holds the whole set."""
    bundle = _graph_bundle(stored=40, total=40)
    uids = [r["uid"] for r in bundle["graph_result"]["data"]]
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=SEEDED_CYPHER,
                 calls=lambda rq: [rq(question="types", seed_uids=uids, stored_query=_stored_query_of(bundle))])

    assert seen.params[0]["uids"] == uids, "every UID, not a sample of them"
    assert "ALREADY BOUND as the query parameter $uids" in seen.refine[0]
    assert REFINE_LEAD not in seen.refine[0]
    payload = seen.payloads[0]
    assert payload["seed_mode"] == "uids"
    assert payload["uids_available"] == payload["uids_applied"] == 40
    assert payload["scope_note"] is None


def test_a_complete_seed_without_a_stored_query_is_unchanged_too(monkeypatch, tmp_path):
    bundle = _graph_bundle(stored=40, total=40)
    uids = [r["uid"] for r in bundle["graph_result"]["data"]]
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=SEEDED_CYPHER,
                 calls=lambda rq: [rq(question="types", seed_uids=uids)])

    assert seen.params[0]["uids"] == uids
    assert seen.payloads[0]["seed_mode"] == "uids"
    assert seen.payloads[0]["scope_note"] is None


def test_a_capped_seed_with_no_stored_query_binds_what_it_has_and_says_so(monkeypatch, tmp_path):
    """A REST result has no query to rebuild from: the partial seed stays, and the reply must say so."""
    bundle = _rest_bundle()
    uids = [r["uid"] for r in bundle["memory_payload"]["data"]]
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=SEEDED_CYPHER,
                 calls=lambda rq: [rq(question="types", seed_uids=uids, stored_query=None)])

    assert seen.params[0]["uids"] == uids
    payload = seen.payloads[0]
    assert payload["seed_mode"] == "uids"
    assert payload["uids_applied"] == 20
    note = payload["scope_note"]
    assert note and "20 of the 250" in note and "caveats" in note


def test_a_stored_query_that_needs_the_earlier_uids_is_not_rebuilt(monkeypatch, tmp_path):
    """A follow-up's own bundle stores ``$uids`` as a placeholder; rebuilt, it could bind nothing."""
    followup_cypher = ("MATCH (m:Sample) WHERE m.uuid IN $uids MATCH (d:Sample)-[:DERIVED_FROM]->(m) "
                       "RETURN d.uuid AS uid LIMIT 20")
    bundle = _graph_bundle(stored=20, total=250, cypher=followup_cypher,
                           parameters={"uids": "<1549 UIDs of bundle 1>"})
    uids = [r["uid"] for r in bundle["graph_result"]["data"]]
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=SEEDED_CYPHER,
                 calls=lambda rq: [rq(question="types", seed_uids=uids, stored_query=_stored_query_of(bundle))])

    assert seen.params[0]["uids"] == uids
    payload = seen.payloads[0]
    assert payload["seed_mode"] == "uids"
    assert payload["scope_note"] and "20 of the 250" in payload["scope_note"]


def test_no_seed_and_no_stored_query_runs_unscoped_with_no_note(monkeypatch, tmp_path):
    """A fresh question (seed_uids false) is not about the earlier result, so nothing is said."""
    bundle = _graph_bundle()
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=REBUILT_CYPHER,
                 calls=lambda rq: [rq(question="how many NHP samples exist", seed_uids=[], stored_query=None)])

    assert seen.refine[0] is None
    assert seen.payloads[0]["seed_mode"] == "none"
    assert seen.payloads[0]["scope_note"] is None


def test_a_scope_asked_for_but_impossible_says_so(monkeypatch, tmp_path):
    bundle = _rest_count_bundle()
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=REBUILT_CYPHER,
                 calls=lambda rq: [rq(question="q", seed_uids=[], stored_query=None, scoped=True),
                                   rq(question="q", seed_uids=[], stored_query=None, scoped=False)])

    asked, fresh = seen.payloads
    assert asked["seed_mode"] == fresh["seed_mode"] == "none"
    assert "could not be scoped" in asked["scope_note"]
    assert "every matching sample" in asked["scope_note"] and "caveats" in asked["scope_note"]
    assert fresh["scope_note"] is None, "a fresh question stays silent"
    assert "\u2014" not in asked["scope_note"], "plain words, no em-dash"


def _loop(monkeypatch, tmp_path, bundle, *, seed):
    """The real loop and the real seam, with the model, graph agent and Neo4j stubbed.
    Returns the run_new_query payload the model was handed, and the refine contexts."""
    refines = []

    def graph_agent(config, question, entity, plan, refine_context=None, **kw):
        refines.append(refine_context)
        return GraphAgentPlan(cypher=REBUILT_CYPHER, context_mode="catalog")

    monkeypatch.setattr(orch, "graph_agent", graph_agent)
    monkeypatch.setattr(orch, "tool_neo4j_query", lambda config, cypher, params=None: {
        "ok": True, "count": 1, "total": 1, "truncated": False, "data": [{"n": 9000}],
        "cypher": cypher, "submitted_cypher": cypher, "parameters": dict(params or {})})
    client = _ScriptedClient([
        _tool_use("run_new_query", {"question": "which labs are the mouse samples from", "seed_uids": seed}),
        _tool_use("answer", {"text": "x", "caveats": []}),
    ])
    orch._run_followup_agent(_Cfg(client), session={}, user_text="which labs are those from?",
                             bundle=bundle, log_dir=str(tmp_path))
    payload = json.loads(client.turns[1]["messages"][-1]["content"][0]["content"])
    return payload, refines


def test_a_follow_up_of_a_follow_up_count_says_it_could_not_be_scoped(monkeypatch, tmp_path):
    """"Which labs are those from?" one turn deeper: the earlier result is a follow-up's count.
    It holds no UIDs, and its query needs UIDs it no longer has, so nothing can scope the new
    query. It then covers every matching sample, and the model must be told so."""
    payload, refines = _loop(monkeypatch, tmp_path, _followup_count_bundle(), seed=True)

    assert refines == [None], "nothing to scope by, so no scope text is invented"
    assert payload["seed_mode"] == "none"
    assert "could not be scoped" in payload["scope_note"]


def test_a_uid_less_rest_seed_says_it_could_not_be_scoped(monkeypatch, tmp_path):
    payload, _ = _loop(monkeypatch, tmp_path, _rest_count_bundle(), seed=True)
    assert payload["seed_mode"] == "none"
    assert "could not be scoped" in payload["scope_note"]


@pytest.mark.parametrize("bundle,seed_mode", [
    (_graph_bundle(stored=20, total=250), "stored_query"),
    (_count_bundle(), "stored_query"),
    (_followup_count_bundle(), "none"),
    (_graph_bundle(stored=20, total=250,
                   cypher="MATCH (s:Sample) WHERE s.uuid IN $uids RETURN s.uuid AS uid LIMIT 20",
                   parameters={"uids": "<1549 UIDs of bundle 1>"}), "uids"),
], ids=["graph-capped", "graph-count", "followup-count", "followup-capped"])
def test_the_flag_the_model_reads_agrees_with_what_the_seam_does(monkeypatch, tmp_path, bundle, seed_mode):
    """Rebuilt exactly when the model was told it would be; otherwise UIDs or no scope."""
    flag = describe_stored_result(bundle)["stored_query_rebuildable"]
    payload, _ = _loop(monkeypatch, tmp_path, bundle, seed=True)

    assert payload["seed_mode"] == seed_mode
    assert (payload["seed_mode"] == "stored_query") is flag


@pytest.mark.parametrize("bundle", [_followup_count_bundle(), _rest_count_bundle()], ids=["followup", "rest"])
def test_a_fresh_question_about_such_a_result_stays_silent(monkeypatch, tmp_path, bundle):
    payload, _ = _loop(monkeypatch, tmp_path, bundle, seed=False)
    assert payload["seed_mode"] == "none"
    assert payload["scope_note"] is None


def test_a_seeded_query_that_ignores_uids_still_says_so(monkeypatch, tmp_path):
    bundle = _graph_bundle(stored=40, total=40)
    uids = [r["uid"] for r in bundle["graph_result"]["data"]]
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=REBUILT_CYPHER,
                 calls=lambda rq: [rq(question="q", seed_uids=uids, stored_query=_stored_query_of(bundle))])

    payload = seen.payloads[0]
    assert payload["seed_mode"] == "uids"
    assert payload["uids_applied"] is None
    assert "did not filter on $uids" in payload["scope_note"]


def test_a_query_that_could_not_be_written_still_reports_its_seed_mode(monkeypatch, tmp_path):
    bundle = _graph_bundle(stored=20, total=250)
    uids = [r["uid"] for r in bundle["graph_result"]["data"]]
    seen = _seam(monkeypatch, tmp_path, bundle, cypher="",
                 calls=lambda rq: [rq(question="q", seed_uids=uids, stored_query=_stored_query_of(bundle))])

    assert seen.payloads[0]["ok"] is False
    assert seen.payloads[0]["seed_mode"] == "stored_query"


def test_every_payload_names_its_seed_mode(monkeypatch, tmp_path):
    bundle = _graph_bundle(stored=20, total=250)
    uids = [r["uid"] for r in bundle["graph_result"]["data"]]
    stored = _stored_query_of(bundle)
    seen = _seam(monkeypatch, tmp_path, bundle, cypher=SEEDED_CYPHER, calls=lambda rq: [
        rq(question="a", seed_uids=uids, stored_query=stored),
        rq(question="b", seed_uids=uids, stored_query=None),
        rq(question="c", seed_uids=[], stored_query=None),
    ])
    assert [p["seed_mode"] for p in seen.payloads] == ["stored_query", "uids", "none"]
    assert {p["seed_mode"] for p in seen.payloads} <= SEED_MODES


# --------------------------------------------------------------------------- #
# The turn's debug record says which seed each loop query used
# --------------------------------------------------------------------------- #

def test_the_turn_debug_records_each_loop_query_s_seed_mode(tmp_path):
    prior = _graph_bundle(stored=20, total=250, bundle_id=1)
    session = {"results_history": [prior], "bundle_seq": 1}

    def fake_followup(config, *, user_text, bundle, run_query, log_dir, **_):
        uids = [r["uid"] for r in bundle["graph_result"]["data"]]
        stored = describe_stored_result(bundle)["stored_query"]
        result = run_query(question="liver", seed_uids=uids, stored_query=stored)
        return {"reply": "12 are from liver.", "caveats": [],
                "queries": [{"question": "liver", "seeded": True, "result": result}],
                "tool_calls": ["run_new_query", "answer"]}

    def neo4j(config, cypher, params=None):
        return {"ok": True, "count": 1, "total": 1, "truncated": False, "data": [{"n": 12}],
                "cypher": cypher, "submitted_cypher": cypher, "parameters": dict(params or {})}

    plan = ParserPlan(mode="ask_about_last_results", intent_summary="liver", target_result_id=1)
    with patch.object(orch.pipeline_agent, "is_active", return_value=False), \
            patch.object(orch, "_ensure_query_log_dir", return_value=str(tmp_path)), \
            patch.object(orch, "shortlist_catalog", return_value=([], [], {})), \
            patch.object(orch, "entity_agent", return_value=EntityAgentOutput()), \
            patch.object(orch, "parser_agent", return_value=plan), \
            patch.object(orch, "graph_agent",
                         lambda *a, **k: GraphAgentPlan(cypher=REBUILT_CYPHER, context_mode="catalog")), \
            patch.object(orch, "tool_neo4j_query", neo4j), \
            patch.object(orch, "run_followup", fake_followup), \
            patch.object(orch, "append_turn"), \
            patch.object(orch, "_artifacts_for", MagicMock(return_value=None)):
        payload = orch.run_query(session, SimpleNamespace(MODEL_MODE="test", MIN_SAMPLETYPES=[], MIN_ASSAYS=[]),
                                 "Of those, how many are from liver?", None)

    queries = payload["debug"]["followup"]["queries"]
    assert queries[0]["seed_mode"] == "stored_query"
