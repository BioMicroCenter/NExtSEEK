"""Every terminal event the NS engine really sends validates against the CC plugin's models.

The Container-CC ops ``nextseek-query``, ``nextseek-plan`` and ``nextseek-pipeline``
poll an NS turn (``POST query/async/``, then ``tasks/<id>/progress/``) and validate its
terminal event with the plugin's own mirror of the event models
(``build_context/plugins/nextseek/bin/_assistant_models.py``, copied to ``/opt/dmac/`` by
the cc-agent Dockerfile). Those models are ``extra="forbid"``, so a key the server adds
and the mirror lacks fails the op with AGENT_FAILED after the NS turn has run and been
paid for. It happened with the turn record (``total_cost_usd``, ``cost_partial``,
``models_used``, ``model_fallback``) and with the fatal query_error (``fatal``, ``reason``,
``detail``, ``model_fallback``).

Hand-shaped payloads cannot catch that drift, so every payload here is built by the
server code that sends it: ``_emit_query_complete`` inside a collected turn, the fatal
handlers of ``run_query`` and ``run_query_plan``, and the pipeline body's
``_report_fatal``. Each is stored the way the async endpoint stores it (the DB callback
adds ``session_id``; the progress list is JSON) and validated against the plugin models,
directly and through the real ``AssistantClient.run_query``. The server's OpenAPI event
models (``nextseek_api.assistant.models_api``) are held to the same payloads, so the
documented schema cannot go stale either.

Hermetic: no model, no network, no database (the event store is a list).
"""
from __future__ import annotations

import importlib
import json
import sys
import uuid

import httpx
import pytest

from NessieAI import paths
from chat_nextseek import orchestrator, turn_spend
from chat_nextseek.llm_clients import LLMFatalError, LLMResponse

SID = str(uuid.UUID(int=0xAA))
TASK = str(uuid.UUID(int=0xBB))
OPUS = "us.anthropic.claude-opus-4-7"
FLASH = "gemini-3.5-flash"
MOVE = {"agent": "graph_agent", "from": OPUS, "to": FLASH, "reason": "timeout"}


@pytest.fixture(scope="module")
def plugin():
    sys.path.insert(0, str(paths.CC_PLUGIN_BIN))
    try:
        models = importlib.reload(importlib.import_module("_assistant_models"))
        client_mod = importlib.reload(importlib.import_module("_assistant_client"))
        yield models, client_mod
    finally:
        if str(paths.CC_PLUGIN_BIN) in sys.path:
            sys.path.remove(str(paths.CC_PLUGIN_BIN))


class _Store:
    """The async endpoint's event store: terminal events get the session id
    (``make_db_event_callback``), and the progress list is kept as JSON."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def __call__(self, name, data):
        if name in ("query_complete", "query_error"):
            data.setdefault("session_id", SID)
        self.events.append((name, json.loads(json.dumps(data, default=str))))

    def terminal(self, name):
        found = [d for e, d in self.events if e == name]
        assert found, f"no {name} was sent: {[e for e, _ in self.events]}"
        return found[-1]


def _spend_a_call():
    """One answered call that moved on a timeout: the turn record then has every key."""
    turn_spend.record_call(
        {"agent": "graph_agent", "provider": "gcp", "model": FLASH, "attempt": 2, "outcome": "ok",
         "fallback_from": OPUS, "fallback_reason": "timeout"},
        resp=LLMResponse(content="x", raw=None, model=FLASH, provider="gcp", metadata={},
                         usage={"prompt_tokens": 4000, "completion_tokens": 100, "thoughts_tokens": 300,
                                "cached_tokens": 1000}))


def _validate(plugin, name, data):
    """The plugin's mirror (what the ops enforce) and the server's own OpenAPI models
    (``nextseek_api.assistant.models_api``, which document the event) both accept it."""
    from nextseek_api.assistant import models_api

    models, _ = plugin
    for source in (models, models_api):
        model = source.QueryCompleteEvent if name == "query_complete" else source.QueryErrorEvent
        model(**data)


def _through_the_client(plugin, name, data):
    """The production seam: AssistantClient.run_query polling a task that ended on ``data``."""
    _, client_mod = plugin
    status = "completed" if name == "query_complete" else "error"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"task_id": TASK, "session_id": SID})
        return httpx.Response(200, json={"task_id": TASK, "session_id": SID, "status": status,
                                         "progress": [{"event": name, "data": data}], "result": data})

    client = client_mod.AssistantClient(base_url="http://test", assistant_prefix="nextseek_api/assistant",
                                        auth=("u", "p"), transport=httpx.MockTransport(handler), timeout=5)
    terminal, _ = client.run_query("q", mode="standard")
    return terminal


def _stub_turn(monkeypatch, tmp_path):
    """Let an orchestrator entry point run with no identity, log dir or artifact store."""
    monkeypatch.setattr(orchestrator, "_identity_gate", lambda session, config, *a, **k: (config, None))
    monkeypatch.setattr(orchestrator, "_ensure_query_log_dir", lambda session, config: str(tmp_path))
    monkeypatch.setattr(orchestrator, "ArtifactStore", lambda log_dir: None)
    monkeypatch.setattr(orchestrator, "_accepted_suggestion", lambda session, text: None)


def _raise_after_a_call(exc):
    def run(*_a, **_k):
        _spend_a_call()
        raise exc
    return run


def _unavailable():
    return LLMFatalError("All provider fallbacks exhausted: agent 'graph_agent': 503", agent="graph_agent",
                         unavailable=True, model_fallback=[MOVE])


# ---------------------------------------------------------------------------- query_complete

def test_an_ns_query_complete_with_its_turn_record_validates(plugin):
    store = _Store()
    with turn_spend.collecting():
        _spend_a_call()
        orchestrator._emit_query_complete(
            store, "Found 3 samples.", {"parser_plan": {"mode": "graph_query"}}, 4,
            files=[{"key": "api_result", "label": "Full API result JSON", "path": "/x/y.json",
                    "filename": "y.json", "mime": "application/json"}])
    data = store.terminal("query_complete")
    assert {"total_cost_usd", "cost_partial", "models_used", "model_fallback"} <= set(data)
    _validate(plugin, "query_complete", data)
    assert _through_the_client(plugin, "query_complete", data)["total_cost_usd"] == data["total_cost_usd"]


# ---------------------------------------------------------------------------- the fatal handlers

@pytest.mark.parametrize("fatal", [_unavailable, lambda: LLMFatalError("Unrecoverable LLM error: 400", agent="parser")],
                         ids=["unavailable", "not_unavailable"])
def test_run_query_ends_a_fatal_turn_with_events_the_plugin_accepts(plugin, monkeypatch, tmp_path, fatal):
    _stub_turn(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "_handle_pipeline_agent_turn", _raise_after_a_call(fatal()))
    store = _Store()
    orchestrator.run_query({}, object(), "how many mice", store)
    for name in ("query_error", "query_complete"):
        _validate(plugin, name, store.terminal(name))
    error = store.terminal("query_error")
    assert error["fatal"] is True
    terminal = _through_the_client(plugin, "query_error", error)
    assert terminal["__error__"] == error["error"]


def test_run_query_plan_ends_a_fatal_turn_with_events_the_plugin_accepts(plugin, monkeypatch, tmp_path):
    _stub_turn(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "shortlist_catalog", _raise_after_a_call(_unavailable()))
    store = _Store()
    config = type("Config", (), {"MIN_SAMPLETYPES": [], "MIN_ASSAYS": []})()  # read before the shortlist
    orchestrator.run_query_plan({}, config, "how many mice", store)
    error = store.terminal("query_error")
    assert error["reason"] == "model_unavailable" and error["model_fallback"] == [MOVE]
    _validate(plugin, "query_error", error)
    _validate(plugin, "query_complete", store.terminal("query_complete"))


def test_a_fatal_that_escaped_the_pipeline_launch_is_reported_in_a_shape_the_plugin_accepts(
        plugin, monkeypatch, tmp_path):
    """nextseek-pipeline: run_pipeline_launch does not guard the pipeline agent, so the
    pipeline body's _report_fatal sends the terminal event, with the turn's cost."""
    from NessieAI.ns import turn as ns_turn

    _stub_turn(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator.pipeline_agent, "start", _raise_after_a_call(_unavailable()))
    monkeypatch.setattr(ns_turn, "_save_session_or_report", lambda *a, **k: None)
    store = _Store()

    class _Req:
        mode = "pipeline"
        query = "launch rnaseq on those samples"

    ns_turn.run_async_pipeline(adapter={}, chat_config=object(), req=_Req(), send_event=store,
                               api_user="u", api_pass="p", chat_session=object(), resolved_session_id=SID)
    error = store.terminal("query_error")
    assert error["total_cost_usd"] is not None and error["model_fallback"] == [MOVE]
    _validate(plugin, "query_error", error)
    assert _through_the_client(plugin, "query_error", error)["__error__"] == error["error"]


# ---------------------------------------------------------------------------- a crash

def test_a_turn_that_crashed_in_run_query_reports_its_cost_in_a_shape_the_plugin_accepts(
        plugin, monkeypatch, tmp_path):
    """run_query answers any other exception with its own query_error, then re-raises;
    that event is the turn's last and now carries what the turn spent."""
    _stub_turn(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "_handle_pipeline_agent_turn", _raise_after_a_call(RuntimeError("boom")))
    store = _Store()
    with pytest.raises(RuntimeError):
        orchestrator.run_query({}, object(), "how many mice", store)
    error = store.terminal("query_error")
    assert error["error"] == "boom" and error["total_cost_usd"] is not None
    assert error["model_fallback"] == [MOVE]
    _validate(plugin, "query_error", error)


def test_a_crash_that_escaped_the_pipeline_launch_reports_its_cost_in_a_shape_the_plugin_accepts(
        plugin, monkeypatch, tmp_path):
    """The pipeline body's generic query_error, for an exception run_pipeline_launch did
    not answer itself, carries the cost the turn record took out on the exception."""
    from NessieAI.ns import turn as ns_turn

    _stub_turn(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator.pipeline_agent, "start", _raise_after_a_call(RuntimeError("boom")))
    monkeypatch.setattr(ns_turn, "_save_session_or_report", lambda *a, **k: None)
    store = _Store()

    class _Req:
        mode = "pipeline"
        query = "launch rnaseq on those samples"

    ns_turn.run_async_pipeline(adapter={}, chat_config=object(), req=_Req(), send_event=store,
                               api_user="u", api_pass="p", chat_session=object(), resolved_session_id=SID)
    error = store.terminal("query_error")
    assert error["error"] == "Internal pipeline error" and error["total_cost_usd"] is not None
    _validate(plugin, "query_error", error)


# ---------------------------------------------------------------------------- the guard itself

def test_the_mirror_still_refuses_a_key_the_server_does_not_send(plugin):
    models, _ = plugin
    with pytest.raises(Exception):
        models.QueryCompleteEvent(reply="r", invented_field=1)
    with pytest.raises(Exception):
        models.QueryErrorEvent(error="e", invented_field=1)
