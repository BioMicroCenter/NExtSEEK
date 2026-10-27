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
from chat_nextseek.seqera.schema_check import check_params

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


FIXTURES = Path(__file__).parent / "fixtures" / "nfcore"


def test_sarek_defaults_never_reach_for_an_unstaged_cache():
    """vep/snpeff/bcfann/snpsift and merge need annotation caches no Luria run has."""
    p = curated("sarek")
    tools = {t.strip() for t in str(p.get("tools") or "").split(",") if t.strip()}
    assert not tools & {"vep", "snpeff", "merge", "bcfann", "snpsift"}, tools
    assert not p.get("download_cache")


def test_sarek_defaults_pass_the_pinned_schema():
    schema = json.loads((FIXTURES / "sarek.nextflow_schema.json").read_text())
    errors, skipped = check_params("sarek", "3.10.0", curated("sarek"),
                                   schema_getter=lambda _p, _r: {"nextflow_schema": schema})
    assert skipped is None and errors == []


def _sarek_template():
    return json.loads((TEMPLATES / "sarek.json").read_text())


@pytest.mark.parametrize("tool", ["ascat", "controlfreec"])
def test_sarek_offers_no_tool_whose_reference_files_are_not_staged(tool):
    """ascat's allele/loci/GC files and controlfreec's mappability files are not on Luria."""
    template = _sarek_template()
    offered, unavailable = template["params"]["tools"]["help"].split("Not available:")
    assert tool not in offered and tool in unavailable
    pair_rule = next(r for r in template["validation_rules"] if r.startswith("Tumour-pair tools"))
    assert tool not in pair_rule
    assert any(r.startswith("Do not request") and tool in r for r in template["validation_rules"])


def test_sarek_bam_output_is_not_steerable():
    """reingest registers sarek's alignments as CRAMs only until the map has a BAM rule."""
    spec = _sarek_template()["params"]["save_output_as_bam"]
    assert spec["steerable"] is False and spec["default"] is False
    assert "CRAM" in spec["help"]


PINS = {"atacseq": "2.1.2", "chipseq": "2.1.0", "methylseq": "4.2.0"}  # Task 3 Step 2, owner-approved


@pytest.mark.parametrize("key", sorted(PINS))
def test_new_choosable_pipelines_pin_the_approved_release(key):
    from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
    assert NFCORE_PIPELINE_CATALOG[key]["default_revision"] == PINS[key]


def launched(key, bundle_key="GRCh38"):
    """The params a launch for a human cohort would send: curated defaults, the bundle's
    genome, and the example of each value only the user can give (its first word: an
    example may carry a gloss, as smrnaseq's "hsa   (human; ...)" does)."""
    from chat_nextseek.seqera.user_params import required_user_params
    user = {spec["name"]: (spec.get("example") or "x").split()[0] for spec in required_user_params(key)}
    merged, errors, _status = build_run_params(key, user, bundle_key)
    assert errors == [], f"{key}: launch params do not validate: {errors}"
    return merged


@pytest.mark.parametrize("key", sorted(PINS))
def test_new_choosable_pipelines_defaults_pass_the_pinned_schema(key):
    schema = json.loads((FIXTURES / f"{key}.nextflow_schema.json").read_text())
    errors, skipped = check_params(key, PINS[key], launched(key),
                                   schema_getter=lambda _p, _r: {"nextflow_schema": schema})
    assert skipped is None and errors == []


PINNED_SCHEMAS = sorted(p.name.split(".")[0] for p in FIXTURES.glob("*.nextflow_schema.json"))
#: Required params NExtSEEK has no way to set today. rnafusion's prebuilt reference bundle
#: (genomes_base) is not provisioned, so a launch would abort at nf-schema.
UNSET_REQUIRED = {"rnafusion": ["genomes_base"]}


@pytest.mark.parametrize("key", PINNED_SCHEMAS)
def test_a_human_launch_leaves_no_required_param_unset_on_any_pinned_schema(key):
    """The required check must not fail a launch that works: the bundle's genome fills the
    reference, the launcher writes input/outdir, and nextflow.config sets every schema default."""
    from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
    from chat_nextseek.seqera.schema_check import missing_required
    schema = json.loads((FIXTURES / f"{key}.nextflow_schema.json").read_text())
    names, skipped = missing_required(key, NFCORE_PIPELINE_CATALOG[key]["default_revision"],
                                      launched(key),
                                      schema_getter=lambda _p, _r: {"nextflow_schema": schema})
    assert skipped is None and names == UNSET_REQUIRED.get(key, [])


def test_configure_run_refuses_chipseq_with_no_reference_genome(monkeypatch, tmp_path):
    """chipseq 2.1.0 requires fasta: with no species-resolved genome the run would die on the
    cluster, so the agent is told to send one."""
    from chat_nextseek.pipeline import agent_tools

    _pinned_schema(monkeypatch)
    state = _state(tmp_path)
    out = json.loads(agent_tools.tool_configure_run(
        object(), state, {"pipeline_key": "chipseq", "params": {}}, str(tmp_path)))
    assert out["ok"] is False
    assert any("'fasta'" in e and "genome" in e for e in out["errors"])

    out = json.loads(agent_tools.tool_configure_run(
        object(), state, {"pipeline_key": "chipseq", "params": {"genome": "GRCh38"}}, str(tmp_path)))
    assert out["ok"] is True, out
    assert out["schema_check"] == {"status": "ok"}


def test_a_required_param_the_agent_cannot_send_warns_not_errors(monkeypatch, tmp_path):
    """rnafusion's genomes_base is on no menu: the agent can neither send nor drop it, so
    hard-failing would spin it to MAX_ITER, as a drifted curated default would."""
    from chat_nextseek.pipeline import agent_tools

    _pinned_schema(monkeypatch)
    state = _state(tmp_path)
    state["bundle_key"] = "GRCh38"
    out = json.loads(agent_tools.tool_configure_run(
        object(), state, {"pipeline_key": "rnafusion", "params": {}}, str(tmp_path)))
    assert out["ok"] is True, out
    assert any("genomes_base" in w for w in out["schema_check"]["required_warnings"])


@pytest.mark.parametrize("key", sorted(PINS))
def test_new_choosable_pipelines_templates_name_every_required_input_column(key):
    """The samplesheet columns the template asks for are the pinned input schema's required ones."""
    path = FIXTURES / f"{key}.schema_input.json"
    if not path.exists():
        pytest.skip(f"{key} {PINS[key]} ships no assets/schema_input.json")
    items = json.loads(path.read_text())["items"]
    required = set(items.get("required") or [])
    template = json.loads((TEMPLATES / f"{key}.json").read_text())
    assert required <= set(template["pipeline"]["required_columns"]), required - set(template["pipeline"]["required_columns"])


@pytest.mark.parametrize("key", sorted(PINS))
def test_new_choosable_pipelines_offer_only_params_of_the_pinned_schema(key):
    """Every param on the menu, not only those with a default, is one the pinned release declares."""
    schema = json.loads((FIXTURES / f"{key}.nextflow_schema.json").read_text())
    menu = json.loads((TEMPLATES / f"{key}.json").read_text())["params"]
    errors, skipped = check_params(key, PINS[key], {name: None for name in menu}, required=False,
                                   schema_getter=lambda _p, _r: {"nextflow_schema": schema})
    assert skipped is None and errors == []


@pytest.mark.parametrize("key", sorted(PINS))
def test_new_choosable_pipelines_template_rows_use_only_input_schema_columns(key):
    items = json.loads((FIXTURES / f"{key}.schema_input.json").read_text())["items"]
    template = json.loads((TEMPLATES / f"{key}.json").read_text())
    for row in template["samplesheet_template"]:
        assert set(row) <= set(items["properties"]), set(row) - set(items["properties"])


def test_atacseq_offers_input_controls_off_by_default():
    """atacseq 2.1.2 calls peaks against controls only with --with_control and the control columns."""
    template = json.loads((TEMPLATES / "atacseq.json").read_text())
    spec = template["params"]["with_control"]
    assert spec["type"] == "bool" and spec["default"] is False and spec["steerable"] is True
    assert curated("atacseq")["with_control"] is False
    assert {"control", "control_replicate"} <= set(template["samplesheet_template"][0])


def _pinned_schema(monkeypatch):
    """Serve every schema check from the pinned fixtures, never the network."""
    from chat_nextseek.seqera import schema_check

    def get(pipeline, revision):
        return {"nextflow_schema": json.loads((FIXTURES / f"{pipeline}.nextflow_schema.json").read_text())}
    monkeypatch.setattr(schema_check, "get_schema", get)


def test_configure_run_launches_atacseq_with_its_input_controls(monkeypatch, tmp_path):
    import csv

    import yaml

    from chat_nextseek.pipeline import agent_tools

    _pinned_schema(monkeypatch)
    rows = [{"sample": "D.SEQ-IN-1", "fastq_1": "/net/x/in_R1.fastq.gz", "replicate": 1},
            {"sample": "D.SEQ-ATAC-1", "fastq_1": "/net/x/atac_R1.fastq.gz", "replicate": 1,
             "control": "D.SEQ-IN-1", "control_replicate": 1}]
    state = {"resolved": {"uids": [r["sample"] for r in rows], "accessions": []},
             "accession_file_paths": {}, "bundle_key": "GRCh38"}
    built = json.loads(agent_tools.tool_write_samplesheet(
        object(), state, {"pipeline_key": "atacseq", "cohorts": [{"label": "c", "rows": rows}]},
        str(tmp_path)))
    assert built["ok"] is True, built
    with open(built["samplesheet"], newline="") as fh:
        sheet = list(csv.DictReader(fh))
    assert sheet[1]["control"] == "D.SEQ-IN-1" and sheet[1]["control_replicate"] == "1"

    out = json.loads(agent_tools.tool_configure_run(
        object(), state, {"pipeline_key": "atacseq", "params": {"with_control": True}}, str(tmp_path)))
    assert out["ok"] is True, out
    assert out["schema_check"]["status"] == "ok"
    assert yaml.safe_load(open(out["params_yml"]))["with_control"] is True
