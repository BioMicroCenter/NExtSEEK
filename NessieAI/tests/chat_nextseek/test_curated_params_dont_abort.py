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

from NessieAI.paths import CHAT_NEXTSEEK_DIR

import pytest

from chat_nextseek.seqera.pipeline_params import build_run_params

TEMPLATES = CHAT_NEXTSEEK_DIR / "src/chat_nextseek/reports/templates/nfcore"


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


# --- ampliseq: primers, or an explicit skip --------------------------------------

def test_ampliseq_elicits_primers_rather_than_defaulting_them():
    """utils_nfcore_ampliseq_pipeline/main.nf:

        if ( ... && (!params.FW_primer || !params.RV_primer) && !params.skip_cutadapt )
            error("`--FW_primer` and `--RV_primer` are required for primer trimming.")

    Primers are a bench fact, so they are asked for. Leaving them null with no
    elicitation -- which is how the template shipped -- aborts the run."""
    from chat_nextseek.seqera.user_params import missing_user_params
    asked = {s["name"] for s in missing_user_params("ampliseq", {})}
    assert asked == {"FW_primer", "RV_primer"}
    # skip_cutadapt is the pipeline's own escape hatch and must satisfy the gate.
    assert missing_user_params("ampliseq", {"skip_cutadapt": True}) == []


def test_ampliseq_primer_validation_rejects_junk():
    from chat_nextseek.seqera.user_params import validate_user_params
    assert validate_user_params("ampliseq", {"FW_primer": "GTGY CAGC"})      # spaces
    assert validate_user_params("ampliseq", {"FW_primer": "GTGYCAGC-MGCC"})  # punctuation
    assert validate_user_params(
        "ampliseq", {"FW_primer": "GTGYCAGCMGCCGCGGTAA",
                     "RV_primer": "GGACTACNVGGGTWTCTAAT"}) == []             # IUPAC is fine


# --- the standing rule --------------------------------------------------------

@pytest.mark.parametrize("key", ["detaxizer", "mag", "bacass", "seqinspector", "ampliseq"])
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


# --- sample ids the pipeline will actually accept -----------------------------

def test_ampliseq_sample_ids_are_sanitised_and_traceable(tmp_path):
    """ampliseq demands ^[a-zA-Z][a-zA-Z0-9_]+$ for sampleID. A NExtSEEK UID
    (D.SEQ-230512FOR-287-PUB) fails on the dot and the hyphens, and the run aborts at
    samplesheet validation — AFTER the reads have been downloaded. Found on a real
    Luria run, 2026-08-05."""
    import csv
    import re as _re
    from chat_nextseek.seqera.emitter import (
        PIPELINE_SAMPLE_ID_RULES, SAMPLE_ID_PROVENANCE_COLUMN, emit_nfcore_artifacts)

    uid = "D.SEQ-230512FOR-287-PUB"
    emit_nfcore_artifacts(
        tmp_path, pipeline="ampliseq", samplesheet_rows=[{"sample": uid}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={uid: {"Link_PrimaryData": "/net/x/a_R1.fastq.gz"}})
    row = next(iter(csv.DictReader((tmp_path / "samplesheet.csv").open(newline=""))))
    pattern = PIPELINE_SAMPLE_ID_RULES["ampliseq"]["pattern"]
    assert _re.fullmatch(pattern, row["sampleID"]), row["sampleID"]
    # The original must survive, or a result cannot be traced back to its record.
    assert row[SAMPLE_ID_PROVENANCE_COLUMN] == uid


def test_only_pipelines_that_need_it_get_their_ids_rewritten(tmp_path):
    """Mangling an id that did not need mangling loses traceability for no gain.
    Every other catalogued pipeline declares ^\\S+$, which a UID already satisfies."""
    import csv
    from chat_nextseek.seqera.emitter import PIPELINE_SAMPLE_ID_RULES, emit_nfcore_artifacts

    assert set(PIPELINE_SAMPLE_ID_RULES) == {"ampliseq"}, (
        "a new id rule was added — confirm the pipeline really constrains the format")
    uid = "D.SEQ-230512FOR-287-PUB"
    for key in ("rnaseq", "detaxizer", "seqinspector"):
        out = tmp_path / key
        emit_nfcore_artifacts(
            out, pipeline=key, samplesheet_rows=[{"sample": uid}], resolutions=[],
            launch_plan=None, tower_env=None,
            accession_metadata={uid: {"Link_PrimaryData": "/net/x/a_R1.fastq.gz"}})
        row = next(iter(csv.DictReader((out / "samplesheet.csv").open(newline=""))))
        assert row["sample"] == uid, f"{key} rewrote an id it did not need to"


def _state(tmp_path):
    (tmp_path / "s.csv").write_text("sample\nA\n")
    return {"artifacts": {"samplesheet": str(tmp_path / "s.csv"), "base_dir": str(tmp_path)},
            "pipeline_key": "rnaseq", "bundle_key": None}


def _fake_check(bad_names):
    """Stand in for check_params: error on any param named in `bad_names`."""
    def check(pipeline_key, revision, params, **kwargs):
        return ([f"param {n!r} is not a parameter of nf-core/{pipeline_key}@{revision}"
                 for n in params if n in bad_names], None)
    return check


def test_a_param_the_agent_supplied_is_rejected(monkeypatch, tmp_path):
    """An agent-supplied param the schema rejects is something the agent can fix."""
    import json

    from chat_nextseek.pipeline import agent_tools

    monkeypatch.setattr(agent_tools, "check_params", _fake_check({"aligner"}))
    monkeypatch.setattr(agent_tools, "check_reference_flags", lambda k, r, **kw: [])
    monkeypatch.setattr(agent_tools, "build_run_params",
                        lambda k, ap, bk: ({"aligner": "bowtie2", "gencode": True}, [], "no_bundle"))

    out = json.loads(agent_tools.tool_configure_run(
        object(), _state(tmp_path),
        {"pipeline_key": "rnaseq", "params": {"aligner": "bowtie2"}}, str(tmp_path)))
    assert out["ok"] is False
    assert any("aligner" in e for e in out["errors"])


def test_a_drifted_curated_default_warns_but_still_builds(monkeypatch, tmp_path):
    """A bad curated DEFAULT is our bug, not the agent's, and the agent cannot
    remove it — build_run_params merges defaults first and params can only
    override. Hard-failing here would spin the agent to MAX_ITER re-sending a
    param it has no way to drop."""
    import json

    from chat_nextseek.pipeline import agent_tools

    monkeypatch.setattr(agent_tools, "check_params", _fake_check({"ghost_default"}))
    monkeypatch.setattr(agent_tools, "check_reference_flags", lambda k, r, **kw: [])
    monkeypatch.setattr(agent_tools, "build_run_params",
                        lambda k, ap, bk: ({"ghost_default": True, "aligner": "star_salmon"},
                                           [], "no_bundle"))

    out = json.loads(agent_tools.tool_configure_run(
        object(), _state(tmp_path),
        {"pipeline_key": "rnaseq", "params": {"aligner": "star_salmon"}}, str(tmp_path)))
    assert out["ok"] is True
    assert any("ghost_default" in w for w in out["schema_check"]["curated_warnings"])


def test_a_clean_run_carries_no_warnings(monkeypatch, tmp_path):
    import json

    from chat_nextseek.pipeline import agent_tools

    monkeypatch.setattr(agent_tools, "check_params", _fake_check(set()))
    monkeypatch.setattr(agent_tools, "check_reference_flags", lambda k, r, **kw: [])

    out = json.loads(agent_tools.tool_configure_run(
        object(), _state(tmp_path), {"pipeline_key": "rnaseq", "params": {}}, str(tmp_path)))
    assert out["ok"] is True
    assert out["schema_check"] == {"status": "ok"}


def test_an_unreachable_schema_reports_a_skip_and_still_builds(monkeypatch, tmp_path):
    import json

    from chat_nextseek.pipeline import agent_tools

    monkeypatch.setattr(agent_tools, "check_params",
                        lambda k, r, p, **kw: ([], "rnaseq@3.18.0 schema unavailable (read timeout)"))
    monkeypatch.setattr(agent_tools, "check_reference_flags", lambda k, r, **kw: [])

    out = json.loads(agent_tools.tool_configure_run(
        object(), _state(tmp_path), {"pipeline_key": "rnaseq", "params": {}}, str(tmp_path)))
    assert out["ok"] is True
    assert out["schema_check"]["status"] == "skipped"
    assert "read timeout" in out["schema_check"]["reason"]


def test_the_revision_checked_is_the_revision_that_will_run(monkeypatch, tmp_path):
    from chat_nextseek.pipeline import agent_tools

    seen = {}

    def spy(pipeline_key, revision, params, **kwargs):
        seen["revision"] = revision
        return [], None

    monkeypatch.setattr(agent_tools, "check_params", spy)
    monkeypatch.setattr(agent_tools, "check_reference_flags", lambda k, r, **kw: [])
    state = _state(tmp_path)

    agent_tools.tool_configure_run(
        object(), state, {"pipeline_key": "rnaseq", "params": {}, "revision": "3.14.0"},
        str(tmp_path))
    assert seen["revision"] == "3.14.0"          # an explicit revision wins

    agent_tools.tool_configure_run(
        object(), state, {"pipeline_key": "rnaseq", "params": {}}, str(tmp_path))
    assert seen["revision"] == \
        agent_tools.NFCORE_PIPELINE_CATALOG["rnaseq"]["default_revision"]


def test_reference_flag_warnings_ride_along_without_blocking(monkeypatch, tmp_path):
    import json

    from chat_nextseek.pipeline import agent_tools

    monkeypatch.setattr(agent_tools, "check_params", lambda k, r, p, **kw: ([], None))
    monkeypatch.setattr(agent_tools, "check_reference_flags",
                        lambda k, r, **kw: ["the catalog declares reference flag(s) gff"])

    out = json.loads(agent_tools.tool_configure_run(
        object(), _state(tmp_path), {"pipeline_key": "rnaseq", "params": {}}, str(tmp_path)))
    assert out["ok"] is True
    assert "gff" in out["schema_check"]["reference_warnings"][0]


# --- Task 12 review round: the split must survive :675's setdefault and
# --- :697/700's genome pop, not just partition the untouched merged dict. ---

def test_a_setdefault_filled_data_driven_param_the_schema_rejects_warns_not_errors(monkeypatch, tmp_path):
    """rnaseq's extra_salmon_quant_args is a real run_param target in the atlas:
    :675 setdefaults it into agent_params from unanimous leaf evidence, so it was
    never in tool_input["params"]. The agent cannot drop it (re-sending a
    different value is rejected by check_run_params as a correction; re-sending
    the derived value changes nothing) and cannot correct it either — so a
    schema complaint about it must warn, not hard-fail, or this is the same
    MAX_ITER livelock the curated-default case exists to prevent, just sourced
    from the param atlas instead of the curated menu.
    """
    import json

    from chat_nextseek.pipeline import agent_tools

    monkeypatch.setattr(agent_tools, "check_params", _fake_check({"extra_salmon_quant_args"}))
    monkeypatch.setattr(agent_tools, "check_reference_flags", lambda k, r, **kw: [])

    state = _state(tmp_path)
    state["data_driven_evidence"] = {
        "leaf-1": {"extra_salmon_quant_args": {"value": "--noLengthCorrection",
                                               "verdict": "corroborated"}},
    }

    out = json.loads(agent_tools.tool_configure_run(
        object(), state, {"pipeline_key": "rnaseq", "params": {}}, str(tmp_path)))
    assert out["ok"] is True
    assert any("extra_salmon_quant_args" in w for w in out["schema_check"]["curated_warnings"])


def test_a_resolved_genome_override_the_schema_rejects_warns_not_errors(monkeypatch, tmp_path):
    """A genome override that resolves to a known bundle is popped out of
    agent_params at :697/700; the `genome` that lands in `merged` comes from the
    curated reference-bundle file, not from anything the agent typed. A schema
    complaint about it is a curation error, so it must warn, not hard-fail —
    this is the trap the naive 'partition on tool_input alone' fix falls into.
    """
    import json

    from chat_nextseek.pipeline import agent_tools

    monkeypatch.setattr(agent_tools, "check_params", _fake_check({"genome"}))
    monkeypatch.setattr(agent_tools, "check_reference_flags", lambda k, r, **kw: [])
    monkeypatch.setattr(agent_tools, "load_reference_bundles", lambda: {"bundles": {}})
    monkeypatch.setattr(agent_tools, "resolve_bundle_for_species", lambda species: "mm10_bundle")
    monkeypatch.setattr(agent_tools, "build_run_params",
                        lambda k, ap, bk: ({"genome": "GRCm39", "aligner": "star_salmon"},
                                           [], "igenomes_fallback"))

    out = json.loads(agent_tools.tool_configure_run(
        object(), _state(tmp_path), {"pipeline_key": "rnaseq", "params": {"genome": "mouse"}},
        str(tmp_path)))
    assert out["ok"] is True
    assert any("genome" in w for w in out["schema_check"]["curated_warnings"])


def test_an_unresolvable_genome_override_the_schema_rejects_still_hard_fails(monkeypatch, tmp_path):
    """A genome override that does NOT resolve to a known bundle or species stays
    in agent_params (the agent's literal iGenomes key, passed through verbatim).
    The agent chose that value and CAN change it, so a schema complaint about it
    must still be a hard error — this is the case a naive 'always warn on
    genome' fix would wrongly silence.
    """
    import json

    from chat_nextseek.pipeline import agent_tools

    monkeypatch.setattr(agent_tools, "check_params", _fake_check({"genome"}))
    monkeypatch.setattr(agent_tools, "check_reference_flags", lambda k, r, **kw: [])
    monkeypatch.setattr(agent_tools, "load_reference_bundles", lambda: {"bundles": {}})
    monkeypatch.setattr(agent_tools, "resolve_bundle_for_species", lambda species: None)
    monkeypatch.setattr(agent_tools, "build_run_params",
                        lambda k, ap, bk: ({"genome": "GRCh38", "aligner": "star_salmon"},
                                           [], "igenomes_fallback"))

    out = json.loads(agent_tools.tool_configure_run(
        object(), _state(tmp_path), {"pipeline_key": "rnaseq", "params": {"genome": "GRCh38"}},
        str(tmp_path)))
    assert out["ok"] is False
    assert any("genome" in e for e in out["errors"])
