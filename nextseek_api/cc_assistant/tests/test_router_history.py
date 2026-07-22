"""cc_router.decide history augmentation (#8).

With history present, the BAML router sees the conversation block + a CC steer;
the keyword heuristic fallback always sees the RAW query.
"""
from nextseek_api.cc_assistant import router as cc_router


def _cc():
    return cc_router.RouteDecision(route=cc_router.ROUTE_CC, model_class="opus",
                                   model_id="m", reasoning="baml", source="baml")


def _ns():
    return cc_router.RouteDecision(route=cc_router.ROUTE_NS, model_class=None,
                                   model_id=None, reasoning="baml", source="baml")


def test_augments_query_when_history_present(monkeypatch):
    captured = {}
    monkeypatch.setattr(cc_router, "_baml_decision",
                        lambda q: (captured.__setitem__("q", q), _cc())[1])
    cc_router.decide("counts of those monkeys",
                     history='- user asked: "find NHP" -> 139 result(s)')
    q = captured["q"]
    assert q.startswith("counts of those monkeys")
    assert "Conversation so far" in q
    assert "139 result(s)" in q
    assert "container_cc" in q  # the follow-up -> CC steer


def test_no_history_passes_raw_query(monkeypatch):
    captured = {}
    monkeypatch.setattr(cc_router, "_baml_decision",
                        lambda q: (captured.__setitem__("q", q), _ns())[1])
    cc_router.decide("what sample types exist")
    assert captured["q"] == "what sample types exist"


def test_heuristic_fallback_sees_raw_query(monkeypatch):
    seen = {}
    monkeypatch.setattr(cc_router, "_baml_decision", lambda q: None)
    monkeypatch.setattr(cc_router, "_heuristic",
                        lambda q: (seen.__setitem__("q", q), _ns())[1])
    cc_router.decide("raw query", history="some history block")
    assert seen["q"] == "raw query"
