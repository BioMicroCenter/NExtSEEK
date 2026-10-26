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


from pathlib import Path

COHORT_DEFS = Path(__file__).resolve().parents[3] / "scripts" / "pipeline_scenarios" / "cohorts"


def test_every_test_cohort_has_a_definition_with_a_parent_chain():
    names = set(json.loads(st.TEST_COHORTS.read_text())["cohorts"])
    defined = {p.stem for p in COHORT_DEFS.glob("*.json")}
    assert names == defined, names ^ defined
    for path in COHORT_DEFS.glob("*.json"):
        d = json.loads(path.read_text())
        known = set()
        for level in d["levels"]:
            for row in level["rows"]:
                if "Parent" in row:
                    assert row["Parent"] in known, (path.name, row["Name"])
                assert row.get("Scientist") == "Nessie Test", (path.name, row["Name"])
            known |= {row["Name"] for row in level["rows"]}
        assert d["levels"][-1]["sample_type"] == "D.SEQ", path.name


def _choosable():
    atlas = load_atlas()["pipelines"]
    return sorted(k for k, e in atlas.items() if e.get("launchable", True) and k in CATALOG_KEYS)


@pytest.mark.parametrize("key", _choosable())
def test_every_choosable_pipeline_has_at_least_four_stories(key):
    n = sum(1 for s in st.load_stories() if any(key in a for a in s.acceptable))
    assert n >= 4, f"{key} has {n} stories"


def test_every_not_yet_pipeline_has_a_story():
    for key in st.NOT_YET_PIPELINES:
        assert any(s.expect == "not_yet" and key in s.why.get("pipelines", "") for s in st.load_stories()), key


def test_there_are_off_topic_stories():
    assert sum(1 for s in st.load_stories() if s.family == "off_topic") >= 4


def test_a_route_only_off_topic_story_needs_no_acceptable():
    story = st.Story(id="ot", family="off_topic", question="How do I upload samples?", cohort="",
                     route="nextseek_query", phrasing="off_topic", why={"route": "site how-to"},
                     ruling={"state": "proposed", "date": "2026-10-26", "note": ""})
    assert st.problems([story], ATLAS_KEYS, CATALOG_KEYS) == []
