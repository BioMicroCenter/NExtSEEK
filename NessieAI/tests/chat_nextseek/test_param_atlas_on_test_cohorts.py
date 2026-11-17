"""The shipped param-atlas rules on the pipeline-scenario test cohorts, as their stories expect.

The cohorts are the definitions `scripts/pipeline_scenarios/make_test_cohorts.py` uploads
(`scripts/pipeline_scenarios/cohorts/<name>.json`). Each D.SEQ row is flattened with its
ancestors, root first and the leaf winning, as `_flatten_lineage` does for resolve_samples. A
rule that asks on one of these cohorts would stop a ruled story that expects a built launch.
"""
import json
import warnings
from pathlib import Path

import pytest

from chat_nextseek.seqera.param_atlas import (
    check_row_column_params,
    check_run_params,
    evaluate_leaf,
    load_param_atlas,
    typed_value,
)

COHORTS = Path(__file__).resolve().parents[3] / "scripts" / "pipeline_scenarios" / "cohorts"


@pytest.fixture(scope="module")
def atlas():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return load_param_atlas()


def _leaves(name: str) -> dict[str, dict]:
    """{leaf Name: flattened metadata} for every D.SEQ row of a test cohort."""
    levels = json.loads((COHORTS / f"{name}.json").read_text())["levels"]
    by_name = {row["Name"]: row for level in levels for row in level["rows"]}
    out = {}
    for row in next(level for level in levels if level["sample_type"] == "D.SEQ")["rows"]:
        chain, cur = [], row
        while cur is not None:
            chain.append(cur)
            cur = by_name.get(cur.get("Parent"))
        merged: dict = {}
        for md in reversed(chain):
            merged.update({k: v for k, v in md.items() if v not in (None, "")})
        out[row["Name"]] = merged
    return out


def _evidence(pipeline, cohort, atlas):
    return {uid: evaluate_leaf(pipeline, md, atlas) for uid, md in _leaves(cohort).items()}


def _run_values(pipeline, cohort, atlas):
    """The run params configure_run would fill, after checking nothing is asked."""
    evidence = _evidence(pipeline, cohort, atlas)
    checked = check_run_params(pipeline, {}, evidence, atlas)
    assert checked["ask_uids"] == [], checked
    out = {}
    for name, spec in atlas["pipelines"][pipeline]["params"].items():
        if spec["target"] != "run_param":
            continue
        values = {v[name]["value"] for v in evidence.values()
                  if v[name]["verdict"] not in ("not_applicable", "absent", "conflict")}
        assert len(values) == 1, (name, evidence)
        out[name] = typed_value(spec, values.pop())
    return out


def test_the_rrbs_cohort_turns_rrbs_on_without_asking(atlas):
    """stories/methylation.json requires rrbs: true on test::methylation; NTC-METH-2 says RRBS
    only in its SequencingType and its library's Protocol."""
    values = _run_values("methylseq", "methylation", atlas)
    assert values == {"rrbs": True, "em_seq": False, "pbat": False, "accel": False, "zymo": False}


def test_the_atac_cohort_gets_read_length_50(atlas):
    """38 bp reads: 50, as nf-core/atacseq 2.1.2 conf/test.config sets for the same test data."""
    assert _run_values("atacseq", "atac", atlas) == {"read_length": 50}


def test_the_chip_cohort_gets_75_and_broad_peaks_with_inputs_abstaining(atlas):
    """H3K27ac is a histone mark (docs/usage.md: broad histone marks); the inputs have no say."""
    assert _run_values("chipseq", "chip", atlas) == {"read_length": 75, "narrow_peak": False}


def test_the_cutandrun_cohort_keeps_the_e_coli_spike_in(atlas):
    """Its Protocol keeps E. coli carry-over DNA as the spike-in: K12-MG1655, the default."""
    assert _run_values("cutandrun", "cutandrun", atlas) == {"spikein_genome": "K12-MG1655"}


def test_the_riboseq_cohort_types_both_libraries_riboseq(atlas):
    """NTC-RIBO-2 is deposited as LibraryStrategy RNA-Seq; its SequencingType, LibraryType and
    Protocol say ribosome profiling, so it is not corrected to rnaseq."""
    evidence = _evidence("riboseq", "riboseq", atlas)
    assert {uid: v["type"]["value"] for uid, v in evidence.items()} == {
        "NTC-RIBO-1": "riboseq", "NTC-RIBO-2": "riboseq"}
    rows = [{"sample": uid, "type": "riboseq", "strandedness": "auto"} for uid in evidence]
    out = check_row_column_params("riboseq", rows, evidence, atlas)
    assert out["ask_uids"] == [] and out["corrections"] == []
