"""The intent-only selection cases: structure, not model behaviour."""
import json
from pathlib import Path

from chat_nextseek.seqera.nfcore_atlas import ATLAS_PATH, load_atlas

CASES_PATH = Path(__file__).parent.parent / "evals" / "rna_selection_cases.json"
KNOWN = set(load_atlas(ATLAS_PATH)["pipelines"])


def _cases():
    return json.loads(CASES_PATH.read_text())


def test_every_case_has_the_required_shape():
    for case in _cases():
        assert set(case) >= {"id", "question", "acceptable", "must_not"}, case
        assert isinstance(case["id"], str), case["id"]
        assert isinstance(case["question"], str), case["id"]
        assert isinstance(case["must_not"], list), case["id"]
        assert case["acceptable"], case["id"]
        assert isinstance(case["acceptable"][0], list), case["id"]


def test_no_case_names_a_pipeline_in_its_question():
    """The whole point: the user describes the analysis and never names the tool."""
    for case in _cases():
        lowered = case["question"].lower()
        for pipeline in KNOWN:
            assert pipeline not in lowered, f"{case['id']} names {pipeline}"


def test_every_referenced_pipeline_exists_in_the_atlas():
    for case in _cases():
        for answer_set in case["acceptable"]:
            for pipeline in answer_set:
                assert pipeline in KNOWN, f"{case['id']}: {pipeline}"
        for pipeline in case["must_not"]:
            assert pipeline in KNOWN, f"{case['id']}: {pipeline}"


def test_acceptable_and_must_not_never_overlap():
    for case in _cases():
        acceptable = {p for s in case["acceptable"] for p in s}
        assert not (acceptable & set(case["must_not"])), case["id"]


def test_ids_are_unique():
    ids = [c["id"] for c in _cases()]
    assert len(ids) == len(set(ids))


def test_the_six_rich_pipelines_each_have_at_least_one_case():
    covered = {p for c in _cases() for s in c["acceptable"] for p in s}
    for pipeline in ("rnaseq", "scrnaseq", "smrnaseq", "hlatyping", "rnafusion", "rnasplice"):
        assert pipeline in covered, pipeline
