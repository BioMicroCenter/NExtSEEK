"""The chat end-to-end variants in corpus.json are generated from the stories, never hand-edited."""
import json

from NessieAI.paths import NESSIE_CORPUS
from NessieAI.tests.pipeline_scenarios import build_e2e


def test_the_corpus_holds_exactly_the_generated_variants():
    corpus = json.loads(NESSIE_CORPUS.read_text())
    have = [v for v in corpus["families"]["pipeline_launch"]["variants"] if "pipeline_scenarios" in v.get("tags", [])]
    assert have == build_e2e.variants()


def test_every_e2e_story_is_ruled():
    from NessieAI.tests.pipeline_scenarios.stories import story_by_id
    for sid in json.loads(build_e2e.E2E.read_text())["stories"]:
        assert story_by_id(sid).ruled, sid


def test_no_turn_confirms_a_submit_and_the_build_asks_for_no_submit():
    """The chat variants run against a real stack: a turn that reads as a confirmation could
    submit a real job, so the build and push turns say not to submit."""
    from NessieAI.tests.pipeline_scenarios.run import DEFAULT_REPLY
    assert build_e2e.BUILD_ONLY == "Build the run, but do not submit anything yet."
    for v in build_e2e.variants():
        later = [t for t in v["turns"] if t["label"] in ("build", "push_to_build")]
        assert later, v["id"]
        assert all(t["query"] == build_e2e.BUILD_ONLY for t in later), v["id"]
        assert all(DEFAULT_REPLY not in t["query"] and "submit it" not in t["query"].lower()
                   for t in v["turns"]), v["id"]


def test_every_variant_needs_the_opt_in_env_var():
    assert {tuple(v["requires_env"]) for v in build_e2e.variants()} == {("NESSIE_PIPELINE_SCENARIOS_E2E",)}


def test_the_harness_skips_the_variants_without_the_opt_in(monkeypatch):
    from pathlib import Path
    from NessieAI.tests.nessie_tests import corpus, runner
    monkeypatch.delenv("NESSIE_PIPELINE_SCENARIOS_E2E", raising=False)
    ours = [v for v in corpus.merged(Path(NESSIE_CORPUS)) if v.id.startswith("pipeline_scenarios.")]
    assert len(ours) == len(build_e2e.variants())

    def never(*a, **k):
        raise AssertionError("a turn was sent without the opt-in")
    for v in ours:
        entry = runner.run_case(v, tier="full", post_query=never, get_progress=never)
        assert entry.status == "skipped" and "NESSIE_PIPELINE_SCENARIOS_E2E" in entry.reason, v.id


def _push_turn():
    (v,) = [v for v in build_e2e.variants()
            if any(t["label"] == "push_to_build" for t in v["turns"])]
    return next(t for t in v["turns"] if t["label"] == "push_to_build")


def test_the_not_yet_push_turn_fails_when_the_agent_did_not_answer_it():
    """Nothing built and nothing offered is also what a turn the pipeline agent never saw looks like:
    the push turn passes only on the agent's own snapshot of the chat."""
    from NessieAI.tests.e2e.criteria import check_pass
    criteria = _push_turn()["pass_criteria"]
    assert check_pass({}, criteria)[0] is False                         # no snapshot on the turn
    assert check_pass({"pipeline_agent": {}}, criteria)[0] is False     # an empty agent state
    declined = {"pipeline_agent": {"active": True, "pipeline_key": None, "message_count": 4,
                                   "selection": {"verdict": "refused", "pipelines": []}}}
    assert check_pass(declined, criteria)[0] is True
    built = {"pipeline_agent": {**declined["pipeline_agent"], "pipeline_key": "chipseq"}}
    assert check_pass(built, criteria)[0] is False
