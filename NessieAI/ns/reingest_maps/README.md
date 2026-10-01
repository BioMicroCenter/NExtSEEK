# Reingest output maps

One `<pipeline>.outputs.json` per nf-core pipeline. Committed, code-reviewed,
read-only at runtime. `NessieAI/ns/reingest/maps.py` defines the schema and
`NessieAI/tests/ns/reingest/test_map_contract.py` gates every file in CI.

## What a map may contain

- `harvest_globs` — extra files this pipeline needs, on top of the generic
  `pipeline_info/` and MultiQC allowlist in `reingest/harvest.py`.
- `accepts_parent_types` — the sample types this pipeline's samplesheet can
  legitimately point back to as a PARENT, e.g. `["D.SEQ", "A.ALN"]` for a
  pipeline whose input schema accepts a `bam` column alongside `fastq_1`.
  Scopes the fastq/file-path fallback lookup
  (`nextseek_api.services.reingest_lookups.uids_by_primary_data`) that runs
  when a run has no Nessie launch record to resolve a sample's UID by name.
  Defaults to `["D.SEQ"]` — the lookup's original, sole scope — so an
  undeclared or unverified map keeps searching exactly what it always
  searched rather than silently widening. Set it only after checking the
  pipeline's own pinned `schema_input.json` (or, absent a fixture, a census
  of its required samplesheet columns): declaring a type the pipeline's
  input schema does not actually accept would let a same-run coincidence
  masquerade as a real parent, which is worse than finding nothing — see
  `_matches_path` in `reingest_lookups.py`. Must be non-empty;
  `test_map_contract.py` enforces both that and that every entry names a
  real sample type in the catalog.
- `outputs` — glob to SampleType, with `cardinality` `per_sample` (1 to 1) or
  `per_run` (many to 1, `Parent` semicolon-joined).
- `provenance_attributes` — a single flat dict shared by the whole map, merged
  into a row only for the output rule(s) that opt in with
  `include_provenance: true` (default `false`). A rule that does not opt in
  gets only its own `attributes`. A map with a non-empty
  `provenance_attributes` and no output rule opted in is a bug, not a
  no-op — the contract test fails it deliberately, because that block would
  otherwise be merged into nothing.
- `qc_attributes` — raw key to a sample attribute on an EXISTING sample type.
- `deliberately_unmapped` — a ruling, with a real reason. Without it the agent
  re-proposes the same key on every run and the review queue fills with noise.

`$`-prefixed values are lookups into a manifest section (`params`, `pipeline`,
`software_versions`, `outputs`, `metrics`, `derived`). There is no evaluation.
A map cannot compute; `derived_metrics` in `reingest/derived.py` is the only
place reingest does arithmetic on a measurement.

A map's `from`/ref value can be wrong in a way no test catches: the contract
test can confirm that a rule's *target* attribute exists on its sample type,
but it has no way to confirm that a raw MultiQC key or `$`-ref actually
appears in a real run's output — a wrong key just never appears on any row,
silently, forever. Verify a new `qc_attributes` entry against a real run
before committing it, or leave the key to the approval queue instead of
guessing (see "Filling in a stub" below).

## Output rules are written by hand

The approval queue only ever proposes raw QC keys (`mapper.apply` reports
`sample.metrics` it could not place). A file no rule claims is never proposed,
so a map's `outputs` come from a person reading the pipeline, never from the
queue. Two rules decide whether an output can have one:

- The file must be in `harvest.INVENTORY_GLOBS`. A rule's `glob` is matched
  only against that inventory, so a rule for anything else (a fusion table, an
  HLA call, a miRNA count table, a DE results TSV) matches nothing, silently.
  Extend the inventory first, in `harvest.py`.
- The output must have a sample type that means what it holds. A fusion VCF
  is not an `A.VCF` of variant calls, and an HLA-only BAM is not a genome
  alignment. No fitting type is a schema request, not a reason to stretch one.

Write the rule from the pipeline's `conf/modules.config` (or its `main.nf`
`output {}` block) at the pinned release, not from its output documentation
alone: those docs are stale on filenames in several pipelines. Set
`applies_to_versions` to the release you read. It is not enforced at runtime,
so it records what was checked rather than gating anything.

## Outputs with no rule yet

The running list of documented deliverables that no map claims. Delete a row
when its rule lands. Paths are relative to `--outdir`, taken from each
pipeline's `conf/modules.config` at the release in its map's
`applies_to_versions` (differentialabundance: 2.0.0, which has no map range
yet). "Unconfirmed" means the docs and the code disagree, or neither names the
file exactly: check a real run before writing the rule.

What blocks each row:

- **ready**: in the inventory, with a fitting type. Only the rule is missing.
- **inventory**: needs a pattern in `harvest.INVENTORY_GLOBS` first.
- **type**: no sample type means what the file holds; a schema request.
- **per contrast**: one file per contrast. A rule is `per_sample` or `per_run`
  only, so one row would stand for every contrast.
- **parent**: the parent is an analysis sample (the input matrix's `A.GEX`),
  which the parent lookup cannot reach; it finds only the samplesheet's own files.
- **choice**: a decision on whether, or which of several, to register.

| Pipeline | Output | Type | Blocked by |
|---|---|---|---|
| denovotranscript | `evigene/okayset/all_assembled.okay.mrna` (the final assembly, FASTA) | none | type, inventory |
| denovotranscript | `evigene/okayset/all_assembled.okay.aa` (its proteins) | none | type, inventory |
| denovotranscript | `trinity/pooled_reads.fa.gz`, `rnaspades/pooled_reads.transcripts.fa.gz` (assemblies before redundancy reduction) | none | type, inventory, choice |
| denovotranscript | `tx2gene/*tx2gene.tsv` (unconfirmed name), as secondary data to `quant.sf` | A.GEX | inventory |
| differentialabundance | `tables/differential/<paramset>/<contrast>.{deseq2,limma,dream}.results.tsv` | A.GEX | inventory, per contrast, parent |
| differentialabundance | `tables/processed_abundance/<paramset>/all.normalised_counts.tsv`, `all.vst.tsv` | A.GEX | inventory, parent |
| differentialabundance | `other/deseq2/<paramset>/<contrast>.dds.rld.rds`; its `.dds.rld.rds` suffix also misses the `deseq2_dds_rdata` named output | A.GEX | per contrast, parent |
| differentialabundance | `other/limma/<paramset>/<contrast>.MArrayLM.limma.rds` | A.GEX | per contrast, parent |
| differentialabundance | `report/<paramset>/<study>_differentialabundance_report.html` (its QC report; there is no MultiQC) | A.GEX link | inventory, parent |
| hlatyping | `optitype/<sample>/<sample>_result.tsv` (unconfirmed name) | none (HLA genotype) | type, inventory |
| hlatyping | `hlahd/<sample>/<sample>_final.result.txt` (only with `--tools hlahd`) | none (HLA genotype) | type, inventory |
| hlatyping | `yara/<sample>/<sample>.mapped.bam`, or `<sample>_1`/`_2.mapped.bam` when paired | A.ALN? | choice: HLA-only reference, and two BAMs per paired sample |
| riboseq | `alignment/star/sorted/<sample>.transcriptome.sorted.bam` (+ `.bai`) | A.ALN | choice: a second A.ALN per sample |
| riboseq | `alignment/star/deduplicated/<sample>.umi_dedup.genome.sorted.bam` (with `--with_umi`; unconfirmed name) | A.ALN | choice: preferred over the sorted BAM when present |
| riboseq | `quantification/salmon/salmon.merged.{gene_tpm,transcript_counts,transcript_tpm}.tsv` | A.GEX | ready, choice |
| riboseq | `quantification/salmon/salmon.merged.*.SummarizedExperiment.rds` | A.GEX | ready, choice |
| riboseq | `quantification/salmon/<sample>/quant.sf` | A.GEX | ready, choice |
| riboseq | `translational_efficiency/anota2seq/<contrast>.*.anota2seq.results.tsv`, `*.Anota2seqDataSet.rds` | A.GEX | inventory, per contrast |
| riboseq | `orf_predictions/ribotish/*_pred.txt`, `orf_predictions/ribotricer/*_translating_ORFs.tsv` (ORF calls) | none | type, inventory |
| riboseq | `ribowaltz/*.psite.tsv`, `*.cds_coverage_psite.tsv` (P-site coverage) | none | type, inventory |
| rnafusion | `fusionreport/<sample>/<sample>.fusionreport.tsv` (consensus across callers) | none (fusion calls) | type, inventory |
| rnafusion | `arriba/`, `starfusion/`, `fusioncatcher/`, `fusioninspector/` per-caller tables | none (fusion calls) | type, inventory, choice |
| rnafusion | `vcf/<sample>_fusion_data.vcf` (fusions written as VCF) | A.VCF by format only | type |
| rnafusion | `star/<sample>.Aligned.sortedByCoord.out.cram` (with `--cram`) | A.ALN | ready: a second rule with `DataType` CRAM |
| rnafusion | `salmon/<sample>/quant.sf` (with `--tools salmon`) | A.GEX | ready |
| rnafusion | `star/<sample>.ReadsPerGene.out.tab` | A.GEX | inventory |
| rnafusion | `stringtie/stringtie.merged.gtf` (unconfirmed path) | none | type, inventory |
| rnafusion | `ctatsplicing/<sample>.cancer.introns` | none (splicing) | type, inventory |
| rnasplice | `{salmon,star_salmon/salmon}/<sample>/quant.sf` | A.GEX | ready |
| rnasplice | `*/tximport/salmon.merged.{gene_tpm,transcript_counts,transcript_tpm}.tsv` | A.GEX | ready, choice |
| rnasplice | `*/tximport/salmon.merged.*.rds` (tximport objects) | A.GEX | ready, choice |
| rnasplice | `<aligner>/dexseq_exon/counts/*.clean.count.txt` (exon-bin counts) | A.GEX | inventory |
| rnasplice | DEXSeq, edgeR, DTU, rMATS and SUPPA2 results under `<aligner>/` | none (splicing) | type, inventory, per contrast |
| rnavar | `annotation/<sample>/*.ann.vcf.gz` (snpEff/VEP/bcftools; unconfirmed names) | A.VCF | ready, choice: preferred over the filtered VCF when present |
| rnavar | `variant_calling/<sample>/<sample>.haplotypecaller.vcf.gz` (raw calls, when filtering is skipped) | A.VCF | ready, choice |
| rnavar | `preprocessing/<sample>/<sample>.md.bam` (when base recalibration is skipped) | A.ALN | ready, choice |
| rnavar | gVCF with `--generate_gvcf` (unconfirmed name) | A.VCF secondary | choice |
| rnavar | `seq2hla/<sample>/*.HLAgenotype4digits` (with `--tools seq2hla`) | none (HLA genotype) | type, inventory |
| smrnaseq | `mirna_quant/mirtop/joined_samples_mirtop.tsv` (isomiR counts) | A.GEX | inventory |
| smrnaseq | `mirna_quant/edger_qc/{mature,hairpin}_normalized_CPM.txt` | A.GEX | inventory |
| smrnaseq | `genome_quant/bam/*.bam`, `mirna_quant/bam/{hairpin,mature,seqcluster}/*.bam` (only with `--save_intermediates`; `.csi` index) | A.ALN | choice; the `.csi` is not inventoried |
| smrnaseq | `mirdeep2/**/result_*.csv` (novel miRNA calls) | none | type, inventory |
| all | A.VCF rows link no assay where the house assay "Variant Calling Analysis" does not exist | A.VCF | a house assay to create |

## Filling in a stub

The maps without output rules carry no `provenance_attributes` either — a stub
has no provenance block because it has no output rule to carry it; provenance
arrives together with the first output rule that opts in, not before. Rather
than guessing a stub's QC attributes
against a pipeline nobody has run here, run one and let the mapper name what
it could not map. Those land in the approval queue at
`GET /nextseek_api/reingest-proposals/?pipeline=nf-core/<name>`, ordered by how
often they have been seen. Approve the ones that are right, then fold the
approved rows into this file in an ordinary PR and delete them from the queue.

A proposed attribute that does not exist on the sample type is NOT a mapping
problem. It is a schema request: route it through the native Attribute API, or
`dmac-curation:curate-sampletype`. Reingest never creates an attribute.
