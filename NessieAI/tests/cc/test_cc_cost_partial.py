"""A Container-CC turn that ran an op backed by NS agents says its cost is partial.

Claude Code's ``total_cost_usd`` counts the Bedrock calls the agent itself made. Several
of its ops answer on the server by running NS agents (the entity, parser, graph and API
agents, the report writer, or a whole NS turn), whose model calls go to the NS providers
and are in no CC number. A CC turn that ran one of them keeps Claude Code's own
``total_cost_usd`` (and ``cost_by_price_table_usd``) but sets ``cost_partial: true`` and
names the ops in ``cost_partial_reason``; the harness already reads ``cost_partial``
off any engine's ``query_complete`` (``NessieAI/tests/nessie_tests/turn_cost.py``).

Which ops run NS agents is pinned here against the op registry and against the server
handlers themselves, so an op that starts calling an agent cannot be missed.
"""
from __future__ import annotations

import inspect

import pytest

from NessieAI.cc import translate
from NessieAI.cc.translate import NS_AGENT_OPS, CCStreamTranslator

OPUS_48 = "us.anthropic.claude-opus-4-8"


def _bash(command):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "tu", "name": "Bash", "input": {"command": command}}]}}


def _complete(*frames):
    t = CCStreamTranslator(model_id=OPUS_48)
    for frame in frames:
        t.handle(frame)
    (event, data), = t.handle({"type": "result", "subtype": "success", "is_error": False, "result": "done",
                               "session_id": "s", "total_cost_usd": 0.5})
    assert event == "query_complete"
    return data


@pytest.mark.parametrize("command, ops", [
    ("nextseek-graph --query 'how many mice'", ["nextseek-graph"]),
    ("/app/plugins/nextseek/bin/nextseek-aggregate --query 'by sex' --parts '[]'", ["nextseek-aggregate"]),
    ("nextseek-entity-extract --query x && nextseek-parse --query x", ["nextseek-entity-extract", "nextseek-parse"]),
    ("python3 /app/plugins/nextseek/bin/nextseek-query --query x", ["nextseek-query"]),
])
def test_a_turn_that_ran_an_ns_agent_op_is_partial_and_says_which(command, ops):
    data = _complete(_bash(command))
    assert data["cost_partial"] is True
    assert data["total_cost_usd"] == 0.5, "Claude Code's own number is kept"
    for op in ops:
        assert op in data["cost_partial_reason"]


@pytest.mark.parametrize("command", [
    "nextseek-graph-schema --types SEQ",
    "nextseek-recall --turn 2",
    "nextseek-report --project 2 --mode summary",
    "nextseek-run-ls --run r1",
    "ls /data/scratch && cat notes.txt",
])
def test_a_turn_that_ran_no_ns_agent_op_is_whole(command):
    data = _complete(_bash(command))
    assert data["cost_partial"] is False
    assert "cost_partial_reason" not in data


def test_an_op_named_only_in_text_does_not_count():
    """Only a Bash call runs an op; the agent writing its name in prose does not."""
    data = _complete({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "I will not run nextseek-graph for this."}]}})
    assert data["cost_partial"] is False


def test_each_op_is_named_once_in_the_order_it_first_ran():
    data = _complete(_bash("nextseek-graph --query a"), _bash("nextseek-parse --query b"),
                     _bash("nextseek-graph --query c"))
    reason = data["cost_partial_reason"]
    assert reason.count("nextseek-graph") == 1
    assert reason.index("nextseek-graph") < reason.index("nextseek-parse")


# ---------------------------------------------------------------------------- the list is the truth

def test_every_listed_op_is_a_registered_op():
    from NessieAI.cc.op_registry.ops import OPS

    assert NS_AGENT_OPS <= {op.bin_name for op in OPS}


#: What a granular handler calls when it runs an NS agent (NessieAI/ns/granular.py).
_AGENT_CALLS = ("entity_agent", "parser_agent", "graph_agent", "api_agent_build_request",
                "report_writer_agent", "run_graph_question", "run_aggregate")


def test_the_list_is_every_op_whose_server_side_runs_an_ns_agent():
    """A viewset op on query/async/ runs a whole NS turn; a sidecar op runs agents when
    its granular handler calls one. The list must be exactly those."""
    from NessieAI.cc.op_registry.models import Transport
    from NessieAI.cc.op_registry.ops import OPS
    from NessieAI.ns import granular

    expected = set()
    for op in OPS:
        if op.transport == Transport.viewset and op.assistant_endpoint.endswith("/query/async/"):
            expected.add(op.bin_name)
        elif op.transport == Transport.sidecar:
            source = inspect.getsource(granular._HANDLERS[op.op_id])
            if any(call in source for call in _AGENT_CALLS):
                expected.add(op.bin_name)
    assert set(NS_AGENT_OPS) == expected


def test_the_pattern_does_not_take_one_op_for_another():
    assert translate._ns_agent_ops_in("nextseek-graph-schema") == []
    assert translate._ns_agent_ops_in("nextseek-api-readme") == []
    assert translate._ns_agent_ops_in("xnextseek-graph") == []
