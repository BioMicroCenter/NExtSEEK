"""The follow-up loop's queries: the answer sees their rows, and the turn attaches them.

Production acceptance run, 2026-09-22, case ``post.followup_downstream_types`` (task
0006a373). "Find me mice treated with ndma" (1,641 MUS), then "Of those mice, what
downstream data types are associated with them?". The loop ran four seeded queries,
every one with ``uids_applied`` 1641; three returned 23 rows, which is exactly the
graph's 23 downstream types (TIS 25,936, D.IMG 3,450, DNA 766 ...). The reply said:

    "... though the individual type names were not returned as separate rows in this
    count."

Two defects, both in how the loop's own results leave it:

A. ``run_new_query``'s tool result carried ``count``, ``rows_returned`` and up to three
   ``examples`` harvested from uid/id/name columns, never a row. A breakdown row is
   ``{"type": "TIS", "n": 25936}``, which has none of those keys, so the model was
   handed "23" and nothing it could name, and so was the final answer-only pass, which
   reads the same conversation.
B. The turn re-emitted the STORED bundle's files (the turn-1 mouse list, bundle 1) and
   no file of its own, so the 23 rows the answer was about were unreachable.

Every agent and tool is stubbed; no model, Neo4j or network call is made.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from chat_nextseek import orchestrator as orch
from chat_nextseek.agents import followup as followup_mod
from chat_nextseek.agents.followup import FOLLOWUP_AGENT_KEY, MAX_ITER
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan

#: The 23 downstream types of the production run, in the shape a breakdown query returns.
TYPES = [
    ("TIS", 25936), ("D.IMG", 3450), ("DNA", 766), ("D.SEQ", 531), ("RNA", 402),
    ("D.MSP", 390), ("A.IMG", 311), ("PRT", 250), ("D.FLOW", 201), ("BLD", 188),
    ("D.MBL", 150), ("SER", 120), ("D.CYTOF", 99), ("A.FLOW", 80), ("D.SPC", 64),
    ("CEL", 51), ("D.NMR", 40), ("A.MSP", 33), ("D.VIA", 21), ("PLS", 17),
    ("D.TITR", 9), ("URN", 4), ("D.FILE", 2),
]
TYPE_ROWS = [{"type": code, "n": n} for code, n in TYPES]
SEEDED_CYPHER = ("MATCH (m:Sample) WHERE m.uuid IN $uids MATCH (d:Sample)-[:DERIVED_FROM*1..]->(m) "
                 "RETURN d.type AS type, count(DISTINCT d) AS n ORDER BY n DESC")
PRIOR_UIDS = [f"MUS-200901ENG-{i}" for i in range(40)]


def _neo4j_result(rows, cypher=SEEDED_CYPHER):
    return {"ok": True, "count": len(rows), "total": len(rows), "truncated": False, "limit": None,
            "data": rows, "cypher": cypher + " /* scoped */", "submitted_cypher": cypher,
            "parameters": {}, "counters": {}, "scope": {"decision": "proven"}}


def _prior_bundle():
    return {
        "id": 1, "mode": "graph_query", "user_query": "Find me mice treated with ndma",
        "graph_result": {"ok": True, "count": len(PRIOR_UIDS), "total": len(PRIOR_UIDS),
                         "data": [{"uuid": u} for u in PRIOR_UIDS]},
        "files": [{"key": "graph_result", "kind": "graph_result", "label": "Graph query result rows",
                   "filename": "graph_result_bundle_1.json", "path": "/nowhere/graph_result_bundle_1.json",
                   "mime": "application/json", "bundle_id": 1}],
    }


# --------------------------------------------------------------------------- #
# A scripted tool model, as in test_followup_agent.py
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


def _stub_graph(monkeypatch, rows):
    seen = SimpleNamespace(params=[])
    monkeypatch.setattr(orch, "graph_agent",
                        lambda *a, **k: GraphAgentPlan(cypher=SEEDED_CYPHER, context_mode="catalog"))

    def neo4j(config, cypher, params=None):
        seen.params.append(params)
        return _neo4j_result(rows)

    monkeypatch.setattr(orch, "tool_neo4j_query", neo4j)
    return seen


# --------------------------------------------------------------------------- #
# Defect A: the model is handed the rows, not only their count
# --------------------------------------------------------------------------- #

def test_the_query_result_the_model_receives_carries_the_rows(monkeypatch, tmp_path):
    seen = _stub_graph(monkeypatch, TYPE_ROWS)
    client = _ScriptedClient([
        _tool_use("run_new_query", {"question": "downstream types of the 40 NDMA mice", "seed_uids": True}),
        _tool_use("answer", {"text": "23 types.", "caveats": []}),
    ])

    orch._run_followup_agent(_Cfg(client), session={}, user_text="what downstream types?",
                             bundle=_prior_bundle(), log_dir=str(tmp_path))

    assert seen.params[0]["uids"] == PRIOR_UIDS, "the seeding still binds every UID"
    tool_result = client.turns[1]["messages"][-1]["content"][0]
    payload = json.loads(tool_result["content"])
    assert payload["count"] == 23
    for code, n in TYPES:
        assert code in tool_result["content"], f"{code} never reached the model"
    assert payload["rows"][0] == {"type": "TIS", "n": 25936}
    assert payload["rows_shown"] == 23


def test_the_final_answer_only_pass_sees_the_rows_too(monkeypatch, tmp_path):
    """The terminal pass answers from the conversation; the rows must be in it."""
    _stub_graph(monkeypatch, TYPE_ROWS)
    script = [_tool_use("run_new_query", {"question": "downstream types", "seed_uids": True})] * MAX_ITER
    script.append(_tool_use("answer", {"text": "23 types, led by TIS.", "caveats": []}))
    client = _ScriptedClient(script)

    out = orch._run_followup_agent(_Cfg(client), session={}, user_text="what downstream types?",
                                   bundle=_prior_bundle(), log_dir=str(tmp_path))

    assert out["reply"] == "23 types, led by TIS."
    final = client.turns[-1]
    assert [t["name"] for t in final["tools"]] == ["answer"]
    blob = json.dumps(final["messages"])
    for code, _ in TYPES:
        assert f'\\"type\\": \\"{code}\\"' in blob, f"{code} is not in the final pass's input"


def test_the_rows_are_bounded():
    """A tool result is re-sent on every later iteration, so the preview is capped."""
    rows = [{"uuid": f"TIS-{i}", "type": "TIS", "note": "x" * 40} for i in range(5000)]
    shown = followup_mod.preview_rows(rows)
    assert 0 < len(shown) <= followup_mod.FOLLOWUP_ROWS_MAX
    assert shown == rows[:len(shown)], "the head, in order"
    assert len(json.dumps(shown)) < 8000


def test_the_seam_reports_the_total_beside_a_capped_preview(monkeypatch, tmp_path):
    rows = [{"uuid": f"TIS-{i}"} for i in range(500)]
    monkeypatch.setattr(orch, "graph_agent",
                        lambda *a, **k: GraphAgentPlan(cypher=SEEDED_CYPHER, context_mode="catalog"))
    monkeypatch.setattr(orch, "tool_neo4j_query", lambda *a, **k: {**_neo4j_result(rows), "total": 24421})
    captured = {}

    def fake_followup(config, *, user_text, bundle, run_query, log_dir):
        captured["payload"] = run_query(question="q", seed_uids=PRIOR_UIDS)
        return {"reply": "x", "queries": [], "tool_calls": []}

    monkeypatch.setattr(orch, "run_followup", fake_followup)
    orch._run_followup_agent(MagicMock(), session={}, user_text="q", bundle=_prior_bundle(), log_dir=str(tmp_path))

    payload = captured["payload"]
    assert payload["count"] == 24421
    assert payload["rows_returned"] == 500
    assert payload["rows_shown"] == len(payload["rows"]) <= followup_mod.FOLLOWUP_ROWS_MAX
