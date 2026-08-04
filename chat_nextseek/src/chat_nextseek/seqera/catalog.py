"""Curated catalog of nf-core pipelines supported by the NFCORE report flow.

Each entry describes the pipeline well enough for the emitter to assemble
launch.yml + params.yml. Pipeline keys here must match the
`reports/templates/nfcore/<key>.json` context files (curated params +
reference_resources), which `seqera/pipeline_params.py` loads by key.
"""
from __future__ import annotations

from typing import Any


NFCORE_PIPELINE_CATALOG: dict[str, dict[str, Any]] = {
    "rnaseq": {
        "repo": "https://github.com/nf-core/rnaseq",
        "default_revision": "3.18.0",
        "description": "Bulk RNA-seq quantification + QC (STAR/Salmon).",
        "common_assays": ["RNA-seq", "bulk RNA", "RNA Sample", "D.SEQ"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        "required_columns": ["sample", "fastq_1", "fastq_2", "strandedness"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^RNA[-_ ]?seq$",
            r"^bulk[-_ ]?RNA([-_ ]?seq)?$",
            r"^RNA[ _]Sample$",
        ],
        "pipeline_kind_description": (
            "Bulk RNA-seq quantification & QC. Needs paired or single-end "
            "FASTQ from RNA-seq libraries."
        ),
    },
    "scrnaseq": {
        "repo": "https://github.com/nf-core/scrnaseq",
        "default_revision": "2.7.1",
        "description": "Single-cell RNA-seq (10x, dropseq, smartseq).",
        "common_assays": ["scRNA-seq", "single cell", "single-cell"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        "required_columns": ["sample", "fastq_1", "fastq_2", "expected_cells"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^scRNA[-_ ]?seq$",
            r"^single[- ]cell([- ]?RNA[- ]?seq)?$",
            r"^10x[- ]?(Genomics)?$",
            r"^smart[- ]?seq2?$",
        ],
        "pipeline_kind_description": (
            "Single-cell RNA-seq. Needs 10x/dropseq/smartseq FASTQ pairs "
            "with expected cell counts."
        ),
    },
    "atacseq": {
        "repo": "https://github.com/nf-core/atacseq",
        "default_revision": "2.1.2",
        "description": "ATAC-seq peak calling and differential accessibility.",
        "common_assays": ["ATAC-seq", "ATAC", "chromatin accessibility"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        "required_columns": ["sample", "fastq_1", "fastq_2", "replicate"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^ATAC[-_ ]?seq$",
            r"^ATAC$",
            r"chromatin[- ]?accessibility",
        ],
        "pipeline_kind_description": (
            "ATAC-seq peak calling + differential accessibility. Needs "
            "ATAC-seq FASTQs."
        ),
    },
    "chipseq": {
        "repo": "https://github.com/nf-core/chipseq",
        "default_revision": "2.0.0",
        "description": "ChIP-seq peak calling against an input/control.",
        "common_assays": ["ChIP-seq", "ChIP"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        "required_columns": [
            "sample", "fastq_1", "fastq_2", "antibody", "control", "control_replicate",
        ],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^ChIP[-_ ]?seq$",
            r"^ChIP$",
        ],
        "pipeline_kind_description": (
            "ChIP-seq peak calling vs input/control. Requires antibody and "
            "control mapping in metadata."
        ),
    },
    "sarek": {
        "repo": "https://github.com/nf-core/sarek",
        "default_revision": "3.4.4",
        "description": "Germline + somatic variant calling from WES/WGS.",
        "common_assays": ["WGS", "WES", "DNA-seq", "variant"],
        "default_genome": "GATK.GRCh38",
        "default_profile": "docker",
        "reference_cli_flags": ["genome", "fasta"],
        "required_columns": [
            "patient", "sample", "lane", "fastq_1", "fastq_2", "sex", "status",
        ],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^WGS$",
            r"^WES$",
            r"^DNA[-_ ]?seq$",
            r"\bvariant",
        ],
        "pipeline_kind_description": (
            "Germline + somatic variant calling from WES/WGS. Needs "
            "patient+sample+sex+status metadata."
        ),
    },
    "methylseq": {
        "repo": "https://github.com/nf-core/methylseq",
        "default_revision": "2.7.1",
        "description": "Bisulfite / methylation sequencing analysis.",
        "common_assays": ["WGBS", "RRBS", "EM-seq", "methylation"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "reference_cli_flags": ["genome", "fasta"],
        "required_columns": ["sample", "fastq_1", "fastq_2"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^WGBS$",
            r"^RRBS$",
            r"^EM[-_ ]?seq$",
            r"methylation",
        ],
        "pipeline_kind_description": (
            "Bisulfite / methylation sequencing analysis. Needs WGBS / RRBS / "
            "EM-seq FASTQ inputs."
        ),
    },
    "ampliseq": {
        "repo": "https://github.com/nf-core/ampliseq",
        "default_revision": "2.10.0",
        "description": "Amplicon (16S/ITS) microbial profiling.",
        "common_assays": ["16S", "ITS", "amplicon", "microbiome"],
        "default_genome": None,
        "default_profile": "docker",
        "reference_cli_flags": [],
        "required_columns": ["sampleID", "forwardReads", "reverseReads"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^16S",
            r"^ITS",
            r"amplicon",
            r"microbiome",
        ],
        "pipeline_kind_description": "16S / ITS amplicon microbial profiling.",
    },
    "fetchngs": {
        "repo": "https://github.com/nf-core/fetchngs",
        "default_revision": "1.12.0",
        "description": "Download FASTQs from SRA/ENA/DDBJ/GEO accessions; emits a downstream-pipeline samplesheet.",
        "common_assays": [],
        "default_genome": None,
        "default_profile": "docker",
        "reference_cli_flags": [],
        "required_columns": ["accession"],
        "samplesheet_input_kind": "accession",
        "accepted_leaf_sample_types": [],
        "accepted_assay_patterns": [],
        "pipeline_kind_description": (
            "Download FASTQs from SRA/ENA/DDBJ/GEO accessions. Input is "
            "accession strings, not NExtSEEK lineage."
        ),
    },
    "seqinspector": {
        "repo": "https://github.com/nf-core/seqinspector",
        "default_revision": "1.1.0",
        "description": "QC-only inspection of sequencing data (FastQC, seqfu, sequali, fastq_screen).",
        # Deliberately broad: seqinspector is assay-agnostic QC, so it is a valid
        # target for ANY D.SEQ cohort rather than one library type.
        "common_assays": ["QC", "sequencing QC", "RNA-seq", "scRNA-seq", "ATAC-seq",
                          "ChIP-seq", "WGS", "WES", "D.SEQ"],
        # No genome. seqinspector's reference options are all optional; without one it
        # skips the BWAMEM2 alignment step and the tools that depend on it, which is
        # exactly what we want — the alignment path would need a bwamem2 index, and
        # the Luria refs tree carries only fasta+gtf (no indices).
        "default_genome": None,
        "reference_cli_flags": [],
        "default_profile": "singularity",
        "required_columns": ["sample", "fastq_1", "fastq_2"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        # Intentionally empty: QC applies to every assay, so gating on assay title
        # would only ever produce false negatives.
        "accepted_assay_patterns": [],
        "pipeline_kind_description": (
            "Quality-control inspection of raw sequencing reads. Needs FASTQ and "
            "nothing else — no genome, no index. Produces MultiQC reports, globally "
            "and per tag group. Use it to triage a cohort before committing to a "
            "full analysis pipeline, or to audit data already in NExtSEEK."
        ),
    },
}


def list_pipeline_keys() -> list[str]:
    return list(NFCORE_PIPELINE_CATALOG.keys())


def get_pipeline_entry(pipeline: str) -> dict[str, Any]:
    """Return the catalog entry for a pipeline; raises KeyError if unknown."""
    key = (pipeline or "").strip().lower()
    if key not in NFCORE_PIPELINE_CATALOG:
        raise KeyError(f"Unknown nf-core pipeline: {pipeline!r}. Known: {list_pipeline_keys()}")
    return NFCORE_PIPELINE_CATALOG[key]


def catalog_for_prompt() -> str:
    """Compact JSON-ish text snippet for inclusion in LLM prompts."""
    lines = []
    for key, entry in NFCORE_PIPELINE_CATALOG.items():
        lines.append(
            f"- {key}: {entry['description']} "
            f"(common assays: {', '.join(entry.get('common_assays') or []) or 'n/a'})"
        )
    return "\n".join(lines)
