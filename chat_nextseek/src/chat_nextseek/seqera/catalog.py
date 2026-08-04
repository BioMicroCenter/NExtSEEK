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
    "smrnaseq": {
        "repo": "https://github.com/nf-core/smrnaseq",
        "default_revision": "2.4.1",
        "description": "Small-RNA / miRNA sequencing quantification and QC.",
        "common_assays": ["smRNA-seq", "small RNA", "miRNA", "microRNA", "miRNA-seq"],
        "default_genome": "GRCh38",
        # No gtf param in the 2.4.1 schema.
        "reference_cli_flags": ["genome", "fasta"],
        "default_profile": "singularity",
        "required_columns": ["sample", "fastq_1"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^sm(all)?[-_ ]?RNA([-_ ]?seq)?$",
            r"^mi(cro)?RNA([-_ ]?seq)?$",
        ],
        "pipeline_kind_description": (
            "Small-RNA/miRNA quantification. Needs (usually single-end) FASTQ from "
            "small-RNA libraries, plus a miRTrace species code."
        ),
    },
    "riboseq": {
        "repo": "https://github.com/nf-core/riboseq",
        "default_revision": "1.2.0",
        "description": "Ribosome profiling (Ribo-seq) analysis.",
        "common_assays": ["Ribo-seq", "riboseq", "ribosome profiling", "ribosome footprinting"],
        "default_genome": "GRCh38",
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        "default_profile": "singularity",
        # `type` is enum-constrained (riboseq|rnaseq|tiseq) and must come from real
        # assay metadata: mislabelling an RNA-seq library as riboseq corrupts the run.
        "required_columns": ["sample", "fastq_1", "strandedness", "type"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^ribo[-_ ]?seq$",
            r"^ribosome[-_ ]?(profiling|footprinting)$",
        ],
        "pipeline_kind_description": (
            "Ribosome profiling. Needs FASTQ plus a per-library type "
            "(riboseq / rnaseq / tiseq) taken from assay metadata, never defaulted."
        ),
    },
    "hlatyping": {
        "repo": "https://github.com/nf-core/hlatyping",
        "default_revision": "2.2.0",
        "description": "Precision HLA typing from NGS data (OptiType).",
        "common_assays": ["HLA typing", "HLA", "immunogenetics", "WES", "WGS", "RNA-seq"],
        # OptiType ships its own HLA reference: no genome needed from us, which makes
        # this one of the few pipelines that runs on the Luria refs tree as-is.
        "default_genome": None,
        "reference_cli_flags": ["genome"],
        "default_profile": "singularity",
        "required_columns": ["sample", "seq_type"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [r"^HLA([-_ ]?typing)?$"],
        "pipeline_kind_description": (
            "HLA typing. Needs FASTQ (or BAM) plus a per-sample seq_type of dna or rna, "
            "derived from the library's assay. Carries its own reference."
        ),
    },
    "hic": {
        "repo": "https://github.com/nf-core/hic",
        "default_revision": "2.1.0",
        "description": "Hi-C chromosome conformation capture: contact maps and TADs.",
        "common_assays": ["Hi-C", "HiC", "chromosome conformation", "3C", "Micro-C"],
        "default_genome": "GRCh38",
        # No gtf param in the 2.1.0 schema.
        "reference_cli_flags": ["genome", "fasta"],
        "default_profile": "singularity",
        "required_columns": ["sample", "fastq_1"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^Hi[-_ ]?C$",
            r"^Micro[-_ ]?C$",
            r"^3C$",
            r"^chromosome[-_ ]conformation.*$",
        ],
        "pipeline_kind_description": (
            "Hi-C contact maps. Needs PAIRED FASTQ (the assay is paired by construction) "
            "and a digestion protocol, or dnase mode."
        ),
    },
    "rnavar": {
        "repo": "https://github.com/nf-core/rnavar",
        "default_revision": "1.3.0",
        "description": "GATK4 short-variant calling from RNA-seq.",
        "common_assays": ["RNA-seq variant calling", "RNA variants", "RNA-seq", "bulk RNA"],
        "default_genome": "GRCh38",
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        "default_profile": "singularity",
        "required_columns": ["sample", "fastq_1"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        # Deliberately narrow: a plain "RNA-seq" cohort should route to rnaseq, not
        # here. rnavar is for an explicit variant-calling ask.
        "accepted_assay_patterns": [
            r"^RNA[-_ ]?seq[-_ ]variant.*$",
            r"^RNA[-_ ]variant[-_ ]calling$",
        ],
        "pipeline_kind_description": (
            "Variant calling FROM RNA-seq (not DNA — use sarek for that). Base "
            "recalibration and variant annotation are off by default: the required "
            "dbsnp / known_indels / snpEff caches are not provisioned on Luria."
        ),
    },
    "crisprseq": {
        "repo": "https://github.com/nf-core/crisprseq",
        "default_revision": "2.3.0",
        "description": "CRISPR editing-efficiency analysis (targeted) and pooled sgRNA screens (screening).",
        "common_assays": ["CRISPR", "CRISPR-seq", "amplicon sequencing", "gene editing",
                          "knockout", "knock-in", "sgRNA screen"],
        # No genome: crisprseq works against the supplied amplicon, not a whole-genome
        # reference, so it needs nothing from the Luria refs tree.
        "default_genome": None,
        "reference_cli_flags": [],
        "default_profile": "singularity",
        # Only these two are required by assets/schema_input.json. reference /
        # protospacer / template are OPTIONAL per-row columns, and the run-level
        # --protospacer / --reference_fasta override them — which is what makes a
        # shared-guide cohort answerable with a handful of questions.
        "required_columns": ["sample", "fastq_1"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^CRISPR([-_ ]?seq)?$",
            r"^gene[-_ ]editing$",
            r"^(sg)?RNA[-_ ]screen$",
        ],
        "pipeline_kind_description": (
            "CRISPR amplicon analysis. Needs FASTQ plus a guide sequence and amplicon "
            "reference, which the user supplies at launch — they are not recorded in "
            "NExtSEEK and must never be guessed."
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
