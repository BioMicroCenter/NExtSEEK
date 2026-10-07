"""An op's deadline reaches every model call inside it, so the move happens inside the op (F4, ruling D5, 2026-09-28).

The Container-CC sidecar waits 60 s for an op (ns-sidecar/app/ns_client.py), but the NS agents behind it had their
own budgets (300 s first try), so a Gemini stall failed the op at 60 s and the fallback, 300 s in, was never reached;
Django then kept spending model calls nobody read for about ten minutes. Each op now opens a scope with a 55 s
deadline (``ns/granular.run_op``), and every attempt's wall clock is cut to fit it:

* a first try with a move still possible: at most what is left minus 20 s (the reserve for the move), never below
  5 s, never more than what is left. Not for the report writer (operator ruling on review
  finding 1, option A): the move could not redo its work in 20 s, so its first try gets what is left, as before;
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
            "gcp:current": {a: {"provider": "gcp", "model": PRO, "thinking_level": "high"}
                            for a in ("parser", "report_writer")},
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
    assert windows[0] == (FLASH, 30), "the entity's own 30 s first try fits: 55 - 20 = 35 is more"
    assert windows[1] == (SONNET, 25), "the move gets what is left after the 30 s stall, not its full 90 s"
    assert [m for m, _ in windows] == [FLASH, SONNET, OPUS, SONNET], "the graph agent skips the stalled Gemini"


def test_the_first_try_leaves_the_move_its_reserve(run, clock):
    windows, behaviour = run
    behaviour.update({FLASH: "stall", SONNET: 1.0})
    with _op_scope(55):
        clock.now += 25  # 30 s left: the api agent's 30 s first try is cut to 30 - 20 = 10 s
        _call(_Config(), "api", FLASH)
    assert windows == [(FLASH, 10), (SONNET, 20)]


def test_the_first_try_never_drops_below_5_s(run, clock):
    windows, behaviour = run
    behaviour.update({FLASH: 1.0})
    with _op_scope(55):
        clock.now += 45  # 10 s left: 10 - 20 is below the floor
        _call(_Config(), "api", FLASH)
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
        with pytest.raises(LLMFatalError) as excinfo:
            _call(config, "api", FLASH)
        # The op ran out of time on a cut window: the deadline, not a model outage (review, 2026-09-28).
        assert excinfo.value.reason == "deadline" and excinfo.value.unavailable is False
        assert str(excinfo.value).startswith("deadline: the op's 55 s ran out during the api call")
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


def test_a_timeout_on_a_window_the_deadline_did_not_cut_is_still_an_outage(run, clock):
    """Plenty of time left: both models stalling on their full windows is unavailability, as before."""
    windows, behaviour = run
    behaviour.update({FLASH: "stall", SONNET: "stall"})
    with _op_scope(1000):
        with pytest.raises(LLMFatalError) as excinfo:
            _call(_Config(), "entity", FLASH)
    assert windows == [(FLASH, 30), (SONNET, 90)]
    assert excinfo.value.reason == "timeout" and excinfo.value.unavailable is True


def test_the_deadline_record_names_a_move_made_just_before_it(run, clock, tmp_path):
    windows, behaviour = run
    behaviour.update({FLASH: "stall"})
    config = _Config(log_dir=str(tmp_path))
    with _op_scope(55):
        clock.now += 48  # 7 s left: a 5 s first try (the floor) stalls, and 2 s is too little for the move
        with pytest.raises(LLMFatalError) as excinfo:
            _call(config, "api", FLASH)
    assert windows == [(FLASH, 5)], "the fallback is never called"
    assert excinfo.value.reason == "deadline"
    entries = [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]
    (deadline,) = [e for e in entries if e["outcome"] == "deadline"]
    assert deadline["model"] == SONNET
    assert deadline["fallback_from"] == FLASH and deadline["fallback_reason"] == "timeout"


def test_the_tool_loop_ends_on_the_deadline_too(run, clock, monkeypatch):
    from chat_nextseek import tool_loop

    def fake(fn, timeout):
        clock.now += timeout
        raise LLMTimeoutError(f"LLM call timed out after {timeout} seconds")

    monkeypatch.setattr(tool_loop, "_run_with_wall_clock", fake)

    class _Tools:
        provider = "bedrock"

        def reset_connections(self):
            return True

        def chat_with_tools(self, **kw):  # pragma: no cover - the wall clock stands in for it
            raise AssertionError

    client = _Tools()
    config = _Config()
    config.LLM_CLIENTS = {"anth": client}
    config.AGENT_MODEL_CATALOG = {"_fallback": {"followup": {"provider": "anth", "model": SONNET,
                                                              "thinking_level": None}}}
    with _op_scope(55):
        clock.now += 30  # 25 s left: 5 s first try, then 20 s
        with pytest.raises(LLMFatalError) as excinfo:
            tool_loop.call_tools(config, messages=[], tools=[], system="s", model_name=OPUS, client=client,
                                 agent_label="followup")
    assert excinfo.value.reason == "deadline" and excinfo.value.unavailable is False


# --------------------------------------------------------------------------
# Option A (operator ruling on review finding 1, 2026-09-28): no reserve for the report writer. Round 6: the graph
# agent no longer skips it.
# --------------------------------------------------------------------------

def test_a_graph_answer_late_in_an_op_leaves_the_move_its_reserve(run, clock):
    """entity and parser took 20 s: 35 s are left. Round 6: the graph agent keeps the move reserve, so its first
    try gets min(60, max(35 - 20, 5)) = 15 s and the move keeps 20 s."""
    windows, behaviour = run
    behaviour.update({FLASH: 10.0})
    with _op_scope(55):
        clock.now += 20
        assert _call(_Config(), "graph", FLASH).mode == FLASH
    assert windows == [(FLASH, 15)], "what is left less the move reserve"


def test_the_graph_agents_repair_calls_keep_the_reserve_too(run, clock):
    """The repair call runs on the same catalog key, so the same row (round 6: the graph agent keeps the move
    reserve): cut to leave room for a move."""
    windows, behaviour = run
    behaviour.update({FLASH: 1.0})
    config = _Config()
    with _op_scope(55):
        clock.now += 20
        call_llm_structured(config, "q", _Plan, system="s", client=config.gcp, model_name=FLASH,
                            agent_label="graph", log_label="graph_agent_repair")
    assert windows == [(FLASH, 15)]


def test_a_stalled_graph_agent_late_in_an_op_ends_on_the_deadline_not_as_an_outage(run, clock):
    windows, behaviour = run
    behaviour.update({FLASH: "stall", SONNET: "stall"})
    with _op_scope(55) as scope:
        clock.now += 20
        with pytest.raises(LLMFatalError) as excinfo:
            _call(_Config(), "graph", FLASH)
        assert scope.failed(("gcp", FLASH)) is None, "a window the deadline cut marks nothing"
    assert windows == [(FLASH, 15), (SONNET, 20)], "round 6: the graph agent keeps the move reserve; no call starts after the deadline"
    assert excinfo.value.reason == "deadline" and excinfo.value.unavailable is False


def test_the_report_writer_in_generate_submission_gets_what_is_left(run, clock):
    """After 25 s of metadata and protocol preparation 30 s are left; the writer used to be cut to 10 s."""
    windows, behaviour = run
    behaviour.update({OPUS: 28.0})
    with _op_scope(55):
        clock.now += 25
        assert _call(_Config(), "report_writer", OPUS).mode == OPUS
    assert windows == [(OPUS, 30)], "its chain (Gemini 3.1 Pro) exists, and still no reserve is taken"


@pytest.mark.parametrize("agent, model, fallback", [
    ("entity", FLASH, SONNET), ("api", FLASH, SONNET), ("chatter", FLASH, SONNET), ("parser", OPUS, PRO),
], ids=["entity", "api", "chatter", "parser"])
def test_the_short_agents_keep_their_move_inside_the_op(run, clock, agent, model, fallback):
    windows, behaviour = run
    behaviour.update({model: "stall", fallback: 3.0})
    with _op_scope(55):
        clock.now += 25  # 30 s left: a first try of at most 10 s, then the move
        assert _call(_Config(), agent, model).mode == fallback
    assert windows == [(model, 10), (fallback, 20)]


# --- approach 1, piece 2: the waits that are not model calls ---------------------------------------------------

def test_a_wait_outside_any_scope_keeps_its_own_length():
    assert call_scope.time_left_for(90.0) == 90.0


def test_an_ns_turn_scope_has_no_deadline_so_nothing_is_cut():
    with call_scope.scope():
        assert call_scope.time_left_for(120.0) == 120.0


def test_a_wait_inside_an_op_is_cut_to_the_time_left(clock):
    with _op_scope(30.0):
        assert call_scope.time_left_for(90.0) == 30.0
        clock.now += 20.0
        assert call_scope.time_left_for(90.0) == pytest.approx(10.0)
        assert call_scope.time_left_for(5.0) == 5.0


def test_no_wait_starts_with_two_seconds_or_less_left(clock):
    with _op_scope(30.0):
        clock.now += 28.0
        assert call_scope.time_left_for(90.0) is None


def _run_op_scope(seconds):
    """What run_op opens since round 6: a deadline AND the op marker."""
    return call_scope.scope(deadline_s=seconds, op=True)


def test_inside_an_op_the_parser_gives_up_at_20_s(run, clock):
    """Round 6 (SPEC-1 T1): no healthy Opus parser call took over 28.1 s in 420; a hang inside an op moves at 20 s."""
    windows, behaviour = run
    behaviour.update({OPUS: "stall", PRO: 6.0})
    with _run_op_scope(90):
        assert _call(_Config(), "parser", OPUS).mode == PRO
    assert windows[0] == (OPUS, 20)
    assert windows[1][0] == PRO


def test_a_deadline_without_the_op_marker_keeps_the_parser_window(run, clock):
    """The vocabulary pre-run and a nested NS turn have a deadline but are not ops: the parser keeps 50 s."""
    windows, behaviour = run
    behaviour.update({OPUS: "stall", PRO: 6.0})
    with _op_scope(90):
        _call(_Config(), "parser", OPUS)
    assert windows[0] == (OPUS, 50)


def test_an_op_scope_shared_by_an_inner_scope_stays_an_op(run, clock):
    windows, behaviour = run
    behaviour.update({OPUS: "stall", PRO: 6.0})
    with _run_op_scope(90), call_scope.scope(deadline_s=80):
        _call(_Config(), "parser", OPUS)
    assert windows[0] == (OPUS, 20)
