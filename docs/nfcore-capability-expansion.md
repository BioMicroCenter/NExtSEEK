---
editor_options: 
  markdown: 
    wrap: 72
---

# What analyses can Nessie run? — in plain English

*What Nessie can and can't launch for you, what we added, and the
uncomfortable thing we found while checking. No bioinformatics
background assumed. About a 15-minute read.*

**Branch:** `dev-v3-merge` · **Started** 2026-08-04 · **Last revised**
2026-08-05

------------------------------------------------------------------------

## 1. The short version

Nessie can start standard analyses for you on the lab's computing
cluster. It knows a fixed list of them. This document is about how long
that list is, how we decided what goes on it, and which entries actually
work.

Three things happened:

1.  We found a bug that had quietly stopped three analyses from running
    at all — for months. Fixed.
2.  We grew the list from 8 analyses to 31.
3.  We checked whether NExtSEEK actually holds the kind of data each
    analysis needs. It mostly doesn't.

|                                             | Before |    Now |
|---------------------------------------------|-------:|-------:|
| Analyses Nessie knows about                 |      8 | **31** |
| …that produce a valid launch command        |      5 | **31** |
| …that we hold matching data for             |      5 | **13** |
| …proven to run to completion on the cluster |      2 |  **3** |

------------------------------------------------------------------------

## 2. Vocabulary

Five terms do most of the work in this document.

| Term | What it means here |
|------------------|------------------------------------------------------|
| **nf-core** | A public, community-maintained library of standard analysis recipes. Free, widely used, and the source of everything discussed here. |
| **Pipeline** | One such recipe — "turn RNA sequencing files into gene counts", say. There are 137 of them. |
| **Samplesheet** | The list you hand a pipeline: one row per sample, saying where its files live. Getting this right is most of Nessie's job. |
| **Schema** | A machine-readable spec each pipeline publishes, saying exactly which columns and settings it demands. This turns out to matter enormously — see section 8. |
| **Luria** | The shared computing cluster at MIT where the analyses actually run. |

------------------------------------------------------------------------

## 3. The rule we now follow

The test for adding a pipeline is now:

-   **Can we build its samplesheet from what we know?**
-   **Does it need any setting we can't supply or ask for?**

Whether anyone currently has the data is recorded in the status table
(section 6).

What still gets turned down is a pipeline whose **input we need to build
out more** — one that wants two separate spreadsheets, or a whole folder
rather than a list of samples, or an experimental design that nobody can
work out from the files. Section 9 lists those.

------------------------------------------------------------------------

## 4. Rules Claudes needed to learn

**Lesson one: read the spec, not the example.**

Every pipeline ships a machine-readable schema saying what it needs. It
usually also ships an example samplesheet. The first pass through this
work read the examples and assumed every column in them was required.

That was wrong six times out of twenty-two. Three pipelines were
rejected as too hard when they were actually straightforward. A separate
pass that judged pipelines by their one-line GitHub description
overestimated how many were easy by roughly **four times**.

The rule now: fetch both schema files at the exact version we intend to
run, and believe those. Nothing else.

**Lesson two: "the spec says it's optional" doesn't mean you can skip
it.**

Some settings are technically optional but produce silently wrong
results if left unset. `metatdenovo` has a setting for which gene-finder
to use; the bacterial one assumes genes have no introns. Point it at a
plant or animal sample and it will not complain — it will just find the
wrong genes. Same story with `viralrecon`, which needs to be told the
sequencing platform and the lab protocol.

So for eight pipelines Nessie now **refuses to build the run** until
someone answers those questions. More on that in section 7.

------------------------------------------------------------------------

## 5. What NExtSEEK actually holds

This is the section that changed the conclusions.

| Kind of data              |    Samples |
|---------------------------|-----------:|
| **Imaging**               | **13,680** |
| **Flow / mass cytometry** |  **6,028** |
| **Sequencing**            |  **2,273** |
| **Mass spec proteomics**  |    **416** |
| **NMR**                   |     **75** |

### The sequencing data, broken down

Almost everything Nessie can launch operates on sequencing data, so what
that 2,273 consists of decides what's useful. The field recording the
library type covers every one of the 2,057 raw sequencing samples, so
this list is complete rather than a sample:

| Library type        |   Samples | Handled by              |
|---------------------|----------:|-------------------------|
| Amplicon            | **1,179** | `ampliseq`, `crisprseq` |
| RNA-seq             |   **507** | `rnaseq`, `rnavar`      |
| Whole genome (WGS)  |       188 | `sarek`                 |
| Single-cell RNA-seq |       102 | `scrnaseq`              |
| Targeted Capture    |        69 | `sarek`, `hlatyping`    |
| Hi-C                |        12 | `hic`                   |

### There are some library types that do not have samples

We searched all the sample records for every other kind of sequencing.
Here is what came back:

> ATAC **0** · ChIP **0** · methylation/bisulfite **0** · small-RNA or
> miRNA **0** · Ribo-seq **0** · metagenomics **0** · CRISPR **0**
>
> **Long-read sequencing of any kind: 0.** Every sequencing machine on
> record is an Illumina (plus 26 Singular G4 — also short-read).

That matters because **seven analyses had been on Nessie's list for a
long time with nothing to run them on**: `atacseq`, `chipseq`,
`methylseq`, `smrnaseq`, `riboseq`, `crisprseq` and `mag`. Nobody had
checked. `methylseq` was even carrying a note saying "retest this after
the bug fix" — a retest that was never possible, because there has never
been a methylation experiment in the database.

Two more things we looked for and didn't find:

-   **Viruses.** 19 mentions of virus, viral, SARS, COVID or influenza
    across all 51,359 samples. None is a sequencing experiment.
-   **Bacterial isolates.** There are 1,402 bacteria samples, but not
    one of them has sequencing data attached. There *are* 3,975 mentions
    of tuberculosis — but they're on the **host** samples (mice, animals
    and patients that were infected), not on sequenced bacteria. The TB
    work here studies how the host responds, not the pathogen's genome.

## 6. Status of every analysis — **keep this table current**

This is the most useful table in the document, and the one most likely
to go stale.

Every entry below passes automated tests proving Nessie builds a
correctly-formed command for it. **That is not the same as the pipeline
accepting that command, and neither is the same as the analysis
finishing.** Three pipelines sat broken for months precisely because
nobody tracked the difference.

**The three states:**

-   ✅ — someone ran it, it finished. Three of 31.
-   ❌ — we could run it; nobody has yet. Ten of 31.
-   ⛔ — **we hold no data of that kind**, so there is nothing to run
    and no test to schedule. Eighteen of 31.

The eighteen ⛔ entries break down as ten added deliberately under the
rule in section 3, and seven that predate this work and were quietly
assumed to be working, plus `nanoseq`.

**When you launch something, update its row and record the run
directory** — that's the evidence.

| Pipeline | Status | Evidence / what to do |
|------------------------|------------------------|------------------------|
| `rnaseq` | ✅ verified | Real run `nfcore_rnaseq_260723_205359_0` |
| `scrnaseq` | ✅ verified | Real run `nfcore_gideon-4wk_260711_024438_0`; only one with a Luria setup script |
| `fetchngs` | 🟡 partial | Used as a preparatory step inside other runs; never launched on its own |
| `atacseq` | ⛔ no data | 0 samples mention ATAC |
| `chipseq` | ⛔ no data | 0 mention ChIP |
| `methylseq` | ⛔ no data | 0 mention methylation or bisulfite. Its bug was real and is fixed; the retest it asked for was never possible |
| `sarek` | ❌ untested | Was sending a setting it rejects; fixed 2026-08-04. First run since is the real test |
| `ampliseq` | ❌ **never worked** | Was sent three settings it doesn't accept. **Covers our largest library type** (1,179 amplicon samples). A first successful run is the proof |
| `seqinspector` | ❌ untested | Needs no reference genome; cheapest way to prove the launch path works |
| `hlatyping` | ❌ untested | Brings its own reference data |
| `smrnaseq` | ⛔ no data | 0 mention small-RNA or miRNA |
| `riboseq` | ⛔ no data | 0 mention Ribo-seq. Check it asks for the library type rather than assuming |
| `hic` | ❌ untested | First run per genome pays for a slow index build |
| `rnavar` | ❌ untested | Confirm the recalibration step is genuinely skipped |
| `crisprseq` | ⛔ no identifiable data | 0 mention CRISPR, though the 1,179 amplicon samples are only labelled "DNA barcoding". **Worth asking a curator.** The question-asking itself is confirmed working |
| `nanoseq` | ⛔ no data | 0 long-read samples of any kind |
| `mag` | ⛔ no data | 0 mention metagenomics |
| `bamtofastq` | ❌ blocked on data | Tested end-to-end against the real 32 alignment records — but all of them point at an archive URL, **not a file on the cluster** |
| `bacass` | ⛔ no data | No bacterial isolate sequencing exists |
| `bactmap` | ⛔ no data | Same. Check it asks for the reference genome when data arrives |
| `pacvar` | ⛔ no data | No long-read data. Confirm the file is instrument output, not an alignment |
| `viralrecon` | ⛔ no data | 19 viral mentions in 51,359 samples. Check it asks for platform and protocol first |
| `viralmetagenome` | ⛔ no data | Same |
| `viralintegration` | ⛔ no data | Same. Pinned to version 0.1.1 — the only release that exists |
| `magmap` | ⛔ no data | No metagenomic samples. Check it asks which genome collection to use |
| `metatdenovo` | ⛔ no data | Same. Check it asks which gene-finder — the wrong one fails silently |
| `denovotranscript` | ❌ untested | Data exists (507 RNA-seq) but all our organisms have reference genomes, so `rnaseq` is usually the better answer |
| `detaxizer` | ✅ verified | Real run `detaxizer-smoke_260805_181226_0`, 2 samples, 2m57s, 18 processes. **Only cheap on the bbduk path** — its own default is Kraken2 against a 64 GB database. A first attempt was cancelled 4.2 GB into that download; the curated params are what prevent it |
| `fastqrepair` | ❌ untested | Works on anything, but it's a repair tool: use it when a run fails on unreadable files |
| `genomeassembler` | ⛔ no data | No long-read data. Check it picks the right column per machine type, and that Illumina data lands in neither |
| `pathogensurveillance` | ⛔ no data | No isolate sequencing. Check the grouping column reflects a real distinction |

**Where to start.** `detaxizer` is done (2026-08-05). Next `ampliseq`,
which covers our biggest library type and has literally never run
successfully. Then `crisprseq`, to prove the question-asking works from
end to end. Leave `hic` and `rnavar` for last, since they build a large
index first and a failure takes hours to surface.

**"Verified" should mean** the pipeline accepted the settings and the
analysis finished — not merely that the cluster accepted the job.

------------------------------------------------------------------------

## 7. The four things we built

**1. Only send settings the pipeline actually accepts.**

This was the bug. Nessie was sending three reference-genome settings to
every pipeline regardless of whether that pipeline understood them — and
these tools abort immediately on an unrecognised setting. `methylseq`
and `sarek` were being killed at the starting line, and `ampliseq` had
**never once been able to run**. Now each entry records exactly which
settings its version accepts, and nothing else goes out.

**2. Nessie can ask you for things it can't work out.**

Some values genuinely aren't in the database and can't be guessed — the
guide sequence used in a CRISPR experiment, the enzyme used to prepare a
Hi-C library, which sequencing platform a viral sample came from.

Nessie now **refuses to build the run** until you answer, and gives you
a plain-English definition and a worked example rather than a bare
setting name. The refusal is enforced in code rather than by instructing
the AI, deliberately: an instruction can be forgotten mid-conversation,
and these failures are silent. A wrong CRISPR guide doesn't produce an
error — it produces a confident, wrong efficiency number.

It also checks your answer: stray spaces, an RNA letter where DNA was
wanted, a misspelled option. Eight pipelines use this.

**3. Results can now feed new analyses.**

Until now Nessie could only start an analysis from **raw** data. But an
analysis output registered back into NExtSEEK is exactly what the next
analysis needs. `bamtofastq` was the first to work this way — it takes
alignment records rather than raw files.

This matters beyond that one pipeline: it's the same capability the
highest-value item on our roadmap needs (section 10).

**4. Nessie reads the sequencing machine from the sample record.**

Two pipelines need the column name, or a value in it, to depend on which
machine produced the data. We can't ask the user, because the
samplesheet gets written before the point in the conversation where
questions happen. But the machine is recorded on the sample already — so
Nessie reads it there. That's earlier, needs no question, and handles a
mixed batch where different samples came off different machines, which a
single answer couldn't have covered.

------------------------------------------------------------------------

## 8. How we checked all 137 pipelines

Rather than judging pipelines by their descriptions, we downloaded both
schema files for every one of them, at the exact version we'd run, and
sorted them mechanically. The raw output is saved alongside this
document at `docs/nfcore-schema-census-2026-08-05.json` so the numbers
can be audited without redoing the download.

**First surprise: 42 of the 138 have never had a release.** That's
nearly a third. There is no stable version to pin to, so running one
means running whatever happens to be in the repository today. (Also,
`nf-core/tools` isn't a pipeline at all — it's the community's software
toolkit, mislabelled. So the real population is **137**.)

Of the 96 that do have a release:

| Verdict | Count | What it means |
|-----------------|----------------:|--------------------------------------|
| **Straightforward** | **27** | Needs nothing we can't supply. Nine were already supported |
| Needs a decision | 53 | Wants a column we can't derive, a reference file we don't host, or a database nobody has downloaded |
| Can't tell | 15 | Publishes no machine-readable input spec — read by hand instead, see below |
| Blocked | 1 | `cutandrun`, which needs each sample paired with its control |

A useful sanity check: the sorter put `bamtofastq`, `hlatyping`,
`crisprseq` and `nanoseq` in "needs a decision" rather than
"straightforward" — which is right. Those are exactly the ones that
needed new work. **"Needs a decision" is not "impossible"** — it's the
category the four mechanisms in section 7 exist to serve.

### The 15 we had to read by hand

None turned out to be worth adding, but the reason is interesting:
**most of them are simply old.** Six predate the convention of listing
samples in a spreadsheet at all, and instead want a folder or a filename
pattern. They aren't difficult; they're from an earlier era, and they
don't fit how Nessie works.

| Pipeline | What it wants | Verdict |
|------------------|--------------------------|----------------------------|
| `bactmap` | An ordinary samplesheet | **Added.** Fails only on there being no data |
| `cageseq` | A filename pattern | Old style |
| `dualrnaseq` | Two genomes at once | Doesn't fit our reference handling |
| `eager` | A filename pattern | Old style; ancient DNA |
| `imcyto` | A folder of instrument files | Old style |
| `mnaseseq` | An older sheet format | Old style |
| `clipseq` | Single-end reads plus two references | No matching data |
| `diaproteomics` | **Three** separate sheets | Proteomics; bigger job |
| `mcmicro` | A sheet **plus** a marker sheet | Imaging; bigger job |
| `pangenome` | Assembled genomes, not reads | Wrong input |
| `phyloplace` | A curated evolutionary tree per row | Wrong input |
| `proteogenomicsdb` | No samples at all | It builds a database |
| `seqsubmit` | Assemblies for deposit | A submission tool |
| `hadge` | — | Doesn't follow nf-core conventions |
| `tools` | — | **Not a pipeline** |

> One caveat on our own method: two of these *do* publish a
> machine-readable spec, just under an unusual filename. Our automated
> check only looked for the standard name and missed them. It didn't
> change any verdict, but a future re-run should look more broadly.

------------------------------------------------------------------------

## 9. What's still out of reach, and roughly what it would cost

Estimates are hands-on work for one person. They **exclude** review,
testing against real data, and any waiting for storage or approvals —
which for the first group will probably dominate.

### Needs reference data nobody hosts

Something must be downloaded, given a permanent home, and maintained.
That's a storage and ownership question before it's a coding one.

| Pipeline | The blocker | Estimate |
|-----------------|--------------------------------------|-----------------|
| `taxprofiler` | Needs reference databases; one standard set alone is \~100 GB | 2–4 days, mostly downloading |
| `rnafusion` | Needs a prebuilt reference tree, itself a separate multi-hour build | 3–5 days |
| `raredisease` | Needs a whole bundle of variant reference files per genome | 4–6 days |
| `oncoanalyser` | Not references — it needs one row per *file* and a tumour/normal pairing we can't infer | 1–2 weeks |

### Needs an experimental design nobody can infer

The pipeline is fine. The problem is it needs to be told which samples
to compare against which, and that judgement isn't in the filenames.

| Pipeline | The blocker | Estimate |
|-----------------|--------------------------------------|-----------------|
| `rnasplice` | Needs a list of comparisons to make | 1–2 weeks, then trivial |
| `cutandrun` | Each sample must be paired with its control | 3–5 days |
| `rnadnavar` | Needs tumour/normal designations | as `oncoanalyser` |

### Needs a different shape of input

| Pipeline | The blocker | Estimate |
|------------------|------------------------------------|------------------|
| `scnanoseq` | A different read layout and four settings with no safe defaults | 2–3 days |
| `epitopeprediction` | Wants variants plus per-sample HLA types, not reads. Should follow `hlatyping` being in real use | 1 week |
| `funcscan` | Wants assembled sequence; no NExtSEEK sample type holds one | 1 week |
| `demultiplex` | Wants a raw instrument run folder. It *produces* sequencing data rather than consuming it | 1 week — but it may belong upstream of NExtSEEK entirely |

### Blocked by someone else

| Pipeline | The blocker |
|------------------|------------------------------------------------------|
| `circrna` | No released version exists |
| `airrflow` | Nine required metadata columns (species, tissue, sex, age…). Our sample relationships cover several; the rest is a real mapping project |

### Proteomics — a different kind of input entirely

`mhcquant`, `diaproteomics`, `mspepid` and `metaboigniter` all work on
mass-spec data. Supporting them means teaching Nessie to find a
different kind of file, and — the bigger issue — **a "reference" in
proteomics is a protein database, not a genome.** All our reference
handling assumes genomes. That's a second concept, not another entry in
a list.

> ⚠️ **One route just closed.** `quantms` was archived by nf-core in May
> 2024 and moved to a different organisation. It was our only path to
> **TMT / isobaric quantification**, and `mhcquant` does not replace it.
> Using the relocated version means running something outside nf-core's
> testing and release guarantees — a deliberate decision, not something
> to drift into.

------------------------------------------------------------------------

## 10. What to do next

**1. Fix result registration first.**

Only 1.7% of the database is analysis results. There is already a loop
for registering them, and an audit found the spreadsheets it produces
are **structurally invalid** — every analysis record requires five
fields, and only some are being filled in. Adding more pipelines before
this multiplies an unvalidated path: more analyses means more output
shapes to map, and that mapping is currently written as prose with no
test to catch mistakes.

**2. `differentialabundance` — the highest-value thing on the list.**

The idea: run `rnaseq`, register the gene counts back into NExtSEEK, and
then run a comparison analysis **on our own registered results**. It
reads exactly the file our registration loop already produces.

Half of this now exists — `bamtofastq` proved that registered results
can feed a new analysis. The missing half, and the real content of this
item, is working out **which groups to compare**: reading treatment,
genotype and cohort off the sample relationships, proposing the
comparisons, and having a person confirm them.

That's the part worth building, because **Nessie can propose the
comparisons precisely because it knows how the samples relate to each
other.** A tool without a sample database can't.

**3. `mhcquant` for the mass-spec labs.**

416 samples is small, but it's where nf-core is strongest and where labs
are actively working — the White lab's current manuscript is exactly
this kind of experiment (PRIDE PXD057588). Some of the plumbing already
exists. Caveat: it does not do TMT quantification; that was `quantms`'
job, and `quantms` has left nf-core.

**4. More entries — as labs ask.**

Cheap, and now demand-led. There is nothing left on the straightforward
list that we hold data for.

**Not recommended:** the imaging pipelines, and anything for flow
cytometry — see section 5.

------------------------------------------------------------------------

## 11. Open questions for the team

-   Is there a set of multi-round whole-slide images hidden inside the
    13,386 imaging samples? If so, `mcmicro` becomes interesting. If
    not, imaging stays out of scope.
-   Flow cytometry is our second-largest data type with no nf-core path
    at all. Do we care enough to look outside nf-core?
-   Are treatment, genotype and cohort recorded consistently enough to
    propose comparisons automatically, or does a person confirm each
    one?
-   **Are any of the 1,179 amplicon samples CRISPR experiments?**
    Nothing labels them that way — most say "DNA barcoding" — but that's
    a labelling question, not proof. This decides whether `crisprseq` is
    usable today.

------------------------------------------------------------------------

## 12. How the numbers were produced

-   Counts come from a direct query against the sample database on the
    local copy (51,359 samples). This is the real database, not a
    project spreadsheet export.
-   Grouping samples into "imaging", "sequencing" and so on is my
    judgement, by sample-type code and meaning.
-   Claims about what the imaging and flow data actually *is* come from
    reading real sample records, not from the type names.
-   **Versions move.** When a pipeline's pinned version is bumped,
    re-read its schemas and update the settings and columns we send. A
    stale setting aborts the run immediately; a stale column name fails
    after the job has already queued.
-   Two traps in the checking script, recorded so nobody hits them
    again: GitHub's "latest release" query prints its *error message* to
    normal output, so a pipeline with no releases looks like it has a
    strangely-named one; and `curl` can leave behind a file containing a
    404 error page, which looks like a successful download until you try
    to read it.

## Sources

-   [nf-core pipelines index](https://nf-co.re/pipelines) — the site
    advertises 155; that count includes archived and untagged
    repositories, ours does not
-   [nf-core/quantms](https://github.com/nf-core/quantms) —
    **archived**; continued at
    [bigbio/quantms](https://github.com/bigbio/quantms)
-   [mhcquant usage](https://nf-co.re/mhcquant/latest/docs/usage/)
-   [differentialabundance
    usage](https://nf-co.re/differentialabundance/latest/docs/usage/)
-   [mcmicro usage](https://nf-co.re/mcmicro/latest/docs/usage/)
-   [nf-core/imcyto](https://github.com/nf-core/imcyto) — imaging mass
    cytometry, not flow cytometry
