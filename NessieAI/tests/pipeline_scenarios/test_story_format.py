"""The story files: structure only, no model, no stack."""
import json

import pytest

from NessieAI.tests.pipeline_scenarios import stories as st
from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.nfcore_atlas import load_atlas

ATLAS_KEYS = set(load_atlas()["pipelines"])
CATALOG_KEYS = set(NFCORE_PIPELINE_CATALOG)


def test_the_story_files_load():
    assert st.load_stories(), "no stories"


def test_the_stories_have_no_format_problems():
    assert st.problems(st.load_stories(), ATLAS_KEYS, CATALOG_KEYS) == []


def test_every_file_holds_only_its_own_family():
    for path in sorted(st.STORIES_DIR.glob("*.json")):
        payload = json.loads(path.read_text())
        assert payload["family"] == path.stem, path.name
        assert all(s.get("family", path.stem) == path.stem for s in payload["stories"]), path.name


@pytest.mark.parametrize("bad, message", [
    ({"route": "somewhere"}, "route"),
    ({"acceptable": [["rnaseq"]], "must_not": ["rnaseq"]}, "both acceptable and must_not"),
    ({"acceptable": [["nosuchpipeline"]]}, "not in the atlas"),
    ({"phrasing": "plain", "question": "Run rnaseq on these"}, "names a pipeline"),
    ({"expect": "not_yet", "acceptable": [["rnaseq"]]}, "must have no acceptable"),
    ({"acceptable": []}, "needs acceptable"),
    ({"cohort": "nosuchstudy::A.GEX"}, "unknown cohort"),
    ({"ruling": {"state": "maybe", "date": "2026-10-26", "note": ""}}, "ruling"),
])
def test_problems_catches(bad, message):
    good = {"id": "x", "family": "bulk_rna", "question": "Which genes change between the groups?",
            "cohort": "241219BRY::A.GEX", "route": "nextseek_query", "acceptable": [["rnaseq"]],
            "must_not": ["scrnaseq"], "launch": {"built": True, "must_match": {}},
            "phrasing": "plain", "why": {"route": "r", "pipelines": "p"},
            "ruling": {"state": "proposed", "date": "2026-10-26", "note": ""}}
    story = st.Story(**{**good, **bad})
    found = st.problems([story], ATLAS_KEYS, CATALOG_KEYS)
    assert any(message in p for p in found), found


def test_a_real_cohort_resolves_to_uids():
    assert st.cohort_uids("241219BRY::A.GEX")


def test_an_unknown_cohort_raises():
    with pytest.raises(KeyError):
        st.cohort_uids("nosuchstudy::A.GEX")
