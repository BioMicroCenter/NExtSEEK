"""The pipeline agent builds on the result the parser named (target_result_id), not the newest one."""
from types import SimpleNamespace

import pytest

from chat_nextseek.pipeline import agent as pa
from chat_nextseek.pipeline import agent_tools

MICE = [{"uuid": f"MUS-1-{n}-PUB"} for n in (1, 2, 3)]
NHP = [{"uuid": f"NHP-9-{n}-PUB"} for n in (1, 2)]


def _session():
    return {
        "results_history": [
            {"id": 1, "user_query": "mice treated with a drug", "graph_result": {"ok": True, "data": MICE}},
            {"id": 2, "user_query": "monkeys from a site", "graph_result": {"ok": True, "data": NHP}},
        ],
        "chat_log": [{"turn_id": 1, "ts": "t", "mode": "graph_query", "user_query": "mice treated with a drug",
                      "assistant_reply": "Found 3 mice.", "bundle_id": 1, "status": "ok"}],
    }


@pytest.fixture
def loop(monkeypatch):
    monkeypatch.setattr(pa, "_run_loop", lambda session, config, **k: {"action": "ask", "reply": "loop", "params": None})


def _state(session):
    return session[pa.PIPELINE_AGENT_KEY]


def test_a_named_result_is_seeded_and_remembered(loop):
    session = _session()
    pa.start(session, SimpleNamespace(), user_query="Run rnaseq on the mice", parser_plan={"target_result_id": 1})
    seed = _state(session)["messages"][0]["content"]
    assert _state(session)["source_bundle_id"] == 1
    assert "the result you mean: id 1, query 'mice treated with a drug'" in seed and "3 sample UIDs" in seed
    assert "earlier turns of this chat" in seed  # the parser's chat history rides along
    assert pa.snapshot_for_chat_log(session)["source_bundle_id"] == 1


def test_the_tools_resolve_the_named_bundle(loop, monkeypatch):
    session = _session()
    pa.start(session, SimpleNamespace(), user_query="Run rnaseq on the mice", parser_plan=SimpleNamespace(
        target_result_id=1, model_dump=lambda: {"target_result_id": 1}))
    seen = []
    monkeypatch.setattr(agent_tools, "uids_from_last_search", lambda s, bundle_id=None: seen.append(bundle_id) or [])
    agent_tools.tool_resolve_samples(SimpleNamespace(), session, _state(session), {"kind": "last_search"}, "rnaseq")
    assert seen == [1]


def test_no_target_uses_the_newest_set_and_the_summary_agrees(loop):
    session = _session()
    pa.start(session, SimpleNamespace(), user_query="Run rnaseq on those")
    assert _state(session)["source_bundle_id"] == 2
    assert "monkeys from a site" in _state(session)["messages"][0]["content"]


def test_a_named_count_asks_and_offers_the_newest_set(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no model call")
    monkeypatch.setattr(pa, "_run_loop", boom)
    session = _session()
    session["results_history"].append({"id": 3, "user_query": "how many by type",
                                       "graph_result": {"ok": True, "data": [{"name": "RNA-seq", "n": 5}]}})
    out = pa.start(session, SimpleNamespace(), user_query="Run rnaseq on those", parser_plan={"target_result_id": 3})
    assert out["action"] == "ask"
    assert "how many by type" in out["reply"] and "monkeys from a site" in out["reply"] and "2 samples" in out["reply"]
    state = _state(session)
    assert state["active"] and state["source_bundle_id"] == 2  # a "yes" builds on exactly the offered set
    assert [m["role"] for m in state["messages"]] == ["user", "assistant"]
    assert state["messages"][1]["content"][0]["text"] == out["reply"]


def test_a_named_count_with_no_set_to_offer(monkeypatch):
    monkeypatch.setattr(pa, "_run_loop", lambda *a, **k: pytest.fail("no model call"))
    session = {"results_history": [{"id": 1, "user_query": "by type", "graph_result": {"ok": True, "data": [{"name": "x", "n": 1}]}}]}
    out = pa.start(session, SimpleNamespace(), user_query="build it", parser_plan={"target_result_id": 1})
    assert out["action"] == "ask" and "UIDs" in out["reply"]
    assert _state(session)["source_bundle_id"] is None


def test_the_cc_bridge_with_explicit_uids_still_starts(loop):
    session = {}
    out = pa.start(session, SimpleNamespace(), user_query="Run rnaseq on MUS-1-1-PUB, MUS-1-2-PUB")
    assert out["reply"] == "loop" and _state(session)["source_bundle_id"] is None
