"""param_atlas: load + validate the curated nf-core parameter atlas."""
import json
import warnings

import pytest

from chat_nextseek.seqera.param_atlas import (
    ATLAS_PATH,
    ParamAtlasError,
    data_driven_params,
    evaluate_data_driven_param,
    load_param_atlas,
    required_signals,
)


def _spec(**over):
    spec = {
        "target": "row_column",
        "scope": "sample",
        "allowed": ["dna", "rna"],
        "derive_from": {"signal": "SequencingType", "map": {"rna": ["RNA"], "dna": ["WES"]}},
        "corroborate_with": [{"signal": "Name", "rna_markers": ["GEX"], "dna_markers": ["WES"]}],
        "ask": {"definition": "d", "on_conflict": "c", "on_absent": "a"},
    }
    spec.update(over)
    return spec


def _atlas(params=None, guidance=None):
    return {
        "guidance": guidance or {"notes": ["x"]},
        "pipelines": {"hlatyping": {"params": params if params is not None else {"seq_type": _spec()}}},
    }


def _write(tmp_path, payload):
    p = tmp_path / "param_atlas.json"
    p.write_text(json.dumps(payload))
    return p


def test_loads_a_minimal_valid_atlas(tmp_path):
    out = load_param_atlas(_write(tmp_path, _atlas()))
    assert out["pipelines"]["hlatyping"]["params"]["seq_type"]["allowed"] == ["dna", "rna"]


def test_raises_when_pipelines_key_is_missing(tmp_path):
    with pytest.raises(ParamAtlasError, match="pipelines"):
        load_param_atlas(_write(tmp_path, {"guidance": {"notes": ["x"]}}))


def test_raises_when_a_param_has_no_target(tmp_path):
    with pytest.raises(ParamAtlasError, match="target"):
        load_param_atlas(_write(tmp_path, _atlas(params={"seq_type": _spec(target=None)})))


def test_raises_when_a_param_has_no_allowed(tmp_path):
    bad = _spec()
    del bad["allowed"]
    with pytest.raises(ParamAtlasError, match="allowed"):
        load_param_atlas(_write(tmp_path, _atlas(params={"seq_type": bad})))


def test_raises_when_a_param_has_no_derive_from_signal(tmp_path):
    with pytest.raises(ParamAtlasError, match="derive_from"):
        load_param_atlas(_write(tmp_path, _atlas(params={"seq_type": _spec(derive_from={"map": {}})})))


def test_raises_on_unknown_target_value(tmp_path):
    with pytest.raises(ParamAtlasError, match="target"):
        load_param_atlas(_write(tmp_path, _atlas(params={"seq_type": _spec(target="samplesheet")})))


def test_warns_when_pipeline_not_in_catalog(tmp_path):
    payload = {"guidance": {"notes": ["x"]},
               "pipelines": {"not_a_pipeline": {"params": {"p": _spec()}}}}
    with pytest.warns(UserWarning, match="not_a_pipeline"):
        load_param_atlas(_write(tmp_path, payload))


def test_required_signals_collects_derive_and_corroborators(tmp_path):
    atlas = load_param_atlas(_write(tmp_path, _atlas()))
    assert required_signals("hlatyping", atlas) == {"SequencingType", "Name"}


def test_required_signals_empty_for_unknown_pipeline(tmp_path):
    atlas = load_param_atlas(_write(tmp_path, _atlas()))
    assert required_signals("rnaseq", atlas) == set()


def test_data_driven_params_returns_specs(tmp_path):
    atlas = load_param_atlas(_write(tmp_path, _atlas()))
    assert "seq_type" in data_driven_params("hlatyping", atlas)
    assert data_driven_params("rnaseq", atlas) == {}


def test_shipped_param_atlas_loads_clean(recwarn):
    out = load_param_atlas()  # the real shipped file
    assert "hlatyping" in out["pipelines"]
    assert not [w for w in recwarn.list if issubclass(w.category, UserWarning)]
    assert ATLAS_PATH.exists()


def test_warns_when_row_column_param_name_not_in_pipeline_columns(tmp_path):
    # hlatyping's samplesheet columns are sample/seq_type (required) + fastq_1/fastq_2/bam
    # (optional) -- "not_a_real_column" is in neither, so this is drift.
    bad = _spec(target="row_column")
    payload = _atlas(params={"not_a_real_column": bad})
    with pytest.warns(UserWarning, match="not_a_real_column"):
        load_param_atlas(_write(tmp_path, payload))


def test_warns_when_run_param_name_not_in_pipeline_params_menu(tmp_path):
    bad = _spec(target="run_param")
    payload = _atlas(params={"not_a_real_run_param": bad})
    with pytest.warns(UserWarning, match="not_a_real_run_param"):
        load_param_atlas(_write(tmp_path, payload))


def test_warns_on_substring_marker_collision_across_allowed_values(tmp_path):
    # The documented cDNA/DNA hazard: "DNA" (dna marker) is a substring of "cDNA"
    # (rna marker), so a DNA sample's name would also vote rna.
    bad = _spec(derive_from={"signal": "SequencingType",
                              "map": {"rna": ["cDNA"], "dna": ["DNA"]}})
    payload = _atlas(params={"seq_type": bad})
    with pytest.warns(UserWarning, match="(?i)substring"):
        load_param_atlas(_write(tmp_path, payload))


import json as _json
from pathlib import Path as _Path

from chat_nextseek.seqera.param_atlas import evaluate_data_driven_param, evaluate_leaf

_SNAP = (_Path(__file__).resolve().parent.parent
         / "evals" / "seqtype_fix" / "snapshot-20260817-150707.json")

#: The snapshot is 493 KB of real production sample metadata pulled from
#: nextseek.mit.edu, so it is deliberately not committed. The two tests that read
#: it still run for anyone who has it locally, and skip cleanly for anyone who
#: does not, rather than failing for a missing file that is not theirs to have.
_needs_snapshot = pytest.mark.skipif(
    not _SNAP.exists(),
    reason=f"needs the uncommitted production snapshot at {_SNAP.parent.name}/{_SNAP.name}",
)

_SEQ_TYPE_SPEC = {
    "target": "row_column", "scope": "sample", "allowed": ["dna", "rna"],
    "derive_from": {"signal": "SequencingType",
                    "map": {"rna": ["RNA", "TCR", "GEX", "TRANSCRIPTOM", "scRNA"],
                            "dna": ["WES", "WGS", "EXOME", "GENOMIC"]}},
    "corroborate_with": [
        {"signal": "Name", "rna_markers": ["GEX", "TCR", "RNA", "cDNA"], "dna_markers": ["WES", "WGS", "EXOME"]},
        {"signal": "File_PrimaryData", "rna_markers": ["GEX", "TCR", "RNA", "cDNA"], "dna_markers": ["WES", "WGS", "EXOME"]},
    ],
    "ask": {"definition": "d", "on_conflict": "c", "on_absent": "a"},
}


@_needs_snapshot
def test_verdict_corroborated_on_a_real_gex_record():
    snap = _json.loads(_SNAP.read_text())
    md = snap["D.SEQ-241114SHA-1"]["metadata"]  # SequencingType 'Single Cell TCR', Name '...-GEX'
    signals = {k: md.get(k, "") for k in ("SequencingType", "Name", "File_PrimaryData")}
    out = evaluate_data_driven_param(_SEQ_TYPE_SPEC, signals)
    assert out["value"] == "rna"
    assert out["verdict"] == "corroborated"


@_needs_snapshot
def test_verdict_every_snapshot_record_is_rna_and_never_conflict():
    snap = _json.loads(_SNAP.read_text())
    for uid, rec in snap.items():
        md = rec["metadata"]
        signals = {k: md.get(k, "") for k in ("SequencingType", "Name", "File_PrimaryData")}
        out = evaluate_data_driven_param(_SEQ_TYPE_SPEC, signals)
        assert out["value"] == "rna", uid
        assert out["verdict"] in ("corroborated", "derived_uncorroborated"), uid


def test_verdict_conflict_when_primary_and_marker_disagree():
    signals = {"SequencingType": "RNA-Seq", "Name": "PATIENT7_WES_L001", "File_PrimaryData": ""}
    out = evaluate_data_driven_param(_SEQ_TYPE_SPEC, signals)
    assert out["verdict"] == "conflict"


def test_verdict_absent_when_no_signal_resolves():
    signals = {"SequencingType": "", "Name": "SAMPLE_01", "File_PrimaryData": "SAMPLE_01_L001_R1.fastq.gz"}
    out = evaluate_data_driven_param(_SEQ_TYPE_SPEC, signals)
    assert out["verdict"] == "absent"
    assert out["value"] is None


def test_verdict_derived_uncorroborated_when_only_primary_votes():
    signals = {"SequencingType": "WES", "Name": "SAMPLE_01", "File_PrimaryData": ""}
    out = evaluate_data_driven_param(_SEQ_TYPE_SPEC, signals)
    assert out["value"] == "dna"
    assert out["verdict"] == "derived_uncorroborated"


def test_verdict_derived_from_corroborator_when_primary_silent():
    signals = {"SequencingType": "", "Name": "PATIENT7_WES_L001", "File_PrimaryData": ""}
    out = evaluate_data_driven_param(_SEQ_TYPE_SPEC, signals)
    assert out["value"] == "dna"
    assert out["verdict"] == "derived_uncorroborated"


def test_evaluate_leaf_uses_the_shipped_atlas():
    signals = {"SequencingType": "Single Cell RNAseq", "Name": "X-GEX", "File_PrimaryData": ""}
    out = evaluate_leaf("hlatyping", signals)
    assert out["seq_type"]["value"] == "rna"
    assert evaluate_leaf("atacseq", signals) == {}   # no atlas entry -> no params


def test_evaluate_leaf_rnaseq_defaults_to_length_correction_without_prep_text():
    """rnaseq carries one data-driven param; silence must land on the standard-library default."""
    signals = {"SequencingType": "RNA-Seq", "Name": "X-GEX", "__protocol_text__": ""}
    out = evaluate_leaf("rnaseq", signals)
    assert out["extra_salmon_quant_args"]["verdict"] == "defaulted"
    assert out["extra_salmon_quant_args"]["value"] == ""


def test_verdict_conflict_value_is_the_primarys_vote():
    signals = {"SequencingType": "RNA-Seq", "Name": "PATIENT7_WES_L001", "File_PrimaryData": ""}
    out = evaluate_data_driven_param(_SEQ_TYPE_SPEC, signals)
    assert out["verdict"] == "conflict"
    assert out["value"] == "rna"


def test_verdict_conflict_with_primary_silent_gives_value_none():
    signals = {"SequencingType": "", "Name": "X_WES", "File_PrimaryData": "Y-GEX_R1.fastq.gz"}
    out = evaluate_data_driven_param(_SEQ_TYPE_SPEC, signals)
    assert out["verdict"] == "conflict"
    assert out["value"] is None


def test_verdict_internally_ambiguous_signal_forces_conflict():
    signals = {"SequencingType": "", "Name": "SAMPLE_WES_GEX_L001", "File_PrimaryData": ""}
    out = evaluate_data_driven_param(_SEQ_TYPE_SPEC, signals)
    assert out["verdict"] == "conflict"


def _spec_default(**over):
    s = {"target": "run_param", "scope": "sample", "allowed": ["x", "y"],
         "on_absent": "use_default", "default": "x",
         "derive_from": {"signal": "Kit", "map": {"x": ["kit-x"], "y": ["kit-y"]}},
         "ask": {"definition": "d", "on_conflict": "c", "on_absent": "a"}}
    s.update(over)
    return s


def test_verdict_defaulted_when_absent_and_use_default():
    out = evaluate_data_driven_param(_spec_default(), {"Kit": ""})
    assert out["verdict"] == "defaulted"
    assert out["value"] == "x"


def test_verdict_still_asks_on_conflict_even_with_use_default():
    out = evaluate_data_driven_param(_spec_default(), {"Kit": "kit-x kit-y"})
    assert out["verdict"] == "conflict"


def test_verdict_absent_stays_absent_without_use_default():
    s = _spec_default(); s.pop("on_absent"); s.pop("default")
    assert evaluate_data_driven_param(s, {"Kit": ""})["verdict"] == "absent"


from chat_nextseek.seqera.param_atlas import (
    check_row_column_params,
    render_param_elicitation,
)


def _evidence(verdict, value="rna"):
    return {"D.SEQ-1": {"seq_type": {"value": value, "verdict": verdict, "evidence": "e"}}}


def test_check_passes_when_row_value_matches_corroborated():
    rows = [{"sample": "D.SEQ-1", "seq_type": "rna"}]
    out = check_row_column_params("hlatyping", rows, _evidence("corroborated"))
    assert out["ask_uids"] == [] and out["corrections"] == []


def test_check_corrects_when_row_value_differs_from_verdict():
    rows = [{"sample": "D.SEQ-1", "seq_type": "dna"}]
    out = check_row_column_params("hlatyping", rows, _evidence("corroborated", "rna"))
    assert out["corrections"] == [("D.SEQ-1", "seq_type", "rna")]


def test_check_asks_on_conflict():
    rows = [{"sample": "D.SEQ-1", "seq_type": "rna"}]
    out = check_row_column_params("hlatyping", rows, _evidence("conflict", None))
    assert out["ask_uids"] == ["D.SEQ-1"]
    assert "seq_type" in out["ask_specs"]


def test_render_elicitation_names_the_param_and_uids():
    specs = {"seq_type": data_driven_params("hlatyping")["seq_type"]}
    evidence = _evidence("conflict", None)
    text = render_param_elicitation(specs, ["D.SEQ-1"], evidence)
    assert "seq_type" in text and "D.SEQ-1" in text


from chat_nextseek.seqera.param_atlas import check_run_params

_RUN_PARAM_SPEC = {
    "target": "run_param", "scope": "cohort", "allowed": ["dna", "rna"],
    "derive_from": {"signal": "SequencingType", "map": {"rna": ["RNA"], "dna": ["WES"]}},
    "ask": {"definition": "d", "on_conflict": "c", "on_absent": "a"},
}


def _run_atlas():
    return {"guidance": {"notes": ["x"]},
            "pipelines": {"hlatyping": {"params": {"seq_type": _RUN_PARAM_SPEC}}}}


def test_check_run_params_asks_on_conflict_verdict():
    evidence = {"D.SEQ-1": {"seq_type": {"value": None, "verdict": "conflict", "evidence": "e"}}}
    out = check_run_params("hlatyping", {}, evidence, atlas=_run_atlas())
    assert out["ask_uids"] == ["D.SEQ-1"]
    assert "seq_type" in out["ask_specs"]
    assert out["corrections"] == []


def test_check_run_params_asks_when_decisive_leaves_disagree():
    # Two leaves, each individually decisive (corroborated), but they vote for
    # different values -> a heterogeneous cohort. Must block, not silently pass.
    evidence = {
        "D.SEQ-1": {"seq_type": {"value": "rna", "verdict": "corroborated", "evidence": "e"}},
        "D.SEQ-2": {"seq_type": {"value": "dna", "verdict": "corroborated", "evidence": "e"}},
    }
    out = check_run_params("hlatyping", {}, evidence, atlas=_run_atlas())
    assert out["ask_uids"] == ["D.SEQ-1", "D.SEQ-2"]
    assert "seq_type" in out["ask_specs"]
    assert out["corrections"] == []


def test_check_run_params_corrects_when_supplied_value_disagrees_with_unanimous_verdict():
    evidence = {
        "D.SEQ-1": {"seq_type": {"value": "rna", "verdict": "corroborated", "evidence": "e"}},
        "D.SEQ-2": {"seq_type": {"value": "rna", "verdict": "derived_uncorroborated", "evidence": "e"}},
    }
    out = check_run_params("hlatyping", {"seq_type": "dna"}, evidence, atlas=_run_atlas())
    assert out["ask_uids"] == []
    assert out["corrections"] == [("*", "seq_type", "rna")]


def test_check_run_params_clean_when_supplied_value_matches_unanimous_verdict():
    evidence = {
        "D.SEQ-1": {"seq_type": {"value": "rna", "verdict": "corroborated", "evidence": "e"}},
        "D.SEQ-2": {"seq_type": {"value": "rna", "verdict": "derived_uncorroborated", "evidence": "e"}},
    }
    out = check_run_params("hlatyping", {"seq_type": "rna"}, evidence, atlas=_run_atlas())
    assert out["ask_uids"] == [] and out["corrections"] == []


def _defaulted_run_atlas():
    return {"guidance": {"notes": ["x"]},
            "pipelines": {"hlatyping": {"params": {"seq_type": _spec_default()}}}}


def test_check_run_params_corrects_when_supplied_value_disagrees_with_unanimous_defaulted_verdict():
    evidence = {"D.SEQ-1": {"seq_type": {"value": "x", "verdict": "defaulted", "evidence": "e"}}}
    out = check_run_params("hlatyping", {"seq_type": "y"}, evidence, atlas=_defaulted_run_atlas())
    assert out["ask_uids"] == []
    assert out["corrections"] == [("*", "seq_type", "x")]


def test_check_run_params_clean_when_supplied_value_matches_unanimous_defaulted_verdict():
    evidence = {"D.SEQ-1": {"seq_type": {"value": "x", "verdict": "defaulted", "evidence": "e"}}}
    out = check_run_params("hlatyping", {"seq_type": "x"}, evidence, atlas=_defaulted_run_atlas())
    assert out["ask_uids"] == [] and out["corrections"] == []


def test_check_run_params_no_ask_when_two_leaves_agree_on_defaulted_verdict():
    evidence = {
        "D.SEQ-1": {"seq_type": {"value": "x", "verdict": "defaulted", "evidence": "e"}},
        "D.SEQ-2": {"seq_type": {"value": "x", "verdict": "defaulted", "evidence": "e"}},
    }
    out = check_run_params("hlatyping", {}, evidence, atlas=_defaulted_run_atlas())
    assert out["ask_uids"] == [] and out["corrections"] == []


def test_render_elicitation_on_cross_leaf_disagreement_is_non_empty_and_names_uids():
    evidence = {
        "D.SEQ-1": {"seq_type": {"value": "rna", "verdict": "corroborated", "evidence": "e"}},
        "D.SEQ-2": {"seq_type": {"value": "dna", "verdict": "corroborated", "evidence": "e"}},
    }
    out = check_run_params("hlatyping", {}, evidence, atlas=_run_atlas())
    text = render_param_elicitation(out["ask_specs"], out["ask_uids"], evidence)
    assert text.strip() != ""
    assert "D.SEQ-1" in text and "D.SEQ-2" in text


def test_use_default_without_default_raises(tmp_path):
    bad = _spec()  # existing helper: target row_column, allowed dna/rna
    bad["on_absent"] = "use_default"
    with pytest.raises(ParamAtlasError, match="use_default"):
        load_param_atlas(_write(tmp_path, _atlas(params={"seq_type": bad})))


def test_scrnaseq_protocol_derives_10xv3_from_protocol_text():
    spec = data_driven_params("scrnaseq")["protocol"]
    out = evaluate_data_driven_param(spec, {"ExpressionKit": "", "__protocol_text__": "Chromium Single Cell 3' v3"})
    assert out["value"] == "10XV3"


def test_scrnaseq_protocol_asks_when_family_only():
    spec = data_driven_params("scrnaseq")["protocol"]
    out = evaluate_data_driven_param(spec, {"ExpressionKit": "10x Genomics", "__protocol_text__": ""})
    assert out["verdict"] == "absent"          # family known, version not -> ask


def test_smrnaseq_adapter_defaults_to_illumina_when_absent():
    spec = data_driven_params("smrnaseq")["three_prime_adapter"]
    out = evaluate_data_driven_param(spec, {"ExpressionKit": "", "__protocol_text__": ""})
    assert out["verdict"] == "defaulted"
    assert out["value"] == "AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"


def test_smrnaseq_adapter_overrides_for_qiaseq():
    spec = data_driven_params("smrnaseq")["three_prime_adapter"]
    out = evaluate_data_driven_param(spec, {"ExpressionKit": "QIAseq miRNA", "__protocol_text__": ""})
    assert out["value"] == "AACTGTAGGCACCATCAAT"


def test_shipped_param_atlas_still_loads_clean_with_new_entries(recwarn):
    load_param_atlas()
    assert not [w for w in recwarn.list if issubclass(w.category, UserWarning)]


def test_rnaseq_length_correction_derives_from_quantseq_protocol_text():
    spec = data_driven_params("rnaseq")["extra_salmon_quant_args"]
    out = evaluate_data_driven_param(spec, {"__protocol_text__": "Lexogen QuantSeq 3' mRNA-Seq FWD"})
    assert out["value"] == "--noLengthCorrection"


def test_rnaseq_length_correction_derives_from_three_prime_prep_prose():
    """The 250605SHO shape: the 3' fact lives only in the prep text, no structured field."""
    spec = data_driven_params("rnaseq")["extra_salmon_quant_args"]
    out = evaluate_data_driven_param(
        spec, {"__protocol_text__": "oligo(dT)-primed UMI-based 3' RNA-Seq library"})
    assert out["value"] == "--noLengthCorrection"


def test_rnaseq_length_correction_defaults_off_for_standard_polya_library():
    spec = data_driven_params("rnaseq")["extra_salmon_quant_args"]
    out = evaluate_data_driven_param(
        spec, {"__protocol_text__": "TruSeq Stranded mRNA, poly-A selected", "ExpressionKit": ""})
    assert out["verdict"] == "defaulted"
    assert out["value"] == ""


def test_rnaseq_length_correction_is_in_the_curated_param_menu():
    """A run_param the atlas targets must exist in the menu, or configure_run cannot emit it."""
    from chat_nextseek.seqera.pipeline_params import load_pipeline_context
    menu = load_pipeline_context("rnaseq")["params"]
    assert "extra_salmon_quant_args" in menu
    assert menu["extra_salmon_quant_args"]["default"] == ""


# --- detect_only params, and a defaulted verdict is not evidence (Task 8) ---

from chat_nextseek.seqera.param_atlas import (  # noqa: E402
    check_row_column_params, check_run_params, render_param_elicitation,
)


def _detect_spec(**over):
    spec = {"target": "run_param", "scope": "run", "detect_only": True,
            "detect": {"signal": "LibraryStrategy", "markers": ["WXS", "exome"]},
            "corroborate_with": [{"signal": "SequencingType", "markers": ["capture"]}],
            "ask": {"definition": "the kit BED", "on_detect": "Which BED file?"}}
    spec.update(over)
    return spec


def test_detect_only_hit_needs_a_value():
    v = evaluate_data_driven_param(_detect_spec(), {"LibraryStrategy": "WXS"})
    assert v["verdict"] == "needs_value" and v["value"] is None


def test_detect_only_miss_is_not_detected():
    v = evaluate_data_driven_param(_detect_spec(), {"LibraryStrategy": "WGS"})
    assert v["verdict"] == "not_detected"


def test_detect_only_corroborator_alone_still_detects():
    v = evaluate_data_driven_param(_detect_spec(), {"SequencingType": "Hybrid capture"})
    assert v["verdict"] == "needs_value"


def test_detect_only_loads_without_allowed_or_derive_from(tmp_path):
    path = _write(tmp_path, _atlas(params={"intervals": _detect_spec()}))
    with pytest.warns(UserWarning, match="intervals"):  # hlatyping's menu has no `intervals`
        atlas = load_param_atlas(path)
    assert atlas["pipelines"]["hlatyping"]["params"]["intervals"]["detect_only"]


def test_detect_only_must_target_run_param(tmp_path):
    path = _write(tmp_path, _atlas(params={"intervals": _detect_spec(target="row_column")}))
    with pytest.raises(ParamAtlasError, match="run_param"):
        load_param_atlas(path)


def test_detect_only_needs_markers(tmp_path):
    path = _write(tmp_path, _atlas(params={"intervals": _detect_spec(detect={"signal": "X", "markers": []})}))
    with pytest.raises(ParamAtlasError, match="markers"):
        load_param_atlas(path)


def test_required_signals_include_the_detect_signal():
    atlas = _atlas(params={"intervals": _detect_spec()})
    assert {"LibraryStrategy", "SequencingType"} <= required_signals("hlatyping", atlas)


def test_run_without_the_value_is_asked_and_with_it_is_not():
    atlas = _atlas(params={"intervals": _detect_spec()})
    evidence = {"D.SEQ-1-PUB": {"intervals": {"verdict": "needs_value", "value": None}}}
    asked = check_run_params("hlatyping", {}, evidence, atlas)
    assert asked["ask_uids"] == ["D.SEQ-1-PUB"] and "intervals" in asked["ask_specs"]
    assert "Which BED file?" in render_param_elicitation(asked["ask_specs"], asked["ask_uids"], evidence)
    given = check_run_params("hlatyping", {"intervals": "/net/lab/kit.bed"}, evidence, atlas)
    assert given["ask_uids"] == [] and given["corrections"] == []


def test_a_defaulted_row_value_is_never_corrected():
    spec = _spec(on_absent="use_default", default="dna")
    atlas = _atlas(params={"seq_type": spec})
    evidence = {"S1": {"seq_type": {"verdict": "defaulted", "value": "dna"}}}
    out = check_row_column_params("hlatyping", [{"sample": "S1", "seq_type": "rna"}], evidence, atlas)
    assert out["corrections"] == [] and out["ask_uids"] == []


# --- review fixes: ambiguous_markers, corroborator validation, shipped sarek entries ---

def _bad_detect_atlas(tmp_path, spec):
    return _write(tmp_path, _atlas(params={"intervals": spec}))


def test_detect_only_needs_on_detect(tmp_path):
    spec = _detect_spec(ask={"definition": "the kit BED"})
    with pytest.raises(ParamAtlasError, match="on_detect"):
        load_param_atlas(_bad_detect_atlas(tmp_path, spec))


@pytest.mark.parametrize("corr", [
    [{"markers": ["capture"]}],
    [{"signal": "SequencingType"}],
    [{"signal": "SequencingType", "markers": []}],
])
def test_detect_only_corroborators_need_signal_and_markers(tmp_path, corr):
    with pytest.raises(ParamAtlasError, match="corroborate_with"):
        load_param_atlas(_bad_detect_atlas(tmp_path, _detect_spec(corroborate_with=corr)))


def _ambig_spec(**over):
    return _spec(ambiguous_markers=["non-tumor", "abnormal"], **over)


def test_ambiguous_marker_makes_the_verdict_a_conflict():
    spec = _ambig_spec()
    spec["derive_from"]["map"] = {"rna": ["RNA"], "dna": ["WES", "tumor"]}
    out = evaluate_data_driven_param(spec, {"SequencingType": "Non-tumor WES"})
    assert out["verdict"] == "conflict"


def test_ambiguous_marker_in_a_corroborator_also_conflicts():
    out = evaluate_data_driven_param(_ambig_spec(), {"SequencingType": "WES", "Name": "abnormal WES"})
    assert out["verdict"] == "conflict"


def test_without_ambiguous_markers_behaviour_is_unchanged():
    out = evaluate_data_driven_param(_spec(), {"SequencingType": "WES", "Name": "x-WES"})
    assert out["verdict"] == "corroborated" and out["value"] == "dna"


@pytest.mark.parametrize("bad", [[], "abnormal", [""], [1]])
def test_ambiguous_markers_must_be_a_non_empty_list_of_strings(tmp_path, bad):
    path = _write(tmp_path, _atlas(params={"seq_type": _spec(ambiguous_markers=bad)}))
    with pytest.raises(ParamAtlasError, match="ambiguous_markers"):
        load_param_atlas(path)


def _sarek(name):
    return data_driven_params("sarek")[name]


@pytest.mark.parametrize("signals,verdict,value", [
    ({"Type": "Primary tumor"}, "derived_uncorroborated", "1"),
    ({"Type": "Blood"}, "derived_uncorroborated", "0"),
    ({"Type": "Normal-adjacent tissue"}, "conflict", None),
    ({"Type": "Non-tumor tissue"}, "conflict", None),
    ({"Type": "Non-neoplastic tissue"}, "conflict", None),
    ({"Type": "tumor-free margin"}, "conflict", None),
    ({"Type": "non tumor tissue"}, "conflict", None),
    ({}, "defaulted", "0"),
])
def test_shipped_sarek_status(signals, verdict, value):
    out = evaluate_data_driven_param(_sarek("status"), signals)
    assert out["verdict"] == verdict
    if verdict != "conflict":
        assert out["value"] == value


@pytest.mark.parametrize("strategy,verdict", [
    ("WXS", "needs_value"), ("Targeted Capture", "needs_value"), ("WGS", "not_detected"),
])
def test_shipped_sarek_intervals(strategy, verdict):
    out = evaluate_data_driven_param(_sarek("intervals"), {"LibraryStrategy": strategy})
    assert out["verdict"] == verdict


# --- confirm_in: a row_column spec whose unclear verdicts the pairing table confirms ---------

def test_confirm_in_pairing_table_loads_on_a_row_column_spec(tmp_path):
    path = _write(tmp_path, _atlas(params={"seq_type": _spec(confirm_in="pairing_table")}))
    assert load_param_atlas(path)["pipelines"]["hlatyping"]["params"]["seq_type"]["confirm_in"] == "pairing_table"


def test_confirm_in_must_be_on_a_row_column_spec(tmp_path):
    path = _write(tmp_path, _atlas(params={"seq_type": _spec(target="run_param", confirm_in="pairing_table")}))
    with pytest.raises(ParamAtlasError, match="confirm_in"):
        load_param_atlas(path)


def test_confirm_in_takes_only_pairing_table(tmp_path):
    path = _write(tmp_path, _atlas(params={"seq_type": _spec(confirm_in="chat")}))
    with pytest.raises(ParamAtlasError, match="confirm_in"):
        load_param_atlas(path)


def _confirm_atlas():
    return _atlas(params={"seq_type": _spec(confirm_in="pairing_table")})


def test_a_confirm_in_spec_does_not_block_on_conflict_or_absent():
    rows = [{"sample": "D.SEQ-1", "seq_type": "rna"}, {"sample": "D.SEQ-2", "seq_type": "rna"}]
    evidence = {"D.SEQ-1": {"seq_type": {"value": None, "verdict": "conflict", "evidence": "e"}},
                "D.SEQ-2": {"seq_type": {"value": None, "verdict": "absent", "evidence": "e"}}}
    out = check_row_column_params("hlatyping", rows, evidence, atlas=_confirm_atlas())
    assert out["ask_uids"] == [] and out["ask_specs"] == {}


def test_a_confirm_in_spec_still_corrects_a_decisive_verdict():
    rows = [{"sample": "D.SEQ-1", "seq_type": "dna"}]
    out = check_row_column_params("hlatyping", rows, _evidence("corroborated", "rna"), atlas=_confirm_atlas())
    assert out["corrections"] == [("D.SEQ-1", "seq_type", "rna")]


def test_shipped_sarek_status_is_confirmed_in_the_pairing_table():
    assert _sarek("status")["confirm_in"] == "pairing_table"


def test_a_sarek_conflict_does_not_block_the_samplesheet():
    rows = [{"patient": "P1", "sample": "N1", "status": "0"}]
    evidence = {"N1": {"status": {"value": None, "verdict": "conflict", "evidence": "ambiguous in Type"}}}
    assert check_row_column_params("sarek", rows, evidence)["ask_uids"] == []
