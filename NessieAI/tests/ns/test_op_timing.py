"""Round 6 (SPEC-1 T4): every op logs, once, how many seconds after the view took it each step happened."""
import logging
import time

from NessieAI.ns import granular


def test_an_op_logs_one_timing_line_with_its_steps(monkeypatch, caplog):
    def fake(args, config, session, write_gate, neo4j_exec, outputs_dir, *, limit_s=None, turn=None):
        from chat_nextseek import call_scope
        call_scope.mark("parser_end")
        return {"ok": True}

    monkeypatch.setitem(granular._HANDLERS, "graph", fake)
    caplog.set_level(logging.INFO, logger="dmac.op_timing")
    out = granular.run_op("graph", {"query": "q"}, config=None, session=None, write_gate=lambda *a: None,
                          limit_s=90.0, timing=[("view", time.monotonic())])
    assert out == {"ok": True}
    lines = [r.getMessage() for r in caplog.records if r.name == "dmac.op_timing"]
    assert len(lines) == 1
    print(lines[0])
    assert lines[0].startswith("op_timing op=graph turn=None limit=90.0 view=0.00 ")
    for step in ("scope=", "parser_end=", "end="):
        assert step in lines[0]


def test_a_mark_outside_any_scope_is_a_no_op():
    from chat_nextseek import call_scope
    call_scope.mark("anything")  # never raises


def test_a_logging_fault_never_costs_the_answer(monkeypatch):
    monkeypatch.setitem(granular._HANDLERS, "graph", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(granular, "_TIMING_LOG", None)  # .info raises AttributeError
    out = granular.run_op("graph", {"query": "q"}, config=None, session=None, write_gate=lambda *a: None,
                          limit_s=90.0, timing=[("view", time.monotonic())])
    assert out == {"ok": True}
