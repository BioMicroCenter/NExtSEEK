"""'My run is done' goes to Container-CC's reingest, never to a reopened pipeline wizard.

The submit reply's "Watching this run" block ends by telling the user to say "my run is
done" once the run finishes. That turn names no folder: CC's run-harvest finds the user's
launched run itself. On NExtSEEK the same words reach the reporter, which can start a fresh
pipeline wizard, so ``policy._decide_route`` redirects them after the router decides.

The redirect is a backstop to the router's intent rule (SF-9, 2026-10-30): it fires only
when the whole message is the hand-off. A finished run named as context for another
request keeps the router's route.
"""
import pytest

from NessieAI.router import policy
from NessieAI.router import router as cc_router
from NessieAI.router import run_done


class _Req:
    def __init__(self, query, force_route=None):
        self.query = query
        self.force_route = force_route


class _User:
    is_staff = False
    is_superuser = False


class _Admin(_User):
    is_superuser = True


def _router_says(monkeypatch, route, **extra):
    decision = cc_router.RouteDecision(
        route=route, model_class="opus" if route == cc_router.ROUTE_CC else None,
        model_id=None, reasoning="r", source="baml", **extra)
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: decision)
    return decision


RUN_DONE = [
    "my run is done",
    "My run is done!",
    "My run is done.",
    "my run's done",
    "The run finished.",
    "my nf-core run has finished, can you register the outputs?",
    "My rnaseq run is complete",
    "the pipeline is done",
    "My pipeline run completed overnight",
    "my job finished",
    "Run is done, register its outputs please",
    "My Luria run finished successfully",
    "my run in /net/bmc-pub10/data1/bmc/pipeline_cd/runs/nfcore_x_0 is done",
    "The nf-core run you launched for me has finished. Can you register its outputs?",
    "the job I submitted yesterday is done",
]

NOT_RUN_DONE = [
    "is my run done?",
    "Has my run finished?",
    "did the pipeline finish yet?",
    "Launch an nf-core rnaseq run for the NDMA mice",
    "When the sequencing run is done, launch rnaseq on it",
    "Start a new run now that the last one is done",
    "my run failed",
    "How many samples are done?",
    "Find the samples from the finished study",
    "I'm done for today",
    "Find samples from the run. The QC is done",
    "",
]


def test_every_corpus_run_done_case_is_a_cue():
    # The route_gate cases in corpus.json assert the route; the cue is the backstop
    # that holds them when the router does not.
    import json
    from NessieAI import paths
    corpus = json.loads(paths.NESSIE_CORPUS.read_text(encoding="utf-8"))
    cases = [v for v in corpus["families"]["pipeline_output_reingest"]["variants"]
             if v["id"].startswith("route.cc_reingest_") and v["status"] == "active"]
    assert len(cases) == 3
    for case in cases:
        assert run_done.run_done_reason(case["turns"][0]["query"]), case["id"]


def test_no_corpus_run_done_context_case_is_a_cue():
    # SF-9: the route_gate cases that name a finished run as context for a search assert
    # nextseek_query; the backstop must leave them to the router.
    import json
    from NessieAI import paths
    corpus = json.loads(paths.NESSIE_CORPUS.read_text(encoding="utf-8"))
    cases = [v for v in corpus["families"]["sample_search"]["variants"]
             if v["id"].startswith("route.ns_run_done_context_") and v["status"] == "active"]
    assert len(cases) == 2
    for case in cases:
        assert run_done.run_done_reason(case["turns"][0]["query"]) is None, case["id"]


@pytest.mark.parametrize("query", RUN_DONE)
def test_a_run_done_announcement_is_a_cue(query):
    assert run_done.run_done_reason(query)


@pytest.mark.parametrize("query", NOT_RUN_DONE)
def test_a_question_a_launch_or_another_subject_is_not(query):
    assert run_done.run_done_reason(query) is None


@pytest.mark.parametrize("route", [cc_router.ROUTE_NS, cc_router.ROUTE_UNRELATED])
def test_a_run_done_turn_the_router_kept_off_cc_goes_to_cc_reingest(monkeypatch, route):
    _router_says(monkeypatch, route, router_model="m-1")
    d = policy._decide_route(_User(), _Req("My run is done."), force_cc=False, session={})
    assert d.route == cc_router.ROUTE_CC
    assert d.source == "reingest"
    assert d.task_family == "pipeline_output_reingest"
    assert d.model_class == "opus"
    # What the router said is kept, as the follow-up guard keeps it.
    assert d.attempted_route == route and d.attempted_source == "baml"
    assert d.router_model == "m-1"


def test_an_open_pipeline_wizard_does_not_capture_a_run_done_turn(monkeypatch):
    _router_says(monkeypatch, cc_router.ROUTE_NS)
    session = {"pipeline_agent": {"active": True}}
    d = policy._decide_route(_User(), _Req("my run is done"), force_cc=False, session=session)
    assert d.route == cc_router.ROUTE_CC
    assert d.source == "reingest"


def test_a_run_done_turn_the_router_sent_to_cc_is_left_alone(monkeypatch):
    sent = _router_says(monkeypatch, cc_router.ROUTE_CC)
    d = policy._decide_route(_User(), _Req("my run is done"), force_cc=False, session={})
    assert d is sent


def test_an_admin_force_to_ns_still_wins(monkeypatch):
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: pytest.fail("router called"))
    d = policy._decide_route(_Admin(), _Req("my run is done", force_route="ns"),
                             force_cc=False, session={})
    assert d.route == cc_router.ROUTE_NS and d.source == "forced"


def test_a_question_about_the_run_is_left_to_the_router(monkeypatch):
    sent = _router_says(monkeypatch, cc_router.ROUTE_NS)
    d = policy._decide_route(_User(), _Req("is my run done?"), force_cc=False, session={})
    assert d is sent


def test_a_reingest_turn_does_not_fall_back_to_ns_when_cc_is_down(monkeypatch):
    # NExtSEEK cannot register a run's outputs; its reporter would open a new wizard.
    _router_says(monkeypatch, cc_router.ROUTE_NS)
    d = policy._decide_route(_User(), _Req("my run is done"), force_cc=False, session={})
    assert policy._fallback_when_cc_unavailable(
        d, lambda: pytest.fail("a reingest turn must not probe the runner")) is d


def test_the_cue_survives_bad_input():
    assert run_done.run_done_reason(None) is None


# ------------------------------------------------------------------ SF-9: run-done by intent
#: A finished run named only as context for another request (2026-10-30, SF-9). Each matched
#: the old cue and went to Container-CC's reingest whatever the router said.
RUN_DONE_AS_CONTEXT = [
    "The sequencing run is complete; how many D.SEQ samples are in project 12?",
    "the PCR run is done, which protocol did we use?",
    "Our sequencing run finished last week. Which samples are from project 12?",
    "My run is done. Which genes changed between the two groups?",
    "the run finished, show me the MultiQC report",
    "My run is done, how many samples did it process?",
    "Find D.SEQ samples in project 12. My run is done.",
    "my rnaseq run is done, now compare the treated and control samples",
]

#: A wet-lab run has no nf-core outputs to register; whether it is a hand-off is the router's.
LAB_RUN_DONE = [
    "The sequencing run is complete",
    "my flow run is done",
    "Our NovaSeq run finished overnight.",
]

#: The hand-off said other ways, still the backstop's: greetings, timing, a folder, a request
#: to register or load the outputs.
MORE_RUN_DONE = [
    "Hi! My run is done.",
    "My run finished. Please load the results into NExtSEEK.",
    "my sarek run is done, go ahead and register the outputs",
    "The run finished 2 hours ago",
    "my run is done: /net/bmc-pub10/data1/bmc/pipeline_cd/runs/nfcore_rnaseq_1",
    "Thanks! The pipeline has finished, register it please",
]


@pytest.mark.parametrize("query", RUN_DONE_AS_CONTEXT + LAB_RUN_DONE)
def test_a_finished_run_named_as_context_is_not_a_cue(query):
    assert run_done.run_done_reason(query) is None


@pytest.mark.parametrize("query", MORE_RUN_DONE)
def test_the_hand_off_said_other_ways_is_still_a_cue(query):
    assert run_done.run_done_reason(query)


@pytest.mark.parametrize("query", RUN_DONE_AS_CONTEXT[:3])
@pytest.mark.parametrize("route", [cc_router.ROUTE_NS, cc_router.ROUTE_UNRELATED])
def test_the_router_keeps_a_turn_whose_finished_run_is_only_context(monkeypatch, route, query):
    sent = _router_says(monkeypatch, route)
    assert policy._decide_route(_User(), _Req(query), force_cc=False, session={}) is sent


def test_the_heuristic_fallback_keeps_a_search_off_reingest(monkeypatch):
    # BAML down: the keyword heuristic decides, and the backstop no longer overrides it.
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: cc_router._heuristic(q))
    d = policy._decide_route(_User(), _Req(RUN_DONE_AS_CONTEXT[0]), force_cc=False, session={})
    assert d.source == "heuristic" and d.route == cc_router.ROUTE_NS


def test_the_heuristic_fallback_still_sends_the_hand_off_to_reingest(monkeypatch):
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: cc_router._heuristic(q))
    d = policy._decide_route(_User(), _Req("My run is done."), force_cc=False, session={})
    assert (d.route, d.source, d.task_family) == (
        cc_router.ROUTE_CC, "reingest", "pipeline_output_reingest")
    assert d.attempted_source == "heuristic"


def _router_prompt():
    from NessieAI import paths
    src = (paths.DMAC_ASSISTANT_DIR / "baml_src" / "router.baml").read_text(encoding="utf-8")
    return " ".join(src.split())


def test_the_router_prompt_states_the_run_done_intent_rule():
    src = _router_prompt()
    assert "A message whose intent is to hand off a finished run" in src
    assert "A message whose intent is to hand off a finished run is `container_cc`" in src
    assert "A finished run named only as context for another request is routed by that request" in src
    assert "how many D.SEQ samples are in project 12?" in src
    # The rule sits with the other nf-core paragraph, inside the prompt, before the follow-up rule.
    assert (src.index("Choosing, building or launching an nf-core pipeline")
            < src.index("A message whose intent is to hand off a finished run")
            < src.index("{{ input.followup_rule }}"))
