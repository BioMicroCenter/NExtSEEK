"""The follow-up split (operator 2026-09-24, routing review section 5).

A follow-up NExtSEEK can answer from the earlier result or by re-running the earlier search stays on
nextseek_query: counting or filtering those results, a breakdown by one or two fields, recalling what a turn
found or which query it ran, a re-run with one filter changed. One that needs a file or download, a chart,
code, a comparison, summary or analysis goes to container_cc. Once a chat has a completed container_cc turn,
anything that refers back stays there. ``NESSIE_FOLLOWUP_ROUTING=cc`` restores the 2026-09-23 rule
(every follow-up to container_cc) word for word, with no rebuild.

Router stubbed exactly as test_followups_to_cc.py does (the BAML client is not called in this lane).
"""
from __future__ import annotations

import pytest

from NessieAI import paths
from NessieAI.router import followup
from NessieAI.router import policy
from NessieAI.router import router as cc_router
from NessieAI.router import router_context


class _Req:
    def __init__(self, query, force_route=None):
        self.query = query
        self.force_route = force_route


class _User:
    is_staff = True
    is_superuser = False


def _turn(choice, status="completed", position=1):
    return router_context.HistoryTurn(position=position, user_message="prior question",
                                      router_choice=choice, status=status)


def _entry(choice, status="completed", turn_id=1):
    return {"turn_id": turn_id, "user_query": "q", "router_choice": choice, "status": status,
            "mode": "cc" if choice == cc_router.ROUTE_CC else "graph_query"}


def _decision(route, source="baml", reasoning="router reasoning"):
    return cc_router.RouteDecision(route=route, model_class=None, model_id=None,
                                   reasoning=reasoning, source=source)


@pytest.fixture
def router_says_ns(monkeypatch):
    sentinel = _decision(cc_router.ROUTE_NS, reasoning="looks like a lookup")
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: sentinel)
    monkeypatch.setattr(cc_router, "_resolve_cc_model_id", lambda: "opus-id")
    return sentinel


@pytest.fixture
def split(monkeypatch):
    monkeypatch.delenv(followup.FOLLOWUP_ROUTING_ENV, raising=False)


@pytest.fixture
def cc_mode(monkeypatch):
    monkeypatch.setenv(followup.FOLLOWUP_ROUTING_ENV, "cc")


# ------------------------------------------------------------------ the mode
@pytest.mark.parametrize("value, mode", [
    (None, "split"), ("split", "split"), ("cc", "cc"), ("CC", "cc"), (" cc ", "cc"), ("", "split"),
    ("bogus", "split"),
])
def test_the_mode_defaults_to_split(monkeypatch, value, mode):
    if value is None:
        monkeypatch.delenv(followup.FOLLOWUP_ROUTING_ENV, raising=False)
    else:
        monkeypatch.setenv(followup.FOLLOWUP_ROUTING_ENV, value)
    assert followup.followup_mode() == mode


# ------------------------------------------------------------------ the shape (routing review 5.0)
NS_SHAPED = [
    "Which species are among those 73, and how many of each?",
    "Same search, but drop the flow cytometry requirement: only require sequencing data derived from them.",
    "What query did you run to get those?",
    "Break those down by sex.",
    "Remind me what that number was.",
    "Group those by genotype and give me the five largest groups.",
    "Which species are among those?",
    "Of those, how many have a treatment recorded?",
    "What sample types were represented in those results",
    "Now filter those to only CD8-depleted animals",
    'Try that search again with "Water" instead of "Water Study"',
    "Just the 4 week ones.",
    "Which of those sequencing samples have 'SHA' in their UID?",
    "which labs are those mouse samples from?",
    "Of those, how many are current smokers?",
    "And how many of those current smokers are female?",
    "Of those, which TCGA studies do they come from? Give me the top three with counts.",
    "What did you find?",
    "Tell me more about those",
]
CC_SHAPED = [
    "Plot the species of those as a bar chart.",
    "Open the results file from that search and tell me how many of those mice are female.",
    "Can you provide a table of these 250 samples for download/copy into Excel with the metadata info?",
    "that chart needs a log scale",
    "send me the file",
    "Download those samples as a spreadsheet",
    "Give me the previous results as CSV",
    "export them",
    "plot that",
    "Summarise those for me.",
    "Compare those with the MetNet ones.",
    "Write the UIDs of those to a file.",
    "Write a script that bins those samples by age.",
]


@pytest.mark.parametrize("query", NS_SHAPED)
def test_an_ns_shaped_followup(query):
    assert followup.followup_cue(query) is not None, query
    assert followup.followup_shape(query) == "ns", query


@pytest.mark.parametrize("query", CC_SHAPED)
def test_a_cc_shaped_followup(query):
    assert followup.followup_cue(query) is not None, query
    assert followup.followup_shape(query) == "cc", query


@pytest.mark.parametrize("query", ["How many HeLa samples do we have?", "What is the graph schema?",
                                   "Generate the SRP project summary report", ""])
def test_a_self_contained_question_has_no_shape(query):
    assert followup.followup_shape(query) is None


def test_the_graph_is_not_a_chart():
    """"in the graph" is an NExtSEEK question, not a request for a chart."""
    assert followup.followup_shape("How many of those are in the graph?") == "ns"


# ------------------------------------------------------------------ the policy under split
def test_split_keeps_an_ns_shaped_followup_where_the_router_sent_it(router_says_ns, split):
    d = policy._decide_route(_User(), _Req("Break those down by sex."), force_cc=False,
                             history=[_turn(cc_router.ROUTE_NS)], chat_log=[_entry(cc_router.ROUTE_NS)])
    assert d is router_says_ns


def test_split_sends_a_cc_shaped_followup_to_cc(router_says_ns, split):
    d = policy._decide_route(_User(), _Req("Plot the species of those."), force_cc=False,
                             history=[_turn(cc_router.ROUTE_NS)], chat_log=[_entry(cc_router.ROUTE_NS)])
    assert d.route == cc_router.ROUTE_CC and d.source == "followup"


def test_split_keeps_any_followup_on_cc_once_the_chat_used_cc(router_says_ns, split):
    d = policy._decide_route(_User(), _Req("Break those down by sex."), force_cc=False,
                             history=[_turn(cc_router.ROUTE_CC)], chat_log=[_entry(cc_router.ROUTE_CC)])
    assert d.route == cc_router.ROUTE_CC and d.source == "sticky"


def test_split_never_moves_a_cc_decision_to_ns(monkeypatch, split):
    sentinel = _decision(cc_router.ROUTE_CC)
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: sentinel)
    d = policy._decide_route(_User(), _Req("Break those down by sex."), force_cc=False,
                             history=[_turn(cc_router.ROUTE_NS)], chat_log=[_entry(cc_router.ROUTE_NS)])
    assert d is sentinel


# ------------------------------------------------------------------ the policy under cc (today's rule)
def test_cc_mode_sends_every_followup_to_cc(router_says_ns, cc_mode):
    d = policy._decide_route(_User(), _Req("Break those down by sex."), force_cc=False,
                             history=[_turn(cc_router.ROUTE_NS)], chat_log=[_entry(cc_router.ROUTE_NS)])
    assert d.route == cc_router.ROUTE_CC and d.source == "followup"


# ------------------------------------------------------------------ one source of the rule text
def test_the_rule_text_follows_the_mode(split, monkeypatch):
    assert followup.followup_rule_text() == followup.FOLLOWUP_RULE_SPLIT
    monkeypatch.setenv(followup.FOLLOWUP_ROUTING_ENV, "cc")
    assert followup.followup_rule_text() == followup.FOLLOWUP_RULE_CC


def test_the_cc_rule_is_the_2026_09_23_text_word_for_word():
    assert followup.FOLLOWUP_RULE_CC.startswith("Follow-ups go to `container_cc`. When the CURRENT message refers back to")
    assert "A chat that reaches `container_cc` stays there for anything that refers back:" in followup.FOLLOWUP_RULE_CC
    words = " ".join(followup.FOLLOWUP_RULE_CC.split())
    assert words.endswith("An out-of-scope message is still `unrelated`.")


def test_the_split_rule_says_the_operators_line():
    rule = followup.FOLLOWUP_RULE_SPLIT
    assert "A follow-up goes to `nextseek_query` when NExtSEEK can answer it from the" in rule
    assert "breaks them down by one or two fields" in rule
    assert "A follow-up goes to `container_cc` when it needs more than that" in rule
    assert "Once a chat has a completed `container_cc` turn" in rule
    assert "NExtSEEK cannot see Container-CC's results." in rule
    assert "—" not in rule


def test_router_baml_renders_the_rule_from_the_input_between_the_guard_and_the_summaries():
    src = (paths.DMAC_ASSISTANT_DIR / "baml_src" / "router.baml").read_text(encoding="utf-8")
    assert "followup_rule string?" in src
    guard = src.index("If the query has no connection")
    rule = src.index("{{ input.followup_rule }}")
    summaries = src.index("Open-ended summaries go to `container_cc`.")
    assert guard < rule < summaries
    assert "Follow-ups go to `container_cc`." not in src   # the text lives in one place: followup.py


def test_the_router_hands_the_rule_to_route_query(monkeypatch, split):
    """The rule reaches the BAML call as RouterInput.followup_rule (the real generated class)."""
    from dmac_assistant.router.baml_client.types import Route

    seen = {}

    class _B:
        async def RouteQuery(self, input):
            seen["rule"] = input.followup_rule
            raise RuntimeError("stop here")

    monkeypatch.setattr(cc_router, "_load_router_deps",
                        lambda: (lambda capabilities=None: None, lambda path=None: [], Route, _B()))
    monkeypatch.setattr(cc_router, "_build_context_dir", lambda: None)
    assert cc_router._route_query("Break those down by sex.") is None
    assert seen["rule"] == followup.FOLLOWUP_RULE_SPLIT
    monkeypatch.setenv(followup.FOLLOWUP_ROUTING_ENV, "cc")
    assert cc_router._route_query("Break those down by sex.") is None
    assert seen["rule"] == followup.FOLLOWUP_RULE_CC
