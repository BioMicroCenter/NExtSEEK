"""The router says what its model calls cost, on every route that made one.

``router.decide`` attaches a ``baml_py.Collector`` to each BAML call it makes (the
RouteQuery on GCPReasoner, the one GCPFlash retry, and ClassifyQuery when posterior
routing is on), prices every ``LLMCall`` in it by the model its client declares, and
puts three fields on the decision: ``router_cost_usd``, ``router_usage`` and
``router_cost_partial``. The CC turn copies them onto ``route_decided``; the policy
keeps them when it rebuilds a decision (pipeline, sticky, follow-up, cc_unavailable).

The rules pinned here:

* Gemini thinking is billed as output. BAML's ``usage.output_tokens`` leaves it out, so
  it is read from the response body's ``usageMetadata.thoughtsTokenCount``; a call whose
  body cannot be read is a floor, and the cost is partial;
* a BAML retry is its own ``LLMCall``: a 503 has a status and no usage and is not
  billed; a call cut off by the time limit has neither, may still be billed, and makes
  the cost partial;
* a model call that left nothing in the collector is unobserved, never free;
* a decision no router call made (a forced turn) has no router cost fields at all,
  while the keyword rules deciding because BAML could not load cost exactly 0.

Every BAML call here is a fake except the last test, which runs the real BAML runtime
against a fake Gemini endpoint on 127.0.0.1: no model is ever reached.
"""
from __future__ import annotations

import ast
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from NessieAI import paths
from NessieAI.router import policy
from NessieAI.router import router as cc_router
from chat_nextseek import model_prices

PRO = "gemini-3.1-pro-preview"
FLASH = "gemini-3.5-flash"


def _price(model, prompt, output, cached=0, thoughts=0):
    return model_prices.call_cost(model, {"prompt_tokens": prompt, "completion_tokens": output,
                                          "cached_tokens": cached, "thoughts_tokens": thoughts}).cost_usd


# ---------------------------------------------------------------------------- a fake Collector

class _Usage:
    def __init__(self, input_tokens=None, output_tokens=None, cached_input_tokens=None):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cached_input_tokens = cached_input_tokens


class _Body:
    def __init__(self, data):
        self._data = data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class _Call:
    def __init__(self, client, *, status=None, prompt=None, output=None, cached=None, thoughts=None, body=True):
        self.client_name = client
        self.provider = "google-ai"
        self.usage = _Usage(prompt, output, cached)
        meta = {"promptTokenCount": prompt, "candidatesTokenCount": output}
        if cached is not None:
            meta["cachedContentTokenCount"] = cached
        if thoughts is not None:
            meta["thoughtsTokenCount"] = thoughts
        self.http_response = None if status is None else SimpleNamespace(
            status=status, body=_Body({"usageMetadata": meta} if body is True else body))


def ok(client, prompt=1200, output=30, cached=0, thoughts=450, **kw):
    return _Call(client, status=200, prompt=prompt, output=output, cached=cached, thoughts=thoughts, **kw)


def unavailable(client):
    return _Call(client, status=503)


class _Collector:
    def __init__(self):
        self.logs = []


class _FakeB:
    """RouteQuery that writes scripted LLMCalls into the collector it is handed.

    An outcome is ``(calls, result)``: the calls BAML would log, then a decision to
    return, an exception to raise, or a float to sleep (a stall the time limit cuts)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def RouteQuery(self, input, baml_options=None):
        self.calls.append(baml_options)
        calls, result = self.outcomes.pop(0)
        collector = (baml_options or {}).get("collector")
        if collector is not None:
            collector.logs.append(SimpleNamespace(calls=list(calls)))
        if isinstance(result, float):
            await asyncio.sleep(result)
        if isinstance(result, BaseException):
            raise result
        return result


class _Decision:
    def __init__(self, route, reasoning="because"):
        self.route, self.reasoning, self.model_class = route, reasoning, None


@pytest.fixture
def baml(monkeypatch):
    from dmac_assistant.router.baml_client.types import Route

    monkeypatch.setattr(cc_router, "_new_collector", _Collector)
    monkeypatch.setattr(cc_router, "_build_context_dir", lambda: None)
    monkeypatch.setattr(cc_router.posterior_selector, "posterior_routing_enabled", lambda: False)

    def install(*outcomes):
        fake = _FakeB(*outcomes)
        monkeypatch.setattr(cc_router, "_load_router_deps",
                            lambda: (lambda capabilities=None: None, lambda path=None: [], Route, fake))
        return fake

    return install, Route


# ---------------------------------------------------------------------------- the decision

def test_the_primary_answer_is_priced_with_its_thinking(baml):
    install, Route = baml
    install(([ok("GCPReasoner", prompt=1200, output=30, cached=1000, thoughts=450)], _Decision(Route.NextseekQuery)))

    d = cc_router.decide("how many mice")

    assert d.router_model == PRO
    assert d.router_cost_usd == pytest.approx(_price(PRO, 1200, 30, cached=1000, thoughts=450), abs=1e-9)
    assert d.router_cost_partial is False
    (call,) = d.router_usage["calls"]
    assert call["model"] == PRO and call["client"] == "GCPReasoner" and call["status"] == 200
    assert call["thoughts_tokens"] == 450 and call["cached_tokens"] == 1000
    assert d.router_usage["output_tokens"] == 30 and d.router_usage["thoughts_tokens"] == 450
    assert d.router_usage["price_table_version"] == model_prices.load_price_table().version


def test_baml_retries_that_were_refused_cost_nothing_and_flash_is_priced_as_flash(baml):
    install, Route = baml
    install(([unavailable("GCPReasoner")] * 3, RuntimeError("503 after BAML's retries")),
            ([ok("GCPFlash", prompt=900, output=20, thoughts=0)], _Decision(Route.ContainerCC)))

    d = cc_router.decide("write a script")

    assert d.router_model == FLASH and d.router_fallback["reason"] == "error"
    assert d.router_cost_usd == pytest.approx(_price(FLASH, 900, 20), abs=1e-9)
    assert d.router_cost_partial is False
    assert [c["status"] for c in d.router_usage["calls"]] == [503, 503, 503, 200]


def test_a_call_cut_off_by_the_time_limit_makes_the_cost_partial(baml, monkeypatch):
    monkeypatch.setattr(cc_router, "ROUTER_PRIMARY_LIMIT_S", 0.1)
    install, Route = baml
    install(([_Call("GCPReasoner")], 5.0),
            ([ok("GCPFlash", prompt=900, output=20, thoughts=0)], _Decision(Route.NextseekQuery)))

    d = cc_router.decide("how many mice")

    assert d.router_fallback == {"from": PRO, "to": FLASH, "reason": "timeout"}
    assert d.router_cost_partial is True
    assert d.router_cost_usd == pytest.approx(_price(FLASH, 900, 20), abs=1e-9)
    assert d.router_usage["unobserved_calls"] == 1


def test_the_keyword_rules_deciding_after_both_models_failed_still_carry_the_cost(baml):
    install, _ = baml
    install(([unavailable("GCPReasoner")], RuntimeError("down")),
            ([unavailable("GCPFlash")], RuntimeError("down too")))

    d = cc_router.decide("Find me all mice treated with NDMA.")

    assert d.source == "heuristic"
    assert d.router_cost_usd == 0.0 and d.router_cost_partial is False
    assert len(d.router_usage["calls"]) == 2


def test_a_call_that_left_nothing_in_the_collector_is_unobserved_not_free(baml):
    install, Route = baml
    install(([], _Decision(Route.NextseekQuery)))

    d = cc_router.decide("how many mice")

    assert d.router_cost_usd is None and d.router_cost_partial is True
    assert d.router_usage["unobserved_calls"] == 1


def test_a_body_that_cannot_be_read_hides_the_thinking_so_the_cost_is_a_floor(baml):
    install, Route = baml
    install(([ok("GCPReasoner", prompt=1200, output=30, body=ValueError("not json"))], _Decision(Route.NextseekQuery)))

    d = cc_router.decide("how many mice")

    assert d.router_cost_usd == pytest.approx(_price(PRO, 1200, 30), abs=1e-9)
    assert d.router_cost_partial is True


def test_no_router_call_costs_zero(monkeypatch):
    """BAML not importable: the keyword rules decide and no model was asked."""
    def broken():
        raise ImportError("no dmac_assistant")

    monkeypatch.setattr(cc_router, "_load_router_deps", broken)
    monkeypatch.setattr(cc_router.posterior_selector, "posterior_routing_enabled", lambda: False)
    d = cc_router.decide("Find me all mice treated with NDMA.")
    assert d.source == "heuristic"
    assert d.router_cost_usd == 0.0 and d.router_cost_partial is False
    assert d.router_usage["calls"] == []


def test_route_query_outside_decide_attaches_no_collector(baml):
    """Nobody reads the cost of a bare _route_query, so its call options are unchanged."""
    install, Route = baml
    fake = install(([ok("GCPReasoner")], _Decision(Route.NextseekQuery)))
    assert cc_router._route_query("how many mice") is not None
    assert fake.calls == [None]


def test_the_classifier_call_is_counted_when_posterior_routing_asks_it(monkeypatch):
    class _B:
        async def ClassifyQuery(self, input, baml_options=None):
            baml_options["collector"].logs.append(SimpleNamespace(calls=[ok("GCPReasoner", prompt=500, output=10)]))
            return SimpleNamespace(task_family=None, reasoning="unrelated")

    monkeypatch.setattr(cc_router, "_new_collector", _Collector)
    monkeypatch.setattr(cc_router, "_load_router_deps", lambda: (object, object, SimpleNamespace(), _B()))
    monkeypatch.setattr(cc_router, "runtime_type_builder", lambda _snap: object())
    monkeypatch.setattr(cc_router, "type_builder", lambda _snap: {"members": []})
    with cc_router._collecting_router_spend() as spend:
        cc_router._classify_query("who won the superbowl")
    fields = spend.fields()
    assert fields["router_cost_usd"] == pytest.approx(_price(PRO, 500, 10, thoughts=450), abs=1e-9)


def test_a_classifier_call_cut_off_by_the_time_limit_makes_the_cost_partial(monkeypatch):
    """F7: ClassifyQuery gets RouteQuery's limit, and a cut-off attempt is noted as one:
    the 503 BAML logged before the stall is not billed, and the cut-off retry may be."""
    class _B:
        async def ClassifyQuery(self, input, baml_options=None):
            baml_options["collector"].logs.append(SimpleNamespace(calls=[unavailable("GCPReasoner")]))
            await asyncio.sleep(5.0)

    monkeypatch.setattr(cc_router, "ROUTER_PRIMARY_LIMIT_S", 0.1)
    monkeypatch.setattr(cc_router, "_new_collector", _Collector)
    monkeypatch.setattr(cc_router, "_load_router_deps", lambda: (object, object, SimpleNamespace(), _B()))
    monkeypatch.setattr(cc_router, "runtime_type_builder", lambda _snap: object())
    monkeypatch.setattr(cc_router, "type_builder", lambda _snap: {"members": []})
    with cc_router._collecting_router_spend() as spend:
        family, _, _ = cc_router._classify_query("how many mice")
    fields = spend.fields()
    assert family is None
    assert fields["router_cost_usd"] == 0.0
    assert fields["router_cost_partial"] is True


# ---------------------------------------------------------------------------- the event and the policy

ROUTED = cc_router.RouteDecision(
    route=cc_router.ROUTE_NS, model_class=None, model_id=None, reasoning="r", source="baml",
    router_model=PRO, router_fallback=None, router_cost_usd=0.0042,
    router_usage={"calls": [{"model": PRO}]}, router_cost_partial=False,
)
_REQ = SimpleNamespace(query="and of those, which are lung?", force_route=None)
_USER = SimpleNamespace(is_superuser=False)


def _keeps_the_cost(d):
    assert d.router_cost_usd == 0.0042
    assert d.router_usage == {"calls": [{"model": PRO}]}
    assert d.router_cost_partial is False
    assert cc_router.router_cost_fields(d) == {
        "router_cost_usd": 0.0042, "router_cost_partial": False, "router_usage": {"calls": [{"model": PRO}]}}


def test_a_pipeline_redirect_keeps_the_router_cost(monkeypatch):
    monkeypatch.setattr(policy.cc_router, "decide", lambda q, history=None: ROUTED)
    monkeypatch.setattr(policy.pipeline_agent, "is_active", lambda session: True)
    d = policy._decide_route(_USER, _REQ, force_cc=False, session={})
    assert d.source == "pipeline"
    _keeps_the_cost(d)


def test_a_sticky_redirect_and_its_cc_unavailable_fallback_keep_the_router_cost(monkeypatch):
    monkeypatch.setattr(policy.cc_router, "decide", lambda q, history=None: ROUTED)
    monkeypatch.setattr(policy.followup_rule, "followup_reason", lambda q, turns: "of those")
    monkeypatch.setattr(policy, "_chat_is_sticky_cc", lambda turns: True)
    d = policy._decide_route(_USER, _REQ, force_cc=False, chat_log=[])
    assert d.source == "sticky"
    _keeps_the_cost(d)
    back = policy._fallback_when_cc_unavailable(d, lambda: (False, "runner down"))
    assert back.source == "cc_unavailable"
    _keeps_the_cost(back)


def test_a_followup_redirect_keeps_the_router_cost(monkeypatch):
    monkeypatch.setattr(policy.cc_router, "decide", lambda q, history=None: ROUTED)
    monkeypatch.setattr(policy.followup_rule, "followup_reason", lambda q, turns: "of those")
    monkeypatch.setattr(policy, "_chat_is_sticky_cc", lambda turns: False)
    monkeypatch.setattr(policy.followup_rule, "followup_mode", lambda: "cc")
    d = policy._decide_route(_USER, _REQ, force_cc=False, chat_log=[])
    assert d.source == "followup"
    _keeps_the_cost(d)


@pytest.mark.parametrize("force", ["ns", "cc"])
def test_a_forced_turn_made_no_router_call_and_carries_no_router_cost(force):
    admin = SimpleNamespace(is_superuser=True)
    d = policy._decide_route(admin, SimpleNamespace(query="q", force_route=force), force_cc=False)
    assert d.source == "forced"
    assert cc_router.router_cost_fields(d) == {}


def test_route_decided_spreads_the_router_cost_fields():
    tree = ast.parse((paths.CC_DIR / "turn.py").read_text(encoding="utf-8"))
    payloads = [
        n.args[1] for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "send_event"
        and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "route_decided"
    ]
    assert len(payloads) == 1
    spread = [v for k, v in zip(payloads[0].keys, payloads[0].values) if k is None]
    assert [ast.unparse(v) for v in spread] == ["cc_router.router_cost_fields(decision)"]


# ---------------------------------------------------------------------------- the real BAML runtime

class _FakeGemini(BaseHTTPRequestHandler):
    """generateContent as Gemini answers it; a 503 first when the plan says so."""

    plan: list[str] = []

    def log_message(self, *_a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        step = self.plan.pop(0) if self.plan else "ok"
        if step == "503":
            body = {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}}
            status = 503
        else:
            text = json.dumps({"route": "nextseek_query", "model_class": None, "reasoning": "fake"})
            body = {"candidates": [{"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 30, "thoughtsTokenCount": 450,
                                      "cachedContentTokenCount": 1000, "totalTokenCount": 1680}}
            status = 200
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_the_real_collector_is_read_as_the_fakes_assume(monkeypatch):
    """The real BAML runtime and Collector, against a Gemini stand-in on 127.0.0.1: one
    503 that BAML retries, then an answer with thinking and a cached prefix."""
    baml_py = pytest.importorskip("baml_py")
    try:
        from dmac_assistant.router.baml_client.async_client import b
        from dmac_assistant.router.baml_client.types import Route, RouterInput
    except Exception as exc:  # the generated client is gitignored; a checkout may lack it
        pytest.skip(f"generated BAML client unavailable: {exc!r}")
    monkeypatch.setenv("GCP_API_KEY", "dummy")
    server = HTTPServer(("127.0.0.1", 0), _FakeGemini)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _FakeGemini.plan = ["503", "ok"]
        registry = baml_py.ClientRegistry()
        registry.add_llm_client("GCPReasoner", "google-ai", {
            "model": PRO, "api_key": "dummy", "base_url": f"http://127.0.0.1:{server.server_address[1]}/v1beta",
        }, "Exponential")
        registry.set_primary("GCPReasoner")
        request = RouterInput(user_query="how many mice", routes=[], history=[], followup_rule=None)
        with cc_router._collecting_router_spend() as spend:
            routed, reason = cc_router._ask(b, request, Route, limit_s=20,
                                            options={"client_registry": registry}, model=PRO)
        fields = spend.fields()
    finally:
        server.shutdown()
    assert routed is not None and reason is None
    # BAML does not promise the order of a log's calls: the retry can be listed first.
    assert sorted(c["status"] for c in fields["router_usage"]["calls"]) == [200, 503]
    assert fields["router_cost_usd"] == pytest.approx(_price(PRO, 1200, 30, cached=1000, thoughts=450), abs=1e-9)
    assert fields["router_cost_partial"] is False
