import json
import pathlib

import pytest

from nessie_tests import bayes_manifest as bm
from nessie_tests import bayesian, corpus, runner
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


# --- Task 5 fix round 1: guards on the paid record --------------------------


def _prior_manifest(out_dir, *, fingerprint="unset", ids=("green.mus_ndma",)):
    """A plausible completed paired manifest on disk, without paying for a run."""
    fp = runner.corpus_fingerprint(CORPUS) if fingerprint == "unset" else fingerprint
    m = bm.BayesManifest(
        run_meta={"mode": "bayesian", "corpus_fingerprint": fp},
        pairs=[bm.BayesPair(id=i, family="f", hibayes_subtype=None,
                            ns=_entry(i), cc=_entry(i, cost=0.10)) for i in ids])
    bm.write_bayes_manifest(m, out_dir)
    return m


def test_a_fresh_run_refuses_to_overwrite_a_prior_paired_manifest(tmp_path):
    """The same-schema half of the collision `MANIFEST_NAME` already guards.

    Pairs are written as they complete, so a second non-resume run in the same
    out_dir replaces a 130-pair record with its own first pair and every paid
    result after that point is gone. Reproduced before this guard existed: 130
    pairs on disk became 2.
    """
    _prior_manifest(tmp_path)
    post_query, get_progress, calls = _recording_fakes()
    with pytest.raises(bayesian.PriorRunWouldBeOverwritten):
        bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress, skip_preflight=True)
    assert calls == [], "the guard fired only after spending turns"
    assert len(bm.read_bayes_manifest(tmp_path).pairs) == 1, "the prior record was touched"


def test_the_overwrite_guard_fires_before_the_preflight_spends_a_turn(tmp_path):
    """Ordering is the whole point: a mistyped --out must cost zero turns.

    These fakes would make the preflight raise `ForceRouteRejected`. Getting the
    overwrite error instead proves the guard runs first.
    """
    _prior_manifest(tmp_path)

    def post_query(_body):
        return {"task_id": "t", "session_id": "s"}

    def get_progress(_):
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": "unrelated", "source": "baml"}},
            {"event": "query_complete", "data": {"reply": "r", "session_id": "s"}},
        ]}

    with pytest.raises(bayesian.PriorRunWouldBeOverwritten):
        bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress)


def test_resume_refuses_when_the_corpus_changed_underneath_it(tmp_path):
    """A resume across a corpus edit is not a continuation of the same run.

    `manifest.pairs` is rebuilt from the current selection rather than merged with
    the prior pairs, so an id dropped from selection loses its paid result
    silently. Selection can only change if corpus.json changed, so pinning the
    fingerprint closes the deletion path and the comparability question at once.
    """
    _prior_manifest(tmp_path, fingerprint="sha256:something-else-entirely")
    post_query, get_progress, calls = _recording_fakes()
    with pytest.raises(bayesian.CorpusChanged):
        bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress, skip_preflight=True, resume=True)
    assert calls == []
    assert len(bm.read_bayes_manifest(tmp_path).pairs) == 1


def test_resume_refuses_a_prior_manifest_that_records_no_fingerprint(tmp_path):
    """Unprovable is refused, not assumed safe.

    `preflight` already takes the raising path on its inconclusive case for the
    same reason. `run_paired` always writes the key, so nothing it produced can
    trip this; a manifest without one was hand-edited or truncated, and neither
    is something to resume a paid run onto.
    """
    m = bm.BayesManifest(run_meta={"mode": "bayesian"},
                         pairs=[bm.BayesPair(id="green.mus_ndma", family="f", ns=_entry())])
    bm.write_bayes_manifest(m, tmp_path)
    post_query, get_progress, calls = _recording_fakes()
    with pytest.raises(bayesian.CorpusChanged):
        bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=get_progress, skip_preflight=True, resume=True)
    assert calls == []


def test_a_crash_between_the_arms_of_one_pair_keeps_the_completed_ns_arm(tmp_path):
    """`completed_arms` is keyed on the arm so an interrupted run does not repay
    for the NS half. Writing only once both arms were done made that guarantee
    decorative on the crash path: 3 arms paid for, 2 persisted, and the resume
    repaid the lost one."""
    post_query, get_progress, calls = _recording_fakes()
    real_get = get_progress

    def crashing_get(task_id):
        # calls 1,2 = pair 1's ns,cc; call 3 = pair 2's ns; call 4 = pair 2's cc.
        if len(calls) == 4:
            raise KeyboardInterrupt("operator hit Ctrl-C mid-pair")
        return real_get(task_id)

    with pytest.raises(KeyboardInterrupt):
        bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                            corpus_path=CORPUS, post_query=post_query,
                            get_progress=crashing_get, skip_preflight=True)

    ids = corpus.bayesian_ids(CORPUS)
    m = bm.read_bayes_manifest(tmp_path)
    cut = next(p for p in m.pairs if p.id == ids[1])
    assert cut.ns is not None, "the completed ns arm was not persisted"
    assert cut.cc is None
    assert (ids[1], "ns") in bm.completed_arms(m)
    assert (ids[1], "cc") not in bm.completed_arms(m)

    calls.clear()
    bayesian.run_paired(base_url="http://x", auth_header="", out_dir=tmp_path,
                        corpus_path=CORPUS, post_query=post_query,
                        get_progress=get_progress, skip_preflight=True, resume=True)
    assert calls[0][1] == "cc", "resume repaid the ns arm the crash had already bought"
    assert (ids[1], "ns") not in [(ids[1], a) for _q, a in calls[:1]]


def test_a_truncated_manifest_is_not_silently_treated_as_resumable(tmp_path):
    """Returning None here would mean 'nothing completed' and repay a whole paired
    run — the same silent-repay failure the distinct filename exists to prevent."""
    (tmp_path / bm.MANIFEST_NAME).write_text('{"run_meta": {}, "pairs": [{"id": "a"',
                                             encoding="utf-8")
    with pytest.raises(Exception) as e:
        bm.read_bayes_manifest(tmp_path)
    assert not isinstance(e.value, AssertionError)


def test_an_interrupted_write_leaves_the_previous_manifest_intact(tmp_path, monkeypatch):
    """~260 writes per run, one per arm. A Ctrl-C inside any of them must not
    truncate the record every earlier arm was written into."""
    prior = _prior_manifest(tmp_path, ids=("a", "b", "c"))
    bigger = bm.BayesManifest(run_meta=prior.run_meta,
                              pairs=prior.pairs + [bm.BayesPair(id="d", family="f")])

    real_write_text = pathlib.Path.write_text

    def half_written(self, data, *a, **kw):
        real_write_text(self, data[:len(data) // 2], *a, **kw)
        raise KeyboardInterrupt("Ctrl-C mid-write")

    monkeypatch.setattr(pathlib.Path, "write_text", half_written)
    with pytest.raises(KeyboardInterrupt):
        bm.write_bayes_manifest(bigger, tmp_path)
    monkeypatch.undo()

    assert [p.id for p in bm.read_bayes_manifest(tmp_path).pairs] == ["a", "b", "c"]
