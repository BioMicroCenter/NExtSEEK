# Sample download: how it worked, and how it works now

Every "Download samples" control in NExtSEEK. Written while unifying them onto
one API and adding a README sheet to the workbook — see
`docs/2026-08-06-unified-sample-download-readme-design.md` for the design and
`...-plan.md` for the implementation steps.

**Line numbers under "Before" refer to commit `a8fc358`**, the branch base.
Everything under "After" refers to the current tree.

---

## Before

### Live controls

Three rendered pages carried seven controls between them.

**`/seek/search/` → `searchAdvanced.html`** (`seek/views.py:869`)

| | Control | Went to |
|---|---|---|
| a | Retrieval form | `POST /seek/admin/retrieve/` |
| b | Simple tab grid → `simple_downloadSamples` | Yes/No prompt. Yes → `/seek/admin/retrieve/`; No → `/seek/samples/download/` |
| c | Advanced tab grid → `downloadSamples0` | Always `/seek/admin/retrieve/` |

**`/seek/newsearch/` → `newSearch.html`** (`seek/views.py:1742`)

| | Control | Went to |
|---|---|---|
| d | Simple grid → `downloadSamples` | `POST /seek/samples/download/`, `includeSampleTree=0` |
| e | Advanced grid → same function | same |
| f | Retrieval form | `POST /nextseek_api/admin/samples/retrieve/` |

**Sample detail page → `pages/samples.embed.html`** (via `samples.html`, `seek/views.py:166`)

| | Control | Went to |
|---|---|---|
| g | "Download All Samples" form | `POST /seek/admin/retrieve/` |

### Backends

- **`seek/views.py:adminRetrieveSamples`** — expanded the selection over Neo4j
  `DERIVED_FROM*0..` in both directions, fetched `json_metadata` from MySQL,
  `pd.json_normalize`d it, wrote one xlsx with a sheet per sample type via the
  module function `sample_retrieval_data`. Despite the URL, not admin-gated:
  `verifySuperUser` only widened the project scope.
- **`nextseek_api/views.py:admin_retrieve_samples`** — the same shape, plus JSON
  output, numeric SEEK-id resolution, and a Neo4j-failure fallback. Wrote its
  workbook through `DBtable_sample.sampleRetrievalData` — a *second, separate
  copy* of the same pandas/openpyxl loop.
- **`seek/views.py:sampleDownload`** — the oldest path
  (`downloadSamples_new` / `downloadSamples_noTree`). Returned JSON carrying a
  link to a file on disk rather than the bytes, and emitted a zip of per-type
  `.xls` when the selection spanned sample types.

Two writers meant a change to the workbook had to be made twice or it silently
applied to only some pages. That is what motivated the unification.

### Dead code found while mapping

The template source implied far more behavior than actually ran.

1. **Two include-tree prompts never fired.** `downloadSamples`
   (`searchAdvanced_stable.embed.html:2`) and `downloadSamples0` (`:137`) each
   set `includeSampleTree = 1` and then `return`ed unconditionally on their third
   line. The `$.messager.confirm` in the first and the entire legacy `$.post` in
   the second were unreachable. The advanced tab always downloaded the full tree.
2. **`downloadSamples_new` ignored its `url_download` argument** — the
   `$.post(url_download, …)` block at `:112-120` was commented out, so it fetched
   `/seek/admin/retrieve/` unconditionally.
3. **`attributeFilter` was empty at every live call site.** A comma-separated
   header allowlist consumed at `dbtable_sample.py:2332`, `:4406`, `:4453`.
   `samples_stable.embed.html:107` hardcoded `''`;
   `searchAdvanced_stable.embed.html:67-69` computed `getTreeChecked()` into it
   and then discarded it along with the commented-out POST. **`getTreeChecked`
   was only ever defined in `searchAdvanced_tree.embed.html`, an orphan** — so on
   the live page it was an undefined function that would have thrown had the
   branch been reachable.
4. **Orphaned templates.** Nothing includes `searchAdvanced_rtable.embed.html`
   (download button at `:28`) or `searchAdvanced_tree.embed.html` (`:26`).
   `sampleSearch.html`'s `render` is commented out at `seek/views.py:412` and the
   view redirects to `/seek/search/`; `sampleDeletion.html` has no view at all.
   Both of those include `samples_stable.embed.html`, so its grid was live only
   via `searchAdvanced.html`. `samples_stable.embed.html.bk` is a backup file.

Net: `/seek/samples/download/` was live in three controls (b's "No" branch, d, e),
all with `includeSampleTree=0` and `attributeFilter=''`.

### The gate

`/nextseek_api/admin/samples/retrieve/` was `[IsAuthenticated, IsAdminUser]`.
DRF's `IsAdminUser` checks `is_staff`, and `dmac/views.py:80` and `:97` set
`is_staff = 1` on every SEEK user at registration *and* at every login update —
so the "admin" endpoint already admitted every SEEK-synced user (11 of 20
accounts in the local seed).

Removing the gate naively would have returned 404 rather than data:
`views.py:628` read `is_superuser or is_staff`, and a non-staff caller took the
project-filtered branch with `user_project_ids` that was **always** `[]`.
`SeekDB(None, None, None)` takes the username-is-None branch
(`seek/seekdb.py:31`), never calls `getSeekLogin()`, leaves `__server` as `None`,
so `getCurrentUser()` raised `TypeError` (`seek/seekapi.py:189`) into a bare
`except`. Empty project list → `ps.project_id IN ('')` → zero rows → 404.

---

## After

Every control now follows one path:

```
control (a…g)
  └─ window.nsDownloadSamples(identifiers, {includeTree})   static/js/ns_sample_download.js:51
       └─ POST /nextseek_api/admin/samples/retrieve/
            {identifiers, output_format: "excel", include_tree}
            └─ AdminSampleViewSet.admin_retrieve_samples    nextseek_api/views.py:689
                 ├─ permission_classes = [IsAuthenticated]  nextseek_api/views.py:656
                 ├─ SeekDB(None, *basic_tuple) → real projects   :740
                 ├─ include_tree ? getChildrenUIDs (Neo4j)  :807
                 │                : project-scoped MySQL query
                 └─ dbs.sampleRetrievalData(df, path)   :945
                      └─ write_samples_workbook(...)  nextseek_api/services/sample_workbook.py:430
                           ├─ lineage first, because the sheet order comes from it
                           │    (load_derivation_hops :217 → derivation_edges,
                           │     sample_type_depths, build_provenance_rows —
                           │     all in nextseek_api/services/sample_provenance.py)
                           ├─ sheet 1: README      (build_readme_blocks :122,
                           │                        load_sample_type_context :149,
                           │                        load_sample_field_context :176,
                           │                        _write_readme :320)
                           ├─ sheet 2: How this data flowed   (_write_flow_sheet :367)
                           │                                   — only if there is lineage
                           ├─ sheet 3: Controlled Vocabularies (_write_vocabulary_sheet :96)
                           │                                   — only if a column is governed
                           └─ one sheet per sample type, in generation order
                                (_annotate_header :387, _apply_dropdowns :404)
```

**Line numbers above were re-verified against the current tree on 2026-08-21.**
They move whenever this module does; treat a mismatch as the citation being
stale, not the code being wrong.

`DBtable_sample.sampleRetrievalData` (`seek/dbtable_sample.py:1038`) and
`seek.views.sample_retrieval_data` (`seek/views.py:1252`) both delegate to
`write_samples_workbook`, so the README cannot drift between call paths even
though both legacy views still exist.

### What the workbook contains

This is the canonical description; everything below refers back here rather than
restating it.

A downloaded workbook has up to three preamble sheets and then one sheet per
sample type:

| # | Sheet | Written when |
|---|---|---|
| 1 | `README` | always |
| 2 | `How this data flowed` | there is any lineage to draw |
| 3 | `Controlled Vocabularies` | any written column is governed by a vocabulary |
| 4… | one per sample type | always |

**Sample-type tabs are in generation order, not alphabetical order.** Each type
is placed at its longest distance from a type with no parent
(`sample_type_depths`), so a reader meets the types in the order the samples
were made — `PAT`, `PAV`, `TIS`, `DNA`, not `DNA`, `PAT`, `PAV`, `TIS`. A type
with no hop at all cannot be placed in the pipeline and sorts after everything
that can be; with no lineage available at all, every depth is infinite and the
order falls back to alphabetical, which is what it was before. The README's
summary table uses the same order, because a workbook whose tabs and README
disagree reads as a bug.

#### The README sheet

Sheet 1 of every downloaded workbook, in this order:

1. **A1** — a hyperlink to the contextdb on GitHub. Row 2 is blank.
2. **A summary table**, from row 3: a bold `Sample Type` / `Name` /
   `Description` header, then one row per sample type. This comes first so a
   reader sees what the workbook contains before meeting any column detail.
3. **A pointer to the flow sheet** — `How this data flowed: see the 'How this
   data flowed' sheet.`, bold, written *only* when that sheet exists. A reader
   who never opens the tab must still learn it is there; a pointer to a sheet
   that was not written would be worse than none.
4. **One section per sample type**, in the same order the sheets are written: a
   bold `CODE — Name` heading in column A, then a `Column` / `Meaning` table
   indented into columns B and C listing every column of that tab. A sample type
   whose columns all dropped out gets its heading but no table header, since a
   `Column` / `Meaning` row with nothing under it reads as a rendering bug.

Note that the per-type description lives in the summary table, not under the
section heading — the heading is followed directly by a blank line and then the
column table.

#### The flow sheet

`How this data flowed` renders the derivation graph as one chain per row,
alternating type and arrow cells (`PAT`, `--[Consent]-->`, `PAV`, …), so a flow
reads left to right. Hops come from Neo4j's `DERIVED_FROM` relationship, where
the assay is recorded on the edge itself; when the graph is unreachable they are
recovered from each row's own `Parent` UIDs and labelled from the weaker
per-sample assay link, or left as a bare `------>`. Upstream types are drawn
even when they were not downloaded — that is the part a reader cannot otherwise
see. It is a separate sheet rather than a README section precisely so it can
carry its own alternating column widths; the README's 46/34/100 layout would put
a 100-wide gap at every second type of a chain. No lineage, no sheet.

#### Controlled vocabularies and dropdowns

`Controlled Vocabularies` parks each needed vocabulary in its own column, and
every governed column on a sample-type tab gets an Excel dropdown pointing at
it. The terms need a real sheet because Excel caps an inline dropdown formula at
255 characters. Which columns are governed, and by what, is data:
`nextseek_api/services/controlled_vocabularies.json`, whose `variants` block
also records which observed spellings each canonical term absorbs and the
production row count that judgement rested on.

**The dropdowns warn; they never reject.** `errorStyle` is `"warning"`, not
`"stop"`, because downloaded data already contains values that predate these
vocabularies (`RNA-seq` for `RNA-Seq`, `Paired End` for `paired`) and a hard
reject would fire on open for rows the researcher never touched. Each range also
runs 500 rows past the last filled row, so a researcher adding samples keeps the
dropdown instead of losing it at the first empty row.

Separately from the dropdowns, every column whose meaning is known also carries
that definition as a **hover note on its header cell** — a researcher filling
the sheet in reads the header, not the README. Columns with no definition get no
note rather than an empty one.

#### Where the text comes from

Sample type names and descriptions come from `dmac.sample_types_context` via the
`Sample_types_context` model (`seek/models.py`); per-column meanings come from
`dmac.sample_attributes_unique` via `Sample_attributes_unique`, where `sample_type =
''` is the definition used on every tab and a sample type code overrides it for
that tab only. **Both join on a string key, never on an id** —
`sample_types_context` on `sample_type` and `sample_attributes_unique` on
`field_name` — because the id column does not agree with `sample_types.id`
across instances (production context has
`sampletype_id` 10 = `MUS`, while local `seek_production.sample_types` has id
10 = `DNA`).

Rows are derived from the columns actually written to the workbook, *after* the
all-empty-column drop, so the README never claims a column the researcher cannot
see. Nothing has to be registered: a new sample type gets a section the first
time anyone downloads one, and a new attribute gets a row the first time it
carries a value.

Nothing is omitted either. A sample type with no context row is still listed,
with the bare code as its heading, and a column with no definition is still
listed with a blank meaning — so a gap is visible rather than silent.
Definitions are written only for columns whose meaning is not self-evident; a
column judged obvious has no row and renders blank, and "obvious" and
"undocumented" are deliberately not distinguished in the workbook.

Every lookup here fails soft, and that is the rule the whole module is written
to: a lost lookup costs its section, never the download. If either context table
is missing or unreachable the lookup logs and returns empty — the README goes
unpopulated but the download still works. Neo4j is bounded at five seconds and
falls back to the `Parent` column, which costs the assay labels and not the
lineage; losing the lineage entirely costs the flow sheet and returns the tabs
to alphabetical order. An unreadable `controlled_vocabularies.json` costs the
dropdowns. A UID carrying no sample-type code costs that row its tab — it must
not cost the workbook, which is what the `isinstance` guard in
`derivation_edges` is for.

And every string written to a cell is stripped of
control characters and truncated to Excel's 32,767-character cell limit
(`_safe_cell_value`), because a `\x0b` — what a line break pasted from Word or a
PDF becomes — otherwise raises `IllegalCharacterError` out of the writer and one
bad definition row would cost every download of every workbook carrying that
sample type.

### One trap worth knowing

The datagrids render the `uid` column as an anchor, so `row.uid` is markup like
`<a href="…">MUS-230101ABC-1</a>`, not a bare UID. Both legacy collectors ran
`row.uid.match(/(?<=>).*?(?=<)/g)[0]` to recover the text. That is now
`nsExtractUid` (`static/js/ns_sample_download.js:103`), used by
`nsCollectSelectedUids`.

The two `newSearch.html` grids are different again: they select via
`getSelections()` rather than a `ck` column, and their UID column is named
`uuid`. They send numeric `s.id`, which the endpoint resolves to UUIDs.
`nsCollectSelectedUids` does **not** apply to them.

### Not a download: "Export samples to Import"

`simple_downloadSamples` used to be overloaded on its `url_download` argument, so
the ImmPort export button shared it, passing `/seek/samples/export/`. Splitting
the download out would have silently turned that button into a sample download.
It is now `simple_exportSamples` in `pages/samples_stable.embed.html`, posting to
`sampleExport` as before. `test_the_immport_export_did_not_become_a_download`
guards it.

### Guard tests

`nextseek_api/tests/test_download_call_sites.py` asserts that no live template
references a legacy download endpoint, that the helper script is loaded on the
three rendered pages, that every wrapper still calls the helper, and that the
orphan list still matches reality — so wiring an orphan back up trips a test
rather than shipping a broken button.

---

## Deliberately left alone

- **`is_superuser or is_staff`** (`nextseek_api/views.py:638`). Staff still see
  every sample rather than only their projects. The prerequisite the old comment
  named — real project resolution — is now satisfied, so dropping `is_staff` is a
  safe one-line change on its own terms, but it narrows what 11 of 20 local
  accounts can read and would affect the assistant and container-CC consumers,
  which currently rely on unfiltered reads. Assess that first.
- **`sampleDownload`, `adminRetrieveSamples`, the zip-of-xls path.** Unreachable
  from the UI, kept for external callers.
- **The dead advanced-tab include-tree prompt.** Not revived; that is a UX change,
  not a refactor.
- **The orphaned templates.** Nothing renders them.
- **`attributeFilter`.** Not ported — dead at every call site.

## Seed gap

Production's `dmac` has `sample_types_context`, `assay_context` and
`projects_context`. Only `sample_types_context` was added to
`startup/seed/dmac.sql.gz` (101 rows), because at the time only it was needed
for the README. `assay_context` and `projects_context` remain a seed/production
divergence and will bite whoever next depends on them.

`sample_attributes_unique` is a fourth gap, and a different one: the README's
per-column meanings *do* need it, but it could not be folded into the seed here
because `./startup.sh dump-db` requires maintainer credentials for a remote host
and regenerates all three seed dumps together. The dump therefore still does not
carry the table — but the gap no longer reaches a fresh install, because
`startup/steps/schema_fixups.py` registers `dmac.sample_attributes_unique` as a
`MissingTable` and install runs its DDL
(`startup/seed/sql/sample_attributes_unique.sql`) whenever it is absent. The same
hook heals an existing install on its next run. An instance that is running and
not about to be reinstalled — production — still takes that DDL by hand. If the
table is missing anyway, the loader renders meanings blank by design rather than
failing.

## Rolling out per-column definitions

What the sheet renders is described under "The README sheet" above. What it
takes to light the meanings up on an instance:

1. **Create the table.** `./startup.sh install` does it —
   `dmac.sample_attributes_unique` is registered in `startup/steps/schema_fixups.py`,
   so install (and `reset`) run `startup/seed/sql/sample_attributes_unique.sql`
   whenever the table is absent, whether or not seeds ran. For an instance
   that is already running — production included — apply that same DDL by hand
   instead of reinstalling; it is `CREATE TABLE IF NOT EXISTS`, so it is safe to
   run against an instance that already has the table.
2. **Load definitions into the table.** *No mechanism for this exists yet* —
   `load_field_definitions`, which reads the reviewer-edited xlsx, is Phase 2.
   Until it lands, rows go in by hand.
3. **Deploy.**

Steps can happen in any order. `load_sample_field_context` fails soft: if the
table is missing or unreachable it logs and every meaning renders blank, so
there is no window in which downloads break.
