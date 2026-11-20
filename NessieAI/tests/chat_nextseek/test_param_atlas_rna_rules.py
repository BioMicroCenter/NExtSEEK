"""The shipped param-atlas rules for rnafusion, rnasplice, riboseq and rnavar, on realistic leaves.

Each rule is read off the pipeline's pinned files (NessieAI/tests/chat_nextseek/fixtures/nfcore/):
the docstring of each test names the file. The D.SEQ Strandedness field holds forward, reverse or
unstranded, written by reingest from an earlier nf-core/rnaseq run (NessieAI/ns/reingest/derived.py).
"""
import json
import warnings

import pytest

import chat_nextseek.pipeline.agent_tools as at
from chat_nextseek.seqera.param_atlas import (
    check_row_column_params,
    evaluate_data_driven_param,
    load_param_atlas,
)


@pytest.fixture(scope="module")
def atlas():
    with warnings.catch_warnings():
        warnings.simplefilter("error")   # no drift or collision warning from the shipped file
        return load_param_atlas()


def _spec(atlas, pipeline, param):
    return atlas["pipelines"][pipeline]["params"][param]


# --- strandedness: rnafusion and rnasplice ask, riboseq infers --------------------------

@pytest.mark.parametrize("pipeline", ["rnafusion", "rnasplice", "riboseq"])
@pytest.mark.parametrize("measured", ["forward", "reverse", "unstranded"])
def test_a_measured_strandedness_sets_the_column(atlas, pipeline, measured):
    out = evaluate_data_driven_param(_spec(atlas, pipeline, "strandedness"), {"Strandedness": measured})
    assert out["value"] == measured and out["verdict"] == "derived_uncorroborated"


def test_rnasplice_asks_when_nothing_measured_strandedness(atlas):
    """rnasplice 2.0.0 assets/schema_input.json (the fastq source's): strandedness is required
    and its enum is forward, reverse, unstranded; docs/usage.md "Samplesheet input" says the
    same. There is no auto, so a row the agent filled with auto is asked about, never sent."""
    from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
    from NessieAI.paths import NESSIE_ROOT
    spec = _spec(atlas, "rnasplice", "strandedness")
    fixture = NESSIE_ROOT / "tests/chat_nextseek/fixtures/nfcore/rnasplice.schema_input.json"
    pinned = json.loads(fixture.read_text())["items"]["properties"]["strandedness"]["enum"]
    assert sorted(spec["allowed"]) == sorted(pinned)
    revision = NFCORE_PIPELINE_CATALOG["rnasplice"]["default_revision"]
    assert f"rnasplice {revision}" in spec["ask"]["definition"]
    assert "auto" not in spec["allowed"]
    verdict = evaluate_data_driven_param(spec, {})
    assert verdict["verdict"] == "absent"
    rows = [{"sample": "D.SEQ-1", "strandedness": "auto", "condition": "a"}]
    out = check_row_column_params("rnasplice", rows, {"D.SEQ-1": {"strandedness": verdict}}, atlas)
    assert out["ask_by_param"] == {"strandedness": ["D.SEQ-1"]}


def test_rnasplice_corrects_a_row_that_contradicts_the_measured_strandedness(atlas):
    verdict = evaluate_data_driven_param(_spec(atlas, "rnasplice", "strandedness"), {"Strandedness": "reverse"})
    rows = [{"sample": "D.SEQ-1", "strandedness": "forward", "condition": "a"}]
    out = check_row_column_params("rnasplice", rows, {"D.SEQ-1": {"strandedness": verdict}}, atlas)
    assert out["corrections"] == [("D.SEQ-1", "strandedness", "reverse")]


def test_rnafusion_offers_every_value_its_schema_allows(atlas):
    """rnafusion 4.1.3 assets/schema_input.json: strandedness is required, enum forward, reverse,
    unstranded, unknown. With nothing measured the user is asked, and may say unknown."""
    spec = _spec(atlas, "rnafusion", "strandedness")
    assert spec["allowed"] == ["forward", "reverse", "unstranded", "unknown"]
    assert evaluate_data_driven_param(spec, {})["verdict"] == "absent"


def test_riboseq_infers_strandedness_when_nothing_measured_it(atlas):
    """riboseq 2.0.0 docs/usage.md "Strandedness Prediction": auto sub-samples a million reads and
    infers strandedness with Salmon; assets/schema_input.json allows auto."""
    out = evaluate_data_driven_param(_spec(atlas, "riboseq", "strandedness"), {})
    assert out == {"value": "auto", "verdict": "defaulted",
                   "evidence": "no signal; used the safe default 'auto'"}


# --- riboseq type ---------------------------------------------------------------------

@pytest.mark.parametrize("signals, value, verdict", [
    ({"SequencingType": "Ribo-seq", "Name": "HeLa_footprint_rep1"}, "riboseq", "corroborated"),
    ({"SequencingType": "RNA-seq", "Name": "HeLa_totalRNA_rnaseq_rep1"}, "rnaseq", "corroborated"),
    ({"SequencingType": "TI-seq"}, "tiseq", "derived_uncorroborated"),
    ({"SequencingType": "RNA-seq", "Name": "HeLa_footprint_rep1"}, None, "conflict"),
    ({"LibraryStrategy": "OTHER", "Name": "HeLa_rep1"}, None, "absent"),
    # Footprint libraries are often deposited as LibraryStrategy RNA-Seq: that is no vote.
    ({"LibraryStrategy": "RNA-Seq", "SequencingType": "ribosome profiling"}, "riboseq", "derived_uncorroborated"),
    ({"LibraryStrategy": "RNA-Seq", "Protocol": "RNase I footprinting of polysomes"}, "riboseq", "derived_uncorroborated"),
    # A ribo-depleted RNA-seq library names "ribo" but is not a footprint library.
    ({"SequencingType": "RNA-Seq", "Name": "Ribo-Zero_depleted_rep1"}, "rnaseq", "derived_uncorroborated"),
])
def test_riboseq_type_follows_each_libraries_own_metadata(atlas, signals, value, verdict):
    """riboseq 2.0.0 assets/schema_input.json: type is required, enum riboseq, rnaseq, tiseq;
    README.md and docs/usage.md: riboseq or tiseq for Ribo-seq / TI-seq rows, rnaseq for the
    matched RNA-seq that translational efficiency (anota2seq) pairs with them. The cohort's
    protocol documents cover both kinds of library, so only each library's own fields vote
    (its SequencingType, its lineage's LibraryType and Protocol, its Name)."""
    out = evaluate_data_driven_param(_spec(atlas, "riboseq", "type"), signals)
    assert out["verdict"] == verdict, out
    if verdict != "conflict":
        assert out["value"] == value


# --- rnavar read_length ---------------------------------------------------------------

@pytest.mark.parametrize("signals, value, verdict", [
    ({"F_bp": "151", "R_bp": "151"}, "151", "corroborated"),
    ({"F_bp": 101}, "101", "derived_uncorroborated"),
    ({}, "150", "defaulted"),
])
def test_rnavar_read_length_is_the_reads_length(atlas, signals, value, verdict):
    """rnavar 1.3.0 docs/usage.md "Alignment options": read_length "has to be changed according to
    the input reads ... if the input read length is 2x151bp, then you use --read_length 151"; it
    is STAR's genomeGenerate overhang plus one. nextflow_schema.json: default 150."""
    out = evaluate_data_driven_param(_spec(atlas, "rnavar", "read_length"), signals)
    assert (out["value"], out["verdict"]) == (value, verdict)


class _Cfg:
    PROVIDER = "test"
    TOWER_ENV = {}


def test_configure_run_writes_rnavar_read_length_as_a_number(tmp_path):
    """The derived "151" reaches params.yml as 151: nextflow_schema.json types read_length as a
    number, and nf-schema refuses a string there."""
    samplesheet = tmp_path / "s.csv"
    samplesheet.write_text("sample,fastq_1,fastq_2\n")
    state = {"artifacts": {"samplesheet": str(samplesheet), "base_dir": str(tmp_path)},
             "bundle_key": None,
             "data_driven_evidence": {"D.SEQ-1": {"read_length": {
                 "value": "151", "verdict": "corroborated", "evidence": "F_bp=151; R_bp=151"}}}}
    out = json.loads(at.tool_configure_run(_Cfg(), state, {"pipeline_key": "rnavar"}, str(tmp_path)))
    assert out["ok"] is True, out
    assert out["resolved_params"]["read_length"] == 151


# --- the library kit as a second strandedness signal (owner's choice, 2026-11-17) ------------
#
# Not from the pinned files: dUTP second-strand kits read the reverse strand first, by how they
# are made. Only kits whose strand is fixed are listed; a kit that does not say leaves the ask.

@pytest.mark.parametrize("pipeline", ["rnafusion", "rnasplice"])
@pytest.mark.parametrize("signals, value, verdict", [
    ({"ExpressionKit": "NEBNext Ultra II Directional RNA Library Prep"}, "reverse", "derived_uncorroborated"),
    ({"Protocol": "polyA selection, TruSeq Stranded mRNA"}, "reverse", "derived_uncorroborated"),
    ({"Strandedness": "reverse", "ExpressionKit": "Illumina Stranded mRNA Prep"}, "reverse", "corroborated"),
    ({"Strandedness": "forward", "ExpressionKit": "KAPA Stranded mRNA-Seq"}, None, "conflict"),
    ({"ExpressionKit": "NEBNext Ultra II RNA Library Prep"}, None, "absent"),   # unstranded or not: unsaid
    ({"ExpressionKit": "SMARTer Stranded Total RNA-Seq Kit v2"}, None, "absent"),  # strand varies by version
])
def test_a_kit_with_a_fixed_strand_sets_strandedness(atlas, pipeline, signals, value, verdict):
    out = evaluate_data_driven_param(_spec(atlas, pipeline, "strandedness"), signals)
    assert out["verdict"] == verdict, out
    if verdict != "conflict":
        assert out["value"] == value
