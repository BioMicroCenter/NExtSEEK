"""Fix 4 and 5 (U7): the reply pins for the spelled-out type and the flow/CyTOF count."""
import json
import re
from pathlib import Path

CORPUS = Path(__file__).resolve().parent.parent / "corpus.json"


def _case(cid):
    def walk(o):
        if isinstance(o, dict):
            if o.get("id") == cid:
                return o
            o = list(o.values())
        if isinstance(o, list):
            for v in o:
                r = walk(v)
                if r:
                    return r
    return walk(json.loads(CORPUS.read_text()))


def _passes(cid, reply):
    crits = [c for c in _case(cid)["turns"][0]["pass_criteria"]
             if c["field"] == "last_reply" and c["op"] == "matches_re"]
    return all(re.search(c["value"], reply) for c in crits)


PBMC = "entity.how_many_pbmc_samples_do_we_have"
FLOW = "graph.project_samples_with_flow_or_cytof"


def test_pbmc_accepts_short_and_spelled_out_type():
    assert _passes(PBMC, "1,826 tissue (TIS) samples have the type Peripheral Blood Mononuclear Cells or PBMC.")
    assert _passes(PBMC, "There are 2,906 TIS samples that mention PBMC or peripheral blood mononuclear cells.")


def test_pbmc_rejects_the_pre_fix_answers():
    """R10: 390 (Type 'PBMC' only) and 2,711 (text 'pbmc' only) are the answers before the fix."""
    assert not _passes(PBMC, "390 tissue samples have Type PBMC.")
    assert not _passes(PBMC, "There are 2,711 TIS samples that mention PBMC.")


def test_pbmc_rejects_all_types_total_and_unnamed_type():
    assert not _passes(PBMC, "7,931 records mention it across all sample types.")
    assert not _passes(PBMC, "390 matches.")


def test_flow_accepts_either_count_when_it_says_which():
    assert _passes(FLOW, "8,372 samples have flow cytometry or CyTOF data.")
    assert _passes(FLOW, "I found 10,872 flow and CyTOF data records in the project.")
    assert _passes(FLOW, "8,375 distinct samples have a D.FLOW, D.CYTOF or D.FCS record.")
    # R10: the record count with D.FCS included is the count fix 7 asks for.
    assert _passes(FLOW, "There are 12,244 flow cytometry, FCS and CyTOF data records.")


def test_flow_rejects_a_count_that_does_not_say_which():
    assert not _passes(FLOW, "10,872 samples have flow cytometry or CyTOF data.")
    assert not _passes(FLOW, "12,244 samples have flow cytometry or CyTOF data.")
    assert not _passes(FLOW, "The answer is 10,872.")
    assert not _passes(FLOW, "8,372.")


def test_refine_recall_is_retired_with_a_reason_and_not_active():
    ret = json.loads((CORPUS.parent / "retired.json").read_text())["retired"]
    rec = next(r for r in ret if r["id"] == "green.refine_recall")
    assert rec["status"] == "retired" and rec["retirement"]["reason"]
    assert _case("green.refine_recall") is None or _case("green.refine_recall")["status"] == "retired"


def test_prod_read_2026_10_13_pins():
    assert _passes("cat.people_versus_scientists", "124 people and 216 scientists") is True
    assert not _passes("cat.people_versus_scientists", "130 people and 216 scientists")
    assert _passes("harmon.organ_lung_case_split", "23,485 Lung samples: Lung, lung and LUNG")
    assert not _passes("harmon.organ_lung_case_split", "23,218 Lung and lung samples")
    assert _passes(PBMC, "2,906 tissue samples (TIS) mention PBMC or peripheral blood mononuclear cells.")


def _crit_ok(cid, reply):
    from NessieAI.tests.e2e.criteria import check_pass  # noqa: F401  (same flags as the harness)
    crits = [c for c in _case(cid)["turns"][-1]["pass_criteria"] if c["op"] == "matches_re"]
    return all(re.search(c["value"], reply, flags=re.IGNORECASE) for c in crits)


def test_lung_split_needs_all_three_capitalisations():
    ok = "23,485 samples: Lung 17,592, lung 3,361 and LUNG 2,532."
    assert _crit_ok("harmon.organ_lung_case_split", ok)
    assert not _crit_ok("harmon.organ_lung_case_split", "23,485 samples: lung 3,361, lung 17,592.")
    assert not _crit_ok("harmon.organ_lung_case_split", "23,485 samples: Lung 20,953 and LUNG 2,532.")


def test_novaseq_pin_is_the_space_insensitive_total():
    assert _crit_ok("sr.miseq_to_novaseq", "NovaSeq 6000 total 2,452 across all spellings")
    assert not _crit_ok("sr.miseq_to_novaseq", "NovaSeq 6000 total 1,864")
    assert not _crit_ok("sr.miseq_to_novaseq", "NovaSeq 6000 total 2,285")
