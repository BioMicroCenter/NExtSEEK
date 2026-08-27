"""tool_select_pipeline: evidence in, one small verdict out, never an exception."""
import json

import pytest

from chat_nextseek.pipeline import agent_tools
from chat_nextseek.pipeline.sample_digest import DigestError
from chat_nextseek.pipeline.selection import Verdict
from chat_nextseek.pipeline.selection_context import PayloadTooLargeError

DIGEST = {"n_uids": 2, "metadata_summary": {}, "grouping_candidates": {}, "protocols": {}}


class _Config:
    LOG_DIR = "."

    def get_agent_model(self, key):
        return object(), "test-model", None


class _RaisingConfig(_Config):
    """A config whose model catalog is broken — get_agent_model raises."""

    def get_agent_model(self, key):
        raise KeyError("model")


@pytest.fixture(autouse=True)
def _clean():
    """No cache to clear any more (Task 6 removed metadata_cache), but
    test_the_tool_is_exposed_first still names this fixture explicitly, so it
    must keep existing."""
    yield


@pytest.fixture
def patched(monkeypatch):
    """Stub every boundary: the digest build, the payload build, and the model."""
    calls = {"digest": 0, "decide": 0}

    def fake_digest(config, uids, **kwargs):
        calls["digest"] += 1
        return DIGEST

    class _Ctx:
        def to_prompt_text(self, sections=None):
            return "PAYLOAD"

        def size_report(self, sections=None):
            return {"est_tokens": 1234}

    def fake_context(**kwargs):
        calls["context"] = kwargs
        return _Ctx()

    def fake_decide(**kwargs):
        calls["decide"] += 1
        calls["decide_kwargs"] = kwargs
        return Verdict(kind="chosen", pipelines=["rnaseq"], reason="bulk polyA")

    monkeypatch.setattr(agent_tools, "build_sample_digest", fake_digest)
    monkeypatch.setattr(agent_tools, "build_selection_context", fake_context)
    monkeypatch.setattr(agent_tools.selection, "decide", fake_decide)
    monkeypatch.setattr(agent_tools, "load_atlas",
                        lambda: {"pipelines": {"rnaseq": {"revision": "3.18.0"}}})
    return calls


def _call(tool_input, config=None, session=None, state=None, **kw):
    return json.loads(agent_tools.tool_select_pipeline(
        config or _Config(), session or {}, state if state is not None else {},
        tool_input, **kw))


def test_chosen_verdict_round_trips(patched):
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "expression?"})
    assert out["ok"] is True
    assert out["verdict"] == "chosen"
    assert out["pipelines"] == ["rnaseq"]
    assert out["reason"] == "bulk polyA"


def test_the_question_reaches_the_model_verbatim(patched):
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"],
           "question": "Which isoforms shift between groups?"})
    assert patched["decide_kwargs"]["question"] == "Which isoforms shift between groups?"


def test_only_the_three_live_sections_are_requested(patched):
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert patched["context"]["sections"] == ("atlas", "digest", "schemas")


def test_atlas_keys_are_passed_so_invented_keys_can_be_caught(patched):
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert patched["decide_kwargs"]["atlas_keys"] == {"rnaseq"}


def test_missing_question_is_a_tool_error_not_a_verdict():
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"]})
    assert out["ok"] is False
    assert "question" in out["error"]


def test_explicit_uids_with_empty_list_is_a_tool_error_not_a_verdict():
    out = _call({"kind": "explicit_uids", "uids": [], "question": "q"})
    assert out["ok"] is False
    assert "uids" in out["error"]


def test_unknown_kind_is_a_tool_error_not_a_verdict():
    out = _call({"kind": "bogus", "question": "q"})
    assert out["ok"] is False
    assert "bogus" in out["error"]


def test_accessions_are_out_of_scope_with_a_reason():
    out = _call({"kind": "accessions", "accessions": ["SRR123"], "question": "q"})
    assert out["verdict"] == "out_of_scope"
    assert "accession" in out["reason"].lower()


def test_no_pinned_search_is_out_of_scope():
    out = _call({"kind": "last_search", "question": "q"}, session={})
    assert out["verdict"] == "out_of_scope"


def test_a_cohort_over_the_cap_is_out_of_scope_naming_the_cap(patched):
    uids = [f"D.SEQ-{i}" for i in range(agent_tools.MAX_SELECTION_UIDS + 1)]
    out = _call({"kind": "explicit_uids", "uids": uids, "question": "q"})
    assert out["verdict"] == "out_of_scope"
    assert str(agent_tools.MAX_SELECTION_UIDS) in out["reason"]
    assert patched["digest"] == 0          # and it never paid for the digest


def test_digest_error_is_out_of_scope(monkeypatch, patched):
    def boom(config, uids, **kwargs):
        raise DigestError("Sample metadata fetch failed: 403")
    monkeypatch.setattr(agent_tools, "build_sample_digest", boom)
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert out["verdict"] == "out_of_scope"
    assert "403" in out["reason"]


def test_payload_too_large_is_out_of_scope(monkeypatch, patched):
    def boom(**kwargs):
        raise PayloadTooLargeError("exceeds ceiling")
    monkeypatch.setattr(agent_tools, "build_selection_context", boom)
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert out["verdict"] == "out_of_scope"


def test_an_unexpected_exception_is_out_of_scope_not_a_crash(monkeypatch, patched):
    def boom(**kwargs):
        raise ZeroDivisionError("nope")
    monkeypatch.setattr(agent_tools, "build_selection_context", boom)
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert out["verdict"] == "out_of_scope"
    assert "ZeroDivisionError" in out["reason"]


def test_a_slow_digest_times_out_into_out_of_scope(monkeypatch, patched):
    import time

    def slow(config, uids, **kwargs):
        time.sleep(2)
        return DIGEST

    monkeypatch.setattr(agent_tools, "build_sample_digest", slow)
    monkeypatch.setattr(agent_tools, "DIGEST_TIMEOUT_SECONDS", 0.05)
    t0 = time.perf_counter()
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    elapsed = time.perf_counter() - t0
    assert out["verdict"] == "out_of_scope"
    assert "timed out" in out["reason"].lower()
    # The actual behaviour under test: the call must return promptly once the
    # DIGEST_TIMEOUT_SECONDS deadline passes, not wait out the abandoned
    # future's 2-second sleep. A `with ThreadPoolExecutor(...) as pool:` still
    # returns the right verdict here but only after ~2s, because __exit__
    # calls shutdown(wait=True) — this bound is what catches that regression.
    assert elapsed < 1.0


def test_the_verdict_is_recorded_in_state(patched):
    state = {}
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"}, state=state)
    assert state["selection"]["verdict"] == "chosen"
    assert state["selection"]["pipelines"] == ["rnaseq"]


def test_selection_never_sets_pipeline_key(patched):
    """resolve_samples reads state['pipeline_key'] BEFORE tool_input. Writing it
    here would make a fork the user redirects resolve on the wrong pipeline."""
    state = {}
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"}, state=state)
    assert "pipeline_key" not in state


def test_progress_events_are_emitted_in_order(patched):
    seen = []
    _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"},
          send_event=lambda name, payload: seen.append(name))
    assert seen == ["selection_started", "selection_evidence_ready", "selection_done"]


@pytest.mark.parametrize("tool_input", [
    {"kind": "accessions", "accessions": ["SRR123"], "question": "q"},
    {"kind": "last_search", "question": "q"},
    {"kind": "explicit_uids",
     "uids": [f"D.SEQ-{i}" for i in range(agent_tools.MAX_SELECTION_UIDS + 1)],
     "question": "q"},
], ids=["accessions", "no_pinned_search", "over_cap"])
def test_early_verdicts_still_pair_started_with_done(patched, tool_input):
    """These three exits return a verdict before the digest is ever built, but a
    verdict is still a verdict — a UI that opened a progress row on
    selection_started must not be left with an orphan selection_done."""
    seen = []
    _call(tool_input, session={}, send_event=lambda name, payload: seen.append(name))
    assert seen == ["selection_started", "selection_done"]


def test_a_malformed_call_emits_nothing(patched):
    """kind='explicit_uids' with an empty list is a tool error, not a verdict —
    it must not open a progress row that nothing will ever close."""
    seen = []
    _call({"kind": "explicit_uids", "uids": [], "question": "q"},
          send_event=lambda name, payload: seen.append(name))
    assert seen == []


def test_no_send_event_is_fine(patched):
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"},
                send_event=None)
    assert out["ok"] is True


def test_a_broken_model_config_is_out_of_scope_not_a_crash(patched):
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"},
                config=_RaisingConfig())
    assert out["verdict"] == "out_of_scope"
    assert "model" in out["reason"].lower()


def test_a_raising_send_event_does_not_crash_the_call(patched):
    def boom(name, payload):
        raise RuntimeError("websocket gone")
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"},
                send_event=boom)
    assert out["ok"] is True
    assert out["verdict"] == "chosen"


def test_a_raising_size_report_does_not_crash_the_call(monkeypatch, patched):
    class _BrokenSizeCtx:
        def to_prompt_text(self, sections=None):
            return "PAYLOAD"

        def size_report(self, sections=None):
            raise RuntimeError("size boom")

    monkeypatch.setattr(agent_tools, "build_selection_context",
                        lambda **kwargs: _BrokenSizeCtx())
    out = _call({"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"})
    assert out["ok"] is True
    assert out["verdict"] == "chosen"


def test_the_tool_is_exposed_first(_clean):
    names = [t["name"] for t in agent_tools.build_pipeline_tool_schemas(_Config())]
    assert names[0] == "select_pipeline"
    assert names[1] == "resolve_samples"


def test_the_schema_demands_a_verbatim_question():
    schema = agent_tools._SCHEMA_BY_NAME["select_pipeline"]
    props = schema["input_schema"]["properties"]
    assert schema["input_schema"]["required"] == ["kind", "question"]
    assert "verbatim" in props["question"]["description"].lower()


def test_dispatch_routes_select_pipeline(patched):
    out = json.loads(agent_tools.dispatch_pipeline_tool_call(
        config=_Config(), session={}, state={}, name="select_pipeline",
        tool_input={"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"},
        log_dir="."))
    assert out["verdict"] == "chosen"


def test_dispatch_forwards_send_event(patched):
    seen = []
    agent_tools.dispatch_pipeline_tool_call(
        config=_Config(), session={}, state={}, name="select_pipeline",
        tool_input={"kind": "explicit_uids", "uids": ["D.SEQ-1"], "question": "q"},
        log_dir=".", send_event=lambda n, p: seen.append(n))
    assert "selection_done" in seen


def test_max_iter_has_headroom_for_the_extra_tool():
    from chat_nextseek.pipeline import agent
    assert agent.MAX_ITER >= 13


def test_the_prompt_teaches_every_verdict():
    from pathlib import Path

    import chat_nextseek

    text = (Path(chat_nextseek.__file__).parent / "prompts" / "pipeline_agent.txt").read_text()
    for token in ("select_pipeline", "chosen", "fork", "refused", "out_of_scope"):
        assert token in text, f"the prompt never mentions {token!r}"
    # The skip condition is the thing most likely to be dropped in an edit, and
    # dropping it makes every named-pipeline build pay for a selection call.
    assert "named a pipeline" in text.lower()
    # The agent must not tell the user about the machinery.
    assert "Do not mention select_pipeline" in text


def test_agent_loop_threads_send_event_into_the_tool(monkeypatch):
    """The events exist only if the loop passes the callback down."""
    from chat_nextseek.pipeline import agent

    seen = []
    captured = {}

    def fake_dispatch(*, config, session, state, name, tool_input, log_dir, send_event=None):
        captured["send_event"] = send_event
        if send_event:
            send_event("selection_started", {})
        return json.dumps({"ok": True, "verdict": "chosen", "pipelines": ["rnaseq"]})

    class _Client:
        def __init__(self):
            self.n = 0

        def chat_with_tools(self, *, messages, tools, system, model):
            self.n += 1
            if self.n == 1:
                return {"content": [{"type": "tool_use", "id": "t1",
                                     "name": "select_pipeline",
                                     "input": {"kind": "explicit_uids",
                                               "uids": ["D.SEQ-1"], "question": "q"}}]}
            return {"content": [{"type": "text", "text": "Using nf-core/rnaseq."}]}

    class _Cfg:
        LOG_DIR = "."
        PIPELINE_LAUNCH_MODE = "luria"

        def get_agent_model(self, key):
            return _Client(), "m", None

        def _load_prompt(self, name):
            return "prompt {catalog} {launch_mode}"

    monkeypatch.setattr(agent, "dispatch_pipeline_tool_call", fake_dispatch)
    monkeypatch.setattr(agent, "catalog_for_prompt", lambda: "CATALOG")

    session = {}
    agent.start(session, _Cfg(), user_query="which isoforms change?",
                send_event=lambda n, p: seen.append(n))
    assert captured["send_event"] is not None
    assert "selection_started" in seen


def test_agent_start_without_send_event_still_works(monkeypatch):
    """The CC bridge calls start() with no callback; that must stay legal."""
    from chat_nextseek.pipeline import agent
    import inspect

    sig = inspect.signature(agent.start)
    assert sig.parameters["send_event"].default is None
    sig2 = inspect.signature(agent.handle_turn)
    assert sig2.parameters["send_event"].default is None
