from NessieAI.router import policy
from NessieAI.router import router as cc_router


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
    d = policy._decide_route(_User(), _Req("anything at all"), force_cc=False, session=session)
    assert called, "the router must be consulted before the pipeline gate"
    assert d.route == cc_router.ROUTE_NS
    assert d.source == "pipeline"


def test_inactive_pipeline_falls_through(monkeypatch):
    sentinel = cc_router.RouteDecision(route=cc_router.ROUTE_CC, model_class="opus",
                                       model_id=None, reasoning="x", source="baml")
    seen = {}
    monkeypatch.setattr(cc_router, "decide",
                        lambda q, history=None: (seen.update(q=q, history=history), sentinel)[1])
    d = policy._decide_route(_User(), _Req("write me code"), force_cc=False,
                             session={"pipeline_agent": {}}, history="prior turns")
    assert d is sentinel
    assert seen == {"q": "write me code", "history": "prior turns"}  # history threaded through


def _router_says_cc(monkeypatch):
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: cc_router.RouteDecision(
        route=cc_router.ROUTE_CC, model_class="opus", model_id="m", reasoning="x", source="baml"))


def test_open_build_takes_a_turn_the_router_sent_to_cc(monkeypatch):
    """Prod chat e80cee5a: "Submit" with a built run went to CC, whose relay wiped the build. The turn goes to the
    wizard, which hands back (turn.py runs the router's CC call then) when the turn is not about the build."""
    _router_says_cc(monkeypatch)
    d = policy._decide_route(_User(), _Req("Submit"), force_cc=False, session={"pipeline_agent": {"active": True}})
    assert d.route == cc_router.ROUTE_NS and d.source == "pipeline"
    assert d.attempted_route == cc_router.ROUTE_CC and d.attempted_source == "baml"
    assert d.reasoning.startswith("pipeline_active; router said cc")


def test_the_gate_keeps_the_routers_family_for_the_ledger(monkeypatch):
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: cc_router.RouteDecision(
        route=cc_router.ROUTE_CC, model_class="opus", model_id="m", reasoning="x", source="baml",
        task_family="harmonization", family_source="baml", generation_id=7, generation_hash="h"))
    d = policy._decide_route(_User(), _Req("Submit"), force_cc=False, session={"pipeline_agent": {"active": True}})
    assert (d.task_family, d.family_source, d.generation_id, d.generation_hash) == ("harmonization", "baml", 7, "h")


def test_an_ns_turn_kept_by_the_wizard_has_no_cc_to_go_back_to(monkeypatch):
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: cc_router.RouteDecision(
        route=cc_router.ROUTE_NS, model_class=None, model_id=None, reasoning="x", source="baml"))
    d = policy._decide_route(_User(), _Req("Submit"), force_cc=False, session={"pipeline_agent": {"active": True}})
    assert d.source == "pipeline" and d.attempted_route is None


def test_plan_mode_keeps_the_routers_cc(monkeypatch):
    """run_query_plan has no wizard, so a plan turn the router sent to CC stays there."""
    _router_says_cc(monkeypatch)
    req = _Req("Submit")
    req.mode = "plan"
    d = policy._decide_route(_User(), req, force_cc=False, session={"pipeline_agent": {"active": True}})
    assert d.route == cc_router.ROUTE_CC


def test_no_build_open_keeps_the_routers_cc(monkeypatch):
    _router_says_cc(monkeypatch)
    d = policy._decide_route(_User(), _Req("Submit"), force_cc=False, session={"pipeline_agent": {"active": False}})
    assert d.route == cc_router.ROUTE_CC


def test_force_cc_beats_active_pipeline():
    session = {"pipeline_agent": {"active": True}}
    d = policy._decide_route(_User(), _Req("x"), force_cc=True, session=session)
    assert d.route == cc_router.ROUTE_CC
