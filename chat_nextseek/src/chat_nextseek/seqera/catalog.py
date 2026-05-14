"""Curated catalog of nf-core pipelines supported by the NFCORE report flow.

Each entry describes the pipeline well enough for the seqera_agent to draft
sensible default params, and the emitter to assemble launch.yml + params.yml.
Pipeline keys here must match `reports/nfcore/<key>.json` filenames so the
nf-core wizard's dynamic pipeline list and these defaults stay in sync.
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
        "required_columns": ["sample", "fastq_1", "fastq_2", "strandedness"],
    },
    "scrnaseq": {
        "repo": "https://github.com/nf-core/scrnaseq",
        "default_revision": "2.7.1",
        "description": "Single-cell RNA-seq (10x, dropseq, smartseq).",
        "common_assays": ["scRNA-seq", "single cell", "single-cell"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "required_columns": ["sample", "fastq_1", "fastq_2", "expected_cells"],
    },
    "atacseq": {
        "repo": "https://github.com/nf-core/atacseq",
        "default_revision": "2.1.2",
        "description": "ATAC-seq peak calling and differential accessibility.",
        "common_assays": ["ATAC-seq", "ATAC", "chromatin accessibility"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "required_columns": ["sample", "fastq_1", "fastq_2", "replicate"],
    },
    "chipseq": {
        "repo": "https://github.com/nf-core/chipseq",
        "default_revision": "2.0.0",
        "description": "ChIP-seq peak calling against an input/control.",
        "common_assays": ["ChIP-seq", "ChIP"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "required_columns": [
            "sample", "fastq_1", "fastq_2", "antibody", "control", "control_replicate",
        ],
    },
    "sarek": {
        "repo": "https://github.com/nf-core/sarek",
        "default_revision": "3.4.4",
        "description": "Germline + somatic variant calling from WES/WGS.",
        "common_assays": ["WGS", "WES", "DNA-seq", "variant"],
        "default_genome": "GATK.GRCh38",
        "default_profile": "docker",
        "required_columns": [
            "patient", "sample", "lane", "fastq_1", "fastq_2", "sex", "status",
        ],
    },
    "methylseq": {
        "repo": "https://github.com/nf-core/methylseq",
        "default_revision": "2.7.1",
        "description": "Bisulfite / methylation sequencing analysis.",
        "common_assays": ["WGBS", "RRBS", "EM-seq", "methylation"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "required_columns": ["sample", "fastq_1", "fastq_2"],
    },
    "ampliseq": {
        "repo": "https://github.com/nf-core/ampliseq",
        "default_revision": "2.10.0",
        "description": "Amplicon (16S/ITS) microbial profiling.",
        "common_assays": ["16S", "ITS", "amplicon", "microbiome"],
        "default_genome": None,
        "default_profile": "docker",
        "required_columns": ["sampleID", "forwardReads", "reverseReads"],
    },
    "fetchngs": {
        "repo": "https://github.com/nf-core/fetchngs",
        "default_revision": "1.12.0",
        "description": "Download FASTQs from SRA/ENA/DDBJ/GEO accessions; emits a downstream-pipeline samplesheet.",
        "common_assays": [],
        "default_genome": None,
        "default_profile": "docker",
        "required_columns": ["accession"],
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
