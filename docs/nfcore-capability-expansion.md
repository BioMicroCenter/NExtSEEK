---
editor_options: 
  markdown: 
    wrap: 72
---

# What else could Nessie run? — nf-core capability review

**Date:** 2026-08-04 · **Branch:** `dev-v3-merge` · **Status:** findings
for discussion

**Data source:** direct query against `seek_production` (51,359 samples,
104 sample types)

------------------------------------------------------------------------

## Headline finding

Only **1.7% of the database is analysis output** (890 of 51,359
samples). We hold 22,779 raw-data samples and 890 registered analyses —
a **26:1 ratio**. For sequencing specifically, 2,057 `D.SEQ` samples
have produced just 216 registered analysis samples: roughly **10%
coverage**.

And the two largest data classes we hold — imaging and flow cytometry —
have **little or no nf-core coverage at all**, for reasons that more
pipelines won't fix.

Adding pipelines addresses neither problem. Getting results *registered
back* does.

------------------------------------------------------------------------

## What's actually in NExtSEEK

| Modality | Samples | Types counted |
|----------------------|----------------------------:|----------------------|
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

## Coverage reality check — including two hard negatives

Volume does **not** equal opportunity here. I checked what the data
actually is before mapping pipelines to it.

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

## Where we are today

`chat_nextseek/src/chat_nextseek/seqera/catalog.py` curates 8 pipelines:
`rnaseq`, `scrnaseq`, `atacseq`, `chipseq`, `methylseq`, `sarek`,
`ampliseq`, `fetchngs`.

Every one declares:

``` python
"samplesheet_input_kind": "fastq",
"accepted_leaf_sample_types": ["D.SEQ"],
```

`helpers/lineage.py::enumerate_lineage_leaves` filters the cohort on
that list, and `seqera/emitter.py::_fastq_from_meta` scans metadata for
a FASTQ path. **That single assumption is load-bearing in three
modules** — it, not the catalog size, is what would need to change for
anything non-sequencing.

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

Classifying all 138 topic-tagged, non-archived nf-core pipelines:

| Bucket | Count | Cost |
|-------------------|------:|-------------------------------------|
| Already supported | 8 | — |
| **Tier 1** | **73** | Config only |
| **Tier 2** | **9** | New file resolver + new reference concept |
| **Tier 3** | **24** | Each its own design |
| Not applicable | 24 | Astronomy, permafrost, reference-builders, sequence-only tools |
| **Total** | **138** | |

So ~106 are integrable in principle, 98 beyond what we have. **But
"integrable" is not "worth integrating"** — see the caveats below.

**And "Tier 1" turned out to be optimistic.** An earlier version of this
document put the realistic Tier 1 shortlist at 15–20. When each candidate's
actual `nextflow_schema.json` was read instead of its one-line description,
**6 of 22 qualified as config-only.** The rest are blocked on references,
experimental design, or input shape — see *What we rejected* below. Treat
the 73 as an upper bound on *shape*, not on effort.

nf-core's site advertises 155 pipelines; the gap to 138 is archived and
untagged repos.

### Tier 1 — catalog entry only, no new code · **73 pipelines**

Same FASTQ / `D.SEQ` shape; one `NFCORE_PIPELINE_CATALOG` entry plus one
`templates/nfcore/<key>.json` each. Per-pipeline column renaming is
already an extension point (`emitter.PIPELINE_COLUMN_ALIASES`), so odd
samplesheet headers are a dict entry, not code.

Of the 73, most are bacterial, viral, metagenomic or ancient-DNA
pipelines with no obvious demand here. Of the 22 plausible candidates,
**6 have shipped** (catalog now 14, up from 8):

| Shipped | Rev | Reference need |
|-----------------|---------|-----------------------------------------|
| `seqinspector` | 1.1.0 | none — QC only |
| `hlatyping` | 2.2.0 | none — OptiType carries its own |
| `smrnaseq` | 2.4.1 | fasta |
| `riboseq` | 1.2.0 | fasta + gtf |
| `hic` | 2.1.0 | fasta (bowtie2 index built per run) |
| `rnavar` | 1.3.0 | fasta + gtf (BQSR and annotation off) |

The other 16 are in the rejection table below.

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

> ⚠️ **`quantms` is no longer an nf-core pipeline.** `nf-core/quantms` was
> **archived** (last push 2024-05-06); development moved to
> `bigbio/quantms`. An earlier draft of this document recommended shipping
> it alongside `mhcquant` — that recommendation is withdrawn. The bigbio
> version is still Nextflow and still runnable, but it sits outside
> nf-core's conventions, CI and release guarantees, which is a different
> proposition and should be decided deliberately rather than inherited.
> This matters because `quantms` was our only route to **TMT / isobaric**
> quantification; `mhcquant` does not cover it.

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
`quantms`' remit**, and `quantms` has left nf-core (see the Tier 2 note).
If the labs need TMT specifically, that is a separate decision about
adopting a non-nf-core Nextflow pipeline — not something `mhcquant`
delivers.

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

The 16 shortlist candidates that did **not** ship, grouped by what blocks
them. Estimates are engineering effort for one competent person, and they
**exclude** review, real-data validation, and any time spent waiting for
storage or approvals — which for the reference-heavy rows is likely to
dominate. Read them as "how big is this", not as commitments.

### Blocked on reference data we don't have

These cannot be built from a FASTA. Something has to be downloaded, hosted
somewhere permanent, and maintained. That is a storage and ownership
question before it is a coding one.

| Pipeline | Why it didn't work | What it would take | Estimate |
|---------------|--------------------------|--------------------------|----------|
| `taxprofiler` | Requires a `databases` sheet listing profiler databases. We have none. | Decide which profilers we support, download their databases to Luria (a standard Kraken2 set alone is ~100 GB), give them a permanent home, write the database sheet, then the usual entry + template. | 2–4 days, mostly download and storage |
| `mag` | Needs BUSCO, CAT and CheckM2 databases for genome-bin QC. | Same shape as taxprofiler: source and host three databases, then entry + template. | 2–4 days |
| `rnafusion` | Requires `genomes_base` pointing at a prebuilt reference tree, and a separate reference-building run covering Arriba, STAR-Fusion, FusionCatcher and HGNC. | Run the reference build once per genome (hours, several hundred GB), host the result, then entry + template. One-off, but large. | 3–5 days, dominated by the build and disk |
| `raredisease` | Requires `intervals_wgs`, `intervals_y` and an explicit FASTA up front, plus dbsnp, SVDB and mobile-element references. | Assemble and host a GATK-style bundle per supported genome, then entry + template. | 4–6 days |
| `oncoanalyser` | Needs the full Hartwig (HMF) reference bundle plus bwa-mem2, GRIDSS and STAR indices. | Download and host the HMF bundle (~300 GB) and build three indices per genome, then entry + template. Largest reference footprint on the list. | 1–2 weeks |

### Blocked on experimental design Nessie can't infer

The pipeline is fine; the problem is that it needs to be told which samples
to compare against which, and that judgement isn't in the file paths.

| Pipeline | Why it didn't work | What it would take | Estimate |
|---------------|--------------------------|--------------------------|----------|
| `rnasplice` | Requires a `contrasts` file naming which groups to compare — the same blocker as `differentialabundance`. | Build the metadata→contrasts derivation: read Treatment / Genotype / Cohort off the samples, propose the contrasts, have a human confirm. The pipeline itself is then trivial. | 1–2 weeks for the derivation, then half a day for the pipeline |
| `cutandrun` | Samplesheet is `group,replicate,fastq_1,fastq_2,control` — each sample must be paired with its IgG control, and nothing tells Nessie which sample that is. | Either derive the control from lineage/metadata, or add a step where the curator pairs them. Smaller than contrasts, same category. | 3–5 days |

### Blocked on input shape

| Pipeline | Why it didn't work | What it would take | Estimate |
|-------------------|----------------------|----------------------|----------|
| `bamtofastq` | Takes BAM/CRAM, so its inputs are `A.ALN` analysis records, not `D.SEQ` raw data — and the file resolver only recognises FASTQ. | Teach the resolver to find a BAM, and allow `A.*` types as cohort leaves. **Cheapest item on this list**, and it doubles as the groundwork for reingested outputs feeding new runs. | 1–2 days |
| `scnanoseq` | A single `fastq` column rather than R1/R2, plus `cell_count`, and four required params with no safe defaults. | Relax the paired-read assumption in the resolver — the same change Tier 2 needs — then entry + template. | 2–3 days |
| `nanoseq` | Samplesheet is `group,replicate,barcode,input_file,genome,transcriptome` — the genome is specified *per row*, and it needs a `protocol`. | Generalise the samplesheet builder to allow per-row references, plus a barcode concept. Check first whether we hold Nanopore data worth running. | 3–5 days, verify demand first |
| `epitopeprediction` | Input is `sample,alleles,mhc_class,filename` — variants or peptide lists plus HLA alleles, not reads. Misclassified as Tier 1 in the first draft. | A new input kind for variant/peptide files, plus per-sample HLA alleles — which would most naturally come from running `hlatyping` and reingesting its output. | 1 week, and it should follow `hlatyping` being in real use |
| `demultiplex` | Input is a sequencer run directory (BCL) plus a per-flowcell sample sheet. It *produces* `D.SEQ` data rather than consuming it, so it doesn't fit the cohort model at all. | A different launch path: point at a run directory instead of resolving a sample cohort. | 1 week — but question whether it belongs here at all rather than upstream of NExtSEEK |
| `funcscan` | Doubly blocked: needs Bakta/ABRicate/AMPcombi databases, *and* its input is assembled contigs and proteins (`sample,fasta,protein,gbk,gff`), not reads. | Provision the databases, add an assembly input kind, and decide which NExtSEEK sample type holds an assembly — there isn't an obvious one today. | 1–2 weeks |

### Blocked on metadata NExtSEEK doesn't record

| Pipeline | Why it didn't work | What it would take | Estimate |
|---------------|--------------------------|--------------------------|----------|
| `crisprseq` | Every row needs `reference`, `protospacer` and `template` sequences plus an `analysis` mode. NExtSEEK records no guide-RNA metadata. | Either add those fields to a sample type and get curators populating them, or treat them as per-run questions the user answers. **The blocker is curation practice, not code.** | 2–3 days of code; unknown lead time on the metadata itself |

### Blocked externally

| Pipeline | Why it didn't work | What it would take | Estimate |
|---------------|--------------------------|--------------------------|----------|
| `circrna` | No released version exists — nothing to pin. | Wait for a release, or pin a commit and accept it is unsupported. | Blocked on upstream |
| `airrflow` | Declares no genome params and ships no example samplesheet; typically needs IMGT germline databases. **I did not characterise it fully** — it is on this list as un-scoped, not as assessed. | Read the usage docs properly and determine input shape and reference needs before estimating anything. | Half a day to scope, then unknown |

### If you only fund one row

`bamtofastq`, at 1–2 days. Not because the pipeline matters much, but
because the change it requires — letting `A.*` analysis records serve as
pipeline *inputs* — is the same capability `differentialabundance` needs,
and it is the cheapest possible way to prove that pattern works.

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
    and forked repos. The site's 155 includes archived and untagged ones.
-   **Tier assignment was made from repo descriptions, not by reading
    138 usage documents.** It is directional. Any pipeline that reaches a
    shortlist should have its `usage.md` checked before anyone commits an
    estimate.
-   **Two ways the Tier 1 count of 73 overstates the opportunity.**
    First, most of the 73 are bacterial / viral / metagenomic / ancient
    DNA and have no demand here. Second, perhaps 10–15 need an
    *experimental design* Nessie would have to infer from metadata —
    tumour/normal pairing (`oncoanalyser`, `rnadnavar`), case/control,
    spike-in controls (`cutandrun`). Those are Tier 1 in shape but carry
    the same hidden work as `differentialabundance`'s contrasts file.

## Sources

-   [nf-core pipelines index](https://nf-co.re/pipelines)
-   [nf-core/quantms](https://github.com/nf-core/quantms) — **archived**;
    continued at [bigbio/quantms](https://github.com/bigbio/quantms)
-   [mhcquant usage](https://nf-co.re/mhcquant/latest/docs/usage/)
-   [differentialabundance
    usage](https://nf-co.re/differentialabundance/latest/docs/usage/)
-   [mcmicro usage](https://nf-co.re/mcmicro/latest/docs/usage/)
-   [nf-core/imcyto](https://github.com/nf-core/imcyto) (imaging mass
    cytometry, not FCS)
