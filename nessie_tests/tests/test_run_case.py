"""`run_case` is the per-variant body `run_suite` used to inline.

The extraction is a pure refactor, so these tests assert the boundary rather than
the behaviour: the behaviour is already pinned by tests/test_runner.py, and if any
of those moved, the refactor was not pure.
"""
import pathlib

from nessie_tests import corpus, runner
from nessie_tests.manifest import NessieManifestEntry

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus.json"
OVERLAY = pathlib.Path(__file__).resolve().parents[1] / "overlay.json"

# merged() is mid-migration from overlay.json to the unified corpus.json on a
# parallel branch. Both resolve to the same 283 variants, so either is a valid
# fixture source; this collapses to corpus.merged() once that lands.
_UNIFIED = hasattr(corpus, "load_unified")


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
    return next(v for v in corpus.merged(CORPUS if _UNIFIED else OVERLAY) if v.id == vid)


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
    expected failure had stopped happening on evidence that cannot support it."""
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
