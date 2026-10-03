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
    "Where can I download the Quill lab's mouse samples I found as Excel?",
    "Is there a way to export the ZZZ-990101ABC-1-PUB record to a spreadsheet?",
    "what's the best way to register a new sample type?",
])
def test_a_howto_the_router_sent_to_cc_goes_to_ns(monkeypatch, query):
    _says(monkeypatch, cc_router.ROUTE_CC)
    d = _route(query)
    assert (d.route, d.source) == (cc_router.ROUTE_NS, "howto")
    assert d.model_class is None and d.model_id is None
    assert "the router" in d.reasoning


@pytest.mark.parametrize("query", [
    "How do I fix this sheet?\nuid\tspecies\nZZZ-1\tmouse\nZZZ-2\trat",           # supplied table
    "How do I make an upload sheet? Could you make one for the Zeta mice?",       # work request
    "Validate this workbook for the Zeta project",                               # no how-to opener
    "How can I clean zeta_samples.xlsx? Please do it for me.",                    # file name + work
    "How do I fix this?\n```python\nx = 1\n```",                                  # code block
    "How do I load /home/quill/data/zeta.csv into the project?",                  # path
])
def test_work_or_supplied_content_stays_on_cc(monkeypatch, query):
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


def test_route_text_and_generated_file_agree():
    path = paths.DMAC_ASSISTANT_DIR / "build_context" / "route_capabilities.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    text = json.dumps(doc)
    assert "how-to questions about the site" in text
    assert "a question about how to get a file from the site is the NS route" in text
    assert "that the user supplies or asks to have made" in text
    assert "building/validating" not in text
