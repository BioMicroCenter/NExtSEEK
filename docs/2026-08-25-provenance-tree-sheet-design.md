# The "How this data flowed" sheet becomes a tree

**Date:** 2026-08-25
**Status:** design approved, ready for implementation plan
**Related:** [`2026-08-21-download-provenance-order-and-house-vocabularies-design.md`](2026-08-21-download-provenance-order-and-house-vocabularies-design.md) — the design that created the sheet this replaces

## Problem

The sheet reads as a list of disconnected fragments. Fifteen separate rows begin
bare at `TIS`, with nothing to say where `TIS` itself came from:

```
TIS -> D.GPT
TIS -> D.IMG
TIS -> RNA
TIS -> DNA -> A.SCXP -> A.SCCL
```

This is not a defect in the implementation, it is the implementation working as
designed. `build_provenance_rows` computes a *fresh-hop path cover*: every hop
appears in exactly one row. Once `MUS -> TIS -> D.IMG` has claimed the
`TIS -> D.IMG` hop, no other row may re-tread it, so a chain arriving at `TIS`
from `AB` has nothing left to extend into and simply stops.

The optimisation is sound and the docstring justifies it: it records 129 hops
collapsing to 71 chains with nothing duplicated. (The graph has grown since that
figure was recorded — it measures 150 hops and 87 chains today. The argument is
unaffected.) But minimising rows is not what a reader
needs. A reader needs to see where a sample type came from and what was made
from it, and re-treading a shared prefix is exactly how a person reads lineage.

## Current state

`nextseek_api/services/sample_provenance.py` exposes three functions. Only the
third changes:

| Function | Role | Changes? |
|---|---|---|
| `derivation_edges` | `(parent type, child type) -> assay titles`, from Neo4j hops or the `Parent` column | no |
| `sample_type_depths` | each type's longest distance from a root, cycle-safe | no |
| `build_provenance_rows` | flat chains, one hop used once | **replaced** |

Measured on the full local graph (project 1, 51,359 samples): **150 hops, 88
types, 14 roots**.

## Goal

The sheet shows an indented tree: each root, then what was derived from it, and
what was derived from that, with the assay for each hop shown beside the type it
produced.

```
AB
├── ABP   [ELISA Assay, Flow Cytometry, Immunohistochemistry – Data Linked]
│   ├── D.ELSA   [ELISA Assay]
│   ├── D.FCRB   [FC-Receptor Binding Assay]
│   │   ├── A.FCRB   [FC Receptor Binding Analysis]
│   │   └── M.LMM   [Linear Mixed Model]
│   └── D.IMG   (expanded above)
├── D.ADCD   [Antibody Dependent Complement Deposition]
│   └── A.ADCD   [ADCD Analysis]
```

### Non-goals

- No change to `derivation_edges` or `sample_type_depths`.
- No change to the sheet's name, position, or to any other sheet.
- No image or diagram. Considered and rejected: the sample-page graph is a
  client-side D3 v3 tree with no server-side rendering, and a picture would need
  either a headless browser (~300MB of browser binaries) or graphviz as a new
  system package. Text reproduces the wanted shape at no dependency cost.
- No per-sample lineage. The sheet is per sample *type*; a download of 51,359
  samples cannot carry a graph each.

## Design

### 1. Tree construction

Build `children: {parent type -> [(child type, assays)]}` from `edges`, then
depth-first walk from each root, children sorted.

**Roots** are the types with no parent — 14 in the local graph: `A`, `AB`, `C`,
`D.WTR`, `F`, `H`, `LOC`, `MEF`, `MUS`, `NHP`, `P`, `PAT`, `T`, `WTR`.

Two guards stop the walk, and both are load-bearing:

**Repeats — `(expanded above)`.** The graph is a DAG, not a tree: `TIS` has six
parents and `D.IMG` has seventeen. Expanding every occurrence in full produces
**3,763 rows**, most of them the same subtrees over and over. A type's subtree is
therefore drawn the first time it is reached; every later occurrence renders as
`TYPE   (expanded above)` and does not recurse. This is the single decision that
makes the sheet usable: **178 rows instead of 3,763**.

**Cycles — `(cycle)`.** `CEL <-> D.FLOW`, `TIS <-> BAC`, `TIS <-> D.BSRA`,
`CEL <-> OOC` and `A.IMG <-> MDL` are all real in production. A type already on
the current path renders as `TYPE   (cycle)` and stops — the same guard
`sample_type_depths` already applies. Without it the walk does not terminate.

Note the guards are distinct and both are needed. `(cycle)` tests membership of
the *current path*; `(expanded above)` tests membership of everything expanded
*so far*. A cycle would not be caught by the repeat guard on the first pass,
because the repeat guard only fires once a subtree has been completed.

### 2. Assay labels

Each line shows the assays recorded on the hop **into** that type:
`D.FCRB   [FC-Receptor Binding Assay]`. Sorted, comma-joined, empty set renders
no bracket at all.

This is per-hop, not per-type, and that distinction matters. Attaching assays to
the type instead — as a one-row-per-type variant would force — merges every
incoming edge's assays into one cell: `D.IMG` would carry 19 assay names and a
trailing `(also from AB, ABP, C, CEL, CHM, D.NMR, ...)`. That was measured, and
rejected as exactly the clutter this redesign removes.

### 3. Sheet rendering

One column. Each tree line is written to column A of the "How this data flowed"
sheet, and a monospace font (`Consolas`, falling back to the workbook default) is
set on those cells via openpyxl so the box-drawing characters align. Column width
is set to fit the longest line.

The box-drawing characters (`├── └── │`) carry the structure even if a reader's
Excel substitutes a proportional font: the tree is still readable, merely ragged.

### 4. What replaces what

`build_provenance_rows(edges, depths) -> list[list[str]]` is replaced by
`build_provenance_tree(edges, depths) -> list[str]`, returning one string per
line. The caller in `sample_workbook.py` writes one cell per line rather than
one row of alternating cells, and applies the font.

## Measurements

All from project 1 on the local instance, 51,359 samples:

| Variant | Rows | Verdict |
|---|---|---|
| Current flat chains | 87 | fragments, 15 rows orphaned at `TIS` |
| Full tree expansion | 3,763 | faithful, unreadable |
| **Tree, repeats collapsed** | **178** | chosen |
| One row per type | 102 | assays merge into 19-name cells; rejected |

## Testing

`nextseek_api/tests/test_sample_provenance.py` has 22 tests. The 14 covering
`derivation_edges` and `sample_type_depths` are untouched. Of the 8 covering
`build_provenance_rows`:

- `test_every_hop_appears_exactly_once` becomes *at least* once
- `test_a_chain_may_start_mid_pipeline` inverts: every line is reachable from a
  root, because the walk starts at roots
- `test_a_chain_reads_left_to_right_across_the_row` is replaced by an
  indentation assertion
- the arrow-formatting tests move to the bracket format

New tests:

- every hop appears somewhere in the tree
- a type with two parents is expanded once and referenced once
- a two-cycle renders `(cycle)` and terminates
- roots appear at zero indentation, in sorted order
- an empty edge set yields no lines

## Risks

**A reader may miss a subtree behind `(expanded above)`.** The information is
present but not local. Accepted: the alternative is 3,763 rows, and the marker
names the type so it can be found.

**Excel may not honour the monospace font.** The tree degrades to ragged but
readable. Accepted.

**Row count grows with graph density, not sample count.** 178 rows for 150 hops.
A far denser graph could push this up, but it is bounded by the number of hops,
not by the 51,359 samples, so it cannot blow up with download size.
