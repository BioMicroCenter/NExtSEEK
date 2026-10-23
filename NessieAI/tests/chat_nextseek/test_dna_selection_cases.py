"""The DNA intent-only selection cases: structure, not model behaviour."""
import json
import re

from NessieAI.paths import CHAT_NEXTSEEK_DIR

from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.nfcore_atlas import ATLAS_PATH, load_atlas

CASES_PATH = CHAT_NEXTSEEK_DIR / "evals" / "dna_selection_cases.json"
# The cases are scored before any DNA atlas entry exists, so a key only has to be
# a catalog pipeline (sarek) or an RNA atlas entry.
KNOWN = set(load_atlas(ATLAS_PATH)["pipelines"]) | set(NFCORE_PIPELINE_CATALOG)


def _cases():
    return json.loads(CASES_PATH.read_text())


def test_every_case_has_the_required_shape():
    for case in _cases():
        assert set(case) >= {"id", "question", "acceptable", "must_not"}, case
        assert case["acceptable"] and isinstance(case["acceptable"][0], list), case["id"]
        assert isinstance(case["must_not"], list), case["id"]


def test_no_case_names_a_pipeline_in_its_question():
    # Whole words only: catalog keys such as "hic" and "mag" hide inside ordinary words
    # ("which", "image").
    for case in _cases():
        lowered = case["question"].lower()
        for pipeline in KNOWN:
            assert not re.search(rf"\b{re.escape(pipeline)}\b", lowered), f"{case['id']} names {pipeline}"


def test_every_referenced_pipeline_is_known():
    for case in _cases():
        for answer in case["acceptable"]:
            assert set(answer) <= KNOWN, case["id"]
        assert set(case["must_not"]) <= KNOWN, case["id"]


def test_acceptable_and_must_not_never_overlap():
    for case in _cases():
        acceptable = {p for s in case["acceptable"] for p in s}
        assert not (acceptable & set(case["must_not"])), case["id"]


def test_ids_are_unique():
    ids = [c["id"] for c in _cases()]
    assert len(ids) == len(set(ids))


def test_sarek_has_cases_and_so_do_its_neighbours():
    covered = {p for c in _cases() for s in c["acceptable"] for p in s}
    assert {"sarek", "rnavar", "hlatyping"} <= covered
