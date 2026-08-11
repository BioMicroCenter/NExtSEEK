import nextseek_api.services.cc_assistant as cc_svc
from nextseek_api.cc_assistant import router as cc_router


class _Req:
    def __init__(self, query, force_route=None):
        self.query = query
        self.force_route = force_route


class _User:
    is_staff = False
    is_superuser = False


def test_active_pipeline_forces_ns(monkeypatch):
    # The router decides FIRST, then an active pipeline only *keeps* a turn the
    # router already sent to NS. This stub used to raise on any call, asserting
    # the router was never consulted -- the contract 4241289 deliberately
    # reverted, because short-circuiting before the router let an open build
    # hijack every following turn ("searching the database isn't something I can
    # do" in answer to a plain sample search).
    called = {}

    def _router_says_ns(_q, history=None):
        called["decide"] = True
        return cc_router.RouteDecision(route=cc_router.ROUTE_NS, model_class=None,
                                       model_id=None, reasoning="r", source="baml")
    monkeypatch.setattr(cc_router, "decide", _router_says_ns)
    session = {"pipeline_agent": {"active": True}}
    d = cc_svc._decide_route(_User(), _Req("anything at all"), force_cc=False, session=session)
    assert called, "the router must be consulted before the pipeline gate"
    assert d.route == cc_router.ROUTE_NS
    assert d.source == "pipeline"


def test_inactive_pipeline_falls_through(monkeypatch):
    sentinel = cc_router.RouteDecision(route=cc_router.ROUTE_CC, model_class="opus",
                                       model_id=None, reasoning="x", source="baml")
    seen = {}
    monkeypatch.setattr(cc_router, "decide",
                        lambda q, history=None: (seen.update(q=q, history=history), sentinel)[1])
    d = cc_svc._decide_route(_User(), _Req("write me code"), force_cc=False,
                             session={"pipeline_agent": {}}, history="prior turns")
    assert d is sentinel
    assert seen == {"q": "write me code", "history": "prior turns"}  # history threaded through


def test_active_pipeline_does_not_hijack_a_cc_turn(monkeypatch):
    """4241289: an open build must not capture a turn the router sent to CC."""
    sentinel = cc_router.RouteDecision(route=cc_router.ROUTE_CC, model_class="opus",
                                       model_id=None, reasoning="x", source="baml")
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: sentinel)
    d = cc_svc._decide_route(_User(), _Req("find me all D.SEQ samples"),
                             force_cc=False, session={"pipeline_agent": {"active": True}})
    assert d is sentinel


def test_force_cc_beats_active_pipeline():
    session = {"pipeline_agent": {"active": True}}
    d = cc_svc._decide_route(_User(), _Req("x"), force_cc=True, session=session)
    assert d.route == cc_router.ROUTE_CC
