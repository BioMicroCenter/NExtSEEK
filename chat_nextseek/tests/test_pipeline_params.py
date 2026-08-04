import json
from pathlib import Path

import pytest

NFCORE_DIR = Path(__file__).resolve().parent.parent / "src" / "chat_nextseek" / "reports" / "templates" / "nfcore"


def _load(name):
    return json.loads((NFCORE_DIR / f"{name}.json").read_text())


def test_rnaseq_has_curated_params_and_reference_resources():
    doc = _load("rnaseq")
    params = doc["params"]
    assert params["aligner"]["allowed"] == ["star_salmon", "star_rsem", "hisat2"]
    assert params["aligner"]["default"] == "star_salmon"
    assert params["pseudo_aligner"]["default"] == "salmon"
    assert params["gencode"]["type"] == "bool"
    for key, spec in params.items():
        assert "type" in spec and "default" in spec and "steerable" in spec, key
    assert doc["reference_resources"] == ["fasta", "gtf", "star_index", "rsem_index", "salmon_index", "bed12"]


def test_scrnaseq_has_curated_params_and_reference_resources():
    doc = _load("scrnaseq")
    params = doc["params"]
    assert "alevin" in params["aligner"]["allowed"]
    assert params["protocol"]["default"] == "auto"
    assert "expected_cells" not in params
    assert doc["reference_resources"] == ["fasta", "gtf", "salmon_index", "star_index", "txp2gene"]


ALL_PIPELINES = ["rnaseq", "scrnaseq", "atacseq", "chipseq", "sarek", "methylseq", "ampliseq", "fetchngs"]


@pytest.mark.parametrize("key", ALL_PIPELINES)
def test_every_pipeline_json_has_params_and_reference_resources_keys(key):
    doc = _load(key)
    assert isinstance(doc.get("params"), dict)
    assert isinstance(doc.get("reference_resources"), list)


def test_reference_bundles_structure():
    doc = json.loads((NFCORE_DIR / "reference_bundles.json").read_text())
    assert doc["store_root"] is None  # v1: unconfigured
    s2b = doc["species_to_bundle"]
    assert s2b["mouse"] == "GRCm39"
    assert s2b["human"] == "GRCh38"
    assert s2b["human+mouse"] == "hg38_mm39"
    assert doc["bundles"]["GRCh38"]["igenomes_key"] == "GRCh38"
    assert doc["bundles"]["hg38_mm39"]["igenomes_key"] is None  # PDX combo: no iGenomes fallback
    assert "{store_root}" in doc["bundles"]["GRCh38"]["resources"]["fasta"]


from chat_nextseek.seqera import pipeline_params as pp


def test_load_pipeline_context_returns_params_menu():
    ctx = pp.load_pipeline_context("rnaseq")
    assert "aligner" in ctx["params"]
    assert ctx["reference_resources"][0] == "fasta"


def test_resolve_bundle_for_species_is_case_insensitive():
    assert pp.resolve_bundle_for_species("Mus musculus") == "GRCm39"
    assert pp.resolve_bundle_for_species("HUMAN") == "GRCh38"
    assert pp.resolve_bundle_for_species("doesnotexist") is None
    assert pp.resolve_bundle_for_species(None) is None


def test_build_reference_params_local_luria_for_registered_genome():
    params, status = pp.build_reference_params("rnaseq", "GRCm39")
    assert params == {"genome": "GRCm39"}
    assert status == "local_luria"   # GRCm39 has a local Luria ref -> not an iGenomes fallback


def test_build_reference_params_igenomes_fallback_for_unregistered(monkeypatch):
    bundles = {"store_root": None, "species_to_bundle": {"zeb": "GRCz11"},
               "bundles": {"GRCz11": {"igenomes_key": "GRCz11"}}}   # not in LURIA_GENOMES
    monkeypatch.setattr(pp, "load_reference_bundles", lambda: bundles)
    params, status = pp.build_reference_params("rnaseq", "GRCz11")
    assert params == {"genome": "GRCz11"} and status == "igenomes_fallback"


def test_build_reference_params_pdx_no_fallback():
    params, status = pp.build_reference_params("rnaseq", "hg38_mm39")
    assert params == {}
    assert status == "unconfigured_no_fallback"


def test_build_reference_params_emits_paths_when_store_configured(monkeypatch):
    # GRCz11 (zebrafish) is NOT in LURIA_GENOMES, so the top-priority local_luria status
    # does not preempt the configured store_root path this test exercises.
    bundles = {
        "store_root": "/refs",
        "species_to_bundle": {"zebrafish": "GRCz11"},
        "bundles": {"GRCz11": {"igenomes_key": "GRCz11", "gencode": True,
                               "resources": {"fasta": "{store_root}/GRCz11/genome.fa",
                                             "gtf": "{store_root}/GRCz11/genes.gtf",
                                             "star_index": "{store_root}/GRCz11/index/star/"}}}}
    monkeypatch.setattr(pp, "load_reference_bundles", lambda: bundles)
    params, status = pp.build_reference_params("rnaseq", "GRCz11")
    assert status == "configured"
    assert params["fasta"] == "/refs/GRCz11/genome.fa"
    assert params["gtf"] == "/refs/GRCz11/genes.gtf"
    assert params["star_index"] == "/refs/GRCz11/index/star/"
    assert "rsem_index" not in params
    assert params["gencode"] is True


def test_build_reference_params_configured_skips_gencode_for_pipeline_without_it(monkeypatch):
    # A bundle's gencode flag must NOT be stamped onto a pipeline that has no gencode param.
    # GRCz11 (not in LURIA_GENOMES) so the configured store_root path is exercised rather
    # than the top-priority local_luria status.
    bundles = {
        "store_root": "/refs",
        "species_to_bundle": {"zebrafish": "GRCz11"},
        "bundles": {"GRCz11": {"igenomes_key": "GRCz11", "gencode": True,
                               "resources": {"fasta": "{store_root}/GRCz11/genome.fa",
                                             "bwa_index": "{store_root}/GRCz11/bwa/"}}}}
    monkeypatch.setattr(pp, "load_reference_bundles", lambda: bundles)
    params, status = pp.build_reference_params("atacseq", "GRCz11")  # atacseq has no gencode param
    assert status == "configured"
    assert "gencode" not in params
    assert params["fasta"] == "/refs/GRCz11/genome.fa"
    assert params["bwa_index"] == "/refs/GRCz11/bwa/"


def test_build_run_params_merges_defaults_bundle_and_overrides():
    merged, errors, status = pp.build_run_params(
        "rnaseq", agent_params={"aligner": "hisat2"}, bundle_key="GRCm39")
    assert errors == []
    assert merged["aligner"] == "hisat2"
    assert merged["pseudo_aligner"] == "salmon"
    assert merged["genome"] == "GRCm39"
    assert status == "local_luria"   # was igenomes_fallback before the local-ref bias


def test_build_run_params_rejects_unknown_key():
    merged, errors, status = pp.build_run_params(
        "rnaseq", agent_params={"not_a_real_param": 1}, bundle_key="GRCm39")
    assert any("not_a_real_param" in e for e in errors)
    # guardrail invariant: on any error, no params leak through and status is 'invalid'
    assert merged == {} and status == "invalid"


def test_build_run_params_rejects_bad_enum_value():
    merged, errors, status = pp.build_run_params(
        "rnaseq", agent_params={"aligner": "bowtie"}, bundle_key="GRCm39")
    assert any("aligner" in e and "bowtie" in e for e in errors)


def test_build_run_params_allows_genome_and_reference_keys():
    merged, errors, status = pp.build_run_params(
        "rnaseq", agent_params={"genome": "GRCh38", "fasta": "/x/g.fa"}, bundle_key=None)
    assert errors == []
    assert merged["genome"] == "GRCh38"
    assert merged["fasta"] == "/x/g.fa"


def test_build_run_params_strips_input_and_outdir():
    merged, errors, status = pp.build_run_params(
        "rnaseq", agent_params={"input": "/x.csv", "outdir": "/out"}, bundle_key="GRCm39")
    assert "input" not in merged and "outdir" not in merged
    assert errors == []


# --- seqinspector: QC-only, reference-free -------------------------------------

def test_seqinspector_template_is_reference_free_and_well_formed():
    doc = _load("seqinspector")
    assert doc["report_type"] == "NFCORE_SEQINSPECTOR_SAMPLESHEET"
    # The whole point of this pipeline as a first Tier-1 add: it needs no reference.
    # The Luria refs tree carries only fasta+gtf (no bwamem2 index), so requesting
    # the alignment-dependent tools would force a per-run index build.
    assert doc["reference_resources"] == []
    assert doc["pipeline"]["required_columns"] == ["sample", "fastq_1", "fastq_2"]
    assert doc["pipeline"]["optional_columns"] == ["rundir", "tags"]
    for key, spec in doc["params"].items():
        assert "type" in spec and "default" in spec and "steerable" in spec, key
    # Pinned-schema facts that would silently rot if the revision moved.
    assert doc["params"]["sample_size"]["default"] == 0
    assert doc["params"]["continue_with_lint_fail"]["default"] is True
    assert doc["params"]["tools_bundle"]["default"] == "default"


def test_seqinspector_is_registered_everywhere_a_pipeline_key_must_be():
    from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG, get_pipeline_entry
    from chat_nextseek.reports.templates_meta import (
        get_report_template_basename, nfcore_pipeline_from_report_type, normalize_report_type,
    )
    entry = get_pipeline_entry("seqinspector")
    assert entry["default_revision"] == "1.1.0"
    assert entry["accepted_leaf_sample_types"] == ["D.SEQ"]
    assert entry["samplesheet_input_kind"] == "fastq"
    assert entry["default_genome"] is None
    assert "seqinspector" in NFCORE_PIPELINE_CATALOG
    # Report-type aliases resolve to the pipeline key and its template basename.
    for alias in ("NFCORE_SEQINSPECTOR", "nf-core seqinspector", "NFCORE_SEQINSPECTOR_SAMPLESHEET"):
        assert normalize_report_type(alias) == "NFCORE_SEQINSPECTOR"
        assert nfcore_pipeline_from_report_type(alias) == "seqinspector"
        assert get_report_template_basename(alias) == "nfcore/seqinspector"


def test_every_catalog_pipeline_has_a_loadable_template():
    # A catalog entry whose template is missing fails at launch, not at import —
    # so assert the pairing here instead of discovering it on Luria.
    from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
    for key in NFCORE_PIPELINE_CATALOG:
        doc = _load(key)
        assert doc.get("params") is not None, f"{key} template has no params block"
        assert doc.get("reference_resources") is not None, f"{key} template has no reference_resources"


# --- Tier-1 batch: 5 reference-light pipelines added 2026-08-04 -----------------

_TIER1_BATCH = {
    # key: (revision, reference_cli_flags, required samplesheet columns)
    "smrnaseq":  ("2.4.1", ["genome", "fasta"], ["sample", "fastq_1"]),
    "riboseq":   ("1.2.0", ["genome", "fasta", "gtf"], ["sample", "fastq_1", "strandedness", "type"]),
    "hlatyping": ("2.2.0", ["genome"], ["sample", "seq_type"]),
    "hic":       ("2.1.0", ["genome", "fasta"], ["sample", "fastq_1"]),
    "rnavar":    ("1.3.0", ["genome", "fasta", "gtf"], ["sample", "fastq_1"]),
}


def test_tier1_batch_catalog_entries_match_their_pinned_schemas():
    """reference_cli_flags and required columns were read off each pinned revision's
    nextflow_schema.json / assets/schema_input.json on 2026-08-04. If a revision is
    bumped, re-derive them — a stale gtf flag here aborts the run at nf-schema."""
    from chat_nextseek.seqera.catalog import get_pipeline_entry
    for key, (rev, flags, req_cols) in _TIER1_BATCH.items():
        entry = get_pipeline_entry(key)
        assert entry["default_revision"] == rev, key
        assert entry["reference_cli_flags"] == flags, key
        assert entry["required_columns"] == req_cols, key
        assert entry["accepted_leaf_sample_types"] == ["D.SEQ"], key
        assert entry["samplesheet_input_kind"] == "fastq", key


def test_tier1_batch_templates_agree_with_the_catalog():
    from chat_nextseek.seqera.catalog import get_pipeline_entry
    for key, (_rev, _flags, req_cols) in _TIER1_BATCH.items():
        doc = _load(key)
        assert doc["pipeline"]["required_columns"] == req_cols, key
        assert doc["pipeline"]["required_columns"] == get_pipeline_entry(key)["required_columns"], key
        for name, spec in doc["params"].items():
            assert "type" in spec and "default" in spec and "steerable" in spec, f"{key}:{name}"


def test_tier1_batch_report_type_aliases_resolve():
    from chat_nextseek.reports.templates_meta import (
        get_report_template_basename, nfcore_pipeline_from_report_type,
    )
    for key in _TIER1_BATCH:
        for alias in (f"NFCORE_{key.upper()}", f"NF_CORE_{key.upper()}",
                      f"NFCORE_{key.upper()}_SAMPLESHEET"):
            assert nfcore_pipeline_from_report_type(alias) == key, alias
            assert get_report_template_basename(alias) == f"nfcore/{key}", alias


def test_enum_constrained_columns_are_documented_with_their_allowed_values():
    """riboseq `type` and hlatyping `seq_type` are enum-constrained and cannot be
    guessed from a FASTQ path — they come from assay metadata. If the field
    dictionary stops naming the allowed values, the agent will invent one."""
    ribo = _load("riboseq")["schema"]["sections"]["samplesheet_columns"]["fields"]
    assert "riboseq" in ribo["type"]["allowed_values"] and "rnaseq" in ribo["type"]["allowed_values"]
    assert "forward" in ribo["strandedness"]["allowed_values"]
    hla = _load("hlatyping")["schema"]["sections"]["samplesheet_columns"]["fields"]
    assert hla["seq_type"]["allowed_values"] == "dna | rna"


def test_rnavar_defaults_to_skipping_bqsr():
    """BQSR needs dbsnp + known_indels; neither is provisioned in the Luria refs tree,
    so the curated default must be to skip it rather than fail deep into a run."""
    assert _load("rnavar")["params"]["skip_baserecalibration"]["default"] is True


def test_crisprseq_entry_and_template_agree_with_the_pinned_schema():
    """Only sample+fastq_1 are required by assets/schema_input.json — the sequence
    columns are optional, and the run-level --protospacer / --reference_fasta
    override them. That is what makes a shared-guide cohort answerable in a few
    questions rather than needing per-row curation."""
    from chat_nextseek.seqera.catalog import get_pipeline_entry
    from chat_nextseek.reports.templates_meta import (
        get_report_template_basename, nfcore_pipeline_from_report_type,
    )
    entry = get_pipeline_entry("crisprseq")
    assert entry["default_revision"] == "2.3.0"
    assert entry["required_columns"] == ["sample", "fastq_1"]
    assert entry["reference_cli_flags"] == []          # works off the amplicon, not a genome
    assert entry["default_genome"] is None
    doc = _load("crisprseq")
    assert doc["pipeline"]["required_columns"] == entry["required_columns"]
    assert "protospacer" in doc["pipeline"]["optional_columns"]
    for alias in ("NFCORE_CRISPRSEQ", "NF_CORE_CRISPRSEQ", "NFCORE_CRISPRSEQ_SAMPLESHEET"):
        assert nfcore_pipeline_from_report_type(alias) == "crisprseq"
        assert get_report_template_basename(alias) == "nfcore/crisprseq"


def test_declared_user_param_specs_are_well_formed():
    """Every required_user_param must carry a definition and an example — the whole
    point is that the user sees what is being asked for, not a bare param name."""
    import re
    from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
    from chat_nextseek.seqera.user_params import required_user_params
    seen = 0
    for key in NFCORE_PIPELINE_CATALOG:
        for spec in required_user_params(key):
            seen += 1
            assert spec.get("definition"), f"{key}:{spec['name']} has no definition"
            assert spec.get("example"), f"{key}:{spec['name']} has no example"
            assert spec.get("scope") in ("run", "sample"), f"{key}:{spec['name']} scope"
            if spec.get("pattern"):
                re.compile(spec["pattern"])          # a bad regex would silently no-op
            # A declared param must be one the pipeline actually accepts.
            assert spec["name"] in _load(key)["params"], f"{key}:{spec['name']} not in params"
    assert seen >= 6, "expected crisprseq (4) + smrnaseq (1) + hic (1)"
