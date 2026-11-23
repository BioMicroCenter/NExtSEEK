# Reingest output maps

One `<pipeline>.outputs.json` per nf-core pipeline. Committed, code-reviewed,
read-only at runtime. `NessieAI/ns/reingest/maps.py` defines the schema and
`NessieAI/tests/ns/reingest/test_map_contract.py` gates every file in CI.

**Where the maps are checked.** `test_map_contract.py` (the schema and catalog contract), `test_fixture_runs.py`
(each map against its run fixture), `test_doc_only_maps.py` (maps written from the docs alone), and
`test_replicate_samples.py` (replicate handling), `test_methylseq_map.py` (methylseq's tiers against its variant
fixtures), `test_chromatin_module_qc.py` (atacseq and chipseq QC from module tables), `test_cutandrun_map.py`
(cutandrun's group samplesheet, MultiQC folder, primary-caller QC and consensus parents), `test_rnasplice_map.py`
(rnasplice 2.0.0's MultiQC 1.35 columns from nf-core's AWS `test_full` run, beside 1.0.4's MultiQC 1.18 ones),
`test_qc_completeness_dna.py` (the QC-completeness check for atacseq, chipseq, methylseq and cutandrun against every
fixture that holds a run's MultiQC data), `test_raredisease_map.py` (raredisease's case-level VCF parents) and
`test_eager_dsl1_run.py` (eager's DSL1 run: TSV samplesheet, versions CSV, rules), all under `NessieAI/tests/ns/reingest/`; and the link test
`NessieAI/tests/chat_nextseek/test_choosable_pipelines_have_maps.py` (a choosable pipeline has a map for its pinned release).

## What a map may contain

- `applies_to_versions` — the release range the map was read at (a PEP 440 specifier). It
  gates nothing: a run outside it still maps, with a warning.
- `applies_to_commits` — for a pipeline pinned to a commit because it has no release (the
  catalog's `default_revision` and the atlas `revision` are then a 40-hex SHA): the full,
  lowercase SHAs the map was read at. A run reports its commit as nf-core's `-g<short sha>`
  version suffix, and `maps.effective()` treats a run whose short commit starts a listed SHA
  as checked. `PipelineMap.covers_revision()` is the link test's check: a release against the
  range, a SHA against this list.

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
    matched (the contrast id for `*.deseq2.results.tsv`). A `per_file` row is parented
    by every sample of the run, unless the rule sets `parents_by_samplesheet`, a
    samplesheet column whose value is the row's `@file_stem`: then only the samples
    with that value are its parents (chipseq's `<antibody>.consensus_peaks.bed`, column
    `antibody`). A file whose value no sample of this run's samplesheet has is not this
    run's (a folder reused by a launch with another antibody): it gets no row, and the
    reply lists it under FILES LEFT OUT: NOT FROM THIS RUN (`MapResult.stale_files`). A
    per_file rule may also name an `every_sample_stem`, the stem of a file made from the whole run
    (cutandrun's `all_samples` consensus, `--consensus_peak_mode all`): that row is parented by every
    sample, less the controls when `controls_by_samplesheet` names the column each sample's control is
    given in (cutandrun's `control`: a sample whose group another sample names there is a control). A
    named sample that resolved to no D.SEQ is left out of `Parent` with a warning; when
    none resolved, the row ships with no `Parent` and the reply lists it, by the owner's
    ruling on parentless group rows (`NessieAI/ns/reingest/README.md` "Invariants").
    Two rows of one sample type whose files share a name (a contrast under two
    paramsets; one antibody's broad and narrow consensus in a reused folder) keep as
    many trailing folders in `File_PrimaryData` as tell them apart.
  - `companions` — files derived from the row's primary file (a subset, an index,
    other units, a model object). A companion is never its own row. It goes into
    its `slot` if it names one, the row's type has it and it is empty; else
    `File_SecondaryData` (which also fills `Checksum_SecondaryData` when the manifest
    has that file's checksum); else it is appended to `Notes` after "Also in run: ". A
    type without `Notes` sends the leftovers to the build report instead.
  - `optional` — a flag-dependent output (`--cram`, `--tools salmon`): matching
    nothing is expected, so it is not reported and builds no rows.
  - `skip_samples_without_file` — optional per_sample rules only: a sample with no file gets no row (logged), e.g. tumours for germline callers. Without it, such a sample gets a fileless row, which blocks the upload.
  - `alternative_group` — optional primary rules only, per_sample or per_file (not mixed in one group), two or more to a group, one sample type per group; a per_file rule's rows are counted by their `@file_stem` (chipseq's narrow and broad consensus of one antibody): rules naming one group are outputs of which one launch writes at most one per sample (methylseq's three A.MSEQ rules; atacseq's and chipseq's narrow and broad peaks). nf-core never deletes an old output, so a run folder reused by a second launch can hold both; the sample (counted by replicate group where the run has them) then gets a row from each, every row is kept, and the reply's FILES FROM AN EARLIER RUN TO CHECK names the files, plus any other row of the sample whose file won over a later candidate pattern (methylseq's aln rule prefers Bismark's BAM). Leave it unset for outputs that coexist (a bigWig beside its peaks, sarek's variant callers).
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
- `module_tables` — MultiQC data files by name (`multiqc_picard_dups.yaml`), each with a
  `namespace` (and an optional row `suffix`), read like the harvest's own `_MODULE_TABLES`:
  column `C` of row `<sample>` becomes the metric `<namespace>-C`. For a pipeline whose
  MultiQC writes no general-stats table (atacseq and chipseq exclude it). MultiQC's
  `data_format: yaml` writes `.yaml` tables, which are staged only through this map's
  `harvest_globs`; list the `.txt` name too when the format is unsure. A plot's data file
  (`picard_deduplication.yaml`) reads the same way, one row per sample. `shape: bargraph`
  reads MultiQC's custom-content bar graph (atacseq's `multiqc_mlib_frip_score-plot.txt`:
  each row's one value under a column named after the row or its file) as the single metric
  `<namespace>-value`; `columns` names the only columns read, so a 35-column flagstat
  table yields the one a rule wants and nothing for the review queue. A replicate group's
  own rows (atacseq's merged-replicate `mrep` tables) become `RunManifest.group_metrics`,
  which the group's merged row reads as its metrics.
- `sample_files` — per-sample QC files outside MultiQC's tables, by metric namespace, each
  a `glob` with `<sample>` for the sample's name (staged with `*` in its place) and a
  `reader`: `value` (a one-value custom-content `*_mqc.tsv`), `table` (a `Sample`-headed
  table, read from the row named `<sample>` or `<sample>.<suffix>`) or `ataqv` (the JSON's
  scalar metrics); `columns` as for module tables. chipseq reads its FRiP, peak count and
  phantompeakqualtools coefficients this way, both maps their plotFingerprint metrics, and
  atacseq ataqv's TSS enrichment.
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
  methylseq folds `_1_val_1`: MultiQC 1.32's default name cleaning cuts a Bismark report's name at `_bismark`, so a paired-end sample's Bismark rows are named after Trim Galore's read-1 file, `<sample>_1_val_1` (single-end rows are `<sample>`). With `--skip_trimming` they are named after the input FASTQ instead (for example `<sample>_R1`), which no fixed suffix catches, so those rows are not folded. Checked on a real run: `4.2.0-local-test` names the paired sample's Bismark row `Ecoli_10K_methylated_1_val_1`, and its single-end rows `<sample>` (`test_methylseq_map.py`); the full-size AWS run of 4.2.0 names its Bismark files the same way (`<sample>_1_val_1_bismark_bt2_pe.*`).
- `replicate_samples` — when true, a samplesheet `replicate` column splits each `sample` into one sample per biological replicate, `<sample>_REP<replicate>`, the id nf-core/atacseq and nf-core/chipseq give each replicate's files, each with its own D.SEQ parent (and its own QC backfill). Rows sharing both `sample` and `replicate` are lanes of one library and stay one multi-run sample. The samplesheet's `sample` becomes the replicate group (`RunManifest.replicate_groups`): a file named after the group (atacseq's merged replicates, `<sample>.mRp.*`) gets one row for the group, parented by every replicate, when its candidate comes no later than any replicate's, and the replicates' files for that rule become its losing candidates. Off by default: a `replicate` column in another pipeline's samplesheet is sample metadata. The launch record is matched per replicate by the samplesheet `sample` and `fastq_1`.
- `samplesheet_sample_column` — the samplesheet column that names a sample, `sample` by default. nf-core/cutandrun calls it `group`; the harvest copies it into `sample` on every row and keeps the original column for `$samplesheet` refs and `parents_by_samplesheet`. It also decides which top-level CSV is the samplesheet.
- `replicate_name` — with `replicate_samples`, how a replicate's files are named: `{sample}_REP{replicate}` by default (atacseq, chipseq), `{sample}_R{replicate}` for cutandrun. It must use exactly those two fields.
- `multiqc_data_dirs` — MultiQC data folders below the run root, as run-relative globs ending in `/`, for a release that does not publish MultiQC in a top-level `multiqc*/` folder (cutandrun: `04_reporting/multiqc/multiqc_data/`). Their `multiqc_*.txt` tables are staged, and general stats and `module_tables` are looked for there as well as in the generic folder.
- `claims_every_file` — when true, the fixture contract test requires every file of a run to be a rule's candidate or companion, or to match `unmapped_files`.
- `unmapped_files` — globs of run files no rule registers on purpose, each with a `why`. The mapper drops a matching file before any rule sees it, so a ruled-out file is never a rule's candidate or companion, and with `claims_every_file` set the fixture test counts it as claimed. Matching is `fnmatch` over brace-expanded globs, and `*` crosses `/`. `except_globs` names files the glob also matches that the ruling leaves to the rules (methylseq rules out bwa-meth's unsorted `alignments/<sample>.bam` but not the `<sample>.sorted.bam` its aln rule reads); `maps.UnmappedFiles.rules_out` is the one matcher.

`$`-prefixed values are lookups into a manifest section (`params`, `pipeline`,
`software_versions`, `outputs`, `metrics`, `derived`, `samplesheet`). There is no evaluation.
`params` is the release's `pipeline_info/params*.json`. A release that writes none
(atacseq 2.1.2) gets the params Nessie launched it with instead: its `params.yml`
overlaid with the `--name value` flags of `run.sh`'s `nextflow run` line, where
`--genome` is (`parsers.parse_launch_params`). A run Nessie did not launch has neither,
so its `$params` refs resolve to nothing.
`$samplesheet.<column>` is the sample's own samplesheet cell. The harvest keeps only the
columns the run's map reads (`PipelineMap.samplesheet_columns`: its `$samplesheet` refs
and `parents_by_samplesheet`), and no path: the manifest is stored and handed to
Container-CC whole, so sarek's sex or status never reach it. On a row with no sample in
scope (`per_run`, `per_file`) it is the value every sample of the row's run shares, else
nothing.
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

- **G-1 Scope.** A run Nessie launched is uploaded on its launch record. A run launched elsewhere may be uploaded too (owner's ruling, 2026-10-23; design #139, 2026-11-12): an admin (`is_superuser`) harvests it from the runs root, or from a results bundle uploaded in the chat (`run-harvest --bundle`), and Nessie reads its identity from the run itself, refusing it unless its `pipeline_info` names the pipeline and release, a committed map exists for that pipeline, and the run folder holds the samplesheet the run used (the only source of the samples' parents). Its build reply opens with NOT LAUNCHED BY NESSIE and says how each sample was matched. An admin may also build workbooks from local copies for testing; those are test-only and refused at upload.
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
| atacseq | the per-replicate files of a sample whose replicates were merged: `<ALIGNER>/merged_library/<sample>_REP<n>.mLb.clN.sorted.bam`, its peaks and its bigWig | A.ATAC | choice: whether to register replicate-level files beside the merged-replicate ones. Today the sample's merged-replicate file wins (G-4) and is registered once for the group, parented by every replicate; the replicates' files are its losing candidates, so they reach its companions or the build report. Rows of their own would need separate rules. A sample with one replicate, or a run with `--skip_merge_replicates`, registers each replicate's own files, parented by its own D.SEQ (`replicate_samples`) |
| atacseq | `<ALIGNER>/merged_replicate/macs2/<PEAK_TYPE>/consensus/consensus_peaks.mRp.clN.bed` and its `featureCounts.txt` (the consensus over the merged-replicate peaks) | A.ATAC | inventory, choice: a second consensus set beside the merged-library one, which the map registers |
| denovotranscript | `evigene/okayset/all_assembled.okay.mrna` (the final assembly, FASTA) | none | type, inventory |
| denovotranscript | `evigene/okayset/all_assembled.okay.aa` (its proteins) | none | type, inventory |
| denovotranscript | `trinity/pooled_reads.fa.gz`, `rnaspades/pooled_reads.transcripts.fa.gz` (assemblies before redundancy reduction) | none | type, inventory, choice: whether to register pre-reduction assemblies at all |
| denovotranscript | `tx2gene/*tx2gene.tsv` (unconfirmed name), as secondary data to `quant.sf` | A.GEX | inventory |
| differentialabundance | `tables/differential/[<paramset>/]<contrast>.deseq2.results_filtered.tsv` (1.5+, a subset of the results table: a companion of the results row) | A.GEX | inventory |
| differentialabundance | `tables/differential/<paramset>/<contrast>_<study>_<method>.annotated.tsv` (2.0.0: the results joined to gene annotation; a companion; its glob must carry the method in its suffix, `*_<method>.annotated.tsv`, as in the per_file companion paragraph above) | A.GEX | inventory |
| differentialabundance | `other/dream/<paramset>/<contrast>_<study>.dream.model.txt`, and each method's `*.model.txt` | A.GEX | inventory |
| differentialabundance | `MethodSettings` on 2.0.0 rows: each paramset has its own settings, which the run's params do not hold, so the field is blank there | A.GEX | inventory: the paramsets file |
| differentialabundance | non-RNA-seq study types (`--study_type affy_array`, `geo_soft_file`, `maxquant`) are named in `Notes`; their `*.matrix.tsv` normalised matrix has no rule, maxquant's results are protein abundance filed as A.GEX, and their samplesheets name no FASTQ, so no parent resolves | A.GEX, other | type, parent |
| eager | a sample's libraries merged after deduplication, `merged_bams/initial/<Sample_Name>_udg<udg>_libmerged_rmdup.bam`, and the BAMs that go into genotyping, `merged_bams/additional/` (with BAM trimming) | A.ALN | choice: whether a merged sample BAM replaces its libraries' rows. A Nessie launch writes one library per D.SEQ, so it never merges |
| eager | the alignments before deduplication, `mapping/<mapper>/` and `samtools/filter/` (the only BAMs with `--skip_deduplication`; unconfirmed names) | A.ALN | ready: later candidates of the `aln` rule; check a real run |
| eager | damage-processed BAMs: `trimmed_bam/` (`--run_trim_bam`), `pmdtools/` (`--run_pmdtools`), `damage_rescaling/` (`--run_mapdamage_rescaling`) | A.ALN | choice: companions of the `aln` row or rows of their own; inventory |
| eager | damage and authentication: `damageprofiler/<library>/` (or `mapdamage/`) misincorporation and length tables, `preseq/`, `qualimap/<sample>/`, `endorspy/` | none | type: no sample type holds a damage profile. Their summary numbers (endogenous DNA, C-to-T at the first base, fragment length, duplication, coverage) are in MultiQC general stats, but no eager 2.5.3 MultiQC table was read, so no `qc_attributes` entry names a key |
| eager | the metagenomic screen: `metagenomic_classification/{malt,kraken}/` and `maltextract/results/` (not offered in Luria launches: no database is staged) | A.TAXP | type: A.TAXP is in `context/sample_types.json` but on no instance and not in the attribute fixture; inventory |
| eager | `sex_determination/` (Sex.DetERRmine), `nuclear_contamination/nuclear_contamination.txt`, `mtnucratio/` | none | type: no attribute holds genetic sex, contamination or the mt/nuclear ratio |
| eager | pileupCaller's EIGENSTRAT and ANGSD's likelihood outputs under `genotyping/`, `consensus_sequence/`, `multivcfanalyzer/` | none | type: not VCFs; not offered in Luria launches |
| eager | a run launched elsewhere: its samplesheet names reads in `R1`, which `uid_resolve` does not read (it reads `fastq_1`), so only a Nessie launch record parents its rows | D.SEQ | parent |
| funcscan | the per-sample screening tables beside the run summaries: AMPcombi's `reports/ampcombi2/<sample>/<sample>_ampcombi.tsv` and comBGC's `reports/combgc/<sample>/combgc_summary.tsv` | A.FSCR | inventory, choice: a per-assembly row beside (or instead of) the run summary row |
| funcscan | the taxonomy-merged summaries written instead of the plain ones with `--run_taxa_classification` (`*_complete_summary_taxonomy.tsv.gz` under `reports/ampcombi2/`, `reports/hamronization_summarize/`, `reports/combgc/`; unconfirmed names: output.md and the TABIX_BGZIP prefixes disagree) | A.FSCR | inventory; check a real run |
| funcscan | each screening tool's own results (`amp/`, `arg/`, `bgc/` including antiSMASH's per-region GenBank files, `cazyme/dbcan/cgc/` and `substrate/`), argNorm's `arg/argnorm/<tool>/*.normalized.tsv`, MMseqs2's `taxonomic_classification/` | A.FSCR, none | inventory, choice: companions of the summary rows, or rows of their own |
| funcscan | the annotation rows' folders (`annotation/<tool>/all/`) come from `conf/modules.config`'s `withName: PYRODIGAL`/`PRODIGAL` blocks, which the aliased `PYRODIGAL_GBK`/`PRODIGAL_GBK` processes may not match; no snapshot saves annotations (`--save_annotations`) | A.GANN | check a real run with `--save_annotations` |
| hlatyping | `hlahd/<sample>/<sample>_final.result.txt` (only with `--tools hlahd`) | A.HLA (its class II fields, `HLA_DRB1_1` to `HLA_DPB1_2`) | inventory; one row per locus, so the rule needs a reader for that layout |
| hlatyping | `yara/<sample>/<sample>.mapped.bam`, or `<sample>_1`/`_2.mapped.bam` when paired | A.ALN? | choice: HLA-only reference, and two BAMs per paired sample |
| methylseq | Bismark's per-read, per-context calls `<aligner>/methylation_calls/methylation_calls/{CpG,CHG,CHH}_{OT,OB}_*.txt.gz`, the targeted bedGraph `*.bedGraph.targeted.bedGraph` (with `--run_targeted_sequencing`), and `bismark/coverage2cytosine/{coverage,reports}/` (with `--cytosine_report` or `--nomeseq`). The CpG coverage and bedGraph have rules (A.MSEQ); their MeanCpGMethylationPercent reads the general-stats column `bismark-percent_cpg_meth`, checked on `4.2.0-local-test` | A.MSEQ | choice: whether to register the per-read calls (large; summarised by the coverage file) and the genome-wide cytosine report, which would need a slot or Notes on A.MSEQ |
| methylseq | `methyldackel/*_{CHG,CHH}.bedGraph` (with `--all_contexts`; `*_{CHG,CHH}.methylKit` with `--methyl_kit` as well), `methyldackel/*.targeted.bedGraph`, and Rastair's TAPS calls `rastair/{call,methylkit}/` (bwa-mem, or bwa-meth with `--taps`). MethylDackel's CpG calls have rules (A.MSEQ): `*_CpG.bedGraph`, or `*_CpG.methylKit`, which `--methyl_kit` writes instead of the bedGraph. With `--taps` and a Bismark aligner, Bismark still calls the reads as bisulfite data, so its calls come out inverted while the row's ConversionMethod says TAPS; methylseq only logs a recommendation to use bwa-mem (`workflows/methylseq/main.nf`) | A.MSEQ | inventory; choice: A.MSEQ has one secondary slot and no Notes for the non-CpG files; TAPS calls need their own DataType and Software; whether to refuse a TAPS run aligned with Bismark |
| pangenome | odgi's graph statistics `odgi_stats/<input>.gfaffix.og.stats.yaml` as A.PANG's `GraphLengthBp`, `NodeCount`, `EdgeCount` (listed in the graph row's `Notes` today) | A.PANG | a reader for odgi's stats YAML (not a MultiQC general-stats table; no run seen) |
| pangenome | `vg_deconstruct/<input>.gfaffix.*.vcf` and its `.decomposed.vcf` (with `--vcf_spec`): variants of the graph against one of its genomes, a companion of the graph row today | A.VCF? | type: A.VCF's parents are reads or alignments, not an assembly's graph; choice |
| pangenome | the intermediate graphs (`seqwish/`, `smoothxg/`, `gfaffix/`, `odgi_build/`, `odgi_unchop/`), the alignments (`wfmash*/`) and the layouts and drawings (`odgi_layout/`, `odgi_viz/`, `odgi_draw/`) | none | choice: steps on the way to the final graph, not registered |
| pangenome | each community's own graph with `--communities` (`FINAL_GFA/<input>.community.<n>.gfaffix.unchop.Ygs.view.gfa`): losing candidates of the graph row, named in its `Notes` | A.PANG | choice: one row per community, parented by the one A.ASM |
| proteinfold | per-model confidence as A.PSTR's `MeanPLDDT`, `PTM`, `IPTM`: `<MODE>/<id>/<id>_plddt.tsv` (per residue, one column per rank), `<id>_ptm.tsv` and `<id>_iptm.tsv` (rank, value). The pLDDT file is the row's secondary file and the others are in `Notes` | A.PSTR | a reader for these layouts (a mean over a column; the rank_0 line); no MultiQC general-stats key is confirmed |
| proteinfold | `<MODE>/<id>/paes/<id>_<rank>_pae.tsv` (predicted aligned error matrices), `<MODE>/<id>/<id>_<MODE>_msa.tsv`, the chainwise (i)pTM tables, `reports/<id>_<MODE>_report.html` and `compare/<id>_comparison_report.html` | A.PSTR | inventory, choice: companions of the model row |
| proteinfold | the lower-ranked models (in `raw/` only with `--save_intermediates`) and `foldseek_easysearch/` hits (with `--skip_foldseek false`) | A.PSTR, none | choice; type for Foldseek's structural-similarity hits |
| proteinfold | `DBs/<mode>/` (the databases or weights a run downloads when no `<mode>_db` is given) | none | ruled out in substance: reference data, not results; not inventoried |
| oncoanalyser | germline calls and the normal's own files: `purple/<tumor>.purple.germline.vcf.gz`, `purple/<tumor>.purple.sv.germline.vcf.gz`, `pave/germline/`, `sage/germline/`, `linx/germline_annotations/`, `purple/<tumor>.purple.driver.catalog.germline.tsv`, `esvee/<tumor>.esvee.germline.vcf.gz` | A.VCF | parent: named after the tumour but holding the matched normal's calls, so a per_sample rule would file them under the tumour's D.SEQ; needs a pair-level rule (as sarek's somatic pairs) |
| oncoanalyser | somatic rows parented by the tumour alone: every rule's file is the tumour/normal pair's result, but its row names only the tumour's D.SEQ | A.VCF, A.CNV, A.MUSP | choice: whether pair outputs name the normal too, the same ruling sarek's `*_vs_*` folders wait for |
| oncoanalyser | `purple/<tumor>.purple.purity.tsv`, `purity.range.tsv`, `purple.qc` (purity, ploidy and the fit) | none | type: no attribute or sample type holds purity and ploidy; A.CNV has neither `File_SecondaryData` nor `Notes` to carry the file |
| oncoanalyser | driver catalogues `purple/<tumor>.purple.driver.catalog.somatic.tsv`, `linx/somatic_annotations/<tumor>.linx.driver.catalog.tsv` | none | type, inventory |
| oncoanalyser | fusions `linx/somatic_annotations/<tumor>.linx.fusion.tsv`, `isofox/<rna>.isf.pass_fusions.tsv` | A.FUS (assay Gene Fusion Detection exists; the sample type does not) | type, inventory |
| oncoanalyser | `lilac/<tumor>.lilac.tsv` (HLA class I alleles and their tumour status) | A.HLA | inventory; a reader for LILAC's one-row-per-allele layout |
| oncoanalyser | `orange/<tumor>.orange.pdf` and `.orange.json` (the summary report), `linx/<tumor>_linx.html`, `qsee/` | none | type |
| oncoanalyser | `chord/` (HRD), `cuppa/` (tissue of origin), `teal/` (telomeres), `virusbreakend/` and `virusinterpreter/` (viral integration), `cider/` (IG/TCR CDR3), `neo/` (neoepitopes), `peach/` (pharmacogenomics), `wisp/` (`--mode purity_estimate`) | none | type, inventory |
| oncoanalyser | MultiQC: `multiqc_report.html` sits at the run root, which no core inventory pattern reaches, and no general-stats key has been seen in a run | D.SEQ | inventory; QC keys need a real run (G-7) |
| oncoanalyser | a run started from BAM or CRAM (launched elsewhere): the `filepath` column is not one of `uid_resolve._PATH_COLUMNS`, so only a launch record resolves its parents | D.SEQ, A.ALN | parent |
| scdownstream | `10_reports/artifacts/concatenated.h5ad` (with `--qc_only`: the QC'd object, no clustering) and `02_quality_control/finalized/<sample>_qc.h5ad` (with `--save_intermediates`) | A.SCXP | type: A.SCXP's declared parents are D.SEQ, DNA and A.ALN, and the assay Single Cell Expression Analysis takes no A.SCXP parent |
| scdownstream | integrated objects `03_combine/integrate/<tool>/*.h5ad`, cluster and UMAP objects under `05_cluster_dimred/`, cell-type annotations under `04_celltypes/` | A.SCCL | choice: each is an intermediate of `09_finalized/merged.h5ad`, which holds them all; registered only if a ruling wants them |
| scdownstream | differential expression `06_per_group/**/differential_expression/**/*_results.parquet`, pseudobulk matrices, decoupler scores, LIANA and cell2cell plots | none | type, inventory, per contrast |
| scdownstream | QC from `11_multiqc/` and `10_reports/qc-report.html` | A.SCXP | inventory; no MultiQC key has been seen in a run |
| raredisease | `alignment/<sample>_sorted_md.cram` (with `--save_all_mapped_as_cram`, kept off in Luria launches) and `alignment/<sample>_sorted_md_primary_contigs.cram` (`--save_noalt_mapped_as_cram`) | A.ALN | ready: a second rule with `DataType` CRAM |
| raredisease | annotated and ranked VCFs: `annotate_snv/genome/`, `annotate_snv/mitochondria/`, `annotate_sv/`, `rank_and_filter/<case>_{snv,sv,mt}_ranked_{clinical,research}.vcf.gz` (need the VEP cache, gnomAD, vcfanno resources and rank models, not staged) | A.VCF | choice: whether a ranked clinical or research VCF replaces the call set as a first candidate; annotation runs in no Luria launch |
| raredisease | mobile elements: `call_mobile_elements/<case>_mobile_elements.vcf.gz`, `annotate_mobile_elements/` (skipped: `mobile_element_references` not staged) | A.VCF | choice |
| raredisease | repeat expansions: `repeat_expansions/<case>_repeat_expansion_stranger.vcf.gz`, each sample's `_repeat_expansion.vcf` and `_exphunter_sorted.bam` (skipped: `variant_catalog` not staged) | none | type: an STR genotype is not an A.VCF of variant calls |
| raredisease | mitochondrial deletions: `call_sv/<case>_saltshaker.vcf.gz`, `call_sv/<sample>.saltshaker*`, `call_sv/<sample>_mitochondria_deletions.txt` | none | type |
| raredisease | `peddy/` (pedigree, sex and relatedness checks), `ngsbits_samplegender/` (inferred sex), `smncopynumbercaller/` (SMN1/SMN2 copy number), `gens/` (skipped) | none | type |
| raredisease | MultiQC general-stats QC (mosdepth coverage, Picard alignment and duplication metrics, VerifyBamID contamination) | A.ALN, D.SEQ | the run fixture is an nf-test snapshot, which holds no MultiQC values, so no key is confirmed |
| riboseq | `alignment/star/sorted/<sample>.transcriptome.sorted.bam` (+ `.bai`) | A.ALN | ready: a companion of the genome BAM's row |
| riboseq | `alignment/star/deduplicated/<sample>.umi_dedup.genome.sorted.bam` (with `--with_umi`, or 2.0.0's per-library `with_umi` samplesheet column; unconfirmed name) | A.ALN | ready: a first candidate, ahead of the sorted BAM |
| riboseq | `quantification/salmon/salmon.merged.{gene_tpm,transcript_counts,transcript_tpm}.tsv` | A.GEX | ready |
| riboseq | `quantification/salmon/salmon.merged.*.SummarizedExperiment.rds` | A.GEX | ready |
| riboseq | `quantification/salmon/<sample>/quant.sf` | A.GEX | ready |
| riboseq | `quantification/inframe_psite/gene_counts.tsv` (2.0.0, written by default under `--te_quantification_method plastid_psite`: Salmon's length-scaled RNA-seq columns with the Ribo-seq columns replaced by in-frame P-site counts) | A.GEX | inventory; choice: one matrix mixing two count definitions, which `Matrix` and `Notes` would have to say |
| riboseq | `quantification/{salmon,kallisto}_te_pseudo/*.merged.*.te_pseudo.*` (2.0.0, `--te_quantification_method pseudo` with contrasts) | A.GEX | inventory |
| riboseq | `translational_efficiency/anota2seq/<contrast>.*.anota2seq.results.tsv`, `*.Anota2seqDataSet.rds`; 2.0.0 adds `translational_efficiency/deltate/<contrast>.*.deltate.results.tsv` and the ORF-level `translational_efficiency/orf_level/{anota2seq,deltate,dotseq}/`. Nessie launches riboseq without a contrasts file, so its own runs write none | A.GEX | inventory, per contrast |
| riboseq | `orf_predictions/ribotish/*_pred.txt`, `orf_predictions/ribocode/*.txt` (2.0.0 default callers), and the opt-in `orf_predictions/ribotricer/*_translating_ORFs.tsv` (default in 1.x, `--run_ribotricer` from 2.0.0), `orf_predictions/price/`, `orf_predictions/rpbp/` (ORF calls) | none | type, inventory |
| riboseq | `orf_predictions/catalogue/*.catalogue.{bed12,tsv}` and `quantification/orf_level/orf_psite_counts.tsv` (2.0.0, `--extended_orf_analysis`). Its `orf_class` is positional only (`canonical_cds`, `uORF`, `uoORF`, `dORF`, `doORF`, `intORF`, `novel_u`, `other`) with no `smORF` class: short ORFs carry an `is_smorf` column instead (#226) | none | type, inventory |
| riboseq | `psites/ribowaltz/*.psite.tsv`, `*.cds_coverage_psite.tsv` (P-site coverage; `ribowaltz/` in 1.2.0), and 2.0.0's `psites/plastid/` offsets and tracks | none | type, inventory |
| riboseq | `coverage/<sample>.{forward,reverse,unstranded}.bigWig` (2.0.0) | none | type, inventory |
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
| rnasplice | DEXSeq, edgeR, DTU, rMATS and SUPPA2 results under `<aligner>/`. 2.0.0 renamed several: DEXSeq's per contrast `{contrast}.dexseq.{dataset,results,gene_qvalues}.{rds,tsv}` (1.0.4: `DEXSeqResults.{contrast}.csv` and kin), rMATS's `rmats/prep/<sample>.rmats` and `rmats/{contrast}{_paired}/` (1.0.4: `rmats/{contrast}/rmats_post/`), SUPPA's `suppa/generate_events/per_local_event/merged_events/` (1.0.4: a top-level `mergeevents/`) and `suppa/clustergroups/{local,transcript}_<contrast>_groups.txt`. A rule that spans both releases needs a `versions` override or a candidate per layout; 2.0.0's DEXSeq fold changes and SUPPA dPSI also have the opposite sign to 1.0.4's | none (splicing) | type, inventory, per contrast |
| rnasplice | `isoformswitchanalyzer/isoformswitchanalyzer_{summary,isoformfeatures}.csv`, `switchlist.rds` (2.0.0, on by default upstream; Nessie launches it only when asked) and LeafCutter's `leafcutter/*` (2.0.0, `--leafcutter`) | none (splicing) | type, inventory |
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
| taxprofiler | a second database of one classifier (two `kraken2/<db>/` folders): the per-sample rule takes one profile per sample, so the other is a candidate the curator chooses between | A.TAXP | choice: one row per sample per database |
| taxprofiler | `ClassificationDatabase` on the per-sample rows: the database is the profile's folder name (`kraken2/<db_name>/`), which no token reads | A.TAXP | a token for the row file's folder |
| taxprofiler | `<tool>/<tool>_<db>_combined_reports.txt` (each tool's own run-wide table) | A.TAXP | inventory, choice: the tool-native twin of the TAXPASTA table |
| taxprofiler | MALT's `malt/<db>/<sample>_<n>.merged.txt.gz` and `.rma6` (named per read file), Melon's `melon/<db>/<sample>_<db>/*.tsv`, MetaCache's `metacache/<db>/<sample>_<db>.mapping.txt` (`*.abundances.txt` only with `--metacache_abundances`) | A.TAXP | inventory, choice: sample attribution and which file is the profile |
| taxprofiler | `krona/<tool>_<db>.html` | none | type |
| all | A.VCF rows link no assay where the house assay "Variant Call Filtering" does not exist; it is a pending `create_internal` in `context/assay_mappings.json` | A.VCF | a house assay to create |

Ruled out on purpose, so not on the list above: differentialabundance's `all.vst.tsv` / `all.rlog.tsv` (regenerated from the normalised counts), its plots, Shiny app, `*.sizefactors.tsv` and session logs, and the upstream counts matrix as `Parent` (G-6: the stager reads nothing outside the run folder; rows keep the sequencing samples). A map that sets `claims_every_file` (sarek, methylseq) lists its own ruled-out files in `unmapped_files`, each with its reason. atacseq and chipseq list theirs too, without `claims_every_file`: no fixture lists their files.

## Fixtures

Evidence for a rule (G-7) is a fixture folder under
`NessieAI/tests/ns/fixtures/reingest_runs/`; how to make one, and how its snapshot is
regenerated, is in `NessieAI/tests/ns/fixtures/reingest_runs/README.md`.

### Maps written from the docs

The owner may waive G-7's fixture for a release with no nf-test snapshot and no usable
published results. Its file rules then come from the release's pinned docs/output.md
(committed as `NessieAI/tests/chat_nextseek/fixtures/nfcore/<pipeline>.output.md`), checked
against its `conf/modules.config`, and `test_doc_only_maps.py` checks that every folder a
rule names is one that output.md documents (`DOC_ONLY`). A map file cannot say this itself
(`PipelineMap` forbids unknown keys), so `DOC_ONLY` and a row here do. Delete both when run
fixtures cover every rule's folder.

atacseq 2.1.2 and chipseq 2.1.0 began this way and have left it: each release's broad- and
narrow-peak `-profile test` runs (`<version>-local-test` and `<version>-local-narrow`, made
with `scripts/reingest/make_fixture.sh --local`) match every pattern of every rule, so
neither is in `DOC_ONLY`. The maps written from the docs now:

| Pipeline | Release | Evidence | Why no fixture |
|---|---|---|---|
| pangenome | 1.1.3 | pinned docs/output.md, checked against `conf/modules.config` and `subworkflows/local/pggb.nf` at the tag (output.md names the `FINAL_GFA/` graph `*.view.og`; the module writes `<input>.gfaffix.unchop.Ygs.view.gfa`) | the release has no pipeline-level nf-test snapshot; its AWS results were not read. A `-profile test` run (`DRB1-3123.fa.gz`, `n_haplotypes 12`) would replace this row |
| oncoanalyser | 3.0.0 | pinned docs/output.md, checked against `subworkflows/local/prepare_outputs/main.nf` at the tag (each WiGiTS tool's folder is published as `<group_id>/<tool>/`; REDUX and Picard BAMs as `<group_id>/alignments/<sample_id>/`). Rules are per tumour (or RNA) sample, named by the file's `<tumor_dna_id>` prefix | its nf-test snapshot and AWS results were not read, and no run has been made; a `-profile test` run through Nessie (it downloads the Hartwig bundle) would replace this row |
| scdownstream | commit d9264b35 | pinned docs/output.md at the commit (`09_finalized/merged.h5ad`, the final object) | no release, so no AWS results; a `-profile test` run at the commit would replace this row |

### taxprofiler and funcscan: names from the nf-test snapshots

Neither map is in `DOC_ONLY`: both releases' output.md disagree with `conf/modules.config` on
folders (taxprofiler documents a `bracken/<db>/<db>/` folder; funcscan documents
`reports/ampcombi/`, `prodigal/` and `comBGC/` where it publishes `reports/ampcombi2/`,
`annotation/<tool>/<category>/` and `reports/combgc/`), and both name their database folders
`<db_name>`, which `test_doc_only_maps.py` does not read as a placeholder. Their rules come
from `conf/modules.config` at the tag, with every file name checked against the release's own
nf-test snapshots (taxprofiler `default`, `test_motus`, `test_fastpnonpareilkrakenuniq`;
funcscan `default` and `test_cazyme_pyrodigal`), and `test_taxprofiler_funcscan_maps.py` runs
both maps over those names. No run fixture exists yet (G-7: `import_evidence.py snapshot` would
make one), and no MultiQC key is mapped: taxprofiler's only QC entry is the report link.

taxprofiler names a per-sample profile `<sample>_<db>.<tool>...` with `--perform_runmerging`
and `<sample>_<run>_<db>.<tool>...` without; both start with the sample, so the harvest
attributes either. Every rule is optional: a classifier runs only with its database and
`--run_<tool>`, TAXPASTA only with `--run_profile_standardisation`. funcscan's A.GANN rules
(one per `--annotation_tool`, one `alternative_group`) match only with `--save_annotations`;
its run summaries are per_run A.FSCR rows parented by every assembly, and dbCAN's per-sample
overview a per_sample one. A foreign funcscan run cannot resolve its parents by FASTA path
(`uid_resolve._PATH_COLUMNS` has no `fasta`), so only a Nessie launch record parents its rows.

### proteinfold: one rule per prediction mode

The 2.0.0 map has one optional A.PSTR rule per `--mode` (esmfold, colabfold, alphafold2 under its
`standard/` or `split_msa_prediction/` folder, boltz, rosettafold_all_atom, alphafold3, helixfold3,
rosettafold2na): each sample's `<MODE>/top_ranked_structures/<id>.pdb`, with its pLDDT table as the
secondary file. Predictions of two modes coexist (a run of `--mode esmfold,colabfold`, or a reused
folder), so the rules share no `alternative_group`. Each rule is checked against the release's own
nf-test snapshot of that mode (`2.0.0` is alphafold2 standard, `2.0.0-<mode>` the rest), imported with
`import_evidence.py snapshot ... --variant <mode> --param mode=<mode>`. Those tests are stub runs:
the paths are real, the files empty, and no MultiQC value exists, so the map reads no QC key. The
samplesheet names a row's sequence `id` (`samplesheet_sample_column`).

### raredisease: one VCF per family

nf-core/raredisease 3.1.2 joint-genotypes each family: its SNV, mitochondrial and SV VCFs are named
after the samplesheet's `case_id` (`call_snv/genome/<case>_snv.vcf.gz`,
`call_snv/mitochondria/<case>_mitochondria.vcf.gz`, `call_sv/<case>_sv.vcf.gz`), so each is a
`per_file` A.VCF row parented by every sample of that case (`parents_by_samplesheet: case_id`).
The fixture `3.1.2` is the release's own nf-test snapshot (`tests/default.nf.test.snap`, a stub
run of one trio), and it disagrees with output.md: the merged SV VCF is `call_sv/<case>_sv.vcf.gz`,
not `call_sv/genome/<case>_sv_merge.vcf.gz`. The map claims every file; what it leaves out is in
its `unmapped_files`, and `test_raredisease_map.py` checks the case rows' parents. Each sample's
`alignment/<sample>_sorted_md.bam` is its A.ALN row.

### eager: rules from main.nf, with no fixture

nf-core/eager 2.5.3 is a DSL1 release with no nf-test, and its AWS results were not read, so no
run fixture exists; its map is not in `DOC_ONLY` either, because output.md is stale where the
`aln` rule reads (it documents `deduplication/dedup/<sample>/`, while main.nf's `dedup` and
`markduplicates` publish `deduplication/<Library_ID>/<Library_ID>_rmdup.bam`). Its rules come from
main.nf's `publishDir` and output names at the tag: that BAM per library (Library_ID is the
sample's UID in a Nessie launch, so the harvest attributes the file to its sample) and each
genotyper's `genotyping/<Sample_Name>.{unifiedgenotyper,haplotypecaller,freebayes}.vcf.gz`.
Three harvest paths exist for this release alone, all checked by `test_eager_dsl1_run.py` on a
synthetic run: the samplesheet is `samplesheet.tsv` (`Sample_Name`, read as a TSV after every CSV
candidate), the software versions are `pipeline_info/software_versions.csv`, and run.sh launches
it as `NXF_VER=22.10.8 nextflow run`, which `parsers.parse_launch_params` reads past. The QC entries
are the report link and the genome only. A `-profile test` run (G-7) would replace this note.

### atacseq: every output is A.ATAC

By the owner's ruling of 2026-11-16, ATAC-seq from every source is the sample type A.ATAC under
the house assay ATAC-Seq, whatever its parent (DNA for imported BAMs, D.SEQ for an nf-core/atacseq
run). So all five atacseq rules, the BAM as well as the peaks, bigWig and consensus peaks, write
A.ATAC rows; A.ALN and A.CHRM (ChIP-seq, CUT&RUN and the like) hold no atacseq output. Each row
carries `DataType`, `Scientist`, `SampleCreationDate`, `Aligner`, `Genome` (`$params.genome`; A.CHRM
calls it `Assembly`) and `Pipeline`, and `Name` from `row_defaults`. A.ATAC has `Notes`, so a merged
group's BAM row names its replicates' own BAMs there rather than in the build report. Where the type
and assay exist on each instance: `docs/reingest-seeding-rollout.md`; on an instance without A.ATAC
the build stops at `UNKNOWN_SAMPLETYPE`.

### atacseq and chipseq: QC without general stats

Both releases' `assets/multiqc_config.yml` set `exclude_modules: general_stats`, so no
`multiqc_general_stats.txt` is written, and `data_format: yaml`, which atacseq's MultiQC 1.13
does not apply to its tables (they are `.txt`) and chipseq's MultiQC 1.23 does: it writes no
table files at all, only plot data and `multiqc_data.json`, which the harvest does not read.
Both maps set `replicate_samples`, so each replicate's own D.SEQ takes the QC backfill; only
lanes of one replicate, a multi-run sample, get none (`mapper._NO_BACKFILL`), as in every
pipeline. Where each value comes from (owner's ruling of 2026-10-30 on the peak-quality
metrics: a peak set's own on its peak row, A.ATAC for atacseq and A.CHRM for chipseq, a library's on its D.SEQ):

| Attribute | Row | atacseq 2.1.2 | chipseq 2.1.0 |
|---|---|---|---|
| `DuplicationPercent` | D.SEQ | `multiqc_picard_dups` (module table; Picard's 0-1 fraction, x100 by `$derived.duplication_pct`) | `picard_deduplication.yaml`, the bar plot's read counts: duplicates over duplicates plus unique, Picard's own rate |
| `MappedPercent` | D.SEQ | `multiqc_samtools_flagstat.txt`, the first of four flagstat sections (each run's own alignment, rows `<sample>_REP<n>_T<run>`; checked against `multiqc_sources.txt`), column `mapped_passed_pct` only | none: the release publishes flagstat only for the filtered alignment (100% mapped by construction); the per-library one is only in `multiqc_data.json` |
| `FRiPPercent`, `PeakCount` | peak row (A.ATAC, A.CHRM) | MultiQC's custom-content bar graphs (`shape: bargraph`): `mlib_*` for a replicate's peaks, `mrep_*` for a merged replicate's, through `RunManifest.group_metrics` | the per-sample `<sample>.FRiP_mqc.tsv` and `.peak_count_mqc.tsv` (`sample_files`) |
| `NSC`, `RSC` | D.SEQ | (no phantompeakqualtools) | `<sample>.spp_nsc_mqc.tsv`, `.spp_rsc_mqc.tsv`; inputs get them too |
| `FingerprintJSD` | D.SEQ | `<sample>.mLb.clN.plotFingerprint.qcmetrics.txt`, `Synthetic JS Distance` | the same file, the IP's own row; inputs get none |
| `TSSEnrichment` | D.SEQ | `<sample>.ataqv.json`, `metrics.tss_enrichment`; null, so absent, for single-end libraries | (no ataqv) |

FRiP is reads in peaks over mapped reads, a 0-1 fraction in both releases' scripts, so
`$derived.frip_pct` multiplies it by 100. Neither release passes plotFingerprint a
`--JSDsample`, so only the synthetic Jensen-Shannon distance (against uniform coverage) is
written. An ataqv JSON lists every peak; a large run's files can exceed the harvest caps, and
those samples then have no TSS enrichment (recorded as truncated). Consensus peak rows carry
no FRiP or peak count: no table holds one for them. Library complexity (Preseq is a curve;
Picard's `ESTIMATED_LIBRARY_SIZE` is ruled out) has no attribute.

atacseq's A.ATAC and chipseq's A.CHRM rows carry a `Name` (`@row_name`) and, for chipseq, the `Antibody` their
replicate's samplesheet names (`$samplesheet.antibody`; blank for the input controls),
both as `row_defaults`. chipseq's per-antibody consensus peaks are one `per_file` row
each (`consensus_narrow` or `consensus_broad`, one `alternative_group`), parented only by
that antibody's samples (`parents_by_samplesheet`), with its `featureCounts.txt` as
companion. A companion of a peak row (atacseq's consensus `featureCounts.txt` on its A.ATAC row)
goes to `File_SecondaryData`, then `Notes`, or to the build report on an instance whose type lacks
them. A
merged-replicate group's row names its replicates' own peaks or bigWigs in `Notes` (or the
build report), never in `File_SecondaryData`: one replicate's output is not the group's
secondary data (G-4).

### cutandrun

The 3.x map (`applies_to_versions` `>=3.1,<3.3`, pinned release 3.2.2) is checked against two fixtures:
`3.2.2-pytest`, the output paths 3.2.2's pytest-workflow tests assert, merged (the release has no nf-test
snapshot, and its AWS `test_full` run published only `pipeline_info/`); and `3.1-aws-test_full`, the last
complete AWS run, for the MultiQC keys and the plotFingerprint metrics. 3.1 differs from 3.2 in its file names
only in the consensus peaks, which gained the caller's name (`<group>.seacr.consensus.peaks.awk.bed`), and in
3.2's `--remove_linear_duplicates` BAM: the `<3.2` override keeps 3.1's consensus name.

Samples are replicates, `<group>_R<n>` (`samplesheet_sample_column` `group`, `replicate_name`). The aln rule takes
the most processed BAM of `linear_dedup/`, `dedup/` (IgG controls by default; targets with
`--dedup_target_reads`) and `markdup/`. Every replicate gets a bigWig row; peak rows (SEACR, MACS2 narrow or
broad) skip the controls, which have no peaks. Each group's consensus is one `per_file` row parented by its
replicates, and `all_samples` (`--consensus_peak_mode all`) one row parented by every target.

| Attribute | Row | Source |
|---|---|---|
| `MappedPercent` | D.SEQ | general stats, `Bowtie2 (target)` overall alignment rate |
| `DuplicationPercent` | D.SEQ | `multiqc_picard_dups.txt`, x100 by `$derived.duplication_pct` |
| `GCPercent` | D.SEQ | general stats, `FastQC (raw)`: the folded row holds read 2's report |
| `FingerprintJSD` | D.SEQ | `04_reporting/deeptools_qc/<sample>.plotFingerprint.qcmetrics.txt`, IgG controls too |
| `FRiPPercent`, `PeakCount` | A.CHRM peak row of the primary caller | MultiQC bar graphs `multiqc_primary_fripscore_plot.txt`, `multiqc_primary_peakcounts_plot.txt` |

FRiP and the peak count are computed for the primary caller's peaks only, the first of `--peakcaller`, so the
`@seacr_*` and `@macs2_*` tokens (`NessieAI/ns/reingest/pipelines/cutandrun.py`) put them on that caller's rows
and leave the other caller's blank. The FRiP table's name is read off the release's custom-content header
(`assets/multiqc/frip_score_header.txt`, plot id `primary_fripscore_plot`) and has not been seen in a run: 3.1's
AWS run wrote no FRiP table. A.CHRM has no attribute for the peak caller, so with `--peakcaller seacr,macs2` a
replicate's two peak rows differ only by `DataType` and file. The spike-in alignment rate has no D.SEQ attribute
and is ruled out. Antibody stays blank: the samplesheet has no antibody column.

### methylseq's variant fixtures

The 4.2.0 map's file rules are checked against seven fixtures, each one nf-test snapshot
test: `4.2.0` (the default `-profile test`, Bismark), and `4.2.0-<variant>` for bwameth,
bwameth-rrbs, bwameth-skip_deduplication, bwameth-save_align_intermeds, bismark-rrbs and
bismark-skip_deduplication, imported with `import_evidence.py --test --variant --param`.
With `--rrbs` or `--skip_deduplication` deduplication is skipped, so the sorted BAM under
`alignments/` is the aln row's file. With `--save_align_intermeds`, bwa-meth's unsorted
`alignments/<sample>.bam` is ruled out, as Bismark's unsorted BAM is, and the sorted BAM
before deduplication is a losing candidate of the aln rule: A.ALN's one secondary slot holds
the index and A.ALN has no `Notes`, so it is named in the build report. A snapshot holds no
MultiQC values, so the map's QC keys are checked against the release's own `-profile test`
runs by `test_qc_completeness_dna.py`: `4.2.0-local-test` (Bismark) and `4.2.0-local-bwameth`
(`--aligner bwameth`, whose samtools and Picard keys are the QC rules' alternates).

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
