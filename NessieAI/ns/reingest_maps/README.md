# Reingest output maps

One `<pipeline>.outputs.json` per nf-core pipeline. Committed, code-reviewed,
read-only at runtime. `NessieAI/ns/reingest/maps.py` defines the schema and
`NessieAI/tests/ns/reingest/test_map_contract.py` gates every file in CI.

**Where the maps are checked.** `test_map_contract.py` (the schema and catalog contract), `test_fixture_runs.py`
(each map against its run fixture), `test_doc_only_maps.py` (maps written from the docs alone), and
`test_replicate_samples.py` (replicate handling), all under `NessieAI/tests/ns/reingest/`; and the link test
`NessieAI/tests/chat_nextseek/test_choosable_pipelines_have_maps.py` (a choosable pipeline has a map for its pinned release).

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
- `outputs` — the output rules, each with a unique `id` (`rule0`, `rule1`, ... for a
  v1 map, which still loads: its `glob` reads as one candidate and its
  `secondary_data_glob` as one companion).
  - `candidates` — glob patterns ordered by preference, most-processed first. The
    first pattern with a match supplies the row's primary file; files the later
    patterns match become companions.
  - `sample_type` and `cardinality`: `per_sample` (1 to 1), `per_run` (many to 1,
    `Parent` semicolon-joined; only the first matching file), or `per_file` (one
    per_run-shaped row per matching file, for outputs written once per contrast).
    A `per_file` rule must set `primary_data` and takes exactly one candidate. Its
    rows can read `@file_stem`, the part of the file name the glob's leading `*`
    matched (the contrast id for `*.deseq2.results.tsv`).
  - `companions` — files derived from the row's primary file (a subset, an index,
    other units, a model object). A companion is never its own row. It goes into
    its `slot` if it names one, the row's type has it and it is empty; else
    `File_SecondaryData` (which also fills `Checksum_SecondaryData` when the manifest
    has that file's checksum); else it is appended to `Notes` after "Also in run: ". A
    type without `Notes` sends the leftovers to the build report instead.
  - `optional` — a flag-dependent output (`--cram`, `--tools salmon`): matching
    nothing is expected, so it is not reported and builds no rows.
  - `skip_samples_without_file` — optional per_sample rules only: a sample with no file gets no row (logged), e.g. tumours for germline callers. Without it, such a sample gets a fileless row, which blocks the upload.
  - `alternative_group` — per_sample primary rules only, and one sample type per group: rules naming one group are outputs of which one launch writes at most one per sample (methylseq's three A.MSEQ rules; atacseq's and chipseq's narrow and broad peaks). nf-core never deletes an old output, so a run folder reused by a second launch can hold both; the sample then gets a row from each, every row is kept, and the mapper adds a map note naming the files. Leave it unset for outputs that coexist (a bigWig beside its peaks, sarek's variant callers).
  - `assay` — the house assay the rule's rows are filed under, by its `internal_assays` title (a catalog alternative name also resolves). A name, never an id: the ASSAY sheet needs a study-scoped `assays.id`, resolved per run from the parents' study, and the upload creates the study assay when the study has none. Every rule must name one (`test_map_contract.py`).
  - `attributes`, `primary_data`, `include_provenance` — as in the schema.

  A `per_file` row takes a companion only when the companion's own
  glob-captured stem equals the row's stem (the same logic as `@file_stem`) and the
  file sits under the same wildcard folders. So a `per_file` companion glob must
  end in a suffix as specific as the candidate's: candidate `*.deseq2.results.tsv`
  pairs with companion `*.deseq2.results_filtered.tsv`, not with
  `*.results_filtered.tsv`, whose captured stem would include `.deseq2` and never
  equal the row's. A companion glob that is not `*` plus a literal suffix is
  matched by prefix instead (the row's stem followed by `.` or `_`).
- `provenance_attributes` — a single flat dict shared by the whole map, merged
  into a row only for the output rule(s) that opt in with
  `include_provenance: true` (default `false`). A rule that does not opt in
  gets only its own `attributes`. A map with a non-empty
  `provenance_attributes` and no output rule opted in is a bug, not a
  no-op — the contract test fails it deliberately, because that block would
  otherwise be merged into nothing.
- `qc_attributes` — raw key to a sample attribute on an EXISTING sample type.
- `inventory_globs` — this map's own inventory patterns (see "Inventory" below).
  Each must start with `**/`; the stager lists the union of these across every map
  and `harvest.CORE_INVENTORY_GLOBS`.
- `row_defaults` — attribute to value, merged into every output rule's attributes
  before the rule's own (the way to give every row a Scientist, Lab or
  SampleCreationDate). An attribute the row's type lacks is dropped, never sent.
- `versions` — release-specific differences. Each entry has a `range`, `outputs`
  that replace a base rule with the same `id` or add a new one, and `drop`, a list
  of base rule ids to remove. `maps.effective()` picks the first entry whose range
  contains the run's release. A release outside `applies_to_versions` still maps,
  with a warning that the rules are unchecked. The release is read with nf-core's
  `-g<commit>` suffix dropped (`v4.1.0-gf7bf36d` is 4.1.0).
- `deliberately_unmapped` — a ruling, with a real reason. Without it the agent
  re-proposes the same key on every run and the review queue fills with noise.
- `qc_row_suffixes` — MultiQC rows named `<sample><suffix>` (sarek: `.recal`, `.md`) whose columns join that sample's metrics, best first; a column already held is kept. Folded columns no rule or `qc_attributes` entry claims go to the QC review queue unless a `deliberately_unmapped` ruling rules them out.
  methylseq folds `_1_val_1`: MultiQC 1.32's default name cleaning cuts a Bismark report's name at `_bismark`, so a paired-end sample's Bismark rows are named after Trim Galore's read-1 file, `<sample>_1_val_1` (single-end rows are `<sample>`). With `--skip_trimming` they are named after the input FASTQ instead (for example `<sample>_R1`), which no fixed suffix catches, so those rows are not folded. Unverified until a real run.
- `replicate_samples` — when true, a samplesheet `replicate` column splits each `sample` into one sample per biological replicate, `<sample>_REP<replicate>`, the id nf-core/atacseq and nf-core/chipseq give each replicate's files, each with its own D.SEQ parent (and its own QC backfill). Rows sharing both `sample` and `replicate` are lanes of one library and stay one multi-run sample. The samplesheet's `sample` becomes the replicate group (`RunManifest.replicate_groups`): a file named after the group (atacseq's merged replicates, `<sample>.mRp.*`) gets one row for the group, parented by every replicate, when its candidate comes no later than any replicate's, and the replicates' files for that rule become its losing candidates. Off by default: a `replicate` column in another pipeline's samplesheet is sample metadata. The launch record is matched per replicate by the samplesheet `sample` and `fastq_1`.
- `claims_every_file` — when true, the fixture contract test requires every file of a run to be a rule's candidate or companion, or to match `unmapped_files`.
- `unmapped_files` — globs of run files no rule registers on purpose, each with a `why`. The mapper drops a matching file before any rule sees it, so a ruled-out file is never a rule's candidate or companion, and with `claims_every_file` set the fixture test counts it as claimed. Matching is `fnmatch` over brace-expanded globs, and `*` crosses `/`.

`$`-prefixed values are lookups into a manifest section (`params`, `pipeline`,
`software_versions`, `outputs`, `metrics`, `derived`). There is no evaluation.
A map cannot compute; `derived_metrics` in `reingest/derived.py` is the only
place reingest does arithmetic on a measurement.

A raw general-stats key is either a `qc_attributes` `from`/alternate or, on an
output row, `$metrics.<key>` (rnavar's VCF row reads snpEff's Ts/Tv this way).
Either way it counts as claimed and is never queued. FastQC and Cutadapt write
per read file, on MultiQC's `<sample>_1` / `<sample>_2` rows: name such a column
`<key>@R1` or `<key>@R2` and harvest folds it onto the sample. Only claimed
`@R1`/`@R2` keys are folded, so an unread per-read column never reaches the queue.

`@`-prefixed values are named tokens computed in code, in
`NessieAI/ns/reingest/pipelines/`: `common.py` for every pipeline, and one module
per pipeline that needs its own (`differentialabundance.py`). A map may use the
common tokens and its own pipeline's, and no other pipeline's.

A map's `from`/ref value can be wrong in a way no test catches: the contract
test can confirm that a rule's *target* attribute exists on its sample type,
but it has no way to confirm that a raw MultiQC key or `$`-ref actually
appears in a real run's output — a wrong key just never appears on any row,
silently, forever. Verify a new `qc_attributes` entry against a real run
before committing it, or leave the key to the approval queue instead of
guessing (see "Filling in a stub" below).

## General rulings

Settled once for every pipeline in the design "Reingest general rulings and a data-driven
map engine". Map rules follow them; a pipeline-specific exception is a new ruling, not
a quiet departure.

- **G-1 Scope.** Only Nessie-launched runs are uploaded; an admin (`is_superuser`) may build workbooks from local copies for testing, and those are test-only and refused at upload.
- **G-2 Versions.** A release outside `applies_to_versions` still maps, with a warning; release differences go in `versions` overrides.
- **G-3 Companions.** A file derived from a registered output is a companion of that row, never its own row: named slot, then `File_SecondaryData`, then `Notes`.
- **G-4 Variants.** One row per sample per kind of output; `candidates` are listed most-processed first and the first present wins, the rest become companions.
- **G-5 No fitting type.** Never stretch a type; the output has no rule until a specific sample type is requested and built.
- **G-6 Outside the run folder.** The stager reads nothing outside `run_dir`; rows keep the sequencing samples as `Parent`.
- **G-7 Evidence.** A rule merges when a committed fixture at the pinned release exercises it. The fixture's source, in order of preference: nf-core's nf-test snapshot at the release tag, nf-core's published AWS results for the release, then a `-profile test` run launched through Nessie. QC attribute keys and units come from a real `multiqc_general_stats.txt` (AWS results or a Nessie run; a snapshot holds no MultiQC values). Flag-dependent outputs get a variant fixture or are `optional`.
- **G-8 Discovery.** There is no approval queue for outputs and no list of unclaimed files; new outputs are noticed by reading the pipeline source and through the snapshot diff when a release's fixture lands.
- **G-9 Row defaults.** Name, Scientist, Lab and SampleCreationDate come from the map's `row_defaults`, for every rule whose type has the attribute.
- **G-10 Optional outputs.** Documented flag-dependent outputs (`--cram`, `--tools salmon`) get rules marked `optional: true`.

## Output rules are written by hand

The approval queue only ever proposes raw QC keys (`mapper.apply` reports
`sample.metrics` it could not place). A file no rule claims is never proposed,
so a map's `outputs` come from a person reading the pipeline, never from the
queue. Two rules decide whether an output can have one:

- The file must be inventoried: in `harvest.CORE_INVENTORY_GLOBS` or this map's
  own `inventory_globs`. A rule's `candidates` and a companion's `glob` are
  matched only against that inventory, so a rule for anything else (a fusion
  table, an HLA call, a miRNA count table, a DE results TSV) matches nothing,
  silently. Add the pattern to the map.
- The output must have a sample type that means what it holds. A fusion VCF
  is not an `A.VCF` of variant calls, and an HLA-only BAM is not a genome
  alignment. No fitting type is a schema request, not a reason to stretch one.

Write the rule from the pipeline's `conf/modules.config` (or its `main.nf`
`output {}` block) at the pinned release, not from its output documentation
alone: those docs are stale on filenames in several pipelines. Set
`applies_to_versions` to the release you read. It does not gate
anything: a run outside it still maps, with a warning (G-2), so it records what
was checked.

### Inventory

The inventory is the list of files the stager names (path and size) without
copying them. `harvest.CORE_INVENTORY_GLOBS` holds the classes that can become
primary data or a named link in any pipeline: alignment files and their indexes,
single-cell and R objects, VCFs, the MultiQC html report and contaminant-screening
reports. Everything pipeline-specific belongs in the map's `inventory_globs`.
Shape a pattern like this:

- Start with `**/`, and name the file exactly. Not `**/*.tsv`: every pipeline
  writes many TSVs and the inventory is capped at `MAX_INVENTORY_FILES`.
- A pattern's `*` never crosses a `/`, so each folder layout needs its own
  pattern. differentialabundance 1.x publishes tables flat and 2.0.0 under a
  `<paramset>/` folder, so it lists both.
- Reach a report no deeper than its own folder. The study report is
  `report/<study>.html` (1.x) or `report/<paramset>/<study>_differentialabundance_report.html`
  (2.0.0). 1.5 and 2.0.0 also write hundreds of GSEA pages under `report/gsea/`,
  which a deeper pattern would list.
- Text matrices whose names are not rnaseq's `*.merged.*` convention need their own
  entry: smrnaseq's merged miRNA counts, Salmon's per-sample `quant.sf` (one per
  sample folder; the folder names the sample), and differentialabundance's
  normalised matrix and per-contrast results tables.
- A companion needs an inventory pattern too. differentialabundance's
  `*.results_filtered.tsv` subsets and `*.annotated.tsv` joins are not listed yet.
- The multiqc report patterns are in the core list because nf-core/scrnaseq
  prefixes the file with the run name and nf-core/rnavar 1.3 and later publishes it
  under `reports/`.
- A file the inventory claims is listed, never staged: a pattern in `harvest_globs`
  whose file name an inventory pattern already covers is dropped from staging.

## Outputs with no rule yet

The running list of documented deliverables that no map claims. Delete a row
when its rule lands. Paths are relative to `--outdir`, taken from each
pipeline's `conf/modules.config` at the release in its map's
`applies_to_versions`. differentialabundance rows give both layouts: 1.4 and
1.5 publish flat, and 2.0.0 adds a `<paramset>/` folder, shown as
`[<paramset>/]`; its map covers both. "Unconfirmed" means the docs
and the code disagree, or neither names the file exactly: check a real run
before writing the rule.

What blocks each row:

- **ready**: in the inventory, with a fitting type. Only the rule is missing; a derived file is written as a companion on its row's rule, not as a row of its own.
- **inventory**: needs a pattern in the map's `inventory_globs` first.
- **type**: no sample type means what the file holds; a schema request.
- **per contrast**: one file per contrast. A `per_file` rule gives each its own
  row (with `@contrast` for the id), so this no longer blocks on its own; the
  row still needs its other blockers cleared.
- **parent**: the parent is an analysis sample (the input matrix's `A.GEX`),
  which the parent lookup cannot reach; it finds only the samplesheet's own files.
- **choice**: a ruling still open for that pipeline. The general rulings settled
  the recurring cases: a derived file is a companion of the row it derives from
  (G-3), and several variants of one output are candidates, most-processed first
  (G-4).

| Pipeline | Output | Type | Blocked by |
|---|---|---|---|
| atacseq | the per-replicate files of a sample whose replicates were merged: `<ALIGNER>/merged_library/<sample>_REP<n>.mLb.clN.sorted.bam`, its peaks and its bigWig | A.ALN, A.CHRM | choice: whether to register replicate-level files beside the merged-replicate ones. Today the sample's merged-replicate file wins (G-4) and is registered once for the group, parented by every replicate; the replicates' files are its losing candidates, so they reach its companions or the build report. Rows of their own would need separate rules. A sample with one replicate, or a run with `--skip_merge_replicates`, registers each replicate's own files, parented by its own D.SEQ (`replicate_samples`) |
| atacseq | `<ALIGNER>/merged_replicate/macs2/<PEAK_TYPE>/consensus/consensus_peaks.mRp.clN.bed` and its `featureCounts.txt` (the consensus over the merged-replicate peaks) | A.CHRM | inventory, choice: a second consensus set beside the merged-library one, which the map registers |
| chipseq | `<ALIGNER>/merged_library/macs3/<PEAK_TYPE>/consensus/<ANTIBODY>/<ANTIBODY>.consensus_peaks.bed` and its `featureCounts.txt`, one per antibody | A.CHRM | inventory, parent: a `per_file` row is parented by every sample of the run, controls and other antibodies' samples included |
| chipseq | the antibody each peak row was called for | A.CHRM | type: A.CHRM has no Name or antibody field, and harvest reads no `antibody` column |
| denovotranscript | `evigene/okayset/all_assembled.okay.mrna` (the final assembly, FASTA) | none | type, inventory |
| denovotranscript | `evigene/okayset/all_assembled.okay.aa` (its proteins) | none | type, inventory |
| denovotranscript | `trinity/pooled_reads.fa.gz`, `rnaspades/pooled_reads.transcripts.fa.gz` (assemblies before redundancy reduction) | none | type, inventory, choice: whether to register pre-reduction assemblies at all |
| denovotranscript | `tx2gene/*tx2gene.tsv` (unconfirmed name), as secondary data to `quant.sf` | A.GEX | inventory |
| differentialabundance | `tables/differential/[<paramset>/]<contrast>.deseq2.results_filtered.tsv` (1.5+, a subset of the results table: a companion of the results row) | A.GEX | inventory |
| differentialabundance | `tables/differential/<paramset>/<contrast>_<study>_<method>.annotated.tsv` (2.0.0: the results joined to gene annotation; a companion; its glob must carry the method in its suffix, `*_<method>.annotated.tsv`, as in the per_file companion paragraph above) | A.GEX | inventory |
| differentialabundance | `other/dream/<paramset>/<contrast>_<study>.dream.model.txt`, and each method's `*.model.txt` | A.GEX | inventory |
| differentialabundance | `MethodSettings` on 2.0.0 rows: each paramset has its own settings, which the run's params do not hold, so the field is blank there | A.GEX | inventory: the paramsets file |
| differentialabundance | non-RNA-seq study types (`--study_type affy_array`, `geo_soft_file`, `maxquant`) are named in `Notes`; their `*.matrix.tsv` normalised matrix has no rule, maxquant's results are protein abundance filed as A.GEX, and their samplesheets name no FASTQ, so no parent resolves | A.GEX, other | type, parent |
| hlatyping | `hlahd/<sample>/<sample>_final.result.txt` (only with `--tools hlahd`) | A.HLA (its class II fields, `HLA_DRB1_1` to `HLA_DPB1_2`) | inventory; one row per locus, so the rule needs a reader for that layout |
| hlatyping | `yara/<sample>/<sample>.mapped.bam`, or `<sample>_1`/`_2.mapped.bam` when paired | A.ALN? | choice: HLA-only reference, and two BAMs per paired sample |
| methylseq | Bismark's per-read, per-context calls `<aligner>/methylation_calls/methylation_calls/{CpG,CHG,CHH}_{OT,OB}_*.txt.gz`, the targeted bedGraph `*.bedGraph.targeted.bedGraph` (with `--run_targeted_sequencing`), and `bismark/coverage2cytosine/{coverage,reports}/` (with `--cytosine_report` or `--nomeseq`). The CpG coverage and bedGraph have rules (A.MSEQ); their MeanCpGMethylationPercent reads the general-stats column `bismark-percent_cpg_meth`, unverified until a real run | A.MSEQ | choice: whether to register the per-read calls (large; summarised by the coverage file) and the genome-wide cytosine report, which would need a slot or Notes on A.MSEQ |
| methylseq | `methyldackel/*_{CHG,CHH}.bedGraph` (with `--all_contexts`; `*_{CHG,CHH}.methylKit` with `--methyl_kit` as well), `methyldackel/*.targeted.bedGraph`, and Rastair's TAPS calls `rastair/{call,methylkit}/` (bwa-mem, or bwa-meth with `--taps`). MethylDackel's CpG calls have rules (A.MSEQ): `*_CpG.bedGraph`, or `*_CpG.methylKit`, which `--methyl_kit` writes instead of the bedGraph. With `--taps` and a Bismark aligner, Bismark still calls the reads as bisulfite data, so its calls come out inverted while the row's ConversionMethod says TAPS; methylseq only logs a recommendation to use bwa-mem (`workflows/methylseq/main.nf`) | A.MSEQ | inventory; choice: A.MSEQ has one secondary slot and no Notes for the non-CpG files; TAPS calls need their own DataType and Software; whether to refuse a TAPS run aligned with Bismark |
| methylseq | A.MSEQ and its producing assay "Methylation Calling" exist on the local stack only; the A.MSEQ rows cannot upload on dev or prod until both are created there. The committed assay catalog (`assays_db.json`, `min_assays_db.json`, the installer seed) stays prod-true and does not list the assay: the contract test reads it from the test-only overlay `NessieAI/tests/ns/fixtures/local_only_assays.json`. Before this map merges to a branch that deploys, Methylation Calling must be in `context/assays.json` and the exports (and the overlay entry deleted), and A.MSEQ created on dev and prod. | A.MSEQ | a sample type and a house assay to create on dev and prod |
| riboseq | `alignment/star/sorted/<sample>.transcriptome.sorted.bam` (+ `.bai`) | A.ALN | ready: a companion of the genome BAM's row |
| riboseq | `alignment/star/deduplicated/<sample>.umi_dedup.genome.sorted.bam` (with `--with_umi`; unconfirmed name) | A.ALN | ready: a first candidate, ahead of the sorted BAM |
| riboseq | `quantification/salmon/salmon.merged.{gene_tpm,transcript_counts,transcript_tpm}.tsv` | A.GEX | ready |
| riboseq | `quantification/salmon/salmon.merged.*.SummarizedExperiment.rds` | A.GEX | ready |
| riboseq | `quantification/salmon/<sample>/quant.sf` | A.GEX | ready |
| riboseq | `translational_efficiency/anota2seq/<contrast>.*.anota2seq.results.tsv`, `*.Anota2seqDataSet.rds` | A.GEX | inventory, per contrast |
| riboseq | `orf_predictions/ribotish/*_pred.txt`, `orf_predictions/ribotricer/*_translating_ORFs.tsv` (ORF calls) | none | type, inventory |
| riboseq | `ribowaltz/*.psite.tsv`, `*.cds_coverage_psite.tsv` (P-site coverage) | none | type, inventory |
| rnafusion | `fusionreport/<sample>/<sample>.fusionreport.tsv` (consensus across callers) | none (fusion calls) | type, inventory |
| rnafusion | `arriba/`, `starfusion/`, `fusioncatcher/`, `fusioninspector/` per-caller tables | none (fusion calls) | type, inventory |
| rnafusion | `vcf/<sample>_fusion_data.vcf` (fusions written as VCF) | A.VCF by format only | type |
| rnafusion | `star/<sample>.Aligned.sortedByCoord.out.cram` (with `--cram`) | A.ALN | ready: a second rule with `DataType` CRAM |
| rnafusion | `salmon/<sample>/quant.sf` (with `--tools salmon`) | A.GEX | ready |
| rnafusion | `star/<sample>.ReadsPerGene.out.tab` | A.GEX | inventory |
| rnafusion | `stringtie/stringtie.merged.gtf` (unconfirmed path) | none | type, inventory |
| rnafusion | `ctatsplicing/<sample>.cancer.introns` | none (splicing) | type, inventory |
| rnasplice | `{salmon,star_salmon/salmon}/<sample>/quant.sf` | A.GEX | ready |
| rnasplice | `*/tximport/salmon.merged.{gene_tpm,transcript_counts,transcript_tpm}.tsv` | A.GEX | ready |
| rnasplice | `*/tximport/salmon.merged.*.rds` (tximport objects) | A.GEX | ready |
| rnasplice | `<aligner>/dexseq_exon/counts/*.clean.count.txt` (exon-bin counts) | A.GEX | inventory |
| rnasplice | DEXSeq, edgeR, DTU, rMATS and SUPPA2 results under `<aligner>/` | none (splicing) | type, inventory, per contrast |
| rnavar | `annotation/<sample>/*.ann.vcf.gz` (snpEff/VEP/bcftools; unconfirmed names) | A.VCF | ready: a first candidate, ahead of the filtered VCF |
| rnavar | `variant_calling/<sample>/<sample>.haplotypecaller.vcf.gz` (raw calls, when filtering is skipped) | A.VCF | ready: a later candidate, behind the filtered VCF |
| rnavar | gVCF with `--generate_gvcf` (unconfirmed name) | A.VCF secondary | ready: a companion of the VCF row |
| rnavar | `seq2hla/<sample>/<sample>-ClassI-class.HLAgenotype4digits`, with the `ClassII` file as its companion (with `--tools seq2hla`) | A.HLA (its class II fields, `HLA_DRB1_1` to `HLA_DPB1_2`) | inventory; one row per locus, so the rule needs a reader for that layout |
| sarek | somatic pair outputs under `variant_calling/<caller>/<tumor>_vs_<normal>/` and `annotation/<tumor>_vs_<normal>/` (Mutect2, Strelka, Manta, TIDDIT, MuSE and the other somatic callers) | A.VCF | choice: a pair-level rule whose row names both samples as parents. Until then the map's `unmapped_files` rules out anything under a `*_vs_*` folder, so no germline rule takes a pair's file as the tumor's own |
| sarek | `variant_calling/haplotypecaller/joint_variant_calling/joint_germline{,_recalibrated}.vcf.gz` (with `--joint_germline`), and the Sentieon `joint_variant_calling/` twins | A.VCF | ready: a per_run rule |
| sarek | other germline callers' VCFs, by `--tools`: `variant_calling/bcftools/<sample>/<sample>.bcftools.vcf.gz`, and `sentieon_dnascope/`, `sentieon_haplotyper/`, `parabricks_haplotypecaller/`, `tiddit/` (unconfirmed names) | A.VCF | ready: optional rules like the five germline callers' |
| sarek | `variant_calling/{filtered,normalized,consensus,concat}/<sample>/` post-calling VCFs (with `--filter_vcfs`, `--normalize_vcfs`, `--snv_consensus_calling`, `--concatenate_vcfs`) | A.VCF | choice: whether one replaces the caller's own VCF as a first candidate |
| sarek | `preprocessing/recalibrated/<sample>/<sample>.recal.bam` and `preprocessing/markduplicates/<sample>/<sample>.md.bam` (+ `.bai`, with `--save_output_as_bam`) | A.ALN | ready: a second rule with `DataType` BAM |
| sarek | alignments from the other preprocessing paths: `preprocessing/mapped/` (with `--save_mapped`), `preprocessing/sentieon_dedup/`, `preprocessing/parabricks/` | A.ALN | ready: later candidates of the `aln` rule |
| sarek | CNV, ploidy and MSI results (ASCAT, CNVKit, Control-FREEC, MSIsensor2, MSIsensorPro) and indexcov's coverage tables | none | type, inventory |
| smrnaseq | `mirna_quant/mirtop/joined_samples_mirtop.tsv` (isomiR counts) | A.GEX | inventory |
| smrnaseq | `mirna_quant/edger_qc/{mature,hairpin}_normalized_CPM.txt` | A.GEX | inventory |
| smrnaseq | `genome_quant/bam/*.bam`, `mirna_quant/bam/{hairpin,mature,seqcluster}/*.bam` (only with `--save_intermediates`; `.csi` index) | A.ALN | choice; the `.csi` is not inventoried |
| smrnaseq | `mirdeep2/**/result_*.csv` (novel miRNA calls) | none | type, inventory |
| all | A.VCF rows link no assay where the house assay "Variant Call Filtering" does not exist; it is a pending `create_internal` in `context/assay_mappings.json` | A.VCF | a house assay to create |

Ruled out on purpose, so not on the list above: differentialabundance's `all.vst.tsv` / `all.rlog.tsv` (regenerated from the normalised counts), its plots, Shiny app, `*.sizefactors.tsv` and session logs, and the upstream counts matrix as `Parent` (G-6: the stager reads nothing outside the run folder; rows keep the sequencing samples). A map that sets `claims_every_file` (sarek, methylseq) lists its own ruled-out files in `unmapped_files`, each with its reason. atacseq and chipseq list theirs too, without `claims_every_file`: no fixture lists their files.

## Fixtures

Evidence for a rule (G-7) is a fixture folder under
`NessieAI/tests/ns/fixtures/reingest_runs/`; how to make one, and how its snapshot is
regenerated, is in `NessieAI/tests/ns/fixtures/reingest_runs/README.md`.

### Maps written from the docs alone

The owner waived G-7's fixture for these releases: they have no nf-test snapshot, and
nf-core's published results hold partial files and no MultiQC. Their file rules come
from the release's pinned docs/output.md (committed as
`NessieAI/tests/chat_nextseek/fixtures/nfcore/<pipeline>.output.md`), checked against
its `conf/modules.config`. `test_doc_only_maps.py` checks that every folder a rule names
is one that output.md documents. A map file cannot say this itself
(`PipelineMap` forbids unknown keys), so this table does. Delete a row when the
release's run fixture lands, with its entry in `DOC_ONLY`.

| Pipeline | Release | Evidence | QC |
|---|---|---|---|
| atacseq | 2.1.2 | pinned docs/output.md only; unverified until a real run | none from general stats: the release's `assets/multiqc_config.yml` sets `exclude_modules: general_stats`, which MultiQC 1.13 reads as `skip_generalstats`, so no `multiqc_general_stats.txt` is written. D.SEQ gets only `Link_QC` and `Genome` |
| chipseq | 2.1.0 | pinned docs/output.md only; unverified until a real run | none from general stats, as for atacseq (MultiQC 1.23 reads the same setting the same way) |

Both maps set `replicate_samples`, so each replicate's own D.SEQ takes the QC backfill
(`Link_QC`, `Genome`); only lanes of one replicate, a multi-run sample, get none
(`mapper._NO_BACKFILL`), as in every pipeline.

A.CHRM has no `File_SecondaryData` or `Notes`, so a companion of an A.CHRM row
(atacseq's consensus `featureCounts.txt`, a losing peak or bigWig candidate) reaches
only the build report.

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
