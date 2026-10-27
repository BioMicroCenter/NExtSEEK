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
