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
