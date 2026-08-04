import json
import pathlib

import pytest

from nessie_tests import bayes_manifest as bm
from nessie_tests import bayesian, corpus
from nessie_tests.manifest import NessieManifest, NessieManifestEntry, write_manifest

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus.json"


def _entry(vid="x.y", status="passed", cost=None):
    return NessieManifestEntry(id=vid, family="f", tier="full", status=status, cost=cost)


def test_a_pair_holds_both_arms():
    p = bm.BayesPair(id="x.y", family="f", hibayes_subtype="Search-Basic",
                     ns=_entry(), cc=_entry())
    assert p.ns.status == "passed" and p.cc.status == "passed"


def test_a_half_finished_pair_is_representable():
    """Pairs are written as they complete so --resume works. A pair whose CC arm
    has not run yet must round-trip rather than fail validation."""
    p = bm.BayesPair(id="x.y", family="f", hibayes_subtype=None, ns=_entry(), cc=None)
    assert bm.BayesPair.model_validate(json.loads(p.model_dump_json())).cc is None


def test_manifest_round_trips_through_disk(tmp_path):
    m = bm.BayesManifest(run_meta={"mode": "bayesian"},
                         pairs=[bm.BayesPair(id="x.y", family="f", hibayes_subtype=None,
                                             ns=_entry(), cc=_entry())])
    bm.write_bayes_manifest(m, tmp_path)
    assert bm.read_bayes_manifest(tmp_path).pairs[0].id == "x.y"


def test_read_returns_none_when_there_is_nothing_to_resume(tmp_path):
    assert bm.read_bayes_manifest(tmp_path) is None


def test_a_normal_run_directory_is_not_mistaken_for_a_resumable_paired_run(tmp_path):
    """A `run_suite` manifest must not read back as a paired one.

    Both models tolerate the other's JSON: pydantic ignores extra keys and both
    `BayesManifest` fields default, so a normal manifest would validate as an
    EMPTY paired manifest rather than raising. `--resume` would then see zero
    completed arms and repay for every arm of a ~150-variant two-engine run, and
    the first per-pair write would overwrite the prior run's record beyond
    recovery. The manifests must therefore not share a filename.
    """
    write_manifest(NessieManifest(started_at="t0", ended_at="t1", tier="full", scope="all"),
                   tmp_path / "manifest.json")
    assert bm.read_bayes_manifest(tmp_path) is None


def test_completed_arms_reports_only_arms_that_actually_ran():
    m = bm.BayesManifest(run_meta={}, pairs=[
        bm.BayesPair(id="a", family="f", hibayes_subtype=None, ns=_entry("a"), cc=_entry("a")),
        bm.BayesPair(id="b", family="f", hibayes_subtype=None, ns=_entry("b"), cc=None),
    ])
    assert bm.completed_arms(m) == {("a", "ns"), ("a", "cc"), ("b", "ns")}


# --- Task 5: the paired orchestrator -----------------------------------------


def _recording_fakes(cost_per_cc=0.10):
    """Records the exact order of (query, force_route) so interleaving is provable."""
    calls = []

    def post_query(body):
        calls.append((body["query"], body.get("force_route")))
        return {"task_id": f"t{len(calls)}", "session_id": f"s{len(calls)}"}

    def get_progress(_):
        arm = calls[-1][1]
        data = {"reply": "ok", "session_id": "s"}
        if arm == "cc":
            data["total_cost_usd"] = cost_per_cc
        return {"status": "completed", "progress": [
            {"event": "route_decided",
             "data": {"route": "container_cc" if arm == "cc" else "nextseek_query",
                      "source": "forced"}},
            {"event": "query_complete", "data": data},
        ]}
    return post_query, get_progress, calls


def test_arms_are_interleaved_per_question_not_run_as_two_passes(tmp_path):
    """Two passes would confound engine with wall-clock time: an outage during one
    pass becomes a fake engine effect. Interleaving is the entire reason the design
    is paired, so it is asserted directly rather than assumed."""
    post_query, get_progress, calls = _recording_fakes()
    bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                        corpus_path=CORPUS, post_query=post_query,
                        get_progress=get_progress, skip_preflight=True)
    arms = [arm for _q, arm in calls]
    assert arms[:4] == ["ns", "cc", "ns", "cc"], arms[:8]


def test_every_selected_variant_produces_a_complete_pair(tmp_path):
    post_query, get_progress, _ = _recording_fakes()
    m = bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress, skip_preflight=True)
    assert [p.id for p in m.pairs] == corpus.bayesian_ids(CORPUS)
    assert all(p.ns is not None and p.cc is not None for p in m.pairs)


def test_run_meta_records_what_makes_two_runs_comparable(tmp_path):
    post_query, get_progress, _ = _recording_fakes()
    m = bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress, skip_preflight=True)
    assert m.run_meta["mode"] == "bayesian"
    assert m.run_meta["arms"] == ["ns", "cc"]
    assert m.run_meta["corpus_fingerprint"]
    assert m.run_meta["selected_ids"] == corpus.bayesian_ids(CORPUS)


def test_budget_ceiling_aborts_rather_than_running_on(tmp_path):
    post_query, get_progress, calls = _recording_fakes(cost_per_cc=1.00)
    with pytest.raises(bayesian.BudgetExceeded):
        bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress, skip_preflight=True, max_usd=2.50)
    assert len(calls) < 2 * len(corpus.bayesian_ids(CORPUS))


def test_budget_treats_an_unobserved_cost_as_unknown_not_zero(tmp_path):
    """NS turns emit no total_cost_usd. Summing None as 0 would understate spend
    and let a run sail past its ceiling; the manifest already distinguishes
    'no cost observed' from 'free' and this must too."""
    assert bayesian._spent([None, 0.5, None, 0.25]) == 0.75


def test_the_manifest_is_written_as_each_pair_completes(tmp_path):
    """Written per pair, not at the end, which is what makes resume possible after
    a crash, a timeout or a Ctrl-C."""
    seen = []

    def post_query(body):
        seen.append(bm.read_bayes_manifest(tmp_path))
        return {"task_id": "t", "session_id": "s"}

    def get_progress(_):
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": "nextseek_query", "source": "forced"}},
            {"event": "query_complete", "data": {"reply": "ok", "session_id": "s"}},
        ]}

    bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                        corpus_path=CORPUS, post_query=post_query,
                        get_progress=get_progress, skip_preflight=True)
    written = [m for m in seen if m is not None]
    assert written, "nothing was written until the run ended"
    assert len(written[-1].pairs) > 1


def test_resume_skips_completed_arms_and_reruns_nothing(tmp_path):
    post_query, get_progress, calls = _recording_fakes()
    bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                        corpus_path=CORPUS, post_query=post_query,
                        get_progress=get_progress, skip_preflight=True)
    first = len(calls)
    calls.clear()
    bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                        corpus_path=CORPUS, post_query=post_query,
                        get_progress=get_progress, skip_preflight=True, resume=True)
    assert first > 0
    assert calls == [], "resume re-ran arms that had already completed"


def test_preflight_runs_by_default_and_aborts_the_run(tmp_path):
    from nessie_tests import preflight

    def post_query(_body):
        return {"task_id": "t", "session_id": "s"}

    def get_progress(_):
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": "unrelated", "source": "baml"}},
            {"event": "query_complete", "data": {"reply": "r", "session_id": "s"}},
        ]}

    with pytest.raises(preflight.ForceRouteRejected):
        bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress)
