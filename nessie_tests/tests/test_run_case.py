"""`run_case` is the per-variant body `run_suite` used to inline.

The extraction is a pure refactor, so these tests assert the boundary rather than
the behaviour: the behaviour is already pinned by tests/test_runner.py, and if any
of those moved, the refactor was not pure.
"""
import pathlib

from nessie_tests import corpus, runner
from nessie_tests.manifest import NessieManifestEntry

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus.json"


def _fakes(route="nextseek_query", reply="ok", cost=None):
    """Minimal endpoint doubles. Mirrors the shape tests/test_runner.py uses."""
    def post_query(body):
        post_query.bodies.append(body)
        return {"task_id": "t1", "session_id": "s1"}
    post_query.bodies = []

    data = {"reply": reply, "session_id": "s1"}
    if cost is not None:
        data["total_cost_usd"] = cost

    def get_progress(_task_id):
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": route, "source": "forced"}},
            {"event": "query_complete", "data": data},
        ]}
    return post_query, get_progress


def _variant(vid="green.mus_ndma"):
    return next(v for v in corpus.merged(CORPUS) if v.id == vid)


def test_run_case_returns_exactly_one_entry():
    post_query, get_progress = _fakes()
    entry = runner.run_case(_variant(), tier="full",
                            post_query=post_query, get_progress=get_progress)
    assert isinstance(entry, NessieManifestEntry)
    assert entry.id == "green.mus_ndma"


def test_run_case_forces_new_on_the_first_turn_only():
    """Isolate the case, but keep its own follow-ups in the session its seed opened."""
    post_query, get_progress = _fakes()
    runner.run_case(_variant("refrec.refine_to_cd8"), tier="full",
                    post_query=post_query, get_progress=get_progress)
    bodies = post_query.bodies
    assert len(bodies) >= 2
    assert bodies[0].get("force_new") is True
    assert all("force_new" not in b for b in bodies[1:])
    assert all(b.get("session_id") == "s1" for b in bodies[1:])


def test_run_case_records_a_requires_env_skip_rather_than_failing():
    v = _variant().model_copy(update={"requires_env": ["NESSIE_DEFINITELY_UNSET"]})
    post_query, get_progress = _fakes()
    entry = runner.run_case(v, tier="full",
                            post_query=post_query, get_progress=get_progress)
    assert entry.status == "skipped"
    assert "requires_env unset" in entry.reason
    assert not post_query.bodies, "a skipped case must not hit the endpoint"


def test_run_case_skips_a_non_gate_case_at_route_tier():
    post_query, get_progress = _fakes()
    entry = runner.run_case(_variant(), tier="route",
                            post_query=post_query, get_progress=get_progress)
    assert entry.status == "skipped"
    assert "skipped at route tier" in entry.reason


def test_route_criteria_are_stripped_when_forcing():
    """Forcing the route makes a route assertion tautological: it tests the harness,
    not the product. Every one of them goes, whatever its origin, including what
    corpus.apply_route_policy injects."""
    v = _variant("route.unrelated")
    post_query, get_progress = _fakes(route="nextseek_query")
    entry = runner.run_case(v, tier="full", force_route="ns", strip_route_criteria=True,
                            post_query=post_query, get_progress=get_progress)
    fields = {o.field for o in entry.observations}
    assert not (fields & runner.STRIPPED_UNDER_FORCING)


def test_route_criteria_survive_when_not_forcing():
    """run_suite must be unaffected. The flag is the only thing that changes this."""
    v = _variant("route.unrelated")
    post_query, get_progress = _fakes(route="unrelated")
    entry = runner.run_case(v, tier="route",
                            post_query=post_query, get_progress=get_progress)
    fields = {o.field for o in entry.observations}
    assert "route" in fields


def test_the_stripped_count_is_recorded_rather_than_silent():
    v = _variant("route.unrelated")
    post_query, get_progress = _fakes()
    entry = runner.run_case(v, tier="full", force_route="ns", strip_route_criteria=True,
                            post_query=post_query, get_progress=get_progress)
    assert "stripped" in entry.reason and "route criteri" in entry.reason


def test_known_fail_does_not_become_xpass_under_forcing():
    """The tag records an expectation about ROUTER-DECIDED NS behaviour. A forced
    arm says nothing about it, so promoting a pass to xpass would claim the
    expected failure had stopped happening on evidence that cannot support it.

    NOTE: this test is VACUOUS as a pin and is kept only as a smoke test. Its
    fixture (`green.mus_ndma`) reds four criteria against the doubles, so the case
    lands on `failed` and `_apply_xpass` is never reached at all -- the assertion
    holds identically with the guard in place and with it deleted. The guard is
    really pinned by `test_a_forced_pass_is_not_promoted_where_an_unforced_one_is`
    below, which swaps in a variant that PASSES against the doubles (`unsup.weather`)
    and runs it down both arms, so the differing status is attributable to the
    guard. Do not delete that one as a near-duplicate of this one; it is the only
    coverage the guard has."""
    v = _variant().model_copy(update={"tags": ["nessie", "full", "known_fail"]})
    post_query, get_progress = _fakes()
    entry = runner.run_case(v, tier="full", force_route="cc", strip_route_criteria=True,
                            post_query=post_query, get_progress=get_progress)
    assert entry.status != "xpass"


def test_a_forced_pass_is_not_promoted_where_an_unforced_one_is():
    """Pins the xpass guard itself. The test above is satisfied by a case that
    fails whatever the guard does (green.mus_ndma reds four criteria against the
    doubles), so it would stay green if the promotion were left in. Here the
    variant, the doubles and the observed route are identical on both arms and
    forcing is the only difference — so the different status is the guard."""
    v = _variant("unsup.weather").model_copy(
        update={"tags": ["nessie", "full", "known_fail"]})
    post_query, get_progress = _fakes(route="unrelated")
    assert runner.run_case(v, tier="full",
                           post_query=post_query, get_progress=get_progress).status == "xpass"
    post_query, get_progress = _fakes(route="unrelated")
    assert runner.run_case(v, tier="full", force_route="ns", strip_route_criteria=True,
                           post_query=post_query,
                           get_progress=get_progress).status == "passed"


# ── the forced-arm skip, from the runner side ────────────────────────────────
#
# `_fakes` emits a `query_complete` with no `debug` key, which IS the real
# container_cc shape, so `route="container_cc"` gives a faithful CC arm and
# `route="nextseek_query"` a faithful arm whose NS pipeline produced nothing.
#
# `green.mus_ndma` carries route + parser_plan.mode + entity_sampletype_codes +
# api_ok + api_result_meta.row_count + last_reply + outcome_observed: one of every
# category this fix has to keep straight.

_NS_INTERNAL = {"parser_plan.mode", "entity_sampletype_codes", "api_ok",
                "api_result_meta.row_count"}


def test_the_forced_cc_arm_skips_ns_pipeline_criteria_instead_of_failing_them():
    """The measurement defect: four NS internals reddened a CC arm that answered."""
    post_query, get_progress = _fakes(route="container_cc", reply="Found 195 MUS samples")
    entry = runner.run_case(_variant(), tier="full", force_route="cc",
                            strip_route_criteria=True,
                            post_query=post_query, get_progress=get_progress)

    by_field = {o.field: o for o in entry.observations}
    for field in _NS_INTERNAL:
        assert by_field[field].skipped is True, f"{field} was still scored"
    assert entry.status == "passed"
    assert entry.failed_criteria == []


def test_the_forced_ns_arm_keeps_every_one_of_them():
    """THE CRUX, at the runner. `run_paired` passes `strip_route_criteria=True` on
    BOTH arms, so this is the arm a flag-keyed strip would have destroyed. Same
    variant, same flag, same doubles — only the route differs."""
    post_query, get_progress = _fakes(route="nextseek_query", reply="Found 195 MUS samples")
    entry = runner.run_case(_variant(), tier="full", force_route="ns",
                            strip_route_criteria=True,
                            post_query=post_query, get_progress=get_progress)

    by_field = {o.field: o for o in entry.observations}
    for field in _NS_INTERNAL:
        assert by_field[field].skipped is False, f"{field} lost its assertion on the NS arm"
    assert entry.status == "failed", "an NS arm whose pipeline emitted nothing must go red"
    assert _NS_INTERNAL <= {c.split(":", 1)[1] for c in entry.failed_criteria}


def test_the_survivors_on_a_forced_cc_arm_are_still_real_assertions():
    """`last_reply` carries the load once the internals are skipped, so it has to
    stay failable — otherwise the CC arm is unfailable, which is the opposite
    defect and would bias the paired comparison toward CC."""
    v = _variant().model_copy(update={"turns": [
        _variant().turns[0].model_copy(update={"pass_criteria": [
            {"field": "api_ok", "op": "true", "value": None},
            {"field": "last_reply", "op": "matches_re", "value": r"\b195\b"}]})]})
    post_query, get_progress = _fakes(route="container_cc", reply="nothing found")
    entry = runner.run_case(v, tier="full", force_route="cc", strip_route_criteria=True,
                            post_query=post_query, get_progress=get_progress)

    assert entry.status == "failed"
    assert entry.failed_criteria == ["main:last_reply"]


def test_the_forced_skip_count_is_recorded_rather_than_silent():
    """Same convention as the stripped-route note, and a SEPARATE number: these
    criteria were kept and scored as skipped, not removed."""
    post_query, get_progress = _fakes(route="container_cc")
    entry = runner.run_case(_variant(), tier="full", force_route="cc",
                            strip_route_criteria=True,
                            post_query=post_query, get_progress=get_progress)

    assert "skipped 4 criteria unsatisfiable on a forced container_cc arm" in entry.reason
    assert "stripped 1 route criterion (forced route)" in entry.reason

    ns_post, ns_get = _fakes(route="nextseek_query")
    ns_entry = runner.run_case(_variant(), tier="full", force_route="ns",
                               strip_route_criteria=True,
                               post_query=ns_post, get_progress=ns_get)
    assert "unsatisfiable" not in ns_entry.reason


def test_a_router_decided_cc_turn_still_fails_them_through_run_case():
    """No forcing, no widening. This is `run_suite`'s path through `run_case`."""
    post_query, get_progress = _fakes(route="container_cc", reply="Found 195 MUS samples")
    entry = runner.run_case(_variant(), tier="full",
                            post_query=post_query, get_progress=get_progress)

    by_field = {o.field: o for o in entry.observations}
    for field in _NS_INTERNAL:
        assert by_field[field].skipped is False, f"{field} was skipped without forcing"
    assert entry.status == "failed"


def test_run_suite_cannot_reach_the_widening(tmp_path, monkeypatch):
    """End-to-end through `run_suite` itself, not through its callee.

    `run_suite` has no `strip_route_criteria` parameter and passes none, so a
    CC-routed case in a router-decided run is scored exactly as it was before this
    fix. Driving the whole function is what makes that a property of `run_suite`
    rather than of the arguments this test chose.
    """
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [_variant()])
    post_query, get_progress = _fakes(route="container_cc", reply="Found 195 MUS samples")
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="specific",
        corpus_path=CORPUS, out_dir=tmp_path,
        post_query=post_query, get_progress=get_progress,
        sleep=lambda s: None, clock=lambda: 0.0)

    entry = next(e for e in m.entries if e.id == "green.mus_ndma")
    assert entry.status == "failed"
    assert _NS_INTERNAL <= {c.split(":", 1)[1] for c in entry.failed_criteria}
    assert "unsatisfiable" not in (entry.reason or "")
    assert "route" in {o.field for o in entry.observations}, (
        "run_suite must not strip route criteria either")


def test_no_bayesian_variant_evaluates_nothing_on_a_forced_cc_arm():
    """The load-bearing corpus property, and the one a future edit can break.

    Every one of the 127 selected variants carries at least one `last_reply`, so
    none becomes `no_assertions` — which `runner._is_real_failure` counts as a
    REAL FAILURE, i.e. the exact false-red this fix exists to remove would come
    straight back through the vacuity guard. 107 of them are left with ONLY
    `last_reply nonempty`, which is a thin pass rather than a real one; 20 keep at
    least one content or artifact assertion.

    Drop the last `last_reply` from a bayesian variant and this goes red before
    the money does, exactly as `test_no_route_gate_variant_is_selected_for_the_
    paid_paired_run` does for the other route to `no_assertions`.
    """
    ids = corpus.bayesian_ids(CORPUS)
    by_id = {v.id: v for v in corpus.merged(CORPUS)}
    assert ids, "fixture drifted: nothing is selected for --bayesian"

    vacuous = []
    for vid in ids:
        post_query, get_progress = _fakes(route="container_cc", reply="Found 195 MUS samples")
        entry = runner.run_case(by_id[vid], tier="full", force_route="cc",
                                strip_route_criteria=True,
                                post_query=post_query, get_progress=get_progress)
        if entry.status == "no_assertions":
            vacuous.append(vid)

    assert vacuous == [], (
        f"these variants evaluate zero criteria on a forced cc arm and would be "
        f"counted as real failures every paid run: {vacuous}. Give each one a "
        f"`last_reply` assertion, or clear its `is_bayesian` flag in corpus.json.")


def test_route_source_is_stripped_under_forcing_like_route_and_engine():
    """Under forcing it is `"forced"` on BOTH arms by construction — which is why
    `ROUTE_DECISION_SOURCES` already refuses to read it as evidence — so asserting
    it tests the harness's own request body exactly as `route` does."""
    assert "route_source" in runner.STRIPPED_UNDER_FORCING

    v = _variant("route.unrelated").model_copy(update={"turns": [
        _variant("route.unrelated").turns[0].model_copy(update={"pass_criteria": [
            {"field": "route_source", "op": "eq", "value": "baml"},
            {"field": "last_reply", "op": "nonempty", "value": None}]})]})

    post_query, get_progress = _fakes(route="container_cc")
    forced = runner.run_case(v, tier="full", force_route="cc", strip_route_criteria=True,
                             post_query=post_query, get_progress=get_progress)
    assert "route_source" not in {o.field for o in forced.observations}

    post_query, get_progress = _fakes(route="container_cc")
    decided = runner.run_case(v, tier="full",
                              post_query=post_query, get_progress=get_progress)
    assert "route_source" in {o.field for o in decided.observations}, (
        "run_suite must be unaffected — the flag is the only thing that changes this"
    )


def test_no_artifact_criterion_left_scored_on_a_cc_arm_is_guaranteed_red():
    """The invariant behind the `api_artifact.*` skip, corpus-wide.

    `api_artifact.<name>` was DELIBERATELY made CC-observable, so the family must
    stay scored. Two sub-assertions still cannot be satisfied by any CC turn —
    `.rows_gte`, because CC indexes artifacts by bare label with no readable path,
    and a real basename on a multi-deliverable turn, because `_publish_artifacts`
    publishes one `artifacts.zip` whose members never reach `query_complete`.

    So: for every bayesian variant, drive the turn with a CC payload that really
    published the single basename it asserts, and require every artifact criterion
    still being SCORED to pass. Anything that cannot is a guaranteed false red,
    which is the whole defect class this fix exists to remove.
    """
    from nessie_tests import evaluate
    from nessie_tests.route_observer import RouteObservation

    obs = RouteObservation("container_cc", None, "forced", "", None, "container_cc")
    by_id = {v.id: v for v in corpus.merged(CORPUS)}
    guaranteed_red = []

    for vid in corpus.bayesian_ids(CORPUS):
        for turn in by_id[vid].turns:
            criteria = [c for c in turn.pass_criteria
                        if runner._criterion_field(c) not in runner.STRIPPED_UNDER_FORCING]
            names = evaluate.asserted_artifact_basenames(criteria)
            if not names:
                continue
            # The most generous turn the product could have produced: it published
            # every basename this turn names. On a real multi-deliverable turn CC
            # would have zipped them, which is exactly what makes those criteria
            # unsatisfiable — and what this asserts has already been skipped.
            payload = {"status": "completed", "progress": [
                {"event": "route_decided",
                 "data": {"route": "container_cc", "source": "forced"}},
                {"event": "query_complete", "data": {
                    "reply": "done", "mode": "cc",
                    "artifacts": [{"artifact_type": "file", "label": n} for n in names]}},
            ]}
            _p, results, _o = evaluate.evaluate_turn(
                payload, criteria, obs, last_reply="done", forced=True)
            guaranteed_red += [
                f"{vid}/{turn.label}:{r['field']}" for r in results
                if r["field"].startswith(evaluate.ARTIFACT_PREFIX)
                and not r.get("skipped") and not r["passed"]]

    assert guaranteed_red == [], (
        f"these artifact criteria are still scored on a forced cc arm but can "
        f"never pass one: {guaranteed_red}. Each is a false red on a correct "
        f"answer — the defect class this fix removes.")
