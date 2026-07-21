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
    def _boom(_q):
        raise AssertionError("cc_router.decide must not be called when a pipeline is active")
    monkeypatch.setattr(cc_router, "decide", _boom)
    session = {"pipeline_agent": {"active": True}}
    d = cc_svc._decide_route(_User(), _Req("anything at all"), force_cc=False, session=session)
    assert d.route == cc_router.ROUTE_NS
    assert d.source == "pipeline"


def test_inactive_pipeline_falls_through(monkeypatch):
    sentinel = cc_router.RouteDecision(route=cc_router.ROUTE_CC, model_class="opus",
                                       model_id=None, reasoning="x", source="baml")
    monkeypatch.setattr(cc_router, "decide", lambda q: sentinel)
    d = cc_svc._decide_route(_User(), _Req("write me code"), force_cc=False,
                             session={"pipeline_agent": {}})
    assert d is sentinel


def test_force_cc_beats_active_pipeline():
    session = {"pipeline_agent": {"active": True}}
    d = cc_svc._decide_route(_User(), _Req("x"), force_cc=True, session=session)
    assert d.route == cc_router.ROUTE_CC
