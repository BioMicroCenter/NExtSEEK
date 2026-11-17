"""The shipped param-atlas rules for atacseq, chipseq, cutandrun and methylseq, on realistic leaves.

Each rule is read off the pipeline's pinned files (NessieAI/tests/chat_nextseek/fixtures/nfcore/);
each test's docstring names the file. D.SEQ F_bp and R_bp are the forward and reverse read lengths.
"""
import json
import warnings

import pytest

import chat_nextseek.pipeline.agent_tools as at
from chat_nextseek.seqera.param_atlas import (
    check_run_params,
    evaluate_data_driven_param,
    evaluate_leaf,
    load_param_atlas,
)


@pytest.fixture(scope="module")
def atlas():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return load_param_atlas()


def _spec(atlas, pipeline, param):
    return atlas["pipelines"][pipeline]["params"][param]


# --- read_length: atacseq and chipseq ---------------------------------------------------

@pytest.mark.parametrize("pipeline", ["atacseq", "chipseq"])
@pytest.mark.parametrize("signals, value, verdict", [
    ({"F_bp": "151", "R_bp": "151"}, "150", "corroborated"),
    ({"F_bp": "76"}, "75", "derived_uncorroborated"),
    ({"F_bp": "36"}, None, "absent"),
    ({}, None, "absent"),
])
def test_read_length_rounds_down_to_a_genome_size_key(atlas, pipeline, signals, value, verdict):
    """atacseq 2.1.2 and chipseq 2.1.0 nextflow_schema.json: read_length is an integer enum of
    50, 75, 100, 150, 200, used with the genome to look up or compute the MACS effective genome
    size when macs_gsize is not given; chipseq stops with an error and atacseq skips peak calling
    when neither is known (macs_gsize help_text). No read length on record is asked, not guessed."""
    out = evaluate_data_driven_param(_spec(atlas, pipeline, "read_length"), signals)
    assert (out["value"], out["verdict"]) == (value, verdict)


class _Cfg:
    LOG_DIR = "/tmp"
    TOWER_ENV = {}


def test_configure_run_writes_atacseq_read_length_as_an_allowed_integer(tmp_path):
    """The menu (and the schema) allow the integers 50..200, so the derived "150" must go out as 150."""
    samplesheet = tmp_path / "s.csv"
    samplesheet.write_text("sample,fastq_1,fastq_2,replicate\n")
    state = {"artifacts": {"samplesheet": str(samplesheet), "base_dir": str(tmp_path)},
             "bundle_key": None,
             "data_driven_evidence": {"D.SEQ-1": {"read_length": {
                 "value": "150", "verdict": "corroborated", "evidence": "F_bp=150; R_bp=150"}}}}
    out = json.loads(at.tool_configure_run(_Cfg(), state, {"pipeline_key": "atacseq"}, str(tmp_path)))
    assert out["ok"] is True, out
    assert out["resolved_params"]["read_length"] == 150


# --- chipseq narrow_peak ----------------------------------------------------------------

def test_a_histone_mark_keeps_broad_peaks_and_input_controls_sit_it_out(atlas):
    """chipseq 2.1.0 nextflow_schema.json: MACS3 runs with --broad unless narrow_peak is set;
    docs/usage.md "Samplesheet input" contrasts "narrow transcription factors and broad histone
    marks". The input controls name no mark and have no say."""
    spec = _spec(atlas, "chipseq", "narrow_peak")
    evidence = {
        "IP1": {"narrow_peak": evaluate_data_driven_param(spec, {"Name": "H3K27me3_rep1"})},
        "IP2": {"narrow_peak": evaluate_data_driven_param(spec, {"Name": "H3K27me3_rep2"})},
        "IN1": {"narrow_peak": evaluate_data_driven_param(spec, {"Name": "Input_rep1"})},
    }
    assert evidence["IP1"]["narrow_peak"]["value"] == "false"
    assert evidence["IN1"]["narrow_peak"]["verdict"] == "not_applicable"
    out = check_run_params("chipseq", {}, evidence, atlas)
    assert out["ask_uids"] == [] and out["corrections"] == []


def test_a_target_that_is_not_a_histone_mark_is_asked(atlas):
    """A transcription factor's name (FoxA1, the README's own test IP) votes nothing, so the user
    says narrow or broad: the schema's default is broad."""
    spec = _spec(atlas, "chipseq", "narrow_peak")
    evidence = {"IP1": {"narrow_peak": evaluate_data_driven_param(spec, {"Name": "FoxA1_rep1"})},
                "IN1": {"narrow_peak": evaluate_data_driven_param(spec, {"Name": "IgG_rep1"})}}
    out = check_run_params("chipseq", {}, evidence, atlas)
    assert out["ask_by_param"] == {"narrow_peak": ["IP1"]}


# --- cutandrun spikein_genome -------------------------------------------------------------

@pytest.mark.parametrize("protocol, value, verdict", [
    ("Cells were bound to ConA beads ... 1 ng Drosophila spike-in DNA was added.", "BDGP6", "derived_uncorroborated"),
    ("pA-MNase digestion followed by a yeast spike-in.", "R64-1-1", "derived_uncorroborated"),
    ("CUT&Tag with pAG-Tn5 (EpiCypher).", "K12-MG1655", "defaulted"),
])
def test_cutandrun_spikein_genome_follows_the_protocol(atlas, protocol, value, verdict):
    """cutandrun 3.2.2 docs/usage.md "Reference genomes": the default spike-in genome is E. coli
    (K12-MG1655), the carry-over of the enzyme prep; for added yeast DNA set R64-1-1, for fruit
    fly BDGP6. nextflow_schema.json spikein_genome says the same."""
    out = evaluate_data_driven_param(_spec(atlas, "cutandrun", "spikein_genome"),
                                     {"__protocol_text__": protocol})
    assert (out["value"], out["verdict"]) == (value, verdict)


# --- methylseq library presets ------------------------------------------------------------

@pytest.mark.parametrize("signals, on", [
    ({"ExpressionKit": "NEBNext Enzymatic Methyl-seq Kit"}, {"em_seq"}),
    ({"ExpressionKit": "Ovation RRBS Methyl-Seq", "LibrarySelection": "Reduced Representation"}, {"rrbs"}),
    ({"ExpressionKit": "Accel-NGS Methyl-Seq DNA Library Kit"}, {"accel"}),
    ({"ExpressionKit": "Pico Methyl-Seq Library Prep Kit"}, {"zymo"}),
    ({"Name": "liver_PBAT_rep1"}, {"pbat"}),
    ({"ExpressionKit": "EZ DNA Methylation-Gold", "LibraryStrategy": "Bisulfite-Seq"}, set()),
    # A protocol that only compares itself with RRBS does not turn the RRBS preset on.
    ({"__protocol_text__": "Unlike RRBS, whole-genome bisulfite sequencing covers every CpG."}, set()),
])
def test_methylseq_presets_follow_the_kit(atlas, signals, on):
    """methylseq 4.2.0 nextflow_schema.json "special_library_types": rrbs (MspI-digested
    material; also skips deduplication), em_seq, pbat (aligns complementary strands), accel and
    zymo (the Accel and Zymo kits' trimming presets; zymo also sets non_directional). No kit named
    leaves every preset at its default, off."""
    verdicts = evaluate_leaf("methylseq", signals, atlas)
    switched = {name for name, v in verdicts.items() if v["value"] == "true"}
    assert switched == on, verdicts
    assert all(v["verdict"] in ("corroborated", "derived_uncorroborated", "defaulted") for v in verdicts.values())


def test_configure_run_writes_a_derived_preset_as_a_real_boolean(tmp_path):
    """nextflow_schema.json types em_seq as boolean; nf-schema refuses the string "true"."""
    samplesheet = tmp_path / "s.csv"
    samplesheet.write_text("sample,fastq_1,fastq_2\n")
    state = {"artifacts": {"samplesheet": str(samplesheet), "base_dir": str(tmp_path)},
             "bundle_key": None,
             "data_driven_evidence": {"D.SEQ-1": {
                 "em_seq": {"value": "true", "verdict": "derived_uncorroborated", "evidence": "e"},
                 "rrbs": {"value": "false", "verdict": "defaulted", "evidence": "e"}}}}
    out = json.loads(at.tool_configure_run(_Cfg(), state, {"pipeline_key": "methylseq"}, str(tmp_path)))
    assert out["ok"] is True, out
    assert out["resolved_params"]["em_seq"] is True
    assert out["resolved_params"]["rrbs"] is False
