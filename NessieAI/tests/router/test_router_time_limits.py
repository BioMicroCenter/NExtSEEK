"""The router's model call has a time limit and one fallback model, then the keyword rules.

Operator ruling 2026-09-25 (fix 5). ``asyncio.run(b.RouteQuery(...))`` had no time limit:
BAML's GCPReasoner (gemini-3.1-pro-preview, two retries) ran for as long as it took, and
only after it raised did the keyword heuristic decide. Now:

* RouteQuery on GCPReasoner gets 30 s, its BAML retries included;
* on a timeout, an error or ``<router_unavailable>``, ONE try on GCPFlash
  (gemini-3.5-flash) through a per-call client override, with 15 s;
* then the keyword rules, whose source is pinned and untouched.

The decision says which model answered (``router_model``) and whether the router fell
back (``router_fallback``: ``{"from", "to", "reason"}``, ``to`` a model id or
``"heuristic"``); the CC turn puts both on the ``route_decided`` event. No ``.baml``
file changes: the override is a ``baml_py.ClientRegistry`` naming the declared client.

Every BAML call here is a fake; nothing reaches a model.
"""
from __future__ import annotations

import ast
import asyncio
import time

import pytest

from NessieAI import paths
from NessieAI.router import router as cc_router

PRO = "gemini-3.1-pro-preview"
FLASH = "gemini-3.5-flash"


class _Decision:
    def __init__(self, route, reasoning="because"):
        self.route = route
        self.reasoning = reasoning
        self.model_class = None


class _FakeB:
    """RouteQuery as the generated async client exposes it; each call pops one outcome.

    An outcome is a decision, an exception to raise, or a float: sleep that long first."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def RouteQuery(self, input, baml_options=None):
        self.calls.append({"input": input, "baml_options": baml_options})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, float):
            await asyncio.sleep(outcome)
            outcome = self.outcomes.pop(0) if self.outcomes else _Decision(None)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Registry:
    """Stands in for baml_py.ClientRegistry and records the client it was pointed at."""

    made: list["_Registry"] = []

    def __init__(self):
        self.primary = None
        _Registry.made.append(self)

    def set_primary(self, name):
        self.primary = name


@pytest.fixture
def baml(monkeypatch):
    """Install a fake BAML surface; returns a function that installs a given _FakeB."""
    from dmac_assistant.router.baml_client.types import Route
    import baml_py

    _Registry.made = []
    monkeypatch.setattr(baml_py, "ClientRegistry", _Registry)
    monkeypatch.setattr(cc_router, "_build_context_dir", lambda: None)
    monkeypatch.setattr(cc_router.posterior_selector, "posterior_routing_enabled", lambda: False)

    def install(fake):
        monkeypatch.setattr(cc_router, "_load_router_deps",
                            lambda: (lambda capabilities=None: None, lambda path=None: [], Route, fake))
        return fake, Route

    return install


# ---------------------------------------------------------------------------- the pins

def test_the_limits_and_clients_are_the_ruling():
    assert cc_router.ROUTER_PRIMARY_CLIENT == "GCPReasoner"
    assert cc_router.ROUTER_PRIMARY_LIMIT_S == 30
    assert cc_router.ROUTER_FALLBACK_CLIENT == "GCPFlash"
    assert cc_router.ROUTER_FALLBACK_LIMIT_S == 15


def test_the_model_ids_come_from_the_declared_clients():
    assert cc_router._baml_client_model("GCPReasoner") == PRO
    assert cc_router._baml_client_model("GCPFlash") == FLASH
    assert cc_router._baml_client_model("NoSuchClient") is None


def test_no_baml_file_was_edited_for_this():
    """RouteQuery still names GCPReasoner and the two clients keep their models: the
    fallback is a per-call override, so no cc-agent rebuild is needed."""
    src = (paths.DMAC_ASSISTANT_DIR / "baml_src" / "router.baml").read_text(encoding="utf-8")
    route_query = src[src.index("function RouteQuery"):]
    assert route_query.split("\n", 2)[1].strip() == "client GCPReasoner"
    clients = (paths.DMAC_ASSISTANT_DIR / "baml_src" / "clients.baml").read_text(encoding="utf-8")
    assert f'model "{PRO}"' in clients and f'model "{FLASH}"' in clients


# ---------------------------------------------------------------------------- the ladder

def test_the_primary_answers_and_says_so(baml):
    fake, Route = baml(_FakeB(_Decision(None)))
    fake.outcomes = [_Decision(Route.NextseekQuery)]

    d = cc_router.decide("how many mice")

    assert (d.route, d.source) == (cc_router.ROUTE_NS, "baml")
    assert d.router_model == PRO and d.router_fallback is None
    assert len(fake.calls) == 1 and fake.calls[0]["baml_options"] is None


@pytest.mark.parametrize("first, reason", [
    (RuntimeError("503 from Gemini after BAML's retries"), "error"),
], ids=["error"])
def test_an_error_on_the_primary_moves_to_flash_once(baml, first, reason):
    fake, Route = baml(_FakeB(first))
    fake.outcomes = [first, _Decision(Route.ContainerCC, "flash says cc")]

    d = cc_router.decide("write a script")

    assert d.route == cc_router.ROUTE_CC and d.source == "baml"
    assert d.reasoning == "flash says cc"
    assert d.router_model == FLASH
    assert d.router_fallback == {"from": PRO, "to": FLASH, "reason": reason}
    assert len(fake.calls) == 2
    assert fake.calls[1]["input"] is fake.calls[0]["input"], "Flash is asked the same question"
    registry = fake.calls[1]["baml_options"]["client_registry"]
    assert isinstance(registry, _Registry) and registry.primary == "GCPFlash"


def test_the_sentinel_on_the_primary_moves_to_flash(baml):
    fake, Route = baml(_FakeB())
    fake.outcomes = [_Decision(Route.NextseekQuery, cc_router._FALLBACK_SENTINEL), _Decision(Route.NextseekQuery)]

    d = cc_router.decide("how many mice")

    assert d.router_model == FLASH
    assert d.router_fallback == {"from": PRO, "to": FLASH, "reason": "error"}


def test_a_stalled_primary_is_cut_at_its_limit_and_flash_answers(baml, monkeypatch):
    monkeypatch.setattr(cc_router, "ROUTER_PRIMARY_LIMIT_S", 0.2)
    fake, Route = baml(_FakeB())
    fake.outcomes = [5.0, _Decision(Route.NextseekQuery)]

    t0 = time.perf_counter()
    d = cc_router.decide("how many mice")

    assert time.perf_counter() - t0 < 2.0, "the router waited out the stalled model"
    assert d.router_model == FLASH
    assert d.router_fallback == {"from": PRO, "to": FLASH, "reason": "timeout"}


def test_when_flash_fails_too_the_keyword_rules_decide_and_say_why(baml, monkeypatch):
    monkeypatch.setattr(cc_router, "ROUTER_PRIMARY_LIMIT_S", 0.2)
    monkeypatch.setattr(cc_router, "ROUTER_FALLBACK_LIMIT_S", 0.2)
    fake, _ = baml(_FakeB())
    fake.outcomes = [5.0, 5.0]

    t0 = time.perf_counter()
    d = cc_router.decide("Find me all mice treated with NDMA.")

    assert time.perf_counter() - t0 < 2.0
    assert d.source == "heuristic" and d.route == cc_router.ROUTE_NS
    assert d.router_model is None
    assert d.router_fallback == {"from": PRO, "to": "heuristic", "reason": "timeout"}
    assert len(fake.calls) == 2, "one try on Flash, not a walk"


def test_route_query_still_returns_none_when_both_fail(baml):
    fake, _ = baml(_FakeB(RuntimeError("down"), RuntimeError("down too")))
    assert cc_router._route_query("q") is None
    assert len(fake.calls) == 2


def test_no_model_asked_means_no_fallback_record(monkeypatch):
    """BAML not importable: the keyword rules decide, but no model failed."""
    def broken():
        raise ImportError("no dmac_assistant")

    monkeypatch.setattr(cc_router, "_load_router_deps", broken)
    monkeypatch.setattr(cc_router.posterior_selector, "posterior_routing_enabled", lambda: False)
    d = cc_router.decide("Find me all mice treated with NDMA.")
    assert d.source == "heuristic" and d.router_fallback is None and d.router_model is None


def test_a_failure_recorded_for_another_query_is_not_applied(baml):
    fake, _ = baml(_FakeB(RuntimeError("down"), RuntimeError("down too")))
    assert cc_router._route_query("first question") is None
    d = cc_router._heuristic_after_route_failure("second question")
    assert d.router_fallback is None


def test_a_heuristic_decision_has_no_router_model():
    d = cc_router._heuristic("write a script")
    assert d.router_model is None and d.router_fallback is None


# ---------------------------------------------------------------------------- the event

def test_route_decided_carries_the_router_model_and_the_fallback():
    """The CC turn's route_decided payload names both, read off the decision."""
    tree = ast.parse((paths.CC_DIR / "turn.py").read_text(encoding="utf-8"))
    payloads = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "send_event"
        and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "route_decided"
    ]
    assert payloads, "no send_event('route_decided', ...) in cc/turn.py"
    keys = {k.value for p in payloads for k in p.args[1].keys if isinstance(k, ast.Constant)}
    assert {"router_model", "router_fallback"} <= keys
    src = ast.unparse(payloads[0].args[1])
    assert "router_model" in src and "decision" in src


# ---------------------------------------------------------------------------- the policy keeps it

from types import SimpleNamespace  # noqa: E402

from NessieAI.router import policy  # noqa: E402

ROUTED = cc_router.RouteDecision(route=cc_router.ROUTE_NS, model_class=None, model_id=None, reasoning="r",
                                 source="baml", router_model=FLASH,
                                 router_fallback={"from": PRO, "to": FLASH, "reason": "timeout"})
_REQ = SimpleNamespace(query="and of those, which are lung?", force_route=None)
_USER = SimpleNamespace(is_superuser=False)


def _keeps_the_router_record(d):
    assert d.router_model == FLASH
    assert d.router_fallback == {"from": PRO, "to": FLASH, "reason": "timeout"}


def test_a_pipeline_redirect_keeps_the_router_record(monkeypatch):
    monkeypatch.setattr(policy.cc_router, "decide", lambda q, history=None: ROUTED)
    monkeypatch.setattr(policy.pipeline_agent, "is_active", lambda session: True)
    d = policy._decide_route(_USER, _REQ, force_cc=False, session={})
    assert d.source == "pipeline"
    _keeps_the_router_record(d)


def test_a_followup_redirect_keeps_the_router_record(monkeypatch):
    monkeypatch.setattr(policy.cc_router, "decide", lambda q, history=None: ROUTED)
    monkeypatch.setattr(policy.followup_rule, "followup_reason", lambda q, turns: "of those")
    monkeypatch.setattr(policy, "_chat_is_sticky_cc", lambda turns: True)
    d = policy._decide_route(_USER, _REQ, force_cc=False, chat_log=[])
    assert d.source == "sticky" and d.route == cc_router.ROUTE_CC
    _keeps_the_router_record(d)

    back = policy._fallback_when_cc_unavailable(d, lambda: (False, "runner down"))
    assert back.source == "cc_unavailable"
    _keeps_the_router_record(back)


def test_a_forced_turn_has_no_router_record():
    admin = SimpleNamespace(is_superuser=True)
    d = policy._decide_route(admin, SimpleNamespace(query="q", force_route="ns"), force_cc=False)
    assert d.source == "forced" and d.router_model is None and d.router_fallback is None
