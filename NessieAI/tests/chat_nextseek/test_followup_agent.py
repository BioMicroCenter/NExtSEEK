"""The follow-up agent: the memory branch stops being a dead end.

``ask_about_last_results`` read one stored bundle and returned, with no path back to
the graph, so a follow-up that needed data the stored result could not contain was
answered from the stored result anyway. The production review counts 10 of 53 bad
answers here, including wesselr 440 being told "No other data types are available"
about a result that could not have held them, and mchao 118 being told that a 20-row
answer to a 250-row question was normal paging.

Two properties these tests hold to:

* the agent can find out that the stored result cannot answer the question, which is
  what makes re-querying a decision rather than a guess;
* no tool ever returns rows. A tool loop re-sends its whole conversation on each
  iteration, so a tool result is paid for once per remaining iteration.
"""
from __future__ import annotations

import json

import pytest

from chat_nextseek.agents.followup import (
    FOLLOWUP_AGENT_KEY,
    MAX_ITER,
    build_followup_tool_schemas,
    describe_stored_result,
    run_followup,
)


class _ScriptedClient:
    """Replays tool_use / text turns and records what it was sent."""

    provider = "bedrock"

    def __init__(self, script):
        self.script = list(script)
        self.turns: list[dict] = []

    def chat_with_tools(self, *, messages, tools, system, model, **kwargs):
        self.turns.append({"messages": [dict(m) for m in messages], "tools": tools, "system": system})
        return self.script.pop(0)


class _Cfg:
    LOG_DIR = "/tmp"
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None}
    AGENT_MODEL_CATALOG: dict = {}

    def __init__(self, client):
        self._client = client
        self.LLM_CLIENT = client
        self.LLM_MODEL = "m"
        self.LLM_CLIENTS = {"anth": client}

    def get_agent_model(self, label):
        assert label == FOLLOWUP_AGENT_KEY
        return self._client, "us.anthropic.claude-opus-4-7", None

    def _load_prompt(self, name):
        assert name == "followup_agent.txt"
        return "SYSTEM PROMPT"


def _tool_use(name, payload, tid="t1"):
    return {"stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": payload}]}


def _text(text):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


# --------------------------------------------------------------------------
# describe_stored_result: what the bundle holds, and what it cannot.
# --------------------------------------------------------------------------

def test_a_capped_result_says_so():
    """mchao 117-119: 250 found, 20 stored, and the reply used the 20 as the answer."""
    bundle = {
        "id": 3, "user_query": "D.SEQ samples with SHA in the UID", "mode": "graph_query",
        "graph_result": {"ok": True, "count": 20, "total": 250,
                         "data": [{"uid": f"D.SEQ-{i}"} for i in range(20)]},
    }
    described = describe_stored_result(bundle)
    assert described["total"] == 250
    assert described["rows_stored"] == 20
    assert described["capped"] is True
    assert described["uid_count"] == 20
    assert "cannot answer a question about the whole set" in described["note"]


def test_a_complete_result_says_so():
    bundle = {
        "id": 1, "user_query": "IMPACT patients with flow data", "mode": "graph_query",
        "graph_result": {"ok": True, "count": 3, "total": 3,
                         "data": [{"uid": "PAT-1"}, {"uid": "PAT-2"}, {"uid": "PAT-3"}]},
    }
    described = describe_stored_result(bundle)
    assert described["capped"] is False
    assert described["uid_count"] == 3
    assert "holds every row" in described["note"]


def test_a_count_only_result_reports_that_it_kept_no_rows():
    """wesselr 428: "which labs are those mouse samples from?" after a count query.

    The shape is the one tool_neo4j_query really emits: it sets total to len(records),
    so an aggregate stores total=1 and the number it computed sits in the row. This
    test used to hand-write total=705, which the producer can never do, and so it
    passed while F5's defect was live.
    """
    bundle = {
        "id": 2, "user_query": "how many mouse samples", "mode": "graph_query",
        "graph_result": {"ok": True, "count": 1, "total": 1, "data": [{"n": 705}]},
    }
    described = describe_stored_result(bundle)
    assert described["uid_count"] == 0
    assert described["aggregate_values"] == {"n": 705}
    assert described["total"] == 705, "the value it computed, not the row count"
    assert described["capped"] is False, "an aggregate is complete, not truncated"
    assert "aggregate" in described["note"]


def test_the_recalled_number_is_the_aggregate_not_the_row_count():
    """memory.number_recall_within_chat: turn 1 said 890, turn 2 said "there is 1"."""
    bundle = {
        "id": 3, "user_query": "how many mass spectrometry data samples", "mode": "graph_query",
        "graph_result": {"ok": True, "count": 1, "total": 1, "data": [{"n": 890}]},
        "terminal_reply": "There are 890 Mass Spectrometry Data samples.",
    }
    described = describe_stored_result(bundle)
    assert described["total"] == 890
    assert described["previous_reply"] == "There are 890 Mass Spectrometry Data samples."


def test_rows_carrying_a_uid_are_records_not_an_aggregate():
    bundle = {
        "id": 4, "user_query": "one sample", "mode": "graph_query",
        "graph_result": {"ok": True, "count": 1, "total": 1,
                         "data": [{"uuid": "TIS-200901ENG-1", "type": "TIS"}]},
    }
    described = describe_stored_result(bundle)
    assert described["aggregate_values"] is None
    assert described["total"] == 1


def test_the_description_never_carries_rows():
    bundle = {
        "id": 1, "user_query": "q", "mode": "graph_query",
        "graph_result": {"ok": True, "count": 500, "total": 500,
                         "data": [{"uid": f"MUS-{i}", "secret": "x" * 100} for i in range(500)]},
    }
    described = describe_stored_result(bundle)
    blob = json.dumps(described)
    assert len(described["uid_sample"]) <= 5
    assert "secret" not in blob
    assert len(blob) < 2000, "a tool result is re-sent on every later iteration"


# --------------------------------------------------------------------------
# The loop.
# --------------------------------------------------------------------------

def _bundle(total=1206, stored=20):
    return {
        "id": 7, "user_query": "mouse samples with CC in them", "mode": "graph_query",
        "graph_result": {"ok": True, "count": stored, "total": total,
                         "data": [{"uid": f"MUS-{i}"} for i in range(stored)]},
    }


def test_the_agent_reads_the_stored_result_then_requeries_and_answers():
    client = _ScriptedClient([
        _tool_use("read_stored_result", {}),
        _tool_use("run_new_query", {"question": "how many CC mice have transcriptomic data", "seed_uids": True}),
        _tool_use("answer", {"text": "731 of them have transcriptomic data.", "caveats": []}),
    ])
    seen = {}

    def _run_query(*, question, seed_uids):
        seen["question"] = question
        seen["seed_uids"] = seed_uids
        return {"ok": True, "count": 731, "examples": ["MUS-1"], "seeded_uid_count": len(seed_uids)}

    out = run_followup(_Cfg(client), user_text="how many of those have transcriptomic data?",
                       bundle=_bundle(), run_query=_run_query)

    assert out["reply"] == "731 of them have transcriptomic data."
    assert out["tool_calls"] == ["read_stored_result", "run_new_query", "answer"]
    assert len(seen["seed_uids"]) == 20, "the previous result's UIDs are what 'those' means"
    assert out["queries"][0]["seeded"] is True


def test_caveats_come_back_as_data_not_as_prose():
    """B13: 731 was presented as a subset of 1,206 CC mice when neither query filtered
    on CC, and the plan's own note already said the filter could not be applied."""
    client = _ScriptedClient([
        _tool_use("answer", {
            "text": "731 mouse records have transcriptomic data.",
            "caveats": ["The CC filter could not be applied in the graph, so this is every mouse, not only the CC ones."],
        }),
    ])
    out = run_followup(_Cfg(client), user_text="summarize those", bundle=_bundle(),
                       run_query=lambda **kw: {})
    assert out["caveats"] == [
        "The CC filter could not be applied in the graph, so this is every mouse, not only the CC ones."
    ]


def test_seed_uids_can_be_declined_for_a_genuinely_fresh_question():
    client = _ScriptedClient([
        _tool_use("run_new_query", {"question": "how many NHP samples exist", "seed_uids": False}),
        _tool_use("answer", {"text": "704.", "caveats": []}),
    ])
    seen = {}

    def _run_query(*, question, seed_uids):
        seen["seed_uids"] = seed_uids
        return {"ok": True, "count": 704}

    run_followup(_Cfg(client), user_text="and how many NHPs are there in total?",
                 bundle=_bundle(), run_query=_run_query)
    assert seen["seed_uids"] == []


def test_a_failing_query_is_reported_to_the_model_not_raised():
    client = _ScriptedClient([
        _tool_use("run_new_query", {"question": "q", "seed_uids": True}),
        _tool_use("answer", {"text": "I could not run that.", "caveats": ["The query failed."]}),
    ])

    def _boom(**kwargs):
        raise RuntimeError("neo4j is down")

    out = run_followup(_Cfg(client), user_text="q", bundle=_bundle(), run_query=_boom)
    tool_result = client.turns[1]["messages"][-1]["content"][0]
    assert json.loads(tool_result["content"])["ok"] is False
    assert "neo4j is down" in json.loads(tool_result["content"])["error"]
    assert out["reply"] == "I could not run that."


def test_plain_text_without_the_answer_tool_is_still_used():
    client = _ScriptedClient([_text("There are 731.")])
    out = run_followup(_Cfg(client), user_text="q", bundle=_bundle(), run_query=lambda **kw: {})
    assert out["reply"] == "There are 731."


def test_the_loop_is_bounded():
    client = _ScriptedClient([_tool_use("read_stored_result", {})] * (MAX_ITER + 3))
    out = run_followup(_Cfg(client), user_text="q", bundle=_bundle(), run_query=lambda **kw: {})
    assert out["exhausted"] is True
    assert len(client.turns) == MAX_ITER
    assert out["reply"] is None, "an exhausted loop must not invent a reply"


def test_a_profile_without_a_tool_capable_model_degrades_instead_of_failing():
    """The old stored-result path still runs: worse, but never worse than before."""

    class _NoTools:
        provider = "gcp"

    out = run_followup(_Cfg(_NoTools()), user_text="q", bundle=_bundle(), run_query=lambda **kw: {})
    assert out["unsupported"] is True
    assert out["reply"] is None


# --------------------------------------------------------------------------
# Tool surface.
# --------------------------------------------------------------------------

def test_the_three_tools_are_the_whole_surface():
    names = [t["name"] for t in build_followup_tool_schemas()]
    assert names == ["read_stored_result", "run_new_query", "answer"]


def test_run_new_query_demands_a_standalone_question():
    """A query runs with no memory of the conversation, so "those" resolves to nothing."""
    tool = next(t for t in build_followup_tool_schemas() if t["name"] == "run_new_query")
    description = tool["input_schema"]["properties"]["question"]["description"]
    assert "standalone" in description
    assert tool["input_schema"]["required"] == ["question"]


def test_answer_makes_caveats_a_field_rather_than_an_instruction():
    tool = next(t for t in build_followup_tool_schemas() if t["name"] == "answer")
    assert "caveats" in tool["input_schema"]["properties"]


def test_the_followup_agent_is_registered_in_every_profile():
    """An unregistered agent key degrades silently to the globally configured model,
    which for this agent could be one with no tool surface at all."""
    import json as _json
    from pathlib import Path

    catalog_path = Path(__file__).resolve().parents[2] / "chat_nextseek" / "agent_model_catalog.json"
    catalog = _json.loads(catalog_path.read_text())
    for profile, body in catalog.items():
        if profile.startswith("_"):
            continue
        agents = {
            agent
            for entry in body["models"].values()
            for item in (entry if isinstance(entry, list) else [entry])
            for agent in item["agents"]
        }
        assert FOLLOWUP_AGENT_KEY in agents, f"profile {profile} has no followup entry"
