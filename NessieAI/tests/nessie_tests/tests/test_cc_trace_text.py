"""`cc_trace_text`: what a Container-CC turn did, exposed to criteria (2026-09-23).

The follow-up probe asserts that CC read the previous turn's files and did not rebuild
the result from scratch; the only place a CC turn's tool calls reach the harness is the
`cc_traces` list on its `query_complete` event.
"""
from __future__ import annotations

from NessieAI.tests.nessie_tests import evaluate, runner


def _payload(cc_traces=None, debug=None):
    data = {"reply": "ok"}
    if cc_traces is not None:
        data["cc_traces"] = cc_traces
    if debug is not None:
        data["debug"] = debug
    return {"progress": [{"event": "route_decided", "data": {"route": "container_cc"}},
                         {"event": "query_complete", "data": data}]}


TRACE = [{"steps": [
    {"line": 1, "kind": "text", "text": "Reading the previous turn."},
    {"line": 2, "kind": "read", "tool": "Read", "detail": "/data/previous_turns/MANIFEST.md"},
    {"line": 3, "kind": "bash", "tool": "Bash",
     "detail": "python -c 'import polars as pl; pl.read_csv(\"/data/previous_turns/turn-01/rows.csv\")'"},
    {"line": 4, "kind": "bash", "tool": "Bash", "detail": "nextseek-aggregate --query 'species'"},
]}]


def test_the_trace_becomes_one_line_per_tool_call():
    text = evaluate.build_observed_debug(_payload(TRACE))[evaluate.CC_TRACE_TEXT_FIELD]
    lines = text.splitlines()
    assert lines[0] == "read Read /data/previous_turns/MANIFEST.md"
    assert lines[2] == "bash Bash nextseek-aggregate --query 'species'"
    assert "Reading the previous turn" not in text          # narration is not a call


def test_a_criterion_can_read_it_and_it_survives_a_forced_cc_arm():
    debug = evaluate.build_observed_debug(_payload(TRACE))
    from NessieAI.tests.e2e.criteria import check_pass
    passed, _ = check_pass(debug, [
        {"field": "cc_trace_text", "op": "matches_re", "value": r"/data/previous_turns/turn-01/rows\.csv"},
        {"field": "cc_trace_text", "op": "matches_re", "value": r"(?s)\A(?!.*nextseek-query)"},
    ])
    assert passed
    assert not evaluate.is_ns_pipeline_internal("cc_trace_text")


def test_no_trace_no_field():
    assert evaluate.build_observed_debug(_payload()) == {}
    assert evaluate.build_observed_debug(_payload([])) == {}
    assert evaluate.build_observed_debug(_payload(["junk", {"steps": "x"}])) == {"cc_trace_text": ""}
    assert evaluate.build_observed_debug(_payload(debug={"a": 1})) == {"a": 1}


def test_followup_is_a_routing_decision_and_cc_unavailable_is_not():
    assert "followup" in runner.ROUTE_DECISION_SOURCES
    assert "cc_unavailable" not in runner.ROUTE_DECISION_SOURCES
