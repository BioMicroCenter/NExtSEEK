---
editor_options: 
  markdown: 
    wrap: 72
---

# What else could Nessie run? — nf-core capability review

**Branch:** `dev-v3-merge` · **Started** 2026-08-04 · **Last revised**
2026-08-05

Began as "what else could Nessie run?". Answering it surfaced a launch
bug that had made three of the eight existing pipelines unrunnable, so
the survey and the fixes are recorded together.

|                                     | Started |    Now |
|-------------------------------------|--------:|-------:|
| Pipelines catalogued                |       8 | **31** |
| Generating a valid launch command   |       5 | **31** |
| …that we hold identifiable data for |       5 | **13** |
| Verified end-to-end on Luria        |       2 |  **2** |

**Data source:** direct query against `seek_production` (51,359 samples,
104 sample types).

## The rule for adding a pipeline

> "Keep flagging in the table if no data exists to test, but it's okay
> if there isn't. The goal is to add functions in hopes that users with
> that data will use NExtSEEK." — project owner, 2026-08-05

**No matching data is a label on the row, not a reason to skip.** That
is an adoption argument: a lab arriving with PacBio reads or bacterial
isolates should find the capability present, not be told it could be
added. A catalog entry costs ~half a day and no runtime.

The bar is therefore: **config-only against its pinned schemas, and an
input shape our machinery can produce.** Rejections are for shapes we
cannot build — a second samplesheet, a glob instead of a cohort, an
experimental design nobody can infer.

## Two rules learned the hard way

1.  **The pinned schemas are the authority.** Fetch both
    `assets/schema_input.json` (required columns, enums) and
    `nextflow_schema.json` (required params; which of
    `genome`/`fasta`/`gtf` are declared) at the tag. Deriving
    requirements from `assets/samplesheet.csv` was wrong 6 times out of
    22; deriving them from the repo description overstated the
    config-only count roughly fourfold.
2.  **"Config-only by schema" is a floor, not a ceiling.** A
    schema-*optional* param can still be functionally required and
    silently wrong if unset — `viralrecon`'s `platform`/`protocol`,
    `metatdenovo`'s `orf_caller`. An ORF caller that assumes no introns
    will happily call genes in a eukaryotic transcriptome.

------------------------------------------------------------------------

## What's actually in NExtSEEK

| Modality | Samples | Types |
|---------------------------|-----------:|-------------------------------|
| **Imaging** | **13,680** | `D.IMG` 13,386 · `A.IMG` 278 · `D.MSI` 16 |
| **Flow / mass cytometry** | **6,028** | `D.FLOW` 5,210 · `D.FCS` 752 · `A.FLOW` 35 · `D.CYTOF` 30 |
| **Sequencing** | **2,273** | `D.SEQ` 2,057 · analyses 216 |
| **Mass spec proteomics** | **416** | `D.MSP` 208 · `A.MSP` 145 · `A.MUSP` 46 · `D.MSI` 16 |
| **NMR** | **75** | `D.NMR` 54 · `A.NMR` 21 |

Whole-database split: biological material 27,690 (53.9%) · `D.*` raw
data 22,779 (44.4%) · **`A.*` analysis results 890 (1.7%)**.

### What the sequencing actually is

`LibraryStrategy` on `D.SEQ` sums to exactly 2,057, so the field is
complete and authoritative:

| `LibraryStrategy` | Samples | Covered by |
|-------------------------|----------:|--------------------------|
| Amplicon (2 spellings) | **1,179** | `ampliseq`, `crisprseq` |
| RNA-seq (3 spellings) | **507** | `rnaseq`, `rnavar` |
| WGS | 188 | `sarek` |
| scRNA-seq | 102 | `scrnaseq` |
| Targeted Capture | 69 | `sarek`, `hlatyping` |
| Hi-C | 12 | `hic` |

A keyword sweep of the full metadata confirms what is **absent**: ATAC
0, ChIP 0, methylation/bisulfite/WGBS/RRBS 0, small-RNA/miRNA 0,
Ribo-seq 0, metagenomic 0, CRISPR 0, and **zero long-read samples of any
platform** (every `Sequencer` is Illumina, plus 26 Singular G4). Also: 19
viral mentions in the whole 51,359, and the 1,402 `BAC` samples have **no
`D.SEQ` children** — the 3,975 mycobacterium/TB mentions sit on host
samples (`MUS` 626, `PAV` 254, `PAT` 89, `NHP` 57), i.e. infected
animals, not sequenced isolates.

### Two hard negatives on the largest data classes

-   **Imaging (13,680) — wrong shape.** Sampled `D.IMG` metadata is
    conventional fluorescence microscopy: single `.tif` from Keyence
    BZ-X700 / Zeiss LSM 880, DAPI/EdU, hosted in OMERO, often tagged
    `"Type": "Representative"`. `mcmicro` wants multi-cycle
    whole-slide OME-TIFF plus a markersheet; `molkart`, `spatialvi` and
    `imcyto` want other instruments entirely. Revisit only if a
    multiplexed whole-slide subset exists that I did not find.
-   **Flow cytometry (6,028) — no nf-core path exists at all.**
    `imcyto` is *imaging* mass cytometry, not FCS. Serving these is an
    R-ecosystem conversation.

------------------------------------------------------------------------

## Luria verification status — **maintain this table**

Catalogued is not the same as working. Everything below passes unit
tests proving the generated `run.sh` is well-formed for that pipeline's
declared schema; that is **not** the same as Nextflow accepting it, or
the run completing. Three pipelines sat catalogued-but-unlaunchable for
months because nobody tracked the distinction.

**Three states.** ✅ a real run completed · ❌ could be run, hasn't been
· ⛔ **no identifiable data of that kind exists**, so there is nothing to
run and no test to schedule. Update this table whenever a pipeline is
launched, and record the run directory — it is the evidence.

18 of the 31 are ⛔. Ten were added deliberately under the adoption rule;
**seven predate this document** (`atacseq`, `chipseq`, `methylseq`,
`smrnaseq`, `riboseq`, `crisprseq`, `mag`) and were assumed testable
until this was measured.

| Pipeline | Status | Evidence / what to do |
|---|---|---|
| `rnaseq` | ✅ verified | Real run `nfcore_rnaseq_260723_205359_0` |
| `scrnaseq` | ✅ verified | Real run `nfcore_gideon-4wk_260711_024438_0`; only pipeline with a Luria provisioning script |
| `fetchngs` | 🟡 partial | Exercised as a pre-stage inside other runs; never launched standalone |
| `atacseq` | ⛔ no data | 0 `D.SEQ` mention ATAC |
| `chipseq` | ⛔ no data | 0 mention ChIP |
| `methylseq` | ⛔ no data | 0 mention methylation/bisulfite/WGBS/RRBS. Its `--gtf` fault was real and fixed; the retest it called for was never possible |
| `sarek` | ❌ untested | Was sending `--gtf`, which it rejects; fixed 2026-08-04. First run since is the real test |
| `ampliseq` | ❌ **never worked** | Was sent all three reference flags and declares none. **Best-represented library type we hold** (1,179 amplicon). A first successful run is the proof |
| `seqinspector` | ❌ untested | Reference-free; cheapest end-to-end proof of the launch path |
| `hlatyping` | ❌ untested | Carries its own reference |
| `smrnaseq` | ⛔ no data | 0 mention small-RNA/miRNA. Confirm `mirtrace_species` elicitation if data arrives |
| `riboseq` | ⛔ no data | 0 mention Ribo-seq. Confirm the `type` column is asked for, not defaulted |
| `hic` | ❌ untested | First run per genome pays a bowtie2 index build |
| `rnavar` | ❌ untested | Confirm BQSR is genuinely skipped |
| `crisprseq` | ⛔ no identifiable data | 0 mention CRISPR; the 1,179 amplicon libraries are labelled "DNA barcoding". **Ask a curator.** Elicitation itself is verified working in the container |
| `nanoseq` | ⛔ no data | 0 long-read samples, any platform |
| `mag` | ⛔ no data | 0 mention metagenomics. `short_reads_1/2` rename is unit-tested |
| `bamtofastq` | ❌ blocked on data | Verified end-to-end against the real 32 `A.ALN` rows, but all are archive-only — **no BAM has a cluster path** |
| `bacass` | ⛔ no data | No isolate sequencing: `BAC` has no `D.SEQ` children |
| `bactmap` | ⛔ no data | Same. Confirm `reference` elicitation when data arrives |
| `pacvar` | ⛔ no data | No long-read data. Confirm the BAM is instrument output, never an aligner's |
| `viralrecon` | ⛔ no data | 19 viral mentions in 51,359 samples. Confirm `platform`+`protocol` elicit before `primer_set` |
| `viralmetagenome` | ⛔ no data | Same |
| `viralintegration` | ⛔ no data | Same. Pinned at 0.1.1, the only release |
| `magmap` | ⛔ no data | No metagenomic cohorts. Confirm `genomeinfo` elicitation |
| `metatdenovo` | ⛔ no data | Same. Confirm `orf_caller` elicitation — the prokaryote/eukaryote choice is silent if wrong |
| `denovotranscript` | ❌ untested | Data exists (507 RNA-seq) but all our organisms have references, so `rnaseq` usually wins |
| `detaxizer` | ❌ untested | Runnable on anything. No reference, no elicitation, 2,057 candidates — **best first real test** |
| `fastqrepair` | ❌ untested | Runnable on anything, but a remedy: use it when a run fails on unreadable input |
| `genomeassembler` | ⛔ no data | No long-read data. Confirm the platform-derived read column picks `ontreads`/`hifireads`, and that Illumina lands in neither |
| `pathogensurveillance` | ⛔ no data | No isolate sequencing. Confirm `sequence_type` is stamped and `report_group_ids` carries a real grouping |

**Suggested order.** `detaxizer` first — reference-free, no elicitation,
and it applies to any of the 2,057 samples. Then `ampliseq`, which
exercises the empty-reference-flag case, covers our largest library type
and has never once run. Then `crisprseq` to prove the elicitation loop
end to end. The index-building ones (`hic`, `rnavar`) last, since a slow
build makes failure expensive to diagnose.

**"Verified" means** Nextflow accepted the parameters and the run
reached completion — not that `sbatch` returned a job id.

------------------------------------------------------------------------

## The four mechanisms built

1.  **Reference flags gated on each pipeline's own schema.** A catalog
    field declares which of `genome`/`fasta`/`gtf` that revision
    accepts; nothing else is emitted. nf-schema aborts a run on an
    unrecognised param, which is what had silently broken `methylseq`,
    `sarek` and `ampliseq`.
2.  **`required_user_params` — the wizard can ask.** A template declares
    values nobody can derive, with a definition, a worked example and a
    validation pattern; `configure_run` refuses to build a run until
    they are answered. Enforced in code, not the prompt, because the
    failure is silent: a wrong CRISPR guide reports a wrong editing
    efficiency rather than erroring. Used by `crisprseq`, `smrnaseq`,
    `hic`, `nanoseq`, `bactmap`, `viralrecon`, `magmap`, `metatdenovo`.
3.  **Cohorts can resolve to `A.*` analysis records.** `bamtofastq`
    resolves to `A.ALN` and the emitter finds a BAM/CRAM, so registered
    NExtSEEK outputs are now a legal pipeline *input* — the capability
    `differentialabundance` needs.
4.  **A column can be chosen, or filled, from the sequencing platform**
    in the sample's own metadata, per row. `genomeassembler` names its
    read column `ontreads` or `hifireads`; `pathogensurveillance` wants
    the platform as a value in `sequence_type`. Neither could come from
    the static alias map, and neither could be elicited —
    `write_samplesheet` runs *before* `configure_run`. Reading metadata
    is earlier, needs no question, and handles a mixed-platform cohort
    that a single per-run answer could not express.

------------------------------------------------------------------------

## The schema census (2026-08-05)

Every pipeline in the `nf-core` org tagged `nf-core`+`pipeline`,
non-archived and non-forked, with both schemas fetched at its latest
release tag. Population: 138 — but `nf-core/tools` is the community's
Python package, mis-tagged, so the real figure is **137**. Raw
per-pipeline output is committed at
`docs/nfcore-schema-census-2026-08-05.json`.

| | Count |
|--------------------------------------|------:|
| Has a release (pinnable) | **96** |
| **No release — nothing to pin** | **42** |

Of the 96 pinnable:

| Verdict | Count | Meaning |
|-------------------|------:|-------------------------------------------|
| **config-only** | **27** | No required param lacking a default; every required column is a sample id or a file we can resolve |
| needs a decision | 53 | Non-derivable columns (27), explicit reference or index (14), other required params (8), a database we don't host (4) |
| no input schema | 15 | Not machine-assessable — all read by hand, see below |
| blocked on pairing | 1 | `cutandrun`: its required `control` column names another sample |

The classifier put `bamtofastq`, `atacseq`, `hlatyping`, `crisprseq` and
`nanoseq` in *needs a decision* rather than *config-only* — correct, and
a good calibration check: those are exactly the ones that needed a
resolver or an elicited value. **"Needs a decision" is not
"impossible"**; it is the bucket mechanisms 2–4 exist to serve.

### The 15 with no input schema, read by hand

None is a candidate, but *why* is the useful part: **a missing
`assets/schema_input.json` mostly means DSL1-era, not complicated.**

| Pipeline | Rev | What it takes | Verdict |
|---------------|-------|--------------------------|---------------------|
| `bactmap` | 1.0.0 | `sample,fastq_1,fastq_2` | **Shipped** — right shape, one elicitable param. Fails on data only |
| `cageseq` | 1.0.2 | `--input '*_R1.fastq.gz'` glob | DSL1-era; no CAGE data |
| `dualrnaseq` | 1.0.0 | glob + `--genome_host` **and** `--genome_pathogen` | Two genomes at once |
| `eager` | 2.5.3 | glob | DSL1-era; ancient DNA |
| `imcyto` | 1.0.0 | `--input "*.mcd"` + `--metadata` | DSL1-era, no `nextflow_schema.json` |
| `mnaseseq` | 1.0.0 | design `group,replicate,fastq_1,fastq_2` | DSL1-era, no `nextflow_schema.json` |
| `clipseq` | 1.0.0 | `sample,fastq`, single-end | Needs `--fasta` + `--smrna_fasta` |
| `diaproteomics` | 1.2.4 | **three** sheets | Confirms Tier 2 |
| `mcmicro` | 2.0.0 | `input_sample`/`input_cycle` + required `marker_sheet` | Confirms Tier 3 |
| `pangenome` | 1.1.3 | bgzipped FASTA + `--n_haplotypes` | Assemblies, not reads |
| `phyloplace` | 2.1.0 | per row: `refseqfile, refphylogeny, model` | Needs curated reference phylogenies |
| `proteogenomicsdb` | 1.0.0 | no samplesheet | Builds a search database; a reference builder |
| `seqsubmit` | 1.0.0 | `id, fasta, run_accession, assembler…` | ENA deposit utility |
| `hadge` | 0.2.0 | — | Not nf-core-templated at all |
| `tools` | 4.1.0 | — | **Not a pipeline** |

> Two of these *do* publish machine-readable schemas under non-standard
> filenames (`assets/schema_phyloplace_input.json`,
> `schema_input_assembly.json`). A re-run should glob
> `assets/schema*.json` rather than assume the one name.

------------------------------------------------------------------------

## Still blocked, and what each would take

Estimates are engineering effort for one competent person and **exclude**
review, real-data validation, and waiting for storage or approvals —
which for the reference-heavy rows will dominate. Read them as "how big
is this", not as commitments.

### Reference data we don't host

Something must be downloaded, hosted permanently and maintained. A
storage and ownership question before a coding one.

| Pipeline | Blocker | Estimate |
|---------------|-------------------------------------------|--------------|
| `taxprofiler` | Needs a `databases` sheet; a standard Kraken2 set alone is ~100 GB. Its sheet also needs `run_accession` and `instrument_platform` | 2–4 days, mostly download and storage |
| `rnafusion` | Needs `genomes_base` and a separate reference build across Arriba, STAR-Fusion, FusionCatcher, HGNC | 3–5 days, dominated by build and disk |
| `raredisease` | Needs `intervals_wgs`/`intervals_y`, explicit FASTA, dbsnp, SVDB, mobile-element refs | 4–6 days |
| `oncoanalyser` | Not references — a **per-file** row model (7 required columns) plus tumour/normal pairing | 1–2 weeks |

### Experimental design Nessie can't infer

| Pipeline | Blocker | Estimate |
|---------------|--------------------------------------------|--------------|
| `rnasplice` | Needs a `contrasts` file — same blocker as `differentialabundance` | 1–2 weeks for the derivation, then half a day |
| `cutandrun` | Each sample must be paired with its IgG control | 3–5 days |
| `rnadnavar` | Tumour/normal via `status` / `normal_id` | as `oncoanalyser` |

### Input shape

| Pipeline | Blocker | Estimate |
|-------------------|----------------------------------------|--------------|
| `scnanoseq` | Single `fastq` column, `cell_count`, four required params with no safe defaults | 2–3 days |
| `epitopeprediction` | Variants/peptide lists + per-sample HLA alleles, not reads. Should follow `hlatyping` being in real use | 1 week |
| `funcscan` | Input is an assembled contig FASTA; no NExtSEEK type holds an assembly | 1 week |
| `demultiplex` | Input is a BCL run directory. It *produces* `D.SEQ` rather than consuming it | 1 week — but question whether it belongs upstream of NExtSEEK |

### External

| Pipeline | Blocker |
|---------------|-----------------------------------------------------|
| `circrna` | No released version exists — nothing to pin |
| `airrflow` | 9 required AIRR-standard columns (`species`, `pcr_target_locus`, `tissue`, `sex`, `age`…). Lineage covers several; the rest is a real mapping project |

### Tier 2 — proteomics, one new input kind

Needs a `_spectra_from_meta` resolver and the per-sample file model
relaxed from paired R1/R2 to a single file: `mhcquant`,
`diaproteomics`, `mspepid`, `metaboigniter` (targets `D.MSP` / `D.MSI`);
`methylarray`, `nanostring` have no matching sample type today.

> ⚠️ Proteomics needs a **protein FASTA database**, not a genome.
> `reference_bundles.json` and the species→iGenomes logic are entirely
> genome-oriented. That is a second reference *concept*, not another key.

> ⚠️ **`quantms` has left nf-core** (archived 2024-05-06; continued at
> `bigbio/quantms`). It was our only route to **TMT / isobaric**
> quantification, and `mhcquant` does not cover it. Adopting the bigbio
> version is a deliberate decision about running something outside
> nf-core's CI and release guarantees.

------------------------------------------------------------------------

## Priorities

**1. Fix result registration.** 1.7% analysis coverage is the finding
that should drive the roadmap. The reingest loop (`nextseek-pipeline` →
`nextseek-run-ls` → `nextseek-build-upload-xlsx`) produces structurally
invalid workbooks: every `A.*` type requires `File_PrimaryData`,
`Link_PrimaryData`, `Scientist`, `Parent` and `Checksum_PrimaryData`,
and the agent composes only a subset. Adding pipelines multiplies an
unvalidated path — more output shapes to map to `A.*` types, and that
mapping is prose in `SKILL.md` with no test fixture.

**2. `differentialabundance`.** `rnaseq` → reingest as `A.GEX` →
`differentialabundance` on that count matrix → reingest the result. It
reads `salmon.merged.gene_counts.tsv`, exactly what the reingest loop
registers as `File_PrimaryData`. *Half of this is now built* —
`bamtofastq` established `A.*`-as-input with tests. The unbuilt half,
and the real content of this row, is the **contrasts derivation**:
reading `Treatment1` / `Genotype` / `Cohort` off the lineage, proposing
comparisons, and having a human confirm. **Nessie can write experimental
contrasts because it knows the lineage; nothing without a sample
database can.**

**3. `mhcquant`.** 416 mass-spec samples is small, but it is where
nf-core coverage is strongest and where labs are actively working — the
White lab's current manuscript is TMTpro MHC-I immunopeptidomics (PRIDE
**PXD057588**). Its PRIDE-accession input path mirrors `fetchngs`, so
some plumbing exists. Caveat: TMT quantification was `quantms`' remit,
not `mhcquant`'s.

**4. More catalog entries** — cheap, and now demand-led rather than
opportunity-led. The config-only well holds nothing further we hold data
for; what remains is worth adding under the adoption rule as labs ask.

------------------------------------------------------------------------

## Open questions

-   Is there a multiplexed / whole-slide subset inside the 13,386
    `D.IMG` samples? If so `mcmicro` moves up sharply; if not, imaging
    stays out of scope.
-   Flow cytometry is our #2 data class with zero nf-core path. Do we
    care enough to look outside nf-core?
-   Are `Treatment1` / `Genotype` / `Cohort` populated consistently
    enough to derive contrasts automatically, or does a human confirm
    every one?
-   **Are any of the 1,179 amplicon libraries CRISPR amplicons?**
    Nothing labels them so; `SequencingType` says "DNA barcoding" for
    most. This decides whether `crisprseq` is runnable today.

## Method and caveats

-   Counts are direct SQL against `seek_production` on the local
    snapshot (51,359 samples; the graph agent's docs cite ~50,161,
    consistent). This is the sample database, not a project export.
-   Modality groupings are mine, by code prefix and meaning. `D.MSI` is
    counted under both imaging and mass spec (16 samples).
-   `D.IMG` and `D.FLOW` shape claims come from sampling actual
    `json_metadata` rows, not type names.
-   Revisions move. Re-derive `reference_cli_flags` and
    `required_columns` from the new schema whenever a
    `default_revision` is bumped — a stale `gtf` aborts the run at
    validation, and a stale column name fails after the job has queued.
-   The census tooling has two traps worth knowing: `gh api
    releases/latest` writes its 404 body to **stdout** (so a
    release-less repo yields a JSON blob as its "tag"), and `curl -f -o`
    can leave a file containing GitHub's 404 body — check the JSON
    parses, don't just check the file exists.

## Sources

-   [nf-core pipelines index](https://nf-co.re/pipelines) — the site's
    155 includes archived and untagged repos; our 137 does not
-   [nf-core/quantms](https://github.com/nf-core/quantms) —
    **archived**; continued at
    [bigbio/quantms](https://github.com/bigbio/quantms)
-   [mhcquant usage](https://nf-co.re/mhcquant/latest/docs/usage/)
-   [differentialabundance
    usage](https://nf-co.re/differentialabundance/latest/docs/usage/)
-   [mcmicro usage](https://nf-co.re/mcmicro/latest/docs/usage/)
-   [nf-core/imcyto](https://github.com/nf-core/imcyto) (imaging mass
    cytometry, not FCS)
