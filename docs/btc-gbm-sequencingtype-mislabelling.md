---
title: 54 gene-expression libraries are labelled "Single Cell TCR"
study: 241114SHA (BTC GBM, trial 1a)
instance: https://nextseek.mit.edu (production)
found: 2026-08-17
status: fixed and verified on 2026-08-17
---

# 54 gene-expression libraries are labelled "Single Cell TCR"

## Status: fixed

Applied to production on 2026-08-17, job `63d89156-07bb-4c34-b1e4-bd1bdeda8fc7`:
54 rows processed, 54 updated, 0 failed. `survey_seqtype_labels.py` now reports
0 disagreements out of 114, and the study reads 54 `Single Cell RNAseq` to 54
`Single Cell TCR`. All 108 records still hold their UID as their title and both
of their assay links.

The `DFCl2` name typo described at the end of this document was fixed the same
day, on all three records carrying it.

The rest of this document describes the defect as found, and what it took to
repair it safely. Everything below the fold is in the past tense.

## The short version

In study `241114SHA` there are 108 sequencing records. Every one of them
carried `SequencingType = "Single Cell TCR"`.

Half of those 108 are not TCR libraries. They are gene-expression (GEX)
libraries, and they say so in their own name and in their own file names.

The correct picture is 54 gene-expression libraries and 54 TCR libraries,
one of each per sample, which is what a 10x 5' experiment produces. The
labels said 0 gene-expression and 108 TCR.

Nothing is wrong with the data itself. The FASTQ files are all present and
correctly named. This is one metadata field on 54 records.

## What it looks like

Cross-tabulating the label against the library each record names, as found:

| `SequencingType` | named `-GEX` | named `-TCR` | name says neither |
|---|---|---|---|
| Single Cell TCR | **54** | 54 | 0 |

The 54 in bold were the problem. The other 54 records were fine.

Two examples:

- `D.SEQ-241114SHA-1`: name `BTC-GBM-001-001-GEX`, files
  `BTC-GBM-001-001-GEX_S1_L001_R1_001.fastq.gz` … labelled `Single Cell TCR`
- `D.SEQ-241114SHA-101`: name `GBM1-DFCI5-S5-L3-GEX-LIB`, files
  `GBM1-DFCI5-S5-L3-GEX-LIB_S47_L001_I1_001.fastq.gz` … labelled `Single Cell TCR`

The survey script below reports 114 records, not 108, because it walks the
whole `fibroblast-subtypes` cohort and that cohort spans two studies. The six
extra records are `D.SEQ-241115BOI-7` through `-12`. They belong to study
`241115BOI`, they are the ones labelled `Single Cell RNAseq`, and none of them
is in `241114SHA`.

## It is systematic, not scattered

The 54 affected records are exactly the odd-numbered UIDs from
`D.SEQ-241114SHA-1` through `D.SEQ-241114SHA-107`, a complete, unbroken run
with no exceptions. Their TCR partners are the even-numbered records in the
same range.

That pattern says the records were created in GEX/TCR pairs and then the
label was applied across the whole batch as `Single Cell TCR`, rather than
per record. It is one bulk action to undo, not 54 individual mistakes to
chase.

## What the value should be, and what was written

`Single Cell RNAseq`, which is what the 54 records now carry. That is the
spelling on the six correctly labelled records in study `241115BOI`, which
sits in the same project and is reached by the same cohort query. There was no
precedent inside `241114SHA` itself: all 108 of its records carried the wrong
label, so nothing in this study established a house spelling to match.

Nothing will enforce the choice. `SequencingType` is attribute 609 on the
`D.SEQ` sample type, of type Text with regexp `.*`, not required, with no
controlled vocabulary bound to it. The server will accept any string, so the
spelling is a curation decision and the only protection against a second
spelling is picking deliberately.

## Why it matters

The assistant picks an analysis pipeline by reading sample metadata. With the
old labels it read this study as entirely T-cell receptor data, which was
wrong about half of the study's raw data. Widened to the cohort, it saw only 6
gene-expression samples out of 114, and all 6 of those came from the
neighbouring study.

**It is not currently known whether this changes the assistant's answer.** An
earlier single run suggested it did. Repeating that run showed the model is
not deterministic on this question, and that the corrected and uncorrected
labels produce the same answers at every payload size tested (3 repeats each).
The dominant factor is something else: how much nf-core documentation is in
the payload. That measurement is in
`chat_nextseek/evals/demo-output-ablation*`.

So this is worth fixing because it is **wrong**, not because of a demonstrated
downstream effect. Anyone reading the study to see what data it holds gets a
false picture, whether that reader is a person or a program.

This study is unusually exposed to a bad label. It has no protocol documents
attached, and `LibraryStrategy`, `LibrarySource` and `LibrarySelection` are
empty on all 108 records. `SequencingType` is therefore the *only* field
saying what kind of library each record holds. Everywhere else in the team's
data there is a protocol document or a populated library field to cross-check
against. Here there is nothing, so a wrong label goes straight through.

## How to check this independently

Compare each record's `SequencingType` against the `-GEX` / `-TCR` marker in
its own `Name` and `File_PrimaryData`. Everything needed is already in the
record, with no re-sequencing and no external lookup.

The check is scripted at
`chat_nextseek/evals/survey_seqtype_labels.py`; it runs against production and
prints the cross-tab above plus every disagreement by UID:

```bash
cd chat_nextseek && uv run python evals/survey_seqtype_labels.py
```

It needs production credentials, and it only reads. Run after the fix it
reports 0 disagreements out of 114, which is what it now does.

## How the fix was applied

There is no sample-edit endpoint. The only write path into sample metadata is
`batch-upload` in upsert mode (`update_existing=true`). The fix is scripted at
`chat_nextseek/evals/fix_seqtype_labels.py`, which re-derives its 54 targets
from the same two helpers the survey uses, so the two cannot drift apart.

Without `--apply` it surveys, resolves, builds the payload and validates it
server-side. Nothing is written:

```bash
cd chat_nextseek && uv run python evals/fix_seqtype_labels.py \
    --project-id 9 --term "Single Cell RNAseq"
```

Three behaviours of the upsert path make the obvious payload the wrong one,
and the script handles each:

- Assay links are reconciled, not merged. A row without `assay_ids` deletes
  every link on that sample, and all 54 of these records carry two. The
  script resolves each record's current links first and sends them back
  unchanged, refusing to build anything if one cannot be verified.
- `title` is overwritten from the row and recomputed by `extract_identity`,
  which for a `D.` class prefers `File_PrimaryData` over `Name`. A payload
  echoing the record's full metadata therefore recomputes the title as a
  semicolon-joined list of eight FASTQ names and loses the whole batch to
  `Data too long for column 'title'`; a payload carrying `Name` would rewrite
  the title to the Name. All 108 records hold their UID as their title, which
  is what the title falls back to when the row carries no identity field at
  all. So rows carry only `SequencingType`, and the script refuses to build
  anything unless every target's title really is its UID.
- `enable_auto_permissions` defaults on and resets touched policies to
  private-by-default. The script turns it off, so a label fix cannot renarrow
  sharing.

Two things that look like safety nets and are not. The `validate` endpoint
stops before the INSERT stage, so it passed the over-long-title payload that
then failed in full. And a Celery state of `SUCCESS` only says the task
function returned: the batch whose every row failed on that DB constraint
still reported `SUCCESS`, with `processed: 54, success: 0, failed: 54` in
`result.totals`. The totals are what decides, and the script now reads them.

Every run snapshots the current metadata of all 108 records before touching
anything and refuses to overwrite an existing file. After a write it re-reads
all 108 and fails if any field other than `SequencingType` moved, or if any
assay link changed. The run that landed reported no drift on any of the 108.

## Two smaller things noticed alongside

- A lowercase L for a capital I in `DFCI2`, on three records rather than the
  one first spotted: `D.SEQ-241114SHA-14` (`GBM1_DFCl2_S10`), `-15`
  (`GBM1_DFCl2_S3`) and `-16` (`GBM1_DFCl2_S11`). All three spell `DFCI2`
  correctly in their own FASTQ names, so this was a typo in `Name` alone.
  Cosmetic, but it broke name-based grouping. **All three were fixed on
  2026-08-17**, `-15` in jobs `61be93dd` and `422ee920`, `-14` and `-16` in
  `3dce9817` and `766f0dea`. No `Name` in the study now carries it.

  Correcting `Name` needs two passes, and the script for it is
  `chat_nextseek/evals/fix_name_typo.py`. A row carrying `Name` recomputes the
  title from it, which would rename a sample whose title is its UID; a row
  carrying `File_PrimaryData` as well recomputes an over-long title and fails.
  So pass 1 sends the corrected `Name` and accepts the renamed title, and pass
  2 sends one non-identity field at its current value, which makes the title
  fall back to the UID while the deep merge keeps the corrected Name. The
  dedup key `name_identity` hashes the same identity precedence, so for these
  file-based records it comes from `File_PrimaryData` and a Name change does
  not strand it.
- `Name` is inconsistent across the study. Some records carry the full library
  name (`GBM1-DFCI4-S1-GEX-LIB`), others a sample-level name with no library
  marker (`GBM1_DFCI2_S1`, `GBM_DFCI2_S4`). The file names are complete in
  every case, which is why the check above reads both.

## The full list

All 54 had `SequencingType` changed from `Single Cell TCR` to
`Single Cell RNAseq`.

| UID | Name |
|---|---|
| `D.SEQ-241114SHA-1` | BTC-GBM-001-001-GEX |
| `D.SEQ-241114SHA-3` | BTC-GBM-001-002-GEX |
| `D.SEQ-241114SHA-5` | BTC-GBM-001-003-GEX |
| `D.SEQ-241114SHA-7` | BTC-GBM-001-004-GEX |
| `D.SEQ-241114SHA-9` | BTC-GBM-001-005-L3-GEX |
| `D.SEQ-241114SHA-11` | GBM1_DFCI2_S1 |
| `D.SEQ-241114SHA-13` | GBM1_DFCI2_S2 |
| `D.SEQ-241114SHA-15` | GBM1_DFCl2_S3 (since corrected to `GBM1_DFCI2_S3`) |
| `D.SEQ-241114SHA-17` | GBM_DFCI2_S4 |
| `D.SEQ-241114SHA-19` | GBM_DFCI2_S5 |
| `D.SEQ-241114SHA-21` | GBM_DFCI2_S6 |
| `D.SEQ-241114SHA-23` | GBM_DFCI2_S7 |
| `D.SEQ-241114SHA-25` | GBM_DFCI2_S8 |
| `D.SEQ-241114SHA-27` | GBM1_MSK1_S1 |
| `D.SEQ-241114SHA-29` | GBM1_MSK1_S2 |
| `D.SEQ-241114SHA-31` | GBM1_MSK1_S3 |
| `D.SEQ-241114SHA-33` | GBM1_MSK1_S4 |
| `D.SEQ-241114SHA-35` | GBM1-DFCI3-S1-L1-GEX-LIB |
| `D.SEQ-241114SHA-37` | GBM1-DFCI3-S1-L2-GEX-LIB |
| `D.SEQ-241114SHA-39` | GBM1-DFCI3-S3-L1-GEX-LIB |
| `D.SEQ-241114SHA-41` | GBM1-DFCI3-S3-L2-GEX-LIB |
| `D.SEQ-241114SHA-43` | GBM1-DFCI3-S4-L1-GEX-LIB |
| `D.SEQ-241114SHA-45` | GBM1-DFCI3-S4-L1Deeper-GEX-LIB |
| `D.SEQ-241114SHA-47` | GBM1-DFCI3-S4-L2-GEX-LIB |
| `D.SEQ-241114SHA-49` | GBM1-DFCI3-S4-L2Deeper-GEX-LIB |
| `D.SEQ-241114SHA-51` | GBM1-DFCI3-S5-L1-GEX-LIB |
| `D.SEQ-241114SHA-53` | GBM1-DFCI3-S5-L2-GEX-LIB |
| `D.SEQ-241114SHA-55` | GBM1-DFCI3-S6-L1-GEX-LIB |
| `D.SEQ-241114SHA-57` | GBM1-DFCI3-S6-L2-GEX-LIB |
| `D.SEQ-241114SHA-59` | GBM1-DFCI4-S1-GEX-LIB |
| `D.SEQ-241114SHA-61` | GBM1-DFCI4-S2-GEX-LIB |
| `D.SEQ-241114SHA-63` | GBM1-DFCI4-S3-L1-GEX-LIB |
| `D.SEQ-241114SHA-65` | GBM1-DFCI4-S3-L2-GEX-LIB |
| `D.SEQ-241114SHA-67` | GBM1-DFCI4-S4-L1-GEX-LIB |
| `D.SEQ-241114SHA-69` | GBM1-DFCI4-S4-L2-GEX-LIB |
| `D.SEQ-241114SHA-71` | GBM1-DFCI4-S5-L1-GEX-LIB |
| `D.SEQ-241114SHA-73` | GBM1-DFCI4-S5-L2-GEX-LIB |
| `D.SEQ-241114SHA-75` | GBM1-DFCI4-S6-L1-GEX-LIB |
| `D.SEQ-241114SHA-77` | GBM1-DFCI4-S6-L3-GEX-LIB |
| `D.SEQ-241114SHA-79` | GBM1-DFCI4-S6-L4-GEX-LIB |
| `D.SEQ-241114SHA-81` | GBM1-DFCI4-S6-L5-GEX-LIB |
| `D.SEQ-241114SHA-83` | GBM1-DFCI4-S6-L6-GEX-LIB |
| `D.SEQ-241114SHA-85` | GBM1-DFCI4-S6-L7-GEX-LIB |
| `D.SEQ-241114SHA-87` | GBM1-DFCI5-S1-GEX-LIB |
| `D.SEQ-241114SHA-89` | GBM1-DFCI5-S2-GEX-LIB |
| `D.SEQ-241114SHA-91` | GBM1-DFCI5-S3-GEX-LIB |
| `D.SEQ-241114SHA-93` | GBM1-DFCI5-S4-L1-GEX-LIB |
| `D.SEQ-241114SHA-95` | GBM1-DFCI5-S4-L2-GEX-LIB |
| `D.SEQ-241114SHA-97` | GBM1-DFCI5-S5-L1-GEX-LIB |
| `D.SEQ-241114SHA-99` | GBM1-DFCI5-S5-L2-GEX-LIB |
| `D.SEQ-241114SHA-101` | GBM1-DFCI5-S5-L3-GEX-LIB |
| `D.SEQ-241114SHA-103` | GBM1-DFCI5-S5-L4-GEX-LIB |
| `D.SEQ-241114SHA-105` | GBM1-DFCI5-S5-L5-GEX-LIB |
| `D.SEQ-241114SHA-107` | GBM1-DFCI5-S5-L6-GEX-LIB |
