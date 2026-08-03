from pathlib import Path
from nessie_tests import runner

OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"

CC_ROUTED = {"status": "running", "progress": [
    {"event": "route_decided", "data": {"route": "container_cc", "model_class": "opus", "source": "baml", "reasoning": ""}}]}

NS_ROUTED = {"status": "running", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": ""}}]}

# A COMPLETED nextseek turn. Full-tier cases poll until the status is terminal,
# so a perpetually-"running" payload would spin forever against a frozen clock.
NS_DONE = {"status": "completed", "progress": NS_ROUTED["progress"] + [
    {"event": "query_complete", "data": {"reply": "ok", "debug": {"parser_plan": {"mode": "new_search"}}}}]}


def _post():
    def post_query(body):
        return {"task_id": "t", "session_id": "s"}
    return post_query


def _cc_gate(vid="gate.cc"):
    """A CC route_gate variant, built here rather than taken from the corpus.

    The 2026-07-30 review retired all three real CC gates, which broke these
    tests. That coupling was the bug: a runner test should not depend on which
    cases the operator currently keeps. Building the variant inline makes the
    test measure the runner and survive any corpus decision.
    """
    from e2e.catalog import Variant, Turn
    return Variant(
        family="nessie_route", id=vid, name="cc gate",
        tags=["nessie", "route_gate", "overlay"], requires_env=[],
        turns=[Turn(label="m", query="q",
                    pass_criteria=[{"field": "route", "op": "eq", "value": "container_cc"}])])



def test_run_suite_route_tier_specific(tmp_path, monkeypatch):
    # route tier + specific scope → only route_gate cases; injected clients, no live stack
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [_cc_gate()])
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: CC_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "gate.cc")
    assert entry.status == "passed" and entry.route == "container_cc"
    assert (tmp_path / "manifest.json").exists() and (tmp_path / "report.html").exists()


def test_no_route_expectation_is_injected_into_imported_variants():
    """Imported variants must not inherit an uncurated route expectation.

    The blanket `route == nextseek_query` injection is what made deliberate
    container_cc routing (open-ended analysis, resource creation) read as a
    product failure. Routing is asserted only where it was actually decided:
    the route_gate variants in overlay.json.
    """
    from e2e.catalog import Variant, Turn
    base = Variant(family="f", id="b", name="n", tags=["base"], turns=[Turn(label="m", query="q")])
    ov = Variant(family="nessie_route", id="o", name="n", tags=["overlay"], turns=[Turn(label="m", query="q")])
    assert runner.default_route_criterion(base) is None
    assert runner.default_route_criterion(ov) is None


# ── FIX 2 — tier-gating ───────────────────────────────────────────────────

def test_route_tier_skips_full_tagged_variant(tmp_path):
    # A `full`-tagged (non-route_gate) variant needs execution → skipped in route tier.
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path, variant_id="green.global_count",
        post_query=_post(), get_progress=lambda tid: CC_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "green.global_count")
    assert entry.status == "skipped"


def test_full_tier_drives_route_gate_cc_route_only(tmp_path, monkeypatch):
    # A route_gate CC variant in a FULL run is driven route-only: it passes on
    # route==container_cc without a completed turn AND never calls bundle_reader.
    def boom_bundle(session_id):  # would run only at full depth
        raise AssertionError("bundle_reader must not run for a route_gate case")

    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [_cc_gate()])
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: CC_ROUTED, bundle_reader=boom_bundle,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "gate.cc")
    assert entry.status == "passed" and entry.route == "container_cc"


def test_unsatisfied_requires_env_is_skipped(tmp_path, monkeypatch):
    from e2e.catalog import Variant, Turn
    v = Variant(family="nessie_route", id="needs.env", name="n",
                tags=["nessie", "route_gate", "overlay"],
                requires_env=["NESSIE_DEFINITELY_UNSET_ENV"],
                turns=[Turn(label="m", query="q",
                            pass_criteria=[{"field": "route", "op": "eq", "value": "container_cc"}])])
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [v])
    monkeypatch.delenv("NESSIE_DEFINITELY_UNSET_ENV", raising=False)
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: CC_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "needs.env")
    assert entry.status == "skipped"


# ── FIX 5 — a criteria MISS is a failure, not an error ────────────────────

def test_first_turn_isolates_the_case_later_turns_share_it(tmp_path, monkeypatch):
    """Per-CASE isolation: turn 1 asks for a fresh session, turn 2+ reuse it.

    This is what keeps refine/recall honest — each case starts clean, but the
    follow-up still sees its own seed's results rather than a neighbour's.
    """
    from e2e.catalog import Variant, Turn
    v = Variant(family="refine_and_recall", id="multi.turn", name="n", tags=["overlay"],
                turns=[Turn(label="seed", query="q1"), Turn(label="followup", query="q2")])
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [v])
    bodies = []

    def post_query(body):
        bodies.append(dict(body))
        return {"task_id": "t", "session_id": "sess-A"}

    runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=post_query, get_progress=lambda tid: NS_DONE,
        sleep=lambda s: None, clock=lambda: 0.0)

    assert len(bodies) == 2
    assert bodies[0].get("force_new") is True and "session_id" not in bodies[0]
    assert bodies[1].get("session_id") == "sess-A" and "force_new" not in bodies[1]


def test_known_fail_that_passes_is_reported_as_xpass(tmp_path, monkeypatch):
    from e2e.catalog import Variant, Turn
    v = Variant(family="nessie_repro", id="repro.stale", name="n",
                tags=["nessie", "known_fail", "overlay"],
                turns=[Turn(label="main", query="q",
                            pass_criteria=[{"field": "route", "op": "eq", "value": "nextseek_query"}])])
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [v])
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: NS_DONE,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "repro.stale")
    assert entry.status == "xpass"
    # an xpass is a real signal, not a green: the gate must count it
    assert runner.gate_failed(m) == 1


def test_criteria_miss_marks_failed_with_reasons(tmp_path, monkeypatch):
    # route_gate case asserts route==container_cc but the turn routes nextseek_query.
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [_cc_gate()])
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: NS_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "gate.cc")
    assert entry.status == "failed"
    assert entry.failed_criteria and any("route" in fc for fc in entry.failed_criteria)


# --------------------------------------------------------------------------- #
# T3.1 — xpass must apply to consistency groups too.
#
# runner.py applied xpass inline in the variant loop only. The consistency branch
# emitted plain "passed"/"failed", so cons.nhp_sequencing_engine passed while tagged
# known_fail and was then PRINTED under "expected to fail" — reading as reassurance
# for a case that was actually green.
# --------------------------------------------------------------------------- #


def test_apply_xpass_promotes_a_passing_known_fail():
    assert runner._apply_xpass("passed", True)[0] == "xpass"
    assert "stale" in runner._apply_xpass("passed", True)[1]


def test_apply_xpass_leaves_everything_else_alone():
    assert runner._apply_xpass("passed", False) == ("passed", "")
    assert runner._apply_xpass("failed", True) == ("failed", "")
    assert runner._apply_xpass("failed", False) == ("failed", "")
    assert runner._apply_xpass("error", True) == ("error", "")
    assert runner._apply_xpass("skipped", True) == ("skipped", "")


def test_the_consistency_branch_uses_the_shared_helper(monkeypatch, tmp_path):
    """A known_fail consistency group that passes must be xpass, not passed."""
    from nessie_tests import consistency

    group = {"id": "cons.fake", "tags": ["known_fail"],
             "queries": ["a", "b"], "assert": ["same_route", "same_count"]}
    monkeypatch.setattr("nessie_tests.corpus.load_consistency_groups", lambda p: [group])
    monkeypatch.setattr(
        consistency, "run_group",
        lambda g, drive: type("R", (), {"passed": True, "reasons": [], "observations": []})(),
    )

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path, variant_id="route.ns_advanced",
        post_query=_post(), get_progress=lambda tid: CC_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0, run_consistency=True)

    entry = next(e for e in m.entries if e.id == "cons.fake")
    assert entry.status == "xpass", "a passing known_fail group was recorded as passed"
    assert entry.expected_fail is True
    assert runner.gate_failed(m) >= 1, "an xpass group must count against the gate"


def _manifest(*statuses):
    """statuses: (status, expected_fail) or (status, expected_fail, outage) tuples."""
    from nessie_tests.manifest import NessieManifest, NessieManifestEntry
    return NessieManifest(
        started_at="a", ended_at="b", tier="full", scope="all",
        entries=[NessieManifestEntry(id=f"c{i}", family="f", tier="full",
                                     status=t[0], expected_fail=t[1],
                                     outage=(t[2] if len(t) > 2 else False))
                 for i, t in enumerate(statuses)])


def test_the_summary_and_the_gate_cannot_disagree():
    """The printed 'GATE: PASS' used to contradict SystemExit(1) on an xpass run."""
    m = _manifest(("passed", False), ("xpass", True))

    summary = runner.classify_entries(m)

    assert len(summary["real_fails"]) == runner.gate_failed(m) == 1


def test_an_xpass_is_not_filed_under_expected_failures():
    """The line that read as reassurance: a PASSING known_fail listed as expected."""
    m = _manifest(("xpass", True), ("failed", True))

    summary = runner.classify_entries(m)

    assert [e.status for e in summary["known_failed"]] == ["failed"]
    assert summary["counts"]["xpass"] == 1


def test_a_genuinely_failing_known_fail_is_excluded_from_the_gate():
    m = _manifest(("passed", False), ("failed", True))

    assert runner.classify_entries(m)["real_fails"] == []
    assert runner.gate_failed(m) == 0


def test_errors_on_unexpected_cases_still_count():
    m = _manifest(("error", False), ("error", True))

    assert len(runner.classify_entries(m)["real_fails"]) == 1


# --------------------------------------------------------------------------- #
# B2 — an outage must be scored as infrastructure and must not fail the gate,
# while every OTHER kind of error keeps its gate-failing behaviour.
# --------------------------------------------------------------------------- #

OUTAGE_REPLY = (
    "**The request could not be completed.**\n\nAll provider fallbacks exhausted "
    "� agent 'parser': An error occurred (ServiceUnavailableException) when "
    "calling the Converse operation (reached max retries: 4)."
)

NS_OUTAGE = {"status": "completed", "progress": NS_ROUTED["progress"] + [
    {"event": "query_complete", "data": {"reply": OUTAGE_REPLY, "debug": {}}}]}


def _variant(vid="sys.q", *criteria, turns=1, family="system_question"):
    from e2e.catalog import Variant, Turn
    return Variant(
        family=family, id=vid, name="n", tags=["nessie", "overlay", "full"], requires_env=[],
        turns=[Turn(label=f"t{i}", query=f"q{i}", pass_criteria=list(criteria))
               for i in range(turns)])


def test_an_outaged_case_is_error_and_cannot_fail_the_gate(tmp_path, monkeypatch):
    v = _variant("sys.what_can_nextseek_do",
                 {"field": "parser_plan.mode", "op": "eq", "value": "new_search"})
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [v])

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: NS_OUTAGE,
        sleep=lambda s: None, clock=lambda: 0.0)

    entry = next(e for e in m.entries if e.id == "sys.what_can_nextseek_do")
    assert entry.status == "error", "an outage was scored as an ordinary failure"
    assert entry.outage is True
    assert "provider" in entry.reason.lower()
    assert runner.gate_failed(m) == 0, "a Bedrock outage must not be able to fail the gate"
    assert runner.classify_entries(m)["real_fails"] == []
    assert [e.id for e in runner.classify_entries(m)["outage"]] == ["sys.what_can_nextseek_do"]


def test_an_outage_still_records_what_the_criteria_saw(tmp_path, monkeypatch):
    """Exempt from the gate, not erased: the evidence must survive for triage."""
    v = _variant("sys.q", {"field": "last_reply", "op": "nonempty"})
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [v])

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: NS_OUTAGE,
        sleep=lambda s: None, clock=lambda: 0.0)

    entry = next(e for e in m.entries if e.id == "sys.q")
    # `last_reply nonempty` is SATISFIED by an outage reply — that is precisely why
    # a passing criterion cannot rescue an outaged turn.
    assert entry.status == "error" and entry.outage is True
    assert any(o.field == "last_reply" for o in entry.observations)


def test_an_outage_stops_driving_the_rest_of_the_case(tmp_path, monkeypatch):
    """Turns 2+ against a dead provider are noise and cost money."""
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [_variant("m.t", turns=3)])
    calls = []

    def post_query(body):
        calls.append(body)
        return {"task_id": "t", "session_id": "s"}

    runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=post_query, get_progress=lambda tid: NS_OUTAGE,
        sleep=lambda s: None, clock=lambda: 0.0)

    assert len(calls) == 1, "the case kept driving turns after the provider gave up"


def test_a_non_outage_error_still_fails_the_gate(tmp_path, monkeypatch):
    """The distinction that matters: a TimeoutError is NOT exempt."""
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [_variant("infra.dead")])

    def dead(tid):
        raise TimeoutError("the read timed out")

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=dead,
        sleep=lambda s: None, clock=lambda: 0.0)

    entry = next(e for e in m.entries if e.id == "infra.dead")
    assert entry.status == "error"
    assert entry.outage is False
    assert runner.gate_failed(m) == 1
    assert runner.classify_entries(m)["outage"] == []


def test_an_ordinary_failure_is_untouched_by_the_outage_path(tmp_path, monkeypatch):
    """Non-vacuity: a red without the marker is still a red."""
    v = _variant("sys.red", {"field": "parser_plan.mode", "op": "eq", "value": "graph_query"})
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [v])

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: NS_DONE,
        sleep=lambda s: None, clock=lambda: 0.0)

    entry = next(e for e in m.entries if e.id == "sys.red")
    assert entry.status == "failed" and entry.outage is False
    assert runner.gate_failed(m) == 1


def test_an_outaged_consistency_group_is_error_not_a_count_failure(monkeypatch, tmp_path):
    """The tenth case: a group tagged known_fail whose members both outaged."""
    group = {"id": "cons.nhp_sequencing_engine", "tags": ["known_fail"],
             "queries": ["a", "b"], "assert": {"same_route": True, "same_count": True}}
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [])
    monkeypatch.setattr("nessie_tests.corpus.load_consistency_groups", lambda p: [group])

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: NS_OUTAGE,
        sleep=lambda s: None, clock=lambda: 0.0, run_consistency=True)

    entry = next(e for e in m.entries if e.id == "cons.nhp_sequencing_engine")
    assert entry.status == "error"
    assert entry.outage is True
    assert "could not be resolved" not in entry.reason
    assert not any("could not be resolved" in fc for fc in entry.failed_criteria)
    assert runner.gate_failed(m) == 0


def test_an_outaged_known_fail_is_not_filed_as_a_failure_that_met_expectation():
    """It produced no evidence at all, so "failed as expected" would be a lie."""
    m = _manifest(("error", True, True), ("failed", True, False))

    summary = runner.classify_entries(m)

    assert [e.id for e in summary["known_failed"]] == ["c1"]
    assert [e.id for e in summary["outage"]] == ["c0"]


def test_the_summary_and_the_gate_still_cannot_disagree_about_outages():
    m = _manifest(("error", False, True), ("error", False, False), ("passed", False, False))

    summary = runner.classify_entries(m)

    assert len(summary["real_fails"]) == runner.gate_failed(m) == 1
    assert summary["counts"]["error"] == 2, "the manifest status is still `error` for both"
    assert len(summary["outage"]) == 1


# --------------------------------------------------------------------------- #
# Fix round 1 — a mid-case outage must not erase an earlier turn's genuine red.
#
# v_status was overwritten unconditionally, so a case whose turn 1 really failed
# and whose turn 2 caught a transient 503 went out as status=error + outage=True.
# _is_real_failure short-circuits on that flag, so the case dropped out of
# real_fails, out of gate_failed and out of known_failed: a real regression
# passing the gate, rendered grey. The `break` is right (turns 2+ share a session
# with a turn that never reached the product); the overwrite was not.
# --------------------------------------------------------------------------- #

def test_an_outage_after_a_real_failure_does_not_erase_it(tmp_path, monkeypatch):
    v = _variant("multi.red", {"field": "parser_plan.mode", "op": "eq", "value": "graph_query"},
                 turns=2, family="refine_and_recall")
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [v])
    payloads = iter([NS_DONE, NS_OUTAGE])  # turn 1 genuinely fails, turn 2 outages

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: next(payloads),
        sleep=lambda s: None, clock=lambda: 0.0)

    entry = next(e for e in m.entries if e.id == "multi.red")
    assert entry.status == "failed", "a later outage un-failed an earlier genuine red"
    assert entry.outage is False
    assert entry.failed_criteria, "the turn-1 red must survive in the manifest"
    assert runner.gate_failed(m) == 1, "a real regression escaped the gate behind an outage"
    summary = runner.classify_entries(m)
    assert [e.id for e in summary["real_fails"]] == ["multi.red"]
    assert summary["outage"] == [], "a case that WAS scored is not a case lost to an outage"
    # the outage is still recorded, so triage can see why the case stopped early
    assert "provider outage" in entry.reason and "earlier turn" in entry.reason


def test_an_outage_on_the_first_turn_of_a_multi_turn_case_is_still_exempt(tmp_path, monkeypatch):
    """The other half: with no prior evidence, the exemption is still correct.

    This is the shape the stored seed-6 run actually had — in
    refrec.refine_those_results_to_cd8_de and refrec.refine_to_cd8 the outage
    landed on the `seed` turn, before any independent evidence existed.
    """
    v = _variant("multi.out", {"field": "parser_plan.mode", "op": "eq", "value": "graph_query"},
                 turns=2, family="refine_and_recall")
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [v])
    payloads = iter([NS_OUTAGE, NS_DONE])

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: next(payloads),
        sleep=lambda s: None, clock=lambda: 0.0)

    entry = next(e for e in m.entries if e.id == "multi.out")
    assert entry.status == "error" and entry.outage is True
    assert entry.failed_criteria == []
    assert runner.gate_failed(m) == 0
    assert [e.id for e in runner.classify_entries(m)["outage"]] == ["multi.out"]


def test_an_outage_flag_cannot_hide_a_recorded_criterion_failure():
    """Belt and braces, independent of the runner: the flag is not a free pass.

    Two mechanisms, because this is the exact class of bug the task exists to
    prevent. The runner should never emit this combination now, so any entry
    that carries both is either hand-built or produced by an older run — and in
    both cases the recorded red is the more trustworthy of the two signals.
    """
    from nessie_tests.manifest import NessieManifest, NessieManifestEntry

    m = NessieManifest(
        started_at="a", ended_at="b", tier="full", scope="all",
        entries=[NessieManifestEntry(id="c0", family="f", tier="full", status="error",
                                     outage=True, failed_criteria=["seed:api_ok"])])

    assert runner.gate_failed(m) == 1
    summary = runner.classify_entries(m)
    assert len(summary["real_fails"]) == 1
    assert summary["outage"] == [], "real_fails and outage must stay disjoint"


def test_a_clean_outage_is_still_exempt_with_no_failed_criteria():
    """Non-vacuity for the guard above: the flag still works when it should."""
    m = _manifest(("error", False, True))

    assert runner.gate_failed(m) == 0
    assert len(runner.classify_entries(m)["outage"]) == 1


# --------------------------------------------------------------------------- #
# A sticky route is a DECISION, not a router outage.
#
# `_decide_route` returns source="sticky" when the BAML router says NExtSEEK but
# the previous turn was container_cc. Two harness behaviours combined badly with
# that: `route_source` was assigned per turn inside the turn loop (so an entry
# carried its LAST turn's source), and the `heuristic_routed` bucket was a
# DENYLIST (`!= "baml"`). Together, any multi-turn case whose follow-up went
# sticky was filed as an infrastructure condition and its routing evidence
# thrown away — which is exactly the evidence the next live run exists to
# collect.
#
# It matters particularly because corpus.apply_route_policy attaches its route
# criterion to turns[0], which is always a COLD turn and can never be sticky:
# the bucketed `route_source` was describing a different turn from the one the
# assertion tested.
# --------------------------------------------------------------------------- #

CC_STICKY_DONE = {"status": "completed", "progress": [
    {"event": "route_decided", "data": {"route": "container_cc", "model_class": "opus",
                                        "source": "sticky",
                                        "reasoning": "previous turn was container_cc"}},
    {"event": "query_complete", "data": {"reply": "ok", "debug": {}}}]}

NS_HEURISTIC_DONE = {"status": "completed", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None,
                                        "source": "heuristic", "reasoning": "keyword"}},
    {"event": "query_complete", "data": {"reply": "ok", "debug": {}}}]}


def _two_turn_run(tmp_path, monkeypatch, second_payload, vid="multi.route_src"):
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [_variant(vid, turns=2)])
    payloads = iter([NS_DONE, second_payload])
    return runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: next(payloads),
        sleep=lambda s: None, clock=lambda: 0.0)


def test_a_sticky_followup_is_not_an_infrastructure_condition(tmp_path, monkeypatch):
    m = _two_turn_run(tmp_path, monkeypatch, CC_STICKY_DONE)

    entry = next(e for e in m.entries if e.id == "multi.route_src")
    assert entry.route_sources == ["baml", "sticky"], "the per-turn sequence must be recorded"
    assert entry.route_source == "baml", (
        "route_source must describe turn 0 — the turn apply_route_policy asserts on — "
        "not the last turn")
    assert runner.classify_entries(m)["heuristic_routed"] == [], (
        "a sticky follow-up is a deliberate product decision downstream of a real "
        "BAML call; filing it as 'the router was unavailable' discards the evidence")


def test_a_heuristic_followup_is_still_an_infrastructure_condition(tmp_path, monkeypatch):
    """Non-vacuity: the bucket still catches a turn that fell to the keyword regex."""
    m = _two_turn_run(tmp_path, monkeypatch, NS_HEURISTIC_DONE, vid="multi.heur")

    entry = next(e for e in m.entries if e.id == "multi.heur")
    assert entry.route_sources == ["baml", "heuristic"]
    assert entry.route_source == "baml"
    assert [e.id for e in runner.classify_entries(m)["heuristic_routed"]] == ["multi.heur"], (
        "a turn routed by a keyword regex is not evidence about routing, whichever "
        "turn it was")


def _entry_with(route_source=None, route_sources=None):
    from nessie_tests.manifest import NessieManifestEntry
    kwargs = {} if route_sources is None else {"route_sources": route_sources}
    return NessieManifestEntry(id="c0", family="f", tier="full", status="passed",
                               route_source=route_source, **kwargs)


def _bucketed(entry) -> bool:
    from nessie_tests.manifest import NessieManifest
    m = NessieManifest(started_at="a", ended_at="b", tier="full", scope="all", entries=[entry])
    return bool(runner.classify_entries(m)["heuristic_routed"])


def test_an_unknown_source_is_bucketed_because_the_predicate_is_an_allowlist():
    """The fail-safe direction: a source nobody has vetted is NOT evidence.

    The tempting alternative is to keep a denylist and just extend it —
    `not in {"heuristic", "forced", "pipeline"}`. That silently TRUSTS any
    source added downstream later. This instrument's job is telling the truth
    about the product, so an unrecognised source must read as 'not evidence'
    until someone decides otherwise.
    """
    assert _bucketed(_entry_with("quantum_router", ["baml", "quantum_router"]))
    assert _bucketed(_entry_with("quantum_router"))
    assert "sticky" in runner.ROUTE_DECISION_SOURCES
    assert "baml" in runner.ROUTE_DECISION_SOURCES
    assert "heuristic" not in runner.ROUTE_DECISION_SOURCES


def test_a_manifest_written_before_route_sources_classifies_off_route_source_alone():
    """Old runs must keep the classification they had, in both directions."""
    assert not _bucketed(_entry_with("baml"))
    assert _bucketed(_entry_with("heuristic"))
    assert _bucketed(_entry_with("forced"))
    assert _bucketed(_entry_with("pipeline"))
    # An entry that observed NO route_decided at all was never bucketed and must
    # not start being: it says nothing about the router either way.
    assert not _bucketed(_entry_with(None))


def test_any_turn_outside_the_allowlist_buckets_the_entry():
    """The sequence, not just turn 0, decides the bucket."""
    assert _bucketed(_entry_with("baml", ["baml", "sticky", "heuristic"]))
    assert not _bucketed(_entry_with("baml", ["baml", "sticky", "sticky"]))


# --------------------------------------------------------------------------- #
# C1 part 2 — the vacuity guard.
#
# Skipping the four NS outcome fields on a container_cc turn stops them being
# automatic reds. On its own that is dangerous in the opposite direction: a case
# whose ONLY criteria were skipped would report `passed` while having asserted
# nothing whatsoever about the product. That is corpus drift — the same class
# `xpass` exists to catch — so it gets its own status, and that status counts as
# a real failure.
#
# The status is a CASE-level field, so the predicate is case-level: a case is
# `no_assertions` only when it evaluated ZERO criteria across ALL of its turns.
# A multi-turn case that really asserted something on one turn did assert
# something, and labelling it "no assertions" would be the instrument telling a
# different lie. `test_a_multi_turn_case_that_asserted_something_is_not_vacuous`
# pins that boundary.
# --------------------------------------------------------------------------- #

# A real Container-CC completion: reply + mode + the container's own keys, and
# NO `debug` — which is why all four NS outcome fields are constant-false on it.
CC_DONE_NO_DEBUG = {"status": "completed", "progress": [
    {"event": "route_decided", "data": {"route": "container_cc", "model_class": "opus",
                                        "source": "baml", "reasoning": ""}},
    {"event": "query_complete", "data": {"reply": "I wrote the workbook.", "mode": "cc"}}]}

# An NS turn that genuinely produced an outcome.
NS_ANSWERED = {"status": "completed", "progress": NS_ROUTED["progress"] + [
    {"event": "query_complete",
     "data": {"reply": "408 samples", "debug": {"graph_result": {"count": 408}}}}]}

CC_OUTAGE = {"status": "completed", "progress": [
    {"event": "route_decided", "data": {"route": "container_cc", "model_class": "opus",
                                        "source": "baml", "reasoning": ""}},
    {"event": "query_complete", "data": {"reply": OUTAGE_REPLY, "mode": "cc"}}]}

FLOOR = {"field": "outcome_observed", "op": "true", "value": None}


def _run(tmp_path, monkeypatch, variant, payloads):
    payloads = iter(payloads)
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [variant])
    return runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: next(payloads),
        sleep=lambda s: None, clock=lambda: 0.0)


def test_a_cc_case_whose_every_criterion_was_skipped_is_no_assertions(tmp_path, monkeypatch):
    """The exact shape of `green.refine_recall` under a floored family, routed CC."""
    m = _run(tmp_path, monkeypatch, _variant("green.refine_recall", FLOOR),
             [CC_DONE_NO_DEBUG])

    entry = next(e for e in m.entries if e.id == "green.refine_recall")
    assert entry.status == "no_assertions", (
        "a case that evaluated nothing reported green")
    assert "asserted nothing" in entry.reason
    assert runner.gate_failed(m) == 1, "a case proving nothing must not pass the gate"


def test_the_same_case_routed_to_nextseek_is_still_scored_normally(tmp_path, monkeypatch):
    """Non-vacuity in both directions: the skip is conditional on the route."""
    green = _run(tmp_path, monkeypatch, _variant("ns.answered", FLOOR), [NS_ANSWERED])
    assert next(e for e in green.entries if e.id == "ns.answered").status == "passed"
    assert runner.gate_failed(green) == 0

    red = _run(tmp_path, monkeypatch, _variant("ns.empty", FLOOR), [NS_DONE])
    assert next(e for e in red.entries if e.id == "ns.empty").status == "failed", (
        "an NS turn that produced no outcome must still go red — the four fields "
        "are only skipped on a container_cc turn")
    assert runner.gate_failed(red) == 1


def test_a_multi_turn_case_that_asserted_something_is_not_vacuous(tmp_path, monkeypatch):
    """The case-level boundary, stated as a test.

    Turn 1 routes NS and really evaluates `outcome_observed`; turn 2 routes CC and
    evaluates nothing. The case DID assert something and passed it, so calling it
    `no_assertions` would be false. The vacuous TURN is still visible in the
    observations table, where every one of its rows is marked SKIPPED.
    """
    v = _variant("multi.half_cc", FLOOR, turns=2, family="refine_and_recall")
    m = _run(tmp_path, monkeypatch, v, [NS_ANSWERED, CC_DONE_NO_DEBUG])

    entry = next(e for e in m.entries if e.id == "multi.half_cc")
    assert entry.status == "passed"
    assert runner.gate_failed(m) == 0
    # ...and the skip is recorded, not silently dropped
    assert any("container_cc" in (o.reason or "") for o in entry.observations)


def test_a_genuine_red_on_an_earlier_turn_beats_no_assertions(tmp_path, monkeypatch):
    v = _variant("multi.red_then_cc", FLOOR, turns=2, family="refine_and_recall")
    m = _run(tmp_path, monkeypatch, v, [NS_DONE, CC_DONE_NO_DEBUG])

    entry = next(e for e in m.entries if e.id == "multi.red_then_cc")
    assert entry.status == "failed"
    assert entry.failed_criteria


def test_an_outage_beats_no_assertions(tmp_path, monkeypatch):
    """Both mean "nothing was proved"; the outage says WHY, so it wins and stays exempt."""
    m = _run(tmp_path, monkeypatch, _variant("cc.outaged", FLOOR), [CC_OUTAGE])

    entry = next(e for e in m.entries if e.id == "cc.outaged")
    assert entry.status == "error" and entry.outage is True
    assert runner.gate_failed(m) == 0


def test_an_infrastructure_error_beats_no_assertions(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [_variant("cc.dead", FLOOR)])

    def dead(tid):
        raise TimeoutError("the read timed out")

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=dead, sleep=lambda s: None, clock=lambda: 0.0)

    entry = next(e for e in m.entries if e.id == "cc.dead")
    assert entry.status == "error" and entry.outage is False
    assert runner.gate_failed(m) == 1


def test_a_case_whose_turns_carry_no_criteria_at_all_is_also_vacuous(tmp_path, monkeypatch):
    """Zero criteria and all-skipped criteria are the same claim: nothing was tested."""
    m = _run(tmp_path, monkeypatch, _variant("empty.case", turns=1), [NS_DONE])

    assert next(e for e in m.entries if e.id == "empty.case").status == "no_assertions"


# ── the classification side: one predicate, shared ────────────────────────────

def test_no_assertions_is_a_real_failure_and_the_two_readers_agree():
    m = _manifest(("passed", False), ("no_assertions", False))

    summary = runner.classify_entries(m)

    assert len(summary["real_fails"]) == runner.gate_failed(m) == 1
    assert summary["counts"]["no_assertions"] == 1


def test_a_known_fail_that_asserted_nothing_is_still_a_real_failure():
    """`known_fail` claims the case FAILS. A case proving nothing cannot show that.

    So the tag does not excuse it, exactly as it does not excuse an `xpass`, and
    it is not filed on the reassuring "failed as expected" line either.
    """
    m = _manifest(("no_assertions", True), ("failed", True))

    summary = runner.classify_entries(m)

    assert len(summary["real_fails"]) == runner.gate_failed(m) == 1
    assert [e.status for e in summary["known_failed"]] == ["failed"]


def test_no_assertions_is_never_promoted_to_xpass():
    assert runner._apply_xpass("no_assertions", True) == ("no_assertions", "")
    assert runner._apply_xpass("no_assertions", False) == ("no_assertions", "")


def test_the_gate_and_the_summary_use_the_one_predicate():
    """`_is_real_failure` is the single definition; neither reader re-derives it."""
    from nessie_tests.manifest import NessieManifestEntry

    def entry(**kw):
        return NessieManifestEntry(id="c", family="f", tier="full", **kw)

    assert runner._is_real_failure(entry(status="no_assertions")) is True
    assert runner._is_real_failure(entry(status="no_assertions", expected_fail=True)) is True
    assert runner._is_real_failure(entry(status="passed")) is False


# --------------------------------------------------------------------------- #
# Fix round 1 — two guards the review found.
# --------------------------------------------------------------------------- #

def test_the_outage_exemption_cannot_swallow_a_no_assertions_entry():
    """A `no_assertions` entry ALWAYS has empty `failed_criteria`.

    So `outage and not failed_criteria` exempted it wholesale. Unreachable from
    the runner (the outage branch sets `error` and breaks), but that second guard
    exists precisely for manifests the runner did not write — which is the only
    place the combination can occur.
    """
    from nessie_tests.manifest import NessieManifest, NessieManifestEntry

    m = NessieManifest(
        started_at="a", ended_at="b", tier="full", scope="all",
        entries=[NessieManifestEntry(id="c0", family="f", tier="full",
                                     status="no_assertions", outage=True)])

    assert runner.gate_failed(m) == 1
    assert len(runner.classify_entries(m)["real_fails"]) == 1
    assert runner.classify_entries(m)["outage"] == [], "must stay disjoint from real_fails"


def test_a_genuine_outage_is_still_exempt_after_narrowing_the_guard():
    """Non-vacuity: narrowing the exemption to `error` must not disarm it."""
    m = _manifest(("error", False, True))

    assert runner.gate_failed(m) == 0
    assert len(runner.classify_entries(m)["outage"]) == 1


def test_a_skipped_criterion_is_machine_readable_in_the_manifest(tmp_path, monkeypatch):
    """`skipped` was only ever a prefix on a prose reason string.

    Downstream readers had to string-match `"SKIPPED — "` to find it, and a
    stored manifest could not be re-checked for vacuity the way `outage` can.
    This change makes skipped rows far more common, so the flag becomes a field.
    """
    m = _run(tmp_path, monkeypatch, _variant("cc.vacuous", FLOOR), [CC_DONE_NO_DEBUG])

    entry = next(e for e in m.entries if e.id == "cc.vacuous")
    obs = next(o for o in entry.observations if o.field == "outcome_observed")
    assert obs.skipped is True
    assert obs.passed is True, "a skipped criterion is not a failure either"

    # ...and it survives a write/read round trip
    from nessie_tests.manifest import load_manifest
    reloaded = load_manifest(tmp_path / "manifest.json")
    assert next(o for o in reloaded.entries[0].observations
                if o.field == "outcome_observed").skipped is True


def test_an_evaluated_criterion_is_not_marked_skipped(tmp_path, monkeypatch):
    """Non-vacuity for the flag: it must not default to True."""
    m = _run(tmp_path, monkeypatch, _variant("ns.real", FLOOR), [NS_ANSWERED])

    entry = next(e for e in m.entries if e.id == "ns.real")
    assert all(o.skipped is False for o in entry.observations)


def test_old_manifests_load_without_the_skipped_field(tmp_path):
    from nessie_tests import manifest as M

    p = tmp_path / "manifest.json"
    p.write_text(
        '{"started_at":"a","ended_at":"b","tier":"full","scope":"all","entries":'
        '[{"id":"c0","family":"f","tier":"full","status":"passed","observations":'
        '[{"turn":"main","field":"api_ok","op":"true","passed":true}]}]}',
        encoding="utf-8")

    assert M.load_manifest(p).entries[0].observations[0].skipped is False
