"""Catalog enrichment: every entry has the four pipeline_agent fields, regex
patterns compile, and the worked-example assays match each pipeline."""
import re

import pytest

from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.input_kinds import INPUT_KINDS, SAMPLESHEET_FORMATS


NEW_FIELDS = (
    "samplesheet_input_kind",
    "accepted_leaf_sample_types",
    "accepted_assay_patterns",
    "pipeline_kind_description",
)

# The registry is the source of truth, so a new kind is valid the day it is registered
# and a catalog entry naming anything else still fails here.
VALID_INPUT_KINDS = set(INPUT_KINDS)


@pytest.mark.parametrize("key,entry", list(NFCORE_PIPELINE_CATALOG.items()))
def test_every_entry_has_new_fields(key, entry):
    for field in NEW_FIELDS:
        assert field in entry, f"{key} missing {field}"


@pytest.mark.parametrize("key,entry", list(NFCORE_PIPELINE_CATALOG.items()))
def test_input_kind_is_valid(key, entry):
    assert entry["samplesheet_input_kind"] in VALID_INPUT_KINDS, key


@pytest.mark.parametrize("key,entry", list(NFCORE_PIPELINE_CATALOG.items()))
def test_input_extensions_narrow_the_kinds_own(key, entry):
    own = entry.get("input_extensions")
    if own is None:
        return
    allowed = set(INPUT_KINDS[entry["samplesheet_input_kind"]].extensions)
    assert own and set(own) <= allowed, (key, sorted(set(own) - allowed))


@pytest.mark.parametrize("key,entry", list(NFCORE_PIPELINE_CATALOG.items()))
def test_samplesheet_format_is_valid(key, entry):
    assert entry.get("samplesheet_format", "csv") in SAMPLESHEET_FORMATS, key


@pytest.mark.parametrize("key,entry", list(NFCORE_PIPELINE_CATALOG.items()))
def test_a_file_input_pipeline_names_the_sample_types_its_files_belong_to(key, entry):
    """The kind says which file; accepted_leaf_sample_types says whose. A file kind with no
    sample types would resolve no leaves and build an empty sheet."""
    if INPUT_KINDS[entry["samplesheet_input_kind"]].path_column:
        assert entry["accepted_leaf_sample_types"], key


@pytest.mark.parametrize("key,entry", list(NFCORE_PIPELINE_CATALOG.items()))
def test_nextflow_version_when_set_is_a_release_number(key, entry):
    from chat_nextseek.luria.run_script import validate_nextflow_version
    if "nextflow_version" in entry:
        validate_nextflow_version(entry["nextflow_version"])


@pytest.mark.parametrize("key,entry", list(NFCORE_PIPELINE_CATALOG.items()))
def test_default_revision_is_a_release_or_a_full_commit_sha(key, entry):
    from packaging.version import InvalidVersion, Version
    rev = entry["default_revision"]
    if re.fullmatch(r"[0-9a-f]{40}", rev):
        return
    try:
        Version(rev)
    except InvalidVersion:
        pytest.fail(f"{key}: default_revision {rev!r} is neither a release nor a 40-hex commit")


@pytest.mark.parametrize("key,entry", list(NFCORE_PIPELINE_CATALOG.items()))
def test_assay_patterns_compile(key, entry):
    for pat in entry["accepted_assay_patterns"]:
        re.compile(pat, re.IGNORECASE)


@pytest.mark.parametrize("key,entry", list(NFCORE_PIPELINE_CATALOG.items()))
def test_pipeline_kind_description_is_nonempty_when_relevant(key, entry):
    assert entry["pipeline_kind_description"], key


def test_fetchngs_uses_accession_input():
    fetchngs = NFCORE_PIPELINE_CATALOG["fetchngs"]
    assert fetchngs["samplesheet_input_kind"] == "accession"
    assert fetchngs["accepted_leaf_sample_types"] == []
    assert fetchngs["accepted_assay_patterns"] == []


def test_rnaseq_accepts_dseq_and_common_rna_assay_names():
    rnaseq = NFCORE_PIPELINE_CATALOG["rnaseq"]
    assert "D.SEQ" in rnaseq["accepted_leaf_sample_types"]
    assays = ["RNA-seq", "RNA Sample", "bulk RNA", "bulk RNA-seq", "RNAseq"]
    for a in assays:
        assert any(re.search(p, a, re.IGNORECASE) for p in rnaseq["accepted_assay_patterns"]), a


def test_sarek_accepts_dseq_and_wgs_wes_assay_names():
    sarek = NFCORE_PIPELINE_CATALOG["sarek"]
    assert "D.SEQ" in sarek["accepted_leaf_sample_types"]
    for a in ["WGS", "WES", "DNA-seq"]:
        assert any(re.search(p, a, re.IGNORECASE) for p in sarek["accepted_assay_patterns"]), a


def test_methylseq_accepts_methylation_terms():
    m = NFCORE_PIPELINE_CATALOG["methylseq"]
    for a in ["WGBS", "RRBS", "EM-seq", "EM_seq", "methylation"]:
        assert any(re.search(p, a, re.IGNORECASE) for p in m["accepted_assay_patterns"]), a


def test_sarek_variant_pattern_does_not_overmatch_invariant():
    """Regression: bare r'variant' pattern would match 'invariant region' (false positive)."""
    s = NFCORE_PIPELINE_CATALOG["sarek"]
    assert any(re.search(p, "variant calling", re.IGNORECASE) for p in s["accepted_assay_patterns"])
    assert any(re.search(p, "variant_filter", re.IGNORECASE) for p in s["accepted_assay_patterns"])
    assert not any(re.search(p, "invariant region", re.IGNORECASE) for p in s["accepted_assay_patterns"])


def test_ampliseq_accepts_long_form_amplicon_assay_names():
    """Regression: ^16S$ / ^ITS$ strict anchors rejected '16S rRNA', 'ITS1' etc."""
    a = NFCORE_PIPELINE_CATALOG["ampliseq"]
    for name in ["16S", "16S rRNA", "16S V3-V4", "ITS", "ITS1", "ITS2", "amplicon", "microbiome"]:
        assert any(re.search(p, name, re.IGNORECASE) for p in a["accepted_assay_patterns"]), name
