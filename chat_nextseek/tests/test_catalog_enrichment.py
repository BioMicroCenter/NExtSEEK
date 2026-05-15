"""Catalog enrichment: every entry has the four pipeline_agent fields, regex
patterns compile, and the worked-example assays match each pipeline."""
import re

import pytest

from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG


NEW_FIELDS = (
    "samplesheet_input_kind",
    "accepted_leaf_sample_types",
    "accepted_assay_patterns",
    "pipeline_kind_description",
)

VALID_INPUT_KINDS = {"fastq", "bam", "accession"}


@pytest.mark.parametrize("key,entry", list(NFCORE_PIPELINE_CATALOG.items()))
def test_every_entry_has_new_fields(key, entry):
    for field in NEW_FIELDS:
        assert field in entry, f"{key} missing {field}"


@pytest.mark.parametrize("key,entry", list(NFCORE_PIPELINE_CATALOG.items()))
def test_input_kind_is_valid(key, entry):
    assert entry["samplesheet_input_kind"] in VALID_INPUT_KINDS, key


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
