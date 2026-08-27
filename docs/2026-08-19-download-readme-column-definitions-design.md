# Download README — per-column definitions

> **Renamed since this document was written (2026-08-21):** the table
> `dmac.sample_fields_context` is now `dmac.sample_attributes_unique`, the model
> `Sample_fields_context` is now `Sample_attributes_unique`, and the DDL/data files
> are `startup/seed/sql/sample_attributes_unique{,_data}.sql`. Names below are kept
> as originally written; read them against the new ones.

**Date:** 2026-08-19
**Branch:** `feat/download-readme-columns` (worktree, based on `origin/main` @ `ffc3cb60`)
**Status:** design approved, ready for implementation plan
**Related:** [`2026-08-06-unified-sample-download-readme-design.md`](2026-08-06-unified-sample-download-readme-design.md) — the design that created the README sheet this extends

## Problem

A sample download hands the researcher a workbook with one tab per sample type
and a README sheet that says what each *tab* is. It says nothing about what each
*column* is. A researcher opening the `MUS` tab sees `Treatment1Route`,
`Group_InCohort`, `Checksum_PrimaryData` and has no way, inside the file, to
learn what any of them mean.

## Current state

`nextseek_api/services/sample_workbook.py` is the single writer for every sample
download; both live call sites (`seek/views.py:1236`,
`seek/dbtable_sample.py:944`) funnel through `write_samples_workbook`, so the
README cannot drift between the legacy `seek` path and the `nextseek_api`
endpoint. It writes:

- **A1** — hyperlink to `sampletypes_db.json` on GitHub
- **row 2** — blank
- **row 3** — header `Sample Type` | `Name` | `Description`
- **rows 4+** — one row per sample type present, sorted by code

Name and description come from `dmac.sample_types_context`, joined on the
`sample_type` string code. Undocumented codes are listed with blanks rather than
omitted, so the README always indexes every sheet and a gap is visible instead
of silent. 21 tests in `nextseek_api/tests/test_sample_workbook.py` cover it.

### Per-column descriptions do not exist anywhere

Both plausible sources were checked against the local seed (51,359 samples, 80
sample types) and neither holds prose:

| Source | What it has | What it lacks |
|---|---|---|
| `seek_production.sample_attributes` | 2,650 rows, 1,097 distinct titles, 195 flagged required | `description` populated on **0 of 2,650 rows** — an empty schema slot |
| `dmac.sample_types_context` / `sampletypes_db.json` | per-sample-type field *name* lists, bucketed Required / Standard / Possible | no prose for any individual field |

### Neither registry describes what researchers actually receive

Workbook columns come from `pd.json_normalize` over each sample's
`json_metadata`, so the live keys are the ground truth. Measured against them:

- **681** distinct column names are in live use across 80 sample types
- **632** are registered in `sample_attributes`; **49 in use are not registered
  at all**; **465 registered names are never used by any sample**
- `sampletypes_db.json`'s tiered lists cover **626 of 681**

So no registry is a superset of live usage, and syncing from one would miss
real columns. Only the downloaded data itself is authoritative.

### Field names are overwhelmingly type-specific

- **546 of 681 (80%)** appear in exactly one sample type
- **135** are shared, and the most-shared are generic: `UID` (80 types),
  `Scientist` (80), `Parent` (75), `Protocol` (70), `File_PrimaryData` (64),
  `Link_PrimaryData` (64), `Checksum_PrimaryData` (55), `Type` (40),
  `SampleCreationDate` (38), `Name` (36)
- Median 17 columns per tab; widest is `D.SNSR` at 88

A dictionary keyed on field name alone is 681 entries; keyed per
(sample type, field) it is 1,673. The 80% single-type majority makes the
distinction moot for most fields, and only a handful — `Name`, `Type`, `Parent`
— plausibly differ in meaning between tabs.

### Noted in passing, out of scope

One live field name is `'C3Ab_Catalog# '`, with a trailing space. It is a data
hygiene problem in `json_metadata`, not a README problem, and is left alone
here. It will render as a column row with a trailing space.

## Goal

Every column in a downloaded workbook is accounted for on the README sheet, with
a plain-English meaning where one has been written and reviewed, organised so a
researcher can read the README beside the tab it describes.

### Non-goals

- No change to the download API, views, URLs, permissions, or frontend.
- No Django migration (see "Schema" — this table family is created out-of-band).
- No definitions for `assay_context` or `projects_context`, still absent from
  the seed and still out of scope.
- No attempt to reconcile `sample_attributes` with live usage, or to fix the
  trailing-space field name.
- No automatic publication of undrafted or unreviewed definition text.

## Design

### 1. Where the change lands

Entirely inside `nextseek_api/services/sample_workbook.py`, plus one model in
`seek/models.py` and one new table. Both download paths inherit the change for
free because they already share the writer.

The column list for each tab is computed **after** the existing
`dropna(axis=1, how="all")`, so the README never documents a column the
researcher cannot see in the workbook.

### 2. Storage — mimic `sample_types_context`

Definitions live in a new `dmac.sample_attributes_unique`, a sibling of
`sample_types_context`, with the repo-side JSON generated from it exactly as
`sampletypes_db.json` is generated from `sample_types_context` today
(`chat_nextseek/src/chat_nextseek/config.py:822`).

```sql
CREATE TABLE `sample_attributes_unique` (
  `id`          int NOT NULL AUTO_INCREMENT,
  `field_name`  varchar(255) NOT NULL,
  `sample_type` varchar(32)  NOT NULL DEFAULT '',
  `meaning`     text,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_field_scope` (`field_name`, `sample_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

`sample_type = ''` is the global definition for that field name; a sample type
code is a per-tab override. This is the global-plus-override model the 80/20
split argues for: at most 681 global entries — fewer in practice, since a
column judged self-evident gets no row at all — with overrides only for `Name`,
`Type`, `Parent` and the like.

**`sample_type` is `NOT NULL DEFAULT ''`, not nullable, deliberately.** MySQL
treats NULLs as distinct within a unique index, so a nullable scope column would
accept two conflicting global definitions for the same field name and the
constraint would never fire.

**The join key is the `field_name` string, never an id.** This repeats the
lesson from the sample-type README: `sample_types_context.sampletype_id` does
not agree with `sample_types.id` across instances.

Lookup precedence per column, per tab:

1. row where `sample_type` = the tab's code
2. row where `sample_type` = `''`
3. blank

### 3. Model and loader

`seek/models.py` gains `Sample_attributes_unique`, mirroring `Sample_types_context`
(`_DATABASE = NEXTSEEK_DATABASE`, `db_table = "sample_attributes_unique"`).

`sample_workbook.py` gains `load_sample_field_context(pairs)` beside
`load_sample_type_context`. It takes the `(sample_type, field_name)` pairs
actually present in the workbook and returns
`{(sample_type, field_name): meaning}` already resolved through the precedence
above, so the caller never re-implements the fallback. It carries the same
fail-soft posture: a missing,
unreachable, or malformed table is logged and yields blanks rather than costing
the user their download.

That posture defines the rollout behaviour. The DDL must be run on production
before the feature is useful there; until it is, the README renders every
meaning blank — degraded, not broken.

### 4. Seed

Fold the table and its rows into `startup/seed/dmac.sql.gz` via the existing
maintainer command `./startup.sh dump-db`, so a fresh install renders real
definitions. `sample_types_context` had to be seeded for exactly this reason;
`assay_context` and `projects_context` are still missing and still cause blank
context on fresh installs.

### 5. Schema management

`sample_types_context` has **no Django migration** — no file under
`seek/migrations/` references it. The table is created out-of-band in SQL and
the model simply maps it. The new table follows the same convention: DDL in the
seed and in a documented production step, model without a migration.

### 6. Export for the assistant

Register `dmac.sample_attributes_unique` in `_fetch_context_files_from_db`
(`chat_nextseek/src/chat_nextseek/config.py:822`) so `samplefields_db.json` is
generated alongside `sampletypes_db.json`. The assistant then answers column
questions from the same definitions the workbook shows.

The README's A1 hyperlink is **unchanged** — it keeps pointing at
`sampletypes_db.json`. Adding a second link would shift every row index in the
layout below for no reader benefit, since the definitions are already in the
sheet.

### 7. README sheet layout

The flat table becomes one section per tab, ordered by sample type code — the
same order `groupby` writes the sheets in, so README sections and tabs stay in
lockstep.

```
A1   Sample type definitions: sampletypes_db.json (GitHub)
     (blank)
A3   MUS — Mouse                                    [bold]
A4   A mouse sample represents an experimental animal used as a model system…
     (blank)
B6   Column              C6  Meaning                [bold]
B7   UID                 C7  The unique NExtSEEK identifier for this sample.
B8   Name                C8  The animal's facility or ear-tag ID (e.g. 19-1892).
B9   Sex                 C9
B10  Treatment1Route     C10 How the first treatment was administered.
     (blank)
A12  D.SEQ — Sequencing                             [bold]
…
```

- Section headings sit in column A; the column table indents into B/C, which is
  what makes sections scannable rather than one undifferentiated grid.
- Column widths: A=22, B=34, C=100.
- **Columns appear in sheet order, not alphabetically**, so the README can be
  read side by side with the tab.
- A sample type with no `sample_types_context` entry still gets a section, with
  the bare code as its heading and a blank description row.

### 8. Undefined columns are listed, not omitted

Every column in the workbook gets a row. A column with no reviewed definition
gets a blank `Meaning`. This is the principle the README already applies to
undocumented sample types: the index is always complete, and a gap is visible
rather than silent.

Accepted consequence: a wide tab renders many blank rows at first — `D.SNSR`
produces 88 rows. Coverage improves over time; completeness is guaranteed from
day one.

This is also what makes new sample types and attributes appear automatically.
Because rows derive from the actual columns in the actual download, a new sample
type gets a section the first time anyone downloads one, and a new attribute
gets a row the first time it carries a value. Nothing registers it, no sync job
runs, and it cannot be silently missed.

### 9. Authoring the definitions

Definitions are written for columns whose meaning is not self-evident. A column
judged obvious simply has no row in the table and renders blank — "obvious" and
"undocumented" are deliberately not distinguished in the output.

First release is drafted then reviewed:

`scripts/draft_field_definitions.py` (maintainer-only, never invoked at download
time) assembles per field name: which sample types carry it, its
Required / Standard / Possible tier from `sample_types_context`, the owning
sample type's own description, and up to five real distinct values from
`seek_production.samples`. Real values are the strongest signal available —
`Treatment1Route` is guesswork from the name, and obvious once the values
`intraperitoneal` and `oral gavage` are visible.

It emits an xlsx review sheet: field name · sample types · example values ·
drafted meaning · reviewer's edit. 681 rows is a spreadsheet job.

Two rules bind the review:

- **A plausible but wrong definition is worse than a blank.** Anything the
  reviewer is unsure of is deleted, not kept.
- **Example values come from real records.** No scientist name or personal
  identifier may survive into committed definition text.

`manage.py load_field_definitions <xlsx>` upserts the reviewed sheet into
`sample_attributes_unique`.

### 10. Maintenance loop

1. `manage.py field_definitions_report` — lists undefined field names, ranked by
   how many samples carry them, so the largest gaps surface first
2. `scripts/draft_field_definitions.py` — drafts **only names absent from the
   table**; the drafter is incremental by construction, not a one-shot
3. reviewer prunes and edits the xlsx
4. `manage.py load_field_definitions <xlsx>` — upserts

A curator who wants a one-off correction edits the table directly in production
and skips the loop entirely; no deploy, no PR.

## Implementation phasing

The work splits cleanly in two, and the plan should stage it that way. Phase 1
is shippable on its own: the README is already more complete and more honest
with an empty definitions table than it is today, because every column is at
least indexed under its tab.

**Phase 1 — mechanism.** Table DDL, seed, model, `load_sample_field_context`,
the sectioned README layout, the rewritten tests. Ships with whatever
definitions exist, including none.

**Phase 2 — content and upkeep.** `draft_field_definitions.py`, the review
round trip, `load_field_definitions`, `field_definitions_report`, and the
`config.py` export registration.

## Testing

The 21 existing tests assert the flat-table shape and are rewritten alongside
`build_readme_rows`. New coverage:

- lookup precedence: per-sample-type row beats global row beats blank
- an unknown field name renders blank and never raises
- a missing or malformed `sample_attributes_unique` yields blanks everywhere and
  still writes a complete workbook
- listed columns match the columns actually written, after the all-empty-column
  drop
- README section order matches sheet order
- a sample type with no context entry still gets a section
- a column ordering test pinning sheet order rather than alphabetical
- round trip: `load_field_definitions` on a small xlsx, then a workbook write
  that shows those meanings

## Rollout

1. Run the DDL on production `dmac`.
2. Load reviewed definitions via `manage.py load_field_definitions`.
3. Deploy. Until steps 1–2 land, the README renders blank meanings — the
   fail-soft loader means there is no window where downloads break.

## Decisions the plan inherits rather than reopens

- Bold is applied with `openpyxl.styles.Font(bold=True)` on the section heading
  cell and on the `Column` / `Meaning` header cells. No other styling.
- `field_definitions_report` writes a table to stdout, with an optional
  `--out <path>` to also write csv. Stdout is the default because the common use
  is a human asking "what is missing right now".
