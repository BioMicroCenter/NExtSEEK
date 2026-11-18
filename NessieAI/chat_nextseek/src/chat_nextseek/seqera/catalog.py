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
    "rnafusion": {
        "repo": "https://github.com/nf-core/rnafusion",
        "default_revision": "4.1.3",
        "description": "Detect gene fusions (chimeric transcripts) from RNA-seq. Human-focused.",
        "common_assays": ["RNA-seq", "fusion", "cancer", "translocation"],
        "default_genome": "GRCh38",
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        "default_profile": "singularity",
        "required_columns": ["sample", "fastq_1", "fastq_2", "strandedness"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^RNA([-_ ]?seq)?$",
            r"^fusion",
        ],
        "pipeline_kind_description": (
            "Gene-fusion detection from RNA-seq reads, usually in cancer. Human only "
            "(needs a large prebuilt reference: arriba, STAR-Fusion, FusionCatcher, COSMIC)."
        ),
    },
    "rnasplice": {
        "repo": "https://github.com/nf-core/rnasplice",
        "default_revision": "1.0.4",
        "description": "Differential splicing / isoform usage between two named groups of samples.",
        "common_assays": ["RNA-seq", "splicing", "isoform", "alternative splicing"],
        "default_genome": "GRCh38",
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        "default_profile": "singularity",
        "required_columns": ["sample", "fastq_1", "fastq_2", "strandedness", "condition"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^RNA([-_ ]?seq)?$",
        ],
        "pipeline_kind_description": (
            "Differential splicing between two defined groups. Needs a 'condition' column "
            "grouping samples and a contrasts file naming the comparison."
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
        "required_columns": ["sample", "fastq_1", "replicate"],
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
        "default_revision": "2.1.0",
        "description": "ChIP-seq peak calling against an input/control.",
        "common_assays": ["ChIP-seq", "ChIP"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        "required_columns": ["sample", "fastq_1"],
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
    "cutandrun": {
        "repo": "https://github.com/nf-core/cutandrun",
        "default_revision": "3.2.2",
        "description": "CUT&RUN and CUT&Tag peak calling with spike-in and IgG-control normalisation.",
        "common_assays": ["CUT&RUN", "CUT&Tag", "CUTRUN", "CUTTAG"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        # assets/schema_input.json at 3.2.2 requires all five; bin/check_samplesheet.py reads
        # them by position and refuses single-end rows.
        "required_columns": ["group", "replicate", "fastq_1", "fastq_2", "control"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^CUT[-_ &]?(and|&)?[-_ ]?RUN$",
            r"^CUT[-_ &]?(and|&)?[-_ ]?Tag$",
        ],
        "pipeline_kind_description": (
            "CUT&RUN / CUT&Tag peak calling, paired-end only. Each sample belongs to a "
            "group (its target) with numbered replicates; an IgG control group is named "
            "in the control column, or use_control is off."
        ),
    },
    "sarek": {
        "repo": "https://github.com/nf-core/sarek",
        "default_revision": "3.10.0",
        "description": "Germline and somatic variant calling from WGS, WES or targeted DNA sequencing.",
        "common_assays": ["WGS", "WES", "DNA-seq", "variant", "exome", "targeted capture"],
        # sarek's own key for the GATK GRCh38 bundle; on Luria the launcher maps the
        # species-resolved GRCh38 to it (run_script.LURIA_PIPELINE_LAUNCH genome_alias).
        "default_genome": "GATK.GRCh38",
        "default_profile": "singularity",
        "reference_cli_flags": ["genome", "fasta"],
        # schema_input.json requires patient+sample; lane and fastq_1 are required for the
        # FASTQ input this catalog supports (usage.md, "--step mapping").
        "required_columns": ["patient", "sample", "lane", "fastq_1"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^WGS$",
            r"^WES$",
            r"^WXS$",
            r"^DNA[-_ ]?seq$",
            r"\bvariant",
            r"\bexome\b",
        ],
        "pipeline_kind_description": (
            "Germline and somatic short-variant and structural-variant calling from DNA "
            "(WGS, WES, targeted capture). Somatic tools need a tumour and a matched normal "
            "per patient; variant annotation is off because its caches are not staged."
        ),
    },
    "methylseq": {
        "repo": "https://github.com/nf-core/methylseq",
        "default_revision": "4.2.0",
        "description": "Bisulfite / methylation sequencing analysis.",
        "common_assays": ["WGBS", "RRBS", "EM-seq", "methylation"],
        "default_genome": "GRCh38",
        "default_profile": "docker",
        "reference_cli_flags": ["genome", "fasta"],
        "required_columns": ["sample", "fastq_1"],
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
    "fastquorum": {
        "repo": "https://github.com/nf-core/fastquorum",
        "default_revision": "2.0.0",
        "description": "UMI consensus reads (fgbio): duplex or single-strand consensus BAMs from UMI-tagged DNA libraries.",
        "common_assays": ["duplex sequencing", "Duplex-Seq", "UMI", "consensus", "error-corrected sequencing"],
        "default_genome": "GRCh38",
        "default_profile": "singularity",
        # 2.0.0 declares genome, fasta and fasta_fai, and builds the BWA index from the fasta.
        "reference_cli_flags": ["genome", "fasta"],
        # assets/schema_input.json at 2.0.0 requires these three; fastq_2 to fastq_4 and
        # library_id, lane, flowcell, umi_file are optional.
        "required_columns": ["sample", "fastq_1", "read_structure"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"duplex",
            r"\bUMI\b",
            r"consensus",
        ],
        "pipeline_kind_description": (
            "Consensus reads from UMI-tagged DNA libraries (fgbio best practice): groups reads "
            "by UMI and position, calls a duplex or single-strand consensus, and aligns it. "
            "Every row needs the library's fgbio read structure, which says where the UMI sits."
        ),
    },
    "variantbenchmarking": {
        "repo": "https://github.com/nf-core/variantbenchmarking",
        "default_revision": "1.6.1",
        "description": "Scores existing variant calls against a truth set (GIAB, SEQC2): precision, recall and F1 per caller.",
        "common_assays": ["variant benchmarking", "truth set", "Genome in a Bottle", "GIAB", "NA12878",
                          "HG001", "precision recall", "hap.py", "vcfeval"],
        # Its runs use the GATK GRCh38 bundle sarek calls on, so the test VCFs' contigs match the
        # FASTA (run_script.LURIA_PIPELINE_LAUNCH genome_alias, as for sarek).
        "default_genome": "GATK.GRCh38",
        "default_profile": "singularity",
        # 1.6.1 nextflow_schema.json declares genome and fasta (and requires fai, passed as a
        # resource param), and no gtf.
        "reference_cli_flags": ["genome", "fasta"],
        # assets/schema_input.json at 1.6.1 requires id and caller, and test_vcf or test_regions
        # (anyOf dependentRequired); id is unique (uniqueEntries). docs/usage.md: the first three
        # columns are id,test_vcf,caller (emitter PIPELINE_HEADER_PREFIX writes test_vcf second).
        # This catalog supports test_vcf (A.VCF) only.
        "required_columns": ["id", "caller"],
        "samplesheet_input_kind": "vcf",
        "accepted_leaf_sample_types": ["A.VCF"],
        "accepted_assay_patterns": [
            r"variant call",
            r"\bVCF\b",
            r"benchmark",
        ],
        "pipeline_kind_description": (
            "Benchmarks variant calls that already exist (A.VCF records, e.g. from sarek) against a "
            "truth set such as Genome in a Bottle HG001 for NA12878: precision, recall and F1 per "
            "caller and variant class. Calls nothing itself; the truth VCF and its confident-regions "
            "BED are paths the user gives, since none is staged."
        ),
    },
    "oncoanalyser": {
        "repo": "https://github.com/nf-core/oncoanalyser",
        "default_revision": "3.0.0",
        "description": "Whole cancer-genome analysis of WGS tumour/normal pairs (Hartwig WiGiTS): somatic SNVs, indels, SVs, copy number, purity and ploidy, drivers, HLA, signatures, HRD, tissue of origin and a report.",
        "common_assays": ["WGS", "tumour/normal", "tumor/normal", "cancer genome", "somatic", "purity", "ploidy"],
        # oncoanalyser's own key for the Hartwig GRCh38 bundle (lib/Constants.groovy
        # GENOMES_SUPPORTED: GRCh37_hmf, GRCh38_hmf); the launcher maps the species-resolved
        # GRCh38 to it (run_script.LURIA_PIPELINE_LAUNCH genome_alias).
        "default_genome": "GRCh38_hmf",
        "default_profile": "singularity",
        # 3.0.0 declares genome but no fasta or gtf (nextflow_schema.json): its genome files are
        # ref_data_genome_{fasta,fai,dict,img,bwamem2_index,gridss_index} and the WiGiTS bundle
        # ref_data_hmf_data_path, all defaulting to data.oncoanalyser.com URLs (conf/hmf_genomes.config,
        # nextflow.config ref_data_base). The launcher passes local copies (resource_params);
        # configure_run refuses the run while no GRCh38_hmf bundle is registered.
        "reference_cli_flags": ["genome"],
        # assets/schema_input.json at 3.0.0 requires these seven. info is optional there, but
        # lib/Utils.groovy parseInput requires library_id and lane in it on every FASTQ row,
        # and filepath holds "<R1>;<R2>" (exactly two paths). Long format: one row per FASTQ pair.
        "required_columns": ["group_id", "subject_id", "sample_id", "sample_type", "sequence_type",
                             "filetype", "filepath"],
        # The reads are resolved as FASTQ; the emitter writes them in oncoanalyser's long format
        # (seqera/emitter.py _oncoanalyser_rows). GDC/TCGA BAMs are not accepted: usage.md
        # "Sequencing technologies" requires alignments to the bundled genome with soft-clipped
        # supplementary reads and mate CIGARs.
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        # Whole genome only: exome or panel data in wgts mode mis-fits copy number and purity,
        # and targeted mode needs a panel's resources (only TSO500 on GRCh37 is built in).
        "accepted_assay_patterns": [
            r"^WGS$",
            r"^DNA[-_ ]?seq$",
            r"whole[-_ ]?genome",
            r"\bsomatic\b",
        ],
        "pipeline_kind_description": (
            "Whole cancer-genome analysis of a whole-genome tumour and its matched normal with the "
            "Hartwig WiGiTS tools: somatic SNVs, indels and SVs, copy number with purity and ploidy, "
            "drivers, HLA, signatures, HRD, tissue of origin and an ORANGE report. Human only, FASTQ "
            "only here; its GRCh38_hmf reference bundle is not staged on the cluster yet."
        ),
    },
    "createpanelrefs": {
        "repo": "https://github.com/nf-core/createpanelrefs",
        "default_revision": "1.1.0",
        "description": "Panels of normals from many normal alignments: a GATK Mutect2 somatic panel of normals and a CNVkit copy-number reference.",
        "common_assays": ["panel of normals", "PoN", "normal", "WGS", "WES", "exome"],
        # sarek's key for the GATK GRCh38 bundle, whose fasta, fai and dict are staged
        # (run_script.LURIA_GENOME_RESOURCES); 1.1.0's conf/igenomes.config names the same key.
        # A panel meant for sarek's Mutect2 must be built on sarek's reference.
        "default_genome": "GATK.GRCh38",
        "default_profile": "singularity",
        # 1.1.0 declares genome, fasta, fai and dict, and no gtf (nextflow_schema.json).
        "reference_cli_flags": ["genome", "fasta"],
        # assets/schema_input.json at 1.1.0: sample required, oneOf bam or cram, no
        # additionalProperties: false. The bam branch's mapped/index/file_type become bam/bai or
        # cram/crai by the row's file_type (seqera/emitter.py _createpanelrefs_rows).
        "required_columns": ["sample"],
        # Alignments in, like pacvar: D.SEQ leaves whose registered data file is a BAM or CRAM
        # (TCGA's GDC normals). A FASTQ-only cohort must be aligned first.
        "samplesheet_input_kind": "bam",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^WGS$",
            r"^WES$",
            r"^WXS$",
            r"\bexome\b",
            r"^DNA[-_ ]?seq$",
            r"panel of normals",
        ],
        "pipeline_kind_description": (
            "Builds reference panels from many normal (non-tumour) DNA alignments, BAM or CRAM: a "
            "Mutect2 somatic panel of normals for later somatic calling and a CNVkit pooled "
            "copy-number reference. Calls nothing for any one sample. The FASTA must be the one the "
            "alignments were made against."
        ),
    },
    "rnadnavar": {
        "repo": "https://github.com/nf-core/rnadnavar",
        "default_revision": "1.0.0",
        "description": "Somatic SNVs and indels from tumour RNA-seq together with the same patient's normal DNA (and tumour DNA).",
        "common_assays": ["RNA-seq", "WXS", "WES", "WGS", "somatic mutation", "tumour/normal", "tumor/normal"],
        # Runs on the GATK GRCh38 bundle (refs/GATK.GRCh38), as sarek does: dbSNP, known
        # indels, gnomAD and the PoN share its contigs. rnadnavar 1.0.0's own conf/igenomes.config
        # has no GATK.GRCh38 key, so the run needs igenomes_ignore (main.nf genomeExistsError via
        # utils_nfcore_rnadnavar_pipeline); with it the key is only a label.
        "default_genome": "GATK.GRCh38",
        "default_profile": "singularity",
        # 1.0.0 declares genome, fasta and gtf; STAR_GENOMEGENERATE needs the GTF
        # (subworkflows/local/prepare_genome/main.nf), and the GATK bundle has none.
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        # assets/schema_input.json requires patient+sample only, but samplesheet_to_channel
        # (subworkflows/local/samplesheet_to_channel/main.nf) takes a FASTQ row only with lane
        # and fastq_2, a BAM row only with lane, and status is written on every row because the
        # schema's default (1) and docs/usage.md's (0) disagree: emitter._rnadnavar_rows writes
        # status and lane on every row.
        "required_columns": ["patient", "sample"],
        # NEW input kind (emitter_needs): FASTQ pairs when the D.SEQ has local FASTQs, else
        # its BAM (+ .bai) under columns bam/bai; usage.md "Starting from different steps": mapping.
        "samplesheet_input_kind": "fastq_or_bam",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"\bsomatic\b",
            r"tumou?r[-_ /]?normal",
            r"\bRNA\b.*\bDNA\b|\bDNA\b.*\bRNA\b",
        ],
        "pipeline_kind_description": (
            "Somatic mutations of one patient's tumour from its RNA-seq and DNA, each tumour "
            "(DNA status 1, RNA status 2) called against the patient's normal DNA (status 0) "
            "with Strelka2 and Mutect2, then normalised, merged into a consensus MAF and filtered, "
            "with RNA-specific filters on the RNA tumour. Needs a DNA normal per patient; an RNA "
            "library from normal tissue cannot be used. Annotation, SAGE and the HISAT2 "
            "realignment pass are off: their resources are not staged."
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
    "nanoseq": {
        "repo": "https://github.com/nf-core/nanoseq",
        "default_revision": "3.1.0",
        "description": "Oxford Nanopore QC, demultiplexing and alignment.",
        "common_assays": ["Nanopore", "ONT", "long read", "long-read sequencing", "MinION", "PromethION"],
        # Declares no genome/fasta/gtf at 3.1.0.
        "default_genome": None,
        "reference_cli_flags": [],
        "default_profile": "singularity",
        "required_columns": ["sample", "fastq_1"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^nanopore$", r"^ONT$", r"^long[-_ ]read.*$", r"^(min|prometh)ion$",
        ],
        "pipeline_kind_description": (
            "Oxford Nanopore long-read QC and demultiplexing. Needs single-file FASTQ "
            "plus a protocol (DNA / cDNA / directRNA) the user supplies. Verify the "
            "cohort really is Nanopore first — an Illumina cohort here yields nonsense, "
            "not an error."
        ),
    },
    "mag": {
        "repo": "https://github.com/nf-core/mag",
        "default_revision": "5.5.0",
        "description": "Metagenome assembly and binning (de novo).",
        "common_assays": ["metagenomics", "metagenome", "shotgun metagenomics", "microbiome", "WGS"],
        # Assembles de novo: no genome to resolve, and no genome/fasta/gtf param.
        "default_genome": None,
        "reference_cli_flags": [],
        "default_profile": "singularity",
        # NOT fastq_1/fastq_2 — mag names them short_reads_1/2. PIPELINE_COLUMN_ALIASES
        # in the emitter does the rename, the same mechanism ampliseq already uses.
        "required_columns": ["sample", "group"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^meta[-_ ]?genom(e|ics)$", r"^shotgun[-_ ]metagenom.*$", r"^microbiome$",
        ],
        "pipeline_kind_description": (
            "De novo metagenome assembly and binning. Needs short-read FASTQ and a "
            "co-assembly grouping. Bin QC and GTDB-Tk are off by default — both want "
            "databases that are not provisioned on Luria."
        ),
    },
    # --- Added 2026-08-05 after the full schema census. -----------------------------
    # These eight are config-only against their pinned schemas but have NO matching data
    # in NExtSEEK today (no long-read platform of any kind; no bacterial isolate
    # sequencing; 19 viral mentions in 51,359 samples). They are catalogued so the
    # capability exists the day such a cohort is registered, NOT because anyone can run
    # them now. Do not count them as verified capability — see the Luria table in
    # docs/nfcore-capability-expansion.md, where every one is marked "no data".
    "bacass": {
        "repo": "https://github.com/nf-core/bacass",
        "default_revision": "2.6.1",
        "description": "Bacterial isolate assembly, annotation and QC (short, long or hybrid).",
        "common_assays": ["bacterial WGS", "isolate sequencing", "bacterial genome", "assembly"],
        "default_genome": None,
        # Declares none of genome/fasta/gtf; it assembles de novo and takes an optional
        # reference_fasta of its own name instead.
        "reference_cli_flags": [],
        # Post-alias: bacass uses ID/R1/R2, not sample/fastq_1/fastq_2.
        "required_columns": ["ID", "R1", "R2"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^bacterial[-_ ]?(WGS|genome|isolate)$",
            r"^isolate[-_ ]sequencing$",
        ],
        "pipeline_kind_description": (
            "Bacterial ISOLATE assembly — one bacterial genome per sample. Not "
            "metagenomics (use mag) and not amplicon (use ampliseq). NExtSEEK holds no "
            "isolate sequencing today: BAC samples have no D.SEQ children."
        ),
    },
    "bactmap": {
        "repo": "https://github.com/nf-core/bactmap",
        "default_revision": "1.0.0",
        "description": "Map bacterial reads to a reference and call variants (phylogeny from a pseudogenome alignment).",
        "common_assays": ["bacterial WGS", "isolate sequencing", "bacterial variant calling"],
        "default_genome": None,
        # 1.0.0 declares NO genome/fasta/gtf. Its reference arrives via its own
        # `--reference` param, which is required and has no default -> elicited.
        "reference_cli_flags": [],
        # 1.0.0 ships no assets/schema_input.json; docs/usage.md at the tag specifies
        # a 3-column sheet.
        "required_columns": ["sample", "fastq_1", "fastq_2"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^bacterial[-_ ]?(WGS|variant[-_ ]calling)$",
            r"^isolate[-_ ]sequencing$",
        ],
        "pipeline_kind_description": (
            "Reference-based bacterial mapping and variant calling, for outbreak / "
            "phylogenetic work on isolates of ONE species. The user supplies the "
            "reference genome at launch; there is no iGenomes key for it."
        ),
    },
    "pacvar": {
        "repo": "https://github.com/nf-core/pacvar",
        "default_revision": "1.1.0",
        "description": "PacBio long-read processing for WGS and PureTarget repeat expansions.",
        "common_assays": ["PacBio", "HiFi", "long read", "long-read sequencing", "Revio", "Sequel"],
        "default_genome": None,
        "reference_cli_flags": ["genome", "fasta"],
        # PacBio delivers unaligned reads AS BAM, so this is raw data (D.SEQ) that
        # happens to be a bam — which is why input kind and sample type are independent
        # knobs. Post-alias columns: mapped -> bam, index -> pbi.
        "required_columns": ["sample", "bam"],
        "samplesheet_input_kind": "bam",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^PacBio([-_ ]?HiFi)?$", r"^HiFi$", r"^(revio|sequel)$",
        ],
        "pipeline_kind_description": (
            "PacBio long-read analysis. Input is the unaligned HiFi BAM the instrument "
            "produces — NOT an Illumina alignment BAM, which would be meaningless here. "
            "NExtSEEK holds zero long-read samples of any platform today."
        ),
    },
    "viralrecon": {
        "repo": "https://github.com/nf-core/viralrecon",
        "default_revision": "3.0.0",
        "description": "Viral genome assembly and intrahost / low-frequency variant calling.",
        # Deliberately NOT bare "amplicon" or "metagenomic": those are viralrecon's two
        # PROTOCOL modes, not assay names, and amplicon is 57% of the D.SEQ we hold.
        # Listing them here would nominate viralrecon for every 16S cohort in the
        # database. The protocol is asked for once the virus is established.
        "common_assays": ["viral", "virus", "virome", "SARS-CoV-2", "viral genome", "viral amplicon"],
        "default_genome": None,
        "reference_cli_flags": ["genome", "fasta"],
        "required_columns": ["sample", "fastq_1", "fastq_2"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^viral([-_ ]?(seq|genome|amplicon))?$",
            r"^SARS[-_ ]?CoV[-_ ]?2$",
        ],
        "pipeline_kind_description": (
            "Viral genome reconstruction and variant calling. Needs the platform and the "
            "library protocol from the user — both change the analysis and neither is "
            "recorded. NExtSEEK holds essentially no viral data (19 mentions in 51,359 "
            "samples)."
        ),
    },
    "viralmetagenome": {
        "repo": "https://github.com/nf-core/viralmetagenome",
        "default_revision": "1.1.3",
        "description": "Untargeted viral genome reconstruction with iSNV detection from metagenomes.",
        "common_assays": ["viral metagenomics", "virome", "metagenomics", "shotgun metagenomics"],
        "default_genome": None,
        "reference_cli_flags": [],
        "required_columns": ["sample", "fastq_1"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [r"^vir(al|ome)[-_ ]?metagenom.*$", r"^virome$"],
        "pipeline_kind_description": (
            "Reconstructs viral genomes de novo from a metagenomic library, without "
            "targeting a known virus. Use viralrecon instead when the virus is known."
        ),
    },
    "viralintegration": {
        "repo": "https://github.com/nf-core/viralintegration",
        "default_revision": "0.1.1",
        "description": "Identify viral integration sites in host genomes via chimeric reads.",
        "common_assays": ["viral integration", "HPV", "HBV", "insertional mutagenesis", "RNA-seq", "WGS"],
        "default_genome": "GRCh38",
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        "required_columns": ["sample", "fastq_1"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [r"^viral[-_ ]integration$", r"^(HPV|HBV)([-_ ]integration)?$"],
        "pipeline_kind_description": (
            "Finds where a virus has integrated into the HOST genome, so it needs a host "
            "reference as well as a viral one. Pinned at 0.1.1 — the only release, and an "
            "early one."
        ),
    },
    "magmap": {
        "repo": "https://github.com/nf-core/magmap",
        "default_revision": "1.1.0",
        "description": "Map metagenomic reads against a large, user-supplied collection of genomes.",
        "common_assays": ["metagenomics", "shotgun metagenomics", "microbiome", "metagenome"],
        "default_genome": None,
        "reference_cli_flags": [],
        "required_columns": ["sample", "fastq_1"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^meta[-_ ]?genom(e|ics)$", r"^shotgun[-_ ]metagenom.*$",
        ],
        "pipeline_kind_description": (
            "Maps reads to a KNOWN collection of genomes, rather than assembling new ones "
            "(that is mag). The collection is the whole point and the user must name it — "
            "no genome set is provisioned on Luria."
        ),
    },
    "metatdenovo": {
        "repo": "https://github.com/nf-core/metatdenovo",
        "default_revision": "1.4.0",
        "description": "De novo assembly and annotation of metatranscriptomes and metagenomes.",
        "common_assays": ["metatranscriptomics", "metatranscriptome", "metagenomics", "microbiome"],
        "default_genome": None,
        "reference_cli_flags": [],
        "required_columns": ["sample", "fastq_1"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^meta[-_ ]?transcriptom(e|ics)$", r"^meta[-_ ]?genom(e|ics)$",
        ],
        "pipeline_kind_description": (
            "Assembles and annotates a metatranscriptome (or metagenome) de novo. The ORF "
            "caller must match the domain of life being studied — prodigal/prokka for "
            "prokaryotes, transdecoder for eukaryotes — so the user is asked."
        ),
    },
    "denovotranscript": {
        "repo": "https://github.com/nf-core/denovotranscript",
        "default_revision": "1.2.1",
        "description": "De novo transcriptome assembly and quantification from paired-end bulk RNA-seq.",
        "common_assays": ["RNA-seq", "bulk RNA", "de novo transcriptome", "transcriptome assembly",
                          "non-model organism"],
        "default_genome": None,
        # 1.2.1 does declare all three, so passing them will not abort the run. They are
        # only consulted for optional evaluation — the assembly itself is reference-free.
        "reference_cli_flags": ["genome", "fasta", "gtf"],
        "required_columns": ["sample", "fastq_1", "fastq_2"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        # Narrow on purpose: a plain RNA-seq cohort belongs in rnaseq. This is for an
        # explicit de novo ask, which is what you need when there is no good reference.
        "accepted_assay_patterns": [
            r"^de[-_ ]?novo[-_ ]transcriptome.*$",
            r"^transcriptome[-_ ]assembly$",
        ],
        "pipeline_kind_description": (
            "Assembles a transcriptome from RNA-seq reads WITHOUT a reference, which is "
            "what you want for an organism with no good genome. If the organism has one, "
            "rnaseq gives a better-constrained answer."
        ),
    },
    "detaxizer": {
        "repo": "https://github.com/nf-core/detaxizer",
        "default_revision": "1.3.0",
        "description": "Identify and optionally remove contaminating sequences (host reads by default) from raw data.",
        "common_assays": ["contamination screening", "host removal", "decontamination", "QC"],
        "default_genome": None,
        "reference_cli_flags": ["genome"],
        # Post-alias: detaxizer names the read columns short_reads_fastq_1/2.
        "required_columns": ["sample", "short_reads_fastq_1", "short_reads_fastq_2"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        # Like seqinspector, this applies to any library, so gating on assay title would
        # only ever produce false negatives.
        "accepted_assay_patterns": [],
        "pipeline_kind_description": (
            "Screens reads for contaminating taxa — by default Homo sapiens — and can "
            "remove them. Applies to any library type. Useful before depositing data "
            "publicly, or when a library is suspected of host or index contamination."
        ),
    },
    "fastqrepair": {
        "repo": "https://github.com/nf-core/fastqrepair",
        "default_revision": "1.0.0",
        "description": "Recover corrupted gzipped FASTQ files: drop or fix non-compliant reads, resolve unpaired and desynchronised reads.",
        "common_assays": ["QC", "data repair", "corrupted FASTQ"],
        "default_genome": None,
        "reference_cli_flags": [],
        "required_columns": ["sample", "fastq_1", "fastq_2"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [],
        "pipeline_kind_description": (
            "A repair utility, not an analysis: it fixes truncated or desynchronised "
            "FASTQ files so another pipeline can read them. Reach for it when a run has "
            "failed on unreadable input, not as a first step by default."
        ),
    },
    "genomeassembler": {
        "repo": "https://github.com/nf-core/genomeassembler",
        "default_revision": "1.1.0",
        "description": "Assembly and scaffolding of haploid / unphased genomes from long ONT or PacBio HiFi reads.",
        "common_assays": ["genome assembly", "long read", "Nanopore", "ONT", "PacBio", "HiFi"],
        "default_genome": None,
        "reference_cli_flags": [],
        # `sample` is the only schema-required column: the read column depends on the
        # platform and is filled per row by PIPELINE_PLATFORM_COLUMNS in the emitter.
        "required_columns": ["sample"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^genome[-_ ]assembly$", r"^de[-_ ]?novo[-_ ]genome.*$",
        ],
        "pipeline_kind_description": (
            "Assembles a genome from LONG reads. The read column is chosen per sample "
            "from the platform in its metadata — ontreads for Nanopore, hifireads for "
            "PacBio. A short-read-only cohort is not a valid input and will land in no "
            "read column at all, which is deliberate."
        ),
    },
    "pathogensurveillance": {
        "repo": "https://github.com/nf-core/pathogensurveillance",
        "default_revision": "1.1.0",
        "description": "Population genomics surveillance of pathogens: identification, variants and reports per group.",
        "common_assays": ["pathogen surveillance", "outbreak", "population genomics",
                          "bacterial WGS", "isolate sequencing"],
        "default_genome": None,
        "reference_cli_flags": [],
        # Post-alias: sample -> sample_id, fastq_1 -> path, fastq_2 -> path_2.
        # sequence_type is stamped per row from the sample's platform metadata.
        "required_columns": ["sample_id", "path", "path_2"],
        "samplesheet_input_kind": "fastq",
        "accepted_leaf_sample_types": ["D.SEQ"],
        "accepted_assay_patterns": [
            r"^pathogen[-_ ]surveillance$", r"^outbreak.*$",
        ],
        "pipeline_kind_description": (
            "Surveillance across a SET of pathogen isolates — it compares them and "
            "reports per group, so it wants a cohort rather than one sample. References "
            "are selected automatically from NCBI. The grouping is a curation decision: "
            "report_group_ids should come from the cohort field the user asked about."
        ),
    },
    "bamtofastq": {
        "repo": "https://github.com/nf-core/bamtofastq",
        "default_revision": "2.2.1",
        "description": "Convert aligned BAM/CRAM back to FASTQ, with QC on the extracted reads.",
        "common_assays": ["alignment", "BAM", "CRAM", "aligned reads", "realignment"],
        # The FIRST catalog entry whose cohort leaves are ANALYSIS records, not raw data:
        # a BAM is something a previous run produced, so its NExtSEEK home is A.ALN. This
        # is the pattern differentialabundance needs — a registered output feeding a run.
        "accepted_leaf_sample_types": ["A.ALN"],
        "samplesheet_input_kind": "bam",
        # Only needed to decode a CRAM (which carries no sequence of its own). A BAM
        # cohort runs with no reference at all; every A.ALN we hold is DataType BAM.
        "default_genome": None,
        # 2.2.1 declares genome/fasta/fasta_fai — and NO gtf.
        "reference_cli_flags": ["genome", "fasta"],
        # Post-alias names: the emitter renames sample -> sample_id for this pipeline.
        # `index` is optional per assets/schema_input.json and is emitted only when the
        # sample's metadata actually carries a .bai/.crai.
        "required_columns": ["sample_id", "mapped", "file_type"],
        "accepted_assay_patterns": [],
        "pipeline_kind_description": (
            "Convert aligned BAM/CRAM back to FASTQ. Its inputs are A.ALN alignment "
            "records, NOT D.SEQ raw data — resolve the cohort to the alignments, not to "
            "the reads they came from. A CRAM cohort additionally needs the reference "
            "the CRAM was compressed against; a BAM cohort needs no reference."
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
    """Compact JSON-ish text snippet for inclusion in LLM prompts.

    Each line carries the sample types the pipeline's cohort resolves to. Almost
    every entry is D.SEQ, which is why it was safe to leave implicit — until
    bamtofastq, whose leaves are A.ALN alignment records. Without this the agent
    would resolve a D.SEQ cohort, get zero leaves back, and have no way to tell
    why.
    """
    lines = []
    for key, entry in NFCORE_PIPELINE_CATALOG.items():
        leaves = entry.get("accepted_leaf_sample_types") or []
        inputs = f"input samples: {', '.join(leaves)}" if leaves else "input: raw archive accessions"
        lines.append(
            f"- {key}: {entry['description']} "
            f"({inputs}; common assays: {', '.join(entry.get('common_assays') or []) or 'n/a'})"
        )
    return "\n".join(lines)
