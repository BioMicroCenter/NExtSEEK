"""SEC-002: the plan-mode context engineer's model-written code runs in the row_compute sandbox.

Before the fix ``context_engineer_step`` passed the code to ``exec`` with full builtins in the app process. Now it
goes through ``row_compute.run_code_isolated``: code outside the memory-code subset is refused and the step falls
back to LLM extraction. Every model call is mocked.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from chat_nextseek.agents.planner import agent as planner_mod
from chat_nextseek.schemas import ContextEngineerOutput, PlanStep

ROWS = [{"uid": "TIS-1", "type": "TIS"}, {"uid": "<a>x</a>", "type": "CEL"}]
FALLBACK = '{"uids": ["FROM-FALLBACK"]}'


def _run(monkeypatch, code):
    monkeypatch.setattr(planner_mod, "call_llm_structured", lambda **kw: ContextEngineerOutput(
        extraction_code=code, enriched_context={}, method="code"))
    fallback_calls = []

    def _text(config, **kwargs):
        fallback_calls.append(kwargs)
        return FALLBACK

    monkeypatch.setattr(planner_mod, "call_llm_text", _text)
    config = SimpleNamespace(CONTEXT_ENGINEER_SYSTEM_PROMPT="s",
                             get_agent_model=lambda label: (None, "m", None))
    step = PlanStep(step_id=1, tool="graph_query", context_prompt="find tissue", extraction_hint="uids")
    out = planner_mod.context_engineer_step(config, step, {"ok": True, "tool": "graph_query",
                                                           "output": {"data": ROWS}})
    return out, fallback_calls


@pytest.mark.parametrize("code", [
    'result = {"pid": __import__("os").getpid()}',
    'result = {"text": open("/etc/hostname").read()}',
    'result = {"cls": ().__class__.__base__.__subclasses__()}',
], ids=["import", "open", "dunder"])
def test_hostile_code_is_refused_and_the_llm_fallback_answers(monkeypatch, code):
    out, fallback_calls = _run(monkeypatch, code)

    assert out.method == "llm"
    assert len(fallback_calls) == 1
    assert out.enriched_context == {"uids": ["FROM-FALLBACK"]}
    assert "pid" not in out.enriched_context and "text" not in out.enriched_context


def test_the_code_never_runs_in_this_process(monkeypatch):
    """Even code the subset allows runs in a child: it sees the rows, not this process's state."""
    marker = f"pid-{os.getpid()}"
    out, _ = _run(monkeypatch, 'result = {"n": len(rows), "keys": sorted(data.keys())}')

    assert out.method == "code"
    assert out.enriched_context == {"n": 2, "keys": ["data", "ok", "output", "tool"]}
    assert marker not in str(out.enriched_context)


@pytest.mark.parametrize("code,expected", [
    ('result = {"uids": [r.get("uid") or r.get("s.UID") or r.get("s.uid") for r in rows if r]}\n'
     'result["uids"] = [u for u in result["uids"] if u and "<" not in str(u)]', {"uids": ["TIS-1"]}),
    ('result = {"sample_types": sorted({r.get("type") for r in rows if r.get("type")})}',
     {"sample_types": ["CEL", "TIS"]}),
    ('summary = data.get("output", {}).get("data", {}) or {}\n'
     'result = {"n": len(summary)}', {"n": 2}),
], ids=["uids", "types", "data-dict"])
def test_the_prompt_examples_still_run_as_code(monkeypatch, code, expected):
    """The shapes context_engineer.txt teaches pass the sandbox, so plan turns keep their code path."""
    out, fallback_calls = _run(monkeypatch, code)

    assert out.method == "code"
    assert fallback_calls == []
    assert out.enriched_context == expected
