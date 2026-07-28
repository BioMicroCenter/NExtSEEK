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


def test_run_suite_route_tier_specific(tmp_path):
    # route tier + specific scope → only route_gate cases; injected clients, no live stack
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path, variant_id="route.cc_reingest",
        post_query=_post(), get_progress=lambda tid: CC_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "route.cc_reingest")
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


def test_full_tier_drives_route_gate_cc_route_only(tmp_path):
    # A route_gate CC variant in a FULL run is driven route-only: it passes on
    # route==container_cc without a completed turn AND never calls bundle_reader.
    def boom_bundle(session_id):  # would run only at full depth
        raise AssertionError("bundle_reader must not run for a route_gate case")

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path, variant_id="route.cc_reingest",
        post_query=_post(), get_progress=lambda tid: CC_ROUTED, bundle_reader=boom_bundle,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "route.cc_reingest")
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


def test_criteria_miss_marks_failed_with_reasons(tmp_path):
    # route_gate case asserts route==container_cc but the turn routes nextseek_query.
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path, variant_id="route.cc_reingest",
        post_query=_post(), get_progress=lambda tid: NS_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "route.cc_reingest")
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
        overlay_path=OVERLAY, out_dir=tmp_path, variant_id="route.cc_reingest",
        post_query=_post(), get_progress=lambda tid: CC_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0, run_consistency=True)

    entry = next(e for e in m.entries if e.id == "cons.fake")
    assert entry.status == "xpass", "a passing known_fail group was recorded as passed"
    assert entry.expected_fail is True
    assert runner.gate_failed(m) >= 1, "an xpass group must count against the gate"


def _manifest(*statuses):
    """statuses: (status, expected_fail) pairs."""
    from nessie_tests.manifest import NessieManifest, NessieManifestEntry
    return NessieManifest(
        started_at="a", ended_at="b", tier="full", scope="all",
        entries=[NessieManifestEntry(id=f"c{i}", family="f", tier="full",
                                     status=s, expected_fail=ef)
                 for i, (s, ef) in enumerate(statuses)])


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
