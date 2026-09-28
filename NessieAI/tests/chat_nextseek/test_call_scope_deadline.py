"""An op's deadline reaches every model call inside it, so the move happens inside the op (F4, ruling D5, 2026-09-28).

The Container-CC sidecar waits 60 s for an op (ns-sidecar/app/ns_client.py), but the NS agents behind it had their
own budgets (300 s first try), so a Gemini stall failed the op at 60 s and the fallback, 300 s in, was never reached;
Django then kept spending model calls nobody read for about ten minutes. Each op now opens a scope with a 55 s
deadline (``ns/granular.run_op``), and every attempt's wall clock is cut to fit it:

* a first try with a move still possible: at most what is left minus 20 s (the reserve for the move), never below
  5 s, never more than what is left;
* the moved call, or a call with nowhere to move: at most what is left;
* 2 s or less left before a call starts: no call; ``LLMFatalError`` with ``reason="deadline"`` (not unavailability:
  no model failed), and one ``deadline`` ledger record;
* an attempt whose window the deadline cut is recorded ``deadline_capped``, and its timeout marks nothing;
* with no deadline (every NS turn) nothing changes.

The clock is faked; the calls run through the real ladder with stand-in clients.
"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from chat_nextseek import call_scope
from chat_nextseek.llm_clients import LLMFatalError, LLMResponse, LLMTimeoutError
from chat_nextseek.schemas import schema_helper
from chat_nextseek.schemas.schema_helper import call_llm_structured

FLASH = "gemini-3.5-flash"
PRO = "gemini-3.1-pro-preview"
SONNET = "us.anthropic.claude-sonnet-4-6"
OPUS = "us.anthropic.claude-opus-4-7"


class _Plan(BaseModel):
    mode: str = "unsupported"


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(call_scope, "_monotonic", c)
    return c


@pytest.fixture
def run(monkeypatch, clock):
    """Stand in for the wall-clock call: a stalled model eats its whole window, a healthy one ``answer_s`` seconds.

    Returns the list of (model, window) every attempt got.
    """
    windows: list[tuple[str, float]] = []
    behaviour: dict[str, object] = {}

    def fake(*, client, model_name, timeout_seconds, **kw):
        windows.append((model_name, round(timeout_seconds, 3)))
        what = behaviour.get(model_name, 5.0)
        if what == "stall":
            clock.now += timeout_seconds
            raise LLMTimeoutError(f"LLM call timed out after {timeout_seconds} seconds")
        clock.now += what
        return LLMResponse(content='{"mode": "' + model_name + '"}', raw=None, usage=None, model=model_name,
                           provider=client.provider, metadata={"stop_reason": "end_turn"})

    monkeypatch.setattr(schema_helper, "_call_llm_with_timeout", fake)
    return windows, behaviour


class _Client:
    def __init__(self, provider):
        self.provider = provider

    def reset_connections(self):
        return True


class _Config:
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None, "high": 16000}
    LLM_MODEL = FLASH

    def __init__(self, log_dir=None):
        self.LOG_DIR = log_dir
        self.gcp, self.anth = _Client("gcp"), _Client("bedrock")
        self.LLM_CLIENT = self.gcp
        self.LLM_CLIENTS = {"gcp": self.gcp, "anth": self.anth}
        self.AGENT_MODEL_CATALOG = {
            "anth:current": {a: {"provider": "anth", "model": SONNET, "thinking_level": None}
                             for a in ("entity", "graph", "api", "chatter")},
            "gcp:current": {"parser": {"provider": "gcp", "model": PRO, "thinking_level": "high"}},
        }


def _call(config, agent, model):
    client = config.anth if model == OPUS else config.gcp
    return call_llm_structured(config, "q", _Plan, system="s", client=client, model_name=model, agent_label=agent)


def _op_scope(seconds):
    """An op scope with a deadline: what run_op opens."""
    return call_scope.scope(deadline_s=seconds)


def test_a_gemini_stall_inside_a_graph_op_moves_and_answers_inside_55_s(run, clock):
    """The graph op's chain: entity (Gemini, stalled), parser (Opus, fine), graph agent (Gemini, remembered)."""
    windows, behaviour = run
    behaviour.update({FLASH: "stall", SONNET: 6.0, OPUS: 5.0})
    config = _Config()
    start = clock.now
    with _op_scope(55):
        assert _call(config, "entity", FLASH).mode == SONNET
        assert _call(config, "parser", OPUS).mode == OPUS
        assert _call(config, "graph", FLASH).mode == SONNET
    assert clock.now - start < 55
    assert windows[0] == (FLASH, 20), "the entity's own 20 s first try fits: 55 - 20 = 35 is more"
    assert windows[1] == (SONNET, 35), "the move gets what is left, not its full 90 s"
    assert [m for m, _ in windows] == [FLASH, SONNET, OPUS, SONNET], "the graph agent skips the stalled Gemini"


def test_the_first_try_leaves_the_move_its_reserve(run, clock):
    windows, behaviour = run
    behaviour.update({FLASH: "stall", SONNET: 1.0})
    with _op_scope(55):
        clock.now += 25  # 30 s left: the graph agent's 60 s first try is cut to 30 - 20 = 10 s
        _call(_Config(), "graph", FLASH)
    assert windows == [(FLASH, 10), (SONNET, 20)]


def test_the_first_try_never_drops_below_5_s(run, clock):
    windows, behaviour = run
    behaviour.update({FLASH: 1.0})
    with _op_scope(55):
        clock.now += 45  # 10 s left: 10 - 20 is below the floor
        _call(_Config(), "graph", FLASH)
    assert windows == [(FLASH, 5)]


def test_a_call_with_nowhere_to_move_gets_what_is_left(run, clock):
    windows, behaviour = run
    behaviour.update({FLASH: 1.0})
    config = _Config()
    config.AGENT_MODEL_CATALOG = {}
    with _op_scope(55):
        clock.now += 45
        _call(config, "graph", FLASH)
    assert windows == [(FLASH, 10)]


def test_no_call_starts_with_two_seconds_or_less_left(run, clock, tmp_path):
    windows, behaviour = run
    config = _Config(log_dir=str(tmp_path))
    with _op_scope(55):
        clock.now += 53
        with pytest.raises(LLMFatalError) as excinfo:
            _call(config, "graph", FLASH)
    assert windows == [], "no model is called"
    fatal = excinfo.value
    assert fatal.reason == "deadline" and fatal.unavailable is False
    assert str(fatal).startswith("deadline: the op's 55 s ran out before the graph call could start")
    (entry,) = [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]
    assert entry["outcome"] == "deadline" and entry["agent"] == "graph"


def test_a_window_the_deadline_cut_is_recorded_and_its_timeout_marks_nothing(run, clock, tmp_path):
    windows, behaviour = run
    behaviour.update({FLASH: "stall", SONNET: "stall"})
    config = _Config(log_dir=str(tmp_path))
    with _op_scope(55) as scope:
        clock.now += 25  # 30 s left
        with pytest.raises(LLMFatalError):
            _call(config, "graph", FLASH)
        entries = [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]
        assert [(e["model"], e["timeout_seconds"], e.get("deadline_capped")) for e in entries] == [
            (FLASH, 10, True), (SONNET, 20, True)]
        assert scope.failed(("gcp", FLASH)) is None and scope.failed(("anth", SONNET)) is None


def test_with_no_deadline_nothing_changes(run, clock):
    windows, behaviour = run
    behaviour.update({FLASH: "stall", SONNET: 1.0})
    with call_scope.scope():
        clock.now += 10_000
        _call(_Config(), "graph", FLASH)
    assert windows == [(FLASH, 60), (SONNET, 90)]


def test_a_later_deadline_never_extends_an_earlier_one(clock):
    with call_scope.scope(deadline_s=55) as outer:
        with call_scope.scope(deadline_s=80) as inner:
            assert inner is outer
            assert outer.remaining() == pytest.approx(55)
        call_scope.limit_current(50)
        assert outer.remaining() == pytest.approx(50)
    assert call_scope.limit_current(10) is None, "no scope, nothing to limit"
