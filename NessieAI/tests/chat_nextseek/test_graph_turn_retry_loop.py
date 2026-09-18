"""The graph turn generates, executes, reads the outcome and may try again.

It used to retry once, and only when Cypher errored. A query that ran perfectly well
and matched nothing was final, which is B11 in the production review: juanita's
question was answered against a guessed assay name, the graph returned zero, and the
zero was reported as the answer.

Two rules the loop must not break:

* a second query that also finds nothing does NOT replace the first result, because
  reporting a different query's number would be worse than reporting zero;
* a retry that changes the answer is recorded, so the reply can say the first query
  found nothing and the filter was changed.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from chat_nextseek import orchestrator as orch
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan


def _run(monkeypatch, tmp_path, plans, results):
    """Drive one graph turn with scripted agent plans and scripted Neo4j results."""
    plan_iter = iter(plans)
    calls = {"agent": 0, "neo4j": 0, "retry_contexts": []}

    def _agent(config, user_text, entity_result, plan, retry_context=None, refine_context=None):
        calls["agent"] += 1
        if retry_context:
            calls["retry_contexts"].append(retry_context)
        return next(plan_iter)

    result_iter = iter(results)

    def _neo4j(config, cypher, params=None):
        calls["neo4j"] += 1
        return next(result_iter)

    def _chatter(*a, **k):
        calls["chatter_kwargs"] = k
        return "reply"

    monkeypatch.setattr(orch, "graph_agent", _agent)
    monkeypatch.setattr(orch, "tool_neo4j_query", _neo4j)
    monkeypatch.setattr(orch, "chatter_agent_answer", _chatter)
    monkeypatch.setattr(orch, "append_turn", lambda *a, **k: None)

    config = MagicMock()
    config.MODEL_MODE = "test"
    debug: dict = {}
    payload = orch._execute_graph_turn(
        config=config, session={}, user_text="how many CC mice", entity_result=EntityAgentOutput(),
        plan=ParserPlan(mode="graph_query", intent_summary="count"), log_dir=str(tmp_path),
        artifact_store=MagicMock(register_path=MagicMock(return_value=None)),
        send_event=lambda *a, **k: None, debug_payload=debug, t_total_start=time.perf_counter(),
    )
    return payload, debug, calls


def _ok(count, data=None):
    return {"ok": True, "count": count, "data": data if data is not None else [{"n": 1}] * count}


def _err(message="SyntaxError"):
    return {"ok": False, "count": 0, "data": [], "error": message}


def test_a_query_that_works_first_time_is_not_retried(monkeypatch, tmp_path):
    _, debug, calls = _run(
        monkeypatch, tmp_path,
        plans=[GraphAgentPlan(cypher="MATCH (s) RETURN count(*)", context_mode="catalog")],
        results=[_ok(1206)],
    )
    assert calls["agent"] == 1
    assert calls["neo4j"] == 1
    assert [a["reason"] for a in debug["graph_attempts"]] == ["initial"]


def test_a_cypher_error_is_retried_with_the_error_text(monkeypatch, tmp_path):
    _, debug, calls = _run(
        monkeypatch, tmp_path,
        plans=[
            GraphAgentPlan(cypher="BAD", context_mode="catalog"),
            GraphAgentPlan(cypher="GOOD", context_mode="catalog"),
        ],
        results=[_err("Variable `s` not defined"), _ok(12)],
    )
    assert calls["agent"] == 2
    assert "Variable `s` not defined" in calls["retry_contexts"][0]
    assert [a["reason"] for a in debug["graph_attempts"]] == ["initial", "cypher_error"]
    assert debug["graph_attempts"][-1]["count"] == 12


def test_zero_rows_gets_one_more_go(monkeypatch, tmp_path):
    """B11: the query ran, matched nothing, and the zero was the answer."""
    _, debug, calls = _run(
        monkeypatch, tmp_path,
        plans=[
            GraphAgentPlan(cypher="MATCH (a:A_GUESSED) RETURN count(*)", context_mode="catalog"),
            GraphAgentPlan(cypher="MATCH (a:A_REAL) RETURN count(*)", context_mode="catalog"),
        ],
        results=[_ok(0), _ok(731)],
    )
    assert calls["agent"] == 2
    assert "matched 0 records" in calls["retry_contexts"][0]
    assert debug["graph_attempts"][-1]["count"] == 731
    assert debug["graph_retry_changed_answer"] is True


def test_a_single_row_aggregate_zero_also_gets_one_more_go(monkeypatch, tmp_path):
    """`RETURN count(s) AS total` matching nothing comes back as ONE row holding zero, so a
    row-count test sees a hit and the retry never fires.

    Measured on the 2026-09-16 evaluation run: three of the five wrong-zero graph answers
    were this shape, and none of them was retried. One told the user there are no HeLa
    samples when there are four; another that no samples come from a lab that has 9,821.
    """
    _, debug, calls = _run(
        monkeypatch, tmp_path,
        plans=[
            GraphAgentPlan(cypher="MATCH (s:T_CEL) WHERE s.CellLine CONTAINS 'hela' RETURN count(s) AS total",
                           context_mode="catalog"),
            GraphAgentPlan(cypher="MATCH (s:T_CEL) WHERE toLower(s.search_text) CONTAINS 'hela' RETURN count(s) AS total",
                           context_mode="catalog"),
        ],
        results=[_ok(1, data=[{"total": 0}]), _ok(1, data=[{"total": 4}])],
    )
    assert calls["agent"] == 2, "a one-row zero must be retried like a no-row result"
    assert "matched 0 records" in calls["retry_contexts"][0]
    assert debug["graph_attempts"][-1]["count"] == 1
    assert debug["graph_retry_changed_answer"] is True


def test_a_single_row_real_count_is_left_alone(monkeypatch, tmp_path):
    """The narrowness matters: a real answer must not be re-queried and risk replacement."""
    _, debug, calls = _run(
        monkeypatch, tmp_path,
        plans=[GraphAgentPlan(cypher="MATCH (s:T_CEL) RETURN count(s) AS total", context_mode="catalog")],
        results=[_ok(1, data=[{"total": 3288}])],
    )
    assert calls["agent"] == 1, "one row holding a real count is an answer, not an empty result"
    assert "graph_retry_changed_answer" not in debug


def test_zero_twice_keeps_the_first_result_and_stops(monkeypatch, tmp_path):
    """A second query that also finds nothing must not replace the first: zero is a
    valid answer, and a different query's zero is not a better one."""
    _, debug, calls = _run(
        monkeypatch, tmp_path,
        plans=[
            GraphAgentPlan(cypher="FIRST", context_mode="catalog"),
            GraphAgentPlan(cypher="SECOND", context_mode="catalog"),
        ],
        results=[_ok(0), _ok(0)],
    )
    assert calls["agent"] == 2, "exactly one zero-row retry, not a hunt"
    assert calls["neo4j"] == 2
    assert debug["graph_attempts"][0]["cypher"] == "FIRST"
    assert "graph_retry_changed_answer" not in debug


def test_zero_rows_is_retried_at_most_once_even_after_an_error(monkeypatch, tmp_path):
    """The error retry and the zero-row retry share one bounded budget, and the
    zero-row retry is still spent only once within it."""
    _, debug, calls = _run(
        monkeypatch, tmp_path,
        plans=[
            GraphAgentPlan(cypher="BAD", context_mode="catalog"),
            GraphAgentPlan(cypher="EMPTY", context_mode="catalog"),
            GraphAgentPlan(cypher="FOUND", context_mode="catalog"),
        ],
        results=[_err(), _ok(0), _ok(5)],
    )
    reasons = [a["reason"] for a in debug["graph_attempts"]]
    assert calls["neo4j"] <= orch.GRAPH_MAX_TRIES
    assert reasons == ["initial", "cypher_error", "zero_rows"]
    assert reasons.count("zero_rows") == 1


def test_a_retry_that_errors_does_not_replace_a_working_result(monkeypatch, tmp_path):
    _, debug, _ = _run(
        monkeypatch, tmp_path,
        plans=[
            GraphAgentPlan(cypher="FIRST", context_mode="catalog"),
            GraphAgentPlan(cypher="BROKEN", context_mode="catalog"),
        ],
        results=[_ok(0), _err("boom")],
    )
    assert debug["graph_attempts"][0]["count"] == 0
    assert debug["graph_attempts"][1]["ok"] is False
    assert "graph_retry_changed_answer" not in debug


def test_an_agent_that_returns_no_cypher_on_retry_stops_the_loop(monkeypatch, tmp_path):
    _, debug, calls = _run(
        monkeypatch, tmp_path,
        plans=[
            GraphAgentPlan(cypher="FIRST", context_mode="catalog"),
            GraphAgentPlan(cypher="", context_mode="catalog"),
        ],
        results=[_err("boom")],
    )
    assert calls["neo4j"] == 1
    assert len(debug["graph_attempts"]) == 1


def test_the_loop_is_bounded(monkeypatch, tmp_path):
    """Every try is a model call plus a Neo4j round trip on the user's latency budget."""
    plans = [GraphAgentPlan(cypher=f"Q{i}", context_mode="catalog") for i in range(10)]
    _, debug, calls = _run(monkeypatch, tmp_path, plans=plans, results=[_err()] * 10)
    assert calls["neo4j"] <= orch.GRAPH_MAX_TRIES
    assert len(debug["graph_attempts"]) <= orch.GRAPH_MAX_TRIES


# --------------------------------------------------------------------------
# The record has to reach the writer, not just the debug panel.
#
# `graph_retry_changed_answer` was set here and read by nothing but these tests. The
# comment on it says the user must not be told a number without being told the first
# query found nothing and the filter was changed to get it -- and the chatter was
# never handed it, so the user never was.
# --------------------------------------------------------------------------

def test_a_retry_that_changed_the_answer_is_disclosed_to_the_chatter(monkeypatch, tmp_path):
    _, debug, calls = _run(
        monkeypatch, tmp_path,
        plans=[
            GraphAgentPlan(cypher="MATCH (a:A_GUESSED) RETURN count(*)", context_mode="catalog"),
            GraphAgentPlan(cypher="MATCH (a:A_REAL) RETURN count(*)", context_mode="catalog"),
        ],
        results=[_ok(0), _ok(731)],
    )

    assert debug["graph_retry_changed_answer"] is True
    notes = calls["chatter_kwargs"].get("query_notes") or []
    assert any("matched nothing" in note for note in notes), notes


def test_a_first_time_answer_carries_no_such_note(monkeypatch, tmp_path):
    _, debug, calls = _run(
        monkeypatch, tmp_path,
        plans=[GraphAgentPlan(cypher="MATCH (s) RETURN count(*)", context_mode="catalog")],
        results=[_ok(1206)],
    )

    assert "graph_retry_changed_answer" not in debug
    assert not (calls["chatter_kwargs"].get("query_notes") or [])


def test_the_zero_row_retry_does_not_invite_a_substitute_answer(monkeypatch, tmp_path):
    """ChIP-seq, Pilot A v2: the first query correctly found nothing; the retry message
    said "use the closest value that really exists", the agent swapped in every Chromatin
    Sequencing Analysis sample, and the reply led with 12 Hi-C samples."""
    _, _, calls = _run(
        monkeypatch, tmp_path,
        plans=[
            GraphAgentPlan(cypher="MATCH (s:Sample) WHERE s.search_text CONTAINS 'chip-seq' RETURN count(*)",
                           context_mode="catalog"),
            GraphAgentPlan(cypher="MATCH (s:Sample) WHERE s.search_text CONTAINS 'chip-seq' RETURN count(*)",
                           context_mode="catalog"),
        ],
        results=[_ok(0), _ok(0)],
    )
    ctx = calls["retry_contexts"][0]

    assert "closest value that really exists" not in ctx
    assert "never replace the thing the user asked for" in ctx
    assert "SAME query unchanged" in ctx
