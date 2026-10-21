"""Round 4 U7: how-to questions go to NExtSEEK (the user docs), in code and in the router text."""
import json

import pytest

from NessieAI import paths
from NessieAI.router import followup, policy
from NessieAI.router import router as cc_router
from NessieAI.router import router_context


class _Req:
    def __init__(self, query, force_route=None):
        self.query = query
        self.force_route = force_route


class _User:
    is_staff = False
    is_superuser = False


class _Admin:
    is_superuser = True


def _says(monkeypatch, route):
    d = cc_router.RouteDecision(route=route, model_class="opus" if route == cc_router.ROUTE_CC else None,
                                model_id=None, reasoning="the router", source="baml")
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: d)
    return d


def _route(q, **kw):
    return policy._decide_route(_User(), _Req(q, **kw), force_cc=False, history=[])


@pytest.mark.parametrize("query", [
    "How do I check my workbook for the Zeta project before uploading it?",
    "How do I apply the Seq-Well protocol to my tissue samples?",
    "How would I apply the clustering approach from a paper to my own cohort?",
])
def test_a_container_cc_decision_on_a_howto_stays_container_cc(monkeypatch, query):
    """The docs-or-not call is the router's intent call, not a regex after it."""
    sent = _says(monkeypatch, cc_router.ROUTE_CC)
    assert _route(query) is sent


def test_forced_cc_stays_cc(monkeypatch):
    _says(monkeypatch, cc_router.ROUTE_CC)
    d = policy._decide_route(_Admin(), _Req("How do I upload samples?", force_route="cc"),
                             force_cc=False, history=[])
    assert (d.route, d.source) == (cc_router.ROUTE_CC, "forced")


def test_an_ns_decision_is_untouched(monkeypatch):
    sent = _says(monkeypatch, cc_router.ROUTE_NS)
    assert _route("How do I upload samples?") is sent


# ------------------------------------------------------------------ U7-02: no follow-up rule on a first turn
def _seen_rule(monkeypatch, history):
    from dmac_assistant.router.baml_client.types import Route
    seen = {}

    class _B:
        async def RouteQuery(self, input):
            seen["rule"] = input.followup_rule
            raise RuntimeError("stop here")

    monkeypatch.setattr(cc_router, "_load_router_deps",
                        lambda: (lambda capabilities=None: None, lambda path=None: [], Route, _B()))
    monkeypatch.setattr(cc_router, "_build_context_dir", lambda: None)
    cc_router._route_query("Break those down by sex.", history)
    return seen["rule"]


def test_followup_rule_is_none_for_an_empty_history(monkeypatch):
    assert _seen_rule(monkeypatch, []) is None
    assert _seen_rule(monkeypatch, None) is None


def test_followup_rule_is_the_split_text_after_one_answered_turn(monkeypatch):
    monkeypatch.delenv(followup.FOLLOWUP_ROUTING_ENV, raising=False)
    turn = router_context.HistoryTurn(position=1, user_message="Find Zeta mice",
                                      router_choice=cc_router.ROUTE_NS, status="completed")
    assert _seen_rule(monkeypatch, [turn]) == followup.FOLLOWUP_RULE_SPLIT


# ------------------------------------------------------------------ U7-03: "analysis" in a data-type name
@pytest.mark.parametrize("query", [
    "Of those, how many have a spectroscopy analysis derived from them?",
    "Of those, how many have an imaging analysis child?",
    "Which of them have a zeta-assay analysis sample?",
])
def test_analysis_in_a_data_type_name_is_not_a_cc_cue(query):
    assert followup.followup_shape(query) == "ns"


@pytest.mark.parametrize("query", [
    "Analyse those samples by sex", "Of those, analyze the age spread", "Run a statistical analysis on those samples",
    "Do an analysis of those samples", "Perform a survival analysis on those samples",
])
def test_an_analysis_request_is_a_cc_cue(query):
    assert followup.followup_shape(query) == "cc"


# ------------------------------------------------------------------ U7-04..09: text
def _baml():
    return (paths.DMAC_ASSISTANT_DIR / "baml_src" / "router.baml").read_text(encoding="utf-8")


def test_router_text_sends_site_howtos_to_ns_and_drops_the_join_sentence():
    src = _baml()
    assert "How-to questions about the site go to `nextseek_query`." in src
    assert "how many sample types or assay kinds" in src
    assert "one query over one source" not in src
    assert src.index("How-to questions about the site") < src.index("{{ input.followup_rule }}")


def test_router_and_container_cc_text_state_the_intent_rule():
    assert "Judge a how-to by its intent" in _baml()
    from NessieAI.cc.op_registry import routes
    assert "a how-to is judged by its intent" in routes.CONTAINER_CC_ROUTE.not_for


def test_route_text_and_generated_file_agree():
    path = paths.DMAC_ASSISTANT_DIR / "build_context" / "route_capabilities.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    text = json.dumps(doc)
    assert "how-to questions about the site" in text
    assert "a question about how to get a file from the site is the NS route" in text
    assert "that the user supplies or asks to have made" in text
    assert "building/validating" not in text


# ------------------------------------------------------------------ fix round 4.1: the file of one named SOP
@pytest.mark.parametrize("query", [
    "Give me the file for the SOP titled Z.QIL-990101-V2_Zeta-fixation.docx",   # the dev smoke's shape
    "Download SOP 9001",                                                          # by id
    'Can I get the PDF of the protocol "Quill Staining Protocol"?',              # quoted title, protocol = SOP
    "Download SOP #9002 for me, please.",
])
def test_the_file_of_one_named_sop_the_router_sent_to_cc_goes_to_ns(monkeypatch, query):
    _says(monkeypatch, cc_router.ROUTE_CC)
    d = _route(query)
    assert (d.route, d.source) == (cc_router.ROUTE_NS, "sop_file")
    assert d.model_class is None and d.model_id is None
    assert "the router" in d.reasoning


@pytest.mark.parametrize("query", [
    "make me a SOP template file",                                    # no SOP named: a new file to make
    "convert these SOPs to a PDF",                                    # several SOPs, a new file
    "Make a PDF of SOP 9001",                                         # one SOP, but a new file to make
    "Download SOP 9001 and SOP 9002",                                 # two SOPs
    "Download SOP 9001 and check it against /home/quill/zeta.csv",    # supplies a file to work on
    "Which SOP did the Zeta mice follow?",                            # no SOP named, no file asked for
])
def test_new_files_several_sops_or_supplied_content_stay_on_cc(monkeypatch, query):
    sent = _says(monkeypatch, cc_router.ROUTE_CC)
    assert _route(query) is sent


def test_router_and_route_text_send_a_named_sops_file_to_ns():
    src = " ".join(_baml().split())
    assert "The file of one SOP named by its id or its exact title goes to `nextseek_query`" in src
    assert src.index("How-to questions about the site") < src.index("The file of one SOP named") < src.index(
        "{{ input.followup_rule }}")
    path = paths.DMAC_ASSISTANT_DIR / "build_context" / "route_capabilities.json"
    assert "the file of one SOP named by its id or its exact title" in path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ 2026-10-13 harmo_onto: harmonize requests
def test_route_text_splits_harmonization_by_intent():
    """Operator rulings 2026-10-13: asking which values or spellings exist (and how many) is a graph
    search on NS; asking to harmonize, merge or map them to the house terms is container_cc's, and a
    chat that asked what exists on NS pivots there with that result. Both the registry text and the
    generated route_capabilities.json carry it."""
    from NessieAI.cc.op_registry.routes import CONTAINER_CC_ROUTE

    text = " ".join(CONTAINER_CC_ROUTE.not_for.split())
    assert "counts and breakdowns, including counts across spellings, all run in the graph." in text
    assert "Values written several ways are judged by intent, like a how-to" in text
    assert ("asking which values, spellings or types an attribute holds, and how many of each, is a "
            "NExtSEEK graph search on the NS route") in text
    assert ("asking to harmonize, merge or map them to the house terms, or to build the update workbook "
            "that does it, is this route.") in text
    assert "then asks to harmonize it comes here with that earlier result" in text
    assert "harmonisation over metadata" not in text
    path = paths.DMAC_ASSISTANT_DIR / "build_context" / "route_capabilities.json"
    assert "Values written several ways are judged by intent, like a how-to" in path.read_text(encoding="utf-8")


def test_route_text_sends_writes_to_container_cc():
    """Operator ruling 2026-10-14: a request to create, register, change, upload, share or delete a
    stored record is container_cc's (it prepares the upload workbook or says where the change is made);
    how to do it in the site stays a how-to on the NS route. Registry text, generated
    route_capabilities.json and router.baml all carry it."""
    from NessieAI.cc.op_registry.routes import CONTAINER_CC_ROUTE, NS_WRITE_NOT_FOR

    assert ("And a request to create, register, change, upload, share or delete a stored record: no "
            "write runs from chat, so this route prepares the upload workbook for review or says "
            "where the change is made.") in " ".join(CONTAINER_CC_ROUTE.best_for.split())
    path = paths.DMAC_ASSISTANT_DIR / "build_context" / "route_capabilities.json"
    routes = json.loads(path.read_text(encoding="utf-8"))["routes"]
    ns = next(r for r in routes if r["route_name"] == "nextseek_query")
    assert NS_WRITE_NOT_FOR in ns["not_for"]
    baml = (paths.DMAC_ASSISTANT_DIR / "baml_src" / "router.baml").read_text(encoding="utf-8")
    baml = " ".join(baml.split())
    assert ("A request to create, register, change, upload, share or delete a stored record (a sample, "
            "study, investigation, project, protocol or a sample type's attributes) is `container_cc`, "
            "however short it is") in baml
    assert "No write runs from this chat" in baml
