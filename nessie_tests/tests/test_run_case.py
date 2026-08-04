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
