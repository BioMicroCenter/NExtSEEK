---
editor_options: 
  markdown: 
    wrap: 72
---

# What else could Nessie run? — nf-core capability review

**Date:** 2026-08-04 · **Branch:** `dev-v3-merge`

Began as "what else could Nessie run?". Answering it surfaced a launch
bug that made three of the eight existing pipelines unrunnable, so the
survey and the fixes are recorded together. **Pipelines available went
from 8 to 26; pipelines able to launch went 5 → 26; pipelines verified
on the cluster remains 2.**

**Update 2026-08-05, in two parts.** First, `bamtofastq` shipped — the
doc's own "if you only fund one row" recommendation, and the first
pipeline whose cohort leaves are `A.*` analysis records rather than
`D.SEQ` raw data. Then a full schema census replaced the
description-based tiering below, and eight further pipelines were
catalogued off the back of it.

> ⚠️ **Read the third row of that table carefully.** Eight of the
> twenty-six were added *specifically because we hold no data for them*
> — they are config-only against their pinned schemas, and catalogued so
> the capability exists the day such a cohort is registered. "Available"
> counts catalog entries. It has never counted runs.

**Data source:** direct query against `seek_production` (51,359 samples,
104 sample types)

------------------------------------------------------------------------

## What's actually in NExtSEEK

| Modality | Samples | Types counted |
|----------------------|---------------------------:|----------------------|
| **Imaging** | **13,680** | `D.IMG` 13,386 · `A.IMG` 278 · `D.MSI` 16 |
| **Flow / mass cytometry** | **6,028** | `D.FLOW` 5,210 · `D.FCS` 752 · `A.FLOW` 35 · `D.CYTOF` 30 |
| **Sequencing** | **2,273** | `D.SEQ` 2,057 · analyses 216 |
| **Mass spec proteomics** | **416** | `D.MSP` 208 · `A.MSP` 145 · `A.MUSP` 46 · `D.MSI` 16 |
| **NMR** | **75** | `D.NMR` 54 · `A.NMR` 21 |

Whole-database split:

| Class                                               | Samples |    Share |
|-----------------------------------------------------|--------:|---------:|
| Biological material (`TIS`, `MUS`, `CEL`, `PAT`, …) |  27,690 |    53.9% |
| `D.*` raw data                                      |  22,779 |    44.4% |
| `A.*` analysis results                              | **890** | **1.7%** |

------------------------------------------------------------------------

### Imaging (13,680) — poor fit, despite being the largest class

Sampled `D.IMG` metadata shows conventional fluorescence microscopy:
single `.tif` files from **Keyence BZ-X700 / Zeiss LSM 880**, DAPI/EdU
stains, hosted in **OMERO** (`omero.mit.edu`), often flagged
`"Type": "Representative"`.

nf-core's imaging pipelines target something else: - `mcmicro` expects
**multi-cycle, multi-channel whole-slide** images (CyCIF), with a
samplesheet *and* a markersheet, in OME-TIFF. - `molkart` is for Resolve
Bioscience Molecular Cartography; `spatialvi` for 10x Visium; `imcyto`
for **imaging** mass cytometry.

Representative micrographs are not the input any of these want. **The
imaging volume does not convert into an nf-core opportunity** without a
distinct subset of multiplexed WSI data that I did not find.

### Sequencing (2,273) — well covered, already supported

### Mass spec (416) — small, but genuinely well covered by nf-core

------------------------------------------------------------------------

## Where we started

`chat_nextseek/src/chat_nextseek/seqera/catalog.py` curated **8
pipelines**: `rnaseq`, `scrnaseq`, `atacseq`, `chipseq`, `methylseq`,
`sarek`, `ampliseq`, `fetchngs`.

Every one declared:

``` python
"samplesheet_input_kind": "fastq",
"accepted_leaf_sample_types": ["D.SEQ"],
```

`helpers/lineage.py::enumerate_lineage_leaves` filters the cohort on
that list, and `seqera/emitter.py::_fastq_from_meta` scans metadata for
a FASTQ path. **That single assumption is load-bearing in three
modules** — it, not the catalog size, is what would need to change for
anything non-sequencing.

*(Partly resolved 2026-08-05. `bamtofastq` broke both halves of it:
cohorts can now resolve to `A.*` records, and the emitter can find a
BAM/CRAM as well as a FASTQ. What remains assumed is that the input is
**one file per sample listed in one samplesheet** — which is what still
blocks the Tier 3 pipelines.)*

And of those 8, only **5 could actually launch.** `run.sh` passed
`--genome` unconditionally and `--fasta --gtf` whenever local references
existed, regardless of what each pipeline declares — and nf-schema
aborts a run on an unrecognised parameter. `methylseq` and `sarek` have
no `gtf` param; `ampliseq` declares none of the three and had therefore
**never been launchable at all.**

## Where we are now

|                                          | Started |    Now |
|------------------------------------------|--------:|-------:|
| Pipelines catalogued                     |       8 | **26** |
| Generating a valid launch command        |       5 | **26** |
| …that we hold identifiable data for      |       5 | **10** |
| Verified end-to-end on Luria             |       2 |  **2** |

> ⚠️ **The third row is the one that was never measured before, and it
> is worse than it looks for the ORIGINAL catalog, not just the new
> entries.** `LibraryStrategy` on `D.SEQ` sums to exactly 2,057 — the
> field is complete, so it is authoritative. Its only values are
> Amplicon, RNA-Seq, WGS, scRNA-Seq, Targeted Capture and Hi-C. A
> keyword sweep of the full metadata confirms it: **ATAC 0, ChIP 0,
> methylation/bisulfite/WGBS/RRBS 0, small-RNA/miRNA 0, Ribo-seq 0,
> metagenomic 0, CRISPR 0.** `atacseq`, `chipseq` and `methylseq` have
> been catalogued since before this document existed and have never had
> a cohort to run on either.

Ten pipelines added (`seqinspector`, `hlatyping`, `smrnaseq`, `riboseq`,
`hic`, `rnavar`, `crisprseq`, `nanoseq`, `mag`, `bamtofastq`), three
repaired, and three mechanisms built that were not in the original
survey:

-   **Reference flags are now gated on each pipeline's own schema.** A
    catalog field declares which of `genome`/`fasta`/`gtf` that revision
    accepts; nothing else is emitted.
-   **The launch wizard can ask the user for values nothing can derive**
    — a CRISPR guide, a Nanopore protocol, a Hi-C digestion enzyme —
    with a definition, a worked example, and validation. `configure_run`
    refuses to build a run until they are answered. This is what turned
    three "blocked on metadata" rejections into shipped pipelines.
-   **A cohort can now resolve to `A.*` analysis records.** Previously
    every pipeline filtered lineage down to `D.SEQ` raw data, and the
    only file the emitter could find in a sample's metadata was a FASTQ.
    `bamtofastq` resolves to `A.ALN` alignments and the emitter finds a
    BAM/CRAM. Registered NExtSEEK outputs are now a legal pipeline
    *input*.

Separately, the reingest path now validates workbooks against the real
sample-type catalog rather than against themselves — see the reingest
plan; it is the 1.7% problem below, not this document's subject.

**The number that has not moved is the last row.** Everything new passes
unit tests proving the generated `run.sh` is well-formed; none of it has
been run on the cluster. See *Luria verification status*, which now
marks **sixteen of the twenty-six** as having no data to run on — seven
of those being pipelines catalogued long before this document existed.

------------------------------------------------------------------------

## Three tiers, if we expand

### How to read the tiers

A tier is not difficulty or scientific value — it is **how much of
Nessie's existing launch machinery still works**. Launching anything
today does five things: resolve a cohort to leaf samples → find each
leaf's data file from its metadata → write a samplesheet → resolve a
reference → submit to Luria.

-   **Tier 1** — all five hold. Pure configuration.
-   **Tier 2** — steps 2 and 4 break. The file isn't a FASTQ, and the
    "reference" is a protein database rather than a genome.
-   **Tier 3** — step 3 breaks: the input stops being one samplesheet of
    per-sample files. Sometimes step 1 inverts too.

**Tier ≠ priority.** `differentialabundance` is Tier 3 — the most work —
and is ranked #2 below, above every free Tier 1 addition.

### How many pipelines are in scope

> ⚠️ **Superseded 2026-08-05 by a full schema census.** The table below
> was the original description-based classification. Every pipeline has
> since been checked against its own pinned schemas, and the answer is
> much smaller. The original is kept only so the correction is legible.

| Bucket | Count | Cost |
|-------------------|--------------:|--------------------------------------|
| Already supported | 8 → **18** | — |
| ~~**Tier 1**~~ | ~~73~~ | ~~Config only~~ — **see census below** |
| **Tier 2** | **9** | New file resolver + new reference concept |
| **Tier 3** | **24** | Each its own design |
| Not applicable | 24 | Astronomy, permafrost, reference-builders, sequence-only tools |
| **Total** | **138** |  |

nf-core's site advertises 155 pipelines; the gap to 138 is archived and
untagged repos.

### The schema census — what the 138 actually are

The tiering above was assigned from repo descriptions. That method was
already known to be unreliable (it was wrong 6 times out of the 22
candidates later checked properly), so on 2026-08-05 every pipeline was
enumerated from the GitHub API and both authoritative schemas fetched at
its **latest release tag**: `nextflow_schema.json` for required params
and reference options, `assets/schema_input.json` for required
samplesheet columns. Population reproduced exactly: 138.

Three independent corrections fell out, each shrinking the number.

**1. 42 of the 138 have no GitHub release at all** — 30% of the
population. There is no tag to pin, and pinning a commit means running
something with no release guarantees. The original document flagged
exactly one of these (`circrna`). This alone removes nearly a third of
the "integrable" count.

| Pinnable? | Count |
|-----------------------------|------:|
| Has a release               | **96** |
| No release — nothing to pin | **42** |

**2. Of the 96 pinnable, only 27 are config-only by schema** — and 9 of
those are pipelines we already ship.

| Verdict (of 96) | Count | Meaning |
|---------------------|------:|------------------------------------------|
| **config-only** | **27** | No required param lacking a default; every required column is a sample id or a file we can resolve. 9 already shipped → **18 new candidates** |
| needs a decision | 53 | Non-derivable columns (27), an explicit reference or index (14), other required params (8), a database we don't host (4) |
| no input schema | 15 | No `assets/schema_input.json` — not machine-assessable. **All 15 have since been read by hand; none is a candidate** — see below |
| blocked on pairing | 1 | `cutandrun` — its required `control` column names another sample |

Worth noting the classifier put `bamtofastq`, `atacseq`, `hlatyping`,
`crisprseq` and `nanoseq` in *needs a decision*, not *config-only* —
which is correct, and is a good sign it is calibrated. Those are exactly
the pipelines that needed a resolver or an elicited value. **"Needs a
decision" is not "impossible"** — it is the bucket the two new
mechanisms exist to serve.

**3. Most of the 18 remaining candidates have no data here to run on.**
This is the check the original never made, and it is the one that
matters most. From `seek_production`:

| Finding | Evidence | Kills |
|-------------------|--------------------------|--------------------------|
| **No long-read data whatsoever** | 0 of 2,057 `D.SEQ` mention PacBio / Nanopore / ONT / MinION / PromethION / Revio / Sequel. Every `Sequencer` value is Illumina (plus 26 Singular G4, also short-read) | `genomeassembler`, `pacvar` — and see the `nanoseq` note below |
| **`BAC` samples have no sequencing children** | 1,402 `BAC` samples, but **0** `D.SEQ` records have a `BAC` parent | `bacass` |
| **Essentially no viral data** | 19 samples in the whole 51,359 mention SARS / COVID / influenza / virus / viral | `viralrecon`, `viralmetagenome`, `viralintegration` |
| Not our data shape | — | `demo` (a workshop pipeline), `drugresponseeval` (benchmarks ML models against named public datasets; its `schema_input.json` is an unmodified nf-core template and does not describe its real input), `multiplesequencealign`, `reportho` (both take sequence FASTA, not reads), `sopa` (spatial imaging), `pathogensurveillance` |
| Design-blocked | tumour/normal via `status` / `normal_id` | `rnadnavar` |

**Conclusion: no config-only pipeline remains that we hold data for.**
Not "73 cheap additions" — zero, on today's data. What the database
actually contains is overwhelmingly amplicon and bulk RNA-seq, both
already covered:

| `LibraryStrategy` (2,057 `D.SEQ`) | Samples | Covered by |
|--------------------------|--------:|--------------------------|
| Amplicon (2 spellings)   | **1,179** | `ampliseq`, `crisprseq` |
| RNA-seq (3 spellings)    | **507** | `rnaseq`, `rnavar` |
| WGS                      | 188 | `sarek` |
| scRNA-seq                | 102 | `scrnaseq` |
| Targeted Capture         | 69 | `sarek`, `hlatyping` |
| Hi-C                     | 12 | `hic` |

> ⚠️ **`nanoseq` is catalogued but has nothing to run on.** Its
> verification row already said "confirm we hold Nanopore data at all".
> Confirmed: we do not — zero long-read samples of any platform. It is
> not broken and the entry can stay for when such data arrives, but it
> should not be counted as capability, and it should not be anyone's
> Luria test.

The remaining growth is therefore **not** in adding catalog entries for
data we have. It is in `differentialabundance`'s contrasts derivation,
in `mhcquant` for the mass-spec labs, and in getting results registered
back — the 1.7% problem. Those were already ranked #1–#3 below; this
census removes the argument that cheap Tier 1 additions are a competing
use of time.

#### …but the eight config-only entries were added anyway, deliberately

A catalog entry is roughly half a day and carries no runtime cost. The
census established that these eight are config-only against their pinned
schemas and fail **only** the data test — so the argument for adding
them is that the work is cheap *now*, the schemas are *fresh in hand*,
and the alternative is re-deriving all of it under time pressure the
week someone registers a PacBio run.

| Added | Rev | Why we can't run it today | Elicits |
|-------------------|--------|------------------------------|-----------------|
| `bacass` | 2.6.1 | No bacterial isolate sequencing | — |
| `bactmap` | 1.0.0 | Same | `reference` |
| `pacvar` | 1.1.0 | No long-read data, any platform | — |
| `viralrecon` | 3.0.0 | Essentially no viral data | `platform`, `protocol`, `primer_set` |
| `viralmetagenome` | 1.1.3 | Same | — |
| `viralintegration` | 0.1.1 | Same | — |
| `magmap` | 1.1.0 | No metagenomic cohorts | `genomeinfo` |
| `metatdenovo` | 1.4.0 | Same | `orf_caller` |

Two things fell out of building them that were not obvious from the
census:

-   **`pacvar` proves input kind and sample type are independent
    knobs.** It reads a BAM, like `bamtofastq` — but its samples are
    `D.SEQ` raw data, because PacBio instruments *deliver* unaligned
    reads in BAM format. `bamtofastq`'s BAMs are `A.ALN` analysis
    outputs. Same resolver, opposite ends of the lineage.
-   **Four of the eight needed elicitation that the schema did not
    demand.** `viralrecon`'s `platform` and `protocol`, `magmap`'s
    `genomeinfo` and `metatdenovo`'s `orf_caller` are all schema-
    optional and all silently wrong if unset — an ORF caller that
    assumes no introns will happily call genes in a eukaryotic
    transcriptome. "Config-only by schema" is a *floor* on what a
    pipeline needs, never a ceiling.

**One was rejected during implementation.** `genomeassembler` is
config-only by schema, but its read column depends on the sequencing
platform (`ontreads` vs `hifireads`), and our column-alias map is static
and applied in `write_samplesheet` — which runs *before* `configure_run`,
where elicitation happens. A platform-dependent column name cannot be
resolved in that order. Fixing it means either threading launch params
into the emitter or moving elicitation earlier; neither is worth doing
for a pipeline with no data. Recorded here so the next person does not
rediscover it.

*(Reproducible: the enumeration and classifier are mechanical — list
`nf-core` org repos tagged `nf-core`+`pipeline`, take each latest
release, fetch both schemas, apply the two tests above. Re-run it when
revisions move rather than trusting this table.)*

### The 15 with no input schema — read by hand

The census could not assess these mechanically, so each was read
directly: `nextflow_schema.json`, `docs/usage.md`, and the `assets/`
listing at its pinned tag. **None is a candidate.** The interesting part
is *why* they were unassessable, because it turned out to be a signal in
itself.

| Pipeline | Rev | What it actually takes | Verdict |
|-----------------|--------|--------------------------------|-----------------|
| `bactmap` | 1.0.0 | `sample,fastq_1,fastq_2` — the right shape, and its one required param (`--reference`) is exactly what the elicitation mechanism is for | **Closest call. No data**: bacterial isolate sequencing does not exist here — `BAC` has no `D.SEQ` children, and all 188 WGS records descend from `DNA` samples with no TB mention |
| `cageseq` | 1.0.2 | `--input '*_R1.fastq.gz'` — a glob | DSL1-era, pre-samplesheet; no CAGE data |
| `dualrnaseq` | 1.0.0 | glob + `--genome_host` **and** `--genome_pathogen` | Two genomes at once; not our reference model |
| `eager` | 2.5.3 | `--input '*_R{1,2}.fastq.gz'` — a glob | DSL1-era; ancient DNA |
| `imcyto` | 1.0.0 | `--input "*.mcd"` + `--metadata` | DSL1-era, no `nextflow_schema.json`; imaging mass cytometry |
| `mnaseseq` | 1.0.0 | design `group,replicate,fastq_1,fastq_2` | DSL1-era, no `nextflow_schema.json`; no MNase data |
| `clipseq` | 1.0.0 | design `sample,fastq`, single-end only | Needs `--fasta` + `--smrna_fasta`; no CLIP data |
| `diaproteomics` | 1.2.4 | **three** sheets: samples, spectral library, iRTs | Confirms its Tier 2 placement |
| `mcmicro` | 2.0.0 | `input_sample`/`input_cycle` (not `input`) **plus a required `marker_sheet`** | Confirms its Tier 3 placement, for exactly the stated reason |
| `pangenome` | 1.1.3 | a bgzipped FASTA of assembled haplotypes + `--n_haplotypes` | Input is assemblies, not reads |
| `phyloplace` | 2.1.0 | per row: `queryseqfile, refseqfile, refphylogeny, model` | Needs a curated reference phylogeny and evolutionary model |
| `proteogenomicsdb` | 1.0.0 | no samplesheet at all | It **builds a search database** — a reference builder, not an analysis |
| `seqsubmit` | 1.0.0 | `id, fasta, run_accession, assembler, assembler_version` + `centre_name`, `mode` | Submits assemblies to ENA; an archive-deposit utility |
| `hadge` | 0.2.0 | — | Not nf-core-templated: no `assets/`, no `docs/usage.md`, no `nextflow_schema.json` |
| `tools` | 4.1.0 | — | **Not a pipeline.** `nf-core/tools` is the community's Python tooling package; it merely carries the `pipeline` topic |

Three things this pass changed:

-   **The population is 137, not 138.** `nf-core/tools` is mis-tagged and
    should never have been counted.
-   **A missing `assets/schema_input.json` mostly means "DSL1-era", not
    "complicated".** Six of the fifteen predate the samplesheet
    convention and take a glob or a directory. They are old, not hard —
    but a glob input does not fit the cohort model at all.
-   **Two of them do publish machine-readable schemas, under
    non-standard filenames** — `phyloplace` uses
    `assets/schema_phyloplace_input.json`, `seqsubmit` uses
    `assets/schema_input_assembly.json` and `…_genomes.json`. The census
    looked only for the canonical name and so under-reported. Neither
    verdict changes, but a future re-run should glob `assets/schema*.json`
    rather than assume the one filename.

### Tier 1 — catalog entry only, no new code · ~~73 pipelines~~ **see the census above**

Same FASTQ / `D.SEQ` shape; one `NFCORE_PIPELINE_CATALOG` entry plus one
`templates/nfcore/<key>.json` each. Per-pipeline column renaming is
already an extension point (`emitter.PIPELINE_COLUMN_ALIASES`), so odd
samplesheet headers are a dict entry, not code.

Of the 73, most are bacterial, viral, metagenomic or ancient-DNA
pipelines with no obvious demand here. Of the 22 plausible candidates,
**10 have shipped** (catalog now 18, up from 8):

| Shipped        | Rev   | Reference need                         |
|----------------|-------|----------------------------------------|
| `seqinspector` | 1.1.0 | none — QC only                         |
| `hlatyping`    | 2.2.0 | none — OptiType carries its own        |
| `smrnaseq`     | 2.4.1 | fasta                                  |
| `riboseq`      | 1.2.0 | fasta + gtf                            |
| `hic`          | 2.1.0 | fasta (bowtie2 index built per run)    |
| `rnavar`       | 1.3.0 | fasta + gtf (BQSR and annotation off)  |
| `crisprseq`    | 2.3.0 | none — works off the supplied amplicon |
| `nanoseq`      | 3.1.0 | none — declares no genome/fasta/gtf    |
| `mag`          | 5.5.0 | none — assembles de novo               |
| `bamtofastq`   | 2.2.1 | none for BAM; fasta required for CRAM  |

The other 12 are in the rejection table below. `bamtofastq` is listed
here because it shipped, but it was **not** config-only — it is the one
entry that needed new code (see *Blocked on input shape*).

### Tier 2 — one new input kind (`spectra`) · **9 pipelines**

Needs a `_spectra_from_meta` resolver beside the FASTQ one, and the
per-sample file model relaxed from paired R1/R2 to a single file. Same
single-samplesheet emitter otherwise.

| Pipeline | Input | Target |
|------------------------|------------------------|------------------------|
| `mhcquant` | `ID, Sample, Condition, ReplicateFileName` + protein FASTA; `.raw`/`.mzML`/`.d`, **or a PRIDE accession** | `D.MSP` |
| `diaproteomics` | DIA proteomics quantification | `D.MSP` |
| `mspepid` | MS2 peptide identification | `D.MSP` |
| `metaboigniter` | MS-based metabolomics | `D.MSP`, `D.MSI` |
| `methylarray`, `nanostring` | Illumina array / nCounter | — (no matching type today) |

> ⚠️ **`quantms` is no longer an nf-core pipeline.** `nf-core/quantms`
> was **archived** (last push 2024-05-06); development moved to
> `bigbio/quantms`. An earlier draft of this document recommended
> shipping it alongside `mhcquant` — that recommendation is withdrawn.
> The bigbio version is still Nextflow and still runnable, but it sits
> outside nf-core's conventions, CI and release guarantees, which is a
> different proposition and should be decided deliberately rather than
> inherited. This matters because `quantms` was our only route to **TMT
> / isobaric** quantification; `mhcquant` does not cover it.

> ⚠️ Proteomics needs a **protein FASTA database**, not a genome.
> `reference_bundles.json` and the species→iGenomes logic in
> `pipeline_params.py` are entirely genome-oriented. This is a second
> reference concept, not another key.

### Tier 3 — structurally new input · **24 pipelines**

`differentialabundance` (matrix + contrasts, not reads) · `mcmicro`
(needs a second sheet) · `molkart`, `imcyto`, `spatialvi`, `sopa`,
`lsmquant` · `scdownstream`

------------------------------------------------------------------------

## Revised priorities

### 1. Fix result registration before adding anything

1.7% analysis coverage is the finding that should drive the roadmap.
There is already a reingest loop (`nextseek-pipeline` →
`nextseek-run-ls` → `nextseek-build-upload-xlsx`), and an audit of it
found the workbooks it produces are **structurally invalid**: every
`A.*` type requires `File_PrimaryData`, `Link_PrimaryData`, `Scientist`,
`Parent`, `Checksum_PrimaryData`, and the agent is instructed to compose
only a subset. The QA that should catch this is disabled by
construction. See the reingest-loop plan for detail.

Adding pipelines before this multiplies an unvalidated path: more
pipelines means more output shapes to map to `A.*` types, and that
mapping is currently prose in `SKILL.md` with no test fixture.

### 2. `differentialabundance` — highest strategic value

`rnaseq` → reingest as `A.GEX` → `differentialabundance` on that count
matrix → reingest the result. It reads `salmon.merged.gene_counts.tsv`,
exactly the file the reingest loop registers as `File_PrimaryData`. **It
makes NExtSEEK's own registered outputs a valid pipeline input.**

The differentiator is the **contrasts file** — it needs
`variable / reference / target`, and those are derivable from metadata
we already hold (`Treatment1`, `Genotype`, `Cohort` on `MUS` / `TIS`
rows). *Nessie can write the experimental contrasts because it knows the
lineage.* Nothing without a sample database can do that. It also
directly attacks the 1.7% problem by generating registrable results from
data already in NExtSEEK.

**Half of this is now built.** `bamtofastq` established the `A.*`-as-
input pattern (2026-08-05), so "registered output feeds a new run" is no
longer hypothetical — it has a working example with tests. What is still
unbuilt, and is the real content of this row, is the **contrasts
derivation**: reading Treatment / Genotype / Cohort off the lineage,
proposing the comparisons, and having a human confirm them. Nothing in
the `bamtofastq` work touches that.

### 3. `mhcquant` — small volume, high scientific fit

416 mass-spec samples is not much in absolute terms, but it is the
modality where nf-core coverage is strongest and where specific labs are
actively working — the White lab's current manuscript is TMTpro MHC-I
immunopeptidomics (PRIDE **PXD057588**), which is precisely `mhcquant`'s
remit. It is actively maintained (last release 2026-08), and its
PRIDE-accession input path mirrors `fetchngs`, so part of the plumbing
already exists.

Caveat worth stating to whoever asks for this: `mhcquant` identifies and
quantifies MHC-eluted peptides, but **TMT / isobaric quantification was
`quantms`' remit**, and `quantms` has left nf-core (see the Tier 2
note). If the labs need TMT specifically, that is a separate decision
about adopting a non-nf-core Nextflow pipeline — not something
`mhcquant` delivers.

### 4. Tier 1 additions — cheap, demand-led

Genuinely low cost once (1) lands. Order by what labs ask for, not by
this list.

### Explicitly **not** recommended

-   **`mcmicro` and the imaging tier.** Largest data class, wrong data
    shape. Revisit only if a multiplexed whole-slide subset exists that
    I did not find.
-   **Anything for flow cytometry.** No nf-core pipeline exists. If we
    want to serve those 6,028 samples, that is an R-ecosystem
    conversation, not an nf-core one.

------------------------------------------------------------------------

## What we rejected, and what each would actually take

The 12 shortlist candidates that did **not** ship, grouped by what
blocks them.

> **These entries were audited against each pipeline's
> `assets/schema_input.json`.** The first draft derived requirements
> from `assets/samplesheet.csv` headers, treating every column as
> mandatory. That was wrong three times — `crisprseq`, `nanoseq` and
> `mag` have all shipped since — and gave the wrong *reason* for three
> more (`oncoanalyser`, `funcscan`, `taxprofiler`). Corrections are
> marked inline. The rule now: `schema_input.json` is the authority, the
> example CSV is not. Estimates are engineering effort for one competent
> person, and they **exclude** review, real-data validation, and any
> time spent waiting for storage or approvals — which for the
> reference-heavy rows is likely to dominate. Read them as "how big is
> this", not as commitments.

### Blocked on reference data we don't have

These cannot be built from a FASTA. Something has to be downloaded,
hosted somewhere permanent, and maintained. That is a storage and
ownership question before it is a coding one.

| Pipeline | Why it didn't work | What it would take | Estimate |
|---------------|---------------------|---------------------|---------------|
| `taxprofiler` | Requires a `databases` sheet listing profiler databases. We have none. Its samplesheet also needs `run_accession` and `instrument_platform`, which the first pass missed. | Decide which profilers we support, download their databases to Luria (a standard Kraken2 set alone is \~100 GB), give them a permanent home, write the database sheet, then the usual entry + template. | 2–4 days, mostly download and storage |
| `rnafusion` | Requires `genomes_base` pointing at a prebuilt reference tree, and a separate reference-building run covering Arriba, STAR-Fusion, FusionCatcher and HGNC. | Run the reference build once per genome (hours, several hundred GB), host the result, then entry + template. One-off, but large. | 3–5 days, dominated by the build and disk |
| `raredisease` | Requires `intervals_wgs`, `intervals_y` and an explicit FASTA up front, plus dbsnp, SVDB and mobile-element references. | Assemble and host a GATK-style bundle per supported genome, then entry + template. | 4–6 days |
| `oncoanalyser` | **Corrected:** it declares *no required params*, so the reference claim in the first draft was wrong. The real blocker is the samplesheet — 7 required columns (`group_id, subject_id, sample_id, sample_type, sequence_type, filetype, filepath`), one row **per file**, carrying tumour/normal designation. | A different row model (per-file, not per-sample) plus tumour/normal pairing Nessie cannot infer. Reference hosting is likely still wanted in practice, but it is not what blocks it. | 1–2 weeks |

### Blocked on experimental design Nessie can't infer

The pipeline is fine; the problem is that it needs to be told which
samples to compare against which, and that judgement isn't in the file
paths.

| Pipeline | Why it didn't work | What it would take | Estimate |
|---------------|---------------------|---------------------|---------------|
| `rnasplice` | Requires a `contrasts` file naming which groups to compare — the same blocker as `differentialabundance`. | Build the metadata→contrasts derivation: read Treatment / Genotype / Cohort off the samples, propose the contrasts, have a human confirm. The pipeline itself is then trivial. | 1–2 weeks for the derivation, then half a day for the pipeline |
| `cutandrun` | Samplesheet is `group,replicate,fastq_1,fastq_2,control` — each sample must be paired with its IgG control, and nothing tells Nessie which sample that is. | Either derive the control from lineage/metadata, or add a step where the curator pairs them. Smaller than contrasts, same category. | 3–5 days |

### Blocked on input shape

| Pipeline | Why it didn't work | What it would take | Estimate |
|-----------------|--------------------|--------------------|----------------|
| ~~`bamtofastq`~~ | **SHIPPED 2026-08-05.** Was: takes BAM/CRAM, so its inputs are `A.ALN` analysis records, not `D.SEQ` raw data — and the file resolver only recognised FASTQ. | Done. See *What `bamtofastq` actually cost* below. | Estimated 1–2 days; **actual ~half a day** |
| `scnanoseq` | A single `fastq` column rather than R1/R2, plus `cell_count`, and four required params with no safe defaults. | Relax the paired-read assumption in the resolver — the same change Tier 2 needs — then entry + template. | 2–3 days |
| `epitopeprediction` | Input is `sample,alleles,mhc_class,filename` — variants or peptide lists plus HLA alleles, not reads. Misclassified as Tier 1 in the first draft. | A new input kind for variant/peptide files, plus per-sample HLA alleles — which would most naturally come from running `hlatyping` and reingesting its output. | 1 week, and it should follow `hlatyping` being in real use |
| `demultiplex` | Input is a sequencer run directory (BCL) plus a per-flowcell sample sheet. It *produces* `D.SEQ` data rather than consuming it, so it doesn't fit the cohort model at all. | A different launch path: point at a run directory instead of resolving a sample cohort. | 1 week — but question whether it belongs here at all rather than upstream of NExtSEEK |
| `funcscan` | **Narrowed:** it has *no required params* and only `sample,fasta` are required columns — the database claim in the first draft overstated it. The remaining blocker is real though: input is an assembled contig FASTA, not reads. | Add an assembly input kind, and decide which NExtSEEK sample type holds an assembly — there isn't an obvious one today. Databases are optional and can be skipped. | 1 week |

### Resolved since first draft — `crisprseq`, `nanoseq` and `mag` **have now shipped**

All three original rejections were **wrong**, in the same way, and the
correction is worth recording because it is what prompted the audit that
found the rest. `crisprseq` is written up in full below; `nanoseq`
turned out to have a plain `sample,fastq_1,fastq_2` sheet rather than
the `group,replicate,barcode,genome` one the example CSV shows, and
`mag` has **no required params at all** — its databases are optional and
skippable, not mandatory.

It said every row needs `reference`, `protospacer` and `template`. That
came from reading the example samplesheet's header and assuming every
column was mandatory. The authoritative `assets/schema_input.json` says:

```         
REQUIRED columns: ['sample', 'fastq_1']
```

Everything else is optional — and there are run-level overrides,
`--protospacer` (*"the same protospacer sequence for all samples"*) and
`--reference_fasta`, that supersede the per-row columns entirely. So for
a targeted experiment where the cohort shares one guide and one amplicon
— the common case — **three answers cover the whole run, at any sample
count.**

Which meant the real blocker was never curation practice. It was simply
that nobody had asked the user. The launch wizard is already a
multi-turn conversation, so now it does.

**What shipped instead of the rejection:** a general
`required_user_params` contract. A pipeline declares, in its template,
the values nobody can derive — with a plain-English definition, a worked
example, and a validation pattern. `configure_run` then **refuses to
build a run** until they are answered, handing the agent back a
ready-made question.

The refusal lives in code, not in the prompt, deliberately: a prompt
instruction can be forgotten mid-conversation, and the failure mode here
is silent. A wrong guide sequence does not error — it reports a wrong
editing efficiency. Validation catches pasted whitespace, an RNA `U`
where DNA is wanted, and enum typos before any cluster time is spent.

It generalises, which is the actual prize. Two pipelines already shipped
were quietly carrying the same problem and now use it too:

| Pipeline | Asked for | Why it can't be derived |
|-----------------|------------------------|-------------------------------|
| `crisprseq` | `analysis`, then guide + amplicon (targeted) **or** sgRNA library (screening) | Not recorded anywhere in NExtSEEK |
| `smrnaseq` | `mirtrace_species` | Depends on the cohort's organism; without it the miRNA QC is meaningless |
| `hic` | `digestion` — unless the library is DNase Hi-C | The enzyme protocol is a bench decision, not a file property |

Still true, and still worth doing: storing guide and amplicon sequences
on a sample type would make them **reusable and FAIR**, where a value
typed into chat is a one-off. Prompting is a legitimate bridge, not a
replacement for curation. And where guides genuinely differ per sample,
the answer is an uploaded sheet, not typing N sequences — the templates
say so.

**Actual cost: about half a day**, against the 2–3 days plus "unknown
lead time" estimated when the requirement was misread.

### Blocked externally

| Pipeline | Why it didn't work | What it would take | Estimate |
|---------------|---------------------|---------------------|---------------|
| `circrna` | No released version exists — nothing to pin. | Wait for a release, or pin a commit and accept it is unsupported. | Blocked on upstream |
| `airrflow` | **Now scoped.** No required params, but the samplesheet demands **9 required columns**: `sample_id, subject_id, species, pcr_target_locus, tissue, sex, age, biomaterial_provider, single_cell` — AIRR-standard metadata. | Map those fields from NExtSEEK where possible — `sex` sits on `MUS`, `tissue` on `TIS`, so lineage covers several — and elicit or curate the rest. Less hopeless than it looks, but a real mapping project, not config. | 1 week |

### What `bamtofastq` actually cost — the "fund one row" pick, now done

It was recommended at 1–2 days, not because the pipeline matters much
but because the change it requires — letting `A.*` analysis records
serve as pipeline *inputs* — is the same capability
`differentialabundance` needs, and it is the cheapest possible way to
prove that pattern works. It shipped on 2026-08-05 in **about half a
day**. What it actually took:

-   A catalog entry declaring `accepted_leaf_sample_types: ["A.ALN"]`
    and `samplesheet_input_kind: "bam"`. `enumerate_lineage_leaves` was
    already generic over the accepted-type list, so **no lineage change
    was needed** — the "D.SEQ is load-bearing in three modules" claim
    above turned out to hold in two of them, not three.
-   An `_alignment_from_meta` resolver beside `_fastq_from_meta`. It
    scans metadata by VALUE, like its FASTQ sibling, and derives
    `file_type` from the extension of the path it found — never from the
    record's `DataType` field, which is free text and can disagree.
-   One column alias (`sample` → `sample_id`) and a `mapped` / `index` /
    `file_type` branch in the emitter.
-   A gate on the **fetchngs pre-stage**, which was the one genuine trap.
    It decides whether to fetch by looking for rows with a blank
    `fastq_1` and an accession — and a bam samplesheet has no `fastq_1`
    column at all, so every row looked fetchable. It would have
    downloaded reads into a sheet with nowhere to put them. The gate is
    now the pipeline's declared input kind, not the sheet's contents.
-   Two visibility fixes that were not bamtofastq-specific: the catalog
    line the agent sees now names each pipeline's input sample types,
    and a zero-leaf resolution now explains the type mismatch instead of
    returning an empty list. Without these the agent resolves a `D.SEQ`
    cohort, gets nothing back, and has no way to work out why.

**What it does not prove.** Every `A.ALN` record in the seeded database
stores a bare filename plus an SRA archive URL, not a cluster path — so
a real run still needs someone to supply where the BAMs actually live.
The emitted samplesheet leaves `mapped` blank rather than inventing a
path, and the template tells the agent to stop and say so. This is the
same finding as the `D.SEQ` FASTQ paths: it is a data-population gap,
not a code gap.

------------------------------------------------------------------------

## Luria verification status — **maintain this table**

Catalogued is not the same as working. Everything below passes unit
tests proving the generated `run.sh` is well-formed for that pipeline's
declared schema; that is **not** the same as Nextflow accepting it, or
the run completing. Three pipelines sat catalogued-but-unlaunchable for
months precisely because nobody tracked this distinction.

**Three states, not two.** ✅ means a real run completed. ❌ means it
could be run and has not been. ⛔ means **we hold no identifiable data
of that kind at all**, so there is nothing to run and no test to
schedule. Sixteen of the twenty-six are in that state and should never
appear on a testing plan until the corresponding data is registered.

That sixteen includes `atacseq`, `chipseq`, `methylseq`, `smrnaseq`,
`riboseq`, `crisprseq` and `mag` — pipelines that have been catalogued
for a long time and were, until this was checked on 2026-08-05, assumed
testable. `methylseq` in particular was carrying a "**retest** after the
`--gtf` fix" note that could never have been acted on.

*(Caveat on `crisprseq`: 1,179 amplicon libraries exist and nothing
identifies any of them as CRISPR amplicons — `SequencingType` says "DNA
barcoding" for the bulk of them. So the honest statement is that no
cohort is **identifiable** as CRISPR, not that none exists. Ask a
curator before concluding either way.)*

**Update this table whenever a pipeline is launched on Luria.** Record
the run directory — it is the evidence.

| Pipeline | Status | Evidence / what to do |
|------------------|------------------|------------------------------------|
| `rnaseq` | ✅ verified | Real run `nfcore_rnaseq_260723_205359_0` |
| `scrnaseq` | ✅ verified | Real run `nfcore_gideon-4wk_260711_024438_0`; only pipeline with a Luria provisioning script |
| `fetchngs` | 🟡 partial | Exercised as a pre-stage inside other runs, never launched standalone |
| `atacseq` | ⛔ **no data exists** | Flags were already correct, but **0 D.SEQ samples mention ATAC** (2026-08-05). Never launched, and not launchable |
| `chipseq` | ⛔ **no data exists** | Flags were already correct, but **0 D.SEQ samples mention ChIP**. Never launched, and not launchable |
| `methylseq` | ⛔ **no data exists** | The `--gtf` fault was real and is fixed, but the "retest" it called for was never possible: **0 D.SEQ samples mention methylation / bisulfite / WGBS / RRBS** |
| `sarek` | ❌ **retest** | Same `--gtf` fault, same fix |
| `ampliseq` | ❌ **never worked** | Declares none of genome/fasta/gtf; was sent all three. **Has never been launchable.** A first successful run is the proof |
| `seqinspector` | ❌ untested | New. Reference-free — the cheapest end-to-end proof available, do this one first |
| `hlatyping` | ❌ untested | New. Carries its own reference |
| `smrnaseq` | ⛔ **no data exists** | **0 D.SEQ samples mention small-RNA / miRNA.** Confirm `mirtrace_species` elicitation if data ever arrives |
| `riboseq` | ⛔ **no data exists** | **0 D.SEQ samples mention Ribo-seq / ribosome.** Confirm the `type` column is asked for if data ever arrives |
| `hic` | ❌ untested | New. First run per genome pays a bowtie2 index build — allow for it |
| `rnavar` | ❌ untested | New. Confirm BQSR is genuinely skipped |
| `crisprseq` | ⛔ **no identifiable data** | Elicitation is **verified working** in the deployed container (staged questions, enum + DNA-base validation, clean build once answered). But 0 D.SEQ mention CRISPR — the 1,179 amplicon libraries are labelled "DNA barcoding". Ask a curator |
| `nanoseq` | ⛔ **no data exists** | Answered 2026-08-05: **we hold zero long-read samples** (0 of 2,057 `D.SEQ` mention any long-read platform). Untestable here. Keep the entry for future data; do not count it as capability |
| `mag` | ⛔ **no data exists** | **0 D.SEQ samples mention metagenomics.** The `short_reads_1/2` rename is unit-tested; nothing else is testable |
| `bamtofastq` | ❌ **blocked on data** | New. The path is verified end-to-end against the real 32 `A.ALN` rows (leaves resolve, sheet emits `sample_id,mapped,file_type`), but every one of those records is archive-only — **no BAM has a cluster path**. Needs a cohort with real `/net/…` alignments before a Luria run means anything |
| `bacass` | ⛔ **no data exists** | Added 2026-08-05 from the census. No bacterial isolate sequencing: `BAC` has no `D.SEQ` children. Unit tests confirm the `ID`/`R1`/`R2` rename lands in the CSV; nothing else is testable here |
| `bactmap` | ⛔ **no data exists** | Same. Confirm the `reference` elicitation fires when a cohort finally exists |
| `pacvar` | ⛔ **no data exists** | Zero long-read samples of any platform. Unit tests confirm the `bam`/`pbi` rename; confirm the BAM is instrument output, never an aligner's |
| `viralrecon` | ⛔ **no data exists** | 19 viral mentions in 51,359 samples, none a sequencing cohort. Confirm `platform` + `protocol` elicit before `primer_set` is asked |
| `viralmetagenome` | ⛔ **no data exists** | Same |
| `viralintegration` | ⛔ **no data exists** | Same. Also pinned at 0.1.1, the only release — re-derive its flags if a later version appears |
| `magmap` | ⛔ **no data exists** | No metagenomic cohorts registered. Confirm `genomeinfo` elicitation fires |
| `metatdenovo` | ⛔ **no data exists** | Same. Confirm `orf_caller` elicitation fires — the prokaryote/eukaryote choice is silent if wrong |

**Suggested order.** `seqinspector` first — no reference, no
elicitation, so a failure isolates the launch path itself. Then
`ampliseq`, because it exercises the empty-reference-flag case, has
never once run, **and is the single best-represented library type we
hold** (1,179 of 2,057 `D.SEQ` are amplicon). Then `crisprseq`, which
proves the elicitation loop end to end — not `nanoseq`, which has no
data. The reference-building ones (`hic`, `rnavar`) last, since a slow
index build makes a failure expensive to diagnose.

**What "verified" should mean here:** Nextflow accepted the parameters
and the run reached completion — not merely that `sbatch` returned a job
id.

------------------------------------------------------------------------

## Open questions for the team

-   Is there a multiplexed / whole-slide imaging subset inside the
    13,386 `D.IMG` samples? If so, `mcmicro` moves up sharply. If not,
    imaging stays out of scope.
-   Flow cytometry is our #2 data class with zero nf-core path. Do we
    care enough to look outside nf-core?
-   For `differentialabundance`: are `Treatment1` / `Genotype` /
    `Cohort` populated consistently enough to derive contrasts
    automatically, or does a human confirm every contrast?

------------------------------------------------------------------------

## Method and caveats

-   Counts are from a direct SQL query against `seek_production` on the
    local stack, seeded from the instance snapshot (51,359 samples; the
    graph agent's docs cite \~50,161, consistent). This is the sample
    database, not a project export.
-   Modality groupings are mine — I assigned sample types to modalities
    by code prefix and meaning. `D.MSI` is counted under both imaging
    and mass spec (16 samples; immaterial either way).
-   Data-shape claims for `D.IMG` and `D.FLOW` come from sampling actual
    `json_metadata` rows, not from type names alone.
-   nf-core availability checked against the nf-core GitHub org and the
    docs below on 2026-08-04. Versions move; re-check before committing.
-   Tier assignments reflect **input shape**, not scientific difficulty
    or runtime cost.
-   The 138-pipeline population is repos in the `nf-core` GitHub org
    tagged with both `nf-core` and `pipeline` topics, excluding archived
    and forked repos. The site's 155 includes archived and untagged
    ones.
-   **Tier assignment was made from repo descriptions, not by reading
    138 usage documents.** It is directional. Any pipeline that reaches
    a shortlist should have its `usage.md` checked before anyone commits
    an estimate.
-   **Two ways the Tier 1 count of 73 overstates the opportunity.**
    First, most of the 73 are bacterial / viral / metagenomic / ancient
    DNA and have no demand here. Second, perhaps 10–15 need an
    *experimental design* Nessie would have to infer from metadata —
    tumour/normal pairing (`oncoanalyser`, `rnadnavar`), case/control,
    spike-in controls (`cutandrun`). Those are Tier 1 in shape but carry
    the same hidden work as `differentialabundance`'s contrasts file.

## Sources

-   [nf-core pipelines index](https://nf-co.re/pipelines)
-   [nf-core/quantms](https://github.com/nf-core/quantms) —
    **archived**; continued at
    [bigbio/quantms](https://github.com/bigbio/quantms)
-   [mhcquant usage](https://nf-co.re/mhcquant/latest/docs/usage/)
-   [differentialabundance
    usage](https://nf-co.re/differentialabundance/latest/docs/usage/)
-   [mcmicro usage](https://nf-co.re/mcmicro/latest/docs/usage/)
-   [nf-core/imcyto](https://github.com/nf-core/imcyto) (imaging mass
    cytometry, not FCS)
