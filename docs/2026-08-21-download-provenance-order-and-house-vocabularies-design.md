# Download workbook: provenance order, tab order, and house vocabularies

**Date:** 2026-08-21
**Branch:** `feat/download-readme-columns` (PR #111, open against `dev`)
**Status:** design approved, implementation not started

Finishes the download-workbook work before the production SQL publish. Four
changes, all in `nextseek_api/services/sample_workbook.py` plus
`controlled_vocabularies.json`. No new tables, no new production SQL script.

## Problem

Three complaints from the user, plus one consequence discovered while
designing:

1. The provenance section lists hops in **alphabetical** order, one long text
   string per hop in column A. It should read in the order samples were
   generated, laid out left to right across the sheet.
2. Dropdowns stop at the last filled row. A researcher adding rows loses them.
3. Dropdown options are GEO/SRA's terms, which frequently are **not** the values
   NExtSEEK actually holds — so the dropdown does not drive curators toward the
   house convention. Measured on production: `LibraryDesign` is `Paired End`
   3511 / `Paired` 789 / `PAIRED` 705 / `Paired-end` 218 / `Paired end` 118 /
   `paired` 52, and the dropdown offers only `paired`, the rarest form.
4. Sample **tabs** are alphabetical too, for the same underlying reason
   (`df.groupby("sample_type")` sorts). They should follow provenance order.

## Background: what is and is not available

- **SEEK's controlled-vocabulary tables cannot be the source.** Measured:
  6 vocabularies, 2,362 terms, all stock EDAM/SysMO, and
  `sample_attributes.sample_controlled_vocab_id` is populated on **0** rows.
  "Values used within NExtSEEK" can therefore only mean *observed sample
  values*.
- **The type-level derivation graph is not a tree.** Measured from Neo4j on the
  local stack: 77 sample types, 129 distinct type→type hops, longest chain 12,
  and 5 genuine 2-cycles: `CEL ↔ D.FLOW`, `TIS ↔ BAC`, `TIS ↔ D.BSRA`,
  `CEL ↔ OOC`, `A.IMG ↔ MDL`. Any ordering or traversal must be cycle-safe.
  These graph figures are the one thing here still measured locally — the
  production graph is larger, so treat 129 hops / 71 chains as a floor on the
  worst case, not a ceiling. The algorithm is correct at any size; only the
  row count in the worst case moves.

## Change 1 — provenance depth, and chains that read left to right

### Depth

Each sample type in the download's hop set gets a **depth** = the longest path
to it from any type with no parent in that set. Cycle-safe: memoise, and treat
a node already on the current recursion stack as depth 0 rather than recursing.
Nodes are visited in sorted order so that a cycle resolves the same way on
every run — without that, which member of `CEL ↔ D.FLOW` gets depth 0 would
depend on dict iteration.

Depth is the definition of "generation order" used everywhere below.
`PAT`=0, `PAV`=1, `TIS`=2, `DNA`=3, `D.SEQ`=4.

### Chain building: a fresh-hop path cover

```
free = all hops
while free:
    seed  = the hop in free with the lowest (depth of parent, parent, child)
    chain = [seed.parent, seed.child];  remove seed from free
    extend forward:  from chain[-1], take the free out-hop with lowest
                     (depth of child, child) whose child is not already in
                     chain; append; remove from free. Repeat.
    extend backward: from chain[0], take the free in-hop with the highest
                     (depth of parent, parent) whose parent is not already in
                     chain; prepend; remove from free. Repeat.
    emit chain
sort chains by (depth of first type, first type)
```

The `not already in chain` guard is what makes cycles safe — a chain can never
revisit a type.

**Verified against the full local graph:** 129 hops → 71 chains, every hop
covered exactly once, widest chain 13 cells. 71 is the worst case for the
*entire* 77-type graph; a single project's download produces a handful of rows.

Rejected alternative: root-anchored paths, longest first, keeping any path that
covers a new hop. Measured 89 chains that re-tread shared prefixes
(`MUS→PAV→TIS→BAC→CEL→OOC→RNA→DNA→D.SEQ→A.GEX→MDL→A.IMG` and five near-identical
neighbours) and still missed 2 hops reachable only through cycles.

Chains that start mid-pipeline (`D.SEQ --[…]--> A.MUSP`) are expected and
correct — they carry hops whose upstream was already shown.

### Layout: a dedicated sheet

Chains go on their own sheet, **`How this data flowed`**, inserted immediately
after README. README keeps a one-line pointer to it.

Rationale: README's columns are sized A=46 / B=34 / C=100 for the summary and
column tables. A chain laid across those widths puts a 100-wide gap at its
second type. The new sheet sets its own widths — type cells 14, arrow cells 34.

Cell layout per chain row, starting at column A:

| A | B | C | D | E |
|---|---|---|---|---|
| `PAT` | `--[Consent]-->` | `PAV` | `--[Tissue Collection]-->` | `TIS` |

Types in odd columns, arrows in even. Arrow cell is `--[Assay]-->`, or
`------>` when the hop carries no assay title. Several assays on one hop join
with `, ` — unchanged from today.

### What is preserved

- Neo4j remains the authority for the assay on a hop; the `Parent`-column
  fallback and the 5s timeout are unchanged.
- Upstream types not themselves downloaded still appear — that is the part a
  reader cannot otherwise see.

## Change 2 — sheet and section order follows provenance

`prepared` is currently built from `df.groupby("sample_type")`, which sorts
alphabetically. Sort it by `(depth, code)` instead.

This requires moving the hop lookup (`load_derivation_hops`) **above** sheet
preparation, since depth is derived from the hops. The hops are already loaded
once per download; this is a reordering, not an extra query.

Consequences, all intended:

- Sample **tabs** appear in generation order.
- The README **summary table** and the per-tab **column sections** follow the
  same order, because both derive from the same `sheets` list.

Fallbacks:

- A type that appears in **no hop at all** — neither as a parent nor as a child,
  i.e. isolated, not merely a root — gets depth `+∞` and sorts to the end,
  alphabetically among its peers.
- No hops at all (Neo4j unreachable *and* no usable `Parent` column) → order
  stays alphabetical, exactly as today.

## Change 3 — dropdowns extend past the filled rows

In `_apply_dropdowns`, the range becomes the last filled row **plus 500**:

```python
last = max(row_count + 1, 2) + DROPDOWN_SPARE_ROWS   # DROPDOWN_SPARE_ROWS = 500
rule.add(f"{letter}2:{letter}{last}")
```

Full-column validation (`2:1048576`) was rejected: some tools slow noticeably
on full-column validation applied across many columns.

## Change 4 — canonical NExtSEEK house vocabularies

Derived from **production** (166,235 samples), not the local seed. The seed
would have been materially wrong: it showed 21 distinct `Sequencer` values
where production has 100, and 71 `DataType` values where production has 102.

`controlled_vocabularies.json` is rewritten. `_source` changes from "the
repositories' own" to a statement that these are NExtSEEK's house terms, chosen
from the values NExtSEEK actually holds. `field_map` is unchanged.

### The house rules

1. One term per concept — the spelling production uses most, unless rule 2 or 3
   overrides.
2. Where GEO already has the concept and NExtSEEK agrees on spelling, GEO's
   spelling wins (it is the manufacturer's or the repository's own name).
3. **Illumina instruments are always prefixed `Illumina `.** GEO is internally
   inconsistent here — it has `Illumina MiSeq`, `Illumina HiSeq 2500` and
   `Illumina NovaSeq 6000` but bare `NextSeq 500`, `NextSeq 550`, `NextSeq 1000`,
   `NextSeq 2000`. Production splits on exactly that seam (`Illumina NextSeq 500`
   526 vs bare `NextSeq 500` 390). Prefixing all of them is what stops the split
   recurring.
4. Values that are not of the column's kind are excluded, however many rows use
   them. A kit name is not a library layout; a strategy is not a selection.

### `LibraryDesign` → `library_layout`

`Paired End`, `Single End`

Absorbs `Paired End` 3511, `Paired` 789, `PAIRED` 705, `Paired-end` 218,
`Paired end` 118, `paired` 52.

Excluded, per rule 4: `3DGE` (384, a prep method), `Duplex Sequencing Mouse
Mutagenesis Panel v2.0` (32, a kit), `Paired-End 2x75nt` (4, a read config).

**Production holds no single-end value at all.** `Single End` is offered
prospectively so the concept has a term when it is first needed.

### `LibrarySource` → `library_source`

`Genomic`, `Transcriptomic`

Casing only, no exclusions: `Genomic` 1504 / `GENOMIC` 1284, and
`transcriptomic` 1034 / `TRANSCRIPTOMIC` 907 / `Transcriptomic` 688.

### `LibraryStrategy` → `library_strategy`

`RNA-Seq`, `Bulk RNA-Seq`, `scRNA-Seq`, `WGS`, `Amplicon`, `Targeted Capture`,
`Hi-C`, `Other`

`RNA-Seq` absorbs 2001 + `RNA-seq` 196 + `RNAseq` 102 + `RNA-SEQ` 2.
`Amplicon` absorbs 755 + `AMPLICON` 563. `Other` absorbs `OTHER` 32.

`Bulk RNA-Seq` (186) is kept as a distinct term rather than folded into
`RNA-Seq`: the distinction from `scRNA-Seq` (142) is one the labs are actively
making.

### `LibrarySelection` → `library_selection`

`Other`, `cDNA`, `PCR`, `RT-PCR`, `PolyA`, `Hybrid Selection`, `Inverse rRNA`,
`RANDOM`

`Other` absorbs `other` 1489 + `Other` 25 — the single most common value in the
column. `RANDOM` absorbs 22 + `Random` 4 (GEO's spelling, and the production
majority). `Inverse rRNA` is GEO's term for `Inverse rRNA selection` (27).

Excluded, per rule 4: `Whole Genome Sequencing` (292) — a strategy written into
the selection column.

### `DataType` → `filetype`

Bare uppercase extensions, 34 terms:

`FASTQ`, `BAM`, `FAST5`, `POD5`, `H5AD`, `H5`, `MTX`, `MCOOL`, `HIC`, `RDS`,
`MZML`, `TIF`, `OIB`, `OIF`, `LIF`, `CZI`, `ND2`, `DICOM`, `NII`, `JPG`, `PNG`,
`AVI`, `MOV`, `CSV`, `TSV`, `TXT`, `XLSX`, `XML`, `PDF`, `DOCX`, `PPTX`, `PZFX`,
`SLX`, `SBD`

Notable absorptions: `TIF` takes `tif` 5877 + `TIF` 2447 + `tiff` 666 + `.tif`
24 + `TIFF` 4 + `ome.tif` 3 = 9,021 rows across six spellings. `FASTQ` takes
4202 + `fastq` 1655 + `Fastq` 332 + `fastq.gz` 226 + `FastQ` 202. `DICOM` takes
`.dcm` 345 + `DICOM` 239 + `Dicom` 97. `CZI` takes `.czi` 360 + `CZI` 276 +
`czi` 109. `XML` takes `tiff_metadata.xml` 636 (a filename in a format column).
`JPG` takes 1172 + `JPG` 17 + `JPEG` 7 + `jpeg` 4.

The leading dot is dropped (`.lif` 1038 → `LIF`) and compression suffixes are
dropped (`fastq.gz` → `FASTQ`, `nii.gz` → `NII`): the column names a format,
not a filename.

Excluded, per rule 4 — results and descriptions written into a format column:
the 22 distinct `Ki-67 proliferation index = …` values (1 row each),
`Targeted LC-MS metabolomics + stable-isotope tracing` (126),
`Super-resolution localization microscopy (STORM)` (76),
`MALDI-MSI spatial molecular images / annotated features` (42),
`IHC / histology (Ki-67, H&E)` (35), `PRISM` (18),
`mzML (converted; original .raw not deposited to PXD055726)` (4),
`Mutational spectra catalogue (absolute)` and `(differential)` (1 each).

### `Sequencer` → `instrument_model`

GEO's 82-instrument catalog, with rule 3 applied and NExtSEEK's own instruments
appended.

Renamed under rule 3: `NextSeq 500` → `Illumina NextSeq 500`, `NextSeq 550` →
`Illumina NextSeq 550`, `NextSeq 1000` → `Illumina NextSeq 1000`, `NextSeq 2000`
→ `Illumina NextSeq 2000`. The bare forms are **not** also offered — offering
both is what produced the split.

Appended (in production, absent from GEO): `Singular G4` (130 + `SingularG4` 8),
`PromethION P2 Solo` (`P2 Solo` 9).

Mapped to existing GEO terms: `Element Aviti` 190 + `Aviti` 76 + `Element AVITI`
4 → GEO's `Element AVITI`. `Prometheon 24` 19 → GEO's `PromethION`.
`Illumina NovaSeqX` 80 + `Illumina NovaSeq X 25B` 27 → `Illumina NovaSeq X`
(25B is a flow cell, not a model).

Excluded:

- **The `NovaSeq 60NN` counter artifact — 61 of the 100 distinct values.**
  `Illumina NovaSeq 6001`…`6038`, `Illumina NovaSeq6001`…`6023`, and
  `NovaSeq 6001`…`6011`, every one at exactly 1 row. Someone numbered
  instruments sequentially. The seed showed only 11 of these; production has 61.
- `{"vendor": "10x, Illumina", "platform": "Chromium,NovaSeq6000"}` (47) — a
  JSON blob in a text column.
- `P.BMC-240301-V1_Illumina_NextSeq_Standard_Workflow.doc` (2) — a protocol
  filename.
- `NextSeq500:George` (7) — an instrument nickname.
- `Illumina HiSeq` (95) — no model; ambiguous between six GEO HiSeq entries.

### The `_variants` block

A new documentation-only key records which observed spellings each canonical
term absorbs, with the production row counts they were judged from:

```json
"_variants": {
  "library_layout": {
    "Paired End": ["Paired End (3511)", "Paired (789)", "PAIRED (705)",
                   "Paired-end (218)", "Paired end (118)", "paired (52)"]
  }
}
```

Nothing reads it. It exists so a later normalisation pass is mechanical rather
than re-derived, and so the basis for each judgement survives review.

### errorStyle stays `warning`

Unchanged, and now more necessary, not less: the house list deliberately
excludes spellings that thousands of existing rows use. A hard reject would
fire on open for rows the researcher never touched (ANN-9).

## Testing

Extend `nextseek_api/tests/test_sample_workbook.py`.

Depth and chains — pure functions, no DB:

- depth is longest-path, not shortest, when a type has two parents at different
  depths
- a 2-cycle terminates and yields a chain that visits each type once
- every input hop appears in exactly one emitted chain
- chains sort by the depth of their first type
- a hop with no assay renders `------>`; several assays join with `, `

Order:

- tabs and README sections both follow `(depth, code)`
- a type with no hops sorts last, alphabetically among its peers
- no hops at all → alphabetical, unchanged

Dropdowns:

- validation range ends at last filled row + 500
- a sheet with zero data rows still gets a usable range
- every `field_map` column resolves to a vocabulary that exists

Vocabularies:

- the JSON parses, and every `field_map` value is a key in `vocabularies`
- no vocabulary term contains a character Excel rejects
- `_variants` keys are a subset of `vocabularies` keys (guards drift)

## Risks

- **The house lists deliberately exclude values thousands of production rows
  use.** By design — that is what makes them converge — but a researcher who
  downloads existing data will see warnings on rows they never touched.
  Measured worst cases: `Whole Genome Sequencing` 292 rows, `3DGE` 384 rows,
  `Illumina HiSeq` 95 rows, and every `tif`/`fastq` casing variant.
  `errorStyle` stays `warning` precisely for this; do not change it (ANN-9).
- **Row counts are a snapshot of 2026-08-21 production** (166,235 samples).
  The judgements that turned on a majority — `Illumina NextSeq 500` 526 vs bare
  `NextSeq 500` 390 being the closest — could invert as data grows. The
  `_variants` block records the counts each call rested on, so the call can be
  re-examined rather than re-guessed.
- **Five exclusions are judgement, not arithmetic**, and are the likeliest
  place this is wrong: `3DGE` (384), `Whole Genome Sequencing` (292),
  `Illumina HiSeq` (95), keeping `Bulk RNA-Seq` separate from `RNA-Seq`, and
  dropping compression suffixes so `fastq.gz` becomes `FASTQ`.
- **Moving `load_derivation_hops` earlier** puts a Neo4j call ahead of sheet
  preparation. It is already bounded at 5s and already fails soft; the failure
  mode on timeout is now "alphabetical order plus no provenance sheet" rather
  than "alphabetical order plus no provenance section". Issue #110 (profiling
  that timeout at production scale) is unchanged and still open.
- **Tab order changes are visible to anyone with an existing workflow** that
  assumed alphabetical tabs. No such consumer is known in this repo.

## Out of scope

- Normalising the existing data to the house terms.
- The missing `platform` column for SRA (ANN-8).
- Anything touching `sample_fields_context` or the three production SQL scripts.
