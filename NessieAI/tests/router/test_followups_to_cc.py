"""The 2026-09-23 rule, which is now the ``NESSIE_FOLLOWUP_ROUTING=cc`` setting (the rollback for
the 2026-09-24 follow-up split; the split itself is pinned in test_followup_split.py). Every test
here runs with the switch at ``cc``; stickiness holds under both settings.

2026-09-23 rulings: every follow-up goes to container_cc; once a chat is on
container_cc, a turn that refers back to its results or to the conversation stays there,
and a SELF-CONTAINED question (one that refers back to nothing) is routed normally again.

Pins, at the Python layer around the BAML router (the BAML client is not generated in
this lane, so the router is stubbed exactly as test_decide_route_sticky_cc.py does):

* ``followup.followup_cue``: which messages refer back to an earlier result, and which
  self-contained questions do not;
* ``policy._decide_route``: an NS-bound follow-up after an answered turn becomes
  ``container_cc`` with ``source == "followup"``; the precedence around it;
* stickiness, narrowed: in a chat already on CC a turn that refers back stays on CC
  (``source == "sticky"``) and a self-contained one is left to the router, over the
  whole chat_log, whatever came in between;
* ``policy._fallback_when_cc_unavailable``: a policy-made CC turn falls back to NS
  for one turn when the CC runner is down, and nothing else does;
* route_capabilities.json and router.baml say the same thing.
"""
from __future__ import annotations

import json
import re

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
    is_staff = True          # the SEEK login sets is_staff on everyone
    is_superuser = False


class _Admin:
    is_staff = True
    is_superuser = True


def _turn(choice, status="completed", position=1):
    return router_context.HistoryTurn(position=position, user_message="prior question",
                                      router_choice=choice, status=status)


def _entry(choice, status="completed", turn_id=1):
    """A raw chat_log entry, as the CC turn hands _decide_route the whole log."""
    return {"turn_id": turn_id, "user_query": "q", "router_choice": choice, "status": status,
            "mode": "cc" if choice == cc_router.ROUTE_CC else "graph_query"}


def _decision(route, source="baml", reasoning="router reasoning"):
    return cc_router.RouteDecision(route=route, model_class=None, model_id=None,
                                   reasoning=reasoning, source=source)


@pytest.fixture(autouse=True)
def _followup_switch_at_cc(monkeypatch):
    monkeypatch.setenv(followup.FOLLOWUP_ROUTING_ENV, "cc")


@pytest.fixture
def router_says_ns(monkeypatch):
    sentinel = _decision(cc_router.ROUTE_NS, reasoning="looks like a lookup")
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: sentinel)
    monkeypatch.setattr(cc_router, "_resolve_cc_model_id", lambda: "opus-id")
    return sentinel


# ------------------------------------------------------------------ the cue
FOLLOWUPS = [
    "that chart needs a log scale",
    "send me the file",
    "And how many are female?",
    "What about the liver?",
    "Are they all from the same lab?",
    "the female ones only",
    "Which ones are in the MetNet project?",
    "Group those by genotype and give me the five largest groups",
    "Which species are among those 73?",
    "which of them are female?",
    "Of those, how many came from the liver?",
    "Plot the species of those.",
    "plot that",
    "Download those samples as a spreadsheet",
    "break that down by lab",
    "Break those down by genotype",
    "Remind me what that number was.",
    "How many results was that again?",
    "What did you find?",
    "What query did you run?",
    "which cypher did you use for that?",
    "Same search but only D.SEQ",
    "Run that search again with DFCI4 instead",
    "Just the 4 week ones.",
    "Show me the ones from China instead.",
    "What labs do those samples come from?",
    "Give me the previous results as CSV",
    "export them",
    "follow up on those",
    "Tell me more about those",
]

SELF_CONTAINED = [
    "How many HeLa samples do we have?",
    "Show me the list of sample types",
    "Generate the SRP project summary report",
    "What species are the samples in the IMPAcTb project, and how many of each?",
    "What is the graph schema?",
    "How many samples are in the SRP project?",
    "Find NHP samples that have both flow cytometry data and sequencing data derived from them",
    "How many RNA samples does the Kamm lab have?",
    "What is a TIS sample?",
    "List the sample types in NExtSEEK",
    "Which assays do I have access to?",
    "Show me the first sample registered in the MetNet project",
    "Find the tissue samples in the MIT_SRP project",
    "What is the weather in Boston?",
    "Which mouse samples treated with NDMA are female?",
    "",
]


@pytest.mark.parametrize("query", FOLLOWUPS)
def test_a_back_reference_is_a_followup_cue(query):
    assert followup.followup_cue(query) is not None, query


@pytest.mark.parametrize("query", SELF_CONTAINED)
def test_a_self_contained_question_is_not(query):
    assert followup.followup_cue(query) is None, (query, followup.followup_cue(query))


def test_a_cue_with_nothing_answered_before_it_is_not_a_followup():
    assert followup.followup_reason("which of them are female?", []) is None
    assert followup.followup_reason("which of them are female?", None) is None
    only_asides = [_turn(cc_router.ROUTE_UNRELATED, position=1)]
    assert followup.followup_reason("which of them are female?", only_asides) is None
    only_errors = [_turn(cc_router.ROUTE_NS, status="error", position=1)]
    assert followup.followup_reason("which of them are female?", only_errors) is None


def test_either_engine_can_be_followed_up():
    for route in (cc_router.ROUTE_NS, cc_router.ROUTE_CC):
        assert followup.followup_reason("which of them are female?", [_turn(route)]) == "of-those"
    # Raw chat_log entries count too, legacy ones (no router_choice) as NS turns.
    assert followup.followup_reason("plot that", [{"turn_id": 1, "mode": "graph_query"}])


# ------------------------------------------------------------ the policy
def test_an_ns_bound_followup_after_an_ns_turn_goes_to_cc(router_says_ns):
    d = policy._decide_route(_User(), _Req("Which species are among those 73?"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_NS)])
    assert d.route == cc_router.ROUTE_CC
    assert d.source == "followup"
    assert d.model_class == "opus" and d.model_id == "opus-id"
    assert d.attempted_route == cc_router.ROUTE_NS and d.attempted_source == "baml"
    assert "followup_cc (of-those)" in d.reasoning and "looks like a lookup" in d.reasoning


def test_a_fresh_question_in_the_same_chat_stays_with_the_router(router_says_ns):
    d = policy._decide_route(_User(), _Req("How many samples are in the SRP project?"),
                             force_cc=False, history=[_turn(cc_router.ROUTE_NS)])
    assert d is router_says_ns


def test_a_first_message_is_never_a_followup(router_says_ns):
    d = policy._decide_route(_User(), _Req("which of them are female?"), force_cc=False,
                             history=[], chat_log=[])
    assert d is router_says_ns


def test_unrelated_is_never_turned_into_a_followup(monkeypatch):
    sentinel = _decision(cc_router.ROUTE_UNRELATED)
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: sentinel)
    d = policy._decide_route(_User(), _Req("what's the weather for those?"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_NS)])
    assert d is sentinel


def test_a_router_cc_decision_is_not_relabelled(monkeypatch):
    sentinel = _decision(cc_router.ROUTE_CC)
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: sentinel)
    d = policy._decide_route(_User(), _Req("plot that"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_NS)])
    assert d is sentinel


def test_force_route_ns_beats_the_followup_rule(router_says_ns):
    d = policy._decide_route(_Admin(), _Req("plot that", force_route="ns"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_NS)])
    assert d.route == cc_router.ROUTE_NS and d.source == "forced"


def test_a_non_admin_cannot_force_ns_past_the_followup_rule(router_says_ns):
    d = policy._decide_route(_User(), _Req("plot that", force_route="ns"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_NS)])
    assert d.route == cc_router.ROUTE_CC and d.source == "followup"


def test_an_open_pipeline_wizard_beats_the_followup_rule(router_says_ns):
    d = policy._decide_route(_User(), _Req("use those samples"), force_cc=False,
                             session={"pipeline_agent": {"active": True}},
                             history=[_turn(cc_router.ROUTE_NS)])
    assert d.route == cc_router.ROUTE_NS and d.source == "pipeline"


def test_a_back_reference_in_a_chat_on_cc_is_labelled_sticky(router_says_ns):
    d = policy._decide_route(_User(), _Req("plot that"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_CC)])
    assert d.route == cc_router.ROUTE_CC and d.source == "sticky"


def test_a_broken_followup_inspection_falls_through_to_the_router(router_says_ns, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(followup, "followup_reason", boom)
    d = policy._decide_route(_User(), _Req("plot that"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_NS)])
    assert d is router_says_ns


# ------------------------------------------------------------ stickiness
def test_a_turn_that_refers_back_after_cc_stays_on_cc(router_says_ns):
    for query in ("Which of those are female?", "make that chart log-scale", "send me the file",
                  "What did you find?", "Remind me what that number was."):
        d = policy._decide_route(_User(), _Req(query), force_cc=False,
                                 history=[_turn(cc_router.ROUTE_CC)],
                                 chat_log=[_entry(cc_router.ROUTE_CC)])
        assert d.route == cc_router.ROUTE_CC and d.source == "sticky", query
        assert d.attempted_route == cc_router.ROUTE_NS
        assert d.reasoning.startswith("sticky_cc (")


def test_a_self_contained_question_after_cc_is_routed_normally(router_says_ns):
    """The narrowing: "How many HeLa samples do we have?" goes where a new chat's would."""
    for query in ("How many HeLa samples do we have?", "How many samples are in the SRP project?",
                  "Find the mice in the MIT_SRP project"):
        d = policy._decide_route(_User(), _Req(query), force_cc=False,
                                 history=[_turn(cc_router.ROUTE_CC)],
                                 chat_log=[_entry(cc_router.ROUTE_CC)])
        assert d is router_says_ns, query


def test_cc_then_self_contained_then_of_those_goes_back_to_cc(router_says_ns):
    """The probe's stickiness case, at the policy layer: NS, CC follow-up, a fresh
    question back on NS, and an "of those" about it back on CC."""
    log = [_entry(cc_router.ROUTE_NS, turn_id=1), _entry(cc_router.ROUTE_CC, turn_id=2)]
    fresh = policy._decide_route(_User(), _Req("How many HeLa samples do we have?"),
                                 force_cc=False, history=router_context.build_history(log),
                                 chat_log=log)
    assert fresh is router_says_ns
    log.append(_entry(cc_router.ROUTE_NS, turn_id=3))
    back = policy._decide_route(_User(), _Req("Of those, which are HeLa-S3?"), force_cc=False,
                                history=router_context.build_history(log), chat_log=log)
    assert back.route == cc_router.ROUTE_CC and back.source == "sticky"


def test_the_label_outlives_the_routers_history_window(router_says_ns):
    """Six `unrelated` asides push the CC turn out of the 5-turn window, not out of the chat."""
    log = [_entry(cc_router.ROUTE_CC, turn_id=1)] + [
        _entry(cc_router.ROUTE_UNRELATED, turn_id=n) for n in range(2, 8)]
    window = router_context.build_history(log)
    assert all(t.router_choice == cc_router.ROUTE_UNRELATED for t in window)
    d = policy._decide_route(_User(), _Req("plot those"), force_cc=False,
                             history=window, chat_log=log)
    assert d.route == cc_router.ROUTE_CC and d.source == "sticky"
    # Without the whole log there is nothing answered in view: left to the router.
    assert policy._decide_route(_User(), _Req("plot those"), force_cc=False,
                                history=window) is router_says_ns


def test_a_later_cc_error_or_forced_ns_turn_does_not_change_the_label(router_says_ns):
    for later in (_entry(cc_router.ROUTE_CC, status="error", turn_id=2),
                  _entry(cc_router.ROUTE_NS, turn_id=2)):
        log = [_entry(cc_router.ROUTE_CC, turn_id=1), later]
        d = policy._decide_route(_User(), _Req("which of them are female?"), force_cc=False,
                                 history=router_context.build_history(log), chat_log=log)
        assert d.route == cc_router.ROUTE_CC and d.source == "sticky"


def test_a_chat_whose_only_cc_turn_errored_is_not_on_cc(router_says_ns):
    log = [_entry(cc_router.ROUTE_CC, status="error", turn_id=1)]
    assert policy._decide_route(_User(), _Req("how many mice"), force_cc=False,
                                history=router_context.build_history(log),
                                chat_log=log) is router_says_ns
    # ...and a back-reference there has nothing answered to refer to.
    assert policy._decide_route(_User(), _Req("which of them"), force_cc=False,
                                history=router_context.build_history(log),
                                chat_log=log) is router_says_ns


def test_the_nested_ns_entries_a_cc_turn_writes_keep_the_sticky_label(router_says_ns):
    """A CC turn's own nextseek-query calls append NS entries; the chat is still on CC."""
    log = [_entry(cc_router.ROUTE_NS, turn_id=1), _entry(cc_router.ROUTE_CC, turn_id=2),
           _entry(cc_router.ROUTE_NS, turn_id=3)]
    d = policy._decide_route(_User(), _Req("which of those are lung?"), force_cc=False,
                             history=router_context.build_history(log), chat_log=log)
    assert d.route == cc_router.ROUTE_CC and d.source == "sticky"


def test_unrelated_is_still_never_converted_in_a_chat_on_cc(monkeypatch):
    sentinel = _decision(cc_router.ROUTE_UNRELATED)
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: sentinel)
    log = [_entry(cc_router.ROUTE_CC)]
    d = policy._decide_route(_User(), _Req("what's the weather for those?"), force_cc=False,
                             history=router_context.build_history(log), chat_log=log)
    assert d is sentinel


# -------------------------------------------------- CC unavailable: one-turn fallback
def _policy_cc(source):
    return cc_router.RouteDecision(route=cc_router.ROUTE_CC, model_class="opus", model_id="m",
                                   reasoning="r", source=source,
                                   attempted_route=cc_router.ROUTE_NS, attempted_source="baml")


@pytest.mark.parametrize("source", ["sticky", "followup"])
def test_a_policy_made_cc_turn_falls_back_to_ns_when_cc_is_down(source):
    d = policy._fallback_when_cc_unavailable(_policy_cc(source), lambda: (False, "no docker"))
    assert d.route == cc_router.ROUTE_NS
    assert d.source == "cc_unavailable"
    assert d.attempted_route == cc_router.ROUTE_CC and d.attempted_source == source
    assert "no docker" in d.reasoning


def test_a_probe_that_raises_counts_as_unavailable():
    def boom():
        raise OSError("socket")
    d = policy._fallback_when_cc_unavailable(_policy_cc("sticky"), boom)
    assert d.route == cc_router.ROUTE_NS


def test_a_policy_made_cc_turn_stays_when_cc_is_up():
    original = _policy_cc("followup")
    assert policy._fallback_when_cc_unavailable(original, lambda: (True, "ok")) is original


@pytest.mark.parametrize("decision", [
    _decision(cc_router.ROUTE_CC),                             # the router chose CC
    _decision(cc_router.ROUTE_CC, source="forced"),            # an admin forced CC
    _decision(cc_router.ROUTE_NS),
    _decision(cc_router.ROUTE_UNRELATED),
])
def test_nothing_else_falls_back_or_is_even_probed(decision):
    def must_not_probe():
        raise AssertionError("probed")
    assert policy._fallback_when_cc_unavailable(decision, must_not_probe) is decision


# ------------------------------------- route_capabilities.json and router.baml agree
def _routes():
    doc = json.loads((paths.DMAC_BUILD_CONTEXT / "route_capabilities.json").read_text(encoding="utf-8"))
    return {r["route_name"]: r for r in doc["routes"]}


def test_route_capabilities_carries_the_ruling():
    """2026-09-24: the route descriptions stop restating the follow-up rule (it lives in the router
    prompt paragraph, filled from the switch), so they are right under either setting."""
    from NessieAI.build_tools.gen_op_surfaces.route_capabilities import apply_followup_ruling
    from NessieAI.cc.op_registry.routes import (
        CONTAINER_CC_ROUTE,
        FOLLOWUP_FAMILIES_ON_CC,
        NS_FOLLOWUP_NOT_FOR,
    )

    routes = _routes()
    ns, cc = routes[cc_router.ROUTE_NS], routes[cc_router.ROUTE_CC]
    assert apply_followup_ruling(ns) == ns
    ns_families = {f["name"] for f in ns["task_families"]}
    cc_families = {f["name"] for f in cc["task_families"]}
    assert FOLLOWUP_FAMILIES_ON_CC == ("cross_session_memory",)
    assert "cross_session_memory" not in ns_families
    assert {"followup_over_results", "search_refinement"} <= ns_families
    assert {"followup_over_results", "search_refinement", "cross_session_memory"} <= cc_families
    for label in ("Follow-up Questions", "Search Refinements"):
        assert label in ns["best_for"]
    assert NS_FOLLOWUP_NOT_FOR in ns["not_for"]
    assert "container_cc answers every follow-up" not in ns["not_for"]
    assert cc["best_for"] == CONTAINER_CC_ROUTE.best_for
    assert "every follow-up to an earlier turn" not in cc["best_for"]
    assert "the follow-ups the follow-up rule sends here" in cc["best_for"]
    assert "a later message that refers back stays here" in cc["best_for"]
    assert "self-contained question is routed on its own merits" in cc["best_for"]
    assert cc["not_for"].endswith("or a follow-up the follow-up rule sends here.")


def test_apply_followup_ruling_is_idempotent_and_keeps_the_rest():
    from NessieAI.build_tools.gen_op_surfaces.route_capabilities import apply_followup_ruling

    before = {
        "route_name": "nextseek_query", "description": "d", "tools": ["x"],
        "best_for": "Requests supported by the NS capability authority: Sample Search; "
                    "Follow-up Questions; Search Refinements; Graph Queries.",
        "not_for": "Not intended for: Generate visualizations or charts.; Compare groups analytically..",
        "task_families": [{"name": "sample_search"}, {"name": "followup_over_results"},
                          {"name": "search_refinement"}, {"name": "cross_session_memory"}],
    }
    once = apply_followup_ruling(before)
    assert apply_followup_ruling(once) == once
    assert once["best_for"] == before["best_for"]
    assert once["not_for"].startswith("Not intended for: Generate visualizations or charts.; "
                                      "Compare groups analytically.; A follow-up the follow-up rule sends")
    assert once["not_for"].endswith("'what is X' stay here.")
    assert [f["name"] for f in once["task_families"]] == [
        "sample_search", "followup_over_results", "search_refinement"]
    assert once["description"] == "d" and once["tools"] == ["x"]


def test_router_baml_states_the_followup_and_sticky_rules_after_the_unrelated_guard():
    """The rule text now lives in followup.FOLLOWUP_RULE_CC / _SPLIT; router.baml renders it."""
    src = (paths.DMAC_ASSISTANT_DIR / "baml_src" / "router.baml").read_text(encoding="utf-8")
    guard = src.index("If the query has no connection")
    rule = src.index("{{ input.followup_rule }}")
    out = src.index("{{ ctx.output_format }}")
    assert guard < rule < out
    text = followup.FOLLOWUP_RULE_CC
    assert text.index("Follow-ups go to `container_cc`.") < text.index(
        "A chat that reaches `container_cc` stays there for anything that refers back")
    # The rule names the self-contained exception, and never lets CC take `unrelated`.
    for rule_text in (followup.FOLLOWUP_RULE_CC, followup.FOLLOWUP_RULE_SPLIT):
        assert re.search(r"self-contained question[\s\S]{0,120}routed\s+on its own merits", rule_text)
        assert "message is still `unrelated`." in rule_text
    assert "is routed exactly as it would be in a new chat" in followup.FOLLOWUP_RULE_CC


def test_open_ended_summaries_are_ruled_to_container_cc_in_every_router_surface():
    """2026-09-23 ruling: open-ended project/investigation summaries go to container_cc, and formal
    reports, upload statistics and 'what is X' stay on NS. The router reads route_capabilities.json,
    which the generator cannot rebuild while its evidence fingerprint is stale, so the committed
    file must match routes.py and the ruling by hand: pinned here (prod retest ticket 4, "How much
    data does the Shoulders investigation hold?" stayed on NS)."""
    import json
    from pathlib import Path

    from NessieAI.build_tools.gen_op_surfaces.route_capabilities import apply_followup_ruling
    from NessieAI.cc.op_registry.routes import CONTAINER_CC_ROUTE, NS_SUMMARY_NOT_FOR

    root = Path(__file__).resolve().parents[3]
    doc = json.loads((root / "NessieAI/dmac_assistant/build_context/route_capabilities.json").read_text())
    routes = {r["route_name"]: r for r in doc["routes"]}
    assert routes["container_cc"]["best_for"] == CONTAINER_CC_ROUTE.best_for
    assert routes["container_cc"]["not_for"] == CONTAINER_CC_ROUTE.not_for
    assert "open-ended summary" in CONTAINER_CC_ROUTE.best_for
    assert NS_SUMMARY_NOT_FOR in routes["nextseek_query"]["not_for"]
    assert apply_followup_ruling(routes["nextseek_query"]) == routes["nextseek_query"]
    baml = (root / "NessieAI/dmac_assistant/baml_src/router.baml").read_text()
    assert "Open-ended summaries go to `container_cc`" in baml
