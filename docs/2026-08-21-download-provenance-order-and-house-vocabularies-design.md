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
   house convention. Measured on the local stack: `LibraryDesign` is
   `Paired End` 946 / `Paired` 595 / `PAIRED` 226 / `Paired end` 162 /
   `paired` 126, and the dropdown offers only `paired`, the rarest form.
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

`controlled_vocabularies.json` is rewritten. `_source` changes from "the
repositories' own" to a statement that these are NExtSEEK's house terms, chosen
from the values NExtSEEK actually holds.

`field_map` is unchanged. `vocabularies` becomes:

| Column | Vocabulary | Terms |
|---|---|---|
| `LibraryDesign` | `library_layout` | `Paired End`, `Single End` |
| `LibrarySource` | `library_source` | `Genomic`, `Transcriptomic` |
| `LibraryStrategy` | `library_strategy` | `Amplicon`, `RNA-Seq`, `scRNA-Seq`, `WGS`, `Targeted Capture`, `Hi-C` |
| `LibrarySelection` | `library_selection` | `PCR`, `RT-PCR`, `cDNA`, `PolyA`, `Hybrid Selection`, `RANDOM`, `Other` |
| `DataType` | `filetype` | `FASTQ`, `BAM`, `TIF`, `OIB`, `CZI`, `DICOM`, `JPG`, `JPEG`, `PNG`, `ND2`, `OIF`, `H5AD`, `H5`, `HIC`, `MCOOL`, `MTX`, `RDS`, `CSV`, `TXT`, `XLSX`, `AVI`, `MOV` |
| `Sequencer` | `instrument_model` | GEO's 82-instrument catalog **+** `Illumina NextSeq 500`, `Illumina NextSeq 550` **+** `Singular G4` |

`Sequencer` is deliberately the one union rather than a house list: GEO's list
is a manufacturer catalog, not a style choice, and NExtSEEK holds only six real
instruments from it. Researchers will legitimately use instruments NExtSEEK has
never seen.

### Exclusions, and why

- **Prose in `DataType`.** 38 of its 71 distinct values are Ki-67 measurement
  results (`Ki-67 proliferation index = 70.16% positive nuclei (GBM12, Control
  diet)`), one row each, plus
  `Targeted LC-MS metabolomics + stable-isotope tracing` (90) and
  `IHC / histology (Ki-67, H&E)` (35). These are results and descriptions
  written into a filetype column. Not offered as terms. Same shape as ANN-9's
  finding; cleaning them is separate work.
- **`NovaSeq 6001`–`NovaSeq 6011`**, 11 values at 1 row each, and
  `NovaSeq 6000, Illumina` (24). Treated as typos for
  `Illumina NovaSeq 6000` (427).

### The `_variants` block

A new documentation-only key records which observed spellings each canonical
term absorbs, with the row counts they were judged from:

```json
"_variants": {
  "library_layout": {
    "Paired End": ["Paired End (946)", "Paired (595)", "PAIRED (226)",
                   "Paired end (162)", "paired (126)"]
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

- **The house lists are derived from the local seed (51k samples), not
  production (166k).** ANN-16's standing warning is to measure against
  production for anything driving a decision, and this decides which spelling
  becomes canonical. Production may hold instruments or filetypes the seed does
  not; those would surface as warnings on open, not as errors. Re-deriving
  against production before the lists are written is the mitigation, and needs
  `ssh fairdata` unblocked.
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
