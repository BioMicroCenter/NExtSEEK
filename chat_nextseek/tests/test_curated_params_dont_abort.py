"""Curated params must not produce a combination the pipeline rejects or a hidden download.

Every assertion here encodes a gate read out of a pinned workflow's own .nf source on
2026-08-05, after detaxizer was cancelled 4.2 GB into a 64 GB database download that
its template said would not happen.

The lesson these guard: **a param named `skip_x` or set to `false` is not evidence
that x is off.** It has to be read against the workflow's actual gating. Three of the
four pipelines audited on that basis turned out to be wrong.
"""
import json
from pathlib import Path

import pytest

from chat_nextseek.seqera.pipeline_params import build_run_params

TEMPLATES = Path(__file__).resolve().parent.parent / "src/chat_nextseek/reports/templates/nfcore"


def curated(key):
    """The params we would actually send, defaults merged, no agent overrides."""
    merged, errors, _status = build_run_params(key, {}, None)
    assert errors == [], f"{key}: curated params do not validate: {errors}"
    return merged


# --- mag: skip_binqc + any run_<tool> is a hard error --------------------------

def test_mag_does_not_combine_skip_binqc_with_a_bin_qc_tool():
    """subworkflows/local/utils_nfcore_mag_pipeline/main.nf:

        if (params.skip_binqc && (params.run_busco || params.run_checkm || params.run_checkm2))
            error("Both --skip_binqc and --run_<bin_qc_tool_name> are specified!")

    mag 5.5.0 defaults run_busco to TRUE, so shipping skip_binqc without also
    turning run_busco off aborted the run before a single task started.
    """
    p = curated("mag")
    if p.get("skip_binqc"):
        for tool in ("run_busco", "run_checkm", "run_checkm2"):
            assert p.get(tool) is False, (
                f"mag sets skip_binqc but leaves {tool} at the pipeline default (run_busco "
                f"defaults to TRUE) — the run will error() at validation")


def test_mag_keeps_the_gtdb_database_unstaged():
    """workflows/mag.nf: gtdb = params.skip_binqc || params.skip_gtdbtk ? false : params.gtdb_db

    gtdb_db defaults to a remote GTDB R232 tarball, so at least one skip must hold."""
    p = curated("mag")
    assert p.get("skip_binqc") or p.get("skip_gtdbtk"), (
        "neither skip_binqc nor skip_gtdbtk is set — mag will stage the GTDB-Tk database")


# --- bacass: assembly_type has no default and error()s ------------------------

def test_bacass_sets_assembly_type():
    """bacass declares no default for assembly_type and error()s without one."""
    p = curated("bacass")
    assert p.get("assembly_type") in ("short", "long", "hybrid"), (
        "bacass has no default assembly_type and aborts at validation without it")


def test_bacass_skips_both_database_steps():
    """Its validator error()s on !skip_kraken2 && !kraken2db, and the same for kmerfinder."""
    p = curated("bacass")
    assert p.get("skip_kraken2") is True and not p.get("kraken2db")
    assert p.get("skip_kmerfinder") is True and not p.get("kmerfinderdb")


def test_bacass_annotation_tool_needs_no_database():
    p = curated("bacass")
    assert p.get("annotation_tool") == "prokka", (
        "bakta/dfast need a database that is not provisioned; prokka does not")


# --- seqinspector: the default bundle, and what it drags in -------------------

def test_seqinspector_default_bundle_excludes_kraken2():
    """The 'default' bundle is fastqc, fastqscreen, fq_lint,
    picard_collectmultiplemetrics, rundirparser, seqfu_stats, sequali. kraken2 is NOT
    in it, and the pipeline error()s if kraken2 is requested with no kraken2_db — so
    there is no silent download here. Guards the tools list against drift."""
    p = curated("seqinspector")
    assert p.get("tools_bundle") == "default"
    assert not p.get("tools"), "an explicit tools list overrides the bundle — re-check kraken2"
    if "kraken2" in str(p.get("tools") or "") + str(p.get("tools_bundle") or ""):
        assert p.get("kraken2_db"), "kraken2 requested without a database"


def test_seqinspector_skips_fastqscreen_until_references_are_curated():
    """fastqscreen IS in the default bundle, and fastq_screen_references defaults to an
    EXAMPLE csv shipped with the pipeline that points at three S3 iGenomes indices.
    Screening against an example set is not a curated choice."""
    p = curated("seqinspector")
    skipped = str(p.get("skip_tools") or "")
    assert "fastqscreen" in skipped or p.get("fastq_screen_references"), (
        "fastqscreen is enabled with no curated reference CSV — it will pull the example "
        "S3 references")


# --- the standing rule --------------------------------------------------------

@pytest.mark.parametrize("key", ["detaxizer", "mag", "bacass", "seqinspector"])
def test_templates_record_why_their_skip_flags_are_load_bearing(key):
    """Each of these has at least one param whose value prevents a download or an abort.
    The template must say so, or the next person will 'tidy' it away."""
    doc = json.loads((TEMPLATES / f"{key}.json").read_text())
    blob = json.dumps(doc).lower()
    assert any(w in blob for w in ("abort", "error()", "download", "not provisioned", "staged")), (
        f"{key}: nothing in the template explains why its curated params matter")


def test_detaxizer_still_avoids_the_kraken2_default():
    """The regression that started this: Kraken2 is detaxizer's FALLBACK classifier, so
    classification_kraken2=false does not disable it — bbduk must be on instead."""
    p = curated("detaxizer")
    assert p.get("classification_bbduk") is True, (
        "with both classifiers off detaxizer runs Kraken2 and stages a 64 GB database")
    assert p.get("fasta_bbduk"), (
        "bbduk with no fasta_bbduk falls back to getGenomeAttribute('fasta') — iGenomes on S3")
