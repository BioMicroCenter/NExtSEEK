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
            └─ AdminSampleViewSet.admin_retrieve_samples    nextseek_api/views.py:566
                 ├─ permission_classes = [IsAuthenticated]  nextseek_api/views.py:533
                 ├─ SeekDB(None, *basic_tuple) → real projects   :617
                 ├─ include_tree ? getChildrenUIDs (Neo4j)  :681
                 │                : project-scoped MySQL query
                 └─ write_samples_workbook(...)   nextseek_api/services/sample_workbook.py:91
                      ├─ sheet 1: README   (build_readme_rows :33, load_sample_type_context :50)
                      └─ one sheet per sample type
```

`DBtable_sample.sampleRetrievalData` (`seek/dbtable_sample.py:901`) and
`seek.views.sample_retrieval_data` (`seek/views.py:1233`) both delegate to
`write_samples_workbook`, so the README cannot drift between call paths even
though both legacy views still exist.

### The README sheet

Sheet 1 of every downloaded workbook. A1 hyperlinks to the contextdb on GitHub,
row 2 is blank, row 3 is the header `Sample Type | Name | Description`, and rows
4+ list every sample type present in the workbook, sorted by code.

Content comes from `dmac.sample_types_context` via the `Sample_types_context`
model (`seek/models.py`). **The join key is the `sample_type` code string, not
`sampletype_id`** — the id column does not agree with `sample_types.id` across
instances (production context has `sampletype_id` 10 = `MUS`, while local
`seek_production.sample_types` has id 10 = `DNA`).

A sample type with no context row is still listed, with blank name and
description, so the README always indexes every sheet and a gap in the context
table is visible rather than silent. If the table is missing entirely the lookup
logs and returns `{}` — the README goes unpopulated but the download still works.

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
`startup/seed/dmac.sql.gz` (101 rows), because only it is needed for the README.
The other two remain a seed/production divergence and will bite whoever next
depends on them.
